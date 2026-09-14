# AGENTS.md

**WaitWise** is a restaurant waitlist manager for the AI Dev Tools Zoomcamp,
module 2. Admins manage restaurants, restaurant staff run their live waitlist,
and eaters join a list and watch their place in line. The course spec is
[`_docs/WaitWise_Restaurant_Waitlist_Manager_Specification.md`](_docs/WaitWise_Restaurant_Waitlist_Manager_Specification.md).
The API contract is [`openapi.yaml`](openapi.yaml).
[`_docs/progress.md`](_docs/progress.md) records what has shipped, the
decisions behind it, and the open items. Read it before starting work, and add a
dated entry at the end of each session.

**Where the project stands:** all four phases of the spec (§3) are done.

- **Frontend:** React, calling a FastAPI backend.
- **Backend:** FastAPI, persisting to a database through **SQLAlchemy**. It uses
  SQLite by default, and the database is chosen by `WAITWISE_DATABASE_URL`.
- **Mock:** the in-browser mock is still there for backend-free work.

## Commands

Run from `module2/`:

```
npm run setup          # install frontend (npm) and backend (uv) dependencies
npm run dev:backend    # FastAPI on http://localhost:9127 (docs at /docs)
npm run dev            # frontend on http://localhost:3417, talking to the backend
npm run dev:mock       # frontend on the in-browser mock, no backend needed
npm run test:all       # frontend (vitest) then backend (pytest)
npm run build          # frontend typecheck + production build
```

The `Makefile` in `module2/` wraps the same commands: `make install`, `make dev`
(both servers in one terminal), `make backend`, `make frontend`, `make mock`,
`make test`, `make build`, `make db-init` and `make db-reset`. Keep it in step with `package.json`. Its recipes
use only `npm --prefix` and `uv --directory`, so they run the same under `sh` and
Windows `cmd`. Keep `help` text free of `( ) < > | & ;` and quotes, because the
two shells parse those differently. On this machine GNU Make comes from winget
(`ezwinports.make`); open a new shell after installing so it is on `PATH`.

Backend, from `module2/backend/`:

```
uv run uvicorn app.main:app --reload --port 9127
uv run pytest                                   # whole backend suite
uv run pytest tests/test_auth.py -k expires     # one file / one test
uv run python -m app.manage reset-db            # drop, recreate and reseed the database
uv run python -m app.manage init-db             # create missing tables (startup does this too)
```

Frontend, from `module2/frontend/`:

```
npm test                                        # vitest, single run
npx vitest run src/services/http                # one folder
npm run typecheck
```

Requirements:

- **Node 20.19+ or 22.12+** (Vite 8).
- **Python 3.13** with [uv](https://docs.astral.sh/uv/). `uv run` works inside
  the project environment, so there is nothing to activate.

Demo logins (seeded, in both the backend and the mock):

| Username | Password |
|---|---|
| `admin` | `password` |
| `bluebird` | `password` |
| `oakember` | `password` |

## Architecture

```
module2/
├── openapi.yaml            the contract, checked by tests on both sides
├── frontend/src/
│   ├── domain/             business rules as pure functions; types.ts = the API DTOs
│   ├── services/           the ONLY path to a backend
│   │   ├── WaitWiseService.ts   interface: one method per endpoint
│   │   ├── http/                the real client (fetch → FastAPI), the default
│   │   ├── mock/                in-browser implementation of the same interface
│   │   ├── errors.ts            ServiceError + friendlyErrorMessage
│   │   └── index.ts             picks http or mock from VITE_SERVICE_MODE / --mode
│   ├── auth/ hooks/ components/ pages/ test/
└── backend/
    ├── app/
    │   ├── main.py         create_app(): DB setup + seed, CORS, error handlers, routers under /api
    │   ├── config.py       Settings from WAITWISE_* environment variables (incl. DATABASE_URL)
    │   ├── db.py           engine, per-request session, UTCDateTime; ALL database-specific code
    │   ├── tables.py       SQLAlchemy ORM tables, portable types only
    │   ├── models.py       Pydantic request/response models + enums + field validation
    │   ├── rules.py        pure waitlist rules (transitions, position, no-show, history)
    │   ├── store.py        Store: every query and write, over one request's session
    │   ├── auth.py         scrypt hashing, bearer tokens, session/store/role dependencies
    │   ├── serializers.py  ORM rows → response models (positions, counts)
    │   ├── errors.py       ApiError subclasses (+ IntegrityError) → the Error body
    │   ├── seed.py         demo data, loaded only into an empty database
    │   ├── manage.py       `python -m app.manage init-db | seed | reset-db`
    │   └── routers/        auth.py, public.py, restaurant.py, admin.py
    └── tests/              pytest + TestClient; conftest.py has the fixtures
```

### The contract

- `openapi.yaml` is the source of truth. Each operation's `x-service-method`
  names its `WaitWiseService` method, and `x-required-role` marks protected
  endpoints.
- **Change these together:** `openapi.yaml`, `WaitWiseService.ts` (whose JSDoc
  names each endpoint), `domain/types.ts`, the backend `models.py` and router,
  and the mock.
- Tests enforce this. The backend's `tests/test_contract.py`:
  - checks that the app implements exactly the spec's operations;
  - drives all 15 of them;
  - validates every status code and response body against the spec;
  - checks `WaitWiseService.ts` annotations against the spec.
- The frontend's `httpService.test.ts` checks every request against those same
  annotations.
- Every error is `{code, message, field_errors}`. `message` is shown to users
  as-is, so write it for guests and staff. Never put exception text in it.

### Frontend services layer

- Components get services from `useServices()` and **never** call `fetch` or
  import an implementation. `src/test/architecture.test.ts` enforces this on
  the AST.
  - `fetch` is allowed only in `services/http/`.
  - Only `services/index.ts` may import `http/` or `mock/`.
- DTO fields are `snake_case`, matching the JSON, so there is no renaming layer.
- The HTTP client turns every failure into a `ServiceError`:
  - the server's Error body passes through unchanged;
  - a network failure becomes `UNAVAILABLE`;
  - anything unparseable gets a generic message by status.
- The mock enforces the same rules as the backend, including password checks.
  It stores plain-text passwords, because it is a mock. It persists to
  `localStorage` (`waitwise.mockdb.v1`). **Reset demo data** appears in the
  footer only in mock mode.

### Backend

- **Routers stay thin.** Each one validates input with a Pydantic model, calls
  `Store` operations and serializes the result. None of them manage
  transactions.
- **One transaction per request.** `db.get_session` is declared with
  `Depends(..., scope="function")`. It commits after the endpoint returns but
  before the response is sent, and any exception rolls the whole request back.
  `auth.get_store` wraps that session and runs the automatic no-show sweep first
  (spec §14), so there is no background worker.
- **The store owns the data and its invariants.** That includes username
  uniqueness (checked first, with the unique constraint as a backstop that maps
  to 409), the closed-waitlist check, transitions, and token revocation when a
  password changes.
- **Concurrent writes** use conditional UPDATEs (`WHERE id = … AND status =
  expected`), checking the row count. A stale request re-decides against the
  current status and never overwrites a newer one. This needs no row locks, so
  it behaves the same on every database.
- **Rules are pure.** `rules.py` functions take `now` as an argument. `Store`
  takes a `clock`, and tests pass a hand-cranked one.
- **Validation messages.** Pydantic validators raise `ValueError` with the same
  messages as `frontend/src/domain/validation.ts`, and `errors.py` maps them
  into `field_errors`. Keep the two in sync. That includes the **length limits,
  which must match the column sizes in `tables.py`**: SQLite ignores them, but
  Postgres and others reject overlong values.

### Database

- **Choosing the database.** `WAITWISE_DATABASE_URL` is any SQLAlchemy URL.
  - The default is `sqlite:///<backend>/waitwise.db`, which is gitignored.
  - `sqlite://` gives a throwaway in-memory database.
  - Another database needs only its driver added with `uv add` and a URL such as
    `postgresql+psycopg://user:pass@host/waitwise`.
- **Keep it database-agnostic.**
  - All dialect-specific code lives in `db.py`: SQLite pragmas, `StaticPool`
    for in-memory databases, WAL.
  - Everywhere else uses ORM queries and generic types only. No
    `sqlalchemy.text`, no `sqlalchemy.dialects` imports, no database functions.
    Do date arithmetic in Python.
  - `tests/test_database.py` enforces this. It checks column types (strings need
    a length, enums must be non-native) and banned imports and strings in
    `app/`. It also compiles the schema for PostgreSQL, MySQL and SQL Server.
- **Timestamps** use `UTCDateTime`. It stores naive UTC, returns aware UTC, and
  refuses naive input.
- **Schema.** `create_schema()` (SQLAlchemy `create_all`) runs at startup. It
  creates missing tables but never alters existing ones, and there is no
  migration tool. After changing a table, run `make db-reset`, which deletes
  the data. Add Alembic before any schema change must keep existing data.
- **Seed data** loads only into an empty database, so restarts never duplicate
  it. The demo queue's timestamps are fixed at seeding time and go stale; run
  `make db-reset` for a fresh demo.
- **Login tokens** persist, stored as SHA-256 hashes, so sessions survive
  restarts. Expired tokens are purged at the next login.

### Authentication

This deliberately goes beyond the course spec: §5 says passwords are ignored,
and §39 excludes hashing. It was added at the project owner's request.

- **Passwords.** Hashed with stdlib `hashlib.scrypt`, with a random salt and the
  parameters stored alongside as `scrypt$n$r$p$salt$hash`, and compared in
  constant time. They are never returned by any endpoint.
- **Login.** Returns an opaque `secrets.token_urlsafe` bearer token. The
  database stores only its SHA-256 hash. There is no JWT.
  - A token expires after `WAITWISE_TOKEN_TTL_MINUTES` (default 12 h).
  - Changing a restaurant's password revokes its tokens.
  - A wrong password and an unknown username get the same 401 message.
- **Password rules.** New restaurant passwords need at least 8 characters. On
  edit, a blank password keeps the current one.
- **Frontend.** Stores the token in `localStorage` and sends
  `Authorization: Bearer …` only on restaurant and admin calls. A 401 on the
  dashboard offers **Log in again**.

## Rules

- **Scope is the spec**, plus the authentication above. Do not build anything
  else from the spec's §39 out-of-scope list (reservations, SMS, JWT,
  WebSockets, Docker, background workers, and so on). If a task seems to need
  something outside the spec, propose it and get agreement first.
- **Business rules are pure functions:** `frontend/src/domain/` and
  `backend/app/rules.py`. Pages and routers do not decide.
- **Wait quotes are frozen at join time.** `quoted_wait_minutes` and
  `estimated_ready_at` are never recomputed.
- **Queue position is never stored.** It is always derived from active entries
  ordered by `joined_at`, then `id`.
- **Polling, not WebSockets.** The eater page and dashboard re-fetch their GET
  endpoint every 10 s.
- **Ports are fixed:** frontend 3417, backend 9127 (`/api`). The backend's CORS
  allows the frontend origin by default (`WAITWISE_CORS_ORIGINS`).
- **No UI component framework.** Plain CSS in `src/styles.css`.
- **Dependencies:**
  - frontend: `frontend/package.json` + `package-lock.json`;
  - backend: `backend/pyproject.toml` + `uv.lock`, added with `uv add`.
  - Do not add one without asking.
- **Never commit** `node_modules/`, `dist/`, `.venv/`, or `.env` files.

## Testing

- **Prove a new test can fail.** Break the line it defends, watch the test go
  red, then restore the line. A test that no mutation can fail is decoration.

**Backend (pytest)**

- Every test gets a fresh app and a fresh database from `conftest.py`:
  - `api`: the demo seed.
  - `quiet_api`: the seed without waitlist entries.
  - `empty_api`: no data at all.
- **Test database.** The default is in-memory SQLite, created per test. Set
  `WAITWISE_TEST_DATABASE_URL` to run the whole suite on another database. Its
  tables are dropped before every test, so never point it at real data.
- `api.clock.advance(minutes=…)` moves time. `api.login("bluebird")` returns
  auth headers. `with api.db() as store:` arranges or inspects rows directly,
  in its own committed transaction. Hashing uses a low scrypt cost in tests, so
  keep it that way.
- **File-backed tests.** Persistence and concurrency tests need a real file,
  because two sessions on in-memory SQLite share one connection. Use `tmp_path`
  with `api_for(clock, database_url=...)`.
- Test through HTTP with `TestClient`. Use `test_rules.py` for pure rules.
- New endpoints or response codes belong in `openapi.yaml` and in
  `test_contract.py`'s scenario. Its final assertions require that every
  documented status code is actually produced.

**Frontend (Vitest + React Testing Library)**

- UI tests render the whole app with `renderApp(path, services)` on a fresh
  in-memory **mock** (`createTestServices()`). Log in with
  `signInAs(services, 'bluebird')`. Do not mock individual components.
- `httpService.test.ts` tests the real client against a fake `fetch`. The
  frontend never calls a live backend in tests.
- For time-dependent tests, fake only `setInterval`, `clearInterval` and `Date`
  (`vi.useFakeTimers({ toFake: [...] })`), so `findBy` keeps working. Use
  `userEvent.setup({ delay: null })`.
- Full-app renders are slow on some machines, so timeouts are raised in
  `vite.config.ts` and `src/test/setup.ts`. Vitest is capped at 4 workers,
  because one jsdom worker per core exhausts memory and workers then time out
  on startup.

## Next steps (not started)

- **Postgres support:**
  1. Add a driver (`uv add psycopg[binary]`).
  2. Run the test suite with `WAITWISE_TEST_DATABASE_URL` pointing at a
     disposable database.
  3. Fix anything that shows up. The code already avoids SQLite-only behavior.
- **Migrations:** add Alembic before the first schema change that has to keep
  existing data.
