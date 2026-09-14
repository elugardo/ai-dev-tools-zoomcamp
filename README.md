# ai-dev-tools-zoomcamp

Coursework for the AI Dev Tools Zoomcamp. Each module is a self-contained project
in its own numbered folder.

| Module | Project | Stack |
|---|---|---|
| [Module 1](#module-1--city-journal) | City Journal: a personal place-journal CLI | Python, Django, SQLite |
| [Module 2](#module-2--waitwise) | WaitWise: a restaurant waitlist manager | React, TypeScript, Vite (mock backend) |

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

### Status: frontend complete, running on a mock backend

The course builds this in phases: scope, then frontend, then backend, then
database. **Phase 2 (frontend) is done.** Every screen and workflow in the spec
works in the browser today, with no server.

That works because **every backend call goes through one services layer**:

- `WaitWiseService` is a TypeScript interface with one method per API endpoint.
- The app currently runs on an in-browser **mock implementation** of that
  interface. The mock enforces the real rules: validation, status transitions,
  role checks, and automatic no-shows.
- An architecture test fails the build if a page or component calls `fetch` or
  imports the mock directly.

When the FastAPI backend arrives (Phase 3), an HTTP implementation replaces the
mock and no screen changes.

The mock keeps its data in `localStorage`. Open an eater page in one tab and the
staff dashboard in another, and each sees the other's changes within 10 seconds.

### Stack

- **Frontend:** React 19, TypeScript, Vite, React Router, plain CSS
- **Tests:** Vitest and React Testing Library
- **Planned for Phases 3–4:** FastAPI, SQLAlchemy, Pydantic and pytest, on
  SQLite locally and PostgreSQL in production

Needs Node 20.19+ or 22.12+.

### Usage

```
cd ai-dev-tools-zoomcamp/module2
npm run setup        # installs frontend dependencies
npm run dev          # http://localhost:3417
```

Demo accounts come from the seed data, and any password works:

| Username | Role | Lands on |
|---|---|---|
| `admin` | Admin | `/admin` |
| `bluebird` | Bluebird Cafe staff | `/restaurant/dashboard` |
| `oakember` | Oak & Ember staff | `/restaurant/dashboard` |

A quick demo of the core scenario:

1. Open `http://localhost:3417`, pick **Bluebird Cafe**, and join as a party of 4.
   You land on your status page with your position and remaining wait.
2. In a second tab, log in as `bluebird`. Your party is at the bottom of the
   queue.
3. Click **Notify** on your party. Within 10 seconds the first tab shows
   **Your table is ready!** without a refresh.
4. Click **Seat**. The party moves to **Today's History**.

Seeded eater pages have readable links, such as
`http://localhost:3417/wait/demo-sarah`. **Reset demo data** in the footer
reseeds everything.

### Layout

```
module2/
├── _docs/          the WaitWise specification
├── frontend/
│   └── src/
│       ├── domain/     business rules: queue, wait time, validation (pure functions)
│       ├── services/   WaitWiseService interface + mock implementation
│       ├── pages/      one component per route
│       ├── components/ shared UI
│       ├── auth/ hooks/ test/
│       └── styles.css
├── package.json    root scripts: setup, dev, build, test:all
├── AGENTS.md       commands and rules for coding agents
└── CLAUDE.md       points to AGENTS.md
```

### Tests

```
cd module2
npm run test:all
```

The suite has 116 tests:

- **Domain rules:** wait-time math using the spec's worked example, queue order,
  transitions, the no-show boundary, and form validation.
- **Service contract:** the backend behaviors from spec §33, run against the mock
  so they can be ported to pytest.
- **Full-app UI:** every scenario in spec §32, including a fake-clock test that
  the eater page picks up a Notify through polling.
- **Architecture:** the services-layer boundary.

Each key rule was checked by deliberately breaking it and confirming a test
failed.

### Homework Information

2. **What is the name you chose?** WaitWise
3. **What is the SHA1 hash for this commit?** Filled in at submission, from
   `git rev-parse HEAD`.
4. **Which command do you use to start the frontend?** `npm run dev`, run from
   `module2/frontend/` (or from `module2/`).
5. **Which command do you use to start the backend?** Not built yet. Phase 3 will
   use `uvicorn app.main:app --reload --port 9127`, run from `module2/backend/`.
6. **Which URL does the frontend use to talk to the backend?**
   `http://localhost:9127/api`, set through `VITE_API_BASE_URL`. Until the
   backend exists, the app uses the mock services layer.
7. **Which command do you use for running tests?** `npm run test:all`, run from
   `module2/`. For now it runs the frontend suite; pytest joins it in Phase 3.

The course homework and FAQ URLs will be added once they are published.
