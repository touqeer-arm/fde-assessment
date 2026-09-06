import logging
import sys
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)

app = FastAPI(title="MCP Security Gateway")

DOWNSTREAM_URL = "http://127.0.0.1:8001/mcp"

TOKEN_ROLES = {
    "admin-token": "admin",
    "viewer-token": "viewer",
}


def get_role(authorization: str | None) -> str | None:
    if authorization is None:
        return None

    scheme, _, token = authorization.partition(" ")

    if scheme.lower() != "bearer" or not token:
        return None

    return TOKEN_ROLES.get(token)


def json_rpc_error(
    request_id: Any,
    code: int,
    message: str,
) -> JSONResponse:
    return JSONResponse(
        content={
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": code,
                "message": message,
            },
        }
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/mcp")
async def proxy_mcp(request: Request) -> JSONResponse:
    payload: dict[str, Any] = await request.json()

    request_id = payload.get("id")
    method = payload.get("method")

    role = get_role(request.headers.get("authorization"))

    if role is None:
        return json_rpc_error(
            request_id,
            -32000,
            "Invalid or missing bearer token",
        )

    if method == "tools/call":
        params = payload.get("params") or {}
        tool_name = params.get("name")

        if (
            isinstance(tool_name, str)
            and tool_name.startswith("admin_")
            and role != "admin"
        ):
            logger.warning(
                "Blocked unauthorized tool call: role=%s tool=%s",
                role,
                tool_name,
            )

            return json_rpc_error(
                request_id,
                -32001,
                "Unauthorized Tool Call",
            )

    async with httpx.AsyncClient() as client:
        response = await client.post(
            DOWNSTREAM_URL,
            json=payload,
            timeout=5.0,
        )

    return JSONResponse(
        status_code=response.status_code,
        content=response.json(),
    )