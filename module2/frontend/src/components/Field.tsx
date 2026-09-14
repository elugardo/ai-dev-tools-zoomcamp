import { useId, type ReactNode } from 'react'

interface FieldProps {
  label: string
  error?: string
  hint?: string
  children: (props: { id: string; 'aria-invalid': boolean; 'aria-describedby'?: string }) => ReactNode
}

/** A labelled form control with its hint and error message wired up for screen readers. */
export function Field({ label, error, hint, children }: FieldProps) {
  const id = useId()
  const messageId = `${id}-message`
  return (
    <div className={`field${error ? ' field--error' : ''}`}>
      <label htmlFor={id}>{label}</label>
      {children({ id, 'aria-invalid': Boolean(error), 'aria-describedby': error || hint ? messageId : undefined })}
      {error ? (
        <p className="field__error" id={messageId}>
          {error}
        </p>
      ) : hint ? (
        <p className="field__hint" id={messageId}>
          {hint}
        </p>
      ) : null}
    </div>
  )
}
