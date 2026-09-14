"""Password hashing, login, bearer tokens and role checks."""

import pytest

from app.auth import hash_password, verify_password
from app.models import UserRole
from tests.conftest import FAST_SCRYPT_N, restaurant_body


class TestPasswordHashing:
    def test_verifies_the_right_password_only(self):
        stored = hash_password("correct horse", n=FAST_SCRYPT_N)
        assert verify_password("correct horse", stored)
        assert not verify_password("correct hors", stored)

    def test_salts_every_hash(self):
        assert hash_password("same", n=FAST_SCRYPT_N) != hash_password("same", n=FAST_SCRYPT_N)

    def test_never_stores_the_plain_password(self):
        stored = hash_password("hunter2hunter2", n=FAST_SCRYPT_N)
        assert "hunter2" not in stored
        assert stored.startswith(f"scrypt${FAST_SCRYPT_N}$")

    @pytest.mark.parametrize("garbage", ["", "plain-text", "bcrypt$1$2$3$4$5", "scrypt$x$8$1$abc$def"])
    def test_rejects_malformed_hashes_instead_of_crashing(self, garbage):
        assert not verify_password("anything", garbage)


class TestLogin:
    def test_returns_a_token_and_the_user(self, api):
        response = api.post("/auth/login", json={"username": "  BlueBird ", "password": "password"})
        assert response.status_code == 200
        body = response.json()
        assert body["user"] == {"id": 2, "username": "bluebird", "role": "RESTAURANT", "restaurant_id": 1}
        assert len(body["token"]) >= 32

    def test_admin_has_no_restaurant(self, api):
        body = api.post("/auth/login", json={"username": "admin", "password": "password"}).json()
        assert body["user"]["role"] == "ADMIN"
        assert body["user"]["restaurant_id"] is None

    def test_wrong_password_is_rejected(self, api):
        response = api.post("/auth/login", json={"username": "bluebird", "password": "not-it"})
        assert response.status_code == 401
        assert response.json()["code"] == "UNAUTHORIZED"

    def test_unknown_user_gets_the_same_message_as_a_wrong_password(self, api):
        unknown = api.post("/auth/login", json={"username": "ghost", "password": "password"}).json()
        wrong = api.post("/auth/login", json={"username": "bluebird", "password": "nope"}).json()
        assert unknown["message"] == wrong["message"] == "Incorrect username or password."

    def test_blank_fields_are_validation_errors(self, api):
        response = api.post("/auth/login", json={"username": " ", "password": ""})
        assert response.status_code == 422
        assert set(response.json()["field_errors"]) == {"username", "password"}

    def test_each_login_gets_a_distinct_token(self, api):
        assert api.login("bluebird") != api.login("bluebird")


class TestBearerTokens:
    def test_missing_token_is_401_with_a_bearer_challenge(self, api):
        response = api.get("/restaurant/waitlist")
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
        assert response.json()["code"] == "UNAUTHORIZED"

    @pytest.mark.parametrize("header", ["Bearer not-a-real-token", "Basic Ymx1ZWJpcmQ6cGFzc3dvcmQ=", "Bearer "])
    def test_bad_tokens_are_401(self, api, header):
        assert api.get("/restaurant/waitlist", headers={"Authorization": header}).status_code == 401

    def test_token_expires_after_the_ttl(self, api):
        headers = api.login("bluebird")
        api.clock.advance(minutes=12 * 60 - 1)
        assert api.get("/restaurant/waitlist", headers=headers).status_code == 200
        api.clock.advance(minutes=1)
        response = api.get("/restaurant/waitlist", headers=headers)
        assert response.status_code == 401
        assert response.json()["message"] == "Your session has expired. Please log in again."

    def test_changing_a_restaurant_password_signs_out_its_sessions(self, api):
        staff = api.login("bluebird")
        admin = api.login("admin")
        api.put("/admin/restaurants/1", json=restaurant_body(username="bluebird", password="brand-new-pass"), headers=admin)

        assert api.get("/restaurant/waitlist", headers=staff).status_code == 401
        assert api.login("bluebird", "brand-new-pass")


class TestRoles:
    RESTAURANT_ENDPOINTS = [
        ("get", "/restaurant/waitlist", None),
        ("post", "/restaurant/waitlist", {"guest_name": "A", "mobile_phone": "555-010-0000", "party_size": 2}),
        ("patch", "/restaurant/waitlist/1", {"status": "SEATED"}),
        ("patch", "/restaurant/settings", {"current_wait_minutes": 5}),
    ]
    ADMIN_ENDPOINTS = [
        ("get", "/admin/restaurants", None),
        ("post", "/admin/restaurants", restaurant_body()),
        ("get", "/admin/restaurants/1", None),
        ("put", "/admin/restaurants/1", restaurant_body(username="bluebird")),
        ("patch", "/admin/restaurants/1/status", {"is_active": False}),
    ]

    @pytest.mark.parametrize(("method", "path", "body"), RESTAURANT_ENDPOINTS + ADMIN_ENDPOINTS)
    def test_every_protected_endpoint_needs_a_token(self, api, method, path, body):
        response = getattr(api, method)(path, json=body)
        assert response.status_code == 401, path

    @pytest.mark.parametrize(("method", "path", "body"), RESTAURANT_ENDPOINTS)
    def test_admin_cannot_use_restaurant_endpoints(self, api, method, path, body):
        response = getattr(api, method)(path, json=body, headers=api.login("admin"))
        assert response.status_code == 403
        assert response.json()["code"] == "FORBIDDEN"

    @pytest.mark.parametrize(("method", "path", "body"), ADMIN_ENDPOINTS)
    def test_restaurant_staff_cannot_use_admin_endpoints(self, api, method, path, body):
        response = getattr(api, method)(path, json=body, headers=api.login("bluebird"))
        assert response.status_code == 403

    def test_restaurant_endpoints_check_the_role_not_just_a_restaurant_id(self, api):
        # The API never creates such a user, but the role is what grants access.
        api.store.add_user("odd-admin", hash_password("password", n=FAST_SCRYPT_N), UserRole.ADMIN, 1)
        response = api.get("/restaurant/waitlist", headers=api.login("odd-admin"))
        assert response.status_code == 403

    def test_no_response_ever_contains_a_password_or_hash(self, api):
        admin = api.login("admin")
        bodies = [
            api.post("/auth/login", json={"username": "admin", "password": "password"}).text,
            api.get("/admin/restaurants", headers=admin).text,
            api.post("/admin/restaurants", json=restaurant_body(), headers=admin).text,
            api.get("/restaurant/waitlist", headers=api.login("bluebird")).text,
        ]
        for text in bodies:
            assert "password" not in text
            assert "scrypt$" not in text
