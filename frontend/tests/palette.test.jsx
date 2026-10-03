/** Command palette: shortcut, search, keyboard navigation, actions, a11y. */
import React from 'react'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import App from '../src/App'
import { dashboardPayload, healthPayload, securityEvents } from './fixtures'

function jsonResponse(body, status = 200) {
  return { ok: status >= 200 && status < 300, status, text: async () => JSON.stringify(body) }
}

/** Records every POST so tests can assert which action a command fired. */
function mockApi({ dashboard = dashboardPayload } = {}) {
  const posts = []
  globalThis.fetch = vi.fn(async (url, init) => {
    const path = String(url)
    if (init?.method === 'POST') {
      posts.push({ path, body: init.body ? JSON.parse(init.body) : {} })
      return jsonResponse({
        task_id: 'cmd-1', task_type: 'refresh_all', status: 'PENDING',
        submitted_at: '2026-10-02T00:00:00Z', executor: 'celery',
        poll_url: '/api/tasks/cmd-1', message: 'Queued.',
      }, 202)
    }
    if (path.includes('/api/tasks/')) {
      return jsonResponse({
        task_id: 'cmd-1', task_type: 'refresh_all', status: 'COMPLETED',
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
  return posts
}

/** Open via the keyboard shortcut and wait for the dialog. */
async function openPalette() {
  // Which actions are offered depends on the loaded payload (whether the
  // deployment allows test-anomaly injection), so wait for it first. The
  // sidebar figure appears once the payload is in, on every page.
  await screen.findByText('Projected spend')
  fireEvent.keyDown(window, { key: 'k', metaKey: true })
  return screen.findByRole('dialog', { name: /command palette/i })
}

// A route's chunk loads lazily; a cold transform can outlast the 1s default.
const LAZY_PAGE = { timeout: 3000 }

beforeEach(() => {
  vi.restoreAllMocks()
  // These suites exercise the dashboard shell, which opens behind the welcome
  // screen; start on the dashboard itself (tests/landing.test.jsx covers the rest).
  window.history.replaceState(null, '', '/overview')
})

// ==========================================================================
describe('Opening and closing', () => {
  it('opens with the meta+K shortcut', async () => {
    mockApi()
    render(<App />)
    await screen.findByText('Pipeline status', {}, LAZY_PAGE)

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(await openPalette()).toBeInTheDocument()
  })

  it('opens with ctrl+K for non-Mac users', async () => {
    mockApi()
    render(<App />)
    await screen.findByText('Pipeline status', {}, LAZY_PAGE)

    fireEvent.keyDown(window, { key: 'k', ctrlKey: true })
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
  })

  it('ignores a bare "k" so typing in a field is unaffected', async () => {
    mockApi()
    render(<App />)
    await screen.findByText('Pipeline status', {}, LAZY_PAGE)

    fireEvent.keyDown(window, { key: 'k' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('opens from the top-bar trigger', async () => {
    const user = userEvent.setup()
    mockApi()
    render(<App />)
    await screen.findByText('Pipeline status', {}, LAZY_PAGE)

    await user.click(screen.getByRole('button', { name: /open command palette/i }))
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
  })

  it('closes on Escape', async () => {
    mockApi()
    render(<App />)
    await screen.findByText('Pipeline status', {}, LAZY_PAGE)
    const dialog = await openPalette()

    fireEvent.keyDown(dialog, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('toggles shut on a second shortcut press', async () => {
    mockApi()
    render(<App />)
    await screen.findByText('Pipeline status', {}, LAZY_PAGE)
    await openPalette()

    fireEvent.keyDown(window, { key: 'k', metaKey: true })
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })
})

// ==========================================================================
describe('Commands', () => {
  it('lists every page and action, grouped', async () => {
    mockApi()
    render(<App />)
    await screen.findByText('Pipeline status', {}, LAZY_PAGE)
    const dialog = await openPalette()

    expect(within(dialog).getByText('Navigate')).toBeInTheDocument()
    expect(within(dialog).getByText('Actions')).toBeInTheDocument()

    for (const label of [
      'Overview', 'Cost Intelligence', 'Security Analytics', 'Cloud Resources',
      'AI Insights', 'Refresh all data', 'Retrain cost forecast',
      'Re-score security events', 'Inject test anomaly',
    ]) {
      expect(within(dialog).getByText(label)).toBeInTheDocument()
    }
  })

  it('marks the page you are already on', async () => {
    mockApi()
    render(<App />)
    await screen.findByText('Pipeline status', {}, LAZY_PAGE)
    const dialog = await openPalette()

    const overview = within(dialog).getByText('Overview').closest('button')
    expect(within(overview).getByText('current')).toBeInTheDocument()
  })

  it('filters as you type', async () => {
    const user = userEvent.setup()
    mockApi()
    render(<App />)
    await screen.findByText('Pipeline status', {}, LAZY_PAGE)
    const dialog = await openPalette()

    await user.type(within(dialog).getByLabelText(/search commands/i), 'anomaly')

    expect(within(dialog).getByText('Inject test anomaly')).toBeInTheDocument()
    expect(within(dialog).queryByText('Cloud Resources')).not.toBeInTheDocument()
  })

  it('matches on the description, not just the title', async () => {
    const user = userEvent.setup()
    mockApi()
    render(<App />)
    await screen.findByText('Pipeline status', {}, LAZY_PAGE)
    const dialog = await openPalette()

    await user.type(within(dialog).getByLabelText(/search commands/i), 'prophet')
    expect(within(dialog).getByText('Retrain cost forecast')).toBeInTheDocument()
  })

  it('ranks a label match above a description match', async () => {
    const user = userEvent.setup()
    mockApi()
    render(<App />)
    await screen.findByText('Pipeline status', {}, LAZY_PAGE)
    const dialog = await openPalette()

    // "security" also appears in Overview's description; the page named
    // Security Analytics must still come first.
    await user.type(within(dialog).getByLabelText(/search commands/i), 'security')

    const options = within(dialog).getAllByRole('option')
    expect(options[0]).toHaveTextContent('Security Analytics')
    expect(options[0]).toHaveAttribute('aria-selected', 'true')
  })

  it('reports when nothing matches', async () => {
    const user = userEvent.setup()
    mockApi()
    render(<App />)
    await screen.findByText('Pipeline status', {}, LAZY_PAGE)
    const dialog = await openPalette()

    await user.type(within(dialog).getByLabelText(/search commands/i), 'zzzznope')
    expect(within(dialog).getByText(/no command matches/i)).toBeInTheDocument()
  })
})

// ==========================================================================
describe('Keyboard navigation', () => {
  it('starts with the first command selected', async () => {
    mockApi()
    render(<App />)
    await screen.findByText('Pipeline status', {}, LAZY_PAGE)
    const dialog = await openPalette()

    const first = within(dialog).getByText('Overview').closest('button')
    expect(first).toHaveAttribute('aria-selected', 'true')
  })

  it('moves the selection with the arrow keys', async () => {
    mockApi()
    render(<App />)
    await screen.findByText('Pipeline status', {}, LAZY_PAGE)
    const dialog = await openPalette()

    fireEvent.keyDown(dialog, { key: 'ArrowDown' })
    const second = within(dialog).getByText('Cost Intelligence').closest('button')
    expect(second).toHaveAttribute('aria-selected', 'true')

    fireEvent.keyDown(dialog, { key: 'ArrowUp' })
    const first = within(dialog).getByText('Overview').closest('button')
    expect(first).toHaveAttribute('aria-selected', 'true')
  })

  it('wraps around at the ends of the list', async () => {
    mockApi()
    render(<App />)
    await screen.findByText('Pipeline status', {}, LAZY_PAGE)
    const dialog = await openPalette()

    fireEvent.keyDown(dialog, { key: 'ArrowUp' })  // wraps to the last command
    const last = within(dialog).getByText('Inject test anomaly').closest('button')
    expect(last).toHaveAttribute('aria-selected', 'true')
  })

  it('navigates to the selected page on Enter', async () => {
    mockApi()
    render(<App />)
    await screen.findByText('Pipeline status', {}, LAZY_PAGE)
    const dialog = await openPalette()

    fireEvent.keyDown(dialog, { key: 'ArrowDown' })   // Cost Intelligence
    fireEvent.keyDown(dialog, { key: 'Enter' })

    expect(await screen.findByText('Historical spend and forecast', {}, LAZY_PAGE)).toBeInTheDocument()
    expect(window.location.pathname).toBe('/cost')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})

// ==========================================================================
describe('Actions', () => {
  it('fires a refresh and closes', async () => {
    const user = userEvent.setup()
    const posts = mockApi()
    render(<App />)
    await screen.findByText('Pipeline status', {}, LAZY_PAGE)
    const dialog = await openPalette()

    await user.click(within(dialog).getByText('Refresh all data').closest('button'))

    await waitFor(() =>
      expect(posts.some((p) => p.path.includes('/api/dashboard/refresh'))).toBe(true))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('sends inject_anomaly for the test-anomaly command', async () => {
    const user = userEvent.setup()
    const posts = mockApi()
    render(<App />)
    await screen.findByText('Pipeline status', {}, LAZY_PAGE)
    const dialog = await openPalette()

    await user.click(within(dialog).getByText('Inject test anomaly').closest('button'))

    await waitFor(() => {
      const call = posts.find((p) => p.path.includes('/api/security/anomalies/run'))
      expect(call?.body.inject_anomaly).toBe(true)
    })
  })

  it('leaves out the test-anomaly command where the deployment forbids it', async () => {
    mockApi({
      dashboard: { ...dashboardPayload, features: { anomaly_injection: false } },
    })
    render(<App />)
    await screen.findByText('Pipeline status', {}, LAZY_PAGE)
    const dialog = await openPalette()

    expect(within(dialog).getByText('Re-score security events')).toBeInTheDocument()
    expect(within(dialog).queryByText('Inject test anomaly')).not.toBeInTheDocument()
  })

  it('sends force for a retrain', async () => {
    const user = userEvent.setup()
    const posts = mockApi()
    render(<App />)
    await screen.findByText('Pipeline status', {}, LAZY_PAGE)
    const dialog = await openPalette()

    await user.click(within(dialog).getByText('Retrain cost forecast').closest('button'))

    await waitFor(() => {
      const call = posts.find((p) => p.path.includes('/api/costs/forecast/run'))
      expect(call?.body.force).toBe(true)
    })
  })
})

// ==========================================================================
describe('Accessibility', () => {
  it('is a labelled modal dialog', async () => {
    mockApi()
    render(<App />)
    await screen.findByText('Pipeline status', {}, LAZY_PAGE)
    const dialog = await openPalette()

    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(dialog).toHaveAttribute('aria-label', 'Command palette')
  })

  it('focuses the search field on open', async () => {
    mockApi()
    render(<App />)
    await screen.findByText('Pipeline status', {}, LAZY_PAGE)
    const dialog = await openPalette()

    await waitFor(() =>
      expect(document.activeElement).toBe(within(dialog).getByLabelText(/search commands/i)))
  })

  it('exposes the command list with option semantics', async () => {
    mockApi()
    render(<App />)
    await screen.findByText('Pipeline status', {}, LAZY_PAGE)
    const dialog = await openPalette()

    expect(within(dialog).getByRole('listbox')).toBeInTheDocument()
    expect(within(dialog).getAllByRole('option').length).toBe(9)
  })

  it('points aria-activedescendant at the highlighted option', async () => {
    mockApi()
    render(<App />)
    await screen.findByText('Pipeline status', {}, LAZY_PAGE)
    const dialog = await openPalette()

    const input = within(dialog).getByLabelText(/search commands/i)
    const selected = within(dialog).getByText('Overview').closest('button')
    expect(input).toHaveAttribute('aria-activedescendant', selected.id)
  })

  it('restores focus to the trigger on close', async () => {
    const user = userEvent.setup()
    mockApi()
    render(<App />)
    await screen.findByText('Pipeline status', {}, LAZY_PAGE)

    const trigger = screen.getByRole('button', { name: /open command palette/i })
    await user.click(trigger)
    const dialog = await screen.findByRole('dialog')
    fireEvent.keyDown(dialog, { key: 'Escape' })

    await waitFor(() => expect(document.activeElement).toBe(trigger))
  })
})
