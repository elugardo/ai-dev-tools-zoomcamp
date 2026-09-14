import { useEffect, useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ErrorNotice, Loading } from '../components/Feedback'
import { Field } from '../components/Field'
import type { RestaurantInput } from '../domain/types'
import {
  DESCRIPTION_MAX,
  hasErrors,
  NO_SHOW_MINUTES_MAX,
  NO_SHOW_MINUTES_MIN,
  RESTAURANT_DEFAULTS,
  validateRestaurantInput,
  WAIT_MINUTES_MAX,
  type FieldErrors,
} from '../domain/validation'
import { isServiceError } from '../services/errors'
import { useServices } from '../services/ServicesContext'

/** Form state keeps numbers as strings so a half-typed value stays editable. */
type FormValues = Omit<RestaurantInput, 'current_wait_minutes' | 'no_show_minutes'> & {
  current_wait_minutes: string
  no_show_minutes: string
}

function toFormValues(input: RestaurantInput): FormValues {
  return {
    ...input,
    current_wait_minutes: String(input.current_wait_minutes),
    no_show_minutes: String(input.no_show_minutes),
  }
}

function toNumber(value: string): number {
  return value.trim() === '' ? NaN : Number(value)
}

export function RestaurantFormPage() {
  const services = useServices()
  const navigate = useNavigate()
  const { id } = useParams()
  const restaurantId = id === undefined ? null : Number(id)
  const isEdit = restaurantId !== null

  const [values, setValues] = useState<FormValues>(() => toFormValues(RESTAURANT_DEFAULTS))
  const [loadError, setLoadError] = useState<unknown>(null)
  const [loading, setLoading] = useState(isEdit)
  const [errors, setErrors] = useState<FieldErrors<RestaurantInput>>({})
  const [submitError, setSubmitError] = useState<unknown>(null)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    if (restaurantId === null) return
    let cancelled = false
    services
      .getAdminRestaurant(restaurantId)
      .then((r) => {
        if (cancelled) return
        setValues(
          toFormValues({
            name: r.name,
            address: r.address,
            phone: r.phone,
            description: r.description,
            current_wait_minutes: r.current_wait_minutes,
            no_show_minutes: r.no_show_minutes,
            is_active: r.is_active,
            username: r.username,
            password: '',
          }),
        )
      })
      .catch((error) => !cancelled && setLoadError(error))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [services, restaurantId])

  const set = <K extends keyof FormValues>(field: K, value: FormValues[K]) =>
    setValues((v) => ({ ...v, [field]: value }))

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    const input: RestaurantInput = {
      ...values,
      current_wait_minutes: toNumber(values.current_wait_minutes),
      no_show_minutes: toNumber(values.no_show_minutes),
    }
    const found = validateRestaurantInput(input)
    setErrors(found)
    setSubmitError(null)
    if (hasErrors(found)) return

    setSubmitting(true)
    try {
      if (restaurantId === null) await services.createRestaurant(input)
      else await services.updateRestaurant(restaurantId, input)
      navigate('/admin')
    } catch (error) {
      if (isServiceError(error) && Object.keys(error.fieldErrors).length > 0) setErrors(error.fieldErrors)
      setSubmitError(error)
      setSubmitting(false)
    }
  }

  if (loading) return <Loading />
  if (loadError) {
    return (
      <div className="stack narrow">
        <ErrorNotice error={loadError} />
        <Link to="/admin">Back to restaurants</Link>
      </div>
    )
  }

  return (
    <div className="stack narrow">
      <Link to="/admin" className="back-link">
        ← All restaurants
      </Link>
      <section className="card stack-sm">
        <h1>{isEdit ? 'Edit restaurant' : 'Add restaurant'}</h1>
        <form className="form" onSubmit={handleSubmit} noValidate>
          {submitError ? <ErrorNotice error={submitError} /> : null}

          <fieldset className="fieldset">
            <legend>Restaurant</legend>
            <Field label="Name" error={errors.name}>
              {(props) => <input {...props} value={values.name} onChange={(e) => set('name', e.target.value)} />}
            </Field>
            <Field label="Address" error={errors.address}>
              {(props) => <input {...props} value={values.address} onChange={(e) => set('address', e.target.value)} />}
            </Field>
            <Field label="Phone" error={errors.phone}>
              {(props) => (
                <input {...props} type="tel" value={values.phone} onChange={(e) => set('phone', e.target.value)} />
              )}
            </Field>
            <Field
              label="Description"
              error={errors.description}
              hint={`${values.description.length}/${DESCRIPTION_MAX}`}
            >
              {(props) => (
                <textarea
                  {...props}
                  rows={3}
                  value={values.description}
                  onChange={(e) => set('description', e.target.value)}
                />
              )}
            </Field>
            <div className="field-row">
              <Field label="Current wait (minutes)" error={errors.current_wait_minutes}>
                {(props) => (
                  <input
                    {...props}
                    type="number"
                    min={0}
                    max={WAIT_MINUTES_MAX}
                    value={values.current_wait_minutes}
                    onChange={(e) => set('current_wait_minutes', e.target.value)}
                  />
                )}
              </Field>
              <Field label="No-show timeout (minutes)" error={errors.no_show_minutes}>
                {(props) => (
                  <input
                    {...props}
                    type="number"
                    min={NO_SHOW_MINUTES_MIN}
                    max={NO_SHOW_MINUTES_MAX}
                    value={values.no_show_minutes}
                    onChange={(e) => set('no_show_minutes', e.target.value)}
                  />
                )}
              </Field>
            </div>
            <label className="checkbox">
              <input type="checkbox" checked={values.is_active} onChange={(e) => set('is_active', e.target.checked)} />
              Active (visible to the public)
            </label>
          </fieldset>

          <fieldset className="fieldset">
            <legend>Restaurant login</legend>
            <Field label="Username" error={errors.username}>
              {(props) => (
                <input
                  {...props}
                  autoComplete="off"
                  value={values.username}
                  onChange={(e) => set('username', e.target.value)}
                />
              )}
            </Field>
            <Field
              label="Password"
              error={errors.password}
              hint={isEdit ? 'Leave blank to keep the current password.' : 'Not checked at login in this class version.'}
            >
              {(props) => (
                <input
                  {...props}
                  type="password"
                  autoComplete="new-password"
                  value={values.password}
                  onChange={(e) => set('password', e.target.value)}
                />
              )}
            </Field>
          </fieldset>

          <div className="form__actions">
            <Link to="/admin" className="button button--ghost">
              Cancel
            </Link>
            <button type="submit" className="button button--primary" disabled={submitting}>
              {submitting ? 'Saving…' : isEdit ? 'Save changes' : 'Create restaurant'}
            </button>
          </div>
        </form>
      </section>
    </div>
  )
}
