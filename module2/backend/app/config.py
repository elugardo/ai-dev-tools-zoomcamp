"""Settings, read from environment variables so nothing is hard-coded per machine.

| Variable                      | Default                                   |
|-------------------------------|-------------------------------------------|
| WAITWISE_DATABASE_URL         | sqlite:///<backend>/waitwise.db           |
| WAITWISE_CORS_ORIGINS         | http://localhost:3417,http://127.0.0.1:3417 |
| WAITWISE_TOKEN_TTL_MINUTES    | 720                                       |
| WAITWISE_SEED_DEMO_DATA       | true (only ever seeds an empty database)  |

WAITWISE_DATABASE_URL is any SQLAlchemy URL: `sqlite:///path/to/file.db`,
`sqlite://` for a throwaway in-memory database, or another database such as
`postgresql+psycopg://user:pass@host/waitwise` once its driver is installed.
"""

import os
from dataclasses import dataclass
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_URL = f"sqlite:///{(BACKEND_DIR / 'waitwise.db').as_posix()}"
DEFAULT_CORS_ORIGINS = ("http://localhost:3417", "http://127.0.0.1:3417")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # Which database to connect to; any SQLAlchemy URL.
    database_url: str = DEFAULT_DATABASE_URL
    # Origins allowed to call the API from a browser; the Vite dev server by default.
    cors_origins: tuple[str, ...] = DEFAULT_CORS_ORIGINS
    # How long a login token stays valid.
    token_ttl_minutes: int = 12 * 60
    # Load the demo restaurants, logins and waitlist when the database is empty.
    seed_demo_data: bool = True
    # scrypt CPU/memory cost. 2**14 is the interactive-login recommendation;
    # tests lower it so hashing does not dominate the suite's run time.
    scrypt_n: int = 2**14

    @classmethod
    def from_env(cls) -> "Settings":
        origins = os.environ.get("WAITWISE_CORS_ORIGINS")
        database_url = os.environ.get("WAITWISE_DATABASE_URL", "").strip()
        return cls(
            database_url=database_url or DEFAULT_DATABASE_URL,
            cors_origins=tuple(o.strip() for o in origins.split(",") if o.strip())
            if origins
            else DEFAULT_CORS_ORIGINS,
            token_ttl_minutes=int(os.environ.get("WAITWISE_TOKEN_TTL_MINUTES", cls.token_ttl_minutes)),
            seed_demo_data=_env_bool("WAITWISE_SEED_DEMO_DATA", cls.seed_demo_data),
        )
