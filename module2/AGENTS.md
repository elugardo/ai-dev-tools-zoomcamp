# AGENTS.md

**WaitWise** is a restaurant waitlist manager for the AI Dev Tools Zoomcamp,
module 2. Admins manage restaurants, restaurant staff run their live waitlist,
and eaters join a list and watch their place in line. The course spec is
[`_docs/WaitWise_Restaurant_Waitlist_Manager_Specification.md`](_docs/WaitWise_Restaurant_Waitlist_Manager_Specification.md).
The API contract is [`openapi.yaml`](openapi.yaml).

**Where the project stands:** Phases 2 and 3 of the spec (§3) are done.

- **Frontend:** React, calling a FastAPI backend.
- **Backend:** FastAPI with an **in-memory store**, seeded with demo data on
  startup.
- **Mock:** the in-browser mock is still there for backend-free work.
- **Not started:** Phase 4, a SQLAlchemy database.

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
`make test` and `make build`. Keep it in step with `package.json`. Its recipes
use only `npm --prefix` and `uv --directory`, so they run the same under `sh` and
Windows `cmd`. Keep `help` text free of `( ) < > | & ;` and quotes, because the
two shells parse those differently. On this machine GNU Make comes from winget
(`ezwinports.make`); open a new shell after installing so it is on `PATH`.

Backend, from `module2/backend/`:

```
uv run uvicorn app.main:app --reload --port 9127
uv run pytest                                   # whole backend suite
uv run pytest tests/test_auth.py -k expires     # one file / one test
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
    │   ├── main.py         create_app(): CORS, error handlers, routers under /api
    │   ├── config.py       Settings from WAITWISE_* environment variables
    │   ├── models.py       Pydantic request/response models + enums + field validation
    │   ├── rules.py        pure waitlist rules (transitions, position, no-show, history)
    │   ├── store.py        in-memory Store: records, tokens, locked operations
    │   ├── auth.py         scrypt hashing, bearer tokens, role dependencies
    │   ├── serializers.py  records → response models (positions, counts)
    │   ├── errors.py       ApiError subclasses → the Error body
    │   ├── seed.py         demo data
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

- **Routers stay thin.** Each one validates input with a Pydantic model, runs
  inside `with store.transaction():`, calls `Store` operations and serializes
  the result.
- **The store owns the data and its invariants.** That includes username
  uniqueness, the closed-waitlist check, transitions, and token revocation when
  a password changes.
- **Locking and the no-show sweep.** Every store method holds a re-entrant
  lock, and `transaction()` makes the whole request atomic. It also runs the
  automatic no-show sweep first (spec §14), so there is no background worker.
- **Rules are pure.** `rules.py` functions take `now` as an argument. `Store`
  takes a `clock`, and tests pass a hand-cranked one.
- **Validation messages.** Pydantic validators raise `ValueError` with the same
  messages as `frontend/src/domain/validation.ts`, and `errors.py` maps them
  into `field_errors`. Keep the two in sync.
- **The store is in memory.** Restarting uvicorn, including a `--reload`,
  resets the data to the seed and signs everyone out. Phase 4 will replace
  `store.py` with SQLAlchemy.

### Authentication

This deliberately goes beyond the course spec: §5 says passwords are ignored,
and §39 excludes hashing. It was added at the project owner's request.

- **Passwords.** Hashed with stdlib `hashlib.scrypt`, with a random salt and the
  parameters stored alongside as `scrypt$n$r$p$salt$hash`, and compared in
  constant time. They are never returned by any endpoint.
- **Login.** Returns an opaque `secrets.token_urlsafe` bearer token, held in the
  store. There is no JWT.
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

- Every test gets a fresh app and store from `conftest.py`:
  - `api`: the demo seed.
  - `quiet_api`: the seed without waitlist entries.
  - `empty_api`: no data at all.
- `api.clock.advance(minutes=…)` moves time. `api.login("bluebird")` returns
  auth headers. Hashing uses a low scrypt cost in tests, so keep it that way.
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

## Next phase (not started)

**Phase 4:** SQLAlchemy models (§25) replace `store.py`. SQLite is the local
default, with the database URL taken from the environment so PostgreSQL works
in production. The routers, the contract and the tests should not need to
change.
