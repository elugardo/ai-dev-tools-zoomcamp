# Testing guidelines

Read before writing tests. Every task in the backlog ships with tests; a task
with no test is not finished.

## Mechanics

- Django's own runner: `.venv\Scripts\python manage.py test`. No pytest — it is
  not in `requirements.txt` and would need approval to add.
- Tests live in `places/tests.py` until it gets unwieldy, then become a
  `places/tests/` package with one module per unit (`test_models.py`,
  `test_search.py`, `test_commands.py`).
- Use `django.test.TestCase` when the test touches the database; plain
  `unittest.TestCase` when it does not, since it is much faster.

## What to test

- **Test behavior, not implementation.** Assert on what a command outputs or
  what the database contains afterward, not on which private helper was called.
- **Test the ranking module directly.** `places/search.py` is pure by design —
  data in, ranked data out. Cover typo tolerance, field weighting, and the
  closest-3 fallback there, with no database and no CLI involved. This is the
  most important test surface in the project.
- **Test management commands through `call_command`**, passing a `StringIO` as
  `stdout` and asserting on the captured text:
  `call_command("find", "coffee", stdout=out)`. Do not shell out to `manage.py`.
- **Pin randomness.** `surprise` must be tested with a fixed seed so its
  weighting is asserted deterministically, never by running it repeatedly and
  hoping.
- **Cover the empty and ambiguous cases.** An empty database, a query matching
  nothing, and a place name matching two records are where this tool will
  actually break.

## What not to do

- No mocking of the ORM — use a real SQLite test database, which Django creates
  and destroys per run.
- No network in tests, ever. The tool is offline; a test that needs a connection
  means something is wrong with the design.
- No asserting on exact whitespace or column alignment in command output. Assert
  that the important substrings are present, so formatting can be improved
  without breaking tests.
