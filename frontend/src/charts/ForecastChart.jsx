import React, { useMemo, useState } from 'react'

import { money, shortDate } from '../utils/format'
import { niceTicks, scaleLinear, useChartWidth } from './useChartWidth'

const MARGIN = { top: 14, right: 18, bottom: 26, left: 56 }

/**
 * Historical spend joined to the Prophet forecast with its confidence band.
 *
 * One y-axis, two series (observed = blue slot 1, forecast = orange slot 2,
 * dashed). The band is the forecast's own interval, drawn beneath both lines so
 * neither is obscured. A crosshair tooltip is the default interaction layer.
 */
export default function ForecastChart({
  history = [],
  forecast = [],
  height = 280,
  intervalLabel = 'forecast interval',
}) {
  const [containerRef, width] = useChartWidth()
  const [hover, setHover] = useState(null)

  const model = useMemo(() => {
    const actual = history
      .map((d) => ({ date: d.date, value: Number(d.cost) }))
      .filter((d) => d.date && Number.isFinite(d.value))
    const predicted = forecast
      .map((d) => ({
        date: d.date,
        value: Number(d.predicted_cost),
        lower: Number(d.lower_bound),
        upper: Number(d.upper_bound),
      }))
      .filter((d) => d.date && Number.isFinite(d.value))

    // One combined index so both series share a single x scale.
    const points = [
      ...actual.map((d) => ({ ...d, kind: 'actual' })),
      ...predicted.map((d) => ({ ...d, kind: 'forecast' })),
    ]
    if (!points.length) return null

    const values = [
      ...actual.map((d) => d.value),
      ...predicted.flatMap((d) => [d.lower, d.upper, d.value]),
    ].filter(Number.isFinite)

    const min = Math.min(...values)
    const max = Math.max(...values)
    // Pad the domain so the band never touches the frame.
    const pad = (max - min) * 0.12 || max * 0.1 || 1
    return {
      points,
      actualCount: actual.length,
      yMin: Math.max(0, min - pad),
      yMax: max + pad,
    }
  }, [history, forecast])

  if (!model) return null

  const innerW = Math.max(120, width - MARGIN.left - MARGIN.right)
  const innerH = Math.max(80, height - MARGIN.top - MARGIN.bottom)
  const { points, actualCount, yMin, yMax } = model

  const x = scaleLinear(0, Math.max(1, points.length - 1), 0, innerW)
  const y = scaleLinear(yMin, yMax, innerH, 0)
  const yTicks = niceTicks(yMin, yMax, 4)

  const actualPath = points
    .slice(0, actualCount)
    .map((p, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(2)},${y(p.value).toFixed(2)}`)
    .join(' ')

  // Area beneath the observed line. It carries no extra information, so it
  // fades to nothing rather than competing with the line for attention.
  const actualArea = actualCount
    ? `${actualPath} L${x(actualCount - 1).toFixed(2)},${innerH} L0,${innerH} Z`
    : null

  // Start the forecast line at the last actual point so the series joins visually.
  const forecastIdx = Math.max(0, actualCount - 1)
  const forecastPoints = points.slice(forecastIdx)
  const forecastPath = forecastPoints
    .map((p, i) => `${i === 0 ? 'M' : 'L'}${x(forecastIdx + i).toFixed(2)},${y(p.value).toFixed(2)}`)
    .join(' ')

  // Band: upper edge forwards, lower edge back. Anchored on the join point so
  // there is no gap between the actual line and the band.
  const banded = forecastPoints.filter((p) => Number.isFinite(p.upper))
  const bandPath = banded.length
    ? [
        ...banded.map((p, i) => {
          const idx = points.indexOf(p)
          return `${i === 0 ? 'M' : 'L'}${x(idx).toFixed(2)},${y(p.upper).toFixed(2)}`
        }),
        ...banded
          .slice()
          .reverse()
          .map((p) => {
            const idx = points.indexOf(p)
            return `L${x(idx).toFixed(2)},${y(p.lower).toFixed(2)}`
          }),
        'Z',
      ].join(' ')
    : null

  const xTickIdx = []
  const tickStep = Math.max(1, Math.round(points.length / 6))
  for (let i = 0; i < points.length; i += tickStep) xTickIdx.push(i)
  if (xTickIdx[xTickIdx.length - 1] !== points.length - 1) xTickIdx.push(points.length - 1)

  const handleMove = (event) => {
    const rect = event.currentTarget.getBoundingClientRect()
    const px = event.clientX - rect.left - MARGIN.left
    const idx = Math.round((px / innerW) * (points.length - 1))
    const clamped = Math.max(0, Math.min(points.length - 1, idx))
    setHover({ index: clamped, point: points[clamped] })
  }

  const hoveredX = hover ? MARGIN.left + x(hover.index) : 0

  return (
    <div className="chart" ref={containerRef}>
      <div className="chart-legend">
        <span className="legend-item">
          <span className="legend-swatch line" style={{ background: 'var(--series-1)' }} />
          Observed spend
        </span>
        <span className="legend-item" style={{ color: 'var(--series-2)' }}>
          <span className="legend-swatch dashed" />
          <span style={{ color: 'var(--text-secondary)' }}>Prophet forecast</span>
        </span>
        <span className="legend-item">
          <span className="legend-swatch band" style={{ background: 'var(--series-2)' }} />
          {intervalLabel}
        </span>
      </div>

      <svg
        viewBox={`0 0 ${width} ${height}`}
        height={height}
        role="img"
        aria-label={`Daily cloud spend with ${forecast.length}-day Prophet forecast and confidence interval`}
        onMouseMove={handleMove}
        onMouseLeave={() => setHover(null)}
      >
        <defs>
          <linearGradient id="cg-actual-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--series-1)" stopOpacity="0.28" />
            <stop offset="100%" stopColor="var(--series-1)" stopOpacity="0" />
          </linearGradient>
          {/* Bloom is painted as a separate soft pass beneath the line, so the
              line itself is never blurred. */}
          <filter id="cg-line-glow" x="-20%" y="-60%" width="140%" height="220%">
            <feGaussianBlur stdDeviation="2.6" />
          </filter>
        </defs>

        <g transform={`translate(${MARGIN.left},${MARGIN.top})`}>
          {/* recessive gridlines */}
          <g className="chart-grid">
            {yTicks.map((t) => (
              <line key={t} x1={0} x2={innerW} y1={y(t)} y2={y(t)} />
            ))}
          </g>

          {/* y ticks */}
          {yTicks.map((t) => (
            <text key={t} className="chart-tick" x={-10} y={y(t)} dy="0.32em" textAnchor="end">
              {money(t, { compact: true })}
            </text>
          ))}

          {/* forecast boundary */}
          {actualCount > 0 && actualCount < points.length && (
            <>
              <line
                x1={x(forecastIdx)} x2={x(forecastIdx)} y1={0} y2={innerH}
                stroke="var(--border-strong)" strokeWidth="1" strokeDasharray="4 4"
              />
              <text className="chart-label is-subtle" x={x(forecastIdx) + 6} y={12}>
                forecast →
              </text>
            </>
          )}

          {bandPath && <path d={bandPath} fill="var(--series-2-soft)" stroke="none" />}

          {actualArea && <path d={actualArea} fill="url(#cg-actual-fill)" stroke="none" />}

          <path d={actualPath} fill="none" stroke="var(--series-1)" strokeWidth="2.5"
                strokeLinejoin="round" strokeLinecap="round"
                opacity="0.4" filter="url(#cg-line-glow)" />
          <path d={actualPath} fill="none" stroke="var(--series-1)" strokeWidth="2"
                strokeLinejoin="round" strokeLinecap="round" />
          <path d={forecastPath} fill="none" stroke="var(--series-2)" strokeWidth="2"
                strokeDasharray="5 4" strokeLinejoin="round" strokeLinecap="round" />

          {/* The join between observed and predicted, marked. */}
          {actualCount > 0 && (
            <circle
              cx={x(actualCount - 1)} cy={y(points[actualCount - 1].value)} r="3.5"
              fill="var(--series-1)" stroke="var(--bg-surface)" strokeWidth="2"
            />
          )}

          {/* crosshair */}
          {hover && (
            <>
              <line className="chart-crosshair" x1={x(hover.index)} x2={x(hover.index)} y1={0} y2={innerH} />
              <circle
                cx={x(hover.index)} cy={y(hover.point.value)} r="4.5"
                fill={hover.point.kind === 'actual' ? 'var(--series-1)' : 'var(--series-2)'}
                stroke="var(--bg-surface)" strokeWidth="2"
              />
            </>
          )}

          <g className="chart-axis">
            <line x1={0} x2={innerW} y1={innerH} y2={innerH} />
          </g>

          {xTickIdx.map((i) => (
            <text key={i} className="chart-tick" x={x(i)} y={innerH + 16} textAnchor="middle">
              {shortDate(points[i].date)}
            </text>
          ))}
        </g>
      </svg>

      {hover && (
        <div
          className="chart-tooltip"
          style={{ left: hoveredX, top: MARGIN.top + y(hover.point.value) }}
        >
          <div className="chart-tooltip-title">{shortDate(hover.point.date)}</div>
          <div className="chart-tooltip-row">
            <span
              className="swatch"
              style={{
                background: hover.point.kind === 'actual' ? 'var(--series-1)' : 'var(--series-2)',
              }}
            />
            {hover.point.kind === 'actual' ? 'Observed' : 'Predicted'}
            <span className="val">{money(hover.point.value, { decimals: 2 })}</span>
          </div>
          {hover.point.kind === 'forecast' && Number.isFinite(hover.point.lower) && (
            <div className="chart-tooltip-row">
              <span className="swatch" style={{ background: 'var(--series-2-soft)' }} />
              Range
              <span className="val">
                {money(hover.point.lower)} – {money(hover.point.upper)}
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
