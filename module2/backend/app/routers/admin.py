"""Admin endpoints. Every one requires the ADMIN token."""

from fastapi import APIRouter, Request, status

from ..auth import AdminUser, StoreDep, hash_password
from ..models import AdminRestaurant, RestaurantCreateRequest, RestaurantStatusRequest, RestaurantUpdateRequest
from ..serializers import admin_restaurant

router = APIRouter(prefix="/admin/restaurants", tags=["Admin"])


@router.get("", response_model=list[AdminRestaurant], operation_id="getAdminRestaurants")
def get_admin_restaurants(_: AdminUser, store: StoreDep) -> list[AdminRestaurant]:
    with store.transaction():
        return [admin_restaurant(store, r) for r in store.list_restaurants(active_only=False)]


@router.post(
    "",
    response_model=AdminRestaurant,
    status_code=status.HTTP_201_CREATED,
    operation_id="createRestaurant",
)
def create_restaurant(
    body: RestaurantCreateRequest, _: AdminUser, request: Request, store: StoreDep
) -> AdminRestaurant:
    password_hash = hash_password(body.password, n=request.app.state.settings.scrypt_n)
    with store.transaction():
        return admin_restaurant(store, store.create_restaurant(body, password_hash))


@router.get("/{restaurant_id}", response_model=AdminRestaurant, operation_id="getAdminRestaurant")
def get_admin_restaurant(restaurant_id: int, _: AdminUser, store: StoreDep) -> AdminRestaurant:
    with store.transaction():
        return admin_restaurant(store, store.restaurant(restaurant_id))


@router.put("/{restaurant_id}", response_model=AdminRestaurant, operation_id="updateRestaurant")
def update_restaurant(
    restaurant_id: int, body: RestaurantUpdateRequest, _: AdminUser, request: Request, store: StoreDep
) -> AdminRestaurant:
    # A blank password keeps the current one.
    password_hash = hash_password(body.password, n=request.app.state.settings.scrypt_n) if body.password else None
    with store.transaction():
        return admin_restaurant(store, store.update_restaurant(restaurant_id, body, password_hash))


@router.patch("/{restaurant_id}/status", response_model=AdminRestaurant, operation_id="setRestaurantStatus")
def set_restaurant_status(
    restaurant_id: int, body: RestaurantStatusRequest, _: AdminUser, store: StoreDep
) -> AdminRestaurant:
    with store.transaction():
        return admin_restaurant(store, store.set_restaurant_active(restaurant_id, body.is_active))
