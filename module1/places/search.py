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
``dict`` iteration order or on ``PYTHONHASHSEED``. **Ties are broken by
lowercased name, ascending**, so equal scores order the same way on every run
-- which also fixes *which* three the fallback picks when candidates 3, 4 and
5 all score alike. Output order therefore depends only on the candidates' own
data, never on their position in the input.

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
    cleaned = (query or "").strip()
    candidates = tuple(candidates)

    # No query and no candidates are different kinds of nothing, but both
    # answer with an empty set -- and neither triggers the fallback.
    if not cleaned or not candidates:
        return RankedResults(results=(), is_weak=False)

    query_tokens = _tokenize(cleaned)
    scored = [(_score(query_tokens, place), place) for place in candidates]
    scored.sort(key=lambda pair: (-pair[0], _sort_name(pair[1])))

    strong = [pair for pair in scored if pair[0] >= STRONG_MATCH_THRESHOLD]
    if strong:
        return _results(strong, is_weak=False)
    return _results(scored[:WEAK_MATCH_LIMIT], is_weak=True)


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


def _sort_name(place):
    """The tie-break key: the candidate's name, lowercased."""
    return _text(place, "name").strip().lower()
