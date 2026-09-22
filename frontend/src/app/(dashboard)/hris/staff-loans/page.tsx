'use client'

/**
 * /hris/staff-loans — Staff Loan module (CFO 2026-07-15).
 *
 * One page, three roles:
 *   • any employee applies (staff loan ≤ one month salary / ≤ 4 months, or a
 *     vehicle loan ≤ P40,000 with the blue book held by Alpha Direct/Veritas),
 *     then signs the undertaking once approved;
 *   • the CFO approves or declines (and may adjust amount / term / rate);
 *   • Finance releases it (CFO 15-Sep-2026) — which creates the payroll loan,
 *     posts the GL entry, and loads the payment for CFO authorisation. HR is
 *     notified for their records.
 *
 * The money (monthly salary deduction + outstanding balance) is the live
 * payroll EmployeeLoan engine; this page is the paperwork in front of it.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { apiFetch } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { OverdueBanner, OverdueModal, type OverdueInfo } from '@/components/hris/OverdueWork'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  Banknote, Car, CheckCircle2, XCircle, Clock, PenLine, Wallet,
  Loader2, Send, ShieldCheck,
} from 'lucide-react'

interface BankAccount { code: string; name: string }
interface Loan {
  id: string
  employee_name: string
  loan_type: 'staff' | 'vehicle'
  loan_type_display: string
  amount_requested: string
  term_months_requested: number
  reason: string
  monthly_salary_snapshot: string | null
  vehicle_description: string
  vehicle_reg: string
  blue_book_holder: string
  blue_book_holder_display: string
  blue_book_received: boolean
  status: string
  status_display: string
  decline_reason: string
  approved_amount: string | null
  approved_term_months: number | null
  annual_rate_pct: string
  interest_amount: string
  total_repayable: string
  monthly_instalment: string
  outstanding: string | null
  signatory_full_name: string
  disbursement_ref: string
  journal_entry_number: string | null
  is_mine: boolean
  can_submit: boolean
  can_cfo_decide: boolean
  can_sign: boolean
  can_disburse: boolean
  can_cancel: boolean
  bank_accounts: BankAccount[]
}
interface Meta {
  default_rate_pct: string
  staff_max_term_months: number
  vehicle_max_amount: string
  vehicle_max_term_months: number
  blue_book_holders: { value: string; label: string }[]
  has_employee_record: boolean
  my_monthly_salary: string | null
  attendance_ok?: boolean
  attendance_msg?: string
}

const BADGE: Record<string, string> = {
  draft: 'bg-muted text-foreground',
  pending_cfo: 'bg-amber-100 text-amber-700',
  approved: 'bg-blue-100 text-blue-700',
  signed: 'bg-indigo-100 text-indigo-700',
  active: 'bg-emerald-100 text-emerald-700',
  declined: 'bg-red-100 text-red-700',
  cancelled: 'bg-muted text-muted-foreground',
}

const pula = (n: number | string) =>
  `P${new Intl.NumberFormat('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Number(n) || 0)}`

export default function StaffLoansPage() {
  const [loans, setLoans] = useState<Loan[]>([])
  const [meta, setMeta] = useState<Meta | null>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  // Why this loan now needs a CEO/CFO signature (CFO 2026-08-07).
  const [overdue, setOverdue] = useState<OverdueInfo | null>(null)
  const [loading, setLoading] = useState(true)

  // apply form
  const [loanType, setLoanType] = useState<'staff' | 'vehicle'>('staff')
  const [amount, setAmount] = useState('')
  const [term, setTerm] = useState('')
  const [reason, setReason] = useState('')
  const [vehDesc, setVehDesc] = useState('')
  const [vehReg, setVehReg] = useState('')
  const [bbHolder, setBbHolder] = useState('')
  const [noOtherLoans, setNoOtherLoans] = useState(false)
  const [viaVeritas, setViaVeritas] = useState(false)

  // modals
  const [approveFor, setApproveFor] = useState<Loan | null>(null)
  const [disburseFor, setDisburseFor] = useState<Loan | null>(null)
  const [signFor, setSignFor] = useState<Loan | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [ls, m] = await Promise.all([
        apiFetch<Loan[]>('/staff-loans/').catch(() => []),
        apiFetch<Meta>('/staff-loans/meta/').catch(() => null),
      ])
      setLoans(Array.isArray(ls) ? ls : [])
      setMeta(m)
    } finally { setLoading(false) }
  }, [])
  useEffect(() => { load() }, [load])

  const rate = Number(meta?.default_rate_pct ?? '15.5')
  const amtN = Number(amount) || 0
  const termN = Number(term) || 0
  const previewInterest = amtN > 0 && termN > 0 ? amtN * (rate / 100) * (termN / 12) : 0
  const previewTotal = amtN + previewInterest
  const previewMonthly = termN > 0 ? previewTotal / termN : 0

  const salaryCap = meta?.my_monthly_salary ? Number(meta.my_monthly_salary) : null
  const maxTerm = loanType === 'staff' ? (meta?.staff_max_term_months ?? 6) : (meta?.vehicle_max_term_months ?? 24)
  const maxAmount = loanType === 'staff' ? salaryCap : Number(meta?.vehicle_max_amount ?? 40000)

  // A staff loan whose salary cap is unknown (no contract on file) must be
  // BLOCKED, not silently uncapped — otherwise any amount passes (bug d9c7a1fe,
  // Oprah Mogomotsi 2026-07-22: 999999999 was accepted because maxAmount==null
  // short-circuited to "ok"). The vehicle tab never hits this (cap defaults to
  // 40000), which is why it enforced correctly.
  const amountOk = amtN > 0 && maxAmount != null && amtN <= maxAmount
  const termOk = termN >= 1 && termN <= maxTerm
  const words = reason.trim() ? reason.trim().split(/\s+/).length : 0
  const vehicleOk = loanType === 'staff' || (!!bbHolder && viaVeritas)
  const canSubmit = !!meta?.has_employee_record && amountOk && termOk && vehicleOk
    && words >= 50 && noOtherLoans && meta?.attendance_ok !== false

  async function apply() {
    setBusy(true); setMsg(null)
    try {
      const body: Record<string, unknown> = {
        loan_type: loanType, amount_requested: amount,
        term_months_requested: termN, reason: reason.trim(),
        no_other_loans: noOtherLoans,
      }
      if (loanType === 'vehicle') {
        body.vehicle_description = vehDesc; body.vehicle_reg = vehReg
        body.blue_book_holder = bbHolder; body.purchased_via_veritas = viaVeritas
      }
      const created = await apiFetch<Loan>('/staff-loans/', { method: 'POST', body: JSON.stringify(body) })
      const sent = await apiFetch<OverdueInfo>(
        `/staff-loans/${created.id}/submit/`, { method: 'POST', body: '{}' })
      setMsg('Sent to the CFO for approval.')
      if (sent?.needs_exec_signoff) setOverdue(sent)
      setAmount(''); setTerm(''); setReason(''); setVehDesc(''); setVehReg(''); setBbHolder('')
      setNoOtherLoans(false); setViaVeritas(false)
      load()
    } catch (e) { setMsg(cleanErr(e)) } finally { setBusy(false) }
  }

  async function act(url: string, body: unknown, done?: () => void) {
    setBusy(true); setMsg(null)
    try {
      await apiFetch(url, { method: 'POST', body: JSON.stringify(body ?? {}) })
      done?.(); load()
    } catch (e) { setMsg(cleanErr(e)) } finally { setBusy(false) }
  }

  const mine = loans.filter(l => l.is_mine)
  const cfoQueue = loans.filter(l => l.can_cfo_decide)
  // Finance's queue now, not HR's. can_disburse is role-gated server-side.
  const financeQueue = loans.filter(l => l.can_disburse)
  const others = loans.filter(l => !l.is_mine && !l.can_cfo_decide && !l.can_disburse)

  return (
    <div>
      <TopBar />
      <div className="p-6 max-w-5xl mx-auto space-y-6">
        <div>
          <h1 className="text-xl font-semibold flex items-center gap-2">
            <Banknote className="h-5 w-5 text-[#F07F00]" /> Staff Loans
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            Apply for a staff loan or a vehicle loan. The CFO approves, HR pays it out,
            and it comes off your salary each month.
          </p>
          <div className="mt-3 text-xs rounded-md border-l-4 border-[#F07F00] bg-[#FFF7ED] text-[#7c2d12] p-3">
            A staff loan is a <b>favour, not an entitlement</b> — not everyone qualifies.
            Approval weighs your Time Doctor hours, discipline and performance. Vehicle loans
            are only for vehicles bought through our Veritas salvage yard.
          </div>
        </div>
        {msg && <div className="text-sm rounded-md bg-muted/60 p-3">{msg}</div>}

        {loading ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground py-10 justify-center">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading…
          </div>
        ) : (
        <>
        {/* Apply */}
        <Card><CardContent className="p-5 space-y-4">
          <div className="font-medium">Apply for a loan</div>
          {/* Warn before the form is filled in, not after (CFO 2026-08-07). */}
          <OverdueBanner what="loan" />
          {!meta?.has_employee_record && (
            <p className="text-xs text-amber-600">
              Your account isn&apos;t linked to a payroll record yet, so you can&apos;t apply.
              Ask HR to link you first.
            </p>
          )}
          {meta?.attendance_ok === false && (
            <div className="text-xs rounded-md border-l-4 border-red-500 bg-red-50 text-red-700 p-3">
              {meta.attendance_msg}
            </div>
          )}

          {/* type toggle */}
          <div className="flex gap-2">
            {(['staff', 'vehicle'] as const).map(t => (
              <button key={t} onClick={() => setLoanType(t)}
                className={'flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm border ' +
                  (loanType === t ? 'bg-[#0B0B3B] text-white border-[#0B0B3B]' : 'bg-background hover:bg-muted')}>
                {t === 'staff' ? <Wallet className="h-4 w-4" /> : <Car className="h-4 w-4" />}
                {t === 'staff' ? 'Staff loan' : 'Vehicle loan'}
              </button>
            ))}
          </div>

          <div className="grid sm:grid-cols-2 gap-3">
            <div>
              <label className="text-xs font-medium">Amount (BWP)</label>
              <input value={amount} onChange={e => setAmount(e.target.value)} inputMode="decimal"
                placeholder="e.g. 5000" className="mt-1 w-full px-3 py-2 text-sm border rounded-md bg-background" />
              {loanType === 'staff' && salaryCap != null && (
                <p className={'text-[11px] mt-1 ' + (amountOk || !amtN ? 'text-muted-foreground' : 'text-red-600')}>
                  Up to one month salary — {pula(salaryCap)}.
                </p>
              )}
              {loanType === 'staff' && salaryCap == null && (
                <p className="text-[11px] mt-1 text-amber-600">No salary on file — HR must load your contract first.</p>
              )}
              {loanType === 'vehicle' && (
                <p className={'text-[11px] mt-1 ' + (amountOk || !amtN ? 'text-muted-foreground' : 'text-red-600')}>
                  Up to {pula(meta?.vehicle_max_amount ?? 40000)}.
                </p>
              )}
            </div>
            <div>
              <label className="text-xs font-medium">Repay over (months)</label>
              <input value={term} onChange={e => setTerm(e.target.value)} inputMode="numeric"
                placeholder={`1 – ${maxTerm}`} className="mt-1 w-full px-3 py-2 text-sm border rounded-md bg-background" />
              <p className={'text-[11px] mt-1 ' + (termOk || !termN ? 'text-muted-foreground' : 'text-red-600')}>
                {loanType === 'staff' ? `Within ${meta?.staff_max_term_months ?? 6} months.` : `1 to ${maxTerm} months.`}
              </p>
            </div>
          </div>

          {loanType === 'vehicle' && (
            <div className="grid sm:grid-cols-2 gap-3">
              <input value={vehDesc} onChange={e => setVehDesc(e.target.value)} placeholder="Vehicle (make / model)"
                className="px-3 py-2 text-sm border rounded-md bg-background" />
              <input value={vehReg} onChange={e => setVehReg(e.target.value)} placeholder="Registration (e.g. B 123 ABC)"
                className="px-3 py-2 text-sm border rounded-md bg-background" />
              <div className="sm:col-span-2">
                <label className="text-xs font-medium">Blue book held by</label>
                <select value={bbHolder} onChange={e => setBbHolder(e.target.value)}
                  className="mt-1 w-full px-3 py-2 text-sm border rounded-md bg-background">
                  <option value="">Choose…</option>
                  {(meta?.blue_book_holders || []).map(h => <option key={h.value} value={h.value}>{h.label}</option>)}
                </select>
                <p className="text-[11px] mt-1 text-muted-foreground">
                  The blue book stays in this name until the loan is fully repaid.
                </p>
              </div>
              <label className="sm:col-span-2 flex items-start gap-2 text-sm">
                <input type="checkbox" checked={viaVeritas} onChange={e => setViaVeritas(e.target.checked)} className="mt-0.5" />
                <span>I confirm this vehicle was <b>purchased through the Veritas salvage yard</b> (vehicle loans are only for Veritas salvage vehicles).</span>
              </label>
            </div>
          )}

          <div>
            <label className="text-xs font-medium">Motivation — why you need this loan (at least 50 words)</label>
            <textarea value={reason} onChange={e => setReason(e.target.value)} rows={4}
              placeholder="Explain in your own words: what the loan is for, why you need it now, and how it helps. At least 50 words."
              className="mt-1 w-full px-3 py-2 text-sm border rounded-md bg-background" />
            <p className={'text-[11px] mt-1 ' + (words >= 50 ? 'text-emerald-600' : 'text-muted-foreground')}>{words}/50 words</p>
          </div>

          <label className="flex items-start gap-2 text-sm">
            <input type="checkbox" checked={noOtherLoans} onChange={e => setNoOtherLoans(e.target.checked)} className="mt-0.5" />
            <span>I confirm I have <b>no other loans</b> with any bank or financial institution.</span>
          </label>

          {/* live preview — only for in-range input. Never compute figures for
              an amount/term outside the limits (bug d9c7a1fe): showing
              P154,999,999 for a 999999999 request read as if the system had
              accepted it. Out of range → prompt to fix, don't calculate. */}
          {amtN > 0 && termN > 0 && amountOk && termOk && (
            <div className="rounded-md border bg-muted/40 p-3 text-sm grid grid-cols-3 gap-2 text-center">
              <div><div className="text-[11px] text-muted-foreground">Interest ({rate}%/yr)</div><div className="font-medium">{pula(previewInterest)}</div></div>
              <div><div className="text-[11px] text-muted-foreground">Total to repay</div><div className="font-medium">{pula(previewTotal)}</div></div>
              <div><div className="text-[11px] text-muted-foreground">Per month</div><div className="font-medium text-[#0B0B3B]">{pula(previewMonthly)}</div></div>
            </div>
          )}
          {amtN > 0 && termN > 0 && !(amountOk && termOk) && (
            <div className="rounded-md border border-red-200 bg-red-50 p-3 text-[13px] text-red-700">
              Amount or repayment term is outside the allowed limits shown above. Fix those before an estimate can be calculated.
            </div>
          )}

          <div className="flex justify-end">
            <Button size="sm" disabled={busy || !canSubmit} onClick={apply}>
              <Send className="h-4 w-4 mr-1" /> Submit application
            </Button>
          </div>
        </CardContent></Card>

        {/* CFO approval queue */}
        {cfoQueue.length > 0 && (
          <Card><CardContent className="p-5">
            <div className="font-medium mb-2 flex items-center gap-2">
              <Clock className="h-4 w-4 text-amber-600" /> Pending your approval ({cfoQueue.length})
            </div>
            {cfoQueue.map(l => (
              <LoanRow key={l.id} l={l} actions={
                <Button size="sm" disabled={busy} onClick={() => setApproveFor(l)}>
                  <CheckCircle2 className="h-4 w-4 mr-1" /> Review
                </Button>} />
            ))}
          </CardContent></Card>
        )}

        {/* Finance release queue */}
        {financeQueue.length > 0 && (
          <Card><CardContent className="p-5">
            <div className="font-medium mb-2 flex items-center gap-2">
              <Banknote className="h-4 w-4 text-emerald-600" /> To release ({financeQueue.length})
            </div>
            {financeQueue.map(l => (
              <LoanRow key={l.id} l={l} actions={
                <Button size="sm" disabled={busy} onClick={() => setDisburseFor(l)}>
                  <Banknote className="h-4 w-4 mr-1" /> Disburse
                </Button>} />
            ))}
          </CardContent></Card>
        )}

        {/* My loans */}
        <Card><CardContent className="p-5">
          <div className="font-medium mb-2">My loans</div>
          {mine.length === 0 ? <p className="text-sm text-muted-foreground">No applications yet.</p> :
            mine.map(l => (
              <LoanRow key={l.id} l={l} actions={<>
                {l.can_submit && <Button size="sm" disabled={busy} onClick={() => act(`/staff-loans/${l.id}/submit/`, {})}><Send className="h-4 w-4 mr-1" />Submit</Button>}
                {l.can_sign && <Button size="sm" disabled={busy} onClick={() => setSignFor(l)}><PenLine className="h-4 w-4 mr-1" />Sign</Button>}
                {l.can_cancel && <Button size="sm" variant="outline" disabled={busy}
                  onClick={() => act(`/staff-loans/${l.id}/cancel/`, {})}>Cancel</Button>}
              </>} />
            ))}
        </CardContent></Card>

        {/* Admin register (HR/CFO see everyone else's) */}
        {others.length > 0 && (
          <Card><CardContent className="p-5">
            <div className="font-medium mb-2">All staff loans</div>
            {others.map(l => <LoanRow key={l.id} l={l} />)}
          </CardContent></Card>
        )}
        </>
        )}
      </div>

      {approveFor && <ApproveModal loan={approveFor} defaultRate={meta?.default_rate_pct ?? '15.5'} busy={busy}
        onClose={() => setApproveFor(null)}
        onApprove={(payload) => act(`/staff-loans/${approveFor.id}/cfo_decide/`, { approve: true, ...payload }, () => setApproveFor(null))}
        onDecline={(reason) => act(`/staff-loans/${approveFor.id}/cfo_decide/`, { approve: false, decline_reason: reason }, () => setApproveFor(null))} />}

      {disburseFor && <DisburseModal loan={disburseFor} busy={busy}
        onClose={() => setDisburseFor(null)}
        onDisburse={(payload) => act(`/staff-loans/${disburseFor.id}/disburse/`, payload, () => setDisburseFor(null))} />}

      {signFor && <SignModal loan={signFor} busy={busy}
        onClose={() => setSignFor(null)}
        onSign={(payload) => act(`/staff-loans/${signFor.id}/sign/`, payload, () => setSignFor(null))} />}

      {overdue && <OverdueModal info={overdue} onClose={() => setOverdue(null)} />}
    </div>
  )
}

function cleanErr(e: unknown): string {
  if (e instanceof Error) return e.message.replace(/^HTTP \d+: /, '')
  return 'Something went wrong.'
}

function LoanRow({ l, actions }: { l: Loan; actions?: React.ReactNode }) {
  const amt = l.approved_amount ?? l.amount_requested
  const term = l.approved_term_months ?? l.term_months_requested
  return (
    <div className="py-2.5 border-b last:border-0 flex flex-wrap items-center gap-x-3 gap-y-1">
      <span className="flex items-center gap-1.5 font-medium flex-1 min-w-[180px]">
        {l.loan_type === 'vehicle' ? <Car className="h-4 w-4 text-muted-foreground" /> : <Wallet className="h-4 w-4 text-muted-foreground" />}
        {l.loan_type_display} — {pula(amt)} · {term} mo
      </span>
      <span className="text-xs text-muted-foreground">{l.employee_name}</span>
      {Number(l.outstanding) > 0 && <span className="text-xs text-muted-foreground">outstanding {pula(l.outstanding!)}</span>}
      {l.loan_type === 'vehicle' && l.blue_book_holder && (
        <span className="text-xs text-muted-foreground flex items-center gap-0.5">
          <ShieldCheck className="h-3 w-3" /> blue book: {l.blue_book_holder_display}
        </span>
      )}
      {l.journal_entry_number && <span className="text-xs text-muted-foreground">GL {l.journal_entry_number}</span>}
      <span className={'text-xs px-2 py-0.5 rounded ' + (BADGE[l.status] || '')}>{l.status_display}</span>
      {l.status === 'declined' && l.decline_reason && <span className="text-xs text-red-600 w-full">↩ {l.decline_reason}</span>}
      {actions}
    </div>
  )
}

function ApproveModal({ loan, defaultRate, busy, onClose, onApprove, onDecline }: {
  loan: Loan; defaultRate: string; busy: boolean; onClose: () => void
  onApprove: (p: { approved_amount: string; approved_term_months: number; annual_rate_pct: string; notes: string }) => void
  onDecline: (reason: string) => void
}) {
  const [amount, setAmount] = useState(loan.amount_requested)
  const [term, setTerm] = useState(String(loan.term_months_requested))
  const [rate, setRate] = useState(loan.annual_rate_pct || defaultRate)
  const [notes, setNotes] = useState('')
  const [declineReason, setDeclineReason] = useState('')
  return (
    <Modal onClose={onClose} title={`Review — ${loan.employee_name}`}>
      <p className="text-xs text-muted-foreground">
        {loan.loan_type_display} · requested {pula(loan.amount_requested)} over {loan.term_months_requested} months.
        {loan.monthly_salary_snapshot && ` Monthly salary on file: ${pula(loan.monthly_salary_snapshot)}.`}
      </p>
      <p className="text-xs text-muted-foreground italic">&ldquo;{loan.reason}&rdquo;</p>
      <div className="grid grid-cols-3 gap-2">
        <Field label="Amount (BWP)"><input value={amount} onChange={e => setAmount(e.target.value)} className="w-full px-3 py-2 text-sm border rounded-md bg-background" /></Field>
        <Field label="Term (months)"><input value={term} onChange={e => setTerm(e.target.value)} className="w-full px-3 py-2 text-sm border rounded-md bg-background" /></Field>
        <Field label="Rate (%/yr)"><input value={rate} onChange={e => setRate(e.target.value)} className="w-full px-3 py-2 text-sm border rounded-md bg-background" /></Field>
      </div>
      <textarea value={notes} onChange={e => setNotes(e.target.value)} rows={2} placeholder="Note (optional)"
        className="w-full px-3 py-2 text-sm border rounded-md bg-background" />
      <div className="flex justify-end gap-2">
        <Button size="sm" disabled={busy || !amount || !term || !rate}
          onClick={() => onApprove({ approved_amount: amount, approved_term_months: Number(term), annual_rate_pct: rate, notes })}>
          <CheckCircle2 className="h-4 w-4 mr-1" /> Approve
        </Button>
      </div>
      <div className="border-t pt-3">
        <input value={declineReason} onChange={e => setDeclineReason(e.target.value)} placeholder="Reason to decline"
          className="w-full px-3 py-2 text-sm border rounded-md bg-background" />
        <div className="flex justify-end mt-2">
          <Button size="sm" variant="outline" disabled={busy || !declineReason.trim()} onClick={() => onDecline(declineReason.trim())}>
            <XCircle className="h-4 w-4 mr-1" /> Decline
          </Button>
        </div>
      </div>
    </Modal>
  )
}

function DisburseModal({ loan, busy, onClose, onDisburse }: {
  loan: Loan; busy: boolean; onClose: () => void
  onDisburse: (p: { disbursement_bank_code: string; disbursement_ref: string; blue_book_received: boolean }) => void
}) {
  const [bank, setBank] = useState(loan.bank_accounts[0]?.code ?? '')
  const [ref, setRef] = useState('')
  const [bbReceived, setBbReceived] = useState(false)
  const needBook = loan.loan_type === 'vehicle'
  const ok = !!bank && (!needBook || bbReceived)
  return (
    <Modal onClose={onClose} title={`Disburse — ${loan.employee_name}`}>
      <p className="text-xs text-muted-foreground">
        {loan.loan_type_display}: pay out {pula(loan.approved_amount ?? loan.amount_requested)}.
        The employee repays {pula(loan.total_repayable)} total ({pula(loan.monthly_instalment)}/month).
      </p>
      <Field label="Pay from account">
        <select value={bank} onChange={e => setBank(e.target.value)} className="w-full px-3 py-2 text-sm border rounded-md bg-background">
          <option value="">Choose bank / cash account…</option>
          {loan.bank_accounts.map(b => <option key={b.code} value={b.code}>{b.code} — {b.name}</option>)}
        </select>
      </Field>
      <Field label="Payment / PACOTO reference">
        <input value={ref} onChange={e => setRef(e.target.value)} placeholder="e.g. PACOTO batch / FNB ref" className="w-full px-3 py-2 text-sm border rounded-md bg-background" />
      </Field>
      {needBook && (
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={bbReceived} onChange={e => setBbReceived(e.target.checked)} />
          The vehicle blue book is held by {loan.blue_book_holder_display || 'Alpha Direct'}.
        </label>
      )}
      <div className="flex justify-end">
        <Button size="sm" disabled={busy || !ok}
          onClick={() => onDisburse({ disbursement_bank_code: bank, disbursement_ref: ref, blue_book_received: bbReceived })}>
          <Banknote className="h-4 w-4 mr-1" /> Pay out & post to GL
        </Button>
      </div>
    </Modal>
  )
}

function SignModal({ loan, busy, onClose, onSign }: {
  loan: Loan; busy: boolean; onClose: () => void
  onSign: (p: { signature_data_url: string; signatory_full_name: string }) => void
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [name, setName] = useState(loan.employee_name || '')
  const [drawn, setDrawn] = useState(false)
  const drawing = useRef(false)

  const pos = (e: React.PointerEvent) => {
    const c = canvasRef.current!; const r = c.getBoundingClientRect()
    // scale CSS pixels -> canvas backing-store pixels so the ink lands under
    // the finger on a phone (canvas is w-full but width is fixed at 440).
    return { x: (e.clientX - r.left) * (c.width / r.width), y: (e.clientY - r.top) * (c.height / r.height) }
  }
  const start = (e: React.PointerEvent) => {
    drawing.current = true; const ctx = canvasRef.current!.getContext('2d')!
    const p = pos(e); ctx.beginPath(); ctx.moveTo(p.x, p.y)
  }
  const move = (e: React.PointerEvent) => {
    if (!drawing.current) return
    const ctx = canvasRef.current!.getContext('2d')!
    const p = pos(e); ctx.lineTo(p.x, p.y); ctx.strokeStyle = '#0B0B3B'; ctx.lineWidth = 2; ctx.stroke(); setDrawn(true)
  }
  const end = () => { drawing.current = false }
  const clear = () => {
    const c = canvasRef.current!; c.getContext('2d')!.clearRect(0, 0, c.width, c.height); setDrawn(false)
  }

  return (
    <Modal onClose={onClose} title="Sign your loan undertaking">
      <p className="text-xs text-muted-foreground">
        {loan.loan_type_display}: {pula(loan.approved_amount ?? loan.amount_requested)} over {loan.approved_term_months} months
        at {loan.annual_rate_pct}%/yr. You repay {pula(loan.total_repayable)} in total, {pula(loan.monthly_instalment)} a month
        from your salary.
        {loan.loan_type === 'vehicle' && ` The blue book stays with ${loan.blue_book_holder_display} until it is fully repaid.`}
      </p>
      <Field label="Full name">
        <input value={name} onChange={e => setName(e.target.value)} className="w-full px-3 py-2 text-sm border rounded-md bg-background" />
      </Field>
      <div>
        <label className="text-xs font-medium">Sign below</label>
        <canvas ref={canvasRef} width={440} height={140}
          onPointerDown={start} onPointerMove={move} onPointerUp={end} onPointerLeave={end}
          className="mt-1 w-full border rounded-md bg-white touch-none" style={{ height: 140 }} />
        <button onClick={clear} className="text-xs text-blue-600 mt-1">Clear</button>
      </div>
      <div className="flex justify-end">
        <Button size="sm" disabled={busy || !drawn || !name.trim()}
          onClick={() => onSign({ signature_data_url: canvasRef.current!.toDataURL('image/png'), signatory_full_name: name.trim() })}>
          <PenLine className="h-4 w-4 mr-1" /> Sign & start
        </Button>
      </div>
    </Modal>
  )
}

function Modal({ title, children, onClose }: { title: string; children: React.ReactNode; onClose: () => void }) {
  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <Card className="w-full max-w-md" onClick={e => e.stopPropagation()}>
        <CardContent className="p-5 space-y-3">
          <div className="flex items-center justify-between">
            <div className="font-medium">{title}</div>
            <button onClick={onClose}><XCircle className="h-4 w-4" /></button>
          </div>
          {children}
        </CardContent>
      </Card>
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="text-xs font-medium">{label}</label>
      <div className="mt-1">{children}</div>
    </div>
  )
}
