import React, { useCallback, useEffect, useRef, useState } from 'react'
import {
  ArrowRight, Cloud, CornerDownLeft, Radar, Server, ShieldCheck, TrendingUp,
} from 'lucide-react'

import { api } from '../api/client'
import { useDashboard } from '../state/DashboardContext'
import { money } from '../utils/format'

// How long the exit animation plays before the dashboard takes over.
const LEAVE_MS = 520
// A sleeping free-tier API takes about a minute to start; keep knocking a
// little longer than that before calling it unavailable.
const WAKE_RETRY_MS = 4000
const WAKE_GIVE_UP_MS = 150000

function prefersReducedMotion() {
  return Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches)
}

/**
 * Ping the API until it answers.
 *
 * The welcome screen doubles as a warm-up: on a free host the API sleeps when
 * idle, so this starts waking it the moment someone arrives, and by the time
 * they click through the dashboard has data instead of a cold-start error.
 */
function useApiWakeup(onOnline) {
  const [status, setStatus] = useState('waking')
  const onOnlineRef = useRef(onOnline)
  onOnlineRef.current = onOnline

  useEffect(() => {
    const controller = new AbortController()
    const startedAt = Date.now()
    let timer = null
    let cancelled = false

    const ping = async () => {
      try {
        await api.health({ signal: controller.signal })
        if (cancelled) return
        setStatus('online')
        onOnlineRef.current?.()
      } catch (error) {
        if (cancelled || error?.name === 'AbortError') return
        if (Date.now() - startedAt > WAKE_GIVE_UP_MS) {
          setStatus('offline')
          return
        }
        timer = setTimeout(ping, WAKE_RETRY_MS)
      }
    }
    ping()

    return () => {
      cancelled = true
      clearTimeout(timer)
      controller.abort()
    }
  }, [])

  return status
}

const STATUS_COPY = {
  waking: 'Waking the analysis engine…',
  online: 'Analysis engine online',
  offline: 'Analysis engine unavailable — the dashboard will keep retrying',
}

export default function Landing({ onEnter }) {
  const { overview, pipelineByName, reload } = useDashboard()
  const [leaving, setLeaving] = useState(false)
  const leaveTimer = useRef(null)

  // Fetch fresh numbers the moment the API answers, rather than on the next poll.
  const status = useApiWakeup(reload)

  useEffect(() => () => clearTimeout(leaveTimer.current), [])

  // The Overview chunk is what the visitor sees next; have it ready.
  useEffect(() => {
    import('./Overview')
  }, [])

  const enter = useCallback(() => {
    if (leaving) return
    setLeaving(true)
    leaveTimer.current = setTimeout(onEnter, prefersReducedMotion() ? 0 : LEAVE_MS)
  }, [leaving, onEnter])

  const forecastReady = pipelineByName.cost_forecast?.ready
  const securityReady = pipelineByName.security_anomalies?.ready
  const resourcesReady = pipelineByName.cloud_resources?.ready

  // Live figures when the pipelines have run; a description of the model otherwise.
  const features = [
    {
      icon: TrendingUp,
      title: 'Cost intelligence',
      text: forecastReady
        ? `${money(overview?.forecast_month_end, { decimals: 0 })} projected for month end`
        : 'Prophet forecasts month-end spend with a confidence band',
    },
    {
      icon: Radar,
      title: 'Security analytics',
      text: securityReady
        ? `${overview?.anomaly_count ?? 0} anomalies across ${overview?.total_security_events ?? 0} windows`
        : 'IsolationForest flags behaviour no rule was written for',
    },
    {
      icon: Server,
      title: 'Cloud resources',
      text: resourcesReady
        ? `${overview?.active_resources ?? 0} resources, ${overview?.idle_resources ?? 0} idle`
        : 'EC2 inventory joined with CloudWatch utilisation',
    },
  ]

  return (
    <div className={`landing ${leaving ? 'is-leaving' : ''}`}>
      <div className="landing-backdrop" aria-hidden="true">
        <div className="landing-aurora a1" />
        <div className="landing-aurora a2" />
        <div className="landing-grid" />
      </div>

      <header className="landing-top">
        <span className="brand-mark"><Cloud size={19} aria-hidden="true" /></span>
        <span className="landing-wordmark">CloudGuard</span>
        <span className={`landing-status is-${status}`} role="status" aria-live="polite">
          <span className="landing-status-dot" aria-hidden="true" />
          {STATUS_COPY[status]}
        </span>
      </header>

      <main className="landing-main">
        <section className="landing-copy">
          <p className="landing-eyebrow lp-reveal" style={{ '--d': '0.05s' }}>
            AI-powered cloud security &amp; cost intelligence
          </p>
          <h1 className="landing-title lp-reveal" style={{ '--d': '0.15s' }}>
            Your cloud, <span className="landing-accent">forecast and defended.</span>
          </h1>
          <p className="landing-lead lp-reveal" style={{ '--d': '0.28s' }}>
            CloudGuard forecasts what your cloud will cost and flags the behaviour
            that does not belong, from live pipelines rather than static reports.
          </p>

          <div className="landing-actions lp-reveal" style={{ '--d': '0.42s' }}>
            {/* The one thing to do here, so it takes focus: Enter goes straight in. */}
            <button type="button" className="btn btn-primary landing-cta" onClick={enter} autoFocus>
              Enter dashboard
              <ArrowRight size={17} aria-hidden="true" className="landing-cta-arrow" />
            </button>
            <span className="landing-hint">
              or press <kbd className="palette-kbd"><CornerDownLeft size={11} aria-hidden="true" /> Enter</kbd>
            </span>
          </div>
        </section>

        <div className="landing-visual lp-reveal" style={{ '--d': '0.2s' }} aria-hidden="true">
          <div className="orbit">
            <div className="orbit-glow" />
            <div className="orbit-sweep" />
            <div className="orbit-ring r1"><span className="orbit-node" /></div>
            <div className="orbit-ring r2">
              <span className="orbit-node" />
              <span className="orbit-node n2" />
            </div>
            <div className="orbit-ring r3">
              <span className="orbit-node" />
              <span className="orbit-node n2" />
              <span className="orbit-node n3" />
            </div>
            <span className="orbit-ping p1" />
            <span className="orbit-ping p2" />
            <div className="orbit-core">
              <ShieldCheck size={34} strokeWidth={1.6} />
            </div>
          </div>
        </div>
      </main>

      <ul className="landing-features">
        {features.map(({ icon: Icon, title, text }, index) => (
          <li key={title} className="landing-feature lp-reveal" style={{ '--d': `${0.55 + index * 0.1}s` }}>
            <span className="landing-feature-icon"><Icon size={17} aria-hidden="true" /></span>
            <span className="landing-feature-title">{title}</span>
            <span className="landing-feature-text">{text}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
