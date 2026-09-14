# AGENTS.md

**WaitWise** is a restaurant waitlist manager for the AI Dev Tools Zoomcamp,
module 2. Admins manage restaurants, restaurant staff run their live waitlist,
and eaters join a list and watch their place in line. The full spec is
[`_docs/WaitWise_Restaurant_Waitlist_Manager_Specification.md`](_docs/WaitWise_Restaurant_Waitlist_Manager_Specification.md).

**Where the project stands:** Phase 2 of the spec (§3). The React frontend is
complete and runs entirely against an **in-browser mock** of the backend. There
is no FastAPI backend or database yet. Those are Phases 3 and 4.

## Commands

Run from `module2/`:

```
npm run setup        # install frontend dependencies (once, and after pulls)
npm run dev          # frontend at http://localhost:3417
npm run test:all     # every test suite (frontend only, until a backend exists)
npm run build        # typecheck + production build
```

Or from `module2/frontend/`:

```
npm run dev                                   # vite on port 3417 (strict)
npm test                                      # vitest, single run
npx vitest run src/pages/HomePage.test.tsx    # one file
npx vitest run -t "seats a party out of order"   # one test by name
npm run typecheck                             # tsc, no emit
```

**Node 20.19+ or 22.12+ is required** (Vite 8). An older Node fails at startup
with `does not provide an export named 'styleText'`. Check `node --version` in
whichever shell you use.

## Architecture

```
frontend/src/
├── domain/      business rules: pure functions, no React, no I/O
│   ├── types.ts        the API contract (DTOs, enums), snake_case like the JSON
│   ├── waitlist.ts     queue order/position, status transitions, no-show, history
│   ├── waitTime.ts     remaining-wait math and formatting
│   └── validation.ts   join form and restaurant form rules
├── services/    the ONLY path to a backend
│   ├── WaitWiseService.ts   interface: one method per endpoint in spec §24
│   ├── errors.ts            ServiceError + friendlyErrorMessage
│   ├── index.ts             createServices(): picks the implementation
│   ├── ServicesContext.tsx  <ServicesProvider> / useServices()
│   └── mock/                mockService.ts (implementation), mockDb.ts (tables + seed)
├── auth/        session in localStorage, <AuthProvider>, <RequireRole>
├── hooks/       useResource (load + poll), useNow (ticking clock)
├── components/  Layout, PartyForm, Field, StatusBadge, Feedback
├── pages/       one component per route in spec §23
└── test/        setup, renderApp helpers, architecture test
```

### The services layer

- Every backend call goes through `WaitWiseService`. Components get it from
  `useServices()` and **never** call `fetch`, import from `services/mock/`, or
  keep their own data. `src/test/architecture.test.ts` enforces this by walking
  the AST, and fails the suite if the rule is broken.
- [`openapi.yaml`](openapi.yaml) is the backend contract: every endpoint, body,
  error and auth requirement the frontend expects. Each operation's
  `x-service-method` names its interface method. Change the interface, the
  types in `domain/types.ts` and `openapi.yaml` together.
- Each interface method is annotated with its endpoint (`GET /api/restaurants`,
  and so on). When the backend lands, add an HTTP implementation of the same
  interface, select it in `services/index.ts` with `VITE_SERVICE_MODE`, and
  change nothing in `pages/` or `components/`.
- Staff and admin methods take no user argument. The implementation reads the
  session token itself (`getToken`), the way an HTTP client sets a header.
- Implementations throw only `ServiceError`, whose message is already safe to
  show a user. The UI displays `friendlyErrorMessage(error)` and never the raw
  text of an unexpected exception (spec §31).
- DTO fields are `snake_case` to match the FastAPI JSON, so the HTTP client needs
  no renaming layer. Keep it that way.

### The mock

- `createMockService` enforces the real rules: validation, role checks, the
  status transition table, and the automatic no-show sweep (run on every call,
  because there is no background worker). Treat it as the backend's executable
  spec. `mockService.test.ts` covers the §33 backend test list and should port
  to pytest almost scenario for scenario.
- In the browser, state is persisted in `localStorage` under
  `waitwise.mockdb.v1`, so an eater tab and a staff tab share one waitlist and
  see each other through polling. Use **Reset demo data** in the footer, or
  delete that key, to reseed.
- The seed (spec §34) is built relative to the current time. Demo logins are
  `admin`, `bluebird` and `oakember`, and any password works. Seeded eater pages
  have readable tokens, for example `/wait/demo-sarah`.
- The mock adds 250 ms of latency in the browser so loading states are visible.
  Tests use 0.

## Rules

- **Scope is the spec.** Do not build anything in its §39 out-of-scope list
  (reservations, SMS, JWT, WebSockets, Docker, and so on). If a task seems to
  need something outside the spec, propose it and get agreement first.
- **Business rules live in `src/domain/`** as pure functions that take `now` as
  an argument. Pages format and display; they do not decide.
- **Wait quotes are frozen at join time.** `quoted_wait_minutes` and
  `estimated_ready_at` are set once and never recomputed from the restaurant's
  current wait.
- **Queue position is never stored.** It is always derived from active entries
  ordered by `joined_at` (§27).
- **Polling, not WebSockets.** Eater status and the staff dashboard poll every
  10 s through `useResource`, and the countdown ticks locally in between.
- **Ports are fixed:** frontend 3417, backend 9127 (`/api`). Keep them the same
  in config, docs and examples.
- No UI component framework. Plain CSS in `src/styles.css`, with one accent
  color.
- Dependencies are declared in `frontend/package.json` and locked in
  `package-lock.json`. Do not add one without asking.
- Never commit `node_modules/`, `dist/`, or `.env` files (see `.gitignore`).

## Testing

- Vitest + React Testing Library, with test files next to the code they cover
  (`*.test.ts[x]`).
- UI tests render the **whole app** through `renderApp(path, services)`, using a
  fresh in-memory mock (`createTestServices()`) and real routes. Log in with
  `signInAs(services, 'bluebird')` before rendering. Do not mock individual
  components.
- For time-dependent behavior, fake only `setInterval`, `clearInterval` and
  `Date`: `vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'Date'] })`.
  Leaving `setTimeout` real keeps `findBy` and `waitFor` working. Drive polls
  with `await act(() => vi.advanceTimersByTimeAsync(ms))`.
- Service-level behavior belongs in `mockService.test.ts`, which uses its own
  hand-cranked clock. Pure rules belong in `domain/*.test.ts`, with no rendering.
- **Prove a new test can fail.** Break the line it defends, watch it go red, then
  restore the line. A test that no mutation can fail is decoration.
- Use `userEvent.setup({ delay: null })`. Full-app renders are slow on some
  machines, so timeouts are raised (20 s per test, 5 s for `findBy`) in
  `vite.config.ts` and `src/test/setup.ts`.

## Next phases (not started)

- **Phase 3:** a FastAPI backend in `module2/backend/` on port 9127, implementing
  spec §24. Add an HTTP `WaitWiseService`, add pytest to `test:all`, and port
  the scenarios in `mockService.test.ts`.
- **Phase 4:** SQLAlchemy models (§25), with SQLite by default and the database
  URL taken from the environment.
