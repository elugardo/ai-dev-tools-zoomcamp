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
  }

  it('accepts a restaurant built on the spec defaults', () => {
    expect(RESTAURANT_DEFAULTS.current_wait_minutes).toBe(30)
    expect(RESTAURANT_DEFAULTS.no_show_minutes).toBe(10)
    expect(RESTAURANT_DEFAULTS.is_active).toBe(true)
    expect(validateRestaurantInput(valid)).toEqual({})
  })

  it('requires name, address, phone and username', () => {
    const errors = validateRestaurantInput(RESTAURANT_DEFAULTS)
    expect(Object.keys(errors).sort()).toEqual(['address', 'name', 'phone', 'username'])
  })

  it('rejects negative waits and a zero no-show timeout', () => {
    const errors = validateRestaurantInput({ ...valid, current_wait_minutes: -5, no_show_minutes: 0 })
    expect(errors.current_wait_minutes).toBeDefined()
    expect(errors.no_show_minutes).toBeDefined()
  })

  it('rejects a username with spaces', () => {
    expect(validateRestaurantInput({ ...valid, username: 'blue bird' }).username).toBeDefined()
  })
})
