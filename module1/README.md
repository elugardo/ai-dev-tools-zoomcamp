# City Journal — running it

This is the hands-on document for City Journal: how to install it, what each
command does, and a recorded session showing all six commands against a real
journal. For what the tool is, why it exists, and how the code is laid out, read
the [repo README](../README.md) first — this file assumes you already know that
and only answers "how do I run it".

Every command below is run from `module1/`, the directory this file lives in.

## Prerequisites

[uv](https://docs.astral.sh/uv/) — and nothing else. `pyproject.toml` requires
Python 3.13; uv downloads and manages that for you, so there is no separate
Python to install and no version to match by hand.

## Setup

Four steps, in this order:

```
git clone https://github.com/elugardo/ai-dev-tools-zoomcamp.git
cd ai-dev-tools-zoomcamp/module1
uv sync
uv run python manage.py migrate
```

`uv sync` creates the project environment and installs Django. `uv run` then
executes inside that environment, so **there is no virtualenv to activate** —
never `activate` anything, and never call `python manage.py` directly. Every
invocation in this file is `uv run python manage.py <command>`, identical on
Linux, macOS and Windows.

`migrate` creates `db.sqlite3` in `module1/`. That file is gitignored and it is
your journal: nothing ships with the repo, so a fresh clone starts empty.

You do **not** need a superuser to use the CLI. Creating one is only for the
admin, and it is covered [further down](#the-admin).

## The commands

Six commands, all under `uv run python manage.py`:

| Command | What it does, and its flags |
| --- | --- |
| `add "<name>"` | Records a place. `--neighborhood`, `--address`, `--note`, `--rating` (1–5), `--status` (`wishlist` or `visited`), `--tag` (repeatable). |
| `find "<query>"` | Ranked search that tolerates typos, over the name, the note, the tags and the neighborhood. `--limit` (default 5), `--status`, `--neighborhood`, `--tag` (repeatable, and ANDed). |
| `todo` | Lists the places still on the wishlist, oldest first. `--tag`, `--neighborhood` (the whole value, matched case-insensitively). |
| `visit "<name>"` | Flips a place to `visited` and stamps the time. `--note`, `--rating`. The name is matched case-insensitively: the whole name first, then as a substring. |
| `surprise` | Suggests one place to go to, biased towards the never-visited and the long-neglected. `--tag`, `--neighborhood`, `--seed` (pins the draw). |
| `stats` | Coverage report: counts, neighborhoods touched, places per neighborhood, top tags. No flags. |

Two behaviors are worth knowing before you meet them:

- **`add` without `--rating` files the place as `wishlist`.** A rating means you
  have been there, so it infers `visited`; an explicit `--status` always wins.
- **`find` never comes back empty.** When nothing clears its confidence
  threshold it prints the closest three under `No strong match. Closest 3:`, so
  a weak guess is labelled as a weak guess rather than dressed up as an answer.

For the full flag list of any command, ask it:

```
uv run python manage.py add --help
uv run python manage.py find --help
```

## A recorded session

Everything below was printed by the commands, on an empty database, in the order
shown. Run `migrate`, then paste the blocks top to bottom and you will get the
same output back — with three exceptions that legitimately vary:

- **timestamps** (the `stamped ...` line `visit` prints, and the elapsed time the
  test runner reports),
- **the place `surprise` picks**, which is random by design,
- **any absolute path**, which depends on where you cloned the repo.

### Seven places

```
uv run python manage.py add "Tartine Bakery" --neighborhood Mission --tag bakery --tag pastry --note "morning bun is the whole point, line moves fast before nine" --rating 5
uv run python manage.py add "Ritual Coffee Roasters" --neighborhood Mission --tag coffee --tag wifi --note "big communal table, good wifi, quiet before ten" --rating 4
uv run python manage.py add "Bi-Rite Creamery" --neighborhood Mission --tag dessert --tag icecream --note "salted caramel, the line wraps the corner on weekends" --rating 5
uv run python manage.py add "Swan Oyster Depot" --neighborhood "Nob Hill" --tag seafood --tag lunch --note "eighteen stools, cash only, get there before eleven"
uv run python manage.py add "Rich Table" --neighborhood "Hayes Valley" --tag dinner --tag restaurant --note "sardine chips, book two weeks out"
uv run python manage.py add "Sightglass Coffee" --neighborhood SoMa --tag coffee --tag wifi --note "mezzanine upstairs has outlets and no queue"
uv run python manage.py add "Nopa" --neighborhood "Alamo Square" --tag dinner --tag restaurant --note "late kitchen, walk-ins at the bar after ten"
```

```
Added "Tartine Bakery" (Mission) - visited, rating 5, tags: bakery, pastry
Added "Ritual Coffee Roasters" (Mission) - visited, rating 4, tags: coffee, wifi
Added "Bi-Rite Creamery" (Mission) - visited, rating 5, tags: dessert, icecream
Added "Swan Oyster Depot" (Nob Hill) - wishlist, tags: seafood, lunch
Added "Rich Table" (Hayes Valley) - wishlist, tags: dinner, restaurant
Added "Sightglass Coffee" (SoMa) - wishlist, tags: coffee, wifi
Added "Nopa" (Alamo Square) - wishlist, tags: dinner, restaurant
```

Real places in San Francisco, across five neighborhoods. Three carry a rating,
so they were filed as `visited`; the four without one went to the wishlist.

### Finding a place I only half-remember

I remember a bakery in the Mission and I cannot spell it. The query is not a
substring of the name — the ranker is matching a misspelling, not the letters:

```
uv run python manage.py find "tartene bakry"
```

```
Tartine Bakery - Mission - visited - rating 5 - morning bun is the whole point, line moves fast before nine
```

### Finding nothing, usefully

There is no bookstore in this journal. Rather than an empty screen, `find` says
so and offers what it has:

```
uv run python manage.py find "bookstore"
```

```
No strong match. Closest 3:
Bi-Rite Creamery - Mission - visited - rating 5 - salted caramel, the line wraps the corner on weekends
Nopa - Alamo Square - wishlist - late kitchen, walk-ins at the bar after ten
Rich Table - Hayes Valley - wishlist - sardine chips, book two weeks out
```

Those three are not answers — the header is the command saying it looked and
found nothing good enough.

### What I still owe the city

```
uv run python manage.py todo
```

```
4 places on the wishlist, oldest first:
Swan Oyster Depot - Nob Hill - tags: lunch, seafood - eighteen stools, cash only, get there before eleven
Rich Table - Hayes Valley - tags: dinner, restaurant - sardine chips, book two weeks out
Sightglass Coffee - SoMa - tags: coffee, wifi - mezzanine upstairs has outlets and no queue
Nopa - Alamo Square - tags: dinner, restaurant - late kitchen, walk-ins at the bar after ten
```

### Crossing one off

`visit` takes the note and the rating as flags — it never prompts, so it works
the same in a script as it does by hand. The note replaces the one `add` stored:

```
uv run python manage.py visit "Rich Table" --rating 4 --note "sardine chips live up to it, the douglas fir levain does not"
```

```
Visited Rich Table - Hayes Valley - visited - rating 4 - sardine chips live up to it, the douglas fir levain does not - stamped 2026-09-08 02:15 UTC
```

The stamp is UTC, and it is one of the three things that will differ in your own
run. The wishlist is now one shorter:

```
uv run python manage.py todo
```

```
3 places on the wishlist, oldest first:
Swan Oyster Depot - Nob Hill - tags: lunch, seafood - eighteen stools, cash only, get there before eleven
Sightglass Coffee - SoMa - tags: coffee, wifi - mezzanine upstairs has outlets and no queue
Nopa - Alamo Square - tags: dinner, restaurant - late kitchen, walk-ins at the bar after ten
```

and `find` agrees the place has changed sides:

```
uv run python manage.py find "sardine chips"
```

```
Rich Table - Hayes Valley - visited - rating 4 - sardine chips live up to it, the douglas fir levain does not
```

### Where should I go

```
uv run python manage.py surprise
```

```
Go here: Sightglass Coffee - SoMa - wishlist - tags: coffee, wifi
Why: never visited
Note: mezzanine upstairs has outlets and no queue
```

This one is a weighted random draw, so run it again and it will very likely name
a different place — that is the command working, not a bug. Pass `--seed 7` to
pin the draw when you want the same answer twice.

### How much of the city have I touched

```
uv run python manage.py stats
```

```
Counts:
  total places  7
  visited       4
  wishlist      3

Neighborhoods touched: 5

Places per neighborhood:
  Mission       3
  Alamo Square  1
  Hayes Valley  1
  Nob Hill      1
  SoMa          1

Top tags:
  coffee      2
  dinner      2
  restaurant  2
  wifi        2
  bakery      1
```

Seven adds, three of them rated and so filed as visited, plus the one `visit`
above: seven total, four visited, three left. Five neighborhoods named, five
touched.

## The admin

The stock Django admin is a window onto exactly the same records the CLI writes —
browse them, edit them, delete them. It is the **only** web surface in the
project; there are no other pages, and nothing in the CLI requires it.

It is the one place that needs an account. `createsuperuser` is interactive and
will prompt you for a username, an email and a password:

```
uv run python manage.py createsuperuser
uv run python manage.py runserver
```

Then open `http://localhost:8000/admin/` and log in.

`runserver` holds the terminal until you stop it — press **Ctrl-C** (that is
Ctrl, not Command, on macOS too, in every shell). Nothing else in this file
needs the server running.

## Running the tests

Before trusting any of the above, check your install:

```
uv run python manage.py test
```

A green run finishes with this summary (the per-test progress dots above it are
omitted here, and the elapsed time depends on your machine). A line about
destroying the test database is printed after it — that is teardown, not
trouble:

```
----------------------------------------------------------------------
Ran 500 tests in 38.843s

OK
```

There is also a configuration sanity check:

```
uv run python manage.py check
```

```
System check identified no issues (0 silenced).
```

## If a command surprises you

Before filing it as a bug, check whether it was a decision:
[`_docs/plan.md`](_docs/plan.md) records what this tool refuses to do, and
[`_docs/tasks.md`](_docs/tasks.md) traces each command back to the issue that
specified it. If you are changing the code rather than using it,
[`../AGENTS.md`](../AGENTS.md) is the file to read first.
