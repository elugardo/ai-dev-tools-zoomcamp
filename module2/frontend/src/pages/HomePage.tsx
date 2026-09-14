import { Link } from 'react-router-dom'
import { EmptyState, ErrorNotice, Loading } from '../components/Feedback'
import { formatWaitQuote } from '../domain/waitTime'
import { useResource } from '../hooks/useResource'
import { useServices } from '../services/ServicesContext'

export function HomePage() {
  const services = useServices()
  const restaurants = useResource(() => services.getRestaurants(), [services])

  return (
    <div className="stack">
      <section className="hero">
        <h1>Skip the line. Join the waitlist.</h1>
        <p className="muted">See the current wait, add your party, and get a heads-up when your table is ready.</p>
      </section>

      {restaurants.loading ? <Loading label="Loading restaurants…" /> : null}
      {restaurants.error && !restaurants.data ? <ErrorNotice error={restaurants.error} /> : null}

      {restaurants.data && restaurants.data.length === 0 ? (
        <EmptyState>No restaurants are currently available.</EmptyState>
      ) : null}

      {restaurants.data && restaurants.data.length > 0 ? (
        <ul className="card-grid" aria-label="Restaurants">
          {restaurants.data.map((restaurant) => (
            <li key={restaurant.id} className="card restaurant-card">
              <div>
                <h2 className="restaurant-card__name">{restaurant.name}</h2>
                <p className="muted">{restaurant.address}</p>
              </div>
              <div className="restaurant-card__wait">
                <span className="wait-pill">{formatWaitQuote(restaurant.current_wait_minutes)}</span>
                {restaurant.online_waitlist_enabled ? (
                  <span className="availability availability--open">Online waitlist open</span>
                ) : (
                  <span className="availability availability--closed">Online waitlist currently unavailable</span>
                )}
              </div>
              <Link to={`/restaurants/${restaurant.id}`} className="button button--secondary">
                View Waitlist
              </Link>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}
