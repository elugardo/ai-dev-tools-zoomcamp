import { screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { emptyDb } from '../services/mock/mockDb'
import { ServiceError } from '../services/errors'
import { createTestServices, renderApp, signInAs } from '../test/renderApp'

describe('public home page', () => {
  it('renders the active restaurants with wait, availability and a View Waitlist link', async () => {
    renderApp('/')
    const list = await screen.findByRole('list', { name: 'Restaurants' })
    const cards = within(list).getAllByRole('listitem')
    expect(cards).toHaveLength(2)

    const bluebird = cards[0]
    expect(within(bluebird).getByRole('heading', { name: 'Bluebird Cafe' })).toBeInTheDocument()
    expect(within(bluebird).getByText('123 Main Street')).toBeInTheDocument()
    expect(within(bluebird).getByText('30 min wait')).toBeInTheDocument()
    expect(within(bluebird).getByText('Online waitlist open')).toBeInTheDocument()
    expect(within(bluebird).getByRole('link', { name: 'View Waitlist' })).toHaveAttribute('href', '/restaurants/1')
  })

  it('shows a paused restaurant with the unavailable message', async () => {
    const services = createTestServices()
    await signInAs(services, 'oakember')
    await services.updateRestaurantSettings({ online_waitlist_enabled: false })
    localStorage.clear() // browse as a guest

    renderApp('/', services)
    const card = (await screen.findByRole('heading', { name: 'Oak & Ember' })).closest('li')!
    expect(within(card).getByText('Online waitlist currently unavailable')).toBeInTheDocument()
  })

  it('does not show an inactive restaurant', async () => {
    const services = createTestServices()
    await signInAs(services, 'admin')
    await services.setRestaurantStatus(1, false)
    localStorage.clear()

    renderApp('/', services)
    expect(await screen.findByRole('heading', { name: 'Oak & Ember' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Bluebird Cafe' })).not.toBeInTheDocument()
  })

  it('shows the empty state when no restaurants are available', async () => {
    renderApp('/', createTestServices({ initialDb: emptyDb }))
    expect(await screen.findByText('No restaurants are currently available.')).toBeInTheDocument()
  })

  it('shows a friendly message when the backend is unavailable', async () => {
    const services = createTestServices()
    vi.spyOn(services, 'getRestaurants').mockRejectedValue(
      new ServiceError('UNAVAILABLE', "We can't reach WaitWise right now. Please try again shortly."),
    )
    renderApp('/', services)
    expect(await screen.findByRole('alert')).toHaveTextContent("We can't reach WaitWise right now.")
  })

  it('never shows the text of an unexpected exception', async () => {
    const services = createTestServices()
    vi.spyOn(services, 'getRestaurants').mockRejectedValue(new TypeError('Failed to fetch at line 42'))
    renderApp('/', services)
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Something went wrong. Please try again.')
    expect(alert).not.toHaveTextContent('line 42')
  })
})
