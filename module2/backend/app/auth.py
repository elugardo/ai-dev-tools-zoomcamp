"""Password hashing, login tokens, and the role dependencies routers declare.

- Passwords are hashed with scrypt from the standard library: a random salt per
  password, parameters stored with the hash, constant-time comparison.
- A login issues an opaque random bearer token that the store maps to its user.
  Tokens expire after `Settings.token_ttl_minutes`, and a restaurant's tokens
  are revoked when its password changes. Tokens live in memory, so restarting
  the server signs everyone out.
"""

import base64
import hashlib
import hmac
import secrets
from datetime import timedelta
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .errors import ForbiddenError, UnauthorizedError
from .models import UserRole
from .store import RestaurantRecord, Store, UserRecord

SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_KEY_BYTES = 32
LOGIN_FAILED = "Incorrect username or password."
SESSION_EXPIRED = "Your session has expired. Please log in again."


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str, *, n: int = 2**14) -> str:
    """Returns `scrypt$n$r$p$salt$hash`; the parameters travel with the hash."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_KEY_BYTES
    )
    return f"scrypt${n}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, expected = stored.split("$")
        if scheme != "scrypt":
            return False
        expected_bytes = _unb64(expected)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_unb64(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected_bytes),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected_bytes)


@lru_cache
def _dummy_hash(n: int) -> str:
    return hash_password("timing-equalizer", n=n)


def authenticate(store: Store, username: str, password: str, *, scrypt_n: int) -> UserRecord:
    user = store.user_by_username(username)
    if user is None:
        # Verify against a throwaway hash so an unknown username takes as long as a wrong password.
        verify_password(password, _dummy_hash(scrypt_n))
        raise UnauthorizedError(LOGIN_FAILED)
    if not verify_password(password, user.password_hash):
        raise UnauthorizedError(LOGIN_FAILED)
    return user


def issue_token(store: Store, user: UserRecord, ttl_minutes: int) -> str:
    token = secrets.token_urlsafe(32)
    store.save_token(token, user.id, store.now() + timedelta(minutes=ttl_minutes))
    return token


# ---- FastAPI dependencies ----------------------------------------------------

# auto_error=False so a missing header gets our Error body instead of FastAPI's.
_bearer = HTTPBearer(auto_error=False, description="The token returned by POST /api/auth/login.")


def get_store(request: Request) -> Store:
    return request.app.state.store


StoreDep = Annotated[Store, Depends(get_store)]


def current_user(
    store: StoreDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> UserRecord:
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise UnauthorizedError("Please log in to continue.")
    user = store.user_for_token(credentials.credentials)
    if user is None:
        raise UnauthorizedError(SESSION_EXPIRED)
    return user


def require_admin(user: Annotated[UserRecord, Depends(current_user)]) -> UserRecord:
    if user.role is not UserRole.ADMIN:
        raise ForbiddenError("You don't have access to that page.")
    return user


def require_restaurant(
    store: StoreDep, user: Annotated[UserRecord, Depends(current_user)]
) -> RestaurantRecord:
    """The signed-in staff member's own restaurant. Staff can never address another one."""
    if user.role is not UserRole.RESTAURANT or user.restaurant_id is None:
        raise ForbiddenError("You don't have access to that page.")
    if user.restaurant_id not in store.restaurants:
        raise UnauthorizedError(SESSION_EXPIRED)
    return store.restaurants[user.restaurant_id]


AdminUser = Annotated[UserRecord, Depends(require_admin)]
StaffRestaurant = Annotated[RestaurantRecord, Depends(require_restaurant)]
