import { useState, type FormEvent } from 'react'
import { useAuth } from '../auth/AuthContext'
import { EmptyState, ErrorNotice, Loading } from '../components/Feedback'
import { Field } from '../components/Field'
import { PartyForm } from '../components/PartyForm'
import { StatusBadge } from '../components/StatusBadge'
import type { AdminRestaurant, JoinWaitlistInput, StaffWaitlistEntry, WaitlistStatus } from '../domain/types'
import { validateWaitMinutes, WAIT_MINUTES_MAX } from '../domain/validation'
import { formatClockTime, formatMinutes, formatWaitQuote, minutesSince } from '../domain/waitTime'
import { allowedTransitions, finalStatusAt } from '../domain/waitlist'
import { useNow } from '../hooks/useNow'
import { useResource } from '../hooks/useResource'
import { isServiceError } from '../services/errors'
import { useServices } from '../services/ServicesContext'

export const DASHBOARD_POLL_MS = 10_000

const ACTION_LABELS: Partial<Record<WaitlistStatus, string>> = {
  NOTIFIED: 'Notify',
  SEATED: 'Seat',
  CANCELED: 'Cancel',
  NO_SHOW: 'No Show',
}

export function RestaurantDashboardPage() {
  const services = useServices()
  const { logout } = useAuth()
  const now = useNow(15_000)
  const waitlist = useResource(() => services.getRestaurantWaitlist(), [services], DASHBOARD_POLL_MS)

  const [panel, setPanel] = useState<'none' | 'wait' | 'walk-in'>('none')
  const [pendingEntryId, setPendingEntryId] = useState<number | null>(null)
  const [actionError, setActionError] = useState<unknown>(null)
  const [togglingOnline, setTogglingOnline] = useState(false)

  if (waitlist.loading) return <Loading label="Loading waitlist…" />
  if (!waitlist.data) {
    const sessionProblem = isServiceError(waitlist.error, 'UNAUTHORIZED') || isServiceError(waitlist.error, 'FORBIDDEN')
    return (
      <ErrorNotice
        error={waitlist.error}
        action={
          sessionProblem ? (
            <button type="button" className="button button--small button--secondary" onClick={logout}>
              Log in again
            </button>
          ) : null
        }
      />
    )
  }

  const { restaurant, active, history } = waitlist.data

  async function changeStatus(entry: StaffWaitlistEntry, status: WaitlistStatus) {
    setPendingEntryId(entry.id)
    setActionError(null)
    try {
      await services.updateWaitlistEntry(entry.id, status)
    } catch (error) {
      setActionError(error)
    } finally {
      await waitlist.reload()
      setPendingEntryId(null)
    }
  }

  async function toggleOnline() {
    setTogglingOnline(true)
    setActionError(null)
    try {
      await services.updateRestaurantSettings({ online_waitlist_enabled: !restaurant.online_waitlist_enabled })
      await waitlist.reload()
    } catch (error) {
      setActionError(error)
    } finally {
      setTogglingOnline(false)
    }
  }

  async function addWalkIn(input: JoinWaitlistInput) {
    await services.addWalkIn(input)
    setPanel('none')
    await waitlist.reload()
  }

  return (
    <div className="stack">
      <section className="card dashboard-header">
        <div className="dashboard-header__title">
          <p className="eyebrow">Restaurant dashboard</p>
          <h1>{restaurant.name}</h1>
          {!restaurant.is_active ? (
            <span className="badge badge--inactive">Inactive — hidden from the public</span>
          ) : null}
        </div>

        <dl className="stats">
          <div className="stat">
            <dt>Current wait</dt>
            <dd>{formatWaitQuote(restaurant.current_wait_minutes)}</dd>
          </div>
          <div className="stat">
            <dt>Parties waiting</dt>
            <dd>{active.length}</dd>
          </div>
          <div className="stat">
            <dt>Online waitlist</dt>
            <dd>{restaurant.online_waitlist_enabled ? 'Open' : 'Paused'}</dd>
          </div>
        </dl>

        <div className="dashboard-header__controls">
          <button
            type="button"
            className="button button--secondary"
            aria-expanded={panel === 'wait'}
            onClick={() => setPanel(panel === 'wait' ? 'none' : 'wait')}
          >
            Change Wait
          </button>
          <button
            type="button"
            className="button button--primary"
            aria-expanded={panel === 'walk-in'}
            onClick={() => setPanel(panel === 'walk-in' ? 'none' : 'walk-in')}
          >
            Add Walk-In
          </button>
          <label className="switch">
            <input
              type="checkbox"
              role="switch"
              checked={restaurant.online_waitlist_enabled}
              onChange={toggleOnline}
              disabled={togglingOnline}
            />
            <span className="switch__track" aria-hidden="true" />
            Accepting Online Waitlist
          </label>
        </div>
      </section>

      {panel === 'wait' ? (
        <ChangeWaitPanel
          restaurant={restaurant}
          onSave={async (minutes) => {
            await services.updateRestaurantSettings({ current_wait_minutes: minutes })
            setPanel('none')
            await waitlist.reload()
          }}
          onCancel={() => setPanel('none')}
        />
      ) : null}

      {panel === 'walk-in' ? (
        <section className="card stack-sm" aria-labelledby="walk-in-heading">
          <h2 id="walk-in-heading">Add walk-in</h2>
          <PartyForm submitLabel="Add to Waitlist" onSubmit={addWalkIn} onCancel={() => setPanel('none')} />
        </section>
      ) : null}

      {actionError ? <ErrorNotice error={actionError} /> : null}
      {waitlist.error ? (
        <div className="notice notice--warning" role="status">
          Couldn't refresh the waitlist. Showing the last update.
        </div>
      ) : null}

      <section className="card table-card" aria-labelledby="queue-heading">
        <h2 id="queue-heading" className="table-card__title">
          Active waitlist
        </h2>
        {active.length === 0 ? (
          <EmptyState>No one is waiting right now.</EmptyState>
        ) : (
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th>Pos</th>
                  <th>Guest</th>
                  <th>Party</th>
                  <th>Joined</th>
                  <th>Waiting</th>
                  <th>Est. ready</th>
                  <th>Status</th>
                  <th className="table__actions-head">Actions</th>
                </tr>
              </thead>
              <tbody>
                {active.map((entry) => (
                  <tr key={entry.id} data-testid={`entry-${entry.id}`}>
                    <td className="table__pos">{entry.position}</td>
                    <td>
                      <div className="table__strong">{entry.guest_name}</div>
                      <div className="table__sub">
                        {entry.mobile_phone}
                        {entry.source === 'STAFF' ? ' · walk-in' : ''}
                      </div>
                      {entry.notes ? <div className="table__sub">“{entry.notes}”</div> : null}
                    </td>
                    <td>{entry.party_size}</td>
                    <td>{formatClockTime(entry.joined_at)}</td>
                    <td>{formatMinutes(minutesSince(entry.joined_at, now))}</td>
                    <td>{formatClockTime(entry.estimated_ready_at)}</td>
                    <td>
                      <StatusBadge status={entry.status} />
                      {entry.notified_at ? (
                        <div className="table__sub">Notified {minutesSince(entry.notified_at, now)} min ago</div>
                      ) : null}
                    </td>
                    <td>
                      <div className="actions">
                        {allowedTransitions(entry.status).map((status) => (
                          <button
                            key={status}
                            type="button"
                            className={`button button--small ${actionClass(status)}`}
                            onClick={() => changeStatus(entry, status)}
                            disabled={pendingEntryId === entry.id}
                            aria-label={`${ACTION_LABELS[status]} ${entry.guest_name}`}
                          >
                            {ACTION_LABELS[status]}
                          </button>
                        ))}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="card table-card" aria-labelledby="history-heading">
        <h2 id="history-heading" className="table-card__title">
          Today's History
        </h2>
        {history.length === 0 ? (
          <EmptyState>No completed parties yet today.</EmptyState>
        ) : (
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th>Guest</th>
                  <th>Party</th>
                  <th>Joined</th>
                  <th>Final status</th>
                  <th>Final status time</th>
                </tr>
              </thead>
              <tbody>
                {history.map((entry) => (
                  <tr key={entry.id}>
                    <td className="table__strong">{entry.guest_name}</td>
                    <td>{entry.party_size}</td>
                    <td>{formatClockTime(entry.joined_at)}</td>
                    <td>
                      <StatusBadge status={entry.status} />
                    </td>
                    <td>{formatClockTime(finalStatusAt(entry)!)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}

function actionClass(status: WaitlistStatus): string {
  if (status === 'NOTIFIED') return 'button--primary'
  if (status === 'SEATED') return 'button--secondary'
  return 'button--ghost'
}

function ChangeWaitPanel({
  restaurant,
  onSave,
  onCancel,
}: {
  restaurant: AdminRestaurant
  onSave: (minutes: number) => Promise<void>
  onCancel: () => void
}) {
  const [value, setValue] = useState(String(restaurant.current_wait_minutes))
  const [fieldError, setFieldError] = useState<string>()
  const [error, setError] = useState<unknown>(null)
  const [saving, setSaving] = useState(false)

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    const minutes = value.trim() === '' ? NaN : Number(value)
    const message = validateWaitMinutes(minutes)
    setFieldError(message ?? undefined)
    setError(null)
    if (message) return
    setSaving(true)
    try {
      await onSave(minutes)
    } catch (err) {
      setError(err)
      setSaving(false)
    }
  }

  return (
    <section className="card stack-sm" aria-labelledby="wait-heading">
      <h2 id="wait-heading">Change current wait</h2>
      <p className="muted">
        New guests are quoted this wait. Parties already in line keep the wait they were quoted.
      </p>
      <form className="form form--inline" onSubmit={handleSubmit} noValidate>
        {error ? <ErrorNotice error={error} /> : null}
        <Field label="Current wait (minutes)" error={fieldError}>
          {(props) => (
            <input
              {...props}
              type="number"
              min={0}
              max={WAIT_MINUTES_MAX}
              step={5}
              value={value}
              onChange={(e) => setValue(e.target.value)}
            />
          )}
        </Field>
        <div className="quick-picks" role="group" aria-label="Quick picks">
          {[0, 15, 30, 45, 60].map((minutes) => (
            <button key={minutes} type="button" className="chip" onClick={() => setValue(String(minutes))}>
              {minutes === 0 ? 'No wait' : `${minutes}m`}
            </button>
          ))}
        </div>
        <div className="form__actions">
          <button type="button" className="button button--ghost" onClick={onCancel}>
            Cancel
          </button>
          <button type="submit" className="button button--primary" disabled={saving}>
            {saving ? 'Saving…' : 'Save wait'}
          </button>
        </div>
      </form>
    </section>
  )
}
