# Testing guidelines

Read before writing tests. Every task in the backlog ships with tests; a task
with no test is not finished.

## Mechanics

- Django's own runner: `uv run python manage.py test`. No pytest — it is not a
  dependency in `pyproject.toml` and would need approval to add.
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

## Prove the test can fail

A test that asserts something true which no bug can falsify is worse than no test:
it reports coverage it does not provide. This has happened repeatedly here —
across the models, `add`, the ranking module, `find` and `surprise` — and it has
never been caught by reading the diff.

**Before you call a behavior tested, break it and watch the test fail.** Change
the implementation line the test exists to defend, run the suite, confirm the
failure names your test, then restore with `git checkout --`. If the suite stays
green, the test is decoration.

Two failure modes seen so far:

- **Data that cannot discriminate.** A tie-break test whose names sort the same
  way with or without the `.lower()` it is meant to pin. A duplicate-name test
  covering the direction an unanchored regex also passes.
- **Never assert over module text.** `inspect.getsource()` returns docstrings and
  comments, so `assertIn("order_by(\"pk\")", source)` is satisfied by a docstring
  that merely mentions it — and `assertNotIn` fails when an accurate sentence is
  added with no code change. Walk the AST instead: `_imported_modules`,
  `_called_names` and `_dotted_calls` in `places/tests.py` show the pattern, and
  an assertion over parsed nodes can be neither satisfied nor broken by prose.

Not every mutation that survives is a missing test. An **equivalent mutant** —
one that cannot change behavior — proves nothing is wrong: on SQLite,
`icontains` and `contains` compile to the same `LIKE`, so no test can tell them
apart. Say so rather than writing a test that cannot fail either.

## What not to do

- No mocking of the ORM — use a real SQLite test database, which Django creates
  and destroys per run.
- No network in tests, ever. The tool is offline; a test that needs a connection
  means something is wrong with the design.
- No asserting on exact whitespace or column alignment in command output. Assert
  that the important substrings are present, so formatting can be improved
  without breaking tests.
