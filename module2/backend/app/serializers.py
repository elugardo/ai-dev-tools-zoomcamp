"""ORM rows -> response models. Derived fields (positions, counts, usernames)
are computed here, never stored."""

from sqlalchemy import inspect

from .db import Base
from .models import AdminRestaurant, EaterWaitlistView, PublicRestaurant, SessionUser, StaffWaitlistEntry
from .store import Store
from .tables import Restaurant, User, WaitlistEntry


def columns(row: Base) -> dict:
    """A row's column values by attribute name."""
    return {attr.key: getattr(row, attr.key) for attr in inspect(row).mapper.column_attrs}


def session_user(user: User) -> SessionUser:
    return SessionUser(id=user.id, username=user.username, role=user.role, restaurant_id=user.restaurant_id)


def public_restaurant(store: Store, restaurant: Restaurant) -> PublicRestaurant:
    return PublicRestaurant(**columns(restaurant), waiting_parties=store.waiting_parties(restaurant.id))


def admin_restaurant(store: Store, restaurant: Restaurant) -> AdminRestaurant:
    login = store.restaurant_login(restaurant.id)
    return AdminRestaurant(
        **columns(restaurant),
        waiting_parties=store.waiting_parties(restaurant.id),
        username=login.username if login else "",
    )


def eater_view(store: Store, entry: WaitlistEntry) -> EaterWaitlistView:
    return EaterWaitlistView(
        **columns(entry),
        restaurant_name=store.restaurant(entry.restaurant_id).name,
        position=store.position(entry),
    )


def staff_entry(store: Store, entry: WaitlistEntry, position: int | None = None) -> StaffWaitlistEntry:
    """`position` may be passed when already known (e.g. while listing the queue)
    to avoid a count query per row."""
    return StaffWaitlistEntry(**columns(entry), position=position if position is not None else store.position(entry))
