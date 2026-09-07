"""``add``: capture a place in one line, with no follow-up questions.

    uv run python manage.py add "Blue Bottle" --neighborhood Mission \
        --tag coffee --tag wifi --note "good wifi, quiet before 10"

Design notes worth keeping in view:

* **Tag names are normalized once, at the entry point.** Every ``--tag`` value
  goes through :func:`places.models.normalize_tag_name`, the project's single
  normalizer (issue #2). It strips surrounding whitespace *and* folds case,
  which matters because ``Tag.name`` carries the ``NOCASE`` collation: the
  database handles case on every query path, but it does not strip. There is
  deliberately no second normalizer here.
* **Status is inferred only when the user said nothing.** A rating means the
  place has been visited; an explicit ``--status`` always wins, silently. The
  two states are read off ``Place.Status`` (issue #2) rather than spelled out,
  so adding or renaming a state cannot leave this command behind.
* **This command never stamps ``last_visited_at``.** That is the ``visit``
  command's job (issue #7). ``add`` does not know *when* the visit happened --
  a place logged today may have been visited last month -- and ``surprise``
  (issue #8) reads a null there as "no known recent visit", which surfaces the
  place sooner. A fabricated "now" would hide it for months.
* **Nothing is written until every value has been validated,** so a rejected
  run cannot leave an orphaned tag row behind.
"""

import re

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from places.models import Place, Tag, normalize_tag_name

#: The inclusive bounds the model's validators enforce (issue #2). Checked here
#: too, so a bad value is a one-line ``CommandError`` rather than a traceback.
MIN_RATING = 1
MAX_RATING = 5


class Command(BaseCommand):
    help = "Add a place to the journal in one line."

    def add_arguments(self, parser):
        parser.add_argument("name", help='The place\'s name, e.g. "Blue Bottle".')
        parser.add_argument(
            "--neighborhood", default="", help="Where in the city it is."
        )
        parser.add_argument(
            "--address", default="", help="Free text, stored verbatim."
        )
        parser.add_argument("--note", default="", help="Whatever is worth remembering.")
        parser.add_argument(
            "--rating",
            type=int,
            default=None,
            help=(
                f"A whole number from {MIN_RATING} to {MAX_RATING}. "
                "Giving one implies the place has been visited."
            ),
        )
        parser.add_argument(
            "--status",
            choices=Place.Status.values,
            default=None,
            help=(
                "One of: "
                + ", ".join(Place.Status.values)
                + ". Overrides what --rating would imply."
            ),
        )
        parser.add_argument(
            "--tag",
            action="append",
            dest="tags",
            default=None,
            metavar="TAG",
            help="Repeatable: --tag coffee --tag wifi.",
        )

    def handle(self, *args, **options):
        # --- validate everything first; write nothing yet -------------------
        name = (options["name"] or "").strip()
        if not name:
            raise CommandError('A name is required, e.g. add "Blue Bottle".')

        rating = options["rating"]
        if rating is not None and not MIN_RATING <= rating <= MAX_RATING:
            raise CommandError(
                f"--rating must be a whole number from {MIN_RATING} to "
                f"{MAX_RATING}; got {rating}."
            )

        tag_names = []
        for raw_tag in options["tags"] or []:
            canonical = normalize_tag_name(raw_tag)
            if not canonical:
                raise CommandError("--tag needs a value; a blank tag is not a tag.")
            # The same tag typed twice, in any case, attaches once.
            if canonical not in tag_names:
                tag_names.append(canonical)

        neighborhood = (options["neighborhood"] or "").strip()
        address = (options["address"] or "").strip()
        note = (options["note"] or "").strip()

        # An explicit --status wins; the inference only fills a gap the user left.
        status = options["status"] or (
            Place.Status.VISITED if rating is not None else Place.Status.WISHLIST
        )

        # Counted before the insert so the number describes the *other* places.
        # ``Place.name`` has no case-insensitive collation of its own -- unlike
        # ``Tag.name`` -- and this comparison exists purely to decide whether to
        # warn, so it is anchored on the raw name and never touches what is
        # stored.
        collisions = list(
            Place.objects.filter(
                name__iregex=r"^" + re.escape(name) + r"$"
            ).values_list("name", flat=True)
        )

        # --- everything is valid; now write ---------------------------------
        with transaction.atomic():
            # ``Tag.name``'s NOCASE collation makes this ``get`` find a stored
            # row whatever case it was created in, so no duplicate is inserted.
            tags = [Tag.objects.get_or_create(name=n)[0] for n in tag_names]
            place = Place.objects.create(
                name=name,
                neighborhood=neighborhood,
                address=address,
                note=note,
                rating=rating,
                status=status,
            )
            if tags:
                # ``add`` never detaches a tag from anything else.
                place.tags.add(*tags)

        self.stdout.write(self._confirmation(place, tag_names))

        if collisions:
            others = "place is" if len(collisions) == 1 else "places are"
            # Quote the *stored* spelling, which is the row the user should go
            # look at -- it may be capitalized differently from what they typed.
            self.stderr.write(
                f"Warning: {len(collisions)} other {others} already named "
                f'"{collisions[0]}".'
            )

    def _confirmation(self, place, tag_names):
        """One line describing what was actually stored, not what was typed."""
        headline = f'Added "{place.name}"'
        if place.neighborhood:
            headline += f" ({place.neighborhood})"

        details = [place.status]
        if place.rating is not None:
            details.append(f"rating {place.rating}")
        if tag_names:
            details.append("tags: " + ", ".join(tag_names))

        return f"{headline} - " + ", ".join(details)
