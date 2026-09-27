# Incident 20260927-000317: Order Tracker 5xx responses

**Status:** fixed, app restarted, verified. Not committed (awaiting human review).

## What users saw

`GET /api/orders/express-1002` returned `500 Internal Server Error` every time
(4 failures between 00:00:57Z and 00:02:01Z on 2026-09-27). Other routes were
healthy: `/healthz` and `/api/orders` returned 200. Any express order placed on
the last two days of a month would have failed the same way, including on
`POST /api/orders` and `PATCH /api/orders/{id}`, which also call `load_order()`.

## Root cause

`app/main.py:62`, in `order_detail()`:

    estimated_at = placed_at.replace(day=placed_at.day + 2)

This adds 2 to the day-of-month number instead of adding two days. The seeded
order `express-1002` has `created_at=2026-08-31`, so it asked for day 33 and
raised `ValueError: day is out of range for month`. All 4 error logs and all 4
failed traces show this same exception on the `order.lookup` span.

## What changed

- `app/main.py:62`: `estimated_at = placed_at + timedelta(days=2)`
  (`timedelta` was already imported). One-line change, no behaviour change for
  dates that worked before.
- `tests/test_api.py`: two tests added.
  - `test_seeded_express_order_has_estimated_delivery`: `GET` on the seeded
    `express-1002` returns 200 with the expected `estimated_delivery`.
  - `test_express_estimated_delivery_crosses_month_end`: `order_detail()` over
    month end, 30-day month, year end, leap-year February and a mid-month date.

## Verification

- `uv run --frozen pytest -q`: 11 passed.
- `docker compose up --build -d --wait app`: app recreated and healthy. Only
  the `app` service was touched.
- Against the live app after the restart:
  - `GET /api/orders/express-1002` -> 200, `estimated_delivery: 2026-09-02`
  - `GET /api/orders/standard-1001` -> 200
  - `GET /api/orders/missing` -> 404
  - `GET /healthz` -> 200

## Follow-up

The alert counts 5xx over a 5 minute window, so it stays firing until the old
errors age out, then returns to Normal on its own.
