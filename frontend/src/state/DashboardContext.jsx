import React, { createContext, useCallback, useContext, useMemo } from 'react'

import { api } from '../api/client'
import { useResource } from '../hooks/useResource'
import { useTask } from '../hooks/useTask'

const DashboardContext = createContext(null)

const DASHBOARD_POLL_MS = Number(import.meta.env.VITE_DASHBOARD_POLL_MS || 20000)
const HEALTH_POLL_MS = 30000

/**
 * One aggregate fetch drives every page.
 *
 * `/api/dashboard` always answers 200 with per-pipeline readiness, so the
 * provider never needs to fan out N requests, and pages render partial state
 * while individual pipelines are still warming up.
 */
export function DashboardProvider({ children }) {
  const dashboard = useResource((opts) => api.dashboard(opts), { intervalMs: DASHBOARD_POLL_MS })
  const health = useResource((opts) => api.health(opts), { intervalMs: HEALTH_POLL_MS })

  // After a refresh task completes, pull the new numbers immediately.
  const refreshTask = useTask({
    onComplete: () => {
      dashboard.reload()
      health.reload()
    },
  })

  const runRefresh = useCallback(
    (body = {}) => refreshTask.run(() => api.refreshAll(body)),
    [refreshTask],
  )
  const runForecast = useCallback(
    (body = {}) => refreshTask.run(() => api.runForecast(body)),
    [refreshTask],
  )
  const runAnomalyDetection = useCallback(
    (body = {}) => refreshTask.run(() => api.runAnomalyDetection(body)),
    [refreshTask],
  )

  const value = useMemo(() => {
    const data = dashboard.data
    const pipelines = data?.pipelines || []
    const pipelineByName = Object.fromEntries(pipelines.map((p) => [p.name, p]))

    return {
      // dashboard payload
      data,
      overview: data?.overview || null,
      cost: data?.cost || null,
      forecast: data?.forecast || null,
      security: data?.security || null,
      cloudHealth: data?.cloud_health || null,
      recentEvents: data?.recent_events || [],
      insights: data?.insights || [],
      pipelines,
      pipelineByName,
      degraded: Boolean(data?.degraded),
      // The server decides; a payload without the flag (or not loaded yet)
      // means no, so the control never appears where it would be refused.
      canInjectAnomaly: data?.features?.anomaly_injection === true,
      // Pipelines whose cached data is past its freshness threshold, or whose
      // cache expired after a previously successful run.
      stalePipelines: pipelines.filter((p) => p.stale),
      freshness: data?.freshness || null,

      // fetch state
      loading: dashboard.loading,
      refreshing: dashboard.refreshing,
      error: dashboard.error,
      isStale: dashboard.isStale,
      lastUpdated: dashboard.lastUpdated,
      reload: dashboard.reload,

      // health
      health: health.data,
      healthError: health.error,
      // One process without Redis or Celery, on purpose (REDIS_URL=none): jobs
      // running inline are the design there, not a fallback to warn about.
      standalone: health.data?.mode === 'standalone',

      // background work
      task: refreshTask.task,
      taskError: refreshTask.error,
      isRunning: refreshTask.isRunning,
      runRefresh,
      runForecast,
      runAnomalyDetection,
    }
  }, [dashboard, health, refreshTask, runRefresh, runForecast, runAnomalyDetection])

  return <DashboardContext.Provider value={value}>{children}</DashboardContext.Provider>
}

export function useDashboard() {
  const context = useContext(DashboardContext)
  if (!context) {
    throw new Error('useDashboard must be used inside a DashboardProvider')
  }
  return context
}
