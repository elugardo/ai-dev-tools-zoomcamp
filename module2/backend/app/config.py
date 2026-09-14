"""Settings, read from environment variables so nothing is hard-coded per machine."""

import os
from dataclasses import dataclass

DEFAULT_CORS_ORIGINS = ("http://localhost:3417", "http://127.0.0.1:3417")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # Origins allowed to call the API from a browser; the Vite dev server by default.
    cors_origins: tuple[str, ...] = DEFAULT_CORS_ORIGINS
    # How long a login token stays valid.
    token_ttl_minutes: int = 12 * 60
    # Load the demo restaurants, logins and waitlist on startup.
    seed_demo_data: bool = True
    # scrypt CPU/memory cost. 2**14 is the interactive-login recommendation;
    # tests lower it so hashing does not dominate the suite's run time.
    scrypt_n: int = 2**14

    @classmethod
    def from_env(cls) -> "Settings":
        origins = os.environ.get("WAITWISE_CORS_ORIGINS")
        return cls(
            cors_origins=tuple(o.strip() for o in origins.split(",") if o.strip())
            if origins
            else DEFAULT_CORS_ORIGINS,
            token_ttl_minutes=int(os.environ.get("WAITWISE_TOKEN_TTL_MINUTES", cls.token_ttl_minutes)),
            seed_demo_data=_env_bool("WAITWISE_SEED_DEMO_DATA", cls.seed_demo_data),
        )
