# ai-dev-tools-zoomcamp

Coursework for the AI Dev Tools Zoomcamp. Each module is a self-contained project
in its own numbered folder.

| Module | Project | Stack |
|---|---|---|
| [Module 1](#module-1--city-journal) | City Journal: a personal place-journal CLI | Python, Django, SQLite |
| [Module 2](#module-2--waitwise) | WaitWise: a restaurant waitlist manager | React, TypeScript, Vite, FastAPI |
| [Module 3](#module-3--agent-relay) | Agent Relay: containerize and deploy a task relay for agents | FastAPI, PostgreSQL, Docker, Compose, Kubernetes (kind), GitHub Actions (act) |

## Module 1 — City Journal

A personal place journal for a city I just moved to. It is **memory, not
discovery**: it remembers places I have been or want to go, and lets me find them
again when I only half-remember them — *"where was that coffee shop with the good
wifi?"*

It is deliberately not a recommender, not a "what should I do today" tool, and
not a neighborhood guide.

### Features

1. **Capture a place in one line.** Name, neighborhood, free-text note, tags, and
   an optional rating — low enough friction to log a spot while still standing
   outside it.
2. **Find it again when I only half-remember it.** Typo-tolerant ranked search
   across name, note, tags, and neighborhood, which never comes back empty: below
   the confidence threshold it offers the closest 3 as weak matches.
3. **Want-to-go → been-there loop.** Every place is `wishlist` or `visited`.
   `todo` shows what I still owe the city; `visit` flips a place over and takes
   the note and rating while the memory is fresh.
4. **Nudges and coverage.** `surprise` picks somewhere to go, favoring the
   never-visited and long-neglected. `stats` shows how much of the city I have
   actually touched.

### Stack

Python 3.13 + Django 6.1 with SQLite, managed with [uv](https://docs.astral.sh/uv/).
The interface is a CLI built from Django management commands
(`uv run python manage.py add`, `find`, `todo`, `visit`, `surprise`, `stats`),
with the stock Django admin as an optional web window onto the same data. Fully
offline — no API keys, no network calls, no geocoding.

### Usage

Setup, once:

```
git clone https://github.com/elugardo/ai-dev-tools-zoomcamp.git
cd ai-dev-tools-zoomcamp/module1
uv sync
uv run python manage.py migrate
```

Every command runs as `uv run python manage.py <command>` from `module1/`. There
is no virtualenv to activate.

**Record a place.** Pass a rating and it files the place as `visited` — you can
only rate somewhere you have been. Leave the rating off and it lands on the
wishlist.

```
$ uv run python manage.py add "Tartine Bakery" --neighborhood Mission --tag bakery --note "morning bun is the whole point" --rating 5
Added "Tartine Bakery" (Mission) - visited, rating 5, tags: bakery

$ uv run python manage.py add "Swan Oyster Depot" --neighborhood "Nob Hill" --tag seafood --note "eighteen stools, cash only"
Added "Swan Oyster Depot" (Nob Hill) - wishlist, tags: seafood
```

**Find it again when you half-remember it.** Search is typo-tolerant and covers
the name, note, tags and neighborhood:

```
$ uv run python manage.py find "tartene bakry"
Tartine Bakery - Mission - visited - rating 5 - morning bun is the whole point
```

A search always answers. If nothing scores well enough, it labels its guesses as
guesses instead of presenting them as a match:

```
$ uv run python manage.py find "bookstore"
No strong match. Closest 3:
Sightglass Coffee - SoMa - visited - rating 4 - quiet upstairs
Swan Oyster Depot - Nob Hill - wishlist - eighteen stools, cash only
Tartine Bakery - Mission - visited - rating 5 - morning bun is the whole point
```

Narrow it with `--tag`, `--status`, `--neighborhood` or `--limit`:

```
$ uv run python manage.py find "coffee" --status visited
Sightglass Coffee - SoMa - visited - rating 4 - quiet upstairs
```

**See what you still owe the city, then cross one off.**

```
$ uv run python manage.py todo
2 places on the wishlist, oldest first:
Swan Oyster Depot - Nob Hill - tags: seafood - eighteen stools, cash only
Sightglass Coffee - SoMa - tags: coffee, wifi - mezzanine has outlets

$ uv run python manage.py visit "Sightglass" --note "quiet upstairs" --rating 4
Visited Sightglass Coffee - SoMa - visited - rating 4 - quiet upstairs - stamped 2026-09-08 08:41 UTC
```

You do not have to type the full name: `visit` tries an exact match first and
falls back to a partial one, ignoring case, so `"Sightglass"` reaches
`Sightglass Coffee`. Where a name could mean two places, it lists them and stops
instead of picking one.

**Get pushed out of your rut.** `surprise` draws one place at random, with the
odds tilted towards somewhere you have not been lately, and shows its reasoning:

```
$ uv run python manage.py surprise
Go here: Tartine Bakery - Mission - visited - rating 5 - tags: bakery
Why: visited, date unknown
Note: morning bun is the whole point
```

That is one draw — run it again and you will get a different place. `--seed`
pins the choice if you want the same one back.

**See how much of the city you have actually touched.**

```
$ uv run python manage.py stats
Counts:
  total places  3
  visited       2
  wishlist      1

Neighborhoods touched: 3

Places per neighborhood:
  Mission   1
  Nob Hill  1
  SoMa      1

Top tags:
  bakery   1
  coffee   1
  seafood  1
  wifi     1
```

Every command takes `--help`. There is also a Django admin at
`localhost:8000/admin/` after `createsuperuser` and `runserver`, as a web window
onto the same data.

A longer walkthrough against a seven-place journal, with the full flag reference,
is in [`module1/README.md`](module1/README.md).

### Layout

```
module1/
├── cityjournal/    Django project (settings, urls, wsgi)
├── places/         the app — models, admin, management commands
├── _docs/          plan, backlog, process, testing guidelines
├── manage.py
├── pyproject.toml  dependencies (uv)
└── uv.lock
```

### Documents

| File | What it holds |
|---|---|
| [`module1/_docs/plan.md`](module1/_docs/plan.md) | Scope: the data model, the commands, and what is explicitly out |
| [`module1/_docs/tasks.md`](module1/_docs/tasks.md) | The ten-task backlog, mirrored as issues [#1–#10](https://github.com/elugardo/ai-dev-tools-zoomcamp/issues) |
| [`module1/_docs/process.md`](module1/_docs/process.md) | How work is picked up, branched, committed, and closed |
| [`module1/_docs/testing-guidelines.md`](module1/_docs/testing-guidelines.md) | Testing conventions |
| [`AGENTS.md`](AGENTS.md) | Commands and rules for coding agents working in module 1 (module 2 and module 3 have [their](module2/AGENTS.md) [own](module3/AGENTS.md)) |

### Status

Module 1 is built: all six commands (`add`, `find`, `todo`, `visit`, `surprise`,
`stats`) and the admin are working, with the test suite green.

566 tests, and the walkthrough in [`module1/README.md`](module1/README.md) is
verified to reproduce from a clean clone.

## Module 2 — WaitWise

A simple restaurant waitlist manager with three experiences:

- **Eaters** find a restaurant, see its current wait, join the list without an
  account, and watch their position and remaining wait update live. The page
  tells them when their table is ready.
- **Restaurant staff** run the active queue. They add walk-ins, notify, seat,
  cancel or mark no-shows in any order, change the quoted wait, and pause online
  joining.
- **Admins** create, edit, activate and deactivate restaurants and their logins.

It is deliberately not a reservation, table-management or POS system. The full
spec is in
[`module2/_docs/`](module2/_docs/WaitWise_Restaurant_Waitlist_Manager_Specification.md).

### Status: frontend, backend and database complete

The course builds this in phases: scope, then frontend, then backend, then
database. All of them are done.

- **Phase 2, frontend:** done.
- **Phase 3, backend:** done. A FastAPI server.
- **Phase 4, database:** done. The backend persists to a database through
  SQLAlchemy. It uses SQLite by default, and `WAITWISE_DATABASE_URL` selects
  another database. The code is database-agnostic, so adding Postgres later
  needs a driver and a URL, not code changes.

The two halves meet at a single contract, [`module2/openapi.yaml`](module2/openapi.yaml):

- **Frontend.** Every backend call goes through one services layer,
  `WaitWiseService`, a TypeScript interface with one method per endpoint. It has
  two implementations. The default HTTP client calls the FastAPI backend. An
  in-browser mock enforces the same rules, so the UI still runs with no server
  (`npm run dev:mock`).
- **Backend.** Split into routers, models, store, auth and database modules,
  and serves exactly the operations in `openapi.yaml`.
- **Tests on both sides** check the code against the spec, so the two can't
  drift apart.

**Authentication goes beyond the course spec**, which ignores passwords.
Restaurant and admin logins check scrypt-hashed passwords, and each login
returns a bearer token. A token expires after 12 hours, and changing a
restaurant's password revokes its tokens.

### Stack

- **Frontend:** React 19, TypeScript, Vite, React Router, plain CSS; Vitest and
  React Testing Library
- **Backend:** Python 3.13, FastAPI, Pydantic, SQLAlchemy 2.0, uvicorn; pytest
  with FastAPI's TestClient; managed with [uv](https://docs.astral.sh/uv/)
- **Database:** SQLite by default (`backend/waitwise.db`), selected with
  `WAITWISE_DATABASE_URL`; designed to add PostgreSQL next

Needs Node 20.19+ or 22.12+, Python 3.13, and uv.

### Getting started

#### 1. Install the prerequisites

| Tool | Version | Check | If it's missing |
|---|---|---|---|
| Node.js | 20.19+ or 22.12+ | `node --version` | [nodejs.org](https://nodejs.org/) or nvm |
| uv | any recent | `uv --version` | `winget install astral-sh.uv` (Windows) or `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Python | 3.13 | `uv python list` | Nothing to do: `uv` downloads 3.13 on first `uv sync` if needed |
| GNU make | optional | `make --version` | `winget install ezwinports.make` (Windows; open a new terminal afterwards). macOS and Linux usually have it |

`make` is only a shortcut. Every `make` target below has an `npm run`
equivalent, so you can skip it.

#### 2. Get the code and install dependencies

```
git clone https://github.com/elugardo/ai-dev-tools-zoomcamp.git
cd ai-dev-tools-zoomcamp/module2
make install           # or: npm run setup
```

This installs the frontend packages with npm and the backend packages with uv.
Run it again whenever you pull changes that touch `package.json` or
`pyproject.toml`.

#### 3. Start the app

With make, one terminal runs both servers, and Ctrl+C stops both:

```
make dev
```

Without make, use two terminals, both in `module2/`:

```
npm run dev:backend    # terminal 1: the API
npm run dev            # terminal 2: the web app
```

The first backend start creates the database (`module2/backend/waitwise.db`)
and loads the demo data. Then open:

| What | URL |
|---|---|
| The app | http://localhost:3417 |
| Interactive API docs (Swagger) | http://localhost:9127/docs |
| The API itself | http://localhost:9127/api |

To look at the UI with no backend at all, run `make mock` (or
`npm run dev:mock`) instead. The app then runs on an in-browser copy of the API,
stored in your browser. **Reset demo data** in the page footer rebuilds that
copy.

#### 4. Log in

Every demo account uses the password `password`:

| Username | Who | Lands on |
|---|---|---|
| `admin` | WaitWise administrator | Restaurants list (`/admin`) |
| `bluebird` | Staff at Bluebird Cafe | Bluebird's dashboard (`/restaurant/dashboard`) |
| `oakember` | Staff at Oak & Ember | Oak & Ember's dashboard |

Eaters never log in. Use **Staff login** in the top-right corner for these
accounts.

### Using WaitWise

**Try the whole flow in two minutes.** Use two browser windows, or one normal
window and one private window, so each keeps its own login.

1. In window A, open http://localhost:3417, choose **Bluebird Cafe**, and join
   as a party of 4. You land on your personal status page.
2. In window B, log in as `bluebird` / `password`. Your party is at the bottom of
   **Active waitlist**.
3. In window B, click **Notify** on your party. Within 10 seconds window A shows
   **Your table is ready!**, with no refresh.
4. Click **Seat**. The party moves to **Today's History**.

#### As an eater (no account)

- **Find a restaurant.** The home page lists every active restaurant with its
  current wait. **View Waitlist** opens its page.
- **Join.** Enter your name, mobile number and party size (1–20), plus optional
  notes, then click **Join Waitlist**.
  - The button is disabled if the restaurant has paused online joining, in which
    case the page says "Online waitlist currently unavailable".
- **Your status page** (`/wait/<token>`) is your ticket, so **bookmark it**.
  - It shows your place in line and the estimated remaining wait. That estimate
    counts down live and never goes below "Ready soon".
  - The page checks for updates every 10 seconds, and it switches to **Your
    table is ready!** when the restaurant notifies you.
- **Leave.** **Leave Waitlist**, then **Yes, leave**, gives up your spot.

The quoted wait is fixed when you join. If the restaurant later changes its
wait, your estimate does not move.

#### As restaurant staff

The dashboard refreshes itself every 10 seconds.

- **The header** shows the current wait, the number of parties waiting, and
  whether online joining is open.
  - **Change Wait** sets the wait quoted to new guests only; parties already in
    line keep their quote.
  - **Add Walk-In** adds someone at the door. Walk-ins are allowed even while
    online joining is paused.
  - **Accepting Online Waitlist** pauses or resumes joining from the website.
- **Active waitlist** lists parties in the order they joined. You can act on any
  party, not just the first:
  - **Notify** tells the guest their table is ready. The row then shows
    "Notified N min ago".
  - **Seat**, **Cancel** or **No Show** finish the party and move it to
    **Today's History**.
  - A notified party who doesn't arrive becomes a **No Show** automatically once
    the restaurant's no-show timeout passes (10 minutes by default).
- **Today's History** lists everyone seated, canceled or marked no-show today.

#### As an admin

- **Restaurants** lists every restaurant, including inactive ones, with its
  login username.
- **Add Restaurant** creates a restaurant and its staff login. The new login's
  password needs at least 8 characters. New restaurants start with a 30-minute
  wait, a 10-minute no-show timeout, and active status.
- **Edit** changes details and the login. Leave the password blank to keep the
  current one. Setting a new password signs that restaurant's staff out
  everywhere.
- **Deactivate** hides a restaurant from the public list and stops online
  joining; its staff can still use their dashboard. **Activate** reverses it.

#### Handy demo links

The demo data includes guests whose status pages have readable links:
http://localhost:3417/wait/demo-sarah and `/wait/demo-james` (waiting), and
`/wait/demo-chen` (already seated). Open one next to the Bluebird dashboard and
click **Notify** to watch the status page change.

### Commands

Run these from `module2/`.

| make | npm equivalent | What it does |
|---|---|---|
| `make` | | List all targets |
| `make install` | `npm run setup` | Install frontend and backend dependencies |
| `make dev` | both commands below, in two terminals | Run the backend and the frontend together |
| `make backend` | `npm run dev:backend` | Run only the API on port 9127, reloading when code changes |
| `make frontend` | `npm run dev` | Run only the web app on port 3417 |
| `make mock` | `npm run dev:mock` | Run the web app on the in-browser mock, no backend |
| `make test` | `npm run test:all` | Run all frontend and backend tests |
| `make test-frontend` / `make test-backend` | `npm run test:frontend` / `npm run test:backend` | Run one side's tests |
| `make build` | `npm run build` | Typecheck and build the frontend for production |
| `make db-init` | `uv --directory backend run python -m app.manage init-db` | Create any missing database tables |
| `make db-reset` | `uv --directory backend run python -m app.manage reset-db` | **Delete all data**, recreate the tables and reload the demo data |

### Configuration

Everything has a working default. Set these only to change behavior.

**Backend.** Set these in the shell before `make backend` or `make dev`:

| Variable | Default | Purpose |
|---|---|---|
| `WAITWISE_DATABASE_URL` | `sqlite:///<module2>/backend/waitwise.db` | Which database to use; any SQLAlchemy URL |
| `WAITWISE_SEED_DEMO_DATA` | `true` | Load the demo data into an **empty** database |
| `WAITWISE_TOKEN_TTL_MINUTES` | `720` (12 hours) | How long a login lasts |
| `WAITWISE_CORS_ORIGINS` | `http://localhost:3417,http://127.0.0.1:3417` | Browser origins allowed to call the API |

```
# PowerShell
$env:WAITWISE_DATABASE_URL = "sqlite:///C:/data/waitwise.db"; make backend

# macOS / Linux / Git Bash (note the four slashes for an absolute path)
WAITWISE_DATABASE_URL=sqlite:////home/me/waitwise.db make backend
```

**Frontend.** Copy `module2/frontend/.env.example` to
`module2/frontend/.env.local` and edit it:

| Variable | Default | Purpose |
|---|---|---|
| `VITE_API_BASE_URL` | `http://localhost:9127/api` | Where the web app sends API requests |
| `VITE_SERVICE_MODE` | `http` | `http` uses the backend; `mock` uses the in-browser mock |

If you move the frontend to a different address, add that address to
`WAITWISE_CORS_ORIGINS`, or the browser will block its requests.

### The database

- **Where data lives.** By default, everything (restaurants, logins, waitlists
  and sessions) is in the SQLite file `module2/backend/waitwise.db`. It is
  ignored by git. Stopping or restarting the backend keeps all data, and
  logged-in users stay logged in.
- **Demo data** is added only when the database is empty, so it is never
  duplicated. Its times are fixed when it's added, so after a while the demo
  queue looks hours old. Run `make db-reset` for a fresh one. That deletes
  everything else in the database too.
- **Starting over.** Run `make db-reset`, or stop the backend and delete
  `waitwise.db`; it is recreated on the next start.
- **A throwaway database** that vanishes when the backend stops:
  `WAITWISE_DATABASE_URL=sqlite://`.
- **Other databases.** The backend is database-agnostic. For PostgreSQL, install
  a driver (`uv --directory backend add "psycopg[binary]"`) and set a URL like
  `postgresql+psycopg://user:password@localhost/waitwise`. Postgres support
  hasn't been tested against a live server yet; see
  [`module2/_docs/progress.md`](module2/_docs/progress.md).
- **There are no migrations yet.** Tables are created at startup, but existing
  tables are never altered. If a code change modifies a table, run
  `make db-reset`.

### Troubleshooting

| Symptom | Cause and fix |
|---|---|
| The app says **"We can't reach WaitWise right now."** | The backend isn't running, or `VITE_API_BASE_URL` points elsewhere. Start it with `make backend` and check that http://localhost:9127/docs loads. If the backend is running, check its terminal for an error. |
| `Port 3417 is already in use`, or the backend fails to bind port 9127 | A previous run is still going. Stop it with Ctrl+C in its terminal, or find the process. PowerShell: `Get-NetTCPConnection -LocalPort 3417 -State Listen \| Select OwningProcess`, then `Stop-Process -Id <id>`. macOS/Linux: `lsof -ti:3417 \| xargs kill` |
| `The requested module 'node:util' does not provide an export named 'styleText'` | Node is too old. Install Node 20.19+ or 22.12+ and check `node --version` in the same terminal. |
| `make: command not found` right after installing make | Open a new terminal so it picks up the updated `PATH`. |
| **"Incorrect username or password."** for a demo account | The password is `password`. If an admin changed it, run `make db-reset` to restore the demo accounts. |
| **"Your session has expired. Please log in again."** | Logins last 12 hours, and changing a restaurant's password signs its staff out. `make db-reset` also clears every session. Log in again. |
| The demo parties have been "waiting" for hours or days | The demo data's times were fixed when it was loaded. Run `make db-reset`. |
| A restaurant's page says **"Online waitlist currently unavailable"** | Its staff turned off **Accepting Online Waitlist**, or an admin deactivated it. |
| After pulling code, pages fail to load, and the **backend terminal** shows `no such column` or `no such table` | The table layout changed and there are no migrations. Run `make db-reset`. |
| `database is locked` | Another program (such as a SQLite browser) has `waitwise.db` open for writing. Close it. |

**Trying the API directly.** Open http://localhost:9127/docs:

1. Call `POST /api/auth/login` with `{"username": "bluebird", "password": "password"}`.
2. Copy the `token` from the response.
3. Click **Authorize** and paste it in.

The restaurant and admin endpoints then work from the same page.

### Layout

```
module2/
├── _docs/          the WaitWise specification, and progress.md: what shipped, decisions, open items
├── openapi.yaml    the API contract both halves are tested against
├── frontend/
│   └── src/
│       ├── domain/     business rules: queue, wait time, validation (pure functions)
│       ├── services/   WaitWiseService interface + http client + in-browser mock
│       ├── pages/      one component per route
│       ├── components/ shared UI
│       ├── auth/ hooks/ test/
│       └── styles.css
│   └── .env.example    frontend settings: API URL, http or mock mode
├── backend/
│   ├── app/
│   │   ├── main.py       app factory: database setup, CORS, error handlers, routers under /api
│   │   ├── routers/      auth, public (restaurants + eater waitlist), restaurant, admin
│   │   ├── models.py     Pydantic request/response models
│   │   ├── store.py      every query and write, over a SQLAlchemy session
│   │   ├── db.py         engine, sessions, UTC timestamps - all database-specific code
│   │   ├── tables.py     SQLAlchemy ORM tables
│   │   ├── auth.py       password hashing, bearer tokens, role checks
│   │   ├── rules.py      pure waitlist rules
│   │   └── seed.py, manage.py, config.py, errors.py, serializers.py
│   ├── tests/
│   └── pyproject.toml, uv.lock
├── Makefile        make install / dev / test / db-reset ... (run `make` for the list)
├── package.json    the same commands as npm scripts
├── AGENTS.md       commands and rules for coding agents
└── CLAUDE.md       points to AGENTS.md
```

### Tests

```
cd module2
npm run test:all
```

This runs 357 tests: 163 in the frontend (Vitest), then 194 in the backend
(pytest).

- **Backend:**
  - **Auth:** password hashing, login, token expiry and revocation, and role
    checks on every protected endpoint.
  - **Endpoints:** every endpoint, including the backend scenarios from spec §33.
  - **No-show timing:** the automatic no-show boundary, driven by a controllable
    clock.
  - **Contract:** drives all 15 operations and validates each status code and
    response body against `openapi.yaml`.
  - **Database:** persistence across restarts, rollback of failed requests,
    safe concurrent updates, and portability checks, including compiling the
    schema for PostgreSQL, MySQL and SQL Server.
- **Frontend:**
  - **Domain rules:** the business rules as pure functions.
  - **HTTP client:** every request and error mapping, checked against the
    service interface's endpoint annotations.
  - **Full-app UI:** the scenarios from spec §32, rendered against the mock.
  - **Architecture:** an AST test that keeps network calls inside the services
    layer.

Each key rule on both sides was checked by deliberately breaking it and
confirming a test failed.

### Homework Information

2. **What is the name you chose?** WaitWise
3. **What is the SHA1 hash for this commit?** Filled in at submission, from
   `git rev-parse HEAD`.
4. **Which command do you use to start the frontend?** `npm run dev`, run from
   `module2/frontend/` (or from `module2/`).
5. **Which command do you use to start the backend?**
   `uv run uvicorn app.main:app --reload --port 9127`, run from
   `module2/backend/`. Inside an activated virtualenv, plain
   `uvicorn app.main:app --reload --port 9127` also works. From `module2/`,
   `npm run dev:backend` does the same.
6. **Which URL does the frontend use to talk to the backend?**
   `http://localhost:9127/api`, set through `VITE_API_BASE_URL`.
7. **Which command do you use for running tests?** `npm run test:all`, run from
   `module2/`. It runs the frontend Vitest suite and then the backend pytest
   suite.

The course homework and FAQ URLs will be added once they are published.

## Module 3 — Agent Relay

[Homework 3](https://github.com/DataTalksClub/ai-dev-tools-zoomcamp/blob/main/cohorts/2026/homework/03-deployment/homework.md)
is about deployment, not features. The app is
[Agent Relay](https://github.com/alexeygrigorev/agent-relay), a small
messaging system for software agents: one agent sends a task, a worker claims
it through the HTTP API, and the worker reports the result. The database
stores the tasks and every delivery attempt, and a dashboard shows the
lifecycle. The protocol is in [`module3/SPEC.md`](module3/SPEC.md).

The homework goes in six steps, and each is done and verified:

| Step | What was done | Where |
|---|---|---|
| 1. Understand the project | Ran the starter, traced a task by hand | |
| 2. Integration test | Two agents exchange a task and its result over real HTTP against the real DB | [`test_integration.py`](module3/test_integration.py) |
| 3. Containerize | Image `agent-relay:local`, uvicorn on `0.0.0.0`, health check on `/ready`, non-root | [`Dockerfile`](module3/Dockerfile) |
| 4. Compose + PostgreSQL | The app runs on PostgreSQL; `api` + `postgres` services | [`compose.yaml`](module3/compose.yaml), [`database.py`](module3/database.py), [`storage.py`](module3/storage.py) |
| 5. Kubernetes | Manifests for kind: Deployment, StatefulSet with a PVC, Services, probes | [`k8s/`](module3/k8s/) |
| 6. CI/CD | Tests on PostgreSQL, then a uniquely tagged image is built and rolled out to kind, only if tests pass; run locally with act | [`.github/workflows/ci.yml`](.github/workflows/ci.yml) |

### Running it

Everything runs from `module3/` and needs [uv](https://docs.astral.sh/uv/),
Docker Desktop, and for steps 5 and 6 `kind`, `kubectl` and `act`.

| Goal | Commands |
|---|---|
| Locally on SQLite | `uv sync` then `uv run uvicorn main:app --reload`; dashboard at http://127.0.0.1:8000/ |
| Run the tests | `uv run pytest -q` (the integration test skips itself when no server is running) |
| The integration test against any running relay | `$env:RELAY_BASE_URL = "http://127.0.0.1:8080"; uv run pytest -q test_integration.py` |
| One container on SQLite | `docker build -t agent-relay:local .` then `docker run -d --name agent-relay -p 8080:8000 -v agent-relay-data:/data agent-relay:local` |
| API + PostgreSQL with Compose | `docker compose up --build -d`; API on http://127.0.0.1:8080, PostgreSQL on 5432 |
| Kubernetes on kind | `kind create cluster --name agent-relay`, `kind load docker-image agent-relay:local --name agent-relay`, `kubectl apply -k k8s/`, then `kubectl -n agent-relay port-forward svc/agent-relay 8080:8000` |
| The CI workflow, locally | From the repo root: `act -W .github/workflows/ci.yml` (the Compose stack must be down first) |

To try the task flow by hand, register two agents with
`POST /api/v1/agents`, send a task as one, claim and complete it as the other,
and read the result as the sender. Paste an agent's token into the dashboard to
watch its tasks. The exact requests are in [`module3/README.md`](module3/README.md).

### Where this differs from what the homework expects

- **No GitHub fork.** The starter was copied into `module3/` (at upstream
  commit `0a2895b`, without its git history) so it lives in this monorepo like
  the other modules. The upstream README was kept and only its "no
  PostgreSQL/Docker" paragraph replaced.
- **The workflow lives at the repo root**, `.github/workflows/ci.yml`, because
  that is where GitHub looks in a monorepo. It runs with
  `working-directory: module3` and only on pushes that touch `module3/**`.
- **Host port 8080, not 8000.** The container, the Compose stack and the kind
  port-forward all publish on 8080, so they can run next to a dev server on
  8000. Inside the container the API still listens on 8000.
- **PostgreSQL was added, not swapped in.** SQLite stays the default and the
  test suite still runs on it. `RELAY_DATABASE_URL` picks PostgreSQL. What
  SQLite got from its `BEGIN IMMEDIATE` writer lock, PostgreSQL gets from row
  locks: claims use `FOR UPDATE SKIP LOCKED`, and recovery, heartbeat and
  completion lock the task first and then the attempt, so they cannot
  deadlock. The starter's concurrent-claims test passes on PostgreSQL.
- **The integration test is HTTP-only.** It never imports the app, so the same
  test runs against the dev server, the container, Compose, kind and CI, just
  by setting `RELAY_BASE_URL`. It only adds rows, so it is safe on a database
  with data, and it skips when nothing is listening.
- **Kubernetes details.** The manifests are a kustomization in an
  `agent-relay` namespace. PostgreSQL is a StatefulSet with a 1Gi PVC (data
  survives deleting the pod). The API runs two replicas, which the PostgreSQL
  locking makes safe. The API gained a `startupProbe` and a
  `RELAY_DB_WAIT_SECONDS` setting after the first deploy showed the API
  crashing while PostgreSQL was still pulling its image.
- **CI networking.** act runs each job on the Docker host's network, so the
  workflow reaches the PostgreSQL service and the kind API server on
  `localhost`, the same as a GitHub-hosted runner. Under act the deploy job
  reuses the local `agent-relay` cluster; on GitHub it creates a throwaway
  one. Each run builds `agent-relay:<short sha>-<timestamp>` and sets that tag
  in the kustomization inside CI's copy of the repo, so the committed manifests
  always say `local`.
- **The dashboard heading is `Agent Relay v2`** in the repo: that is the
  homework's final state, deployed through the workflow.
- **Tools.** `kind` and `act` were installed as single binaries in `~/bin`, and
  `~/.actrc` selects the `catthehacker/ubuntu:act-latest` runner image so act
  never prompts.

### Homework answers

| Question | Answer |
|---|---|
| 1. Architecture | Agents claim tasks from a DB through an HTTP API |
| 2. Status the sender sees after the result is submitted | `completed` |
| 3. Docker option that publishes a port | `-p` |
| 4. Hostname for the `postgres` service in Compose | `postgres` |
| 5. Resource that keeps replicas running and manages updates | `Deployment` |
| 6. If a test fails in the workflow | Keep the existing version running and stop the deployment |
