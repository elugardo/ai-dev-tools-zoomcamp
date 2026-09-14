// The real implementation of WaitWiseService: HTTP calls to the FastAPI backend.
// Paths, bodies and error handling follow module2/openapi.yaml.
//
// Every failure becomes a ServiceError with a message safe to show a person:
// - the server's Error body `{code, message, field_errors}` is passed through;
// - a network failure (backend down, CORS, DNS) becomes UNAVAILABLE;
// - anything else (an HTML proxy page, a bare 500) gets a generic message by status.

import type {
  AdminRestaurant,
  EaterWaitlistView,
  LoginResult,
  PublicRestaurant,
  RestaurantWaitlist,
  StaffWaitlistEntry,
} from '../../domain/types'
import { ServiceError, type ServiceErrorCode } from '../errors'
import type { WaitWiseService } from '../WaitWiseService'

export interface HttpServiceOptions {
  /** e.g. http://localhost:9127/api — no trailing slash needed. */
  baseUrl: string
  /** The session's bearer token, read fresh on every call. */
  getToken: () => string | null
  /** Injected in tests; defaults to the global fetch. */
  fetchImpl?: typeof fetch
}

type Method = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'

const SERVER_CODES: readonly ServiceErrorCode[] = [
  'UNAUTHORIZED',
  'FORBIDDEN',
  'NOT_FOUND',
  'WAITLIST_CLOSED',
  'ENTRY_CLOSED',
  'CONFLICT',
  'VALIDATION',
]

export const UNAVAILABLE_MESSAGE = "We can't reach WaitWise right now. Please try again shortly."

function fallbackError(status: number): ServiceError {
  switch (status) {
    case 401:
      return new ServiceError('UNAUTHORIZED', 'Your session has expired. Please log in again.')
    case 403:
      return new ServiceError('FORBIDDEN', "You don't have access to that page.")
    case 404:
      return new ServiceError('NOT_FOUND', "We couldn't find that.")
    case 409:
      return new ServiceError('CONFLICT', 'That change conflicts with the current data. Please refresh and try again.')
    case 400:
    case 422:
      return new ServiceError('VALIDATION', 'Please check your input and try again.')
    default:
      return new ServiceError('UNAVAILABLE', 'WaitWise is having trouble right now. Please try again shortly.')
  }
}

function isErrorBody(value: unknown): value is { code: ServiceErrorCode; message: string; field_errors?: unknown } {
  if (!value || typeof value !== 'object') return false
  const body = value as Record<string, unknown>
  return (
    typeof body.code === 'string' &&
    SERVER_CODES.includes(body.code as ServiceErrorCode) &&
    typeof body.message === 'string' &&
    body.message.length > 0
  )
}

function stringRecord(value: unknown): Record<string, string> {
  if (!value || typeof value !== 'object') return {}
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>).filter((pair): pair is [string, string] => typeof pair[1] === 'string'),
  )
}

export function createHttpService(options: HttpServiceOptions): WaitWiseService {
  const baseUrl = options.baseUrl.replace(/\/+$/, '')
  const fetchImpl = options.fetchImpl ?? ((input, init) => fetch(input, init))

  async function request<T>(method: Method, path: string, { body, auth = false }: { body?: unknown; auth?: boolean } = {}) {
    const headers: Record<string, string> = { Accept: 'application/json' }
    if (body !== undefined) headers['Content-Type'] = 'application/json'
    if (auth) {
      const token = options.getToken()
      if (token) headers.Authorization = `Bearer ${token}`
    }

    let response: Response
    try {
      response = await fetchImpl(`${baseUrl}${path}`, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
      })
    } catch {
      throw new ServiceError('UNAVAILABLE', UNAVAILABLE_MESSAGE)
    }

    let payload: unknown = null
    try {
      payload = await response.json()
    } catch {
      payload = null
    }

    if (response.ok) {
      if (payload === null) throw fallbackError(502)
      return payload as T
    }
    if (isErrorBody(payload)) {
      throw new ServiceError(payload.code, payload.message, stringRecord(payload.field_errors))
    }
    throw fallbackError(response.status)
  }

  const id = (value: number) => encodeURIComponent(String(value))

  return {
    login: (username, password) => request<LoginResult>('POST', '/auth/login', { body: { username, password } }),

    getRestaurants: () => request<PublicRestaurant[]>('GET', '/restaurants'),
    getRestaurant: (restaurantId) => request<PublicRestaurant>('GET', `/restaurants/${id(restaurantId)}`),

    joinWaitlist: (restaurantId, input) =>
      request<EaterWaitlistView>('POST', `/restaurants/${id(restaurantId)}/waitlist`, { body: input }),
    getWaitlistEntry: (token) => request<EaterWaitlistView>('GET', `/waitlist/${encodeURIComponent(token)}`),
    cancelWaitlistEntry: (token) => request<EaterWaitlistView>('DELETE', `/waitlist/${encodeURIComponent(token)}`),

    getRestaurantWaitlist: () => request<RestaurantWaitlist>('GET', '/restaurant/waitlist', { auth: true }),
    addWalkIn: (input) => request<StaffWaitlistEntry>('POST', '/restaurant/waitlist', { body: input, auth: true }),
    updateWaitlistEntry: (entryId, status) =>
      request<StaffWaitlistEntry>('PATCH', `/restaurant/waitlist/${id(entryId)}`, { body: { status }, auth: true }),
    updateRestaurantSettings: (input) =>
      request<AdminRestaurant>('PATCH', '/restaurant/settings', { body: input, auth: true }),

    getAdminRestaurants: () => request<AdminRestaurant[]>('GET', '/admin/restaurants', { auth: true }),
    createRestaurant: (input) => request<AdminRestaurant>('POST', '/admin/restaurants', { body: input, auth: true }),
    getAdminRestaurant: (restaurantId) =>
      request<AdminRestaurant>('GET', `/admin/restaurants/${id(restaurantId)}`, { auth: true }),
    updateRestaurant: (restaurantId, input) =>
      request<AdminRestaurant>('PUT', `/admin/restaurants/${id(restaurantId)}`, { body: input, auth: true }),
    setRestaurantStatus: (restaurantId, isActive) =>
      request<AdminRestaurant>('PATCH', `/admin/restaurants/${id(restaurantId)}/status`, {
        body: { is_active: isActive },
        auth: true,
      }),
  }
}
