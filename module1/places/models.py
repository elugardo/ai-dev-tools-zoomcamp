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

    class Meta:
        ordering = ["name"]

    def __str__(self):
        if self.neighborhood:
            return f"{self.name} ({self.neighborhood})"
        return self.name
