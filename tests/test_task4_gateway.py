import asyncio
from typing import Any, ClassVar

import httpx
import pytest
from fastapi.testclient import TestClient

from task4_resilient_gateway import gateway
from task4_resilient_gateway.rate_limit import RateLimitResult


class FakeProviderResponse:
    def __init__(
        self,
        status_code: int,
        body: dict[str, Any] | None = None,
        json_error: Exception | None = None,
    ) -> None:
        self.status_code = status_code
        self.body = body or {}
        self.json_error = json_error

    def json(self) -> dict[str, Any]:
        if self.json_error is not None:
            raise self.json_error

        return self.body


class FakeAsyncClient:
    outcomes: ClassVar[
        list[FakeProviderResponse | Exception]
    ] = []

    calls: ClassVar[list[dict[str, Any]]] = []

    def __init__(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(
        self,
        exc_type,
        exc,
        traceback,
    ) -> None:
        pass

    async def post(
        self,
        url: str,
        json: dict[str, Any],
        timeout: float,
    ) -> FakeProviderResponse:
        self.__class__.calls.append(
            {
                "url": url,
                "json": json,
                "timeout": timeout,
            }
        )

        outcome = self.__class__.outcomes.pop(0)

        if isinstance(outcome, Exception):
            raise outcome

        return outcome


@pytest.fixture(autouse=True)
def mock_dependencies(monkeypatch):
    FakeAsyncClient.outcomes = []
    FakeAsyncClient.calls = []

    monkeypatch.setattr(
        gateway.httpx,
        "AsyncClient",
        FakeAsyncClient,
    )

    async def allow_request(
        tenant_id: str,
        tokens: int,
    ) -> RateLimitResult:
        return RateLimitResult(
            allowed=True,
            used_tokens=tokens,
            limit=50_000,
        )

    monkeypatch.setattr(
        gateway.rate_limiter,
        "check_and_record",
        allow_request,
    )


client = TestClient(gateway.app)


def request_payload() -> dict[str, Any]:
    return {
        "messages": [
            {
                "role": "user",
                "content": "hello",
            }
        ]
    }


def test_primary_success_is_returned():
    FakeAsyncClient.outcomes = [
        FakeProviderResponse(
            200,
            {
                "provider": "primary",
                "result": "ok",
            },
        )
    ]

    response = client.post(
        "/v1/chat/completions",
        headers={
            "X-Tenant-ID": "tenant-a",
        },
        json=request_payload(),
    )

    assert response.status_code == 200

    assert response.json() == {
        "provider": "primary",
        "result": "ok",
    }

    assert len(FakeAsyncClient.calls) == 1

    assert (
        FakeAsyncClient.calls[0]["url"]
        == gateway.PRIMARY_URL
    )

    assert (
        FakeAsyncClient.calls[0]["timeout"]
        == 3.0
    )


def test_primary_429_uses_fallback():
    FakeAsyncClient.outcomes = [
        FakeProviderResponse(
            429,
            {
                "error": "secret primary details",
            },
        ),
        FakeProviderResponse(
            200,
            {
                "provider": "fallback",
                "result": "ok",
            },
        ),
    ]

    response = client.post(
        "/v1/chat/completions",
        headers={
            "X-Tenant-ID": "tenant-a",
        },
        json=request_payload(),
    )

    assert response.status_code == 200
    assert response.json()["provider"] == "fallback"

    assert [
        call["url"]
        for call in FakeAsyncClient.calls
    ] == [
        gateway.PRIMARY_URL,
        gateway.FALLBACK_URL,
    ]


def test_primary_http_timeout_uses_fallback():
    FakeAsyncClient.outcomes = [
        httpx.ReadTimeout(
            "primary exceeded timeout"
        ),
        FakeProviderResponse(
            200,
            {
                "provider": "fallback",
                "result": "ok",
            },
        ),
    ]

    response = client.post(
        "/v1/chat/completions",
        headers={
            "X-Tenant-ID": "tenant-a",
        },
        json=request_payload(),
    )

    assert response.status_code == 200
    assert response.json()["provider"] == "fallback"

    assert len(FakeAsyncClient.calls) == 2


def test_hard_deadline_uses_fallback(
    monkeypatch,
):
    class SlowPrimaryClient(FakeAsyncClient):
        async def post(
            self,
            url: str,
            json: dict[str, Any],
            timeout: float,
        ) -> FakeProviderResponse:
            FakeAsyncClient.calls.append(
                {
                    "url": url,
                    "json": json,
                    "timeout": timeout,
                }
            )

            if url == gateway.PRIMARY_URL:
                await asyncio.sleep(0.05)

                return FakeProviderResponse(
                    200,
                    {
                        "provider": "primary",
                    },
                )

            return FakeProviderResponse(
                200,
                {
                    "provider": "fallback",
                    "result": "ok",
                },
            )

    monkeypatch.setattr(
        gateway.httpx,
        "AsyncClient",
        SlowPrimaryClient,
    )

    monkeypatch.setattr(
        gateway,
        "UPSTREAM_TIMEOUT_SECONDS",
        0.01,
    )

    response = client.post(
        "/v1/chat/completions",
        headers={
            "X-Tenant-ID": "tenant-a",
        },
        json=request_payload(),
    )

    assert response.status_code == 200
    assert response.json()["provider"] == "fallback"

    assert [
        call["url"]
        for call in FakeAsyncClient.calls
    ] == [
        gateway.PRIMARY_URL,
        gateway.FALLBACK_URL,
    ]


def test_primary_500_does_not_trigger_failover():
    FakeAsyncClient.outcomes = [
        FakeProviderResponse(
            500,
            {
                "secret": "provider stack trace",
            },
        )
    ]

    response = client.post(
        "/v1/chat/completions",
        headers={
            "X-Tenant-ID": "tenant-a",
        },
        json=request_payload(),
    )

    assert response.status_code == 502

    assert response.json() == {
        "error": {
            "code": "upstream_error",
            "message": "Upstream provider failed",
        }
    }

    assert "provider stack trace" not in response.text
    assert len(FakeAsyncClient.calls) == 1


def test_fallback_failure_returns_sanitized_error():
    FakeAsyncClient.outcomes = [
        FakeProviderResponse(
            429,
            {
                "secret": "primary secret",
            },
        ),
        FakeProviderResponse(
            500,
            {
                "secret": "fallback secret",
            },
        ),
    ]

    response = client.post(
        "/v1/chat/completions",
        headers={
            "X-Tenant-ID": "tenant-a",
        },
        json=request_payload(),
    )

    assert response.status_code == 502

    assert response.json() == {
        "error": {
            "code": "upstream_error",
            "message": "Upstream providers failed",
        }
    }

    assert "primary secret" not in response.text
    assert "fallback secret" not in response.text


def test_fallback_timeout_returns_sanitized_error():
    FakeAsyncClient.outcomes = [
        FakeProviderResponse(
            429,
            {
                "secret": "primary details",
            },
        ),
        httpx.ReadTimeout(
            "fallback secret timeout details"
        ),
    ]

    response = client.post(
        "/v1/chat/completions",
        headers={
            "X-Tenant-ID": "tenant-a",
        },
        json=request_payload(),
    )

    assert response.status_code == 504

    assert response.json() == {
        "error": {
            "code": "upstream_timeout",
            "message": "Upstream providers timed out",
        }
    }

    assert "secret" not in response.text.lower()


def test_local_rate_limit_blocks_upstream(
    monkeypatch,
):
    async def block_request(
        tenant_id: str,
        tokens: int,
    ) -> RateLimitResult:
        return RateLimitResult(
            allowed=False,
            used_tokens=50_000,
            limit=50_000,
            retry_after=12,
        )

    monkeypatch.setattr(
        gateway.rate_limiter,
        "check_and_record",
        block_request,
    )

    response = client.post(
        "/v1/chat/completions",
        headers={
            "X-Tenant-ID": "tenant-a",
        },
        json=request_payload(),
    )

    assert response.status_code == 429

    assert response.json() == {
        "error": {
            "code": "rate_limit_exceeded",
            "message": "Tenant token rate limit exceeded",
        }
    }

    assert response.headers["Retry-After"] == "12"
    assert FakeAsyncClient.calls == []


def test_missing_tenant_id_is_rejected():
    response = client.post(
        "/v1/chat/completions",
        json=request_payload(),
    )

    assert response.status_code == 400

    assert (
        response.json()["error"]["code"]
        == "missing_tenant_id"
    )

    assert FakeAsyncClient.calls == []


def test_primary_connection_error_returns_sanitized_error():
    FakeAsyncClient.outcomes = [
        httpx.ConnectError(
            "connection refused: secret provider detail"
        )
    ]

    response = client.post(
        "/v1/chat/completions",
        headers={
            "X-Tenant-ID": "tenant-a",
        },
        json=request_payload(),
    )

    assert response.status_code == 502

    assert response.json() == {
        "error": {
            "code": "upstream_unavailable",
            "message": "Upstream provider unavailable",
        }
    }

    assert "secret provider detail" not in response.text
    assert len(FakeAsyncClient.calls) == 1


def test_unexpected_error_is_sanitized(monkeypatch):
    async def explode(tenant_id: str, tokens: int) -> RateLimitResult:
        raise RuntimeError("internal detail: db path /secret/usage.db")

    monkeypatch.setattr(
        gateway.rate_limiter,
        "check_and_record",
        explode,
    )

    safe_client = TestClient(
        gateway.app,
        raise_server_exceptions=False,
    )

    response = safe_client.post(
        "/v1/chat/completions",
        headers={
            "X-Tenant-ID": "tenant-a",
        },
        json=request_payload(),
    )

    assert response.status_code == 500

    assert response.json() == {
        "error": {
            "code": "internal_error",
            "message": "Internal gateway error",
        }
    }

    assert "secret" not in response.text.lower()
    assert FakeAsyncClient.calls == []