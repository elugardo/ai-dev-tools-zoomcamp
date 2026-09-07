# AGENTS.md

Repo for the AI Dev Tools Zoomcamp. Each module is its own self-contained
project in a numbered folder. Current work is **module1 — City Journal**, a
personal place-journal CLI built on Django 6.1 + SQLite. Run every command below
from `module1/`, which is where the virtualenv and `manage.py` live.

## Commands

```
python -m venv .venv                              # first time only
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python manage.py migrate            # create/update the SQLite db
.venv\Scripts\python manage.py test               # full test suite
.venv\Scripts\python manage.py test places.tests.PlaceModelTests   # one test class
.venv\Scripts\python manage.py check              # config sanity check
.venv\Scripts\python manage.py createsuperuser    # needed once for the admin
.venv\Scripts\python manage.py runserver          # admin at localhost:8000/admin/
```

Shell is PowerShell on Windows. In bash use `.venv/Scripts/python.exe` instead.

## Rules

- Dependencies are pinned in `module1/requirements.txt`. Do not add one without
  asking.
- The tool is **fully offline**. No network calls, no API keys, no geocoding, no
  LLM calls at runtime. If a task seems to need one, stop and ask.
- The CLI is built from Django management commands in
  `places/management/commands/`. The only web surface is the stock Django admin —
  do not add custom views, templates, or URLs.
- Never commit `.venv/` or `db.sqlite3`; both are gitignored.
- Scope lives in `module1/_docs/plan.md`. Do not silently build something outside
  it — propose the change, get agreement, then update the plan.

## Documents

- Before starting or closing a task, read `module1/_docs/process.md`.
- Before writing tests, read `module1/_docs/testing-guidelines.md`.
- For what this tool is and is deliberately not, read `module1/_docs/plan.md`.
- For the task list behind GitHub issues #1–#10, read `module1/_docs/tasks.md`.
