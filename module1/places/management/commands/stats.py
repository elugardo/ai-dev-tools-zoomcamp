"""``stats``: how much of this city have I actually touched.

    uv run python manage.py stats

One fixed screen, no flags. Four sections in a fixed order -- the counts, the
neighborhoods reached, the per-neighborhood breakdown, and the tags I keep
reaching for.

Design notes worth keeping in view:

* **Every number is computed by the database.** ``aggregate`` and
  ``annotate``/``Count`` do all four sections; nothing is tallied in Python,
  no ``Counter``, no dict, no loop over places. Issue #2 rules out
  denormalized counters on the models, and this command exists precisely so
  none are needed: the report is derived from the rows every time it runs.
* **The query count is fixed at four, whatever the journal holds.** One
  aggregate for the counts, one for the neighborhoods reached, one grouped
  query for the breakdown and one for the tags. Each row-returning query is
  materialized before it is rendered, because the renderer measures the
  widest label and *then* prints -- two passes over a lazy queryset would be
  two round trips.
* **The many-to-many is never joined by the counting queries.**
  ``Place.tags`` is a m2m, so a query that joins it returns one row per
  (place, tag) pair and a place with three tags would be counted three
  times. The counts and the neighborhood breakdown are taken straight off
  ``Place``, with no tag join anywhere near them, and count distinct primary
  keys even so. The tag section is the only place a join happens, and there
  it counts distinct *places* per tag.
* **Blank neighborhoods are a bucket, not a hole.** A place whose
  ``neighborhood`` is blank lands under :data:`NO_NEIGHBORHOOD_LABEL`,
  printed last, and is deliberately *not* a neighborhood "touched": I have
  not reached a neighborhood by failing to name one. So the breakdown always
  sums to the total while the touched count does not count that row.
* **What counts as blank is decided on write, not here.** ``Place.save``
  runs every free-text column through ``places.models.normalize_text``
  (issue #23), so a neighborhood of tabs, newlines or spaces is stored as
  ``""`` and a padded ``"	Mission
"`` is stored as ``Mission``. This
  command therefore does not have to define "blank" itself, and neither do
  ``todo``, ``surprise`` or ``find``. The ``Trim`` below is a residual safety
  net for rows that reached the table without going through ``save()`` at
  all -- a ``QuerySet.update()`` or raw SQL -- and *not* a second statement
  of the rule: it is SQLite's ``TRIM()``, which strips ``U+0020`` and nothing
  else, so it could never have been the rule in the first place. That
  mismatch is what issue #23 was.
* **Ordering is total, so two runs on the same data print the same screen.**
  Count descending, then name ascending, then a last tiebreak that cannot
  tie. Without it the tag cut at five would depend on whatever order SQLite
  happened to return rows in, and the fifth row would flicker between two
  tags with equal counts.
* **Names are sorted case-insensitively.** ``Tag.name`` carries the ``NOCASE``
  collation so it already is; ``Place.neighborhood`` does not, so ``Lower``
  supplies it -- otherwise ``Soma`` would sort ahead of ``mission``.
* **An empty journal gets a sentence, not a table of zeros.** Reported off
  the total the counts query already fetched, so the empty case costs no
  extra query.
"""

from django.core.management.base import BaseCommand
from django.db.models import Case, CharField, Count, F, IntegerField, Q, Value, When
from django.db.models.functions import Lower, Trim

from places.models import Place, Tag

#: The literal row label for places with no neighborhood recorded. A row, so
#: the breakdown still sums to the total, and named rather than blank so the
#: reader can see that the places exist.
NO_NEIGHBORHOOD_LABEL = "(no neighborhood)"

#: How many tags the report shows. Fixed: issue #9 rules out a ``--top`` flag.
TOP_TAG_LIMIT = 5

#: How wide the label column is allowed to grow before rows stop being padded
#: to match it. Without a cap, one 100-character neighborhood name would push
#: every count in the section past an 80-column terminal.
LABEL_COLUMN_LIMIT = 40

#: Indent on a row, so rows read as belonging to the heading above them.
ROW_INDENT = "  "

#: Two spaces between the padded label and its count, so the number never
#: touches the longest name in the column.
COUNT_GAP = "  "

COUNTS_HEADING = "Counts:"
TOUCHED_HEADING = "Neighborhoods touched:"
NEIGHBORHOOD_HEADING = "Places per neighborhood:"
TAGS_HEADING = "Top tags:"

TOTAL_LABEL = "total places"
VISITED_LABEL = "visited"
WISHLIST_LABEL = "wishlist"

#: Printed, alone, when there are no places at all. Deliberately carries none
#: of the four headings and no digits, so an empty journal cannot be mistaken
#: for a report full of zeros.
EMPTY_JOURNAL_MESSAGE = (
    "Nothing recorded yet -- the journal has no places, so there is no "
    'coverage to report. Add the first one: manage.py add "Blue Bottle" '
    "--tag coffee"
)

#: Printed under the tags heading when places exist but none carries a tag.
NO_TAGS_MESSAGE = (
    'No tags used yet -- add one with: manage.py add "Blue Bottle" --tag coffee'
)


class Command(BaseCommand):
    help = "Show journal coverage: counts, neighborhoods and the most-used tags."

    # No `add_arguments`: the report is one fixed view. Issue #9 rules out
    # every filter, `--top N` and machine-readable output by name.

    def handle(self, *args, **options):
        summary = self._summary()

        # The counts query already knows whether the journal is empty, so the
        # empty case costs nothing extra.
        if not summary["total"]:
            self.stdout.write(EMPTY_JOURNAL_MESSAGE)
            return

        self._write_rows(
            COUNTS_HEADING,
            [
                (TOTAL_LABEL, summary["total"]),
                (VISITED_LABEL, summary["visited"]),
                (WISHLIST_LABEL, summary["wishlist"]),
            ],
        )

        self.stdout.write("")
        self.stdout.write(f"{TOUCHED_HEADING} {self._neighborhoods_touched()}")

        self.stdout.write("")
        self._write_rows(NEIGHBORHOOD_HEADING, self._neighborhood_rows())

        self.stdout.write("")
        self._write_tag_section()

    def _summary(self):
        """Total, visited and wishlist counts, in one query and no join.

        ``filter=Q(...)`` puts both status counts in the same aggregate, so
        the three numbers are read off the same table scan and cannot
        disagree with each other. Counting ``pk`` -- distinct, belt and
        braces -- rather than ``tags`` is what keeps a place with three tags
        worth one place.
        """
        return Place.objects.aggregate(
            total=Count("pk", distinct=True),
            visited=Count("pk", distinct=True, filter=Q(status=Place.Status.VISITED)),
            wishlist=Count(
                "pk", distinct=True, filter=Q(status=Place.Status.WISHLIST)
            ),
        )

    def _neighborhoods_touched(self):
        """How many distinct neighborhoods the journal has actually reached.

        ``exclude(trimmed="")`` is the whole point: a place with no
        neighborhood is still a place, and still shows in the breakdown, but
        it is not a neighborhood I have been to. Dropping the exclude would
        report one neighborhood too many the moment a single place is
        recorded without one.

        Blank has already been decided by ``Place.save`` before the row got
        here; ``Trim`` only catches a space-padded row that bypassed it.
        """
        return (
            Place.objects.annotate(trimmed=Trim("neighborhood"))
            .exclude(trimmed="")
            .aggregate(touched=Count("trimmed", distinct=True))["touched"]
        )

    def _neighborhood_rows(self):
        """``(label, count)`` per neighborhood, ordered and materialized.

        Grouped in the database by the *displayed* label, so blank
        neighborhoods collapse into one bucket instead of one row each.
        ``is_blank`` is carried alongside purely to sort with: it is the
        first ordering key, which pins :data:`NO_NEIGHBORHOOD_LABEL` last
        however large its count grows.

        The explicit ``order_by`` also clears ``Place.Meta.ordering``, which
        would otherwise join ``name`` to the ``GROUP BY`` and give every
        place a row of its own.
        """
        rows = (
            Place.objects.annotate(trimmed=Trim("neighborhood"))
            .annotate(
                is_blank=Case(
                    When(trimmed="", then=Value(1)),
                    default=Value(0),
                    output_field=IntegerField(),
                ),
                label=Case(
                    When(trimmed="", then=Value(NO_NEIGHBORHOOD_LABEL)),
                    default=F("trimmed"),
                    output_field=CharField(),
                ),
            )
            .values("label", "is_blank")
            .annotate(place_count=Count("pk", distinct=True))
            .order_by("is_blank", "-place_count", Lower("label"), "label")
        )
        # Reshaping already-aggregated rows for the renderer. Nothing is
        # counted here; every number in the pair came out of the database.
        return [(row["label"], row["place_count"]) for row in rows]

    def _tag_rows(self):
        """``(name, count)`` for the most-used tags, at most :data:`TOP_TAG_LIMIT`.

        ``Count("places", distinct=True)`` counts the *places* on the far
        side of the many-to-many, so the number is "how many places carry
        this tag" rather than how many join rows exist.

        ``filter(place_count__gt=0)`` becomes a ``HAVING``, which is what
        keeps a tag nobody uses off the report instead of listing it at 0.
        The slice becomes a ``LIMIT``, so the cut at five happens in the
        database and the query never returns more than five rows.
        """
        rows = (
            Tag.objects.annotate(place_count=Count("places", distinct=True))
            .filter(place_count__gt=0)
            .order_by("-place_count", "name", "pk")[:TOP_TAG_LIMIT]
        )
        return [(tag.name, tag.place_count) for tag in rows]

    def _write_tag_section(self):
        """The tags, or a line saying there are none yet -- never a bare heading."""
        rows = self._tag_rows()
        if not rows:
            self.stdout.write(TAGS_HEADING)
            self.stdout.write(ROW_INDENT + NO_TAGS_MESSAGE)
            return
        self._write_rows(TAGS_HEADING, rows)

    def _write_rows(self, heading, rows):
        """A heading and its rows, labels in one column and counts in another.

        ``rows`` is an already-materialized list of ``(label, count)`` pairs,
        because the width is measured in one pass and printed in the next: a
        lazy queryset would be fetched twice.
        """
        self.stdout.write(heading)
        width = min(max(len(label) for label, _ in rows), LABEL_COLUMN_LIMIT)
        for label, count in rows:
            self.stdout.write(f"{ROW_INDENT}{label.ljust(width)}{COUNT_GAP}{count}")
