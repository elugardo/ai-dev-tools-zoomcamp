# AGENTS.md

**Agent Relay** is a small messaging system for software agents, used for the AI
Dev Tools Zoomcamp homework 3 (containerize and deploy). One agent sends a task
to another, a worker claims it over HTTP, and the worker completes or fails it.
The database stores the tasks and their delivery attempts, and a dashboard
shows the lifecycle.

The code is Alexey Grigorev's starter,
[`alexeygrigorev/agent-relay`](https://github.com/alexeygrigorev/agent-relay),
copied in at commit `0a2895b`. It is a plain folder in this repo, not a git
submodule. The homework is
[`03-deployment/homework.md`](https://github.com/DataTalksClub/ai-dev-tools-zoomcamp/blob/main/cohorts/2026/homework/03-deployment/homework.md).

- The protocol and lifecycle are defined in [`SPEC.md`](SPEC.md). Its
  acceptance scenarios are at the end.
- [`README.md`](README.md) is the starter's own usage guide.

## Homework steps

Each step builds on the one before it.

1. Run the project and work out its architecture.
2. Follow `SPEC.md` to register two agents and exchange a task and its result.
   Check it in the dashboard, then turn the flow into an API integration test
   against the real API and database.
3. Write a `Dockerfile`, build it as `agent-relay:local`, and run it with `-p`.
   Uvicorn must listen on `--host 0.0.0.0` inside the container.
4. Switch to PostgreSQL and add a `compose.yaml` that runs the API and a database
   service named `postgres`. Run the integration test against the stack.
5. Deploy to a local **kind** cluster using manifests in `k8s/`: Deployments,
   Services, persistent database storage, and readiness checks. Load the image
   into kind and reach the dashboard through `kubectl port-forward`.
6. Add `.github/workflows/ci.yml`. It runs the tests (including the integration
   test on PostgreSQL), builds an image with a unique tag, and deploys to kind
   only if the tests pass. Run it locally with `act`. Then change the dashboard
   heading to `Agent Relay v2` and ship that through the workflow.

Nothing needs a cloud account, an LLM key, or an external broker. Install
Docker, kind, kubectl and act as needed, and verify each one.

## Commands

Run from `module3/`:

```
uv sync                                  # create .venv and install deps
uv run uvicorn main:app --reload         # API + dashboard on http://127.0.0.1:8000/
uv run pytest -q                         # test suite
uv run python main.py worker --base-url http://127.0.0.1:8000 \
  --name uppercase --credentials ./uppercase-credentials.json --worker-id laptop-1
```

`uv run` works inside the project environment, so there is nothing to activate.

## Architecture

```
module3/
├── main.py            FastAPI app: routes, error handlers, body-size middleware,
│                      /health, /ready, dashboard; `python main.py worker` runs the worker
├── schemas.py         Pydantic request/response models
├── storage.py         task, claim, heartbeat, completion and lease-recovery operations
├── database.py        SQLAlchemy models, engine, SQLite WAL, BEGIN IMMEDIATE helper,
│                      RELAY_* settings
├── errors.py          RelayError → the error body
├── worker.py          reference worker: claims, heartbeats, returns input.upper()
├── dashboard.py/.html token-based dashboard
└── test_agent_relay.py
```

- **Clients pull work from the database through the HTTP API.** Agents never
  talk to each other directly, and there is no broker.
- **Delivery is at-least-once.** A claim holds a lease, 60 s by default, that
  heartbeats extend. An expired claim is redelivered with a new claim token and
  a higher attempt number, up to `RELAY_MAX_ATTEMPTS`.
- **Auth.** Registration (`POST /api/v1/agents`) is the only endpoint that needs
  no auth, and it returns the agent's token once. Every other call sends
  `Authorization: Bearer <token>`. Completing or failing a task also needs the
  claim token. When `RELAY_ENROLLMENT_SECRET` is set, registration requires
  `X-Enrollment-Secret`.
- **The storage seam.** `storage.py` and `database.py` hold all of the database
  code. SQLite has no `FOR UPDATE SKIP LOCKED`, so the starter serializes writers
  with `BEGIN IMMEDIATE`. The PostgreSQL port (step 4) belongs here, and the HTTP
  protocol in `SPEC.md` must not change. The `psycopg[binary]` driver is already
  a dependency.

### Configuration

| Variable | Default | Purpose |
|---|---|---|
| `RELAY_DATABASE_URL` (or `DATABASE_URL`) | `sqlite:///./agent-relay.db` | SQLAlchemy URL |
| `RELAY_LEASE_SECONDS` | `60` | claim lease length |
| `RELAY_MAX_ATTEMPTS` | `5` | delivery attempts before a task fails |
| `RELAY_RECOVERY_INTERVAL_SECONDS` | `5` | how often expired leases are recovered |
| `RELAY_MAX_BODY_BYTES` | `262144` | request body limit |
| `RELAY_ENROLLMENT_SECRET` | unset | if set, registration requires it |
| `RELAY_BASE_URL` | `http://127.0.0.1:8000` | where the worker connects |

## Rules

- **Keep the protocol stable.** Deployment work (Docker, Compose, k8s, CI) must
  not change the HTTP API or the lifecycle in `SPEC.md`. The only planned app
  change is the `Agent Relay v2` dashboard heading.
- **Readiness.** `/health` is liveness. `/ready` queries the real tables, so use
  it for container and Kubernetes readiness checks.
- **Secrets.** Agent tokens, `*-credentials.json`, database passwords and the
  enrollment secret never go in git. In Compose and k8s, pass them in through
  environment variables or a Secret.
- **Dependencies** go in `pyproject.toml` and `uv.lock`, added with `uv add`.
  Do not add one without asking.
- **Never commit** `.venv/`, `*.db` files, or credential files. The starter's
  `.gitignore` already covers them.

## Testing

- **The tests wipe their database.** The fixture drops and recreates every
  table on whatever `RELAY_DATABASE_URL` points at. By default that is a
  scratch file at `/tmp/agent-relay-test.db` (on Windows, `C:\tmp\`). Never
  point it at a database you want to keep.
- The starter tests in `test_agent_relay.py` use FastAPI's `TestClient`.
- `test_integration.py` is the step 2 integration test. It covers SPEC
  acceptance scenario 1 over real HTTP against a **running** server, so the same
  test targets the local server, the container, the Compose stack and kind.
  - Set `RELAY_BASE_URL` to point it elsewhere. The default is
    `http://127.0.0.1:8000`.
  - It skips when nothing answers, so a plain `uv run pytest` works without a
    server.
  - It only adds rows (fresh agents and one task per run), so it is safe
    against a database you want to keep.
  - To run only this test against the server at `RELAY_BASE_URL`, use
    `uv run pytest -q test_integration.py`.
- **Prove a new test can fail.** Break the line it defends, watch the test go
  red, then restore the line.
