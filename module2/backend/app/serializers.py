"""Store records -> response models. Derived fields (positions, counts, usernames)
are computed here, never stored."""

from dataclasses import asdict

from .models import AdminRestaurant, EaterWaitlistView, PublicRestaurant, SessionUser, StaffWaitlistEntry
from .store import EntryRecord, RestaurantRecord, Store, UserRecord


def session_user(user: UserRecord) -> SessionUser:
    return SessionUser(id=user.id, username=user.username, role=user.role, restaurant_id=user.restaurant_id)


def public_restaurant(store: Store, restaurant: RestaurantRecord) -> PublicRestaurant:
    return PublicRestaurant(**asdict(restaurant), waiting_parties=store.waiting_parties(restaurant.id))


def admin_restaurant(store: Store, restaurant: RestaurantRecord) -> AdminRestaurant:
    login = store.restaurant_login(restaurant.id)
    return AdminRestaurant(
        **asdict(restaurant),
        waiting_parties=store.waiting_parties(restaurant.id),
        username=login.username if login else "",
    )


def eater_view(store: Store, entry: EntryRecord) -> EaterWaitlistView:
    return EaterWaitlistView(
        **asdict(entry),
        restaurant_name=store.restaurant(entry.restaurant_id).name,
        position=store.position(entry),
    )


def staff_entry(store: Store, entry: EntryRecord) -> StaffWaitlistEntry:
    return StaffWaitlistEntry(**asdict(entry), position=store.position(entry))
