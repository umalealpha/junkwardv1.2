'use client'

/** Small shared pieces for the five "raise it from your phone" staff screens
 * (raise PO, raise payment, vehicle trip, letter request, staff loan). Same
 * look as the existing /m/staff screens: navy hero header, white cards, one
 * orange primary button, 44px touch targets. Nothing here computes money. */
import Link from 'next/link'
import { ArrowLeft, RefreshCw } from 'lucide-react'
import { ApiError, staffToken, staffReauth } from '@/app/(customer)/api'
import { C, serif, sans, card, headerPad } from '@/app/(customer)/ui'

export const inputStyle: React.CSSProperties = {
  width: '100%', boxSizing: 'border-box', minHeight: 44, padding: '12px 14px', border: 'none',
  background: '#F0F2F5', borderRadius: 12, fontSize: 15, color: C.ink, outline: 'none', fontFamily: sans,
}
export const labelStyle: React.CSSProperties = { display: 'block', fontSize: 12, fontWeight: 700, color: C.inkSoft, margin: '10px 0 6px' }
// Navy text on the orange gradient: white on #F4A623 is ~2:1 and failed axe
// colour-contrast (serious) on Raise a PO / Raise a payment; navy passes AA at
// both ends of the gradient.
export const primaryBtn = (busy: boolean): React.CSSProperties => ({
  width: '100%', minHeight: 48, padding: 14, borderRadius: 999, border: 'none',
  background: `linear-gradient(135deg, ${C.orange}, ${C.orangeDeep})`, color: C.head, fontWeight: 800,
  fontSize: 15, cursor: busy ? 'default' : 'pointer', fontFamily: sans, opacity: busy ? 0.6 : 1,
  display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
})
export const ghostBtn: React.CSSProperties = {
  minHeight: 44, padding: '10px 16px', borderRadius: 999, border: `1px solid ${C.line}`, background: C.card,
  color: C.ink, fontWeight: 700, fontSize: 13, cursor: 'pointer', fontFamily: sans,
}
export const STATUS_COLOR: Record<string, string> = {
  approved: '#047857', issued: '#047857', active: '#047857', closed: '#047857', paid: '#047857', signed: '#2563EB',
  rejected: '#B91C1C', declined: '#B91C1C', refused: '#B91C1C', cancelled: C.inkSoft,
  pending: '#B45309', pending_cfo: '#B45309', pending_finance: '#B45309', draft: C.inkSoft,
  checked_out: '#B45309', returned_pending: '#2563EB',
}

export function ScreenFrame({ title, base, children, footer }: {
  title: string; base: string; children: React.ReactNode; footer?: React.ReactNode
}) {
  return (
    <div style={{ background: C.surface, minHeight: '100vh', fontFamily: sans }}>
      <header style={{ display: 'flex', alignItems: 'center', gap: 12, padding: headerPad, background: C.navy, color: '#fff' }}>
        <Link href={base} aria-label="Back" style={{ color: '#fff', display: 'grid', placeItems: 'center', minWidth: 44, minHeight: 44, margin: '-12px 0 -12px -12px' }}><ArrowLeft size={20} /></Link>
        <h1 style={{ fontFamily: serif, fontWeight: 800, fontSize: 20, margin: 0, color: '#fff' }}>{title}</h1>
      </header>
      <main style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 14 }}>{children}</main>
      {footer}
    </div>
  )
}

export function Card({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return <div style={{ ...card, padding: 18, ...style }}>{children}</div>
}

/** Inline network-error banner with a retry button — never a blank screen or a
 * fake "0" when the server could not be reached. */
export function RetryBanner({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div role="alert" style={{ ...card, padding: 14, borderColor: '#FCA5A5', background: '#FEF2F2', display: 'flex', alignItems: 'center', gap: 12 }}>
      <p style={{ margin: 0, flex: 1, fontSize: 13, color: '#991B1B', lineHeight: 1.5 }}>{message}</p>
      <button onClick={onRetry} style={{ ...ghostBtn, color: '#991B1B', display: 'flex', alignItems: 'center', gap: 6 }}>
        <RefreshCw size={14} /> Retry
      </button>
    </div>
  )
}

/** The server's own words, shown verbatim (a control code first when it sent one). */
export function ServerMessage({ text, control, tone = 'warn' }: { text: string; control?: string; tone?: 'warn' | 'error' | 'ok' }) {
  const bg = tone === 'ok' ? '#ECFDF5' : tone === 'error' ? '#FEF2F2' : '#FFFBEB'
  const fg = tone === 'ok' ? '#065F46' : tone === 'error' ? '#991B1B' : '#92400E'
  const bd = tone === 'ok' ? '#A7F3D0' : tone === 'error' ? '#FCA5A5' : '#FCD34D'
  return (
    <div role="status" style={{ background: bg, border: `1px solid ${bd}`, borderRadius: 12, padding: '10px 12px', fontSize: 13, color: fg, lineHeight: 1.5, whiteSpace: 'pre-line' }}>
      {control && <b style={{ display: 'block', fontSize: 11, letterSpacing: '0.04em', marginBottom: 2 }}>{control}</b>}
      {text}
    </div>
  )
}

export function Toast({ text }: { text: string | null }) {
  if (!text) return null
  return (
    <div style={{ position: 'fixed', bottom: 32, left: '50%', transform: 'translateX(-50%)', background: C.navy, color: '#fff', padding: '11px 20px', borderRadius: 999, fontSize: 13, fontWeight: 600, zIndex: 40, maxWidth: '88%', textAlign: 'center' }}>
      {text}
    </div>
  )
}

export function StatusPill({ status, label }: { status: string; label?: string }) {
  // 13px/800 in #047857 (not 12px #059669, which failed AA on white).
  return <span style={{ fontSize: 13, fontWeight: 800, color: STATUS_COLOR[status] || C.inkSoft, whiteSpace: 'nowrap' }}>{label || status}</span>
}

export const errText = (e: unknown, fallback: string) => (e instanceof Error && e.message ? e.message : fallback)

// ---------------------------------------------------------------------------
// Raw response access. `sfetch` flattens an error body to ONE string and drops
// the `control` code, which is right for most screens. Two of these screens
// must show the server's words exactly as sent — a DRF field-error map on a PO
// ("lines[1].unit_price: A valid number is required."), or a payment control
// (PAY-AMT-01 / PAY-BANK-03 / PAY-SUP-01) that decides which tick-box to open
// — so this returns the body untouched. Same token, same base, same 401
// recovery as sfetch; nothing else differs.
// ---------------------------------------------------------------------------
const API = `${process.env.NEXT_PUBLIC_API_BASE ?? ''}/api/v1`
const HRIS = `${process.env.NEXT_PUBLIC_API_BASE ?? ''}/hris/api`
export type JsonBody = Record<string, unknown>
export interface RawResult { ok: boolean; status: number; body: JsonBody }

async function rawFetch(base: string, path: string, init: RequestInit): Promise<RawResult> {
  const { token: t, app } = staffToken()   // app device token wins, else Nexus token (same as sfetch)
  const isForm = typeof FormData !== 'undefined' && init.body instanceof FormData
  let res: Response
  try {
    res = await fetch(`${base}${path}`, {
      ...init,
      headers: {
        ...(isForm ? {} : { 'Content-Type': 'application/json' }),
        ...(t ? { Authorization: `Bearer ${t}` } : {}),
        ...(init.headers || {}),
      },
    })
  } catch {
    throw new ApiError('No connection. Check your internet and try again.', 0)
  }
  if (res.status === 401 && typeof window !== 'undefined') {
    staffReauth(app)
    throw new ApiError('Please sign in again.', 401)
  }
  let body: JsonBody = {}
  try { const j: unknown = await res.json(); if (j && typeof j === 'object') body = j as JsonBody } catch { /* empty / non-JSON */ }
  return { ok: res.ok, status: res.status, body }
}
export const rawStaffFetch = (path: string, init: RequestInit = {}) => rawFetch(API, path, init)
export const rawHrisFetch = (path: string, init: RequestInit = {}) => rawFetch(HRIS, path, init)

/** Fetch a protected file (e.g. an issued letter PDF) with the staff token and
 * open it in a new tab — the HRIS base has no equivalent of sfetchBlob. */
export async function openHrisFile(pathOrUrl: string): Promise<void> {
  const { token: t, app } = staffToken()
  const url = /^https?:/.test(pathOrUrl) ? pathOrUrl
    : `${process.env.NEXT_PUBLIC_API_BASE ?? ''}${pathOrUrl.startsWith('/') ? '' : '/'}${pathOrUrl}`
  const res = await fetch(url, { headers: { ...(t ? { Authorization: `Bearer ${t}` } : {}) } })
  if (res.status === 401) { staffReauth(app); throw new ApiError('Please sign in again.', 401) }
  if (!res.ok) throw new ApiError(`Download failed (${res.status})`, res.status)
  const blob = await res.blob()
  const u = URL.createObjectURL(blob)
  window.open(u, '_blank', 'noopener')
  setTimeout(() => URL.revokeObjectURL(u), 60_000)
}

/** Turn a DRF error body into readable lines, verbatim. {"detail": "..."} →
 * one line; {"lines": [{}, {"unit_price": ["A valid number is required."]}]} →
 * "lines[2] · unit_price: A valid number is required." */
export function formatServerErrors(body: JsonBody, status: number): string {
  const out: string[] = []
  const walk = (v: unknown, path: string) => {
    if (v == null || v === '') return
    if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') { out.push(path ? `${path}: ${v}` : String(v)); return }
    if (Array.isArray(v)) {
      const allStrings = v.every(x => typeof x === 'string')
      if (allStrings) { out.push(path ? `${path}: ${v.join(' ')}` : v.join(' ')); return }
      v.forEach((x, i) => walk(x, path ? `${path}[${i + 1}]` : `[${i + 1}]`))
      return
    }
    if (typeof v === 'object') {
      for (const [k, x] of Object.entries(v as JsonBody)) {
        if (k === 'control' || k === 'duplicates' || k === 'duplicate_warnings' || k === 'new_payee' || k === 'valid') continue
        walk(x, path ? `${path} · ${k}` : k === 'detail' || k === 'error' || k === 'non_field_errors' ? '' : k)
      }
    }
  }
  walk(body, '')
  return out.length ? out.join('\n') : `Request failed (${status})`
}
