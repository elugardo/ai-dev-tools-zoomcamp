# Incident 20260927-000317-order-tracker-5xx-responses: Order Tracker 5xx responses

You are the automatic first responder for **Order Tracker**, the FastAPI app in
`C:\Projects\ai-dev-tools-zoomcamp\module4` (your working directory). A Grafana alert fired, and the evidence
below was collected for you. Work from this evidence and the repository only;
do not guess.

## Your task

1. Decide whether this is a real incident. If the alert is a test, or the
   evidence shows no server errors, say so and stop. Do not change any file.
2. If it is real: find the root cause of the failing requests from the error
   logs (they include the traceback) and the failed traces, and confirm it
   against the code in `app/`.
3. Fix it in the smallest correct way. Add or adjust a test in `tests/` that
   would have caught it. Run `uv run --frozen pytest -q` until it passes.
4. Rebuild and restart the app with `docker compose up --build -d --wait app`,
   then verify that the request that was failing now succeeds, for example
   `curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/orders/<id>`.
5. Write `C:\Projects\ai-dev-tools-zoomcamp\module4\incident-response\incidents\20260927-000317-order-tracker-5xx-responses/report.md` (under 40 lines): what users saw, the root
   cause (file and line), what you changed, and how you verified it.

## Rules

- Only edit files under `app/` and `tests/`, plus the report. Do not touch
  `compose.yaml`, `observability/` or `incident-response/`.
- Do not commit. A human reviews the diff.
- Never stop, restart or delete anything other than the `app` service.
- Finish your answer with exactly one line starting with `RESULT:` that sums
  up the outcome, for example
  `RESULT: fixed <cause> in app/main.py; app restarted; verified` or
  `RESULT: no incident (test alert); nothing changed`.

## Alert

- status: firing
- label `alertname`: Order Tracker 5xx responses
- label `endpoint`: /api/orders/{order_id}
- label `grafana_folder`: Order Tracker
- label `http_route`: /api/orders/{order_id}
- label `service`: order-tracker
- label `severity`: critical
- annotation `dashboard`: http://localhost:3000/d/order-tracker/order-tracker
- annotation `description`: Users are getting 5xx responses from /api/orders/{order_id} on the order-tracker app. Check the logs and failed traces linked below, then the recent changes.
- annotation `endpoint`: /api/orders/{order_id}
- annotation `logs`: http://localhost:3000/explore?schemaVersion=1&panes={"a":{"datasource":"loki","queries":[{"refId":"A","expr":"{service_name=\"order-tracker\"} | http_response_status_code >= 500"}],"range":{"from":"now-15m","to":"now"}}}
- annotation `summary`: /api/orders/{order_id} returned {map[%!f(string=):%!f(string=)] 4 %!f(bool=true)} server error(s) in the last 5 minutes
- annotation `traces`: http://localhost:3000/explore?schemaVersion=1&panes={"a":{"datasource":"tempo","queries":[{"refId":"A","queryType":"traceql","query":"{resource.service.name=\"order-tracker\" && status=error}"}],"range":{"from":"now-15m","to":"now"}}}
- annotation `window`: 5m

## Evidence

Collected 2026-09-27T00:03:17Z, window 2026-09-26T23:48:17Z to 2026-09-27T00:03:17Z (15 min).

### App
- `GET /healthz`: 200 {"status":"ok"}
- `GET /api/orders`: 200, 3 orders:
  - `standard-1001` priority=standard status=received created_at=2026-09-26T22:42:53.475613+00:00
  - `standard-1003` priority=standard status=shipped created_at=2026-09-26T22:42:53.475613+00:00
  - `express-1002` priority=express status=preparing created_at=2026-08-31T22:42:53.475613+00:00

### Metrics (Prometheus)
- requests_by_route_and_status:
  - http_request_method=GET, http_response_status_code=200, http_route=/api/orders: 2.01
  - http_request_method=GET, http_response_status_code=500, http_route=/api/orders/{order_id}: 4.12
- server_errors_by_route:
  - http_route=/api/orders/{order_id}: 4.12
- order_lookups_by_outcome:
  - http_response_status_code=500, outcome=error: 4.12

### Server error logs (Loki, `{service_name="order-tracker"} | http_response_status_code >= 500`): 4 in window
- 2026-09-27T00:02:01Z [ERROR] order lookup failed (order_id=express-1002, status=500, trace_id=a128c36a9184c8ef2c151315275cd730)
  ```
  Traceback (most recent call last):
    File "/app/app/main.py", line 125, in get_order
      order = load_order(order_id)
              ^^^^^^^^^^^^^^^^^^^^
    File "/app/app/main.py", line 114, in load_order
      return order_detail(row)
             ^^^^^^^^^^^^^^^^^
    File "/app/app/main.py", line 62, in order_detail
      estimated_at = placed_at.replace(day=placed_at.day + 2)
                     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  ValueError: day is out of range for month
  ```
- 2026-09-27T00:01:59Z [ERROR] order lookup failed (order_id=express-1002, status=500, trace_id=b71e7392b230a9f16ace1de1275d3703)
  ```
  Traceback (most recent call last):
    File "/app/app/main.py", line 125, in get_order
      order = load_order(order_id)
              ^^^^^^^^^^^^^^^^^^^^
    File "/app/app/main.py", line 114, in load_order
      return order_detail(row)
             ^^^^^^^^^^^^^^^^^
    File "/app/app/main.py", line 62, in order_detail
      estimated_at = placed_at.replace(day=placed_at.day + 2)
                     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  ValueError: day is out of range for month
  ```
- 2026-09-27T00:01:57Z [ERROR] order lookup failed (order_id=express-1002, status=500, trace_id=f3c8e53b6a7f2d331fbcd2990cd2620c)
  ```
  Traceback (most recent call last):
    File "/app/app/main.py", line 125, in get_order
      order = load_order(order_id)
              ^^^^^^^^^^^^^^^^^^^^
    File "/app/app/main.py", line 114, in load_order
      return order_detail(row)
             ^^^^^^^^^^^^^^^^^
    File "/app/app/main.py", line 62, in order_detail
      estimated_at = placed_at.replace(day=placed_at.day + 2)
                     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  ValueError: day is out of range for month
  ```
- 2026-09-27T00:00:57Z [ERROR] order lookup failed (order_id=express-1002, status=500, trace_id=63d435261f84c88b7a9b6a15a7fdbd80)
  ```
  Traceback (most recent call last):
    File "/app/app/main.py", line 125, in get_order
      order = load_order(order_id)
              ^^^^^^^^^^^^^^^^^^^^
    File "/app/app/main.py", line 114, in load_order
      return order_detail(row)
             ^^^^^^^^^^^^^^^^^
    File "/app/app/main.py", line 62, in order_detail
      estimated_at = placed_at.replace(day=placed_at.day + 2)
                     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  ValueError: day is out of range for month
  ```

### Failed traces (Tempo, `{resource.service.name="order-tracker" && status=error}`): 4 in window
- trace `a128c36a9184c8ef2c151315275cd730` root `GET /api/orders/{order_id}`
  - span `SELECT orders` [ok] {'db.operation.name': 'SELECT'}
  - span `order.lookup` [ok] {'order.id': 'express-1002'}
    - exception ValueError: day is out of range for month
  - span `GET /api/orders/{order_id} http send` [ok] {'http.response.status_code': '500'}
  - span `GET /api/orders/{order_id} http send` [ok] {}
  - span `GET /api/orders/{order_id}` [ok] {'http.response.status_code': '500', 'http.request.method': 'GET', 'http.route': '/api/orders/{order_id}'}
    - exception ValueError: day is out of range for month
- trace `b71e7392b230a9f16ace1de1275d3703` root `GET /api/orders/{order_id}`
  - span `SELECT orders` [ok] {'db.operation.name': 'SELECT'}
  - span `order.lookup` [ok] {'order.id': 'express-1002'}
    - exception ValueError: day is out of range for month
  - span `GET /api/orders/{order_id} http send` [ok] {'http.response.status_code': '500'}
  - span `GET /api/orders/{order_id} http send` [ok] {}
  - span `GET /api/orders/{order_id}` [ok] {'http.response.status_code': '500', 'http.request.method': 'GET', 'http.route': '/api/orders/{order_id}'}
    - exception ValueError: day is out of range for month
- trace `f3c8e53b6a7f2d331fbcd2990cd2620c` root `GET /api/orders/{order_id}`
  - span `SELECT orders` [ok] {'db.operation.name': 'SELECT'}
  - span `order.lookup` [ok] {'order.id': 'express-1002'}
    - exception ValueError: day is out of range for month
  - span `GET /api/orders/{order_id} http send` [ok] {'http.response.status_code': '500'}
  - span `GET /api/orders/{order_id} http send` [ok] {}
  - span `GET /api/orders/{order_id}` [ok] {'http.response.status_code': '500', 'http.request.method': 'GET', 'http.route': '/api/orders/{order_id}'}
    - exception ValueError: day is out of range for month
- trace `63d435261f84c88b7a9b6a15a7fdbd80` root `GET /api/orders/{order_id}`
  - span `SELECT orders` [ok] {'db.operation.name': 'SELECT'}
  - span `order.lookup` [ok] {'order.id': 'express-1002'}
    - exception ValueError: day is out of range for month
  - span `GET /api/orders/{order_id} http send` [ok] {'http.response.status_code': '500'}
  - span `GET /api/orders/{order_id} http send` [ok] {}
  - span `GET /api/orders/{order_id}` [ok] {'http.response.status_code': '500', 'http.request.method': 'GET', 'http.route': '/api/orders/{order_id}'}
    - exception ValueError: day is out of range for month

### Repository
- directory: `C:\Projects\ai-dev-tools-zoomcamp\module4`
- recent commits:
  - 55b587e Module 4: incident responder that runs Claude Code headless (Q5)
  - 0aac6ba Module 4: Grafana alert on 5xx responses (Q4)
  - a29f1bc Module 4: telemetry pipeline with Collector, Prometheus, Loki, Tempo, Grafana (Q3)
  - 7eeeb01 Module 4: OpenTelemetry metrics, logs and traces for order lookups (Q2)
  - 328fb31 Module 4: Order Tracker starter and AGENTS.md
- working tree: M compose.yaml; ?? incident-response/incidents/20260927-000317-order-tracker-5xx-responses/; ?? observability/grafana/provisioning/alerting/contact-points.yaml

