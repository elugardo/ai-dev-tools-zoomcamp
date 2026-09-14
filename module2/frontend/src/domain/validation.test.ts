import { describe, expect, it } from 'vitest'
import type { JoinWaitlistInput, RestaurantInput } from './types'
import { isValidPhone, RESTAURANT_DEFAULTS, validateJoinInput, validateRestaurantInput } from './validation'

const validJoin: JoinWaitlistInput = { guest_name: 'Marcos', mobile_phone: '555-010-6677', party_size: 4, notes: '' }

describe('join waitlist validation', () => {
  it('accepts a complete party', () => {
    expect(validateJoinInput(validJoin)).toEqual({})
  })

  it('requires name, mobile and party size', () => {
    const errors = validateJoinInput({ guest_name: '', mobile_phone: '', party_size: NaN, notes: '' })
    expect(Object.keys(errors).sort()).toEqual(['guest_name', 'mobile_phone', 'party_size'])
  })

  it('treats a whitespace-only name as missing', () => {
    expect(validateJoinInput({ ...validJoin, guest_name: '   ' }).guest_name).toBe('Name is required.')
  })

  it.each([0, 21, 2.5, -1])('rejects party size %s', (party_size) => {
    expect(validateJoinInput({ ...validJoin, party_size }).party_size).toBeDefined()
  })

  it.each([1, 20])('accepts party size %s at the boundary', (party_size) => {
    expect(validateJoinInput({ ...validJoin, party_size })).toEqual({})
  })

  it('allows 250 characters of notes and rejects 251', () => {
    expect(validateJoinInput({ ...validJoin, notes: 'x'.repeat(250) })).toEqual({})
    expect(validateJoinInput({ ...validJoin, notes: 'x'.repeat(251) }).notes).toBeDefined()
  })
})

describe('phone format', () => {
  it.each(['555-010-6677', '(555) 201-4455', '+44 20 7946 0958', '5550106677'])('accepts %s', (value) => {
    expect(isValidPhone(value)).toBe(true)
  })

  it.each(['call me', '12345', '555-CALL-NOW', '+1 555 010 6677 1234 5'])('rejects %s', (value) => {
    expect(isValidPhone(value)).toBe(false)
  })
})

describe('restaurant validation', () => {
  const valid: RestaurantInput = {
    ...RESTAURANT_DEFAULTS,
    name: 'Bluebird Cafe',
    address: '123 Main Street',
    phone: '(555) 201-4455',
    username: 'bluebird',
    password: 'bluebird-pass',
  }

  it('accepts a restaurant built on the spec defaults', () => {
    expect(RESTAURANT_DEFAULTS.current_wait_minutes).toBe(30)
    expect(RESTAURANT_DEFAULTS.no_show_minutes).toBe(10)
    expect(RESTAURANT_DEFAULTS.is_active).toBe(true)
    expect(validateRestaurantInput(valid, { isNew: true })).toEqual({})
  })

  it('requires name, address, phone, username and, for a new restaurant, a password', () => {
    const errors = validateRestaurantInput(RESTAURANT_DEFAULTS, { isNew: true })
    expect(Object.keys(errors).sort()).toEqual(['address', 'name', 'password', 'phone', 'username'])
  })

  it('lets an edit leave the password blank to keep the current one', () => {
    expect(validateRestaurantInput({ ...valid, password: '' }, { isNew: false })).toEqual({})
  })

  it('requires at least 8 characters for a new or changed password', () => {
    const tooShort = 'Password must be at least 8 characters.'
    expect(validateRestaurantInput({ ...valid, password: 'short12' }, { isNew: true }).password).toBe(tooShort)
    expect(validateRestaurantInput({ ...valid, password: 'short12' }, { isNew: false }).password).toBe(tooShort)
    expect(validateRestaurantInput({ ...valid, password: '12345678' }, { isNew: true })).toEqual({})
  })

  it('rejects negative waits and a zero no-show timeout', () => {
    const errors = validateRestaurantInput({ ...valid, current_wait_minutes: -5, no_show_minutes: 0 }, { isNew: true })
    expect(errors.current_wait_minutes).toBeDefined()
    expect(errors.no_show_minutes).toBeDefined()
  })

  it('rejects a username with spaces', () => {
    expect(validateRestaurantInput({ ...valid, username: 'blue bird' }, { isNew: true }).username).toBeDefined()
  })

  it('enforces the database column lengths at the boundary', () => {
    const at = { ...valid, name: 'n'.repeat(120), address: 'a'.repeat(200), username: 'u'.repeat(64), password: 'p'.repeat(128) }
    expect(validateRestaurantInput(at, { isNew: true })).toEqual({})

    const over = { ...valid, name: 'n'.repeat(121), address: 'a'.repeat(201), username: 'u'.repeat(65), password: 'p'.repeat(129) }
    expect(Object.keys(validateRestaurantInput(over, { isNew: true })).sort()).toEqual(['address', 'name', 'password', 'username'])
  })
})

describe('length limits on the join form', () => {
  it('allows a 100-character name and rejects 101', () => {
    expect(validateJoinInput({ ...validJoin, guest_name: 'x'.repeat(100) })).toEqual({})
    expect(validateJoinInput({ ...validJoin, guest_name: 'x'.repeat(101) }).guest_name).toBe(
      'Name must be 100 characters or fewer.',
    )
  })

  it('rejects a phone number longer than 32 characters even with a valid digit count', () => {
    expect(isValidPhone('5  5  5  5  5  5  5  5  5  5  5  5')).toBe(false)
  })
})
