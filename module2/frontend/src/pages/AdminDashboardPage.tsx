import { useState } from 'react'
import { Link } from 'react-router-dom'
import { EmptyState, ErrorNotice, Loading } from '../components/Feedback'
import type { AdminRestaurant } from '../domain/types'
import { formatMinutes } from '../domain/waitTime'
import { useResource } from '../hooks/useResource'
import { useServices } from '../services/ServicesContext'

export function AdminDashboardPage() {
  const services = useServices()
  const restaurants = useResource(() => services.getAdminRestaurants(), [services])
  const [pendingId, setPendingId] = useState<number | null>(null)
  const [actionError, setActionError] = useState<unknown>(null)

  async function toggleActive(restaurant: AdminRestaurant) {
    setPendingId(restaurant.id)
    setActionError(null)
    try {
      await services.setRestaurantStatus(restaurant.id, !restaurant.is_active)
      await restaurants.reload()
    } catch (error) {
      setActionError(error)
    } finally {
      setPendingId(null)
    }
  }

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>Restaurants</h1>
          <p className="muted">Every restaurant on WaitWise, including inactive ones.</p>
        </div>
        <Link to="/admin/restaurants/new" className="button button--primary">
          Add Restaurant
        </Link>
      </div>

      {actionError ? <ErrorNotice error={actionError} /> : null}
      {restaurants.loading ? <Loading label="Loading restaurants…" /> : null}
      {restaurants.error && !restaurants.data ? <ErrorNotice error={restaurants.error} /> : null}
      {restaurants.data?.length === 0 ? <EmptyState>No restaurants yet. Add the first one.</EmptyState> : null}

      {restaurants.data && restaurants.data.length > 0 ? (
        <div className="card table-card">
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Address</th>
                  <th>Status</th>
                  <th>Current wait</th>
                  <th>Username</th>
                  <th className="table__actions-head">Actions</th>
                </tr>
              </thead>
              <tbody>
                {restaurants.data.map((restaurant) => (
                  <tr key={restaurant.id}>
                    <td className="table__strong">{restaurant.name}</td>
                    <td>{restaurant.address}</td>
                    <td>
                      <span className={`badge ${restaurant.is_active ? 'badge--active' : 'badge--inactive'}`}>
                        {restaurant.is_active ? 'Active' : 'Inactive'}
                      </span>
                    </td>
                    <td>{formatMinutes(restaurant.current_wait_minutes)}</td>
                    <td>
                      <code>{restaurant.username}</code>
                    </td>
                    <td>
                      <div className="actions">
                        <Link
                          to={`/admin/restaurants/${restaurant.id}/edit`}
                          className="button button--secondary button--small"
                          aria-label={`Edit ${restaurant.name}`}
                        >
                          Edit
                        </Link>
                        <button
                          type="button"
                          className={`button button--small ${restaurant.is_active ? 'button--ghost-danger' : 'button--ghost'}`}
                          onClick={() => toggleActive(restaurant)}
                          disabled={pendingId === restaurant.id}
                          aria-label={`${restaurant.is_active ? 'Deactivate' : 'Activate'} ${restaurant.name}`}
                        >
                          {restaurant.is_active ? 'Deactivate' : 'Activate'}
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </div>
  )
}
