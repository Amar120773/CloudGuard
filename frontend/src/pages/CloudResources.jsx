import React, { useState } from 'react'
import {
  Activity, Cpu, MoonStar, Play, Search, Server,
} from 'lucide-react'

import { api } from '../api/client'
import {
  Badge, Banner, EmptyState, ErrorState, StatTile, TableSkeleton,
} from '../components/Primitives'
import { useResource } from '../hooks/useResource'
import { useDashboard } from '../state/DashboardContext'
import { money, number, percent, utilizationColor } from '../utils/format'

const STATUS_OPTIONS = [
  { value: '', label: 'All states' },
  { value: 'running', label: 'Running' },
  { value: 'stopped', label: 'Stopped' },
]

const ENV_OPTIONS = [
  { value: '', label: 'All environments' },
  { value: 'production', label: 'Production' },
  { value: 'staging', label: 'Staging' },
  { value: 'development', label: 'Development' },
]

export default function CloudResources() {
  const { cloudHealth, pipelineByName, runRefresh, isRunning } = useDashboard()

  const [status, setStatus] = useState('')
  const [environment, setEnvironment] = useState('')
  const [search, setSearch] = useState('')
  const [idleOnly, setIdleOnly] = useState(false)

  const ready = Boolean(pipelineByName.cloud_resources?.ready)

  // Filtering happens server-side so the page scales past a demo-sized fleet.
  const resources = useResource(
    (opts) => api.cloudResources({ status, environment, search, idleOnly, ...opts }),
    { deps: [status, environment, search, idleOnly], enabled: ready, intervalMs: 45000 },
  )

  const metrics = useResource((opts) => api.cloudMetrics(opts), { enabled: ready })

  if (!ready) {
    return (
      <Banner
        variant="banner-info"
        icon={Server}
        action={(
          <button type="button" className="btn btn-sm" onClick={() => runRefresh()} disabled={isRunning}>
            <Play size={13} /> Collect inventory
          </button>
        )}
      >
        <strong>No inventory cached. </strong>
        {pipelineByName.cloud_resources?.message || 'EC2 collection has not run yet.'}
      </Banner>
    )
  }

  if (resources.error && !resources.hasData) {
    return <ErrorState error={resources.error} onRetry={resources.reloadBlocking} />
  }

  const data = resources.data
  const list = data?.resources || []

  return (
    <>
      <div className="tile-grid">
        <StatTile
          loading={!cloudHealth}
          label="Estate health"
          icon={Activity}
          value={cloudHealth ? cloudHealth.status : '—'}
          valueClass="sm"
          meta={cloudHealth ? `score ${cloudHealth.health_score}/100` : null}
          score={cloudHealth?.health_score}
        />
        <StatTile
          loading={resources.loading && !data}
          label="Resources"
          icon={Server}
          value={data?.total_resources ?? '—'}
          meta={data ? `${data.running} running · ${data.stopped} stopped` : null}
        />
        <StatTile
          loading={resources.loading && !data}
          label="Idle waste"
          icon={MoonStar}
          value={data?.idle_resources ?? '—'}
          meta={data ? `${money(data.potential_monthly_savings)}/mo recoverable` : null}
          footer={data?.idle_resources > 0 && (
            <Badge variant="badge-warning">Review for right-sizing</Badge>
          )}
        />
        <StatTile
          loading={metrics.loading && !metrics.data}
          label="Average CPU"
          icon={Cpu}
          value={metrics.data?.average_cpu != null ? percent(metrics.data.average_cpu, 1) : '—'}
          meta={metrics.data?.peak_cpu != null
            ? `peak ${percent(metrics.data.peak_cpu, 1)} · ${number(metrics.data.total_network_out_mb)} MB egress`
            : 'From CloudWatch'}
        />
      </div>

      <section className="panel fade-up d1">
        <header className="panel-header">
          <div>
            <h2 className="panel-title">Resource inventory</h2>
            <p className="panel-subtitle">
              Read from EC2 and joined with CloudWatch utilisation · {data?.region}
              {' · '}{money(data?.estimated_monthly_cost)}/mo estimated
            </p>
          </div>
          <div className="panel-actions">
            {resources.refreshing && (
              <span style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>Updating…</span>
            )}
          </div>
        </header>

        <div className="filters">
          <label className="input-search">
            <Search size={14} aria-hidden="true" />
            <span className="sr-only">Search resources</span>
            <input
              className="input"
              type="search"
              placeholder="Search id, name, type or owner"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </label>

          <label>
            <span className="sr-only">Filter by state</span>
            <select className="select" value={status} onChange={(e) => setStatus(e.target.value)}>
              {STATUS_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </label>

          <label>
            <span className="sr-only">Filter by environment</span>
            <select className="select" value={environment} onChange={(e) => setEnvironment(e.target.value)}>
              {ENV_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </label>

          <button
            type="button"
            className="chip"
            aria-pressed={idleOnly}
            onClick={() => setIdleOnly((v) => !v)}
          >
            <MoonStar size={13} /> Idle only
          </button>

          {(status || environment || search || idleOnly) && (
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => { setStatus(''); setEnvironment(''); setSearch(''); setIdleOnly(false) }}
            >
              Clear
            </button>
          )}
        </div>

        {resources.loading && !data ? (
          <TableSkeleton rows={8} />
        ) : !list.length ? (
          <EmptyState
            title="No resources match"
            message="Adjust the filters, or collect inventory again."
            icon={Server}
          />
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                {/* On phones the `col-optional` columns are hidden; their type,
                    environment and note move under the resource name instead. */}
                <tr>
                  <th>Resource</th>
                  <th className="col-optional">Type</th>
                  <th className="col-optional">Env</th>
                  <th>State</th>
                  <th className="num">CPU</th>
                  <th className="num col-optional">Egress</th>
                  <th className="num">Est. cost / mo</th>
                  <th className="col-optional">Note</th>
                </tr>
              </thead>
              <tbody>
                {list.map((resource) => (
                  <tr key={resource.resource_id} className={resource.idle ? 'is-anomaly' : undefined}>
                    <td>
                      <div style={{ fontWeight: 550 }}>{resource.name || resource.resource_id}</div>
                      <div className="resource-id" style={{ fontSize: '0.75rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                        {resource.resource_id}
                      </div>
                      <div className="only-narrow resource-narrow-meta">
                        {resource.instance_type} · {resource.environment || 'no environment tag'}
                      </div>
                      {(resource.idle || resource.optimization_hint) && (
                        <div className="only-narrow resource-narrow-note">
                          {resource.idle && <Badge variant="badge-warning" icon={MoonStar}>Idle</Badge>}{' '}
                          {resource.optimization_hint || ''}
                        </div>
                      )}
                    </td>
                    <td className="col-optional">{resource.instance_type}</td>
                    <td className="col-optional" style={{ color: 'var(--text-secondary)' }}>{resource.environment || '—'}</td>
                    <td>
                      <Badge variant={resource.status === 'running' ? 'badge-good' : ''}>
                        {resource.status}
                      </Badge>
                    </td>
                    <td className="num" style={{ color: utilizationColor(resource.cpu_utilization) }}>
                      {resource.cpu_utilization != null ? percent(resource.cpu_utilization, 1) : '—'}
                    </td>
                    <td className="num col-optional">
                      {resource.network_out_mb != null ? `${number(resource.network_out_mb)} MB` : '—'}
                    </td>
                    <td className="num" style={{ fontWeight: 650 }}>{money(resource.estimated_cost, { decimals: 2 })}</td>
                    <td className="col-optional" style={{ color: 'var(--text-muted)', fontSize: '0.8rem', maxWidth: 260 }}>
                      {resource.idle && <Badge variant="badge-warning" icon={MoonStar}>Idle</Badge>}{' '}
                      {resource.optimization_hint || ''}
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <td colSpan={8}>
                    {list.length} of {data.total_resources} resources ·{' '}
                    {money(list.reduce((sum, r) => sum + (r.estimated_cost || 0), 0), { decimals: 2 })} shown
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>
        )}
      </section>
    </>
  )
}
