# Task 4: Rate Limiting & Model Fallback Router

A resilient LLM gateway that enforces a per-tenant rolling token budget (backed by
on-disk SQLite) and fails over from a primary model provider to a fallback when
the primary is rate-limited or too slow.

Setup is in the [root README](../README.md#setup).

## Run

```powershell
uvicorn task4_resilient_gateway.gateway:app --port 8000
```

| Provider | URL |
| --- | --- |
| Primary | `http://127.0.0.1:8003/v1/chat/completions` |
| Fallback | `http://127.0.0.1:8004/v1/chat/completions` |

The test suite stands up mocked providers, so no external service is needed to
run the tests. SQLite lives at `task4_resilient_gateway/token_usage.db`, override
with `TASK4_DB_PATH`.

## Request contract

`POST /v1/chat/completions`

- `X-Tenant-ID` header, non-empty — otherwise `400 missing_tenant_id`
- JSON object body — otherwise `400 invalid_request`

All error responses share one shape:

```json
{ "error": { "code": "<code>", "message": "<message>" } }
```

## Rate limiting

- **50,000 tokens per rolling 60-second window, per tenant.** Exactly 50,000 is
  allowed; a request that would exceed it returns `429 rate_limit_exceeded`.
- Request size is counted with `tiktoken` (`cl100k_base`) over the serialized
  request body. Only request tokens are counted — completion tokens are not
  reserved.
- Tokens are charged **before** the upstream call and are not refunded if every
  provider fails. This keeps the limiter simple and stops a burst of failing
  requests from bypassing the budget.
- Usage rows (`tenant_id`, `recorded_at`, `tokens`) live in SQLite. Rows that
  have left the window are deleted before each check; a `(tenant_id, recorded_at)`
  index keeps the read fast.
- On rejection the response carries a `Retry-After` header when one can be
  computed (i.e. when enough existing usage will age out; absent if the single
  request is itself larger than the limit).

### Concurrency

`check_and_record` is serialized two ways so two requests can never read the same
remaining budget and both spend it:

- an `asyncio.Lock` — same-process, same-event-loop
- `BEGIN IMMEDIATE` + a SQLite busy timeout — independent connections / processes
  sharing the database file (WAL journaling enabled)

The timestamp is sampled inside the transaction, so queued requests are stamped
with the moment they actually commit.

## Provider failover

The primary is called first. Each provider call is bounded by **both** an `httpx`
timeout of 3000 ms and an outer `asyncio.wait_for` hard deadline of 3000 ms.

| Primary result | Action |
| --- | --- |
| `2xx` | return it |
| `429` | fail over to fallback |
| timeout (either mechanism) | fail over to fallback |
| other `4xx` / `5xx` | `502 upstream_error`, no failover |
| connection error | `502 upstream_unavailable`, no failover |

| Fallback result | Response |
| --- | --- |
| `2xx` | return it |
| timeout | `504 upstream_timeout` |
| connection error | `502 upstream_unavailable` |
| any `>= 400` | `502 upstream_error` |

## Error sanitization

Client-facing errors never contain raw provider bodies, connection details, stack
traces, database paths, or internal exception text. Any unhandled exception is
caught by a global handler and returned as `500 internal_error`; the real cause is
logged to stderr only.

## Assessment mapping

| Evaluation area | Implementation |
| --- | --- |
| Async concurrency | `asyncio.Lock` + SQLite writer serialization; timestamp taken inside the transaction |
| Timeout race handling | dual 3s bound (`httpx` timeout **and** `asyncio.wait_for`) per provider call |
| Token tracking | `tiktoken` request count; per-tenant rows persisted in SQLite |
| Window eviction | expired rows deleted before usage is summed; exact-50k boundary allowed |
| Shared-state safety | `BEGIN IMMEDIATE` + busy timeout guards independent connections on one DB file |
| Fallback mechanics | `429` and timeout route to fallback; other failures return standardized errors |
| Error sanitization | fixed `{"error": {...}}` shape; global handler for unexpected errors |

## Tests

```powershell
pytest tests/test_task4_rate_limit.py -v   # 8 tests
pytest tests/test_task4_gateway.py -v      # 11 tests
```

Rate limiter: exact boundary, tenant isolation, sliding-window expiry, SQLite
persistence, concurrent accounting (single and cross-connection), `Retry-After`
accuracy, oversized request. Gateway: primary success, `429` failover, HTTP-
timeout failover, hard-deadline failover, non-429 error without failover,
connection error, fallback failure / timeout, local rate-limit block, missing
tenant, sanitized unexpected error.
