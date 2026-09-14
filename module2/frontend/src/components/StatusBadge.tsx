import type { WaitlistStatus } from '../domain/types'
import { STATUS_LABELS } from '../domain/waitlist'

export function StatusBadge({ status }: { status: WaitlistStatus }) {
  return <span className={`badge badge--${status.toLowerCase().replace('_', '-')}`}>{STATUS_LABELS[status]}</span>
}
