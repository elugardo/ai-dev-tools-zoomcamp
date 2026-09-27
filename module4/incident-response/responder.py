"""Incident responder: Grafana webhook -> evidence packet -> headless agent.

    uv run python incident-response/responder.py      # from module4/

Listens on port 8001. ``POST /alerts`` takes Grafana's webhook payload, answers
202 at once, and for each firing alert creates ``incidents/<id>/`` with:

    webhook.json        the payload as received
    alert.json          the one alert this incident is about
    evidence.json/.md   app state, metrics, 5xx logs with tracebacks, failed
                        traces, recent commits (see evidence.py)
    prompt.md           the brief handed to the agent
    agent-output.json   Claude Code's raw JSON output
    agent-response.md   its final answer; the last line starts with RESULT:
    status.json         running / done / error, timing, cost, last line
    report.md           written by the agent when it fixed something

A repeated notification for an alert that is still being handled is appended
to that incident's ``alerts.jsonl`` instead of starting another agent.
``RESPONDER_DRY_RUN=1`` collects evidence but does not start the agent.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import BackgroundTasks, FastAPI

import agent
import evidence

LOGGER = logging.getLogger("responder")
HERE = Path(__file__).resolve().parent
REPO_DIR = HERE.parent

app = FastAPI(title="Order Tracker incident responder")
_running: dict[str, str] = {}
_lock = threading.Lock()


def incidents_dir() -> Path:
    return Path(os.getenv("INCIDENTS_DIR", HERE / "incidents"))


def dry_run() -> bool:
    return os.getenv("RESPONDER_DRY_RUN") == "1"


@app.get("/healthz")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/incidents")
def list_incidents() -> list[dict[str, Any]]:
    out = []
    for path in sorted(incidents_dir().glob("*/")):
        status = read_json(path / "status.json") or {}
        out.append({"incident": path.name, "state": status.get("state", "new"), "last_line": status.get("last_line")})
    return out


@app.post("/alerts", status_code=202)
def alerts(payload: dict[str, Any], background: BackgroundTasks) -> dict[str, Any]:
    handled = []
    for alert in payload.get("alerts") or []:
        fingerprint = alert_fingerprint(alert)
        if alert.get("status") == "resolved":
            handled.append({"fingerprint": fingerprint, "action": record_resolved(fingerprint, alert)})
            continue
        with _lock:
            running = _running.get(fingerprint)
            if running:
                append_jsonl(incidents_dir() / running / "alerts.jsonl", alert)
                handled.append({"fingerprint": fingerprint, "incident": running, "action": "already handling"})
                continue
            incident_id = new_incident_id(alert)
            _running[fingerprint] = incident_id
        incident_dir = incidents_dir() / incident_id
        incident_dir.mkdir(parents=True, exist_ok=True)
        write_json(incident_dir / "webhook.json", payload)
        write_json(incident_dir / "alert.json", alert)
        LOGGER.info("incident %s opened for alert %s", incident_id, alert_title(alert))
        background.add_task(handle_incident, incident_id, alert, fingerprint)
        handled.append({"fingerprint": fingerprint, "incident": incident_id, "action": "started"})
    return {"received": len(payload.get("alerts") or []), "handled": handled}


def handle_incident(incident_id: str, alert: dict[str, Any], fingerprint: str) -> None:
    incident_dir = incidents_dir() / incident_id
    try:
        packet = evidence.collect(alert)
        write_json(incident_dir / "evidence.json", packet)
        evidence_md = evidence.to_markdown(packet)
        (incident_dir / "evidence.md").write_text(evidence_md, encoding="utf-8")
        (incident_dir / "prompt.md").write_text(build_prompt(incident_id, incident_dir, alert, evidence_md), encoding="utf-8")
        LOGGER.info("incident %s: evidence collected (%d error logs, %d failed traces)",
                    incident_id,
                    len(packet.get("logs", {}).get("server_error_logs", [])),
                    len(packet.get("traces", {}).get("failed_traces", [])))
        if dry_run():
            status = {"state": "skipped", "reason": "RESPONDER_DRY_RUN=1"}
        else:
            LOGGER.info("incident %s: starting the agent", incident_id)
            status = agent.run(incident_dir, REPO_DIR)
            LOGGER.info("incident %s: agent %s: %s", incident_id, status.get("state"), status.get("last_line"))
        write_json(incident_dir / "status.json", status)
    except Exception as exc:  # noqa: BLE001 - always leave a status behind
        LOGGER.exception("incident %s failed", incident_id)
        write_json(incident_dir / "status.json", {"state": "error", "error": f"{type(exc).__name__}: {exc}"})
    finally:
        with _lock:
            _running.pop(fingerprint, None)


def build_prompt(incident_id: str, incident_dir: Path, alert: dict[str, Any], evidence_md: str) -> str:
    template = (HERE / "prompt-template.md").read_text(encoding="utf-8")
    alert_lines = [f"- status: {alert.get('status')}"]
    for section in ("labels", "annotations"):
        for key, value in (alert.get(section) or {}).items():
            alert_lines.append(f"- {section[:-1]} `{key}`: {value}")
    return template.format(
        incident_id=incident_id,
        alert_title=alert_title(alert),
        repo_dir=REPO_DIR,
        incident_dir=incident_dir,
        alert_md="\n".join(alert_lines),
        evidence_md=evidence_md,
    )


def record_resolved(fingerprint: str, alert: dict[str, Any]) -> str:
    matches = sorted(
        path
        for path in incidents_dir().glob("*/")
        if (saved := read_json(path / "alert.json")) and alert_fingerprint(saved) == fingerprint
    )
    if not matches:
        return "resolved (no matching incident)"
    write_json(matches[-1] / "resolved.json", alert)
    return f"resolved ({matches[-1].name})"


# --- helpers -----------------------------------------------------------------


def alert_title(alert: dict[str, Any]) -> str:
    return (alert.get("labels") or {}).get("alertname") or "alert"


def alert_fingerprint(alert: dict[str, Any]) -> str:
    if alert.get("fingerprint"):
        return str(alert["fingerprint"])
    labels = json.dumps(alert.get("labels") or {}, sort_keys=True)
    return hashlib.sha256(labels.encode()).hexdigest()[:16]


def new_incident_id(alert: dict[str, Any]) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", alert_title(alert).lower()).strip("-") or "alert"
    return f"{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{slug}"


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def append_jsonl(path: Path, data: Any) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(data, default=str) + "\n")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # 0.0.0.0 so Grafana, inside Docker, can reach it as host.docker.internal.
    uvicorn.run(app, host=os.getenv("RESPONDER_HOST", "0.0.0.0"), port=int(os.getenv("RESPONDER_PORT", "8001")))
