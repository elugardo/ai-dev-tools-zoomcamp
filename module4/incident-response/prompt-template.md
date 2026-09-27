# Incident {incident_id}: {alert_title}

You are the automatic first responder for **Order Tracker**, the FastAPI app in
`{repo_dir}` (your working directory). A Grafana alert fired, and the evidence
below was collected for you. Work from this evidence and the repository only;
do not guess.

## Your task

1. Decide whether this is a real incident. If the alert is a test, or the
   evidence shows no server errors, say so and stop. Do not change any file.
2. If it is real: find the root cause of the failing requests from the error
   logs (they include the traceback) and the failed traces, and confirm it
   against the code in `app/`.
3. Fix it in the smallest correct way. Add or adjust a test in `tests/` that
   would have caught it. Run `uv run --frozen pytest -q` until it passes.
4. Rebuild and restart the app with `docker compose up --build -d --wait app`,
   then verify that the request that was failing now succeeds, for example
   `curl -s -o /dev/null -w "%{{http_code}}" http://localhost:8000/api/orders/<id>`.
5. Write `{incident_dir}/report.md` (under 40 lines): what users saw, the root
   cause (file and line), what you changed, and how you verified it.

## Rules

- Only edit files under `app/` and `tests/`, plus the report. Do not touch
  `compose.yaml`, `observability/` or `incident-response/`.
- Do not commit. A human reviews the diff.
- Never stop, restart or delete anything other than the `app` service.
- Finish your answer with exactly one line starting with `RESULT:` that sums
  up the outcome, for example
  `RESULT: fixed <cause> in app/main.py; app restarted; verified` or
  `RESULT: no incident (test alert); nothing changed`.

## Alert

{alert_md}

## Evidence

{evidence_md}
