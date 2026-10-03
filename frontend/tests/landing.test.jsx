/**
 * The welcome screen: shown at the root URL, entered on request, skipped by
 * deep links, and used to wake a sleeping API before the visitor clicks in.
 */
import React from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import App from '../src/App'
import { coldDashboardPayload, dashboardPayload, healthPayload } from './fixtures'

// The exit animation runs before the dashboard takes over.
const AFTER_EXIT = { timeout: 3000 }

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

beforeEach(() => {
  vi.restoreAllMocks()
  window.location.hash = ''
})

// ==========================================================================
describe('Welcome screen', () => {
  it('opens at the root URL instead of the dashboard', async () => {
    mockApi()
    render(<App />)

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(/forecast and defended/i)
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
    expect(window.location.hash).toBe('#/overview')
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

  it('lets deep links skip it', async () => {
    window.location.hash = '#/cost'
    mockApi()
    render(<App />)

    expect(await screen.findByText('Historical spend and forecast')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /enter dashboard/i })).not.toBeInTheDocument()
  })

  it('is one click away from the dashboard via the logo', async () => {
    const user = userEvent.setup()
    window.location.hash = '#/overview'
    mockApi()
    render(<App />)

    await screen.findByText('Pipeline status')
    await user.click(screen.getByRole('link', { name: /cloudguard/i }))

    await waitFor(() => expect(enterButton()).toBeInTheDocument())
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
