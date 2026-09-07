# Process

How work on City Journal is organized. Read this before starting a task and
again before closing one.

## The loop

- **Tasks are GitHub issues, one at a time.** The backlog in
  [tasks.md](tasks.md) is mirrored as issues #1–#10 in
  `elugardo/ai-dev-tools-zoomcamp`. Pick one, finish it, then pick the next.
- **Groom before implementing.** Every task is groomed by the PM first, which
  rewrites the issue into the four sections of [task-template.md](task-template.md).
  A raw two-sentence backlog entry is not ready to build from.
- **Read the acceptance criteria before starting and before closing.** On a
  groomed issue they are the definition of done — tick every box, literally. On
  an ungroomed one the Goal line stands in until the PM has been through it.
- **Work on a branch** named for the issue: `1-project-skeleton`,
  `5-fuzzy-ranking`. Never commit feature work directly to `main`.
- **Commit regularly** — at each point the tests pass, not once at the end.
  Reference the issue in the commit body (`Refs #5`), and use `Closes #5` on the
  commit or PR that finishes it.
- **Leave the suite green.** `manage.py test` must pass before any commit.

## Roles

Role definitions live in [`team/`](team/). Adopt one only when asked for it by
name.

- **PM** — grooms a task before anyone implements it, follows
  [`team/pm.md`](team/pm.md). Decides what "done" means, names the edge cases,
  and writes no code.
- **Software Engineer** — implements one groomed task at a time, follows
  [`team/software-engineer.md`](team/software-engineer.md). Builds against the
  acceptance criteria without changing them, writes tests, commits regularly, and
  does not close the issue.
- **QA Engineer** — checks finished work against the issue that specified it,
  follows [`team/qa-engineer.md`](team/qa-engineer.md). Verifies each criterion,
  hunts the cases the criteria describe but the tests miss, fixes nothing, and
  reports PASS or FAIL as an issue comment.

An issue is closed only after QA returns PASS. A FAIL goes back to the engineer,
who fixes it on the same branch; the cycle repeats until it passes.

## The orchestrator

The main session is the orchestrator. It launches the PM, the engineer and QA as
subagents. It does not groom, implement or test itself.

### Lifecycle

1. Pick the next open issue from the backlog
2. PM grooms it
3. Engineer implements it
4. QA verifies it
5. On FAIL, back to step 3 with the QA comment as input
6. On PASS, close the issue
7. Repeat until the backlog is empty

### Rules

- Do not skip step 2
- The engineer does not close the issue
- QA does not fix the code, only outputs PASS or FAIL
- The orchestrator closes the issue only after QA outputs PASS

### Why the roles are separate

The work moves through a graph whose nodes are specialised agents, with one
conditional edge — QA's verdict either ends the task or sends it back:

```
  PM ──▶ Engineer ──▶ QA ──PASS──▶ done
              ▲        │
              └──FAIL──┘
```

Separating the roles buys independence, not ceremony. The engineer writes tests
that pass, which is not the same as meeting the criteria: on issue #2 the suite
was green at 32 tests while `Place.objects.filter(tags__name="Coffee")` silently
returned nothing, and a later QA pass found five criteria that the code satisfied
but no test pinned. A fresh reader checking the issue rather than the diff is
what caught both.

It costs more time and more tokens than simply asking an engineer to build the
thing. Spend it where a silent wrong answer is expensive, and skip it where it
is not.

## Order and dependencies

**#1 and #2 are done and merged.** `places` is registered and migrated, and the
`Place` and `Tag` models exist, so nothing in the backlog is blocked on the
schema any more.

What remains: #5 (the ranking module) blocks #6 (`find`). #10 (README) is last,
since it documents every command. #3, #4, #7, #8 and #9 are independent of each
other and can be done in any order.

Tag lookups are case-insensitive at the database level, so `--tag Coffee` matches
a stored `coffee` on every query path. Whitespace is *not* handled by that — run
user input through `normalize_tag_name()` from `places.models` at the entry
point, which strips as well as lowercases.

## When the plan is wrong

If a task turns out to be mis-scoped, contradicts [plan.md](plan.md), or needs
something the plan rules out, stop and say so rather than quietly widening the
work. Scope changes are decided first, then written into `plan.md`, then built.

## Keeping the documents alive

These documents are meant to be corrected, not preserved. After a session where
you were told "don't do it that way," update the document that should have said
so — `AGENTS.md` for a rule, `testing-guidelines.md` for a testing habit,
`plan.md` for a scope decision, this file for a process change.
