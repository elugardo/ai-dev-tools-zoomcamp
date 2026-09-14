"""The SQLAlchemy persistence layer: configuration, durability, transactions,
concurrency, and the guarantees that keep the app database-agnostic."""

import ast
import hashlib
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Boolean, Enum, Integer, String, create_mock_engine, func, inspect, select
from sqlalchemy.dialects import mssql, mysql, postgresql
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.schema import CreateIndex, CreateTable

from app import manage
from app.config import DEFAULT_DATABASE_URL, Settings
from app.db import Base, UTCDateTime, create_db_engine, create_schema
from app.errors import EntryClosedError
from app.main import create_app
from app.models import WaitlistStatus
from app.store import Store
from app.tables import AuthToken, Restaurant, User, WaitlistEntry
from tests.conftest import FAST_SCRYPT_N, START, Clock, api_for, party, restaurant_body

APP_DIR = Path(__file__).resolve().parents[1] / "app"


def file_url(tmp_path: Path) -> str:
    return f"sqlite:///{(tmp_path / 'waitwise-test.db').as_posix()}"


def fast_settings(database_url: str, **overrides) -> Settings:
    return Settings(database_url=database_url, scrypt_n=FAST_SCRYPT_N, **overrides)


# ---- configuration -------------------------------------------------------------


class TestConfiguration:
    def test_database_url_comes_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("WAITWISE_DATABASE_URL", "postgresql+psycopg://waitwise@db.example/waitwise")
        assert Settings.from_env().database_url == "postgresql+psycopg://waitwise@db.example/waitwise"

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_defaults_to_a_sqlite_file_in_the_backend_folder(self, monkeypatch, value):
        if value is None:
            monkeypatch.delenv("WAITWISE_DATABASE_URL", raising=False)
        else:
            monkeypatch.setenv("WAITWISE_DATABASE_URL", value)
        url = Settings.from_env().database_url
        assert url == DEFAULT_DATABASE_URL
        assert url.startswith("sqlite:///") and url.endswith("/backend/waitwise.db")

    def test_the_app_connects_to_the_configured_database(self, tmp_path):
        app = create_app(fast_settings(file_url(tmp_path)))
        try:
            assert (tmp_path / "waitwise-test.db").exists()
            assert {"users", "restaurants", "waitlist_entries", "auth_tokens"} <= set(
                inspect(app.state.engine).get_table_names()
            )
        finally:
            app.state.engine.dispose()

    def test_importing_the_app_module_does_not_open_the_database(self, tmp_path, monkeypatch):
        import importlib

        import app.main as main

        db_file = tmp_path / "lazy.db"
        monkeypatch.setenv("WAITWISE_DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
        monkeypatch.setenv("WAITWISE_SEED_DEMO_DATA", "false")
        module = importlib.reload(main)
        try:
            assert not db_file.exists()  # importing (as the tests do) has no side effects
            application = module.app  # what `uvicorn app.main:app` does
            assert db_file.exists()
            assert module.app is application  # built once
            application.state.engine.dispose()
        finally:
            vars(module).pop("app", None)


# ---- durability and seeding ------------------------------------------------------


class TestPersistence:
    def test_data_and_sessions_survive_a_restart(self, tmp_path):
        clock = Clock()
        url = file_url(tmp_path)
        with api_for(clock, database_url=url) as first:
            token = first.post("/restaurants/1/waitlist", json=party(guest_name="Durable")).json()["public_token"]
            headers = first.login("bluebird")

        # A brand-new app and engine on the same database file, like a server restart.
        second = TestClient(create_app(fast_settings(url), clock=clock, local_tz=UTC))
        try:
            assert second.get(f"/api/waitlist/{token}").json()["guest_name"] == "Durable"
            assert second.get("/api/restaurant/waitlist", headers=headers).status_code == 200
            assert len(second.get("/api/restaurants").json()) == 2  # not seeded twice
        finally:
            second.app.state.engine.dispose()

    def test_seeding_only_happens_on_an_empty_database(self, tmp_path):
        clock = Clock()
        url = file_url(tmp_path)
        with api_for(clock, database_url=url) as api:
            api.post("/admin/restaurants", json=restaurant_body(), headers=api.login("admin"))
        app = create_app(fast_settings(url), clock=clock)
        try:
            with app.state.session_factory() as session:
                assert session.scalar(select(func.count()).select_from(Restaurant)) == 3
                assert session.scalar(select(func.count()).select_from(User)) == 4
        finally:
            app.state.engine.dispose()

    def test_seeding_can_be_turned_off(self, empty_api):
        with empty_api.db() as store:
            assert not store.has_users()

    def test_reset_db_command_rebuilds_the_demo_data(self, tmp_path, monkeypatch, capsys):
        clock = Clock()
        url = file_url(tmp_path)
        with api_for(clock, database_url=url) as api:
            api.post("/restaurants/1/waitlist", json=party(guest_name="Before reset"))
        monkeypatch.setenv("WAITWISE_DATABASE_URL", url)

        assert manage.main(["reset-db"]) == 0
        assert "Seeded demo data." in capsys.readouterr().out

        engine = create_db_engine(url)
        try:
            with engine.connect() as connection:
                names = connection.scalars(select(WaitlistEntry.guest_name)).all()
            assert "Before reset" not in names
            assert "Sarah" in names
        finally:
            engine.dispose()

    def test_seed_command_does_not_touch_existing_data(self, tmp_path, monkeypatch, capsys):
        url = file_url(tmp_path)
        with api_for(Clock(), database_url=url):
            pass
        monkeypatch.setenv("WAITWISE_DATABASE_URL", url)
        manage.main(["seed"])
        assert "already has data" in capsys.readouterr().out


# ---- what the database enforces ----------------------------------------------------


class TestStorage:
    def test_datetimes_come_back_as_aware_utc_whatever_offset_went_in(self, api):
        plus_two = datetime(2026, 9, 14, 20, 30, tzinfo=timezone(timedelta(hours=2)))
        with api.db() as store:
            store.restaurant(1).updated_at = plus_two
        with api.db() as store:
            stored = store.restaurant(1).updated_at
        assert stored.tzinfo is UTC
        assert stored == plus_two  # same instant
        assert stored.hour == 18

    def test_naive_datetimes_are_refused(self, api):
        with pytest.raises(StatementError, match="timezone-aware"):
            with api.db() as store:
                store.restaurant(1).updated_at = datetime(2026, 9, 14, 18, 0)

    def test_only_a_hash_of_each_login_token_is_stored(self, api):
        token = api.post("/auth/login", json={"username": "bluebird", "password": "password"}).json()["token"]
        with api.db() as store:
            hashes = store.session.scalars(select(AuthToken.token_hash)).all()
        assert token not in hashes
        assert hashlib.sha256(token.encode()).hexdigest() in hashes

    def test_expired_tokens_are_purged_at_the_next_login(self, api):
        stale = api.login("bluebird")
        api.clock.advance(hours=13)
        assert api.get("/restaurant/waitlist", headers=stale).status_code == 401

        fresh = api.login("oakember")
        with api.db() as store:
            remaining = store.session.scalars(select(AuthToken.token_hash)).all()
        assert remaining == [hashlib.sha256(fresh["Authorization"].removeprefix("Bearer ").encode()).hexdigest()]

    def test_foreign_keys_are_enforced(self, api):
        with pytest.raises(IntegrityError):
            with api.db() as store:
                store.session.add(_entry(restaurant_id=999))

    def test_check_constraints_are_enforced(self, api):
        with pytest.raises(IntegrityError):
            with api.db() as store:
                store.session.add(_entry(restaurant_id=1, party_size=0))

    def test_a_restaurant_has_at_most_one_login(self, api):
        with pytest.raises(IntegrityError):
            with api.db() as store:
                store.session.add(User(username="second", password_hash="x", role="RESTAURANT", restaurant_id=1, created_at=START))

    @pytest.mark.parametrize(
        ("path", "body", "field"),
        [
            ("/restaurants/1/waitlist", party(guest_name="x" * 101), "guest_name"),
            # 12 digits (a valid count) spread over 34 characters.
            ("/restaurants/1/waitlist", party(mobile_phone="  ".join("5" * 12)), "mobile_phone"),
            ("/admin/restaurants", restaurant_body(name="x" * 121), "name"),
            ("/admin/restaurants", restaurant_body(address="x" * 201), "address"),
            ("/admin/restaurants", restaurant_body(username="x" * 65), "username"),
            ("/admin/restaurants", restaurant_body(password="x" * 129), "password"),
        ],
    )
    def test_values_longer_than_their_column_are_a_friendly_422(self, api, path, body, field):
        response = api.post(path, json=body, headers=api.login("admin"))
        assert response.status_code == 422
        assert field in response.json()["field_errors"]


# ---- transactions and concurrency ------------------------------------------------------


class TestTransactions:
    def test_a_failed_request_rolls_back_everything_it_changed(self, api):
        # Remove Bluebird's login so an edit without a password must fail part-way,
        # after the restaurant's fields were already assigned.
        with api.db() as store:
            store.session.delete(store.restaurant_login(1))
        response = api.put(
            "/admin/restaurants/1",
            json=restaurant_body(name="Should Not Stick", username="bluebird", password=""),
            headers=api.login("admin"),
        )
        assert response.status_code == 422
        assert api.get("/restaurants/1").json()["name"] == "Bluebird Cafe"

    def test_the_unique_constraint_backs_up_the_username_check(self, api, monkeypatch):
        admin = api.login("admin")
        # Simulate two admins racing: the pre-check sees no user, the database still refuses.
        monkeypatch.setattr(Store, "user_by_username", lambda self, username: None)
        response = api.post("/admin/restaurants", json=restaurant_body(username="oakember"), headers=admin)
        assert response.status_code == 409
        assert response.json()["code"] == "CONFLICT"
        monkeypatch.undo()
        assert len(api.get("/restaurants").json()) == 2  # the half-created restaurant was rolled back

    def test_a_stale_request_cannot_overwrite_a_newer_status(self, tmp_path):
        clock = Clock()
        with api_for(clock, database_url=file_url(tmp_path)) as api:
            staff = api.login("bluebird")
            factory = api.app.state.session_factory
            with factory() as stale_session:
                stale = Store(stale_session, clock=clock)
                james = stale.entry_by_token("demo-james")  # loaded while WAITING
                assert james.status is WaitlistStatus.WAITING

                # Meanwhile another request seats James.
                entry_id = james.id
                assert api.patch(f"/restaurant/waitlist/{entry_id}", json={"status": "SEATED"}, headers=staff).status_code == 200

                with pytest.raises(EntryClosedError):
                    stale.change_status(james, WaitlistStatus.NOTIFIED)
                stale_session.rollback()
            assert api.get("/waitlist/demo-james").json()["status"] == "SEATED"

    def test_a_stale_request_is_re_decided_against_the_current_status(self, tmp_path):
        clock = Clock()
        with api_for(clock, database_url=file_url(tmp_path)) as api:
            staff = api.login("bluebird")
            with api.app.state.session_factory() as stale_session:
                stale = Store(stale_session, clock=clock)
                sarah = stale.entry_by_token("demo-sarah")  # loaded while WAITING
                api.patch(f"/restaurant/waitlist/{sarah.id}", json={"status": "NOTIFIED"}, headers=staff)

                # Seating is still allowed from NOTIFIED, so the stale request succeeds cleanly.
                seated = stale.change_status(sarah, WaitlistStatus.SEATED)
                stale_session.commit()
                assert seated.status is WaitlistStatus.SEATED
            body = api.get("/waitlist/demo-sarah").json()
            assert body["status"] == "SEATED"
            assert body["notified_at"] is not None


# ---- database-agnostic guarantees ---------------------------------------------------------


GENERIC_TYPES = (Integer, String, Boolean, UTCDateTime, Enum)


def test_every_column_uses_a_portable_type():
    create_schema(create_db_engine("sqlite://"))  # registers tables on the metadata
    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            assert isinstance(column.type, GENERIC_TYPES), f"{table.name}.{column.name}: {column.type!r}"
            if isinstance(column.type, Enum):
                assert column.type.native_enum is False, f"{table.name}.{column.name} uses a native enum"
            if isinstance(column.type, String) and not isinstance(column.type, Enum):
                assert column.type.length, f"{table.name}.{column.name} needs a length for non-SQLite databases"


@pytest.mark.parametrize("dialect", [postgresql.dialect(), mysql.dialect(), mssql.dialect()], ids=lambda d: d.name)
def test_the_schema_compiles_for_other_databases(dialect):
    """Renders every CREATE TABLE and CREATE INDEX for another database, with no server or driver."""
    create_schema(create_db_engine("sqlite://"))
    statements: list[str] = []
    engine = create_mock_engine(f"{dialect.name}://", lambda sql, *_, **__: statements.append(str(sql.compile(dialect=dialect))))
    Base.metadata.create_all(engine, checkfirst=False)
    rendered = "\n".join(statements)
    for table in Base.metadata.sorted_tables:
        assert str(CreateTable(table).compile(dialect=dialect)).strip() in rendered
        for index in table.indexes:
            assert str(CreateIndex(index).compile(dialect=dialect)).strip() in rendered
    assert "PRAGMA" not in rendered


def _string_constants(tree: ast.AST) -> list[str]:
    """String literals that are not docstrings."""
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
    ]


def test_database_specific_code_is_confined_to_db_and_config():
    allowed = {"db.py", "config.py"}
    offenders = []
    for path in APP_DIR.rglob("*.py"):
        if path.name in allowed:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("sqlalchemy.dialects"):
                offenders.append(f"{path.name}: imports {node.module}")
            if isinstance(node, ast.ImportFrom) and node.module == "sqlalchemy" and any(a.name == "text" for a in node.names):
                offenders.append(f"{path.name}: imports sqlalchemy.text (raw SQL)")
        for value in _string_constants(tree):
            lowered = value.lower()
            if "sqlite" in lowered or "pragma" in lowered or "postgres" in lowered:
                offenders.append(f"{path.name}: database-specific string {value!r}")
    assert offenders == []


def _entry(*, restaurant_id: int, party_size: int = 2) -> WaitlistEntry:
    return WaitlistEntry(
        restaurant_id=restaurant_id,
        public_token=f"direct-{restaurant_id}-{party_size}",
        guest_name="Direct",
        mobile_phone="555-010-0000",
        party_size=party_size,
        source="ONLINE",
        status="WAITING",
        quoted_wait_minutes=10,
        joined_at=START,
        estimated_ready_at=START,
        created_at=START,
        updated_at=START,
    )
