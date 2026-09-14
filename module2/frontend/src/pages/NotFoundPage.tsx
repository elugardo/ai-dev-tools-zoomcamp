import { Link } from 'react-router-dom'

export function NotFoundPage() {
  return (
    <div className="stack narrow">
      <h1>Page not found</h1>
      <p className="muted">That page doesn't exist.</p>
      <Link to="/">Back to restaurants</Link>
    </div>
  )
}
