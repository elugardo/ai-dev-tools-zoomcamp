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

Docker (step 3):

```
docker build -t agent-relay:local .
docker run -d --name agent-relay -p 8080:8000 -v agent-relay-data:/data agent-relay:local
$env:RELAY_BASE_URL = "http://127.0.0.1:8080"; uv run pytest -q test_integration.py
docker rm -f agent-relay                 # stop it; the volume keeps the data
```

- **Image.** Python 3.11 slim, dependencies installed with `uv sync --locked
  --no-dev` from the lock file, running as the non-root user `relay`.
- **Networking.** Uvicorn listens on `0.0.0.0:8000` inside the container.
  Publish it on host port 8080 so it doesn't clash with a dev server on 8000.
- **Data.** SQLite is stored at `/data/agent-relay.db`, so mount a volume at
  `/data` to keep it. `RELAY_DATABASE_URL` overrides the location.
- **Health.** The image's `HEALTHCHECK` polls `/ready`.
- **Build context.** `.dockerignore` keeps `.venv`, local databases and
  credential files out of the image.

Docker Compose with PostgreSQL (step 4):

```
docker compose up --build -d             # API on http://127.0.0.1:8080, PostgreSQL on 5432
$env:RELAY_BASE_URL = "http://127.0.0.1:8080"; uv run pytest -q test_integration.py
docker compose exec postgres psql -U relay -d relay -c "select status, output from tasks;"
docker compose down                      # add -v to delete the database volume too
```

- The services are `postgres` (`postgres:17-alpine`, data in the `pgdata`
  volume, `pg_isready` healthcheck) and `api` (built from the `Dockerfile`).
  `api` waits for `postgres` to be healthy, and connects to it by its service
  name: `postgresql+psycopg://relay:relay@postgres:5432/relay`.
- The dev credentials (`relay`/`relay`/`relay`) are defaults in `compose.yaml`.
  Override them with `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`,
  `POSTGRES_PORT` and `API_PORT` in a gitignored `.env` file.
- To run the **starter suite on PostgreSQL**, give it a database of its own,
  because it drops every table:
  `docker compose exec postgres psql -U relay -d relay -c "create database relay_test;"`
  then `$env:RELAY_DATABASE_URL = "postgresql+psycopg://relay:relay@127.0.0.1:5432/relay_test"; uv run pytest -q test_agent_relay.py`.

Kubernetes with kind (step 5):

```
kind create cluster --name agent-relay          # once; kubectl context kind-agent-relay
kind load docker-image agent-relay:local --name agent-relay
kubectl apply -k k8s/
kubectl -n agent-relay rollout status deployment/agent-relay
kubectl -n agent-relay port-forward svc/agent-relay 8080:8000   # dashboard on http://127.0.0.1:8080
$env:RELAY_BASE_URL = "http://127.0.0.1:8080"; uv run pytest -q test_integration.py
kubectl -n agent-relay exec postgres-0 -- psql -U relay -d relay -c "select status, output from tasks;"
kind delete cluster --name agent-relay          # tear everything down
```

- `kind` is a single binary at `~/bin/kind.exe` (v0.33); `kubectl` comes with
  Docker Desktop.
- `k8s/` is a kustomization, so `kubectl apply -k k8s/` applies everything in
  the `agent-relay` namespace: a `postgres` Secret (dev credentials), a
  PostgreSQL **StatefulSet** with a 1Gi PVC (`data-postgres-0`, which survives
  pod deletion) and Service, and the API **Deployment** (2 replicas) and
  Service. The API builds `RELAY_DATABASE_URL` from the Secret with `$(VAR)`
  expansion, so the credentials live in one place.
- **Probes.** The API has a `startupProbe` on `/health` (up to 150 s, because
  the process blocks in `init_db()` until PostgreSQL answers, and
  `RELAY_DB_WAIT_SECONDS=120` there), a readiness probe on `/ready` and a
  liveness probe on `/health`. PostgreSQL uses `pg_isready`.
- **Images are never pulled.** The Deployment uses `agent-relay:<tag>` with
  `imagePullPolicy: IfNotPresent`; `kind load docker-image` puts it on the node.
  Re-loading the same tag does **not** restart pods: either
  `kubectl rollout restart deployment/agent-relay` or, better, use a new tag
  and `kustomize edit set image agent-relay=agent-relay:<tag>` in `k8s/`
  (what CI does in step 6).

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
  code, and both SQLite and PostgreSQL work through the same ORM code. The
  dialect only matters in `immediate_transaction()`:
  - **SQLite** (the default) has no row locks, so every writer transaction
    starts with `BEGIN IMMEDIATE`, which serializes writers across processes.
    The SQLite dialect compiles the `FOR UPDATE` clauses below away.
  - **PostgreSQL** uses ordinary transactions plus row locks. A claim selects
    the oldest queued task `FOR UPDATE SKIP LOCKED`, so concurrent workers take
    different tasks. Recovery, heartbeat and completion lock rows `FOR UPDATE`,
    **always the task first and then the attempt** (`lock_task()`), which is
    what rules out deadlocks. Keep that order in any new writer. The starter
    suite's concurrent-claims test passes on PostgreSQL, so run it there after
    touching this code.
  - `init_db()` retries for 30 s on PostgreSQL, because Compose and Kubernetes
    may start the API before the database accepts connections.

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
