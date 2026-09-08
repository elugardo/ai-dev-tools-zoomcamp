"""Data model for City Journal: the places I've been or want to go, and their tags.

Two models live here and nothing else. ``Tag`` is a lowercase-only label;
``Place`` is the journal entry itself.

Tags are case-insensitive **end to end**, and that is enforced in one place:
``Tag.name`` carries the ``NOCASE`` collation, so SQLite compares the column
case-insensitively for *every* query -- direct manager lookups, ``Q`` objects,
relation traversals like ``Place.objects.filter(tags__name="Coffee")``, and the
unique constraint itself. A caller cannot write a lookup that quietly misses a
stored tag because of case.

Names are still *stored* lowercase: :func:`normalize_tag_name` is the canonical
form and runs on every write, so ``str(tag)``, admin lists and command output
are always ``coffee``. Callers should still run untrusted input through
``normalize_tag_name`` -- it also strips surrounding whitespace, which the
collation does not.

``Place``'s free text is normalized on write for the same reason (issue #23),
and by the same shape of rule: :func:`normalize_text` strips it, and
``Place.save`` applies that to every free-text column so no caller has to.

Why it lives here rather than in the queries that read it: SQLite's ``TRIM()``
-- which is what Django's ``Trim`` compiles to -- strips ``U+0020`` and
nothing else, while Python's ``str.strip()`` strips tabs, newlines and the
rest of Unicode whitespace. Any comparison that trims on one side in SQL and
the other in Python therefore disagrees with itself: ``stats`` grouped a
tab-only neighborhood into its own visually blank row and counted it as a
neighborhood touched, and ``todo``, ``surprise`` and ``find`` would have
failed to match a ``--neighborhood`` whose stored value was padded with tabs.
Normalizing on write means the column never holds the disputed value, so every
reader -- including ones not written yet -- inherits the guarantee instead of
having to remember it. Same argument as ``Tag.name``'s ``NOCASE`` collation
over a per-queryset override.
"""

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


def normalize_tag_name(name):
    """Return the canonical stored form of a tag name: stripped and lowercased.

    This is the single source of truth for what a tag name *is*. Use it
    wherever a tag name arrives from outside the database -- a ``--tag`` flag,
    a search query, a comparison -- so that ``Coffee``, ``  COFFEE `` and
    ``coffee`` all mean the one tag.
    """
    if name is None:
        return ""
    return str(name).strip().lower()


def normalize_text(value):
    """Return the canonical stored form of a free-text field: stripped.

    The single source of truth for what free text on a :class:`Place` *is*.
    ``None`` becomes ``""`` rather than staying ``None``: issue #2's criteria
    give these columns ``blank=True`` without ``null=True``, so "no value" is
    the empty string and never NULL.

    It strips with Python's ``str.strip()``, which removes tabs, newlines and
    every other Unicode space -- not just ``U+0020``, which is all SQLite's
    ``TRIM()`` removes. That difference is the whole reason this exists; see
    the module docstring.
    """
    if value is None:
        return ""
    return str(value).strip()


class Tag(models.Model):
    """A lowercase label attached to places: ``coffee``, ``ramen``, ``wifi``."""

    #: ``db_collation="NOCASE"`` makes SQLite compare this column without
    #: regard to case, on every query path and in the unique index. It is what
    #: keeps ``Place.objects.filter(tags__name="Coffee")`` working even though
    #: ``Place.objects`` knows nothing about tag normalization.
    name = models.CharField(max_length=50, unique=True, db_collation="NOCASE")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def clean(self):
        """Normalize before ``full_clean`` checks uniqueness.

        Keeps validation and the database agreeing on what the stored name
        will be, so the admin reports a duplicate rather than exploding on the
        constraint after validation passed.
        """
        super().clean()
        self.name = normalize_tag_name(self.name)

    def save(self, *args, **kwargs):
        self.name = normalize_tag_name(self.name)
        return super().save(*args, **kwargs)


class Place(models.Model):
    """A place in the city I have been to, or still want to go to."""

    class Status(models.TextChoices):
        """The two states a place can be in. Refer to these, not to strings."""

        WISHLIST = "wishlist", "Wishlist"
        VISITED = "visited", "Visited"

    name = models.CharField(max_length=200)
    neighborhood = models.CharField(max_length=100, blank=True)
    address = models.CharField(max_length=300, blank=True)
    note = models.TextField(blank=True)
    tags = models.ManyToManyField(Tag, blank=True, related_name="places")
    rating = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text="1-5, or empty for a place I have not rated yet.",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.WISHLIST,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_visited_at = models.DateTimeField(null=True, blank=True)

    #: The columns :meth:`save` runs through :func:`normalize_text`. All of
    #: the model's free text, not just ``neighborhood``: the mismatch issue
    #: #23 describes is a property of the *type*, not of one column, and
    #: ``name`` reads through the same shape of trimmed comparison in
    #: ``places.lookup`` (``name__iexact`` against a Python-stripped string)
    #: that ``neighborhood`` does in ``todo`` and ``surprise``. Every
    #: supported write path already stripped all four in Python -- ``add``
    #: explicitly, ``visit`` for ``note``, the admin through
    #: ``forms.CharField.strip`` -- so this changes nothing a user can reach
    #: and closes the direct-ORM hole for all of them at once.
    NORMALIZED_TEXT_FIELDS = ("name", "neighborhood", "address", "note")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        if self.neighborhood:
            return f"{self.name} ({self.neighborhood})"
        return self.name

    def _normalize_text_fields(self):
        """Rewrite every free-text attribute to its canonical stored form.

        In place, on ``self``, so the instance a caller keeps holding agrees
        with the row that was written.
        """
        for field_name in self.NORMALIZED_TEXT_FIELDS:
            setattr(self, field_name, normalize_text(getattr(self, field_name)))

    def clean(self):
        """Normalize before ``full_clean`` validates, as ``Tag.clean`` does.

        So the admin -- which calls ``full_clean`` on the instance its form
        built -- validates the value that will actually be stored, rather than
        one the save is about to change underneath it.
        """
        super().clean()
        self._normalize_text_fields()

    def save(self, *args, **kwargs):
        """Normalize on every write, whatever the caller.

        ``create()``, ``save()`` and the admin all land here, which is what
        makes the guarantee one rule instead of a convention each call site
        has to remember. ``update_fields`` still decides which columns are
        written; normalizing all of them costs nothing and keeps the
        in-memory instance consistent either way.

        The usual escape hatches remain, exactly as they do for ``Tag``:
        ``QuerySet.update()``, ``bulk_create()`` and raw SQL do not call
        ``save()``. Nothing in this project uses them to write free text.
        """
        self._normalize_text_fields()
        return super().save(*args, **kwargs)
