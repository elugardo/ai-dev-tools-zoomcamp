"""Data model for City Journal: the places I've been or want to go, and their tags.

Two models live here and nothing else. ``Tag`` is a lowercase-only label;
``Place`` is the journal entry itself.

Tags are case-insensitive **end to end**. :func:`normalize_tag_name` is the one
place that decides what a tag name really is, and both the write path
(``Tag.save``) and the read path (``Tag.objects`` lookups) run every name
through it. Later commands should call it rather than lowercasing by hand.
"""

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

#: Lookup suffixes on ``name`` whose right-hand side is a single tag name that
#: should be normalized before it reaches the database.
_SINGLE_VALUE_NAME_LOOKUPS = frozenset(
    {
        "exact",
        "iexact",
        "contains",
        "icontains",
        "startswith",
        "istartswith",
        "endswith",
        "iendswith",
    }
)

#: Lookup suffixes on ``name`` whose right-hand side is an iterable of names.
_MULTI_VALUE_NAME_LOOKUPS = frozenset({"in"})


def normalize_tag_name(name):
    """Return the canonical stored form of a tag name: stripped and lowercased.

    This is the single source of truth for tag identity. Use it anywhere a tag
    name arrives from outside the database -- a ``--tag`` flag, a search query,
    a comparison -- so that ``Coffee``, ``COFFEE`` and ``coffee`` are one tag.
    """
    if name is None:
        return ""
    return str(name).strip().lower()


def _normalize_name_kwargs(kwargs):
    """Return ``kwargs`` with any ``name`` lookup normalized."""
    normalized = {}
    for key, value in kwargs.items():
        field, _, lookup = key.partition("__")
        if field != "name":
            normalized[key] = value
            continue
        lookup = lookup or "exact"
        if lookup in _SINGLE_VALUE_NAME_LOOKUPS:
            normalized[key] = normalize_tag_name(value)
        elif lookup in _MULTI_VALUE_NAME_LOOKUPS:
            normalized[key] = [normalize_tag_name(item) for item in value]
        else:
            normalized[key] = value
    return normalized


class TagQuerySet(models.QuerySet):
    """Queryset that lowercases tag names on the way *in* to a query.

    Normalizing only on save is not enough: ``get_or_create(name="Coffee")``
    would miss the stored ``coffee``, try to insert, and die on the unique
    constraint. ``get``, ``get_or_create`` and ``update_or_create`` all route
    through ``filter``/``exclude``, so normalizing here covers every read path.
    """

    def filter(self, *args, **kwargs):
        return super().filter(*args, **_normalize_name_kwargs(kwargs))

    def exclude(self, *args, **kwargs):
        return super().exclude(*args, **_normalize_name_kwargs(kwargs))


class Tag(models.Model):
    """A lowercase label attached to places: ``coffee``, ``ramen``, ``wifi``."""

    name = models.CharField(max_length=50, unique=True)

    objects = TagQuerySet.as_manager()

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def clean(self):
        """Normalize before ``full_clean`` checks uniqueness.

        Without this, validating ``Coffee`` while ``coffee`` is stored would
        pass validation and then fail on the database constraint.
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
