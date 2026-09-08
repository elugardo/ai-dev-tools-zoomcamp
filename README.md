# ai-dev-tools-zoomcamp

Coursework for the AI Dev Tools Zoomcamp. Each module is a self-contained project
in its own numbered folder.

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
| [`AGENTS.md`](AGENTS.md) | Commands and rules for coding agents working in this repo |

### Status

Module 1 is built: all six commands (`add`, `find`, `todo`, `visit`, `surprise`,
`stats`) and the admin are working, with the test suite green.

566 tests, and the walkthrough in [`module1/README.md`](module1/README.md) is
verified to reproduce from a clean clone.
