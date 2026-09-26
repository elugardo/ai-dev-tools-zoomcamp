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
- **Versions are pinned** in `compose.yaml` (Collector 0.161, Prometheus
  v3.15, Loki 3.7, Tempo 3.0, Grafana 13.2). Tempo 3 has no `usage_report`
  block, and the Collector's exporter types are `otlp_http` / `otlp_grpc`
  with `resource_constant_labels` for resource-attribute labels.

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

- **Do not fix the `express-1002` failure ahead of step 6.** `GET
  /api/orders/express-1002` returning a 500 *is* the incident: the alert must
  catch it and the responder's headless agent must diagnose and fix it from the
  telemetry. Until then, leave `order_detail()` as it is.
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
