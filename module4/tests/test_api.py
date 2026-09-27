from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import main


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "orders.db")
    with TestClient(main.app) as test_client:
        yield test_client


def test_health_and_seeded_orders(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    orders = client.get("/api/orders").json()
    assert len(orders) == 3
    assert {order["priority"] for order in orders} == {"standard", "express"}


def test_create_and_update_order(client):
    response = client.post(
        "/api/orders",
        json={"customer": "Taylor", "item": "Mug", "priority": "standard"},
    )
    assert response.status_code == 201
    order_id = response.json()["id"]
    assert client.get(f"/api/orders/{order_id}").json()["status"] == "received"
    updated = client.patch(f"/api/orders/{order_id}", json={"status": "shipped"})
    assert updated.status_code == 200
    assert updated.json()["status"] == "shipped"


def test_seeded_express_order_has_estimated_delivery(client):
    # express-1002 is seeded on the last day of the previous month.
    response = client.get("/api/orders/express-1002")
    assert response.status_code == 200
    order = response.json()
    placed_at = datetime.fromisoformat(order["created_at"])
    assert order["estimated_delivery"] == (placed_at + timedelta(days=2)).date().isoformat()


@pytest.mark.parametrize(
    ("created_at", "estimated_delivery"),
    [
        ("2026-08-31T22:42:53+00:00", "2026-09-02"),
        ("2026-09-30T10:00:00+00:00", "2026-10-02"),
        ("2026-12-31T10:00:00+00:00", "2027-01-02"),
        ("2028-02-28T10:00:00+00:00", "2028-03-01"),
        ("2026-09-10T10:00:00+00:00", "2026-09-12"),
    ],
)
def test_express_estimated_delivery_crosses_month_end(created_at, estimated_delivery):
    order = main.order_detail({"id": "x", "priority": "express", "created_at": created_at})
    assert order["estimated_delivery"] == estimated_delivery


def test_missing_order(client):
    assert client.get("/api/orders/missing").status_code == 404
