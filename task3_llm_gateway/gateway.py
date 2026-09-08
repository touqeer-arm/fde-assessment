import json
import logging
import sys
from collections import defaultdict
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from task3_llm_gateway.redaction import StreamingRedactor

UPSTREAM_TIMEOUT = httpx.Timeout(5.0, read=None)

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


def content_event(content: str, index: int = 0) -> str:
    return encode_sse_event(
        {
            "choices": [
                {
                    "index": index,
                    "delta": {
                        "content": content,
                    },
                }
            ]
        }
    )


@app.post("/v1/chat/completions", response_model=None)
async def proxy_completion(request: Request):
    payload: dict[str, Any] = await request.json()

    client = httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT)

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

    if upstream_response.status_code >= 400:
        body = await upstream_response.aread()
        await upstream_response.aclose()
        await client.aclose()

        logger.error(
            "Upstream LLM provider returned %s",
            upstream_response.status_code,
        )

        return Response(
            content=body,
            status_code=upstream_response.status_code,
            media_type=upstream_response.headers.get(
                "content-type",
                "application/json",
            ),
        )

    async def stream_response() -> AsyncIterator[str]:
        redactors: dict[int, StreamingRedactor] = defaultdict(StreamingRedactor)
        done_received = False

        def flush_all() -> Iterator[str]:
            for index, redactor in redactors.items():
                remaining = redactor.flush()

                if remaining:
                    yield content_event(remaining, index)

        try:
            async for line in upstream_response.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue

                raw_data = line.removeprefix("data:").strip()

                if raw_data == "[DONE]":
                    for event_text in flush_all():
                        yield event_text

                    yield "data: [DONE]\n\n"
                    done_received = True
                    break

                try:
                    event = json.loads(raw_data)
                except json.JSONDecodeError:
                    logger.warning("Dropped malformed SSE event from upstream")
                    continue

                choices = event.get("choices")

                if not isinstance(choices, list) or not choices:
                    yield encode_sse_event(event)
                    continue

                for choice in choices:
                    if not isinstance(choice, dict):
                        continue

                    delta = choice.get("delta")

                    if not isinstance(delta, dict):
                        continue

                    content = delta.get("content")

                    if not isinstance(content, str):
                        continue

                    index = choice.get("index", 0)
                    delta["content"] = redactors[index].feed(content)

                yield encode_sse_event(event)

            if not done_received:
                for event_text in flush_all():
                    yield event_text

        finally:
            await upstream_response.aclose()
            await client.aclose()

    return StreamingResponse(
        stream_response(),
        status_code=upstream_response.status_code,
        media_type="text/event-stream",
    )
