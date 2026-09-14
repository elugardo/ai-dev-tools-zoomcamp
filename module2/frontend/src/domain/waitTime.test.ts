import { describe, expect, it } from 'vitest'
import { formatMinutes, formatRemainingWait, minutesSince, remainingWaitMinutes } from './waitTime'
import { estimateReadyAt } from './waitlist'

// The worked example from spec §9: join at 6:00 PM with a 30-minute quote.
const joinedAt = '2026-09-14T18:00:00.000Z'
const readyAt = estimateReadyAt(joinedAt, 30)
const at = (hhmmss: string) => new Date(`2026-09-14T${hhmmss}.000Z`)

describe('estimated remaining wait', () => {
  it('sets estimated_ready_at to joined_at plus the quote', () => {
    expect(readyAt).toBe('2026-09-14T18:30:00.000Z')
  })

  it('follows the spec example: 20 min at 6:10, 5 min at 6:25', () => {
    expect(remainingWaitMinutes(readyAt, at('18:10:00'))).toBe(20)
    expect(remainingWaitMinutes(readyAt, at('18:25:00'))).toBe(5)
    expect(formatRemainingWait(readyAt, at('18:10:00'))).toBe('20 min')
  })

  it('says "Ready soon" at and after the ready time, never a negative wait', () => {
    expect(formatRemainingWait(readyAt, at('18:30:00'))).toBe('Ready soon')
    expect(formatRemainingWait(readyAt, at('18:45:00'))).toBe('Ready soon')
    expect(remainingWaitMinutes(readyAt, at('19:30:00'))).toBe(0)
  })

  it('rounds a partial minute up, so the last seconds still read "1 min"', () => {
    expect(remainingWaitMinutes(readyAt, at('18:10:30'))).toBe(20)
    expect(formatRemainingWait(readyAt, at('18:29:59'))).toBe('1 min')
  })

  it('treats a zero-minute quote as ready soon immediately', () => {
    expect(formatRemainingWait(estimateReadyAt(joinedAt, 0), at('18:00:00'))).toBe('Ready soon')
  })
})

describe('minutesSince', () => {
  it('floors elapsed minutes and never goes negative', () => {
    expect(minutesSince(joinedAt, at('18:32:59'))).toBe(32)
    expect(minutesSince(joinedAt, at('17:59:00'))).toBe(0)
  })
})

describe('formatMinutes', () => {
  it('switches to hours past 59 minutes', () => {
    expect(formatMinutes(45)).toBe('45 min')
    expect(formatMinutes(60)).toBe('1 hr')
    expect(formatMinutes(95)).toBe('1 hr 35 min')
  })
})
