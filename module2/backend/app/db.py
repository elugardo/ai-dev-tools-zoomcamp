"""Database engine, sessions, and the one portable column type WaitWise needs.

Everything database-specific lives in this file. The rest of the app uses
generic SQLAlchemy types and ORM queries only, so pointing
WAITWISE_DATABASE_URL at another database (e.g. `postgresql+psycopg://...`,
after installing its driver) needs no code changes elsewhere.
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from fastapi import Request
from sqlalchemy import DateTime, Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator


class Base(DeclarativeBase):
    pass


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware UTC datetimes on every database.

    SQLite has no timezone type and silently drops tzinfo; Postgres has one. To
    behave identically everywhere, values are stored as naive UTC and always come
    back as aware UTC. Naive datetimes are rejected on the way in, so a local
    time can never be stored by mistake.
    """

    impl = DateTime(timezone=False)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("UTCDateTime requires a timezone-aware datetime")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def is_sqlite_memory(url: str) -> bool:
    parsed = make_url(url)
    return parsed.get_backend_name() == "sqlite" and parsed.database in (None, "", ":memory:")


def create_db_engine(url: str) -> Engine:
    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite":
        return create_engine(url, pool_pre_ping=True)

    # ---- SQLite only ----------------------------------------------------------
    in_memory = is_sqlite_memory(url)
    options: dict = {"connect_args": {"check_same_thread": False}}
    if in_memory:
        # One shared connection, or every new connection would see an empty database.
        options["poolclass"] = StaticPool
    elif parsed.database:
        Path(parsed.database).expanduser().parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, **options)

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")  # off by default in SQLite
        if not in_memory:
            # WAL lets the polling readers keep reading while a request writes.
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    return engine


def create_schema(engine: Engine) -> None:
    """Creates any missing tables. Idempotent; existing tables and data are untouched."""
    from . import tables  # noqa: F401  (registers the ORM classes on Base.metadata)

    Base.metadata.create_all(engine)


def drop_schema(engine: Engine) -> None:
    from . import tables  # noqa: F401

    Base.metadata.drop_all(engine)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    # expire_on_commit=False: response models are built from objects after commit.
    return sessionmaker(engine, expire_on_commit=False)


def get_session(request: Request) -> Iterator[Session]:
    """One session and one transaction per request.

    Declared with `scope="function"` (see auth.py), so the commit happens after
    the endpoint returns but before the response is sent: the client never sees
    success for a write that then fails to commit. Any exception, including an
    ApiError, rolls the whole request back.
    """
    session: Session = request.app.state.session_factory()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()
