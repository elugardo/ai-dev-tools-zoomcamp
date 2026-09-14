import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ErrorNotice, Loading } from '../components/Feedback'
import { StatusBadge } from '../components/StatusBadge'
import type { EaterWaitlistView } from '../domain/types'
import { formatClockTime, formatMinutes, formatRemainingWait } from '../domain/waitTime'
import { isActive } from '../domain/waitlist'
import { useNow } from '../hooks/useNow'
import { useResource } from '../hooks/useResource'
import { useServices } from '../services/ServicesContext'

export const WAIT_STATUS_POLL_MS = 10_000

export function WaitStatusPage() {
  const services = useServices()
  const token = useParams().token ?? ''
  const now = useNow()
  const [status, setStatus] = useState<EaterWaitlistView['status'] | null>(null)

  // Poll only while the party is still in line; a finished entry cannot change.
  const pollMs = status === null || isActive(status) ? WAIT_STATUS_POLL_MS : null
  const entry = useResource(
    async () => {
      const view = await services.getWaitlistEntry(token)
      setStatus(view.status)
      return view
    },
    [services, token],
    pollMs,
  )

  const [confirmingLeave, setConfirmingLeave] = useState(false)
  const [leaveError, setLeaveError] = useState<unknown>(null)
  const [leaving, setLeaving] = useState(false)

  if (entry.loading) return <Loading label="Finding your spot…" />
  if (!entry.data) {
    return (
      <div className="stack narrow">
        <ErrorNotice error={entry.error} />
        <Link to="/">Back to restaurants</Link>
      </div>
    )
  }

  const view = entry.data

  async function handleLeave() {
    setLeaving(true)
    setLeaveError(null)
    try {
      const updated = await services.cancelWaitlistEntry(token)
      setStatus(updated.status)
      entry.setData(updated)
    } catch (error) {
      setLeaveError(error)
      void entry.reload()
    } finally {
      setLeaving(false)
      setConfirmingLeave(false)
    }
  }

  return (
    <div className="stack narrow">
      {entry.error ? (
        <div className="notice notice--warning" role="status">
          We're having trouble reaching the restaurant. Showing your last update.
        </div>
      ) : null}

      <section className={`card status-card status-card--${view.status.toLowerCase()}`}>
        <div className="status-card__top">
          <p className="eyebrow">{view.restaurant_name}</p>
          <StatusBadge status={view.status} />
        </div>

        {view.status === 'NOTIFIED' ? (
          <div className="ready" role="alert">
            <p className="ready__title">Your table is ready!</p>
            <p>Please check in with the host.</p>
          </div>
        ) : null}

        {view.status === 'WAITING' ? (
          <div className="status-hero">
            <p className="status-hero__party">Party of {view.party_size}</p>
            <p className="status-hero__position">
              You're <strong>#{view.position}</strong> in line
            </p>
            <p className="status-hero__label">Estimated remaining wait</p>
            <p className="status-hero__wait" data-testid="remaining-wait">
              {formatRemainingWait(view.estimated_ready_at, now)}
            </p>
          </div>
        ) : null}

        {view.status === 'SEATED' ? <p className="final-message">You've been seated. Enjoy your meal!</p> : null}
        {view.status === 'CANCELED' ? <p className="final-message">You've left the waitlist.</p> : null}
        {view.status === 'NO_SHOW' ? (
          <p className="final-message">Your table was released after we couldn't find you. Please see the host.</p>
        ) : null}

        <dl className="details">
          <div>
            <dt>Name</dt>
            <dd>{view.guest_name}</dd>
          </div>
          <div>
            <dt>Party size</dt>
            <dd>{view.party_size}</dd>
          </div>
          <div>
            <dt>Joined</dt>
            <dd>{formatClockTime(view.joined_at)}</dd>
          </div>
          {view.position !== null ? (
            <div>
              <dt>Position</dt>
              <dd>#{view.position}</dd>
            </div>
          ) : null}
          <div>
            <dt>Original quote</dt>
            <dd>{formatMinutes(view.quoted_wait_minutes)}</dd>
          </div>
        </dl>

        {isActive(view.status) ? (
          <div className="stack-sm">
            {leaveError ? <ErrorNotice error={leaveError} /> : null}
            {confirmingLeave ? (
              <div className="confirm">
                <p>Leave the waitlist? You'll lose your spot.</p>
                <div className="form__actions">
                  <button type="button" className="button button--ghost" onClick={() => setConfirmingLeave(false)}>
                    Stay in line
                  </button>
                  <button type="button" className="button button--danger" onClick={handleLeave} disabled={leaving}>
                    {leaving ? 'Leaving…' : 'Yes, leave'}
                  </button>
                </div>
              </div>
            ) : (
              <button type="button" className="button button--ghost-danger button--large" onClick={() => setConfirmingLeave(true)}>
                Leave Waitlist
              </button>
            )}
          </div>
        ) : null}
      </section>

      <p className="fine-print">
        Wait times are estimates and don't guarantee seating order. This page updates automatically.
      </p>
    </div>
  )
}
