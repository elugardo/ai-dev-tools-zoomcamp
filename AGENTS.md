# AGENTS.md

Repo for the AI Dev Tools Zoomcamp. Each module is its own self-contained
project in a numbered folder. Current work is **module1 — City Journal**, a
personal place-journal CLI built on Django 6.1 + SQLite. Run every command below
from `module1/`, which is where the virtualenv and `manage.py` live.

## Commands

```
uv sync                                       # create .venv and install deps
uv run python manage.py migrate               # create/update the SQLite db
uv run python manage.py test                  # full test suite
uv run python manage.py test places.tests.PlaceModelTests   # one test class
uv run python manage.py check                 # config sanity check
uv run python manage.py createsuperuser       # needed once for the admin
uv run python manage.py runserver             # admin at localhost:8000/admin/
```

`uv run` executes inside the project environment, so there is nothing to
activate. `uv sync` is idempotent — run it after pulling if dependencies moved.

## Rules

- Dependencies are declared in `module1/pyproject.toml` and pinned in
  `module1/uv.lock`. Add them with `uv add <pkg>`, never by editing the lock by
  hand — and do not add one without asking.
- The tool is **fully offline**. No network calls, no API keys, no geocoding, no
  LLM calls at runtime. If a task seems to need one, stop and ask.
- The CLI is built from Django management commands in
  `places/management/commands/`. The only web surface is the stock Django admin —
  do not add custom views, templates, or URLs.
- **Tags are always lowercase**, on write *and* on read. Lowercase the input to
  every tag lookup, filter, or comparison — never just on save, or `Coffee` will
  fail to find the stored `coffee` and try to create a duplicate.
- Never commit `.venv/` or `db.sqlite3`; both are gitignored.
- Scope lives in `module1/_docs/plan.md`. Do not silently build something outside
  it — propose the change, get agreement, then update the plan.

## Documents

- Before starting or closing a task, read `module1/_docs/process.md`.
- Before writing tests, read `module1/_docs/testing-guidelines.md`.
- For what this tool is and is deliberately not, read `module1/_docs/plan.md`.
- For the task list behind GitHub issues #1–#10, read `module1/_docs/tasks.md`.
- Before grooming an issue, read `module1/_docs/team/pm.md` and write the result
  into the four sections of `module1/_docs/task-template.md`.
- Before implementing a groomed issue, read
  `module1/_docs/team/software-engineer.md`.
- Before verifying finished work, read `module1/_docs/team/qa-engineer.md`.

