import { useState, type FormEvent } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { homePathFor, useAuth } from '../auth/AuthContext'
import { ErrorNotice } from '../components/Feedback'
import { Field } from '../components/Field'
import { serviceMode } from '../services'

export function LoginPage() {
  const { user, login } = useAuth()
  const navigate = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [usernameError, setUsernameError] = useState<string>()
  const [error, setError] = useState<unknown>(null)
  const [submitting, setSubmitting] = useState(false)

  if (user) return <Navigate to={homePathFor(user)} replace />

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    if (!username.trim()) {
      setUsernameError('Username is required.')
      return
    }
    setUsernameError(undefined)
    setSubmitting(true)
    try {
      const signedIn = await login(username, password)
      navigate(homePathFor(signedIn), { replace: true })
    } catch (err) {
      setError(err)
      setSubmitting(false)
    }
  }

  return (
    <div className="narrow-sm stack">
      <section className="card stack-sm">
        <h1>Staff login</h1>
        <p className="muted">For WaitWise admins and restaurant staff.</p>
        <form className="form" onSubmit={handleSubmit} noValidate>
          {error ? <ErrorNotice error={error} /> : null}
          <Field label="Username" error={usernameError}>
            {(props) => (
              <input {...props} autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} />
            )}
          </Field>
          <Field label="Password" hint="Any password works in this class version.">
            {(props) => (
              <input
                {...props}
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            )}
          </Field>
          <button type="submit" className="button button--primary button--large" disabled={submitting}>
            {submitting ? 'Logging in…' : 'Log in'}
          </button>
        </form>
      </section>
      {serviceMode === 'mock' ? <p className="fine-print">Demo logins: admin, bluebird, oakember.</p> : null}
    </div>
  )
}
