/**
 * Frontend coverage for the remediation work: lazy routes, the stale banner
 * (previously unreachable because the backend could never report stale), and
 * the sparkline that was dead code.
 */
import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

import React from 'react'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import App from '../src/App'
import Overview from '../src/pages/Overview'
import { DashboardProvider } from '../src/state/DashboardContext'
import {
  coldDashboardPayload, dashboardPayload, healthPayload, securityEvents,
  staleDashboardPayload,
} from './fixtures'

function jsonResponse(body, status = 200) {
  return { ok: status >= 200 && status < 300, status, text: async () => JSON.stringify(body) }
}

function mockApi({ dashboard = dashboardPayload } = {}) {
  const fetchMock = vi.fn(async (url, init) => {
    const path = String(url)
    if (init?.method === 'POST') {
      return jsonResponse({
        task_id: 't-1', task_type: 'refresh_all', status: 'PENDING',
        submitted_at: '2026-10-02T00:00:00Z', executor: 'celery',
        poll_url: '/api/tasks/t-1', message: 'Queued.',
      }, 202)
    }
    if (path.includes('/api/tasks/')) {
      return jsonResponse({
        task_id: 't-1', task_type: 'refresh_all', status: 'COMPLETED',
        created_at: '2026-10-02T00:00:00Z', executor: 'celery',
        progress: 100, stage: 'Complete', cache_keys: [],
      })
    }
    if (path.includes('/api/dashboard')) return jsonResponse(dashboard)
    if (path.includes('/api/health')) return jsonResponse(healthPayload)
    if (path.includes('/api/security/events')) {
      return jsonResponse({
        total_events: 2, returned_events: 2, anomaly_count: 1, routine_count: 1,
        events: securityEvents, freshness: {},
      })
    }
    return jsonResponse({})
  })
  globalThis.fetch = fetchMock
  return fetchMock
}

const renderPage = (ui) => render(<DashboardProvider>{ui}</DashboardProvider>)

beforeEach(() => {
  vi.restoreAllMocks()
  // These suites exercise the dashboard shell, which opens behind the welcome
  // screen; start on the dashboard itself (tests/landing.test.jsx covers the rest).
  window.location.hash = '#/overview'
})

// ==========================================================================
describe('Stale data banner', () => {
  it('warns when pipelines are serving stale data', async () => {
    mockApi({ dashboard: staleDashboardPayload })
    renderPage(<Overview onNavigate={() => {}} />)

    expect(await screen.findByText(/2 pipelines serving stale data/i)).toBeInTheDocument()
    expect(screen.getByText(/showing the last successful result/i)).toBeInTheDocument()
  })

  it('names which pipelines are stale and how old they are', async () => {
    mockApi({ dashboard: staleDashboardPayload })
    renderPage(<Overview onNavigate={() => {}} />)

    const banner = (await screen.findByText(/serving stale data/i)).closest('.banner')
    // The names and ages are interpolated into one text block.
    expect(banner.textContent).toMatch(/cost history \(15m ago\)/)
    expect(banner.textContent).toMatch(/cost forecast \(15m ago\)/)
  })

  it('offers a refresh action on the stale banner', async () => {
    mockApi({ dashboard: staleDashboardPayload })
    renderPage(<Overview onNavigate={() => {}} />)

    await screen.findByText(/serving stale data/i)
    expect(screen.getByRole('button', { name: /^Refresh$/ })).toBeInTheDocument()
  })

  it('stays silent when every pipeline is fresh', async () => {
    mockApi()
    renderPage(<Overview onNavigate={() => {}} />)

    await screen.findByText('$483')
    expect(screen.queryByText(/serving stale data/i)).not.toBeInTheDocument()
  })

  it('renders the four freshness states as badges', async () => {
    mockApi({ dashboard: staleDashboardPayload })
    renderPage(<Overview onNavigate={() => {}} />)

    await screen.findByText('Pipeline status')
    expect(screen.getAllByText('stale').length).toBe(2)
    expect(screen.getAllByText('fresh').length).toBe(3)
  })

  it('shows unavailable for pipelines that never ran', async () => {
    mockApi({ dashboard: coldDashboardPayload })
    renderPage(<Overview onNavigate={() => {}} />)

    await screen.findByText('Pipeline status')
    expect(screen.getAllByText('unavailable').length).toBe(5)
    // A never-run pipeline is not "stale" - it has no last-known-good data.
    expect(screen.queryByText(/serving stale data/i)).not.toBeInTheDocument()
  })
})

// ==========================================================================
describe('Spend sparkline', () => {
  // Badge icons are inline SVGs too, so match the sparkline's own class.
  const sparklines = (container) => container.querySelectorAll('svg.sparkline')

  it('draws the trend shape behind the headline spend figure', async () => {
    mockApi()
    const { container } = renderPage(<Overview onNavigate={() => {}} />)

    await screen.findByText('$483')
    await waitFor(() => expect(sparklines(container).length).toBe(1))
    // area + line, from the daily cost series.
    expect(sparklines(container)[0].querySelectorAll('path').length).toBe(2)
  })

  it('is omitted when there is no cost history', async () => {
    mockApi({ dashboard: coldDashboardPayload })
    const { container } = renderPage(<Overview onNavigate={() => {}} />)

    await screen.findByText('Pipeline status')
    expect(sparklines(container).length).toBe(0)
  })
})

// ==========================================================================
describe('Lazy-loaded routes', () => {
  it('renders the Overview route after its chunk resolves', async () => {
    mockApi()
    render(<App />)
    expect(await screen.findByText('Pipeline status')).toBeInTheDocument()
  })

  it('shows a loading fallback while a route chunk loads', async () => {
    mockApi()
    const { container } = render(<App />)
    // Before the lazy chunk resolves, skeletons stand in for the content.
    expect(container.querySelectorAll('.skeleton').length).toBeGreaterThan(0)
    expect(await screen.findByText('Pipeline status')).toBeInTheDocument()
  })

  it('loads each route on navigation without breaking the shell', async () => {
    const user = userEvent.setup()
    mockApi()
    render(<App />)
    await screen.findByText('Pipeline status')

    for (const [label, marker] of [
      [/Cost Intelligence/, 'Historical spend and forecast'],
      [/Security Analytics/, 'Anomaly score over time'],
      [/AI Insights/, /Each insight pairs/],
      [/Overview/, 'Pipeline status'],
    ]) {
      await user.click(screen.getByRole('button', { name: label }))
      expect(await screen.findByText(marker)).toBeInTheDocument()
      // The shell persists across lazy boundaries.
      expect(screen.getByText('CloudGuard')).toBeInTheDocument()
    }
  })

  it('keeps the sidebar interactive while a route loads', async () => {
    mockApi()
    render(<App />)
    expect(await screen.findByRole('button', { name: /Cost Intelligence/ })).toBeEnabled()
  })
})

// ==========================================================================
describe('Dashboard contract after payload reduction', () => {
  it('renders the Overview feed from the short recent_events list', async () => {
    mockApi()
    renderPage(<Overview onNavigate={() => {}} />)

    expect(await screen.findByText('Authentication failure burst')).toBeInTheDocument()
    expect(screen.getByText('Normal API error rate')).toBeInTheDocument()
  })

  it('does not depend on the removed embedded event feed', async () => {
    // security.recent_events is [] in the fixture, mirroring the real payload.
    mockApi()
    renderPage(<Overview onNavigate={() => {}} />)

    await screen.findByText('$483')
    expect(screen.getByText('Recent events')).toBeInTheDocument()
    expect(screen.queryByText('No events scored yet')).not.toBeInTheDocument()
  })

  it('still shows the security summary tiles', async () => {
    mockApi()
    renderPage(<Overview onNavigate={() => {}} />)

    await screen.findByText('$483')
    // The label is uppercased by CSS; the DOM text is sentence case.
    const tile = screen.getByText('Anomalies detected').closest('.tile')
    expect(within(tile).getByText('5')).toBeInTheDocument()
    expect(within(tile).getByText(/of 241 behavioural windows/)).toBeInTheDocument()
  })
})

// ==========================================================================
describe('API client auth header', () => {
  it('sends X-API-Key when one is configured', async () => {
    vi.resetModules()
    vi.stubEnv('VITE_API_KEY', 'configured-key')

    const { api } = await import('../src/api/client')
    const fetchMock = vi.fn(async () => jsonResponse({ ok: true }))
    globalThis.fetch = fetchMock

    await api.refreshAll({})
    const [, init] = fetchMock.mock.calls[0]
    expect(init.headers['X-API-Key']).toBe('configured-key')

    vi.unstubAllEnvs()
    vi.resetModules()
  })

  it('omits the header when no key is configured', async () => {
    vi.resetModules()
    vi.stubEnv('VITE_API_KEY', '')

    const { api } = await import('../src/api/client')
    const fetchMock = vi.fn(async () => jsonResponse({ ok: true }))
    globalThis.fetch = fetchMock

    await api.dashboard()
    const [, init] = fetchMock.mock.calls[0]
    expect(init?.headers?.['X-API-Key']).toBeUndefined()

    vi.unstubAllEnvs()
    vi.resetModules()
  })
})

// ==========================================================================
// Vitest runs from the frontend root (where vitest.config.js lives); under jsdom
// import.meta.url is not the test file's on-disk path, so resolve from there.
const frontendFile = (name) => join(process.cwd(), name)

describe('Static hosting config', () => {
  it('points the favicon at a file that ships with the build', () => {
    const html = readFileSync(frontendFile('index.html'), 'utf8')
    const href = html.match(/<link[^>]*rel="icon"[^>]*href="\/([^"]+)"/)?.[1]

    expect(href).toBeTruthy()
    // Vite copies public/ to the root of dist/, which is what Vercel serves.
    expect(existsSync(frontendFile(`public/${href}`))).toBe(true)
  })

  it('has no rewrite that could answer /api/* with the HTML shell', () => {
    // The app routes with the URL hash, so it needs no SPA fallback at all. A
    // catch-all here turned a missing VITE_API_BASE_URL into 200 + HTML.
    // (A rewrite that proxies /api/* to the real API would be fine; only a
    // fallback to the HTML shell is the bug.)
    const config = JSON.parse(readFileSync(frontendFile('vercel.json'), 'utf8'))
    const htmlFallbacks = [...(config.rewrites || []), ...(config.routes || [])].filter(
      (rule) => /index\.html$/.test(rule.destination || rule.dest || ''),
    )
    for (const rule of htmlFallbacks) {
      const source = rule.source || rule.src || ''
      expect(new RegExp(`^${source}$`).test('/api/dashboard')).toBe(false)
    }
  })

  it('keeps the asset caching and security headers', () => {
    const config = JSON.parse(readFileSync(frontendFile('vercel.json'), 'utf8'))
    const headers = Object.fromEntries(
      config.headers.flatMap((rule) => rule.headers.map((h) => [`${rule.source} ${h.key}`, h.value])),
    )

    expect(headers['/assets/(.*) Cache-Control']).toMatch(/immutable/)
    expect(headers['/(.*) X-Content-Type-Options']).toBe('nosniff')
    expect(headers['/(.*) X-Frame-Options']).toBe('DENY')
    expect(config.outputDirectory).toBe('dist')
    expect(config.installCommand).toBe('npm ci')
  })

  it('loads no third-party stylesheet or font', () => {
    // The typeface ships with the app; a CDN link here would block first paint
    // on someone else's server and send every visitor's IP to it.
    const html = readFileSync(frontendFile('index.html'), 'utf8')
    expect(html).not.toMatch(/fonts\.googleapis\.com|fonts\.gstatic\.com/)
    expect(html).not.toMatch(/<link[^>]+rel="stylesheet"[^>]+href="https?:\/\//)

    const entry = readFileSync(frontendFile('src/main.jsx'), 'utf8')
    expect(entry).toMatch(/@fontsource-variable\/outfit/)
  })

  it('pins the Node major Vite 8 is built and tested on', () => {
    const pkg = JSON.parse(readFileSync(frontendFile('package.json'), 'utf8'))
    expect(pkg.engines?.node).toBe('22.x')
  })
})
