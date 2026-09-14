import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { createTestServices, renderApp, signInAs } from '../test/renderApp'

describe('public restaurant page', () => {
  it('renders the restaurant details, current wait and parties waiting', async () => {
    renderApp('/restaurants/1')
    expect(await screen.findByRole('heading', { name: 'Bluebird Cafe' })).toBeInTheDocument()
    expect(screen.getByText('123 Main Street')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '(555) 201-4455' })).toBeInTheDocument()
    expect(screen.getByText(/sunny patio/)).toBeInTheDocument()

    const waitStat = screen.getByText('Current wait').closest('div')!
    expect(within(waitStat).getByText('30 min wait')).toBeInTheDocument()
    const partiesStat = screen.getByText('Parties waiting').closest('div')!
    expect(within(partiesStat).getByText('4')).toBeInTheDocument()

    expect(screen.getByRole('button', { name: 'Join Waitlist' })).toBeEnabled()
  })

  it('shows a friendly error for a restaurant that does not exist', async () => {
    renderApp('/restaurants/999')
    expect(await screen.findByRole('alert')).toHaveTextContent("We couldn't find that restaurant.")
  })

  it('validates required fields before calling the service', async () => {
    const user = userEvent.setup({ delay: null })
    const services = createTestServices()
    const join = vi.spyOn(services, 'joinWaitlist')
    renderApp('/restaurants/1', services)

    await user.clear(await screen.findByLabelText('Party size'))
    await user.click(screen.getByRole('button', { name: 'Join Waitlist' }))

    expect(screen.getByText('Name is required.')).toBeInTheDocument()
    expect(screen.getByText('Mobile phone is required.')).toBeInTheDocument()
    expect(screen.getByText('Party size is required.')).toBeInTheDocument()
    expect(screen.getByLabelText('Name')).toHaveAttribute('aria-invalid', 'true')
    expect(join).not.toHaveBeenCalled()
  })

  it('rejects a party larger than 20', async () => {
    const user = userEvent.setup({ delay: null })
    renderApp('/restaurants/1')
    await user.type(await screen.findByLabelText('Name'), 'Big Group')
    await user.type(screen.getByLabelText('Mobile phone'), '555-010-9999')
    await user.clear(screen.getByLabelText('Party size'))
    await user.type(screen.getByLabelText('Party size'), '21')
    await user.click(screen.getByRole('button', { name: 'Join Waitlist' }))

    expect(screen.getByText('Party size must be a whole number from 1 to 20.')).toBeInTheDocument()
  })

  it('joins and lands on the personal status page', async () => {
    const user = userEvent.setup({ delay: null })
    renderApp('/restaurants/1')

    await user.type(await screen.findByLabelText('Name'), 'Marcos')
    await user.type(screen.getByLabelText('Mobile phone'), '555-010-6677')
    await user.clear(screen.getByLabelText('Party size'))
    await user.type(screen.getByLabelText('Party size'), '4')
    await user.click(screen.getByRole('button', { name: 'Join Waitlist' }))

    expect(await screen.findByText('Party of 4')).toBeInTheDocument()
    expect(screen.getByText('Bluebird Cafe')).toBeInTheDocument()
    expect(screen.getByText(/in line/)).toHaveTextContent("You're #5 in line")
    expect(screen.getByTestId('remaining-wait')).toHaveTextContent('30 min')
    expect(screen.getByText('Joined')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Leave Waitlist' })).toBeInTheDocument()
  })

  it('disables joining while the online waitlist is paused', async () => {
    const services = createTestServices()
    await signInAs(services, 'bluebird')
    await services.updateRestaurantSettings({ online_waitlist_enabled: false })
    localStorage.clear()

    renderApp('/restaurants/1', services)
    expect(await screen.findByText('Online waitlist currently unavailable')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Join Waitlist' })).toBeDisabled()
    expect(screen.queryByLabelText('Name')).not.toBeInTheDocument()
  })

  it('shows the closed message if joining is paused after the page loaded', async () => {
    const user = userEvent.setup({ delay: null })
    const services = createTestServices()
    renderApp('/restaurants/1', services)
    await user.type(await screen.findByLabelText('Name'), 'Late')
    await user.type(screen.getByLabelText('Mobile phone'), '555-010-1111')

    await signInAs(services, 'bluebird')
    await services.updateRestaurantSettings({ online_waitlist_enabled: false })
    await user.click(screen.getByRole('button', { name: 'Join Waitlist' }))

    expect(await screen.findByText('Online waitlist currently unavailable')).toBeInTheDocument()
  })
})
