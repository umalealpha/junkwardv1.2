'use client'
/** Omni staff app token store + fetch. The token is a 30-day device session
 * (Bearer, 64 hex). All calls hit the normal /api/v1 endpoints — the server's
 * StaffDeviceAuthentication resolves the user; every existing permission applies. */
import type { BankBalances } from '@/lib/bankBalances'

export const APP_TOKEN_KEY = 'omni_app_token'
const BASE = `${process.env.NEXT_PUBLIC_API_BASE ?? ''}/api/v1`
const TIMEOUT_MS = 20000

export function getAppToken(): string | null {
  try { return typeof window !== 'undefined' ? localStorage.getItem(APP_TOKEN_KEY) : null } catch { return null }
}
export function setAppToken(t: string) { try { localStorage.setItem(APP_TOKEN_KEY, t) } catch { /* private mode */ } }
export function clearAppToken() {
  try {
    localStorage.removeItem(APP_TOKEN_KEY)
    // Also drop the cached mobile capability manifest so one user's access never
    // carries to the next sign-in on a shared phone (Fable WS-A review).
    localStorage.removeItem('omni.mobile.caps.v1')
  } catch { /* private mode */ }
}

export class AppApiError extends Error {
  status: number
  constructor(message: string, status: number) { super(message); this.status = status }
}

export async function afetch<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const t = getAppToken()
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS)
  let res: Response
  try {
    res = await fetch(`${BASE}${path}`, {
      ...opts, signal: ctrl.signal,
      headers: { 'Content-Type': 'application/json', ...(t ? { Authorization: `Bearer ${t}` } : {}), ...(opts.headers || {}) },
    })
  } catch (err) {
    if ((err as Error)?.name === 'AbortError') throw new AppApiError('The network is slow right now. Please try again.', 0)
    throw new AppApiError('No connection. Check your internet and try again.', 0)
  } finally { clearTimeout(timer) }
  if (res.status === 401) {
    clearAppToken()
    // Send them to sign in — unless they are already there. Navigating to the
    // page you are on reloads it, the shell mounts again, its probes hit 401
    // again, and the sign-in screen "flickers a million times" (CFO, 4-Sep-2026).
    if (typeof window !== 'undefined' && window.location.pathname.replace(/\/+$/, '') !== '/app/login') window.location.href = '/app/login'
    throw new AppApiError('Please sign in again.', 401)
  }
  if (!res.ok) {
    const e = await res.json().catch(() => ({})) as { detail?: string }
    throw new AppApiError(e.detail || `Request failed (${res.status})`, res.status)
  }
  return res.json() as Promise<T>
}

// --- sign-in (reuses the existing staff email-code endpoints) ---
// Step 1 needs email AND password (the server's staff_login_start refuses an
// email alone with "Enter your email and password" — Kago Tshutlhedi hit
// exactly that on 3-Sep-2026 because the first build sent only the email).
export const loginStart = (email: string, password: string) =>
  afetch<{ otp_sent: boolean }>('/auth/staff/start/', { method: 'POST', body: JSON.stringify({ email, password }) })
// Forgot / never-set password: emailed code → set a new one → signed in (device session).
export const forgotStart = (email: string) =>
  afetch<{ otp_sent: boolean }>('/auth/staff/forgot-start/', { method: 'POST', body: JSON.stringify({ email }) })
export const forgotReset = (email: string, code: string, new_password: string) =>
  afetch<{ token: string }>('/auth/staff/forgot-reset/', { method: 'POST',
    body: JSON.stringify({ email, code, new_password, device: 'phone', device_label: deviceLabel() }) })
export const loginVerify = (email: string, code: string) =>
  afetch<{ token?: string; token_type?: string; change_required: boolean; ticket?: string }>(
    '/auth/staff/verify/', { method: 'POST', body: JSON.stringify({ email, code, device: 'phone', device_label: deviceLabel() }) })
export const loginChange = (email: string, ticket: string, new_password: string) =>
  afetch<{ token: string }>('/auth/staff/change/', { method: 'POST',
    body: JSON.stringify({ email, ticket, new_password, device: 'phone', device_label: deviceLabel() }) })

export interface DeviceRow { id: string; label: string; created_at: string; last_seen_at: string | null; expires_at: string; current: boolean }
export const listDevices = () => afetch<{ devices: DeviceRow[] }>('/auth/devices/')
export const revokeDevice = (id: string) => afetch<{ revoked: boolean }>(`/auth/devices/${id}/`, { method: 'DELETE' })

export function deviceLabel(): string {
  if (typeof navigator === 'undefined') return 'Phone'
  const ua = navigator.userAgent
  if (/iPhone/.test(ua)) return 'iPhone'
  if (/iPad|Macintosh/.test(ua) && 'ontouchend' in document) return 'iPad'
  if (/Android/.test(ua)) return 'Android phone'
  return 'Phone'
}

// --- morning bank balances (CFO 2026-09-20) ---
// Same endpoint and same CanViewFinancials gate as the desktop; the device
// token resolves to the same user, so the phone can never see more than the
// browser would. Read-only, GET only. Shape: lib/bankBalances.ts.
export const getBankBalances = () => afetch<BankBalances>('/banking/balances/')

// --- morning-brief note spaces (CFO 2026-09-10) ---
// 25 words, one note per person per morning. The server is idempotent on
// (audience, author, for_date) — a second POST REPLACES my note — so this needs
// no client_key: a double-tap can never leave two notes in the brief.
export type BriefAudience = 'ceo' | 'cfo'
export interface BriefNote {
  id: string; author: string; author_username: string; is_mine: boolean; body: string
  words: number; status: string; for_date: string; locked: boolean; created_at: string
}
export interface BriefNoteAccess {
  audiences: BriefAudience[]; can_post_ceo: boolean; can_post_cfo: boolean
  word_limit: number; next_brief_date: string
}
export interface BriefNoteSpace {
  audience: BriefAudience; for_date: string; shared: boolean
  can_read_all: boolean; can_post: boolean
  word_limit: number; my_note: BriefNote | null; notes: BriefNote[]
}
export const getBriefNoteAccess = () => afetch<BriefNoteAccess>('/brief-notes/access/')
export const getBriefNoteSpace = (audience: BriefAudience) =>
  afetch<BriefNoteSpace>(`/brief-notes/?audience=${encodeURIComponent(audience)}`)
export const writeBriefNote = (audience: BriefAudience, body: string) =>
  afetch<BriefNote>('/brief-notes/', { method: 'POST', body: JSON.stringify({ audience, body }) })
export const withdrawBriefNote = (id: string) =>
  afetch<{ withdrawn: boolean; id: string }>(`/brief-notes/${id}/`, { method: 'DELETE' })
