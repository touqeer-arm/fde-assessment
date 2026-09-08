import json
from typing import Any, ClassVar

import httpx
import pytest
from fastapi.testclient import TestClient

from task3_llm_gateway import gateway


def sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event)}\n\n"


def delta_event(
    content: str,
    index: int = 0,
) -> dict[str, Any]:
    return {
        "choices": [
            {
                "index": index,
                "delta": {
                    "content": content,
                },
            }
        ]
    }


class FakeUpstreamResponse:
    def __init__(
        self,
        lines: list[str],
        status_code: int = 200,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
    ) -> None:
        self._lines = lines
        self.status_code = status_code
        self._body = body
        self.headers = headers or {"content-type": "application/json"}
        self.closed = False

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self) -> bytes:
        return self._body

    async def aclose(self) -> None:
        self.closed = True


class FakeAsyncClient:
    response: ClassVar[FakeUpstreamResponse | None] = None
    error: ClassVar[Exception | None] = None
    closed: ClassVar[bool] = False

    def __init__(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        pass

    def build_request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return {
            "method": method,
            "url": url,
            **kwargs,
        }

    async def send(
        self,
        request: Any,
        *,
        stream: bool = False,
    ) -> FakeUpstreamResponse:
        if self.__class__.error is not None:
            raise self.__class__.error

        assert self.__class__.response is not None

        return self.__class__.response

    async def aclose(self) -> None:
        self.__class__.closed = True


@pytest.fixture(autouse=True)
def mock_upstream(monkeypatch):
    FakeAsyncClient.response = None
    FakeAsyncClient.error = None
    FakeAsyncClient.closed = False

    monkeypatch.setattr(
        gateway.httpx,
        "AsyncClient",
        FakeAsyncClient,
    )


client = TestClient(gateway.app)


def stream_lines(response) -> list[dict[str, Any]]:
    events = []

    for raw in response.text.split("\n\n"):
        raw = raw.strip()

        if not raw.startswith("data:"):
            continue

        payload = raw.removeprefix("data:").strip()

        if payload == "[DONE]":
            continue

        events.append(json.loads(payload))

    return events


def joined_content(
    response,
    index: int = 0,
) -> str:
    text = ""

    for event in stream_lines(response):
        for choice in event.get("choices", []):
            if choice.get("index", 0) == index:
                text += choice.get(
                    "delta",
                    {},
                ).get(
                    "content",
                    "",
                )

    return text


def test_pii_split_across_events_is_redacted():
    FakeAsyncClient.response = FakeUpstreamResponse(
        [
            sse(delta_event("Reach me at john.")),
            sse(delta_event("doe@example.com or 123-45-")),
            sse(delta_event("6789 today.")),
            "data: [DONE]",
        ]
    )

    response = client.post(
        "/v1/chat/completions",
        json={},
    )

    assert response.status_code == 200

    content = joined_content(response)

    assert "john.doe@example.com" not in content
    assert "123-45-6789" not in content

    assert content == "Reach me at [REDACTED] or [REDACTED] today."

    assert response.text.rstrip().endswith("data: [DONE]")


def test_second_choice_is_also_redacted():
    FakeAsyncClient.response = FakeUpstreamResponse(
        [
            sse(
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": "clean text"},
                        },
                        {
                            "index": 1,
                            "delta": {"content": "ssn 123-45-6789"},
                        },
                    ]
                }
            ),
            "data: [DONE]",
        ]
    )

    response = client.post(
        "/v1/chat/completions",
        json={},
    )

    assert (
        joined_content(
            response,
            index=0,
        )
        == "clean text"
    )

    assert "123-45-6789" not in joined_content(
        response,
        index=1,
    )


def test_finish_reason_survives_when_content_is_held():
    FakeAsyncClient.response = FakeUpstreamResponse(
        [
            sse(
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": "SSN 123-45-"},
                        }
                    ]
                }
            ),
            sse(
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": ""},
                            "finish_reason": "stop",
                        }
                    ]
                }
            ),
            "data: [DONE]",
        ]
    )

    response = client.post(
        "/v1/chat/completions",
        json={},
    )

    finish_reasons = [
        choice.get("finish_reason")
        for event in stream_lines(response)
        for choice in event.get("choices", [])
    ]

    assert "stop" in finish_reasons


def test_upstream_connection_error_returns_502():
    FakeAsyncClient.error = httpx.ConnectError("refused")

    response = client.post(
        "/v1/chat/completions",
        json={},
    )

    assert response.status_code == 502

    assert response.json()["error"]["code"] == "upstream_unavailable"


def test_stream_without_done_flushes_buffer():
    FakeAsyncClient.response = FakeUpstreamResponse(
        [sse(delta_event("all good here "))]
    )

    response = client.post(
        "/v1/chat/completions",
        json={},
    )

    assert joined_content(response) == "all good here "

    assert not response.text.rstrip().endswith("data: [DONE]")
