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


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/mcp")
async def proxy_mcp(request: Request) -> JSONResponse:
    payload: dict[str, Any] = await request.json()

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