/**
 * CloudGuard API client.
 *
 * Normalises every failure into an ApiError carrying the backend's error_code
 * and hint, so components can distinguish "the pipeline has not run yet"
 * (recoverable, show a Run button) from a real outage.
 */

// Same origin by default: nginx serves the app and proxies /api to the backend
// in production, and the Vite dev server proxies /api in development. Only set
// VITE_API_BASE_URL when the dashboard is hosted apart from the API, and then
// give it the API's absolute URL.
//
// Defaulting to "" rather than treating undefined specially matters: Docker
// drops an empty ENV, so an "explicitly empty" value is indistinguishable from
// an unset one and the fallback would silently win.
const BASE_URL = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')
const DEFAULT_TIMEOUT_MS = Number(import.meta.env.VITE_API_TIMEOUT_MS || 20000)
// Sent on every request when the backend has CLOUDGUARD_API_KEY configured.
// Left unset for the open local demo, in which case no header is sent at all.
const API_KEY = import.meta.env.VITE_API_KEY || ''

function buildHeaders(body) {
  const headers = {}
  if (body) headers['Content-Type'] = 'application/json'
  if (API_KEY) headers['X-API-Key'] = API_KEY
  return Object.keys(headers).length ? headers : undefined
}

export class ApiError extends Error {
  constructor(message, { status = 0, code = 'network_error', hint = null, body = null } = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.hint = hint
    this.body = body
  }

  /** True when the backend is healthy but this pipeline has no results yet. */
  get isPipelineNotReady() {
    return this.status === 503 && this.code === 'pipeline_not_ready'
  }

  get isNetworkError() {
    return this.status === 0
  }

  /** True when something answered, but not with the API's JSON. */
  get isInvalidResponse() {
    return this.code === 'invalid_response'
  }
}

const NOT_JSON = Symbol('not-json')

function parseJson(text) {
  if (!text) return NOT_JSON
  try {
    return JSON.parse(text)
  } catch {
    return NOT_JSON
  }
}

/** Pull the message/code/hint out of a FastAPI error body, which may nest detail. */
function parseErrorBody(body, status) {
  const fallback = `Request failed with status ${status}`
  if (!body || typeof body !== 'object') {
    // A short plain-text error is worth showing; a proxy's HTML error page is not.
    const text = typeof body === 'string' ? body.trim() : ''
    const readable = text && text.length <= 200 && !text.startsWith('<')
    return { message: readable ? text : fallback }
  }

  // HTTPException(detail={...}) arrives as { detail: { detail, error_code, hint } }
  const detail = body.detail
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    return {
      message: detail.detail || fallback,
      code: detail.error_code,
      hint: detail.hint,
    }
  }
  return {
    message: typeof detail === 'string' ? detail : body.detail || fallback,
    code: body.error_code,
    hint: body.hint,
  }
}

export async function request(path, { method = 'GET', body, signal, timeoutMs = DEFAULT_TIMEOUT_MS } = {}) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(new Error('timeout')), timeoutMs)

  // Honour a caller's signal as well as our own timeout.
  const onAbort = () => controller.abort(signal?.reason)
  if (signal) {
    if (signal.aborted) onAbort()
    else signal.addEventListener('abort', onAbort, { once: true })
  }

  try {
    const response = await fetch(`${BASE_URL}${path}`, {
      method,
      headers: buildHeaders(body),
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    })

    const text = await response.text()
    const parsed = parseJson(text)
    const payload = parsed === NOT_JSON ? text || null : parsed

    if (!response.ok) {
      const { message, code, hint } = parseErrorBody(payload, response.status)
      throw new ApiError(message, {
        status: response.status,
        code: code || 'http_error',
        hint,
        body: payload,
      })
    }

    // No Content is the one success that legitimately has no body.
    if (response.status === 204 || response.status === 205) return null

    // Every API success is JSON. Anything else came from something that is not
    // the API - typically a static host answering /api/* with its HTML page
    // because VITE_API_BASE_URL is missing or wrong. Treating that as data
    // would leave the dashboard waiting forever with no error shown.
    if (parsed === NOT_JSON) {
      throw new ApiError('The API returned an unexpected response (expected JSON).', {
        status: response.status,
        code: 'invalid_response',
        hint: 'The dashboard may be pointing at the wrong API address.',
      })
    }

    return parsed
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (error?.name === 'AbortError') {
      // A caller-initiated abort should not be reported as a failure.
      if (signal?.aborted) throw error
      throw new ApiError(`Request to ${path} timed out after ${timeoutMs}ms`, {
        code: 'timeout',
        hint: 'The API may be busy. It will retry automatically.',
      })
    }
    // Shown to public visitors, so no hosts or ports: just what happened.
    throw new ApiError('Cannot reach the CloudGuard API.', {
      code: 'network_error',
      hint: 'The service may be temporarily unavailable. Please try again in a moment.',
    })
  } finally {
    clearTimeout(timer)
    if (signal) signal.removeEventListener('abort', onAbort)
  }
}

export const api = {
  baseUrl: BASE_URL,

  health: (opts) => request('/api/health', opts),
  dashboard: (opts) => request('/api/dashboard', opts),

  costForecast: (opts) => request('/api/costs/forecast', opts),

  security: (opts) => request('/api/security', opts),
  securityEvents: ({ limit = 50, offset = 0, status, minScore, ...opts } = {}) => {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
    if (status) params.set('status', status)
    if (minScore != null) params.set('min_score', String(minScore))
    return request(`/api/security/events?${params}`, opts)
  },

  cloudResources: ({ status, environment, search, idleOnly, ...opts } = {}) => {
    const params = new URLSearchParams()
    if (status) params.set('status', status)
    if (environment) params.set('environment', environment)
    if (search) params.set('search', search)
    if (idleOnly) params.set('idle_only', 'true')
    const query = params.toString()
    return request(`/api/cloud/resources${query ? `?${query}` : ''}`, opts)
  },
  cloudMetrics: (opts) => request('/api/cloud/metrics', opts),

  task: (taskId, opts) => request(`/api/tasks/${encodeURIComponent(taskId)}`, opts),

  // Mutations that start background work.
  refreshAll: (body = {}, opts) =>
    request('/api/dashboard/refresh', { method: 'POST', body, ...opts }),
  runForecast: (body = {}, opts) =>
    request('/api/costs/forecast/run', { method: 'POST', body, ...opts }),
  runAnomalyDetection: (body = {}, opts) =>
    request('/api/security/anomalies/run', { method: 'POST', body, ...opts }),
}
