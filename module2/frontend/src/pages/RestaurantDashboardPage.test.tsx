import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { seedDb } from '../services/mock/mockDb'
import { createTestServices, renderApp, signInAs } from '../test/renderApp'

async function renderDashboard(services = createTestServices()) {
  await signInAs(services, 'bluebird')
  renderApp('/restaurant/dashboard', services)
  await screen.findByRole('heading', { name: 'Bluebird Cafe' })
  return services
}

function queueTable() {
  return within(screen.getByRole('region', { name: 'Active waitlist' }))
}

function historyTable() {
  return within(screen.getByRole('region', { name: "Today's History" }))
}

/** Guest names in the active queue, top to bottom. */
function guestNames() {
  return queueTable()
    .getAllByRole('row')
    .slice(1)
    .map((row) => row.querySelector('.table__strong')!.textContent)
}

describe('restaurant dashboard', () => {
  it('renders the header and the active queue in joined order', async () => {
    await renderDashboard()

    expect(screen.getByText('Parties waiting').nextSibling).toHaveTextContent('4')
    expect(screen.getByText('Online waitlist').nextSibling).toHaveTextContent('Open')
    expect(screen.getByRole('switch', { name: 'Accepting Online Waitlist' })).toBeChecked()

    expect(guestNames()).toEqual(['Lena', 'Sarah', 'James', 'Marcos'])
    const firstRow = queueTable().getAllByRole('row')[1]
    expect(within(firstRow).getByText('Notified')).toBeInTheDocument()
    expect(within(firstRow).getByText(/Notified \d+ min ago/)).toBeInTheDocument()
    // A notified party can be seated, canceled or marked no-show, but not notified again.
    expect(within(firstRow).queryByRole('button', { name: 'Notify Lena' })).not.toBeInTheDocument()
    expect(within(firstRow).getByRole('button', { name: 'Seat Lena' })).toBeInTheDocument()

    expect(historyTable().getByText('Chen')).toBeInTheDocument()
    expect(historyTable().getByText('Seated')).toBeInTheDocument()
  })

  it('notifies a party, changing its status in the queue', async () => {
    const user = userEvent.setup({ delay: null })
    await renderDashboard()

    await user.click(screen.getByRole('button', { name: 'Notify James' }))

    const jamesRow = (await queueTable().findByRole('button', { name: 'Seat James' })).closest('tr')!
    expect(within(jamesRow).getByText('Notified')).toBeInTheDocument()
    expect(within(jamesRow).queryByRole('button', { name: 'Notify James' })).not.toBeInTheDocument()
  })

  it("seats a party out of order and moves it to Today's History", async () => {
    const user = userEvent.setup({ delay: null })
    await renderDashboard()

    await user.click(screen.getByRole('button', { name: 'Seat Marcos' }))

    expect(await historyTable().findByText('Marcos')).toBeInTheDocument()
    expect(guestNames()).toEqual(['Lena', 'Sarah', 'James'])
    expect(queueTable().getAllByRole('row')[3]).toHaveTextContent('3')
  })

  it('adds a walk-in to the back of the queue', async () => {
    const user = userEvent.setup({ delay: null })
    const services = await renderDashboard()

    await user.click(screen.getByRole('button', { name: 'Add Walk-In' }))
    await user.type(screen.getByLabelText('Name'), 'Dana')
    await user.type(screen.getByLabelText('Mobile phone'), '555-010-3030')
    await user.click(screen.getByRole('button', { name: 'Add to Waitlist' }))

    expect(await queueTable().findByText('Dana')).toBeInTheDocument()
    expect(guestNames().at(-1)).toBe('Dana')
    const { active } = await services.getRestaurantWaitlist()
    expect(active.at(-1)).toMatchObject({ guest_name: 'Dana', source: 'STAFF' })
  })

  it('changes the current wait', async () => {
    const user = userEvent.setup({ delay: null })
    await renderDashboard()

    await user.click(screen.getByRole('button', { name: 'Change Wait' }))
    const input = screen.getByLabelText('Current wait (minutes)')
    await user.clear(input)
    await user.type(input, '45')
    await user.click(screen.getByRole('button', { name: 'Save wait' }))

    expect(await screen.findByText('45 min wait')).toBeInTheDocument()
  })

  it('pauses the online waitlist with the toggle', async () => {
    const user = userEvent.setup({ delay: null })
    const services = await renderDashboard()

    await user.click(screen.getByRole('switch', { name: 'Accepting Online Waitlist' }))

    expect(await screen.findByText('Paused')).toBeInTheDocument()
    expect((await services.getRestaurant(1)).online_waitlist_enabled).toBe(false)
  })

  it('shows the empty states', async () => {
    const services = createTestServices({
      initialDb: (now) => ({ ...seedDb(now), entries: [] }),
    })
    await renderDashboard(services)
    expect(screen.getByText('No one is waiting right now.')).toBeInTheDocument()
    expect(screen.getByText('No completed parties yet today.')).toBeInTheDocument()
  })

  it('sends a guest to the login page', async () => {
    renderApp('/restaurant/dashboard')
    expect(await screen.findByRole('heading', { name: 'Staff login' })).toBeInTheDocument()
  })
})
