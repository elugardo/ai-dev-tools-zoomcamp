"""Database maintenance commands, run from backend/:

    uv run python -m app.manage init-db    # create missing tables (the server also does this)
    uv run python -m app.manage seed       # load the demo data into an empty database
    uv run python -m app.manage reset-db   # drop everything, recreate, reseed

All of them use WAITWISE_DATABASE_URL, like the server.
"""

import argparse
from functools import partial

from .auth import hash_password
from .config import Settings
from .db import create_db_engine, create_schema, drop_schema, make_session_factory
from .seed import seed_if_empty
from .store import Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.manage", description="WaitWise database commands.")
    parser.add_argument("command", choices=["init-db", "seed", "reset-db"])
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    engine = create_db_engine(settings.database_url)
    try:
        if args.command == "reset-db":
            drop_schema(engine)
        create_schema(engine)
        if args.command in ("seed", "reset-db"):
            with make_session_factory(engine).begin() as session:
                seeded = seed_if_empty(Store(session), partial(hash_password, n=settings.scrypt_n))
            print("Seeded demo data." if seeded else "Database already has data; not seeding.")
        print(f"{args.command}: done ({engine.url.render_as_string(hide_password=True)})")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
