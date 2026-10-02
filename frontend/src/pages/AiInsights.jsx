import React from 'react'
import {
  AlertTriangle, ArrowRight, Brain, DollarSign, Info, Server, Settings, Shield,
} from 'lucide-react'

import {
  Badge, ChartSkeleton, EmptyState, ErrorState, SectionLabel,
} from '../components/Primitives'
import { useDashboard } from '../state/DashboardContext'
import { severityClass, severityColor } from '../utils/format'

const CATEGORY_META = {
  COST: { icon: DollarSign, label: 'Cost' },
  SECURITY: { icon: Shield, label: 'Security' },
  RESOURCE: { icon: Server, label: 'Resource' },
  SYSTEM: { icon: Settings, label: 'System' },
}

export default function AiInsights() {
  const { insights, loading, error, reload, runRefresh, isRunning } = useDashboard()

  if (error && !insights.length) {
    return <ErrorState error={error} onRetry={reload} onRun={() => runRefresh()} runLabel="Run all pipelines" />
  }

  if (loading && !insights.length) {
    return (
      <div className="stack">
        <ChartSkeleton height={120} />
        <ChartSkeleton height={120} />
        <ChartSkeleton height={120} />
      </div>
    )
  }

  if (!insights.length) {
    return (
      <EmptyState
        title="No insights yet"
        message="Insights are generated from forecast and anomaly-detection output."
        icon={Brain}
        action={(
          <button type="button" className="btn btn-primary btn-sm" onClick={() => runRefresh()} disabled={isRunning}>
            Run all pipelines
          </button>
        )}
      />
    )
  }

  const grouped = insights.reduce((acc, insight) => {
    const key = insight.category
    acc[key] = acc[key] || []
    acc[key].push(insight)
    return acc
  }, {})

  const order = ['SECURITY', 'COST', 'RESOURCE', 'SYSTEM'].filter((key) => grouped[key]?.length)

  return (
    <>
      <p className="prose" style={{ marginBottom: '1.25rem', maxWidth: '70ch' }}>
        Each insight pairs the headline metric with the model signal behind it (a forecast
        interval or an anomaly score) and the action it implies. Everything here is derived
        from pipeline output — nothing is hardcoded narrative.
      </p>

      {order.map((category, groupIndex) => {
        const meta = CATEGORY_META[category] || { icon: Info, label: category }
        return (
          <div key={category} style={{ marginBottom: '1.75rem' }}>
            <SectionLabel>{meta.label} · {grouped[category].length}</SectionLabel>
            <div className="stack">
              {grouped[category].map((insight, index) => (
                <InsightCard
                  key={insight.insight_id}
                  insight={insight}
                  icon={meta.icon}
                  className={`fade-up d${Math.min(4, groupIndex + index + 1)}`}
                />
              ))}
            </div>
          </div>
        )
      })}
    </>
  )
}

function InsightCard({ insight, icon: Icon, className = '' }) {
  const color = severityColor(insight.severity)

  return (
    <article className={`insight ${className}`}>
      <span
        className="insight-icon"
        style={{ background: 'rgba(255,255,255,0.05)', color }}
        aria-hidden="true"
      >
        <Icon size={17} />
      </span>

      <div className="insight-head">
        <h3 className="insight-title">{insight.title}</h3>
        <Badge
          variant={severityClass(insight.severity)}
          icon={insight.severity === 'CRITICAL' || insight.severity === 'HIGH' ? AlertTriangle : undefined}
        >
          {insight.severity}
        </Badge>
        {insight.source_model && (
          <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
            via {insight.source_model}
          </span>
        )}
        {insight.confidence != null && (
          <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
            · {Math.round(insight.confidence * 100)}% interval
          </span>
        )}
      </div>

      <div className="insight-body">
        <div className="insight-metric" style={{ color }}>{insight.metric}</div>

        {insight.model_signal && (
          <div className="insight-signal" style={{ borderLeftColor: color }}>
            {insight.model_signal}
          </div>
        )}

        <p className="insight-narrative">{insight.narrative}</p>

        {insight.action && (
          <p className="insight-action">
            <ArrowRight size={14} aria-hidden="true" />
            <span><strong>Action:</strong> {insight.action}</span>
          </p>
        )}

        {insight.related_resource_ids?.length > 0 && (
          <div className="row" style={{ gap: '0.35rem' }}>
            <span style={{ fontSize: '0.74rem', color: 'var(--text-muted)' }}>Related:</span>
            {insight.related_resource_ids.slice(0, 4).map((id) => (
              <code
                key={id}
                style={{
                  fontSize: '0.72rem', fontFamily: 'var(--font-mono)',
                  background: 'rgba(255,255,255,0.05)', padding: '0.1rem 0.35rem',
                  borderRadius: 4, color: 'var(--text-secondary)',
                }}
              >
                {id}
              </code>
            ))}
            {insight.related_resource_ids.length > 4 && (
              <span style={{ fontSize: '0.74rem', color: 'var(--text-muted)' }}>
                +{insight.related_resource_ids.length - 4} more
              </span>
            )}
          </div>
        )}
      </div>
    </article>
  )
}
