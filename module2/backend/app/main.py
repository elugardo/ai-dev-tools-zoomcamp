"""App factory. Run from backend/: `uv run uvicorn app.main:app --reload --port 9127`.

The database comes from WAITWISE_DATABASE_URL (see config.py). On startup the
app creates any missing tables and seeds the demo data into an empty database.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime, tzinfo
from functools import partial

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import Engine

from .auth import hash_password
from .config import Settings
from .db import create_db_engine, create_schema, make_session_factory
from .errors import install_error_handlers
from .routers import admin, auth, public, restaurant
from .seed import seed_if_empty
from .store import Store, utc_now

API_PREFIX = "/api"


def create_app(
    settings: Settings | None = None,
    *,
    engine: Engine | None = None,
    clock: Callable[[], datetime] = utc_now,
    local_tz: tzinfo | None = None,
) -> FastAPI:
    """Builds the app. Tests pass their own engine, clock and timezone."""
    settings = settings or Settings.from_env()
    engine = engine if engine is not None else create_db_engine(settings.database_url)
    create_schema(engine)
    session_factory = make_session_factory(engine)
    if settings.seed_demo_data:
        with session_factory.begin() as session:
            seed_if_empty(Store(session, clock, local_tz), partial(hash_password, n=settings.scrypt_n))

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        engine.dispose()

    app = FastAPI(
        title="WaitWise API",
        version="0.1.0",
        summary="Restaurant waitlist backend. The contract is module2/openapi.yaml.",
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url="/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.clock = clock
    app.state.local_tz = local_tz

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )
    install_error_handlers(app)

    for router in (auth.router, public.router, restaurant.router, admin.router):
        app.include_router(router, prefix=API_PREFIX)
    return app


def __getattr__(name: str) -> FastAPI:
    """`app` is built on first access (`uvicorn app.main:app`), not at import time,
    so importing create_app in tests never opens or seeds the default database."""
    if name == "app":
        globals()["app"] = application = create_app()
        return application
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
