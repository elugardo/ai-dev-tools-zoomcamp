// The mock backend's "database": plain records shaped like the tables in spec §25,
// plus the demo seed data from §34.

import type { UserRole, WaitlistSource, WaitlistStatus } from '../../domain/types'
import { addMinutes } from '../../domain/waitTime'
import { estimateReadyAt } from '../../domain/waitlist'

export interface UserRecord {
  id: number
  username: string
  /** Plain text: this is an in-browser mock. The real backend stores only a salted hash. */
  password: string
  role: UserRole
  restaurant_id: number | null
  created_at: string
}

export interface RestaurantRecord {
  id: number
  name: string
  address: string
  phone: string
  description: string
  current_wait_minutes: number
  no_show_minutes: number
  is_active: boolean
  online_waitlist_enabled: boolean
  created_at: string
  updated_at: string
}

export interface EntryRecord {
  id: number
  restaurant_id: number
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
  created_at: string
  updated_at: string
}

export interface MockDb {
  users: UserRecord[]
  restaurants: RestaurantRecord[]
  entries: EntryRecord[]
}

/** Every demo login uses this password, matching the backend's seed. */
export const DEMO_PASSWORD = 'password'

export function emptyDb(): MockDb {
  return { users: [], restaurants: [], entries: [] }
}

export function nextId(rows: readonly { id: number }[]): number {
  return rows.reduce((max, row) => Math.max(max, row.id), 0) + 1
}

interface SeedEntry {
  token: string
  guest: string
  phone: string
  size: number
  notes?: string
  source?: WaitlistSource
  quote: number
  joinedMinutesAgo: number
  status?: WaitlistStatus
  /** Minutes after joining that the entry reached its current status. */
  statusAfter?: number
}

/**
 * Demo data with timestamps relative to `now`, so the queue always looks live.
 * Seeded tokens are readable (e.g. /wait/demo-sarah) to make the eater page easy to open.
 */
export function seedDb(now: Date): MockDb {
  const at = (minutesAgo: number) => addMinutes(now.toISOString(), -minutesAgo)
  const db = emptyDb()

  db.users.push({
    id: 1,
    username: 'admin',
    password: DEMO_PASSWORD,
    role: 'ADMIN',
    restaurant_id: null,
    created_at: at(60 * 24 * 30),
  })

  const addRestaurant = (
    restaurant: Omit<RestaurantRecord, 'id' | 'created_at' | 'updated_at'>,
    username: string,
    entries: SeedEntry[],
  ) => {
    const id = nextId(db.restaurants)
    db.restaurants.push({ ...restaurant, id, created_at: at(60 * 24 * 30), updated_at: at(60) })
    db.users.push({
      id: nextId(db.users),
      username,
      password: DEMO_PASSWORD,
      role: 'RESTAURANT',
      restaurant_id: id,
      created_at: at(60 * 24 * 30),
    })

    for (const seed of entries) {
      const joined_at = at(seed.joinedMinutesAgo)
      const status = seed.status ?? 'WAITING'
      const statusAt = seed.statusAfter === undefined ? null : addMinutes(joined_at, seed.statusAfter)
      db.entries.push({
        id: nextId(db.entries),
        restaurant_id: id,
        public_token: seed.token,
        guest_name: seed.guest,
        mobile_phone: seed.phone,
        party_size: seed.size,
        notes: seed.notes ?? null,
        source: seed.source ?? 'ONLINE',
        status,
        quoted_wait_minutes: seed.quote,
        joined_at,
        estimated_ready_at: estimateReadyAt(joined_at, seed.quote),
        // A seated or no-show party was notified first, a few minutes before.
        notified_at:
          status === 'NOTIFIED' ? statusAt : status === 'SEATED' || status === 'NO_SHOW' ? addMinutes(statusAt!, -4) : null,
        seated_at: status === 'SEATED' ? statusAt : null,
        canceled_at: status === 'CANCELED' ? statusAt : null,
        no_show_at: status === 'NO_SHOW' ? statusAt : null,
        created_at: joined_at,
        updated_at: statusAt ?? joined_at,
      })
    }
  }

  addRestaurant(
    {
      name: 'Bluebird Cafe',
      address: '123 Main Street',
      phone: '(555) 201-4455',
      description: 'All-day breakfast, strong coffee, and a sunny patio.',
      current_wait_minutes: 30,
      no_show_minutes: 10,
      is_active: true,
      online_waitlist_enabled: true,
    },
    'bluebird',
    [
      { token: 'demo-sarah', guest: 'Sarah', phone: '555-010-2233', size: 6, quote: 30, joinedMinutesAgo: 32 },
      { token: 'demo-james', guest: 'James', phone: '555-010-4455', size: 4, quote: 30, joinedMinutesAgo: 24, notes: 'High chair please' },
      { token: 'demo-marcos', guest: 'Marcos', phone: '555-010-6677', size: 2, quote: 25, joinedMinutesAgo: 18, source: 'STAFF' },
      { token: 'demo-lena', guest: 'Lena', phone: '555-010-8899', size: 3, quote: 30, joinedMinutesAgo: 35, status: 'NOTIFIED', statusAfter: 32 },
      { token: 'demo-chen', guest: 'Chen', phone: '555-010-1100', size: 2, quote: 20, joinedMinutesAgo: 70, status: 'SEATED', statusAfter: 25 },
      { token: 'demo-alex', guest: 'Alex', phone: '555-010-1212', size: 5, quote: 30, joinedMinutesAgo: 50, status: 'CANCELED', statusAfter: 12 },
    ],
  )

  addRestaurant(
    {
      name: 'Oak & Ember',
      address: '456 Market Street',
      phone: '(555) 309-7788',
      description: 'Wood-fired grill and seasonal small plates.',
      current_wait_minutes: 20,
      no_show_minutes: 10,
      is_active: true,
      online_waitlist_enabled: true,
    },
    'oakember',
    [
      { token: 'demo-jordan', guest: 'Jordan', phone: '555-020-3344', size: 5, quote: 20, joinedMinutesAgo: 12 },
      { token: 'demo-taylor', guest: 'Taylor', phone: '555-020-5566', size: 2, quote: 20, joinedMinutesAgo: 5 },
    ],
  )

  return db
}
