"""``find``: get a place back when I only half-remember it.

    uv run python manage.py find "coffee wifi" --tag coffee --status visited

This command is a thin shell around :mod:`places.search` (issue #5). It decides
*which* places are worth looking at, hands them over as candidates, and prints
what comes back in the order it comes back. No scoring, no threshold, no
tie-breaking and no re-sorting happens here; if a result looks wrong, that is a
ranker question, not a printing question.

Design notes worth keeping in view:

* **Filters are hard, the confidence threshold is soft.** ``--tag``,
  ``--status`` and ``--neighborhood`` narrow the candidate set *in the ORM*,
  before anything is scored, and the weak-match fallback draws only from what
  survived them. Reaching past a filter to fill three slots would answer a
  question the user did not ask.
* **The candidates are prefetched.** The ranker reads every candidate's tags,
  and on un-prefetched rows that is one query per candidate. ``Place.tags`` is
  a related manager, so ``prefetch_related("tags")`` is what keeps the ranker
  pure in effect as well as in principle: the query count stays flat however
  many places the journal holds.
* **Tag names are normalized once, at the entry point,** through
  :func:`places.models.normalize_tag_name` (issue #2). ``Tag.name`` carries the
  ``NOCASE`` collation so the database already handles case on every query
  path, but it does not strip -- ``--tag " coffee "`` needs the normalizer.
* **A query with nothing to search for is rejected, not guessed at.** ``""``
  and ``"   "`` are the obvious cases; ``"---"`` and ``"!!"`` are the same
  thing wearing punctuation, and so is ``"кофе"`` -- real intent in a script
  the ranker's tokenizer cannot see. Such a query cannot express a preference
  between any two places, so printing "No strong match. Closest 3:" above
  three arbitrary places would be the tool pretending it looked. The fallback
  means "I looked and nothing was good enough", and that claim needs a real
  query behind it. **Which queries those are is the ranker's call, not this
  command's** (issue #18): the guard asks
  :func:`places.search.has_searchable_tokens` rather than re-deciding with a
  second copy of the tokenizer, which is how the two layers stopped
  disagreeing about ``"кофе"``. One rejection, one message, one exit code, for
  every query with nothing in it to match on.
* **Finding nothing is an answer, not a failure.** An empty journal, filters
  that eliminate everything and a weak fallback all exit 0. Only a malformed
  invocation exits non-zero.
"""

from django.core.management.base import BaseCommand, CommandError

from places.models import Place, normalize_tag_name
from places.search import has_searchable_tokens, rank_places

#: How many strong matches are printed when ``--limit`` is not given.
DEFAULT_LIMIT = 5

#: How much of a note survives on a result line, in characters. Notes are one
#: line here whatever they look like in the database, so this is also what
#: keeps a paragraph from swallowing the screen.
NOTE_SNIPPET_LENGTH = 80

#: Appended to a note that was cut, so a truncated line admits it.
TRUNCATION_MARKER = "…"

#: Raised when the query holds nothing the ranker can match on. One message,
#: word for word, covers ``""``, ``"   "``, ``"---"`` and ``"кофе"`` alike,
#: because the ranker draws no distinction between them either: all four leave
#: it with no tokens to score, and it answers all four with an empty, non-weak
#: result set. The query is not echoed back precisely so that the four stay one
#: rejection rather than becoming four similar ones (issue #18).
NOTHING_TO_SEARCH_FOR_MESSAGE = (
    "There is nothing to search for in that query: it needs at least one "
    'letter a-z or digit 0-9, e.g. find "coffee wifi".'
)

#: Printed when the journal itself is empty. Deliberately worded so no
#: substring of it can be confused with :data:`NO_FILTER_MATCHES_MESSAGE`.
EMPTY_JOURNAL_MESSAGE = (
    "The journal is empty, so there is nothing to search yet. "
    'Add a place first: manage.py add "Blue Bottle" --tag coffee'
)

#: Printed when the journal has places but the filters kept none of them.
NO_FILTER_MATCHES_MESSAGE = (
    "No places carry all of those filters. "
    "Try dropping a --tag, or widening --status or --neighborhood."
)


class Command(BaseCommand):
    help = "Search the journal for a place, ranked, with the closest guesses as a fallback."

    def add_arguments(self, parser):
        parser.add_argument(
            "query",
            help='What you half-remember, as one string: find "coffee wifi".',
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=DEFAULT_LIMIT,
            help=(
                f"How many strong matches to print. Defaults to {DEFAULT_LIMIT}. "
                "The weak-match fallback has its own, smaller cap."
            ),
        )
        parser.add_argument(
            "--status",
            choices=Place.Status.values,
            default=None,
            help="One of: " + ", ".join(Place.Status.values) + ".",
        )
        parser.add_argument(
            "--neighborhood",
            default="",
            help="Case-insensitive substring of the stored neighborhood.",
        )
        parser.add_argument(
            "--tag",
            action="append",
            dest="tags",
            default=None,
            metavar="TAG",
            help="Repeatable, and ANDed: --tag coffee --tag wifi.",
        )

    def handle(self, *args, **options):
        # --- the invocation has to make sense before anything is read -------
        query = (options["query"] or "").strip()
        if not has_searchable_tokens(query):
            raise CommandError(NOTHING_TO_SEARCH_FOR_MESSAGE)

        limit = options["limit"]
        if limit < 1:
            raise CommandError(
                f"--limit must be a whole number of at least 1; got {limit}."
            )

        # --- an empty journal is its own answer, not an empty filter set ----
        if not Place.objects.exists():
            self.stdout.write(EMPTY_JOURNAL_MESSAGE)
            return

        candidates = list(self._candidates(options))
        if not candidates:
            self.stdout.write(NO_FILTER_MATCHES_MESSAGE)
            return

        # --- everything below is the ranker's answer, printed verbatim ------
        results = rank_places(query, candidates)
        shown = results[:limit]

        if results.is_weak:
            # The header names what was actually printed: the filtered set may
            # hold fewer places than the fallback's own cap.
            self.stdout.write(f"No strong match. Closest {len(shown)}:")
        for result in shown:
            self.stdout.write(self._result_line(result.place))

    def _candidates(self, options):
        """The places worth scoring: filtered in the ORM, tags prefetched.

        Every filter is a hard one. What this returns is the whole world as far
        as the ranker is concerned, fallback included.
        """
        queryset = Place.objects.prefetch_related("tags")

        if options["status"]:
            queryset = queryset.filter(status=options["status"])

        neighborhood = (options["neighborhood"] or "").strip()
        if neighborhood:
            queryset = queryset.filter(neighborhood__icontains=neighborhood)

        # One filter call per tag is what ANDs them: a single call with several
        # values would keep a place carrying any one of them.
        for tag_name in self._tag_names(options):
            queryset = queryset.filter(tags__name=tag_name)

        return queryset

    def _tag_names(self, options):
        """The ``--tag`` values in canonical form, each counted once."""
        names = []
        for raw_tag in options["tags"] or []:
            canonical = normalize_tag_name(raw_tag)
            if canonical not in names:
                names.append(canonical)
        return names

    def _result_line(self, place):
        """One place on one line: name, neighborhood, status, rating, note.

        Fields the place does not have are left out rather than printed empty,
        so a wishlist entry with no rating and no note still reads as a
        sentence and nothing ever renders as ``None``.
        """
        parts = [place.name]
        if place.neighborhood:
            parts.append(place.neighborhood)
        parts.append(place.status)
        if place.rating is not None:
            parts.append(f"rating {place.rating}")

        snippet = self._note_snippet(place.note)
        if snippet:
            parts.append(snippet)

        return " - ".join(parts)

    def _note_snippet(self, note):
        """The head of ``note`` as a single line, cut to a bounded length.

        Collapsing whitespace is what keeps a multi-line note from turning one
        hit into several lines of output.
        """
        collapsed = " ".join((note or "").split())
        if len(collapsed) <= NOTE_SNIPPET_LENGTH:
            return collapsed
        return collapsed[:NOTE_SNIPPET_LENGTH].rstrip() + TRUNCATION_MARKER
