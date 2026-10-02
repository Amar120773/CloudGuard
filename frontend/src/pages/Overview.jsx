import React from 'react'
import {
  Activity, AlertTriangle, Brain, DollarSign, Server, Shield, TrendingUp,
} from 'lucide-react'

import ForecastChart from '../charts/ForecastChart'
import ServiceBars from '../charts/ServiceBars'
import Sparkline from '../charts/Sparkline'
import {
  Badge, Banner, ChartSkeleton, Delta, EmptyState, ErrorState, StatTile, StatusBadge,
} from '../components/Primitives'
import { useDashboard } from '../state/DashboardContext'
import { clockTime, money, relativeAge, riskClass, severityClass } from '../utils/format'

export default function Overview({ onNavigate }) {
  const {
    overview, forecast, cost, recentEvents, insights,
    loading, error, isStale, degraded, pipelineByName, stalePipelines,
    reload, runRefresh, isRunning,
  } = useDashboard()

  if (error && !overview) {
    return <ErrorState error={error} onRetry={reload} onRun={() => runRefresh()} runLabel="Run all pipelines" />
  }

  const forecastReady = pipelineByName.cost_forecast?.ready
  const securityReady = pipelineByName.security_anomalies?.ready
  const notReadyCount = Object.values(pipelineByName).filter((p) => !p.ready).length

  return (
    <>
      {isStale && (
        <Banner variant="banner-warn" icon={AlertTriangle}>
          <strong>Live updates interrupted.</strong> Showing the last values the API returned
          successfully; retrying automatically.
        </Banner>
      )}

      {stalePipelines.length > 0 && (
        <Banner
          variant="banner-warn"
          icon={AlertTriangle}
          action={(
            <button type="button" className="btn btn-sm" onClick={() => runRefresh()} disabled={isRunning}>
              Refresh
            </button>
          )}
        >
          <strong>
            {stalePipelines.length} pipeline{stalePipelines.length === 1 ? '' : 's'} serving
            stale data.
          </strong>{' '}
          {stalePipelines
            .map((p) => `${p.name.replace(/_/g, ' ')} (${relativeAge(p.age_seconds)})`)
            .join(', ')}
          . Showing the last successful result.
        </Banner>
      )}

      {!loading && notReadyCount > 0 && (
        <Banner
          variant="banner-info"
          icon={Activity}
          action={(
            <button type="button" className="btn btn-sm" onClick={() => runRefresh()} disabled={isRunning}>
              Run now
            </button>
          )}
        >
          <strong>{notReadyCount} pipeline{notReadyCount === 1 ? '' : 's'} not yet run.</strong>{' '}
          Cost and security intelligence appear once the background tasks complete.
        </Banner>
      )}

      {degraded && !notReadyCount && (
        <Banner variant="banner-warn" icon={AlertTriangle}>
          <strong>Running in degraded mode.</strong> Redis or the Celery worker is unavailable, so
          results are cached per-process and tasks run inline.
        </Banner>
      )}

      {/* ----------------------------------------------------- headline tiles */}
      <div className="tile-grid">
        <StatTile
          loading={loading && !overview}
          label="Current spend (MTD)"
          icon={DollarSign}
          value={money(overview?.current_spend_mtd, { decimals: 0 })}
          meta={cost ? `${money(cost.daily_average)} / day average` : 'Month to date'}
          footer={cost?.daily?.length > 1 && (
            <Sparkline
              values={cost.daily.slice(-30).map((d) => d.cost)}
              color="var(--series-1)"
            />
          )}
          style={{ animationDelay: '0.02s' }}
        />
        <StatTile
          loading={loading && !overview}
          label="Forecast month-end"
          icon={TrendingUp}
          value={forecastReady ? money(overview?.forecast_month_end, { decimals: 0 }) : '—'}
          meta={
            forecastReady
              ? `${money(overview?.forecast_lower)} – ${money(overview?.forecast_upper)} interval`
              : 'Forecast not run yet'
          }
          footer={forecastReady && (
            <>
              <Delta value={overview?.cost_trend_pct} />
              <Badge variant={riskClass(overview?.cost_risk_level)}>
                {overview?.cost_risk_level} risk
              </Badge>
            </>
          )}
        />
        <StatTile
          loading={loading && !overview}
          label="Anomalies detected"
          icon={Shield}
          value={securityReady ? overview?.anomaly_count ?? 0 : '—'}
          meta={
            securityReady
              ? `of ${overview?.total_security_events} behavioural windows`
              : 'Detection not run yet'
          }
          footer={securityReady && (
            <>
              {overview?.critical_anomalies > 0 && (
                <Badge variant="badge-critical" icon={AlertTriangle}>
                  {overview.critical_anomalies} critical
                </Badge>
              )}
              <span style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
                health {Math.round(overview?.security_health_score ?? 0)}/100
              </span>
            </>
          )}
          score={securityReady ? overview?.security_health_score : null}
        />
        <StatTile
          loading={loading && !overview}
          label="Cloud resources"
          icon={Server}
          value={overview?.active_resources ?? '—'}
          meta={
            overview
              ? `${overview.idle_resources} idle · ${money(overview.potential_monthly_savings)}/mo recoverable`
              : 'Inventory not collected yet'
          }
          footer={overview && (
            <Badge variant={overview.cloud_health_status === 'Healthy' ? 'badge-good' : 'badge-warning'}>
              {overview.cloud_health_status}
            </Badge>
          )}
        />
      </div>

      {/* --------------------------------------------------------- main grid */}
      <div className="grid grid-2-wide">
        <section className="panel fade-up d1">
          <header className="panel-header">
            <div>
              <h2 className="panel-title">Spend trajectory</h2>
              <p className="panel-subtitle">
                {forecastReady
                  ? `Observed daily spend with a ${forecast.horizon_days}-day ${forecast.model_name} forecast`
                  : 'Historical daily spend'}
              </p>
            </div>
            <div className="panel-actions">
              <button type="button" className="btn btn-sm" onClick={() => onNavigate('cost')}>
                Details
              </button>
            </div>
          </header>

          {loading && !cost ? (
            <ChartSkeleton />
          ) : !cost ? (
            <EmptyState
              title="No cost history yet"
              message="Cost Explorer ingestion has not run."
              action={(
                <button type="button" className="btn btn-primary btn-sm" onClick={() => runRefresh()}>
                  Ingest now
                </button>
              )}
            />
          ) : (
            <ForecastChart
              history={forecast?.history_tail || cost.daily.slice(-60)}
              forecast={forecast?.forecast || []}
              height={360}
            />
          )}
        </section>

        <section className="panel fade-up d2">
          <header className="panel-header">
            <div>
              <h2 className="panel-title">Recent events</h2>
              <p className="panel-subtitle">Newest behavioural windows, scored</p>
            </div>
            <div className="panel-actions">
              <button type="button" className="btn btn-sm" onClick={() => onNavigate('security')}>
                All events
              </button>
            </div>
          </header>

          {loading && !recentEvents.length ? (
            <ChartSkeleton height={180} />
          ) : !recentEvents.length ? (
            <EmptyState
              title="No events scored yet"
              message="Run anomaly detection to populate the feed."
              icon={Shield}
            />
          ) : (
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Event</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {recentEvents.slice(0, 8).map((event) => (
                    <tr key={event.event_id} className={event.status === 'ANOMALY' ? 'is-anomaly' : undefined}>
                      <td className="mono">{clockTime(event.timestamp)}</td>
                      <td>{event.event_type}</td>
                      <td><StatusBadge status={event.status} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>

      {/* ------------------------------------------------- services + insight */}
      <div className="grid grid-2 mt-1">
        <section className="panel fade-up d3">
          <header className="panel-header">
            <div>
              <h2 className="panel-title">Spend by service</h2>
              <p className="panel-subtitle">
                {overview?.cost_risk_services
                  ? `${overview.cost_risk_services} service(s) flagged as a cost risk`
                  : 'Share of total spend over the ingested window'}
              </p>
            </div>
          </header>
          {loading && !cost ? (
            <ChartSkeleton height={200} />
          ) : !cost?.by_service?.length ? (
            <EmptyState title="No service breakdown" message="Ingest cost data to see this." />
          ) : (
            <ServiceBars services={cost.by_service} limit={7} />
          )}
        </section>

        <section className="panel fade-up d4">
          <header className="panel-header">
            <div>
              <h2 className="panel-title">Top AI insight</h2>
              <p className="panel-subtitle">Highest-severity finding across both models</p>
            </div>
            <div className="panel-actions">
              <button type="button" className="btn btn-sm" onClick={() => onNavigate('insights')}>
                All insights
              </button>
            </div>
          </header>

          {loading && !insights.length ? (
            <ChartSkeleton height={160} />
          ) : !insights.length ? (
            <EmptyState title="No insights yet" icon={Brain} />
          ) : (
            <div className="stack">
              {insights.slice(0, 2).map((insight) => (
                <div key={insight.insight_id} style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                  <div className="row">
                    <Badge variant={severityClass(insight.severity)} icon={AlertTriangle}>
                      {insight.severity}
                    </Badge>
                    <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
                      {insight.source_model}
                    </span>
                  </div>
                  <strong style={{ fontSize: '0.95rem', letterSpacing: '-0.01em' }}>
                    {insight.title}
                  </strong>
                  {insight.model_signal && (
                    <div className="insight-signal">{insight.model_signal}</div>
                  )}
                  <p className="insight-narrative">{insight.narrative}</p>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>

      {/* --------------------------------------------------- pipeline status */}
      <section className="panel fade-up mt-1">
        <header className="panel-header">
          <div>
            <h2 className="panel-title">Pipeline status</h2>
            <p className="panel-subtitle">
              Every value above comes from one of these background pipelines
            </p>
          </div>
        </header>
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Pipeline</th>
                <th>State</th>
                <th className="num">Cache age</th>
                <th>Detail</th>
              </tr>
            </thead>
            <tbody>
              {(Object.values(pipelineByName).length ? Object.values(pipelineByName) : []).map((pipeline) => (
                <tr key={pipeline.name}>
                  <td style={{ fontWeight: 550 }}>{pipeline.name.replace(/_/g, ' ')}</td>
                  <td>
                    <Badge
                      variant={
                        pipeline.state === 'fresh' ? 'badge-good'
                          : pipeline.state === 'stale' ? 'badge-warning'
                            : pipeline.state === 'expired' ? 'badge-serious'
                              : ''
                      }
                    >
                      {pipeline.state}
                    </Badge>
                  </td>
                  <td className="num">{pipeline.age_seconds != null ? `${pipeline.age_seconds}s` : '—'}</td>
                  <td style={{ color: 'var(--text-muted)' }}>{pipeline.message || '—'}</td>
                </tr>
              ))}
              {!Object.values(pipelineByName).length && (
                <tr><td colSpan={4} style={{ color: 'var(--text-muted)' }}>Waiting for the API…</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </>
  )
}
