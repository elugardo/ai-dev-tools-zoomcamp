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
