import React, { useCallback, useEffect, useRef, useState } from 'react'
import {
  ArrowRight, Cloud, CornerDownLeft, Radar, Server, TrendingUp,
} from 'lucide-react'

import { api, apiHostAnswers, apiIsMixedContent } from '../api/client'
import LiveScan from '../components/LiveScan'
import { useDashboard } from '../state/DashboardContext'
import { money } from '../utils/format'
import { prefersReducedMotion } from '../utils/motion'

// How long the exit animation plays before the dashboard takes over.
const LEAVE_MS = 520
// A sleeping free-tier API takes about a minute to start; keep knocking a
// little longer than that before calling it unavailable.
const WAKE_RETRY_MS = 4000
const WAKE_GIVE_UP_MS = 150000
// A host that answers but refuses this origin may be a waking server's
// interstitial page, so a refusal only counts once it outlasts a cold start.
const REFUSAL_GRACE_MS = 60000

/**
 * A failure that retrying cannot fix: only a configuration change will.
 * Returns null when the API may simply still be starting.
 */
async function configurationProblem(error) {
  if (apiIsMixedContent()) return 'insecure'
  // The static host itself answered /api/health: the API address is missing
  // or wrong (VITE_API_BASE_URL is read at build time).
  if (error?.isInvalidResponse || error?.status === 404 || error?.status === 405) return 'not_found'
  // Failed outright, yet something answers there: the browser refused it.
  if (error?.code === 'network_error' && (await apiHostAnswers())) return 'cors'
  return null
}

const OWNER_HINTS = {
  insecure: () => 'VITE_API_BASE_URL uses http:// but this page is https://. Use the API\'s https:// URL and redeploy.',
  not_found: () => 'GET /api/health did not reach the CloudGuard API. Set VITE_API_BASE_URL to the API\'s https:// URL in the hosting project and redeploy (it is read at build time).',
  cors: () => `The API at ${api.baseUrl || window.location.origin} answers but does not allow ${window.location.origin}. Add that origin to CORS_ORIGINS on the API (no trailing slash) and restart it.`,
}

/**
 * Ping the API until it answers.
 *
 * The welcome screen doubles as a warm-up: on a free host the API sleeps when
 * idle, so this starts waking it the moment someone arrives, and by the time
 * they click through the dashboard has data instead of a cold-start error.
 * A misconfigured deployment is reported as such within seconds rather than
 * looking like a server that never finishes waking.
 */
function useApiWakeup(onOnline) {
  const [state, setState] = useState(() => ({ status: 'waking', startedAt: Date.now() }))
  const onOnlineRef = useRef(onOnline)
  onOnlineRef.current = onOnline

  useEffect(() => {
    const controller = new AbortController()
    const startedAt = Date.now()
    let refusedSince = null
    let timer = null
    let cancelled = false

    const stop = (status) => {
      if (cancelled) return
      setState((current) => ({ ...current, status }))
      const hint = OWNER_HINTS[status]
      // For whoever deployed this; visitors only see the short status line.
      if (hint) console.warn(`[CloudGuard] ${hint()}`)
    }

    const ping = async () => {
      try {
        await api.health({ signal: controller.signal })
        if (cancelled) return
        setState((current) => ({ ...current, status: 'online' }))
        onOnlineRef.current?.()
      } catch (error) {
        if (cancelled || error?.name === 'AbortError') return
        const problem = await configurationProblem(error)
        if (cancelled) return

        if (problem && problem !== 'cors') {
          stop(problem)
          return
        }
        if (problem === 'cors') {
          if (refusedSince == null) refusedSince = Date.now()
          if (Date.now() - refusedSince >= REFUSAL_GRACE_MS) {
            stop('cors')
            return
          }
        } else {
          refusedSince = null
        }

        if (Date.now() - startedAt > WAKE_GIVE_UP_MS) {
          stop('offline')
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

  return state
}

const STATUS_COPY = {
  waking: 'Waking the analysis engine…',
  online: 'Analysis engine online',
  offline: 'Analysis engine unavailable — the dashboard will keep retrying',
  not_found: 'Analysis engine not connected to this site',
  cors: 'Analysis engine is refusing this site',
  insecure: 'Analysis engine address must use https',
}

const STATUS_TITLE = {
  waking: 'Free servers sleep when idle and take about a minute to start.',
  not_found: 'This site is not pointed at a CloudGuard API.',
  cors: 'The API is running but does not accept requests from this address.',
  insecure: 'Browsers block an http:// API from an https:// page.',
}

function formatElapsed(seconds) {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}

/** The API status line. A visible timer shows that waking is progressing, not stuck. */
function StatusPill({ status, startedAt }) {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    if (status !== 'waking') return undefined
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [status])

  const tone = status === 'waking' || status === 'online' ? status : 'offline'
  const seconds = Math.max(0, Math.floor((now - startedAt) / 1000))

  return (
    <span
      className={`landing-status is-${tone}`}
      role="status"
      aria-live="polite"
      title={STATUS_TITLE[status]}
    >
      <span className="landing-status-dot" aria-hidden="true" />
      {STATUS_COPY[status]}
      {/* Hidden from the live region, which would otherwise announce every tick. */}
      {status === 'waking' && seconds >= 3 && (
        <span className="landing-status-timer" aria-hidden="true">{formatElapsed(seconds)}</span>
      )}
    </span>
  )
}

const GLYPHS = '#%&*+=<>/\\|$@!?01xkqz'
const DECODE_LEAD_MS = 260
const DECODE_LETTER_MS = 55
const DECODE_FRAME_MS = 45
const DECODE_SETTLE_MS = 320

/**
 * A phrase that resolves out of scrambled glyphs, letter by letter.
 *
 * The real phrase is always in the DOM, and is the heading's accessible name;
 * the scramble is an aria-hidden overlay whose cells are sized by the real
 * letters, so nothing reflows while it runs. Reduced motion skips it.
 */
function DecodeText({ text, delay = 0 }) {
  const [phase, setPhase] = useState(() => (prefersReducedMotion() ? 'done' : 'scramble'))
  const [startedAt] = useState(() => Date.now())
  const [, setFrame] = useState(0)
  const letters = Array.from(text)
  const resolveAt = (index) => delay + DECODE_LEAD_MS + index * DECODE_LETTER_MS
  const finishedAt = resolveAt(letters.length - 1)

  useEffect(() => {
    if (phase === 'scramble') {
      const timer = setInterval(() => {
        if (Date.now() - startedAt >= finishedAt) setPhase('settle')
        else setFrame((frame) => frame + 1)
      }, DECODE_FRAME_MS)
      return () => clearInterval(timer)
    }
    if (phase === 'settle') {
      const timer = setTimeout(() => setPhase('done'), DECODE_SETTLE_MS)
      return () => clearTimeout(timer)
    }
    return undefined
  }, [phase, startedAt, finishedAt])

  const elapsed = Date.now() - startedAt

  return (
    <span className="decode">
      <span className={`landing-accent decode-text ${phase === 'scramble' ? 'is-scrambling' : ''}`}>
        {text}
      </span>
      {phase !== 'done' && (
        <span className={`decode-overlay ${phase === 'settle' ? 'is-settling' : ''}`} aria-hidden="true">
          {letters.map((letter, index) => {
            const set = letter === ' ' || phase === 'settle' || elapsed >= resolveAt(index)
            return (
              <span className="decode-cell" key={index}>
                <span className="decode-sizer">{letter}</span>
                <span className={`decode-glyph ${set ? 'is-set' : ''}`}>
                  {set ? letter : GLYPHS[Math.floor(Math.random() * GLYPHS.length)]}
                </span>
              </span>
            )
          })}
        </span>
      )}
    </span>
  )
}

export default function Landing({ onEnter }) {
  const {
    overview, forecast, security, recentEvents, pipelineByName, reload,
  } = useDashboard()
  const [leaving, setLeaving] = useState(false)
  const leaveTimer = useRef(null)

  // Fetch fresh numbers the moment the API answers, rather than on the next poll.
  const { status, startedAt } = useApiWakeup(reload)

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
        <StatusPill status={status} startedAt={startedAt} />
      </header>

      <main className="landing-main">
        <section className="landing-copy">
          <p className="landing-eyebrow lp-reveal" style={{ '--d': '0.05s' }}>
            AI watchdog for cloud cost &amp; security
          </p>
          {/* Two things, one promise: what it watches, and that it sees them first.
              The spaces between lines keep the accessible name readable. */}
          <h1 className="landing-title lp-reveal" style={{ '--d': '0.15s' }}>
            <span className="landing-title-line">See the <DecodeText text="cloud bill" delay={250} /></span>{' '}
            <span className="landing-title-line">and the <DecodeText text="breach" delay={560} /></span>{' '}
            <span className="landing-title-line">before they hit.</span>
          </h1>
          <p className="landing-lead lp-reveal" style={{ '--d': '0.28s' }}>
            CloudGuard forecasts your month-end spend before the invoice lands and
            flags attacks no rule was written for, as they happen.
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
          {/* The radar now rings the live scan: it sweeps around the console. */}
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
          </div>
          <LiveScan
            forecast={forecastReady ? forecast : null}
            security={securityReady ? security : null}
            events={securityReady ? recentEvents : null}
          />
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
