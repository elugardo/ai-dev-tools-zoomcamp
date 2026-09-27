"""Run Claude Code headless on one incident and keep everything it said.

The agent gets a brief (``prompt.md`` in the incident folder), a bounded tool
allowlist, and the repository as its working directory. Its raw JSON output,
its stderr and its final answer are saved next to the brief.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_ALLOWED_TOOLS = ",".join(
    [
        "Read",
        "Glob",
        "Grep",
        "Edit",
        "Write",
        "Bash(uv run *)",
        "Bash(docker compose *)",
        "Bash(curl *)",
        "Bash(git diff*)",
        "Bash(git log*)",
        "Bash(git status*)",
    ]
)


def settings() -> dict[str, Any]:
    return {
        "claude_bin": shutil.which(os.getenv("CLAUDE_BIN", "claude")),
        "allowed_tools": os.getenv("AGENT_ALLOWED_TOOLS", DEFAULT_ALLOWED_TOOLS),
        "max_turns": int(os.getenv("AGENT_MAX_TURNS", "40")),
        "timeout_seconds": int(os.getenv("AGENT_TIMEOUT_SECONDS", "1200")),
        "model": os.getenv("AGENT_MODEL") or None,
    }


def run(incident_dir: Path, repo_dir: Path) -> dict[str, Any]:
    cfg = settings()
    if not cfg["claude_bin"]:
        return {"state": "error", "error": "claude CLI not found on PATH (set CLAUDE_BIN)"}

    prompt_path = incident_dir / "prompt.md"
    command = [
        cfg["claude_bin"],
        "-p",
        f"You are the on-call responder. Read {prompt_path} and follow it exactly.",
        "--output-format",
        "json",
        "--max-turns",
        str(cfg["max_turns"]),
        "--permission-mode",
        "acceptEdits",
        "--allowedTools",
        cfg["allowed_tools"],
    ]
    if cfg["model"]:
        command += ["--model", cfg["model"]]

    started = time.monotonic()
    status: dict[str, Any] = {
        "state": "running",
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "command": command,
        "cwd": str(repo_dir),
    }
    write_json(incident_dir / "status.json", status)

    try:
        completed = subprocess.run(
            command,
            cwd=repo_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=cfg["timeout_seconds"],
        )
    except subprocess.TimeoutExpired as exc:
        status.update(state="timeout", duration_seconds=round(time.monotonic() - started))
        (incident_dir / "agent-stderr.log").write_text(exc.stderr or "", encoding="utf-8")
        return status

    (incident_dir / "agent-output.json").write_text(completed.stdout, encoding="utf-8")
    (incident_dir / "agent-stderr.log").write_text(completed.stderr, encoding="utf-8")
    result = parse_result(completed.stdout)
    answer = result.get("result") or completed.stdout.strip() or completed.stderr.strip()
    (incident_dir / "agent-response.md").write_text(answer + "\n", encoding="utf-8")

    last_line = next((line for line in reversed(answer.splitlines()) if line.strip()), "")
    status.update(
        state="done" if completed.returncode == 0 else "error",
        exit_code=completed.returncode,
        duration_seconds=round(time.monotonic() - started),
        finished_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        last_line=last_line,
        session_id=result.get("session_id"),
        num_turns=result.get("num_turns"),
        total_cost_usd=result.get("total_cost_usd"),
        model=cfg["model"] or "default",
    )
    return status


def parse_result(stdout: str) -> dict[str, Any]:
    """The JSON output is either the final result object or a list of events
    whose last ``result`` event carries the answer."""

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return {}
    if isinstance(data, dict):
        return data
    for item in reversed(data):
        if isinstance(item, dict) and item.get("type") == "result":
            return item
    return {}


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")
