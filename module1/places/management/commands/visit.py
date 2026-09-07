"""``visit``: flip a place to visited, stamp when, and record it while it is fresh.

    uv run python manage.py visit "Blue Bottle" --rating 4 --note "great cortado"

Design notes worth keeping in view:

* **This command is the only writer of ``last_visited_at``.** ``add`` (issue
  #4) never stamps it, and issue #2 deliberately left the model without a
  ``save()`` override or a signal that would. A place logged today may have
  been visited last month, so only an actual ``visit`` knows that "now" is
  true. ``surprise`` (issue #8) reads a null there as "never visited".
* **Resolution is delegated to :mod:`places.lookup`,** which ``todo`` shares.
  It is two-stage and exact-before-partial, and it does no fuzzy matching on
  purpose: this command writes, so it must be predictable. ``find`` (issue #6)
  is where typo tolerance lives, and nothing from it may be imported here.
* **Nothing is written until everything has been validated.** A bad
  ``--rating``, an unknown name and an ambiguous name all leave the database
  exactly as it was -- the status is not flipped and no timestamp is stamped.
* **A flag that was not passed never changes its field.** ``--note`` and
  ``--rating`` default to ``None``, which is what distinguishes "left alone"
  from ``--note ""``, an explicit request to clear the note. Given a value,
  they *replace*; there is no appending and no visit history (issue #7 rules
  both out).
* **Re-visiting an already-visited place is allowed** and re-stamps the
  timestamp, which is the point of the field being *last* visited. The output
  says so, so replacing an existing note is never a silent surprise.
* **Non-interactive by design.** No ``input()``, ever: the command has to work
  under ``call_command`` and in scripts, so the note and rating arrive as
  flags.
"""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from places.lookup import PlaceNameError, describe_place, resolve_place
from places.models import Place

#: The inclusive bounds the model's validators enforce (issue #2). Checked here
#: too, so a bad value is a one-line ``CommandError`` rather than a traceback --
#: and checked *before* the write, so a rejected run changes nothing.
MIN_RATING = 1
MAX_RATING = 5

#: How the stamped time is rendered back to the user. Stored values are
#: timezone-aware UTC (``USE_TZ = True``), and this says so rather than
#: printing a bare number that could be read as local time.
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M %Z"


class Command(BaseCommand):
    help = "Mark a place as visited, stamping when, with an optional note and rating."

    def add_arguments(self, parser):
        parser.add_argument(
            "name",
            help=(
                'The place\'s name, e.g. "Blue Bottle". Matched case-insensitively: '
                "the whole name first, then as a substring."
            ),
        )
        parser.add_argument(
            "--note",
            default=None,
            help=(
                "Replaces the place's note. Omit it to leave the note alone; "
                'pass --note "" to clear it.'
            ),
        )
        parser.add_argument(
            "--rating",
            type=int,
            default=None,
            help=(
                f"A whole number from {MIN_RATING} to {MAX_RATING}, replacing any "
                "previous rating. Omit it to leave the rating alone."
            ),
        )

    def handle(self, *args, **options):
        # --- validate everything first; write nothing yet -------------------
        rating = options["rating"]
        if rating is not None and not MIN_RATING <= rating <= MAX_RATING:
            raise CommandError(
                f"--rating must be a whole number from {MIN_RATING} to "
                f"{MAX_RATING}; got {rating}."
            )

        try:
            place = resolve_place(options["name"])
        except PlaceNameError as error:
            # One error class per failure mode upstream, one exit code here:
            # `CommandError` is what makes `manage.py` print the message and
            # exit non-zero instead of returning success.
            raise CommandError(error.message) from error

        # --- everything is valid; now write ---------------------------------
        was_visited = place.status == Place.Status.VISITED
        previous_visit = place.last_visited_at

        place.status = Place.Status.VISITED
        place.last_visited_at = timezone.now()
        # `USE_TZ = True`, so this is aware UTC. A naive `datetime.now()` here
        # would make Django warn and store a value nothing can compare safely.

        # Only the fields the user actually asked about are touched, and
        # `update_fields` is what guarantees it: tags, neighborhood, address,
        # name and created_at cannot be caught up in this write even by
        # accident.
        changed = ["status", "last_visited_at"]
        if options["note"] is not None:
            place.note = options["note"].strip()
            changed.append("note")
        if rating is not None:
            place.rating = rating
            changed.append("rating")
        place.save(update_fields=changed)

        if was_visited:
            self.stdout.write(self._repeat_visit_notice(place, previous_visit))
        self.stdout.write(self._confirmation(place))

    def _repeat_visit_notice(self, place, previous_visit):
        """Said before the confirmation when this was not the first visit.

        Without it, ``--note`` quietly overwriting a note written the first
        time round would be invisible in the output.
        """
        when = (
            previous_visit.strftime(TIMESTAMP_FORMAT)
            if previous_visit is not None
            else "date unrecorded"
        )
        return (
            f'"{place.name}" was already visited ({when}); re-stamping it. '
            "A --note or --rating given here replaces the old one."
        )

    def _confirmation(self, place):
        """One line describing what was actually stored."""
        stamped = place.last_visited_at.strftime(TIMESTAMP_FORMAT)
        return f"Visited {describe_place(place)} - stamped {stamped}"
