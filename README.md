# FDE Assessment

Four independent gateway / infrastructure tasks in Python. Each task lives in its
own package with its own README; this file covers setup and the pieces shared
across all four.

| Task | Summary | Details |
| --- | --- | --- |
| 1 | Custom MCP server (STDIO) with two tools and strict schema validation | [`task1_mcp_server/`](task1_mcp_server/README.md) |
| 2 | MCP security gateway: JSON-RPC reverse proxy with role-based tool authorization | [`task2_mcp_gateway/`](task2_mcp_gateway/README.md) |
| 3 | LLM streaming guardrail: SSE proxy that redacts PII mid-stream | [`task3_llm_gateway/`](task3_llm_gateway/README.md) |
| 4 | Token-aware rate limiter + primary/fallback model router | [`task4_resilient_gateway/`](task4_resilient_gateway/README.md) |

## Requirements

- Python 3.11+
- Dependencies in `requirements.txt`

## Setup

### Windows (PowerShell)

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

## Tests

```bash
pytest -v          # full suite
ruff check .       # lint
```

Per-task test commands are in each task's README. Tasks 2-4 mock every network
dependency, so no external service is needed to run the suite.

## Project structure

```text
quilr-fde-assessment/
├── task1_mcp_server/
│   ├── server.py            # MCP server, tools, validation middleware
│   └── README.md
├── task2_mcp_gateway/
│   ├── gateway.py           # auth + JSON-RPC reverse proxy
│   ├── downstream.py        # mock downstream MCP server
│   └── README.md
├── task3_llm_gateway/
│   ├── gateway.py           # async SSE proxy
│   ├── redaction.py         # streaming PII redactor
│   ├── provider.py          # mock streaming LLM provider
│   └── README.md
├── task4_resilient_gateway/
│   ├── gateway.py           # rate-limit check + provider failover
│   ├── rate_limit.py        # SQLite sliding-window limiter
│   ├── tokens.py            # tiktoken request tokenizer
│   └── README.md
├── tests/
├── requirements.txt
└── README.md
```

## Shared conventions

- **Logging**: every service logs to **stderr** at `INFO`; in task 1 this is
  load-bearing, since stdout carries the MCP JSON-RPC protocol stream.
- **Ports**: the task 2, 3, and 4 gateways are all documented on `--port 8000`.
  Run one task at a time, or pass a different port.
- **Mock upstreams**: tasks 2 and 3 ship a mock upstream; task 4's upstreams are
  only stood up by the test suite.
