// In-browser mock of the WaitWise backend. It enforces the same rules the FastAPI
// backend will (validation, status transitions, auto no-show, role checks) so the
// UI can be built and demoed with no server running.
//
// State lives in localStorage by default, which means an eater tab and a
// restaurant tab in the same browser share one waitlist and see each other's
// changes through polling. Tests pass `storage: null` for a private in-memory db.

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
  UserRole,
  WaitlistSource,
  WaitlistStatus,
} from '../../domain/types'
import {
  hasErrors,
  normalizeJoinInput,
  normalizeRestaurantInput,
  validateJoinInput,
  validateRestaurantInput,
  validateWaitMinutes,
} from '../../domain/validation'
import {
  activeQueue,
  canTransition,
  estimateReadyAt,
  isActive,
  isOverdueNoShow,
  noShowDeadline,
  queuePosition,
  timestampFieldFor,
  todaysHistory,
} from '../../domain/waitlist'
import { ServiceError } from '../errors'
import type { WaitWiseService } from '../WaitWiseService'
import {
  nextId,
  seedDb,
  type EntryRecord,
  type MockDb,
  type RestaurantRecord,
  type UserRecord,
} from './mockDb'

export const MOCK_DB_STORAGE_KEY = 'waitwise.mockdb.v1'

export interface MockServiceOptions {
  /** Returns the current session token; the mock resolves it to a user. */
  getToken: () => string | null
  /** Clock override for tests. */
  now?: () => Date
  /** Artificial delay per call, so loading states are visible in the browser. */
  latencyMs?: number
  /** Where to persist the db. `null` keeps it in memory only. */
  storage?: Storage | null
  /** Starting data when nothing is stored yet. Defaults to the demo seed. */
  initialDb?: (now: Date) => MockDb
}

const TOKEN_PREFIX = 'mock-token-'

function generatePublicToken(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID()
  return Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2)
}

function validationError(fieldErrors: Record<string, string | undefined>): ServiceError {
  const clean = Object.fromEntries(
    Object.entries(fieldErrors).filter((pair): pair is [string, string] => pair[1] !== undefined),
  )
  return new ServiceError('VALIDATION', 'Please fix the highlighted fields.', clean)
}

const RESTAURANT_NOT_FOUND = "We couldn't find that restaurant."
const ENTRY_NOT_FOUND = "We couldn't find that waitlist spot. Check your link and try again."

export function createMockService(options: MockServiceOptions): WaitWiseService {
  const now = options.now ?? (() => new Date())
  const latencyMs = options.latencyMs ?? 0
  const storage = options.storage === undefined ? globalThis.localStorage : options.storage
  const initialDb = options.initialDb ?? seedDb
  let memoryDb: MockDb | null = null

  // ---- persistence -------------------------------------------------------

  function readDb(): MockDb {
    if (storage) {
      const raw = storage.getItem(MOCK_DB_STORAGE_KEY)
      if (raw) {
        try {
          return JSON.parse(raw) as MockDb
        } catch {
          // Corrupt demo data: fall through and reseed.
        }
      }
    } else if (memoryDb) {
      return structuredClone(memoryDb)
    }
    const fresh = initialDb(now())
    writeDb(fresh)
    return structuredClone(fresh)
  }

  function writeDb(db: MockDb): void {
    if (storage) storage.setItem(MOCK_DB_STORAGE_KEY, JSON.stringify(db))
    else memoryDb = structuredClone(db)
  }

  /**
   * Loads the db with overdue notified parties already moved to NO_SHOW (§14).
   * There is no background worker; the sweep runs on every call instead.
   */
  function loadDb(): MockDb {
    const db = readDb()
    const current = now()
    let changed = false
    for (const entry of db.entries) {
      const restaurant = db.restaurants.find((r) => r.id === entry.restaurant_id)
      if (restaurant && isOverdueNoShow(entry, restaurant.no_show_minutes, current)) {
        const deadline = noShowDeadline(entry, restaurant.no_show_minutes)!
        entry.status = 'NO_SHOW'
        entry.no_show_at = deadline
        entry.updated_at = deadline
        changed = true
      }
    }
    if (changed) writeDb(db)
    return db
  }

  /** Every call is async and optionally slow, like a network round trip. */
  async function call<T>(work: () => T): Promise<T> {
    if (latencyMs > 0) await new Promise((resolve) => setTimeout(resolve, latencyMs))
    else await Promise.resolve()
    return structuredClone(work())
  }

  // ---- lookups and shaping -----------------------------------------------

  function findRestaurant(db: MockDb, id: number): RestaurantRecord {
    const restaurant = db.restaurants.find((r) => r.id === id)
    if (!restaurant) throw new ServiceError('NOT_FOUND', RESTAURANT_NOT_FOUND)
    return restaurant
  }

  function findEntryByToken(db: MockDb, token: string): EntryRecord {
    const entry = db.entries.find((e) => e.public_token === token)
    if (!entry) throw new ServiceError('NOT_FOUND', ENTRY_NOT_FOUND)
    return entry
  }

  function requireUser(db: MockDb, role: UserRole): UserRecord {
    const token = options.getToken()
    const id = token?.startsWith(TOKEN_PREFIX) ? Number(token.slice(TOKEN_PREFIX.length)) : NaN
    const user = db.users.find((u) => u.id === id)
    if (!user) throw new ServiceError('UNAUTHORIZED', 'Your session has expired. Please log in again.')
    if (user.role !== role) throw new ServiceError('FORBIDDEN', "You don't have access to that page.")
    return user
  }

  function requireStaffRestaurant(db: MockDb): RestaurantRecord {
    const user = requireUser(db, 'RESTAURANT')
    return findRestaurant(db, user.restaurant_id!)
  }

  function entriesFor(db: MockDb, restaurantId: number): EntryRecord[] {
    return db.entries.filter((e) => e.restaurant_id === restaurantId)
  }

  function toPublicRestaurant(db: MockDb, r: RestaurantRecord): PublicRestaurant {
    return {
      id: r.id,
      name: r.name,
      address: r.address,
      phone: r.phone,
      description: r.description,
      current_wait_minutes: r.current_wait_minutes,
      is_active: r.is_active,
      online_waitlist_enabled: r.online_waitlist_enabled,
      waiting_parties: activeQueue(entriesFor(db, r.id)).length,
    }
  }

  function toAdminRestaurant(db: MockDb, r: RestaurantRecord): AdminRestaurant {
    const login = db.users.find((u) => u.role === 'RESTAURANT' && u.restaurant_id === r.id)
    return {
      ...toPublicRestaurant(db, r),
      no_show_minutes: r.no_show_minutes,
      username: login?.username ?? '',
      created_at: r.created_at,
      updated_at: r.updated_at,
    }
  }

  function toEaterView(db: MockDb, entry: EntryRecord): EaterWaitlistView {
    return {
      public_token: entry.public_token,
      restaurant_id: entry.restaurant_id,
      restaurant_name: findRestaurant(db, entry.restaurant_id).name,
      guest_name: entry.guest_name,
      party_size: entry.party_size,
      status: entry.status,
      quoted_wait_minutes: entry.quoted_wait_minutes,
      joined_at: entry.joined_at,
      estimated_ready_at: entry.estimated_ready_at,
      notified_at: entry.notified_at,
      position: queuePosition(entriesFor(db, entry.restaurant_id), entry.id),
    }
  }

  function toStaffEntry(db: MockDb, entry: EntryRecord): StaffWaitlistEntry {
    // Strip the db-only columns; keep everything staff see.
    const { restaurant_id, created_at, updated_at, ...visible } = entry
    return { ...visible, position: queuePosition(entriesFor(db, restaurant_id), entry.id) }
  }

  function createEntry(
    db: MockDb,
    restaurant: RestaurantRecord,
    raw: JoinWaitlistInput,
    source: WaitlistSource,
  ): EntryRecord {
    const errors = validateJoinInput(raw)
    if (hasErrors(errors)) throw validationError(errors)
    const input = normalizeJoinInput(raw)
    const joined_at = now().toISOString()
    const entry: EntryRecord = {
      id: nextId(db.entries),
      restaurant_id: restaurant.id,
      public_token: generatePublicToken(),
      guest_name: input.guest_name,
      mobile_phone: input.mobile_phone,
      party_size: input.party_size,
      notes: input.notes || null,
      source,
      status: 'WAITING',
      quoted_wait_minutes: restaurant.current_wait_minutes,
      joined_at,
      estimated_ready_at: estimateReadyAt(joined_at, restaurant.current_wait_minutes),
      notified_at: null,
      seated_at: null,
      canceled_at: null,
      no_show_at: null,
      created_at: joined_at,
      updated_at: joined_at,
    }
    db.entries.push(entry)
    return entry
  }

  function applyStatus(entry: EntryRecord, status: WaitlistStatus): void {
    if (!isActive(entry.status)) {
      throw new ServiceError('ENTRY_CLOSED', 'This party has already been seated, canceled, or marked as a no-show.')
    }
    if (!canTransition(entry.status, status)) {
      throw new ServiceError('VALIDATION', "That status change isn't allowed.")
    }
    const stamp = now().toISOString()
    entry.status = status
    const field = timestampFieldFor(status)
    if (field) entry[field] = stamp
    entry.updated_at = stamp
  }

  /** Checks input and username uniqueness; returns the normalized input. */
  function checkRestaurantInput(
    db: MockDb,
    raw: RestaurantInput,
    existingLogin: UserRecord | null,
  ): RestaurantInput {
    const errors = validateRestaurantInput(raw)
    if (hasErrors(errors)) throw validationError(errors)
    const input = normalizeRestaurantInput(raw)
    const taken = db.users.some((u) => u.username === input.username && u.id !== existingLogin?.id)
    if (taken) {
      throw new ServiceError('CONFLICT', 'That username is already taken.', {
        username: 'That username is already taken.',
      })
    }
    return input
  }

  // ---- the service -------------------------------------------------------

  return {
    login: (username, _password) =>
      call((): LoginResult => {
        // Password contents are ignored by design (spec §5).
        const db = loadDb()
        const user = db.users.find((u) => u.username === username.trim().toLowerCase())
        if (!user) throw new ServiceError('UNAUTHORIZED', "We don't recognize that username.")
        return {
          token: `${TOKEN_PREFIX}${user.id}`,
          user: { id: user.id, username: user.username, role: user.role, restaurant_id: user.restaurant_id },
        }
      }),

    getRestaurants: () =>
      call(() => {
        const db = loadDb()
        return db.restaurants
          .filter((r) => r.is_active)
          .sort((a, b) => a.name.localeCompare(b.name))
          .map((r) => toPublicRestaurant(db, r))
      }),

    getRestaurant: (restaurantId) =>
      call(() => {
        const db = loadDb()
        return toPublicRestaurant(db, findRestaurant(db, restaurantId))
      }),

    joinWaitlist: (restaurantId, input) =>
      call(() => {
        const db = loadDb()
        const restaurant = findRestaurant(db, restaurantId)
        if (!restaurant.is_active || !restaurant.online_waitlist_enabled) {
          throw new ServiceError(
            'WAITLIST_CLOSED',
            `${restaurant.name} isn't accepting online waitlist entries right now.`,
          )
        }
        const entry = createEntry(db, restaurant, input, 'ONLINE')
        writeDb(db)
        return toEaterView(db, entry)
      }),

    getWaitlistEntry: (token) =>
      call(() => {
        const db = loadDb()
        return toEaterView(db, findEntryByToken(db, token))
      }),

    cancelWaitlistEntry: (token) =>
      call(() => {
        const db = loadDb()
        const entry = findEntryByToken(db, token)
        applyStatus(entry, 'CANCELED')
        writeDb(db)
        return toEaterView(db, entry)
      }),

    getRestaurantWaitlist: () =>
      call((): RestaurantWaitlist => {
        const db = loadDb()
        const restaurant = requireStaffRestaurant(db)
        const entries = entriesFor(db, restaurant.id)
        return {
          restaurant: toAdminRestaurant(db, restaurant),
          active: activeQueue(entries).map((e) => toStaffEntry(db, e)),
          history: todaysHistory(entries, now()).map((e) => toStaffEntry(db, e)),
        }
      }),

    addWalkIn: (input) =>
      call(() => {
        const db = loadDb()
        const restaurant = requireStaffRestaurant(db)
        const entry = createEntry(db, restaurant, input, 'STAFF')
        writeDb(db)
        return toStaffEntry(db, entry)
      }),

    updateWaitlistEntry: (entryId, status) =>
      call(() => {
        const db = loadDb()
        const restaurant = requireStaffRestaurant(db)
        const entry = entriesFor(db, restaurant.id).find((e) => e.id === entryId)
        if (!entry) throw new ServiceError('NOT_FOUND', "We couldn't find that party.")
        applyStatus(entry, status)
        writeDb(db)
        return toStaffEntry(db, entry)
      }),

    updateRestaurantSettings: (input: RestaurantSettingsInput) =>
      call(() => {
        const db = loadDb()
        const restaurant = requireStaffRestaurant(db)
        if (input.current_wait_minutes !== undefined) {
          const message = validateWaitMinutes(input.current_wait_minutes)
          if (message) throw validationError({ current_wait_minutes: message })
          restaurant.current_wait_minutes = input.current_wait_minutes
        }
        if (input.online_waitlist_enabled !== undefined) {
          restaurant.online_waitlist_enabled = input.online_waitlist_enabled
        }
        restaurant.updated_at = now().toISOString()
        writeDb(db)
        return toAdminRestaurant(db, restaurant)
      }),

    getAdminRestaurants: () =>
      call(() => {
        const db = loadDb()
        requireUser(db, 'ADMIN')
        return [...db.restaurants]
          .sort((a, b) => a.name.localeCompare(b.name))
          .map((r) => toAdminRestaurant(db, r))
      }),

    createRestaurant: (raw) =>
      call(() => {
        const db = loadDb()
        requireUser(db, 'ADMIN')
        const input = checkRestaurantInput(db, raw, null)
        const stamp = now().toISOString()
        const restaurant: RestaurantRecord = {
          id: nextId(db.restaurants),
          name: input.name,
          address: input.address,
          phone: input.phone,
          description: input.description,
          current_wait_minutes: input.current_wait_minutes,
          no_show_minutes: input.no_show_minutes,
          is_active: input.is_active,
          online_waitlist_enabled: true,
          created_at: stamp,
          updated_at: stamp,
        }
        db.restaurants.push(restaurant)
        db.users.push({
          id: nextId(db.users),
          username: input.username,
          password: input.password,
          role: 'RESTAURANT',
          restaurant_id: restaurant.id,
          created_at: stamp,
        })
        writeDb(db)
        return toAdminRestaurant(db, restaurant)
      }),

    getAdminRestaurant: (restaurantId) =>
      call(() => {
        const db = loadDb()
        requireUser(db, 'ADMIN')
        return toAdminRestaurant(db, findRestaurant(db, restaurantId))
      }),

    updateRestaurant: (restaurantId, raw) =>
      call(() => {
        const db = loadDb()
        requireUser(db, 'ADMIN')
        const restaurant = findRestaurant(db, restaurantId)
        const login = db.users.find((u) => u.role === 'RESTAURANT' && u.restaurant_id === restaurant.id) ?? null
        const input = checkRestaurantInput(db, raw, login)
        const stamp = now().toISOString()
        Object.assign(restaurant, {
          name: input.name,
          address: input.address,
          phone: input.phone,
          description: input.description,
          current_wait_minutes: input.current_wait_minutes,
          no_show_minutes: input.no_show_minutes,
          is_active: input.is_active,
          updated_at: stamp,
        })
        if (login) {
          login.username = input.username
          // A blank password on edit means "keep the current one".
          if (input.password) login.password = input.password
        } else {
          db.users.push({
            id: nextId(db.users),
            username: input.username,
            password: input.password,
            role: 'RESTAURANT',
            restaurant_id: restaurant.id,
            created_at: stamp,
          })
        }
        writeDb(db)
        return toAdminRestaurant(db, restaurant)
      }),

    setRestaurantStatus: (restaurantId, isActiveNow) =>
      call(() => {
        const db = loadDb()
        requireUser(db, 'ADMIN')
        const restaurant = findRestaurant(db, restaurantId)
        restaurant.is_active = isActiveNow
        restaurant.updated_at = now().toISOString()
        writeDb(db)
        return toAdminRestaurant(db, restaurant)
      }),
  }
}
