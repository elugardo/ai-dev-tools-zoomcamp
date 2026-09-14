"""App factory. Run from backend/: `uv run uvicorn app.main:app --reload --port 9127`."""

from functools import partial

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .auth import hash_password
from .config import Settings
from .errors import install_error_handlers
from .routers import admin, auth, public, restaurant
from .seed import seed_demo_data
from .store import Store

API_PREFIX = "/api"


def create_app(settings: Settings | None = None, store: Store | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    store = store if store is not None else Store()
    if settings.seed_demo_data:
        seed_demo_data(store, partial(hash_password, n=settings.scrypt_n))

    app = FastAPI(
        title="WaitWise API",
        version="0.1.0",
        summary="Restaurant waitlist backend. The contract is module2/openapi.yaml.",
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url="/docs",
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.store = store

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


app = create_app()
