import React from 'react'
import {
  AlertTriangle, ArrowDownRight, ArrowRight, ArrowUpRight, Check, Clock,
  Info, Loader2, Minus, RefreshCw, ShieldAlert, WifiOff, XCircle,
} from 'lucide-react'

import { relativeAge, scoreColor } from '../utils/format'

/* ------------------------------------------------------------------ panel */
export function Panel({ title, subtitle, actions, children, className = '', ...rest }) {
  return (
    <section className={`panel ${className}`} {...rest}>
      {(title || actions) && (
        <header className="panel-header">
          {title && (
            <div>
              <h2 className="panel-title">{title}</h2>
              {subtitle && <p className="panel-subtitle">{subtitle}</p>}
            </div>
          )}
          {actions && <div className="panel-actions">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  )
}

/* ------------------------------------------------------------------ badge */
/**
 * Status badges always render their label text next to the colour. Critical-red
 * and good-green are indistinguishable under deuteranopia, so colour alone can
 * never be the carrier of meaning.
 */
export function Badge({ children, variant = '', icon: Icon, title }) {
  return (
    <span className={`badge ${variant}`} title={title}>
      {Icon && <Icon size={12} aria-hidden="true" />}
      {children}
    </span>
  )
}

export function StatusBadge({ status }) {
  const isAnomaly = String(status).toUpperCase() === 'ANOMALY'
  return (
    <Badge variant={isAnomaly ? 'badge-critical' : ''} icon={isAnomaly ? ShieldAlert : Check}>
      {isAnomaly ? 'ANOMALY' : 'Routine'}
    </Badge>
  )
}

/* ------------------------------------------------------------------ delta */
export function Delta({ value, invertColor = false, suffix = '%' }) {
  if (value == null || Number.isNaN(Number(value))) return null
  const n = Number(value)
  const flat = Math.abs(n) < 0.05
  // For spend, up is bad. `invertColor` flips that for metrics where up is good.
  const good = invertColor ? n > 0 : n < 0
  const cls = flat ? 'delta-flat' : good ? 'delta-down' : 'delta-up'
  const Icon = flat ? Minus : n > 0 ? ArrowUpRight : ArrowDownRight

  return (
    <span className={`delta ${cls}`}>
      <Icon size={14} aria-hidden="true" />
      {n > 0 ? '+' : ''}{n.toFixed(1)}{suffix}
    </span>
  )
}

/* ------------------------------------------------------------------- tile */
export function StatTile({
  label, value, meta, icon: Icon, footer, score, loading, valueClass = '', style,
}) {
  if (loading) {
    return (
      <article className="tile" style={style}>
        <div className="tile-head"><div className="skeleton skeleton-text" style={{ width: '45%' }} /></div>
        <div className="skeleton skeleton-value" />
        <div className="skeleton skeleton-text" style={{ width: '70%' }} />
      </article>
    )
  }
  return (
    <article className="tile" style={style}>
      <div className="tile-head">
        <span className="tile-label">{label}</span>
        {Icon && <span className="tile-icon"><Icon size={16} aria-hidden="true" /></span>}
      </div>
      <div className={`tile-value ${valueClass}`}>{value}</div>
      {meta && <div className="tile-meta">{meta}</div>}
      {score != null && (
        <div className="meter" role="img" aria-label={`Score ${Math.round(score)} out of 100`}>
          <div
            className="meter-fill"
            style={{ width: `${Math.max(2, Math.min(100, score))}%`, background: scoreColor(score) }}
          />
        </div>
      )}
      {footer && <div className="tile-foot">{footer}</div>}
    </article>
  )
}

/* ----------------------------------------------------------------- states */
export function Skeleton({ height = 16, width = '100%', style }) {
  return <div className="skeleton" style={{ height, width, ...style }} />
}

export function ChartSkeleton({ height = 240 }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <Skeleton height={12} width="30%" />
      <Skeleton height={height} />
    </div>
  )
}

export function TableSkeleton({ rows = 5 }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} height={34} style={{ opacity: 1 - i * 0.12 }} />
      ))}
    </div>
  )
}

export function EmptyState({ title, message, hint, icon: Icon = Info, action }) {
  return (
    <div className="state">
      <span className="state-icon"><Icon size={20} aria-hidden="true" /></span>
      <span className="state-title">{title}</span>
      {message && <p className="state-msg">{message}</p>}
      {hint && <p className="state-hint">{hint}</p>}
      {action}
    </div>
  )
}

/**
 * Distinguishes "this pipeline has not run yet" (offer the Run button) from a
 * network outage, because the two need different actions from the operator.
 */
export function ErrorState({ error, onRetry, retryLabel = 'Retry', runLabel, onRun }) {
  const notReady = error?.isPipelineNotReady
  const offline = error?.isNetworkError
  const invalid = error?.isInvalidResponse

  const Icon = notReady ? Clock : offline ? WifiOff : XCircle
  const title = notReady
    ? 'Analysis has not run yet'
    : offline
      ? 'Cannot reach the API'
      : invalid
        ? 'Unexpected response from the API'
        : 'Something went wrong'

  return (
    <div className="state">
      <span className={`state-icon ${notReady ? 'warn' : 'err'}`}>
        <Icon size={20} aria-hidden="true" />
      </span>
      <span className="state-title">{title}</span>
      <p className="state-msg">{error?.message || 'Unexpected error.'}</p>
      {error?.hint && <p className="state-hint">{error.hint}</p>}
      <div className="row" style={{ justifyContent: 'center' }}>
        {notReady && onRun && (
          <button type="button" className="btn btn-primary btn-sm" onClick={onRun}>
            <RefreshCw size={13} /> {runLabel || 'Run analysis'}
          </button>
        )}
        {onRetry && (
          <button type="button" className="btn btn-sm" onClick={onRetry}>
            <RefreshCw size={13} /> {retryLabel}
          </button>
        )}
      </div>
    </div>
  )
}

/* ---------------------------------------------------------------- banners */
export function Banner({ variant = 'banner-info', icon: Icon = Info, children, action }) {
  return (
    <div className={`banner ${variant}`} role={variant === 'banner-err' ? 'alert' : 'status'}>
      <Icon size={15} aria-hidden="true" />
      <div>{children}</div>
      {action && <div className="banner-action">{action}</div>}
    </div>
  )
}

/** "Forecast temporarily unavailable. Last successful forecast: 10 minutes ago." */
export function StaleBanner({ label, ageSeconds, onRetry }) {
  return (
    <Banner
      variant="banner-warn"
      icon={AlertTriangle}
      action={onRetry && (
        <button type="button" className="btn btn-sm" onClick={onRetry}>
          <RefreshCw size={13} /> Retry
        </button>
      )}
    >
      <strong>{label} temporarily unavailable.</strong>{' '}
      {ageSeconds != null
        ? `Showing the last successful result from ${relativeAge(ageSeconds)}.`
        : 'Showing the last successful result.'}
    </Banner>
  )
}

/* --------------------------------------------------------- task progress */
export function TaskProgress({ task, compact = false }) {
  if (!task) return null
  const { status, progress = 0, stage, executor } = task
  const done = status === 'COMPLETED'
  const failed = status === 'FAILED'

  return (
    <div className="task-progress" role="status" aria-live="polite">
      {!done && !failed && <Loader2 size={13} className="spin" aria-hidden="true" />}
      {done && <Check size={13} style={{ color: 'var(--status-good)' }} aria-hidden="true" />}
      {failed && <XCircle size={13} style={{ color: 'var(--status-critical)' }} aria-hidden="true" />}
      {!compact && <span className="task-stage">{stage || status}</span>}
      {!done && !failed && (
        <div className="task-bar">
          <div className="task-bar-fill" style={{ width: `${Math.max(4, progress)}%` }} />
        </div>
      )}
      {executor === 'inline' && !compact && (
        <span title="Celery broker unreachable; running on a local worker thread">
          <Badge variant="badge-warning">inline</Badge>
        </span>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ misc */
export function KeyValue({ label, value, mono = false }) {
  return (
    <div className="kv-row">
      <span className="kv-key">{label}</span>
      <span className={`kv-val ${mono ? 'mono' : ''}`}>{value}</span>
    </div>
  )
}

export function SectionLabel({ children }) {
  return <div className="section-label">{children}</div>
}

export function ActionHint({ children }) {
  return (
    <p className="insight-action">
      <ArrowRight size={14} aria-hidden="true" />
      <span><strong>Action:</strong> {children}</span>
    </p>
  )
}
