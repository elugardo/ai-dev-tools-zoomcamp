"""Public restaurants and the eater waitlist (spec §33 items 1-6 and 13)."""

import pytest

from tests.conftest import party


class TestRestaurants:
    def test_lists_active_restaurants_sorted_by_name(self, api):
        response = api.get("/restaurants")
        assert response.status_code == 200
        assert [(r["name"], r["current_wait_minutes"], r["waiting_parties"]) for r in response.json()] == [
            ("Bluebird Cafe", 30, 4),
            ("Oak & Ember", 20, 2),
        ]

    def test_inactive_restaurants_do_not_appear(self, api):
        api.patch("/admin/restaurants/1/status", json={"is_active": False}, headers=api.login("admin"))
        assert [r["name"] for r in api.get("/restaurants").json()] == ["Oak & Ember"]

    def test_paused_restaurants_still_appear(self, api):
        api.patch("/restaurant/settings", json={"online_waitlist_enabled": False}, headers=api.login("bluebird"))
        bluebird = api.get("/restaurants").json()[0]
        assert bluebird["name"] == "Bluebird Cafe"
        assert bluebird["online_waitlist_enabled"] is False

    def test_empty_list_when_there_are_no_restaurants(self, empty_api):
        assert empty_api.get("/restaurants").json() == []

    def test_one_restaurant_including_an_inactive_one(self, api):
        api.patch("/admin/restaurants/1/status", json={"is_active": False}, headers=api.login("admin"))
        response = api.get("/restaurants/1")
        assert response.status_code == 200
        assert response.json()["is_active"] is False

    def test_missing_restaurant_is_404(self, api):
        response = api.get("/restaurants/999")
        assert response.status_code == 404
        assert response.json() == {
            "code": "NOT_FOUND",
            "message": "We couldn't find that restaurant.",
            "field_errors": {},
        }


class TestJoinWaitlist:
    def test_creates_a_waiting_entry_with_a_token_and_position(self, api):
        response = api.post("/restaurants/1/waitlist", json=party(guest_name="  Marcos  "))
        assert response.status_code == 201
        body = response.json()
        assert body["guest_name"] == "Marcos"
        assert body["status"] == "WAITING"
        assert body["position"] == 5
        assert body["restaurant_name"] == "Bluebird Cafe"
        assert api.get(f"/waitlist/{body['public_token']}").json() == body

    def test_quotes_the_current_wait_and_computes_estimated_ready(self, api):
        body = api.post("/restaurants/1/waitlist", json=party()).json()
        assert body["quoted_wait_minutes"] == 30
        assert body["joined_at"] == "2026-09-14T18:00:00Z"
        assert body["estimated_ready_at"] == "2026-09-14T18:30:00Z"

    def test_quote_is_frozen_when_the_restaurant_changes_its_wait(self, api):
        token = api.post("/restaurants/1/waitlist", json=party()).json()["public_token"]
        api.patch("/restaurant/settings", json={"current_wait_minutes": 90}, headers=api.login("bluebird"))
        after = api.get(f"/waitlist/{token}").json()
        assert after["quoted_wait_minutes"] == 30
        assert after["estimated_ready_at"] == "2026-09-14T18:30:00Z"

    @pytest.mark.parametrize("size", [0, 21, 2.5, -1, "four", True])
    def test_rejects_invalid_party_size(self, api, size):
        response = api.post("/restaurants/1/waitlist", json=party(party_size=size))
        assert response.status_code == 422
        assert response.json()["code"] == "VALIDATION"
        assert "party_size" in response.json()["field_errors"]

    @pytest.mark.parametrize("size", [1, 20])
    def test_accepts_party_size_boundaries(self, api, size):
        assert api.post("/restaurants/1/waitlist", json=party(party_size=size)).status_code == 201

    def test_missing_fields_get_friendly_messages(self, api):
        response = api.post("/restaurants/1/waitlist", json={})
        assert response.json()["field_errors"] == {
            "guest_name": "Name is required.",
            "mobile_phone": "Mobile phone is required.",
            "party_size": "Party size is required.",
        }

    def test_blank_name_bad_phone_and_long_notes(self, api):
        response = api.post(
            "/restaurants/1/waitlist",
            json=party(guest_name="   ", mobile_phone="call me", notes="x" * 251),
        )
        assert response.json()["field_errors"] == {
            "guest_name": "Name is required.",
            "mobile_phone": "Enter a valid mobile number.",
            "notes": "Notes must be 250 characters or fewer.",
        }

    def test_notes_are_optional_and_blank_notes_are_stored_as_null(self, api):
        body = {"guest_name": "A", "mobile_phone": "555-010-0000", "party_size": 2}
        entry = api.post("/restaurants/1/waitlist", json=body).json()
        staff = api.get("/restaurant/waitlist", headers=api.login("bluebird")).json()
        assert next(e for e in staff["active"] if e["public_token"] == entry["public_token"])["notes"] is None

    def test_paused_restaurant_refuses_online_joins(self, api):
        api.patch("/restaurant/settings", json={"online_waitlist_enabled": False}, headers=api.login("bluebird"))
        response = api.post("/restaurants/1/waitlist", json=party())
        assert response.status_code == 409
        assert response.json()["code"] == "WAITLIST_CLOSED"
        assert "Bluebird Cafe" in response.json()["message"]

    def test_inactive_restaurant_refuses_online_joins(self, api):
        api.patch("/admin/restaurants/1/status", json={"is_active": False}, headers=api.login("admin"))
        assert api.post("/restaurants/1/waitlist", json=party()).json()["code"] == "WAITLIST_CLOSED"

    def test_joining_a_missing_restaurant_is_404(self, api):
        assert api.post("/restaurants/999/waitlist", json=party()).status_code == 404

    def test_tokens_are_unguessable_and_unique(self, quiet_api):
        tokens = {quiet_api.post("/restaurants/1/waitlist", json=party()).json()["public_token"] for _ in range(20)}
        assert len(tokens) == 20
        assert all(len(t) >= 16 for t in tokens)


class TestQueuePosition:
    def test_fifo_and_closes_up_when_a_party_ahead_leaves(self, quiet_api):
        api = quiet_api
        first = api.post("/restaurants/1/waitlist", json=party(guest_name="First")).json()
        api.clock.advance(minutes=1)
        second = api.post("/restaurants/1/waitlist", json=party(guest_name="Second")).json()
        api.clock.advance(minutes=1)
        third = api.post("/restaurants/1/waitlist", json=party(guest_name="Third")).json()

        assert api.get(f"/waitlist/{third['public_token']}").json()["position"] == 3
        api.delete(f"/waitlist/{second['public_token']}")
        assert api.get(f"/waitlist/{third['public_token']}").json()["position"] == 2
        assert api.get(f"/waitlist/{first['public_token']}").json()["position"] == 1

    def test_positions_are_per_restaurant(self, quiet_api):
        quiet_api.post("/restaurants/1/waitlist", json=party())
        other = quiet_api.post("/restaurants/2/waitlist", json=party()).json()
        assert other["position"] == 1


class TestEaterStatusAndLeaving:
    def test_seeded_entry_is_readable_by_its_demo_token(self, api):
        body = api.get("/waitlist/demo-sarah").json()
        assert (body["guest_name"], body["party_size"], body["status"], body["position"]) == ("Sarah", 6, "WAITING", 2)

    def test_leaving_cancels_and_removes_from_the_queue(self, api):
        token = api.post("/restaurants/1/waitlist", json=party()).json()["public_token"]
        response = api.delete(f"/waitlist/{token}")
        assert response.status_code == 200
        assert (response.json()["status"], response.json()["position"]) == ("CANCELED", None)

        dashboard = api.get("/restaurant/waitlist", headers=api.login("bluebird")).json()
        assert token not in [e["public_token"] for e in dashboard["active"]]
        canceled = next(e for e in dashboard["history"] if e["public_token"] == token)
        assert canceled["canceled_at"] == "2026-09-14T18:00:00Z"

    def test_a_finished_entry_stays_readable(self, api):
        token = api.post("/restaurants/1/waitlist", json=party()).json()["public_token"]
        api.delete(f"/waitlist/{token}")
        assert api.get(f"/waitlist/{token}").json()["status"] == "CANCELED"

    def test_cannot_leave_twice(self, api):
        token = api.post("/restaurants/1/waitlist", json=party()).json()["public_token"]
        api.delete(f"/waitlist/{token}")
        response = api.delete(f"/waitlist/{token}")
        assert response.status_code == 409
        assert response.json()["code"] == "ENTRY_CLOSED"

    def test_can_leave_after_being_notified(self, api):
        response = api.delete("/waitlist/demo-lena")
        assert response.json()["status"] == "CANCELED"

    @pytest.mark.parametrize("method", ["get", "delete"])
    def test_unknown_token_is_404(self, api, method):
        response = getattr(api, method)("/waitlist/not-a-token")
        assert response.status_code == 404
        assert response.json()["message"].startswith("We couldn't find that waitlist spot")
