"""``surprise``: one place to go to today, weighted against the same-three-spots rut.

    uv run python manage.py surprise --tag coffee --neighborhood Mission

Design notes worth keeping in view:

* **Every place is a candidate, visited ones included.** ``todo`` is the
  command that lists only the wishlist; ``surprise`` deliberately keeps
  visited places in the pool so it can send me back to one I have neglected.
  There is no ``--status`` filter for the same reason -- restricting this to
  the wishlist would just be ``todo`` with dice.
* **The ladder is a fixed set of tiers, not a decay curve.** Four constants,
  four boundaries, every number assertable. A half-life score would be
  smoother and untestable; issue #8 rules it out on purpose.
* **A ``visited`` place with a null ``last_visited_at`` is old, not new.**
  ``add`` (issue #4) never stamps the field, so *every* place added with a
  rating lands here; only ``visit`` (issue #7) ever stamps it. Reading null as
  "never visited" would let those places outrank genuine wishlist entries;
  reading it as "visited just now" would bury exactly the places I logged and
  forgot. "Visited, date unknown, assume it was ages ago" is the honest
  reading, so it shares the oldest-visit tier.
* **Nothing here has weight 0.** Yesterday's cafe is unlikely, never
  impossible -- a candidate that survives the filters can always come up.
* **Filtering happens before weighting,** so ``--tag coffee`` re-normalizes
  the odds among coffee places instead of spending draws on excluded ones.
* **The randomness is command-local.** A private :class:`random.Random`, never
  the module-level ``random.seed()``: seeding the global generator from a
  management command would silently pin randomness for everything else in the
  process, the rest of a test suite included.
* **Candidates are ordered by primary key before the draw.** Without a fixed
  order the same seed picks different places depending on how SQLite happens
  to return rows, and every seeded assertion becomes flaky.
* **This command only ever reads.** No stamping, no "last suggested" field, no
  memory of previous picks -- repeats are correct behavior. Issue #8 puts all
  three out of scope, and remembering would need a schema change issue #2
  rules out.
* **The note is printed in full,** on its own line. It is the reason the
  suggestion is worth taking, so unlike ``find`` and ``todo`` -- which snip it
  to keep a *list* to one line per place -- nothing is cut here. The shared
  one-line description from :mod:`places.lookup` still supplies the headline,
  so a surprise reads like a ``todo`` line; it is handed a note-free copy of
  the place so the note does not appear twice, once truncated.
"""

import copy
import random
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from places.lookup import describe_place
from places.models import Place, normalize_tag_name

#: A place I have never been to. The whole point of the command, so it carries
#: the heaviest weight by a wide margin.
WISHLIST_WEIGHT = 10

#: A place I have not been back to in a long time -- or one whose visit date
#: is unknown, which is assumed to be the same thing.
LONG_AGO_WEIGHT = 5

#: A place I visited within the last few months but not recently.
MIDDLING_WEIGHT = 3

#: A place I was at days ago. Still reachable -- never 0 -- just unlikely.
RECENT_WEIGHT = 1

#: At or past this age a visit counts as long ago.
LONG_AGO_DAYS = 180

#: Below this age a visit counts as recent. Between the two is the middle tier.
RECENT_DAYS = 30

#: Printed when the journal itself holds no places at all. Deliberately shares
#: no distinctive phrase with :data:`NO_CANDIDATES_MESSAGE`: "your journal is
#: empty" and "your filters were too narrow" are different news.
EMPTY_JOURNAL_MESSAGE = (
    "There are no places in the journal yet, so there is nothing to suggest. "
    'Add one first: manage.py add "Blue Bottle" --tag coffee'
)

#: Printed when there are places but the filters excluded every one of them.
#: Formatted with the filters that were actually applied, so the message
#: echoes back what narrowed the pool.
NO_CANDIDATES_MESSAGE = (
    "Nothing matches those filters ({filters}), so there is nothing to "
    "suggest. Try dropping the --tag, or widening --neighborhood."
)

#: How a stamped visit date is rendered. Date only: the hour I walked into a
#: bar eight months ago is not what makes the suggestion make sense.
VISIT_DATE_FORMAT = "%Y-%m-%d"

#: Said of a wishlist place -- status is the authority on "have I been here".
NEVER_VISITED_REASON = "never visited"

#: Said of a visited place with no stamped date. Distinct from both the
#: never-visited wording and the dated wording, so the output tells a reader
#: which of the three tiers-by-reason the place landed in.
UNKNOWN_DATE_REASON = "visited, date unknown"


def age_of(place, now):
    """How long ago ``place`` was last visited, as a never-negative timedelta.

    A ``last_visited_at`` in the future -- clock skew, or a typo in the admin
    -- is clamped to zero. One clamp, in one place, because both the weight
    and the printed reason are computed from this: an unclamped age would put
    a future visit on a rung it does not belong to *and* report it as
    "-400 days ago".

    Only meaningful for a place that has a ``last_visited_at``; callers check.
    """
    return max(now - place.last_visited_at, timedelta(0))


def weight_for(place, now):
    """How likely ``place`` is to be picked, as a whole number of tickets.

    ``now`` is passed in rather than read here so that every candidate in one
    run is aged against the same instant -- otherwise a large journal would
    have its last places judged microseconds later than its first.

    Never returns 0: every candidate that survives the filters is reachable.
    """
    if place.status == Place.Status.WISHLIST:
        return WISHLIST_WEIGHT
    if place.last_visited_at is None:
        return LONG_AGO_WEIGHT

    age = age_of(place, now)
    if age >= timedelta(days=LONG_AGO_DAYS):
        return LONG_AGO_WEIGHT
    if age >= timedelta(days=RECENT_DAYS):
        return MIDDLING_WEIGHT
    return RECENT_WEIGHT


class Command(BaseCommand):
    help = "Suggest one place to go to, biased towards the neglected ones."

    def add_arguments(self, parser):
        parser.add_argument(
            "--tag",
            default=None,
            help=(
                "Only consider places carrying this tag. Case-insensitive, "
                "and one tag per run: --tag coffee."
            ),
        )
        parser.add_argument(
            "--neighborhood",
            default=None,
            help=(
                "Only consider places in this neighborhood. The whole value, "
                "matched case-insensitively -- not a substring."
            ),
        )
        parser.add_argument(
            "--seed",
            type=int,
            default=None,
            help=(
                "Pin the draw so the same data yields the same pick. A testing "
                "aid, and a way to reproduce a suggestion; omit it for a real "
                "surprise."
            ),
        )

    def handle(self, *args, **options):
        tag_name = normalize_tag_name(options["tag"])
        neighborhood = (options["neighborhood"] or "").strip()

        # Asked before filtering, because "the journal is empty" and "your
        # filters matched nothing" are different pieces of news and only the
        # unfiltered question can tell them apart.
        if not Place.objects.exists():
            raise CommandError(EMPTY_JOURNAL_MESSAGE)

        candidates = list(self._candidates(tag_name, neighborhood))
        if not candidates:
            raise CommandError(self._no_candidates_message(tag_name, neighborhood))

        now = timezone.now()
        weights = [weight_for(place, now) for place in candidates]

        # A private generator, seeded with `None` when `--seed` was not given,
        # which is `random.Random`'s own "seed from the OS" case. The
        # process-wide `random` state is never read and never written.
        rng = random.Random(options["seed"])
        place = rng.choices(candidates, weights=weights, k=1)[0]

        self.stdout.write(self._headline(place))
        self.stdout.write(f"Why: {self._reason(place, now)}")
        note = (place.note or "").strip()
        if note:
            self.stdout.write(f"Note: {note}")

    def _candidates(self, tag_name, neighborhood):
        """Every place that survives the filters, in a fixed order.

        ``order_by("pk")`` is the documented candidate order: insertion order,
        total, and stable across runs. It is what makes ``--seed`` mean the
        same thing twice -- without it the draw rides on whatever order
        SQLite happens to return rows in.

        ``prefetch_related`` because the headline names the place's tags, and
        ``Place.tags`` is a related manager; one place is drawn but the whole
        candidate set is fetched, so the prefetch keeps that at two queries.
        """
        queryset = Place.objects.prefetch_related("tags").order_by("pk")

        if tag_name:
            # `Tag.name` carries the NOCASE collation (issue #2), so the
            # database already ignores case; `normalize_tag_name` is still
            # what strips, which the collation does not do.
            queryset = queryset.filter(tags__name=tag_name)
        if neighborhood:
            queryset = queryset.filter(neighborhood__iexact=neighborhood)

        return queryset

    def _no_candidates_message(self, tag_name, neighborhood):
        """Names the filters that emptied the pool, so the reader can widen them."""
        applied = []
        if tag_name:
            applied.append(f"--tag {tag_name}")
        if neighborhood:
            applied.append(f"--neighborhood {neighborhood}")
        return NO_CANDIDATES_MESSAGE.format(filters=", ".join(applied))

    def _headline(self, place):
        """The place on one line: name, neighborhood, status, rating, tags.

        Described through the shared :func:`places.lookup.describe_place` so a
        surprise reads like a ``todo`` line -- and handed a note-free *copy*,
        because that helper always appends a snipped note and this command
        prints the note in full on its own line. Printing both would show the
        same text twice, once cut. The copy is in memory only; it is never
        saved and the real row is untouched.
        """
        without_note = copy.copy(place)
        without_note.note = ""
        return "Go here: " + describe_place(
            without_note, tags=[tag.name for tag in place.tags.all()]
        )

    def _reason(self, place, now):
        """Why this place came up, in a form that makes the weighting visible."""
        if place.status == Place.Status.WISHLIST:
            return NEVER_VISITED_REASON
        if place.last_visited_at is None:
            return UNKNOWN_DATE_REASON

        stamped = place.last_visited_at.strftime(VISIT_DATE_FORMAT)
        days = age_of(place, now).days
        if days == 0:
            elapsed = "today"
        elif days == 1:
            elapsed = "1 day ago"
        else:
            elapsed = f"{days} days ago"
        return f"last visited {stamped} ({elapsed})"
