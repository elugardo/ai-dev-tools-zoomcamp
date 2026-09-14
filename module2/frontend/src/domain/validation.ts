// Form validation (spec §8, §22). Each validator returns field -> message; an empty
// object means valid. The mock service runs the same checks the real backend will,
// so bypassing a form still cannot store bad data.

import type { JoinWaitlistInput, RestaurantInput } from './types'

export type FieldErrors<T> = Partial<Record<keyof T, string>>

export const PARTY_SIZE_MIN = 1
export const PARTY_SIZE_MAX = 20
export const NOTES_MAX = 250
export const DESCRIPTION_MAX = 500
export const WAIT_MINUTES_MAX = 240
export const NO_SHOW_MINUTES_MIN = 1
export const NO_SHOW_MINUTES_MAX = 120
export const PASSWORD_MIN = 8

export function hasErrors(errors: object): boolean {
  return Object.keys(errors).length > 0
}

/** Basic format check only: optional +, then digits with common separators, 7–15 digits. */
export function isValidPhone(value: string): boolean {
  const trimmed = value.trim()
  if (!/^\+?[\d\s().-]+$/.test(trimmed)) return false
  const digits = trimmed.replace(/\D/g, '').length
  return digits >= 7 && digits <= 15
}

function isWholeNumberInRange(value: number, min: number, max: number): boolean {
  return Number.isInteger(value) && value >= min && value <= max
}

export function normalizeJoinInput(input: JoinWaitlistInput): JoinWaitlistInput {
  return {
    guest_name: input.guest_name.trim(),
    mobile_phone: input.mobile_phone.trim(),
    party_size: input.party_size,
    notes: input.notes.trim(),
  }
}

export function validateJoinInput(raw: JoinWaitlistInput): FieldErrors<JoinWaitlistInput> {
  const input = normalizeJoinInput(raw)
  const errors: FieldErrors<JoinWaitlistInput> = {}

  if (!input.guest_name) errors.guest_name = 'Name is required.'

  if (!input.mobile_phone) errors.mobile_phone = 'Mobile phone is required.'
  else if (!isValidPhone(input.mobile_phone)) errors.mobile_phone = 'Enter a valid mobile number.'

  if (Number.isNaN(input.party_size)) {
    errors.party_size = 'Party size is required.'
  } else if (!isWholeNumberInRange(input.party_size, PARTY_SIZE_MIN, PARTY_SIZE_MAX)) {
    errors.party_size = `Party size must be a whole number from ${PARTY_SIZE_MIN} to ${PARTY_SIZE_MAX}.`
  }

  if (input.notes.length > NOTES_MAX) errors.notes = `Notes must be ${NOTES_MAX} characters or fewer.`

  return errors
}

export function validateWaitMinutes(value: number): string | null {
  return isWholeNumberInRange(value, 0, WAIT_MINUTES_MAX)
    ? null
    : `Wait must be a whole number from 0 to ${WAIT_MINUTES_MAX} minutes.`
}

export function normalizeRestaurantInput(input: RestaurantInput): RestaurantInput {
  return {
    ...input,
    name: input.name.trim(),
    address: input.address.trim(),
    phone: input.phone.trim(),
    description: input.description.trim(),
    username: input.username.trim().toLowerCase(),
  }
}

/**
 * `isNew` is true when creating a restaurant, where a password is required. On
 * edit a blank password keeps the current one; a non-blank one must still meet
 * the minimum length.
 */
export function validateRestaurantInput(
  raw: RestaurantInput,
  { isNew }: { isNew: boolean },
): FieldErrors<RestaurantInput> {
  const input = normalizeRestaurantInput(raw)
  const errors: FieldErrors<RestaurantInput> = {}

  if (!input.name) errors.name = 'Name is required.'
  if (!input.address) errors.address = 'Address is required.'

  if (!input.phone) errors.phone = 'Phone is required.'
  else if (!isValidPhone(input.phone)) errors.phone = 'Enter a valid phone number.'

  if (input.description.length > DESCRIPTION_MAX) {
    errors.description = `Description must be ${DESCRIPTION_MAX} characters or fewer.`
  }

  const waitMessage = validateWaitMinutes(input.current_wait_minutes)
  if (waitMessage) errors.current_wait_minutes = waitMessage

  if (!isWholeNumberInRange(input.no_show_minutes, NO_SHOW_MINUTES_MIN, NO_SHOW_MINUTES_MAX)) {
    errors.no_show_minutes = `No-show timeout must be a whole number from ${NO_SHOW_MINUTES_MIN} to ${NO_SHOW_MINUTES_MAX} minutes.`
  }

  if (!input.username) errors.username = 'Username is required.'
  else if (!/^[a-z0-9_-]+$/.test(input.username)) {
    errors.username = 'Username may only use letters, numbers, - and _.'
  }

  // Passwords are never trimmed: spaces are part of what the person typed.
  if (!input.password) {
    if (isNew) errors.password = 'Password is required.'
  } else if (input.password.length < PASSWORD_MIN) {
    errors.password = `Password must be at least ${PASSWORD_MIN} characters.`
  }

  return errors
}

export const RESTAURANT_DEFAULTS: RestaurantInput = {
  name: '',
  address: '',
  phone: '',
  description: '',
  current_wait_minutes: 30,
  no_show_minutes: 10,
  is_active: true,
  username: '',
  password: '',
}
