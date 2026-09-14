import { describe, expect, it } from 'vitest'
import type { WaitlistStatus } from './types'
import {
  activeQueue,
  allowedTransitions,
  canTransition,
  isOverdueNoShow,
  queuePosition,
  todaysHistory,
  type EntryTimeline,
} from './waitlist'

function entry(id: number, joined: string, status: WaitlistStatus = 'WAITING', extra: Partial<EntryTimeline> = {}) {
  return {
    id,
    status,
    joined_at: `2026-09-14T${joined}:00.000Z`,
    notified_at: null,
    seated_at: null,
    canceled_at: null,
    no_show_at: null,
    ...extra,
  } satisfies EntryTimeline
}

describe('queue position', () => {
  it('orders by joined_at, not by id or array order', () => {
    // ids deliberately disagree with join order so an id sort would fail.
    const entries = [entry(1, '18:20'), entry(2, '18:05'), entry(3, '18:10')]
    expect(activeQueue(entries).map((e) => e.id)).toEqual([2, 3, 1])
    expect(queuePosition(entries, 2)).toBe(1)
    expect(queuePosition(entries, 1)).toBe(3)
  })

  it('counts WAITING and NOTIFIED parties and skips finished ones', () => {
    const entries = [
      entry(1, '18:00', 'SEATED'),
      entry(2, '18:01', 'NOTIFIED'),
      entry(3, '18:02', 'CANCELED'),
      entry(4, '18:03', 'NO_SHOW'),
      entry(5, '18:04', 'WAITING'),
    ]
    expect(queuePosition(entries, 2)).toBe(1)
    expect(queuePosition(entries, 5)).toBe(2)
  })

  it('returns null for an entry that has left the queue', () => {
    expect(queuePosition([entry(1, '18:00', 'SEATED')], 1)).toBeNull()
  })

  it('moves everyone behind a party up when it leaves', () => {
    const entries = [entry(1, '18:00'), entry(2, '18:05'), entry(3, '18:10')]
    expect(queuePosition(entries, 3)).toBe(3)
    entries[0] = entry(1, '18:00', 'SEATED')
    expect(queuePosition(entries, 3)).toBe(2)
  })

  it('breaks a joined_at tie by id', () => {
    const entries = [entry(9, '18:00'), entry(4, '18:00')]
    expect(activeQueue(entries).map((e) => e.id)).toEqual([4, 9])
  })
})

describe('status transitions', () => {
  it('allows the spec flows out of WAITING and NOTIFIED', () => {
    expect(allowedTransitions('WAITING')).toEqual(['NOTIFIED', 'SEATED', 'CANCELED', 'NO_SHOW'])
    expect(allowedTransitions('NOTIFIED')).toEqual(['SEATED', 'CANCELED', 'NO_SHOW'])
  })

  it('does not go back to WAITING or re-notify', () => {
    expect(canTransition('NOTIFIED', 'WAITING')).toBe(false)
    expect(canTransition('NOTIFIED', 'NOTIFIED')).toBe(false)
  })

  it('treats SEATED, CANCELED and NO_SHOW as final', () => {
    for (const final of ['SEATED', 'CANCELED', 'NO_SHOW'] as const) {
      expect(allowedTransitions(final)).toEqual([])
    }
  })
})

describe('automatic no-show', () => {
  const notified = entry(1, '18:00', 'NOTIFIED', { notified_at: '2026-09-14T18:30:00.000Z' })

  it('is not overdue one second before notified_at + no_show_minutes', () => {
    expect(isOverdueNoShow(notified, 10, new Date('2026-09-14T18:39:59.000Z'))).toBe(false)
  })

  it('is overdue exactly at the deadline', () => {
    expect(isOverdueNoShow(notified, 10, new Date('2026-09-14T18:40:00.000Z'))).toBe(true)
  })

  it('never applies to a party that was not notified', () => {
    expect(isOverdueNoShow(entry(2, '12:00'), 10, new Date('2026-09-14T23:00:00.000Z'))).toBe(false)
  })
})

describe("today's history", () => {
  const now = new Date(2026, 8, 14, 21, 0) // local time, like the dashboard

  it('keeps only parties that finished today, most recent first', () => {
    const local = (h: number, m: number, day = 14) => new Date(2026, 8, day, h, m).toISOString()
    const entries = [
      entry(1, '18:00', 'SEATED', { seated_at: local(19, 0) }),
      entry(2, '18:00', 'CANCELED', { canceled_at: local(20, 0) }),
      entry(3, '18:00', 'NO_SHOW', { no_show_at: local(20, 0, 13) }), // yesterday
      entry(4, '18:00', 'WAITING'),
      entry(5, '18:00', 'NOTIFIED', { notified_at: local(20, 30) }),
    ]
    expect(todaysHistory(entries, now).map((e) => e.id)).toEqual([2, 1])
  })
})
