"""``todo``: what I still owe the city -- the places on the wishlist.

    uv run python manage.py todo --tag coffee --neighborhood Mission

Design notes worth keeping in view:

* **Oldest first.** Ascending ``created_at``, so the place that has sat on the
  list longest is at the top -- that is the whole point of the list. Ties are
  broken by name, case-insensitively, so two places created in the same
  instant still print in a fixed order. Both are done by the database, not by
  sorting in Python.
* **The listing is prefetched.** Each line names the place's tags, and
  ``Place.tags`` is a related manager: without ``prefetch_related`` that is one
  extra query per wishlist place, so the query count would grow with the
  journal.
* **Tag names are normalized once, at the entry point,** through
  :func:`places.models.normalize_tag_name` (issue #2). ``Tag.name`` carries the
  ``NOCASE`` collation, so the database already handles case on every query
  path -- but it does not strip, and ``--tag " Coffee "`` needs the normalizer.
* **``--neighborhood`` matches the whole value,** case-insensitively:
  ``mission`` finds a stored ``Mission`` and ``Miss`` does not. Partial
  matching is deliberately ``find``'s job (issue #6), not this list's.
* **Finding nothing is an answer, not a failure.** All three empty cases exit
  0, and each says something different -- an empty journal, a cleared wishlist
  and filters that matched nothing are three different pieces of news, and one
  message for all three would be useless in two of them.
* **The place line comes from :mod:`places.lookup`,** which ``visit`` shares,
  so a place reads the same here as it does in that command's candidate list.
"""

from django.core.management.base import BaseCommand
from django.db.models.functions import Lower

from places.lookup import describe_place
from places.models import Place, normalize_tag_name

#: Printed when the journal itself holds no places at all. An empty journal is
#: a valid state -- a new install -- so it is news, not an error.
EMPTY_JOURNAL_MESSAGE = (
    "The journal is empty, so there is nothing on the wishlist yet. "
    'Add a place first: manage.py add "Blue Bottle" --tag coffee'
)

#: Printed when there are places, but every one of them has been visited.
#: Deliberately worded so that no substring of it can be mistaken for
#: :data:`EMPTY_JOURNAL_MESSAGE`: the journal is not empty, the list is.
WISHLIST_CLEARED_MESSAGE = (
    "Nothing left on the wishlist -- every place in the journal has been "
    'visited. Add the next one: manage.py add "Blue Bottle" --tag coffee'
)

#: Printed when filters were given and nothing on the wishlist carried them.
NO_FILTER_MATCHES_MESSAGE = (
    "No wishlist places carry those filters. "
    "Try dropping the --tag, or widening --neighborhood."
)


class Command(BaseCommand):
    help = "List the places still on the wishlist, oldest first."

    def add_arguments(self, parser):
        parser.add_argument(
            "--tag",
            default=None,
            help=(
                "Only places carrying this tag. Case-insensitive, and one tag "
                "per run: --tag coffee."
            ),
        )
        parser.add_argument(
            "--neighborhood",
            default=None,
            help=(
                "Only places in this neighborhood. The whole value, matched "
                "case-insensitively -- not a substring."
            ),
        )

    def handle(self, *args, **options):
        tag_name = normalize_tag_name(options["tag"])
        neighborhood = (options["neighborhood"] or "").strip()
        filtered = bool(tag_name or neighborhood)

        # Asked before filtering, because "the journal is empty" and "nothing
        # matched" are different pieces of news and only the unfiltered
        # question can tell them apart.
        if not Place.objects.exists():
            self.stdout.write(EMPTY_JOURNAL_MESSAGE)
            return

        places = list(self._wishlist(tag_name, neighborhood))
        if not places:
            self.stdout.write(
                NO_FILTER_MATCHES_MESSAGE if filtered else WISHLIST_CLEARED_MESSAGE
            )
            return

        self.stdout.write(self._header(len(places)))
        for place in places:
            self.stdout.write(self._place_line(place))

    def _wishlist(self, tag_name, neighborhood):
        """The wishlist places to print, filtered and ordered in the database.

        ``Lower("name")`` is the tie-break rather than plain ``name`` because
        SQLite compares ``Place.name`` case-sensitively -- it has no ``NOCASE``
        collation, unlike ``Tag.name`` -- which would sort ``Zoo`` before
        ``abc``. ``pk`` last so the order is total.
        """
        queryset = (
            Place.objects.filter(status=Place.Status.WISHLIST)
            .prefetch_related("tags")
            .order_by("created_at", Lower("name"), "pk")
        )

        if tag_name:
            queryset = queryset.filter(tags__name=tag_name)
        if neighborhood:
            queryset = queryset.filter(neighborhood__iexact=neighborhood)

        return queryset

    def _header(self, count):
        """The count, so the list says how much is on it without counting lines."""
        places = "place" if count == 1 else "places"
        return f"{count} {places} on the wishlist, oldest first:"

    def _place_line(self, place):
        """One wishlist place on one line.

        The status is left off: every line here is a wishlist place, so
        printing it would be noise on every row.
        """
        return describe_place(
            place, status=False, tags=[tag.name for tag in place.tags.all()]
        )
