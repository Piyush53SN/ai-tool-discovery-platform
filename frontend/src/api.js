/**
 * Thin fetch wrapper around the DRF API.
 *
 * - JWT access token attached automatically
 * - a single queued refresh on 401 (concurrent 401s share one refresh call)
 * - In development the Vite dev server proxies /api to Django, so the browser
 *   never crosses origins.
 * - In production (Netlify + Render) the build sets VITE_API_BASE to the
 *   Render API origin (https://…onrender.com); the API is then called
 *   cross-origin and CORS must allow the Netlify origin on the backend.
 */

const API_ORIGIN = import.meta.env.VITE_API_BASE ?? ''
const BASE = `${API_ORIGIN}/api`
const ACCESS_KEY = 'aitools.access'
const REFRESH_KEY = 'aitools.refresh'

export const tokens = {
  get access() { return localStorage.getItem(ACCESS_KEY) },
  get refresh() { return localStorage.getItem(REFRESH_KEY) },
  set({ access, refresh }) {
    if (access) localStorage.setItem(ACCESS_KEY, access)
    if (refresh) localStorage.setItem(REFRESH_KEY, refresh)
  },
  clear() {
    localStorage.removeItem(ACCESS_KEY)
    localStorage.removeItem(REFRESH_KEY)
  },
}

export class ApiError extends Error {
  constructor(status, payload) {
    super(`API error ${status}`)
    this.status = status
    this.payload = payload
  }
}

let refreshPromise = null

async function tryRefresh() {
  if (!refreshPromise) {
    refreshPromise = (async () => {
      const refresh = tokens.refresh
      if (!refresh) return false
      const res = await fetch(`${BASE}/auth/token/refresh/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh }),
      })
      if (!res.ok) { tokens.clear(); return false }
      const data = await res.json()
      tokens.set({ access: data.access, refresh: data.refresh ?? refresh })
      return true
    })().finally(() => { refreshPromise = null })
  }
  return refreshPromise
}

export async function api(path, { method = 'GET', body, raw = false, skipRetry } = {}) {
  const headers = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (tokens.access) headers.Authorization = `Bearer ${tokens.access}`

  const res = await fetch(`${BASE}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })

  if (res.status === 401 && !skipRetry && tokens.refresh) {
    const ok = await tryRefresh()
    if (ok) return api(path, { method, body, raw, skipRetry: true })
  }

  if (res.status === 204) return null
  const text = await res.text()
  const data = text ? JSON.parse(text) : null
  if (!res.ok) throw new ApiError(res.status, data)
  return raw ? res : data
}

/** Fetch helper for plain query-string building. */
export function qs(params) {
  const usable = Object.entries(params).filter(
    ([, v]) => v !== undefined && v !== null && v !== '' && !(Array.isArray(v) && !v.length),
  )
  if (!usable.length) return ''
  return '?' + usable.map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&')
}
