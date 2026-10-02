import React from 'react'

/**
 * Tiny trend shape for stat tiles. No axes, no labels, no tooltip - the tile's
 * own value and delta carry the numbers; this only shows the shape.
 */
export default function Sparkline({
  values = [], width = 104, height = 30, color = 'var(--series-1)', fill = true,
}) {
  const clean = values.map(Number).filter(Number.isFinite)
  if (clean.length < 2) return null

  const min = Math.min(...clean)
  const max = Math.max(...clean)
  const span = max - min || 1
  const stepX = width / (clean.length - 1)

  const toPoint = (value, i) => {
    const x = i * stepX
    // 2px inset keeps the 2px stroke from clipping at the edges.
    const y = height - 2 - ((value - min) / span) * (height - 4)
    return [x, y]
  }

  const line = clean.map((v, i) => {
    const [x, y] = toPoint(v, i)
    return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')

  const area = `${line} L${width},${height} L0,${height} Z`

  return (
    <svg
      className="sparkline"
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      aria-hidden="true"
      style={{ display: 'block', overflow: 'visible' }}
    >
      {fill && <path d={area} fill={color} fillOpacity="0.12" stroke="none" />}
      <path d={line} fill="none" stroke={color} strokeWidth="2"
            strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  )
}
