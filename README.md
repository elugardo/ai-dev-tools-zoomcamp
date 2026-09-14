# ai-dev-tools-zoomcamp

Coursework for the AI Dev Tools Zoomcamp. Each module is a self-contained project
in its own numbered folder.

| Module | Project | Stack |
|---|---|---|
| [Module 1](#module-1--city-journal) | City Journal: a personal place-journal CLI | Python, Django, SQLite |
| [Module 2](#module-2--waitwise) | WaitWise: a restaurant waitlist manager | React, TypeScript, Vite, FastAPI |

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
| [`AGENTS.md`](AGENTS.md) | Commands and rules for coding agents working in module 1 (module 2 has [its own](module2/AGENTS.md)) |

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

### Status: frontend and backend complete

The course builds this in phases: scope, then frontend, then backend, then
database.

- **Phase 2, frontend:** done.
- **Phase 3, backend:** done. A FastAPI server with an in-memory store, seeded
  with demo data on startup.
- **Phase 4, database:** SQLAlchemy, not started.

The two halves meet at a single contract, [`module2/openapi.yaml`](module2/openapi.yaml):

- **Frontend.** Every backend call goes through one services layer,
  `WaitWiseService`, a TypeScript interface with one method per endpoint. It has
  two implementations. The default HTTP client calls the FastAPI backend. An
  in-browser mock enforces the same rules, so the UI still runs with no server
  (`npm run dev:mock`).
- **Backend.** Split into routers, models, store and auth modules, and serves
  exactly the operations in `openapi.yaml`.
- **Tests on both sides** check the code against the spec, so the two can't
  drift apart.

**Authentication goes beyond the course spec**, which ignores passwords.
Restaurant and admin logins check scrypt-hashed passwords, and each login
returns a bearer token. A token expires after 12 hours, and changing a
restaurant's password revokes its tokens.

### Stack

- **Frontend:** React 19, TypeScript, Vite, React Router, plain CSS; Vitest and
  React Testing Library
- **Backend:** Python 3.13, FastAPI, Pydantic, uvicorn; pytest with FastAPI's
  TestClient; managed with [uv](https://docs.astral.sh/uv/)
- **Planned for Phase 4:** SQLAlchemy, with SQLite locally and PostgreSQL in
  production

Needs Node 20.19+ or 22.12+, Python 3.13, and uv.

### Usage

```
cd ai-dev-tools-zoomcamp/module2
npm run setup          # frontend (npm) and backend (uv) dependencies

npm run dev:backend    # terminal 1: API on http://localhost:9127 (docs at /docs)
npm run dev            # terminal 2: app on http://localhost:3417
```

To run the UI with no backend at all, use `npm run dev:mock` instead of the two
commands above.

If you have GNU make, `module2/Makefile` wraps the same commands. `make install`
installs dependencies, and `make dev` starts the backend and frontend together
in one terminal; Ctrl+C stops both. `make mock` runs the frontend on the mock,
`make test` runs every test, and `make` on its own lists all targets.

Demo accounts come from the seed data. All of them use the password `password`:

| Username | Role | Lands on |
|---|---|---|
| `admin` | Admin | `/admin` |
| `bluebird` | Bluebird Cafe staff | `/restaurant/dashboard` |
| `oakember` | Oak & Ember staff | `/restaurant/dashboard` |

A quick demo of the core scenario:

1. Open `http://localhost:3417`, pick **Bluebird Cafe**, and join as a party of 4.
   You land on your status page with your position and remaining wait.
2. In a second tab, log in as `bluebird` / `password`. Your party is at the
   bottom of the queue.
3. Click **Notify** on your party. Within 10 seconds the first tab shows
   **Your table is ready!** without a refresh.
4. Click **Seat**. The party moves to **Today's History**.

Seeded eater pages have readable links, such as
`http://localhost:3417/wait/demo-sarah`.

The backend keeps its data in memory. Restarting it resets everything to the
seed and signs everyone out.

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
├── backend/
│   ├── app/
│   │   ├── main.py       app factory: CORS, error handlers, routers under /api
│   │   ├── routers/      auth, public (restaurants + eater waitlist), restaurant, admin
│   │   ├── models.py     Pydantic request/response models
│   │   ├── store.py      in-memory store
│   │   ├── auth.py       password hashing, bearer tokens, role checks
│   │   ├── rules.py      pure waitlist rules
│   │   └── seed.py, config.py, errors.py, serializers.py
│   ├── tests/
│   └── pyproject.toml, uv.lock
├── package.json    scripts: setup, dev, dev:backend, dev:mock, build, test:all
├── AGENTS.md       commands and rules for coding agents
└── CLAUDE.md       points to AGENTS.md
```

### Tests

```
cd module2
npm run test:all
```

This runs 321 tests: 160 in the frontend (Vitest), then 161 in the backend
(pytest).

- **Backend:**
  - **Auth:** password hashing, login, token expiry and revocation, and role
    checks on every protected endpoint.
  - **Endpoints:** every endpoint, including the backend scenarios from spec §33.
  - **No-show timing:** the automatic no-show boundary, driven by a controllable
    clock.
  - **Contract:** drives all 15 operations and validates each status code and
    response body against `openapi.yaml`.
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
