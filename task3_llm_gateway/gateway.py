import logging
import sys
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)

app = FastAPI(title="LLM Streaming Gateway")

UPSTREAM_URL = "http://127.0.0.1:8002/v1/chat/completions"


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/chat/completions", response_model=None)
async def proxy_completion(request: Request):
    payload: dict[str, Any] = await request.json()

    client = httpx.AsyncClient(timeout=None)

    try:
        upstream_request = client.build_request(
            "POST",
            UPSTREAM_URL,
            json=payload,
        )

        upstream_response = await client.send(
            upstream_request,
            stream=True,
        )

    except httpx.RequestError:
        await client.aclose()

        logger.error("Unable to connect to upstream LLM provider")

        return JSONResponse(
            status_code=502,
            content={
                "error": {
                    "code": "upstream_unavailable",
                    "message": "LLM provider unavailable",
                }
            },
        )

    async def stream_response():
        try:
            async for chunk in upstream_response.aiter_bytes():
                yield chunk
        finally:
            await upstream_response.aclose()
            await client.aclose()

    return StreamingResponse(
        stream_response(),
        status_code=upstream_response.status_code,
        media_type="text/event-stream",
    )