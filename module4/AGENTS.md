# AGENTS.md

**Order Tracker** is a small app for creating orders and checking their
status, used for the AI Dev Tools Zoomcamp homework 4 (DevOps and
observability). The homework adds OpenTelemetry instrumentation, a telemetry
pipeline (Collector, Prometheus, Loki, Tempo, Grafana), a Grafana alert, and an
incident responder that starts a headless coding agent when the alert fires.

The code is Alexey Grigorev's starter,
[`alexeygrigorev/order-tracker`](https://github.com/alexeygrigorev/order-tracker),
copied in at commit `72de447`. It is a plain folder in this repo, not a git
submodule. The homework is
[`04-devops/homework.md`](https://github.com/DataTalksClub/ai-dev-tools-zoomcamp/blob/main/cohorts/2026/homework/04-devops/homework.md).
[`README.md`](README.md) is the starter's own guide.

## Homework steps

1. Run the app with Compose and check `/healthz`.
2. Instrument order lookups with OpenTelemetry metrics, logs and traces. The
   request metric carries the route and HTTP status code. Export to the
   console first (`docker compose logs app`).
3. Add an OpenTelemetry Collector, Prometheus, Loki, Tempo and Grafana to
   Compose; route all three signals through the Collector; a Grafana dashboard
   for request counts and errors. All configuration lives in the repo.
4. A Grafana alert on `5xx` responses with the endpoint, time window and
   dashboard link in it, that also copes with periods of no `5xx` at all.
5. `incident-response/`: a service on port `8001` that receives Grafana
   webhooks at `POST /alerts`, saves what is needed to understand the problem
   (endpoint, logs, traces), and starts the coding agent headless.
6. Wire the Grafana alert to the responder and run the real incident:
   `GET /api/orders/express-1002` fails, the alert fires, the responder starts
   the agent, the agent fixes the bug, the app is restarted and verified.

## Commands

Run from `module4/`:

```
docker compose up --build -d --wait      # app on http://localhost:8000 (ORDER_TRACKER_PORT to change)
docker compose logs app                  # app output (OTEL_EXPORTER=console prints telemetry here)
docker compose down                      # stop; add -v to delete the data volumes too
uv sync                                  # create .venv and install deps (incl. dev)
uv run --frozen pytest -q                # test suite
```

The stack, after step 3 (all on 127.0.0.1):

| Service | URL | Notes |
|---|---|---|
| app | http://localhost:8000 | sends OTLP to `otel-collector:4318` |
| Grafana | http://localhost:3000 | anonymous admin, no login; dashboard "Order Tracker" |
| Prometheus | http://localhost:9090 | scrapes the Collector's `:8889` every 5 s |
| Loki | http://localhost:3100 | OTLP ingest at `/otlp`; labels `service_name`, `service_version` |
| Tempo | http://localhost:3200 | OTLP gRPC on 4317 (inside the network) |

Config lives in `observability/`: `otel-collector.yaml`, `prometheus.yaml`,
`loki.yaml`, `tempo.yaml`, and `grafana/` (provisioned `datasources`,
`dashboards` and, from step 4, `alerting`; the dashboard JSON is
`grafana/dashboards/order-tracker.json`). Compose bind-mounts them read-only;
after editing one, `docker compose restart <service>`.

Checking a signal without the UI:

```
curl -s http://localhost:9090/api/v1/query --data-urlencode 'query=order_lookups_total'
curl -s -G http://localhost:3100/loki/api/v1/query_range --data-urlencode 'query={service_name="order-tracker"} | order_id="standard-1002"'
curl -s -G http://localhost:3200/api/search --data-urlencode 'q={span.order.id="standard-1002"}'
curl -s http://localhost:3200/api/traces/<trace_id>
```

- **Metric names in Prometheus:** `http_server_request_duration_seconds_{count,sum,bucket}`
  with labels `http_route`, `http_request_method`, `http_response_status_code`,
  `service_name`, `service_version`; and `order_lookups_total` with
  `http_response_status_code` and `outcome`.
- **Log fields in Loki:** everything but the two stream labels is structured
  metadata with dots turned into underscores: `trace_id`, `span_id`,
  `order_id`, `http_response_status_code`, `severity_text`.
- **The alert** (step 4) is `grafana/provisioning/alerting/rules.yaml`, rule
  uid `order-tracker-5xx`: per-route `increase()` of 5xx responses over 5 m,
  evaluated every 10 s, Pending for 30 s, then Firing. Quiet periods are
  Normal (`or vector(0)` + `noDataState: OK`). Its labels carry `endpoint`
  and its annotations `summary`, `endpoint`, `window`, `dashboard`, `logs` and
  `traces` (Explore links). Check its state with
  `curl -s http://localhost:3000/api/prometheus/grafana/api/v1/rules`.
  Gotchas: Grafana expands `$VAR` in rule **labels** (not annotations), so
  the label template is written `$$labels` while annotations use `$labels`;
  `__dashboardUid__` must come with `__panelId__`; a provisioning error is
  fatal and Grafana exits.
- **First-error visibility.** Prometheus runs with
  `--enable-feature=created-timestamp-zero-ingestion` and the Collector's
  exporter has `enable_open_metrics: true`, so `increase()` sees the very
  first 5xx on a new series (verified: a new series shows `increase > 0` on
  its first sample) instead of needing a second request.
- **Versions are pinned** in `compose.yaml` (Collector 0.161, Prometheus
  v3.15, Loki 3.7, Tempo 3.0, Grafana 13.2). Tempo 3 has no `usage_report`
  block, and the Collector's exporter types are `otlp_http` / `otlp_grpc`
  with `resource_constant_labels` for resource-attribute labels.

The responder (step 5), on the host, from `module4/`:

```
uv run python incident-response/responder.py          # POST /alerts on 0.0.0.0:8001
uv run --frozen pytest -q tests/test_responder.py     # dry-run tests, no agent
curl -s http://localhost:8001/incidents               # state and RESULT line per incident
```

- Read [`incident-response/README.md`](incident-response/README.md) first.
  `responder.py` is the FastAPI service, `evidence.py` the read-only
  collector, `agent.py` runs `claude -p`, `prompt-template.md` is the brief.
  Each incident is a folder under `incident-response/incidents/` and is
  committed (the homework asks for the evidence); it holds no secrets.
- It runs **on the host**, not in Compose: the agent needs the logged-in
  `claude` CLI, the repo and the Docker CLI. Grafana reaches it as
  `http://host.docker.internal:8001/alerts`.
- The `claude` on PATH must be recent (`claude update`; 2.1.29 failed with
  "version too old" for the default model).
- The agent's answer ends with a `RESULT:` line; `status.json` carries state,
  duration, turns, cost and that last line.
- **The webhook (step 6)** is provisioned in
  `observability/grafana/provisioning/alerting/contact-points.yaml`: contact
  point `incident-responder` → `http://host.docker.internal:8001/alerts`, and
  a root notification policy grouped by `alertname, endpoint` with
  `group_wait: 10s`, `group_interval: 30s`, `repeat_interval: 5m`. Grafana's
  resolved notification lands in the incident as `resolved.json`.
- **Timing of the real incident:** 500 at t=0, alert Pending at ~20 s,
  Firing at ~50 s, webhook received at ~80 s, agent done after 212 s
  (17 turns). The alert returns to Normal on its own once the 5-minute window
  has no more 5xx.

## The app

```
module4/
├── app/main.py         FastAPI: /, /healthz, /api/orders (GET, POST), /api/orders/{id} (GET, PATCH)
├── static/index.html   the web page (create an order, check its status)
├── tests/test_api.py   TestClient tests on a temp SQLite file
├── compose.yaml        the `app` service, orders in the `orders` volume
└── Dockerfile          python:3.12-slim, uv sync --frozen --no-dev
```

- **Telemetry** is set up in `app/telemetry.py` (step 2), called once from
  `main.py`. `OTEL_EXPORTER` selects `console` (default), `otlp`
  (Collector at `OTEL_EXPORTER_OTLP_ENDPOINT`) or `none` (tests, via
  `tests/conftest.py`). Signals: the FastAPI histogram
  `http.server.request.duration` (attributes `http.route`,
  `http.request.method`, `http.response.status_code`), the counter
  `order.lookups` (route, status code, `outcome` = found / not_found /
  error), one span per request plus an `order.lookup` span with the order id
  and a `SELECT orders` span for its query, and every `logging` record with
  trace and span ids. (sqlite3 auto-instrumentation does not work here: the
  app uses `connection.execute()`, whose cursor is created in C.)
  `/healthz` is excluded. The stable HTTP semantic conventions are opted in
  (`OTEL_SEMCONV_STABILITY_OPT_IN=http`) before the instrumentation imports,
  so keep the `os.environ.setdefault` at the top of `telemetry.py`.
- **SQLite** at `ORDER_DB_PATH` (`/data/orders.db` in the container). Three
  orders are seeded into an empty database: `standard-1001`, `express-1002`
  and `standard-1003`. Only run one app container at a time.
- **Express orders** get an `estimated_delivery` computed in `order_detail()`
  from `created_at`.

## Rules

- **The `express-1002` incident is done (step 6).** The starter shipped
  `order_detail()` computing the express delivery estimate with
  `placed_at.replace(day=placed_at.day + 2)`, which raised `ValueError: day is
  out of range for month` for an order placed on the last two days of a month.
  The headless agent diagnosed it from the alert's evidence and replaced it
  with `placed_at + timedelta(days=2)`, with tests. See
  `incident-response/incidents/20260927-000317-order-tracker-5xx-responses/`.
  Do not reintroduce the bug to "re-run" the exercise; the incident folder is
  the record.
- **Keep the starter's behaviour.** Instrumentation must not change any
  response. The starter tests must keep passing.
- **Signals must not leak secrets or bloat cardinality.** No request headers
  or bodies in spans or logs; metric attributes are the route template, method
  and status code, never the order id.
- **Dependencies** go in `pyproject.toml` and `uv.lock` via `uv add` (the
  Dockerfile installs with `--frozen`, so an out-of-date lock fails the
  build). Do not add one without asking.
- **Everything is configuration in the repo**: Collector, Prometheus, Loki,
  Tempo and Grafana provisioning (datasources, dashboards, alert rules, contact
  points) are files under `module4/`, mounted by Compose. Nothing is set up by
  hand in the Grafana UI.
- **Never commit** `.venv/`, `data/`, `.env`, or anything the responder
  captures that contains credentials.
