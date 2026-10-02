import React, { useMemo, useState } from 'react'
import {
  Activity, Play, Search, Shield, ShieldAlert, Target, X, Zap,
} from 'lucide-react'

import AnomalyTimeline from '../charts/AnomalyTimeline'
import { api } from '../api/client'
import {
  Badge, Banner, ChartSkeleton, EmptyState, ErrorState, KeyValue,
  SectionLabel, StatTile, StatusBadge, TableSkeleton,
} from '../components/Primitives'
import { useResource } from '../hooks/useResource'
import { useDashboard } from '../state/DashboardContext'
import {
  bytes, clockTime, dateTime, number, percent, severityClass, severityColor,
} from '../utils/format'

const FILTERS = [
  { id: 'all', label: 'All events' },
  { id: 'ANOMALY', label: 'Anomalies only' },
  { id: 'ROUTINE', label: 'Routine only' },
]

export default function SecurityAnalytics() {
  const {
    security, recentEvents, loading, error, pipelineByName,
    reload, runAnomalyDetection, isRunning, task,
  } = useDashboard()

  const [filter, setFilter] = useState('all')
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState(null)

  // The event feed is paginated server-side, so it is fetched separately from
  // the dashboard aggregate.
  const feed = useResource(
    (opts) => api.securityEvents({
      limit: 200,
      status: filter === 'all' ? undefined : filter,
      ...opts,
    }),
    { deps: [filter], enabled: Boolean(pipelineByName.security_anomalies?.ready) },
  )

  const events = useMemo(() => {
    const list = feed.data?.events || recentEvents || []
    if (!search.trim()) return list
    const needle = search.trim().toLowerCase()
    return list.filter(
      (event) =>
        event.event_type?.toLowerCase().includes(needle) ||
        event.source?.toLowerCase().includes(needle) ||
        event.metric?.toLowerCase().includes(needle),
    )
  }, [feed.data, recentEvents, search])

  const pipeline = pipelineByName.security_anomalies

  if (error && !security) {
    return (
      <ErrorState
        error={error}
        onRetry={reload}
        onRun={() => runAnomalyDetection()}
        runLabel="Run detection"
      />
    )
  }

  const accuracy = security?.accuracy

  return (
    <>
      {pipeline && !pipeline.ready && (
        <Banner
          variant="banner-info"
          icon={Shield}
          action={(
            <button type="button" className="btn btn-sm" onClick={() => runAnomalyDetection()} disabled={isRunning}>
              <Play size={13} /> Run detection
            </button>
          )}
        >
          <strong>No scored events cached. </strong>{pipeline.message}
        </Banner>
      )}

      {security?.anomaly_count > 0 && (
        <Banner variant="banner-warn" icon={ShieldAlert}>
          <strong>
            {security.anomaly_count} statistically unusual window
            {security.anomaly_count === 1 ? '' : 's'} detected.
          </strong>{' '}
          Flagged by IsolationForest without a rule written for these specific patterns.
        </Banner>
      )}

      {/* ------------------------------------------------------------- tiles */}
      <div className="tile-grid">
        <StatTile
          loading={loading && !security}
          label="Security health"
          icon={Shield}
          value={security ? `${Math.round(security.security_health_score)}` : '—'}
          meta="out of 100, weighted by anomaly severity"
          score={security?.security_health_score}
        />
        <StatTile
          loading={loading && !security}
          label="Anomalies"
          icon={ShieldAlert}
          value={security?.anomaly_count ?? '—'}
          meta={security ? `of ${number(security.total_events)} windows (${percent(security.anomaly_rate_pct, 2)})` : null}
          footer={security && Object.entries(security.severity_breakdown || {}).map(([sev, count]) => (
            <Badge key={sev} variant={severityClass(sev)}>{count} {sev.toLowerCase()}</Badge>
          ))}
        />
        <StatTile
          loading={loading && !security}
          label="Detection precision"
          icon={Target}
          value={accuracy?.precision != null ? accuracy.precision.toFixed(2) : '—'}
          valueClass="sm"
          meta={accuracy?.recall != null
            ? `recall ${accuracy.recall.toFixed(2)} · F1 ${accuracy.f1_score?.toFixed(2)}`
            : accuracy?.note?.slice(0, 70)}
          footer={accuracy?.labelled_anomalies > 0 && (
            <span style={{ fontSize: '0.76rem', color: 'var(--text-muted)' }}>
              TP {accuracy.true_positives} · FP {accuracy.false_positives} · FN {accuracy.false_negatives}
            </span>
          )}
        />
        <StatTile
          loading={loading && !security}
          label="Model"
          icon={Activity}
          value={security?.model_name || '—'}
          valueClass="sm"
          meta={security ? `contamination ${security.contamination} · ${security.features_used?.length} features` : null}
        />
      </div>

      {/* ---------------------------------------------------------- timeline */}
      <section className="panel fade-up d1">
        <header className="panel-header">
          <div>
            <h2 className="panel-title">Anomaly score over time</h2>
            <p className="panel-subtitle">
              {security?.score_threshold != null
                ? 'Each dot is one observation window; the dashed line is the decision threshold'
                : 'Each dot is one observation window'}
            </p>
          </div>
          <div className="panel-actions">
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => runAnomalyDetection({ inject_anomaly: true })}
              disabled={isRunning}
              title="Append an extreme synthetic event and re-score, to demonstrate detection"
            >
              <Zap size={13} /> Inject test anomaly
            </button>
            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={() => runAnomalyDetection({ force: true })}
              disabled={isRunning}
            >
              <Play size={13} className={isRunning ? 'spin' : undefined} />
              {isRunning ? (task?.stage || 'Running…') : 'Re-score'}
            </button>
          </div>
        </header>

        {loading && !security ? (
          <ChartSkeleton height={240} />
        ) : !events.length ? (
          <EmptyState title="No scored events" message="Run detection to populate the timeline." icon={Shield} />
        ) : (
          <AnomalyTimeline
            events={feed.data?.events || recentEvents || []}
            // The cut the detector applied (ANOMALY_SCORE_THRESHOLD), not a copy of its default.
            threshold={security?.score_threshold}
            onSelect={setSelected}
          />
        )}
      </section>

      {/* ------------------------------------------------------- event table */}
      <section className="panel fade-up d2 mt-1">
        <header className="panel-header">
          <div>
            <h2 className="panel-title">Event feed</h2>
            <p className="panel-subtitle">
              {events.length} event{events.length === 1 ? '' : 's'} shown · click a row for detail
            </p>
          </div>
        </header>

        <div className="filters">
          {FILTERS.map((option) => (
            <button
              key={option.id}
              type="button"
              className="chip"
              aria-pressed={filter === option.id}
              onClick={() => setFilter(option.id)}
            >
              {option.label}
            </button>
          ))}
          <label className="input-search" style={{ marginLeft: 'auto' }}>
            <Search size={14} aria-hidden="true" />
            <span className="sr-only">Search events</span>
            <input
              className="input"
              type="search"
              placeholder="Search event, source or metric"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </label>
        </div>

        {/* Skeleton only while there is nothing to show. The dashboard's recent
            events stay mounted while the full feed loads, so the table does not
            flash rows -> skeleton -> rows (a click mid-flash hit a detached row). */}
        {feed.loading && !feed.hasData && !events.length ? (
          <TableSkeleton rows={8} />
        ) : !events.length ? (
          <EmptyState
            title="No events match"
            message={search ? `Nothing matches “${search}”.` : 'Try a different filter.'}
          />
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Event</th>
                  <th>Source</th>
                  <th>Dominant metric</th>
                  <th className="num">Score</th>
                  <th>Status</th>
                  <th>Severity</th>
                </tr>
              </thead>
              <tbody>
                {events.slice(0, 120).map((event) => (
                  <tr
                    key={event.event_id}
                    className={`clickable ${event.status === 'ANOMALY' ? 'is-anomaly' : ''}`}
                    onClick={() => setSelected(event)}
                    // A clickable row must also be reachable without a mouse.
                    // tabIndex + key handling keeps native table semantics,
                    // which role="button" on a <tr> would destroy.
                    tabIndex={0}
                    aria-label={`Inspect ${event.event_type} at ${clockTime(event.timestamp)}`}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault()
                        setSelected(event)
                      }
                    }}
                  >
                    <td className="mono">{clockTime(event.timestamp)}</td>
                    <td style={{ fontWeight: 550 }}>{event.event_type}</td>
                    <td className="mono">{event.source}</td>
                    <td style={{ color: 'var(--text-secondary)' }}>{event.metric}</td>
                    <td className="num" style={{ fontWeight: 650, color: severityColor(event.severity) }}>
                      {event.anomaly_score.toFixed(1)}
                    </td>
                    <td><StatusBadge status={event.status} /></td>
                    <td>
                      <Badge variant={severityClass(event.severity)}>{event.severity}</Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
              {events.length > 120 && (
                <tfoot>
                  <tr><td colSpan={7}>Showing the first 120 of {events.length} events.</td></tr>
                </tfoot>
              )}
            </table>
          </div>
        )}
      </section>

      {selected && <EventDrawer event={selected} onClose={() => setSelected(null)} />}
    </>
  )
}

/* ------------------------------------------------------------------ drawer */
function EventDrawer({ event, onClose }) {
  const metrics = event.metrics || {}
  const maxZ = Math.max(...(event.deviations || []).map((d) => Math.abs(d.z_score)), 1)

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} role="presentation" />
      <aside className="drawer" role="dialog" aria-modal="true" aria-label="Event detail">
        <header className="drawer-head">
          <span
            className="state-icon"
            style={{
              background: event.status === 'ANOMALY' ? 'var(--status-critical-bg)' : 'var(--status-neutral-bg)',
              color: severityColor(event.severity),
              width: 36, height: 36,
            }}
          >
            {event.status === 'ANOMALY' ? <ShieldAlert size={18} /> : <Shield size={18} />}
          </span>
          <div>
            <h2>{event.event_type}</h2>
            <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
              {dateTime(event.timestamp)}
            </p>
          </div>
          <button type="button" className="drawer-close" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </header>

        <div className="drawer-body">
          <div className="row">
            <StatusBadge status={event.status} />
            <Badge variant={severityClass(event.severity)}>{event.severity}</Badge>
            <Badge variant="badge-info">score {event.anomaly_score.toFixed(1)}/100</Badge>
            {event.simulated_anomaly && (
              <Badge variant="badge-warning" title="Seeded outlier, used to compute precision and recall">
                seeded
              </Badge>
            )}
          </div>

          {event.context && (
            <div>
              <SectionLabel>Why this was flagged</SectionLabel>
              <p className="prose">{event.context}</p>
            </div>
          )}

          {event.recommended_action && (
            <div>
              <SectionLabel>Recommended action</SectionLabel>
              <p className="prose" style={{ color: 'var(--text-primary)' }}>
                {event.recommended_action}
              </p>
            </div>
          )}

          {event.deviations?.length > 0 && (
            <div>
              <SectionLabel>Feature deviation from baseline</SectionLabel>
              {event.deviations.map((deviation) => (
                <div className="dev-row" key={deviation.feature}>
                  <div className="dev-head">
                    <span className="name">{deviation.feature.replace(/_/g, ' ')}</span>
                    <span className="z" style={{ color: severityColor(event.severity) }}>
                      {deviation.z_score > 0 ? '+' : ''}{deviation.z_score.toFixed(2)}σ
                    </span>
                  </div>
                  <div className="dev-track">
                    <div
                      className="dev-fill"
                      style={{
                        width: `${(Math.abs(deviation.z_score) / maxZ) * 100}%`,
                        background: severityColor(event.severity),
                      }}
                    />
                  </div>
                </div>
              ))}
            </div>
          )}

          <div>
            <SectionLabel>Observation window metrics</SectionLabel>
            <div className="kv">
              <KeyValue label="Traffic volume" value={bytes(metrics.traffic_volume)} />
              <KeyValue label="Network transfer" value={`${number(metrics.network_transfer, 1)} MB`} />
              <KeyValue label="Connections" value={number(metrics.connection_count)} />
              <KeyValue label="Requests / min" value={number(metrics.request_frequency)} />
              <KeyValue label="Failed API requests" value={number(metrics.failed_api_requests)} />
              <KeyValue label="Auth failures" value={number(metrics.authentication_failures)} />
              <KeyValue label="Distinct source IPs" value={number(metrics.distinct_source_ips)} />
            </div>
          </div>

          <div>
            <SectionLabel>Provenance</SectionLabel>
            <div className="kv">
              <KeyValue label="Event ID" value={event.event_id} mono />
              <KeyValue label="Source" value={event.source} mono />
              <KeyValue label="Region" value={event.region} />
              <KeyValue label="Raw decision score" value={event.raw_decision_score?.toFixed(6)} mono />
            </div>
          </div>
        </div>
      </aside>
    </>
  )
}
