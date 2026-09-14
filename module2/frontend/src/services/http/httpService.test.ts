// @vitest-environment node
//
// The HTTP implementation against a fake fetch: every service method sends the
// method, path, body and auth header that openapi.yaml specifies, and every
// kind of failure becomes a ServiceError a person can read.

import { readFileSync } from 'node:fs'
import { describe, expect, it, vi } from 'vitest'
import type { JoinWaitlistInput, RestaurantInput } from '../../domain/types'
import { ServiceError } from '../errors'
import type { WaitWiseService } from '../WaitWiseService'
import { createHttpService, UNAVAILABLE_MESSAGE } from './httpService'

const BASE = 'http://localhost:9127/api'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function setup({ token = 'tok-123' as string | null, respond = () => jsonResponse({ ok: true }) } = {}) {
  let currentToken = token
  const fetchImpl = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => respond())
  const service = createHttpService({ baseUrl: BASE, getToken: () => currentToken, fetchImpl })
  return {
    service,
    fetchImpl,
    setToken: (next: string | null) => {
      currentToken = next
    },
    lastCall: () => {
      const [url, init] = fetchImpl.mock.calls.at(-1)!
      const headers = (init?.headers ?? {}) as Record<string, string>
      return {
        url: String(url),
        method: init?.method,
        headers,
        body: init?.body === undefined ? undefined : JSON.parse(String(init.body)),
      }
    },
  }
}

const party: JoinWaitlistInput = { guest_name: 'Marcos', mobile_phone: '555-010-6677', party_size: 4, notes: '' }
const restaurant: RestaurantInput = {
  name: 'Harbor Noodle',
  address: '789 Pier Road',
  phone: '(555) 400-1234',
  description: '',
  current_wait_minutes: 30,
  no_show_minutes: 10,
  is_active: true,
  username: 'harbor',
  password: 'noodles-123',
}

type Case = {
  name: keyof WaitWiseService
  call: (s: WaitWiseService) => Promise<unknown>
  method: string
  path: string
  /** The path as written in openapi.yaml and on the WaitWiseService method. */
  template: string
  body?: unknown
  auth: boolean
}

// One row per operation in openapi.yaml.
const CASES: Case[] = [
  { name: 'login', call: (s) => s.login('bluebird', 'password'), method: 'POST', path: '/auth/login', template: '/auth/login', body: { username: 'bluebird', password: 'password' }, auth: false },
  { name: 'getRestaurants', call: (s) => s.getRestaurants(), method: 'GET', path: '/restaurants', template: '/restaurants', auth: false },
  { name: 'getRestaurant', call: (s) => s.getRestaurant(7), method: 'GET', path: '/restaurants/7', template: '/restaurants/{restaurant_id}', auth: false },
  { name: 'joinWaitlist', call: (s) => s.joinWaitlist(7, party), method: 'POST', path: '/restaurants/7/waitlist', template: '/restaurants/{restaurant_id}/waitlist', body: party, auth: false },
  { name: 'getWaitlistEntry', call: (s) => s.getWaitlistEntry('abc'), method: 'GET', path: '/waitlist/abc', template: '/waitlist/{token}', auth: false },
  { name: 'cancelWaitlistEntry', call: (s) => s.cancelWaitlistEntry('abc'), method: 'DELETE', path: '/waitlist/abc', template: '/waitlist/{token}', auth: false },
  { name: 'getRestaurantWaitlist', call: (s) => s.getRestaurantWaitlist(), method: 'GET', path: '/restaurant/waitlist', template: '/restaurant/waitlist', auth: true },
  { name: 'addWalkIn', call: (s) => s.addWalkIn(party), method: 'POST', path: '/restaurant/waitlist', template: '/restaurant/waitlist', body: party, auth: true },
  { name: 'updateWaitlistEntry', call: (s) => s.updateWaitlistEntry(12, 'SEATED'), method: 'PATCH', path: '/restaurant/waitlist/12', template: '/restaurant/waitlist/{entry_id}', body: { status: 'SEATED' }, auth: true },
  { name: 'updateRestaurantSettings', call: (s) => s.updateRestaurantSettings({ current_wait_minutes: 45 }), method: 'PATCH', path: '/restaurant/settings', template: '/restaurant/settings', body: { current_wait_minutes: 45 }, auth: true },
  { name: 'getAdminRestaurants', call: (s) => s.getAdminRestaurants(), method: 'GET', path: '/admin/restaurants', template: '/admin/restaurants', auth: true },
  { name: 'createRestaurant', call: (s) => s.createRestaurant(restaurant), method: 'POST', path: '/admin/restaurants', template: '/admin/restaurants', body: restaurant, auth: true },
  { name: 'getAdminRestaurant', call: (s) => s.getAdminRestaurant(3), method: 'GET', path: '/admin/restaurants/3', template: '/admin/restaurants/{restaurant_id}', auth: true },
  { name: 'updateRestaurant', call: (s) => s.updateRestaurant(3, restaurant), method: 'PUT', path: '/admin/restaurants/3', template: '/admin/restaurants/{restaurant_id}', body: restaurant, auth: true },
  { name: 'setRestaurantStatus', call: (s) => s.setRestaurantStatus(3, false), method: 'PATCH', path: '/admin/restaurants/3/status', template: '/admin/restaurants/{restaurant_id}/status', body: { is_active: false }, auth: true },
]

describe('requests', () => {
  it('covers every method on the service interface', () => {
    const { service } = setup()
    expect(CASES.map((c) => c.name).sort()).toEqual(Object.keys(service).sort())
  })

  it('matches the endpoint annotated on each WaitWiseService method', () => {
    // WaitWiseService.ts documents each method as `/** METHOD /api/path ... */`,
    // the same operations module2/openapi.yaml lists; the backend's contract test
    // checks those annotations against the spec.
    const source = readFileSync(new URL('../WaitWiseService.ts', import.meta.url), 'utf8')
    const annotated = new Map(
      [...source.matchAll(/\/\*\*\s*(GET|POST|PUT|PATCH|DELETE) \/api(\S+)[^*]*\*\/\s*(\w+)\(/g)].map((m) => [m[3], `${m[1]} ${m[2]}`]),
    )
    expect(annotated.size).toBe(CASES.length)
    for (const c of CASES) expect(`${c.method} ${c.template}`, c.name).toBe(annotated.get(c.name))
  })

  it.each(CASES)('$name sends $method $path', async ({ call, method, path, body, auth }) => {
    const { service, lastCall } = setup()
    await call(service)
    const sent = lastCall()

    expect(sent.method).toBe(method)
    expect(sent.url).toBe(`${BASE}${path}`)
    expect(sent.body).toEqual(body)
    expect(sent.headers.Accept).toBe('application/json')
    expect(sent.headers['Content-Type']).toBe(body === undefined ? undefined : 'application/json')
    // Protected endpoints carry the bearer token; public ones never leak it.
    expect(sent.headers.Authorization).toBe(auth ? 'Bearer tok-123' : undefined)
  })

  it('returns the parsed JSON body', async () => {
    const restaurants = [{ id: 1, name: 'Bluebird Cafe' }]
    const { service } = setup({ respond: () => jsonResponse(restaurants) })
    await expect(service.getRestaurants()).resolves.toEqual(restaurants)
  })

  it('reads the token fresh on every call, so login and logout take effect immediately', async () => {
    const { service, lastCall, setToken } = setup({ token: null })
    await service.getRestaurantWaitlist()
    expect(lastCall().headers.Authorization).toBeUndefined()

    setToken('after-login')
    await service.getRestaurantWaitlist()
    expect(lastCall().headers.Authorization).toBe('Bearer after-login')
  })

  it('URL-encodes the waitlist token', async () => {
    const { service, lastCall } = setup()
    await service.getWaitlistEntry('a/b?c')
    expect(lastCall().url).toBe(`${BASE}/waitlist/a%2Fb%3Fc`)
  })

  it('tolerates a trailing slash on the base URL', async () => {
    const fetchImpl = vi.fn(async () => jsonResponse([]))
    await createHttpService({ baseUrl: `${BASE}/`, getToken: () => null, fetchImpl }).getRestaurants()
    expect(String((fetchImpl.mock.calls[0] as unknown[])[0])).toBe(`${BASE}/restaurants`)
  })
})

async function caught(promise: Promise<unknown>): Promise<ServiceError> {
  const error = await promise.then(
    () => {
      throw new Error('expected the call to fail')
    },
    (e: unknown) => e,
  )
  expect(error).toBeInstanceOf(ServiceError)
  return error as ServiceError
}

describe('errors', () => {
  it("passes the server's Error body through, including field errors", async () => {
    const { service } = setup({
      respond: () =>
        jsonResponse(
          {
            code: 'VALIDATION',
            message: 'Please fix the highlighted fields.',
            field_errors: { party_size: 'Party size must be a whole number from 1 to 20.' },
          },
          422,
        ),
    })
    const error = await caught(service.joinWaitlist(1, party))
    expect(error.code).toBe('VALIDATION')
    expect(error.message).toBe('Please fix the highlighted fields.')
    expect(error.fieldErrors).toEqual({ party_size: 'Party size must be a whole number from 1 to 20.' })
  })

  it.each([
    ['UNAUTHORIZED', 401],
    ['FORBIDDEN', 403],
    ['NOT_FOUND', 404],
    ['WAITLIST_CLOSED', 409],
    ['ENTRY_CLOSED', 409],
    ['CONFLICT', 409],
  ] as const)('keeps the %s code from a %i response', async (code, status) => {
    const { service } = setup({ respond: () => jsonResponse({ code, message: 'Friendly.', field_errors: {} }, status) })
    expect((await caught(service.getRestaurants())).code).toBe(code)
  })

  it('reports an unreachable backend as UNAVAILABLE', async () => {
    const fetchImpl = vi.fn(async () => {
      throw new TypeError('Failed to fetch')
    })
    const service = createHttpService({ baseUrl: BASE, getToken: () => null, fetchImpl })
    const error = await caught(service.getRestaurants())
    expect(error.code).toBe('UNAVAILABLE')
    expect(error.message).toBe(UNAVAILABLE_MESSAGE)
    expect(error.message).not.toContain('Failed to fetch')
  })

  it('never shows a raw server error page', async () => {
    const { service } = setup({
      respond: () => new Response('<html>Traceback: KeyError at line 42</html>', { status: 500 }),
    })
    const error = await caught(service.getRestaurants())
    expect(error.code).toBe('UNAVAILABLE')
    expect(error.message).not.toContain('Traceback')
  })

  it.each([
    [401, 'UNAUTHORIZED'],
    [403, 'FORBIDDEN'],
    [404, 'NOT_FOUND'],
    [422, 'VALIDATION'],
    [502, 'UNAVAILABLE'],
  ] as const)('maps a bare %i without an Error body to %s', async (status, code) => {
    const { service } = setup({ respond: () => new Response(null, { status }) })
    expect((await caught(service.getRestaurants())).code).toBe(code)
  })

  it('ignores an error body with an unknown code or no message', async () => {
    const { service } = setup({ respond: () => jsonResponse({ code: 'KABOOM', message: 'internal detail' }, 400) })
    const error = await caught(service.getRestaurants())
    expect(error.code).toBe('VALIDATION')
    expect(error.message).not.toContain('internal detail')
  })

  it('drops non-string field errors', async () => {
    const { service } = setup({
      respond: () =>
        jsonResponse({ code: 'VALIDATION', message: 'Fix it.', field_errors: { name: 'Name is required.', bad: { nested: 1 } } }, 422),
    })
    expect((await caught(service.createRestaurant(restaurant))).fieldErrors).toEqual({ name: 'Name is required.' })
  })

  it('treats a success response that is not JSON as a failure', async () => {
    const { service } = setup({ respond: () => new Response('not json', { status: 200 }) })
    expect((await caught(service.getRestaurants())).code).toBe('UNAVAILABLE')
  })
})
