'use client'

/**
 * /hris/leave-encashment — Leave Encashment (CFO directive 2026-07-21).
 *
 * SELF-SERVICE: any employee applies to convert annual-leave days to cash.
 * Valuation is server-computed (BASIC ÷ 24 × days) — never typed. The
 * application runs a 4-step chain, each landing on the right dashboard:
 *   apply → CFO → HR → Finance (FC or FM) → paid.
 *
 * Approvers (CFO / HR / Finance) additionally see the whole queue, the
 * stage-appropriate Approve/Reject buttons, and the leave-pay provision
 * register. A plain employee sees only their own quote + their own history.
 */
import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { Banknote, ChevronLeft, Check, Clock, Search, Wallet } from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { authedHrisFetch, fetchHrisEmployees, type HrisEmployee } from '../_shared'

const NAVY = '#1D3270'
const ORANGE = '#F47C20'

interface Sig { signed: boolean; by: string; at: string | null }
interface Encashment {
  id: string
  employee_name: string
  kind?: 'encashment' | 'settlement'
  kind_label?: string
  last_day?: string | null
  department: string
  days: string
  basic_salary: string
  basic_source: string
  daily_rate: string
  amount: string
  tax_base: string
  tax_amount: string
  net_amount: string
  balance_at_request: string
  reason: string
  status: string
  status_label: string
  applicant_email: string
  is_own: boolean
  can_act: boolean
  can_pay: boolean
  created_at: string | null
  signatures: { cfo: Sig; hr: Sig; finance: Sig }
  rejected: { at: string | null; stage: string; notes: string } | null
  paid: { is_paid: boolean; at: string | null }
}
interface Quote {
  has_record: boolean
  employee_name?: string
  basic_salary?: string
  basic_source?: string
  daily_rate?: string
  available_days?: string
  min_residual_days?: string
  max_encashable_days?: string
  can_apply?: boolean
  tax_base?: string
  /** days-as-string → the exact server-computed split for that day-count. */
  tax_by_days?: Record<string, TaxSplit>
}
interface TaxSplit { payout: string; tax: string; net: string }
interface Me {
  can_approve_cfo: boolean
  can_approve_hr: boolean
  can_approve_finance: boolean
  can_view_all: boolean
  can_pay: boolean
}
interface ProvisionRow {
  employee_id: string; employee_name: string; department: string
  basic_salary: string; basic_source: string; daily_rate: string
  balance_days: string; provision: string
}
interface Provision {
  as_at: string
  rows: ProvisionRow[]
  totals: { employees: number; unvalued: number; balance_days: string; provision: string }
}

const fmtP = (v: string | number) =>
  `P${new Intl.NumberFormat('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Number(v) || 0)}`

// Chain step chips shown on every request card.
const STEPS: { key: 'cfo' | 'hr' | 'finance'; label: string }[] = [
  { key: 'cfo', label: 'CFO' },
  { key: 'hr', label: 'HR' },
  { key: 'finance', label: 'FC / FM' },
]

const STATUS_CHIP: Record<string, string> = {
  pending_cfo: 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300',
  pending_hr: 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300',
  pending_finance: 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300',
  approved: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300',
  paid: 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300',
  rejected: 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300',
}

export default function LeaveEncashmentPage() {
  const [me, setMe] = useState<Me | null>(null)
  const [quote, setQuote] = useState<Quote | null>(null)
  const [requests, setRequests] = useState<Encashment[]>([])
  const [provision, setProvision] = useState<Provision | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')
  const [filter, setFilter] = useState('')

  // apply form
  const [days, setDays] = useState('')
  const [reason, setReason] = useState('')
  const [formErr, setFormErr] = useState('')
  const [showRegister, setShowRegister] = useState(false)

  // Offboarding — HR raises a leaver's final settlement (66b1e7a3).
  const [showLeaver, setShowLeaver] = useState(false)
  const [leaverEmployees, setLeaverEmployees] = useState<HrisEmployee[]>([])
  const [leaverEid, setLeaverEid] = useState('')
  const [leaverLastDay, setLeaverLastDay] = useState('')
  const [leaverReason, setLeaverReason] = useState('')
  const [leaverBusy, setLeaverBusy] = useState(false)
  const [leaverMsg, setLeaverMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const load = useCallback(() => {
    authedHrisFetch('/hris/api/leave-encashment/')
      .then(async r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then(d => {
        setMe(d.me); setQuote(d.quote); setRequests(d.requests || []); setError('')
        if (d.me?.can_view_all) {
          authedHrisFetch('/hris/api/leave-encashment/provision/')
            .then(r => (r.ok ? r.json() : null)).then(p => p && setProvision(p))
          fetchHrisEmployees()
            .then(r => setLeaverEmployees((r.employees || []).filter(e => e.eid)))
            .catch(() => {})
        }
      })
      .catch(e => setError(String(e?.message || e)))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  const dailyRate = Number(quote?.daily_rate || 0)
  const available = Number(quote?.available_days || 0)
  // Must keep a minimum residual balance after encashing (CFO 2026-07-23,
  // default 10 days). The most that can be cashed out is available - residual.
  const minResidual = Number(quote?.min_residual_days ?? 10)
  const maxEncashable = quote?.max_encashable_days != null
    ? Number(quote.max_encashable_days)
    : Math.max(0, available - minResidual)
  const previewAmount = dailyRate * (Number(days) || 0)
  const daysValid = Number(days) > 0 && (Number(days) * 2) % 1 === 0 && Number(days) <= maxEncashable
  // PAYE is progressive and can straddle a band boundary, so the exact tax for
  // every legal day-count is pre-computed SERVER-side and looked up here. The
  // browser never calculates tax — one calculator, no drift (and the figure
  // stored on the application is computed again server-side on submit).
  const preview = daysValid ? quote?.tax_by_days?.[String(Number(days))] : undefined
  const taxRateLabel = preview && Number(preview.payout) > 0
    ? `${((Number(preview.tax) / Number(preview.payout)) * 100).toFixed(1)}% of the payout`
    : undefined
  // Show the SERVER's gross next to the server's tax and net. The JS
  // `previewAmount` is float arithmetic over an already-rounded daily rate, so it
  // can land a cent away from the Decimal figure that gets snapshotted — which
  // would make gross − tax ≠ net on screen even though the backend reconciles.
  const grossShown = preview ? preview.payout : previewAmount
  // An employee must never be able to apply without having seen their net. The
  // preview schedule is capped (see _tax_schedule), so a day count outside it
  // has no net to show — block rather than let them submit blind.
  //
  // Only block when the server DID send a schedule and this day-count is not in
  // it. If there is no schedule at all (an older backend during a rolling
  // deploy) fall back to the previous behaviour rather than locking everyone out
  // of the form.
  const hasSchedule = !!quote?.tax_by_days && Object.keys(quote.tax_by_days).length > 0
  const previewMissing = daysValid && hasSchedule && !preview
  const reasonWords = reason.trim() ? reason.trim().split(/\s+/).length : 0
  const reasonOk = reasonWords >= 50

  const apply = async () => {
    setFormErr(''); setBusy('apply')
    try {
      const r = await authedHrisFetch('/hris/api/leave-encashment/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ days, reason }),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) { setFormErr(d.detail || `HTTP ${r.status}`); return }
      setDays(''); setReason(''); load()
    } finally { setBusy('') }
  }

  const action = async (id: string, path: string, body: object = {}) => {
    setBusy(id + path)
    try {
      const r = await authedHrisFetch(`/hris/api/leave-encashment/${id}/${path}/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        alert(d.detail || `Action failed (HTTP ${r.status})`)
      }
      load()
    } finally { setBusy('') }
  }

  const rejectWithReason = (id: string) => {
    const notesIn = window.prompt('Reason for rejection (optional):') ?? null
    if (notesIn === null) return
    action(id, 'reject', { notes: notesIn })
  }

  const raiseLeaverSettlement = async () => {
    if (!leaverEid || !leaverLastDay) {
      setLeaverMsg({ ok: false, text: 'Pick the employee and their last working day.' }); return
    }
    setLeaverBusy(true); setLeaverMsg(null)
    try {
      const r = await authedHrisFetch('/hris/api/leave-encashment/leaver-settlement/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ employee_id: leaverEid, last_day: leaverLastDay, reason: leaverReason.trim() }),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) { setLeaverMsg({ ok: false, text: d.detail || `HTTP ${r.status}` }); return }
      const nm = leaverEmployees.find(e => e.eid === leaverEid)?.nm || 'the employee'
      setLeaverMsg({ ok: true, text: `Final settlement raised for ${nm} — sent to CFO, HR and Finance to approve.` })
      setLeaverEid(''); setLeaverLastDay(''); setLeaverReason(''); load()
    } catch (err) {
      setLeaverMsg({ ok: false, text: err instanceof Error ? err.message : 'Network error' })
    } finally { setLeaverBusy(false) }
  }

  const q = filter.trim().toLowerCase()
  const provRows = (provision?.rows || []).filter(
    r => !q || r.employee_name.toLowerCase().includes(q) || r.department.toLowerCase().includes(q))

  // Split the queue: things I can act on first, then everything else.
  const actionable = requests.filter(r => r.can_act || r.can_pay)
  const mine = requests.filter(r => r.is_own)
  const others = requests.filter(r => !r.can_act && !r.can_pay && !r.is_own)

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-950">
      <TopBar />
      <main className="mx-auto max-w-5xl px-4 py-6">
        <div className="mb-1 flex items-center gap-3">
          <Link href="/hris" className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-gray-800 dark:hover:text-gray-200">
            <ChevronLeft className="h-4 w-4" /> HRIS
          </Link>
          <h1 className="flex items-center gap-2 text-xl font-semibold" style={{ color: NAVY }}>
            <Banknote className="h-5 w-5" style={{ color: ORANGE }} /> Leave Encashment
          </h1>
        </div>
        <p className="mb-6 text-sm text-gray-500 dark:text-gray-400">
          Cash out your unused annual leave. Your payout is worked out automatically —
          basic salary ÷ 24 × the days you cash in. Every application goes CFO → HR →
          Finance before it&apos;s paid.
        </p>

        {loading && (
          <div className="rounded-xl border border-gray-200 bg-white p-6 text-sm text-gray-500 dark:border-gray-800 dark:bg-gray-900">Loading…</div>
        )}
        {!loading && error && (
          <div className="rounded-xl border border-red-200 bg-red-50 p-6 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
            Could not load leave encashment ({error}).
          </div>
        )}

        {/* ── Apply card (self-service) ──────────────────────────────────── */}
        {!loading && quote && (
          <div className="mb-8 overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm dark:border-gray-800 dark:bg-gray-900">
            <div className="px-5 py-3 text-white" style={{ background: NAVY }}>
              <h2 className="flex items-center gap-2 text-base font-semibold">
                <Wallet className="h-4 w-4" /> Apply to cash out leave
              </h2>
            </div>
            {!quote.has_record ? (
              <div className="p-5 text-sm text-gray-500">
                Your account isn&apos;t linked to an employee record yet, so we can&apos;t value an
                encashment. Ask HR to link you.
              </div>
            ) : (
              <div className="p-5">
                {/* HR-rule banner (CFO 2026-08-03): the minimum-balance rule is an
                    HR policy — say so plainly so it doesn't read as a system fault. */}
                <div className="mb-4 rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-900 dark:border-blue-900 dark:bg-blue-950/40 dark:text-blue-100">
                  <b>Please note — HR rule:</b> you must keep at least <b>{minResidual} days</b> of
                  annual leave in your balance. You can only cash out days above that minimum.
                  This limit is set by the HR team.
                </div>
                {/* Three across, two rows: the facts (basic / rate / balance) then
                    the money (gross / tax / net). NOT six across — at a 1120px
                    laptop the sidebar leaves ~780px, so six tiles give each one
                    ~96px of usable width and a figure like "P43,750.05" overflows
                    at text-lg. Three gives ~260px each. */}
                <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-3">
                  <Stat label="Your basic" value={Number(quote.basic_salary) > 0 ? fmtP(quote.basic_salary!) : '—'} sub={quote.basic_source ? `from ${quote.basic_source}` : 'no payslip yet'} />
                  <Stat label="Daily rate ÷24" value={dailyRate > 0 ? fmtP(dailyRate) : '—'} />
                  <Stat label="Leave available" value={`${available} days`} />
                  <Stat label="Gross payout" value={daysValid ? fmtP(grossShown) : '—'} />
                  <Stat label="Less PAYE tax" value={daysValid && preview ? `− ${fmtP(preview.tax)}` : '—'} sub={taxRateLabel} />
                  <Stat label="Net payable" value={daysValid && preview ? fmtP(preview.net) : '—'} highlight />
                </div>
                {/* Plain-English note: staff kept asking why the payout shrank. */}
                {daysValid && preview && (
                  <div className="mb-4 rounded-lg border border-gray-200 bg-gray-50 px-4 py-3 text-sm text-gray-700 dark:border-gray-800 dark:bg-gray-900/60 dark:text-gray-300">
                    A leave cash-out is taxed like normal salary in the month it is paid, so
                    PAYE is deducted before you are paid. <b>{fmtP(preview.payout)}</b> gross
                    less <b>{fmtP(preview.tax)}</b> PAYE = <b>{fmtP(preview.net)}</b> in your
                    account. The tax is worked out at your own tax rate, not a flat rate.
                  </div>
                )}
                {quote.can_apply ? (
                  <div className="flex flex-wrap items-end gap-3">
                    <label className="text-sm">
                      <span className="mb-1 block text-xs font-medium text-gray-500">Days to cash out</span>
                      <input
                        value={days} onChange={e => setDays(e.target.value)}
                        inputMode="decimal" placeholder="e.g. 5 or 2.5"
                        className="w-40 rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-[#F47C20] focus:outline-none dark:border-gray-700 dark:bg-gray-950 dark:text-gray-100"
                      />
                      {/* Visible reason the request is out of range (bug e847fd83):
                          previously an over-balance figure like 50 days still showed
                          a payout with no message and only a silently-disabled button. */}
                      {Number(days) > 0 && !daysValid && (
                        <span className="mt-1 block text-xs text-red-600">
                          {(Number(days) * 2) % 1 !== 0
                            ? 'Enter days in half-day steps (e.g. 5 or 2.5).'
                            : `You can cash out at most ${maxEncashable} day${maxEncashable === 1 ? '' : 's'} — you must keep at least ${minResidual} days of your ${available}-day balance.`}
                        </span>
                      )}
                      {previewMissing && (
                        <span className="mt-1 block text-xs text-red-600">
                          We can&apos;t work out the tax for that many days. Enter a
                          smaller number, or ask HR to check your leave balance.
                        </span>
                      )}
                    </label>
                    <label className="min-w-[16rem] flex-1 text-sm">
                      <span className="mb-1 block text-xs font-medium text-gray-500">Motivation (at least 50 words)</span>
                      <textarea
                        value={reason} onChange={e => setReason(e.target.value)}
                        rows={3}
                        placeholder="Explain in your own words why you need to cash out leave."
                        className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-[#F47C20] focus:outline-none dark:border-gray-700 dark:bg-gray-950 dark:text-gray-100"
                      />
                      <span className={`mt-1 block text-xs ${reasonOk ? 'text-emerald-600' : 'text-gray-400'}`}>
                        {reasonWords}/50 words {reasonOk ? '✓' : ''}
                      </span>
                    </label>
                    <button
                      type="button"
                      disabled={busy === 'apply' || !daysValid || !reasonOk || previewMissing}
                      onClick={apply}
                      className="rounded-lg px-5 py-2 text-sm font-semibold text-white disabled:opacity-50"
                      style={{ background: ORANGE }}
                    >
                      Apply
                    </button>
                  </div>
                ) : (
                  <div className="rounded-lg bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:bg-amber-950/50 dark:text-amber-200">
                    {available <= 0
                      ? 'You have no annual-leave days available to cash out right now.'
                      : maxEncashable <= 0
                      ? `You must keep at least ${minResidual} annual-leave days. Your balance of ${available} days is at or below that, so there is nothing to cash out right now.`
                      : 'No basic salary is on your payslips yet — HR must load payroll before you can apply.'}
                  </div>
                )}
                {formErr && (
                  <div className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950 dark:text-red-300">{formErr}</div>
                )}
              </div>
            )}
          </div>
        )}

        {/* ── Offboarding: raise a leaver's final settlement (privileged) ──── */}
        {me?.can_view_all && (
          <div className="mb-8 rounded-2xl border border-gray-200 bg-white shadow-sm dark:border-gray-800 dark:bg-gray-900">
            <button
              type="button" onClick={() => setShowLeaver(s => !s)}
              className="flex w-full items-center justify-between gap-2 px-5 py-3 text-left"
            >
              <div>
                <h2 className="text-base font-semibold" style={{ color: NAVY }}>Offboarding — raise a leaver&rsquo;s final settlement</h2>
                <p className="text-xs text-gray-500">
                  Pays out the full leave balance to the last working day. Goes to CFO &rarr; HR &rarr; Finance to approve before anything posts.
                </p>
              </div>
              <span className="text-sm text-gray-400">{showLeaver ? 'Hide' : 'Show'}</span>
            </button>
            {showLeaver && (
              <div className="border-t border-gray-100 px-5 py-4 dark:border-gray-800">
                <div className="grid gap-3 sm:grid-cols-2">
                  <label className="text-sm">
                    <span className="mb-1 block font-medium text-gray-700 dark:text-gray-300">Employee</span>
                    <select
                      value={leaverEid} onChange={e => setLeaverEid(e.target.value)}
                      className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-800"
                    >
                      <option value="">Select the leaver…</option>
                      {leaverEmployees.map(e => (
                        <option key={e.eid} value={e.eid}>{e.nm} · {e.company} · {e.ps}</option>
                      ))}
                    </select>
                  </label>
                  <label className="text-sm">
                    <span className="mb-1 block font-medium text-gray-700 dark:text-gray-300">Last working day</span>
                    <input
                      type="date" value={leaverLastDay} onChange={e => setLeaverLastDay(e.target.value)}
                      className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-800"
                    />
                  </label>
                </div>
                <label className="mt-3 block text-sm">
                  <span className="mb-1 block font-medium text-gray-700 dark:text-gray-300">Reason (optional)</span>
                  <input
                    value={leaverReason} onChange={e => setLeaverReason(e.target.value)}
                    placeholder="e.g. resignation, end of contract"
                    className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-800"
                  />
                </label>
                {leaverMsg && (
                  <p className={`mt-3 text-sm ${leaverMsg.ok ? 'text-emerald-600' : 'text-red-600'}`}>{leaverMsg.text}</p>
                )}
                <button
                  type="button" onClick={raiseLeaverSettlement} disabled={leaverBusy}
                  className="mt-4 inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
                  style={{ background: NAVY }}
                >
                  {leaverBusy ? 'Raising…' : 'Raise final settlement'}
                </button>
                <p className="mt-2 text-xs text-gray-500">
                  The asset handover must be clear first — if the person still holds a company asset, this is blocked until it is returned or written off.
                </p>
              </div>
            )}
          </div>
        )}

        {/* ── Provision register (privileged) ────────────────────────────── */}
        {provision && (
          <div className="mb-8 rounded-2xl border border-gray-200 bg-white shadow-sm dark:border-gray-800 dark:bg-gray-900">
            <button
              type="button" onClick={() => setShowRegister(s => !s)}
              className="flex w-full items-center justify-between gap-2 px-5 py-3 text-left"
            >
              <div>
                <h2 className="text-base font-semibold" style={{ color: NAVY }}>Leave-pay provision register</h2>
                <p className="text-xs text-gray-500">
                  {provision.totals.employees} employees · {provision.totals.balance_days} days ·
                  <span className="font-semibold"> {fmtP(provision.totals.provision)}</span> as at {provision.as_at}
                  {provision.totals.unvalued > 0 && <span className="ml-1 text-amber-600">· {provision.totals.unvalued} without a payslip basic</span>}
                </p>
              </div>
              <span className="text-sm text-gray-400">{showRegister ? 'Hide' : 'Show'}</span>
            </button>
            {showRegister && (
              <div className="border-t border-gray-100 dark:border-gray-800">
                <div className="flex items-center justify-end px-5 py-2">
                  <div className="relative">
                    <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-gray-400" />
                    <input value={filter} onChange={e => setFilter(e.target.value)} placeholder="Filter name / dept"
                      className="rounded-lg border border-gray-200 py-1.5 pl-8 pr-3 text-sm dark:border-gray-700 dark:bg-gray-950 dark:text-gray-100" />
                  </div>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-xs uppercase tracking-wide text-gray-500">
                        <th className="px-5 py-2">Employee</th><th className="px-3 py-2">Dept</th>
                        <th className="px-3 py-2 text-right">Basic</th><th className="px-3 py-2 text-right">Rate ÷24</th>
                        <th className="px-3 py-2 text-right">Days</th><th className="px-5 py-2 text-right">Provision</th>
                      </tr>
                    </thead>
                    <tbody>
                      {provRows.map(r => (
                        <tr key={r.employee_id} className="border-t border-gray-100 dark:border-gray-800">
                          <td className="px-5 py-2 font-medium text-gray-900 dark:text-gray-100">{r.employee_name}</td>
                          <td className="px-3 py-2 text-gray-500">{r.department}</td>
                          <td className="px-3 py-2 text-right">{Number(r.basic_salary) > 0 ? fmtP(r.basic_salary) : '—'}</td>
                          <td className="px-3 py-2 text-right">{Number(r.daily_rate) > 0 ? fmtP(r.daily_rate) : '—'}</td>
                          <td className="px-3 py-2 text-right">{r.balance_days}</td>
                          <td className="px-5 py-2 text-right font-semibold">{fmtP(r.provision)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        )}

        {/* ── Approval queue (actionable first) ──────────────────────────── */}
        {actionable.length > 0 && (
          <Section title="Waiting on you">
            {actionable.map(r => <Card key={r.id} r={r} me={me} busy={busy} onAction={action} onReject={rejectWithReason} />)}
          </Section>
        )}

        {/* ── My applications ────────────────────────────────────────────── */}
        <Section title={me?.can_view_all ? 'My applications' : 'My applications'}>
          {mine.length === 0 && !loading && (
            <div className="rounded-xl border border-gray-200 bg-white p-5 text-sm text-gray-500 dark:border-gray-800 dark:bg-gray-900">
              You haven&apos;t applied for any leave encashment yet.
            </div>
          )}
          {mine.map(r => <Card key={r.id} r={r} me={me} busy={busy} onAction={action} onReject={rejectWithReason} />)}
        </Section>

        {/* ── Everything else (approvers only) ───────────────────────────── */}
        {others.length > 0 && (
          <Section title="All other applications">
            {others.map(r => <Card key={r.id} r={r} me={me} busy={busy} onAction={action} onReject={rejectWithReason} />)}
          </Section>
        )}
      </main>
    </div>
  )
}

function Stat({ label, value, sub, highlight }: { label: string; value: string; sub?: string; highlight?: boolean }) {
  return (
    <div className={`rounded-xl border p-3 ${highlight ? 'border-transparent text-white' : 'border-gray-200 dark:border-gray-800'}`}
      style={highlight ? { background: ORANGE } : undefined}>
      <div className={`text-[11px] uppercase tracking-wide ${highlight ? 'text-white/80' : 'text-gray-500'}`}>{label}</div>
      <div className={`mt-0.5 text-lg font-semibold ${highlight ? 'text-white' : 'text-gray-900 dark:text-gray-100'}`}>{value}</div>
      {sub && <div className={`text-[11px] ${highlight ? 'text-white/70' : 'text-gray-400'}`}>{sub}</div>}
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mb-8">
      <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-gray-500">{title}</h3>
      <div className="space-y-4">{children}</div>
    </section>
  )
}

function StepTrack({ r }: { r: Encashment }) {
  const done = (k: 'cfo' | 'hr' | 'finance') => r.signatures[k].signed
  const rejected = r.status === 'rejected'
  return (
    <div className="mt-3 flex flex-wrap items-center gap-1.5 text-xs">
      {STEPS.map((s, i) => {
        const isDone = done(s.key)
        const isCurrent = !rejected && !isDone && r.status === `pending_${s.key}`
        return (
          <span key={s.key} className="flex items-center gap-1.5">
            <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 font-medium ${
              isDone ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300'
              : isCurrent ? 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300'
              : 'bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400'}`}>
              {isDone ? <Check className="h-3 w-3" /> : isCurrent ? <Clock className="h-3 w-3" /> : null}
              {s.label}{isDone ? ' ✓' : ''}
            </span>
            {i < STEPS.length - 1 && <span className="text-gray-300">→</span>}
          </span>
        )
      })}
      <span className="text-gray-300">→</span>
      <span className={`rounded-full px-2.5 py-1 font-medium ${
        r.paid.is_paid ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300' : 'bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400'}`}>
        {r.paid.is_paid ? 'Paid ✓' : 'Payment'}
      </span>
    </div>
  )
}

function Card({ r, me, busy, onAction, onReject }: {
  r: Encashment; me: Me | null; busy: string
  onAction: (id: string, path: string, body?: object) => void
  onReject: (id: string) => void
}) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm dark:border-gray-800 dark:bg-gray-900">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="text-base font-semibold text-gray-900 dark:text-gray-100">
            {r.employee_name}
            {r.kind === 'settlement' && (
              <span className="ml-2 rounded-full bg-rose-50 px-2 py-0.5 text-xs font-semibold text-rose-700 dark:bg-rose-900/30 dark:text-rose-300"
                title="A leaver's full balance to their last working day, raised by HR">
                Leaver final leave pay{r.last_day ? ` · last day ${r.last_day}` : ''}
              </span>
            )}
            <span className="ml-2 text-sm font-normal text-gray-500">
              {r.days} day(s) × {fmtP(r.daily_rate)} = {fmtP(r.amount)} gross
            </span>
          </div>
          {/* Approvers and Finance must see the NET — that is the figure that
              actually gets paid, and the amount to load for payment. */}
          <div className="mt-1 text-sm text-gray-700 dark:text-gray-300">
            Gross {fmtP(r.amount)} · less PAYE {fmtP(r.tax_amount)} ·
            <strong className="ml-1">net payable {fmtP(r.net_amount)}</strong>
          </div>
          <div className="mt-0.5 text-xs text-gray-500">
            Basic {fmtP(r.basic_salary)}{r.basic_source ? ` (${r.basic_source})` : ''} ·
            balance when applied {r.balance_at_request}d
            {r.created_at ? ` · applied ${r.created_at.slice(0, 10)}` : ''}
          </div>
          {r.reason && <div className="mt-1 text-xs text-gray-500">Reason: {r.reason}</div>}
          {r.rejected?.notes && <div className="mt-1 text-xs text-red-600 dark:text-red-400">Rejected ({r.rejected.stage}): {r.rejected.notes}</div>}
        </div>
        <span className={`rounded-full px-3 py-1 text-xs font-medium ${STATUS_CHIP[r.status] || STATUS_CHIP.pending_cfo}`}>
          {r.status_label}
        </span>
      </div>

      <StepTrack r={r} />

      {(r.can_act || r.can_pay) && (
        <div className="mt-4 flex flex-wrap gap-2">
          {r.can_act && (
            <>
              <button type="button" disabled={!!busy} onClick={() => onAction(r.id, 'approve')}
                className="rounded-lg bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50">
                Approve
              </button>
              <button type="button" disabled={!!busy} onClick={() => onReject(r.id)}
                className="rounded-lg border border-red-200 px-3 py-1.5 text-sm font-medium text-red-600 hover:bg-red-50 dark:border-red-900 dark:hover:bg-red-950">
                Reject
              </button>
            </>
          )}
          {r.can_pay && (
            <button type="button" disabled={!!busy} onClick={() => onAction(r.id, 'mark-paid')}
              className="rounded-lg bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50">
              Mark paid
            </button>
          )}
        </div>
      )}
    </div>
  )
}
