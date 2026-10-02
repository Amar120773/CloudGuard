/**
 * Dashboard rendering: loaded state, loading state, cold-start fallbacks and
 * error states, driven through the real DashboardProvider against a mocked fetch.
 */
import React from 'react'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import App from '../src/App'
import Overview from '../src/pages/Overview'
import CostIntelligence from '../src/pages/CostIntelligence'
import SecurityAnalytics from '../src/pages/SecurityAnalytics'
import AiInsights from '../src/pages/AiInsights'
import { DashboardProvider } from '../src/state/DashboardContext'
import {
  coldDashboardPayload, dashboardPayload, healthPayload, securityEvents,
} from './fixtures'

/** Route mocked fetch by URL so the provider's own calls work unchanged. */
function mockApi({ dashboard = dashboardPayload, health = healthPayload, overrides = {} } = {}) {
  const fetchMock = vi.fn(async (url) => {
    const path = String(url)
    for (const [fragment, handler] of Object.entries(overrides)) {
      if (path.includes(fragment)) return handler(path)
    }
    if (path.includes('/api/dashboard')) return jsonResponse(dashboard)
    if (path.includes('/api/health')) return jsonResponse(health)
    if (path.includes('/api/security/events')) {
      return jsonResponse({
        total_events: securityEvents.length,
        returned_events: securityEvents.length,
        anomaly_count: 1,
        routine_count: 1,
        events: securityEvents,
        freshness: { cached: true, age_seconds: 5, stale: false, source: 'cache' },
      })
    }
    if (path.includes('/api/cloud/resources')) {
      return jsonResponse({
        region: 'us-east-1', total_resources: 0, running: 0, stopped: 0,
        idle_resources: 0, estimated_monthly_cost: 0, potential_monthly_savings: 0,
        resources: [], freshness: {},
      })
    }
    return jsonResponse({})
  })
  globalThis.fetch = fetchMock
  return fetchMock
}

function jsonResponse(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(body),
  }
}

function renderPage(ui) {
  return render(<DashboardProvider>{ui}</DashboardProvider>)
}

beforeEach(() => {
  vi.restoreAllMocks()
  window.location.hash = ''
})

// ==========================================================================
describe('Overview', () => {
  it('renders headline metrics from the API payload', async () => {
    mockApi()
    renderPage(<Overview onNavigate={() => {}} />)

    expect(await screen.findByText('$483')).toBeInTheDocument()      // MTD
    expect(screen.getByText('$14,641')).toBeInTheDocument()          // forecast
    expect(screen.getByText('5')).toBeInTheDocument()                // anomalies
    expect(screen.getByText(/ELEVATED risk/i)).toBeInTheDocument()
  })

  it('shows the forecast interval alongside the point estimate', async () => {
    mockApi()
    renderPage(<Overview onNavigate={() => {}} />)
    expect(await screen.findByText(/\$14,168 – \$15,117 interval/)).toBeInTheDocument()
  })

  it('renders a loading skeleton before data arrives', () => {
    globalThis.fetch = vi.fn(() => new Promise(() => {}))  // never resolves
    const { container } = renderPage(<Overview onNavigate={() => {}} />)
    expect(container.querySelectorAll('.skeleton').length).toBeGreaterThan(0)
  })

  it('renders the recent event feed with status badges', async () => {
    mockApi()
    renderPage(<Overview onNavigate={() => {}} />)

    expect(await screen.findByText('Authentication failure burst')).toBeInTheDocument()
    expect(screen.getByText('Normal API error rate')).toBeInTheDocument()
    expect(screen.getAllByText('ANOMALY').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Routine').length).toBeGreaterThan(0)
  })

  it('reports pipeline readiness', async () => {
    mockApi()
    renderPage(<Overview onNavigate={() => {}} />)

    expect(await screen.findByText('Pipeline status')).toBeInTheDocument()
    expect(screen.getByText('cost forecast')).toBeInTheDocument()
    // The badge now carries the four-state freshness value, not ready/pending.
    expect(screen.getAllByText('fresh').length).toBe(5)
  })
})

// ==========================================================================
describe('Overview cold start', () => {
  it('prompts to run the pipelines instead of showing an error', async () => {
    mockApi({ dashboard: coldDashboardPayload })
    renderPage(<Overview onNavigate={() => {}} />)

    expect(await screen.findByText(/5 pipelines not yet run/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /run now/i })).toBeInTheDocument()
  })

  it('shows an empty state for the chart rather than a broken axis', async () => {
    mockApi({ dashboard: coldDashboardPayload })
    renderPage(<Overview onNavigate={() => {}} />)
    expect(await screen.findByText('No cost history yet')).toBeInTheDocument()
  })

  it('does not invent numbers when no pipeline has run', async () => {
    mockApi({ dashboard: coldDashboardPayload })
    renderPage(<Overview onNavigate={() => {}} />)

    await screen.findByText(/5 pipelines not yet run/i)
    // Forecast tile must read as unavailable, not as $0 of real spend.
    expect(screen.getByText('Forecast not run yet')).toBeInTheDocument()
  })
})

// ==========================================================================
describe('Error handling', () => {
  it('shows an offline state when the API is unreachable', async () => {
    globalThis.fetch = vi.fn(async () => { throw new TypeError('Failed to fetch') })
    renderPage(<Overview onNavigate={() => {}} />)

    expect(await screen.findByText(/cannot reach the api/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument()
  })

  it('offers to run the pipeline on a 503 pipeline_not_ready', async () => {
    globalThis.fetch = vi.fn(async (url) => {
      if (String(url).includes('/api/dashboard')) {
        return jsonResponse({
          detail: {
            detail: 'Cost forecast results are not available yet.',
            error_code: 'pipeline_not_ready',
            hint: 'Train the model with POST /api/costs/forecast/run.',
          },
        }, 503)
      }
      return jsonResponse(healthPayload)
    })
    renderPage(<CostIntelligence />)

    expect(await screen.findByText(/analysis has not run yet/i)).toBeInTheDocument()
    expect(screen.getByText(/train the model/i)).toBeInTheDocument()
  })

  it('keeps showing data when a background refresh fails', async () => {
    let callCount = 0
    globalThis.fetch = vi.fn(async (url) => {
      const path = String(url)
      if (path.includes('/api/dashboard')) {
        callCount += 1
        if (callCount > 1) throw new TypeError('Failed to fetch')
        return jsonResponse(dashboardPayload)
      }
      return jsonResponse(healthPayload)
    })

    const { rerender } = renderPage(<Overview onNavigate={() => {}} />)
    expect(await screen.findByText('$14,641')).toBeInTheDocument()

    // The value survives subsequent failures rather than blanking out.
    rerender(<DashboardProvider><Overview onNavigate={() => {}} /></DashboardProvider>)
    expect(screen.getByText('$14,641')).toBeInTheDocument()
  })

  it('reports an HTML page served as /api/* instead of waiting forever', async () => {
    // What a static host returns for /api/* when VITE_API_BASE_URL is wrong.
    globalThis.fetch = vi.fn(async () => ({
      ok: true,
      status: 200,
      text: async () => '<!doctype html><html><body><div id="root"></div></body></html>',
    }))
    renderPage(<Overview onNavigate={() => {}} />)

    expect(await screen.findByText('Unexpected response from the API')).toBeInTheDocument()
    expect(screen.getByText(/wrong API address/i)).toBeInTheDocument()
    expect(screen.queryByText(/waiting for the api/i)).not.toBeInTheDocument()
  })
})

// ==========================================================================
describe('Cost Intelligence', () => {
  it('renders the forecast chart with both series in the legend', async () => {
    mockApi()
    const { container } = renderPage(<CostIntelligence />)

    expect(await screen.findByText('Observed spend')).toBeInTheDocument()
    expect(screen.getByText('Prophet forecast')).toBeInTheDocument()
    // Two line paths plus the confidence band.
    await waitFor(() => {
      expect(container.querySelectorAll('svg path').length).toBeGreaterThanOrEqual(3)
    })
  })

  it('surfaces the spending warning', async () => {
    mockApi()
    renderPage(<CostIntelligence />)
    expect(await screen.findByText(/exceeds the \$14,000 budget/i)).toBeInTheDocument()
  })

  it('reports backtest accuracy, not in-sample fit', async () => {
    mockApi()
    renderPage(<CostIntelligence />)

    expect(await screen.findByText('$9.84')).toBeInTheDocument()  // MAE
    expect(screen.getByText(/14-day hold-out backtest/i)).toBeInTheDocument()
    expect(screen.getByText('+19.2% vs naive')).toBeInTheDocument()
  })

  it('orders service bars by the metric it draws', async () => {
    mockApi()
    const { container } = renderPage(<CostIntelligence />)
    await screen.findByText('Service-level spend')

    const names = [...container.querySelectorAll('.hbar-name')].map((n) => n.textContent)
    // RDS has the largest projection even though EC2 has the largest total.
    expect(names[0]).toMatch(/RDS/)
    expect(names[1]).toMatch(/EC2/)
  })

  it('flags risk services with an icon and a label, not colour alone', async () => {
    mockApi()
    renderPage(<CostIntelligence />)
    await screen.findByText('Service-level spend')

    expect(screen.getByText(/flagged as a cost risk/i)).toBeInTheDocument()
    expect(screen.getAllByText('Risk').length).toBeGreaterThan(0)
  })

  it('labels the band with the interval width the forecast was fitted with', async () => {
    mockApi({
      dashboard: {
        ...dashboardPayload,
        forecast: { ...dashboardPayload.forecast, interval_width: 0.9 },
      },
    })
    renderPage(<CostIntelligence />)
    expect(await screen.findByText('90% interval')).toBeInTheDocument()
    expect(screen.queryByText('85% interval')).not.toBeInTheDocument()
  })

  it('does not invent a width the backend did not report', async () => {
    mockApi({
      dashboard: {
        ...dashboardPayload,
        forecast: { ...dashboardPayload.forecast, interval_width: null },
      },
    })
    renderPage(<CostIntelligence />)
    expect(await screen.findByText('forecast interval')).toBeInTheDocument()
    expect(screen.queryByText(/\d+% interval/)).not.toBeInTheDocument()
  })
})

// ==========================================================================
describe('Security Analytics', () => {
  it('renders posture metrics and the evaluation scores', async () => {
    mockApi()
    renderPage(<SecurityAnalytics />)

    expect(await screen.findByText('60')).toBeInTheDocument()   // health score
    expect(screen.getByText('1.00')).toBeInTheDocument()        // precision
    expect(screen.getByText(/recall 1.00/)).toBeInTheDocument()
    expect(screen.getByText('IsolationForest')).toBeInTheDocument()
  })

  it('renders the event table with scores and severities', async () => {
    mockApi()
    renderPage(<SecurityAnalytics />)

    // Wait for the rows themselves; the panel title renders before any data.
    expect((await screen.findAllByText('Authentication failure burst')).length).toBeGreaterThan(0)
    expect(screen.getByText('Event feed')).toBeInTheDocument()
    expect(screen.getByText('98.4')).toBeInTheDocument()
    expect(screen.getAllByText('CRITICAL').length).toBeGreaterThan(0)
  })

  it('keeps the recent events on screen while the full feed loads', async () => {
    // The paginated feed never answers; the dashboard's recent events must
    // stay visible instead of being swapped for a skeleton.
    mockApi({ overrides: { '/api/security/events': () => new Promise(() => {}) } })
    const { container } = renderPage(<SecurityAnalytics />)

    expect((await screen.findAllByText('Authentication failure burst')).length).toBeGreaterThan(0)
    expect(container.querySelectorAll('table.data tbody tr').length).toBe(securityEvents.length)
  })

  // Row interactions use fireEvent rather than userEvent: userEvent's
  // pointer-events check does not resolve correctly for <tr> under jsdom, and
  // fireEvent dispatches exactly the DOM events React binds here.
  it('opens an event drawer with the feature deviations', async () => {
    mockApi()
    renderPage(<SecurityAnalytics />)

    // Wait for the rows, not the panel title: the title renders immediately,
    // the rows only once the paginated feed request resolves.
    const rows = await screen.findAllByText('Authentication failure burst')
    fireEvent.click(rows[rows.length - 1].closest('tr'))

    const drawer = await screen.findByRole('dialog')
    expect(within(drawer).getByText(/why this was flagged/i)).toBeInTheDocument()
    expect(within(drawer).getByText(/feature deviation from baseline/i)).toBeInTheDocument()
    expect(within(drawer).getByText(/\+8.81σ/)).toBeInTheDocument()
    expect(within(drawer).getByText(/credential stuffing/i)).toBeInTheDocument()
  })

  it('opens the drawer from the keyboard', async () => {
    mockApi()
    renderPage(<SecurityAnalytics />)

    const row = (await screen.findAllByText('Authentication failure burst')).at(-1).closest('tr')

    // The row must be focusable, or keyboard users cannot reach the detail view.
    expect(row).toHaveAttribute('tabindex', '0')
    row.focus()
    expect(document.activeElement).toBe(row)

    fireEvent.keyDown(row, { key: 'Enter' })
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
  })

  it('filters the feed by status', async () => {
    const user = userEvent.setup()
    const fetchMock = mockApi()
    renderPage(<SecurityAnalytics />)

    await screen.findByText('Event feed')
    await user.click(screen.getByRole('button', { name: /anomalies only/i }))

    await waitFor(() => {
      const calls = fetchMock.mock.calls.map(([url]) => String(url))
      expect(calls.some((u) => u.includes('status=ANOMALY'))).toBe(true)
    })
  })

  it('renders the anomaly timeline with a decision threshold', async () => {
    mockApi()
    renderPage(<SecurityAnalytics />)

    expect(await screen.findByText('Anomaly score over time')).toBeInTheDocument()
    expect(screen.getByText(/decision threshold 65/)).toBeInTheDocument()
  })

  it('draws the threshold the detector applied, not a default', async () => {
    mockApi({
      dashboard: {
        ...dashboardPayload,
        security: { ...dashboardPayload.security, score_threshold: 72.5 },
      },
    })
    renderPage(<SecurityAnalytics />)

    expect(await screen.findByText(/decision threshold 72.5/)).toBeInTheDocument()
    expect(screen.queryByText(/decision threshold 65/)).not.toBeInTheDocument()
  })

  it('omits the threshold line when the backend does not report one', async () => {
    mockApi({
      dashboard: {
        ...dashboardPayload,
        security: { ...dashboardPayload.security, score_threshold: null },
      },
    })
    const { container } = renderPage(<SecurityAnalytics />)

    expect(await screen.findByText('Anomaly score over time')).toBeInTheDocument()
    await screen.findAllByText('Authentication failure burst')
    expect(screen.queryByText(/decision threshold/)).not.toBeInTheDocument()
    expect(container.querySelector('line[stroke-dasharray="5 4"]')).toBeNull()
  })
})

// ==========================================================================
describe('AI Insights', () => {
  it('renders metric, model signal and action for each insight', async () => {
    mockApi()
    renderPage(<AiInsights />)

    expect(await screen.findByText(/Authentication failure burst flagged/i)).toBeInTheDocument()
    expect(screen.getByText(/Anomaly score 98.42\/100/)).toBeInTheDocument()
    expect(screen.getByText(/credential stuffing/i)).toBeInTheDocument()

    expect(screen.getByText(/Projected spend exceeds the monthly budget/i)).toBeInTheDocument()
    expect(screen.getByText(/Overrun \$641/)).toBeInTheDocument()
    expect(screen.getAllByText(/Action:/).length).toBe(2)
  })

  it('groups insights by category', async () => {
    mockApi()
    renderPage(<AiInsights />)
    expect(await screen.findByText(/Security · 1/)).toBeInTheDocument()
    expect(screen.getByText(/Cost · 1/)).toBeInTheDocument()
  })
})

// ==========================================================================
describe('App shell', () => {
  it('renders navigation and the default page', async () => {
    mockApi()
    render(<App />)

    expect(await screen.findByText('CloudGuard')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Overview$/ })).toHaveAttribute('aria-current', 'page')
  })

  it('navigates between pages', async () => {
    const user = userEvent.setup()
    mockApi()
    render(<App />)

    await screen.findByText('CloudGuard')
    await user.click(screen.getByRole('button', { name: /Cost Intelligence/ }))

    expect(await screen.findByText('Historical spend and forecast')).toBeInTheDocument()
    expect(window.location.hash).toBe('#/cost')
  })

  it('shows dependency health in the sidebar', async () => {
    mockApi()
    render(<App />)

    expect(await screen.findByText('redis')).toBeInTheDocument()
    expect(screen.getByText('celery')).toBeInTheDocument()
    expect(screen.getByText('1 worker')).toBeInTheDocument()
  })

  it('badges the nav with the live anomaly count', async () => {
    mockApi()
    render(<App />)

    const navButton = await screen.findByRole('button', { name: /Security Analytics/ })
    expect(within(navButton).getByText('5')).toBeInTheDocument()
  })
})

// ==========================================================================
describe('Background task flow', () => {
  it('submits a refresh and polls the task to completion', async () => {
    const user = userEvent.setup()
    let pollCount = 0

    globalThis.fetch = vi.fn(async (url, init) => {
      const path = String(url)
      if (init?.method === 'POST' && path.includes('/api/dashboard/refresh')) {
        return jsonResponse({
          task_id: 'task-123',
          task_type: 'refresh_all',
          status: 'PENDING',
          submitted_at: '2026-10-01T20:30:00Z',
          executor: 'celery',
          poll_url: '/api/tasks/task-123',
          message: 'Queued on the Celery worker pool.',
        }, 202)
      }
      if (path.includes('/api/tasks/task-123')) {
        pollCount += 1
        return jsonResponse({
          task_id: 'task-123',
          task_type: 'refresh_all',
          status: pollCount >= 2 ? 'COMPLETED' : 'PROCESSING',
          created_at: '2026-10-01T20:30:00Z',
          executor: 'celery',
          progress: pollCount >= 2 ? 100 : 55,
          stage: pollCount >= 2 ? 'Complete' : 'Fitting Prophet forecast',
          cache_keys: [],
        })
      }
      if (path.includes('/api/dashboard')) return jsonResponse(dashboardPayload)
      if (path.includes('/api/health')) return jsonResponse(healthPayload)
      return jsonResponse({})
    })

    render(<App />)
    await screen.findByText('CloudGuard')

    await user.click(screen.getByRole('button', { name: /refresh data/i }))

    // The stage from the task record is surfaced while it runs.
    expect(await screen.findByText(/Fitting Prophet forecast/)).toBeInTheDocument()

    await waitFor(
      () => expect(screen.getByRole('button', { name: /refresh data/i })).not.toBeDisabled(),
      { timeout: 6000 },
    )
  })

  it('marks inline execution when Celery is unavailable', async () => {
    const user = userEvent.setup()
    globalThis.fetch = vi.fn(async (url, init) => {
      const path = String(url)
      if (init?.method === 'POST') {
        return jsonResponse({
          task_id: 'inline-abc',
          task_type: 'refresh_all',
          status: 'PENDING',
          submitted_at: '2026-10-01T20:30:00Z',
          executor: 'inline',
          poll_url: '/api/tasks/inline-abc',
          message: 'Celery broker unreachable - running on a local worker thread.',
        }, 202)
      }
      if (path.includes('/api/tasks/')) {
        return jsonResponse({
          task_id: 'inline-abc', task_type: 'refresh_all', status: 'PROCESSING',
          created_at: '2026-10-01T20:30:00Z', executor: 'inline',
          progress: 30, stage: 'Running inline', cache_keys: [],
        })
      }
      if (path.includes('/api/dashboard')) return jsonResponse(dashboardPayload)
      if (path.includes('/api/health')) return jsonResponse(healthPayload)
      return jsonResponse({})
    })

    render(<App />)
    await screen.findByText('CloudGuard')
    await user.click(screen.getByRole('button', { name: /refresh data/i }))

    expect(await screen.findByText('inline')).toBeInTheDocument()
  })

  it('says why a refresh was refused instead of failing silently', async () => {
    const user = userEvent.setup()
    globalThis.fetch = vi.fn(async (url, init) => {
      const path = String(url)
      if (init?.method === 'POST') {
        return jsonResponse({
          detail: 'Background work could not be queued.',
          error_code: 'task_dispatch_failed',
          hint: 'The task broker rejected the submission.',
        }, 503)
      }
      if (path.includes('/api/dashboard')) return jsonResponse(dashboardPayload)
      if (path.includes('/api/health')) return jsonResponse(healthPayload)
      return jsonResponse({})
    })

    render(<App />)
    await screen.findByText('CloudGuard')
    await user.click(screen.getByRole('button', { name: /refresh data/i }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Background work could not be queued.')
    expect(screen.getByRole('button', { name: /refresh data/i })).not.toBeDisabled()
  })
})
