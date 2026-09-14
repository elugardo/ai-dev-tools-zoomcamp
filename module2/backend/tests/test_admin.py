"""Admin restaurant management (spec §33 items 11-12)."""

import pytest

from tests.conftest import party, restaurant_body


@pytest.fixture
def admin(api):
    return api.login("admin")


class TestList:
    def test_lists_every_restaurant_with_its_username(self, api, admin):
        api.patch("/admin/restaurants/1/status", json={"is_active": False}, headers=admin)
        body = api.get("/admin/restaurants", headers=admin).json()
        assert [(r["name"], r["is_active"], r["username"], r["no_show_minutes"]) for r in body] == [
            ("Bluebird Cafe", False, "bluebird", 10),
            ("Oak & Ember", True, "oakember", 10),
        ]

    def test_get_one(self, api, admin):
        assert api.get("/admin/restaurants/2", headers=admin).json()["username"] == "oakember"

    def test_get_missing_is_404(self, api, admin):
        assert api.get("/admin/restaurants/999", headers=admin).status_code == 404


class TestCreate:
    def test_creates_a_restaurant_whose_login_works(self, api, admin):
        response = api.post("/admin/restaurants", json=restaurant_body(username="  Harbor "), headers=admin)
        assert response.status_code == 201
        created = response.json()
        assert (created["name"], created["username"], created["online_waitlist_enabled"]) == (
            "Harbor Noodle",
            "harbor",
            True,
        )
        assert created["waiting_parties"] == 0

        login = api.post("/auth/login", json={"username": "harbor", "password": "noodles-123"})
        assert login.json()["user"]["restaurant_id"] == created["id"]

    def test_new_restaurant_is_public_and_joinable(self, api, admin):
        created = api.post("/admin/restaurants", json=restaurant_body(), headers=admin).json()
        assert "Harbor Noodle" in [r["name"] for r in api.get("/restaurants").json()]
        joined = api.post(f"/restaurants/{created['id']}/waitlist", json=party()).json()
        assert joined["quoted_wait_minutes"] == 15

    def test_stores_only_a_hash_of_the_password(self, api, admin):
        api.post("/admin/restaurants", json=restaurant_body(), headers=admin)
        with api.db() as store:
            stored = store.user_by_username("harbor").password_hash
        assert stored.startswith("scrypt$")
        assert "noodles-123" not in stored

    @pytest.mark.parametrize(
        ("password", "message"),
        [("", "Password is required."), ("short", "Password must be at least 8 characters.")],
    )
    def test_requires_a_real_password(self, api, admin, password, message):
        response = api.post("/admin/restaurants", json=restaurant_body(password=password), headers=admin)
        assert response.status_code == 422
        assert response.json()["field_errors"] == {"password": message}

    def test_username_already_taken_is_409(self, api, admin):
        response = api.post("/admin/restaurants", json=restaurant_body(username="OakEmber"), headers=admin)
        assert response.status_code == 409
        assert response.json() == {
            "code": "CONFLICT",
            "message": "That username is already taken.",
            "field_errors": {"username": "That username is already taken."},
        }

    def test_the_admin_username_is_taken_too(self, api, admin):
        assert api.post("/admin/restaurants", json=restaurant_body(username="admin"), headers=admin).status_code == 409

    def test_validation_errors_name_every_bad_field(self, api, admin):
        body = restaurant_body(
            name=" ", address="", phone="nope", description="x" * 501,
            current_wait_minutes=-5, no_show_minutes=0, username="blue bird",
        )
        response = api.post("/admin/restaurants", json=body, headers=admin)
        assert response.status_code == 422
        assert set(response.json()["field_errors"]) == {
            "name", "address", "phone", "description", "current_wait_minutes", "no_show_minutes", "username",
        }

    def test_defaults_match_the_spec(self, api, admin):
        body = restaurant_body()
        for field in ("description", "current_wait_minutes", "no_show_minutes", "is_active"):
            del body[field]
        created = api.post("/admin/restaurants", json=body, headers=admin).json()
        assert (created["current_wait_minutes"], created["no_show_minutes"], created["is_active"]) == (30, 10, True)


class TestEdit:
    def test_edits_fields_and_renames_the_login(self, api, admin):
        response = api.put(
            "/admin/restaurants/1",
            json=restaurant_body(name="Bluebird Bistro", username="bistro", password="", current_wait_minutes=5),
            headers=admin,
        )
        assert response.status_code == 200
        body = response.json()
        assert (body["name"], body["username"], body["current_wait_minutes"]) == ("Bluebird Bistro", "bistro", 5)

        assert api.post("/auth/login", json={"username": "bistro", "password": "password"}).status_code == 200
        assert api.post("/auth/login", json={"username": "bluebird", "password": "password"}).status_code == 401

    def test_blank_password_keeps_the_current_one(self, api, admin):
        api.put("/admin/restaurants/1", json=restaurant_body(username="bluebird", password=""), headers=admin)
        assert api.post("/auth/login", json={"username": "bluebird", "password": "password"}).status_code == 200

    def test_new_password_replaces_the_old_one(self, api, admin):
        api.put("/admin/restaurants/1", json=restaurant_body(username="bluebird", password="new-secret-1"), headers=admin)
        assert api.post("/auth/login", json={"username": "bluebird", "password": "password"}).status_code == 401
        assert api.post("/auth/login", json={"username": "bluebird", "password": "new-secret-1"}).status_code == 200

    def test_short_new_password_is_rejected(self, api, admin):
        response = api.put("/admin/restaurants/1", json=restaurant_body(username="bluebird", password="abc"), headers=admin)
        assert response.json()["field_errors"] == {"password": "Password must be at least 8 characters."}

    def test_keeping_its_own_username_is_not_a_conflict(self, api, admin):
        response = api.put("/admin/restaurants/1", json=restaurant_body(username="bluebird", password=""), headers=admin)
        assert response.status_code == 200

    def test_taking_another_restaurants_username_is_409(self, api, admin):
        response = api.put("/admin/restaurants/1", json=restaurant_body(username="oakember", password=""), headers=admin)
        assert response.status_code == 409

    def test_leaves_online_waitlist_setting_alone(self, api, admin):
        api.patch("/restaurant/settings", json={"online_waitlist_enabled": False}, headers=api.login("bluebird"))
        body = api.put("/admin/restaurants/1", json=restaurant_body(username="bluebird", password=""), headers=admin).json()
        assert body["online_waitlist_enabled"] is False

    def test_edit_missing_restaurant_is_404(self, api, admin):
        assert api.put("/admin/restaurants/999", json=restaurant_body(), headers=admin).status_code == 404


class TestStatus:
    def test_deactivate_and_reactivate(self, api, admin):
        off = api.patch("/admin/restaurants/1/status", json={"is_active": False}, headers=admin)
        assert (off.status_code, off.json()["is_active"]) == (200, False)
        on = api.patch("/admin/restaurants/1/status", json={"is_active": True}, headers=admin)
        assert on.json()["is_active"] is True
        assert len(api.get("/restaurants").json()) == 2

    def test_staff_of_an_inactive_restaurant_can_still_work(self, api, admin):
        api.patch("/admin/restaurants/1/status", json={"is_active": False}, headers=admin)
        assert api.get("/restaurant/waitlist", headers=api.login("bluebird")).status_code == 200

    def test_requires_is_active(self, api, admin):
        response = api.patch("/admin/restaurants/1/status", json={}, headers=admin)
        assert response.status_code == 422
        assert "is_active" in response.json()["field_errors"]

    def test_missing_restaurant_is_404(self, api, admin):
        assert api.patch("/admin/restaurants/999/status", json={"is_active": True}, headers=admin).status_code == 404
