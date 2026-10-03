import React, { useEffect, useState } from 'react'
import { AlertTriangle, Check, ShieldAlert, ShieldCheck } from 'lucide-react'

import { Badge } from './Primitives'
import { clockTime, money, severityClass } from '../utils/format'
import { prefersReducedMotion } from '../utils/motion'

// A new event arrives this often; four stay on screen.
const FEED_STEP_MS = 1900
const FEED_ROWS = 4

/**
 * Shown until both pipelines have run: event names the detector really emits
 * (FEATURE_LABELS in ml/anomaly_detection.py), with no sources or scores,
 * because nothing has been measured yet. The panel says "Example" meanwhile.
 */
const SAMPLE_FEED = [
  { type: 'Normal API traffic' },
  { type: 'Normal authentication' },
  { type: 'Authentication failure burst', severity: 'CRITICAL' },
  { type: 'Normal data transfer' },
  { type: 'Normal request rate' },
  { type: 'Network transfer spike', severity: 'HIGH' },
  { type: 'Normal connection volume' },
  { type: 'Normal client distribution' },
].map((row, index) => ({ ...row, key: `sample-${index}` }))

function toRow(event) {
  const flagged = String(event.status).toUpperCase() === 'ANOMALY'
  return {
    key: event.event_id,
    type: event.event_type,
    source: event.source,
    severity: flagged ? event.severity || 'HIGH' : null,
  }
}

/**
 * The real feed: the latest scored windows, oldest first so it plays forward
 * in time. Those are usually all routine, so the strongest detections are
 * woven in every few rows; otherwise the feed would show half the model.
 */
function liveFeed(events, anomalies) {
  const recent = [...(events || [])].reverse()
  const seen = new Set(recent.map((event) => event.event_id))
  const flagged = (anomalies || []).filter((event) => !seen.has(event.event_id))
  if (!recent.length && !flagged.length) return null

  const feed = []
  recent.forEach((event, index) => {
    feed.push(event)
    if (index % 3 === 2 && flagged.length) feed.push(flagged.shift())
  })
  return [...feed, ...flagged].map(toRow)
}

// --- the spend glyph -------------------------------------------------------
// A stylised month of cumulative spend: observed to "today", then the
// forecast with its widening interval, against a budget line. It carries no
// axis values; the figures beside it come from the forecast payload.
const W = 320
const TOP = 6
const BOTTOM = 116
const BUDGET_Y = 32
const TODAY = 0.58
// Budget is 1.0; this maps it onto BUDGET_Y.
const VMAX = (BOTTOM - TOP) / (BOTTOM - BUDGET_Y)

function buildGlyph(monthEnd) {
  // Cumulative spend never falls: daily spend varies through the week, so only
  // the slope wobbles (the amplitude stays below 1 to keep it monotonic).
  const k = 2 * Math.PI * 4.3
  const value = (t) => monthEnd * (t - (0.35 * Math.sin(k * t)) / k)
  const x = (t) => t * W
  const y = (v) => BOTTOM - (v / VMAX) * (BOTTOM - TOP)
  const spread = (t) => 0.02 + ((t - TODAY) / (1 - TODAY)) * 0.13

  const steps = Array.from({ length: 65 }, (_, i) => i / 64)
  const past = [...steps.filter((t) => t < TODAY), TODAY]
  const future = [TODAY, ...steps.filter((t) => t > TODAY)]
  const line = (points) => points
    .map(([px, py], i) => `${i ? 'L' : 'M'}${px.toFixed(1)},${py.toFixed(1)}`)
    .join(' ')

  const observed = line(past.map((t) => [x(t), y(value(t))]))
  const upper = future.map((t) => [x(t), y(value(t) + spread(t))])
  const lower = future.map((t) => [x(t), y(value(t) - spread(t))]).reverse()

  // Where the forecast first reaches the budget, if it does.
  let cross = null
  if (value(1) > 1) {
    let lo = TODAY
    let hi = 1
    for (let i = 0; i < 24; i += 1) {
      const mid = (lo + hi) / 2
      if (value(mid) >= 1) hi = mid
      else lo = mid
    }
    cross = { x: x(hi), y: BUDGET_Y }
  }

  return {
    observed,
    area: `${observed} L${x(TODAY)},${BOTTOM} L0,${BOTTOM} Z`,
    forecast: line(future.map((t) => [x(t), y(value(t))])),
    band: `${line(upper)} ${lower.map(([px, py]) => `L${px.toFixed(1)},${py.toFixed(1)}`).join(' ')} Z`,
    today: { x: x(TODAY), y: y(value(TODAY)) },
    cross,
  }
}

const GLYPHS = { over: buildGlyph(1.16), under: buildGlyph(0.84) }

/** What the forecast panel says: real figures when the pipeline has run. */
function forecastStory(forecast) {
  if (!forecast) {
    return { variant: 'over', budget: true, tone: 'over', text: 'Over budget before month end' }
  }
  const projected = forecast.projected_month_end
  const budget = forecast.budget
  if (!(Number(budget?.monthly_budget) > 0)) {
    return { variant: 'under', budget: false, tone: 'neutral', text: `${money(projected)} by month end`, projected }
  }
  if (budget.budget_breach_expected) {
    return {
      variant: 'over', budget: true, tone: 'over', projected,
      text: `${money(Math.abs(budget.headroom))} over budget by month end`,
    }
  }
  return {
    variant: 'under', budget: true, tone: 'under', projected,
    text: `On track: ${money(budget.headroom)} under budget`,
  }
}

function SpendChart({ story }) {
  const glyph = GLYPHS[story.variant]
  const ChipIcon = story.tone === 'over' ? AlertTriangle : Check

  return (
    <div className="ls-chart">
      <svg viewBox={`0 0 ${W} 120`} className="ls-chart-base">
        <line className="ls-grid" x1="0" x2={W} y1="74" y2="74" />
        <line className="ls-grid" x1="0" x2={W} y1={BOTTOM} y2={BOTTOM} />
        {story.budget && <line className="ls-budget" x1="0" x2={W} y1={BUDGET_Y} y2={BUDGET_Y} />}
      </svg>
      {story.budget && <span className="ls-budget-label">Budget</span>}

      {/* Revealed left to right by the scan head, then held, then replayed. */}
      <div className="ls-chart-live">
        <svg viewBox={`0 0 ${W} 120`}>
          <defs>
            <linearGradient id="ls-area-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--series-1)" stopOpacity="0.26" />
              <stop offset="100%" stopColor="var(--series-1)" stopOpacity="0" />
            </linearGradient>
          </defs>
          <path d={glyph.area} fill="url(#ls-area-fill)" />
          <path d={glyph.band} className="ls-band" />
          <path d={glyph.observed} className="ls-observed" />
          <path d={glyph.forecast} className="ls-forecast" />
          <circle className="ls-ping is-today" cx={glyph.today.x} cy={glyph.today.y} r="4" />
          <circle className="ls-dot is-today" cx={glyph.today.x} cy={glyph.today.y} r="3.5" />
          {story.budget && glyph.cross && (
            <>
              <circle className="ls-ping is-cross" cx={glyph.cross.x} cy={glyph.cross.y} r="4" />
              <circle className="ls-dot is-cross" cx={glyph.cross.x} cy={glyph.cross.y} r="3.5" />
            </>
          )}
        </svg>
      </div>
      <div className="ls-scanhead" />

      <span className={`ls-chip is-${story.tone}`}>
        <ChipIcon size={13} aria-hidden="true" />
        {story.text}
      </span>
    </div>
  )
}

/**
 * The welcome screen's live animation: CloudGuard at work, in miniature.
 *
 * Both pipelines run in front of the visitor: a scan head draws the month's
 * spend into a forecast that meets (or clears) the budget, while scored
 * network windows stream in and the anomalies light up. With the pipelines'
 * results in hand it replays them; before that it is labelled an example.
 * Decorative for assistive tech: the feature strip carries the same figures.
 */
export default function LiveScan({ forecast, security, events }) {
  const [reduced] = useState(prefersReducedMotion)
  const [now, setNow] = useState(() => new Date())
  const [tick, setTick] = useState(0)
  const [paused, setPaused] = useState(false)

  const realFeed = security ? liveFeed(events, security.top_anomalies) : null
  const live = Boolean(forecast && realFeed)
  const feed = live ? realFeed : SAMPLE_FEED
  const story = forecastStory(live ? forecast : null)

  // Rows enter one at a time; a short feed is shown as it is rather than looped
  // into duplicates on screen.
  const cycling = !reduced && feed.length > FEED_ROWS

  useEffect(() => {
    if (reduced) return undefined
    const timer = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(timer)
  }, [reduced])

  useEffect(() => {
    if (!cycling || paused) return undefined
    const timer = setInterval(() => setTick((value) => value + 1), FEED_STEP_MS)
    return () => clearInterval(timer)
  }, [cycling, paused])

  // Newest first, opening on the first detection so the very first frame
  // shows what the model is for. One extra row sits below the fold so the
  // push has something to push out.
  const start = Math.max(0, feed.findIndex((row) => row.severity))
  const count = cycling ? FEED_ROWS + 1 : Math.min(feed.length, FEED_ROWS)
  const rows = Array.from({ length: count }, (_, i) => {
    const n = feed.length
    return feed[(((start + tick - i) % n) + n) % n]
  })
  const arrived = cycling && tick > 0
  const alert = arrived && Boolean(rows[0]?.severity)
  const anomalyCount = security?.anomaly_count

  return (
    <div
      className={`live-scan ${alert ? 'is-alert' : ''}`}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
    >
      <div className="ls-head">
        <span className="ls-shield">
          {alert ? <ShieldAlert size={15} /> : <ShieldCheck size={15} />}
        </span>
        <span className="ls-title">Live scan</span>
        <span className={`ls-badge ${live ? 'is-live' : 'is-example'}`}>
          <span className="ls-badge-dot" />
          {live ? 'Live' : 'Example'}
        </span>
        <span className="ls-clock">{clockTime(now.toISOString())}</span>
      </div>

      <section className="ls-panel">
        <div className="ls-panel-head">
          <span className="ls-panel-title">Spend forecast</span>
          <span className="ls-model">Prophet</span>
          {live && story.projected != null && (
            <span className="ls-panel-value">{money(story.projected)}</span>
          )}
        </div>
        {/* Keyed so a change of story restarts the scan instead of jumping. */}
        <SpendChart key={`${story.variant}-${story.budget}`} story={story} />
      </section>

      <section className="ls-panel">
        <div className="ls-panel-head">
          <span className="ls-panel-title">Threat detection</span>
          <span className="ls-model">IsolationForest</span>
          {live && anomalyCount != null && (
            <span className="ls-panel-value">{anomalyCount} flagged</span>
          )}
        </div>
        <div className="ls-feed">
          {/* Remounted per arrival, which replays the push-down animation. */}
          <div className={cycling ? 'ls-feed-rows is-pushing' : 'ls-feed-rows'} key={tick}>
            {rows.map((row, index) => (
              <div
                key={`${row.key}-${index}`}
                className={`ls-row ${row.severity ? 'is-flagged' : ''} ${index === 0 && arrived ? 'is-new' : ''}`}
              >
                <span className="ls-row-icon">
                  {row.severity ? <ShieldAlert size={14} /> : <Check size={13} />}
                </span>
                <span className="ls-row-type">{row.type}</span>
                {row.source && <span className="ls-row-source">{row.source}</span>}
                {row.severity
                  ? <Badge variant={severityClass(row.severity)}>{row.severity}</Badge>
                  : <Badge>Routine</Badge>}
              </div>
            ))}
          </div>
        </div>
      </section>
    </div>
  )
}
