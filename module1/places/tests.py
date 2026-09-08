import ast
import inspect
import random
import unittest
import warnings
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
from types import SimpleNamespace
from unittest import mock

from django.apps import apps
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command, get_commands, load_command_class
from django.core.management.base import CommandError
from django.core.paginator import UnorderedObjectListWarning
from django.db import IntegrityError, connection, transaction
from django.db.models import Q
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone as django_timezone

from places import search
from places.models import Place, Tag, normalize_tag_name

# ---------------------------------------------------------------------------
# Shared AST helpers, used by every source-rule test in this file (issue #21)
# ---------------------------------------------------------------------------
#
# A source rule says something about the *code* of a module -- what it imports,
# what it calls, what it orders by -- and every one of them below is asserted
# over parsed nodes rather than over the module's text.
#
# `inspect.getsource()` returns docstrings and comments as well as code, so a
# text assertion measures the documentation. Three tests here were proved
# defeatable that way: `.order_by("pk")` deleted while the docstring still
# quoted it, `todo.py` pasting a local copy of `describe_place` while its
# docstring still credited `places.lookup`, and `add.py` hand-rolling
# normalization with `.casefold()`. The mirror image was proved too -- one
# purely documentary sentence added to `search.py` failed a passing test with
# no code change.
#
# An assertion over the AST can be neither satisfied nor broken by prose:
# comments never reach the tree at all, and the docstrings that do are excluded
# where it matters (see `_string_constants`). Reach for these helpers rather
# than for `getsource()` when adding a source rule.


def _module_tree(module):
    """``module``'s source, parsed. The basis of every helper below."""
    return ast.parse(inspect.getsource(module))


def _imported_modules(module):
    """Every module name ``module`` imports, from its parsed source.

    Imports rather than a grep over the text, because the text includes
    docstrings: a module that *names* the ranker to explain why it must not
    use it is exactly right, and a substring search would call that a
    violation.
    """
    imported = set()
    for node in ast.walk(_module_tree(module)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    return imported


def _imported_names(module, package):
    """The names ``module`` imports *from* ``package``.

    ``_imported_names(todo, "places.lookup")`` is ``{"describe_place"}``, and
    stays empty however warmly the docstring credits the shared module.
    """
    names = set()
    for node in ast.walk(_module_tree(module)):
        if isinstance(node, ast.ImportFrom) and (node.module or "") == package:
            names.update(alias.asname or alias.name for alias in node.names)
    return names


def _defined_functions(module):
    """Every function ``module`` defines itself, at any nesting depth.

    The other half of "imports it rather than copying it": a pasted copy of a
    shared helper shows up here under its own name.
    """
    return {
        node.name
        for node in ast.walk(_module_tree(module))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _called_names(module):
    """Every function name ``module`` calls -- ``foo()`` and ``bar.foo()``."""
    called = set()
    for node in ast.walk(_module_tree(module)):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                called.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                called.add(node.func.attr)
    return called


def _dotted_calls(module):
    """Every ``name.attribute()`` call in ``module``, as ``"name.attribute"``."""
    calls = set()
    for node in ast.walk(_module_tree(module)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
        ):
            calls.add(f"{node.func.value.id}.{node.func.attr}")
    return calls


def _attribute_chains(module):
    """Every ``name.attribute`` access in ``module``, called or not.

    ``Place.Status.VISITED`` contributes ``"Place.Status"``, which is how a
    test asks whether the choices class is referred to rather than retyped.
    """
    chains = set()
    for node in ast.walk(_module_tree(module)):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            chains.add(f"{node.value.id}.{node.attr}")
    return chains


def _referenced_names(module):
    """Every identifier ``module`` mentions in code: bare names and attributes.

    Broader than ``_called_names`` -- it sees a constant that is read but never
    called, such as a threshold the module has no business knowing about.
    """
    names = set()
    for node in ast.walk(_module_tree(module)):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def _string_arguments(module, method):
    """Every literal string ``module`` passes to a ``.method(...)`` call.

    ``_string_arguments(module, "order_by")`` reports the fields the module
    orders by; ``_string_arguments(module, "prefetch_related")`` what it
    prefetches. Non-literal arguments -- ``Lower("name")`` -- are not strings
    and do not appear.
    """
    arguments = set()
    for node in ast.walk(_module_tree(module)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == method
        ):
            arguments.update(
                argument.value
                for argument in node.args
                if isinstance(argument, ast.Constant)
                and isinstance(argument.value, str)
            )
    return arguments


def _query_lookups(module):
    """Every ORM lookup ``module`` spells out, as its trailing segment.

    ``filter(name__iexact=typed)`` contributes ``"iexact"`` and
    ``filter(tags__name=n)`` contributes ``"name"``, so a test can ask whether
    a command reaches for case-insensitive matching of its own.
    """
    lookups = set()
    for node in ast.walk(_module_tree(module)):
        if isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg and "__" in keyword.arg:
                    lookups.add(keyword.arg.rsplit("__", 1)[1])
    return lookups


def _string_constants(module):
    """Every string literal in ``module``'s code, docstrings excluded.

    Exact values, not substrings: this is what makes ``"visited"`` typed as a
    status distinguishable from the word "visited" inside a sentence the
    command prints. Comments never reach the tree at all.
    """
    tree = _module_tree(module)
    docstrings = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        first = node.body[0] if node.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            docstrings.add(id(first.value))

    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    }


class ProjectSkeletonTests(unittest.TestCase):
    """Smoke tests proving the places app is wired into the project."""

    def test_places_app_is_installed(self):
        self.assertTrue(apps.is_installed("places"))

    def test_the_places_app_declares_exactly_two_models(self):
        """Place and Tag and nothing else -- a third model added later is a
        design change, not a detail, so it should break a test."""
        self.assertEqual(
            sorted(m.__name__ for m in apps.get_app_config("places").get_models()),
            ["Place", "Tag"],
        )


class ProjectChecksTests(SimpleTestCase):
    """Django's own checks, run as tests so drift fails the suite.

    No fixtures and no writes, so ``SimpleTestCase`` rather than ``TestCase``.
    ``databases`` is declared only because ``makemigrations`` reads the
    ``django_migrations`` table for its consistency check; nothing here writes.
    """

    databases = {"default"}

    def test_no_model_changes_are_missing_a_migration(self):
        """``makemigrations --check`` exits non-zero when models and migrations
        have drifted apart -- the likeliest regression as later issues edit the
        models."""
        try:
            call_command(
                "makemigrations", "places", check=True, dry_run=True, verbosity=0
            )
        except SystemExit:
            self.fail(
                "Model changes are not reflected in places/migrations -- "
                "run: uv run python manage.py makemigrations places"
            )

    def test_the_project_passes_the_system_check_framework(self):
        call_command("check", verbosity=0)


class NormalizeTagNameTests(unittest.TestCase):
    """The normalizer is pure, so it needs no database."""

    def test_lowercases(self):
        self.assertEqual(normalize_tag_name("Coffee"), "coffee")
        self.assertEqual(normalize_tag_name("COFFEE"), "coffee")

    def test_leaves_an_already_normal_name_alone(self):
        self.assertEqual(normalize_tag_name("coffee"), "coffee")

    def test_strips_surrounding_whitespace(self):
        self.assertEqual(normalize_tag_name("  Coffee \n"), "coffee")

    def test_none_becomes_empty_string(self):
        self.assertEqual(normalize_tag_name(None), "")


class TagModelTests(TestCase):
    def test_name_is_lowercased_by_objects_create(self):
        tag = Tag.objects.create(name="Coffee")
        self.assertEqual(tag.name, "coffee")
        self.assertEqual(Tag.objects.get(pk=tag.pk).name, "coffee")

    def test_name_is_lowercased_by_plain_save(self):
        tag = Tag(name="RAMEN")
        tag.save()
        tag.refresh_from_db()
        self.assertEqual(tag.name, "ramen")

    def test_name_is_lowercased_by_get_or_create(self):
        tag, created = Tag.objects.get_or_create(name="Wifi")
        self.assertTrue(created)
        self.assertEqual(tag.name, "wifi")
        self.assertEqual(Tag.objects.get(pk=tag.pk).name, "wifi")

    def test_mixed_case_get_or_create_returns_the_existing_row(self):
        existing = Tag.objects.create(name="coffee")

        tag, created = Tag.objects.get_or_create(name="Coffee")

        self.assertFalse(created)
        self.assertEqual(tag.pk, existing.pk)
        self.assertEqual(Tag.objects.count(), 1)

    def test_mixed_case_get_finds_the_existing_row(self):
        existing = Tag.objects.create(name="coffee")
        self.assertEqual(Tag.objects.get(name="COFFEE").pk, existing.pk)

    def test_mixed_case_filter_finds_the_existing_row(self):
        Tag.objects.create(name="coffee")
        self.assertEqual(Tag.objects.filter(name="Coffee").count(), 1)
        self.assertEqual(Tag.objects.filter(name__in=["Coffee", "Ramen"]).count(), 1)

    def test_mixed_case_exclude_excludes_the_existing_row(self):
        Tag.objects.create(name="coffee")
        Tag.objects.create(name="ramen")
        self.assertEqual(
            list(Tag.objects.exclude(name="Coffee").values_list("name", flat=True)),
            ["ramen"],
        )

    def test_a_q_object_lookup_finds_the_existing_row(self):
        """Q objects bypass any manager-level kwarg rewriting -- the column
        collation is what has to carry case-insensitivity here."""
        existing = Tag.objects.create(name="coffee")
        self.assertEqual(Tag.objects.filter(Q(name="Coffee")).count(), 1)
        self.assertEqual(Tag.objects.get(Q(name="COFFEE")).pk, existing.pk)

    def test_surrounding_whitespace_is_stripped_on_save(self):
        """The half of normalization the NOCASE collation does *not* cover, so
        it has to be pinned on the model and not only on the pure function."""
        tag = Tag.objects.create(name="   Coffee \n")
        self.assertEqual(tag.name, "coffee")
        self.assertEqual(Tag.objects.get(pk=tag.pk).name, "coffee")

    def test_a_genuine_duplicate_still_violates_the_unique_constraint(self):
        Tag.objects.create(name="coffee")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Tag.objects.create(name="coffee")

    def test_a_mixed_case_duplicate_is_rejected_when_save_is_bypassed(self):
        """``bulk_create`` skips ``save()``, so no Python-side normalization
        runs -- what rejects this row is the case-insensitive UNIQUE index the
        ``NOCASE`` collation builds. Remove ``db_collation`` from ``Tag.name``
        and this is the test that fails."""
        Tag.objects.create(name="coffee")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Tag.objects.bulk_create([Tag(name="COFFEE")])

    def test_full_clean_rejects_a_duplicate_that_differs_only_in_case(self):
        Tag.objects.create(name="coffee")
        with self.assertRaises(ValidationError):
            Tag(name="Coffee").full_clean()

    def test_str_is_the_name(self):
        self.assertEqual(str(Tag.objects.create(name="Coffee")), "coffee")


class PlaceModelTests(TestCase):
    def test_a_place_needs_only_a_name(self):
        place = Place(name="Bar Alto")
        place.full_clean()
        place.save()
        self.assertIsNotNone(place.pk)

    def test_name_is_required(self):
        with self.assertRaises(ValidationError):
            Place(name="").full_clean()

    def test_optional_text_fields_default_to_empty_strings(self):
        place = Place.objects.create(name="Bar Alto")
        place.refresh_from_db()
        self.assertEqual(place.neighborhood, "")
        self.assertEqual(place.address, "")
        self.assertEqual(place.note, "")

    def test_rating_is_none_when_not_given(self):
        place = Place.objects.create(name="Bar Alto")
        place.refresh_from_db()
        self.assertIsNone(place.rating)

    def test_rating_below_one_is_rejected(self):
        with self.assertRaises(ValidationError):
            Place(name="Bar Alto", rating=0).full_clean()

    def test_rating_above_five_is_rejected(self):
        with self.assertRaises(ValidationError):
            Place(name="Bar Alto", rating=6).full_clean()

    def test_ratings_one_through_five_are_accepted(self):
        for rating in (1, 2, 3, 4, 5):
            with self.subTest(rating=rating):
                Place(name="Bar Alto", rating=rating).full_clean()

    def test_status_defaults_to_wishlist(self):
        place = Place.objects.create(name="Bar Alto")
        place.refresh_from_db()
        self.assertEqual(place.status, Place.Status.WISHLIST)
        self.assertEqual(place.status, "wishlist")

    def test_an_unknown_status_is_rejected(self):
        with self.assertRaises(ValidationError):
            Place(name="Bar Alto", status="maybe").full_clean()

    def test_visited_status_is_accepted(self):
        Place(name="Bar Alto", status=Place.Status.VISITED).full_clean()

    def test_created_at_is_stamped_once_and_does_not_change_on_resave(self):
        place = Place.objects.create(name="Bar Alto")
        first = place.created_at
        self.assertIsNotNone(first)

        place.note = "good wifi"
        place.save()
        place.refresh_from_db()

        self.assertEqual(place.created_at, first)

    def test_last_visited_at_is_none_on_a_new_wishlist_place(self):
        place = Place.objects.create(name="Bar Alto")
        place.refresh_from_db()
        self.assertIsNone(place.last_visited_at)

    def test_saving_does_not_auto_stamp_last_visited_at(self):
        """Stamping the visit timestamp belongs to the visit command, not save."""
        place = Place.objects.create(name="Bar Alto")

        place.status = Place.Status.VISITED
        place.save()
        place.refresh_from_db()

        self.assertEqual(place.status, Place.Status.VISITED)
        self.assertIsNone(place.last_visited_at)

    def test_a_place_can_have_no_tags(self):
        place = Place.objects.create(name="Bar Alto")
        self.assertEqual(place.tags.count(), 0)

    def test_one_tag_can_be_shared_by_two_places(self):
        tag = Tag.objects.create(name="coffee")
        first = Place.objects.create(name="Bar Alto")
        second = Place.objects.create(name="Cafe Sur")

        first.tags.add(tag)
        second.tags.add(tag)

        self.assertEqual(tag.places.count(), 2)
        self.assertEqual(Tag.objects.count(), 1)

    def test_filtering_places_by_tag_name_is_case_insensitive(self):
        """How issue #6's --tag filter and issue #7's todo --tag will be
        written. Place.objects is a plain manager, so nothing rewrites the
        lookup -- it has to work at the database level."""
        tag = Tag.objects.create(name="coffee")
        place = Place.objects.create(name="Bar Alto")
        place.tags.add(tag)

        for query in ("coffee", "Coffee", "COFFEE", "CoFfEe"):
            with self.subTest(query=query):
                self.assertEqual(
                    list(Place.objects.filter(tags__name=query)), [place]
                )

    def test_traversing_from_a_place_to_its_tags_is_case_insensitive(self):
        tag = Tag.objects.create(name="coffee")
        place = Place.objects.create(name="Bar Alto")
        place.tags.add(tag)

        self.assertEqual(place.tags.filter(name="Coffee").count(), 1)
        self.assertEqual(place.tags.get(Q(name="COFFEE")).pk, tag.pk)

    def test_str_starts_with_the_name(self):
        place = Place.objects.create(name="Bar Alto", neighborhood="Chamberi")
        self.assertTrue(str(place).startswith("Bar Alto"))
        self.assertIn("Chamberi", str(place))

    def test_str_of_a_place_without_a_neighborhood_is_just_the_name(self):
        self.assertEqual(str(Place.objects.create(name="Bar Alto")), "Bar Alto")


class AdminTestCase(TestCase):
    """Shared plumbing for the admin tests.

    Every one of these goes through Django's test client against real admin
    URLs, so it needs a logged-in superuser -- created here, in the test
    database, never in ``db.sqlite3``. No server, no browser, no network.
    URLs are resolved by name so the tests survive the admin being mounted
    somewhere other than ``/admin/``.
    """

    @classmethod
    def setUpTestData(cls):
        cls.superuser = get_user_model().objects.create_superuser(
            username="journalkeeper",
            email="journalkeeper@example.com",
            password="not-a-real-password",
        )
        cls.place_changelist_url = reverse("admin:places_place_changelist")
        cls.place_add_url = reverse("admin:places_place_add")
        cls.tag_changelist_url = reverse("admin:places_tag_changelist")
        cls.tag_add_url = reverse("admin:places_tag_add")

    def setUp(self):
        self.client.force_login(self.superuser)

    def place_change_url(self, place):
        return reverse("admin:places_place_change", args=[place.pk])

    def tag_change_url(self, tag):
        return reverse("admin:places_tag_change", args=[tag.pk])

    def place_form_data(self, **overrides):
        """What a browser submits from the place add/change form.

        Every field the form renders is present, empty unless overridden --
        which is what a browser posts. ``status`` carries the select's
        preselected default rather than being absent, and ``last_visited_at``
        is split into its date and time inputs by the admin's widget.
        """
        data = {
            "name": "",
            "neighborhood": "",
            "address": "",
            "note": "",
            "rating": "",
            "status": Place.Status.WISHLIST,
            "last_visited_at_0": "",
            "last_visited_at_1": "",
            "tags": [],
        }
        data.update(overrides)
        return data

    def result_names(self, response):
        """The places the changelist actually listed, in order.

        Read off the ``ChangeList`` rather than parsed out of the HTML -- the
        admin's template is not ours to depend on.
        """
        return [str(obj) for obj in response.context["cl"].result_list]

    def column_index(self, response, column):
        """The ``?o=`` index of a changelist column, as the admin numbers them.

        Taken from the rendered ``ChangeList``, which prepends the action
        checkbox to ``list_display``, so the number is not guessed here.
        """
        return list(response.context["cl"].list_display).index(column)


class AdminRegistrationTests(AdminTestCase):
    """Both models are registered and reachable, including on an empty database."""

    def test_both_models_are_registered(self):
        self.assertIn(Place, admin.site._registry)
        self.assertIn(Tag, admin.site._registry)

    def test_the_admin_index_lists_places_and_tags(self):
        response = self.client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Places")
        self.assertContains(response, "Tags")
        self.assertContains(response, self.place_changelist_url)
        self.assertContains(response, self.tag_changelist_url)

    def test_both_changelists_load_on_an_empty_database(self):
        self.assertEqual(Place.objects.count(), 0)
        self.assertEqual(Tag.objects.count(), 0)

        places = self.client.get(self.place_changelist_url)
        tags = self.client.get(self.tag_changelist_url)

        self.assertEqual(places.status_code, 200)
        self.assertEqual(tags.status_code, 200)
        self.assertContains(places, "0 places")
        self.assertContains(tags, "0 tags")

    def test_a_logged_out_request_is_redirected_to_the_login_page(self):
        self.client.logout()

        response = self.client.get(self.place_changelist_url)

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("admin:login"), response["Location"])

    def test_tags_is_not_a_raw_list_display_entry(self):
        """A many-to-many in ``list_display`` is ``admin.E109``; the display
        method is what renders it. ``ProjectChecksTests`` runs ``check`` and
        would catch the regression too -- this one names it."""
        self.assertNotIn("tags", admin.site._registry[Place].list_display)


class AdminChangelistColumnTests(AdminTestCase):
    """The place changelist's columns, including the nullable ones."""

    def test_a_fully_filled_place_shows_all_five_values_on_one_row(self):
        Place.objects.create(
            name="Bar Alto",
            neighborhood="Chamberi",
            status=Place.Status.VISITED,
            rating=4,
            last_visited_at=datetime(2024, 3, 9, 18, 30, tzinfo=timezone.utc),
        )

        response = self.client.get(self.place_changelist_url)

        self.assertEqual(response.status_code, 200)
        for value in ("Bar Alto", "Chamberi", "Visited", "4", "March 9, 2024"):
            with self.subTest(value=value):
                self.assertContains(response, value)

    def test_a_place_with_no_rating_and_no_visit_renders_without_None(self):
        Place.objects.create(name="Bar Alto")

        response = self.client.get(self.place_changelist_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bar Alto")
        self.assertNotContains(response, "None")
        self.assertContains(response, admin.site._registry[Place].empty_value_display)

    def test_sorting_by_rating_works_in_both_directions(self):
        rated = [
            Place.objects.create(name="Bar Alto", rating=5),
            Place.objects.create(name="Cafe Sur", rating=2),
        ]
        unrated = [
            Place.objects.create(name="Taberna Uno"),
            Place.objects.create(name="Wine Bar Dos"),
        ]
        everything = [str(p) for p in rated + unrated]
        index = self.column_index(self.client.get(self.place_changelist_url), "rating")
        rated_orders = []

        for order in (str(index), "-%d" % index):
            with self.subTest(order=order):
                response = self.client.get(self.place_changelist_url, {"o": order})

                self.assertEqual(response.status_code, 200)
                self.assertCountEqual(self.result_names(response), everything)
                results = response.context["cl"].result_list
                positions = [
                    i for i, place in enumerate(results) if place.rating is None
                ]
                self.assertIn(
                    positions,
                    ([0, 1], [2, 3]),
                    "unrated places should sort to one end, not scatter",
                )
                rated_orders.append([p.name for p in results if p.rating is not None])

        ascending, descending = rated_orders
        self.assertEqual(
            ascending,
            list(reversed(descending)),
            "the two directions did not reverse -- the sort was not applied",
        )
        self.assertNotEqual(ascending, descending)

    def test_sorting_by_last_visited_works_in_both_directions(self):
        visited = Place.objects.create(
            name="Bar Alto",
            status=Place.Status.VISITED,
            last_visited_at=datetime(2024, 3, 9, 18, 30, tzinfo=timezone.utc),
        )
        earlier = Place.objects.create(
            name="Cafe Sur",
            status=Place.Status.VISITED,
            last_visited_at=datetime(2023, 1, 2, 12, 0, tzinfo=timezone.utc),
        )
        never = Place.objects.create(name="Taberna Uno")
        everything = [str(visited), str(earlier), str(never)]
        index = self.column_index(
            self.client.get(self.place_changelist_url), "last_visited_at"
        )
        visited_orders = []

        for order in (str(index), "-%d" % index):
            with self.subTest(order=order):
                response = self.client.get(self.place_changelist_url, {"o": order})

                self.assertEqual(response.status_code, 200)
                self.assertCountEqual(self.result_names(response), everything)
                results = response.context["cl"].result_list
                visited_orders.append(
                    [p.name for p in results if p.last_visited_at is not None]
                )

        ascending, descending = visited_orders
        self.assertEqual(
            ascending,
            list(reversed(descending)),
            "the two directions did not reverse -- the sort was not applied",
        )
        self.assertNotEqual(ascending, descending)

    def test_the_default_ordering_is_deterministic_and_newest_first(self):
        Place.objects.bulk_create([Place(name="Place %03d" % i) for i in range(105)])
        newest = Place.objects.order_by("-created_at", "-pk").first()

        first_load = self.client.get(self.place_changelist_url)
        second_load = self.client.get(self.place_changelist_url)

        self.assertEqual(first_load.status_code, 200)
        page = [p.pk for p in first_load.context["cl"].result_list]
        self.assertEqual(len(page), 100, "expected a full first page")
        self.assertEqual(page, [p.pk for p in second_load.context["cl"].result_list])
        self.assertEqual(page[0], newest.pk)

    def test_loading_the_changelist_raises_no_unordered_object_list_warning(self):
        Place.objects.create(name="Bar Alto")

        with warnings.catch_warnings():
            warnings.simplefilter("error", UnorderedObjectListWarning)
            response = self.client.get(self.place_changelist_url)

        self.assertEqual(response.status_code, 200)

    def test_places_and_tags_are_labelled_by_their_str(self):
        place = Place.objects.create(name="Bar Alto", neighborhood="Chamberi")
        tag = Tag.objects.create(name="Coffee")

        places = self.client.get(self.place_changelist_url)
        tags = self.client.get(self.tag_changelist_url)

        self.assertContains(places, str(place))
        self.assertNotContains(places, "Place object")
        self.assertContains(tags, str(tag))
        self.assertNotContains(tags, "Tag object")


class AdminTagsColumnTests(AdminTestCase):
    """The tags cell on the place changelist."""

    def test_the_tags_column_joins_the_names_lowercase(self):
        place = Place.objects.create(name="Bar Alto")
        place.tags.add(
            Tag.objects.create(name="Coffee"), Tag.objects.create(name="wifi")
        )

        response = self.client.get(self.place_changelist_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "coffee, wifi")
        self.assertNotContains(response, "Coffee")

    def test_a_place_with_no_tags_renders_a_placeholder_cell(self):
        Place.objects.create(name="Bar Alto")

        response = self.client.get(self.place_changelist_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bar Alto")
        self.assertNotContains(response, "None")

    def test_the_tags_column_does_not_cost_a_query_per_row(self):
        coffee = Tag.objects.create(name="coffee")
        wifi = Tag.objects.create(name="wifi")
        for i in range(25):
            Place.objects.create(name="Place %02d" % i).tags.add(coffee, wifi)

        with CaptureQueriesContext(connection) as twenty_five_rows:
            response = self.client.get(self.place_changelist_url)
        self.assertEqual(len(response.context["cl"].result_list), 25)

        for i in range(25, 30):
            Place.objects.create(name="Place %02d" % i).tags.add(coffee, wifi)

        with CaptureQueriesContext(connection) as thirty_rows:
            response = self.client.get(self.place_changelist_url)
        self.assertEqual(len(response.context["cl"].result_list), 30)

        self.assertEqual(
            len(thirty_rows),
            len(twenty_five_rows),
            "the query count grew with the row count -- tags are not prefetched",
        )
        self.assertLess(len(twenty_five_rows), 25)


class AdminFilterAndSearchTests(AdminTestCase):
    """The sidebar filters and the search box."""

    def setUp(self):
        super().setUp()
        self.coffee = Tag.objects.create(name="coffee")
        self.ramen = Tag.objects.create(name="ramen")

        self.bar_alto = Place.objects.create(
            name="Bar Alto",
            neighborhood="Mission",
            note="great wifi, terrible espresso",
            status=Place.Status.VISITED,
            rating=4,
        )
        self.bar_alto.tags.add(self.coffee)

        self.cafe_sur = Place.objects.create(
            name="Cafe Sur",
            neighborhood="mission",
            note="no wifi at all",
            status=Place.Status.WISHLIST,
        )
        self.cafe_sur.tags.add(self.ramen)

        self.taberna = Place.objects.create(
            name="Taberna Uno",
            neighborhood="Chamberi",
            note="quiet, good vermouth",
            status=Place.Status.WISHLIST,
        )

    def test_the_status_filter_narrows_to_wishlist(self):
        response = self.client.get(
            self.place_changelist_url, {"status__exact": Place.Status.WISHLIST}
        )

        self.assertEqual(response.status_code, 200)
        self.assertCountEqual(
            self.result_names(response), [str(self.cafe_sur), str(self.taberna)]
        )

    def test_the_tag_filter_narrows_to_places_carrying_it(self):
        response = self.client.get(
            self.place_changelist_url, {"tags__id__exact": self.coffee.pk}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.result_names(response), [str(self.bar_alto)])

    def test_the_neighborhood_filter_is_exact_on_the_stored_string(self):
        """Free text means one sidebar entry per spelling: filtering on
        Mission does not bring back the place stored as mission. That is
        accepted behavior here, not a bug -- the search box covers it."""
        response = self.client.get(
            self.place_changelist_url, {"neighborhood": "Mission"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.result_names(response), [str(self.bar_alto)])

    def test_the_sidebar_offers_a_filter_for_status_neighborhood_and_tags(self):
        response = self.client.get(self.place_changelist_url)

        self.assertEqual(
            [spec.title for spec in response.context["cl"].filter_specs],
            ["status", "tags", "neighborhood"],
        )

    def test_searching_a_neighborhood_spans_the_spellings_the_filter_splits(self):
        response = self.client.get(self.place_changelist_url, {"q": "mission"})

        self.assertEqual(response.status_code, 200)
        self.assertCountEqual(
            self.result_names(response), [str(self.bar_alto), str(self.cafe_sur)]
        )

    def test_searching_a_fragment_of_a_note_finds_the_place(self):
        response = self.client.get(self.place_changelist_url, {"q": "wifi"})

        self.assertEqual(response.status_code, 200)
        self.assertCountEqual(
            self.result_names(response), [str(self.bar_alto), str(self.cafe_sur)]
        )
        self.assertEqual(
            self.result_names(self.client.get(self.place_changelist_url, {"q": "espresso"})),
            [str(self.bar_alto)],
        )

    def test_search_is_case_insensitive(self):
        lower = self.client.get(self.place_changelist_url, {"q": "wifi"})
        upper = self.client.get(self.place_changelist_url, {"q": "WIFI"})

        self.assertCountEqual(
            self.result_names(lower), [str(self.bar_alto), str(self.cafe_sur)]
        )
        self.assertCountEqual(self.result_names(upper), self.result_names(lower))

    def test_an_empty_search_returns_the_full_list(self):
        response = self.client.get(self.place_changelist_url, {"q": ""})

        self.assertEqual(response.status_code, 200)
        self.assertCountEqual(
            self.result_names(response),
            [str(self.bar_alto), str(self.cafe_sur), str(self.taberna)],
        )

    def test_a_search_matching_nothing_renders_an_empty_changelist(self):
        """No never-return-nothing fallback here -- that is the find command's
        behavior, not the admin's."""
        response = self.client.get(self.place_changelist_url, {"q": "zzzznotaplace"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.result_names(response), [])
        self.assertContains(response, "0 places")

    def test_a_filter_and_a_search_combine(self):
        response = self.client.get(
            self.place_changelist_url,
            {"status__exact": Place.Status.VISITED, "q": "wifi"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.result_names(response), [str(self.bar_alto)])


class PlaceAdminFormTests(AdminTestCase):
    """Adding and editing a place through the admin forms."""

    def test_the_add_form_defaults_status_to_wishlist(self):
        """So that typing only a name really does produce a wishlist place:
        the select arrives preselected and the browser posts it back."""
        response = self.client.get(self.place_add_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["adminform"].form["status"].value(),
            Place.Status.WISHLIST,
        )

    def test_a_place_added_through_the_form_is_visible_to_the_orm(self):
        response = self.client.post(
            self.place_add_url, self.place_form_data(name="Bar Alto")
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.place_changelist_url)
        self.assertEqual(Place.objects.count(), 1)
        place = Place.objects.get(name="Bar Alto")
        self.assertIsNone(place.rating)
        self.assertEqual(place.status, Place.Status.WISHLIST)
        self.assertIsNone(place.last_visited_at)

    def test_a_place_added_through_the_form_shows_up_on_the_changelist(self):
        self.client.post(self.place_add_url, self.place_form_data(name="Bar Alto"))

        response = self.client.get(self.place_changelist_url)

        self.assertEqual(self.result_names(response), ["Bar Alto"])

    def test_editing_a_place_through_the_change_form_persists(self):
        place = Place.objects.create(name="Bar Alto")

        response = self.client.post(
            self.place_change_url(place),
            self.place_form_data(
                name="Bar Alto",
                neighborhood="Chamberi",
                note="great wifi",
                rating="5",
                status=Place.Status.VISITED,
            ),
        )

        self.assertEqual(response.status_code, 302)
        place.refresh_from_db()
        self.assertEqual(place.neighborhood, "Chamberi")
        self.assertEqual(place.note, "great wifi")
        self.assertEqual(place.rating, 5)
        self.assertEqual(place.status, Place.Status.VISITED)

    def test_an_out_of_range_rating_comes_back_as_a_form_error(self):
        for rating in ("0", "6"):
            with self.subTest(rating=rating):
                response = self.client.post(
                    self.place_add_url,
                    self.place_form_data(name="Bar Alto", rating=rating),
                )

                self.assertEqual(response.status_code, 200)
                self.assertIn("rating", response.context["adminform"].form.errors)
                self.assertEqual(Place.objects.count(), 0)

    def test_ratings_one_through_five_save(self):
        for rating in ("1", "5"):
            with self.subTest(rating=rating):
                response = self.client.post(
                    self.place_add_url,
                    self.place_form_data(name="Place %s" % rating, rating=rating),
                )

                self.assertEqual(response.status_code, 302)
                self.assertEqual(
                    Place.objects.get(name="Place %s" % rating).rating, int(rating)
                )

    def test_a_blank_rating_is_stored_as_null(self):
        self.client.post(
            self.place_add_url, self.place_form_data(name="Bar Alto", rating="")
        )

        place = Place.objects.get(name="Bar Alto")
        self.assertIsNone(place.rating)
        self.assertTrue(
            Place.objects.filter(name="Bar Alto", rating__isnull=True).exists()
        )

    def test_tags_selected_on_the_form_are_attached(self):
        coffee = Tag.objects.create(name="coffee")
        wifi = Tag.objects.create(name="wifi")

        self.client.post(
            self.place_add_url,
            self.place_form_data(name="Bar Alto", tags=[coffee.pk, wifi.pk]),
        )

        self.assertEqual(Place.objects.get(name="Bar Alto").tags.count(), 2)

    def test_saving_with_no_tags_selected_attaches_none(self):
        Tag.objects.create(name="coffee")

        self.client.post(self.place_add_url, self.place_form_data(name="Bar Alto"))

        self.assertEqual(Place.objects.get(name="Bar Alto").tags.count(), 0)


class TagAdminFormTests(AdminTestCase):
    """Tags typed freehand into the admin -- the one place a human writes one."""

    def test_a_mixed_case_tag_lands_lowercase(self):
        response = self.client.post(self.tag_add_url, {"name": "Coffee"})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Tag.objects.count(), 1)
        self.assertEqual(Tag.objects.get().name, "coffee")
        self.assertContains(self.client.get(self.tag_changelist_url), "coffee")

    def test_a_tag_name_is_stripped_as_well_as_lowercased(self):
        self.client.post(self.tag_add_url, {"name": "  Coffee  "})

        self.assertEqual(Tag.objects.get().name, normalize_tag_name("Coffee"))

    def test_a_duplicate_differing_only_in_case_is_a_form_error_not_a_500(self):
        """The sharp edge of this issue: the form's uniqueness check has to run
        against the normalized name, or SQLite sees no conflict at validation
        time and the unique index raises an IntegrityError on save."""
        Tag.objects.create(name="coffee")

        response = self.client.post(self.tag_add_url, {"name": "Coffee"})

        self.assertEqual(response.status_code, 200)
        self.assertIn("name", response.context["adminform"].form.errors)
        self.assertContains(response, "already exists")
        self.assertEqual(Tag.objects.count(), 1)

    def test_renaming_a_tag_onto_an_existing_one_is_a_form_error_not_a_500(self):
        Tag.objects.create(name="coffee")
        ramen = Tag.objects.create(name="ramen")

        response = self.client.post(self.tag_change_url(ramen), {"name": "Coffee"})

        self.assertEqual(response.status_code, 200)
        self.assertIn("name", response.context["adminform"].form.errors)
        self.assertEqual(Tag.objects.count(), 2)
        ramen.refresh_from_db()
        self.assertEqual(ramen.name, "ramen")

    def test_renaming_a_tag_to_a_free_name_still_works(self):
        ramen = Tag.objects.create(name="ramen")

        response = self.client.post(self.tag_change_url(ramen), {"name": "Noodles"})

        self.assertEqual(response.status_code, 302)
        ramen.refresh_from_db()
        self.assertEqual(ramen.name, "noodles")

    def test_deleting_a_tag_leaves_its_places_intact(self):
        coffee = Tag.objects.create(name="coffee")
        place = Place.objects.create(name="Bar Alto")
        place.tags.add(coffee)

        response = self.client.post(
            reverse("admin:places_tag_delete", args=[coffee.pk]), {"post": "yes"}
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Tag.objects.count(), 0)
        self.assertEqual(Place.objects.count(), 1)
        place.refresh_from_db()
        self.assertEqual(place.name, "Bar Alto")
        self.assertEqual(place.tags.count(), 0)

# ---------------------------------------------------------------------------
# The `add` command (issue #4)
# ---------------------------------------------------------------------------


def _add_module():
    """The ``add`` command module, imported the way Django finds it."""
    from places.management.commands import add as add_module

    return add_module


class AddCommandDiscoveryTests(SimpleTestCase):
    """The command is discoverable and takes the documented flags.

    No database work here, so ``SimpleTestCase``.
    """

    def parser(self):
        return load_command_class("places", "add").create_parser("manage.py", "add")

    def test_add_is_registered_as_a_command_of_the_places_app(self):
        """What ``manage.py help`` lists under ``[places]`` comes from here."""
        self.assertEqual(get_commands().get("add"), "places")

    def test_help_documents_the_positional_name_and_every_flag(self):
        help_text = self.parser().format_help()

        self.assertIn("name", help_text)
        for flag in (
            "--neighborhood",
            "--address",
            "--note",
            "--rating",
            "--status",
            "--tag",
        ):
            with self.subTest(flag=flag):
                self.assertIn(flag, help_text)

    def test_tag_is_a_repeatable_flag_not_a_comma_separated_string(self):
        tag = next(a for a in self.parser()._actions if a.dest == "tags")

        self.assertEqual(tag.option_strings, ["--tag"])
        self.assertEqual(
            self.parser().parse_args(["X", "--tag", "a", "--tag", "b"]).tags,
            ["a", "b"],
        )

    def test_status_offers_exactly_the_models_two_choices(self):
        status = next(a for a in self.parser()._actions if a.dest == "status")

        self.assertEqual(list(status.choices), list(Place.Status.values))

    def test_there_is_no_flag_for_setting_the_visit_date(self):
        """Stamping ``last_visited_at`` belongs to ``visit`` (#7), so ``add``
        must not grow a date flag by accident."""
        help_text = self.parser().format_help()

        self.assertNotIn("last_visited_at", {a.dest for a in self.parser()._actions})
        for option in ("--last-visited", "--visited-on", "--date"):
            with self.subTest(option=option):
                self.assertNotIn(option, help_text)


class AddCommandSourceRuleTests(SimpleTestCase):
    """Two rules issue #4 states about ``add.py`` itself.

    These are deliberately source-level rather than behavioral: both describe a
    *mechanism* the command must reuse instead of reimplementing, and a
    reimplementation would pass every behavioral test right up until the day
    the shared normalizer or the choices class changes. Read off the AST, never
    off the text -- see the helper block at the top of this file.
    """

    def test_add_does_not_hand_roll_tag_normalization(self):
        called = _called_names(_add_module())

        for hand_rolled in ("lower", "casefold", "upper", "title"):
            with self.subTest(call=hand_rolled):
                self.assertNotIn(hand_rolled, called)
        self.assertNotIn("iexact", _query_lookups(_add_module()))
        self.assertIn("normalize_tag_name", called)

    def test_add_refers_to_the_status_choices_class_not_to_string_literals(self):
        constants = _string_constants(_add_module())

        self.assertNotIn(Place.Status.WISHLIST.value, constants)
        self.assertNotIn(Place.Status.VISITED.value, constants)
        self.assertIn("Place.Status", _attribute_chains(_add_module()))


class AddCommandTestCase(TestCase):
    """Shared plumbing: run ``add`` through ``call_command`` and capture both
    streams, per ``_docs/testing-guidelines.md``. Nothing here shells out."""

    def run_add(self, *args):
        out, err = StringIO(), StringIO()
        call_command("add", *args, stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def run_add_from_command_line(self, *args):
        """Drive ``add`` the way ``manage.py`` does, so exit codes are real.

        ``call_command`` on its own downgrades argparse's usage error into a
        ``CommandError``, because ``CommandParser.error`` only calls argparse's
        own ``error`` -- the one that exits 2 -- when the command knows it was
        invoked from the command line. ``manage.py`` sets that flag; setting it
        here is what lets these tests pin the real exit status without shelling
        out to ``manage.py``, which the testing guidelines forbid.
        """
        command = load_command_class("places", "add")
        command._called_from_command_line = True
        out, err = StringIO(), StringIO()
        call_command(command, *args, stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def assert_rejected(self, *args):
        """Run an ``add`` the command itself validates away.

        Returns the ``CommandError`` -- which ``manage.py`` renders as a
        one-line message on stderr with exit status 1 -- after checking that
        neither table grew.
        """
        before = (Place.objects.count(), Tag.objects.count())
        with self.assertRaises(CommandError) as caught:
            self.run_add(*args)
        self.assertEqual((Place.objects.count(), Tag.objects.count()), before)
        return caught.exception


class AddCommandStorageTests(AddCommandTestCase):
    """What lands in the database."""

    def test_a_name_on_its_own_creates_a_place(self):
        self.run_add("Tartine")

        self.assertEqual(Place.objects.count(), 1)
        self.assertEqual(Place.objects.get().name, "Tartine")

    def test_every_text_flag_is_stored_verbatim(self):
        self.run_add(
            "Blue Bottle",
            "--neighborhood",
            "Mission",
            "--address",
            "66 Mint St",
            "--note",
            "good wifi",
        )

        place = Place.objects.get()
        self.assertEqual(place.name, "Blue Bottle")
        self.assertEqual(place.neighborhood, "Mission")
        self.assertEqual(place.address, "66 Mint St")
        self.assertEqual(place.note, "good wifi")

    def test_the_place_name_keeps_its_capitalization(self):
        """Only tags get a canonical case -- never the place name."""
        self.run_add("Blue Bottle")

        self.assertEqual(Place.objects.get().name, "Blue Bottle")

    def test_surrounding_whitespace_is_stripped_from_every_text_value(self):
        self.run_add(
            "  Tartine  ",
            "--neighborhood",
            "  Mission ",
            "--address",
            " 600 Guerrero St  ",
            "--note",
            "  morning bun  ",
            "--tag",
            "  Pastry  ",
        )

        place = Place.objects.get()
        self.assertEqual(place.name, "Tartine")
        self.assertEqual(place.neighborhood, "Mission")
        self.assertEqual(place.address, "600 Guerrero St")
        self.assertEqual(place.note, "morning bun")
        self.assertEqual([t.name for t in place.tags.all()], ["pastry"])

    def test_omitted_text_flags_are_empty_strings_not_none(self):
        self.run_add("Tartine")

        place = Place.objects.get()
        self.assertEqual(place.neighborhood, "")
        self.assertEqual(place.address, "")
        self.assertEqual(place.note, "")

    def test_add_creates_exactly_one_row_and_leaves_other_places_alone(self):
        existing = Place.objects.create(name="Bar Alto", neighborhood="SoMa", rating=3)

        self.run_add("Tartine")

        self.assertEqual(Place.objects.count(), 2)
        existing.refresh_from_db()
        self.assertEqual(existing.neighborhood, "SoMa")
        self.assertEqual(existing.rating, 3)


class AddCommandTagTests(AddCommandTestCase):
    """Tags: created on demand, reused whatever case they were typed in."""

    def test_tag_is_repeatable_and_attaches_every_value(self):
        self.run_add("Blue Bottle", "--tag", "coffee", "--tag", "wifi")

        place = Place.objects.get()
        self.assertEqual(sorted(t.name for t in place.tags.all()), ["coffee", "wifi"])

    def test_an_unknown_tag_is_created_in_canonical_form(self):
        before = Tag.objects.count()

        self.run_add("Tartine", "--tag", "Pastry")

        self.assertEqual(Tag.objects.count(), before + 1)
        self.assertEqual(Tag.objects.get().name, "pastry")

    def test_an_existing_tag_is_reused_whatever_case_is_typed(self):
        for typed in ("Coffee", "COFFEE", "CoFfEe", "  coffee "):
            with self.subTest(typed=typed):
                Tag.objects.get_or_create(name="coffee")
                before = Tag.objects.count()

                self.run_add("Blue Bottle", "--tag", typed)

                self.assertEqual(Tag.objects.count(), before)
                self.assertEqual(Tag.objects.filter(name="coffee").count(), 1)
                self.assertEqual(
                    [t.name for t in Place.objects.latest("pk").tags.all()], ["coffee"]
                )

    def test_the_same_tag_twice_in_one_command_attaches_once(self):
        before = Tag.objects.count()

        self.run_add("Blue Bottle", "--tag", "coffee", "--tag", "Coffee")

        self.assertEqual(Place.objects.get().tags.count(), 1)
        self.assertEqual(Tag.objects.count(), before + 1)

    def test_two_places_added_separately_share_one_tag_row(self):
        self.run_add("Blue Bottle", "--tag", "coffee")
        self.run_add("Sightglass", "--tag", "Coffee")

        self.assertEqual(Tag.objects.count(), 1)
        self.assertEqual(Tag.objects.get().places.count(), 2)

    def test_attaching_a_tag_never_detaches_it_from_another_place(self):
        coffee = Tag.objects.create(name="coffee")
        sightglass = Place.objects.create(name="Sightglass")
        sightglass.tags.add(coffee)

        self.run_add("Blue Bottle", "--tag", "Coffee")

        self.assertEqual(sightglass.tags.count(), 1)
        self.assertEqual(coffee.places.count(), 2)

    def test_a_place_added_with_no_tags_has_none(self):
        self.run_add("Tartine")

        self.assertEqual(Place.objects.get().tags.count(), 0)


class AddCommandStatusTests(AddCommandTestCase):
    """Inference, and what an explicit ``--status`` does to it."""

    def test_no_rating_and_no_status_lands_on_the_wishlist(self):
        self.run_add("Tartine")

        place = Place.objects.get()
        self.assertEqual(place.status, Place.Status.WISHLIST)
        self.assertIsNone(place.rating)

    def test_a_rating_alone_implies_the_place_was_visited(self):
        self.run_add("Blue Bottle", "--rating", "4")

        place = Place.objects.get()
        self.assertEqual(place.status, Place.Status.VISITED)
        self.assertEqual(place.rating, 4)

    def test_an_explicit_status_overrides_the_rating_inference_silently(self):
        out, err = self.run_add(
            "Blue Bottle", "--rating", "4", "--status", Place.Status.WISHLIST.value
        )

        place = Place.objects.get()
        self.assertEqual(place.status, Place.Status.WISHLIST)
        self.assertEqual(place.rating, 4)
        self.assertEqual(err, "")

    def test_an_explicit_visited_status_needs_no_rating(self):
        out, err = self.run_add("Tartine", "--status", Place.Status.VISITED.value)

        place = Place.objects.get()
        self.assertEqual(place.status, Place.Status.VISITED)
        self.assertIsNone(place.rating)
        self.assertEqual(err, "")

    def test_the_boundary_ratings_are_accepted(self):
        for rating in ("1", "5"):
            with self.subTest(rating=rating):
                self.run_add("Place %s" % rating, "--rating", rating)

                place = Place.objects.get(name="Place %s" % rating)
                self.assertEqual(place.rating, int(rating))
                self.assertEqual(place.status, Place.Status.VISITED)


class AddCommandLastVisitedTests(AddCommandTestCase):
    """``add`` never stamps the visit timestamp -- that is #7's job."""

    def test_last_visited_at_is_none_after_any_successful_add(self):
        cases = (
            ("Tartine", ()),
            ("Blue Bottle", ("--rating", "5")),
            ("Sightglass", ("--status", Place.Status.VISITED.value)),
            ("Bar Alto", ("--rating", "5", "--status", Place.Status.VISITED.value)),
        )
        for name, flags in cases:
            with self.subTest(name=name, flags=flags):
                self.run_add(name, *flags)

                self.assertIsNone(Place.objects.get(name=name).last_visited_at)


class AddCommandDuplicateNameTests(AddCommandTestCase):
    """A repeated name is allowed, and warned about on stderr."""

    def test_a_second_place_with_the_same_name_is_created_not_merged(self):
        self.run_add("Starbucks", "--neighborhood", "Mission")
        self.run_add("Starbucks", "--neighborhood", "SoMa")

        self.assertEqual(Place.objects.filter(name="Starbucks").count(), 2)
        self.assertEqual(
            sorted(p.neighborhood for p in Place.objects.all()), ["Mission", "SoMa"]
        )

    def test_the_second_add_warns_on_stderr_and_still_succeeds(self):
        self.run_add("Starbucks")

        out, err = self.run_add("Starbucks")

        self.assertIn("Starbucks", err)
        self.assertIn("already named", err)
        self.assertNotIn("already named", out)
        self.assertIn("Added", out)

    def test_the_duplicate_check_ignores_case_but_never_changes_what_is_stored(self):
        self.run_add("Starbucks")

        out, err = self.run_add("starbucks")

        self.assertIn("Starbucks", err)
        self.assertEqual(
            sorted(p.name for p in Place.objects.all()), ["Starbucks", "starbucks"]
        )

    def test_a_new_name_prints_no_warning(self):
        self.run_add("Starbucks")

        out, err = self.run_add("Tartine")

        self.assertEqual(err, "")

    def test_a_name_that_merely_contains_another_is_not_a_duplicate(self):
        self.run_add("Starbucks")

        out, err = self.run_add("Starbucks Reserve")

        self.assertEqual(err, "")

    def test_a_stored_name_that_merely_contains_the_new_one_is_not_a_duplicate(self):
        """The direction that pins the anchoring, which the test above does not.

        A longer *stored* name containing the new one is what an unanchored
        comparison mistakes for a collision: adding ``Starbucks`` when only
        ``Starbucks Reserve`` exists is a new place, not a repeat. The count in
        the second half proves the Reserve row is excluded rather than merely
        outnumbered.
        """
        self.run_add("Starbucks Reserve")

        out, err = self.run_add("Starbucks")

        self.assertEqual(err, "")

        # With one genuine repeat now stored, the warning must still count only
        # it -- one other place, not two.
        out, err = self.run_add("Starbucks")

        self.assertIn("1 other place", err)
        self.assertNotIn("Reserve", err)

    def test_a_name_full_of_regex_metacharacters_matches_only_itself(self):
        """``+``, ``.`` and ``()`` are literal parts of a place's name.

        The name being *added* is the one that has to be escaped, since it is
        the one the comparison is built from -- so in each pair the plain
        lookalike is stored first and the metacharacter-bearing name second.
        Unescaped, ``A+B Deli`` would read as "one or more As", ``St. Frank``
        as "any character", and ``Cafe (Mission)`` as a group matching the bare
        text -- each one a false collision with the row already stored.
        """
        pairs = (
            ("AB Deli", "A+B Deli"),
            ("StX Frank", "St. Frank"),
            ("Cafe Mission", "Cafe (Mission)"),
        )
        for stored, metacharacters in pairs:
            with self.subTest(stored=stored, adding=metacharacters):
                self.run_add(stored)

                out, err = self.run_add(metacharacters)

                self.assertEqual(err, "")
                self.assertEqual(
                    Place.objects.filter(name=metacharacters).count(), 1
                )

    def test_the_warning_counts_the_other_places_not_this_one(self):
        self.run_add("Starbucks")
        self.run_add("Starbucks")

        out, err = self.run_add("Starbucks")

        self.assertIn("2", err)
        self.assertEqual(Place.objects.count(), 3)


class AddCommandInvalidInputTests(AddCommandTestCase):
    """Nothing is written, and the exit code is non-zero."""

    def test_a_blank_name_is_rejected_with_a_message_about_the_name(self):
        for name in ("", "   "):
            with self.subTest(name=repr(name)):
                error = self.assert_rejected(name)

                self.assertIn("name", str(error))

    def test_a_rating_outside_one_to_five_names_the_allowed_range(self):
        for rating in ("0", "6"):
            with self.subTest(rating=rating):
                error = self.assert_rejected("Blue Bottle", "--rating", rating)

                self.assertIn("1", str(error))
                self.assertIn("5", str(error))

    def test_a_blank_tag_is_rejected(self):
        for tag in ("", "   "):
            with self.subTest(tag=repr(tag)):
                error = self.assert_rejected("Blue Bottle", "--tag", tag)

                self.assertIn("tag", str(error))

    def test_a_rejected_run_leaves_no_orphan_tag_behind(self):
        """The bug this guards: create the tag, then fail on the rating."""
        self.assert_rejected("Blue Bottle", "--tag", "coffee", "--rating", "9")

        self.assertEqual(Tag.objects.count(), 0)
        self.assertEqual(Place.objects.count(), 0)

    def test_a_rejected_run_leaves_existing_rows_untouched(self):
        Tag.objects.create(name="coffee")
        Place.objects.create(name="Sightglass")

        self.assert_rejected("Blue Bottle", "--tag", "wifi", "--rating", "0")

        self.assertEqual(Tag.objects.count(), 1)
        self.assertEqual(Place.objects.count(), 1)

    def test_argparse_rejects_a_non_integer_rating_before_the_command_body_runs(self):
        """``--rating abc`` and ``--rating 4.5`` exit 2 -- argparse's code, not
        ours -- because a rating is a whole number."""
        for rating in ("abc", "4.5"):
            with self.subTest(rating=rating):
                with redirect_stderr(StringIO()):
                    with self.assertRaises(SystemExit) as caught:
                        self.run_add_from_command_line(
                            "Blue Bottle", "--rating", rating
                        )

                self.assertEqual(caught.exception.code, 2)
                self.assertEqual(Place.objects.count(), 0)

    def test_argparse_rejects_an_unknown_status_before_the_command_body_runs(self):
        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as caught:
                self.run_add_from_command_line("Tartine", "--status", "nope")

        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(Place.objects.count(), 0)


class AddCommandOutputTests(AddCommandTestCase):
    """One clean line on stdout describing what was stored."""

    def test_a_successful_add_prints_exactly_one_line(self):
        out, err = self.run_add(
            "Blue Bottle",
            "--neighborhood",
            "Mission",
            "--tag",
            "coffee",
            "--rating",
            "4",
        )

        self.assertEqual(len(out.strip().splitlines()), 1)

    def test_the_line_names_the_place_its_status_its_rating_and_its_tags(self):
        out, err = self.run_add(
            "Blue Bottle",
            "--neighborhood",
            "Mission",
            "--tag",
            "Coffee",
            "--tag",
            "wifi",
            "--rating",
            "4",
        )

        self.assertIn("Blue Bottle", out)
        self.assertIn(Place.Status.VISITED.value, out)
        self.assertIn("4", out)
        self.assertIn("coffee", out)
        self.assertIn("wifi", out)

    def test_the_line_reflects_the_stored_tag_not_the_typed_one(self):
        out, err = self.run_add("Blue Bottle", "--tag", "Coffee")

        self.assertIn("coffee", out)
        self.assertNotIn("Coffee", out)

    def test_a_wishlist_add_says_so_and_mentions_no_rating(self):
        out, err = self.run_add("Tartine")

        self.assertIn("Tartine", out)
        self.assertIn(Place.Status.WISHLIST.value, out)
        self.assertNotIn("rating", out)

    def test_a_successful_add_writes_nothing_to_stderr(self):
        out, err = self.run_add("Tartine", "--tag", "pastry")

        self.assertEqual(err, "")


# ---------------------------------------------------------------------------
# Issue #5 -- the fuzzy ranking module, places/search.py
#
# Per _docs/testing-guidelines.md this is the most important test surface in
# the project, and per the issue it is pure: plain unittest.TestCase, no
# database, no call_command, no network. Candidates are in-memory stand-ins,
# which is exactly the point -- search.py must not need places.models.
# ---------------------------------------------------------------------------


def place(name="", note="", neighborhood="", tags=()):
    """An in-memory candidate: the duck type search.py documents."""
    return SimpleNamespace(name=name, note=note, neighborhood=neighborhood, tags=tags)


class TagRow:
    """Stands in for a ``Tag`` row: an object carrying a ``.name``."""

    def __init__(self, name):
        self.name = name


class RelatedManager:
    """Stands in for ``place.tags`` on a real ``Place``: not iterable, has all()."""

    def __init__(self, *rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


BLUE_BOTTLE = place("Blue Bottle", "good wifi and quiet", "Mitte", ["coffee"])
RAMEN_SHOP = place("Ramen Shop", "rich tonkotsu broth", "Neukolln", ["ramen"])
TIERGARTEN = place("Tiergarten", "the big park", "Mitte", ["park"])
SAMPLE_PLACES = [BLUE_BOTTLE, RAMEN_SHOP, TIERGARTEN]


class SearchModuleContractTests(unittest.TestCase):
    """The rules issue #5 states about the module itself, not its answers.

    Source-level on purpose: "pure, no database, no models" is a property of
    the code, and a behavioral test would keep passing on the day someone adds
    a queryset to it that happens to work in their environment.
    """

    def test_the_module_imports_neither_the_models_nor_django(self):
        for module in _imported_modules(search):
            self.assertFalse(
                module == "places.models"
                or module.startswith("django")
                or module.startswith("places.management"),
                "search.py must stay pure but imports " + module,
            )

    def test_the_module_performs_no_database_access_of_its_own(self):
        """Nodes, not text: the module docstring describes the caller's
        queryset, and a sentence about a prefetch is not a prefetch."""
        referenced = _referenced_names(search)

        self.assertNotIn("objects", referenced)
        self.assertNotIn("select_related", referenced)
        self.assertNotIn("prefetch_related", referenced)
        self.assertNotIn("filter", _called_names(search))

    def test_the_module_neither_prints_nor_writes(self):
        """Calls, not text: the docstring shows the caller printing, which is
        the point -- the module returns a value and prints nothing itself."""
        called = _called_names(search)

        self.assertNotIn("print", called)
        self.assertNotIn("open", called)
        self.assertNotIn("write", called)

    def test_ranking_emits_nothing_on_either_stream(self):
        out, err = StringIO(), StringIO()

        with redirect_stdout(out), redirect_stderr(err):
            search.rank_places("blu bottl", SAMPLE_PLACES)
            search.rank_places("zzzzqqq", SAMPLE_PLACES)

        self.assertEqual((out.getvalue(), err.getvalue()), ("", ""))

    def test_the_docstring_states_the_candidate_contract(self):
        doc = search.__doc__

        for expected in ("name", "note", "neighborhood", "tags", ".name"):
            self.assertIn(expected, doc)
        self.assertIn("lowercase", doc.lower())

    def test_the_tuning_knobs_are_named_module_level_constants(self):
        self.assertEqual(search.STRONG_MATCH_THRESHOLD, 60)
        self.assertEqual(search.WEAK_MATCH_LIMIT, 3)
        self.assertGreater(search.NAME_WEIGHT, search.NOTE_WEIGHT)
        self.assertGreater(search.NAME_WEIGHT, search.TAG_WEIGHT)
        self.assertGreater(search.NAME_WEIGHT, search.NEIGHBORHOOD_WEIGHT)

    def test_no_second_copy_of_the_threshold_or_the_weak_limit_is_hard_coded(self):
        """Both numbers exist once, as their constant. A literal 60 or 3
        anywhere else in the module is the bug this criterion forbids."""
        named = {"STRONG_MATCH_THRESHOLD", "WEAK_MATCH_LIMIT"}
        tree = _module_tree(search)
        declarations = {
            id(node.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id in named
                for target in node.targets
            )
        }
        self.assertEqual(len(declarations), len(named), "both constants declared once")

        offenders = [
            (node.lineno, node.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and id(node) not in declarations
            and not isinstance(node.value, bool)
            and isinstance(node.value, (int, float))
            and node.value in (60, 3)
        ]

        self.assertEqual(offenders, [])


class SearchReturnShapeTests(unittest.TestCase):
    """Scores, ordering, and what a result set is allowed to contain."""

    def test_every_score_sits_in_the_closed_zero_to_hundred_range(self):
        for query in ("blue bottle", "blu bottl", "zzzzqqq", "coffee wifi park"):
            for result in search.rank_places(query, SAMPLE_PLACES):
                self.assertGreaterEqual(result.score, 0)
                self.assertLessEqual(result.score, 100)

    def test_results_come_back_best_first(self):
        results = search.rank_places("coffee", SAMPLE_PLACES)
        scores = [result.score for result in results]

        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_each_candidate_appears_at_most_once_and_none_are_invented(self):
        results = search.rank_places("zzzzqqq", SAMPLE_PLACES)
        returned = [result.place for result in results]

        self.assertEqual(len(returned), len({id(candidate) for candidate in returned}))
        for candidate in returned:
            self.assertIn(candidate, SAMPLE_PLACES)

    def test_every_result_carries_its_own_score(self):
        for result in search.rank_places("blue bottle", SAMPLE_PLACES):
            self.assertIsInstance(result.score, (int, float))

    def test_the_result_set_is_a_sequence_of_results(self):
        results = search.rank_places("blue bottle", SAMPLE_PLACES)

        self.assertEqual(len(results), len(list(results)))
        self.assertIs(results[0].place, BLUE_BOTTLE)


class SearchFieldWeightingTests(unittest.TestCase):
    """Where the query text sits changes the score, in a stated order."""

    #: Four places identical but for which field holds the query word. The
    #: filler names are chosen to score nothing against "ramen".
    NAME = place("ramen", note="", neighborhood="", tags=())
    NOTE = place("Alpha", note="ramen", neighborhood="", tags=())
    TAG = place("Bravo", note="", neighborhood="", tags=["ramen"])
    HOOD = place("Charlie", note="", neighborhood="ramen", tags=())

    def scores(self, query, candidates):
        return {
            id(result.place): result.score
            for result in search.rank_places(query, candidates)
        }

    def test_a_name_match_outranks_the_same_text_in_a_note(self):
        scores = self.scores("ramen", [self.NAME, self.NOTE])

        self.assertGreater(scores[id(self.NAME)], scores[id(self.NOTE)])

    def test_a_name_match_outranks_the_same_text_in_a_tag(self):
        scores = self.scores("ramen", [self.NAME, self.TAG])

        self.assertGreater(scores[id(self.NAME)], scores[id(self.TAG)])

    def test_a_name_match_outranks_the_same_text_in_a_neighborhood(self):
        scores = self.scores("ramen", [self.NAME, self.HOOD])

        self.assertGreater(scores[id(self.NAME)], scores[id(self.HOOD)])

    def test_the_name_match_is_ranked_first_of_all_four(self):
        results = search.rank_places("ramen", [self.HOOD, self.TAG, self.NOTE, self.NAME])

        self.assertIs(results[0].place, self.NAME)

    def test_two_matching_fields_beat_one(self):
        """The worked example from the issue: same name, same tag, and only
        the note decides."""
        both = place("Blue Bottle", "good wifi", "Mitte", ["coffee"])
        one = place("Blue Bottle", "good pastries", "Mitte", ["coffee"])

        both_score = search.rank_places("coffee wifi", [both])[0].score
        one_score = search.rank_places("coffee wifi", [one])[0].score

        self.assertGreater(both_score, one_score)
        self.assertIs(search.rank_places("coffee wifi", [one, both])[0].place, both)

    def test_a_non_matching_field_costs_nothing(self):
        """A long irrelevant note must not average the score down."""
        chatty = place(
            "Blue Bottle",
            "the chairs are green and the queue on saturdays runs out the door "
            "past the bookshop and around the corner towards the bridge",
            "Mitte",
            ["coffee"],
        )
        terse = place("Blue Bottle", "", "Mitte", ["coffee"])

        chatty_score = search.rank_places("blue bottle", [chatty])[0].score
        terse_score = search.rank_places("blue bottle", [terse])[0].score

        self.assertGreaterEqual(chatty_score, terse_score)


class SearchTypoAndCaseTests(unittest.TestCase):
    """Typos forgiven, case ignored, whitespace trimmed."""

    def ranked_names(self, query, candidates=None):
        return [
            (result.place.name, result.score)
            for result in search.rank_places(query, candidates or SAMPLE_PLACES)
        ]

    def test_blu_bottl_finds_blue_bottle_as_a_strong_top_result(self):
        """The worked example from _docs/task-template.md, literally."""
        results = search.rank_places("blu bottl", SAMPLE_PLACES)

        self.assertIs(results[0].place, BLUE_BOTTLE)
        self.assertFalse(results.is_weak)
        self.assertFalse(results[0].is_weak)

    def test_cofee_finds_the_place_tagged_coffee_without_falling_back(self):
        results = search.rank_places("cofee", SAMPLE_PLACES)

        self.assertIs(results[0].place, BLUE_BOTTLE)
        self.assertFalse(results.is_weak)

    def test_ramn_finds_ramen_shop_without_falling_back(self):
        results = search.rank_places("ramn", SAMPLE_PLACES)

        self.assertIs(results[0].place, RAMEN_SHOP)
        self.assertFalse(results.is_weak)

    def test_tag_matching_ignores_the_case_of_the_query(self):
        self.assertEqual(self.ranked_names("COFFEE"), self.ranked_names("coffee"))
        self.assertEqual(self.ranked_names("Coffee"), self.ranked_names("coffee"))

    def test_tag_matching_ignores_the_case_of_the_stored_tag(self):
        """AGENTS.md: lowercase on read, never assume the caller normalized."""
        shouty = place("Alpha", tags=["COFFEE"])
        titled = place("Alpha", tags=["Coffee"])
        plain = place("Alpha", tags=["coffee"])

        self.assertEqual(
            search.rank_places("coffee", [shouty])[0].score,
            search.rank_places("coffee", [plain])[0].score,
        )
        self.assertEqual(
            search.rank_places("coffee", [titled])[0].score,
            search.rank_places("coffee", [plain])[0].score,
        )

    def test_a_tag_row_and_a_plain_string_score_identically(self):
        rows = place("Alpha", tags=[TagRow("Coffee")])
        strings = place("Alpha", tags=["coffee"])

        self.assertEqual(
            search.rank_places("coffee", [rows])[0].score,
            search.rank_places("coffee", [strings])[0].score,
        )

    def test_tags_arriving_as_a_related_manager_are_read_too(self):
        managed = place("Alpha", tags=RelatedManager(TagRow("Coffee")))
        strings = place("Alpha", tags=["coffee"])

        self.assertEqual(
            search.rank_places("coffee", [managed])[0].score,
            search.rank_places("coffee", [strings])[0].score,
        )

    def test_name_note_and_neighborhood_matching_ignore_case(self):
        self.assertEqual(
            self.ranked_names("BLUE BOTTLE"), self.ranked_names("blue bottle")
        )
        self.assertEqual(self.ranked_names("MITTE"), self.ranked_names("mitte"))
        self.assertEqual(self.ranked_names("TONKOTSU"), self.ranked_names("tonkotsu"))

    def test_surrounding_whitespace_on_the_query_is_ignored(self):
        self.assertEqual(
            self.ranked_names("  blue bottle  "), self.ranked_names("blue bottle")
        )

    def test_punctuation_in_the_query_still_returns_a_ranking(self):
        results = search.rank_places("blue-bottle", SAMPLE_PLACES)

        self.assertIs(results[0].place, BLUE_BOTTLE)
        self.assertFalse(results.is_weak)

    def test_a_query_far_longer_than_any_field_returns_a_result(self):
        results = search.rank_places("blue bottle " * 40, SAMPLE_PLACES)

        self.assertGreaterEqual(len(results), 1)


class SearchThresholdTests(unittest.TestCase):
    """One threshold, applied inclusively, dropping everything below it."""

    def test_only_the_candidates_at_or_above_the_threshold_come_back(self):
        results = search.rank_places("ramn", SAMPLE_PLACES)

        self.assertEqual([result.place for result in results], [RAMEN_SHOP])
        for result in results:
            self.assertGreaterEqual(result.score, search.STRONG_MATCH_THRESHOLD)

    def test_the_strong_path_is_not_capped_at_the_weak_limit(self):
        many = [place("Blue Bottle %d" % index, tags=["coffee"]) for index in range(7)]

        results = search.rank_places("blue bottle", many)

        self.assertFalse(results.is_weak)
        self.assertEqual(len(results), len(many))
        self.assertGreater(len(results), search.WEAK_MATCH_LIMIT)

    def test_a_real_score_landing_exactly_on_the_threshold_is_strong(self):
        """The boundary hit without patching anything: a note-only match at
        0.80 x 75.0 scores 60.00, dead on the constant, and must count as
        strong. If the weights are retuned this test fails loudly, which is the
        point -- 60 is chosen so the issue's examples land on the right side of
        it."""
        on_the_line = place("Quiet Corner", note="abce")

        results = search.rank_places("abcd", [on_the_line])

        self.assertEqual(results[0].score, search.STRONG_MATCH_THRESHOLD)
        self.assertFalse(results.is_weak)
        self.assertFalse(results[0].is_weak)

    def test_a_score_exactly_equal_to_the_threshold_counts_as_strong(self):
        """Inclusive, not exclusive. Pinning the threshold to a score the
        module actually produced is the only way to hit the boundary exactly
        without hard-coding today's tuning."""
        exact = search.rank_places("blu bottl", [BLUE_BOTTLE])[0].score

        with mock.patch.object(search, "STRONG_MATCH_THRESHOLD", exact):
            results = search.rank_places("blu bottl", SAMPLE_PLACES)
        self.assertFalse(results.is_weak)
        self.assertIn(BLUE_BOTTLE, [result.place for result in results])

        with mock.patch.object(search, "STRONG_MATCH_THRESHOLD", exact + 0.01):
            just_missed = search.rank_places("blu bottl", SAMPLE_PLACES)
        self.assertTrue(just_missed.is_weak)


class SearchFallbackTests(unittest.TestCase):
    """No strong match: guess, and say that it is a guess."""

    def test_a_query_matching_nothing_returns_exactly_three_weak_results(self):
        results = search.rank_places("zzzzqqq", SAMPLE_PLACES)

        self.assertEqual(len(results), search.WEAK_MATCH_LIMIT)
        self.assertTrue(results.is_weak)
        self.assertTrue(all(result.is_weak for result in results))

    def test_the_fallback_fires_even_when_every_score_is_zero(self):
        results = search.rank_places("zzzzqqq", SAMPLE_PLACES)

        self.assertEqual([result.score for result in results], [0, 0, 0])
        self.assertTrue(results.is_weak)

    def test_the_fallback_never_returns_more_than_the_weak_limit(self):
        many = [place("Place " + letter) for letter in "abcdefgh"]

        results = search.rank_places("zzzzqqq", many)

        self.assertEqual(len(results), search.WEAK_MATCH_LIMIT)

    def test_two_candidates_give_two_weak_results_and_one_gives_one(self):
        """Closest 3 is a ceiling, never a quota -- nothing is padded."""
        two = search.rank_places("zzzzqqq", [BLUE_BOTTLE, RAMEN_SHOP])
        one = search.rank_places("zzzzqqq", [BLUE_BOTTLE])

        self.assertEqual(len(two), 2)
        self.assertEqual(len(one), 1)
        self.assertTrue(two.is_weak)
        self.assertTrue(one.is_weak)
        self.assertEqual([result.place for result in one], [BLUE_BOTTLE])

    def test_no_candidates_at_all_returns_an_empty_set_and_no_fallback(self):
        for query in ("zzzzqqq", "blue bottle", ""):
            results = search.rank_places(query, [])

            self.assertEqual(len(results), 0)
            self.assertFalse(results.is_weak)

    def test_a_result_set_is_all_strong_or_all_weak_and_never_mixed(self):
        for query in ("blue bottle", "cofee", "zzzzqqq", "ramn"):
            results = search.rank_places(query, SAMPLE_PLACES)
            flags = {result.is_weak for result in results}

            self.assertEqual(flags, {results.is_weak})

    def test_the_weak_flag_is_readable_without_comparing_scores(self):
        """How issue #6 decides whether to print its header: read the flag,
        do not import the threshold and do not recompute anything."""
        weak = search.rank_places("zzzzqqq", SAMPLE_PLACES)
        strong = search.rank_places("blue bottle", SAMPLE_PLACES)

        self.assertIs(weak.is_weak, True)
        self.assertIs(strong.is_weak, False)


class SearchEmptyAndDegenerateInputTests(unittest.TestCase):
    """The inputs that break tools: empty everything, None, punctuation."""

    def test_an_empty_query_returns_nothing_and_does_not_fall_back(self):
        results = search.rank_places("", SAMPLE_PLACES)

        self.assertEqual(len(results), 0)
        self.assertFalse(results.is_weak)

    def test_a_whitespace_only_query_returns_nothing_and_does_not_fall_back(self):
        results = search.rank_places("   \t ", SAMPLE_PLACES)

        self.assertEqual(len(results), 0)
        self.assertFalse(results.is_weak)

    def test_a_place_with_only_a_name_is_scored_on_that_name_alone(self):
        bare = place("Blue Bottle", note="", neighborhood="", tags=())

        results = search.rank_places("blue bottle", [bare])

        self.assertFalse(results.is_weak)
        self.assertGreaterEqual(results[0].score, search.STRONG_MATCH_THRESHOLD)

    def test_empty_fields_never_match_an_empty_string_into_a_high_score(self):
        bare = place("", note="", neighborhood="", tags=())

        results = search.rank_places("blue bottle", [bare])

        self.assertEqual(results[0].score, 0)
        self.assertTrue(results.is_weak)

    def test_a_none_note_or_neighborhood_is_treated_like_an_empty_string(self):
        nones = place("Blue Bottle", note=None, neighborhood=None, tags=())
        empties = place("Blue Bottle", note="", neighborhood="", tags=())

        self.assertEqual(
            search.rank_places("blue bottle", [nones])[0].score,
            search.rank_places("blue bottle", [empties])[0].score,
        )

    def test_a_candidate_with_an_empty_name_does_not_crash_the_module(self):
        nameless = place("", note="good wifi", neighborhood="Mitte", tags=["coffee"])

        results = search.rank_places("coffee", [nameless])

        self.assertEqual(len(results), 1)

    def test_a_candidate_missing_the_attributes_entirely_is_tolerated(self):
        results = search.rank_places(
            "blue bottle", [SimpleNamespace(name="Blue Bottle")]
        )

        self.assertFalse(results.is_weak)


class SearchDeterminismTests(unittest.TestCase):
    """Same data in, same order out -- every run, whatever the input order."""

    def test_the_same_call_twice_returns_equal_results(self):
        first = search.rank_places("coffee wifi", SAMPLE_PLACES)
        second = search.rank_places("coffee wifi", SAMPLE_PLACES)

        self.assertEqual(first, second)
        self.assertEqual(
            [(result.place, result.score, result.is_weak) for result in first],
            [(result.place, result.score, result.is_weak) for result in second],
        )

    def test_shuffling_the_input_does_not_change_the_output_order(self):
        """Fixed permutations, not random ones: a test that shuffles by luck
        cannot say which ordering it proved."""
        baseline = [result.place for result in search.rank_places("zzzzqqq", SAMPLE_PLACES)]

        for order in (
            [TIERGARTEN, RAMEN_SHOP, BLUE_BOTTLE],
            [RAMEN_SHOP, BLUE_BOTTLE, TIERGARTEN],
            [TIERGARTEN, BLUE_BOTTLE, RAMEN_SHOP],
        ):
            shuffled = [result.place for result in search.rank_places("zzzzqqq", order)]

            self.assertEqual(shuffled, baseline)

    def test_ties_are_broken_by_lowercased_name_ascending(self):
        """The names discriminate on purpose: a raw ASCII sort puts every
        capital ahead of every lowercase letter, so it would answer
        ``Mike Bar, Zulu Bar, alpha bar``. Only a lowercased key gives the
        alphabetical order below."""
        zulu = place("Zulu Bar", tags=["coffee"])
        alpha = place("alpha bar", tags=["coffee"])
        mike = place("Mike Bar", tags=["coffee"])

        results = search.rank_places("coffee", [zulu, mike, alpha])

        self.assertEqual(len({result.score for result in results}), 1)
        self.assertEqual(
            [result.place.name for result in results],
            ["alpha bar", "Mike Bar", "Zulu Bar"],
        )
        self.assertNotEqual(
            [result.place.name for result in results],
            sorted(candidate.name for candidate in (zulu, mike, alpha)),
            "test data must distinguish a lowercased sort from an ASCII one",
        )

    def test_the_tie_break_also_decides_which_three_the_fallback_picks(self):
        """Same trap, one level up: an ASCII sort of these five would hand back
        ``Bravo, Delta, alpha`` -- a different three, not merely a different
        order."""
        candidates = [
            place("echo"),
            place("alpha"),
            place("Delta"),
            place("Bravo"),
            place("charlie"),
        ]

        results = search.rank_places("zzzzqqq", candidates)

        self.assertEqual(
            [result.place.name for result in results], ["alpha", "Bravo", "charlie"]
        )
        self.assertNotEqual(
            [result.place.name for result in results],
            sorted(candidate.name for candidate in candidates)[:3],
            "test data must distinguish a lowercased sort from an ASCII one",
        )


# ---------------------------------------------------------------------------
# The `find` command (issue #6)
#
# `find` is a shell around `places/search.py`: it filters, hands candidates
# over, and prints. Ranking itself is tested above, without a database.
# ---------------------------------------------------------------------------


def _find_module():
    """The ``find`` command module, imported the way Django finds it."""
    from places.management.commands import find as find_module

    return find_module


class FindCommandDiscoveryTests(SimpleTestCase):
    """The command is discoverable and takes the documented flags."""

    def parser(self):
        return load_command_class("places", "find").create_parser("manage.py", "find")

    def test_find_is_registered_as_a_command_of_the_places_app(self):
        self.assertEqual(get_commands().get("find"), "places")

    def test_help_documents_the_positional_query_and_every_flag(self):
        help_text = self.parser().format_help()

        self.assertIn("query", help_text)
        for flag in ("--limit", "--status", "--neighborhood", "--tag"):
            with self.subTest(flag=flag):
                self.assertIn(flag, help_text)

    def test_the_query_is_one_positional_string_not_a_list_of_words(self):
        """``find "coffee wifi"`` is one query, not two."""
        parsed = self.parser().parse_args(["coffee wifi"])

        self.assertEqual(parsed.query, "coffee wifi")

    def test_limit_defaults_to_five_and_is_an_integer(self):
        parsed = self.parser().parse_args(["coffee"])

        self.assertEqual(parsed.limit, 5)
        self.assertEqual(self.parser().parse_args(["coffee", "--limit", "2"]).limit, 2)

    def test_tag_is_a_repeatable_flag_not_a_comma_separated_string(self):
        tag = next(a for a in self.parser()._actions if a.dest == "tags")

        self.assertEqual(tag.option_strings, ["--tag"])
        self.assertEqual(
            self.parser().parse_args(["x", "--tag", "a", "--tag", "b"]).tags,
            ["a", "b"],
        )

    def test_status_offers_exactly_the_models_two_choices(self):
        status = next(a for a in self.parser()._actions if a.dest == "status")

        self.assertEqual(list(status.choices), list(Place.Status.values))


class FindCommandSourceRuleTests(SimpleTestCase):
    """Rules issue #6 states about ``find.py`` itself.

    Deliberately source-level: each describes a mechanism the command must
    *delegate* rather than reimplement, and a reimplementation would pass every
    behavioral test right up until the ranker or the normalizer changes. Read
    off the AST -- ``find.py``'s docstring discusses the threshold and the
    fallback at length, and saying so is not doing so.
    """

    def test_find_delegates_ranking_and_owns_no_scoring_of_its_own(self):
        referenced = _referenced_names(_find_module())

        self.assertIn("rank_places", _called_names(_find_module()))
        for module in _imported_modules(_find_module()):
            with self.subTest(module=module):
                self.assertFalse(module.startswith("rapidfuzz"))
        self.assertEqual([name for name in referenced if "fuzz" in name], [])
        self.assertNotIn("STRONG_MATCH_THRESHOLD", referenced)

    def test_find_does_not_re_sort_what_the_ranker_returned(self):
        called = _called_names(_find_module())

        self.assertNotIn("sorted", called)
        self.assertNotIn("sort", called)

    def test_find_does_not_hand_roll_tag_normalization(self):
        called = _called_names(_find_module())

        self.assertIn("normalize_tag_name", called)
        for hand_rolled in ("lower", "casefold", "upper", "title"):
            with self.subTest(call=hand_rolled):
                self.assertNotIn(hand_rolled, called)
        self.assertNotIn("iexact", _query_lookups(_find_module()))


class FindCommandTestCase(TestCase):
    """Shared plumbing: run ``find`` through ``call_command``, per
    ``_docs/testing-guidelines.md``. Nothing here shells out."""

    #: The header the fallback prints, without its count.
    WEAK_HEADER = "No strong match. Closest"

    def make_place(self, name, tags=(), **fields):
        place = Place.objects.create(name=name, **fields)
        for tag_name in tags:
            place.tags.add(Tag.objects.get_or_create(name=tag_name)[0])
        return place

    def run_find(self, *args):
        out, err = StringIO(), StringIO()
        call_command("find", *args, stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def run_find_from_command_line(self, *args):
        """Drive ``find`` the way ``manage.py`` does, so exit codes are real.

        See ``AddCommandTestCase.run_add_from_command_line``: setting
        ``_called_from_command_line`` is what lets argparse's own exit status
        surface without shelling out to ``manage.py``.
        """
        command = load_command_class("places", "find")
        command._called_from_command_line = True
        out, err = StringIO(), StringIO()
        call_command(command, *args, stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def assert_rejected(self, *args):
        """Run a ``find`` the command itself validates away.

        Returns the ``CommandError`` -- rendered by ``manage.py`` as a one-line
        message with a non-zero exit -- after checking nothing was printed.
        """
        out = StringIO()
        with self.assertRaises(CommandError) as caught:
            call_command("find", *args, stdout=out)
        self.assertEqual(out.getvalue(), "")
        return caught.exception

    def lines(self, out):
        """The non-empty lines of captured output, header included."""
        return [line for line in out.splitlines() if line.strip()]

    def result_lines(self, out):
        """The non-empty lines that are not the weak-match header."""
        return [line for line in self.lines(out) if self.WEAK_HEADER not in line]


class FindCommandResultTests(FindCommandTestCase):
    """A populated journal, a query that hits."""

    def test_a_matching_query_prints_one_line_per_matching_place(self):
        self.make_place("Blue Bottle", neighborhood="Mission", note="good wifi")
        self.make_place("Sightglass", neighborhood="SoMa", note="coffee, loud")

        out, err = self.run_find("coffee")

        self.assertIn("Sightglass", out)
        self.assertEqual(len(self.result_lines(out)), 1)

    def test_a_typo_still_finds_the_place(self):
        self.make_place("Blue Bottle", note="the good wifi one")

        out, err = self.run_find("blu bottl")

        self.assertIn("Blue Bottle", out)

    def test_results_are_printed_in_the_rankers_order_not_re_sorted(self):
        """The name match outranks the note match, so it prints first --
        alphabetically it would print second."""
        self.make_place("Coffee Bar", neighborhood="Mission")
        self.make_place("Aardvark Tea", neighborhood="SoMa", note="great coffee here")

        out, err = self.run_find("coffee")

        self.assertIn("Coffee Bar", out)
        self.assertIn("Aardvark Tea", out)
        self.assertLess(out.index("Coffee Bar"), out.index("Aardvark Tea"))

    def test_a_strong_match_prints_no_weak_match_header(self):
        self.make_place("Blue Bottle", note="good coffee")
        self.make_place("Tartine", note="pastries")

        out, err = self.run_find("coffee")

        self.assertNotIn("No strong match", out)

    def test_a_search_writes_nothing_to_stderr(self):
        self.make_place("Blue Bottle", note="good coffee")

        out, err = self.run_find("coffee")

        self.assertEqual(err, "")

    def test_a_search_mutates_nothing(self):
        """``find`` only reads: no status flips, no timestamps, no counters."""
        self.make_place("Blue Bottle", note="coffee", tags=["coffee"])
        self.make_place("Tartine", note="pastry", status=Place.Status.VISITED)
        before = list(Place.objects.values().order_by("id"))

        self.run_find("coffee")
        self.run_find("qqqqqq")

        self.assertEqual(list(Place.objects.values().order_by("id")), before)


class FindCommandLimitTests(FindCommandTestCase):
    """``--limit`` caps strong matches and defaults to five."""

    def populate(self, count):
        for index in range(count):
            self.make_place(f"Coffee Number {index}", note="espresso")

    def test_without_a_limit_at_most_five_strong_matches_are_printed(self):
        self.populate(8)

        out, err = self.run_find("coffee")

        self.assertEqual(len(self.result_lines(out)), 5)

    def test_a_limit_caps_the_number_of_lines(self):
        self.populate(8)

        out, err = self.run_find("coffee", "--limit", "2")

        self.assertEqual(len(self.result_lines(out)), 2)

    def test_a_limit_larger_than_the_journal_prints_what_there_is(self):
        self.populate(4)

        out, err = self.run_find("coffee", "--limit", "100")

        self.assertEqual(len(self.result_lines(out)), 4)
        self.assertNotIn("No strong match", out)

    def test_a_limit_below_three_narrows_the_weak_match_fallback_too(self):
        """``--limit`` never *widens* the fallback -- that is the ranker's
        cap -- but it does narrow it, and the header names what was printed
        rather than the cap it did not reach.

        This is a design decision rather than an acceptance criterion: leaving
        the fallback pinned at 3 whatever ``--limit`` says would still be
        criteria-compliant, which is exactly why it needs a test of its own.
        """
        for limit, expected_header in ((1, "Closest 1:"), (2, "Closest 2:")):
            with self.subTest(limit=limit):
                Place.objects.all().delete()
                self.populate(6)

                out, err = self.run_find("qqqqqq", "--limit", str(limit))

                self.assertIn("No strong match. " + expected_header, out)
                self.assertEqual(len(self.result_lines(out)), limit)

    def test_a_narrowed_fallback_header_never_names_a_count_it_did_not_print(self):
        """The header and the line count are the same number, always."""
        self.populate(5)

        out, err = self.run_find("qqqqqq", "--limit", "1")

        self.assertNotIn("Closest 3", out)
        self.assertNotIn("Closest 2", out)
        self.assertEqual(len(self.result_lines(out)), 1)

    def test_a_zero_or_negative_limit_is_rejected_by_name(self):
        self.populate(4)

        for limit in ("0", "-1", "-10"):
            with self.subTest(limit=limit):
                error = self.assert_rejected("coffee", "--limit", limit)

                self.assertIn("--limit", str(error))


class FindCommandArgumentTests(FindCommandTestCase):
    """Only malformed invocations are errors."""

    def test_a_missing_query_is_an_argparse_usage_error(self):
        self.make_place("Blue Bottle")

        with redirect_stderr(StringIO()) as captured:
            with self.assertRaises(SystemExit) as caught:
                self.run_find_from_command_line()

        self.assertEqual(caught.exception.code, 2)
        self.assertIn("query", captured.getvalue())

    def test_a_blank_query_is_rejected_and_never_reaches_the_ranker(self):
        for index in range(4):
            self.make_place(f"Place {index}")

        for query in ("", "   ", "\t"):
            with self.subTest(query=repr(query)):
                error = self.assert_rejected(query)

                self.assertIn("query", str(error))

    def test_a_query_of_only_punctuation_is_rejected_like_a_blank_one(self):
        """``---`` survives ``strip()`` but carries no search intent: the
        ranker only ever sees letters and digits, so such a query cannot
        prefer one place to another. Guessing three places under a header that
        says we looked would be a lie; see ``find.py``'s module docstring."""
        for index in range(5):
            self.make_place(f"Place {index}")

        # ``--`` is argparse's end-of-options marker, not part of the query:
        # without it a query starting with a dash is read as a flag, which is
        # argparse's business and not this command's.
        for query in ("---", "!!!", ".", "  !!  ", "?? -- ??"):
            with self.subTest(query=query):
                error = self.assert_rejected("--", query)

                self.assertIn("letter", str(error))

    def test_a_query_carrying_one_digit_is_a_real_query(self):
        """The rejection is about having nothing to search for, not about
        punctuation: ``4`` is searchable, ``!`` is not."""
        self.make_place("Cafe 4", note="the one on the corner")

        out, err = self.run_find("4!")

        self.assertIn("Cafe 4", out)

    def test_an_unrecognized_status_lists_the_valid_choices_and_prints_nothing(self):
        self.make_place("Blue Bottle", note="coffee")

        with redirect_stderr(StringIO()) as captured:
            with self.assertRaises(SystemExit) as caught:
                self.run_find_from_command_line("coffee", "--status", "vistied")

        self.assertEqual(caught.exception.code, 2)
        for choice in Place.Status.values:
            with self.subTest(choice=choice):
                self.assertIn(choice, captured.getvalue())
        self.assertNotIn("Blue Bottle", captured.getvalue())

    def test_a_well_formed_query_that_matches_nothing_is_not_an_error(self):
        """Finding nothing is an answer: no exception, so ``manage.py`` exits
        0 -- for a weak fallback, for dead filters, and for an empty journal."""
        self.make_place("Blue Bottle", note="coffee")

        for query, arguments in (
            ("qqqqqq", ()),
            ("qqqqqq", ("--tag", "nonexistent-tag")),
        ):
            with self.subTest(arguments=arguments):
                self.run_find(query, *arguments)

        Place.objects.all().delete()
        self.run_find("anything")


class FindCommandFilterTests(FindCommandTestCase):
    """Filters narrow the candidate set in the ORM, before anything is scored."""

    def setUp(self):
        self.blue = self.make_place(
            "Blue Bottle",
            neighborhood="Mission District",
            note="good wifi",
            tags=["coffee", "wifi"],
            status=Place.Status.VISITED,
            rating=4,
        )
        self.sightglass = self.make_place(
            "Sightglass",
            neighborhood="SoMa",
            note="good coffee, loud",
            tags=["coffee"],
        )
        self.tartine = self.make_place(
            "Tartine",
            neighborhood="Mission District",
            note="coffee is fine, pastries better",
        )

    def test_a_tag_filter_matches_the_stored_tag_whatever_case_is_typed(self):
        for typed in ("Coffee", "COFFEE", "coffee", "cOfFeE"):
            with self.subTest(typed=typed):
                out, err = self.run_find("good", "--tag", typed)

                self.assertIn("Blue Bottle", out)
                self.assertIn("Sightglass", out)

    def test_a_tag_filter_strips_surrounding_whitespace(self):
        """The ``NOCASE`` collation folds case but does not strip, so this is
        what pins the normalizer rather than the database."""
        out, err = self.run_find("good", "--tag", "  Coffee  ")

        self.assertIn("Blue Bottle", out)
        self.assertIn("Sightglass", out)

    def test_a_tag_filter_excludes_a_place_that_does_not_carry_the_tag(self):
        out, err = self.run_find("coffee", "--tag", "Coffee")

        self.assertNotIn("Tartine", out)

    def test_two_tags_are_anded_not_ored(self):
        out, err = self.run_find("good", "--tag", "coffee", "--tag", "wifi")

        self.assertIn("Blue Bottle", out)
        self.assertNotIn("Sightglass", out)

    def test_status_narrows_to_one_side_of_the_journal(self):
        out, err = self.run_find("good", "--status", Place.Status.VISITED)

        self.assertIn("Blue Bottle", out)
        self.assertNotIn("Sightglass", out)

    def test_status_wishlist_never_returns_a_visited_place(self):
        out, err = self.run_find("good", "--status", Place.Status.WISHLIST)

        self.assertNotIn("Blue Bottle", out)
        self.assertIn("Sightglass", out)

    def test_neighborhood_matches_a_case_insensitive_substring(self):
        out, err = self.run_find("good", "--neighborhood", "mission")

        self.assertIn("Blue Bottle", out)
        self.assertNotIn("Sightglass", out)

    def test_every_filter_can_be_combined_in_one_invocation(self):
        out, err = self.run_find(
            "good",
            "--tag",
            "Coffee",
            "--status",
            Place.Status.VISITED,
            "--neighborhood",
            "MISSION",
            "--limit",
            "5",
        )

        self.assertIn("Blue Bottle", out)
        self.assertEqual(len(self.result_lines(out)), 1)

    def test_a_filter_that_matches_nothing_says_so_and_prints_no_results(self):
        out, err = self.run_find("good", "--tag", "nonexistent-tag")

        self.assertNotIn("Blue Bottle", out)
        self.assertNotIn("Sightglass", out)
        self.assertNotIn("No strong match", out)
        self.assertIn("filters", out)

    def test_a_filter_that_matches_nothing_is_not_the_empty_journal_message(self):
        out, err = self.run_find("good", "--tag", "nonexistent-tag")

        self.assertNotIn("empty", out)


class FindCommandFallbackTests(FindCommandTestCase):
    """Below the ranker's threshold the tool guesses out loud."""

    def populate(self, count, **fields):
        return [self.make_place(f"Place {index}", **fields) for index in range(count)]

    def test_a_weak_query_prints_the_exact_header_and_three_lines(self):
        self.populate(6)

        out, err = self.run_find("qqqqqq")

        self.assertIn("No strong match. Closest 3:", out)
        self.assertEqual(len(self.result_lines(out)), 3)

    def test_the_fallback_is_capped_at_three_and_limit_does_not_widen_it(self):
        self.populate(6)

        out, err = self.run_find("qqqqqq", "--limit", "10")

        self.assertIn("No strong match. Closest 3:", out)
        self.assertEqual(len(self.result_lines(out)), 3)

    def test_a_smaller_candidate_set_makes_the_header_name_its_real_count(self):
        self.populate(2)

        out, err = self.run_find("qqqqqq")

        self.assertIn("No strong match. Closest 2:", out)
        self.assertNotIn("Closest 3", out)
        self.assertEqual(len(self.result_lines(out)), 2)

    def test_the_fallback_never_reaches_past_a_tag_filter_to_fill_three_slots(self):
        """Filters are hard; the threshold is soft. One tagged place out of
        five means at most one guess, not three."""
        self.populate(4)
        self.make_place("Blue Bottle", tags=["coffee"])

        out, err = self.run_find("qqqqqq", "--tag", "Coffee")

        self.assertIn("Blue Bottle", out)
        self.assertIn("No strong match. Closest 1:", out)
        self.assertEqual(len(self.result_lines(out)), 1)

    def test_the_fallback_never_reaches_past_a_status_filter(self):
        self.populate(4, status=Place.Status.VISITED)
        self.make_place("Blue Bottle", status=Place.Status.WISHLIST)

        out, err = self.run_find("qqqqqq", "--status", Place.Status.WISHLIST)

        self.assertEqual(len(self.result_lines(out)), 1)
        self.assertIn("Blue Bottle", out)

    def test_the_fallback_never_reaches_past_a_neighborhood_filter(self):
        self.populate(4, neighborhood="SoMa")
        self.make_place("Blue Bottle", neighborhood="Mission District")

        out, err = self.run_find("qqqqqq", "--neighborhood", "mission")

        self.assertEqual(len(self.result_lines(out)), 1)
        self.assertIn("Blue Bottle", out)


class FindCommandEmptyJournalTests(FindCommandTestCase):
    """Zero places is a different nothing from zero survivors."""

    def test_an_empty_journal_says_so_and_points_at_add(self):
        out, err = self.run_find("anything")

        self.assertIn("empty", out)
        self.assertIn("add", out)
        self.assertNotIn("No strong match", out)

    def test_the_empty_journal_message_is_not_the_dead_filter_message(self):
        out, err = self.run_find("anything")

        self.assertNotIn("filters", out)

    def test_an_empty_journal_is_not_an_error(self):
        self.run_find("anything", "--tag", "coffee", "--limit", "3")


class FindCommandResultLineTests(FindCommandTestCase):
    """One place, one readable line."""

    def test_a_line_carries_name_neighborhood_status_rating_and_note(self):
        self.make_place(
            "Blue Bottle",
            neighborhood="Mission",
            note="good wifi before ten",
            rating=4,
            status=Place.Status.VISITED,
            tags=["coffee"],
        )

        out, err = self.run_find("blue bottle")

        line = self.result_lines(out)[0]
        for fragment in ("Blue Bottle", "Mission", Place.Status.VISITED.value, "4"):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, line)
        self.assertIn("good wifi", line)

    def test_a_place_with_no_note_no_rating_and_no_neighborhood_prints_no_none(self):
        self.make_place("Tartine")

        out, err = self.run_find("tartine")

        self.assertIn("Tartine", out)
        self.assertNotIn("None", out)
        self.assertNotIn("null", out)
        self.assertEqual(len(self.result_lines(out)), 1)

    def test_a_wishlist_place_with_a_note_but_no_rating_prints_no_none(self):
        self.make_place("Tartine", neighborhood="Mission", note="the morning bun")

        out, err = self.run_find("tartine")

        self.assertNotIn("None", out)

    def test_a_long_note_is_truncated_head_first_with_a_visible_marker(self):
        self.make_place(
            "Tartine",
            note="the morning bun is the reason to come "
            + "and more words " * 40
            + "TAIL OF THE NOTE",
        )

        out, err = self.run_find("tartine")

        self.assertIn("the morning bun", out)
        self.assertNotIn("TAIL OF THE NOTE", out)
        self.assertTrue(
            "…" in out or "..." in out, "a truncated note must admit it was cut"
        )
        self.assertEqual(len(self.result_lines(out)), 1)

    def test_a_note_with_newlines_still_prints_as_one_line(self):
        self.make_place("Tartine", note="line one\nline two")

        out, err = self.run_find("tartine")

        self.assertEqual(len(self.lines(out)), 1)
        self.assertIn("line one", out)
        self.assertIn("line two", out)

    def test_a_note_of_only_whitespace_prints_no_trailing_separator_junk(self):
        self.make_place("Tartine", note="   \n  ")

        out, err = self.run_find("tartine")

        self.assertIn("Tartine", out)
        self.assertNotIn("None", out)


class FindCommandNonAsciiTests(FindCommandTestCase):
    """Accents survive the round trip and nothing raises."""

    def test_a_non_ascii_query_finds_the_place_with_its_accents_intact(self):
        self.make_place("Café Réveille", neighborhood="Hayes Valley")

        out, err = self.run_find("café")

        self.assertIn("Café Réveille", out)

    def test_a_non_ascii_neighborhood_filter_runs_clean(self):
        self.make_place("Taco Spot", neighborhood="Barrio Logán")
        self.make_place("Blue Bottle", neighborhood="Mission")

        out, err = self.run_find("taco", "--neighborhood", "logán")

        self.assertIn("Taco Spot", out)
        self.assertNotIn("Blue Bottle", out)

    def test_a_non_ascii_tag_filter_runs_clean(self):
        self.make_place("Taco Spot", tags=["mañana"])

        out, err = self.run_find("taco", "--tag", "Mañana")

        self.assertIn("Taco Spot", out)


class FindCommandQueryCountTests(FindCommandTestCase):
    """The ranker reads every candidate's tags.

    ``Place.tags`` is a related manager, so on un-prefetched rows that is one
    query per candidate -- the ranker is pure in effect only when its caller
    prefetches. This pins the caller's half of that bargain.
    """

    def populate(self, count, offset=0):
        for index in range(offset, offset + count):
            self.make_place(f"Coffee Number {index}", tags=["coffee", "wifi"])

    def queries_for_a_search(self):
        with CaptureQueriesContext(connection) as captured:
            self.run_find("coffee")
        return len(captured)

    def test_the_query_count_does_not_grow_with_the_number_of_candidates(self):
        self.populate(3)
        few = self.queries_for_a_search()

        self.populate(15, offset=3)
        many = self.queries_for_a_search()

        self.assertEqual(few, many)
        self.assertLess(many, Place.objects.count())

    def test_the_query_count_does_not_grow_when_filters_are_applied(self):
        self.populate(12)

        with CaptureQueriesContext(connection) as captured:
            self.run_find("coffee", "--tag", "Coffee", "--status", "wishlist")

        self.assertLess(len(captured), Place.objects.count())


# ---------------------------------------------------------------------------
# The wishlist loop: `places/lookup.py`, `todo` and `visit` (issue #7)
#
# `visit` is the only thing in the tool that ever writes `last_visited_at`.
# Name resolution is shared between the two commands and tested on its own
# first, without a command in the way.
# ---------------------------------------------------------------------------


def _lookup_module():
    """The shared name-resolution module."""
    from places import lookup as lookup_module

    return lookup_module


def _visit_module():
    """The ``visit`` command module, imported the way Django finds it."""
    from places.management.commands import visit as visit_module

    return visit_module


def _todo_module():
    """The ``todo`` command module, imported the way Django finds it."""
    from places.management.commands import todo as todo_module

    return todo_module


class LookupModuleContractTests(SimpleTestCase):
    """What the shared module must be, before what it must do.

    Issue #7 asks for one statement of the resolution rule that both commands
    import, importable without a command and independent of the ranker.
    """

    def test_the_helper_is_importable_without_touching_a_command(self):
        lookup = _lookup_module()

        for name in ("resolve_place", "normalize_place_name", "describe_place"):
            with self.subTest(name=name):
                self.assertTrue(callable(getattr(lookup, name)))

    def test_the_helper_is_not_called_search(self):
        """``places/search.py`` is the ranking module (issue #5); the resolver
        may not squat on that name."""
        self.assertNotEqual(_lookup_module().__name__, "places.search")

    def test_the_helper_does_not_import_the_ranker_or_any_command(self):
        for module in _imported_modules(_lookup_module()):
            with self.subTest(module=module):
                self.assertFalse(
                    module == "places.search"
                    or module.startswith("rapidfuzz")
                    or module.startswith("places.management"),
                    f"the resolver must stay independent but imports {module}",
                )

    def test_the_helper_calls_nothing_from_the_ranker(self):
        self.assertNotIn("rank_places", _called_names(_lookup_module()))

    def test_both_commands_import_the_shared_module_rather_than_copying_it(self):
        """Imports and definitions, not text.

        Copying is exactly what this test exists to prevent, and a pasted copy
        leaves the docstring's "comes from ``places.lookup``" in place -- which
        is how a text assertion was defeated by the very mutation it guarded
        against. So: the name has to arrive through an ``import``, and the
        command must not define one of its own.
        """
        for module, borrowed in (
            (_visit_module(), "resolve_place"),
            (_todo_module(), "describe_place"),
        ):
            with self.subTest(module=module.__name__):
                self.assertIn("places.lookup", _imported_modules(module))
                self.assertIn(borrowed, _imported_names(module, "places.lookup"))
                self.assertNotIn(borrowed, _defined_functions(module))

    def test_neither_command_reimplements_name_matching(self):
        """``visit`` resolves through the helper; the lookups themselves must
        not be spelled out a second time in the command."""
        lookups = _query_lookups(_visit_module())

        self.assertIn("resolve_place", _called_names(_visit_module()))
        self.assertNotIn("iexact", lookups)
        self.assertNotIn("icontains", lookups)


class ResolvePlaceTests(TestCase):
    """The rule itself, exercised directly -- no command, no ``call_command``."""

    def setUp(self):
        self.lookup = _lookup_module()

    def make_place(self, name, tags=(), **fields):
        place = Place.objects.create(name=name, **fields)
        for tag_name in tags:
            place.tags.add(Tag.objects.get_or_create(name=tag_name)[0])
        return place

    def resolve(self, name):
        return self.lookup.resolve_place(name)

    def test_an_exact_name_resolves(self):
        place = self.make_place("Blue Bottle")

        self.assertEqual(self.resolve("Blue Bottle").pk, place.pk)

    def test_resolution_is_case_insensitive(self):
        place = self.make_place("Blue Bottle")

        for typed in ("blue bottle", "BLUE BOTTLE", "bLuE bOtTlE"):
            with self.subTest(typed=typed):
                self.assertEqual(self.resolve(typed).pk, place.pk)

    def test_surrounding_whitespace_on_the_argument_is_ignored(self):
        place = self.make_place("Blue Bottle")

        self.assertEqual(self.resolve("  Blue Bottle  ").pk, place.pk)

    def test_a_substring_resolves_when_no_whole_name_matches(self):
        place = self.make_place("Blue Bottle Coffee")

        self.assertEqual(self.resolve("bottle").pk, place.pk)

    def test_the_whole_name_wins_over_a_longer_place_containing_it(self):
        """The case the two stages exist for: ``Blue Bottle`` is not ambiguous
        just because ``Blue Bottle Coffee`` is also stored."""
        exact = self.make_place("Blue Bottle")
        self.make_place("Blue Bottle Coffee")

        self.assertEqual(self.resolve("Blue Bottle").pk, exact.pk)

    def test_the_whole_name_wins_case_insensitively_too(self):
        exact = self.make_place("Blue Bottle")
        self.make_place("Blue Bottle Coffee")

        self.assertEqual(self.resolve("blue bottle").pk, exact.pk)

    def test_a_substring_matching_two_places_is_ambiguous(self):
        self.make_place("Blue Bottle")
        self.make_place("Blue Bottle Coffee")

        with self.assertRaises(self.lookup.AmbiguousPlaceName) as caught:
            self.resolve("Blue")

        self.assertIn("Blue Bottle", str(caught.exception))
        self.assertIn("Blue Bottle Coffee", str(caught.exception))

    def test_two_names_differing_only_by_case_are_ambiguous_at_stage_one(self):
        """Neither is more exact than the other, so the resolver must not pick."""
        self.make_place("Blue Bottle", neighborhood="Mission")
        self.make_place("blue bottle", neighborhood="SoMa")

        with self.assertRaises(self.lookup.AmbiguousPlaceName) as caught:
            self.resolve("Blue Bottle")

        message = str(caught.exception)
        self.assertIn("Mission", message)
        self.assertIn("SoMa", message)

    def test_an_ambiguous_error_carries_every_candidate(self):
        self.make_place("Blue Bottle")
        self.make_place("Blue Bottle Coffee")

        with self.assertRaises(self.lookup.AmbiguousPlaceName) as caught:
            self.resolve("Blue")

        self.assertEqual(len(caught.exception.candidates), 2)

    def test_the_candidate_list_prints_one_place_per_line_with_status(self):
        self.make_place("Blue Bottle", neighborhood="Mission")
        self.make_place(
            "Blue Bottle Coffee",
            neighborhood="SoMa",
            status=Place.Status.VISITED,
        )

        with self.assertRaises(self.lookup.AmbiguousPlaceName) as caught:
            self.resolve("Blue")

        candidate_lines = [
            line
            for line in str(caught.exception).splitlines()
            if "Blue Bottle" in line
        ]
        self.assertEqual(len(candidate_lines), 2)
        self.assertIn(Place.Status.WISHLIST.value, candidate_lines[0])
        self.assertIn(Place.Status.VISITED.value, candidate_lines[1])

    def test_the_candidate_order_is_stable_across_calls(self):
        for name in ("Zoo Cafe", "abc Cafe", "Mid Cafe"):
            self.make_place(name)

        orders = []
        for _ in range(3):
            with self.assertRaises(self.lookup.AmbiguousPlaceName) as caught:
                self.resolve("Cafe")
            orders.append([place.name for place in caught.exception.candidates])

        self.assertEqual(orders[0], orders[1])
        self.assertEqual(orders[1], orders[2])
        self.assertEqual(orders[0], ["abc Cafe", "Mid Cafe", "Zoo Cafe"])

    def test_a_name_matching_nothing_raises_and_names_the_string(self):
        self.make_place("Blue Bottle")

        with self.assertRaises(self.lookup.PlaceNotFound) as caught:
            self.resolve("Nowhere Cafe")

        self.assertIn("Nowhere Cafe", str(caught.exception))

    def test_the_not_found_message_points_at_a_next_step(self):
        with self.assertRaises(self.lookup.PlaceNotFound) as caught:
            self.resolve("Nowhere Cafe")

        message = str(caught.exception)
        self.assertTrue(
            "add" in message or "find" in message,
            f"expected a suggested next step in: {message!r}",
        )

    def test_an_empty_or_whitespace_name_is_rejected_on_its_own(self):
        self.make_place("Blue Bottle")

        for typed in ("", "   ", "\t\n", None):
            with self.subTest(typed=repr(typed)):
                with self.assertRaises(self.lookup.EmptyPlaceName):
                    self.resolve(typed)

    def test_an_empty_name_does_not_fall_through_to_matching_everything(self):
        """``""`` is a substring of every name; stage 2 must never see it."""
        self.make_place("Blue Bottle")
        self.make_place("Tartine")

        with self.assertRaises(self.lookup.PlaceNameError) as caught:
            self.resolve("")

        self.assertEqual(caught.exception.candidates, [])

    def test_resolution_finds_an_already_visited_place(self):
        """Every place is in scope, not just the wishlist, so a place can be
        visited a second time."""
        place = self.make_place("Blue Bottle", status=Place.Status.VISITED)

        self.assertEqual(self.resolve("Blue Bottle").pk, place.pk)

    def test_resolution_matches_on_the_name_and_nothing_else(self):
        self.make_place(
            "Blue Bottle",
            neighborhood="Mission",
            note="the best cortado in town",
            tags=["coffee"],
        )

        for typed in ("Mission", "cortado", "coffee"):
            with self.subTest(typed=typed):
                with self.assertRaises(self.lookup.PlaceNotFound):
                    self.resolve(typed)

    def test_resolution_does_no_fuzzy_matching(self):
        """A typo is a miss here. Typo tolerance belongs to ``find``."""
        self.make_place("Blue Bottle")

        for typo in ("Blu Bottel", "Bleu Bottle", "Blue Botle"):
            with self.subTest(typo=typo):
                with self.assertRaises(self.lookup.PlaceNotFound):
                    self.resolve(typo)

    def test_resolution_writes_nothing(self):
        place = self.make_place("Blue Bottle")
        before = Place.objects.values_list("name", "status", "last_visited_at")[0]

        self.resolve("Blue Bottle")

        self.assertEqual(
            Place.objects.values_list("name", "status", "last_visited_at")[0], before
        )
        self.assertIsNone(Place.objects.get(pk=place.pk).last_visited_at)

    def test_a_narrower_queryset_can_be_passed_in(self):
        """The default is every place; the argument exists so a caller with a
        reason can narrow it, and ``visit`` deliberately has no such reason."""
        self.make_place("Blue Bottle", status=Place.Status.VISITED)

        with self.assertRaises(self.lookup.PlaceNotFound):
            self.lookup.resolve_place(
                "Blue Bottle",
                Place.objects.filter(status=Place.Status.WISHLIST),
            )


class DescribePlaceTests(TestCase):
    """The one-line description both commands print."""

    def setUp(self):
        self.lookup = _lookup_module()

    def test_a_bare_name_renders_as_a_bare_name(self):
        place = Place.objects.create(name="Tartine")

        line = self.lookup.describe_place(place, status=False)

        self.assertEqual(line, "Tartine")

    def test_nothing_missing_renders_as_none_or_an_empty_bracket(self):
        place = Place.objects.create(name="Tartine")

        line = self.lookup.describe_place(place, status=False, tags=[])

        self.assertNotIn("None", line)
        self.assertNotIn("[]", line)
        self.assertNotIn("()", line)
        self.assertFalse(line.endswith("-"))

    def test_present_fields_all_appear(self):
        place = Place.objects.create(
            name="Blue Bottle", neighborhood="Mission", note="good wifi"
        )

        line = self.lookup.describe_place(place, tags=["coffee"])

        for expected in ("Blue Bottle", "Mission", "good wifi", "coffee"):
            with self.subTest(expected=expected):
                self.assertIn(expected, line)
        self.assertIn(Place.Status.WISHLIST.value, line)

    def test_a_long_note_is_cut_to_one_bounded_line(self):
        place = Place.objects.create(name="Tartine", note="word " * 200)

        line = self.lookup.describe_place(place, status=False)

        self.assertLess(len(line), 200)
        self.assertIn(self.lookup.TRUNCATION_MARKER, line)

    def test_a_multi_line_note_stays_on_one_line(self):
        place = Place.objects.create(name="Tartine", note="first\nsecond\nthird")

        line = self.lookup.describe_place(place, status=False)

        self.assertNotIn("\n", line)


# ---------------------------------------------------------------------------
# `visit`
# ---------------------------------------------------------------------------


class VisitCommandDiscoveryTests(SimpleTestCase):
    """The command is discoverable and takes the documented flags."""

    def parser(self):
        return load_command_class("places", "visit").create_parser("manage.py", "visit")

    def test_visit_is_registered_as_a_command_of_the_places_app(self):
        self.assertEqual(get_commands().get("visit"), "places")

    def test_help_documents_the_positional_name_and_every_flag(self):
        help_text = self.parser().format_help()

        self.assertIn("name", help_text)
        for flag in ("--note", "--rating"):
            with self.subTest(flag=flag):
                self.assertIn(flag, help_text)

    def test_note_and_rating_default_to_none_so_omitting_them_is_detectable(self):
        """``--note ""`` clears the note; omitting ``--note`` leaves it alone.
        A default of ``""`` would make those two indistinguishable."""
        parsed = self.parser().parse_args(["Blue Bottle"])

        self.assertIsNone(parsed.note)
        self.assertIsNone(parsed.rating)

    def test_the_rating_flag_takes_a_whole_number(self):
        self.assertEqual(
            self.parser().parse_args(["Blue Bottle", "--rating", "4"]).rating, 4
        )

    def test_there_is_no_status_flag_because_visit_always_means_visited(self):
        help_text = self.parser().format_help()

        self.assertNotIn("--status", help_text)
        self.assertNotIn("status", {a.dest for a in self.parser()._actions})


class VisitCommandSourceRuleTests(SimpleTestCase):
    """Rules issue #7 states as a grep over ``visit.py``.

    Each describes a mechanism the command must reuse or refuse rather than a
    value it prints, and each would survive every behavioral test right up
    until the day it mattered.
    """

    def test_visit_stamps_with_djangos_timezone_aware_now(self):
        """``USE_TZ = True``, so a naive ``datetime.now()`` is a defect."""
        imported = _imported_modules(_visit_module())

        self.assertIn("django.utils", imported)
        self.assertNotIn("datetime", imported)
        self.assertIn("timezone.now", _dotted_calls(_visit_module()))

    def test_visit_imports_nothing_from_the_ranker(self):
        for module in _imported_modules(_visit_module()):
            with self.subTest(module=module):
                self.assertFalse(
                    module == "places.search" or module.startswith("rapidfuzz"),
                    f"visit must not depend on the ranker but imports {module}",
                )
        self.assertNotIn("rank_places", _called_names(_visit_module()))

    def test_visit_never_prompts(self):
        """A prompt would hang under ``call_command`` and in any script."""
        called = _called_names(_visit_module())

        self.assertNotIn("input", called)
        self.assertNotIn("getpass", _imported_modules(_visit_module()))

    def test_visit_refers_to_the_status_choices_class_not_to_string_literals(self):
        """Exact literals, so the word "visited" inside a printed sentence --
        which ``visit`` says a great deal -- is not mistaken for a status."""
        self.assertNotIn(
            Place.Status.VISITED.value, _string_constants(_visit_module())
        )
        self.assertIn("Place.Status", _attribute_chains(_visit_module()))


class VisitCommandTestCase(TestCase):
    """Shared plumbing: run ``visit`` through ``call_command``, per
    ``_docs/testing-guidelines.md``. Nothing here shells out."""

    def make_place(self, name, tags=(), **fields):
        place = Place.objects.create(name=name, **fields)
        for tag_name in tags:
            place.tags.add(Tag.objects.get_or_create(name=tag_name)[0])
        return place

    def run_visit(self, *args):
        out, err = StringIO(), StringIO()
        call_command("visit", *args, stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def run_visit_from_command_line(self, *args):
        """Drive ``visit`` the way ``manage.py`` does, so exit codes are real.

        See ``AddCommandTestCase.run_add_from_command_line``: setting
        ``_called_from_command_line`` is what lets argparse's own exit status
        surface without shelling out to ``manage.py``.
        """
        command = load_command_class("places", "visit")
        command._called_from_command_line = True
        out, err = StringIO(), StringIO()
        call_command(command, *args, stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def snapshot(self):
        """Every field ``visit`` could write, for every place in the journal."""
        return sorted(
            Place.objects.values_list(
                "pk", "name", "status", "last_visited_at", "note", "rating"
            )
        )

    def assert_rejected(self, *args):
        """Run a ``visit`` that must fail, and prove nothing was written.

        Returns the ``CommandError`` -- rendered by ``manage.py`` as a one-line
        message on stderr with a non-zero exit -- after checking that no
        place's status, timestamp, note or rating moved.
        """
        before = self.snapshot()
        out = StringIO()
        with self.assertRaises(CommandError) as caught:
            call_command("visit", *args, stdout=out)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(out.getvalue(), "")
        return caught.exception


class VisitCommandFlipTests(VisitCommandTestCase):
    """The plain flip: wishlist to visited, stamped."""

    def test_a_wishlist_place_becomes_visited(self):
        place = self.make_place("Blue Bottle")

        self.run_visit("Blue Bottle")

        place.refresh_from_db()
        self.assertEqual(place.status, Place.Status.VISITED)

    def test_the_visit_is_stamped_with_an_aware_time_close_to_now(self):
        place = self.make_place("Blue Bottle")
        before = django_timezone.now()

        self.run_visit("Blue Bottle")

        place.refresh_from_db()
        self.assertIsNotNone(place.last_visited_at)
        self.assertIsNotNone(place.last_visited_at.tzinfo)
        self.assertGreaterEqual(place.last_visited_at, before)
        self.assertLess(
            (django_timezone.now() - place.last_visited_at).total_seconds(), 10
        )

    def test_stamping_raises_no_naive_datetime_warning(self):
        """A naive datetime under ``USE_TZ = True`` is a ``RuntimeWarning``."""
        self.make_place("Blue Bottle")

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self.run_visit("Blue Bottle")

        self.assertEqual(
            [w for w in caught if issubclass(w.category, RuntimeWarning)], []
        )

    def test_the_confirmation_names_the_place_and_its_new_status(self):
        self.make_place("Blue Bottle", neighborhood="Mission")

        out, err = self.run_visit("Blue Bottle")

        self.assertIn("Blue Bottle", out)
        self.assertIn(Place.Status.VISITED.value, out)

    def test_a_successful_visit_writes_nothing_to_stderr(self):
        self.make_place("Blue Bottle")

        out, err = self.run_visit("Blue Bottle")

        self.assertEqual(err, "")

    def test_no_flags_is_the_normal_case_and_leaves_note_and_rating_alone(self):
        place = self.make_place("Blue Bottle", note="heard good things")

        self.run_visit("Blue Bottle")

        place.refresh_from_db()
        self.assertEqual(place.status, Place.Status.VISITED)
        self.assertIsNotNone(place.last_visited_at)
        self.assertEqual(place.note, "heard good things")
        self.assertIsNone(place.rating)

    def test_status_flips_even_when_only_a_note_is_given(self):
        """Unlike ``add``, ``visit`` never infers status from a rating."""
        place = self.make_place("Blue Bottle")

        self.run_visit("Blue Bottle", "--note", "great cortado")

        place.refresh_from_db()
        self.assertEqual(place.status, Place.Status.VISITED)
        self.assertIsNotNone(place.last_visited_at)

    def test_visit_leaves_every_other_field_untouched(self):
        place = self.make_place(
            "Blue Bottle",
            tags=["coffee", "wifi"],
            neighborhood="Mission",
            address="315 Linden St",
        )
        created_at = place.created_at

        self.run_visit("Blue Bottle", "--note", "great cortado", "--rating", "4")

        place.refresh_from_db()
        self.assertEqual(place.name, "Blue Bottle")
        self.assertEqual(place.neighborhood, "Mission")
        self.assertEqual(place.address, "315 Linden St")
        self.assertEqual(place.created_at, created_at)
        self.assertEqual(
            sorted(tag.name for tag in place.tags.all()), ["coffee", "wifi"]
        )

    def test_the_write_names_the_fields_it_touches_rather_than_saving_the_row(self):
        """A narrow ``update_fields`` write, pinned where it can actually fail.

        The test above passes under a bare ``place.save()`` too, because
        nothing else has changed on the in-memory instance for a full save to
        carry into the database. So dirty a second field on the instance the
        command is about to save -- exactly what a future bug, or a model with
        a ``save()`` of its own, would do -- and check the database refused it.
        """
        place = self.make_place(
            "Blue Bottle", neighborhood="Mission", note="heard good things"
        )
        resolve_place = _lookup_module().resolve_place

        def resolve_and_dirty(*args, **kwargs):
            resolved = resolve_place(*args, **kwargs)
            resolved.name = "Somewhere Else"
            resolved.neighborhood = "Nowhere"
            return resolved

        with mock.patch(
            "places.management.commands.visit.resolve_place",
            side_effect=resolve_and_dirty,
        ):
            self.run_visit("Blue Bottle", "--note", "great cortado")

        place.refresh_from_db()
        self.assertEqual(place.name, "Blue Bottle")
        self.assertEqual(place.neighborhood, "Mission")
        # ...while the fields `visit` does own were written.
        self.assertEqual(place.status, Place.Status.VISITED)
        self.assertEqual(place.note, "great cortado")
        self.assertIsNotNone(place.last_visited_at)


class VisitCommandResolutionTests(VisitCommandTestCase):
    """The resolution rule, seen from the command."""

    def test_a_place_resolves_whatever_case_the_name_is_typed_in(self):
        for typed in ("blue bottle", "BLUE BOTTLE"):
            with self.subTest(typed=typed):
                place = self.make_place("Blue Bottle")

                self.run_visit(typed)

                place.refresh_from_db()
                self.assertEqual(place.status, Place.Status.VISITED)
                place.delete()

    def test_surrounding_whitespace_resolves_the_same_place(self):
        place = self.make_place("Blue Bottle")

        self.run_visit("  Blue Bottle  ")

        place.refresh_from_db()
        self.assertEqual(place.status, Place.Status.VISITED)

    def test_the_whole_name_beats_a_longer_place_containing_it(self):
        exact = self.make_place("Blue Bottle")
        longer = self.make_place("Blue Bottle Coffee")

        self.run_visit("Blue Bottle")

        exact.refresh_from_db()
        longer.refresh_from_db()
        self.assertEqual(exact.status, Place.Status.VISITED)
        self.assertEqual(longer.status, Place.Status.WISHLIST)
        self.assertIsNone(longer.last_visited_at)

    def test_a_substring_resolves_when_nothing_matches_the_whole_name(self):
        place = self.make_place("Blue Bottle Coffee")

        self.run_visit("bottle coffee")

        place.refresh_from_db()
        self.assertEqual(place.status, Place.Status.VISITED)

    def test_an_unknown_name_is_rejected_and_names_the_string(self):
        self.make_place("Blue Bottle")

        error = self.assert_rejected("Nowhere Cafe")

        self.assertIn("Nowhere Cafe", str(error))

    def test_an_unknown_name_exits_non_zero(self):
        error = self.assert_rejected("Nowhere Cafe")

        self.assertNotEqual(error.returncode, 0)

    def test_an_ambiguous_name_lists_every_candidate_and_writes_nothing(self):
        self.make_place("Blue Bottle", neighborhood="Mission")
        self.make_place("Blue Bottle Coffee", neighborhood="SoMa")

        error = self.assert_rejected("Blue")

        message = str(error)
        self.assertIn("Blue Bottle", message)
        self.assertIn("Blue Bottle Coffee", message)
        self.assertIn("Mission", message)
        self.assertIn("SoMa", message)
        self.assertEqual(
            list(Place.objects.values_list("status", flat=True)),
            [Place.Status.WISHLIST, Place.Status.WISHLIST],
        )

    def test_an_ambiguous_name_exits_non_zero(self):
        self.make_place("Blue Bottle")
        self.make_place("Blue Bottle Coffee")

        error = self.assert_rejected("Blue")

        self.assertNotEqual(error.returncode, 0)

    def test_names_differing_only_by_case_are_reported_not_guessed_between(self):
        self.make_place("Blue Bottle", neighborhood="Mission")
        self.make_place("blue bottle", neighborhood="SoMa")

        error = self.assert_rejected("BLUE BOTTLE")

        self.assertIn("Mission", str(error))
        self.assertIn("SoMa", str(error))

    def test_an_empty_name_is_rejected_and_visits_nothing(self):
        self.make_place("Blue Bottle")
        self.make_place("Tartine")

        for typed in ("", "   "):
            with self.subTest(typed=repr(typed)):
                self.assert_rejected(typed)

                self.assertEqual(
                    Place.objects.filter(status=Place.Status.VISITED).count(), 0
                )
                self.assertEqual(
                    Place.objects.filter(last_visited_at__isnull=False).count(), 0
                )

    def test_a_typo_is_a_miss_rather_than_a_guess(self):
        self.make_place("Blue Bottle")

        self.assert_rejected("Blu Bottel")

    def test_an_already_visited_place_is_still_resolvable(self):
        place = self.make_place("Blue Bottle", status=Place.Status.VISITED)

        self.run_visit("Blue Bottle")

        place.refresh_from_db()
        self.assertIsNotNone(place.last_visited_at)


class VisitCommandNoteAndRatingTests(VisitCommandTestCase):
    """``--note`` and ``--rating`` replace, and only when passed."""

    def test_a_note_is_stored(self):
        place = self.make_place("Blue Bottle")

        self.run_visit("Blue Bottle", "--note", "great cortado")

        place.refresh_from_db()
        self.assertEqual(place.note, "great cortado")

    def test_a_note_replaces_rather_than_appends(self):
        place = self.make_place("Blue Bottle", note="heard good things")

        self.run_visit("Blue Bottle", "--note", "great cortado")

        place.refresh_from_db()
        self.assertEqual(place.note, "great cortado")
        self.assertNotIn("heard good things", place.note)

    def test_an_explicit_empty_note_clears_the_note(self):
        place = self.make_place("Blue Bottle", note="heard good things")

        self.run_visit("Blue Bottle", "--note", "")

        place.refresh_from_db()
        self.assertEqual(place.note, "")

    def test_omitting_the_note_flag_leaves_the_note_alone(self):
        place = self.make_place("Blue Bottle", note="heard good things")

        self.run_visit("Blue Bottle", "--rating", "4")

        place.refresh_from_db()
        self.assertEqual(place.note, "heard good things")

    def test_surrounding_whitespace_is_stripped_from_a_note(self):
        """A note is stored the way ``add`` stores one: stripped."""
        place = self.make_place("Blue Bottle")

        self.run_visit("Blue Bottle", "--note", "  great cortado  ")

        place.refresh_from_db()
        self.assertEqual(place.note, "great cortado")

    def test_a_whitespace_only_note_clears_the_note_like_an_empty_one(self):
        """Deliberate, and the same rule ``add`` follows: a note of nothing but
        spaces is a note of nothing."""
        place = self.make_place("Blue Bottle", note="heard good things")

        self.run_visit("Blue Bottle", "--note", "   ")

        place.refresh_from_db()
        self.assertEqual(place.note, "")

    def test_a_rating_is_stored_and_replaces_any_previous_one(self):
        place = self.make_place("Blue Bottle", rating=2)

        self.run_visit("Blue Bottle", "--rating", "4")

        place.refresh_from_db()
        self.assertEqual(place.rating, 4)

    def test_omitting_the_rating_flag_leaves_the_rating_alone(self):
        place = self.make_place("Blue Bottle", rating=2)

        self.run_visit("Blue Bottle", "--note", "still good")

        place.refresh_from_db()
        self.assertEqual(place.rating, 2)

    def test_the_boundaries_of_the_rating_range_are_accepted(self):
        for rating in ("1", "5"):
            with self.subTest(rating=rating):
                place = self.make_place(f"Place {rating}")

                self.run_visit(f"Place {rating}", "--rating", rating)

                place.refresh_from_db()
                self.assertEqual(place.rating, int(rating))


class VisitCommandRepeatVisitTests(VisitCommandTestCase):
    """Visiting a visited place again is the point of ``last_visited_at``."""

    OLD_VISIT = datetime(2024, 3, 9, 18, 30, tzinfo=timezone.utc)

    def visited_place(self, **fields):
        return self.make_place(
            "Blue Bottle",
            status=Place.Status.VISITED,
            last_visited_at=self.OLD_VISIT,
            **fields,
        )

    def test_a_repeat_visit_is_allowed_and_does_not_raise(self):
        self.visited_place()

        out, err = self.run_visit("Blue Bottle")

        self.assertIn("Blue Bottle", out)

    def test_a_repeat_visit_re_stamps_a_strictly_later_time(self):
        place = self.visited_place()

        self.run_visit("Blue Bottle")

        place.refresh_from_db()
        self.assertGreater(place.last_visited_at, self.OLD_VISIT)

    def test_a_repeat_visit_leaves_the_status_visited(self):
        place = self.visited_place()

        self.run_visit("Blue Bottle")

        place.refresh_from_db()
        self.assertEqual(place.status, Place.Status.VISITED)

    def test_a_repeat_visit_without_a_note_keeps_the_existing_note(self):
        place = self.visited_place(note="first time, great cortado")

        self.run_visit("Blue Bottle")

        place.refresh_from_db()
        self.assertEqual(place.note, "first time, great cortado")

    def test_a_repeat_visit_with_a_note_overwrites_rather_than_appends(self):
        place = self.visited_place(note="first time, great cortado")

        self.run_visit("Blue Bottle", "--note", "second time, still good")

        place.refresh_from_db()
        self.assertEqual(place.note, "second time, still good")
        self.assertNotIn("first time", place.note)

    def test_the_output_says_the_place_was_already_visited(self):
        """Overwriting a note must not be a silent surprise."""
        self.visited_place(note="first time, great cortado")

        out, err = self.run_visit("Blue Bottle", "--note", "second time")

        self.assertIn("already visited", out.lower())

    def test_a_first_visit_does_not_claim_the_place_was_already_visited(self):
        self.make_place("Tartine")

        out, err = self.run_visit("Tartine")

        self.assertNotIn("already visited", out.lower())


class VisitCommandInvalidInputTests(VisitCommandTestCase):
    """Nothing is written, and the exit code is non-zero."""

    def test_a_rating_outside_one_to_five_names_the_allowed_range(self):
        self.make_place("Blue Bottle")

        for rating in ("0", "6"):
            with self.subTest(rating=rating):
                error = self.assert_rejected("Blue Bottle", "--rating", rating)

                self.assertIn("1", str(error))
                self.assertIn("5", str(error))

    def test_a_bad_rating_is_caught_before_the_status_is_flipped(self):
        """Validation runs before the write, so a rejected visit leaves the
        place on the wishlist with no timestamp."""
        place = self.make_place("Blue Bottle", note="heard good things")

        self.assert_rejected("Blue Bottle", "--rating", "9", "--note", "great")

        place.refresh_from_db()
        self.assertEqual(place.status, Place.Status.WISHLIST)
        self.assertIsNone(place.last_visited_at)
        self.assertEqual(place.note, "heard good things")
        self.assertIsNone(place.rating)

    def test_a_bad_rating_leaves_an_already_visited_place_exactly_as_it_was(self):
        old_visit = datetime(2024, 3, 9, 18, 30, tzinfo=timezone.utc)
        place = self.make_place(
            "Blue Bottle",
            status=Place.Status.VISITED,
            last_visited_at=old_visit,
            rating=3,
        )

        self.assert_rejected("Blue Bottle", "--rating", "0")

        place.refresh_from_db()
        self.assertEqual(place.last_visited_at, old_visit)
        self.assertEqual(place.rating, 3)

    def test_argparse_rejects_a_non_integer_rating_before_the_command_body_runs(self):
        self.make_place("Blue Bottle")

        for rating in ("abc", "4.5"):
            with self.subTest(rating=rating):
                with redirect_stderr(StringIO()):
                    with self.assertRaises(SystemExit) as caught:
                        self.run_visit_from_command_line(
                            "Blue Bottle", "--rating", rating
                        )

                self.assertEqual(caught.exception.code, 2)
                self.assertEqual(
                    Place.objects.filter(status=Place.Status.VISITED).count(), 0
                )

    def test_a_non_integer_rating_raises_rather_than_returning_success(self):
        """Through ``call_command`` the same usage error is a ``CommandError``."""
        self.make_place("Blue Bottle")

        with self.assertRaises(CommandError):
            self.run_visit("Blue Bottle", "--rating", "abc")

        self.assertIsNone(Place.objects.get(name="Blue Bottle").last_visited_at)


# ---------------------------------------------------------------------------
# `todo`
# ---------------------------------------------------------------------------


class TodoCommandDiscoveryTests(SimpleTestCase):
    """The command is discoverable and takes the documented flags."""

    def parser(self):
        return load_command_class("places", "todo").create_parser("manage.py", "todo")

    def test_todo_is_registered_as_a_command_of_the_places_app(self):
        self.assertEqual(get_commands().get("todo"), "places")

    def test_help_documents_both_filters(self):
        help_text = self.parser().format_help()

        for flag in ("--tag", "--neighborhood"):
            with self.subTest(flag=flag):
                self.assertIn(flag, help_text)

    def test_todo_takes_no_positional_argument(self):
        self.assertEqual(self.parser().parse_args([]).tag, None)

    def test_tag_takes_one_value_per_run_rather_than_accumulating(self):
        """Repeatable tags with AND/OR semantics is a separate issue."""
        parsed = self.parser().parse_args(["--tag", "coffee"])

        self.assertEqual(parsed.tag, "coffee")

    def test_the_out_of_scope_flags_are_absent(self):
        help_text = self.parser().format_help()

        for flag in ("--status", "--limit", "--sort", "--rating"):
            with self.subTest(flag=flag):
                self.assertNotIn(flag, help_text)


class TodoCommandSourceRuleTests(SimpleTestCase):
    """Rules issue #7 states about ``todo.py`` itself, read off the AST."""

    def test_todo_does_not_hand_roll_tag_normalization(self):
        called = _called_names(_todo_module())

        self.assertIn("normalize_tag_name", called)
        for hand_rolled in ("lower", "casefold", "upper", "title"):
            with self.subTest(call=hand_rolled):
                self.assertNotIn(hand_rolled, called)

    def test_todo_orders_in_the_database_rather_than_in_python(self):
        called = _called_names(_todo_module())

        self.assertIn("order_by", called)
        self.assertNotIn("sorted", called)
        self.assertNotIn("sort", called)

    def test_todo_prefetches_the_tags_it_prints(self):
        self.assertIn(
            "tags", _string_arguments(_todo_module(), "prefetch_related")
        )


class TodoCommandTestCase(TestCase):
    """Shared plumbing: run ``todo`` through ``call_command``."""

    def make_place(self, name, tags=(), created_at=None, **fields):
        place = Place.objects.create(name=name, **fields)
        for tag_name in tags:
            place.tags.add(Tag.objects.get_or_create(name=tag_name)[0])
        if created_at is not None:
            # `created_at` is `auto_now_add`, so it ignores a value passed to
            # `create`; an UPDATE is the only way to pin it.
            Place.objects.filter(pk=place.pk).update(created_at=created_at)
            place.refresh_from_db()
        return place

    def run_todo(self, *args):
        out, err = StringIO(), StringIO()
        call_command("todo", *args, stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def lines(self, out):
        return [line for line in out.splitlines() if line.strip()]

    def listed_names(self, out, names):
        """The given names, in the order their lines appear in ``out``."""
        found = []
        for line in self.lines(out):
            for name in names:
                if name in line and name not in found:
                    found.append(name)
        return found


class TodoCommandListingTests(TodoCommandTestCase):
    """What gets listed, and what does not."""

    def test_every_wishlist_place_is_listed(self):
        self.make_place("Blue Bottle")
        self.make_place("Tartine")

        out, err = self.run_todo()

        self.assertIn("Blue Bottle", out)
        self.assertIn("Tartine", out)

    def test_visited_places_are_left_out(self):
        self.make_place("Blue Bottle")
        self.make_place(
            "Sightglass",
            status=Place.Status.VISITED,
            last_visited_at=datetime(2024, 3, 9, 18, 30, tzinfo=timezone.utc),
        )

        out, err = self.run_todo()

        self.assertIn("Blue Bottle", out)
        self.assertNotIn("Sightglass", out)

    def test_the_output_carries_a_count_of_what_was_listed(self):
        for name in ("Blue Bottle", "Tartine", "Zuni"):
            self.make_place(name)

        out, err = self.run_todo()

        self.assertIn("3", out)

    def test_a_line_carries_the_name_neighborhood_tags_and_note(self):
        self.make_place(
            "Blue Bottle",
            tags=["coffee", "wifi"],
            neighborhood="Mission",
            note="good wifi, quiet before 10",
        )

        out, err = self.run_todo()

        for expected in ("Blue Bottle", "Mission", "coffee", "wifi", "good wifi"):
            with self.subTest(expected=expected):
                self.assertIn(expected, out)

    def test_a_place_with_only_a_name_prints_cleanly(self):
        self.make_place("Tartine")

        out, err = self.run_todo()

        line = next(line for line in self.lines(out) if "Tartine" in line)
        self.assertEqual(line.strip(), "Tartine")
        self.assertNotIn("None", out)

    def test_a_long_note_does_not_wrap_the_line(self):
        self.make_place("Tartine", note="word " * 200)

        out, err = self.run_todo()

        self.assertEqual(len([line for line in self.lines(out) if "word" in line]), 1)

    def test_a_successful_listing_writes_nothing_to_stderr(self):
        self.make_place("Tartine")

        out, err = self.run_todo()

        self.assertEqual(err, "")


class TodoCommandOrderingTests(TodoCommandTestCase):
    """Oldest first, deterministically."""

    def test_the_oldest_wishlist_place_is_listed_first(self):
        self.make_place(
            "Newer", created_at=datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        )
        self.make_place(
            "Older", created_at=datetime(2023, 1, 2, 9, 0, tzinfo=timezone.utc)
        )

        out, err = self.run_todo()

        self.assertEqual(self.listed_names(out, ["Older", "Newer"]), ["Older", "Newer"])

    def test_three_places_come_back_in_created_order_not_alphabetical(self):
        stamps = {
            "Zuni": datetime(2023, 1, 1, 9, 0, tzinfo=timezone.utc),
            "Alpha": datetime(2023, 6, 1, 9, 0, tzinfo=timezone.utc),
            "Mid": datetime(2024, 1, 1, 9, 0, tzinfo=timezone.utc),
        }
        for name, created_at in stamps.items():
            self.make_place(name, created_at=created_at)

        out, err = self.run_todo()

        self.assertEqual(
            self.listed_names(out, list(stamps)), ["Zuni", "Alpha", "Mid"]
        )

    def test_places_created_in_the_same_instant_are_ordered_by_name(self):
        same_instant = datetime(2024, 1, 1, 9, 0, tzinfo=timezone.utc)
        for name in ("Zuni", "Blue Bottle", "Mission Pie"):
            self.make_place(name, created_at=same_instant)

        out, err = self.run_todo()

        self.assertEqual(
            self.listed_names(out, ["Zuni", "Blue Bottle", "Mission Pie"]),
            ["Blue Bottle", "Mission Pie", "Zuni"],
        )

    def test_the_name_tie_break_ignores_case(self):
        """SQLite sorts uppercase before lowercase, so a plain ``name`` sort
        would put ``Zoo`` above ``abc``."""
        same_instant = datetime(2024, 1, 1, 9, 0, tzinfo=timezone.utc)
        for name in ("Zoo Cafe", "abc Cafe"):
            self.make_place(name, created_at=same_instant)

        out, err = self.run_todo()

        self.assertEqual(
            self.listed_names(out, ["Zoo Cafe", "abc Cafe"]), ["abc Cafe", "Zoo Cafe"]
        )

    def test_a_filtered_listing_keeps_the_same_ordering_rule(self):
        self.make_place(
            "Newer Coffee",
            tags=["coffee"],
            created_at=datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc),
        )
        self.make_place(
            "Older Coffee",
            tags=["coffee"],
            created_at=datetime(2023, 1, 2, 9, 0, tzinfo=timezone.utc),
        )

        out, err = self.run_todo("--tag", "coffee")

        self.assertEqual(
            self.listed_names(out, ["Older Coffee", "Newer Coffee"]),
            ["Older Coffee", "Newer Coffee"],
        )


class TodoCommandEmptyCaseTests(TodoCommandTestCase):
    """Three different pieces of news, each of them exit 0."""

    def test_an_empty_journal_says_so_and_lists_nothing(self):
        out, err = self.run_todo()

        self.assertEqual(Place.objects.count(), 0)
        self.assertIn("empty", out.lower())

    def test_an_empty_journal_is_not_an_error(self):
        """``call_command`` raising is what a non-zero exit looks like here."""
        try:
            self.run_todo()
        except CommandError as error:  # pragma: no cover - the failure path
            self.fail(f"an empty journal must exit 0, raised: {error}")

    def test_a_cleared_wishlist_reads_differently_from_an_empty_journal(self):
        empty_journal, _ = self.run_todo()
        self.make_place(
            "Sightglass",
            status=Place.Status.VISITED,
            last_visited_at=datetime(2024, 3, 9, 18, 30, tzinfo=timezone.utc),
        )

        cleared, _ = self.run_todo()

        self.assertNotEqual(cleared.strip(), empty_journal.strip())
        self.assertIn("wishlist", cleared.lower())
        self.assertNotIn("Sightglass", cleared)

    def test_filters_matching_nothing_read_differently_again(self):
        self.make_place("Blue Bottle", tags=["coffee"], neighborhood="Mission")

        no_matches, _ = self.run_todo("--tag", "ramen")

        self.assertNotIn("Blue Bottle", no_matches)
        self.assertIn("filter", no_matches.lower())

    def test_a_tag_that_exists_on_no_place_at_all_is_not_an_error(self):
        self.make_place("Blue Bottle", tags=["coffee"])

        try:
            out, err = self.run_todo("--tag", "nonexistent")
        except CommandError as error:  # pragma: no cover - the failure path
            self.fail(f"an unknown tag must exit 0, raised: {error}")

        self.assertNotIn("Blue Bottle", out)

    def test_the_three_empty_messages_are_all_different(self):
        todo = _todo_module()

        messages = {
            todo.EMPTY_JOURNAL_MESSAGE,
            todo.WISHLIST_CLEARED_MESSAGE,
            todo.NO_FILTER_MATCHES_MESSAGE,
        }
        self.assertEqual(len(messages), 3)


class TodoCommandFilterTests(TodoCommandTestCase):
    """``--tag`` and ``--neighborhood``, ANDed, never leaking visited places."""

    def setUp(self):
        self.coffee = self.make_place(
            "Blue Bottle", tags=["coffee"], neighborhood="Mission"
        )
        self.ramen = self.make_place("Ramen Shop", tags=["ramen"], neighborhood="SoMa")
        self.coffee_soma = self.make_place(
            "Sightglass Wish", tags=["coffee"], neighborhood="SoMa"
        )

    def test_tag_lists_only_places_carrying_that_tag(self):
        out, err = self.run_todo("--tag", "coffee")

        self.assertIn("Blue Bottle", out)
        self.assertIn("Sightglass Wish", out)
        self.assertNotIn("Ramen Shop", out)

    def test_the_tag_filter_ignores_case(self):
        baseline, _ = self.run_todo("--tag", "coffee")

        for typed in ("Coffee", "COFFEE", "  Coffee  "):
            with self.subTest(typed=typed):
                out, err = self.run_todo("--tag", typed)

                self.assertEqual(out, baseline)
                self.assertIn("Blue Bottle", out)

    def test_the_tag_filter_matches_the_whole_tag_not_a_substring(self):
        """The same hard line ``--neighborhood`` draws. ``--tag cof`` must not
        quietly list every coffee place, and ``--tag coffeehouse`` must not
        match ``coffee`` from the other direction."""
        for typed in ("cof", "coffeehouse", "offe"):
            with self.subTest(typed=typed):
                out, err = self.run_todo("--tag", typed)

                self.assertNotIn("Blue Bottle", out)
                self.assertNotIn("Sightglass Wish", out)
                self.assertIn("filter", out.lower())

    def test_the_tag_filter_strips_surrounding_whitespace(self):
        """The ``NOCASE`` collation folds case but does not strip, which is
        why the normalizer runs at the entry point."""
        out, err = self.run_todo("--tag", "  coffee  ")

        self.assertIn("Blue Bottle", out)

    def test_neighborhood_lists_only_places_in_that_neighborhood(self):
        out, err = self.run_todo("--neighborhood", "Mission")

        self.assertIn("Blue Bottle", out)
        self.assertNotIn("Ramen Shop", out)

    def test_the_neighborhood_filter_ignores_case(self):
        for typed in ("mission", "MISSION", "Mission"):
            with self.subTest(typed=typed):
                out, err = self.run_todo("--neighborhood", typed)

                self.assertIn("Blue Bottle", out)

    def test_the_neighborhood_filter_strips_surrounding_whitespace(self):
        """``__iexact`` folds case but matches the value verbatim otherwise,
        so the padding has to come off before the filter is built."""
        out, err = self.run_todo("--neighborhood", "  Mission  ")

        self.assertIn("Blue Bottle", out)
        self.assertNotIn("Ramen Shop", out)

    def test_the_neighborhood_filter_matches_the_whole_value_not_a_substring(self):
        """Partial neighborhood matching is ``find``'s job, deliberately."""
        out, err = self.run_todo("--neighborhood", "Miss")

        self.assertNotIn("Blue Bottle", out)
        self.assertIn("filter", out.lower())

    def test_both_filters_are_anded(self):
        out, err = self.run_todo("--tag", "Coffee", "--neighborhood", "soma")

        self.assertIn("Sightglass Wish", out)
        self.assertNotIn("Blue Bottle", out)
        self.assertNotIn("Ramen Shop", out)

    def test_filters_never_leak_a_visited_place(self):
        self.make_place(
            "Visited Coffee",
            tags=["coffee"],
            neighborhood="Mission",
            status=Place.Status.VISITED,
            last_visited_at=datetime(2024, 3, 9, 18, 30, tzinfo=timezone.utc),
        )

        for args in (("--tag", "coffee"), ("--neighborhood", "Mission"), ()):
            with self.subTest(args=args):
                out, err = self.run_todo(*args)

                self.assertNotIn("Visited Coffee", out)


class TodoCommandQueryCountTests(TodoCommandTestCase):
    """Each line names the place's tags, and ``Place.tags`` is a related
    manager: without a prefetch that is one query per wishlist place."""

    def populate(self, count, offset=0):
        for index in range(offset, offset + count):
            self.make_place(f"Coffee Number {index}", tags=["coffee", "wifi"])

    def queries_for_a_listing(self):
        with CaptureQueriesContext(connection) as captured:
            self.run_todo()
        return len(captured)

    def test_the_query_count_does_not_grow_with_the_wishlist(self):
        self.populate(3)
        few = self.queries_for_a_listing()

        self.populate(15, offset=3)
        many = self.queries_for_a_listing()

        self.assertEqual(few, many)
        self.assertLess(many, Place.objects.count())

    def test_the_query_count_does_not_grow_when_filters_are_applied(self):
        self.populate(12)

        with CaptureQueriesContext(connection) as captured:
            self.run_todo("--tag", "Coffee")

        self.assertLess(len(captured), Place.objects.count())


class WishlistLoopIntegrationTests(TodoCommandTestCase):
    """The loop the issue is named for: on the list, then off it."""

    def run_visit(self, *args):
        out, err = StringIO(), StringIO()
        call_command("visit", *args, stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def test_a_visited_place_drops_off_the_wishlist(self):
        self.make_place("Blue Bottle", tags=["coffee"], neighborhood="Mission")
        self.make_place("Tartine")

        before, _ = self.run_todo()
        self.run_visit("Blue Bottle", "--rating", "5", "--note", "great cortado")
        after, _ = self.run_todo()

        self.assertIn("Blue Bottle", before)
        self.assertNotIn("Blue Bottle", after)
        self.assertIn("Tartine", after)

    def test_visiting_the_last_wishlist_place_clears_the_list(self):
        self.make_place("Blue Bottle")

        self.run_visit("Blue Bottle")
        out, err = self.run_todo()

        self.assertIn(_todo_module().WISHLIST_CLEARED_MESSAGE, out)


# ---------------------------------------------------------------------------
# `surprise` (issue #8)
#
# The weighting is checked statistically in *shape* only: every draw below is
# `call_command("surprise", seed=<fixed int>)`, so the tallies are identical
# on every machine and every run. That is what `_docs/testing-guidelines.md`
# means by "pin randomness" -- not running the command repeatedly and hoping.
# ---------------------------------------------------------------------------


#: How many fixed seeds a distribution test loops through. Issue #8 names
#: 1000, and the bounds below are written as fractions of this constant.
DRAWS = 1000

#: Fixture A, in the order it is created, so primary keys run 1..5.
#:
#: =============== ========= ================ ======
#: Place           status    last_visited_at  weight
#: =============== ========= ================ ======
#: Wishlist Bar    wishlist  None                 10
#: Ghost Diner     visited   None                  5
#: Old Ramen       visited   200 days ago          5
#: Mid Park        visited   90 days ago           3
#: Yesterday Cafe  visited   1 day ago             1
#: =============== ========= ================ ======
FIXTURE_A_NAMES = [
    "Wishlist Bar",
    "Ghost Diner",
    "Old Ramen",
    "Mid Park",
    "Yesterday Cafe",
]


def _surprise_module():
    """The ``surprise`` command module, imported the way Django finds it."""
    from places.management.commands import surprise as surprise_module

    return surprise_module


def _make_place(name, tags=(), days_ago=None, **fields):
    """A place, with its tags attached and its visit date placed in the past."""
    place = Place.objects.create(name=name, **fields)
    for tag_name in tags:
        place.tags.add(Tag.objects.get_or_create(name=tag_name)[0])
    if days_ago is not None:
        place.last_visited_at = django_timezone.now() - timedelta(days=days_ago)
        place.save(update_fields=["last_visited_at"])
    return place


def _create_fixture_a():
    """Create Fixture A. Insertion order fixes the primary keys, and with them
    the candidate order every seeded assertion below depends on."""
    visited = Place.Status.VISITED
    _make_place("Wishlist Bar")
    _make_place("Ghost Diner", status=visited)
    _make_place("Old Ramen", status=visited, days_ago=200)
    _make_place("Mid Park", status=visited, days_ago=90)
    _make_place("Yesterday Cafe", status=visited, days_ago=1)
    return list(FIXTURE_A_NAMES)


class SurpriseCommandDiscoveryTests(SimpleTestCase):
    """The command is discoverable and takes the documented flags."""

    def parser(self):
        return load_command_class("places", "surprise").create_parser(
            "manage.py", "surprise"
        )

    def test_surprise_is_registered_as_a_command_of_the_places_app(self):
        self.assertEqual(get_commands().get("surprise"), "places")

    def test_help_documents_both_filters_and_the_seed(self):
        help_text = self.parser().format_help()

        for flag in ("--tag", "--neighborhood", "--seed"):
            with self.subTest(flag=flag):
                self.assertIn(flag, help_text)

    def test_surprise_takes_no_positional_argument(self):
        parsed = self.parser().parse_args([])

        self.assertEqual((parsed.tag, parsed.neighborhood, parsed.seed), (None,) * 3)

    def test_the_seed_is_parsed_as_a_whole_number(self):
        self.assertEqual(self.parser().parse_args(["--seed", "7"]).seed, 7)

    def test_the_out_of_scope_flags_are_absent(self):
        """Issue #8 rules each of these out by name."""
        help_text = self.parser().format_help()

        for flag in ("--limit", "--status", "--visit", "--accept"):
            with self.subTest(flag=flag):
                self.assertNotIn(flag, help_text)


class SurpriseCommandSourceRuleTests(SimpleTestCase):
    """Rules issue #8 states which are cheaper to read off the source."""

    def test_surprise_does_not_hand_roll_tag_normalization(self):
        called = _called_names(_surprise_module())

        self.assertIn("normalize_tag_name", called)
        for hand_rolled in ("lower", "casefold", "upper", "title"):
            with self.subTest(call=hand_rolled):
                self.assertNotIn(hand_rolled, called)

    def test_surprise_draws_from_a_private_generator_not_the_global_one(self):
        calls = _dotted_calls(_surprise_module())

        self.assertIn("random.Random", calls)
        self.assertNotIn("random.seed", calls)
        self.assertNotIn("random.choices", calls)
        self.assertNotIn("random.choice", calls)

    def test_surprise_orders_its_candidates_by_primary_key(self):
        """The field passed to ``.order_by(...)``, not the text.

        ``_candidates``' docstring quotes ``order_by("pk")`` while explaining
        why the order matters, and that sentence kept this test green after the
        call itself was deleted. The AST holds the argument, not the prose.
        """
        self.assertIn("pk", _string_arguments(_surprise_module(), "order_by"))

    def test_surprise_prefetches_the_tags_it_prints(self):
        self.assertIn(
            "tags", _string_arguments(_surprise_module(), "prefetch_related")
        )

    def test_surprise_reads_the_clock_through_django(self):
        calls = _dotted_calls(_surprise_module())

        self.assertIn("timezone.now", calls)
        self.assertNotIn("datetime.now", calls)

    def test_surprise_pulls_in_nothing_from_the_fuzzy_ranker(self):
        imported = _imported_modules(_surprise_module())

        self.assertNotIn("places.search", imported)
        self.assertNotIn("rapidfuzz", imported)

    def test_surprise_never_writes(self):
        """It suggests; ``visit`` records. No writing call may exist at all."""
        called = _called_names(_surprise_module())

        for writer in ("save", "update", "create", "get_or_create", "delete"):
            with self.subTest(writer=writer):
                self.assertNotIn(writer, called)


class SurpriseCommandTestCase(TestCase):
    """Shared plumbing: run ``surprise`` through ``call_command``."""

    def make_place(self, *args, **kwargs):
        return _make_place(*args, **kwargs)

    def fixture_a(self):
        return _create_fixture_a()

    def run_surprise(self, *args, **options):
        out, err = StringIO(), StringIO()
        call_command("surprise", *args, stdout=out, stderr=err, **options)
        return out.getvalue(), err.getvalue()

    def picked(self, out, names):
        """The one name from ``names`` that ``out`` names, asserting it is one."""
        found = [name for name in names if name in out]
        self.assertEqual(len(found), 1, f"expected exactly one place, got {found}")
        return found[0]

    def tally(self, names, *args, draws=DRAWS, **options):
        """Which place each of ``draws`` fixed seeds picks, counted.

        Every seed is a literal, so this is deterministic to the draw: the
        same numbers come back on every machine and on every run.
        """
        counts = dict.fromkeys(names, 0)
        for seed in range(draws):
            out, _ = self.run_surprise(*args, seed=seed, **options)
            counts[self.picked(out, names)] += 1
        return counts


class SurpriseWeightLadderTests(SimpleTestCase):
    """The ladder itself, on unsaved instances -- no database needed.

    The tiers are pinned here because the boundaries are exact and no
    distribution can tell thirty-days-exactly from thirty-days-and-a-second.
    The distribution tests below are what prove the ladder is actually used.
    """

    def setUp(self):
        self.surprise = _surprise_module()
        self.now = django_timezone.now()

    def weight(self, status, last_visited_at):
        place = Place(name="x", status=status, last_visited_at=last_visited_at)
        return self.surprise.weight_for(place, self.now)

    def visited(self, **age):
        return self.weight(Place.Status.VISITED, self.now - timedelta(**age))

    def test_a_wishlist_place_weighs_ten(self):
        self.assertEqual(self.weight(Place.Status.WISHLIST, None), 10)

    def test_a_wishlist_place_weighs_ten_even_when_a_visit_date_is_stamped(self):
        """Status is the authority on "have I been here", not the timestamp."""
        stamped = self.weight(Place.Status.WISHLIST, self.now - timedelta(days=1))

        self.assertEqual(stamped, 10)

    def test_a_visited_place_with_no_date_weighs_five_not_ten(self):
        """The decision issue #8 records: date unknown means "ages ago"."""
        weight = self.weight(Place.Status.VISITED, None)

        self.assertEqual(weight, 5)

    def test_a_visited_place_with_no_date_weighs_the_same_as_a_long_ago_visit(self):
        self.assertEqual(self.weight(Place.Status.VISITED, None), self.visited(days=200))

    def test_a_visit_long_ago_weighs_five(self):
        self.assertEqual(self.visited(days=200), 5)

    def test_a_visit_exactly_one_hundred_and_eighty_days_ago_weighs_five(self):
        self.assertEqual(self.visited(days=180), 5)

    def test_a_visit_just_under_one_hundred_and_eighty_days_ago_weighs_three(self):
        self.assertEqual(self.visited(days=179, hours=23), 3)

    def test_a_middling_visit_weighs_three(self):
        self.assertEqual(self.visited(days=90), 3)

    def test_a_visit_exactly_thirty_days_ago_weighs_three_not_one(self):
        self.assertEqual(self.visited(days=30), 3)

    def test_a_visit_just_under_thirty_days_ago_weighs_one(self):
        self.assertEqual(self.visited(days=29, hours=23), 1)

    def test_a_recent_visit_weighs_one(self):
        self.assertEqual(self.visited(days=1), 1)

    def test_a_visit_stamped_this_instant_weighs_one(self):
        self.assertEqual(self.visited(days=0), 1)

    def test_a_visit_dated_in_the_future_weighs_one_and_never_goes_negative(self):
        """Clock skew, or a typo in the admin. Never an error, never negative."""
        weight = self.weight(Place.Status.VISITED, self.now + timedelta(days=400))

        self.assertEqual(weight, 1)

    def test_no_rung_of_the_ladder_is_zero(self):
        surprise = self.surprise
        rungs = (
            surprise.WISHLIST_WEIGHT,
            surprise.LONG_AGO_WEIGHT,
            surprise.MIDDLING_WEIGHT,
            surprise.RECENT_WEIGHT,
        )

        for rung in rungs:
            with self.subTest(rung=rung):
                self.assertGreater(rung, 0)

    def test_the_ladder_descends(self):
        surprise = self.surprise

        self.assertGreater(surprise.WISHLIST_WEIGHT, surprise.LONG_AGO_WEIGHT)
        self.assertGreater(surprise.LONG_AGO_WEIGHT, surprise.MIDDLING_WEIGHT)
        self.assertGreater(surprise.MIDDLING_WEIGHT, surprise.RECENT_WEIGHT)


class SurpriseDistributionTests(SurpriseCommandTestCase):
    """A thousand fixed seeds against Fixture A, tallied.

    Statistical in shape, deterministic in fact. The tally is computed once
    for the class -- ``setUpTestData`` gives every method the same rows, so
    running the thousand draws per method would only buy the same numbers
    seven times over.
    """

    _counts = None

    @classmethod
    def setUpTestData(cls):
        _create_fixture_a()

    def setUp(self):
        self.names = list(FIXTURE_A_NAMES)
        if type(self)._counts is None:
            type(self)._counts = self.tally(self.names)
        self.counts = type(self)._counts

    def test_every_place_is_reachable(self):
        for name in self.names:
            with self.subTest(name=name):
                self.assertGreater(self.counts[name], 0)

    def test_the_pick_is_not_always_the_same_place(self):
        chosen = [name for name, count in self.counts.items() if count]

        self.assertGreaterEqual(len(chosen), 2)

    def test_the_tally_is_ordered_by_the_ladder(self):
        counts = self.counts

        self.assertGreater(counts["Wishlist Bar"], counts["Ghost Diner"])
        self.assertGreater(counts["Wishlist Bar"], counts["Old Ramen"])
        self.assertGreater(counts["Ghost Diner"], counts["Mid Park"])
        self.assertGreater(counts["Old Ramen"], counts["Mid Park"])
        self.assertGreater(counts["Mid Park"], counts["Yesterday Cafe"])

    def test_a_wishlist_place_outranks_a_recently_visited_one_by_a_lot(self):
        """Expected under the ladder: roughly 417 and 42 out of 1000."""
        wishlist = self.counts["Wishlist Bar"]
        yesterday = self.counts["Yesterday Cafe"]

        self.assertGreater(wishlist, 0.30 * DRAWS)
        self.assertLess(yesterday, 0.12 * DRAWS)
        self.assertGreaterEqual(wishlist, 3 * yesterday)

    def test_the_equal_weight_pair_lands_within_a_tenth_of_each_other(self):
        """``Ghost Diner`` (date unknown) and ``Old Ramen`` (200 days) both weigh 5."""
        gap = abs(self.counts["Ghost Diner"] - self.counts["Old Ramen"])

        self.assertLessEqual(gap, 0.10 * DRAWS)

    def test_a_visited_place_with_no_date_is_drawn_far_less_than_a_wishlist_one(self):
        """The other half of the null-date decision: weight 5, never promoted to 10."""
        ghost = self.counts["Ghost Diner"]
        wishlist = self.counts["Wishlist Bar"]

        self.assertLess(ghost, 0.75 * wishlist)

    def test_a_visited_place_with_no_date_is_drawn_far_more_than_a_recent_one(self):
        """And never demoted to the bottom rung either."""
        ghost = self.counts["Ghost Diner"]
        yesterday = self.counts["Yesterday Cafe"]

        self.assertGreater(ghost, 2 * yesterday)

    def test_the_tally_adds_up_to_the_number_of_draws(self):
        self.assertEqual(sum(self.counts.values()), DRAWS)


class SurpriseUniformityTests(SurpriseCommandTestCase):
    """Equal weights draw equally often."""

    def test_two_wishlist_places_split_a_thousand_draws_evenly(self):
        self.make_place("Blue Bottle")
        self.make_place("Tartine")

        counts = self.tally(["Blue Bottle", "Tartine"])

        for name, count in counts.items():
            with self.subTest(name=name):
                self.assertGreater(count, 0.40 * DRAWS)
                self.assertLess(count, 0.60 * DRAWS)


class SurpriseSeedTests(SurpriseCommandTestCase):
    """``--seed`` reproduces a pick without disturbing anything else."""

    #: The golden pin: recorded once during implementation against Fixture A,
    #: and never moved. It guards "the draw is reproducible"; the distribution
    #: tests above are what actually prove the ladder.
    GOLDEN_SEED = 42
    GOLDEN_PICK = "Old Ramen"

    def test_the_same_seed_picks_the_same_place_twice(self):
        self.fixture_a()

        first, _ = self.run_surprise(seed=1234)
        second, _ = self.run_surprise(seed=1234)

        self.assertEqual(first, second)

    def test_the_golden_seed_picks_the_place_it_has_always_picked(self):
        names = self.fixture_a()

        out, _ = self.run_surprise(seed=self.GOLDEN_SEED)

        self.assertEqual(self.picked(out, names), self.GOLDEN_PICK)

    def test_the_seed_is_reachable_as_a_keyword_and_as_a_flag(self):
        names = self.fixture_a()

        as_keyword, _ = self.run_surprise(seed=self.GOLDEN_SEED)
        as_flag, _ = self.run_surprise("--seed", str(self.GOLDEN_SEED))

        self.assertEqual(self.picked(as_flag, names), self.GOLDEN_PICK)
        self.assertEqual(as_flag, as_keyword)

    def test_different_seeds_reach_different_places(self):
        names = self.fixture_a()

        picks = {
            self.picked(self.run_surprise(seed=seed)[0], names) for seed in range(40)
        }

        self.assertGreater(len(picks), 1)

    def test_an_unseeded_run_is_genuinely_unseeded(self):
        """Forty unseeded draws over Fixture A.

        The likeliest place carries 5/12 of the weight, so all forty agreeing
        has a probability under 1e-15 -- this is not a coin flip dressed up as
        a test.
        """
        names = self.fixture_a()

        picks = {self.picked(self.run_surprise()[0], names) for _ in range(40)}

        self.assertGreater(len(picks), 1)

    def test_seeding_leaves_the_process_wide_random_state_untouched(self):
        """A command calling ``random.seed()`` would pin the rest of the suite."""
        self.fixture_a()
        before = random.getstate()

        self.run_surprise(seed=1)

        self.assertEqual(random.getstate(), before)

    def test_an_unseeded_run_leaves_the_process_wide_random_state_untouched(self):
        self.fixture_a()
        before = random.getstate()

        self.run_surprise()

        self.assertEqual(random.getstate(), before)

    def test_the_global_generator_yields_the_same_sequence_across_a_run(self):
        """The observable consequence of the state check above."""
        self.fixture_a()
        random.seed(99)
        expected = [random.random() for _ in range(3)]

        random.seed(99)
        first = random.random()
        self.run_surprise(seed=5)
        rest = [random.random() for _ in range(2)]

        self.assertEqual([first] + rest, expected)

    def test_the_draw_follows_primary_key_order_not_name_order(self):
        """Without a fixed candidate order every seeded assertion is flaky.

        Two equally weighted places whose insertion order is the reverse of
        their alphabetical order, and a seed that draws the *first* candidate.
        Which name comes back therefore says which order the command used --
        and ``Place.Meta.ordering`` is by name, so name order is exactly what
        would silently take over if the ``order_by("pk")`` were dropped.
        """
        self.make_place("Zebra Lounge")
        self.make_place("Alpha Bar")

        out, _ = self.run_surprise(seed=1)

        self.assertEqual(self.picked(out, ["Zebra Lounge", "Alpha Bar"]), "Zebra Lounge")


class SurpriseFilterTests(SurpriseCommandTestCase):
    """``--tag`` and ``--neighborhood``, ANDed, applied before the weighting."""

    def setUp(self):
        self.make_place("Blue Bottle", tags=["coffee"], neighborhood="Mission")
        self.make_place("Ramen Shop", tags=["ramen"], neighborhood="Mission")
        self.make_place("Sightglass", tags=["coffee"], neighborhood="SoMa")

    def test_a_tag_narrows_the_pool_to_places_carrying_it(self):
        for seed in range(12):
            with self.subTest(seed=seed):
                out, _ = self.run_surprise("--tag", "coffee", seed=seed)

                self.assertNotIn("Ramen Shop", out)

    def test_a_tag_can_narrow_the_pool_to_one_place(self):
        out, _ = self.run_surprise("--tag", "ramen", seed=3)

        self.assertIn("Ramen Shop", out)

    def test_the_tag_lookup_ignores_the_case_and_padding_of_the_flag(self):
        baseline, _ = self.run_surprise("--tag", "coffee", seed=0)

        for typed in ("Coffee", "COFFEE", "  CoFfEe  "):
            with self.subTest(typed=typed):
                out, _ = self.run_surprise("--tag", typed, seed=0)

                self.assertEqual(out, baseline)
                self.assertNotIn("Ramen Shop", out)

    def test_a_mixed_case_tag_reaches_the_same_single_place(self):
        lower, _ = self.run_surprise("--tag", "ramen", seed=3)
        upper, _ = self.run_surprise("--tag", "RAMEN", seed=3)

        self.assertIn("Ramen Shop", upper)
        self.assertEqual(lower, upper)

    def test_a_neighborhood_narrows_the_pool_to_places_in_it(self):
        for seed in range(12):
            with self.subTest(seed=seed):
                out, _ = self.run_surprise("--neighborhood", "SoMa", seed=seed)

                self.assertIn("Sightglass", out)

    def test_the_neighborhood_lookup_ignores_case(self):
        for typed in ("SoMa", "soma", "SOMA"):
            with self.subTest(typed=typed):
                out, _ = self.run_surprise("--neighborhood", typed, seed=0)

                self.assertIn("Sightglass", out)

    def test_the_neighborhood_lookup_strips_surrounding_whitespace(self):
        """``__iexact`` folds case but matches the value verbatim otherwise,
        so the padding has to come off before the filter is built."""
        padded, _ = self.run_surprise("--neighborhood", "  soma  ", seed=0)
        bare, _ = self.run_surprise("--neighborhood", "SoMa", seed=0)

        self.assertIn("Sightglass", padded)
        self.assertNotIn("Blue Bottle", padded)
        self.assertEqual(padded, bare)

    def test_the_neighborhood_matches_the_whole_value_not_a_substring(self):
        """Partial matching is deliberately ``find``'s job, not this one's."""
        with self.assertRaises(CommandError):
            self.run_surprise("--neighborhood", "Mis", seed=0)

    def test_the_two_filters_combine_with_and(self):
        for seed in range(12):
            with self.subTest(seed=seed):
                out, _ = self.run_surprise(
                    "--tag", "coffee", "--neighborhood", "Mission", seed=seed
                )

                self.assertIn("Blue Bottle", out)
                self.assertNotIn("Sightglass", out)
                self.assertNotIn("Ramen Shop", out)

    def test_the_two_filters_can_exclude_each_other(self):
        with self.assertRaises(CommandError):
            self.run_surprise("--tag", "ramen", "--neighborhood", "SoMa", seed=0)

    def test_filtering_re_normalizes_the_odds_rather_than_wasting_draws(self):
        """Weights are computed over the survivors only.

        Both coffee places are wishlist entries, so with the ramen place
        excluded they split the draws evenly. If the excluded place still held
        a share of the total, some seeds would land on nobody.
        """
        counts = self.tally(["Blue Bottle", "Sightglass"], "--tag", "coffee", draws=200)

        self.assertEqual(sum(counts.values()), 200)
        for name, count in counts.items():
            with self.subTest(name=name):
                self.assertGreater(count, 0.40 * 200)


class SurpriseEmptyCaseTests(SurpriseCommandTestCase):
    """Nothing to suggest, told two different ways, both of them non-zero."""

    def message_of(self, *args, **options):
        with self.assertRaises(CommandError) as raised:
            self.run_surprise(*args, **options)
        return str(raised.exception)

    def test_an_empty_journal_is_an_error_naming_the_situation(self):
        message = self.message_of()

        self.assertEqual(Place.objects.count(), 0)
        self.assertIn("no places in the journal", message)
        self.assertIn("add", message)

    def test_an_empty_journal_prints_nothing_to_stdout(self):
        """Not even a blank line."""
        out = StringIO()

        with self.assertRaises(CommandError):
            call_command("surprise", stdout=out)

        self.assertEqual(out.getvalue(), "")

    def test_an_empty_journal_says_so_even_when_filters_were_given(self):
        """Which question is asked first is a decision, so it is pinned here.

        ``todo`` and ``find`` both ask "is the journal empty?" *before* they
        filter, and ``surprise`` matches them: on an empty database the news
        is that there is nothing to filter, not that the filters were too
        narrow. Checking the filters first would read as "your filters were
        too narrow" to someone who has simply not added a place yet, and
        nothing else in the suite would notice the flip.
        """
        message = self.message_of("--tag", "ramen")

        self.assertEqual(Place.objects.count(), 0)
        self.assertIn("no places in the journal", message)
        self.assertNotIn("Nothing matches those filters", message)
        self.assertNotIn("--tag ramen", message)

    def test_filters_that_match_nothing_read_differently_from_an_empty_journal(self):
        empty = self.message_of()
        self.make_place("Blue Bottle", tags=["coffee"])

        narrow = self.message_of("--tag", "ramen")

        self.assertIn("Nothing matches those filters", narrow)
        self.assertNotIn("Nothing matches those filters", empty)
        self.assertNotIn("no places in the journal", narrow)

    def test_the_filter_message_echoes_back_the_filters_that_were_applied(self):
        self.make_place("Blue Bottle", tags=["coffee"], neighborhood="Mission")

        message = self.message_of("--tag", "Ramen", "--neighborhood", "SoMa")

        self.assertIn("--tag ramen", message)
        self.assertIn("--neighborhood SoMa", message)

    def test_the_filter_message_names_only_the_filter_that_was_given(self):
        self.make_place("Blue Bottle", tags=["coffee"], neighborhood="Mission")

        message = self.message_of("--tag", "ramen")

        self.assertIn("--tag ramen", message)
        self.assertNotIn("--neighborhood ", message.split("Try dropping")[0])

    def test_a_tag_that_matches_no_tag_row_at_all_takes_the_filter_message(self):
        """Not a crash, and not the empty-journal message."""
        self.make_place("Blue Bottle", tags=["coffee"])

        message = self.message_of("--tag", "nonexistent")

        self.assertIn("Nothing matches those filters", message)
        self.assertIn("--tag nonexistent", message)

    def test_the_two_no_candidate_messages_share_no_distinctive_phrase(self):
        surprise = _surprise_module()

        self.assertNotIn(
            "Nothing matches those filters", surprise.EMPTY_JOURNAL_MESSAGE
        )
        self.assertNotIn("no places in the journal", surprise.NO_CANDIDATES_MESSAGE)

    def test_one_candidate_is_always_the_pick_however_low_its_weight(self):
        """The bottom rung of the ladder, alone: still picked, on every seed."""
        self.make_place("Yesterday Cafe", status=Place.Status.VISITED, days_ago=1)

        for seed in range(25):
            with self.subTest(seed=seed):
                out, _ = self.run_surprise(seed=seed)

                self.assertIn("Yesterday Cafe", out)

    def test_one_surviving_candidate_is_the_pick_on_every_seed(self):
        self.make_place("Blue Bottle", tags=["coffee"])
        self.make_place("Ramen Shop", tags=["ramen"])

        for seed in range(15):
            with self.subTest(seed=seed):
                out, _ = self.run_surprise("--tag", "ramen", seed=seed)

                self.assertIn("Ramen Shop", out)
                self.assertNotIn("Blue Bottle", out)


class SurpriseOutputTests(SurpriseCommandTestCase):
    """One place, its reason, and its note in full."""

    def only_place(self, **fields):
        """Create the single place in the journal, then run and return the output."""
        place = _make_place(fields.pop("name", "Blue Bottle"), **fields)
        out, err = self.run_surprise(seed=0)
        return place, out, err

    def test_the_output_names_the_place(self):
        _, out, _ = self.only_place()

        self.assertIn("Blue Bottle", out)

    def test_the_output_carries_neighborhood_status_and_rating(self):
        _, out, _ = self.only_place(
            neighborhood="Mission", status=Place.Status.VISITED, rating=4
        )

        self.assertIn("Mission", out)
        self.assertIn("visited", out)
        self.assertIn("rating 4", out)

    def test_the_output_names_the_places_tags(self):
        _, out, _ = self.only_place(tags=["coffee", "wifi"])

        self.assertIn("coffee", out)
        self.assertIn("wifi", out)

    def test_the_note_is_printed_in_full_and_is_never_snipped(self):
        """Unlike ``find`` and ``todo``, which cut a note to keep a list tidy."""
        note = (
            "the corner table by the window has the only power outlet in the "
            "building, and the cortado is worth the queue on a Saturday "
            "morning even when it wraps around the block"
        )

        _, out, _ = self.only_place(note=note)

        self.assertGreater(len(note), _lookup_module().NOTE_SNIPPET_LENGTH)
        self.assertIn(note, out)
        self.assertNotIn(_lookup_module().TRUNCATION_MARKER, out)

    def test_the_note_is_printed_once_not_twice(self):
        note = "great cortado and quiet upstairs"

        _, out, _ = self.only_place(note=note)

        self.assertEqual(out.count(note), 1)

    def test_a_multi_line_note_survives_intact(self):
        note = "great cortado\nand the upstairs room is always empty"

        _, out, _ = self.only_place(note=note)

        self.assertIn(note, out)

    def test_a_wishlist_place_says_it_has_never_been_visited(self):
        _, out, _ = self.only_place()

        self.assertIn("never visited", out)

    def test_a_visited_place_with_no_date_says_the_date_is_unknown(self):
        _, out, _ = self.only_place(status=Place.Status.VISITED)

        self.assertIn("visited, date unknown", out)
        self.assertNotIn("never visited", out)

    def test_a_visited_place_with_a_date_shows_that_date(self):
        place, out, _ = self.only_place(status=Place.Status.VISITED, days_ago=200)

        self.assertIn(place.last_visited_at.strftime("%Y-%m-%d"), out)
        self.assertNotIn("never visited", out)
        self.assertNotIn("date unknown", out)

    def test_a_visit_dated_in_the_future_reads_as_today_not_as_negative_days(self):
        """Clock skew, or a typo in the admin. Never "-400 days ago"."""
        place = _make_place("Time Traveller", status=Place.Status.VISITED)
        place.last_visited_at = django_timezone.now() + timedelta(days=400)
        place.save(update_fields=["last_visited_at"])

        out, _ = self.run_surprise(seed=0)

        self.assertIn("today", out)
        self.assertNotIn("-400", out)
        self.assertNotIn("days ago", out)

    def test_the_three_reasons_are_three_different_strings(self):
        reasons = set()
        for fields in (
            {"name": "Wishlist Bar"},
            {"name": "Ghost Diner", "status": Place.Status.VISITED},
            {"name": "Old Ramen", "status": Place.Status.VISITED, "days_ago": 200},
        ):
            Place.objects.all().delete()
            _, out, _ = self.only_place(**fields)
            reasons.add(out.split("Why:")[1].splitlines()[0].strip())

        self.assertEqual(len(reasons), 3)

    def test_a_bare_place_prints_no_none_no_null_and_no_empty_note_field(self):
        """No rating, no note, no neighborhood, no tags, no visit date."""
        _, out, _ = self.only_place()

        self.assertNotIn("None", out)
        self.assertNotIn("null", out)
        self.assertNotIn("Note:", out)

    def test_a_visited_place_with_nothing_filled_in_prints_no_none(self):
        _, out, _ = self.only_place(status=Place.Status.VISITED)

        self.assertNotIn("None", out)
        self.assertNotIn("null", out)

    def test_the_output_is_not_blank(self):
        _, out, _ = self.only_place()

        self.assertTrue(out.strip())

    def test_exactly_one_place_is_named(self):
        self.make_place("Blue Bottle")
        self.make_place("Tartine")
        self.make_place("Sightglass")

        for seed in range(20):
            with self.subTest(seed=seed):
                out, _ = self.run_surprise(seed=seed)
                named = [
                    name
                    for name in ("Blue Bottle", "Tartine", "Sightglass")
                    if name in out
                ]

                self.assertEqual(len(named), 1)

    def test_a_successful_run_writes_nothing_to_stderr(self):
        _, _, err = self.only_place()

        self.assertEqual(err, "")


class SurpriseReadOnlyTests(SurpriseCommandTestCase):
    """``surprise`` suggests; it never records. Stamping belongs to ``visit``."""

    def snapshot(self):
        return (
            list(Place.objects.order_by("pk").values()),
            list(Tag.objects.order_by("pk").values()),
            list(Place.tags.through.objects.order_by("pk").values()),
        )

    def test_running_twice_leaves_every_row_byte_identical(self):
        self.fixture_a()
        self.make_place("Blue Bottle", tags=["coffee"], neighborhood="Mission")
        before = self.snapshot()

        self.run_surprise(seed=1)
        self.run_surprise(seed=2)

        self.assertEqual(self.snapshot(), before)

    def test_a_wishlist_place_that_comes_up_stays_on_the_wishlist(self):
        self.make_place("Blue Bottle")

        self.run_surprise(seed=0)

        place = Place.objects.get(name="Blue Bottle")
        self.assertEqual(place.status, Place.Status.WISHLIST)
        self.assertIsNone(place.last_visited_at)

    def test_the_command_issues_no_write_queries(self):
        self.fixture_a()

        with CaptureQueriesContext(connection) as captured:
            self.run_surprise(seed=0)

        for query in captured.captured_queries:
            with self.subTest(sql=query["sql"][:60]):
                self.assertNotRegex(query["sql"], r"(?i)^\s*(INSERT|UPDATE|DELETE)")

    def test_the_same_place_may_be_suggested_twice_in_a_row(self):
        """No memory, by design: a repeat is correct behavior, not a bug."""
        self.make_place("Blue Bottle")

        first, _ = self.run_surprise(seed=0)
        second, _ = self.run_surprise(seed=0)

        self.assertIn("Blue Bottle", first)
        self.assertIn("Blue Bottle", second)


class SurpriseQueryCountTests(SurpriseCommandTestCase):
    """The headline names the place's tags, and ``Place.tags`` is a manager."""

    def queries_for_a_draw(self):
        with CaptureQueriesContext(connection) as captured:
            self.run_surprise(seed=0)
        return len(captured)

    def populate(self, count, offset=0):
        for index in range(offset, offset + count):
            self.make_place(f"Coffee Number {index}", tags=["coffee", "wifi"])

    def test_the_query_count_does_not_grow_with_the_journal(self):
        self.populate(4)
        few = self.queries_for_a_draw()

        self.populate(16, offset=4)
        many = self.queries_for_a_draw()

        self.assertEqual(few, many)
        self.assertLess(many, Place.objects.count())


# ---------------------------------------------------------------------------
# Coverage: the `stats` command (issue #9)
#
# Every number in the report has to come out of the database, and the one way
# this task goes wrong is the many-to-many: joining tags multiplies place rows,
# so a place with three tags gets counted three times. The trap is pinned
# below on the counts, the neighborhood breakdown and the tag rows.
#
# Rows are asserted by finding the line that carries a label and reading the
# number off the end of it -- never by matching a padded literal, per
# `_docs/testing-guidelines.md`.
# ---------------------------------------------------------------------------


#: The cap issue #9 sets on the report's query count.
STATS_QUERY_BUDGET = 5

#: What the command actually costs: one aggregate for the counts, one for the
#: neighborhoods touched, one grouped query for the breakdown, one for the
#: tags. Pinned exactly so an accidental fifth query is a failure, not a
#: shrug -- and checked against the budget above.
STATS_QUERIES = 4

#: The literal label issue #9 names for places with no neighborhood.
NO_NEIGHBORHOOD_ROW = "(no neighborhood)"


def _stats_module():
    """The ``stats`` command module, imported the way Django finds it."""
    from places.management.commands import stats as stats_module

    return stats_module


class StatsCommandDiscoveryTests(SimpleTestCase):
    """The command is registered, and takes nothing at all."""

    def parser(self):
        return load_command_class("places", "stats").create_parser("manage.py", "stats")

    def test_stats_is_registered_as_a_command_of_the_places_app(self):
        self.assertEqual(get_commands().get("stats"), "places")

    def test_stats_takes_no_arguments(self):
        """An empty argv parses; anything positional does not."""
        self.parser().parse_args([])

        with self.assertRaises((CommandError, SystemExit)):
            with redirect_stderr(StringIO()):
                self.parser().parse_args(["mission"])

    def test_the_out_of_scope_flags_are_absent(self):
        """Issue #9 rules each of these out by name: the report is one fixed
        view, with no filtering, no widening and no machine-readable form."""
        help_text = self.parser().format_help()

        for flag in (
            "--tag",
            "--neighborhood",
            "--status",
            "--top",
            "--all",
            "--limit",
            "--json",
            "--csv",
            "--since",
        ):
            with self.subTest(flag=flag):
                self.assertNotIn(flag, help_text)


class StatsAggregationRuleTests(SimpleTestCase):
    """Issue #9 requires the numbers to be computed by the database.

    Read off the parsed syntax tree rather than the source text: a docstring
    that *mentions* ``Counter`` to explain why it is not used would satisfy a
    substring search, and an accurate sentence added later would break one.
    """

    def test_stats_aggregates_in_the_database(self):
        called = _called_names(_stats_module())

        self.assertIn("Count", called)
        self.assertTrue(
            {"annotate", "aggregate"} & called,
            "the report must be built with annotate/aggregate",
        )

    def test_stats_does_not_tally_in_python(self):
        """No ``Counter``, no ``sorted``, no hand-rolled ordering: counting and
        ordering are the database's job here."""
        called = _called_names(_stats_module())

        for banned in ("Counter", "sorted", "sort", "defaultdict", "groupby"):
            with self.subTest(name=banned):
                self.assertNotIn(banned, called)

    def test_stats_does_not_import_a_counting_helper(self):
        imported = _imported_modules(_stats_module())

        for banned in ("collections", "itertools", "operator"):
            with self.subTest(module=banned):
                self.assertNotIn(banned, imported)

    def test_stats_does_not_touch_the_models_module_beyond_reading_it(self):
        """No denormalized counters: issue #2 rules them out and issue #9
        exists so none are needed. The command may only read."""
        called = _called_names(_stats_module())

        for banned in ("save", "create", "update", "add", "bulk_update"):
            with self.subTest(name=banned):
                self.assertNotIn(banned, called)


class StatsCommandTestCase(TestCase):
    """Shared plumbing: run ``stats`` through ``call_command``."""

    def make_place(self, name, tags=(), **fields):
        place = Place.objects.create(name=name, **fields)
        for tag_name in tags:
            place.tags.add(Tag.objects.get_or_create(name=tag_name)[0])
        return place

    def run_stats(self, *args):
        out, err = StringIO(), StringIO()
        call_command("stats", *args, stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def lines(self, out):
        return [line for line in out.splitlines() if line.strip()]

    def row_count(self, out, label):
        """The number on the row labelled ``label``.

        Found by the label and read as the trailing token, so the assertion
        says nothing about padding or column positions.
        """
        for line in self.lines(out):
            stripped = line.strip()
            if stripped.startswith(label):
                rest = stripped[len(label) :].strip()
                if rest.isdigit():
                    return int(rest)
        self.fail(f"no row labelled {label!r} in:\n{out}")

    def has_row(self, out, label):
        """Whether a row labelled ``label`` was printed at all."""
        for line in self.lines(out):
            stripped = line.strip()
            if stripped.startswith(label) and stripped[len(label) :].strip().isdigit():
                return True
        return False

    def order_of(self, out, labels):
        """The given labels, in the order their rows appear in ``out``."""
        found = []
        for line in self.lines(out):
            stripped = line.strip()
            for label in labels:
                if stripped.startswith(label) and label not in found:
                    found.append(label)
        return found

    def section_rows(self, out, heading):
        """The rows under ``heading``, up to the next heading or blank line."""
        rows, collecting = [], False
        for line in out.splitlines():
            if line.strip() == heading:
                collecting = True
                continue
            if collecting:
                if not line.strip():
                    break
                rows.append(line.strip())
        return rows


class StatsEmptyJournalTests(StatsCommandTestCase):
    """An empty journal is news, not a table of zeros."""

    def headings(self):
        module = _stats_module()
        return (
            module.COUNTS_HEADING,
            module.TOUCHED_HEADING,
            module.NEIGHBORHOOD_HEADING,
            module.TAGS_HEADING,
        )

    def test_an_empty_journal_says_so_on_stdout_and_exits_zero(self):
        out, _ = self.run_stats()

        self.assertIn("Nothing recorded yet", out)

    def test_the_empty_message_points_at_add_as_the_next_step(self):
        out, _ = self.run_stats()

        self.assertIn("add", out)

    def test_the_empty_report_carries_none_of_the_four_headings(self):
        out, _ = self.run_stats()

        for heading in self.headings():
            with self.subTest(heading=heading):
                self.assertNotIn(heading, out)

    def test_the_empty_report_prints_no_counts_at_all(self):
        """Not a single digit, so there is no zero-valued table to misread."""
        out, _ = self.run_stats()

        self.assertFalse(any(character.isdigit() for character in out), out)

    def test_the_empty_report_prints_no_neighborhood_or_tag_rows(self):
        out, _ = self.run_stats()

        self.assertNotIn(NO_NEIGHBORHOOD_ROW, out)
        self.assertEqual(len(self.lines(out)), 1)

    def test_tags_with_no_places_is_still_the_empty_case(self):
        """Tags belonging to nothing are not a journal."""
        Tag.objects.create(name="coffee")
        Tag.objects.create(name="ramen")

        out, _ = self.run_stats()

        self.assertIn("Nothing recorded yet", out)
        # "coffee" appears in the example command the message suggests, so the
        # assertion is that no tag *row* was printed, not that the word is absent.
        self.assertFalse(self.has_row(out, "coffee"), out)
        self.assertEqual(len(self.lines(out)), 1)


class StatsSectionTests(StatsCommandTestCase):
    """Four sections, in one fixed order, stable across runs."""

    def setUp(self):
        self.make_place("Blue Bottle", neighborhood="Mission", tags=["coffee"])
        self.make_place("Nopalito", neighborhood="Soma", status=Place.Status.VISITED)

    def test_the_four_headings_are_printed_in_the_documented_order(self):
        module = _stats_module()
        headings = [
            module.COUNTS_HEADING,
            module.TOUCHED_HEADING,
            module.NEIGHBORHOOD_HEADING,
            module.TAGS_HEADING,
        ]

        out, _ = self.run_stats()

        positions = [out.index(heading) for heading in headings]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(len(set(positions)), len(headings))

    def test_two_runs_on_the_same_data_print_the_same_screen(self):
        first, _ = self.run_stats()
        second, _ = self.run_stats()

        self.assertEqual(first, second)

    def test_the_report_writes_nothing_to_stderr(self):
        _, err = self.run_stats()

        self.assertEqual(err, "")


class StatsCountTests(StatsCommandTestCase):
    """The three headline numbers, and the m2m row-multiplication trap."""

    def test_the_total_equals_the_number_of_places(self):
        for index in range(4):
            self.make_place(f"Place {index}")

        out, _ = self.run_stats()

        self.assertEqual(self.row_count(out, "total places"), Place.objects.count())

    def test_visited_and_wishlist_sum_to_the_total(self):
        for index in range(3):
            self.make_place(f"Been {index}", status=Place.Status.VISITED)
        for index in range(2):
            self.make_place(f"Want {index}")

        out, _ = self.run_stats()

        visited = self.row_count(out, "visited")
        wishlist = self.row_count(out, "wishlist")
        self.assertEqual((visited, wishlist), (3, 2))
        self.assertEqual(visited + wishlist, self.row_count(out, "total places"))

    def test_a_place_with_three_tags_is_counted_once(self):
        """The many-to-many join multiplies place rows; the aggregation must
        count distinct places, not joined rows. Issue #9 calls this the single
        most likely way to get the task wrong."""
        self.make_place(
            "Blue Bottle", neighborhood="Mission", tags=["coffee", "wifi", "cheap"]
        )

        out, _ = self.run_stats()

        self.assertEqual(self.row_count(out, "total places"), 1)
        self.assertEqual(self.row_count(out, "wishlist"), 1)
        self.assertEqual(self.row_count(out, "visited"), 0)
        self.assertEqual(self.row_count(out, "Mission"), 1)

    def test_tags_do_not_multiply_a_neighborhoods_rows_either(self):
        """Two places in one neighborhood, one of them heavily tagged."""
        self.make_place(
            "Blue Bottle", neighborhood="Mission", tags=["coffee", "wifi", "cheap"]
        )
        self.make_place("Tartine", neighborhood="Mission")

        out, _ = self.run_stats()

        self.assertEqual(self.row_count(out, "Mission"), 2)
        self.assertEqual(self.row_count(out, "total places"), 2)

    def test_a_place_with_no_tags_is_still_counted_everywhere(self):
        self.make_place("Bare Bar", neighborhood="Soma", status=Place.Status.VISITED)

        out, _ = self.run_stats()

        self.assertEqual(self.row_count(out, "total places"), 1)
        self.assertEqual(self.row_count(out, "visited"), 1)
        self.assertEqual(self.row_count(out, "Soma"), 1)

    def test_the_per_neighborhood_counts_sum_to_the_total(self):
        self.make_place("Blue Bottle", neighborhood="Mission", tags=["coffee", "wifi"])
        self.make_place("Tartine", neighborhood="Mission")
        self.make_place("Nopalito", neighborhood="Soma")
        self.make_place("Nameless", neighborhood="")

        out, _ = self.run_stats()

        rows = self.section_rows(out, _stats_module().NEIGHBORHOOD_HEADING)
        totals = [int(row.rsplit(None, 1)[-1]) for row in rows]
        self.assertEqual(sum(totals), self.row_count(out, "total places"))
        self.assertEqual(sum(totals), 4)


class StatsBlankNeighborhoodTests(StatsCommandTestCase):
    """Blank is a bucket, and it is not a neighborhood touched."""

    def test_an_empty_neighborhood_lands_under_the_literal_label(self):
        self.make_place("Nameless", neighborhood="")

        out, _ = self.run_stats()

        self.assertEqual(self.row_count(out, NO_NEIGHBORHOOD_ROW), 1)

    def test_a_whitespace_only_neighborhood_joins_the_same_bucket(self):
        """Not its own row -- one bucket, whatever the blank looks like."""
        self.make_place("Nameless", neighborhood="")
        self.make_place("Spacey", neighborhood="   ")

        out, _ = self.run_stats()

        self.assertEqual(self.row_count(out, NO_NEIGHBORHOOD_ROW), 2)
        rows = self.section_rows(out, _stats_module().NEIGHBORHOOD_HEADING)
        self.assertEqual(len(rows), 1)

    def test_blank_does_not_count_as_a_neighborhood_touched(self):
        """Issue #9's worked example: two in Mission, one with none, touched
        reads 1 while the breakdown shows two rows."""
        self.make_place("Blue Bottle", neighborhood="Mission")
        self.make_place("Tartine", neighborhood="Mission")
        self.make_place("Nameless", neighborhood="")

        out, _ = self.run_stats()

        self.assertEqual(self.row_count(out, "Neighborhoods touched:"), 1)
        rows = self.section_rows(out, _stats_module().NEIGHBORHOOD_HEADING)
        self.assertEqual(len(rows), 2)

    def test_a_whitespace_only_neighborhood_is_not_a_neighborhood_touched(self):
        self.make_place("Blue Bottle", neighborhood="Mission")
        self.make_place("Spacey", neighborhood="   ")

        out, _ = self.run_stats()

        self.assertEqual(self.row_count(out, "Neighborhoods touched:"), 1)

    def test_touched_equals_the_rows_minus_the_blank_row(self):
        self.make_place("Blue Bottle", neighborhood="Mission")
        self.make_place("Nopalito", neighborhood="Soma")
        self.make_place("Ippudo", neighborhood="Nob Hill")
        self.make_place("Nameless", neighborhood="")

        out, _ = self.run_stats()

        rows = self.section_rows(out, _stats_module().NEIGHBORHOOD_HEADING)
        blank_rows = [row for row in rows if row.startswith(NO_NEIGHBORHOOD_ROW)]
        self.assertEqual(len(blank_rows), 1)
        self.assertEqual(
            self.row_count(out, "Neighborhoods touched:"), len(rows) - len(blank_rows)
        )

    def test_touched_equals_the_row_count_when_nothing_is_blank(self):
        self.make_place("Blue Bottle", neighborhood="Mission")
        self.make_place("Nopalito", neighborhood="Soma")

        out, _ = self.run_stats()

        rows = self.section_rows(out, _stats_module().NEIGHBORHOOD_HEADING)
        self.assertEqual(self.row_count(out, "Neighborhoods touched:"), len(rows))
        self.assertEqual(len(rows), 2)

    def test_no_blank_row_is_printed_when_every_place_has_a_neighborhood(self):
        self.make_place("Blue Bottle", neighborhood="Mission")
        self.make_place("Nopalito", neighborhood="Soma")

        out, _ = self.run_stats()

        self.assertNotIn(NO_NEIGHBORHOOD_ROW, out)

    def test_the_blank_bucket_still_counts_towards_the_total(self):
        self.make_place("Nameless", neighborhood="")
        self.make_place("Blue Bottle", neighborhood="Mission")

        out, _ = self.run_stats()

        self.assertEqual(self.row_count(out, "total places"), 2)
        self.assertEqual(self.row_count(out, "Neighborhoods touched:"), 1)


class StatsNeighborhoodOrderingTests(StatsCommandTestCase):
    """Counts descending, then name ascending, and blank always last."""

    def test_rows_are_ordered_by_count_then_name(self):
        """Issue #9's worked example: Soma=2, Mission=2, Nob Hill=1 prints as
        Mission, Soma, Nob Hill."""
        for index in range(2):
            self.make_place(f"Soma {index}", neighborhood="Soma")
        for index in range(2):
            self.make_place(f"Mission {index}", neighborhood="Mission")
        self.make_place("Nob", neighborhood="Nob Hill")

        out, _ = self.run_stats()

        self.assertEqual(
            self.order_of(out, ["Mission", "Soma", "Nob Hill"]),
            ["Mission", "Soma", "Nob Hill"],
        )

    def test_the_name_tie_break_is_ascending_not_descending(self):
        """Created in the order that a descending sort would already produce,
        so only an ascending tie-break can pass."""
        self.make_place("A place", neighborhood="Zuma")
        self.make_place("B place", neighborhood="Alamo")

        out, _ = self.run_stats()

        self.assertEqual(self.order_of(out, ["Alamo", "Zuma"]), ["Alamo", "Zuma"])

    def test_the_name_tie_break_ignores_case(self):
        self.make_place("A place", neighborhood="Zuma")
        self.make_place("B place", neighborhood="alamo")

        out, _ = self.run_stats()

        self.assertEqual(self.order_of(out, ["alamo", "Zuma"]), ["alamo", "Zuma"])

    def test_two_neighborhoods_differing_only_by_case_have_a_fixed_order(self):
        """``Soma`` and ``soma`` are separate rows -- ``Place.neighborhood``
        has no ``NOCASE`` collation, so the database groups them apart -- and
        they tie on count *and* on the case-insensitive name key. Nothing but
        the final case-sensitive tiebreak separates them, and without it
        SQLite's row order for fully-tied keys is unspecified: the report
        would be free to swap the two rows between runs.
        """
        for index in range(2):
            self.make_place(f"Upper {index}", neighborhood="Soma")
        for index in range(2):
            self.make_place(f"Lower {index}", neighborhood="soma")

        runs = [self.run_stats()[0] for _ in range(4)]

        for out in runs:
            with self.subTest(out=out):
                self.assertEqual(self.row_count(out, "Soma"), 2)
                self.assertEqual(self.row_count(out, "soma"), 2)
                # Uppercase first: the tiebreak sorts the stored spelling, and
                # SQLite compares those bytes with `S` below `s`.
                self.assertEqual(
                    self.order_of(out, ["Soma", "soma"]), ["Soma", "soma"]
                )
        self.assertEqual(len(set(runs)), 1)

    def test_the_blank_row_is_last_even_when_it_has_the_largest_count(self):
        for index in range(5):
            self.make_place(f"Nameless {index}", neighborhood="")
        self.make_place("Blue Bottle", neighborhood="Mission")

        out, _ = self.run_stats()

        self.assertEqual(self.row_count(out, NO_NEIGHBORHOOD_ROW), 5)
        self.assertEqual(
            self.order_of(out, ["Mission", NO_NEIGHBORHOOD_ROW]),
            ["Mission", NO_NEIGHBORHOOD_ROW],
        )

    def test_the_blank_row_is_last_among_several_neighborhoods(self):
        for index in range(3):
            self.make_place(f"Nameless {index}", neighborhood="")
        self.make_place("Blue Bottle", neighborhood="Mission")
        self.make_place("Nopalito", neighborhood="Soma")

        out, _ = self.run_stats()

        rows = self.section_rows(out, _stats_module().NEIGHBORHOOD_HEADING)
        self.assertTrue(rows[-1].startswith(NO_NEIGHBORHOOD_ROW), rows)


class StatsTagTests(StatsCommandTestCase):
    """The tag section: distinct places per tag, ordered, capped at five."""

    def tag_rows(self, out):
        return self.section_rows(out, _stats_module().TAGS_HEADING)

    def test_each_tag_row_counts_the_distinct_places_carrying_it(self):
        self.make_place("Blue Bottle", tags=["coffee", "wifi"])
        self.make_place("Tartine", tags=["coffee"])

        out, _ = self.run_stats()

        self.assertEqual(self.row_count(out, "coffee"), 2)
        self.assertEqual(self.row_count(out, "wifi"), 1)

    def test_tags_are_ordered_by_count_then_name(self):
        """Issue #9's worked example: ramen=2, coffee=2, park=1 prints as
        coffee, ramen, park."""
        self.make_place("Ramen One", tags=["ramen"])
        self.make_place("Ramen Two", tags=["ramen"])
        self.make_place("Coffee One", tags=["coffee"])
        self.make_place("Coffee Two", tags=["coffee"])
        self.make_place("Dolores", tags=["park"])

        out, _ = self.run_stats()

        self.assertEqual(
            self.order_of(out, ["coffee", "ramen", "park"]),
            ["coffee", "ramen", "park"],
        )

    def test_the_tag_name_tie_break_is_ascending_not_descending(self):
        """Created so that only an ascending tie-break gives this order."""
        self.make_place("Zed", tags=["zzz"])
        self.make_place("Ay", tags=["aaa"])

        out, _ = self.run_stats()

        self.assertEqual(self.order_of(out, ["aaa", "zzz"]), ["aaa", "zzz"])

    def test_at_most_five_tags_are_shown(self):
        place = self.make_place("Everything")
        for index in range(7):
            place.tags.add(Tag.objects.create(name=f"tag{index}"))

        out, _ = self.run_stats()

        self.assertEqual(len(self.tag_rows(out)), 5)

    def test_fewer_than_five_tags_are_all_shown(self):
        place = self.make_place("Three Tags")
        for name in ("coffee", "ramen", "park"):
            place.tags.add(Tag.objects.create(name=name))

        out, _ = self.run_stats()

        self.assertEqual(len(self.tag_rows(out)), 3)

    def test_the_cut_at_five_breaks_ties_by_name(self):
        """Six tags, all on one place, so all six tie on count. The cut must
        keep the five alphabetically first and drop the sixth -- and do it the
        same way on every run."""
        place = self.make_place("Everything")
        for name in ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot"):
            place.tags.add(Tag.objects.create(name=name))

        first, _ = self.run_stats()
        second, _ = self.run_stats()

        rows = self.tag_rows(first)
        self.assertEqual(len(rows), 5)
        self.assertTrue(self.has_row(first, "echo"), first)
        self.assertFalse(self.has_row(first, "foxtrot"), first)
        self.assertEqual(self.tag_rows(second), rows)

    def test_the_cut_at_five_keeps_the_higher_count_over_the_name(self):
        """Count beats name: a late-alphabet tag on two places outranks an
        early-alphabet tag on one."""
        for index in range(2):
            self.make_place(f"Zed {index}", tags=["zulu"])
        place = self.make_place("Everything")
        for name in ("alpha", "bravo", "charlie", "delta", "echo"):
            place.tags.add(Tag.objects.create(name=name))

        out, _ = self.run_stats()

        self.assertEqual(self.row_count(out, "zulu"), 2)
        self.assertEqual(self.order_of(out, ["zulu", "alpha"]), ["zulu", "alpha"])
        self.assertFalse(self.has_row(out, "echo"), out)

    def test_a_tag_no_place_uses_is_never_listed(self):
        self.make_place("Blue Bottle", tags=["coffee"])
        Tag.objects.create(name="unused")

        out, _ = self.run_stats()

        self.assertNotIn("unused", out)
        self.assertEqual(len(self.tag_rows(out)), 1)

    def test_places_with_no_tags_at_all_get_a_line_not_an_empty_section(self):
        self.make_place("Bare Bar", neighborhood="Mission")

        out, _ = self.run_stats()

        rows = self.tag_rows(out)
        self.assertEqual(len(rows), 1)
        self.assertIn("No tags used yet", rows[0])

    def test_the_no_tags_line_is_not_a_zero_row(self):
        self.make_place("Bare Bar")
        Tag.objects.create(name="unused")

        out, _ = self.run_stats()

        self.assertFalse(self.has_row(out, "unused"), out)


class StatsFormattingTests(StatsCommandTestCase):
    """Readable columns, without pinning the padding itself."""

    def test_a_long_neighborhood_name_does_not_push_lines_past_eighty_columns(self):
        self.make_place("Long One", neighborhood="X" * 100)
        self.make_place("Short One", neighborhood="Soma")
        self.make_place("Other", neighborhood="Nob Hill")

        out, _ = self.run_stats()

        rows = self.section_rows(out, _stats_module().NEIGHBORHOOD_HEADING)
        for row in rows:
            if len(row) < 80:
                continue
            # Only the over-long name itself may exceed the terminal; every
            # other row must stay inside it rather than being padded to match.
            self.assertTrue(row.startswith("X" * 100), row)

    def test_short_rows_stay_short_when_one_name_is_very_long(self):
        self.make_place("Long One", neighborhood="X" * 100)
        self.make_place("Short One", neighborhood="Soma")

        out, _ = self.run_stats()

        rows = self.section_rows(out, _stats_module().NEIGHBORHOOD_HEADING)
        short = [row for row in rows if row.startswith("Soma")]
        self.assertEqual(len(short), 1)
        self.assertLess(len(short[0]), 80)

    def test_every_row_carries_its_count_on_its_own_line(self):
        self.make_place("Blue Bottle", neighborhood="Mission", tags=["coffee"])

        out, _ = self.run_stats()

        for heading in (
            _stats_module().NEIGHBORHOOD_HEADING,
            _stats_module().TAGS_HEADING,
        ):
            for row in self.section_rows(out, heading):
                with self.subTest(row=row):
                    self.assertTrue(row.rsplit(None, 1)[-1].isdigit(), row)


class StatsQueryCountTests(StatsCommandTestCase):
    """A report whose cost grows with the journal is a report I stop running."""

    def populate(self, count, offset=0):
        """``count`` places spread across many neighborhoods and many tags."""
        for index in range(offset, offset + count):
            self.make_place(
                f"Place {index}",
                neighborhood=f"Neighborhood {index % 7}",
                status=(
                    Place.Status.VISITED if index % 2 else Place.Status.WISHLIST
                ),
                tags=[f"tag{index % 9}", f"tag{(index + 3) % 9}"],
            )

    def queries_for_a_report(self):
        with CaptureQueriesContext(connection) as captured:
            self.run_stats()
        return len(captured)

    def test_the_budget_is_the_one_issue_nine_sets(self):
        self.assertLessEqual(STATS_QUERIES, STATS_QUERY_BUDGET)

    def test_the_query_count_is_fixed_with_three_places(self):
        self.populate(3)

        with self.assertNumQueries(STATS_QUERIES):
            self.run_stats()

    def test_the_query_count_is_the_same_with_thirty_places(self):
        self.populate(30)

        with self.assertNumQueries(STATS_QUERIES):
            self.run_stats()

    def test_the_query_count_does_not_grow_with_the_journal(self):
        self.populate(3)
        few = self.queries_for_a_report()

        self.populate(27, offset=3)
        many = self.queries_for_a_report()

        self.assertEqual(few, many)
        self.assertLessEqual(many, STATS_QUERY_BUDGET)

    def test_the_empty_report_costs_no_more_than_the_full_one(self):
        with self.assertNumQueries(1):
            self.run_stats()


class StatsWorkedExampleTests(StatsCommandTestCase):
    """One populated journal, read end to end the way issue #9 describes it."""

    def setUp(self):
        self.make_place(
            "Blue Bottle",
            neighborhood="Mission",
            status=Place.Status.VISITED,
            tags=["coffee", "wifi", "cheap"],
        )
        self.make_place(
            "Tartine", neighborhood="Mission", status=Place.Status.VISITED,
            tags=["coffee"],
        )
        self.make_place("Nopalito", neighborhood="Soma", status=Place.Status.VISITED)
        self.make_place("Ippudo", neighborhood="Soma", tags=["ramen"])
        self.make_place("Mystery Bar", neighborhood="", tags=["cheap"])

    def test_the_whole_report_reads_correctly(self):
        out, _ = self.run_stats()

        self.assertEqual(self.row_count(out, "total places"), 5)
        self.assertEqual(self.row_count(out, "visited"), 3)
        self.assertEqual(self.row_count(out, "wishlist"), 2)
        self.assertEqual(self.row_count(out, "Neighborhoods touched:"), 2)
        self.assertEqual(self.row_count(out, "Mission"), 2)
        self.assertEqual(self.row_count(out, "Soma"), 2)
        self.assertEqual(self.row_count(out, NO_NEIGHBORHOOD_ROW), 1)
        self.assertEqual(self.row_count(out, "coffee"), 2)
        self.assertEqual(self.row_count(out, "cheap"), 2)
        self.assertEqual(self.row_count(out, "ramen"), 1)
        self.assertEqual(self.row_count(out, "wifi"), 1)

    def test_the_neighborhood_rows_are_in_the_documented_order(self):
        out, _ = self.run_stats()

        self.assertEqual(
            self.order_of(out, ["Mission", "Soma", NO_NEIGHBORHOOD_ROW]),
            ["Mission", "Soma", NO_NEIGHBORHOOD_ROW],
        )

    def test_the_tag_rows_are_in_the_documented_order(self):
        out, _ = self.run_stats()

        self.assertEqual(
            self.order_of(out, ["cheap", "coffee", "ramen", "wifi"]),
            ["cheap", "coffee", "ramen", "wifi"],
        )
