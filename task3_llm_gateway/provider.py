import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

app = FastAPI(title="Mock LLM Provider")


async def generate_stream() -> AsyncIterator[str]:
    chunks = [
        "Hello ",
        "from ",
        "the mock ",
        "LLM provider.",
    ]

    for chunk in chunks:
        payload = {
            "choices": [
                {
                    "delta": {
                        "content": chunk,
                    }
                }
            ]
        }

        yield f"data: {json.dumps(payload)}\n\n"
        await asyncio.sleep(0.1)

    yield "data: [DONE]\n\n"


@app.post("/v1/chat/completions")
async def chat_completions(
    payload: dict[str, Any],
) -> StreamingResponse:
    return StreamingResponse(
        generate_stream(),
        media_type="text/event-stream",
    )