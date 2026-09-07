# City Journal — Backlog

Derived from [plan.md](plan.md). Each task is sized for one session and written to
be picked up by someone who has not read the other tasks.

Shared context every task can assume: a Django 6.1 project named `cityjournal`
living in `module1/`, with an app named `places`, a `.venv/` alongside it, and
SQLite as the database. The tool is a CLI built from Django management commands
(`python manage.py <command>`); it is fully offline, with no API keys and no
network calls.

## 1. Project skeleton with a passing test
Goal: `python manage.py test` runs green against the scaffolded project.
Description: The `cityjournal` project and `places` app already exist on disk, but
the app is not yet registered, so Django cannot see its models, commands, or
tests. Add `"places"` to `INSTALLED_APPS` in `cityjournal/settings.py`, run
`migrate` to create the initial SQLite database, and replace the empty
`places/tests.py` with one trivial assertion. Done when the test runner discovers
and passes that test.

## 2. Place and Tag models
Goal: `Place` and `Tag` exist as migrated tables in SQLite.
Description: Define `Tag` (unique `name`, lowercased on save) and `Place` (`name`,
`neighborhood`, `address`, `note`, nullable `rating` 1–5, `status` of `wishlist`
or `visited`, many-to-many `tags`, plus `created_at` and `last_visited_at`) in
`places/models.py`, then generate and apply the migration. Add model tests
covering tag lowercasing, the status default of `wishlist`, and that a place can
be saved with no rating. Use a `TextChoices` class for `status` so the valid
values live in one place.

## 3. Admin registration
Goal: Places and tags are browsable and editable at `/admin/`.
Description: Register both models in `places/admin.py` with a `ModelAdmin` that
sets `list_display` (name, neighborhood, status, rating, last visited),
`list_filter` (status, tags, neighborhood), and `search_fields` (name, note).
Verify by creating a superuser, running `runserver`, and confirming records added
from the admin are visible to the ORM. This is the only web surface in the
project; no custom views or templates.

## 4. `add` command
Goal: `python manage.py add "Blue Bottle" --tag coffee --note "good wifi"` persists a place.
Description: Write a management command in `places/management/commands/add.py`
that takes a positional name plus optional `--neighborhood`, `--address`,
`--note`, `--rating`, and repeatable `--tag` flags. Tags are created on demand
and reused if they already exist; a place with a rating is stored as `visited`
and one without as `wishlist`, unless `--status` says otherwise. Print a short
confirmation line and cover the tag reuse and status inference in tests.

## 5. Fuzzy ranking module
Goal: A pure, tested function ranks places against a query string.
Description: Add `places/search.py` with a function that takes a query and a list
of places and returns them scored and sorted, using `rapidfuzz` to compare the
query against name, note, tags, and neighborhood, with name matches weighted
highest. Define a confidence threshold, and when nothing clears it, return the
three highest-scoring places flagged as weak matches instead of an empty list.
This module must not import any management-command or CLI code — it takes data
in and returns ranked data out, so it can be tested directly.

## 6. `find` command
Goal: `python manage.py find "coffee wifi"` prints ranked matches and never comes back empty.
Description: Build the command on top of the ranking module from task 5, adding
optional `--tag`, `--status`, and `--neighborhood` filters that narrow the
candidate set before scoring, and a `--limit` that defaults to a handful of
results. When the ranker reports only weak matches, print a `No strong match.
Closest 3:` header above them so the user knows the tool is guessing. Show each
hit as one readable line with name, neighborhood, status, rating, and a snippet
of the note.

## 7. Wishlist loop: `todo` and `visit`
Goal: Places can be listed as not-yet-visited and then flipped to visited.
Description: `todo` lists `wishlist` places, optionally filtered by `--tag` and
`--neighborhood`. `visit` takes a place name, resolves it to exactly one record
(reporting the candidates and exiting non-zero if the name is ambiguous or
unknown), sets `status` to `visited`, stamps `last_visited_at`, and accepts
`--note` and `--rating` to record the impression while it is fresh. These two
commands share the name-resolution helper, which is why they are one task.

## 8. `surprise` command
Goal: `python manage.py surprise` names one place to go to today.
Description: Pick a single place at random, weighted so that never-visited
wishlist entries come up most often and places with an old `last_visited_at` come
up more often than recently visited ones. Support `--tag` and `--neighborhood` to
constrain the pick, and print the place with its note so the suggestion carries
its own reason. Test the weighting by seeding a fixed random seed rather than
asserting on raw randomness.

## 9. `stats` command
Goal: `python manage.py stats` summarizes how much of the city has been covered.
Description: Print counts of visited versus wishlist places, a breakdown of
places per neighborhood, the most-used tags, and the number of distinct
neighborhoods touched. Use ORM aggregation (`annotate`/`Count`) rather than
looping in Python, and format the output as aligned columns that stay readable in
a terminal. Handle the empty-database case with a helpful message instead of a
wall of zeros.

## 10. README and recorded session
Goal: A newcomer can install, run, and understand the tool from the README alone.
Description: Write `module1/README.md` covering setup (venv, `requirements.txt`,
`migrate`, superuser), a one-line description of every command, and a walkthrough
that adds several real places, finds one by a half-remembered detail, marks it
visited, and prints stats. Paste the actual terminal output rather than
idealized examples. Confirm the instructions work from a clean clone with a fresh
database.
