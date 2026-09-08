"""Fuzzy, typo-tolerant ranking for City Journal -- pure data in, ranked data out.

:func:`rank_places` is the only entry point. It takes a query string and a
collection of candidate places, scores every candidate against the query, and
returns them ordered best-first. Nothing here reads the database, imports
``places.models``, or prints: the caller supplies the candidates and decides
what reaches the terminal.

The candidate contract
----------------------

A candidate is *any* object exposing these attributes. All four are optional:
a missing attribute, ``None``, and ``""`` are all treated as "this field
contributes nothing", and none of them raises.

``name``
    Text. The place's name. Weighted highest.
``note``
    Text. The free-text note.
``neighborhood``
    Text.
``tags``
    An iterable of tag entries. Each entry is either a plain string
    (``"coffee"``) or an object with a ``.name`` attribute (a ``Tag`` row).
    Both forms score identically for the same value. A Django related manager
    -- ``place.tags`` on a real ``Place``, which is not itself iterable -- is
    also accepted: its ``all()`` is called to get the related rows, so a real
    ``Place`` and an in-memory stand-in such as::

        SimpleNamespace(name="Blue Bottle", note="good wifi",
                        neighborhood="Mitte", tags=["coffee"])

    both work with no adapter.

Tag names are lowercased *on read* here, per ``AGENTS.md``; this module never
assumes a caller normalized them first. Every other field is lowercased on
read too, so matching is case-insensitive throughout, and the query is
stripped of surrounding whitespace before anything else happens.

What comes back
---------------

A :class:`RankedResults` -- a sequence of :class:`SearchResult` (``place``,
``score``, ``is_weak``) that also carries a group-level ``is_weak`` flag::

    results = rank_places("blu bottl", places)
    if results.is_weak:
        print("No strong match. Closest 3:")
    for result in results:
        print(result.place.name, result.score)

A result set is **either all strong or all weak, never mixed**, which is what
makes one header above the whole group correct. ``is_weak`` is the whole
answer: a caller never has to import the threshold or compare scores itself.

* At least one candidate scoring ``>= STRONG_MATCH_THRESHOLD`` (inclusive):
  every such candidate comes back, ranked, with ``is_weak`` false. The strong
  path is **uncapped** -- trimming for display is the caller's ``--limit``.
* Nothing clearing the threshold: the ``WEAK_MATCH_LIMIT`` highest-scoring
  candidates come back with ``is_weak`` true, even when every score is 0. That
  is a ceiling, not a quota -- two candidates give two weak results, one gives
  one. Nothing is padded, repeated or invented.
* No candidates at all: an empty result set, for any query. There is nothing
  to be close to.
* An empty or whitespace-only query: an empty result set, and *no* fallback.
  The fallback means "I looked and nothing was good enough"; with no query
  there is nothing to be near.
* **A query that tokenizes to nothing means the same thing as no query at
  all**, and gets the same empty, non-weak result set. Tokens are ASCII
  alphanumeric runs, so ``"---"``, an emoji, and any text in a non-Latin
  script -- ``"кофе"``, ``"寿司"`` -- all reduce to no tokens. Text this
  module cannot see cannot make it prefer one candidate to another, so every
  candidate would score 0 and the fallback would print three arbitrary places
  under a header claiming we looked. An empty *query* and a query with no
  *tokens* are therefore one case here, not two.
  :func:`has_searchable_tokens` exposes that same test, so a caller can reject
  such a query up front without keeping a second copy of the tokenizer.
  Making non-Latin text actually *searchable* is a different change -- a wider
  token pattern -- and would be a feature, not this rule.

How a score is built
--------------------

Per field, per query token: the token's best ``rapidfuzz`` similarity against
any token of the field, counted only if it clears
:data:`MIN_TOKEN_SIMILARITY` (so unrelated prose contributes nothing rather
than a trickle of noise), averaged over the query's tokens. That gives each
field a 0-100 "how much of the query does this field cover" score.

The per-field scores are then combined as accumulating evidence, weighted by
field, on a single 0-100 scale that means the same thing for every candidate,
so one threshold applies to all of them:

    score = 100 * (1 - product over fields of (1 - weight * field_score/100))

Two consequences worth stating, because both are acceptance criteria:

* **A non-matching field never costs anything.** A field scoring 0 multiplies
  by 1 and drops out. A place with a long irrelevant note scores exactly what
  it would with an empty note -- scores combine evidence, they do not average
  irrelevant text in.
* **More matching fields beat fewer.** Every field that does match strictly
  raises the total, so a two-field match outranks an otherwise identical
  one-field match.

The field weights, highest first -- ``name`` outranks everything, and the note
comes next because ``_docs/plan.md`` says the note is what fuzzy search
actually leans on:

    ``NAME_WEIGHT`` > ``NOTE_WEIGHT`` > ``TAG_WEIGHT`` > ``NEIGHBORHOOD_WEIGHT``

Changing the relative importance of two fields is a one-line edit to those
constants at the top of this file.

Determinism
-----------

Scoring is deterministic: no randomness, no clock, no reliance on ``set`` or
``dict`` iteration order or on ``PYTHONHASHSEED``. Equal scores order the same
way on every run, which also fixes *which* three the fallback picks when
candidates 3, 4 and 5 all score alike.

**The tie-break chain, in the order it applies:**

1. ``score``, descending -- the ranking proper.
2. ``name``, ascending.
3. ``neighborhood``, ascending.
4. ``note``, ascending.
5. the candidate's ``tags``, ascending: their names sorted, then compared as
   a sequence, so two candidates carrying the same tags in a different order
   are not separated by that difference.

Keys 2-5 are each stripped and lowercased, so the chain is insensitive to
case and to surrounding whitespace, exactly as scoring is. Every one of them
is **read off the candidate's own data** -- never its position in the input --
so ``rank_places(q, [a, b])`` and ``rank_places(q, [b, a])`` return the same
order. That matters because duplicate names are allowed by design (two
branches of one chain in different neighborhoods), and name alone stops
separating them; ``neighborhood``, ``note`` and ``tags`` carry on from there.

The chain runs out when two candidates are identical in every scored field.
Those come back in an unspecified order relative to each other -- **no
exception is raised, and nothing else is consulted to break the tie**. There
is no data left to break it with, and reaching for input position would be
position dependence under another name. Two candidates that far identical are
interchangeable in the output anyway: whichever way they land, the printed
lines are the same.

Each candidate handed in produces exactly one result; this module neither
deduplicates the input nor invents entries.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from rapidfuzz import fuzz

# --- Tuning knobs -----------------------------------------------------------
#
# Field weights, on 0-1. Name is highest by design; see the module docstring.
NAME_WEIGHT = 0.90
NOTE_WEIGHT = 0.80
TAG_WEIGHT = 0.75
NEIGHBORHOOD_WEIGHT = 0.70

#: The weighting table the scorer actually walks. Its order is fixed, which is
#: part of what makes scoring reproducible.
FIELD_WEIGHTS = {
    "name": NAME_WEIGHT,
    "note": NOTE_WEIGHT,
    "tags": TAG_WEIGHT,
    "neighborhood": NEIGHBORHOOD_WEIGHT,
}

#: Similarity (0-100) a single query token must reach against a field's token
#: before it counts at all. Below this it is noise, not a typo: it keeps a long
#: unrelated note from accumulating a score out of coincidental letters.
MIN_TOKEN_SIMILARITY = 70.0

#: At or above this, on the 0-100 scale, a match is strong. Inclusive.
STRONG_MATCH_THRESHOLD = 60.0

#: How many guesses the no-strong-match fallback offers. A ceiling, not a
#: quota -- see ``_docs/plan.md``, "Closest 3".
WEAK_MATCH_LIMIT = 3

#: Decimal places scores are rounded to, so equal evidence yields equal floats.
SCORE_PRECISION = 2

_TOKEN_PATTERN = re.compile(r"[0-9a-z]+")


@dataclass(frozen=True)
class SearchResult:
    """One scored candidate.

    ``place`` is the object that was handed in, untouched. ``score`` is its
    0-100 relevance. ``is_weak`` is true when this result is a fallback guess
    rather than a confident match; it is the same for every result in a set.
    """

    place: Any
    score: float
    is_weak: bool


@dataclass(frozen=True)
class RankedResults(Sequence):
    """The ranked result set: a sequence of :class:`SearchResult`, best first.

    ``is_weak`` describes the whole set -- true when nothing cleared
    :data:`STRONG_MATCH_THRESHOLD` and these are the closest guesses. Read it
    to decide whether to print a "no strong match" header; there is no need to
    import the threshold or compare scores.
    """

    results: tuple
    is_weak: bool

    def __len__(self):
        return len(self.results)

    def __getitem__(self, index):
        return self.results[index]


def rank_places(query, candidates):
    """Score ``candidates`` against ``query`` and return them ranked best-first.

    See the module docstring for the candidate contract, the return contract,
    and the weak-match fallback. ``candidates`` may be any iterable; it is read
    once and never mutated.
    """
    candidates = tuple(candidates)
    query_tokens = _tokenize((query or "").strip())

    # No query, a query with no tokens, and no candidates are three different
    # kinds of nothing, but all three answer with an empty set -- and none of
    # them triggers the fallback. A query the tokenizer cannot see any of --
    # "---", an emoji, "кофе" -- scores every candidate 0, so the three places
    # the fallback would offer are arbitrary; saying "I looked and nothing was
    # good enough" over them would be a lie.
    if not query_tokens or not candidates:
        return RankedResults(results=(), is_weak=False)

    scored = [(_score(query_tokens, place), place) for place in candidates]
    scored.sort(key=lambda pair: (-pair[0], _tie_break_key(pair[1])))

    strong = [pair for pair in scored if pair[0] >= STRONG_MATCH_THRESHOLD]
    if strong:
        return _results(strong, is_weak=False)
    return _results(scored[:WEAK_MATCH_LIMIT], is_weak=True)


def has_searchable_tokens(query):
    """True when ``query`` holds at least one token :func:`rank_places` can score.

    The public form of "is there anything to search for here?", so a caller
    that wants to reject such a query before it reads a database asks the
    tokenizer that will actually run rather than keeping a second copy of it.
    ``rank_places`` answers every query this returns false for exactly as it
    answers an empty one: an empty result set, no fallback.
    """
    return bool(_tokenize((query or "").strip()))


def _results(scored, is_weak):
    """Wrap ``(score, place)`` pairs, already ordered, into a result set."""
    return RankedResults(
        results=tuple(
            SearchResult(place=place, score=score, is_weak=is_weak)
            for score, place in scored
        ),
        is_weak=is_weak,
    )


def _score(query_tokens, place):
    """Combine this place's per-field evidence into one 0-100 score."""
    field_tokens = {
        "name": _tokenize(_text(place, "name")),
        "note": _tokenize(_text(place, "note")),
        "tags": _tokenize(" ".join(_tag_names(place))),
        "neighborhood": _tokenize(_text(place, "neighborhood")),
    }

    remaining = 1.0
    for field, weight in FIELD_WEIGHTS.items():
        coverage = _field_score(query_tokens, field_tokens[field])
        remaining *= 1.0 - weight * (coverage / 100.0)
    return round((1.0 - remaining) * 100.0, SCORE_PRECISION)


def _field_score(query_tokens, field_tokens):
    """How much of the query this one field covers, 0-100.

    Each query token scores its best similarity against any token of the
    field, or nothing at all if that best is below
    :data:`MIN_TOKEN_SIMILARITY`. The average over the query's tokens keeps the
    result on the same 0-100 scale whatever the query's length.
    """
    if not query_tokens or not field_tokens:
        return 0.0

    total = 0.0
    for query_token in query_tokens:
        best = max(fuzz.ratio(query_token, token) for token in field_tokens)
        if best >= MIN_TOKEN_SIMILARITY:
            total += best
    return total / len(query_tokens)


def _tokenize(text):
    """Lowercase ``text`` and split it into alphanumeric tokens."""
    return tuple(_TOKEN_PATTERN.findall(text.lower()))


def _text(place, attribute):
    """Read a text field off a candidate. Missing and ``None`` both mean ``""``."""
    value = getattr(place, attribute, None)
    if value is None:
        return ""
    return str(value)


def _tag_names(place):
    """Read a candidate's tag names as a tuple of raw strings.

    Lowercasing happens once, in :func:`_tokenize`, which every field goes
    through -- tags included, so ``AGENTS.md``'s "lowercase on read as well as
    on write" holds here without a second copy of the rule that no test could
    tell apart from this one.

    Entries may be plain strings or objects with a ``.name``. ``place.tags``
    itself may be a plain iterable or a Django related manager -- a manager is
    not iterable, and ``all()`` is its documented way to yield the rows it
    already relates to. That reads the object handed to us; it builds no
    queryset of this module's own and touches no manager on a model class.
    """
    tags = getattr(place, "tags", None)
    if tags is None:
        return ()

    if not hasattr(tags, "__iter__"):
        related = getattr(tags, "all", None)
        if related is None:
            return ()
        tags = related()

    names = []
    for tag in tags:
        name = tag if isinstance(tag, str) else getattr(tag, "name", None)
        if name:
            names.append(str(name))
    return tuple(names)


def _tie_break_key(place):
    """The tie-break key for one candidate: every scored field, in chain order.

    Name, then neighborhood, then note, then the candidate's tag names sorted
    -- see "Determinism" in the module docstring. Each text component is
    stripped and lowercased, matching how scoring reads the same fields, and
    the tag names are sorted inside the key so that two candidates carrying
    the same tags in a different order compare equal rather than being
    separated by the order they happened to be listed in.

    Every component comes from the candidate's own data. Nothing here reads
    the candidate's index in the input, so the caller's ordering cannot reach
    the output. Two candidates identical in all four fields therefore produce
    equal keys and keep whatever relative order Python's stable sort gives
    them; that is the documented end of the chain, not an error.
    """
    return (
        _sort_text(place, "name"),
        _sort_text(place, "neighborhood"),
        _sort_text(place, "note"),
        tuple(sorted(name.strip().lower() for name in _tag_names(place))),
    )


def _sort_text(place, attribute):
    """One text component of the tie-break key: stripped and lowercased."""
    return _text(place, attribute).strip().lower()
