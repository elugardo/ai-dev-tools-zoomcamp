import { createContext, useContext, type ReactNode } from 'react'
import type { WaitWiseService } from './WaitWiseService'

const ServicesContext = createContext<WaitWiseService | null>(null)

export function ServicesProvider({ services, children }: { services: WaitWiseService; children: ReactNode }) {
  return <ServicesContext.Provider value={services}>{children}</ServicesContext.Provider>
}

/** The one way a component reaches the backend. */
export function useServices(): WaitWiseService {
  const services = useContext(ServicesContext)
  if (!services) throw new Error('useServices must be used inside <ServicesProvider>.')
  return services
}
