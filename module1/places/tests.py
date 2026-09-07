import unittest

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.test import SimpleTestCase, TestCase

from places.models import Place, Tag, normalize_tag_name


class ProjectSkeletonTests(SimpleTestCase):
    """Smoke tests proving the places app is wired into the project."""

    def test_places_app_is_installed(self):
        self.assertTrue(apps.is_installed("places"))


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

    def test_a_genuine_duplicate_still_violates_the_unique_constraint(self):
        Tag.objects.create(name="coffee")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Tag.objects.create(name="coffee")

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
