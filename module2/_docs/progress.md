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
| 3. Backend | Done, with an in-memory store, 2026-09-14 | PR #33 |
| 4. Database (SQLAlchemy) | **Not started** | — |

Test suite on 2026-09-14: 160 frontend (Vitest) and 161 backend (pytest), all
passing through `make test` or `npm run test:all`.

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

- **Phase 4:** SQLAlchemy models (spec §25) replace `backend/app/store.py`.
  SQLite is the default, with the database URL taken from the environment.
  Routers, the contract and the tests should not need to change.
- **Homework:** the SHA1 is filled in at submission (`git rev-parse HEAD` on the
  submitted branch). The course homework and FAQ URLs are still to be added to
  the README.
- **No server-side logout.** Logging out only drops the token in the browser;
  the token stays valid until it expires or the password changes. Tokens also
  live in memory, so every backend restart signs everyone out.
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
