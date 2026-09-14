"""Restaurant operations (spec §33 items 7-10)."""

import pytest

from tests.conftest import party


@pytest.fixture
def staff(api):
    return api.login("bluebird")


def dashboard(api, headers):
    response = api.get("/restaurant/waitlist", headers=headers)
    assert response.status_code == 200
    return response.json()


class TestDashboard:
    def test_returns_restaurant_active_queue_and_history(self, api, staff):
        body = dashboard(api, staff)
        assert body["restaurant"]["name"] == "Bluebird Cafe"
        assert body["restaurant"]["username"] == "bluebird"
        assert [(e["position"], e["guest_name"], e["status"]) for e in body["active"]] == [
            (1, "Lena", "NOTIFIED"),
            (2, "Sarah", "WAITING"),
            (3, "James", "WAITING"),
            (4, "Marcos", "WAITING"),
        ]
        assert [(e["guest_name"], e["status"]) for e in body["history"]] == [("Alex", "CANCELED"), ("Chen", "SEATED")]
        assert all(e["position"] is None for e in body["history"])

    def test_only_shows_the_signed_in_restaurant(self, api):
        body = dashboard(api, api.login("oakember"))
        assert body["restaurant"]["name"] == "Oak & Ember"
        assert [e["guest_name"] for e in body["active"]] == ["Jordan", "Taylor"]

    def test_history_excludes_parties_finished_before_today(self, quiet_api):
        api = quiet_api
        headers = api.login("bluebird")
        entry = api.post("/restaurant/waitlist", json=party(), headers=headers).json()
        api.patch(f"/restaurant/waitlist/{entry['id']}", json={"status": "SEATED"}, headers=headers)
        assert len(dashboard(api, headers)["history"]) == 1

        api.clock.advance(hours=6, minutes=1)  # 18:00 UTC -> 00:01 UTC the next day
        assert dashboard(api, api.login("bluebird"))["history"] == []


class TestWalkIn:
    def test_adds_a_staff_entry_with_the_same_wait_calculation(self, api, staff):
        response = api.post("/restaurant/waitlist", json=party(guest_name="Dana", party_size=2), headers=staff)
        assert response.status_code == 201
        entry = response.json()
        assert (entry["source"], entry["status"], entry["quoted_wait_minutes"]) == ("STAFF", "WAITING", 30)
        assert entry["estimated_ready_at"] == "2026-09-14T18:30:00Z"
        assert entry["position"] == 5
        assert dashboard(api, staff)["active"][-1]["guest_name"] == "Dana"

    def test_allowed_while_online_joining_is_paused(self, api, staff):
        api.patch("/restaurant/settings", json={"online_waitlist_enabled": False}, headers=staff)
        assert api.post("/restaurant/waitlist", json=party(), headers=staff).status_code == 201

    def test_validates_like_an_online_join(self, api, staff):
        response = api.post("/restaurant/waitlist", json=party(party_size=0), headers=staff)
        assert response.status_code == 422
        assert "party_size" in response.json()["field_errors"]


class TestStatusChanges:
    def test_notify_stamps_notified_at_and_the_eater_sees_it(self, api, staff):
        sarah = dashboard(api, staff)["active"][1]
        response = api.patch(f"/restaurant/waitlist/{sarah['id']}", json={"status": "NOTIFIED"}, headers=staff)
        assert response.status_code == 200
        assert (response.json()["status"], response.json()["notified_at"]) == ("NOTIFIED", "2026-09-14T18:00:00Z")
        assert api.get("/waitlist/demo-sarah").json()["status"] == "NOTIFIED"

    def test_seat_a_party_out_of_order(self, api, staff):
        before = dashboard(api, staff)["active"]
        last = before[-1]
        response = api.patch(f"/restaurant/waitlist/{last['id']}", json={"status": "SEATED"}, headers=staff)
        assert (response.json()["status"], response.json()["position"]) == ("SEATED", None)
        assert response.json()["seated_at"] == "2026-09-14T18:00:00Z"

        after = dashboard(api, staff)
        assert [e["id"] for e in after["active"]] == [e["id"] for e in before[:-1]]
        assert after["history"][0]["id"] == last["id"]

    @pytest.mark.parametrize(("target", "stamp"), [("CANCELED", "canceled_at"), ("NO_SHOW", "no_show_at")])
    def test_cancel_and_no_show_stamp_their_time(self, api, staff, target, stamp):
        james = dashboard(api, staff)["active"][2]
        body = api.patch(f"/restaurant/waitlist/{james['id']}", json={"status": target}, headers=staff).json()
        assert (body["status"], body[stamp]) == (target, "2026-09-14T18:00:00Z")

    def test_finished_party_cannot_change(self, api, staff):
        entry = dashboard(api, staff)["active"][1]
        api.patch(f"/restaurant/waitlist/{entry['id']}", json={"status": "SEATED"}, headers=staff)
        response = api.patch(f"/restaurant/waitlist/{entry['id']}", json={"status": "NOTIFIED"}, headers=staff)
        assert response.status_code == 409
        assert response.json()["code"] == "ENTRY_CLOSED"

    def test_notified_party_cannot_be_notified_again(self, api, staff):
        lena = dashboard(api, staff)["active"][0]
        response = api.patch(f"/restaurant/waitlist/{lena['id']}", json={"status": "NOTIFIED"}, headers=staff)
        assert response.status_code == 422
        assert response.json()["message"] == "That status change isn't allowed."

    @pytest.mark.parametrize("status", ["WAITING", "EATING", None])
    def test_invalid_target_status_is_422(self, api, staff, status):
        entry = dashboard(api, staff)["active"][1]
        response = api.patch(f"/restaurant/waitlist/{entry['id']}", json={"status": status}, headers=staff)
        assert response.status_code == 422
        assert "status" in response.json()["field_errors"]

    def test_cannot_touch_another_restaurants_party(self, api, staff):
        oak_entry = dashboard(api, api.login("oakember"))["active"][0]
        response = api.patch(f"/restaurant/waitlist/{oak_entry['id']}", json={"status": "SEATED"}, headers=staff)
        assert response.status_code == 404
        assert api.get("/waitlist/demo-jordan").json()["status"] == "WAITING"


class TestAutomaticNoShow:
    def test_notified_party_becomes_no_show_after_the_timeout(self, quiet_api):
        api = quiet_api
        headers = api.login("bluebird")
        token = api.post("/restaurants/1/waitlist", json=party()).json()["public_token"]
        entry = dashboard(api, headers)["active"][0]
        api.patch(f"/restaurant/waitlist/{entry['id']}", json={"status": "NOTIFIED"}, headers=headers)

        api.clock.advance(minutes=9, seconds=59)
        assert api.get(f"/waitlist/{token}").json()["status"] == "NOTIFIED"

        api.clock.advance(seconds=1)
        body = dashboard(api, headers)
        assert body["active"] == []
        assert (body["history"][0]["status"], body["history"][0]["no_show_at"]) == ("NO_SHOW", "2026-09-14T18:10:00Z")
        assert api.get(f"/waitlist/{token}").json()["status"] == "NO_SHOW"

    def test_the_eater_poll_alone_triggers_the_sweep(self, quiet_api):
        api = quiet_api
        headers = api.login("bluebird")
        token = api.post("/restaurants/1/waitlist", json=party()).json()["public_token"]
        entry = dashboard(api, headers)["active"][0]
        api.patch(f"/restaurant/waitlist/{entry['id']}", json={"status": "NOTIFIED"}, headers=headers)
        api.clock.advance(minutes=10)
        assert api.get(f"/waitlist/{token}").json()["status"] == "NO_SHOW"

    def test_uses_the_restaurants_own_timeout(self, quiet_api):
        api = quiet_api
        admin = api.login("admin")
        current = api.get("/admin/restaurants/1", headers=admin).json()
        api.put(
            "/admin/restaurants/1",
            json={**current, "no_show_minutes": 3, "password": ""},
            headers=admin,
        )
        headers = api.login("bluebird")
        token = api.post("/restaurants/1/waitlist", json=party()).json()["public_token"]
        entry = dashboard(api, headers)["active"][0]
        api.patch(f"/restaurant/waitlist/{entry['id']}", json={"status": "NOTIFIED"}, headers=headers)
        api.clock.advance(minutes=3)
        assert api.get(f"/waitlist/{token}").json()["status"] == "NO_SHOW"

    def test_waiting_party_never_expires(self, quiet_api):
        token = quiet_api.post("/restaurants/1/waitlist", json=party()).json()["public_token"]
        quiet_api.clock.advance(hours=4)
        assert quiet_api.get(f"/waitlist/{token}").json()["status"] == "WAITING"

    def test_seeded_notified_party_expires_on_schedule(self, api):
        # Lena was notified 3 minutes before START with a 10 minute timeout.
        api.clock.advance(minutes=7)
        assert api.get("/waitlist/demo-lena").json()["status"] == "NO_SHOW"


class TestSettings:
    def test_change_wait(self, api, staff):
        response = api.patch("/restaurant/settings", json={"current_wait_minutes": 45}, headers=staff)
        assert response.status_code == 200
        assert response.json()["current_wait_minutes"] == 45
        assert api.get("/restaurants/1").json()["current_wait_minutes"] == 45

    def test_omitted_fields_are_unchanged(self, api, staff):
        api.patch("/restaurant/settings", json={"online_waitlist_enabled": False}, headers=staff)
        body = api.patch("/restaurant/settings", json={"current_wait_minutes": 5}, headers=staff).json()
        assert (body["current_wait_minutes"], body["online_waitlist_enabled"]) == (5, False)

    @pytest.mark.parametrize("minutes", [-1, 241, 12.5])
    def test_rejects_invalid_wait(self, api, staff, minutes):
        response = api.patch("/restaurant/settings", json={"current_wait_minutes": minutes}, headers=staff)
        assert response.status_code == 422
        assert response.json()["field_errors"] == {
            "current_wait_minutes": "Wait must be a whole number from 0 to 240 minutes."
        }

    def test_zero_wait_is_allowed(self, api, staff):
        assert api.patch("/restaurant/settings", json={"current_wait_minutes": 0}, headers=staff).status_code == 200
