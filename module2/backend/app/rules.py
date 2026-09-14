"""Waitlist business rules as pure functions (spec §9, §12, §14, §15, §20, §27).

They take plain data and `now` as arguments and never touch the store or the
clock, the same as frontend/src/domain/waitlist.ts.
"""

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta, tzinfo
from typing import Protocol

from .models import WaitlistStatus

ACTIVE_STATUSES = frozenset({WaitlistStatus.WAITING, WaitlistStatus.NOTIFIED})
FINAL_STATUSES = frozenset({WaitlistStatus.SEATED, WaitlistStatus.CANCELED, WaitlistStatus.NO_SHOW})

TRANSITIONS: dict[WaitlistStatus, frozenset[WaitlistStatus]] = {
    WaitlistStatus.WAITING: frozenset(
        {WaitlistStatus.NOTIFIED, WaitlistStatus.SEATED, WaitlistStatus.CANCELED, WaitlistStatus.NO_SHOW}
    ),
    WaitlistStatus.NOTIFIED: frozenset({WaitlistStatus.SEATED, WaitlistStatus.CANCELED, WaitlistStatus.NO_SHOW}),
    WaitlistStatus.SEATED: frozenset(),
    WaitlistStatus.CANCELED: frozenset(),
    WaitlistStatus.NO_SHOW: frozenset(),
}

TIMESTAMP_FIELDS: dict[WaitlistStatus, str] = {
    WaitlistStatus.NOTIFIED: "notified_at",
    WaitlistStatus.SEATED: "seated_at",
    WaitlistStatus.CANCELED: "canceled_at",
    WaitlistStatus.NO_SHOW: "no_show_at",
}


class Timeline(Protocol):
    id: int
    status: WaitlistStatus
    joined_at: datetime
    notified_at: datetime | None
    seated_at: datetime | None
    canceled_at: datetime | None
    no_show_at: datetime | None


def is_active(status: WaitlistStatus) -> bool:
    return status in ACTIVE_STATUSES


def can_transition(current: WaitlistStatus, target: WaitlistStatus) -> bool:
    return target in TRANSITIONS[current]


def estimate_ready_at(joined_at: datetime, quoted_wait_minutes: int) -> datetime:
    """Set once at join time; never recomputed from the restaurant's later wait."""
    return joined_at + timedelta(minutes=quoted_wait_minutes)


def active_queue[T: Timeline](entries: Iterable[T]) -> list[T]:
    """Active entries, oldest first; id breaks ties so the order is stable."""
    return sorted((e for e in entries if is_active(e.status)), key=lambda e: (e.joined_at, e.id))


def queue_position(entries: Sequence[Timeline], entry_id: int) -> int | None:
    """1-based position among active entries, or None once the entry is finished."""
    for index, entry in enumerate(active_queue(entries), start=1):
        if entry.id == entry_id:
            return index
    return None


def no_show_deadline(entry: Timeline, no_show_minutes: int) -> datetime | None:
    if entry.status is not WaitlistStatus.NOTIFIED or entry.notified_at is None:
        return None
    return entry.notified_at + timedelta(minutes=no_show_minutes)


def is_overdue_no_show(entry: Timeline, no_show_minutes: int, now: datetime) -> bool:
    deadline = no_show_deadline(entry, no_show_minutes)
    return deadline is not None and now >= deadline


def final_status_at(entry: Timeline) -> datetime | None:
    if entry.status not in FINAL_STATUSES:
        return None
    return getattr(entry, TIMESTAMP_FIELDS[entry.status])


def todays_history[T: Timeline](entries: Iterable[T], now: datetime, local_tz: tzinfo | None) -> list[T]:
    """Entries that reached a final status on today's local date, most recent first.

    `local_tz=None` means the server's own local timezone.
    """
    today = now.astimezone(local_tz).date()
    finished = [
        (at, entry)
        for entry in entries
        if (at := final_status_at(entry)) is not None and at.astimezone(local_tz).date() == today
    ]
    finished.sort(key=lambda pair: pair[0], reverse=True)
    return [entry for _, entry in finished]
