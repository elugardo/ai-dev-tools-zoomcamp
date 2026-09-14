import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { DEMO_PASSWORD } from '../services/mock/mockDb'
import { createTestServices, renderApp, signInAs } from '../test/renderApp'

async function renderAsAdmin(path: string, services = createTestServices()) {
  await signInAs(services, 'admin')
  renderApp(path, services)
  return services
}

describe('admin dashboard', () => {
  it('lists all restaurants with status, wait and username', async () => {
    await renderAsAdmin('/admin')
    const row = (await screen.findByText('Bluebird Cafe')).closest('tr')!
    expect(within(row).getByText('123 Main Street')).toBeInTheDocument()
    expect(within(row).getByText('Active')).toBeInTheDocument()
    expect(within(row).getByText('30 min')).toBeInTheDocument()
    expect(within(row).getByText('bluebird')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Add Restaurant' })).toBeInTheDocument()
  })

  it('deactivates and reactivates a restaurant', async () => {
    const user = userEvent.setup({ delay: null })
    const services = await renderAsAdmin('/admin')

    await user.click(await screen.findByRole('button', { name: 'Deactivate Bluebird Cafe' }))
    expect(await screen.findByRole('button', { name: 'Activate Bluebird Cafe' })).toBeInTheDocument()
    expect((await services.getRestaurants()).map((r) => r.name)).not.toContain('Bluebird Cafe')

    await user.click(screen.getByRole('button', { name: 'Activate Bluebird Cafe' }))
    expect(await screen.findByRole('button', { name: 'Deactivate Bluebird Cafe' })).toBeInTheDocument()
  })

  it('keeps restaurant staff out', async () => {
    const services = createTestServices()
    await signInAs(services, 'bluebird')
    renderApp('/admin', services)
    expect(await screen.findByText('Restaurant dashboard')).toBeInTheDocument()
  })
})

describe('add/edit restaurant form', () => {
  it('starts from the spec defaults', async () => {
    await renderAsAdmin('/admin/restaurants/new')
    expect(await screen.findByLabelText('Current wait (minutes)')).toHaveValue(30)
    expect(screen.getByLabelText('No-show timeout (minutes)')).toHaveValue(10)
    expect(screen.getByLabelText('Active (visible to the public)')).toBeChecked()
  })

  it('validates required fields and ranges without calling the service', async () => {
    const user = userEvent.setup({ delay: null })
    const services = createTestServices()
    const create = vi.spyOn(services, 'createRestaurant')
    await renderAsAdmin('/admin/restaurants/new', services)

    const noShow = await screen.findByLabelText('No-show timeout (minutes)')
    await user.clear(noShow)
    await user.type(noShow, '0')
    await user.click(screen.getByRole('button', { name: 'Create restaurant' }))

    expect(screen.getByText('Name is required.')).toBeInTheDocument()
    expect(screen.getByText('Address is required.')).toBeInTheDocument()
    expect(screen.getByText('Phone is required.')).toBeInTheDocument()
    expect(screen.getByText('Username is required.')).toBeInTheDocument()
    expect(screen.getByText('Password is required.')).toBeInTheDocument()
    expect(screen.getByText(/No-show timeout must be/)).toBeInTheDocument()
    expect(create).not.toHaveBeenCalled()
  })

  it('rejects a new password shorter than 8 characters', async () => {
    const user = userEvent.setup({ delay: null })
    await renderAsAdmin('/admin/restaurants/new')
    await user.type(await screen.findByLabelText('Password'), 'short')
    await user.click(screen.getByRole('button', { name: 'Create restaurant' }))
    expect(screen.getByText('Password must be at least 8 characters.')).toBeInTheDocument()
  })

  it('shows the service error when the username is taken', async () => {
    const user = userEvent.setup({ delay: null })
    await renderAsAdmin('/admin/restaurants/new')

    await user.type(await screen.findByLabelText('Name'), 'Copycat')
    await user.type(screen.getByLabelText('Address'), '1 Elm Street')
    await user.type(screen.getByLabelText('Phone'), '555-111-2222')
    await user.type(screen.getByLabelText('Username'), 'bluebird')
    await user.type(screen.getByLabelText('Password'), 'copycat-123')
    await user.click(screen.getByRole('button', { name: 'Create restaurant' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('That username is already taken.')
    expect(screen.getByLabelText('Username')).toHaveAttribute('aria-invalid', 'true')
  })

  it('creates a restaurant whose new login works, and returns to the list', async () => {
    const user = userEvent.setup({ delay: null })
    const services = await renderAsAdmin('/admin/restaurants/new')

    await user.type(await screen.findByLabelText('Name'), 'Harbor Noodle')
    await user.type(screen.getByLabelText('Address'), '789 Pier Road')
    await user.type(screen.getByLabelText('Phone'), '(555) 400-1234')
    await user.type(screen.getByLabelText('Username'), 'harbor')
    await user.type(screen.getByLabelText('Password'), 'noodles-123')
    await user.click(screen.getByRole('button', { name: 'Create restaurant' }))

    const row = (await screen.findByText('Harbor Noodle')).closest('tr')!
    expect(within(row).getByText('harbor')).toBeInTheDocument()
    expect((await services.login('harbor', 'noodles-123')).user.role).toBe('RESTAURANT')
  })

  it('loads an existing restaurant for editing and saves changes without retyping the password', async () => {
    const user = userEvent.setup({ delay: null })
    const services = await renderAsAdmin('/admin/restaurants/1/edit')

    const name = await screen.findByLabelText('Name')
    expect(name).toHaveValue('Bluebird Cafe')
    expect(screen.getByLabelText('Username')).toHaveValue('bluebird')
    expect(screen.getByLabelText('Password')).toHaveValue('')

    await user.clear(name)
    await user.type(name, 'Bluebird Bistro')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    expect(await screen.findByText('Bluebird Bistro')).toBeInTheDocument()
    expect((await services.getRestaurant(1)).name).toBe('Bluebird Bistro')
    expect((await services.login('bluebird', DEMO_PASSWORD)).user.restaurant_id).toBe(1)
  })
})

describe('login', () => {
  it('sends the admin to /admin and restaurant staff to their dashboard', async () => {
    const user = userEvent.setup({ delay: null })
    renderApp('/login')
    await user.type(screen.getByLabelText('Username'), 'admin')
    await user.type(screen.getByLabelText('Password'), DEMO_PASSWORD)
    await user.click(screen.getByRole('button', { name: 'Log in' }))
    expect(await screen.findByRole('heading', { name: 'Restaurants' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Log out' }))
    await user.click(await screen.findByRole('link', { name: 'Staff login' }))
    await user.type(screen.getByLabelText('Username'), 'oakember')
    await user.type(screen.getByLabelText('Password'), DEMO_PASSWORD)
    await user.click(screen.getByRole('button', { name: 'Log in' }))
    expect(await screen.findByRole('heading', { name: 'Oak & Ember' })).toBeInTheDocument()
  })

  it('requires a username and password before calling the service', async () => {
    const user = userEvent.setup({ delay: null })
    const services = createTestServices()
    const login = vi.spyOn(services, 'login')
    renderApp('/login', services)
    await user.click(screen.getByRole('button', { name: 'Log in' }))
    expect(screen.getByText('Username is required.')).toBeInTheDocument()
    expect(screen.getByText('Password is required.')).toBeInTheDocument()
    expect(login).not.toHaveBeenCalled()
  })

  it('rejects a wrong password with a friendly error and stays on the login page', async () => {
    const user = userEvent.setup({ delay: null })
    renderApp('/login')
    await user.type(screen.getByLabelText('Username'), 'bluebird')
    await user.type(screen.getByLabelText('Password'), 'not-the-password')
    await user.click(screen.getByRole('button', { name: 'Log in' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Incorrect username or password.')
    expect(screen.getByRole('heading', { name: 'Staff login' })).toBeInTheDocument()
  })
})
