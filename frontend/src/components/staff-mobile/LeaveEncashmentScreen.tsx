'use client'

/** /app/leave-encashment · /m/staff/leave-encashment — apply to cash out annual
 * leave (CFO pick 2026-09-03). Same endpoints as the desktop /hris/leave-encashment
 * page (hris/leave_encash_views.py):
 *   GET  /hris/api/leave-encashment/   → { me, quote, requests }
 *   POST /hris/api/leave-encashment/   { days, reason }
 * EVERY figure shown — daily rate, payout, PAYE, net — is the server's. The
 * quote carries a pre-computed `tax_by_days` table so the phone only LOOKS UP
 * the row for the chosen day-count; it never multiplies or taxes anything.
 * Approvers keep approving on the Approve tab — no approve action here. */
import { useCallback, useEffect, useState } from 'react'
import { Send } from 'lucide-react'
import { hfetch, reauthOn401 } from '@/app/(customer)/api'
import { C } from '@/app/(customer)/ui'
import { useStaffBase } from './useStaffBase'
import {
  Card, RetryBanner, ScreenFrame, ServerMessage, StatusPill, Toast, errText, formatServerErrors,
  inputStyle, labelStyle, primaryBtn, rawHrisFetch,
} from './StaffFormKit'

interface TaxRow { payout: string; tax: string; net: string }
interface Quote {
  has_record: boolean; employee_name?: string; basic_salary?: string; basic_source?: string
  daily_rate?: string; available_days?: string; min_residual_days?: string; max_encashable_days?: string
  can_apply?: boolean; tax_base?: string; tax_by_days?: Record<string, TaxRow>
}
interface Sig { signed: boolean; by: string; at: string | null }
interface Enc {
  id: string; days: string; daily_rate: string; amount: string; tax_amount: string; net_amount: string
  reason: string; status: string; status_label: string; is_own: boolean; created_at: string | null
  signatures: { cfo: Sig; hr: Sig; finance: Sig }
  rejected: { at: string | null; stage: string; notes: string } | null
  paid: { is_paid: boolean; at: string | null }
}
interface Payload { me: Record<string, boolean>; quote: Quote; requests: Enc[] }

const pula = (s: string | undefined | null) => s == null || s === '' ? '—' : `P${new Intl.NumberFormat('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Number(s) || 0)}`
// '2' for whole days, '2.5' for halves — the key format the server's table uses.
const daysKey = (d: number) => Number.isInteger(d) ? String(d) : d.toFixed(1)
const MIN_WORDS = 50

export default function LeaveEncashmentScreen() {
  const base = useStaffBase()
  const [data, setData] = useState<Payload | null>(null)
  const [loadErr, setLoadErr] = useState<string | null>(null)
  const [days, setDays] = useState(1)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [serverErr, setServerErr] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  const show = (m: string) => { setToast(m); setTimeout(() => setToast(null), 4000) }

  const load = useCallback(() => {
    setLoadErr(null)
    hfetch<Payload>('/leave-encashment/').then(setData)
      .catch(e => { if (!reauthOn401(e)) setLoadErr(errText(e, 'Could not load your leave-pay quote.')) })
  }, [])
  useEffect(() => { load() }, [load])

  const q = data?.quote
  const maxDays = Number(q?.max_encashable_days ?? 0)
  const row = q?.tax_by_days?.[daysKey(days)]
  const words = reason.trim() ? reason.trim().split(/\s+/).length : 0
  const step = (d: number) => setDays(v => Math.min(Math.max(0.5, Math.round((v + d) * 2) / 2), Math.max(0.5, maxDays)))

  async function apply() {
    setServerErr(null)
    setBusy(true)
    try {
      // Server reads request.data.get('days') and request.data.get('reason').
      const r = await rawHrisFetch('/leave-encashment/', { method: 'POST', body: JSON.stringify({ days: daysKey(days), reason: reason.trim() }) })
      if (!r.ok) { setServerErr(formatServerErrors(r.body, r.status)); return }
      show('Sent for approval — CFO first, then HR, then Finance. ✅'); setReason(''); setDays(1); load()
    } catch (e) { if (!reauthOn401(e)) setServerErr(errText(e, 'Could not submit.')) }
    finally { setBusy(false) }
  }

  const mine = (data?.requests ?? []).filter(r => r.is_own)
  const canApply = !!q?.has_record && !!q?.can_apply

  return (
    <ScreenFrame title="Leave pay" base={base}>
      {loadErr && <RetryBanner message={loadErr} onRetry={load} />}
      <Card>
        <p style={{ color: C.inkSoft, fontSize: 13, margin: 0, lineHeight: 1.5 }}>
          Cash out some of your annual leave. Valued at your <b style={{ color: C.ink }}>basic salary ÷ 24</b> per day, taxed as normal pay, and approved by the CFO, HR and Finance in turn.
        </p>
        {data === null && !loadErr && <p style={{ color: C.inkSoft, fontSize: 13, margin: '10px 0 0' }}>Loading…</p>}
        {q && !q.has_record && <div style={{ marginTop: 10 }}><ServerMessage text="Your account isn't linked to an employee record yet, so you can't apply. Ask HR to link you first." /></div>}
        {q?.has_record && (
          <dl style={{ margin: '12px 0 0', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
            {([['Days available', `${q.available_days} d`], ['Must keep', `${q.min_residual_days} d`], ['Can cash out', `${q.max_encashable_days} d`], ['Daily rate', pula(q.daily_rate)]] as const).map(([k, v]) => (
              <div key={k} style={{ background: '#F6F7F9', borderRadius: 12, padding: '8px 10px' }}>
                <dt style={{ fontSize: 11, color: C.inkSoft, fontWeight: 700 }}>{k}</dt>
                <dd style={{ margin: 0, fontSize: 15, fontWeight: 800, color: C.ink }}>{v}</dd>
              </div>
            ))}
          </dl>
        )}
        {q?.has_record && q.basic_source && <p style={{ margin: '6px 0 0', fontSize: 11.5, color: C.inkSoft }}>Rate from your {q.basic_source} payslip basic ({pula(q.basic_salary)}).</p>}
        {q?.has_record && !q.can_apply && <div style={{ marginTop: 10 }}><ServerMessage text={Number(q.basic_salary) > 0 ? `You need to keep ${q.min_residual_days} days, so there is nothing to cash out right now.` : 'No basic salary is on your payslips yet — HR must load payroll before this can be valued.'} /></div>}

        {canApply && (
          <>
            <label htmlFor="enc-days" style={labelStyle}>Days to cash out (half-day steps)</label>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <button type="button" aria-label="Half a day less" onClick={() => step(-0.5)} disabled={days <= 0.5} style={{ minWidth: 48, minHeight: 48, borderRadius: 14, border: `1px solid ${C.line}`, background: C.card, color: C.ink, fontSize: 22, fontWeight: 800, cursor: 'pointer' }}>−</button>
              <input id="enc-days" value={daysKey(days)} readOnly aria-live="polite" style={{ ...inputStyle, textAlign: 'center', fontWeight: 800, fontSize: 18, flex: 1 }} />
              <button type="button" aria-label="Half a day more" onClick={() => step(0.5)} disabled={days >= maxDays} style={{ minWidth: 48, minHeight: 48, borderRadius: 14, border: `1px solid ${C.line}`, background: C.card, color: C.ink, fontSize: 22, fontWeight: 800, cursor: 'pointer' }}>+</button>
            </div>
            <div aria-live="polite" style={{ marginTop: 10, background: '#0D1B2A', color: '#fff', borderRadius: 14, padding: '12px 14px' }}>
              {row ? (
                <>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13 }}><span>Gross ({daysKey(days)} d × {pula(q.daily_rate)})</span><b>{pula(row.payout)}</b></div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, marginTop: 4, opacity: 0.9 }}><span>PAYE withheld</span><span>− {pula(row.tax)}</span></div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 16, marginTop: 8, paddingTop: 8, borderTop: '1px solid rgba(255,255,255,0.2)' }}><b>You would receive</b><b style={{ color: C.orange }}>{pula(row.net)}</b></div>
                  <p style={{ margin: '6px 0 0', fontSize: 11, opacity: 0.8 }}>Worked out by Omni from your payslip — the exact figure is fixed when you apply.</p>
                </>
              ) : <span style={{ fontSize: 13 }}>Pick a day-count up to {q.max_encashable_days} to see the figures.</span>}
            </div>
            <label htmlFor="enc-reason" style={labelStyle}>Why you need this (at least {MIN_WORDS} words)</label>
            <textarea id="enc-reason" value={reason} onChange={e => setReason(e.target.value)} rows={5}
              placeholder="Explain in your own words why you need to cash out leave now."
              style={{ ...inputStyle, resize: 'vertical' }} />
            <p style={{ fontSize: 12, color: words >= MIN_WORDS ? '#065F46' : C.inkSoft, margin: '6px 0 0' }}>{words}/{MIN_WORDS} words</p>
            {serverErr && <div style={{ marginTop: 12 }}><ServerMessage text={serverErr} tone="error" /></div>}
            <button onClick={apply} disabled={busy} style={{ ...primaryBtn(busy), marginTop: 14, color: '#0D1B2A' }}>
              <Send size={16} aria-hidden="true" /> {busy ? 'Sending…' : 'Apply to cash out'}
            </button>
          </>
        )}
      </Card>

      <b style={{ color: C.ink, fontSize: 15 }}>My applications</b>
      {data && mine.length === 0 && <p style={{ color: C.inkSoft, fontSize: 13, margin: 0 }}>No applications yet.</p>}
      {mine.map(r => {
        const legs: [string, boolean][] = [['CFO', r.signatures.cfo.signed], ['HR', r.signatures.hr.signed], ['Finance', r.signatures.finance.signed], ['Paid', r.paid.is_paid]]
        return (
          <Card key={r.id} style={{ padding: 14 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
              <b style={{ color: C.ink, fontSize: 14.5 }}>{r.days} days · {pula(r.amount)} gross</b>
              <StatusPill status={r.status} label={r.status_label} />
            </div>
            <p style={{ margin: '4px 0 0', fontSize: 12.5, color: C.inkSoft }}>Net {pula(r.net_amount)} after {pula(r.tax_amount)} PAYE · {r.created_at ? new Date(r.created_at).toLocaleDateString() : ''}</p>
            <ol aria-label="Approval progress" style={{ listStyle: 'none', margin: '8px 0 0', padding: 0, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {legs.map(([l, ok]) => (
                <li key={l} style={{ fontSize: 11, fontWeight: 700, padding: '3px 9px', borderRadius: 999, background: ok ? C.navy : '#F0F2F5', color: ok ? '#fff' : C.inkSoft }}>
                  {ok ? '✓ ' : ''}{l}
                </li>
              ))}
            </ol>
            {r.rejected && <p style={{ color: '#991B1B', fontSize: 12.5, margin: '8px 0 0' }}>↩ Declined at {r.rejected.stage}{r.rejected.notes ? `: ${r.rejected.notes}` : ''}</p>}
          </Card>
        )
      })}
      <Toast text={toast} />
    </ScreenFrame>
  )
}
