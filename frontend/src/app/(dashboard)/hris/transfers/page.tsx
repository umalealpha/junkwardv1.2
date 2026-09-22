'use client'

/**
 * /hris/transfers — inter-entity employee transfer (feature c0d110b6).
 *
 * Move an employee from one entity to another with an effective date,
 * gated by a two-sided approval: a "Transfer Out" sign-off from the source
 * entity, then a "Transfer In" sign-off from the destination. Only when both
 * land does the employee's entity change. Mirrors the HRIS amendments page;
 * all calls go through authedHrisFetch (Bearer).
 */
import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import {
  ChevronLeft, ArrowLeftRight, Check, X, Clock, Loader2, Send, RefreshCw,
} from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { useHrisAccess } from '@/hooks/useHrisAccess'
import { getCompanies } from '@/lib/api'
import { authedHrisFetch, fetchHrisEmployees, type HrisEmployee } from '../_shared'
import { localYmd } from '@/lib/utils'

interface Transfer {
  id: string
  employee_name: string
  source_company: string
  dest_company: string
  effective_date: string | null
  reason: string
  mode: 'carry' | 'rehire'
  mode_label: string
  leave_treatment: 'payout' | 'carry'
  new_email: string
  new_employee_number: string | null
  settlement: { id: string; days: string; net_amount: string; status_label: string } | null
  status: string
  status_label: string
  out_approver_email: string
  in_approver_email: string
}

interface SettlementQuote {
  days: string; daily_rate: string; amount: string; tax_amount: string; net_amount: string
  basic_salary: string; basic_source: string; can_value: boolean; has_profile: boolean
}

const fmtP = (v: string | number) =>
  `P ${Number(v || 0).toLocaleString('en', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

const today = () => localYmd(new Date())

export default function HrisTransfersPage() {
  const router = useRouter()
  const { theme } = useTheme()
  const allowed = useHrisAccess()

  const [employees, setEmployees] = useState<HrisEmployee[]>([])
  const [companies, setCompanies] = useState<{ id: string; code: string; name: string }[]>([])
  const [eid, setEid] = useState('')
  const [dest, setDest] = useState('')
  const [eff, setEff] = useState(today())
  const [reason, setReason] = useState('')
  // CFO 2026-09-05: two kinds of move. "Carry over" keeps the same record;
  // "Redundancy & re-hire" terminates at the old entity, settles (or carries)
  // the leave, and opens a fresh record at the new one.
  const [mode, setMode] = useState<'carry' | 'rehire'>('carry')
  const [leaveTreatment, setLeaveTreatment] = useState<'payout' | 'carry'>('payout')
  const [newEmail, setNewEmail] = useState('')
  const [quote, setQuote] = useState<SettlementQuote | null>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [rows, setRows] = useState<Transfer[]>([])
  const [loading, setLoading] = useState(true)
  const [acting, setActing] = useState<string | null>(null)

  useEffect(() => { if (allowed === false) router.replace('/dashboard') }, [allowed, router])

  useEffect(() => {
    if (allowed !== true) return
    fetchHrisEmployees().then(r => setEmployees((r.employees || []).filter(e => e.eid)))
    getCompanies({ is_active: 'true' }).then(r =>
      setCompanies((r.results || []).map(c => ({ id: String(c.id), code: c.code, name: c.name }))))
    refresh()
  }, [allowed])

  // Live preview of the leaver's final leave pay (read-only, nothing saved).
  useEffect(() => {
    if (mode !== 'rehire' || !eid || !eff) { setQuote(null); return }
    const d = new Date(eff + 'T00:00:00Z'); d.setUTCDate(d.getUTCDate() - 1)
    const lastDay = localYmd(d)
    authedHrisFetch(`/hris/api/transfers/settlement-preview/?employee_id=${eid}&last_day=${lastDay}`)
      .then(async r => { setQuote(r.ok ? await r.json() : null) })
      .catch(() => setQuote(null))
  }, [mode, eid, eff])

  function refresh() {
    setLoading(true)
    authedHrisFetch('/hris/api/transfers/')
      .then(async r => { if (r.ok) { const d = await r.json(); setRows(Array.isArray(d.transfers) ? d.transfers : []) } })
      .finally(() => setLoading(false))
  }

  async function submit() {
    if (!eid || !dest) { setMsg({ ok: false, text: 'Pick an employee and a destination entity.' }); return }
    setBusy(true); setMsg(null)
    try {
      const r = await authedHrisFetch('/hris/api/transfers/', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          employee_id: eid, dest_company_id: dest, effective_date: eff, reason: reason.trim(),
          mode, leave_treatment: leaveTreatment, new_email: newEmail.trim(),
        }),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) { setMsg({ ok: false, text: d.detail || `HTTP ${r.status}` }); return }
      setMsg({ ok: true, text: mode === 'rehire'
        ? 'Submitted — awaiting Transfer Out approval. On approval the old record is closed, the leave is settled and the new record opens.'
        : 'Transfer submitted — awaiting Transfer Out approval from the source entity.' })
      setReason(''); setEid(''); setDest(''); setNewEmail(''); setMode('carry'); refresh()
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : 'Network error' })
    } finally { setBusy(false) }
  }

  async function act(id: string, path: string) {
    setActing(id); setMsg(null)
    try {
      const r = await authedHrisFetch(`/hris/api/transfers/${id}/${path}/`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) { setMsg({ ok: false, text: d.detail || `HTTP ${r.status}` }); return }
      refresh()
    } finally { setActing(null) }
  }

  const inProgress = useMemo(() => rows.filter(t => t.status === 'pending_out' || t.status === 'pending_in' || t.status === 'scheduled'), [rows])
  const settled = useMemo(() => rows.filter(t => t.status === 'completed' || t.status === 'rejected').slice(0, 20), [rows])

  if (allowed !== true) {
    return <div className="min-h-screen flex items-center justify-center" style={{ backgroundColor: theme.bg }}>
      <div className="w-8 h-8 border-2 border-[#F07F00] border-t-transparent rounded-full animate-spin" />
    </div>
  }

  const card = { background: theme.card, border: `1px solid ${theme.cardBdr}` }
  const inp = { background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }

  function StatusPill({ t }: { t: Transfer }) {
    const map: Record<string, string> = {
      pending_out: theme.orange, pending_in: theme.inf, scheduled: theme.inf, completed: theme.ok, rejected: theme.er,
    }
    return <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold"
      style={{ background: (map[t.status] || theme.t2) + '22', color: map[t.status] || theme.t2 }}>{t.status_label}</span>
  }

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar title="Employee Transfers" breadcrumbs={[{ label: 'HRIS', href: '/hris' }, { label: 'Transfers' }]} />
      <main className="flex-1 overflow-y-auto p-6 space-y-5">
        <Link href="/hris/directory" className="inline-flex items-center gap-1.5 text-sm font-medium" style={{ color: theme.t2 }}>
          <ChevronLeft className="w-4 h-4" /> Back to Directory
        </Link>

        {/* Submit */}
        <div className="rounded-2xl p-5 space-y-3" style={card}>
          <h3 className="font-semibold flex items-center gap-2" style={{ color: theme.text }}>
            <ArrowLeftRight className="w-4 h-4" style={{ color: theme.orange }} /> Propose a transfer
          </h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-xs mb-1" style={{ color: theme.t2 }}>Employee</label>
              <select value={eid} onChange={e => setEid(e.target.value)} className="w-full rounded-lg px-3 py-2 text-sm outline-none" style={inp}>
                <option value="">— select employee —</option>
                {employees.map(e => <option key={e.eid} value={e.eid}>{e.nm} · {e.company} · {e.ps}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs mb-1" style={{ color: theme.t2 }}>Destination entity</label>
              <select value={dest} onChange={e => setDest(e.target.value)} className="w-full rounded-lg px-3 py-2 text-sm outline-none" style={inp}>
                <option value="">— select entity —</option>
                {companies.map(c => <option key={c.id} value={c.id}>{c.code} — {c.name}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs mb-1" style={{ color: theme.t2 }}>Effective date</label>
              <input type="date" value={eff} onChange={e => setEff(e.target.value)} className="w-full rounded-lg px-3 py-2 text-sm outline-none" style={inp} />
            </div>
            <div>
              <label className="block text-xs mb-1" style={{ color: theme.t2 }}>Reason</label>
              <input value={reason} onChange={e => setReason(e.target.value)} placeholder="e.g. departmental reorganisation"
                className="w-full rounded-lg px-3 py-2 text-sm outline-none" style={inp} />
            </div>
            <div className="sm:col-span-2">
              <label className="block text-xs mb-1" style={{ color: theme.t2 }}>How are they moving?</label>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                {([
                  ['carry', 'Carry over', 'Same record, only the entity changes. Leave, login and Time Doctor travel with them.'],
                  ['rehire', 'Redundancy & re-hire', 'Closed at the old entity the day before, leave settled, a fresh record opens at the new one.'],
                ] as const).map(([v, label, help]) => (
                  <button key={v} type="button" onClick={() => setMode(v)}
                    className="text-left rounded-xl px-3 py-2.5"
                    style={{ background: mode === v ? theme.orange + '18' : theme.g100,
                             border: `1px solid ${mode === v ? theme.orange : theme.cardBdr}` }}>
                    <div className="text-sm font-semibold" style={{ color: theme.text }}>{label}</div>
                    <div className="text-[11px] mt-0.5" style={{ color: theme.t2 }}>{help}</div>
                  </button>
                ))}
              </div>
            </div>
            {mode === 'rehire' && (
              <>
                <div>
                  <label className="block text-xs mb-1" style={{ color: theme.t2 }}>Annual leave at the old entity</label>
                  <select value={leaveTreatment} onChange={e => setLeaveTreatment(e.target.value as 'payout' | 'carry')}
                    className="w-full rounded-lg px-3 py-2 text-sm outline-none" style={inp}>
                    <option value="payout">Pay it out (final leave pay, approved by CFO → HR → Finance)</option>
                    <option value="carry">Carry the balance to the new record</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs mb-1" style={{ color: theme.t2 }}>New email address (if it changes)</label>
                  <input type="email" value={newEmail} onChange={e => setNewEmail(e.target.value)} placeholder="name@insurance.co.bw"
                    className="w-full rounded-lg px-3 py-2 text-sm outline-none" style={inp} />
                </div>
                {leaveTreatment === 'payout' && quote && (
                  <div className="sm:col-span-2 rounded-xl px-4 py-3 text-sm" style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}` }}>
                    <div className="font-semibold mb-1" style={{ color: theme.text }}>Final leave pay preview (nothing saved yet)</div>
                    {!quote.has_profile && <div style={{ color: theme.er }}>No HR profile — there is no leave balance to settle.</div>}
                    {quote.has_profile && !quote.can_value && <div style={{ color: theme.er }}>No basic salary on their payslips yet — cannot be valued until payroll is loaded.</div>}
                    {quote.has_profile && quote.can_value && (
                      <div style={{ color: theme.t2 }}>
                        {quote.days} day(s) to the last working day × {fmtP(quote.daily_rate)} (basic {fmtP(quote.basic_salary)} ÷ 24{quote.basic_source ? `, ${quote.basic_source}` : ''})
                        = <b style={{ color: theme.text }}>{fmtP(quote.amount)}</b> gross · PAYE {fmtP(quote.tax_amount)} ·
                        <b style={{ color: theme.text }}> net {fmtP(quote.net_amount)}</b>
                      </div>
                    )}
                  </div>
                )}
              </>
            )}
          </div>
          <button onClick={submit} disabled={busy}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold disabled:opacity-50"
            style={{ background: theme.orange, color: '#fff' }}>
            {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />} Submit transfer
          </button>
          {msg && (
            <div className="rounded-lg px-3 py-2 text-sm" style={{
              background: msg.ok ? theme.okB : theme.erB, color: msg.ok ? theme.ok : theme.er,
              border: `1px solid ${(msg.ok ? theme.ok : theme.er)}40`,
            }}>{msg.text}</div>
          )}
        </div>

        {/* In-progress queue */}
        <div className="rounded-2xl p-5" style={card}>
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold flex items-center gap-2" style={{ color: theme.text }}>
              <Clock className="w-4 h-4" style={{ color: theme.orange }} /> In progress
            </h3>
            {/* Bug 7b40079e: Refresh the transfer list without a full page reload. */}
            <button onClick={refresh} disabled={loading}
              title="Refresh the transfer list"
              className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-semibold disabled:opacity-50"
              style={{ background: theme.g100, color: theme.t2, border: `1px solid ${theme.cardBdr}` }}>
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} /> Refresh
            </button>
          </div>
          {loading && <div className="text-sm py-4 text-center" style={{ color: theme.t2 }}><Loader2 className="w-4 h-4 inline animate-spin mr-1" /> Loading…</div>}
          {!loading && inProgress.length === 0 && <p className="text-sm" style={{ color: theme.t2 }}>No transfers in progress.</p>}
          <div className="space-y-2">
            {inProgress.map(t => (
              <div key={t.id} className="rounded-lg px-3 py-3 flex items-start gap-3" style={{ background: theme.g100 }}>
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-semibold" style={{ color: theme.text }}>
                    {t.employee_name} <span style={{ color: theme.t2 }}>· {t.source_company} → {t.dest_company}</span> <StatusPill t={t} />
                  </div>
                  <div className="text-xs mt-0.5" style={{ color: theme.t2 }}>
                    {t.mode === 'rehire' && <span className="mr-1 px-1.5 py-0.5 rounded text-[10px] font-semibold" style={{ background: theme.er + '18', color: theme.er }}>Redundancy & re-hire · leave {t.leave_treatment === 'payout' ? 'paid out' : 'carried'}</span>}
                    effective {t.effective_date}{t.reason ? ` · ${t.reason}` : ''}
                    {t.out_approver_email ? ` · out ✓ ${t.out_approver_email}` : ''}
                  </div>
                </div>
                <div className="flex items-center gap-2 flex-shrink-0">
                  {t.status === 'pending_out' && (
                    <button onClick={() => act(t.id, 'approve-out')} disabled={acting === t.id}
                      className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-semibold disabled:opacity-50"
                      style={{ background: theme.ok, color: '#fff' }}>
                      {acting === t.id ? <Loader2 className="w-3 h-3 animate-spin" /> : <Check className="w-3 h-3" />} Approve Out
                    </button>
                  )}
                  {t.status === 'pending_in' && (
                    <button onClick={() => act(t.id, 'approve-in')} disabled={acting === t.id}
                      className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-semibold disabled:opacity-50"
                      style={{ background: theme.ok, color: '#fff' }}>
                      {acting === t.id ? <Loader2 className="w-3 h-3 animate-spin" /> : <Check className="w-3 h-3" />} Approve In
                    </button>
                  )}
                  <button onClick={() => act(t.id, 'reject')} disabled={acting === t.id}
                    className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-semibold disabled:opacity-50"
                    style={{ background: theme.er, color: '#fff' }}>
                    <X className="w-3 h-3" /> Reject
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Recent settled */}
        {settled.length > 0 && (
          <div className="rounded-2xl p-5" style={card}>
            <h3 className="font-semibold mb-3 text-sm" style={{ color: theme.text }}>Recent</h3>
            <div className="space-y-1.5">
              {settled.map(t => (
                <div key={t.id} className="text-xs flex items-center gap-2 flex-wrap" style={{ color: theme.t2 }}>
                  <StatusPill t={t} /> {t.employee_name} · {t.source_company} → {t.dest_company} · {t.effective_date}
                  {t.mode === 'rehire' && <span>· re-hired{t.new_employee_number ? ` as ${t.new_employee_number}` : ''}</span>}
                  {t.settlement && <span>· final leave pay {t.settlement.days}d, net {fmtP(t.settlement.net_amount)} ({t.settlement.status_label})</span>}
                </div>
              ))}
            </div>
          </div>
        )}
      </main>
    </div>
  )
}
