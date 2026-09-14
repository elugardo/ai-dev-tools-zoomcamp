// Picks the services implementation for the running app. This file and the
// tests are the only places allowed to import from ./mock.

import { loadSession } from '../auth/session'
import { createMockService, MOCK_DB_STORAGE_KEY } from './mock/mockService'
import type { WaitWiseService } from './WaitWiseService'

export type { WaitWiseService } from './WaitWiseService'
export { ServiceError, friendlyErrorMessage, isServiceError } from './errors'

export const serviceMode: string = import.meta.env.VITE_SERVICE_MODE ?? 'mock'
export const apiBaseUrl: string = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:9127/api'

export function createServices(): WaitWiseService {
  if (serviceMode === 'mock') {
    return createMockService({ getToken: () => loadSession()?.token ?? null, latencyMs: 250 })
  }
  // Phase 3 adds an HTTP implementation of WaitWiseService that calls apiBaseUrl.
  throw new Error(`VITE_SERVICE_MODE="${serviceMode}" is not implemented yet; use "mock".`)
}

/** Demo helper: drop the mock's stored data so the next call reseeds it. */
export function resetMockData(): void {
  localStorage.removeItem(MOCK_DB_STORAGE_KEY)
}
