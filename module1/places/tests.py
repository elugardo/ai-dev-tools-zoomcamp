from django.apps import apps
from django.test import SimpleTestCase


class ProjectSkeletonTests(SimpleTestCase):
    """Smoke tests proving the places app is wired into the project."""

    def test_places_app_is_installed(self):
        self.assertTrue(apps.is_installed("places"))
