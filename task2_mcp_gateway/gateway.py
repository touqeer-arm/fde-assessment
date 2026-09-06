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
    try:
        payload = await request.json()
    except ValueError:
        return json_rpc_error(
            None,
            -32700,
            "Parse error",
        )

    if not isinstance(payload, dict):
        return json_rpc_error(
            None,
            -32600,
            "Invalid Request",
        )

    request_id = payload.get("id")
    method = payload.get("method")

    if payload.get("jsonrpc") != "2.0" or not isinstance(method, str):
        return json_rpc_error(
            request_id,
            -32600,
            "Invalid Request",
        )

    if method == "tools/call":
        params = payload.get("params")

        if not isinstance(params, dict):
            return json_rpc_error(
                request_id,
                -32602,
                "Invalid params",
            )

        tool_name = params.get("name")

        if not isinstance(tool_name, str):
            return json_rpc_error(
                request_id,
                -32602,
                "Invalid params",
            )

        if tool_name.startswith("admin_"):
            role = get_role(
                request.headers.get("authorization")
            )

            if role != "admin":
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

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                DOWNSTREAM_URL,
                json=payload,
                timeout=5.0,
            )

    except httpx.TimeoutException:
        logger.error("Downstream MCP server timed out")

        return json_rpc_error(
            request_id,
            -32002,
            "Downstream MCP Server Timeout",
        )

    except httpx.RequestError:
        logger.error("Downstream MCP server unavailable")

        return json_rpc_error(
            request_id,
            -32003,
            "Downstream MCP Server Unavailable",
        )

    try:
        response_body = response.json()
    except ValueError:
        logger.error("Downstream MCP server returned invalid JSON")

        return json_rpc_error(
            request_id,
            -32004,
            "Invalid Downstream Response",
        )

    return JSONResponse(
        status_code=response.status_code,
        content=response_body,
    )