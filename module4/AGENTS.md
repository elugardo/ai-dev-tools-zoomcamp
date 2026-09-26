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
docker compose logs app                  # app output, including console telemetry (step 2)
docker compose down                      # stop; add -v to delete the orders volume too
uv sync                                  # create .venv and install deps (incl. dev)
uv run --frozen pytest -q                # test suite
```

## The app

```
module4/
├── app/main.py         FastAPI: /, /healthz, /api/orders (GET, POST), /api/orders/{id} (GET, PATCH)
├── static/index.html   the web page (create an order, check its status)
├── tests/test_api.py   TestClient tests on a temp SQLite file
├── compose.yaml        the `app` service, orders in the `orders` volume
└── Dockerfile          python:3.12-slim, uv sync --frozen --no-dev
```

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
