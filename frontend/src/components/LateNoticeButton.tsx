'use client'

/**
 * "I'm running late" — rule 1b of the monthly review (CFO 2026-09-09).
 *
 * Arriving after 08:15 on three mornings costs two points off the month. The
 * CFO will forgive it if the person says so — but only on the morning itself
 * and only before 09:00, because an excuse filed at lunchtime is a defence,
 * not a warning. Both limits are enforced on the server; this only mirrors
 * them so the screen never promises something the server will refuse.
 *
 * It hides itself once the window has closed and nothing was filed, so the
 * common case — the person who arrived on time — sees no clutter at all.
 */
import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2, Clock, Loader2 } from 'lucide-react'
import { authedHrisFetch } from '@/app/(dashboard)/hris/_shared'

type Kind = 'late' | 'sick' | 'client' | 'other'

interface Notice { kind: Kind; reason: string; in_time: boolean; filed_at?: string | null }
interface State { date?: string; window_open?: boolean; cutoff?: string; notice?: Notice | null }

const KINDS: Array<[Kind, string]> = [
  ['late', 'Running late'],
  ['sick', 'Not well today'],
  ['client', 'With a client first'],
  ['other', 'Something else'],
]

export function LateNoticeButton() {
  const [state, setState] = useState<State | null>(null)
  const [open, setOpen] = useState(false)
  const [kind, setKind] = useState<Kind>('late')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const load = useCallback(async () => {
    try {
      // Two separate faults fixed here on 2026-09-10, both of which showed as
      // "the tile is not there":
      //
      //  1. the path carried an ${API_BASE} prefix, so it asked for
      //     /api/v1/hris/api/late-notice/ while the route is mounted at the
      //     site root — 404 on every load and every filing (CF-404-01 class);
      //  2. it was a bare fetch sending `Token ${localStorage.alpha_token}`.
      //     For an Azure-SSO user that value is the sentinel '__sso__', so the
      //     header went out as "Token __sso__" and the server answered 401 —
      //     the same invisible tile, just a different status code (the
      //     FE-SWEEP 2026-06-08 class).
      //
      // authedHrisFetch fixes both at once: it is the helper every other
      // /hris/api/* caller uses, it resolves Bearer→Token properly, retries a
      // cold MSAL token, and recovers a dead sign-in.
      const r = await authedHrisFetch('/hris/api/late-notice/')
      if (r.ok) setState(await r.json())
      else if (r.status !== 401 && r.status !== 403) {
        // A self-hiding tile is a silent failure: this component returns null
        // whenever `state` is unset, which is exactly why the 404 above lived
        // unnoticed. Leave a trace for the next person.
        console.warn('late-notice: HTTP', r.status)
      }
    } catch { /* the tile simply does not appear */ }
  }, [])

  useEffect(() => { void load() }, [load])

  const submit = useCallback(async () => {
    setBusy(true); setErr('')
    try {
      const r = await authedHrisFetch('/hris/api/late-notice/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kind, reason }),
      })
      const body = await r.json().catch(() => ({}))
      if (!r.ok) { setErr(body?.detail || 'Could not save that. Try again.'); return }
      setOpen(false)
      await load()
    } catch {
      setErr('No connection. Try again.')
    } finally { setBusy(false) }
  }, [kind, reason, load])

  if (!state) return null

  // Already told us this morning.
  if (state.notice) {
    const good = state.notice.in_time
    return (
      <div style={{
        display: 'flex', gap: 8, alignItems: 'flex-start', marginTop: 10, padding: '9px 11px',
        borderRadius: 10, fontSize: 12.5, lineHeight: 1.5,
        background: good ? 'rgba(22,163,74,.08)' : 'rgba(217,119,6,.10)',
        color: good ? '#15803d' : '#b45309',
      }}>
        {good ? <CheckCircle2 className="w-4 h-4" style={{ flexShrink: 0, marginTop: 1 }} />
              : <AlertTriangle className="w-4 h-4" style={{ flexShrink: 0, marginTop: 1 }} />}
        <span>{good
          ? 'You told us about this morning in time — it will not count against you.'
          : `You told us after ${state.cutoff}, so this morning still counts. Tell us before ${state.cutoff} next time.`}</span>
      </div>
    )
  }

  // Window shut and nothing filed — say nothing at all.
  if (!state.window_open) return null

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        style={{
          marginTop: 10, width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center',
          gap: 7, padding: '9px 12px', borderRadius: 10, cursor: 'pointer',
          border: '1px solid rgba(244,166,35,.45)', background: 'rgba(244,166,35,.10)',
          color: '#b45309', fontSize: 12.5, fontWeight: 600,
        }}>
        <Clock className="w-4 h-4" />
        Running late this morning? Tell us before {state.cutoff}
      </button>
    )
  }

  return (
    <div style={{
      marginTop: 10, padding: 12, borderRadius: 10,
      border: '1px solid rgba(244,166,35,.45)', background: 'rgba(244,166,35,.07)',
    }}>
      <div style={{ fontSize: 12.5, fontWeight: 600, marginBottom: 8 }}>
        Tell us about this morning
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 9 }}>
        {KINDS.map(([k, label]) => (
          <button key={k} onClick={() => setKind(k)}
            style={{
              padding: '5px 11px', borderRadius: 999, fontSize: 12, cursor: 'pointer',
              border: kind === k ? '1px solid #b45309' : '1px solid rgba(100,116,139,.35)',
              background: kind === k ? 'rgba(244,166,35,.22)' : 'transparent',
              fontWeight: kind === k ? 600 : 400,
            }}>{label}</button>
        ))}
      </div>
      <textarea
        value={reason} onChange={e => setReason(e.target.value)} rows={2} maxLength={500}
        placeholder="A line on why (optional) — your manager sees this."
        style={{
          width: '100%', fontSize: 12.5, padding: '7px 9px', borderRadius: 8, resize: 'vertical',
          border: '1px solid rgba(100,116,139,.35)', background: 'transparent', fontFamily: 'inherit',
        }} />
      {err && <div style={{ color: '#b91c1c', fontSize: 12, marginTop: 6 }}>{err}</div>}
      <div style={{ display: 'flex', gap: 8, marginTop: 9 }}>
        <button onClick={submit} disabled={busy}
          style={{
            flex: 1, padding: '8px 12px', borderRadius: 8, border: 'none', cursor: 'pointer',
            background: '#0D1B2A', color: '#F4A623', fontSize: 12.5, fontWeight: 600,
            display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
          }}>
          {busy && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
          Send
        </button>
        <button onClick={() => { setOpen(false); setErr('') }}
          style={{
            padding: '8px 14px', borderRadius: 8, cursor: 'pointer', fontSize: 12.5,
            border: '1px solid rgba(100,116,139,.35)', background: 'transparent',
          }}>Cancel</button>
      </div>
      <div style={{ fontSize: 11.5, color: '#64748b', marginTop: 8, lineHeight: 1.45 }}>
        Only for today, and only before {state.cutoff}. Approved leave, a logged client visit
        and same-day sick leave already excuse the morning on their own.
      </div>
    </div>
  )
}
