# Incident responder

A small service that turns a Grafana alert into an investigated incident.
Grafana posts its webhook to `POST /alerts`; the responder saves the alert,
collects an evidence packet from the telemetry stack, and starts Claude Code
headless with a written brief. Everything lands in `incidents/<id>/`.

```
Grafana alert ──webhook──▶ responder (:8001) ──▶ incidents/<id>/
                                │                   alert.json, evidence.{json,md}, prompt.md
                                └──▶ claude -p ──▶  agent-response.md (last line: RESULT: ...), report.md
```

## Run it

From `module4/`, with the Compose stack up and the `claude` CLI logged in:

```
uv run python incident-response/responder.py
```

It listens on `0.0.0.0:8001` so that Grafana, inside Docker, can reach it as
`http://host.docker.internal:8001/alerts`. Send a test alert:

```
curl -X POST http://localhost:8001/alerts -H 'Content-Type: application/json' \
  -d '{"alerts":[{"status":"firing","labels":{"alertname":"ResponderTest","test":"true"},"annotations":{"summary":"Test notification; no incident to fix"}}]}'
```

Then watch `incidents/<id>/status.json` (or `GET /incidents`) until the state
is `done`, and read `agent-response.md`.

## What gets collected

[`evidence.py`](evidence.py) issues a fixed set of read-only queries over the
last 15 minutes and records any source that fails instead of crashing:

| Source | What | Query |
|---|---|---|
| app | `/healthz` and the order list | HTTP GET |
| Prometheus | requests by route/status, 5xx by route, lookups by outcome | `increase(...)` over the window |
| Loki | 5xx log lines with `trace_id`, `order_id` and the traceback | `{service_name="order-tracker"} \| http_response_status_code >= 500` |
| Tempo | failed traces with their spans and exception events | `{resource.service.name="order-tracker" && status=error}` |
| repo | recent commits and working tree | `git log`, `git status` |

## The agent

[`agent.py`](agent.py) runs `claude -p` with the brief in `prompt.md`
(rendered from [`prompt-template.md`](prompt-template.md)), the repository as
working directory, `--permission-mode acceptEdits`, `--max-turns 40`, and a
tool allowlist: read/search/edit files, `uv run ...`, `docker compose ...`,
`curl ...` and read-only `git`. The brief tells it to stop on a test alert,
otherwise to fix the root cause under `app/`, add a test, rebuild only the
`app` service, verify, and write `report.md`; its answer ends with one
`RESULT:` line.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `RESPONDER_PORT` / `RESPONDER_HOST` | `8001` / `0.0.0.0` | where to listen |
| `INCIDENTS_DIR` | `incident-response/incidents` | where incidents are written |
| `APP_URL`, `PROMETHEUS_URL`, `LOKI_URL`, `TEMPO_URL` | `http://localhost:8000/9090/3100/3200` | evidence sources |
| `EVIDENCE_WINDOW_MINUTES` | `15` | how far back to look |
| `CLAUDE_BIN` | `claude` | the CLI to run |
| `AGENT_MODEL` | CLI default | `--model` for the agent |
| `AGENT_MAX_TURNS`, `AGENT_TIMEOUT_SECONDS` | `40`, `1200` | bounds on the agent |
| `AGENT_ALLOWED_TOOLS` | see `agent.py` | the tool allowlist |
| `RESPONDER_DRY_RUN` | unset | `1` collects evidence but does not start the agent |

## Tests

`uv run --frozen pytest -q tests/test_responder.py` runs the responder in dry
run against unreachable stores and checks that an alert still produces a
complete incident folder.
