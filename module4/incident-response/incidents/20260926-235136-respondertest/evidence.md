Collected 2026-09-26T23:51:36Z, window 2026-09-26T23:36:36Z to 2026-09-26T23:51:36Z (15 min).

### App
- `GET /healthz`: 200 {"status":"ok"}
- `GET /api/orders`: 200, 3 orders:
  - `standard-1001` priority=standard status=received created_at=2026-09-26T22:42:53.475613+00:00
  - `standard-1003` priority=standard status=shipped created_at=2026-09-26T22:42:53.475613+00:00
  - `express-1002` priority=express status=preparing created_at=2026-08-31T22:42:53.475613+00:00

### Metrics (Prometheus)
- requests_by_route_and_status:
  - http_request_method=GET, http_response_status_code=200, http_route=/api/orders: 1.0
- server_errors_by_route:
  - (none)
- order_lookups_by_outcome:
  - (none)

### Server error logs (Loki, `{service_name="order-tracker"} | http_response_status_code >= 500`): 0 in window

### Failed traces (Tempo, `{resource.service.name="order-tracker" && status=error}`): 0 in window

### Repository
- directory: `C:\Projects\ai-dev-tools-zoomcamp\module4`
- recent commits:
  - 0aac6ba Module 4: Grafana alert on 5xx responses (Q4)
  - a29f1bc Module 4: telemetry pipeline with Collector, Prometheus, Loki, Tempo, Grafana (Q3)
  - 7eeeb01 Module 4: OpenTelemetry metrics, logs and traces for order lookups (Q2)
  - 328fb31 Module 4: Order Tracker starter and AGENTS.md
- working tree: M pyproject.toml;  M uv.lock; ?? incident-response/; ?? tests/test_responder.py
