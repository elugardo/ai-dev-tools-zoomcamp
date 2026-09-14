// The data contract between the UI and the backend.
//
// Field names are snake_case on purpose: they mirror the JSON the FastAPI
// backend will return (spec §24–§26), so the HTTP implementation of the
// services layer can pass responses through without renaming anything.
// Timestamps are ISO-8601 strings, exactly as they arrive over the wire.

export type UserRole = 'ADMIN' | 'RESTAURANT'

export type WaitlistStatus = 'WAITING' | 'NOTIFIED' | 'SEATED' | 'CANCELED' | 'NO_SHOW'

export type WaitlistSource = 'ONLINE' | 'STAFF'

export const ACTIVE_STATUSES: readonly WaitlistStatus[] = ['WAITING', 'NOTIFIED']
export const FINAL_STATUSES: readonly WaitlistStatus[] = ['SEATED', 'CANCELED', 'NO_SHOW']

export interface SessionUser {
  id: number
  username: string
  role: UserRole
  restaurant_id: number | null
}

/** Response of POST /api/auth/login. The token is sent back on staff/admin calls. */
export interface LoginResult {
  token: string
  user: SessionUser
}

/** A restaurant as the public sees it. */
export interface PublicRestaurant {
  id: number
  name: string
  address: string
  phone: string
  description: string
  current_wait_minutes: number
  is_active: boolean
  online_waitlist_enabled: boolean
  /** Parties currently WAITING or NOTIFIED. */
  waiting_parties: number
}

/** A restaurant as the admin sees it, including its login. */
export interface AdminRestaurant extends PublicRestaurant {
  no_show_minutes: number
  username: string
  created_at: string
  updated_at: string
}

/** Body of POST/PUT /api/admin/restaurants. An empty password on edit keeps the old one. */
export interface RestaurantInput {
  name: string
  address: string
  phone: string
  description: string
  current_wait_minutes: number
  no_show_minutes: number
  is_active: boolean
  username: string
  password: string
}

/** Body of PATCH /api/restaurant/settings. */
export interface RestaurantSettingsInput {
  current_wait_minutes?: number
  online_waitlist_enabled?: boolean
}

/** Body of POST /api/restaurants/{id}/waitlist and POST /api/restaurant/waitlist. */
export interface JoinWaitlistInput {
  guest_name: string
  mobile_phone: string
  party_size: number
  notes: string
}

/** Response of GET /api/waitlist/{token} — what an eater is allowed to see. */
export interface EaterWaitlistView {
  public_token: string
  restaurant_id: number
  restaurant_name: string
  guest_name: string
  party_size: number
  status: WaitlistStatus
  quoted_wait_minutes: number
  joined_at: string
  estimated_ready_at: string
  notified_at: string | null
  /** 1-based queue position; null once the entry has left the active queue. */
  position: number | null
}

/** A waitlist entry as restaurant staff see it. */
export interface StaffWaitlistEntry {
  id: number
  public_token: string
  guest_name: string
  mobile_phone: string
  party_size: number
  notes: string | null
  source: WaitlistSource
  status: WaitlistStatus
  quoted_wait_minutes: number
  joined_at: string
  estimated_ready_at: string
  notified_at: string | null
  seated_at: string | null
  canceled_at: string | null
  no_show_at: string | null
  position: number | null
}

/** Response of GET /api/restaurant/waitlist. */
export interface RestaurantWaitlist {
  restaurant: AdminRestaurant
  active: StaffWaitlistEntry[]
  history: StaffWaitlistEntry[]
}
