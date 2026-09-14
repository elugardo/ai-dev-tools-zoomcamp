/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: { port: 3417, strictPort: true },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    restoreMocks: true,
    // Full-app renders with jsdom are slow on some machines when files run in parallel.
    testTimeout: 20_000,
    // Each worker holds its own jsdom; one per core starves memory-constrained machines
    // and workers then time out on startup.
    maxWorkers: 4,
  },
})
