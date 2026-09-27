import importlib
import json

import pytest
from fastapi.testclient import TestClient

TEST_ALERT = {
    "alerts": [
        {
            "status": "firing",
            "labels": {"alertname": "ResponderTest", "test": "true"},
            "annotations": {"summary": "Test notification; no incident to fix"},
        }
    ]
}


@pytest.fixture
def responder(tmp_path, monkeypatch):
    # Evidence sources point at a closed port, so every one of them fails
    # fast and is recorded; the agent is not started.
    monkeypatch.setenv("RESPONDER_DRY_RUN", "1")
    monkeypatch.setenv("INCIDENTS_DIR", str(tmp_path / "incidents"))
    for name in ("APP_URL", "PROMETHEUS_URL", "LOKI_URL", "TEMPO_URL"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    import responder as module

    module = importlib.reload(module)
    with TestClient(module.app) as client:
        yield client, tmp_path / "incidents"


def test_alert_opens_an_incident_with_evidence_and_brief(responder):
    client, incidents = responder
    response = client.post("/alerts", json=TEST_ALERT)
    assert response.status_code == 202
    body = response.json()
    assert body["received"] == 1
    assert body["handled"][0]["action"] == "started"

    incident = incidents / body["handled"][0]["incident"]
    assert incident.name.endswith("-respondertest")
    for name in ("webhook.json", "alert.json", "evidence.json", "evidence.md", "prompt.md", "status.json"):
        assert (incident / name).exists(), name

    evidence = json.loads((incident / "evidence.json").read_text(encoding="utf-8"))
    assert evidence["alert"]["labels"]["alertname"] == "ResponderTest"
    # The stack was unreachable: recorded, not raised.
    assert set(evidence["errors"]) >= {"app", "metrics", "logs", "traces"}
    assert "recent_commits" in evidence["repo"]

    prompt = (incident / "prompt.md").read_text(encoding="utf-8")
    assert "ResponderTest" in prompt
    assert "Test notification; no incident to fix" in prompt
    assert "RESULT:" in prompt

    status = json.loads((incident / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "skipped"
    assert client.get("/incidents").json()[0]["state"] == "skipped"


def test_resolved_alert_does_not_open_an_incident(responder):
    client, incidents = responder
    resolved = {"alerts": [{**TEST_ALERT["alerts"][0], "status": "resolved"}]}
    response = client.post("/alerts", json=resolved)
    assert response.status_code == 202
    assert response.json()["handled"][0]["action"].startswith("resolved")
    assert not incidents.exists() or not any(incidents.iterdir())
