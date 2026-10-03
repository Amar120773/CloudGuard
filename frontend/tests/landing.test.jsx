/**
 * The welcome screen: shown at the root URL, entered on request, skipped by
 * deep links, used to wake a sleeping API before the visitor clicks in, and
 * honest about a deployment that cannot reach the API at all.
 */
import React from 'react'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import App from '../src/App'
import { coldDashboardPayload, dashboardPayload, healthPayload } from './fixtures'

// The exit animation runs before the dashboard takes over.
const AFTER_EXIT = { timeout: 3000 }
const HEADLINE = 'See the cloud bill and the breach before they hit.'

function jsonResponse(body, status = 200) {
  return { ok: status >= 200 && status < 300, status, text: async () => JSON.stringify(body) }
}

function mockApi({ dashboard = dashboardPayload, healthDown = false } = {}) {
  const fetchMock = vi.fn(async (url) => {
    const path = String(url)
    if (path.includes('/api/health')) {
      if (healthDown) throw new TypeError('Failed to fetch')
      return jsonResponse(healthPayload)
    }
    if (path.includes('/api/dashboard')) return jsonResponse(dashboard)
    return jsonResponse({})
  })
  globalThis.fetch = fetchMock
  return fetchMock
}

const enterButton = () => screen.getByRole('button', { name: /enter dashboard/i })

// A route's chunk loads lazily; a cold transform can outlast the 1s default.
const LAZY_PAGE = { timeout: 3000 }

beforeEach(() => {
  vi.restoreAllMocks()
  window.history.replaceState(null, '', '/')
})

afterEach(() => {
  delete window.matchMedia
})

// ==========================================================================
describe('Welcome screen', () => {
  it('opens at the root URL instead of the dashboard', async () => {
    mockApi()
    render(<App />)

    expect(screen.getByRole('heading', { level: 1, name: HEADLINE })).toBeInTheDocument()
    expect(enterButton()).toBeInTheDocument()
    // The dashboard shell has not been rendered yet.
    expect(screen.queryByRole('navigation', { name: /main navigation/i })).not.toBeInTheDocument()
    expect(screen.queryByText('Pipeline status')).not.toBeInTheDocument()
  })

  it('enters the dashboard when asked', async () => {
    const user = userEvent.setup()
    mockApi()
    render(<App />)

    await user.click(enterButton())

    expect(await screen.findByText('Pipeline status', {}, AFTER_EXIT)).toBeInTheDocument()
    expect(window.location.pathname).toBe('/overview')
    expect(document.title).toBe('Overview · CloudGuard')
    expect(screen.queryByRole('button', { name: /enter dashboard/i })).not.toBeInTheDocument()
  })

  it('goes in on Enter, because the button starts focused', async () => {
    const user = userEvent.setup()
    mockApi()
    render(<App />)

    expect(enterButton()).toHaveFocus()
    await user.keyboard('{Enter}')

    expect(await screen.findByText('Pipeline status', {}, AFTER_EXIT)).toBeInTheDocument()
  })

  it('still goes in on Enter after a click moves focus off the button', async () => {
    const user = userEvent.setup()
    mockApi()
    render(<App />)

    await user.click(screen.getByRole('heading', { level: 1 }))
    expect(enterButton()).not.toHaveFocus()
    await user.keyboard('{Enter}')

    expect(await screen.findByText('Pipeline status', {}, AFTER_EXIT)).toBeInTheDocument()
    expect(window.location.pathname).toBe('/overview')
  })

  it('goes in when the "press Enter" hint itself is clicked', async () => {
    const user = userEvent.setup()
    mockApi()
    render(<App />)

    await user.click(screen.getByText('Enter', { selector: 'kbd' }))

    expect(await screen.findByText('Pipeline status', {}, AFTER_EXIT)).toBeInTheDocument()
  })

  it('enters once when the button and the page-wide Enter key both fire', async () => {
    const user = userEvent.setup()
    mockApi()
    render(<App />)
    const pushState = vi.spyOn(window.history, 'pushState')

    await user.keyboard('{Enter}')
    await user.click(enterButton())

    expect(await screen.findByText('Pipeline status', {}, AFTER_EXIT)).toBeInTheDocument()
    expect(pushState).toHaveBeenCalledTimes(1)
  })

  it('lets deep links skip it', async () => {
    window.history.replaceState(null, '', '/cost')
    mockApi()
    render(<App />)

    expect(await screen.findByText('Historical spend and forecast', {}, LAZY_PAGE)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /enter dashboard/i })).not.toBeInTheDocument()
  })

  it('is one click away from the dashboard via the logo', async () => {
    const user = userEvent.setup()
    window.history.replaceState(null, '', '/overview')
    mockApi()
    render(<App />)

    await screen.findByText('Pipeline status', {}, LAZY_PAGE)
    await user.click(screen.getByRole('link', { name: /cloudguard/i }))

    await waitFor(() => expect(enterButton()).toBeInTheDocument())
    expect(window.location.pathname).toBe('/')
  })
})

// ==========================================================================
describe('Dashboard sections are pages', () => {
  it('gives each section its own address and title, and Back returns', async () => {
    const user = userEvent.setup()
    window.history.replaceState(null, '', '/overview')
    mockApi()
    render(<App />)
    await screen.findByText('Pipeline status', {}, LAZY_PAGE)

    const costLink = screen.getByRole('link', { name: /Cost Intelligence/ })
    expect(costLink).toHaveAttribute('href', '/cost')
    await user.click(costLink)

    expect(await screen.findByText('Historical spend and forecast', {}, LAZY_PAGE)).toBeInTheDocument()
    expect(window.location.pathname).toBe('/cost')
    expect(document.title).toBe('Cost Intelligence · CloudGuard')

    act(() => window.history.back())
    expect(await screen.findByText('Pipeline status', {}, LAZY_PAGE)).toBeInTheDocument()
    await waitFor(() => expect(window.location.pathname).toBe('/overview'))
  })

  it('still opens the right page from an old #/ link, at its new address', async () => {
    window.history.replaceState(null, '', '/#/security')
    mockApi()
    render(<App />)

    expect(await screen.findByText('Anomaly score over time', {}, LAZY_PAGE)).toBeInTheDocument()
    expect(window.location.pathname).toBe('/security')
    expect(window.location.hash).toBe('')
  })

  it('sends an unknown path to the Overview page', async () => {
    window.history.replaceState(null, '', '/nope')
    mockApi()
    render(<App />)

    expect(await screen.findByText('Pipeline status', {}, LAZY_PAGE)).toBeInTheDocument()
    expect(window.location.pathname).toBe('/overview')
  })
})

// ==========================================================================
describe('Welcome screen API status', () => {
  it('says when the API is reachable', async () => {
    mockApi()
    render(<App />)

    expect(await screen.findByText('Analysis engine online')).toBeInTheDocument()
  })

  it('says it is waking the API while it does not answer yet', async () => {
    mockApi({ healthDown: true })
    render(<App />)

    expect(screen.getByRole('status')).toHaveTextContent(/waking the analysis engine/i)
    // Entering is never blocked on it.
    expect(enterButton()).toBeEnabled()
  })

  it('reports a site that is not pointed at the API instead of waking', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    // A static host answering /api/health: VITE_API_BASE_URL missing or wrong.
    globalThis.fetch = vi.fn(async () => ({ ok: false, status: 404, text: async () => 'Not Found' }))
    render(<App />)

    expect(await screen.findByText('Analysis engine not connected to this site')).toBeInTheDocument()
    expect(warn).toHaveBeenCalledWith(expect.stringContaining('VITE_API_BASE_URL'))
    expect(enterButton()).toBeEnabled()
  })

  it('reports an API that answers but refuses this site, once it outlasts a cold start', async () => {
    vi.useFakeTimers()
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    // CORS: the readable request fails, but an opaque probe gets an answer.
    globalThis.fetch = vi.fn(async (url, init) => {
      if (init?.mode === 'no-cors') return { ok: false, status: 0, type: 'opaque' }
      throw new TypeError('Failed to fetch')
    })
    render(<App />)

    // A waking server's interstitial looks the same at first, so keep waiting...
    await act(() => vi.advanceTimersByTimeAsync(20000))
    expect(screen.getByRole('status')).toHaveTextContent(/waking the analysis engine/i)

    // ...but not for the full give-up window.
    await act(() => vi.advanceTimersByTimeAsync(50000))
    expect(screen.getByRole('status')).toHaveTextContent('Analysis engine is refusing this site')
    expect(warn).toHaveBeenCalledWith(expect.stringContaining('CORS_ORIGINS'))
  })
})

// ==========================================================================
describe('Welcome screen figures', () => {
  it('shows live figures once the pipelines have run', async () => {
    mockApi()
    render(<App />)

    expect(await screen.findByText('$14,641 projected for month end')).toBeInTheDocument()
    expect(screen.getByText('5 anomalies across 241 windows')).toBeInTheDocument()
    expect(screen.getByText('7 resources, 1 idle')).toBeInTheDocument()
  })

  it('describes the models before any pipeline has run', async () => {
    mockApi({ dashboard: coldDashboardPayload })
    render(<App />)

    expect(await screen.findByText(/Prophet forecasts month-end spend/)).toBeInTheDocument()
    expect(screen.getByText(/IsolationForest flags behaviour/)).toBeInTheDocument()
    // No invented numbers while nothing has run.
    expect(screen.queryByText(/projected for month end/)).not.toBeInTheDocument()
  })
})

// ==========================================================================
describe('Headline decode', () => {
  it('scrambles in behind an aria-hidden overlay, then leaves only the words', async () => {
    mockApi()
    const { container } = render(<App />)

    // The accessible name is whole from the first frame.
    expect(screen.getByRole('heading', { level: 1, name: HEADLINE })).toBeInTheDocument()
    expect(container.querySelector('.decode-overlay')).toHaveAttribute('aria-hidden', 'true')

    await waitFor(
      () => expect(container.querySelector('.decode-overlay')).toBeNull(),
      { timeout: 3000 },
    )
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(HEADLINE)
  })

  it('renders the finished words at once for reduced motion', () => {
    window.matchMedia = vi.fn(() => ({
      matches: true, addEventListener() {}, removeEventListener() {},
    }))
    mockApi()
    const { container } = render(<App />)

    expect(container.querySelector('.decode-overlay')).toBeNull()
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(HEADLINE)
  })
})
