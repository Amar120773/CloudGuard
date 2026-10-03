/** Unit tests for the API client, formatters and chart geometry. */
import React from 'react'
import { render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { ApiError, request } from '../src/api/client'
import ForecastChart from '../src/charts/ForecastChart'
import ServiceBars from '../src/charts/ServiceBars'
import Sparkline from '../src/charts/Sparkline'
import { niceTicks, scaleLinear } from '../src/charts/useChartWidth'
import {
  bytes, money, number, percent, relativeAge, scoreColor, severityClass, shortService,
  utilizationColor,
} from '../src/utils/format'
import { forecastPoints, historyTail, serviceBreakdown } from './fixtures'

function jsonResponse(body, status = 200) {
  return { ok: status >= 200 && status < 300, status, text: async () => JSON.stringify(body) }
}

// ==========================================================================
describe('API client', () => {
  it('returns the parsed body on success', async () => {
    globalThis.fetch = vi.fn(async () => jsonResponse({ ok: true }))
    await expect(request('/api/health')).resolves.toEqual({ ok: true })
  })

  it('unwraps a nested FastAPI detail object', async () => {
    globalThis.fetch = vi.fn(async () => jsonResponse({
      detail: {
        detail: 'Cost forecast results are not available yet.',
        error_code: 'pipeline_not_ready',
        hint: 'Train the model first.',
      },
    }, 503))

    const error = await request('/api/costs/forecast').catch((e) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect(error.message).toBe('Cost forecast results are not available yet.')
    expect(error.code).toBe('pipeline_not_ready')
    expect(error.hint).toBe('Train the model first.')
    expect(error.isPipelineNotReady).toBe(true)
  })

  it('handles a flat validation error body', async () => {
    globalThis.fetch = vi.fn(async () => jsonResponse({
      detail: 'Request validation failed.',
      error_code: 'validation_error',
    }, 422))

    const error = await request('/api/security/events?limit=0').catch((e) => e)
    expect(error.status).toBe(422)
    expect(error.code).toBe('validation_error')
  })

  it('classifies a network failure', async () => {
    globalThis.fetch = vi.fn(async () => { throw new TypeError('Failed to fetch') })

    const error = await request('/api/dashboard').catch((e) => e)
    expect(error.isNetworkError).toBe(true)
    expect(error.code).toBe('network_error')
    expect(error.message).toBe('Cannot reach the CloudGuard API.')
    expect(error.hint).toMatch(/temporarily unavailable/)
  })

  it('does not show public visitors development hosts or ports', async () => {
    globalThis.fetch = vi.fn(async () => { throw new TypeError('Failed to fetch') })

    const error = await request('/api/dashboard').catch((e) => e)
    for (const text of [error.message, error.hint]) {
      expect(text).not.toMatch(/8000|localhost|127\.0\.0\.1|port/i)
    }
  })

  it('times out rather than hanging forever', async () => {
    globalThis.fetch = vi.fn((url, { signal }) => new Promise((_, reject) => {
      signal.addEventListener('abort', () => {
        const err = new Error('aborted')
        err.name = 'AbortError'
        reject(err)
      })
    }))

    const error = await request('/api/dashboard', { timeoutMs: 20 }).catch((e) => e)
    expect(error.code).toBe('timeout')
  })

  it('tolerates a non-JSON body', async () => {
    globalThis.fetch = vi.fn(async () => ({
      ok: false, status: 502, text: async () => 'Bad Gateway',
    }))
    const error = await request('/api/dashboard').catch((e) => e)
    expect(error.status).toBe(502)
    expect(error.message).toBe('Bad Gateway')
  })
})

// ==========================================================================
// A static host answering /api/* with its own HTML page (missing or wrong
// VITE_API_BASE_URL) used to be returned as data, leaving the dashboard on
// "Waiting for the API…" forever with no error.
const HTML_PAGE = '<!doctype html><html><head><title>CloudGuard</title></head><body><div id="root"></div></body></html>'

function textResponse(text, status = 200) {
  return { ok: status >= 200 && status < 300, status, text: async () => text }
}

describe('API client: responses that are not the API', () => {
  it('rejects a 200 HTML page instead of returning it as data', async () => {
    globalThis.fetch = vi.fn(async () => textResponse(HTML_PAGE))

    const error = await request('/api/dashboard').catch((e) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect(error.code).toBe('invalid_response')
    expect(error.isInvalidResponse).toBe(true)
    expect(error.status).toBe(200)
    expect(error.message).toMatch(/unexpected response/i)
    expect(error.message).not.toContain('<')
  })

  it('rejects an empty 200, which is not valid JSON either', async () => {
    globalThis.fetch = vi.fn(async () => textResponse(''))
    const error = await request('/api/dashboard').catch((e) => e)
    expect(error.code).toBe('invalid_response')
  })

  it('still returns a normal JSON API response', async () => {
    const body = { status: 'ok', dependencies: [{ name: 'api', healthy: true }] }
    globalThis.fetch = vi.fn(async () => jsonResponse(body))
    await expect(request('/api/health')).resolves.toEqual(body)
  })

  it('treats 204 No Content as an empty success', async () => {
    globalThis.fetch = vi.fn(async () => textResponse('', 204))
    await expect(request('/api/tasks')).resolves.toBeNull()
  })

  it('keeps an HTML error page out of the error message', async () => {
    globalThis.fetch = vi.fn(async () => textResponse(HTML_PAGE, 404))

    const error = await request('/api/dashboard').catch((e) => e)
    expect(error.status).toBe(404)
    expect(error.code).toBe('http_error')
    expect(error.message).toBe('Request failed with status 404')
  })
})

// ==========================================================================
describe('Formatters', () => {
  it('formats money', () => {
    expect(money(14641.14)).toBe('$14,641')
    expect(money(9.838, { decimals: 2 })).toBe('$9.84')
    expect(money(14641, { compact: true })).toBe('$14.6k')
    expect(money(null)).toBe('—')
    expect(money(undefined)).toBe('—')
    expect(money(NaN)).toBe('—')
  })

  it('puts the minus sign before the currency symbol', () => {
    // The budget tile showed "$-506" for an overrun.
    expect(money(-506.4)).toBe('-$506')
    expect(money(-1234.5, { decimals: 2 })).toBe('-$1,234.50')
    expect(money(-14641, { compact: true })).toBe('-$14.6k')
  })

  it('never shows a sign on a value that rounds to zero', () => {
    expect(money(-0.2)).toBe('$0')
    expect(money(-0.004, { decimals: 2 })).toBe('$0.00')
    expect(money(-0)).toBe('$0')
  })

  it('formats percentages with an optional sign', () => {
    expect(percent(4.59)).toBe('4.6%')
    expect(percent(4.59, 2, { signed: true })).toBe('+4.59%')
    expect(percent(-4.59, 1, { signed: true })).toBe('-4.6%')
    expect(percent(null)).toBe('—')
  })

  it('formats bytes across magnitudes', () => {
    expect(bytes(512)).toBe('512 B')
    expect(bytes(1536)).toBe('1.5 KB')
    expect(bytes(9223672)).toBe('8.8 MB')
    expect(bytes(null)).toBe('—')
  })

  it('formats numbers', () => {
    expect(number(1234567)).toBe('1,234,567')
    expect(number(12.345, 2)).toBe('12.35')
  })

  it('shortens AWS service names', () => {
    expect(shortService('Amazon Elastic Compute Cloud - Compute')).toBe('EC2 Compute')
    expect(shortService('Amazon Relational Database Service')).toBe('RDS')
    expect(shortService('Amazon Simple Storage Service')).toBe('S3')
    expect(shortService('AWS Lambda')).toBe('Lambda')
    expect(shortService('Amazon DynamoDB')).toBe('DynamoDB')
  })

  it('renders relative ages', () => {
    expect(relativeAge(5)).toBe('just now')
    expect(relativeAge(45)).toBe('45s ago')
    expect(relativeAge(120)).toBe('2m ago')
    expect(relativeAge(7200)).toBe('2h ago')
    expect(relativeAge(null)).toBeNull()
  })

  it('maps severity to a status class', () => {
    expect(severityClass('CRITICAL')).toBe('badge-critical')
    expect(severityClass('HIGH')).toBe('badge-serious')
    expect(severityClass('MEDIUM')).toBe('badge-warning')
    expect(severityClass('unknown')).toBe('')
  })

  it('colours CPU by problem band, not as a good/bad ramp', () => {
    // Idle is wasted spend, saturated is a risk, the middle is simply healthy.
    expect(utilizationColor(2.3)).toBe('var(--status-warning)')
    expect(utilizationColor(45)).toBe('var(--text-primary)')
    expect(utilizationColor(95)).toBe('var(--status-serious)')
    // A missing reading is "no data", not "idle at 0%".
    expect(utilizationColor(null)).toBe('var(--text-muted)')
    expect(utilizationColor(undefined)).toBe('var(--text-muted)')
    expect(utilizationColor(0)).toBe('var(--status-warning)')
  })

  it('maps health scores so high is good', () => {
    expect(scoreColor(95)).toBe('var(--status-good)')
    expect(scoreColor(70)).toBe('var(--status-warning)')
    expect(scoreColor(20)).toBe('var(--status-critical)')
  })
})

// ==========================================================================
describe('Chart scales', () => {
  it('maps a domain onto a pixel range', () => {
    const scale = scaleLinear(0, 100, 0, 500)
    expect(scale(0)).toBe(0)
    expect(scale(50)).toBe(250)
    expect(scale(100)).toBe(500)
  })

  it('inverts the range for SVG y axes', () => {
    const scale = scaleLinear(0, 10, 300, 0)
    expect(scale(0)).toBe(300)
    expect(scale(10)).toBe(0)
  })

  it('survives a zero-width domain', () => {
    expect(Number.isFinite(scaleLinear(5, 5, 0, 100)(5))).toBe(true)
  })

  it('produces round tick values covering the domain', () => {
    const ticks = niceTicks(0, 100, 4)
    expect(ticks.length).toBeGreaterThan(2)
    expect(Math.max(...ticks)).toBeLessThanOrEqual(100)
    ticks.forEach((t) => expect(Number.isFinite(t)).toBe(true))
  })

  it('handles a degenerate domain', () => {
    expect(niceTicks(5, 5)).toEqual([5])
    expect(niceTicks(NaN, NaN)).toEqual([0])
  })
})

// ==========================================================================
describe('ForecastChart', () => {
  it('draws the actual line, the forecast line and the band', () => {
    const { container } = render(
      <ForecastChart history={historyTail} forecast={forecastPoints} />,
    )
    const paths = container.querySelectorAll('svg path')
    expect(paths.length).toBeGreaterThanOrEqual(3)

    // The forecast series is dashed so it is distinguishable without colour.
    const dashed = [...paths].filter((p) => p.getAttribute('stroke-dasharray'))
    expect(dashed.length).toBeGreaterThanOrEqual(1)
  })

  it('carries an accessible description', () => {
    render(<ForecastChart history={historyTail} forecast={forecastPoints} />)
    const svg = document.querySelector('svg[role="img"]')
    expect(svg.getAttribute('aria-label')).toMatch(/forecast/i)
  })

  it('renders history alone when there is no forecast', () => {
    const { container } = render(<ForecastChart history={historyTail} forecast={[]} />)
    expect(container.querySelectorAll('svg path').length).toBeGreaterThanOrEqual(1)
  })

  it('renders nothing rather than crashing on empty input', () => {
    const { container } = render(<ForecastChart history={[]} forecast={[]} />)
    expect(container.querySelector('svg')).toBeNull()
  })

  it('ignores malformed points', () => {
    const { container } = render(
      <ForecastChart
        history={[{ date: '2026-09-01', cost: 100 }, { date: null, cost: NaN }]}
        forecast={forecastPoints}
      />,
    )
    expect(container.querySelector('svg')).not.toBeNull()
  })
})

// ==========================================================================
describe('ServiceBars', () => {
  it('sorts by the metric it renders', () => {
    const { container } = render(
      <ServiceBars services={serviceBreakdown} metric="projected_month_end" />,
    )
    const names = [...container.querySelectorAll('.hbar-name')].map((n) => n.textContent)
    expect(names[0]).toMatch(/RDS/)
  })

  it('re-sorts when the metric changes', () => {
    const { container } = render(
      <ServiceBars services={serviceBreakdown} metric="total_cost" />,
    )
    const names = [...container.querySelectorAll('.hbar-name')].map((n) => n.textContent)
    // EC2 has the biggest historical total.
    expect(names[0]).toMatch(/EC2/)
  })

  it('scales the longest bar to full width', () => {
    const { container } = render(
      <ServiceBars services={serviceBreakdown} metric="total_cost" />,
    )
    const widths = [...container.querySelectorAll('.hbar-fill')].map((n) => n.style.width)
    expect(widths[0]).toBe('100%')
  })

  it('skips rows whose metric is null', () => {
    const rows = serviceBreakdown.map((s) => ({ ...s, projected_month_end: null }))
    const { container } = render(<ServiceBars services={rows} metric="projected_month_end" />)
    expect(container.querySelectorAll('.hbar-row').length).toBe(0)
  })

  it('renders nothing for an empty list', () => {
    const { container } = render(<ServiceBars services={[]} />)
    expect(container.firstChild).toBeNull()
  })
})

// ==========================================================================
describe('Sparkline', () => {
  it('draws a line for a usable series', () => {
    const { container } = render(<Sparkline values={[1, 5, 3, 8, 6]} />)
    expect(container.querySelectorAll('path').length).toBe(2)  // area + line
  })

  it('renders nothing for fewer than two points', () => {
    const { container } = render(<Sparkline values={[1]} />)
    expect(container.firstChild).toBeNull()
  })

  it('tolerates a flat series', () => {
    const { container } = render(<Sparkline values={[5, 5, 5]} />)
    expect(container.querySelector('svg')).not.toBeNull()
  })
})
