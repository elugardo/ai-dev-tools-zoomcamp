// Waitlist business rules (spec §8, §12–§15, §20, §27). Pure functions over plain
// data, so the mock service uses them today and they stay testable without a UI.

import { addMinutes, isSameLocalDay } from './waitTime'
import { ACTIVE_STATUSES, FINAL_STATUSES, type WaitlistStatus } from './types'

/** The fields the rules need; both the mock's records and the DTOs satisfy it. */
export interface EntryTimeline {
  id: number
  status: WaitlistStatus
  joined_at: string
  notified_at: string | null
  seated_at: string | null
  canceled_at: string | null
  no_show_at: string | null
}

const TRANSITIONS: Record<WaitlistStatus, readonly WaitlistStatus[]> = {
  WAITING: ['NOTIFIED', 'SEATED', 'CANCELED', 'NO_SHOW'],
  NOTIFIED: ['SEATED', 'CANCELED', 'NO_SHOW'],
  SEATED: [],
  CANCELED: [],
  NO_SHOW: [],
}

export function isActive(status: WaitlistStatus): boolean {
  return ACTIVE_STATUSES.includes(status)
}

export function allowedTransitions(status: WaitlistStatus): readonly WaitlistStatus[] {
  return TRANSITIONS[status]
}

export function canTransition(from: WaitlistStatus, to: WaitlistStatus): boolean {
  return TRANSITIONS[from].includes(to)
}

/** Set once when an entry is created; the quote is never recomputed later (§9). */
export function estimateReadyAt(joinedAt: string, quotedWaitMinutes: number): string {
  return addMinutes(joinedAt, quotedWaitMinutes)
}

/** FIFO by joined_at; id breaks ties so parties joining in the same ms keep a stable order. */
export function compareQueueOrder(a: EntryTimeline, b: EntryTimeline): number {
  const byJoin = new Date(a.joined_at).getTime() - new Date(b.joined_at).getTime()
  return byJoin !== 0 ? byJoin : a.id - b.id
}

/** Active entries in queue order. Positions are never stored (§27). */
export function activeQueue<T extends EntryTimeline>(entries: readonly T[]): T[] {
  return entries.filter((e) => isActive(e.status)).sort(compareQueueOrder)
}

/** 1-based position among active entries, or null if the entry is not active. */
export function queuePosition(entries: readonly EntryTimeline[], entryId: number): number | null {
  const index = activeQueue(entries).findIndex((e) => e.id === entryId)
  return index === -1 ? null : index + 1
}

type StatusTimestampField = 'notified_at' | 'seated_at' | 'canceled_at' | 'no_show_at'

/** The timestamp field stamped when an entry moves into `status`. */
export function timestampFieldFor(status: WaitlistStatus): StatusTimestampField | null {
  switch (status) {
    case 'NOTIFIED':
      return 'notified_at'
    case 'SEATED':
      return 'seated_at'
    case 'CANCELED':
      return 'canceled_at'
    case 'NO_SHOW':
      return 'no_show_at'
    default:
      return null
  }
}

/** The moment an entry reached its final status, or null while it is still active. */
export function finalStatusAt(entry: EntryTimeline): string | null {
  if (!FINAL_STATUSES.includes(entry.status)) return null
  const field = timestampFieldFor(entry.status)
  return field ? entry[field] : null
}

/** A notified party becomes a no-show once `noShowMinutes` have passed since notification (§14). */
export function noShowDeadline(entry: EntryTimeline, noShowMinutes: number): string | null {
  if (entry.status !== 'NOTIFIED' || !entry.notified_at) return null
  return addMinutes(entry.notified_at, noShowMinutes)
}

export function isOverdueNoShow(entry: EntryTimeline, noShowMinutes: number, now: Date): boolean {
  const deadline = noShowDeadline(entry, noShowMinutes)
  return deadline !== null && now.getTime() >= new Date(deadline).getTime()
}

/** Today's finished parties, most recently finished first (§20). */
export function todaysHistory<T extends EntryTimeline>(entries: readonly T[], now: Date): T[] {
  const finished = entries.flatMap((entry) => {
    const at = finalStatusAt(entry)
    return at !== null && isSameLocalDay(at, now) ? [{ entry, at: new Date(at).getTime() }] : []
  })
  return finished.sort((a, b) => b.at - a.at).map((f) => f.entry)
}

export const STATUS_LABELS: Record<WaitlistStatus, string> = {
  WAITING: 'Waiting',
  NOTIFIED: 'Notified',
  SEATED: 'Seated',
  CANCELED: 'Canceled',
  NO_SHOW: 'No Show',
}
