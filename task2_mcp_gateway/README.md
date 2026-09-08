# Task 2: MCP Security Gateway

An HTTP / JSON-RPC reverse proxy that sits between an MCP client and a downstream
MCP server. It validates the JSON-RPC envelope, enforces role-based authorization
on administrative tools, and forwards everything else unchanged.

Setup is in the [root README](../README.md#setup).

## Run

```powershell
uvicorn task2_mcp_gateway.downstream:app --port 8001   # mock downstream MCP server
uvicorn task2_mcp_gateway.gateway:app --port 8000      # gateway (separate terminal)
```

The gateway forwards to `http://127.0.0.1:8001/mcp` with a 5s timeout.
`GET /health` returns `{"status": "ok"}`.

## Authorization

Two mock bearer tokens are recognized (`Authorization: Bearer <token>`):

| Token | Role |
| --- | --- |
| `admin-token` | `admin` |
| `viewer-token` | `viewer` |

Request handling:

- `tools/list` and ordinary `tools/call` requests are forwarded as-is.
- A `tools/call` whose `params.name` starts with `admin_` requires the `admin`
  role. Anything else (viewer token, unknown token, no header) is rejected with
  JSON-RPC `-32001` **before** the downstream server is contacted.

```json
{ "jsonrpc": "2.0", "id": 1, "error": { "code": -32001, "message": "Unauthorized Tool Call" } }
```

## Error handling

Errors the gateway itself raises are returned as JSON-RPC error objects
(HTTP 200) with a controlled message — internal exceptions and connection
details are never surfaced.

| Condition | Code | Message |
| --- | --- | --- |
| Body is not valid JSON | `-32700` | Parse error |
| Not an object / bad `jsonrpc` / missing `method` | `-32600` | Invalid Request |
| `tools/call` with bad `params` or `name` | `-32602` | Invalid params |
| Unauthorized `admin_*` call | `-32001` | Unauthorized Tool Call |
| Downstream timeout | `-32002` | Downstream MCP Server Timeout |
| Downstream connection failure | `-32003` | Downstream MCP Server Unavailable |
| Downstream returned non-JSON | `-32004` | Invalid Downstream Response |

On success the downstream status code and body are passed through unchanged.

## Assessment mapping

| Evaluation area | Implementation |
| --- | --- |
| JSON-RPC parsing | envelope validated before any routing; `method` / `params.name` inspected |
| Transparent proxying | `tools/list` and permitted `tools/call` forwarded over HTTP with `httpx` |
| Bearer role extraction | `Authorization` parsed into `admin` / `viewer` / `None` |
| Fine-grained authorization | only `admin_*` tool names require `admin` |
| Fail-closed | unauthorized calls return `-32001` without touching downstream |
| Clean errors | parse / validation / timeout / connectivity / bad-response all mapped to fixed JSON-RPC errors |

## Tests

```powershell
pytest tests/test_task2_gateway.py -v      # 11 tests
```

Covers transparent forwarding, viewer-vs-admin access, blocked calls never
reaching downstream, every JSON-RPC validation branch, and each downstream
failure mode.
