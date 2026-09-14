// Wait-time arithmetic (spec §9). Pure functions — pass `now` in, never read the clock.

const MINUTE_MS = 60_000

export function addMinutes(iso: string, minutes: number): string {
  return new Date(new Date(iso).getTime() + minutes * MINUTE_MS).toISOString()
}

/**
 * Whole minutes left until `estimatedReadyAt`, rounded up so "20 min" holds
 * until the 20th minute is actually over. Never negative: 0 means ready soon.
 */
export function remainingWaitMinutes(estimatedReadyAt: string, now: Date): number {
  const msLeft = new Date(estimatedReadyAt).getTime() - now.getTime()
  if (msLeft <= 0) return 0
  return Math.ceil(msLeft / MINUTE_MS)
}

export function formatRemainingWait(estimatedReadyAt: string, now: Date): string {
  const minutes = remainingWaitMinutes(estimatedReadyAt, now)
  return minutes === 0 ? 'Ready soon' : formatMinutes(minutes)
}

/** Whole minutes elapsed since `iso`, floored and never negative. */
export function minutesSince(iso: string, now: Date): number {
  return Math.max(0, Math.floor((now.getTime() - new Date(iso).getTime()) / MINUTE_MS))
}

export function formatMinutes(minutes: number): string {
  if (minutes < 60) return `${minutes} min`
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  return rest === 0 ? `${hours} hr` : `${hours} hr ${rest} min`
}

export function formatWaitQuote(minutes: number): string {
  return minutes === 0 ? 'No wait' : `${formatMinutes(minutes)} wait`
}

export function formatClockTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
}

export function isSameLocalDay(iso: string, now: Date): boolean {
  const d = new Date(iso)
  return (
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate()
  )
}
