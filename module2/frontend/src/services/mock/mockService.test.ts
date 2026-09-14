// Contract tests for the services layer, run against the mock. They cover the
// backend behaviors listed in spec §33, so the same scenarios can be ported to
// pytest when the FastAPI backend replaces the mock.

import { beforeEach, describe, expect, it } from 'vitest'
import type { JoinWaitlistInput, RestaurantInput } from '../../domain/types'
import { ServiceError } from '../errors'
import type { WaitWiseService } from '../WaitWiseService'
import { emptyDb, seedDb } from './mockDb'
import { createMockService, MOCK_DB_STORAGE_KEY } from './mockService'

const party = (overrides: Partial<JoinWaitlistInput> = {}): JoinWaitlistInput => ({
  guest_name: 'Marcos',
  mobile_phone: '555-010-6677',
  party_size: 4,
  notes: '',
  ...overrides,
})

const newRestaurant = (overrides: Partial<RestaurantInput> = {}): RestaurantInput => ({
  name: 'Harbor Noodle',
  address: '789 Pier Road',
  phone: '(555) 400-1234',
  description: 'Hand-pulled noodles.',
  current_wait_minutes: 15,
  no_show_minutes: 10,
  is_active: true,
  username: 'harbor',
  password: 'secret',
  ...overrides,
})

/** A mock with a hand-cranked clock and an explicit token, starting from the demo seed. */
function setup(initialDb = seedDb) {
  let clock = new Date('2026-09-14T18:00:00.000Z')
  let token: string | null = null
  const service = createMockService({
    storage: null,
    now: () => new Date(clock),
    getToken: () => token,
    initialDb,
  })
  return {
    service,
    advanceMinutes: (minutes: number) => {
      clock = new Date(clock.getTime() + minutes * 60_000)
    },
    now: () => clock,
    signIn: async (username: string) => {
      token = (await service.login(username, 'whatever')).token
    },
  }
}

async function expectServiceError(promise: Promise<unknown>, code: ServiceError['code']) {
  const error = await promise.catch((e: unknown) => e)
  expect(error).toBeInstanceOf(ServiceError)
  expect((error as ServiceError).code).toBe(code)
  return error as ServiceError
}

async function bluebirdId(service: WaitWiseService) {
  return (await service.getRestaurants()).find((r) => r.name === 'Bluebird Cafe')!.id
}

describe('authentication', () => {
  it('accepts any password for a known username, case-insensitively', async () => {
    const { service } = setup()
    const result = await service.login('  BlueBird ', '')
    expect(result.user).toMatchObject({ username: 'bluebird', role: 'RESTAURANT' })
    expect((await service.login('admin', 'x')).user.role).toBe('ADMIN')
  })

  it('rejects an unknown username', async () => {
    await expectServiceError(setup().service.login('nobody', 'x'), 'UNAUTHORIZED')
  })

  it('refuses staff and admin calls without the right role', async () => {
    const { service, signIn } = setup()
    await expectServiceError(service.getRestaurantWaitlist(), 'UNAUTHORIZED')
    await signIn('bluebird')
    await expectServiceError(service.getAdminRestaurants(), 'FORBIDDEN')
    await signIn('admin')
    await expectServiceError(service.getRestaurantWaitlist(), 'FORBIDDEN')
  })
})

describe('public restaurants', () => {
  it('lists the active seeded restaurants with their waits', async () => {
    const restaurants = await setup().service.getRestaurants()
    expect(restaurants.map((r) => [r.name, r.current_wait_minutes])).toEqual([
      ['Bluebird Cafe', 30],
      ['Oak & Ember', 20],
    ])
  })

  it('hides inactive restaurants from the public list', async () => {
    const { service, signIn } = setup()
    const id = await bluebirdId(service)
    await signIn('admin')
    await service.setRestaurantStatus(id, false)
    expect((await service.getRestaurants()).map((r) => r.name)).toEqual(['Oak & Ember'])
  })

  it('returns an empty list when there are no restaurants', async () => {
    expect(await setup(emptyDb).service.getRestaurants()).toEqual([])
  })

  it('reports a missing restaurant as NOT_FOUND', async () => {
    await expectServiceError(setup().service.getRestaurant(999), 'NOT_FOUND')
  })
})

describe('joining the waitlist', () => {
  it('creates a WAITING, ONLINE entry at the back of the queue with a public token', async () => {
    const { service, signIn } = setup()
    const id = await bluebirdId(service)
    const view = await service.joinWaitlist(id, party({ guest_name: '  Marcos  ' }))

    expect(view).toMatchObject({ guest_name: 'Marcos', party_size: 4, status: 'WAITING', position: 5 })
    expect(view.public_token).toBeTruthy()
    expect(await service.getWaitlistEntry(view.public_token)).toEqual(view)

    await signIn('bluebird')
    const { active } = await service.getRestaurantWaitlist()
    expect(active.at(-1)).toMatchObject({ guest_name: 'Marcos', source: 'ONLINE' })
  })

  it('copies the current wait into the quote and computes estimated_ready_at', async () => {
    const { service, now } = setup()
    const view = await service.joinWaitlist(await bluebirdId(service), party())
    expect(view.quoted_wait_minutes).toBe(30)
    expect(view.joined_at).toBe(now().toISOString())
    expect(view.estimated_ready_at).toBe('2026-09-14T18:30:00.000Z')
  })

  it('keeps the original quote when the restaurant later changes its wait', async () => {
    const { service, signIn } = setup()
    const view = await service.joinWaitlist(await bluebirdId(service), party())
    await signIn('bluebird')
    await service.updateRestaurantSettings({ current_wait_minutes: 90 })

    const after = await service.getWaitlistEntry(view.public_token)
    expect(after.quoted_wait_minutes).toBe(30)
    expect(after.estimated_ready_at).toBe(view.estimated_ready_at)
  })

  it.each([0, 21, 3.5])('rejects party size %s', async (party_size) => {
    const { service } = setup()
    const error = await expectServiceError(
      service.joinWaitlist(await bluebirdId(service), party({ party_size })),
      'VALIDATION',
    )
    expect(error.fieldErrors.party_size).toBeDefined()
  })

  it('refuses online joins while the waitlist is paused, but still takes walk-ins', async () => {
    const { service, signIn } = setup()
    const id = await bluebirdId(service)
    await signIn('bluebird')
    await service.updateRestaurantSettings({ online_waitlist_enabled: false })

    await expectServiceError(service.joinWaitlist(id, party()), 'WAITLIST_CLOSED')
    expect((await service.addWalkIn(party())).status).toBe('WAITING')
  })

  it('refuses joins at an inactive restaurant', async () => {
    const { service, signIn } = setup()
    const id = await bluebirdId(service)
    await signIn('admin')
    await service.setRestaurantStatus(id, false)
    await expectServiceError(service.joinWaitlist(id, party()), 'WAITLIST_CLOSED')
  })
})

describe('queue position', () => {
  it('is FIFO by join time and closes up when a party ahead leaves', async () => {
    const { service, advanceMinutes } = setup(emptyDbWithBluebird)
    const first = await service.joinWaitlist(1, party({ guest_name: 'First' }))
    advanceMinutes(1)
    const second = await service.joinWaitlist(1, party({ guest_name: 'Second' }))
    advanceMinutes(1)
    const third = await service.joinWaitlist(1, party({ guest_name: 'Third' }))

    expect((await service.getWaitlistEntry(third.public_token)).position).toBe(3)
    await service.cancelWaitlistEntry(second.public_token)
    expect((await service.getWaitlistEntry(third.public_token)).position).toBe(2)
    expect((await service.getWaitlistEntry(first.public_token)).position).toBe(1)
  })
})

describe('eater leaving the waitlist', () => {
  it('cancels the entry and removes it from the active queue', async () => {
    const { service, signIn } = setup()
    const view = await service.joinWaitlist(await bluebirdId(service), party())
    const canceled = await service.cancelWaitlistEntry(view.public_token)
    expect(canceled).toMatchObject({ status: 'CANCELED', position: null })

    await signIn('bluebird')
    const { active, history } = await service.getRestaurantWaitlist()
    expect(active.some((e) => e.public_token === view.public_token)).toBe(false)
    expect(history.find((e) => e.public_token === view.public_token)?.canceled_at).toBeTruthy()
  })

  it('cannot cancel an entry that is already finished', async () => {
    const { service } = setup()
    const view = await service.joinWaitlist(await bluebirdId(service), party())
    await service.cancelWaitlistEntry(view.public_token)
    await expectServiceError(service.cancelWaitlistEntry(view.public_token), 'ENTRY_CLOSED')
  })

  it('reports an unknown token as NOT_FOUND', async () => {
    await expectServiceError(setup().service.getWaitlistEntry('nope'), 'NOT_FOUND')
  })
})

describe('restaurant operations', () => {
  it('adds a walk-in with source STAFF and the same wait calculation', async () => {
    const { service, signIn } = setup()
    await signIn('bluebird')
    const walkIn = await service.addWalkIn(party({ guest_name: 'Walk In', party_size: 2 }))
    expect(walkIn).toMatchObject({ source: 'STAFF', status: 'WAITING', quoted_wait_minutes: 30 })
    expect(walkIn.estimated_ready_at).toBe('2026-09-14T18:30:00.000Z')
  })

  it('notifies a party and stamps notified_at', async () => {
    const { service, signIn, now } = setup()
    await signIn('bluebird')
    const { active } = await service.getRestaurantWaitlist()
    const waiting = active.find((e) => e.status === 'WAITING')!
    const notified = await service.updateWaitlistEntry(waiting.id, 'NOTIFIED')
    expect(notified).toMatchObject({ status: 'NOTIFIED', notified_at: now().toISOString() })
    expect((await service.getWaitlistEntry(notified.public_token)).status).toBe('NOTIFIED')
  })

  it('seats a party out of queue order', async () => {
    const { service, signIn } = setup()
    await signIn('bluebird')
    const { active } = await service.getRestaurantWaitlist()
    const last = active.at(-1)!
    expect(last.position).toBe(active.length)

    const seated = await service.updateWaitlistEntry(last.id, 'SEATED')
    expect(seated).toMatchObject({ status: 'SEATED', position: null })
    expect(seated.seated_at).toBeTruthy()

    const after = await service.getRestaurantWaitlist()
    expect(after.active.map((e) => e.id)).toEqual(active.slice(0, -1).map((e) => e.id))
    expect(after.history[0].id).toBe(last.id)
  })

  it('rejects a transition out of a final status', async () => {
    const { service, signIn } = setup()
    await signIn('bluebird')
    const { active } = await service.getRestaurantWaitlist()
    await service.updateWaitlistEntry(active[0].id, 'SEATED')
    await expectServiceError(service.updateWaitlistEntry(active[0].id, 'NOTIFIED'), 'ENTRY_CLOSED')
  })

  it('rejects re-notifying a party that is already notified', async () => {
    const { service, signIn } = setup()
    await signIn('bluebird')
    const { active } = await service.getRestaurantWaitlist()
    const notified = active.find((e) => e.status === 'NOTIFIED')!
    await expectServiceError(service.updateWaitlistEntry(notified.id, 'NOTIFIED'), 'VALIDATION')
  })

  it("cannot touch another restaurant's party", async () => {
    const { service, signIn } = setup()
    await signIn('oakember')
    const oakIds = (await service.getRestaurantWaitlist()).active.map((e) => e.id)
    await signIn('bluebird')
    await expectServiceError(service.updateWaitlistEntry(oakIds[0], 'SEATED'), 'NOT_FOUND')
  })

  it('validates the current wait setting', async () => {
    const { service, signIn } = setup()
    await signIn('bluebird')
    await expectServiceError(service.updateRestaurantSettings({ current_wait_minutes: -1 }), 'VALIDATION')
    expect((await service.updateRestaurantSettings({ current_wait_minutes: 45 })).current_wait_minutes).toBe(45)
  })
})

describe('automatic no-show', () => {
  it('moves a notified party to NO_SHOW once no_show_minutes pass', async () => {
    const { service, signIn, advanceMinutes } = setup(emptyDbWithBluebird)
    const view = await service.joinWaitlist(1, party())
    await signIn('bluebird')
    const [entry] = (await service.getRestaurantWaitlist()).active
    await service.updateWaitlistEntry(entry.id, 'NOTIFIED')

    advanceMinutes(9)
    expect((await service.getWaitlistEntry(view.public_token)).status).toBe('NOTIFIED')

    advanceMinutes(1)
    const { active, history } = await service.getRestaurantWaitlist()
    expect(active).toEqual([])
    expect(history[0]).toMatchObject({ status: 'NO_SHOW', no_show_at: '2026-09-14T18:10:00.000Z' })
    expect((await service.getWaitlistEntry(view.public_token)).status).toBe('NO_SHOW')
  })

  it('never auto-expires a party that is only WAITING', async () => {
    const { service, advanceMinutes } = setup(emptyDbWithBluebird)
    const view = await service.joinWaitlist(1, party())
    advanceMinutes(240)
    expect((await service.getWaitlistEntry(view.public_token)).status).toBe('WAITING')
  })
})

describe('admin', () => {
  let ctx: ReturnType<typeof setup>
  beforeEach(async () => {
    ctx = setup()
    await ctx.signIn('admin')
  })

  it('lists every restaurant, including inactive ones, with its username', async () => {
    const id = await bluebirdId(ctx.service)
    await ctx.service.setRestaurantStatus(id, false)
    const all = await ctx.service.getAdminRestaurants()
    expect(all.map((r) => [r.name, r.is_active, r.username])).toEqual([
      ['Bluebird Cafe', false, 'bluebird'],
      ['Oak & Ember', true, 'oakember'],
    ])
  })

  it('creates a restaurant whose login works and which appears publicly', async () => {
    const created = await ctx.service.createRestaurant(newRestaurant({ username: 'Harbor' }))
    expect(created).toMatchObject({ name: 'Harbor Noodle', username: 'harbor', online_waitlist_enabled: true })
    expect((await ctx.service.getRestaurants()).map((r) => r.name)).toContain('Harbor Noodle')
    expect((await ctx.service.login('harbor', 'anything')).user.restaurant_id).toBe(created.id)
  })

  it('edits a restaurant and its login username', async () => {
    const id = await bluebirdId(ctx.service)
    const before = await ctx.service.getAdminRestaurant(id)
    const updated = await ctx.service.updateRestaurant(id, {
      ...newRestaurant({ name: 'Bluebird Bistro', username: 'bistro', password: '' }),
      current_wait_minutes: 5,
    })
    expect(updated).toMatchObject({ name: 'Bluebird Bistro', current_wait_minutes: 5, username: 'bistro' })
    expect((await ctx.service.login('bistro', 'x')).user.restaurant_id).toBe(before.id)
    await expectServiceError(ctx.service.login('bluebird', 'x'), 'UNAUTHORIZED')
  })

  it('rejects a username that another restaurant already uses', async () => {
    const error = await expectServiceError(
      ctx.service.createRestaurant(newRestaurant({ username: 'oakember' })),
      'CONFLICT',
    )
    expect(error.fieldErrors.username).toBeDefined()
  })

  it('rejects invalid restaurant input with field errors', async () => {
    const error = await expectServiceError(ctx.service.createRestaurant(newRestaurant({ name: ' ' })), 'VALIDATION')
    expect(Object.keys(error.fieldErrors)).toEqual(['name'])
  })
})

describe('persistence', () => {
  it('shares one db between instances on the same storage, like two browser tabs', async () => {
    const storage = new MemoryStorage()
    const eaterTab = createMockService({ storage, getToken: () => null })
    let staffToken: string | null = null
    const staffTab = createMockService({ storage, getToken: () => staffToken })

    const id = await bluebirdId(eaterTab)
    const view = await eaterTab.joinWaitlist(id, party())
    staffToken = (await staffTab.login('bluebird', '')).token
    const mine = (await staffTab.getRestaurantWaitlist()).active.find((e) => e.public_token === view.public_token)!
    await staffTab.updateWaitlistEntry(mine.id, 'NOTIFIED')

    expect((await eaterTab.getWaitlistEntry(view.public_token)).status).toBe('NOTIFIED')
    expect(storage.getItem(MOCK_DB_STORAGE_KEY)).toContain(view.public_token)
  })
})

function emptyDbWithBluebird() {
  const db = seedDb(new Date('2026-09-14T18:00:00.000Z'))
  db.entries = []
  return db
}

class MemoryStorage implements Storage {
  private data = new Map<string, string>()
  get length() {
    return this.data.size
  }
  clear() {
    this.data.clear()
  }
  getItem(key: string) {
    return this.data.get(key) ?? null
  }
  key(index: number) {
    return [...this.data.keys()][index] ?? null
  }
  removeItem(key: string) {
    this.data.delete(key)
  }
  setItem(key: string, value: string) {
    this.data.set(key, value)
  }
}
