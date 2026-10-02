import React, { useMemo, useState } from 'react'

import { clockTime, severityColor } from '../utils/format'
import { niceTicks, scaleLinear, useChartWidth } from './useChartWidth'

const MARGIN = { top: 14, right: 18, bottom: 26, left: 40 }

/**
 * Anomaly score over time, one dot per observation window.
 *
 * Routine windows are recessive dots; flagged ones use the status palette sized
 * up, with the decision threshold drawn as a labelled reference line - so the
 * separation is visible as position and size, not colour alone. Clicking a dot
 * opens the event detail.
 *
 * `threshold` is the cut the model actually applied, as reported by the API.
 * When it is unknown the line is omitted rather than drawn at a guessed value.
 */
export default function AnomalyTimeline({ events = [], threshold = null, height = 220, onSelect }) {
  const [containerRef, width] = useChartWidth()
  const [hover, setHover] = useState(null)
  const hasThreshold = Number.isFinite(threshold)

  const points = useMemo(
    () =>
      events
        .map((event) => ({
          event,
          time: new Date(event.timestamp).getTime(),
          score: Number(event.anomaly_score),
          isAnomaly: event.status === 'ANOMALY',
        }))
        .filter((p) => Number.isFinite(p.time) && Number.isFinite(p.score))
        .sort((a, b) => a.time - b.time),
    [events],
  )

  if (!points.length) return null

  const innerW = Math.max(120, width - MARGIN.left - MARGIN.right)
  const innerH = Math.max(80, height - MARGIN.top - MARGIN.bottom)

  const tMin = points[0].time
  const tMax = points[points.length - 1].time
  const x = scaleLinear(tMin, tMax, 0, innerW)
  const y = scaleLinear(0, 100, innerH, 0)
  const yTicks = niceTicks(0, 100, 4)

  const xTicks = [0, 0.25, 0.5, 0.75, 1].map((f) => tMin + (tMax - tMin) * f)

  return (
    <div className="chart" ref={containerRef}>
      <div className="chart-legend">
        <span className="legend-item">
          <span className="legend-swatch" style={{ background: 'var(--text-muted)', width: 8, height: 8, borderRadius: '50%' }} />
          Routine
        </span>
        <span className="legend-item">
          <span className="legend-swatch" style={{ background: 'var(--status-critical)', width: 11, height: 11, borderRadius: '50%' }} />
          Anomaly
        </span>
        {hasThreshold && (
          <span className="legend-item" style={{ marginLeft: 'auto', color: 'var(--text-muted)' }}>
            decision threshold {threshold}
          </span>
        )}
      </div>

      <svg
        viewBox={`0 0 ${width} ${height}`}
        height={height}
        role="img"
        aria-label={`Anomaly score over time for ${points.length} observation windows`}
      >
        <g transform={`translate(${MARGIN.left},${MARGIN.top})`}>
          <g className="chart-grid">
            {yTicks.map((t) => (
              <line key={t} x1={0} x2={innerW} y1={y(t)} y2={y(t)} />
            ))}
          </g>

          {yTicks.map((t) => (
            <text key={t} className="chart-tick" x={-9} y={y(t)} dy="0.32em" textAnchor="end">
              {t}
            </text>
          ))}

          {/* decision threshold */}
          {hasThreshold && (
            <line
              x1={0} x2={innerW} y1={y(threshold)} y2={y(threshold)}
              stroke="var(--status-serious)" strokeWidth="1.5" strokeDasharray="5 4" opacity="0.75"
            />
          )}

          {points.map((p, i) => (
            <circle
              key={p.event.event_id || i}
              cx={x(p.time)}
              cy={y(p.score)}
              r={p.isAnomaly ? 5.5 : 2.6}
              fill={p.isAnomaly ? severityColor(p.event.severity) : 'var(--text-muted)'}
              fillOpacity={p.isAnomaly ? 1 : 0.5}
              stroke={p.isAnomaly ? 'var(--bg-surface)' : 'none'}
              strokeWidth={p.isAnomaly ? 2 : 0}
              style={{ cursor: onSelect ? 'pointer' : 'default' }}
              onMouseEnter={() => setHover(p)}
              onMouseLeave={() => setHover(null)}
              onClick={() => onSelect?.(p.event)}
            />
          ))}

          <g className="chart-axis">
            <line x1={0} x2={innerW} y1={innerH} y2={innerH} />
          </g>

          {xTicks.map((t) => (
            <text key={t} className="chart-tick" x={x(t)} y={innerH + 16} textAnchor="middle">
              {clockTime(new Date(t).toISOString())}
            </text>
          ))}
        </g>
      </svg>

      {hover && (
        <div
          className="chart-tooltip"
          style={{ left: MARGIN.left + x(hover.time), top: MARGIN.top + y(hover.score) }}
        >
          <div className="chart-tooltip-title">{hover.event.event_type}</div>
          <div className="chart-tooltip-row">
            <span
              className="swatch"
              style={{
                background: hover.isAnomaly ? severityColor(hover.event.severity) : 'var(--text-muted)',
              }}
            />
            Score
            <span className="val">{hover.score.toFixed(1)}</span>
          </div>
          <div className="chart-tooltip-row">
            {hover.isAnomaly ? hover.event.severity : 'Routine'}
            <span className="val">{clockTime(hover.event.timestamp)}</span>
          </div>
        </div>
      )}
    </div>
  )
}
