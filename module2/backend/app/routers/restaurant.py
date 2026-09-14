"""Restaurant operations. Every endpoint requires a RESTAURANT token and acts only
on that user's own restaurant, which comes from the token, never the URL."""

from fastapi import APIRouter, status

from ..auth import StaffRestaurant, StoreDep
from ..models import (
    AdminRestaurant,
    JoinWaitlistRequest,
    RestaurantSettingsRequest,
    RestaurantWaitlist,
    StaffWaitlistEntry,
    UpdateWaitlistEntryRequest,
    WaitlistSource,
)
from ..serializers import admin_restaurant, staff_entry

router = APIRouter(prefix="/restaurant", tags=["Restaurant operations"])


@router.get("/waitlist", response_model=RestaurantWaitlist, operation_id="getRestaurantWaitlist")
def get_restaurant_waitlist(restaurant: StaffRestaurant, store: StoreDep) -> RestaurantWaitlist:
    with store.transaction():
        return RestaurantWaitlist(
            restaurant=admin_restaurant(store, restaurant),
            active=[staff_entry(store, e) for e in store.active_queue(restaurant.id)],
            history=[staff_entry(store, e) for e in store.history(restaurant.id)],
        )


@router.post(
    "/waitlist",
    response_model=StaffWaitlistEntry,
    status_code=status.HTTP_201_CREATED,
    operation_id="addWalkIn",
)
def add_walk_in(body: JoinWaitlistRequest, restaurant: StaffRestaurant, store: StoreDep) -> StaffWaitlistEntry:
    with store.transaction():
        # Walk-ins are allowed even while online joining is paused.
        return staff_entry(store, store.add_entry(restaurant.id, body, WaitlistSource.STAFF))


@router.patch("/waitlist/{entry_id}", response_model=StaffWaitlistEntry, operation_id="updateWaitlistEntry")
def update_waitlist_entry(
    entry_id: int, body: UpdateWaitlistEntryRequest, restaurant: StaffRestaurant, store: StoreDep
) -> StaffWaitlistEntry:
    with store.transaction():
        entry = store.change_status(store.restaurant_entry(restaurant.id, entry_id), body.status)
        return staff_entry(store, entry)


@router.patch("/settings", response_model=AdminRestaurant, operation_id="updateRestaurantSettings")
def update_restaurant_settings(
    body: RestaurantSettingsRequest, restaurant: StaffRestaurant, store: StoreDep
) -> AdminRestaurant:
    with store.transaction():
        updated = store.update_settings(
            restaurant.id,
            current_wait_minutes=body.current_wait_minutes,
            online_waitlist_enabled=body.online_waitlist_enabled,
        )
        return admin_restaurant(store, updated)

