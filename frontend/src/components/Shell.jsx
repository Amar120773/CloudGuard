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

/**
 * A plain left click navigates in place. Anything else (Ctrl/⌘/Shift-click,
 * middle click) keeps its browser meaning, so a page opens in a new tab.
 */
function followInApp(event, go) {
  if (event.defaultPrevented || event.button !== 0) return
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
  event.preventDefault()
  go()
}

export function Sidebar({ page, onNavigate, onHome, open, onClose }) {
  const { overview, health, degraded, standalone } = useDashboard()
  const anomalies = overview?.anomaly_count || 0

  const dependencies = health?.dependencies || []

  return (
    <aside className={`sidebar ${open ? 'open' : ''}`} aria-label="Main navigation">
      {/* The logo leads back to the welcome screen at the root path. */}
      <a
        href="/"
        className="brand"
        title="Back to the welcome screen"
        onClick={(event) => followInApp(event, () => onHome?.())}
      >
        <span className="brand-mark"><Cloud size={19} /></span>
        <span className="brand-text">
          <span className="brand-name">CloudGuard</span>
          <span className="brand-sub">Cloud Intelligence</span>
        </span>
      </a>

      <nav className="nav">
        <span className="nav-label">Analysis</span>
        {/* Real links: each section is a page with its own address. */}
        {PAGES.map(({ id, label, icon: Icon }) => (
          <a
            key={id}
            href={`/${id}`}
            className="nav-item"
            aria-current={page === id ? 'page' : undefined}
            onClick={(event) => followInApp(event, () => { onNavigate(id); onClose?.() })}
          >
            <Icon size={16} aria-hidden="true" />
            {label}
            {id === 'security' && anomalies > 0 && (
              <span className="nav-count">{anomalies}</span>
            )}
          </a>
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
          {/* Redis and the worker are absent on purpose here, so they get a
              neutral line rather than red "down" rows. */}
          {standalone && (
            <div
              className="health-row"
              title="Standalone mode: one process keeps results in memory and runs analysis jobs itself."
            >
              <span className="dot info" />
              mode
              <span className="detail">standalone</span>
            </div>
          )}
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
  const {
    isRunning, task, taskError, runRefresh, lastUpdated, refreshing, standalone,
  } = useDashboard()

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
        {isRunning && task && <TaskProgress task={task} expectedInline={standalone} />}
        {/* A refused submission (rate limit, broker fault) must not fail silently. */}
        {!isRunning && taskError && (
          <span
            className="topbar-error"
            role="alert"
            title={[taskError.message, taskError.hint].filter(Boolean).join(' ')}
          >
            <AlertTriangle size={13} aria-hidden="true" />
            <span className="topbar-error-text">{taskError.message}</span>
          </span>
        )}
        {!isRunning && !taskError && lastUpdated && (
          <span className="topbar-updated">
            {refreshing ? 'Updating…' : `Updated ${relativeAge((Date.now() - lastUpdated.getTime()) / 1000)}`}
          </span>
        )}
        <button
          type="button"
          className="btn btn-primary btn-sm topbar-refresh"
          onClick={() => runRefresh()}
          disabled={isRunning}
        >
          <RefreshCw size={14} className={isRunning ? 'spin' : undefined} aria-hidden="true" />
          {/* Visually hidden on narrow phones, where the icon stands alone; it
              stays in the DOM as the button's accessible name. */}
          <span className="topbar-refresh-label">{isRunning ? 'Running…' : 'Refresh data'}</span>
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
