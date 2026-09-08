# Task 3: LLM Streaming Guardrail

An asynchronous SSE proxy for an LLM chat-completions endpoint. It redacts PII
from the model's output **while the response is still streaming**, without ever
holding the full completion in memory.

Setup is in the [root README](../README.md#setup).

## Run

```powershell
uvicorn task3_llm_gateway.provider:app --port 8002    # mock streaming provider
uvicorn task3_llm_gateway.gateway:app --port 8000     # gateway (separate terminal)
```

The gateway proxies `POST /v1/chat/completions` to
`http://127.0.0.1:8002/v1/chat/completions`. `GET /health` returns
`{"status": "ok"}`.

```bash
curl -N -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H 'Content-Type: application/json' -d '{"messages":[{"role":"user","content":"hi"}]}'
```

## Redaction

Detected values are replaced with `[REDACTED]`:

- email addresses
- US Social Security numbers (`123-45-6789`)
- credit-card numbers (13-19 digits, spaces/hyphens allowed)

All three are one precompiled alternation, applied in a single pass per chunk.

## Streaming design

PII can straddle chunk boundaries (`"john."` + `"doe@example.com"`, or
`"123-45-"` + `"6789"`). `StreamingRedactor` handles this by holding back only
the **trailing ambiguous run** — the shortest suffix that could still grow into a
match — and emitting everything before it immediately.

- upstream consumed with `aiter_lines()`; timeout is `connect/write/pool = 5s`,
  `read` unbounded (long generations)
- `choices[].delta.content` is redacted in place; a separate `StreamingRedactor`
  per `choice.index`
- events are re-emitted whole, so `finish_reason` / `role` survive even when this
  chunk's content is fully withheld
- pending state is bounded at `MAX_PENDING_CHARS` (320). It **fails closed**: an
  ambiguous run that exceeds the bound is replaced with `[REDACTED]` rather than
  released as partial text
- `data: [DONE]` is forwarded only if the upstream sends it; `flush()` redacts
  and emits whatever remains buffered when the stream ends
- upstream connection failure → `502 {"error": {"code": "upstream_unavailable"}}`;
  an upstream `>= 400` status is passed through with its body and status

## Assessment mapping

| Evaluation area | Implementation |
| --- | --- |
| Async chunking | non-blocking `aiter_lines()` loop, streamed `StreamingResponse` |
| Partial-stream state | only the ambiguous trailing suffix is carried between deltas |
| Regex performance | single combined precompiled pattern; commit point found by a reverse scan, not a re-match |
| Cross-chunk correctness | withheld suffix lets a match split over 2+ chunks still be caught |
| Low latency | safe prefix emitted as soon as it cannot be part of a match |
| Memory efficiency | bounded pending buffer; the full response is never accumulated |
| Multiple choices | independent redaction state keyed by `choice.index` |

## Tests

```powershell
pytest tests/test_task3_redaction.py -v    # 10 tests
pytest tests/test_task3_gateway.py -v      # 5 tests
```

Covers cross-chunk email / SSN / card redaction, clean-text passthrough, the
fail-closed bound, numbers that are not PII, multi-choice streams,
`finish_reason` preservation, connection errors, and `[DONE]` handling.
