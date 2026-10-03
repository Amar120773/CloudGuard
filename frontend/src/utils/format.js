/** Display formatters. Kept pure so they are unit-testable. */

export function money(value, { decimals = 0, compact = false } = {}) {
  if (value == null || Number.isNaN(Number(value))) return '—'
  const n = Number(value)
  const abs = Math.abs(n)
  if (compact && abs >= 10000) {
    return `${n < 0 ? '-' : ''}$${(abs / 1000).toFixed(1)}k`
  }
  // The sign goes before the currency symbol ("-$506", not "$-506"), and a value
  // that rounds to zero at this precision carries no sign at all.
  const negative = n < 0 && Number(abs.toFixed(decimals)) !== 0
  return `${negative ? '-' : ''}$${abs.toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })}`
}

export function number(value, decimals = 0) {
  if (value == null || Number.isNaN(Number(value))) return '—'
  return Number(value).toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
}

export function percent(value, decimals = 1, { signed = false } = {}) {
  if (value == null || Number.isNaN(Number(value))) return '—'
  const n = Number(value)
  const sign = signed && n > 0 ? '+' : ''
  return `${sign}${n.toFixed(decimals)}%`
}

/** Compact byte formatting for traffic metrics. */
export function bytes(value) {
  if (value == null || Number.isNaN(Number(value))) return '—'
  let n = Number(value)
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024
    i += 1
  }
  return `${n.toFixed(n < 10 && i > 0 ? 1 : 0)} ${units[i]}`
}

export function clockTime(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

export function shortDate(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

export function dateTime(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString('en-GB', {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

/** "3m ago" — used for data-freshness copy. */
export function relativeAge(seconds) {
  if (seconds == null) return null
  const s = Math.max(0, Math.round(seconds))
  if (s < 10) return 'just now'
  if (s < 60) return `${s}s ago`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

export function duration(seconds) {
  if (seconds == null) return '—'
  const s = Number(seconds)
  if (s < 1) return `${Math.round(s * 1000)}ms`
  if (s < 60) return `${s.toFixed(1)}s`
  return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`
}

/** Trim AWS's verbose service names for axis labels and tables. */
export function shortService(name) {
  if (!name) return '—'
  return name
    .replace('Amazon Elastic Compute Cloud - Compute', 'EC2 Compute')
    .replace('Amazon Elastic Kubernetes Service', 'EKS')
    .replace('Amazon Relational Database Service', 'RDS')
    .replace('Amazon Simple Storage Service', 'S3')
    .replace(/^Amazon /, '')
    .replace(/^AWS /, '')
}

/** Maps a severity to its CSS status class. Colour never travels alone. */
export function severityClass(severity) {
  switch ((severity || '').toUpperCase()) {
    case 'CRITICAL': return 'badge-critical'
    case 'HIGH': return 'badge-serious'
    case 'MEDIUM': return 'badge-warning'
    case 'LOW': return 'badge-info'
    default: return ''
  }
}

export function riskClass(level) {
  switch ((level || '').toUpperCase()) {
    case 'HIGH': return 'badge-critical'
    case 'ELEVATED': return 'badge-serious'
    case 'MODERATE': return 'badge-warning'
    case 'LOW': return 'badge-good'
    default: return ''
  }
}

export function severityColor(severity) {
  switch ((severity || '').toUpperCase()) {
    case 'CRITICAL': return 'var(--status-critical)'
    case 'HIGH': return 'var(--status-serious)'
    case 'MEDIUM': return 'var(--status-warning)'
    case 'LOW': return 'var(--seq-250)'
    default: return 'var(--text-muted)'
  }
}

/**
 * CPU utilisation is not a good/bad scale: too low is wasted spend, too high is
 * a saturation risk, and the middle is healthy. A good->bad ramp would paint a
 * perfectly healthy 40% red and an idle 2% green, which is backwards for cost
 * optimisation - so only the two problem bands are coloured.
 */
export function utilizationColor(cpu) {
  // Number(null) is 0, so a missing reading would otherwise be painted as
  // "idle at 0%" rather than "no data".
  if (cpu == null) return 'var(--text-muted)'
  const n = Number(cpu)
  if (Number.isNaN(n)) return 'var(--text-muted)'
  if (n < 6) return 'var(--status-warning)'    // idle: paying for nothing
  if (n > 88) return 'var(--status-serious)'   // saturated: throughput at risk
  return 'var(--text-primary)'
}

/** Health scores read high-is-good, so the ramp runs good -> critical. */
export function scoreColor(score) {
  const n = Number(score)
  if (Number.isNaN(n)) return 'var(--text-muted)'
  if (n >= 85) return 'var(--status-good)'
  if (n >= 65) return 'var(--status-warning)'
  if (n >= 40) return 'var(--status-serious)'
  return 'var(--status-critical)'
}
