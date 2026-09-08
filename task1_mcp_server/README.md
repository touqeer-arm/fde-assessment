# Task 1: Custom MCP Server

A Model Context Protocol server built on the official MCP Python SDK (`mcp`),
served over STDIO. It exposes two tools, validates their arguments with strict
Pydantic models, and keeps all logging off the protocol stream.

Setup is in the [root README](../README.md#setup).

## Run

```powershell
python -m task1_mcp_server.server
```

The process speaks MCP JSON-RPC on **stdout** and writes logs to **stderr**. It
can also be attached to MCP Inspector.

## Tools

### `get_customer_record`

```json
{ "customer_id": "CUST-12345" }
```

- `customer_id` must match `^CUST-\d{5}$` (strict string)
- returns a fixed mock record (`customer_id`, `name`, `email`, `status`)

### `trigger_refund`

```json
{ "customer_id": "CUST-12345", "amount": 12.5, "reason": "Duplicate order charge" }
```

- `customer_id` — same rule as above
- `amount` — strict `float`, `> 0`
- `reason` — strict `str`, at least 10 characters
- unknown fields are rejected (`extra="forbid"`)
- returns a fixed mock result with `status: "approved"`

## Validation design

Argument validation is a **server middleware** (`validate_tool_arguments`), not
just tool-signature typing:

1. It intercepts `tools/call`, matches the tool name, and validates the arguments
   against `CustomerRecordInput` / `RefundInput`.
2. On `pydantic.ValidationError` it raises `MCPError(code=INVALID_PARAMS)` —
   JSON-RPC `-32602` — with `data={"tool": <name>}`. The raw validation detail is
   logged to stderr, not returned.
3. Calls to an unknown tool are not validated here; the SDK returns a normal
   error result (`is_error=True`, text `Unknown tool: <name>`), not `-32602`.

This centralizes the invalid-parameter contract in one place and guarantees the
error code regardless of how the SDK reports schema failures.

## Assessment mapping

| Evaluation area | Implementation |
| --- | --- |
| Official MCP SDK | `mcp.server.MCPServer`, `@mcp.tool()`, STDIO transport |
| Strict schema validation | `Field(..., strict=True)`, `ConfigDict(extra="forbid")` |
| Invalid parameters | middleware converts `ValidationError` → `MCPError` / JSON-RPC `-32602` |
| STDIO isolation | `logging.basicConfig(stream=sys.stderr)`; stdout carries only JSON-RPC |
| Edge cases | malformed IDs, non-positive / string amounts, short & missing reasons, extra fields |

## Tests

```powershell
pytest tests/test_task1_server.py -v      # 15 tests
```

Covers each tool's happy path, every validation rule, unknown-tool handling, and
two end-to-end STDIO sessions asserting that logs never reach stdout while
JSON-RPC responses (including a `-32602`) do.
