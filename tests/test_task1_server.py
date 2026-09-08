import json
import subprocess
import sys
from pathlib import Path

import pytest
from mcp import Client, MCPError
from mcp.types import INVALID_PARAMS

from task1_mcp_server.server import mcp


@pytest.mark.asyncio
async def test_get_customer_record():
    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_customer_record",
            {"customer_id": "CUST-12345"},
        )

    assert result.is_error is False
    assert result.structured_content == {
        "customer_id": "CUST-12345",
        "name": "John Doe",
        "email": "xyz@example.com",
        "status": "active",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "customer_id",
    [
        "CUST-1234",
        "CUST-12345\n",
    ],
)
async def test_rejects_invalid_customer_ids(customer_id):
    async with Client(mcp) as client:
        with pytest.raises(MCPError) as exc_info:
            await client.call_tool(
                "get_customer_record",
                {"customer_id": customer_id},
            )

    assert exc_info.value.error.code == INVALID_PARAMS


@pytest.mark.asyncio
async def test_trigger_refund():
    async with Client(mcp) as client:
        result = await client.call_tool(
            "trigger_refund",
            {
                "customer_id": "CUST-12345",
                "amount": 49.99,
                "reason": "Duplicate charge",
            },
        )

    assert result.is_error is False
    assert result.structured_content == {
        "customer_id": "CUST-12345",
        "amount": 49.99,
        "reason": "Duplicate charge",
        "status": "approved",
    }


@pytest.mark.asyncio
async def test_accepts_reason_at_minimum_length():
    async with Client(mcp) as client:
        result = await client.call_tool(
            "trigger_refund",
            {
                "customer_id": "CUST-12345",
                "amount": 25.00,
                "reason": "1234567890",
            },
        )

    assert result.is_error is False


@pytest.mark.asyncio
async def test_rejects_reason_below_minimum_length():
    async with Client(mcp) as client:
        with pytest.raises(MCPError) as exc_info:
            await client.call_tool(
                "trigger_refund",
                {
                    "customer_id": "CUST-12345",
                    "amount": 25.00,
                    "reason": "123456789",
                },
            )

    assert exc_info.value.error.code == INVALID_PARAMS


@pytest.mark.asyncio
@pytest.mark.parametrize("amount", [0, -1])
async def test_rejects_non_positive_refund_amounts(amount):
    async with Client(mcp) as client:
        with pytest.raises(MCPError) as exc_info:
            await client.call_tool(
                "trigger_refund",
                {
                    "customer_id": "CUST-12345",
                    "amount": amount,
                    "reason": "Duplicate charge",
                },
            )

    assert exc_info.value.error.code == INVALID_PARAMS


@pytest.mark.asyncio
async def test_rejects_missing_refund_field():
    async with Client(mcp) as client:
        with pytest.raises(MCPError) as exc_info:
            await client.call_tool(
                "trigger_refund",
                {
                    "customer_id": "CUST-12345",
                    "amount": 25.00,
                },
            )

    assert exc_info.value.error.code == INVALID_PARAMS


@pytest.mark.asyncio
async def test_rejects_unexpected_customer_record_field():
    async with Client(mcp) as client:
        with pytest.raises(MCPError) as exc_info:
            await client.call_tool(
                "get_customer_record",
                {
                    "customer_id": "CUST-12345",
                    "unexpected": "value",
                },
            )

    assert exc_info.value.error.code == INVALID_PARAMS


@pytest.mark.asyncio
async def test_rejects_unexpected_refund_field():
    async with Client(mcp) as client:
        with pytest.raises(MCPError) as exc_info:
            await client.call_tool(
                "trigger_refund",
                {
                    "customer_id": "CUST-12345",
                    "amount": 25.00,
                    "reason": "Duplicate charge",
                    "unexpected": "value",
                },
            )

    assert exc_info.value.error.code == INVALID_PARAMS


@pytest.mark.asyncio
async def test_rejects_string_refund_amount():
    async with Client(mcp) as client:
        with pytest.raises(MCPError) as exc_info:
            await client.call_tool(
                "trigger_refund",
                {
                    "customer_id": "CUST-12345",
                    "amount": "49.99",
                    "reason": "Duplicate charge",
                },
            )

    assert exc_info.value.error.code == INVALID_PARAMS


@pytest.mark.asyncio
async def test_unknown_tool_returns_error_result():
    async with Client(mcp) as client:
        result = await client.call_tool(
            "does_not_exist",
            {},
        )

    assert result.is_error is True

    error_text = " ".join(item.text for item in result.content if hasattr(item, "text"))

    assert "Unknown tool" in error_text
    assert "does_not_exist" in error_text


def test_startup_logs_do_not_write_to_stdout():
    project_root = Path(__file__).resolve().parents[1]

    result = subprocess.run(
        [sys.executable, "-m", "task1_mcp_server.server"],
        cwd=project_root,
        input="",
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert result.stdout == ""
    assert "Starting MCP server using stdio transport" in result.stderr


def test_stdio_stdout_contains_only_json_rpc():
    """Verify protocol messages stay on stdout and application logs use stderr."""

    project_root = Path(__file__).resolve().parents[1]

    process = subprocess.Popen(
        [sys.executable, "-m", "task1_mcp_server.server"],
        cwd=project_root,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    assert process.stdin is not None
    assert process.stdout is not None

    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {
                    "name": "isolation-test",
                    "version": "0.0.1",
                },
            },
        },
        {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        },
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "get_customer_record",
                "arguments": {
                    "customer_id": "CUST-12345",
                },
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "get_customer_record",
                "arguments": {
                    "customer_id": "not-valid",
                },
            },
        },
    ]

    stdout_lines = []

    try:
        for request in requests:
            process.stdin.write(json.dumps(request) + "\n")
            process.stdin.flush()

            if "id" in request:
                stdout_lines.append(process.stdout.readline())

    finally:
        process.terminate()

        try:
            _, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            _, stderr = process.communicate()

    assert len(stdout_lines) == 3

    responses = []

    for line in stdout_lines:
        assert line.strip() != ""

        response = json.loads(line)
        assert response.get("jsonrpc") == "2.0"

        responses.append(response)

    assert {response["id"] for response in responses} == {1, 2, 3}

    invalid_response = next(response for response in responses if response["id"] == 3)

    assert invalid_response["error"]["code"] == INVALID_PARAMS

    assert "Starting MCP server using stdio transport" not in "".join(stdout_lines)
    assert "Looking up customer record" not in "".join(stdout_lines)
    assert "Rejected invalid arguments" not in "".join(stdout_lines)

    assert "Looking up customer record: CUST-12345" in stderr
    assert "Rejected invalid arguments" in stderr
