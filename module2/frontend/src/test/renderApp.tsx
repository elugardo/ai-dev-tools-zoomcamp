import { render } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { App } from '../App'
import { loadSession, saveSession } from '../auth/session'
import { createMockService, type MockServiceOptions } from '../services/mock/mockService'
import type { WaitWiseService } from '../services/WaitWiseService'

/** A private in-memory mock, seeded with the demo data unless told otherwise. */
export function createTestServices(options: Partial<MockServiceOptions> = {}): WaitWiseService {
  return createMockService({
    getToken: () => loadSession()?.token ?? null,
    storage: null,
    ...options,
  })
}

/** Logs in through the service and stores the session, as the login page would. */
export async function signInAs(services: WaitWiseService, username: string): Promise<void> {
  saveSession(await services.login(username, 'anything'))
}

/** Renders the full app — real routes, real pages — at `path`. */
export function renderApp(path: string, services: WaitWiseService = createTestServices()) {
  return {
    services,
    ...render(
      <MemoryRouter initialEntries={[path]}>
        <App services={services} />
      </MemoryRouter>,
    ),
  }
}
