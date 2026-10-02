import { useEffect, useRef, useState } from 'react'

/**
 * Measure a container so SVG charts can be responsive without a chart library.
 * Falls back to a sensible width when ResizeObserver is unavailable (jsdom).
 */
export function useChartWidth(fallback = 640) {
  const ref = useRef(null)
  const [width, setWidth] = useState(fallback)

  useEffect(() => {
    const node = ref.current
    if (!node) return undefined

    const measure = () => {
      const next = node.getBoundingClientRect().width
      if (next > 0) setWidth(next)
    }
    measure()

    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', measure)
      return () => window.removeEventListener('resize', measure)
    }
    const observer = new ResizeObserver(measure)
    observer.observe(node)
    return () => observer.disconnect()
  }, [])

  return [ref, width]
}

/** Linear scale factory: domain -> pixel range. */
export function scaleLinear(d0, d1, r0, r1) {
  const span = d1 - d0 || 1
  return (value) => r0 + ((value - d0) / span) * (r1 - r0)
}

/** Pick ~`count` round tick values covering [min, max]. */
export function niceTicks(min, max, count = 4) {
  if (!Number.isFinite(min) || !Number.isFinite(max) || min === max) {
    return [min || 0]
  }
  const rawStep = (max - min) / count
  const magnitude = 10 ** Math.floor(Math.log10(rawStep))
  const normalized = rawStep / magnitude
  const step = (normalized >= 5 ? 10 : normalized >= 2 ? 5 : normalized >= 1 ? 2 : 1) * magnitude

  const ticks = []
  const start = Math.ceil(min / step) * step
  for (let v = start; v <= max + step * 0.001; v += step) {
    ticks.push(Number(v.toFixed(10)))
  }
  return ticks.length ? ticks : [min, max]
}
