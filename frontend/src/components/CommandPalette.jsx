import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Brain, CornerDownLeft, DollarSign, LayoutDashboard, Play, RefreshCw,
  Search, Server, Shield, Sparkles, Zap,
} from 'lucide-react'

import { useDashboard } from '../state/DashboardContext'

/** Mac shows ⌘, everything else shows Ctrl. */
export const isMac =
  typeof navigator !== 'undefined' && /Mac|iPod|iPhone|iPad/.test(navigator.platform || '')

const PAGE_COMMANDS = [
  { id: 'nav-overview', label: 'Overview', hint: 'Cost, security and cloud health', icon: LayoutDashboard, page: 'overview' },
  { id: 'nav-cost', label: 'Cost Intelligence', hint: 'Forecast, budget and service spend', icon: DollarSign, page: 'cost' },
  { id: 'nav-security', label: 'Security Analytics', hint: 'Anomaly timeline and event feed', icon: Shield, page: 'security' },
  { id: 'nav-resources', label: 'Cloud Resources', hint: 'EC2 inventory and idle waste', icon: Server, page: 'resources' },
  { id: 'nav-insights', label: 'AI Insights', hint: 'What the models found', icon: Brain, page: 'insights' },
]

/**
 * ⌘K command palette.
 *
 * Navigation plus the four background actions, reachable without the mouse.
 * Actions here are the same calls the page buttons make, so a task started from
 * the palette shows the same progress in the top bar.
 */
export default function CommandPalette({ open, onClose, onNavigate, currentPage }) {
  const { runRefresh, runForecast, runAnomalyDetection, isRunning } = useDashboard()
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)

  const inputRef = useRef(null)
  const listRef = useRef(null)
  const restoreFocusRef = useRef(null)

  const commands = useMemo(() => {
    const actions = [
      {
        id: 'act-refresh',
        label: 'Refresh all data',
        hint: 'Run every pipeline on the worker',
        icon: RefreshCw,
        group: 'Actions',
        disabled: isRunning,
        run: () => runRefresh({ force: true }),
      },
      {
        id: 'act-forecast',
        label: 'Retrain cost forecast',
        hint: 'Refit Prophet and reproject month end',
        icon: Play,
        group: 'Actions',
        disabled: isRunning,
        run: () => runForecast({ force: true }),
      },
      {
        id: 'act-rescore',
        label: 'Re-score security events',
        hint: 'Rerun IsolationForest over the windows',
        icon: Sparkles,
        group: 'Actions',
        disabled: isRunning,
        run: () => runAnomalyDetection({ force: true }),
      },
      {
        id: 'act-inject',
        label: 'Inject test anomaly',
        hint: 'Append an extreme event, then re-score',
        icon: Zap,
        group: 'Actions',
        disabled: isRunning,
        run: () => runAnomalyDetection({ inject_anomaly: true }),
      },
    ]

    const navigation = PAGE_COMMANDS.map((command) => ({
      ...command,
      group: 'Navigate',
      run: () => onNavigate(command.page),
    }))

    return [...navigation, ...actions]
  }, [isRunning, onNavigate, runAnomalyDetection, runForecast, runRefresh])

  const results = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return commands

    // Rank by where the match landed. Without this, searching "security" puts
    // Overview first - its *description* mentions security - ahead of the page
    // actually called Security Analytics.
    const score = (command) => {
      const label = command.label.toLowerCase()
      if (label.startsWith(needle)) return 0
      if (label.includes(needle)) return 1
      if (command.hint.toLowerCase().includes(needle)) return 2
      if (command.group.toLowerCase().includes(needle)) return 3
      return -1
    }

    return commands
      .map((command, index) => ({ command, rank: score(command), index }))
      .filter((entry) => entry.rank >= 0)
      // Stable within a rank, so the original grouping order is preserved.
      .sort((a, b) => a.rank - b.rank || a.index - b.index)
      .map((entry) => entry.command)
  }, [commands, query])

  // Reset to a clean state whenever the palette opens, and park focus in the
  // input so typing works immediately.
  useEffect(() => {
    if (!open) return undefined
    restoreFocusRef.current = document.activeElement
    setQuery('')
    setActive(0)
    const timer = setTimeout(() => inputRef.current?.focus(), 10)
    return () => clearTimeout(timer)
  }, [open])

  // Return focus to whatever opened it, so keyboard users are not dumped at the
  // top of the document.
  useEffect(() => {
    if (open) return
    const previous = restoreFocusRef.current
    if (previous && typeof previous.focus === 'function') previous.focus()
  }, [open])

  useEffect(() => {
    setActive((index) => Math.min(index, Math.max(0, results.length - 1)))
  }, [results.length])

  // Keep the highlighted row in view during arrow navigation.
  useEffect(() => {
    if (!open) return
    const node = listRef.current?.querySelector('[data-active="true"]')
    // Optional call: scrolling the row into view is a nicety, and environments
    // without scrollIntoView (jsdom, some embedded webviews) must not crash the
    // dialog over it.
    node?.scrollIntoView?.({ block: 'nearest' })
  }, [active, open])

  const select = useCallback(
    (command) => {
      if (!command || command.disabled) return
      onClose()
      // Let the dialog unmount before the action re-renders the page beneath.
      setTimeout(() => command.run(), 0)
    },
    [onClose],
  )

  const onKeyDown = (event) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setActive((i) => (results.length ? (i + 1) % results.length : 0))
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setActive((i) => (results.length ? (i - 1 + results.length) % results.length : 0))
    } else if (event.key === 'Enter') {
      event.preventDefault()
      select(results[active])
    } else if (event.key === 'Escape') {
      event.preventDefault()
      onClose()
    }
  }

  if (!open) return null

  let lastGroup = null

  return (
    <>
      <div className="palette-backdrop" onClick={onClose} role="presentation" />
      <div
        className="palette"
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        onKeyDown={onKeyDown}
      >
        <div className="palette-search">
          <Search size={16} aria-hidden="true" />
          <input
            ref={inputRef}
            className="palette-input"
            type="text"
            placeholder="Search pages and actions…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            aria-label="Search commands"
            aria-activedescendant={results[active] ? `cmd-${results[active].id}` : undefined}
            autoComplete="off"
            spellCheck="false"
          />
          <kbd className="palette-kbd">esc</kbd>
        </div>

        <div className="palette-list" ref={listRef} role="listbox" aria-label="Commands">
          {results.length === 0 && (
            <p className="palette-empty">No command matches “{query}”.</p>
          )}

          {results.map((command, index) => {
            const Icon = command.icon
            const showGroup = command.group !== lastGroup
            lastGroup = command.group
            const isActive = index === active
            const isCurrent = command.page && command.page === currentPage

            return (
              <React.Fragment key={command.id}>
                {showGroup && <div className="palette-group">{command.group}</div>}
                <button
                  type="button"
                  id={`cmd-${command.id}`}
                  className="palette-item"
                  role="option"
                  aria-selected={isActive}
                  data-active={isActive}
                  disabled={command.disabled}
                  onMouseMove={() => setActive(index)}
                  onClick={() => select(command)}
                >
                  <span className="palette-item-icon">
                    <Icon size={15} aria-hidden="true" />
                  </span>
                  <span className="palette-item-text">
                    <span className="palette-item-label">{command.label}</span>
                    <span className="palette-item-hint">{command.hint}</span>
                  </span>
                  {isCurrent && <span className="palette-tag">current</span>}
                  {command.disabled && <span className="palette-tag">running…</span>}
                  {isActive && !command.disabled && (
                    <CornerDownLeft size={13} className="palette-enter" aria-hidden="true" />
                  )}
                </button>
              </React.Fragment>
            )
          })}
        </div>

        <div className="palette-footer">
          <span><kbd className="palette-kbd">↑</kbd><kbd className="palette-kbd">↓</kbd> navigate</span>
          <span><kbd className="palette-kbd">↵</kbd> run</span>
          <span className="palette-footer-right">
            {results.length} command{results.length === 1 ? '' : 's'}
          </span>
        </div>
      </div>
    </>
  )
}

/**
 * Global ⌘K / Ctrl+K listener.
 *
 * Only fires with the modifier held, so typing "k" in a search field is safe.
 */
export function useCommandPalette() {
  const [open, setOpen] = useState(false)

  useEffect(() => {
    const onKeyDown = (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        setOpen((value) => !value)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])

  return {
    open,
    openPalette: useCallback(() => setOpen(true), []),
    closePalette: useCallback(() => setOpen(false), []),
  }
}
