'use client'

/** Apply for a staff loan from the phone. Drives the SAME endpoints as the desktop
 * /hris/staff-loans page (frontend/src/app/(dashboard)/hris/staff-loans/page.tsx);
 * backend staff_loans/views.py (StaffLoanApplicationViewSet):
 *   GET  /staff-loans/meta/           scheme limits + my salary cap (shown, never enforced here — the server is the gate)
 *   GET  /staff-loans/                my applications (server scopes non-HR users to their own)
 *   POST /staff-loans/                { loan_type:'staff', amount_requested, term_months_requested, reason, no_other_loans }
 *   POST /staff-loans/<id>/submit/    send to the CFO
 *   POST /staff-loans/<id>/cancel/
 * Interest / instalment figures are the SERVER's, echoed from the application row. */
import { useCallback, useEffect, useState } from 'react'
import { Send, Wallet } from 'lucide-react'
import { reauthOn401, sfetch } from '@/app/(customer)/api'
import { C } from '@/app/(customer)/ui'
import { useStaffBase } from './useStaffBase'
import {
  Card, RetryBanner, ScreenFrame, ServerMessage, StatusPill, Toast, errText, formatServerErrors,
  ghostBtn, inputStyle, labelStyle, primaryBtn, rawStaffFetch,
} from './StaffFormKit'

interface Loan {
  id: string; loan_type_display: string; amount_requested: string; term_months_requested: number; reason: string
  status: string; status_display: string; decline_reason: string; approved_amount: string | null; approved_term_months: number | null
  annual_rate_pct: string; total_repayable: string; monthly_instalment: string; outstanding: string | null
  is_mine: boolean; can_submit: boolean; can_cancel: boolean; can_sign: boolean
}
interface Meta {
  default_rate_pct: string; staff_max_term_months: number; has_employee_record: boolean
  my_monthly_salary: string | null; attendance_ok?: boolean; attendance_msg?: string
}
interface Overdue { needs_exec_signoff?: boolean; signoff_message?: string; message?: string }
const pula = (n: string | number | null) => n == null ? '—' : `P${new Intl.NumberFormat('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Number(n) || 0)}`

export default function StaffLoanScreen() {
  const base = useStaffBase()
  const [loans, setLoans] = useState<Loan[] | null>(null)
  const [meta, setMeta] = useState<Meta | null>(null)
  const [loadErr, setLoadErr] = useState<string | null>(null)
  const [amount, setAmount] = useState('')
  const [term, setTerm] = useState('')
  const [reason, setReason] = useState('')
  const [noOtherLoans, setNoOtherLoans] = useState(false)
  const [busy, setBusy] = useState(false)
  const [serverErr, setServerErr] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  const show = (m: string) => { setToast(m); setTimeout(() => setToast(null), 4000) }

  const load = useCallback(() => {
    setLoadErr(null)
    Promise.all([
      sfetch<Loan[] | { results: Loan[] }>('/staff-loans/'),
      sfetch<Meta>('/staff-loans/meta/').catch(() => null),
    ]).then(([ls, m]) => {
      const list = Array.isArray(ls) ? ls : (ls.results || [])
      setLoans(list.filter(l => l.is_mine)); setMeta(m)
    }).catch(e => { if (!reauthOn401(e)) { setLoans(l => l ?? []); setLoadErr(errText(e, 'Could not load your loans.')) } })
  }, [])
  useEffect(() => { load() }, [load])

  const words = reason.trim() ? reason.trim().split(/\s+/).length : 0

  async function apply() {
    setServerErr(null); setNotice(null)
    if (!amount || !term) { show('Enter the amount and the months.'); return }
    if (!noOtherLoans) { show('Tick the declaration about other loans.'); return }
    setBusy(true)
    try {
      // Same body the desktop sends for a staff loan (vehicle loans stay on desktop).
      const r = await rawStaffFetch('/staff-loans/', { method: 'POST', body: JSON.stringify({
        loan_type: 'staff', amount_requested: amount, term_months_requested: Number(term), reason: reason.trim(), no_other_loans: noOtherLoans,
      }) })
      if (!r.ok) { setServerErr(formatServerErrors(r.body, r.status)); return }
      const created = r.body as unknown as Loan
      const s = await rawStaffFetch(`/staff-loans/${created.id}/submit/`, { method: 'POST', body: '{}' })
      if (!s.ok) { setServerErr(`Saved as a draft but not sent: ${formatServerErrors(s.body, s.status)}`); load(); return }
      const sent = s.body as unknown as Overdue
      show('Sent to the CFO for approval. ✅')
      if (sent.needs_exec_signoff) setNotice(sent.signoff_message || sent.message || 'This loan also needs an executive sign-off because of overdue work on your board.')
      setAmount(''); setTerm(''); setReason(''); setNoOtherLoans(false)
      load()
    } catch (e) { if (!reauthOn401(e)) setServerErr(errText(e, 'Could not submit the application.')) }
    finally { setBusy(false) }
  }

  async function act(l: Loan, what: 'submit' | 'cancel') {
    if (what === 'cancel' && !window.confirm('Cancel this application?')) return
    setBusy(true); setServerErr(null)
    try {
      const r = await rawStaffFetch(`/staff-loans/${l.id}/${what}/`, { method: 'POST', body: '{}' })
      if (!r.ok) { setServerErr(formatServerErrors(r.body, r.status)); return }
      show(what === 'cancel' ? 'Application cancelled.' : 'Sent to the CFO. ✅'); load()
    } catch (e) { if (!reauthOn401(e)) setServerErr(errText(e, 'Could not do that.')) }
    finally { setBusy(false) }
  }

  const cap = meta?.my_monthly_salary ? Number(meta.my_monthly_salary) : null

  return (
    <ScreenFrame title="Staff loan" base={base}>
      {loadErr && <RetryBanner message={loadErr} onRetry={load} />}
      <Card>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{ width: 40, height: 40, borderRadius: 12, background: '#FFF7ED', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><Wallet size={20} style={{ color: C.orange }} /></div>
          <p style={{ color: C.inkSoft, fontSize: 13, margin: 0, lineHeight: 1.5 }}>A staff loan is a <b style={{ color: C.ink }}>favour, not an entitlement</b>. The CFO approves, HR pays it out, and it comes off your salary monthly.</p>
        </div>
        {meta && !meta.has_employee_record && <div style={{ marginTop: 10 }}><ServerMessage text="Your account isn't linked to a payroll record yet, so you can't apply. Ask HR to link you first." /></div>}
        {meta?.attendance_ok === false && meta.attendance_msg && <div style={{ marginTop: 10 }}><ServerMessage text={meta.attendance_msg} tone="error" /></div>}

        <label htmlFor="loan-amount" style={labelStyle}>Amount (BWP)</label>
        <input id="loan-amount" value={amount} onChange={e => setAmount(e.target.value.replace(/[^0-9.]/g, ''))} inputMode="decimal" placeholder="e.g. 5000" style={inputStyle} />
        {meta && (cap != null
          ? <p style={{ margin: '6px 0 0', fontSize: 12, color: C.inkSoft }}>Up to one month salary — {pula(cap)} (the server enforces this).</p>
          : <p style={{ margin: '6px 0 0', fontSize: 12, color: '#B45309' }}>No salary on file — HR must load your contract first.</p>)}
        <label htmlFor="loan-term" style={labelStyle}>Repay over (months)</label>
        <input id="loan-term" value={term} onChange={e => setTerm(e.target.value.replace(/[^0-9]/g, ''))} inputMode="numeric" placeholder={meta ? `1 – ${meta.staff_max_term_months}` : 'e.g. 3'} style={inputStyle} />
        {meta && <p style={{ margin: '6px 0 0', fontSize: 12, color: C.inkSoft }}>Within {meta.staff_max_term_months} months · interest {meta.default_rate_pct}% a year, worked out by Omni.</p>}
        <label htmlFor="loan-reason" style={labelStyle}>Why you need this loan (at least 50 words)</label>
        <textarea id="loan-reason" value={reason} onChange={e => setReason(e.target.value)} rows={5}
          placeholder="Explain in your own words: what the loan is for, why you need it now, and how it helps."
          style={{ ...inputStyle, resize: 'vertical' }} />
        <p style={{ fontSize: 13, fontWeight: 700, color: words >= 50 ? '#047857' : C.inkSoft, margin: '6px 0 0' }}>{words}/50 words</p>
        <label style={{ display: 'flex', alignItems: 'flex-start', gap: 10, marginTop: 12, fontSize: 13, color: C.ink, minHeight: 44, lineHeight: 1.5 }}>
          <input type="checkbox" checked={noOtherLoans} onChange={e => setNoOtherLoans(e.target.checked)} style={{ width: 24, height: 24, accentColor: C.orange, flexShrink: 0, margin: 0 }} />
          <span>I confirm I have <b>no other loans</b> with any bank or financial institution.</span>
        </label>
        {serverErr && <div style={{ marginTop: 12 }}><ServerMessage text={serverErr} tone="error" /></div>}
        {notice && <div style={{ marginTop: 12 }}><ServerMessage text={notice} /></div>}
        <button onClick={apply} disabled={busy || meta?.has_employee_record === false} style={{ ...primaryBtn(busy || meta?.has_employee_record === false), marginTop: 14 }}>
          <Send size={16} /> {busy ? 'Sending…' : 'Submit application'}
        </button>
      </Card>

      <b style={{ color: C.ink, fontSize: 15 }}>My applications</b>
      {loans === null && !loadErr && <p style={{ color: C.inkSoft, fontSize: 13, margin: 0 }}>Loading…</p>}
      {loans && loans.length === 0 && !loadErr && <p style={{ color: C.inkSoft, fontSize: 13, margin: 0 }}>No applications yet.</p>}
      {loans?.map(l => (
        <Card key={l.id} style={{ padding: 14 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
            <b style={{ color: C.ink, fontSize: 14.5 }}>{l.loan_type_display} — {pula(l.approved_amount ?? l.amount_requested)} · {l.approved_term_months ?? l.term_months_requested} mo</b>
            <StatusPill status={l.status} label={l.status_display} />
          </div>
          <p style={{ margin: '4px 0 0', fontSize: 12.5, color: C.inkSoft }}>
            Repay {pula(l.total_repayable)} total · {pula(l.monthly_instalment)} a month at {l.annual_rate_pct}%
            {Number(l.outstanding) > 0 ? ` · outstanding ${pula(l.outstanding)}` : ''}
          </p>
          {l.status === 'declined' && l.decline_reason && <p style={{ color: '#B91C1C', fontSize: 12.5, margin: '6px 0 0' }}>↩ {l.decline_reason}</p>}
          {l.can_sign && <p style={{ color: '#2563EB', fontSize: 12.5, margin: '6px 0 0' }}>Approved — sign the undertaking in Omni on your computer (HRIS → Staff Loans).</p>}
          {(l.can_submit || l.can_cancel) && (
            <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
              {l.can_submit && <button onClick={() => act(l, 'submit')} disabled={busy} style={{ ...ghostBtn, color: '#B45309' }}>Send to CFO</button>}
              {l.can_cancel && <button onClick={() => act(l, 'cancel')} disabled={busy} style={{ ...ghostBtn, color: C.inkSoft }}>Cancel</button>}
            </div>
          )}
        </Card>
      ))}
      <Toast text={toast} />
    </ScreenFrame>
  )
}
