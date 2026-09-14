import '@testing-library/jest-dom/vitest'
import { cleanup, configure } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

// findBy/waitFor default to 1s, which a loaded CI box or laptop can exceed.
configure({ asyncUtilTimeout: 5000 })

afterEach(() => {
  cleanup()
  globalThis.localStorage?.clear() // absent in node-environment test files
  vi.useRealTimers()
})
