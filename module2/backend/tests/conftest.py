"""Shared fixtures: a hand-cranked clock, a fresh database per test, and API clients.

Tests run against a real database through SQLAlchemy. By default that is an
in-memory SQLite database, created and thrown away per test. Point
WAITWISE_TEST_DATABASE_URL at another database (for example a disposable
Postgres) to run the same suite there; its tables are dropped and recreated for
every test, so never aim it at data you want to keep.

Hashing uses a low scrypt cost to keep the suite fast; production uses Settings' default.
"""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.config import Settings
from app.db import create_db_engine, drop_schema, is_sqlite_memory
from app.main import create_app
from app.store import Store
from app.tables import WaitlistEntry

START = datetime(2026, 9, 14, 18, 0, tzinfo=UTC)
FAST_SCRYPT_N = 2**10
DEMO_PASSWORD = "password"
TEST_DATABASE_URL = os.environ.get("WAITWISE_TEST_DATABASE_URL", "sqlite://")


class Clock:
    def __init__(self, start: datetime = START):
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


class Api:
    """A TestClient plus login and database helpers, so tests read as API calls."""

    def __init__(self, client: TestClient, clock: Clock):
        self.client = client
        self.clock = clock
        self.app = client.app

    @contextmanager
    def db(self) -> Iterator[Store]:
        """A Store in its own committed transaction, for arranging or inspecting data directly."""
        with self.app.state.session_factory.begin() as session:
            yield Store(session, clock=self.clock, local_tz=UTC)

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


def build_api(clock: Clock, *, database_url: str = TEST_DATABASE_URL, seed: bool = True, without_entries: bool = False) -> Api:
    engine = create_db_engine(database_url)
    if not is_sqlite_memory(database_url):
        drop_schema(engine)  # a shared database starts every test clean
    settings = Settings(database_url=database_url, seed_demo_data=seed, scrypt_n=FAST_SCRYPT_N)
    app = create_app(settings, engine=engine, clock=clock, local_tz=UTC)
    if without_entries:
        with app.state.session_factory.begin() as session:
            session.execute(delete(WaitlistEntry))
    return Api(TestClient(app), clock)


@contextmanager
def api_for(clock: Clock, **options) -> Iterator[Api]:
    api = build_api(clock, **options)
    try:
        yield api
    finally:
        api.app.state.engine.dispose()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def api(clock: Clock) -> Iterator[Api]:
    """The demo seed: admin, Bluebird Cafe (id 1) and Oak & Ember (id 2) with parties."""
    with api_for(clock) as api:
        yield api


@pytest.fixture
def quiet_api(clock: Clock) -> Iterator[Api]:
    """The demo restaurants and logins with no waitlist entries."""
    with api_for(clock, without_entries=True) as api:
        yield api


@pytest.fixture
def empty_api(clock: Clock) -> Iterator[Api]:
    """No users, restaurants or entries at all."""
    with api_for(clock, seed=False) as api:
        yield api


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
