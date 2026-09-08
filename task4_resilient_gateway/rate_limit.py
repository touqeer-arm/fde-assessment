import asyncio
import math
import time
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

TOKEN_LIMIT = 50_000
WINDOW_SECONDS = 60

# How long SQLite waits for a competing writer's lock (another process sharing
# the on-disk database) before raising, instead of failing immediately.
BUSY_TIMEOUT_MS = 5_000


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

        # Serializes check-and-record within this process so two concurrent
        # requests can never read the same remaining budget and both spend it.
        # BEGIN IMMEDIATE additionally guards writers in other processes.
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        async with aiosqlite.connect(self.database_path) as database:
            await database.execute("PRAGMA journal_mode=WAL")

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

        async with (
            self._lock,
            aiosqlite.connect(
                self.database_path,
                timeout=BUSY_TIMEOUT_MS / 1000,
            ) as database,
        ):
            await database.execute("BEGIN IMMEDIATE")

            current_time = time.time() if now is None else now
            window_start = current_time - self.window_seconds

            try:
                # Evict usage too old to affect any active sliding window.
                await database.execute(
                    "DELETE FROM token_usage WHERE recorded_at <= ?",
                    (window_start,),
                )

                cursor = await database.execute(
                    """
                    SELECT recorded_at, tokens
                    FROM token_usage
                    WHERE tenant_id = ?
                      AND recorded_at > ?
                    ORDER BY recorded_at ASC, id ASC
                    """,
                    (tenant_id, window_start),
                )

                rows = await cursor.fetchall()

                used_tokens = sum(int(row[1]) for row in rows)
                projected_tokens = used_tokens + tokens

                if projected_tokens > self.token_limit:
                    retry_after = self._calculate_retry_after(
                        rows=rows,
                        incoming_tokens=tokens,
                        used_tokens=used_tokens,
                        current_time=current_time,
                    )

                    # Keep the stale-row eviction even though this
                    # request is rejected.
                    await database.commit()

                    return RateLimitResult(
                        allowed=False,
                        used_tokens=used_tokens,
                        limit=self.token_limit,
                        retry_after=retry_after,
                    )

                await database.execute(
                    """
                    INSERT INTO token_usage (
                        tenant_id, recorded_at, tokens
                    )
                    VALUES (?, ?, ?)
                    """,
                    (tenant_id, current_time, tokens),
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

    def _calculate_retry_after(
        self,
        rows: list[tuple[float, int]],
        incoming_tokens: int,
        used_tokens: int,
        current_time: float,
    ) -> int | None:
        # A request larger than the entire configured budget can never fit.
        if incoming_tokens > self.token_limit:
            return None

        tokens_that_must_expire = used_tokens + incoming_tokens - self.token_limit

        expired_tokens = 0

        for recorded_at, event_tokens in rows:
            expired_tokens += int(event_tokens)

            if expired_tokens >= tokens_that_must_expire:
                available_at = float(recorded_at) + self.window_seconds
                return max(1, math.ceil(available_at - current_time))

        return None
