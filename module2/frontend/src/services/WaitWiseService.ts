// The services layer: the only way the UI talks to a backend.
//
// Every method maps to one endpoint in spec §24 (noted beside it). Components get
// an implementation from `useServices()` and never call fetch, import the mock, or
// touch another data source. Today the only implementation is the in-browser mock
// (./mock/mockService.ts); in Phase 3 an HTTP implementation of this same
// interface replaces it without any component changing.
//
// Staff and admin methods take no user argument: the implementation attaches the
// session token itself, the way an HTTP client sends an Authorization header.

import type {
  AdminRestaurant,
  EaterWaitlistView,
  JoinWaitlistInput,
  LoginResult,
  PublicRestaurant,
  RestaurantInput,
  RestaurantSettingsInput,
  RestaurantWaitlist,
  StaffWaitlistEntry,
  WaitlistStatus,
} from '../domain/types'

export interface WaitWiseService {
  // Authentication
  /** POST /api/auth/login */
  login(username: string, password: string): Promise<LoginResult>

  // Public restaurants
  /** GET /api/restaurants — active restaurants only. */
  getRestaurants(): Promise<PublicRestaurant[]>
  /** GET /api/restaurants/{restaurant_id} */
  getRestaurant(restaurantId: number): Promise<PublicRestaurant>

  // Eater waitlist
  /** POST /api/restaurants/{restaurant_id}/waitlist */
  joinWaitlist(restaurantId: number, input: JoinWaitlistInput): Promise<EaterWaitlistView>
  /** GET /api/waitlist/{token} */
  getWaitlistEntry(token: string): Promise<EaterWaitlistView>
  /** DELETE /api/waitlist/{token} */
  cancelWaitlistEntry(token: string): Promise<EaterWaitlistView>

  // Restaurant operations (RESTAURANT role)
  /** GET /api/restaurant/waitlist */
  getRestaurantWaitlist(): Promise<RestaurantWaitlist>
  /** POST /api/restaurant/waitlist */
  addWalkIn(input: JoinWaitlistInput): Promise<StaffWaitlistEntry>
  /** PATCH /api/restaurant/waitlist/{entry_id} */
  updateWaitlistEntry(entryId: number, status: WaitlistStatus): Promise<StaffWaitlistEntry>
  /** PATCH /api/restaurant/settings */
  updateRestaurantSettings(input: RestaurantSettingsInput): Promise<AdminRestaurant>

  // Admin (ADMIN role)
  /** GET /api/admin/restaurants */
  getAdminRestaurants(): Promise<AdminRestaurant[]>
  /** POST /api/admin/restaurants */
  createRestaurant(input: RestaurantInput): Promise<AdminRestaurant>
  /** GET /api/admin/restaurants/{restaurant_id} */
  getAdminRestaurant(restaurantId: number): Promise<AdminRestaurant>
  /** PUT /api/admin/restaurants/{restaurant_id} */
  updateRestaurant(restaurantId: number, input: RestaurantInput): Promise<AdminRestaurant>
  /** PATCH /api/admin/restaurants/{restaurant_id}/status */
  setRestaurantStatus(restaurantId: number, isActive: boolean): Promise<AdminRestaurant>
}
