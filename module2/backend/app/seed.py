"""Demo data (spec §34), so the frontend has something to show.

It is loaded only into an empty database (`seed_if_empty`), so restarting the
server never duplicates or overwrites real data. Timestamps are relative to the
moment of seeding, so a fresh database looks live; `python -m app.manage
reset-db` rebuilds it when the demo queue has gone stale. Every demo login uses the password `password`. Seeded eater pages have
readable tokens, for example /wait/demo-sarah.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from . import rules
from .models import UserRole, WaitlistSource, WaitlistStatus
from .store import Store
from .tables import Restaurant, WaitlistEntry

DEMO_PASSWORD = "password"


@dataclass(frozen=True)
class SeedEntry:
    token: str
    guest: str
    phone: str
    size: int
    quote: int
    joined_minutes_ago: int
    notes: str | None = None
    source: WaitlistSource = WaitlistSource.ONLINE
    status: WaitlistStatus = WaitlistStatus.WAITING
    # Minutes after joining that the entry reached its current status.
    status_after: int | None = None


BLUEBIRD_ENTRIES = [
    SeedEntry("demo-sarah", "Sarah", "555-010-2233", 6, 30, 32),
    SeedEntry("demo-james", "James", "555-010-4455", 4, 30, 24, notes="High chair please"),
    SeedEntry("demo-marcos", "Marcos", "555-010-6677", 2, 25, 18, source=WaitlistSource.STAFF),
    SeedEntry("demo-lena", "Lena", "555-010-8899", 3, 30, 35, status=WaitlistStatus.NOTIFIED, status_after=32),
    SeedEntry("demo-chen", "Chen", "555-010-1100", 2, 20, 70, status=WaitlistStatus.SEATED, status_after=25),
    SeedEntry("demo-alex", "Alex", "555-010-1212", 5, 30, 50, status=WaitlistStatus.CANCELED, status_after=12),
]

OAK_ENTRIES = [
    SeedEntry("demo-jordan", "Jordan", "555-020-3344", 5, 20, 12),
    SeedEntry("demo-taylor", "Taylor", "555-020-5566", 2, 20, 5),
]


def seed_if_empty(store: Store, hash_password: Callable[[str], str]) -> bool:
    """Seeds the demo data unless the database already has users. Returns True if it seeded."""
    if store.has_users():
        return False
    seed_demo_data(store, hash_password)
    return True


def seed_demo_data(store: Store, hash_password: Callable[[str], str]) -> None:
    now = store.now()
    month_ago = now - timedelta(days=30)
    store.add_user("admin", hash_password(DEMO_PASSWORD), UserRole.ADMIN, None)

    def add_restaurant(username: str, entries: list[SeedEntry], **fields) -> None:
        restaurant = Restaurant(
            is_active=True,
            online_waitlist_enabled=True,
            no_show_minutes=10,
            created_at=month_ago,
            updated_at=now - timedelta(hours=1),
            **fields,
        )
        store.session.add(restaurant)
        store.session.flush()
        store.add_user(username, hash_password(DEMO_PASSWORD), UserRole.RESTAURANT, restaurant.id)

        for seed in entries:
            joined_at = now - timedelta(minutes=seed.joined_minutes_ago)
            status_at = None if seed.status_after is None else joined_at + timedelta(minutes=seed.status_after)
            entry = WaitlistEntry(
                restaurant_id=restaurant.id,
                public_token=seed.token,
                guest_name=seed.guest,
                mobile_phone=seed.phone,
                party_size=seed.size,
                notes=seed.notes,
                source=seed.source,
                status=seed.status,
                quoted_wait_minutes=seed.quote,
                joined_at=joined_at,
                estimated_ready_at=rules.estimate_ready_at(joined_at, seed.quote),
                created_at=joined_at,
                updated_at=status_at or joined_at,
            )
            if status_at is not None:
                setattr(entry, rules.TIMESTAMP_FIELDS[seed.status], status_at)
                # Seated and no-show parties were notified a few minutes first.
                if seed.status in (WaitlistStatus.SEATED, WaitlistStatus.NO_SHOW):
                    entry.notified_at = status_at - timedelta(minutes=4)
            store.session.add(entry)
        store.session.flush()

    add_restaurant(
        "bluebird",
        BLUEBIRD_ENTRIES,
        name="Bluebird Cafe",
        address="123 Main Street",
        phone="(555) 201-4455",
        description="All-day breakfast, strong coffee, and a sunny patio.",
        current_wait_minutes=30,
    )
    add_restaurant(
        "oakember",
        OAK_ENTRIES,
        name="Oak & Ember",
        address="456 Market Street",
        phone="(555) 309-7788",
        description="Wood-fired grill and seasonal small plates.",
        current_wait_minutes=20,
    )
