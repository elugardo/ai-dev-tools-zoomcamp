import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'
import type { SessionUser } from '../domain/types'
import { useServices } from '../services/ServicesContext'
import { clearSession, loadSession, saveSession } from './session'

interface AuthState {
  user: SessionUser | null
  login: (username: string, password: string) => Promise<SessionUser>
  logout: () => void
}

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const services = useServices()
  const [user, setUser] = useState<SessionUser | null>(() => loadSession()?.user ?? null)

  const login = useCallback(
    async (username: string, password: string) => {
      const result = await services.login(username, password)
      saveSession(result)
      setUser(result.user)
      return result.user
    },
    [services],
  )

  const logout = useCallback(() => {
    clearSession()
    setUser(null)
  }, [])

  const value = useMemo(() => ({ user, login, logout }), [user, login, logout])
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthState {
  const auth = useContext(AuthContext)
  if (!auth) throw new Error('useAuth must be used inside <AuthProvider>.')
  return auth
}

export function homePathFor(user: SessionUser): string {
  return user.role === 'ADMIN' ? '/admin' : '/restaurant/dashboard'
}
