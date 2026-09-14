"""The store: every read and write WaitWise makes, over a SQLAlchemy session.

One Store wraps one request's session (see auth.get_store), so everything a
request does is a single transaction that commits or rolls back as a whole.

Portability rules for this module:
- ORM queries and generic SQL only. No raw SQL strings, dialect imports or
  database functions; date arithmetic happens in Python via rules.py.
- Concurrent writers are handled with conditional UPDATEs
  (`... WHERE id = :id AND status = :expected`) that check the row count. That
  works on every database, needs no row locks, and a stale request can never
  overwrite a newer status.
"""

import hashlib
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, time, timedelta, tzinfo

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from . import rules
from .errors import ConflictError, EntryClosedError, NotFoundError, ValidationError, WaitlistClosedError
from .models import JoinWaitlistRequest, RestaurantRequest, UserRole, WaitlistSource, WaitlistStatus
from .tables import AuthToken, Restaurant, User, WaitlistEntry

RESTAURANT_NOT_FOUND = "We couldn't find that restaurant."
ENTRY_NOT_FOUND = "We couldn't find that waitlist spot. Check your link and try again."
PARTY_NOT_FOUND = "We couldn't find that party."
USERNAME_TAKEN = "That username is already taken."
ENTRY_FINISHED = "This party has already been seated, canceled, or marked as a no-show."


def utc_now() -> datetime:
    return datetime.now(UTC)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class Store:
    def __init__(
        self,
        session: Session,
        clock: Callable[[], datetime] = utc_now,
        local_tz: tzinfo | None = None,
    ):
        self.session = session
        self.clock = clock
        # Timezone that decides what "today" means for history; None = server local time.
        self.local_tz = local_tz

    def now(self) -> datetime:
        return self.clock()

    # ---- users and tokens -------------------------------------------------

    def add_user(self, username: str, password_hash: str, role: UserRole, restaurant_id: int | None) -> User:
        if self.user_by_username(username) is not None:
            raise ConflictError(USERNAME_TAKEN, {"username": USERNAME_TAKEN})
        user = User(
            username=username,
            password_hash=password_hash,
            role=role,
            restaurant_id=restaurant_id,
            created_at=self.now(),
        )
        self.session.add(user)
        self.session.flush()  # surfaces constraint violations here, inside the request
        return user

    def user_by_username(self, username: str) -> User | None:
        return self.session.scalar(select(User).where(User.username == username))

    def restaurant_login(self, restaurant_id: int) -> User | None:
        return self.session.scalar(
            select(User).where(User.role == UserRole.RESTAURANT, User.restaurant_id == restaurant_id)
        )

    def has_users(self) -> bool:
        return self.session.scalar(select(func.count()).select_from(User)) > 0

    def save_token(self, token: str, user_id: int, expires_at: datetime) -> None:
        """Stores only a hash of the bearer token, and clears out expired ones.

        Cleanup happens here rather than when an expired token is presented,
        because that request ends in a 401 and its transaction is rolled back.
        """
        now = self.now()
        self.session.execute(delete(AuthToken).where(AuthToken.expires_at <= now))
        self.session.add(AuthToken(token_hash=hash_token(token), user_id=user_id, expires_at=expires_at, created_at=now))
        self.session.flush()

    def user_for_token(self, token: str) -> User | None:
        """The live user behind a token, or None if it is unknown or expired."""
        record = self.session.get(AuthToken, hash_token(token))
        if record is None or self.now() >= record.expires_at:
            return None
        return self.session.get(User, record.user_id)

    def revoke_tokens(self, user_id: int) -> None:
        self.session.execute(delete(AuthToken).where(AuthToken.user_id == user_id))

    # ---- restaurants ------------------------------------------------------

    def restaurant(self, restaurant_id: int) -> Restaurant:
        restaurant = self.session.get(Restaurant, restaurant_id)
        if restaurant is None:
            raise NotFoundError(RESTAURANT_NOT_FOUND)
        return restaurant

    def list_restaurants(self, *, active_only: bool) -> list[Restaurant]:
        query = select(Restaurant)
        if active_only:
            query = query.where(Restaurant.is_active.is_(True))
        rows = self.session.scalars(query).all()
        # Sorted in Python: case-insensitive ordering differs between databases.
        return sorted(rows, key=lambda r: (r.name.lower(), r.id))

    def waiting_parties(self, restaurant_id: int) -> int:
        return self.session.scalar(
            select(func.count())
            .select_from(WaitlistEntry)
            .where(
                WaitlistEntry.restaurant_id == restaurant_id,
                WaitlistEntry.status.in_(rules.ACTIVE_STATUSES),
            )
        )

    def create_restaurant(self, data: RestaurantRequest, password_hash: str) -> Restaurant:
        if self.user_by_username(data.username) is not None:
            raise ConflictError(USERNAME_TAKEN, {"username": USERNAME_TAKEN})
        now = self.now()
        restaurant = Restaurant(
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
        self.session.add(restaurant)
        self.session.flush()
        self.add_user(data.username, password_hash, UserRole.RESTAURANT, restaurant.id)
        return restaurant

    def update_restaurant(self, restaurant_id: int, data: RestaurantRequest, password_hash: str | None) -> Restaurant:
        """Edits the restaurant and its login. `password_hash=None` keeps the password."""
        restaurant = self.restaurant(restaurant_id)
        login = self.restaurant_login(restaurant_id)
        taken_by = self.user_by_username(data.username)
        if taken_by is not None and (login is None or taken_by.id != login.id):
            raise ConflictError(USERNAME_TAKEN, {"username": USERNAME_TAKEN})

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
        self.session.flush()
        return restaurant

    def set_restaurant_active(self, restaurant_id: int, is_active: bool) -> Restaurant:
        restaurant = self.restaurant(restaurant_id)
        restaurant.is_active = is_active
        restaurant.updated_at = self.now()
        self.session.flush()
        return restaurant

    def update_settings(
        self, restaurant_id: int, *, current_wait_minutes: int | None, online_waitlist_enabled: bool | None
    ) -> Restaurant:
        restaurant = self.restaurant(restaurant_id)
        if current_wait_minutes is not None:
            restaurant.current_wait_minutes = current_wait_minutes
        if online_waitlist_enabled is not None:
            restaurant.online_waitlist_enabled = online_waitlist_enabled
        restaurant.updated_at = self.now()
        self.session.flush()
        return restaurant

    # ---- waitlist entries -------------------------------------------------

    def active_queue(self, restaurant_id: int) -> list[WaitlistEntry]:
        """Active parties in queue order: oldest first, id breaking ties (spec §27)."""
        return list(
            self.session.scalars(
                select(WaitlistEntry)
                .where(
                    WaitlistEntry.restaurant_id == restaurant_id,
                    WaitlistEntry.status.in_(rules.ACTIVE_STATUSES),
                )
                .order_by(WaitlistEntry.joined_at, WaitlistEntry.id)
            )
        )

    def position(self, entry: WaitlistEntry) -> int | None:
        """1-based queue position, counted in the database; None once finished."""
        if not rules.is_active(entry.status):
            return None
        return self.session.scalar(
            select(func.count())
            .select_from(WaitlistEntry)
            .where(
                WaitlistEntry.restaurant_id == entry.restaurant_id,
                WaitlistEntry.status.in_(rules.ACTIVE_STATUSES),
                or_(
                    WaitlistEntry.joined_at < entry.joined_at,
                    (WaitlistEntry.joined_at == entry.joined_at) & (WaitlistEntry.id <= entry.id),
                ),
            )
        )

    def history(self, restaurant_id: int) -> list[WaitlistEntry]:
        """Today's finished parties, most recent first (spec §20).

        The database narrows to parties finished since the start of the local
        day; rules.todays_history makes the final, timezone-aware decision.
        """
        now = self.now()
        local_now = now.astimezone(self.local_tz)
        day_start = datetime.combine(local_now.date(), time.min, tzinfo=local_now.tzinfo).astimezone(UTC)
        since = day_start - timedelta(days=1)  # generous margin for DST shifts
        candidates = self.session.scalars(
            select(WaitlistEntry).where(
                WaitlistEntry.restaurant_id == restaurant_id,
                or_(
                    (WaitlistEntry.status == WaitlistStatus.SEATED) & (WaitlistEntry.seated_at >= since),
                    (WaitlistEntry.status == WaitlistStatus.CANCELED) & (WaitlistEntry.canceled_at >= since),
                    (WaitlistEntry.status == WaitlistStatus.NO_SHOW) & (WaitlistEntry.no_show_at >= since),
                ),
            )
        ).all()
        return rules.todays_history(candidates, now, self.local_tz)

    def entry_by_token(self, token: str) -> WaitlistEntry:
        entry = self.session.scalar(select(WaitlistEntry).where(WaitlistEntry.public_token == token))
        if entry is None:
            raise NotFoundError(ENTRY_NOT_FOUND)
        return entry

    def restaurant_entry(self, restaurant_id: int, entry_id: int) -> WaitlistEntry:
        """An entry at this restaurant; another restaurant's entry is reported as missing."""
        entry = self.session.get(WaitlistEntry, entry_id)
        if entry is None or entry.restaurant_id != restaurant_id:
            raise NotFoundError(PARTY_NOT_FOUND)
        return entry

    def add_entry(self, restaurant_id: int, data: JoinWaitlistRequest, source: WaitlistSource) -> WaitlistEntry:
        restaurant = self.restaurant(restaurant_id)
        if source is WaitlistSource.ONLINE and not (restaurant.is_active and restaurant.online_waitlist_enabled):
            raise WaitlistClosedError(f"{restaurant.name} isn't accepting online waitlist entries right now.")
        now = self.now()
        entry = WaitlistEntry(
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
        self.session.add(entry)
        self.session.flush()
        return entry

    def change_status(self, entry: WaitlistEntry, status: WaitlistStatus) -> WaitlistEntry:
        if not rules.is_active(entry.status):
            raise EntryClosedError(ENTRY_FINISHED)
        if not rules.can_transition(entry.status, status):
            raise ValidationError("That status change isn't allowed.")

        now = self.now()
        changed = self._update_if_status(
            entry.id,
            expected=entry.status,
            values={"status": status, rules.TIMESTAMP_FIELDS[status]: now, "updated_at": now},
        )
        self.session.refresh(entry)
        if not changed:
            # Someone else moved this party first. Decide again from its current
            # status; statuses only move forward, so this recursion is bounded.
            return self.change_status(entry, status)
        return entry

    def sweep_no_shows(self) -> None:
        """Moves overdue notified parties to NO_SHOW, stamped at their deadline (spec §14)."""
        now = self.now()
        overdue = self.session.execute(
            select(WaitlistEntry, Restaurant.no_show_minutes)
            .join(Restaurant, Restaurant.id == WaitlistEntry.restaurant_id)
            .where(WaitlistEntry.status == WaitlistStatus.NOTIFIED)
        ).all()
        for entry, no_show_minutes in overdue:
            if rules.is_overdue_no_show(entry, no_show_minutes, now):
                deadline = rules.no_show_deadline(entry, no_show_minutes)
                # Conditional: a party seated a moment ago by staff stays seated.
                if self._update_if_status(
                    entry.id,
                    expected=WaitlistStatus.NOTIFIED,
                    values={"status": WaitlistStatus.NO_SHOW, "no_show_at": deadline, "updated_at": deadline},
                ):
                    self.session.refresh(entry)

    def _update_if_status(self, entry_id: int, *, expected: WaitlistStatus, values: dict) -> bool:
        result = self.session.execute(
            update(WaitlistEntry)
            .where(WaitlistEntry.id == entry_id, WaitlistEntry.status == expected)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        return result.rowcount == 1
