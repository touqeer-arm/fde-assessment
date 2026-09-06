from typing import Any

from fastapi import FastAPI

app = FastAPI(title="Mock MCP Server")


@app.post("/mcp")
async def handle_mcp(
    payload: dict[str, Any],
) -> dict[str, Any]:
    request_id = payload.get("id")
    method = payload.get("method")

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "tools": [
                    {
                        "name": "get_status",
                        "description": "Return system status",
                        "inputSchema": {
                            "type": "object",
                            "properties": {},
                        },
                    },
                    {
                        "name": "admin_reset_key",
                        "description": "Reset an API key",
                        "inputSchema": {
                            "type": "object",
                            "properties": {},
                        },
                    },
                ]
            },
        }

    if method == "tools/call":
        params = payload.get("params") or {}
        tool_name = params.get("name")

        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": f"Executed {tool_name}",
                    }
                ]
            },
        }

    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {
            "code": -32601,
            "message": "Method not found",
        },
    }