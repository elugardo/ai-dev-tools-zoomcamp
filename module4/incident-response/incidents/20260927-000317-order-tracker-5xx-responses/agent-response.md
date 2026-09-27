## Incident resolved

**Real incident.** `GET /api/orders/express-1002` was returning 500 on every request (4 failures in the window, all with the same traceback and matching failed traces).

**Root cause** — `app/main.py:62` in `order_detail()`:

```python
estimated_at = placed_at.replace(day=placed_at.day + 2)
```

This bumps the day-of-month number rather than adding two days. The seeded `express-1002` was placed on 2026-08-31, so it requested day 33 and raised `ValueError: day is out of range for month`. Any express order placed in the last two days of a month would fail the same way, on `POST` and `PATCH` too, since they also go through `load_order()`.

**Fix** — one line, using the already-imported `timedelta`:

```python
estimated_at = placed_at + timedelta(days=2)
```

**Tests added** in `tests/test_api.py`:
- `test_seeded_express_order_has_estimated_delivery` — hits the seeded `express-1002` through the API
- `test_express_estimated_delivery_crosses_month_end` — month end, 30-day month, year end, leap-year February, and a mid-month date

**Verification**

| Check | Result |
|---|---|
| `uv run --frozen pytest -q` | 11 passed |
| `docker compose up --build -d --wait app` | app recreated, healthy |
| `GET /api/orders/express-1002` | 200, `estimated_delivery: 2026-09-02` |
| `GET /api/orders/standard-1001` | 200 |
| `GET /api/orders/missing` | 404 |
| `GET /healthz` | 200 |

**Notes**
- Only `app/main.py`, `tests/test_api.py` and the report were edited. Nothing was committed; the diff is ready for review.
- Only the `app` service was restarted. The pre-existing changes to `compose.yaml` and `contact-points.yaml` were not mine and were left untouched.
- The alert will stay firing until the old errors age out of its 5 minute window, then return to Normal on its own.
- Report written to `incident-response/incidents/20260927-000317-order-tracker-5xx-responses/report.md`.
- Unrelated to this incident: the Atlassian, Google Drive, Microsoft 365 and Slack connectors need authorizing in your claude.ai connector settings before they can be used. None were needed here.

RESULT: fixed month-end day overflow in express delivery estimate in app/main.py; app restarted; verified GET /api/orders/express-1002 returns 200
