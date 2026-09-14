"""Shared fixtures: a hand-cranked clock, a fresh store per test, and API clients.

Every test gets its own app and store, so no state leaks between tests. Hashing
uses a low scrypt cost to keep the suite fast; production uses Settings' default.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.store import Store

START = datetime(2026, 9, 14, 18, 0, tzinfo=UTC)
FAST_SCRYPT_N = 2**10
DEMO_PASSWORD = "password"


class Clock:
    def __init__(self, start: datetime = START):
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


class Api:
    """A TestClient plus login helpers, so tests read as API calls."""

    def __init__(self, client: TestClient, store: Store, clock: Clock):
        self.client = client
        self.store = store
        self.clock = clock

    def login(self, username: str, password: str = DEMO_PASSWORD) -> dict[str, str]:
        response = self.client.post("/api/auth/login", json={"username": username, "password": password})
        assert response.status_code == 200, response.json()
        return {"Authorization": f"Bearer {response.json()['token']}"}

    def request(self, method: str, path: str, *, json=None, **kwargs):
        # GET and DELETE take no body; only send one when there is something to send.
        if json is not None:
            kwargs["json"] = json
        return self.client.request(method.upper(), f"/api{path}", **kwargs)

    def get(self, path: str, **kwargs):
        return self.request("get", path, **kwargs)

    def post(self, path: str, **kwargs):
        return self.request("post", path, **kwargs)

    def put(self, path: str, **kwargs):
        return self.request("put", path, **kwargs)

    def patch(self, path: str, **kwargs):
        return self.request("patch", path, **kwargs)

    def delete(self, path: str, **kwargs):
        return self.request("delete", path, **kwargs)


def make_api(clock: Clock, *, seed: bool = True, without_entries: bool = False) -> Api:
    store = Store(clock=clock, local_tz=UTC)
    app = create_app(Settings(seed_demo_data=seed, scrypt_n=FAST_SCRYPT_N), store)
    if without_entries:
        store.entries.clear()
    return Api(TestClient(app), store, clock)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def api(clock: Clock) -> Api:
    """The demo seed: admin, Bluebird Cafe (id 1) and Oak & Ember (id 2) with parties."""
    return make_api(clock)


@pytest.fixture
def quiet_api(clock: Clock) -> Api:
    """The demo restaurants and logins with no waitlist entries."""
    return make_api(clock, without_entries=True)


@pytest.fixture
def empty_api(clock: Clock) -> Api:
    """No users, restaurants or entries at all."""
    return make_api(clock, seed=False)


def party(**overrides) -> dict:
    return {"guest_name": "Marcos", "mobile_phone": "555-010-6677", "party_size": 4, "notes": "", **overrides}


def restaurant_body(**overrides) -> dict:
    return {
        "name": "Harbor Noodle",
        "address": "789 Pier Road",
        "phone": "(555) 400-1234",
        "description": "Hand-pulled noodles.",
        "current_wait_minutes": 15,
        "no_show_minutes": 10,
        "is_active": True,
        "username": "harbor",
        "password": "noodles-123",
        **overrides,
    }
