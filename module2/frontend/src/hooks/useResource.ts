import { useCallback, useEffect, useRef, useState, type DependencyList } from 'react'

export interface Resource<T> {
  data: T | null
  /** The last failure. Kept alongside stale `data` when a background poll fails. */
  error: unknown
  loading: boolean
  reload: () => Promise<void>
  setData: (data: T) => void
}

/**
 * Loads data from the services layer, reloads when `deps` change, and optionally
 * polls every `pollMs` (spec §11 — polling, not WebSockets). Pass `pollMs: null`
 * to stop polling, e.g. once a waitlist entry is finished.
 */
export function useResource<T>(
  load: () => Promise<T>,
  deps: DependencyList,
  pollMs: number | null = null,
): Resource<T> {
  const [state, setState] = useState<{ data: T | null; error: unknown; loading: boolean }>({
    data: null,
    error: null,
    loading: true,
  })
  const loadRef = useRef(load)
  const generation = useRef(0)

  useEffect(() => {
    loadRef.current = load
  })

  const reload = useCallback(async () => {
    const requestGeneration = generation.current
    try {
      const data = await loadRef.current()
      if (requestGeneration === generation.current) setState({ data, error: null, loading: false })
    } catch (error) {
      if (requestGeneration === generation.current) setState((s) => ({ ...s, error, loading: false }))
    }
  }, [])

  useEffect(() => {
    generation.current += 1
    setState({ data: null, error: null, loading: true })
    void reload()
    return () => {
      generation.current += 1
    }
  }, deps)

  useEffect(() => {
    if (pollMs === null) return
    const id = setInterval(() => void reload(), pollMs)
    return () => clearInterval(id)
  }, [pollMs, reload])

  const setData = useCallback((data: T) => setState({ data, error: null, loading: false }), [])

  return { ...state, reload, setData }
}
