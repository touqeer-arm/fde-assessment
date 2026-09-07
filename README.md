# FDE Assessment

Technical assessment implementing four gateway and infrastructure tasks in Python.

## Tasks

1. Custom MCP Server
2. MCP Security Gateway
3. LLM Streaming Guardrail
4. Rate Limiting and Model Fallback Router

## Setup

### Windows PowerShell

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### macOS / Linux

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> The Task 2, 3, and 4 gateways are each documented on `--port 8000`; run one
> task at a time, or assign different ports.

## Project Structure

```text
fde-assessment/
├── task1_mcp_server/
│   ├── __init__.py
│   └── server.py
├── task2_mcp_gateway/
│   ├── __init__.py
│   ├── gateway.py
│   └── downstream.py
├── task3_llm_gateway/
│   ├── __init__.py
│   ├── gateway.py
│   ├── provider.py
│   └── redaction.py
├── task4_resilient_gateway/
│   ├── __init__.py
│   ├── gateway.py
│   ├── rate_limit.py
│   └── tokens.py
├── tests/
├── requirements.txt
└── README.md
```

## Task 1: Custom MCP Server

Implements a Model Context Protocol server using the official Python MCP SDK and STDIO transport.

### Tools

#### `get_customer_record`

Accepts:

```text
customer_id: CUST-XXXXX
```

Returns a mock customer record.

#### `trigger_refund`

Accepts:

```text
customer_id: CUST-XXXXX
amount: positive float
reason: minimum 10 characters
```

### Design

- Official MCP Python SDK
- STDIO transport
- Strict Pydantic validation
- Extra input fields rejected
- Invalid tool arguments mapped to JSON-RPC `-32602`
- Server logging written to stderr so stdout remains reserved for MCP JSON-RPC traffic

### Run

```bash
python -m task1_mcp_server.server
```

The server can also be exercised using MCP Inspector.

## Task 2: MCP Security Gateway

Implements an HTTP/JSON-RPC reverse proxy between an MCP client and a downstream MCP server.

### Authorization

The gateway understands two mock Bearer tokens:

```text
admin-token  -> admin
viewer-token -> viewer
```

Behavior:

- `tools/list` is forwarded transparently
- normal `tools/call` requests are forwarded
- tools whose names begin with `admin_` require the `admin` role
- unauthorized admin calls return JSON-RPC error `-32001`
- blocked calls never reach the downstream server
- malformed JSON-RPC requests and downstream failures are returned using clean JSON-RPC errors

### Run the mock downstream server

```bash
uvicorn task2_mcp_gateway.downstream:app --port 8001
```

### Run the gateway

```bash
uvicorn task2_mcp_gateway.gateway:app --port 8000
```

## Task 3: LLM Streaming Guardrail

Implements an asynchronous SSE proxy that detects and redacts PII while an LLM response is still streaming.

### Supported PII

- Email addresses
- Social Security numbers
- Credit-card numbers

Detected values are replaced with:

```text
[REDACTED]
```

### Streaming Design

PII may be split across multiple upstream chunks, for example:

```text
chunk 1: john.
chunk 2: doe@example.com
```

or:

```text
chunk 1: 123-45-
chunk 2: 6789
```

The gateway keeps only bounded ambiguous state between chunks. Safe text is emitted as soon as it can be released without exposing a partial PII candidate.

Additional behavior:

- asynchronous upstream streaming
- precompiled PII matching
- bounded redaction state
- separate redaction state for each LLM choice index
- SSE metadata such as `finish_reason` is preserved
- `[DONE]` is forwarded only when received from the upstream provider
- successful responses are not buffered in full
- fails closed: if an ambiguous candidate grows past the bounded state limit,
  it is replaced with `[REDACTED]` rather than emitted as partial text

### Run the mock provider

```bash
uvicorn task3_llm_gateway.provider:app --port 8002
```

### Run the gateway

```bash
uvicorn task3_llm_gateway.gateway:app --port 8000
```

## Task 4: Rate Limiting and Model Fallback Router

Implements a token-aware resilient LLM gateway with per-tenant rate limiting and provider failover.

### Rate Limit

Each tenant is limited to:

```text
50,000 tokens per rolling 60-second window
```

Requests identify the tenant using:

```text
X-Tenant-ID
```

A request without a non-empty `X-Tenant-ID` header is rejected with `400`.

### Token Accounting

- Requests are tokenized with `tiktoken`
- Only request tokens are counted; completion tokens are not reserved
- Tokens are charged against the window before the upstream call and are not
  refunded if every provider fails
- Usage is tracked independently per tenant
- State is persisted in SQLite
- Old records are evicted as they leave the sliding window
- The exact 50,000-token boundary is allowed
- Rejected requests can include an accurate `Retry-After` value

### Concurrency

Rate-limit checks and inserts are atomic.

The implementation uses:

- an async process-level lock
- SQLite `BEGIN IMMEDIATE`
- WAL journaling
- SQLite busy timeout

This prevents concurrent requests from observing the same remaining budget and both spending it.

### Provider Failover

The gateway first calls the primary provider.

Fallback is triggered when the primary:

- returns HTTP `429`
- exceeds the 3000 ms deadline

The 3-second limit is enforced using both HTTP client timeouts and a hard asynchronous wall-clock deadline.

Other upstream failures are converted into standardized gateway errors. Raw provider error bodies, connection details, and internal exceptions are not returned to clients.

### Run

```bash
uvicorn task4_resilient_gateway.gateway:app --port 8000
```

By default the gateway expects:

```text
Primary:
http://127.0.0.1:8003/v1/chat/completions

Fallback:
http://127.0.0.1:8004/v1/chat/completions
```

The SQLite database path can optionally be configured using:

```text
TASK4_DB_PATH
```

The automated test suite mocks upstream providers, so external LLM services are not required to run the tests.

## Tests

Run the full suite:

```bash
pytest -v
```

Run lint checks:

```bash
ruff check .
```

Tests cover, among other cases:

- MCP input validation and JSON-RPC errors
- STDIO protocol isolation
- MCP gateway authorization and forwarding
- PII split across streaming chunks
- bounded streaming redaction state
- multi-choice LLM streams
- per-tenant rate-limit isolation
- exact 50,000-token boundary behavior
- sliding-window expiration
- SQLite persistence
- concurrent rate-limit accounting
- provider `429` failover
- hard timeout failover
- sanitized upstream and internal errors
