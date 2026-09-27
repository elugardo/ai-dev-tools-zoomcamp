"""Collect the evidence an on-call engineer would look at first.

One call produces a JSON document with the app's state, the request and
error metrics (Prometheus), the recent 5xx log lines with their tracebacks
(Loki), the recent failed traces with their exception events (Tempo), and the
repository's recent history. Every source is optional: a failure is recorded
under ``errors`` and never raised, so an alert always yields an incident
folder even when part of the stack is down.

Only read-only queries with fixed shapes are issued.
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

REPO_DIR = Path(__file__).resolve().parent.parent
MAX_LOGS = 30
MAX_TRACES = 5


def settings() -> dict[str, Any]:
    return {
        "app_url": os.getenv("APP_URL", "http://localhost:8000"),
        "prometheus_url": os.getenv("PROMETHEUS_URL", "http://localhost:9090"),
        "loki_url": os.getenv("LOKI_URL", "http://localhost:3100"),
        "tempo_url": os.getenv("TEMPO_URL", "http://localhost:3200"),
        "service": os.getenv("SERVICE_NAME", "order-tracker"),
        "window_minutes": int(os.getenv("EVIDENCE_WINDOW_MINUTES", "15")),
    }


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def collect(alert: dict[str, Any]) -> dict[str, Any]:
    cfg = settings()
    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=cfg["window_minutes"])
    evidence: dict[str, Any] = {
        "collected_at": iso(now),
        "window": {"from": iso(start), "to": iso(now), "minutes": cfg["window_minutes"]},
        "alert": alert,
        "errors": {},
    }
    with httpx.Client(timeout=10) as client:
        for name, fn in (("app", app_state), ("metrics", metrics), ("logs", logs), ("traces", traces)):
            try:
                evidence[name] = fn(client, cfg, start, now)
            except Exception as exc:  # noqa: BLE001 - evidence must never fail as a whole
                evidence["errors"][name] = f"{type(exc).__name__}: {exc}"
    try:
        evidence["repo"] = repo_state()
    except Exception as exc:  # noqa: BLE001
        evidence["errors"]["repo"] = f"{type(exc).__name__}: {exc}"
    return evidence


# --- sources -----------------------------------------------------------------


def app_state(client: httpx.Client, cfg: dict, _start: datetime, _now: datetime) -> dict[str, Any]:
    health = client.get(f"{cfg['app_url']}/healthz")
    orders = client.get(f"{cfg['app_url']}/api/orders")
    return {
        "healthz": {"status_code": health.status_code, "body": health.text[:200]},
        "orders": {"status_code": orders.status_code, "body": orders.json() if orders.status_code == 200 else orders.text[:500]},
    }


def metrics(client: httpx.Client, cfg: dict, _start: datetime, _now: datetime) -> dict[str, Any]:
    window = f"{cfg['window_minutes']}m"
    selector = f'service_name="{cfg["service"]}"'
    queries = {
        "requests_by_route_and_status": (
            f"sum by (http_route, http_request_method, http_response_status_code) "
            f"(increase(http_server_request_duration_seconds_count{{{selector}}}[{window}]))"
        ),
        "server_errors_by_route": (
            f"sum by (http_route) (increase(http_server_request_duration_seconds_count"
            f'{{{selector}, http_response_status_code=~"5.."}}[{window}]))'
        ),
        "order_lookups_by_outcome": (
            f"sum by (http_response_status_code, outcome) (increase(order_lookups_total{{{selector}}}[{window}]))"
        ),
    }
    out: dict[str, Any] = {}
    for name, expr in queries.items():
        response = client.get(f"{cfg['prometheus_url']}/api/v1/query", params={"query": expr})
        response.raise_for_status()
        rows = []
        for item in response.json()["data"]["result"]:
            value = float(item["value"][1])
            if value > 0:
                rows.append({**item["metric"], "value": round(value, 2)})
        out[name] = rows
    return out


def logs(client: httpx.Client, cfg: dict, start: datetime, now: datetime) -> dict[str, Any]:
    query = f'{{service_name="{cfg["service"]}"}} | http_response_status_code >= 500'
    response = client.get(
        f"{cfg['loki_url']}/loki/api/v1/query_range",
        params={
            "query": query,
            "start": str(int(start.timestamp() * 1e9)),
            "end": str(int(now.timestamp() * 1e9)),
            "limit": str(MAX_LOGS),
            "direction": "backward",
        },
    )
    response.raise_for_status()
    entries = []
    for stream in response.json()["data"]["result"]:
        labels = stream["stream"]
        for ts, line in stream["values"]:
            entries.append(
                {
                    "timestamp": iso(datetime.fromtimestamp(int(ts) / 1e9, tz=timezone.utc)),
                    "line": line,
                    "trace_id": labels.get("trace_id"),
                    "order_id": labels.get("order_id"),
                    "status_code": labels.get("http_response_status_code"),
                    "severity": labels.get("severity_text"),
                    "exception_type": labels.get("exception_type"),
                    "exception_message": labels.get("exception_message"),
                    "exception_stacktrace": labels.get("exception_stacktrace"),
                    "code": {k.removeprefix("code_"): v for k, v in labels.items() if k.startswith("code_")},
                }
            )
    entries.sort(key=lambda e: e["timestamp"], reverse=True)
    return {"query": query, "server_error_logs": entries[:MAX_LOGS]}


def traces(client: httpx.Client, cfg: dict, start: datetime, now: datetime) -> dict[str, Any]:
    query = f'{{resource.service.name="{cfg["service"]}" && status=error}}'
    response = client.get(
        f"{cfg['tempo_url']}/api/search",
        params={"q": query, "start": str(int(start.timestamp())), "end": str(int(now.timestamp())), "limit": str(MAX_TRACES)},
    )
    response.raise_for_status()
    found = response.json().get("traces", [])
    summaries = []
    for item in found[:MAX_TRACES]:
        trace_id = item["traceID"]
        detail = client.get(f"{cfg['tempo_url']}/api/traces/{trace_id}")
        detail.raise_for_status()
        summaries.append({"trace_id": trace_id, "root": item.get("rootTraceName"), "spans": summarize_spans(detail.json())})
    return {"query": query, "failed_traces": summaries}


def summarize_spans(trace: dict[str, Any]) -> list[dict[str, Any]]:
    spans = []
    for batch in trace.get("batches", trace.get("resourceSpans", [])):
        for scope in batch.get("scopeSpans", []):
            for span in scope.get("spans", []):
                status = span.get("status", {})
                summary = {
                    "name": span.get("name"),
                    "status": "error" if status.get("code") == 2 else "ok",
                    "attributes": flatten(span.get("attributes", [])),
                    "events": [
                        {"name": event.get("name"), **flatten(event.get("attributes", []))}
                        for event in span.get("events", [])
                    ],
                }
                if not summary["events"]:
                    del summary["events"]
                spans.append(summary)
    return spans


def flatten(attributes: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for attribute in attributes:
        value = attribute.get("value", {})
        out[attribute["key"]] = next(iter(value.values()), None) if value else None
    return out


def repo_state() -> dict[str, Any]:
    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(REPO_DIR), *args], capture_output=True, text=True, timeout=20).stdout.strip()

    return {
        "directory": str(REPO_DIR),
        "recent_commits": git("log", "--oneline", "-8", "--", "."),
        "working_tree": git("status", "--short", "."),
    }


# --- rendering ---------------------------------------------------------------


def to_markdown(evidence: dict[str, Any]) -> str:
    lines: list[str] = []
    add = lines.append
    window = evidence["window"]
    add(f"Collected {evidence['collected_at']}, window {window['from']} to {window['to']} ({window['minutes']} min).")

    if evidence.get("errors"):
        add("")
        add("### Sources that could not be read")
        for name, error in evidence["errors"].items():
            add(f"- {name}: {error}")

    app = evidence.get("app")
    if app:
        add("")
        add("### App")
        add(f"- `GET /healthz`: {app['healthz']['status_code']} {app['healthz']['body']}")
        orders = app["orders"]["body"]
        if isinstance(orders, list):
            add(f"- `GET /api/orders`: {app['orders']['status_code']}, {len(orders)} orders:")
            for order in orders:
                add(f"  - `{order.get('id')}` priority={order.get('priority')} status={order.get('status')} created_at={order.get('created_at')}")
        else:
            add(f"- `GET /api/orders`: {app['orders']['status_code']} {orders}")

    metrics_ = evidence.get("metrics")
    if metrics_:
        add("")
        add("### Metrics (Prometheus)")
        for name, rows in metrics_.items():
            add(f"- {name}:")
            if not rows:
                add("  - (none)")
            for row in rows:
                labels = ", ".join(f"{k}={v}" for k, v in row.items() if k != "value")
                add(f"  - {labels}: {row['value']}")

    logs_ = evidence.get("logs")
    if logs_:
        entries = logs_["server_error_logs"]
        add("")
        add(f"### Server error logs (Loki, `{logs_['query']}`): {len(entries)} in window")
        for entry in entries:
            add(f"- {entry['timestamp']} [{entry['severity']}] {entry['line']}"
                f" (order_id={entry['order_id']}, status={entry['status_code']}, trace_id={entry['trace_id']})")
            if entry.get("exception_stacktrace"):
                add("  ```")
                lines.extend("  " + line for line in entry["exception_stacktrace"].splitlines())
                add("  ```")

    traces_ = evidence.get("traces")
    if traces_:
        found = traces_["failed_traces"]
        add("")
        add(f"### Failed traces (Tempo, `{traces_['query']}`): {len(found)} in window")
        for trace in found:
            add(f"- trace `{trace['trace_id']}` root `{trace['root']}`")
            for span in trace["spans"]:
                attrs = {k: v for k, v in span["attributes"].items() if k in ("http.route", "http.request.method", "http.response.status_code", "order.id", "db.operation.name")}
                add(f"  - span `{span['name']}` [{span['status']}] {attrs}")
                for event in span.get("events", []):
                    if event.get("name") == "exception":
                        add(f"    - exception {event.get('exception.type')}: {event.get('exception.message')}")

    repo = evidence.get("repo")
    if repo:
        add("")
        add("### Repository")
        add(f"- directory: `{repo['directory']}`")
        add("- recent commits:")
        lines.extend("  - " + line for line in repo["recent_commits"].splitlines())
        add("- working tree: " + (repo["working_tree"].replace("\n", "; ") or "clean"))

    return "\n".join(lines) + "\n"
