import json
import logging
import sys
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from task3_llm_gateway.redaction import StreamingRedactor

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


def encode_sse_event(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event)}\n\n"


def content_event(content: str) -> str:
    return encode_sse_event(
        {
            "choices": [
                {
                    "delta": {
                        "content": content,
                    }
                }
            ]
        }
    )


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

        logger.error(
            "Unable to connect to upstream LLM provider"
        )

        return JSONResponse(
            status_code=502,
            content={
                "error": {
                    "code": "upstream_unavailable",
                    "message": "LLM provider unavailable",
                }
            },
        )

    async def stream_response() -> AsyncIterator[str]:
        redactor = StreamingRedactor()
        done_received = False

        try:
            async for line in upstream_response.aiter_lines():
                if not line:
                    continue

                if not line.startswith("data:"):
                    continue

                raw_data = line.removeprefix("data:").strip()

                if raw_data == "[DONE]":
                    remaining = redactor.flush()

                    if remaining:
                        yield content_event(remaining)

                    yield "data: [DONE]\n\n"
                    done_received = True
                    break

                try:
                    event = json.loads(raw_data)
                except json.JSONDecodeError:
                    logger.warning(
                        "Dropped malformed SSE event from upstream"
                    )
                    continue

                choices = event.get("choices")

                if not isinstance(choices, list) or not choices:
                    yield encode_sse_event(event)
                    continue

                choice = choices[0]

                if not isinstance(choice, dict):
                    yield encode_sse_event(event)
                    continue

                delta = choice.get("delta")

                if not isinstance(delta, dict):
                    yield encode_sse_event(event)
                    continue

                content = delta.get("content")

                if not isinstance(content, str):
                    yield encode_sse_event(event)
                    continue

                safe_content = redactor.feed(content)

                if safe_content:
                    delta["content"] = safe_content
                    yield encode_sse_event(event)

            if not done_received:
                remaining = redactor.flush()

                if remaining:
                    yield content_event(remaining)

        finally:
            await upstream_response.aclose()
            await client.aclose()

    return StreamingResponse(
        stream_response(),
        status_code=upstream_response.status_code,
        media_type="text/event-stream",
    )