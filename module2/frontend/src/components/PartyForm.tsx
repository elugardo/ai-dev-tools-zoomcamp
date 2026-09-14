import { useState, type FormEvent } from 'react'
import type { JoinWaitlistInput } from '../domain/types'
import { hasErrors, NOTES_MAX, PARTY_SIZE_MAX, PARTY_SIZE_MIN, validateJoinInput, type FieldErrors } from '../domain/validation'
import { isServiceError } from '../services/errors'
import { ErrorNotice } from './Feedback'
import { Field } from './Field'

interface PartyFormProps {
  submitLabel: string
  onSubmit: (input: JoinWaitlistInput) => Promise<void>
  disabled?: boolean
  onCancel?: () => void
}

/** Name, mobile, party size, notes — shared by the eater join form and staff walk-ins. */
export function PartyForm({ submitLabel, onSubmit, disabled = false, onCancel }: PartyFormProps) {
  const [values, setValues] = useState({ guest_name: '', mobile_phone: '', party_size: '2', notes: '' })
  const [errors, setErrors] = useState<FieldErrors<JoinWaitlistInput>>({})
  const [submitError, setSubmitError] = useState<unknown>(null)
  const [submitting, setSubmitting] = useState(false)

  const set = (field: keyof typeof values) => (value: string) => setValues((v) => ({ ...v, [field]: value }))

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    const input: JoinWaitlistInput = {
      ...values,
      party_size: values.party_size.trim() === '' ? NaN : Number(values.party_size),
    }
    const found = validateJoinInput(input)
    setErrors(found)
    setSubmitError(null)
    if (hasErrors(found)) return

    setSubmitting(true)
    try {
      await onSubmit(input)
    } catch (error) {
      if (isServiceError(error, 'VALIDATION')) setErrors(error.fieldErrors)
      setSubmitError(error)
      setSubmitting(false)
    }
  }

  return (
    <form className="form" onSubmit={handleSubmit} noValidate>
      {submitError ? <ErrorNotice error={submitError} /> : null}
      <Field label="Name" error={errors.guest_name}>
        {(props) => (
          <input {...props} autoComplete="name" value={values.guest_name} onChange={(e) => set('guest_name')(e.target.value)} />
        )}
      </Field>
      <Field label="Mobile phone" error={errors.mobile_phone}>
        {(props) => (
          <input
            {...props}
            type="tel"
            autoComplete="tel"
            inputMode="tel"
            value={values.mobile_phone}
            onChange={(e) => set('mobile_phone')(e.target.value)}
          />
        )}
      </Field>
      <Field label="Party size" error={errors.party_size}>
        {(props) => (
          <input
            {...props}
            type="number"
            inputMode="numeric"
            min={PARTY_SIZE_MIN}
            max={PARTY_SIZE_MAX}
            step={1}
            value={values.party_size}
            onChange={(e) => set('party_size')(e.target.value)}
          />
        )}
      </Field>
      <Field label="Notes (optional)" error={errors.notes} hint={`${values.notes.length}/${NOTES_MAX}`}>
        {(props) => <textarea {...props} rows={2} value={values.notes} onChange={(e) => set('notes')(e.target.value)} />}
      </Field>
      <div className="form__actions">
        {onCancel ? (
          <button type="button" className="button button--ghost" onClick={onCancel}>
            Cancel
          </button>
        ) : null}
        <button type="submit" className="button button--primary button--large" disabled={disabled || submitting}>
          {submitting ? 'Saving…' : submitLabel}
        </button>
      </div>
    </form>
  )
}
