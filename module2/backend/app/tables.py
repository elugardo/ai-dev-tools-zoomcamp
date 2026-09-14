"""ORM tables (spec §25), using only portable SQLAlchemy types.

- Enums are stored as short strings with a CHECK constraint (`native_enum=False`),
  not database-native enum types, so the schema is the same on every database.
- String columns have explicit lengths: SQLite ignores them, but Postgres and
  others enforce them. The request models validate the same limits first.
- Timestamps use UTCDateTime from db.py.
"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, CheckConstraint, Enum, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, UTCDateTime
from .models import (
    ADDRESS_MAX,
    DESCRIPTION_MAX,
    GUEST_NAME_MAX,
    NOTES_MAX,
    PHONE_MAX,
    RESTAURANT_NAME_MAX,
    USERNAME_MAX,
    UserRole,
    WaitlistSource,
    WaitlistStatus,
)


def portable_enum(enum_class: type[StrEnum], name: str) -> Enum:
    return Enum(
        enum_class,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=16,
        values_callable=lambda members: [m.value for m in members],
        validate_strings=True,
    )


class Restaurant(Base):
    __tablename__ = "restaurants"
    __table_args__ = (
        CheckConstraint("current_wait_minutes >= 0 AND current_wait_minutes <= 240", name="ck_restaurants_wait"),
        CheckConstraint("no_show_minutes >= 1 AND no_show_minutes <= 120", name="ck_restaurants_no_show"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(RESTAURANT_NAME_MAX))
    address: Mapped[str] = mapped_column(String(ADDRESS_MAX))
    phone: Mapped[str] = mapped_column(String(PHONE_MAX))
    description: Mapped[str] = mapped_column(String(DESCRIPTION_MAX), default="")
    current_wait_minutes: Mapped[int] = mapped_column(Integer)
    no_show_minutes: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean)
    online_waitlist_enabled: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(USERNAME_MAX), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(portable_enum(UserRole, "user_role"))
    # Unique: a restaurant has exactly one login (spec §25). NULL for the admin.
    restaurant_id: Mapped[int | None] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), unique=True, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class WaitlistEntry(Base):
    __tablename__ = "waitlist_entries"
    __table_args__ = (
        CheckConstraint("party_size >= 1 AND party_size <= 20", name="ck_entries_party_size"),
        # The queue, the dashboard and the no-show sweep all filter on these.
        Index("ix_entries_restaurant_status_joined", "restaurant_id", "status", "joined_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id", ondelete="CASCADE"))
    public_token: Mapped[str] = mapped_column(String(64), unique=True)
    guest_name: Mapped[str] = mapped_column(String(GUEST_NAME_MAX))
    mobile_phone: Mapped[str] = mapped_column(String(PHONE_MAX))
    party_size: Mapped[int] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(String(NOTES_MAX), nullable=True)
    source: Mapped[WaitlistSource] = mapped_column(portable_enum(WaitlistSource, "waitlist_source"))
    status: Mapped[WaitlistStatus] = mapped_column(portable_enum(WaitlistStatus, "waitlist_status"))
    quoted_wait_minutes: Mapped[int] = mapped_column(Integer)
    joined_at: Mapped[datetime] = mapped_column(UTCDateTime)
    estimated_ready_at: Mapped[datetime] = mapped_column(UTCDateTime)
    notified_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    seated_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    canceled_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    no_show_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class AuthToken(Base):
    """A login session. Only a SHA-256 of the bearer token is stored, so a copy of
    the database cannot be used to impersonate anyone."""

    __tablename__ = "auth_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
