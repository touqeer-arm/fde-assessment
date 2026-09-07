import math
import time
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

TOKEN_LIMIT = 50_000
WINDOW_SECONDS = 60


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    used_tokens: int
    limit: int
    retry_after: int | None = None


class SlidingWindowRateLimiter:
    def __init__(
        self,
        database_path: str | Path,
        token_limit: int = TOKEN_LIMIT,
        window_seconds: int = WINDOW_SECONDS,
    ) -> None:
        self.database_path = str(database_path)
        self.token_limit = token_limit
        self.window_seconds = window_seconds

    async def initialize(self) -> None:
        async with aiosqlite.connect(self.database_path) as database:
            await database.execute(
                """
                CREATE TABLE IF NOT EXISTS token_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL,
                    recorded_at REAL NOT NULL,
                    tokens INTEGER NOT NULL CHECK (tokens > 0)
                )
                """
            )

            await database.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_token_usage_tenant_time
                ON token_usage (tenant_id, recorded_at)
                """
            )

            await database.commit()

    async def check_and_record(
        self,
        tenant_id: str,
        tokens: int,
        now: float | None = None,
    ) -> RateLimitResult:
        if tokens <= 0:
            raise ValueError("tokens must be positive")

        current_time = time.time() if now is None else now
        window_start = current_time - self.window_seconds

        async with aiosqlite.connect(self.database_path) as database:
            # Serialize competing updates so two concurrent requests cannot
            # both observe the same remaining token budget and overspend it.
            await database.execute("BEGIN IMMEDIATE")

            try:
                await database.execute(
                    """
                    DELETE FROM token_usage
                    WHERE recorded_at <= ?
                    """,
                    (window_start,),
                )

                cursor = await database.execute(
                    """
                    SELECT COALESCE(SUM(tokens), 0)
                    FROM token_usage
                    WHERE tenant_id = ?
                      AND recorded_at > ?
                    """,
                    (
                        tenant_id,
                        window_start,
                    ),
                )

                row = await cursor.fetchone()
                used_tokens = int(row[0]) if row is not None else 0

                projected_tokens = used_tokens + tokens

                if projected_tokens > self.token_limit:
                    retry_after = await self._retry_after(
                        database,
                        tenant_id,
                        window_start,
                        current_time,
                    )

                    await database.rollback()

                    return RateLimitResult(
                        allowed=False,
                        used_tokens=used_tokens,
                        limit=self.token_limit,
                        retry_after=retry_after,
                    )

                await database.execute(
                    """
                    INSERT INTO token_usage (
                        tenant_id,
                        recorded_at,
                        tokens
                    )
                    VALUES (?, ?, ?)
                    """,
                    (
                        tenant_id,
                        current_time,
                        tokens,
                    ),
                )

                await database.commit()

                return RateLimitResult(
                    allowed=True,
                    used_tokens=projected_tokens,
                    limit=self.token_limit,
                )

            except Exception:
                await database.rollback()
                raise

    async def _retry_after(
        self,
        database: aiosqlite.Connection,
        tenant_id: str,
        window_start: float,
        current_time: float,
    ) -> int:
        cursor = await database.execute(
            """
            SELECT MIN(recorded_at)
            FROM token_usage
            WHERE tenant_id = ?
              AND recorded_at > ?
            """,
            (
                tenant_id,
                window_start,
            ),
        )

        row = await cursor.fetchone()

        if row is None or row[0] is None:
            return self.window_seconds

        oldest_event = float(row[0])

        remaining = (
            oldest_event
            + self.window_seconds
            - current_time
        )

        return max(1, math.ceil(remaining))