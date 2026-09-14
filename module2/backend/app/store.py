"""The in-memory store: every user, restaurant, waitlist entry and login token.

Records are plain dataclasses shaped like the tables in spec §25, so moving to
SQLAlchemy in Phase 4 means swapping this module rather than the routers.

Concurrency: uvicorn runs sync endpoints on a thread pool, so every public
method takes the store's re-entrant lock. Routers wrap a whole request in
`store.transaction()` so a read-check-write sequence is atomic, and the
automatic no-show sweep runs at the start of each transaction.
"""

import secrets
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, tzinfo
from functools import wraps

from . import rules
from .errors import ConflictError, EntryClosedError, NotFoundError, ValidationError, WaitlistClosedError
from .models import (
    JoinWaitlistRequest,
    RestaurantRequest,
    UserRole,
    WaitlistSource,
    WaitlistStatus,
)

RESTAURANT_NOT_FOUND = "We couldn't find that restaurant."
ENTRY_NOT_FOUND = "We couldn't find that waitlist spot. Check your link and try again."
PARTY_NOT_FOUND = "We couldn't find that party."


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class UserRecord:
    id: int
    username: str
    password_hash: str
    role: UserRole
    restaurant_id: int | None
    created_at: datetime


@dataclass
class RestaurantRecord:
    id: int
    name: str
    address: str
    phone: str
    description: str
    current_wait_minutes: int
    no_show_minutes: int
    is_active: bool
    online_waitlist_enabled: bool
    created_at: datetime
    updated_at: datetime


@dataclass
class EntryRecord:
    id: int
    restaurant_id: int
    public_token: str
    guest_name: str
    mobile_phone: str
    party_size: int
    notes: str | None
    source: WaitlistSource
    status: WaitlistStatus
    quoted_wait_minutes: int
    joined_at: datetime
    estimated_ready_at: datetime
    notified_at: datetime | None = None
    seated_at: datetime | None = None
    canceled_at: datetime | None = None
    no_show_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class TokenRecord:
    token: str
    user_id: int
    expires_at: datetime


def _locked[**P, R](method: Callable[P, R]) -> Callable[P, R]:
    @wraps(method)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        with args[0]._lock:  # type: ignore[attr-defined]
            return method(*args, **kwargs)

    return wrapper


@dataclass
class Store:
    clock: Callable[[], datetime] = utc_now
    # Timezone that decides what "today" means for history; None = server local time.
    local_tz: tzinfo | None = None
    users: dict[int, UserRecord] = field(default_factory=dict)
    restaurants: dict[int, RestaurantRecord] = field(default_factory=dict)
    entries: dict[int, EntryRecord] = field(default_factory=dict)
    tokens: dict[str, TokenRecord] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def now(self) -> datetime:
        return self.clock()

    @contextmanager
    def transaction(self) -> Iterator["Store"]:
        with self._lock:
            self.sweep_no_shows()
            yield self

    @staticmethod
    def _next_id(rows: dict[int, object]) -> int:
        return max(rows, default=0) + 1

    # ---- users and tokens -------------------------------------------------

    @_locked
    def add_user(self, username: str, password_hash: str, role: UserRole, restaurant_id: int | None) -> UserRecord:
        if self.user_by_username(username):
            raise ConflictError("That username is already taken.", {"username": "That username is already taken."})
        user = UserRecord(
            id=self._next_id(self.users),
            username=username,
            password_hash=password_hash,
            role=role,
            restaurant_id=restaurant_id,
            created_at=self.now(),
        )
        self.users[user.id] = user
        return user

    @_locked
    def user_by_username(self, username: str) -> UserRecord | None:
        return next((u for u in self.users.values() if u.username == username), None)

    @_locked
    def restaurant_login(self, restaurant_id: int) -> UserRecord | None:
        return next(
            (u for u in self.users.values() if u.role is UserRole.RESTAURANT and u.restaurant_id == restaurant_id),
            None,
        )

    @_locked
    def save_token(self, token: str, user_id: int, expires_at: datetime) -> None:
        self.tokens[token] = TokenRecord(token=token, user_id=user_id, expires_at=expires_at)

    @_locked
    def user_for_token(self, token: str) -> UserRecord | None:
        """The live user behind a token; expired tokens are dropped on sight."""
        record = self.tokens.get(token)
        if record is None:
            return None
        if self.now() >= record.expires_at:
            del self.tokens[token]
            return None
        return self.users.get(record.user_id)

    @_locked
    def revoke_tokens(self, user_id: int) -> None:
        for token in [t for t, record in self.tokens.items() if record.user_id == user_id]:
            del self.tokens[token]

    # ---- restaurants ------------------------------------------------------

    @_locked
    def restaurant(self, restaurant_id: int) -> RestaurantRecord:
        restaurant = self.restaurants.get(restaurant_id)
        if restaurant is None:
            raise NotFoundError(RESTAURANT_NOT_FOUND)
        return restaurant

    @_locked
    def list_restaurants(self, *, active_only: bool) -> list[RestaurantRecord]:
        rows = [r for r in self.restaurants.values() if r.is_active or not active_only]
        return sorted(rows, key=lambda r: (r.name.lower(), r.id))

    @_locked
    def waiting_parties(self, restaurant_id: int) -> int:
        return len(rules.active_queue(self.entries_for(restaurant_id)))

    @_locked
    def create_restaurant(self, data: RestaurantRequest, password_hash: str) -> RestaurantRecord:
        if self.user_by_username(data.username):
            raise ConflictError("That username is already taken.", {"username": "That username is already taken."})
        now = self.now()
        restaurant = RestaurantRecord(
            id=self._next_id(self.restaurants),
            name=data.name,
            address=data.address,
            phone=data.phone,
            description=data.description,
            current_wait_minutes=data.current_wait_minutes,
            no_show_minutes=data.no_show_minutes,
            is_active=data.is_active,
            online_waitlist_enabled=True,
            created_at=now,
            updated_at=now,
        )
        self.restaurants[restaurant.id] = restaurant
        self.add_user(data.username, password_hash, UserRole.RESTAURANT, restaurant.id)
        return restaurant

    @_locked
    def update_restaurant(
        self, restaurant_id: int, data: RestaurantRequest, password_hash: str | None
    ) -> RestaurantRecord:
        """Edits the restaurant and its login. `password_hash=None` keeps the password."""
        restaurant = self.restaurant(restaurant_id)
        login = self.restaurant_login(restaurant_id)
        taken_by = self.user_by_username(data.username)
        if taken_by is not None and taken_by is not login:
            raise ConflictError("That username is already taken.", {"username": "That username is already taken."})

        restaurant.name = data.name
        restaurant.address = data.address
        restaurant.phone = data.phone
        restaurant.description = data.description
        restaurant.current_wait_minutes = data.current_wait_minutes
        restaurant.no_show_minutes = data.no_show_minutes
        restaurant.is_active = data.is_active
        restaurant.updated_at = self.now()

        if login is None:
            if password_hash is None:
                raise ValidationError(
                    "Please fix the highlighted fields.",
                    {"password": "Set a password for this restaurant's login."},
                )
            self.add_user(data.username, password_hash, UserRole.RESTAURANT, restaurant.id)
        else:
            login.username = data.username
            if password_hash is not None:
                login.password_hash = password_hash
                # A new password signs out every session using the old one.
                self.revoke_tokens(login.id)
        return restaurant

    @_locked
    def set_restaurant_active(self, restaurant_id: int, is_active: bool) -> RestaurantRecord:
        restaurant = self.restaurant(restaurant_id)
        restaurant.is_active = is_active
        restaurant.updated_at = self.now()
        return restaurant

    @_locked
    def update_settings(
        self, restaurant_id: int, *, current_wait_minutes: int | None, online_waitlist_enabled: bool | None
    ) -> RestaurantRecord:
        restaurant = self.restaurant(restaurant_id)
        if current_wait_minutes is not None:
            restaurant.current_wait_minutes = current_wait_minutes
        if online_waitlist_enabled is not None:
            restaurant.online_waitlist_enabled = online_waitlist_enabled
        restaurant.updated_at = self.now()
        return restaurant

    # ---- waitlist entries -------------------------------------------------

    @_locked
    def entries_for(self, restaurant_id: int) -> list[EntryRecord]:
        return [e for e in self.entries.values() if e.restaurant_id == restaurant_id]

    @_locked
    def active_queue(self, restaurant_id: int) -> list[EntryRecord]:
        return rules.active_queue(self.entries_for(restaurant_id))

    @_locked
    def history(self, restaurant_id: int) -> list[EntryRecord]:
        return rules.todays_history(self.entries_for(restaurant_id), self.now(), self.local_tz)

    @_locked
    def position(self, entry: EntryRecord) -> int | None:
        return rules.queue_position(self.entries_for(entry.restaurant_id), entry.id)

    @_locked
    def entry_by_token(self, token: str) -> EntryRecord:
        entry = next((e for e in self.entries.values() if e.public_token == token), None)
        if entry is None:
            raise NotFoundError(ENTRY_NOT_FOUND)
        return entry

    @_locked
    def restaurant_entry(self, restaurant_id: int, entry_id: int) -> EntryRecord:
        """An entry at this restaurant; another restaurant's entry is reported as missing."""
        entry = self.entries.get(entry_id)
        if entry is None or entry.restaurant_id != restaurant_id:
            raise NotFoundError(PARTY_NOT_FOUND)
        return entry

    @_locked
    def insert_entry(self, entry: EntryRecord) -> EntryRecord:
        """Stores a fully built entry (used by seeding); assigns the id."""
        entry.id = self._next_id(self.entries)
        self.entries[entry.id] = entry
        return entry

    @_locked
    def add_entry(self, restaurant_id: int, data: JoinWaitlistRequest, source: WaitlistSource) -> EntryRecord:
        restaurant = self.restaurant(restaurant_id)
        if source is WaitlistSource.ONLINE and not (restaurant.is_active and restaurant.online_waitlist_enabled):
            raise WaitlistClosedError(f"{restaurant.name} isn't accepting online waitlist entries right now.")
        now = self.now()
        return self.insert_entry(
            EntryRecord(
                id=0,
                restaurant_id=restaurant.id,
                public_token=secrets.token_urlsafe(16),
                guest_name=data.guest_name,
                mobile_phone=data.mobile_phone,
                party_size=data.party_size,
                notes=data.notes or None,
                source=source,
                status=WaitlistStatus.WAITING,
                quoted_wait_minutes=restaurant.current_wait_minutes,
                joined_at=now,
                estimated_ready_at=rules.estimate_ready_at(now, restaurant.current_wait_minutes),
                created_at=now,
                updated_at=now,
            )
        )

    @_locked
    def change_status(self, entry: EntryRecord, status: WaitlistStatus) -> EntryRecord:
        if not rules.is_active(entry.status):
            raise EntryClosedError("This party has already been seated, canceled, or marked as a no-show.")
        if not rules.can_transition(entry.status, status):
            raise ValidationError("That status change isn't allowed.")
        now = self.now()
        entry.status = status
        setattr(entry, rules.TIMESTAMP_FIELDS[status], now)
        entry.updated_at = now
        return entry

    @_locked
    def sweep_no_shows(self) -> None:
        """Moves overdue notified parties to NO_SHOW, stamped at their deadline (spec §14)."""
        now = self.now()
        for entry in self.entries.values():
            restaurant = self.restaurants.get(entry.restaurant_id)
            if restaurant and rules.is_overdue_no_show(entry, restaurant.no_show_minutes, now):
                deadline = rules.no_show_deadline(entry, restaurant.no_show_minutes)
                entry.status = WaitlistStatus.NO_SHOW
                entry.no_show_at = deadline
                entry.updated_at = deadline
