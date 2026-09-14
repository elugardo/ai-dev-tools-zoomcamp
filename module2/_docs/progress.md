# WaitWise progress

The stable plan is the course spec,
[`WaitWise_Restaurant_Waitlist_Manager_Specification.md`](WaitWise_Restaurant_Waitlist_Manager_Specification.md).
This file tracks what has actually been built, why it was built that way, and
what is still open. Newest session first.

## Status at a glance

| Spec phase (§3) | State | Where |
|---|---|---|
| 1. Scope | Covered by the spec itself; no separate scope doc | `_docs/` |
| 2. Frontend on mock data | Done, 2026-09-14 | PR #32 |
| 3. Backend | Done, 2026-09-14 | PR #33 |
| 4. Database (SQLAlchemy, SQLite by default) | Done, 2026-09-14 | branch `module2-sqlalchemy-db` |

Test suite on 2026-09-14: 163 frontend (Vitest) and 194 backend (pytest), all
passing through `make test` or `npm run test:all`.

---

## 2026-09-14: SQLAlchemy database replaces the in-memory store

### What shipped

- **Persistence.** The backend now uses SQLAlchemy 2.0. SQLite is the default,
  and `WAITWISE_DATABASE_URL` selects the database. The default is
  `sqlite:///<backend>/waitwise.db`, which is gitignored.
- **New modules:**
  - `app/db.py`: the engine, one session and transaction per request, and the
    `UTCDateTime` column type.
  - `app/tables.py`: ORM tables for users, restaurants, waitlist entries and
    auth tokens.
  - `app/manage.py`: the `init-db`, `seed` and `reset-db` commands, also
    available as `make db-init` and `make db-reset`.
- **Store.** `app/store.py` is rewritten over a session with the same method
  names. Routers lost their manual transaction blocks and otherwise changed
  little. The API contract did not change.
- **Durability.** Data and login sessions survive restarts. Tokens are stored
  as SHA-256 hashes. The demo data loads only into an empty database.
- **Length limits.** Values are now capped to match the database columns: guest
  name 100, restaurant name 120, address 200, phone 32, username 64, password
  128 characters. They are enforced in backend validation, frontend validation
  and `openapi.yaml`.

### Verification done

- **Contract unchanged.** All 161 existing backend tests passed unchanged on
  SQLite. The contract test still validates every operation against
  `openapi.yaml`.
- **New database tests.** `tests/test_database.py` adds 33 tests:
  - reads the database URL from the environment;
  - data and sessions survive a restart on a file database;
  - seeds once; `reset-db` rebuilds;
  - datetimes are UTC-aware and naive ones are refused;
  - stores tokens hashed and purges expired ones;
  - enforces foreign keys, check constraints and one login per restaurant;
  - overlong values get a 422;
  - a failed request rolls back completely;
  - the unique constraint backstops the username check with a 409;
  - stale concurrent status changes can't overwrite newer ones;
  - portability guards: generic column types, no dialect code outside `db.py`,
    and the schema compiles for PostgreSQL, MySQL and SQL Server.
- **Deliberate breaks.** Key database guarantees were broken on purpose to
  confirm tests fail, covering commit, rollback, UTC handling, token hashing,
  conditional updates, seeding, foreign keys and native enums.
- **Make targets.** `make db-reset` and `make db-init` were run; the default
  database file is created in `backend/` and ignored by git.
- **Real browser, on the database-backed server.** Starting from `make db-reset`,
  with `make backend` and `make frontend`, the full spec §40 scenario passed in
  headless Chrome: Notify reached the eater page in 5.0 s. Then only the
  backend was restarted:
  - the staff browser was still logged in, without a new login;
  - the seated, walk-in and canceled parties were still in Today's History;
  - the eater's status link still showed Seated;
  - the admin-created restaurant was still listed, and the seed was not
    duplicated.

### Decisions and why

- **Database-agnostic by construction.**
  - Every dialect-specific setting lives in `db.py`: SQLite foreign-key pragma,
    WAL, busy timeout, and `StaticPool` for in-memory databases.
  - The rest uses ORM queries and generic types. Enums are stored as strings
    with CHECK constraints instead of native enum types. Strings have explicit
    lengths.
  - Date arithmetic (no-show deadlines, today's history) stays in Python rather
    than in database-specific SQL.
- **Naive UTC storage behind `UTCDateTime`.** SQLite drops timezones and
  Postgres keeps them. Storing naive UTC and returning aware UTC behaves the
  same on both, and refusing naive input prevents local times from slipping in.
- **One transaction per request** through a `yield` dependency with
  `scope="function"`, so the commit happens before the response is sent. Any
  exception, including a deliberate `ApiError`, rolls back everything the
  request did.
- **Conditional UPDATEs instead of row locks** for status changes and the
  no-show sweep: `WHERE id = … AND status = expected`, checking the row count.
  `SELECT … FOR UPDATE` is not supported the same way everywhere, and SQLite
  ignores it. A stale request re-decides against the current status.
- **Username uniqueness** is checked first so the response can name the field.
  The database's unique constraint is the backstop for races and maps to a
  generic 409.
- **Expired tokens are purged at login**, not when an expired token is used.
  That request ends in 401 and rolls back, so a delete there would never
  commit. A test caught this.
- **`create_all` at startup, no Alembic yet.** The schema is new and the data is
  demo data. `make db-reset` rebuilds it after a schema change. Alembic would
  add a dependency and migration files before there is anything to migrate.
- **`app` is built lazily** through a module `__getattr__`. `uvicorn
  app.main:app` still works, and importing `create_app` in tests never creates
  or seeds `backend/waitwise.db`.
- **Tests run on in-memory SQLite by default.** Setting
  `WAITWISE_TEST_DATABASE_URL` runs the same suite on another database, dropping
  its tables before every test.

### Open items

- **Postgres not yet exercised.** It needs a driver (`uv add psycopg[binary]`)
  and a run of the suite against a disposable database via
  `WAITWISE_TEST_DATABASE_URL`. Only schema compilation for PostgreSQL is
  tested so far.
- **No migrations.** Any schema change currently means `make db-reset`, which
  loses data. Add Alembic before the first change that must keep data.
- **The demo seed goes stale.** Its timestamps are fixed when it is loaded. On a
  long-lived database the seeded parties look hours or days old; run
  `make db-reset` for a fresh demo.

---

## 2026-09-14: frontend, API contract, backend, Makefile

### What shipped

1. **React frontend on a mock services layer** (PR #32, squash commit `ae6d080`)
   - **App:** React 19, TypeScript, Vite and React Router, with plain CSS. Every
     route in spec §23 is built: public list and restaurant page, eater status
     page with polling, staff login, restaurant dashboard, admin list and form.
   - **Services layer:** every backend call goes through the `WaitWiseService`
     interface, which has one method per endpoint. The in-browser mock enforces
     the real rules: validation, transitions, frozen quotes, the auto no-show
     sweep and role checks. It persists to `localStorage`, so two tabs share
     one waitlist.
   - **Rules:** business rules are pure functions in `frontend/src/domain/`.
   - **Architecture test:** an AST-based test forbids `fetch`, and imports of
     the implementations, outside `services/`.
2. **`openapi.yaml`**, the backend contract: all 15 operations, request and
   response schemas, the error shape, and auth requirements. Each operation
   names its frontend method in `x-service-method` and marks protected
   endpoints with `x-required-role`.
3. **FastAPI backend with authentication, and the frontend wired to it**
   (PR #33, squash commit `3096557`)
   - **Layout:** `backend/app/` is split into `routers/`, `models.py`,
     `store.py` and `auth.py`, plus `rules.py`, `seed.py`, `config.py`,
     `errors.py` and `serializers.py`.
   - **Store:** in memory, seeded with the spec §34 demo data. Each request runs
     in a locked `store.transaction()` that sweeps overdue no-shows first.
   - **Frontend:** `frontend/src/services/http/httpService.ts` is now the
     default implementation. `npm run dev:mock` / `make mock` keeps the mock.
4. **Makefile** (`module2/Makefile`, branch `module2-makefile`) with targets
   `install`, `dev`, `backend`, `frontend`, `mock`, `test`, `test-frontend`,
   `test-backend` and `build`.

### Verification done

- **Contract tests on both sides.**
  - The backend `test_contract.py` drives all 15 operations and validates each
    status code and response body against `openapi.yaml`. It rejects
    undocumented fields, requires every documented status code to occur, and
    checks the `WaitWiseService.ts` endpoint annotations against the spec.
  - The frontend `httpService.test.ts` checks every request against those same
    annotations.
- **Deliberate breaks.** Key rules were broken on purpose: 12 during the
  frontend work, and 24 across the backend, HTTP client, mock and forms. Every
  one made a test fail. The one that initially survived exposed a missing role
  test, which was added.
- **Real browser.** Headless Chrome, driven by Playwright from a temp folder,
  ran the spec §40 acceptance scenario against both live servers:
  - wrong password rejected; login;
  - eater joins, then a staff walk-in is added;
  - Notify reached the eater page by polling in about 5 s;
  - staff seat parties, including out of order, into Today's History;
  - a second eater leaves;
  - admin creates a restaurant, and its new login works.

  It passed again when both servers were started with `make dev`. `make mock`
  was confirmed to render with no backend and no API calls.
- **Makefile.** Every target was run with GNU Make 4.4.1 on Windows. `help` ran
  under both `cmd` and `sh`.

### Decisions and why

- **Authentication goes beyond the spec.** Spec §5 ignores passwords and §39
  lists password hashing as out of scope. The owner explicitly asked for hashed
  passwords and bearer tokens, so the backend verifies passwords.
  - **Hashing:** stdlib `hashlib.scrypt` with a per-password salt. This avoids
    adding a dependency.
  - **Tokens:** opaque `secrets.token_urlsafe` strings rather than JWTs, which
    §39 still excludes. They expire after 12 h and are revoked when the
    restaurant's password changes.
  - **Accounts:** a wrong password and an unknown user get the same message.
    All demo accounts use the password `password`. New restaurant passwords
    need at least 8 characters, and a blank password on edit keeps the current
    one.
  - **Docs:** `openapi.yaml`, the mock and the UI were updated to match. The
    course spec file was deliberately left as issued.
- **DTOs use `snake_case` in TypeScript**, matching the JSON, so the HTTP client
  needs no renaming layer.
- **The frontend was built against a mock first**, as spec §3 requires. The mock
  is kept after the backend landed: it runs the UI with no server, and the
  full-app UI tests use it.
- **One Error body everywhere:** `{code, message, field_errors}`. `message` is
  shown to users verbatim, and FastAPI's default `{"detail": [...]}` is
  replaced. This is how spec §31 ("never expose raw backend exceptions") is
  enforced.
- **Positions are derived, never stored**, with ties broken by `id`. **Quotes
  are frozen at join time.** `no_show_at` is stamped at the deadline, not at
  the moment the sweep happened to run.
- **Walk-ins are allowed while online joining is paused.** The public restaurant
  endpoint still returns an inactive restaurant, so its page can show joining as
  disabled. Admin edits leave `online_waitlist_enabled` alone.
- **The backend uses uv and `pyproject.toml`** instead of the spec's suggested
  `requirements.txt`, for consistency with module 1. The start command is
  `uv run uvicorn app.main:app --reload --port 9127`.
- **`httpx2` instead of `httpx`** for tests, because Starlette 1.6's TestClient
  deprecates `httpx`.
- **Vitest is capped at 4 workers.** With one jsdom worker per core, this
  machine ran out of memory and workers timed out on startup.
- **Makefile recipes use only `npm --prefix` / `uv --directory`**, so they work
  under both `sh` and Windows `cmd`. The help text must avoid parentheses and
  other characters the two shells parse differently. Parentheses broke `make`
  under `sh`.

### Open items and loose ends

- ~~**Phase 4:** SQLAlchemy models (spec §25) replace `backend/app/store.py`.~~
  Done; see the database entry above.
- **Homework:** the SHA1 is filled in at submission (`git rev-parse HEAD` on the
  submitted branch). The course homework and FAQ URLs are still to be added to
  the README.
- **No server-side logout.** Logging out only drops the token in the browser;
  the token stays valid until it expires or the password changes. Tokens now
  persist in the database, so this matters more than it did.
- **"Today's History"** uses the server's local timezone in the backend and the
  browser's in the mock. That is fine locally, but a deployed backend needs one
  fixed timezone.
- **The automatic no-show was not exercised in a browser**, because it takes 10
  real minutes. Backend tests cover it with a controllable clock. The seeded
  party Lena turns into a No Show about 7 minutes after the backend starts.
- **No favicon.** `/favicon.ico` returns 404 in the browser console; this is
  harmless.
- **Help text alignment.** Under `sh`, `make help` collapses the column spacing;
  this is cosmetic.
- **The browser acceptance script is not in the repo.** It ran from a temporary
  folder with `playwright-core`. Turning it into a project skill
  (`/run-skill-generator`) or a committed e2e test would need Playwright added
  as a dependency, which requires approval first.
- **`/code-review` was not run** on PRs #32 or #33; the owner chose to merge
  without it.
