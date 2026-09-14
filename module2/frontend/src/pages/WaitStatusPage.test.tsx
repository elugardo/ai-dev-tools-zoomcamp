import { act, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { emptyDb, seedDb } from '../services/mock/mockDb'
import { createTestServices, renderApp, signInAs } from '../test/renderApp'
import { WAIT_STATUS_POLL_MS } from './WaitStatusPage'

const START = new Date('2026-09-14T18:00:00.000Z')

/**
 * Fakes only setInterval and Date. setTimeout stays real, so Testing Library's
 * findBy/waitFor keep working while the page's polling and countdown are driven
 * by hand.
 */
function useFakeClock() {
  vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'Date'] })
  vi.setSystemTime(START)
}

function bluebirdWithoutEntries() {
  const db = seedDb(START)
  db.entries = []
  return db
}

const party = { guest_name: 'Marcos', mobile_phone: '555-010-6677', party_size: 2, notes: '' }

describe('eater status page', () => {
  it('polls and shows "Your table is ready!" once the restaurant notifies, without a refresh', async () => {
    useFakeClock()
    const services = createTestServices({ initialDb: bluebirdWithoutEntries })
    const ahead = await services.joinWaitlist(1, { ...party, guest_name: 'Ahead', party_size: 6 })
    const mine = await services.joinWaitlist(1, party)

    renderApp(`/wait/${mine.public_token}`, services)
    expect(await screen.findByText(/in line/)).toHaveTextContent("You're #2 in line")

    // Staff act in another tab: the party ahead leaves, then this party is notified.
    await services.cancelWaitlistEntry(ahead.public_token)
    await signInAs(services, 'bluebird')
    const { active } = await services.getRestaurantWaitlist()
    await services.updateWaitlistEntry(active.find((e) => e.public_token === mine.public_token)!.id, 'NOTIFIED')

    // Nothing changes until the next poll...
    expect(screen.getByText(/in line/)).toHaveTextContent("You're #2 in line")
    expect(screen.queryByText('Your table is ready!')).not.toBeInTheDocument()

    await act(() => vi.advanceTimersByTimeAsync(WAIT_STATUS_POLL_MS))

    expect(screen.getByText('Your table is ready!')).toBeInTheDocument()
    expect(screen.getByText('Please check in with the host.')).toBeInTheDocument()
    expect(screen.getByText('Notified')).toBeInTheDocument()
    expect(screen.getByText('#1')).toBeInTheDocument()
  })

  it('counts the remaining wait down locally between polls', async () => {
    useFakeClock()
    const services = createTestServices({ initialDb: bluebirdWithoutEntries })
    const mine = await services.joinWaitlist(1, party)
    const getEntry = vi.spyOn(services, 'getWaitlistEntry')

    renderApp(`/wait/${mine.public_token}`, services)
    expect(await screen.findByTestId('remaining-wait')).toHaveTextContent('30 min')
    const callsAfterLoad = getEntry.mock.calls.length

    vi.setSystemTime(new Date(START.getTime() + 12 * 60_000))
    await act(() => vi.advanceTimersByTimeAsync(1000))
    expect(screen.getByTestId('remaining-wait')).toHaveTextContent('18 min')
    expect(getEntry.mock.calls.length).toBe(callsAfterLoad)

    vi.setSystemTime(new Date(START.getTime() + 31 * 60_000))
    await act(() => vi.advanceTimersByTimeAsync(1000))
    expect(screen.getByTestId('remaining-wait')).toHaveTextContent('Ready soon')
  })

  it('lets the eater leave the waitlist after confirming', async () => {
    const user = userEvent.setup({ delay: null })
    const services = createTestServices({ initialDb: bluebirdWithoutEntries })
    const mine = await services.joinWaitlist(1, party)
    renderApp(`/wait/${mine.public_token}`, services)

    await user.click(await screen.findByRole('button', { name: 'Leave Waitlist' }))
    await user.click(screen.getByRole('button', { name: 'Yes, leave' }))

    expect(await screen.findByText("You've left the waitlist.")).toBeInTheDocument()
    expect(screen.getByText('Canceled')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Leave Waitlist' })).not.toBeInTheDocument()
    expect((await services.getWaitlistEntry(mine.public_token)).status).toBe('CANCELED')
  })

  it('stops polling once the entry is finished', async () => {
    useFakeClock()
    const services = createTestServices({ initialDb: bluebirdWithoutEntries })
    const mine = await services.joinWaitlist(1, party)
    await services.cancelWaitlistEntry(mine.public_token)
    const getEntry = vi.spyOn(services, 'getWaitlistEntry')

    renderApp(`/wait/${mine.public_token}`, services)
    expect(await screen.findByText("You've left the waitlist.")).toBeInTheDocument()
    const calls = getEntry.mock.calls.length
    await act(() => vi.advanceTimersByTimeAsync(WAIT_STATUS_POLL_MS * 3))
    expect(getEntry.mock.calls.length).toBe(calls)
  })

  it('shows a friendly error for an invalid token', async () => {
    renderApp('/wait/not-a-real-token', createTestServices({ initialDb: emptyDb }))
    expect(await screen.findByRole('alert')).toHaveTextContent("We couldn't find that waitlist spot.")
  })
})
