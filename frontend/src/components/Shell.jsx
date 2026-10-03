import React from 'react'
import {
  Activity, AlertTriangle, Brain, Cloud, DollarSign, LayoutDashboard, Menu, RefreshCw,
  Search as SearchIcon, Server, Shield, X,
} from 'lucide-react'

import { isMac } from './CommandPalette'

import { relativeAge, scoreColor } from '../utils/format'
import { useDashboard } from '../state/DashboardContext'
import { Badge, TaskProgress } from './Primitives'

export const PAGES = [
  { id: 'overview', label: 'Overview', icon: LayoutDashboard, title: 'Overview',
    blurb: 'Cost, security and cloud health at a glance' },
  { id: 'cost', label: 'Cost Intelligence', icon: DollarSign, title: 'Cost Intelligence',
    blurb: 'Historical spend, Prophet forecast and budget risk' },
  { id: 'security', label: 'Security Analytics', icon: Shield, title: 'Security Analytics',
    blurb: 'IsolationForest anomaly detection over cloud behaviour' },
  { id: 'resources', label: 'Cloud Resources', icon: Server, title: 'Cloud Resources',
    blurb: 'EC2 inventory, utilisation and idle waste' },
  { id: 'insights', label: 'AI Insights', icon: Brain, title: 'AI Insights',
    blurb: 'What the models found, in plain language' },
]

export function Sidebar({ page, onNavigate, open, onClose }) {
  const { overview, health, degraded } = useDashboard()
  const anomalies = overview?.anomaly_count || 0

  const dependencies = health?.dependencies || []

  return (
    <aside className={`sidebar ${open ? 'open' : ''}`} aria-label="Main navigation">
      {/* The logo leads back to the welcome screen (an empty hash). */}
      <a href="#/" className="brand" title="Back to the welcome screen">
        <span className="brand-mark"><Cloud size={19} /></span>
        <span className="brand-text">
          <span className="brand-name">CloudGuard</span>
          <span className="brand-sub">Cloud Intelligence</span>
        </span>
      </a>

      <nav className="nav">
        <span className="nav-label">Analysis</span>
        {PAGES.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            type="button"
            className="nav-item"
            aria-current={page === id ? 'page' : undefined}
            onClick={() => { onNavigate(id); onClose?.() }}
          >
            <Icon size={16} aria-hidden="true" />
            {label}
            {id === 'security' && anomalies > 0 && (
              <span className="nav-count">{anomalies}</span>
            )}
          </button>
        ))}
      </nav>

      {overview && (
        <div className="sidebar-stats">
          <div className="sidebar-stat">
            <span className="sidebar-stat-label">Projected spend</span>
            <span className="sidebar-stat-value">
              {overview.forecast_month_end
                ? `$${Math.round(overview.forecast_month_end).toLocaleString('en-US')}`
                : '—'}
            </span>
            <span className="sidebar-stat-meta">month end</span>
          </div>
          <div className="sidebar-stat">
            <span className="sidebar-stat-label">Security health</span>
            <span className="sidebar-stat-value">
              {Math.round(overview.security_health_score ?? 0)}
              <span className="sidebar-stat-unit">/100</span>
            </span>
            <div className="meter" aria-hidden="true">
              <div
                className="meter-fill"
                style={{
                  width: `${Math.max(2, Math.min(100, overview.security_health_score ?? 0))}%`,
                  background: scoreColor(overview.security_health_score),
                  color: scoreColor(overview.security_health_score),
                }}
              />
            </div>
          </div>
        </div>
      )}

      <div className="sidebar-footer">
        <div className="health-strip">
          <span className="health-strip-title">System</span>
          {dependencies.length === 0 && (
            <div className="health-row">
              <span className="dot down" />
              API unreachable
            </div>
          )}
          {dependencies.map((dependency) => (
            <div className="health-row" key={dependency.name} title={dependency.detail}>
              <span className={`dot ${dependency.healthy ? 'up' : 'down'}`} />
              {dependency.name}
              <span className="detail">
                {dependency.name === 'celery'
                  ? `${dependency.metadata?.workers ?? 0} worker${dependency.metadata?.workers === 1 ? '' : 's'}`
                  : dependency.healthy ? 'ok' : 'down'}
              </span>
            </div>
          ))}
        </div>
        {degraded && (
          <Badge variant="badge-warning" icon={Activity}>Degraded mode</Badge>
        )}
      </div>
    </aside>
  )
}

export function TopBar({ page, onToggleMenu, onOpenPalette }) {
  const meta = PAGES.find((p) => p.id === page) || PAGES[0]
  const { isRunning, task, taskError, runRefresh, lastUpdated, refreshing } = useDashboard()

  return (
    <header className="topbar">
      <button
        type="button"
        className="btn btn-sm menu-toggle"
        onClick={onToggleMenu}
        aria-label="Toggle navigation"
      >
        <Menu size={16} />
      </button>

      <div className="topbar-titles">
        <h1>{meta.title}</h1>
        <p>{meta.blurb}</p>
      </div>

      <div className="topbar-actions">
        {onOpenPalette && (
          <button
            type="button"
            className="palette-trigger"
            onClick={onOpenPalette}
            aria-label="Open command palette"
          >
            <SearchIcon size={14} aria-hidden="true" />
            <span className="label">Search or jump to…</span>
            <kbd className="palette-kbd">{isMac ? '⌘' : 'Ctrl'} K</kbd>
          </button>
        )}
        {isRunning && task && <TaskProgress task={task} />}
        {/* A refused submission (rate limit, broker fault) must not fail silently. */}
        {!isRunning && taskError && (
          <span
            role="alert"
            title={[taskError.message, taskError.hint].filter(Boolean).join(' ')}
            style={{
              display: 'inline-flex', alignItems: 'center', gap: '0.35rem', minWidth: 0,
              fontSize: '0.78rem', color: 'var(--status-critical)', maxWidth: 320,
            }}
          >
            <AlertTriangle size={13} aria-hidden="true" style={{ flexShrink: 0 }} />
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {taskError.message}
            </span>
          </span>
        )}
        {!isRunning && !taskError && lastUpdated && (
          <span style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
            {refreshing ? 'Updating…' : `Updated ${relativeAge((Date.now() - lastUpdated.getTime()) / 1000)}`}
          </span>
        )}
        <button
          type="button"
          className="btn btn-primary btn-sm"
          onClick={() => runRefresh()}
          disabled={isRunning}
        >
          <RefreshCw size={14} className={isRunning ? 'spin' : undefined} />
          {isRunning ? 'Running…' : 'Refresh data'}
        </button>
      </div>
    </header>
  )
}

export function MobileScrim({ open, onClose }) {
  if (!open) return null
  return (
    <div
      className="drawer-backdrop"
      style={{ zIndex: 70 }}
      onClick={onClose}
      role="button"
      tabIndex={-1}
      aria-label="Close navigation"
    />
  )
}

export { X }
