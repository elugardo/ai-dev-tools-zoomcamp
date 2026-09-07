"""Turning a name typed at the CLI into exactly one :class:`~places.models.Place`.

This module is the single statement of the resolution rule that ``visit`` and
``todo`` share (issue #7). It is deliberately not inside either command: a
command module is not importable as a library, and a rule that lives in one
command and is imported by the other has an owner, which this one does not.

The rule, in full
-----------------

:func:`resolve_place` is **two-stage and exact-before-partial**:

1. case-insensitive match on the *whole* name;
2. only if stage 1 found nothing, case-insensitive *substring* match.

The first stage that finds exactly one place wins. That ordering is what makes
the common case work: with both ``Blue Bottle`` and ``Blue Bottle Coffee``
stored, ``Blue Bottle`` resolves to the first -- stage 1 matched exactly one --
rather than being reported as ambiguous. ``Blue`` reaches stage 2, matches
both, and *is* ambiguous.

What it deliberately does **not** do:

* **No fuzzy or typo-tolerant matching.** Typo tolerance belongs to ``find``
  (issues #5 and #6), which only reads. ``visit`` writes, so it has to be
  predictable: a resolver that guesses would silently stamp the wrong place.
  This module must never import :mod:`places.search` or ``rapidfuzz``.
* **No searching outside ``name``.** Not the note, not the tags, not the
  neighborhood.
* **No guessing between candidates.** Zero matches and two-or-more matches are
  both errors -- :class:`PlaceNotFound` and :class:`AmbiguousPlaceName`, each
  carrying a ready-to-print message. The caller turns them into a
  ``CommandError``, which is what makes the process exit non-zero.

Resolution reads *every* place, not just the wishlist, so a place that has
already been visited can be visited again.
"""

from django.db.models.functions import Lower

from places.models import Place

#: How much of a note survives on a one-line description, in characters.
NOTE_SNIPPET_LENGTH = 80

#: Appended to a note that was cut, so a truncated line admits it.
TRUNCATION_MARKER = "…"


class PlaceNameError(Exception):
    """A typed name that does not identify exactly one place.

    Carries the places it *did* match in :attr:`candidates` (empty for a name
    that matched nothing), so a caller that wants to print them differently
    can, and a rendered ``message`` that already names the string the user
    typed and points at a next step.
    """

    def __init__(self, message, candidates=()):
        super().__init__(message)
        self.message = message
        self.candidates = list(candidates)


class EmptyPlaceName(PlaceNameError):
    """No name at all, or only whitespace.

    Its own class because the alternative is far worse than a bad error
    message: an empty string is a substring of every name, so falling through
    to stage 2 with one would "match" the entire journal.
    """


class PlaceNotFound(PlaceNameError):
    """Neither stage matched anything."""


class AmbiguousPlaceName(PlaceNameError):
    """A stage matched more than one place; the caller must not pick one."""


def normalize_place_name(name):
    """Return the canonical form of a name typed at the CLI: stripped.

    Case is *not* folded here -- the lookups below are case-insensitive and
    the stored spelling is what gets echoed back to the user -- but
    surrounding whitespace is, so ``"  Blue Bottle  "`` resolves exactly as
    ``"Blue Bottle"`` does.
    """
    if name is None:
        return ""
    return str(name).strip()


def resolve_place(name, queryset=None):
    """Return the one :class:`~places.models.Place` whose name matches ``name``.

    ``queryset`` defaults to every place in the journal. Pass a narrower one
    only with a reason; ``visit`` deliberately does not, so an already-visited
    place stays reachable.

    Raises :class:`EmptyPlaceName`, :class:`PlaceNotFound` or
    :class:`AmbiguousPlaceName`. This function only ever reads.
    """
    typed = normalize_place_name(name)
    if not typed:
        raise EmptyPlaceName(
            "A place name is required, and it cannot be blank -- an empty "
            'name would match every place in the journal. Try: visit "Blue Bottle"'
        )

    places = Place.objects.all() if queryset is None else queryset

    # Whole name first, substring second. `iexact` and `icontains` are what
    # make both stages case-insensitive; `Place.name` has no NOCASE collation
    # of its own, unlike `Tag.name`. Querysets are lazy, so naming both here
    # costs nothing: stage 2 only reaches the database if stage 1 came back
    # empty, and neither runs at all for a blank name.
    stages = (
        places.filter(name__iexact=typed),
        places.filter(name__icontains=typed),
    )
    for stage in stages:
        # A stable order, so the candidate list a user is asked to choose
        # between does not shuffle between runs. Ordered in the database, and
        # case-insensitively, so `blue bottle` and `Blue Bottle` sit together.
        matches = list(stage.order_by(Lower("name"), "pk"))
        if len(matches) == 1:
            return matches[0]
        if matches:
            raise AmbiguousPlaceName(_ambiguous_message(typed, matches), matches)

    raise PlaceNotFound(
        f'No place matches "{typed}". Add it with: manage.py add "{typed}" '
        f'-- or look for it with: manage.py find "{typed}"',
    )


def _ambiguous_message(typed, matches):
    """The candidate list, one place per line, under a line saying why.

    Every candidate carries its neighborhood and status, which is the minimum
    needed to tell two places with similar names apart.
    """
    lines = [
        f'"{typed}" matches {len(matches)} places, so it is not clear which '
        "one you mean. Name one of these exactly:"
    ]
    lines += [f"  {describe_place(place)}" for place in matches]
    return "\n".join(lines)


def describe_place(place, status=True, tags=None):
    """One place on one line: name, neighborhood, status, tags, note snippet.

    Shared so that the candidate list ``visit`` prints when it refuses to
    guess and the wishlist ``todo`` prints describe a place the same way.
    Fields the place does not have are left out rather than printed empty, so
    a bare name renders as a bare name -- no ``None``, no empty brackets, no
    trailing separator.

    ``status`` is dropped by callers for whom it is constant: every line of
    ``todo`` is a wishlist place. ``tags`` takes an already-fetched iterable
    of tag names, because the caller is the one that knows whether it
    prefetched them.
    """
    parts = [place.name]
    if place.neighborhood:
        parts.append(place.neighborhood)
    if status:
        parts.append(place.status)
    if place.rating is not None:
        parts.append(f"rating {place.rating}")
    tag_names = list(tags or ())
    if tag_names:
        parts.append("tags: " + ", ".join(tag_names))
    snippet = note_snippet(place.note)
    if snippet:
        parts.append(snippet)
    return " - ".join(parts)


def note_snippet(note):
    """The head of ``note`` as a single line, cut to a bounded length.

    Collapsing whitespace is what keeps a multi-line note from turning one
    listed place into several lines of output.
    """
    collapsed = " ".join((note or "").split())
    if len(collapsed) <= NOTE_SNIPPET_LENGTH:
        return collapsed
    return collapsed[:NOTE_SNIPPET_LENGTH].rstrip() + TRUNCATION_MARKER
