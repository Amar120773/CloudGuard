import React from 'react'
import { AlertTriangle } from 'lucide-react'

import { money, percent, shortService } from '../utils/format'
import { Delta } from '../components/Primitives'

/**
 * Per-service spend as horizontal magnitude bars.
 *
 * One measure across many categories, so this is a *single* hue - bar length
 * carries the magnitude. Eight different hues here would be a rainbow that
 * encodes nothing. Risk-flagged services switch to the status colour and carry
 * an icon plus a "risk" label, so the distinction is never colour-alone.
 */
export default function ServiceBars({ services = [], metric = 'total_cost', limit = 8, showTrend = true }) {
  // Sort by the metric being drawn. The API may order by a different figure
  // (projection vs total), and a magnitude chart whose bars are not in length
  // order is simply misread.
  const rows = services
    // Number(null) is 0, which is finite - so null must be excluded explicitly,
    // or a service with no projection draws as a zero-length bar.
    .filter((s) => s?.[metric] != null && Number.isFinite(Number(s[metric])))
    .slice()
    .sort((a, b) => (Number(b[metric]) || 0) - (Number(a[metric]) || 0))
    .slice(0, limit)
  if (!rows.length) return null

  const max = Math.max(...rows.map((s) => Number(s[metric]) || 0), 1)

  return (
    <div className="hbars">
      {rows.map((service) => {
        const value = Number(service[metric]) || 0
        const pct = (value / max) * 100
        const isRisk = Boolean(service.is_risk)

        return (
          <div className="hbar-row" key={service.service}>
            <span className="hbar-name" title={service.service}>
              {isRisk && (
                <AlertTriangle
                  size={12}
                  style={{ color: 'var(--status-serious)', flexShrink: 0 }}
                  aria-hidden="true"
                />
              )}
              {shortService(service.service)}
              {isRisk && <span className="sr-only"> (flagged as a cost risk)</span>}
              <span style={{ color: 'var(--text-muted)', fontSize: '0.76rem' }}>
                {percent(service.share_pct, 0)}
              </span>
            </span>

            <span className="hbar-value">
              {money(value, { decimals: 0 })}
              {showTrend && service.trend_pct != null && (
                <span style={{ marginLeft: 8, fontWeight: 500 }}>
                  <Delta value={service.trend_pct} />
                </span>
              )}
            </span>

            <div className="hbar-track">
              <div
                className="hbar-fill"
                style={{
                  width: `${Math.max(1, pct)}%`,
                  background: isRisk ? 'var(--status-serious)' : 'var(--series-1)',
                }}
              />
            </div>
          </div>
        )
      })}
    </div>
  )
}
