import type { ReactNode } from 'react'
import { friendlyErrorMessage } from '../services/errors'

export function ErrorNotice({ error, action }: { error: unknown; action?: ReactNode }) {
  return (
    <div className="notice notice--error" role="alert">
      <span>{friendlyErrorMessage(error)}</span>
      {action}
    </div>
  )
}

export function Loading({ label = 'Loading…' }: { label?: string }) {
  return (
    <p className="loading" role="status">
      {label}
    </p>
  )
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>
}
