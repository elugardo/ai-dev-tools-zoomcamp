"""The journal's only web surface: the stock Django admin.

Two ``ModelAdmin`` classes and two display methods, per ``_docs/plan.md``. No
custom views, templates, URLs or admin site -- ``AGENTS.md`` reserves the web
side for what ``django.contrib.admin`` gives us for free.

Nothing here lowercases a tag name by hand: ``Tag`` normalizes in ``clean()``
and ``save()`` (issue #2), so the admin form's uniqueness check already runs
against the canonical name and a typed ``Coffee`` collides with a stored
``coffee`` as a form error rather than an ``IntegrityError``.
"""

from django.contrib import admin

from .models import Place, Tag


@admin.register(Place)
class PlaceAdmin(admin.ModelAdmin):
    """The place changelist: the journal as a table."""

    #: ``tags`` cannot appear here -- it is a many-to-many and Django rejects
    #: it with ``admin.E109``. ``tag_names`` below renders it instead.
    list_display = (
        "name_display",
        "neighborhood",
        "status",
        "rating",
        "last_visited_at",
        "tag_names",
    )
    list_filter = ("status", "tags", "neighborhood")
    #: ``neighborhood`` is free text, so the sidebar filter is exact-match on
    #: whatever spelling was typed. Searching it is what covers the gap.
    search_fields = ("name", "note", "neighborhood")
    #: Newest first, with the primary key as a tie-breaker so two places saved
    #: in the same instant still come back in a stable order. Without an
    #: explicit ordering the paginator raises ``UnorderedObjectListWarning``.
    ordering = ("-created_at", "-pk")
    #: Rendered for a NULL rating or last visit, instead of a blank cell.
    empty_value_display = "—"

    def get_queryset(self, request):
        """Prefetch tags so ``tag_names`` costs one extra query, not one per row."""
        return super().get_queryset(request).prefetch_related("tags")

    @admin.display(description="Name", ordering="name")
    def name_display(self, place):
        """``Place.__str__`` from #2 -- never ``Place object (1)``."""
        return str(place)

    @admin.display(description="Tags")
    def tag_names(self, place):
        """The place's tags in one cell, e.g. ``coffee, wifi``.

        Reads the prefetched ``tags`` -- no filtering, no ordering, no new
        query. Names are already lowercase because ``Tag`` stores them that way.
        """
        return ", ".join(str(tag) for tag in place.tags.all()) or None


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    """Tags are a name and nothing else."""

    list_display = ("name_display",)
    search_fields = ("name",)
    ordering = ("name",)

    @admin.display(description="Name", ordering="name")
    def name_display(self, tag):
        """``Tag.__str__`` from #2 -- never ``Tag object (1)``."""
        return str(tag)
