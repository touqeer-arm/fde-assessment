import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from task4_resilient_gateway.rate_limit import SlidingWindowRateLimiter
from task4_resilient_gateway.tokens import count_request_tokens

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)

PRIMARY_URL = "http://127.0.0.1:8003/v1/chat/completions"
FALLBACK_URL = "http://127.0.0.1:8004/v1/chat/completions"

UPSTREAM_TIMEOUT_SECONDS = 3.0

DATABASE_PATH = Path(
    os.getenv(
        "TASK4_DB_PATH",
        "task4_resilient_gateway/token_usage.db",
    )
)

rate_limiter = SlidingWindowRateLimiter(DATABASE_PATH)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await rate_limiter.initialize()
    yield


app = FastAPI(
    title="Resilient LLM Gateway",
    lifespan=lifespan,
)


def error_response(
    status_code: int,
    code: str,
    message: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        headers=headers,
        content={
            "error": {
                "code": code,
                "message": message,
            }
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    # Last line of defence: never let an internal traceback reach
    # the client. The actual error is logged server-side only.
    logger.exception("Unhandled error while processing request")

    return error_response(
        status_code=500,
        code="internal_error",
        message="Internal gateway error",
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


async def call_provider(
    client: httpx.AsyncClient,
    url: str,
    payload: dict[str, Any],
) -> httpx.Response:
    # HTTPX enforces network-operation timeouts while asyncio.wait_for
    # provides a hard wall-clock deadline for the whole provider call.
    return await asyncio.wait_for(
        client.post(
            url,
            json=payload,
            timeout=UPSTREAM_TIMEOUT_SECONDS,
        ),
        timeout=UPSTREAM_TIMEOUT_SECONDS,
    )


def successful_response(
    response: httpx.Response,
) -> JSONResponse:
    try:
        body = response.json()
    except ValueError:
        logger.error(
            "Upstream provider returned invalid JSON"
        )

        return error_response(
            status_code=502,
            code="invalid_upstream_response",
            message="Upstream provider returned an invalid response",
        )

    return JSONResponse(
        status_code=response.status_code,
        content=body,
    )


@app.post("/v1/chat/completions", response_model=None)
async def chat_completions(request: Request):
    tenant_id = request.headers.get("x-tenant-id")

    if tenant_id is None or not tenant_id.strip():
        return error_response(
            status_code=400,
            code="missing_tenant_id",
            message="X-Tenant-ID header is required",
        )

    tenant_id = tenant_id.strip()

    try:
        payload = await request.json()
    except ValueError:
        return error_response(
            status_code=400,
            code="invalid_request",
            message="Request body must be valid JSON",
        )

    if not isinstance(payload, dict):
        return error_response(
            status_code=400,
            code="invalid_request",
            message="Request body must be a JSON object",
        )

    request_tokens = max(
        1,
        count_request_tokens(payload),
    )

    # Tokens are charged against the tenant's window up front, before the
    # upstream call, and are not refunded if every provider fails. This keeps
    # the limiter simple and prevents a burst of failing requests from
    # bypassing the budget; the trade-off is that a request billed here may
    # still return a gateway error. Only request tokens are counted -
    # completion tokens are not reserved.
    limit_result = await rate_limiter.check_and_record(
        tenant_id=tenant_id,
        tokens=request_tokens,
    )

    if not limit_result.allowed:
        logger.warning(
            "Tenant rate limit exceeded: "
            "tenant=%s used=%s limit=%s",
            tenant_id,
            limit_result.used_tokens,
            limit_result.limit,
        )

        headers: dict[str, str] = {}

        if limit_result.retry_after is not None:
            headers["Retry-After"] = str(
                limit_result.retry_after
            )

        return error_response(
            status_code=429,
            code="rate_limit_exceeded",
            message="Tenant token rate limit exceeded",
            headers=headers,
        )

    async with httpx.AsyncClient() as client:
        try:
            primary_response = await call_provider(
                client,
                PRIMARY_URL,
                payload,
            )

        except (TimeoutError, httpx.TimeoutException):
            logger.warning(
                "Primary provider timed out; using fallback"
            )

        except httpx.RequestError:
            logger.error(
                "Primary provider unavailable"
            )

            return error_response(
                status_code=502,
                code="upstream_unavailable",
                message="Upstream provider unavailable",
            )

        else:
            if primary_response.status_code == 429:
                logger.warning(
                    "Primary provider rate limited; using fallback"
                )

            elif primary_response.status_code >= 400:
                logger.error(
                    "Primary provider returned status %s",
                    primary_response.status_code,
                )

                return error_response(
                    status_code=502,
                    code="upstream_error",
                    message="Upstream provider failed",
                )

            else:
                return successful_response(
                    primary_response
                )

        # Reached only when the primary returned 429 or timed out.
        try:
            fallback_response = await call_provider(
                client,
                FALLBACK_URL,
                payload,
            )

        except (TimeoutError, httpx.TimeoutException):
            logger.error(
                "Fallback provider timed out"
            )

            return error_response(
                status_code=504,
                code="upstream_timeout",
                message="Upstream providers timed out",
            )

        except httpx.RequestError:
            logger.error(
                "Fallback provider unavailable"
            )

            return error_response(
                status_code=502,
                code="upstream_unavailable",
                message="Upstream providers unavailable",
            )

        if fallback_response.status_code >= 400:
            logger.error(
                "Fallback provider returned status %s",
                fallback_response.status_code,
            )

            return error_response(
                status_code=502,
                code="upstream_error",
                message="Upstream providers failed",
            )

        return successful_response(
            fallback_response
        )