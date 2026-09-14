import { Link, useNavigate, useParams } from 'react-router-dom'
import { ErrorNotice, Loading } from '../components/Feedback'
import { PartyForm } from '../components/PartyForm'
import type { JoinWaitlistInput } from '../domain/types'
import { formatWaitQuote } from '../domain/waitTime'
import { useResource } from '../hooks/useResource'
import { ServiceError } from '../services/errors'
import { useServices } from '../services/ServicesContext'

export function RestaurantPage() {
  const services = useServices()
  const navigate = useNavigate()
  const restaurantId = Number(useParams().restaurantId)

  const restaurant = useResource(() => {
    if (!Number.isInteger(restaurantId)) {
      return Promise.reject(new ServiceError('NOT_FOUND', "We couldn't find that restaurant."))
    }
    return services.getRestaurant(restaurantId)
  }, [services, restaurantId])

  if (restaurant.loading) return <Loading />
  if (!restaurant.data) {
    return (
      <div className="stack">
        <ErrorNotice error={restaurant.error} />
        <Link to="/">Back to restaurants</Link>
      </div>
    )
  }

  const r = restaurant.data
  const canJoin = r.is_active && r.online_waitlist_enabled

  async function handleJoin(input: JoinWaitlistInput) {
    try {
      const entry = await services.joinWaitlist(r.id, input)
      navigate(`/wait/${entry.public_token}`)
    } catch (error) {
      // The restaurant may have paused joining since the page loaded; show it.
      if (error instanceof ServiceError && error.code === 'WAITLIST_CLOSED') void restaurant.reload()
      throw error
    }
  }

  return (
    <div className="stack narrow">
      <Link to="/" className="back-link">
        ← All restaurants
      </Link>

      <section className="card stack-sm">
        <h1>{r.name}</h1>
        <p className="muted">{r.address}</p>
        <p className="muted">
          <a href={`tel:${r.phone}`}>{r.phone}</a>
        </p>
        {r.description ? <p>{r.description}</p> : null}

        <dl className="stats">
          <div className="stat">
            <dt>Current wait</dt>
            <dd>{formatWaitQuote(r.current_wait_minutes)}</dd>
          </div>
          <div className="stat">
            <dt>Parties waiting</dt>
            <dd>{r.waiting_parties}</dd>
          </div>
        </dl>
      </section>

      <section className="card stack-sm" aria-labelledby="join-heading">
        <h2 id="join-heading">Join the waitlist</h2>
        {canJoin ? (
          <PartyForm submitLabel="Join Waitlist" onSubmit={handleJoin} />
        ) : (
          <>
            <p className="availability availability--closed">
              {r.is_active ? 'Online waitlist currently unavailable' : 'This restaurant is not currently taking guests.'}
            </p>
            <button type="button" className="button button--primary button--large" disabled>
              Join Waitlist
            </button>
          </>
        )}
      </section>
    </div>
  )
}
