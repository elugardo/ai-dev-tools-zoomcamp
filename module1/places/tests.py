import ast
import inspect
import unittest
import warnings
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
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

from places import search
from places.models import Place, Tag, normalize_tag_name


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
    """Two rules issue #4 states as a grep over ``add.py``.

    These are deliberately source-level rather than behavioral: both describe a
    *mechanism* the command must reuse instead of reimplementing, and a
    reimplementation would pass every behavioral test right up until the day
    the shared normalizer or the choices class changes.
    """

    def test_add_does_not_hand_roll_tag_normalization(self):
        source = inspect.getsource(_add_module())

        self.assertNotIn("lower", source)
        self.assertNotIn("iexact", source)
        self.assertIn("normalize_tag_name", source)

    def test_add_refers_to_the_status_choices_class_not_to_string_literals(self):
        source = inspect.getsource(_add_module())

        self.assertNotIn('"%s"' % Place.Status.WISHLIST.value, source)
        self.assertNotIn('"%s"' % Place.Status.VISITED.value, source)
        self.assertIn("Place.Status", source)


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

    def source(self):
        return inspect.getsource(search)

    def test_the_module_imports_neither_the_models_nor_django(self):
        imported = set()
        for node in ast.walk(ast.parse(self.source())):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")

        for module in imported:
            self.assertFalse(
                module == "places.models"
                or module.startswith("django")
                or module.startswith("places.management"),
                "search.py must stay pure but imports " + module,
            )

    def test_the_module_performs_no_database_access_of_its_own(self):
        source = self.source()

        self.assertNotIn(".objects", source)
        self.assertNotIn("select_related", source)
        self.assertNotIn("prefetch_related", source)
        self.assertNotIn("filter(", source)

    def test_the_module_neither_prints_nor_writes(self):
        """Calls, not text: the docstring shows the caller printing, which is
        the point -- the module returns a value and prints nothing itself."""
        called = set()
        for node in ast.walk(ast.parse(self.source())):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    called.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    called.add(node.func.attr)

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
        tree = ast.parse(self.source())
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
    """Rules issue #6 states as a grep over ``find.py``.

    Deliberately source-level: each describes a mechanism the command must
    *delegate* rather than reimplement, and a reimplementation would pass every
    behavioral test right up until the ranker or the normalizer changes.
    """

    def test_find_delegates_ranking_and_owns_no_scoring_of_its_own(self):
        source = inspect.getsource(_find_module())

        self.assertIn("rank_places", source)
        self.assertNotIn("rapidfuzz", source)
        self.assertNotIn("fuzz", source)
        self.assertNotIn("STRONG_MATCH_THRESHOLD", source)

    def test_find_does_not_re_sort_what_the_ranker_returned(self):
        source = inspect.getsource(_find_module())

        self.assertNotIn("sorted(", source)
        self.assertNotIn(".sort(", source)

    def test_find_does_not_hand_roll_tag_normalization(self):
        source = inspect.getsource(_find_module())

        self.assertIn("normalize_tag_name", source)
        self.assertNotIn("lower()", source)
        self.assertNotIn("iexact", source)


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
