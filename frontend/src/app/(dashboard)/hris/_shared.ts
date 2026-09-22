/**
 * Shared helpers for the native HRIS subpages. Each subpage gates on
 * the CFO whitelist (useHrisAccess), uses the same authed fetch for
 * /hris/api/* endpoints, and shows the same skeleton while loading.
 *
 * Kept tiny on purpose — every HRIS subpage is meant to look like a
 * first-class Omni surface (TopBar + theme + cards), not the Graphiter
 * iframe it replaced on 2026-05-18 (CFO directive).
 */
import { getToken, recoverFromDeadSignIn, SSO_SENTINEL_TOKEN } from '@/lib/api'

export interface HrisEmployee {
  id: number
  pid?: string
  eid?: string
  segment?: string
  nm: string
  email?: string
  en?: string
  ps: string
  dp: string
  company: string
  grade: string
  salary: number
  hired: string
  mg: string
  mid?: string
  img: string
  gn: string
  age: number
  loc: string
  grossActual?: number
  netActual?: number
  payeActual?: number
  comp?: Record<string, number>
  vals?: number[]
  pot?: number[]
  okrs?: { nm: string; w: number; s1: number; s2: number }[]
}

export interface HrisEmployeesResponse {
  count: number
  employees: HrisEmployee[]
}

export async function authedHrisFetch(path: string, init?: RequestInit): Promise<Response> {
  async function buildHeaders(): Promise<{ headers: Record<string, string>; bearer: string | null }> {
    const headers: Record<string, string> = { ...(init?.headers as Record<string, string> || {}) }
    let bearer: string | null = null
    try {
      const { acquireApiToken, SSO_API_CALLS_READY } = await import('@/auth/msal')
      if (SSO_API_CALLS_READY) {
        bearer = await acquireApiToken()
        if (bearer) headers['Authorization'] = `Bearer ${bearer}`
      }
    } catch { /* fall through to DRF token */ }
    if (!headers['Authorization']) {
      const t = getToken()
      if (t && t !== SSO_SENTINEL_TOKEN) headers['Authorization'] = `Token ${t}`
    }
    return { headers, bearer }
  }

  // CFO directive 2026-05-18 multi-entity isolation: forward the topbar
  // company selection on every HRIS read so subsidiaries don't see each
  // other's employees. Same auto-injection as apiFetch for /api/v1/*.
  const method = (init?.method || 'GET').toUpperCase()
  let finalPath = path
  if (method === 'GET' && typeof window !== 'undefined' && !path.includes('company=')) {
    const company = localStorage.getItem('alpha_company_id')
    if (company) {
      finalPath = path + (path.includes('?') ? '&' : '?') + `company=${encodeURIComponent(company)}`
    }
  }

  const first = await buildHeaders()
  let response = await fetch(finalPath, { credentials: 'include', ...init, headers: first.headers })
  let bearer = first.bearer

  // Cold-MSAL retry BEFORE any recovery, mirroring apiFetchRaw. On a hard page
  // load the MSAL access token is often not ready for a second, so the first
  // call goes out unauthenticated and the server answers 401/403. Without this
  // retry the recovery below would read that as a dead sign-in and log out a
  // user who is perfectly signed in — a worse bug than the one being fixed
  // (Fable review, 4-Aug-2026: 9 HRIS pages fetch on mount with no access hook).
  if ((response.status === 401 || response.status === 403) && !bearer
      && typeof window !== 'undefined') {
    const retry = await buildHeaders()
    if (retry.bearer) {
      response = await fetch(finalPath, { credentials: 'include', ...init, headers: retry.headers })
      bearer = retry.bearer
    }
  }

  // An expired sign-in must send the user to /login, exactly as it does on the
  // /api/v1 pages. Without this, every HRIS page turned a dead sign-in into a
  // permission message the user could do nothing about — "Access restricted." on
  // Monthly Feedback, "Could not download the payslip (error 403)" on Payslips
  // (two staff bug reports, 4-Aug-2026). Retrying never helped, because the dead
  // key stayed in localStorage.
  await recoverFromDeadSignIn(response, bearer)
  return response
}

export async function fetchHrisEmployees(): Promise<HrisEmployeesResponse> {
  const r = await authedHrisFetch('/hris/api/employees/')
  if (!r.ok) return { count: 0, employees: [] }
  return r.json()
}

export function fmtPula(n: number): string {
  return `P${new Intl.NumberFormat('en-BW', { maximumFractionDigits: 0 }).format(n || 0)}`
}
