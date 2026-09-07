import unittest
import warnings
from datetime import datetime, timezone

from django.apps import apps
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.paginator import UnorderedObjectListWarning
from django.db import IntegrityError, connection, transaction
from django.db.models import Q
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

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
