"""API integration test against a running Agent Relay.

Unlike `test_agent_relay.py`, this does not import the app or touch the
database directly: it talks HTTP to a live server, so the same test covers the
local dev server, the Docker container, the Compose stack and the kind
deployment. Point it elsewhere with RELAY_BASE_URL (default
http://127.0.0.1:8000). It only adds rows (fresh agents and one task per run),
so it is safe to run against a database you want to keep.

If nothing answers at RELAY_BASE_URL the test is skipped, not failed, so a plain
`uv run pytest` still works without a server running.
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest

BASE_URL = os.getenv("RELAY_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


@pytest.fixture
def api():
    with httpx.Client(base_url=BASE_URL, timeout=15) as client:
        try:
            ready = client.get("/ready")
        except httpx.TransportError as exc:
            pytest.skip(f"no Agent Relay at {BASE_URL}: {exc}")
        assert ready.status_code == 200, f"{BASE_URL}/ready answered {ready.status_code}: {ready.text}"
        yield client


def register(api: httpx.Client, name: str) -> tuple[dict, dict[str, str]]:
    response = api.post("/api/v1/agents", json={"name": name})
    assert response.status_code == 201, response.text
    data = response.json()
    return data, {"Authorization": f"Bearer {data['token']}"}


def test_two_agents_exchange_a_task_and_its_result(api):
    """SPEC.md acceptance scenario 1: one agent sends a task; the other claims
    and completes it; the sender reads the result."""
    run = uuid.uuid4().hex[:8]
    sender, sender_headers = register(api, f"it-sender-{run}")
    recipient, recipient_headers = register(api, f"it-uppercase-{run}")

    sent = api.post(
        "/api/v1/tasks",
        headers={**sender_headers, "Idempotency-Key": f"it-{run}"},
        json={"to": recipient["agent_id"], "input": f"hello relay {run}"},
    )
    assert sent.status_code == 201, sent.text
    task_id = sent.json()["task_id"]
    assert sent.json()["status"] == "queued"

    claim = api.post(
        "/api/v1/tasks/claim",
        headers=recipient_headers,
        json={"worker_id": f"it-worker-{run}", "wait_seconds": 5},
    )
    assert claim.status_code == 200, claim.text
    claimed = claim.json()
    assert claimed["task_id"] == task_id
    assert claimed["from"] == sender["agent_id"]
    assert claimed["input"] == f"hello relay {run}"
    assert claimed["attempt"] == 1

    in_flight = api.get(f"/api/v1/tasks/{task_id}", headers=sender_headers).json()
    assert in_flight["status"] == "processing"
    assert in_flight["output"] is None

    complete = api.post(
        f"/api/v1/tasks/{task_id}/complete",
        headers=recipient_headers,
        json={"claim_token": claimed["claim_token"], "output": f"HELLO RELAY {run}"},
    )
    assert complete.status_code == 200, complete.text

    result = api.get(f"/api/v1/tasks/{task_id}", headers=sender_headers)
    assert result.status_code == 200, result.text
    task = result.json()
    assert task["status"] == "completed"
    assert task["output"] == f"HELLO RELAY {run}"
    assert task["error"] is None
    assert task["from"] == sender["agent_id"]
    assert task["to"] == recipient["agent_id"]
    assert task["attempt_count"] == 1
    assert task["finished_at"] is not None

    sent_list = api.get("/api/v1/tasks", headers=sender_headers, params={"direction": "sent"}).json()
    assert [item["task_id"] for item in sent_list["items"]] == [task_id]

    attempts = api.get(f"/api/v1/tasks/{task_id}/attempts", headers=sender_headers).json()["items"]
    assert len(attempts) == 1
    assert attempts[0]["outcome"] == "completed"
    assert attempts[0]["worker_id"] == f"it-worker-{run}"
    assert "claim_token" not in attempts[0]

    # A third agent is not a participant: the task and its result stay hidden.
    _outsider, outsider_headers = register(api, f"it-outsider-{run}")
    assert api.get(f"/api/v1/tasks/{task_id}", headers=outsider_headers).status_code == 404
