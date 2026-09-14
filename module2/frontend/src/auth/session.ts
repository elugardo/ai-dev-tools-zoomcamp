// The signed-in user, kept in localStorage as the spec allows (§5).

import type { LoginResult } from '../domain/types'

export const SESSION_STORAGE_KEY = 'waitwise.session'

export function loadSession(): LoginResult | null {
  try {
    const raw = localStorage.getItem(SESSION_STORAGE_KEY)
    return raw ? (JSON.parse(raw) as LoginResult) : null
  } catch {
    return null
  }
}

export function saveSession(session: LoginResult): void {
  localStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(session))
}

export function clearSession(): void {
  localStorage.removeItem(SESSION_STORAGE_KEY)
}
