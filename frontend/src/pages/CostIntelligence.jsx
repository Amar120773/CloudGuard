import React from 'react'
import {
  AlertTriangle, CalendarClock, Gauge, Play, Target, TrendingUp, Wallet,
} from 'lucide-react'

import ForecastChart from '../charts/ForecastChart'
import ServiceBars from '../charts/ServiceBars'
import {
  Badge, Banner, ChartSkeleton, Delta, EmptyState, ErrorState, KeyValue,
  SectionLabel, StatTile, TableSkeleton,
} from '../components/Primitives'
import { useDashboard } from '../state/DashboardContext'
import { money, number, percent, riskClass, shortDate, shortService } from '../utils/format'

export default function CostIntelligence() {
  const {
    cost, forecast, loading, error, pipelineByName,
    reload, runForecast, isRunning, task,
  } = useDashboard()

  if (error && !cost) {
    return <ErrorState error={error} onRetry={reload} onRun={() => runForecast()} runLabel="Run forecast" />
  }

  const forecastPipeline = pipelineByName.cost_forecast
  const budget = forecast?.budget
  const accuracy = forecast?.accuracy

  // Only the forecast carries per-service projections; history has totals alone.
  const hasProjection = Boolean(forecast?.service_breakdown?.length)
  const serviceRows = hasProjection ? forecast.service_breakdown : (cost?.by_service || [])

  return (
    <>
      {forecast?.spending_warning && (
        <Banner variant={budget?.budget_breach_expected ? 'banner-err' : 'banner-warn'} icon={AlertTriangle}>
          <strong>Spending warning. </strong>{forecast.spending_warning}
        </Banner>
      )}

      {forecast && forecast.model_name !== 'prophet' && (
        <Banner variant="banner-warn" icon={AlertTriangle}>
          <strong>Fallback model in use. </strong>
          Prophet could not be fitted, so a deterministic trend + weekly seasonality
          estimator produced this forecast. Treat the interval as approximate.
        </Banner>
      )}

      {forecastPipeline && !forecastPipeline.ready && (
        <Banner
          variant="banner-info"
          icon={CalendarClock}
          action={(
            <button type="button" className="btn btn-sm" onClick={() => runForecast()} disabled={isRunning}>
              <Play size={13} /> Run forecast
            </button>
          )}
        >
          <strong>No forecast cached. </strong>{forecastPipeline.message}
        </Banner>
      )}

      {/* ------------------------------------------------------------- tiles */}
      <div className="tile-grid">
        <StatTile
          loading={loading && !cost}
          label="Month to date"
          icon={Wallet}
          value={money(forecast?.month_to_date ?? cost?.month_to_date, { decimals: 0 })}
          meta={cost ? `${money(cost.daily_average)} / day average` : null}
        />
        <StatTile
          loading={loading && !forecast}
          label="Predicted month end"
          icon={TrendingUp}
          value={forecast ? money(forecast.projected_month_end, { decimals: 0 }) : '—'}
          meta={forecast
            ? `${money(forecast.projected_month_end_lower)} – ${money(forecast.projected_month_end_upper)}`
            : 'Run the forecast to populate'}
          footer={forecast && <Delta value={forecast.trend_pct} />}
        />
        <StatTile
          loading={loading && !forecast}
          label="Budget position"
          icon={Target}
          value={budget ? money(budget.headroom, { decimals: 0 }) : '—'}
          valueClass="sm"
          meta={budget
            ? `${budget.headroom >= 0 ? 'Headroom against' : 'Overrun against'} ${money(budget.monthly_budget)} budget`
            : null}
          footer={budget && (
            <Badge variant={budget.budget_breach_expected ? 'badge-critical' : budget.budget_breach_possible ? 'badge-warning' : 'badge-good'}>
              {budget.budget_breach_expected
                ? 'Breach expected'
                : budget.budget_breach_possible ? 'Breach possible' : 'Within budget'}
            </Badge>
          )}
        />
        <StatTile
          loading={loading && !forecast}
          label="Model accuracy (MAE)"
          icon={Gauge}
          value={accuracy?.mae != null ? `${money(accuracy.mae, { decimals: 2 })}` : '—'}
          valueClass="sm"
          meta={accuracy?.backtest_days
            ? `per day, ${accuracy.backtest_days}-day hold-out backtest`
            : accuracy?.note || 'No backtest available'}
          footer={accuracy?.skill_vs_baseline_pct != null && (
            <Badge variant={accuracy.skill_vs_baseline_pct > 0 ? 'badge-good' : 'badge-warning'}>
              {accuracy.skill_vs_baseline_pct > 0 ? '+' : ''}
              {accuracy.skill_vs_baseline_pct.toFixed(1)}% vs naive
            </Badge>
          )}
        />
      </div>

      {/* ---------------------------------------------------------- forecast */}
      <section className="panel fade-up d1">
        <header className="panel-header">
          <div>
            <h2 className="panel-title">Historical spend and forecast</h2>
            <p className="panel-subtitle">
              {forecast
                ? `${forecast.model_name} · ${forecast.horizon_days}-day horizon · ${Math.round((forecast.accuracy?.backtest_days || 0))}-day backtest`
                : 'Daily totals aggregated from Cost Explorer'}
            </p>
          </div>
          <div className="panel-actions">
            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={() => runForecast({ force: true })}
              disabled={isRunning}
            >
              <Play size={13} className={isRunning ? 'spin' : undefined} />
              {isRunning ? (task?.stage || 'Running…') : 'Retrain'}
            </button>
          </div>
        </header>

        {loading && !cost ? (
          <ChartSkeleton height={320} />
        ) : !cost ? (
          <EmptyState title="No cost history" message="Ingest Cost Explorer data first." />
        ) : (
          <ForecastChart
            history={forecast?.history_tail || cost.daily.slice(-60)}
            forecast={forecast?.forecast || []}
            height={330}
            // The width the forecast was fitted with (PROPHET_INTERVAL_WIDTH).
            intervalLabel={forecast?.interval_width != null
              ? `${Math.round(forecast.interval_width * 100)}% interval`
              : 'forecast interval'}
          />
        )}
      </section>

      {/* ------------------------------------------------- services + detail */}
      <div className="grid grid-2-wide mt-1">
        <section className="panel fade-up d2">
          <header className="panel-header">
            <div>
              <h2 className="panel-title">Service-level spend</h2>
              <p className="panel-subtitle">
                {hasProjection
                ? 'Projected month-end contribution, growth-weighted from the aggregate forecast'
                : 'Total spend over the ingested window — run the forecast for projections'}
              </p>
            </div>
          </header>

          {loading && !cost ? (
            <TableSkeleton rows={6} />
          ) : !serviceRows.length ? (
            <EmptyState title="No service breakdown available" />
          ) : (
            <>
              <ServiceBars
                services={serviceRows}
                metric={hasProjection ? 'projected_month_end' : 'total_cost'}
                limit={8}
              />
              <div className="table-wrap mt-1">
                <table className="data">
                  <thead>
                    <tr>
                      <th>Service</th>
                      <th className="num">Total</th>
                      <th className="num">Daily avg</th>
                      <th className="num">Trend</th>
                      <th className="num">Projected</th>
                      <th>Risk</th>
                    </tr>
                  </thead>
                  <tbody>
                    {serviceRows.map((service) => (
                      <tr key={service.service}>
                        <td style={{ fontWeight: 550 }}>{shortService(service.service)}</td>
                        <td className="num">{money(service.total_cost)}</td>
                        <td className="num">{money(service.daily_average, { decimals: 2 })}</td>
                        <td className="num"><Delta value={service.trend_pct} /></td>
                        <td className="num">
                          {service.projected_month_end != null
                            ? money(service.projected_month_end)
                            : '—'}
                        </td>
                        <td>
                          {service.is_risk
                            ? <Badge variant="badge-serious" icon={AlertTriangle}>Risk</Badge>
                            : <span style={{ color: 'var(--text-muted)' }}>—</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </section>

        <div className="stack">
          <section className="panel fade-up d3">
            <header className="panel-header">
              <div><h2 className="panel-title">Projection detail</h2></div>
            </header>
            {!forecast ? (
              <EmptyState title="Forecast not run" icon={CalendarClock} />
            ) : (
              <div className="kv">
                <KeyValue label="Model" value={forecast.model_name} />
                <KeyValue label="Horizon" value={`${forecast.horizon_days} days`} />
                <KeyValue label="Month to date" value={money(forecast.month_to_date, { decimals: 2 })} />
                <KeyValue label="Next 7 days" value={money(forecast.next_7_days_total, { decimals: 2 })} />
                <KeyValue label="Next 30 days" value={money(forecast.next_30_days_total, { decimals: 2 })} />
                <KeyValue
                  label="Projected month end"
                  value={money(forecast.projected_month_end, { decimals: 2 })}
                />
                <KeyValue
                  label="Interval"
                  value={`${money(forecast.projected_month_end_lower)} – ${money(forecast.projected_month_end_upper)}`}
                />
                <KeyValue label="Trend vs last 14d" value={percent(forecast.trend_pct, 2, { signed: true })} />
                <KeyValue
                  label="Risk level"
                  value={<Badge variant={riskClass(forecast.risk_level)}>{forecast.risk_level}</Badge>}
                />
                <KeyValue
                  label="Days left in month"
                  value={budget?.days_remaining_in_month ?? '—'}
                />
              </div>
            )}
          </section>

          <section className="panel fade-up d4">
            <header className="panel-header">
              <div>
                <h2 className="panel-title">Backtest accuracy</h2>
                <p className="panel-subtitle">Hold-out evaluation, not in-sample fit</p>
              </div>
            </header>
            {!accuracy || !accuracy.backtest_days ? (
              <EmptyState title="No backtest" message={accuracy?.note} icon={Gauge} />
            ) : (
              <div className="kv">
                <KeyValue label="MAE" value={`${money(accuracy.mae, { decimals: 3 })} / day`} />
                <KeyValue label="MAPE" value={percent(accuracy.mape, 2)} />
                <KeyValue label="RMSE" value={money(accuracy.rmse, { decimals: 3 })} />
                <KeyValue label="Hold-out window" value={`${accuracy.backtest_days} days`} />
                <KeyValue
                  label="Seasonal-naive MAE"
                  value={accuracy.baseline_mae != null ? `${money(accuracy.baseline_mae, { decimals: 3 })} / day` : '—'}
                />
                <KeyValue
                  label="Skill vs baseline"
                  value={(
                    <Badge variant={accuracy.skill_vs_baseline_pct > 0 ? 'badge-good' : 'badge-warning'}>
                      {percent(accuracy.skill_vs_baseline_pct, 2, { signed: true })}
                    </Badge>
                  )}
                />
              </div>
            )}
          </section>

          {cost && (
            <section className="panel fade-up">
              <SectionLabel>Ingestion</SectionLabel>
              <div className="kv">
                <KeyValue label="Records analysed" value={number(cost.records_analysed)} />
                <KeyValue
                  label="Window"
                  value={`${shortDate(cost.start_date)} – ${shortDate(cost.end_date)}`}
                />
                <KeyValue label="Total ingested spend" value={money(cost.total_cost)} />
                {forecast?.model_detail && (
                  <KeyValue label="Data notes" value={forecast.model_detail} />
                )}
              </div>
            </section>
          )}
        </div>
      </div>
    </>
  )
}
