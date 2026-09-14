"""Public endpoints: browsing restaurants and the eater's own waitlist entry.
No token needed; an entry is addressed by its unguessable public token.

Each request runs in one database transaction (see auth.SessionDep)."""

from fastapi import APIRouter, status

from ..auth import StoreDep
from ..models import EaterWaitlistView, JoinWaitlistRequest, PublicRestaurant, WaitlistSource, WaitlistStatus
from ..serializers import eater_view, public_restaurant

router = APIRouter()


@router.get(
    "/restaurants",
    response_model=list[PublicRestaurant],
    tags=["Public restaurants"],
    operation_id="getRestaurants",
)
def get_restaurants(store: StoreDep) -> list[PublicRestaurant]:
    return [public_restaurant(store, r) for r in store.list_restaurants(active_only=True)]


@router.get(
    "/restaurants/{restaurant_id}",
    response_model=PublicRestaurant,
    tags=["Public restaurants"],
    operation_id="getRestaurant",
)
def get_restaurant(restaurant_id: int, store: StoreDep) -> PublicRestaurant:
    return public_restaurant(store, store.restaurant(restaurant_id))


@router.post(
    "/restaurants/{restaurant_id}/waitlist",
    response_model=EaterWaitlistView,
    status_code=status.HTTP_201_CREATED,
    tags=["Eater waitlist"],
    operation_id="joinWaitlist",
)
def join_waitlist(restaurant_id: int, body: JoinWaitlistRequest, store: StoreDep) -> EaterWaitlistView:
    return eater_view(store, store.add_entry(restaurant_id, body, WaitlistSource.ONLINE))


@router.get(
    "/waitlist/{token}",
    response_model=EaterWaitlistView,
    tags=["Eater waitlist"],
    operation_id="getWaitlistEntry",
)
def get_waitlist_entry(token: str, store: StoreDep) -> EaterWaitlistView:
    return eater_view(store, store.entry_by_token(token))


@router.delete(
    "/waitlist/{token}",
    response_model=EaterWaitlistView,
    tags=["Eater waitlist"],
    operation_id="cancelWaitlistEntry",
)
def cancel_waitlist_entry(token: str, store: StoreDep) -> EaterWaitlistView:
    entry = store.change_status(store.entry_by_token(token), WaitlistStatus.CANCELED)
    return eater_view(store, entry)
