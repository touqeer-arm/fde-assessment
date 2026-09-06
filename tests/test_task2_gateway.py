from typing import Any, ClassVar

import httpx
import pytest
from fastapi.testclient import TestClient

from task2_mcp_gateway import gateway


class FakeResponse:
    def __init__(
        self,
        body: dict[str, Any],
        status_code: int = 200,
        json_error: Exception | None = None,
    ):
        self.body = body
        self.status_code = status_code
        self.json_error = json_error

    def json(self) -> dict[str, Any]:
        if self.json_error is not None:
            raise self.json_error
        return self.body


class FakeAsyncClient:
    calls: ClassVar[list[dict[str, Any]]] = []
    response_body: ClassVar[dict[str, Any]] = {}
    response_status: ClassVar[int] = 200
    response_json_error: ClassVar[Exception | None] = None
    error: ClassVar[Exception | None] = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        timeout: float,
    ) -> FakeResponse:
        self.__class__.calls.append(
            {
                "url": url,
                "json": json,
                "timeout": timeout,
            }
        )

        if self.__class__.error is not None:
            raise self.__class__.error

        return FakeResponse(
            self.__class__.response_body,
            self.__class__.response_status,
            self.__class__.response_json_error,
        )


@pytest.fixture(autouse=True)
def mock_downstream(monkeypatch):
    FakeAsyncClient.calls = []
    FakeAsyncClient.response_body = {}
    FakeAsyncClient.response_status = 200
    FakeAsyncClient.response_json_error = None
    FakeAsyncClient.error = None

    monkeypatch.setattr(
        gateway.httpx,
        "AsyncClient",
        FakeAsyncClient,
    )


client = TestClient(gateway.app)


def test_tools_list_without_auth_is_forwarded():
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
    }

    downstream_response = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "tools": [
                {"name": "get_status"},
                {"name": "admin_reset_key"},
            ]
        },
    }

    FakeAsyncClient.response_body = downstream_response

    response = client.post(
        "/mcp",
        json=payload,
    )

    assert response.status_code == 200
    assert response.json() == downstream_response

    assert len(FakeAsyncClient.calls) == 1
    assert FakeAsyncClient.calls[0]["json"] == payload
    assert (
        FakeAsyncClient.calls[0]["url"]
        == gateway.DOWNSTREAM_URL
    )


def test_normal_tool_without_auth_is_forwarded():
    payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "get_status",
            "arguments": {},
        },
    }

    downstream_response = {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": "Executed get_status",
                }
            ]
        },
    }

    FakeAsyncClient.response_body = downstream_response

    response = client.post(
        "/mcp",
        json=payload,
    )

    assert response.status_code == 200
    assert response.json() == downstream_response
    assert len(FakeAsyncClient.calls) == 1


def test_viewer_cannot_call_admin_tool():
    payload = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "admin_reset_key",
            "arguments": {},
        },
    }

    response = client.post(
        "/mcp",
        headers={
            "Authorization": "Bearer viewer-token",
        },
        json=payload,
    )

    assert response.status_code == 200

    assert response.json() == {
        "jsonrpc": "2.0",
        "id": 3,
        "error": {
            "code": -32001,
            "message": "Unauthorized Tool Call",
        },
    }

    assert FakeAsyncClient.calls == []


def test_admin_tool_without_auth_is_blocked():
    payload = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {
            "name": "admin_reset_key",
            "arguments": {},
        },
    }

    response = client.post(
        "/mcp",
        json=payload,
    )

    assert response.status_code == 200

    assert response.json() == {
        "jsonrpc": "2.0",
        "id": 4,
        "error": {
            "code": -32001,
            "message": "Unauthorized Tool Call",
        },
    }

    assert FakeAsyncClient.calls == []


def test_admin_can_call_admin_tool():
    payload = {
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {
            "name": "admin_reset_key",
            "arguments": {},
        },
    }

    downstream_response = {
        "jsonrpc": "2.0",
        "id": 5,
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": "Executed admin_reset_key",
                }
            ]
        },
    }

    FakeAsyncClient.response_body = downstream_response

    response = client.post(
        "/mcp",
        headers={
            "Authorization": "Bearer admin-token",
        },
        json=payload,
    )

    assert response.status_code == 200
    assert response.json() == downstream_response

    assert len(FakeAsyncClient.calls) == 1
    assert FakeAsyncClient.calls[0]["json"] == payload


def test_invalid_tool_call_params_returns_json_rpc_error():
    payload = {
        "jsonrpc": "2.0",
        "id": 6,
        "method": "tools/call",
        "params": {},
    }

    response = client.post(
        "/mcp",
        json=payload,
    )

    assert response.status_code == 200

    assert response.json() == {
        "jsonrpc": "2.0",
        "id": 6,
        "error": {
            "code": -32602,
            "message": "Invalid params",
        },
    }

    assert FakeAsyncClient.calls == []


def test_downstream_timeout_returns_clean_error():
    payload = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/list",
    }

    FakeAsyncClient.error = httpx.TimeoutException(
        "Downstream timed out"
    )

    response = client.post(
        "/mcp",
        json=payload,
    )

    assert response.status_code == 200

    assert response.json() == {
        "jsonrpc": "2.0",
        "id": 7,
        "error": {
            "code": -32002,
            "message": "Downstream MCP Server Timeout",
        },
    }


def test_malformed_request_body_returns_parse_error():
    response = client.post(
        "/mcp",
        content="not valid json {{{",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 200

    assert response.json() == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {
            "code": -32700,
            "message": "Parse error",
        },
    }

    assert FakeAsyncClient.calls == []


def test_wrong_jsonrpc_version_returns_invalid_request():
    payload = {
        "jsonrpc": "1.0",
        "id": 8,
        "method": "tools/list",
    }

    response = client.post(
        "/mcp",
        json=payload,
    )

    assert response.status_code == 200

    assert response.json() == {
        "jsonrpc": "2.0",
        "id": 8,
        "error": {
            "code": -32600,
            "message": "Invalid Request",
        },
    }

    assert FakeAsyncClient.calls == []


def test_downstream_connection_error_returns_unavailable():
    payload = {
        "jsonrpc": "2.0",
        "id": 9,
        "method": "tools/list",
    }

    FakeAsyncClient.error = httpx.ConnectError("Connection refused")

    response = client.post(
        "/mcp",
        json=payload,
    )

    assert response.status_code == 200

    assert response.json() == {
        "jsonrpc": "2.0",
        "id": 9,
        "error": {
            "code": -32003,
            "message": "Downstream MCP Server Unavailable",
        },
    }


def test_downstream_invalid_json_response_returns_clean_error():
    payload = {
        "jsonrpc": "2.0",
        "id": 10,
        "method": "tools/list",
    }

    FakeAsyncClient.response_json_error = ValueError("not valid json")

    response = client.post(
        "/mcp",
        json=payload,
    )

    assert response.status_code == 200

    assert response.json() == {
        "jsonrpc": "2.0",
        "id": 10,
        "error": {
            "code": -32004,
            "message": "Invalid Downstream Response",
        },
    }