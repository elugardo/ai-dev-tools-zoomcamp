// Errors that cross the services boundary. Every implementation throws only
// ServiceError, with a message already fit to show a guest or staff member, so
// the UI never has to display a raw exception (spec §31).

export type ServiceErrorCode =
  | 'UNAVAILABLE' // backend unreachable
  | 'NOT_FOUND' // restaurant, token, or entry does not exist
  | 'VALIDATION' // input rejected; see fieldErrors
  | 'WAITLIST_CLOSED' // restaurant inactive or online joining paused
  | 'ENTRY_CLOSED' // entry already seated, canceled, or no-show
  | 'UNAUTHORIZED' // no session, or an unknown user
  | 'FORBIDDEN' // signed in, but the wrong role
  | 'CONFLICT' // e.g. username already taken

export class ServiceError extends Error {
  readonly code: ServiceErrorCode
  readonly fieldErrors: Record<string, string>

  constructor(code: ServiceErrorCode, message: string, fieldErrors: Record<string, string> = {}) {
    super(message)
    this.name = 'ServiceError'
    this.code = code
    this.fieldErrors = fieldErrors
  }
}

const GENERIC_MESSAGE = 'Something went wrong. Please try again.'

/** The text to show for any thrown value. Unknown errors never leak their details. */
export function friendlyErrorMessage(error: unknown): string {
  return error instanceof ServiceError ? error.message : GENERIC_MESSAGE
}

export function isServiceError(error: unknown, code?: ServiceErrorCode): error is ServiceError {
  return error instanceof ServiceError && (code === undefined || error.code === code)
}
