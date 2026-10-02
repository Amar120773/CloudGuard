import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Fetch a resource with background refresh and stale-data retention.
 *
 * The important behaviour for a live dashboard: a refetch never blanks the
 * screen. Previous data stays mounted while `refreshing` is true, and if the
 * refetch fails the old data stays visible with `isStale` set, so the UI can say
 * "last successful update 3m ago" instead of flashing an error over good data.
 */
export function useResource(fetcher, { deps = [], intervalMs = 0, enabled = true, immediate = true } = {}) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(Boolean(enabled && immediate))
  const [refreshing, setRefreshing] = useState(false)
  const [isStale, setIsStale] = useState(false)
  const [lastUpdated, setLastUpdated] = useState(null)

  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher

  const hasDataRef = useRef(false)
  const mountedRef = useRef(true)
  const inFlightRef = useRef(null)

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      inFlightRef.current?.abort()
    }
  }, [])

  const load = useCallback(async ({ background = false } = {}) => {
    // Supersede any request still in flight so responses cannot land out of order.
    inFlightRef.current?.abort()
    const controller = new AbortController()
    inFlightRef.current = controller

    if (background && hasDataRef.current) setRefreshing(true)
    else setLoading(true)

    try {
      const result = await fetcherRef.current({ signal: controller.signal })
      if (!mountedRef.current || controller.signal.aborted) return result
      setData(result)
      hasDataRef.current = true
      setError(null)
      setIsStale(false)
      setLastUpdated(new Date())
      return result
    } catch (err) {
      if (controller.signal.aborted || err?.name === 'AbortError') return null
      if (!mountedRef.current) return null
      // Keep showing good data; just mark it stale.
      if (hasDataRef.current) setIsStale(true)
      else setData(null)
      setError(err)
      return null
    } finally {
      if (mountedRef.current && !controller.signal.aborted) {
        setLoading(false)
        setRefreshing(false)
      }
    }
  }, [])

  // Initial load + reload when deps change.
  useEffect(() => {
    if (!enabled) {
      setLoading(false)
      return
    }
    load({ background: hasDataRef.current })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, load, ...deps])

  // Background polling.
  useEffect(() => {
    if (!enabled || !intervalMs) return undefined
    const id = setInterval(() => {
      // Pause polling while the tab is hidden - no point burning requests.
      if (typeof document !== 'undefined' && document.hidden) return
      load({ background: true })
    }, intervalMs)
    return () => clearInterval(id)
  }, [enabled, intervalMs, load])

  return {
    data,
    error,
    loading,
    refreshing,
    isStale,
    lastUpdated,
    hasData: data != null,
    reload: useCallback(() => load({ background: true }), [load]),
    reloadBlocking: useCallback(() => load({ background: false }), [load]),
  }
}
