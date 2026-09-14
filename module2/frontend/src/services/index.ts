// Picks the services implementation for the running app. This file and the
// tests are the only places allowed to import from ./mock or ./http.
//
// VITE_SERVICE_MODE=http (the default) talks to the FastAPI backend at
// VITE_API_BASE_URL. VITE_SERVICE_MODE=mock runs the whole app on the in-browser
// mock, with no backend needed.

import { loadSession } from '../auth/session'
import { createHttpService } from './http/httpService'
import { createMockService, MOCK_DB_STORAGE_KEY } from './mock/mockService'
import type { WaitWiseService } from './WaitWiseService'

export type { WaitWiseService } from './WaitWiseService'
export { ServiceError, friendlyErrorMessage, isServiceError } from './errors'

// `vite --mode mock` (npm run dev:mock) selects the mock without an env file.
export const serviceMode: string =
  import.meta.env.VITE_SERVICE_MODE ?? (import.meta.env.MODE === 'mock' ? 'mock' : 'http')
export const apiBaseUrl: string = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:9127/api'

const getToken = () => loadSession()?.token ?? null

export function createServices(): WaitWiseService {
  switch (serviceMode) {
    case 'http':
      return createHttpService({ baseUrl: apiBaseUrl, getToken })
    case 'mock':
      return createMockService({ getToken, latencyMs: 250 })
    default:
      throw new Error(`Unknown VITE_SERVICE_MODE "${serviceMode}". Use "http" or "mock".`)
  }
}

/** Demo helper for mock mode: drop the mock's stored data so the next call reseeds it. */
export function resetMockData(): void {
  localStorage.removeItem(MOCK_DB_STORAGE_KEY)
}
