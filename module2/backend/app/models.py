"""Request and response models: the JSON shapes in openapi.yaml.

Request validators raise ValueError with the same friendly messages the
frontend's form validation shows (frontend/src/domain/validation.ts), because a
request that bypasses the form should still get a readable field error.
"""

import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, StringConstraints, field_validator

PARTY_SIZE_MIN, PARTY_SIZE_MAX = 1, 20
NOTES_MAX = 250
DESCRIPTION_MAX = 500
WAIT_MINUTES_MAX = 240
NO_SHOW_MINUTES_MIN, NO_SHOW_MINUTES_MAX = 1, 120
PASSWORD_MIN = 8

Trimmed = Annotated[str, StringConstraints(strip_whitespace=True)]


class UserRole(StrEnum):
    ADMIN = "ADMIN"
    RESTAURANT = "RESTAURANT"


class WaitlistStatus(StrEnum):
    WAITING = "WAITING"
    NOTIFIED = "NOTIFIED"
    SEATED = "SEATED"
    CANCELED = "CANCELED"
    NO_SHOW = "NO_SHOW"


class WaitlistSource(StrEnum):
    ONLINE = "ONLINE"
    STAFF = "STAFF"


# ---- shared checks ---------------------------------------------------------

_PHONE_CHARS = re.compile(r"^\+?[\d\s().-]+$")


def is_valid_phone(value: str) -> bool:
    """Basic format check only: optional +, digits with common separators, 7-15 digits."""
    if not _PHONE_CHARS.match(value):
        return False
    digits = sum(ch.isdigit() for ch in value)
    return 7 <= digits <= 15


def _whole_number(value: Any, low: int, high: int, message: str) -> int:
    # bool is an int subclass; True must not count as a party of one.
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        if isinstance(value, float) and value.is_integer() and low <= value <= high:
            return int(value)
        raise ValueError(message)
    return value


def check_wait_minutes(value: Any) -> int:
    return _whole_number(
        value, 0, WAIT_MINUTES_MAX, f"Wait must be a whole number from 0 to {WAIT_MINUTES_MAX} minutes."
    )


# ---- auth ------------------------------------------------------------------


class LoginRequest(BaseModel):
    username: Trimmed
    password: str

    @field_validator("username")
    @classmethod
    def _username(cls, value: str) -> str:
        if not value:
            raise ValueError("Username is required.")
        return value.lower()

    @field_validator("password")
    @classmethod
    def _password(cls, value: str) -> str:
        if not value:
            raise ValueError("Password is required.")
        return value


class SessionUser(BaseModel):
    id: int
    username: str
    role: UserRole
    restaurant_id: int | None


class LoginResult(BaseModel):
    token: str
    user: SessionUser


# ---- restaurants -----------------------------------------------------------


class PublicRestaurant(BaseModel):
    id: int
    name: str
    address: str
    phone: str
    description: str
    current_wait_minutes: int
    is_active: bool
    online_waitlist_enabled: bool
    waiting_parties: int


class AdminRestaurant(PublicRestaurant):
    no_show_minutes: int
    username: str
    created_at: datetime
    updated_at: datetime


class RestaurantRequest(BaseModel):
    """Shared by create and edit; the two differ only in the password rule."""

    name: Trimmed
    address: Trimmed
    phone: Trimmed
    description: Trimmed = ""
    current_wait_minutes: Any = 30
    no_show_minutes: Any = 10
    is_active: bool = True
    username: Trimmed
    password: str = ""

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not value:
            raise ValueError("Name is required.")
        return value

    @field_validator("address")
    @classmethod
    def _address(cls, value: str) -> str:
        if not value:
            raise ValueError("Address is required.")
        return value

    @field_validator("phone")
    @classmethod
    def _phone(cls, value: str) -> str:
        if not value:
            raise ValueError("Phone is required.")
        if not is_valid_phone(value):
            raise ValueError("Enter a valid phone number.")
        return value

    @field_validator("description")
    @classmethod
    def _description(cls, value: str) -> str:
        if len(value) > DESCRIPTION_MAX:
            raise ValueError(f"Description must be {DESCRIPTION_MAX} characters or fewer.")
        return value

    @field_validator("current_wait_minutes")
    @classmethod
    def _wait(cls, value: Any) -> int:
        return check_wait_minutes(value)

    @field_validator("no_show_minutes")
    @classmethod
    def _no_show(cls, value: Any) -> int:
        return _whole_number(
            value,
            NO_SHOW_MINUTES_MIN,
            NO_SHOW_MINUTES_MAX,
            f"No-show timeout must be a whole number from {NO_SHOW_MINUTES_MIN} to {NO_SHOW_MINUTES_MAX} minutes.",
        )

    @field_validator("username")
    @classmethod
    def _username(cls, value: str) -> str:
        value = value.lower()
        if not value:
            raise ValueError("Username is required.")
        if not re.fullmatch(r"[a-z0-9_-]+", value):
            raise ValueError("Username may only use letters, numbers, - and _.")
        return value


class RestaurantCreateRequest(RestaurantRequest):
    @field_validator("password")
    @classmethod
    def _password(cls, value: str) -> str:
        if not value:
            raise ValueError("Password is required.")
        if len(value) < PASSWORD_MIN:
            raise ValueError(f"Password must be at least {PASSWORD_MIN} characters.")
        return value


class RestaurantUpdateRequest(RestaurantRequest):
    @field_validator("password")
    @classmethod
    def _password(cls, value: str) -> str:
        # Blank keeps the current password.
        if value and len(value) < PASSWORD_MIN:
            raise ValueError(f"Password must be at least {PASSWORD_MIN} characters.")
        return value


class RestaurantSettingsRequest(BaseModel):
    current_wait_minutes: Any = None
    online_waitlist_enabled: bool | None = None

    @field_validator("current_wait_minutes")
    @classmethod
    def _wait(cls, value: Any) -> int | None:
        return None if value is None else check_wait_minutes(value)


class RestaurantStatusRequest(BaseModel):
    is_active: bool


# ---- waitlist --------------------------------------------------------------


class JoinWaitlistRequest(BaseModel):
    guest_name: Trimmed
    mobile_phone: Trimmed
    party_size: Any
    notes: Trimmed = ""

    @field_validator("guest_name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not value:
            raise ValueError("Name is required.")
        return value

    @field_validator("mobile_phone")
    @classmethod
    def _mobile(cls, value: str) -> str:
        if not value:
            raise ValueError("Mobile phone is required.")
        if not is_valid_phone(value):
            raise ValueError("Enter a valid mobile number.")
        return value

    @field_validator("party_size")
    @classmethod
    def _party_size(cls, value: Any) -> int:
        if value is None:
            raise ValueError("Party size is required.")
        return _whole_number(
            value,
            PARTY_SIZE_MIN,
            PARTY_SIZE_MAX,
            f"Party size must be a whole number from {PARTY_SIZE_MIN} to {PARTY_SIZE_MAX}.",
        )

    @field_validator("notes")
    @classmethod
    def _notes(cls, value: str) -> str:
        if len(value) > NOTES_MAX:
            raise ValueError(f"Notes must be {NOTES_MAX} characters or fewer.")
        return value


class UpdateWaitlistEntryRequest(BaseModel):
    status: WaitlistStatus

    @field_validator("status")
    @classmethod
    def _not_waiting(cls, value: WaitlistStatus) -> WaitlistStatus:
        if value is WaitlistStatus.WAITING:
            raise ValueError("A party can't be moved back to waiting.")
        return value


class EaterWaitlistView(BaseModel):
    public_token: str
    restaurant_id: int
    restaurant_name: str
    guest_name: str
    party_size: int
    status: WaitlistStatus
    quoted_wait_minutes: int
    joined_at: datetime
    estimated_ready_at: datetime
    notified_at: datetime | None
    position: int | None


class StaffWaitlistEntry(BaseModel):
    id: int
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
    notified_at: datetime | None
    seated_at: datetime | None
    canceled_at: datetime | None
    no_show_at: datetime | None
    position: int | None


class RestaurantWaitlist(BaseModel):
    restaurant: AdminRestaurant
    active: list[StaffWaitlistEntry]
    history: list[StaffWaitlistEntry]


class ErrorResponse(BaseModel):
    code: str
    message: str
    field_errors: dict[str, str]
