'use client'

/**
 * "Add Employee to Payroll" with Finance sign-off (Pako Kago 2026-08-12).
 *
 * AddEmployeeButton — header button + modal to stage a new employee for a
 * period. Saves in PENDING status; nothing touches headcount or the period
 * totals until a Finance signer approves.
 * PendingAdditionsCard — the Finance approver's queue (full details + Approve /
 * Reject with a mandatory comment) plus a badge; the requester sees their own.
 */
import { useCallback, useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import {
  createPayrollAddition, getPayrollAdditionMeta, listPayrollAdditions,
  approvePayrollAddition, rejectPayrollAddition,
  type PayrollAdditionMeta, type PayrollAdditionRow,
} from '@/lib/api'
import { useCompany } from '@/contexts/CompanyContext'
import { Button } from '@/components/ui/button'
import {
  UserPlus, X, Plus, Trash2, Loader2, CheckCircle2, AlertCircle, Check, Ban,
} from 'lucide-react'

const INPUT = { background: '#0b1a2c', border: '1px solid #21384f', color: '#eaf1f8' } as const
const pula = (v: string | number) => {
  const n = typeof v === 'string' ? parseFloat(v) : v
  return Number.isNaN(n) ? '—'
    : new Intl.NumberFormat('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n)
}

type Allow = { code: string; amount: string }

export function AddEmployeeButton({ onDone }: { onDone: () => void }) {
  const { selectedId } = useCompany()
  const [open, setOpen] = useState(false)

  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}
        leftIcon={<UserPlus className="w-3.5 h-3.5" />}>
        Add employee
      </Button>
      {open && <AddEmployeeModal defaultCompanyId={selectedId}
        onClose={() => setOpen(false)} onSaved={() => { onDone() }} />}
    </>
  )
}

function AddEmployeeModal({ defaultCompanyId, onClose, onSaved }:
  { defaultCompanyId: string | null; onClose: () => void; onSaved: () => void }) {
  const [meta, setMeta] = useState<PayrollAdditionMeta | null>(null)
  const [fullName, setFullName] = useState('')
  const [empNo, setEmpNo] = useState('')
  const [dept, setDept] = useState('')
  const [companyId, setCompanyId] = useState('')
  const [periodId, setPeriodId] = useState('')
  const [basic, setBasic] = useState('')
  const [commission, setCommission] = useState('')
  const [incentive, setIncentive] = useState('')
  const [allow, setAllow] = useState<Allow[]>([])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [warnings, setWarnings] = useState<string[]>([])
  const [done, setDone] = useState(false)

  useEffect(() => {
    getPayrollAdditionMeta().then(m => {
      setMeta(m)
      setCompanyId(defaultCompanyId || m.companies[0]?.id || '')
      setPeriodId(m.periods[0]?.id || '')
    }).catch(e => setErr(e instanceof Error ? e.message : 'Failed to load options'))
  }, [defaultCompanyId])

  const grossEstimate = [basic, commission, incentive, ...allow.map(a => a.amount)]
    .reduce((s, v) => s + (parseFloat(v) || 0), 0)

  async function submit() {
    setErr(null); setWarnings([])
    if (!fullName.trim()) { setErr('Full name is required.'); return }
    if (!(parseFloat(basic) > 0)) { setErr('Basic salary is required and must be greater than zero.'); return }
    if (!periodId || !companyId) { setErr('Pick an entity and a payroll period.'); return }
    setBusy(true)
    try {
      const res = await createPayrollAddition({
        period_id: periodId, company_id: companyId, full_name: fullName.trim(),
        employee_number: empNo.trim(), department: dept.trim(), basic,
        commission: commission || '0', incentive: incentive || '0',
        allowances: allow.filter(a => a.code && parseFloat(a.amount) > 0),
      })
      setWarnings(res.warnings || [])
      setDone(true)
      onSaved()
    } catch (e) { setErr(e instanceof Error ? e.message : 'Could not submit') }
    finally { setBusy(false) }
  }

  if (typeof document === 'undefined') return null
  return createPortal(
    <div className="fixed inset-0 z-[100] grid place-items-center p-4"
      style={{ background: 'rgba(4,10,20,.66)' }} onMouseDown={onClose}>
      <div className="w-full max-w-lg rounded-2xl border max-h-[90vh] overflow-y-auto"
        style={{ background: '#0f2236', borderColor: '#21384f', color: '#eaf1f8' }}
        onMouseDown={e => e.stopPropagation()}>
        {/* header */}
        <div className="flex items-center justify-between px-5 py-4 border-b" style={{ borderColor: '#21384f' }}>
          <div className="flex items-center gap-2">
            <UserPlus className="w-4 h-4 text-[#F4A623]" />
            <span className="font-semibold">Add employee to payroll</span>
          </div>
          <button onClick={onClose} className="text-[#9bb3c9] hover:text-white"><X className="w-4 h-4" /></button>
        </div>

        {done ? (
          <div className="p-6 text-center">
            <CheckCircle2 className="w-10 h-10 mx-auto text-[#34d399]" />
            <p className="mt-3 font-semibold">Sent for Finance Manager sign-off</p>
            <p className="text-sm text-[#9bb3c9] mt-1">
              {fullName} is staged for {meta?.periods.find(p => p.id === periodId)?.period_name}.
              Nothing shows on headcount or the totals until a Finance signer approves it.
            </p>
            {warnings.length > 0 && (
              <div className="text-left rounded-lg p-3 mt-3 text-[13px]"
                style={{ background: 'rgba(251,191,36,.10)', border: '1px solid rgba(251,191,36,.4)', color: '#fbbf24' }}>
                {warnings.map((w, i) => <div key={i} className="flex gap-2"><AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />{w}</div>)}
              </div>
            )}
            <Button size="sm" className="mt-4" onClick={onClose}>Done</Button>
          </div>
        ) : (
          <div className="p-5 space-y-4">
            <p className="text-[12px] text-[#9bb3c9] -mt-1">
              A Finance Manager must approve before this counts. You cannot approve your own submission.
            </p>

            {/* employee details */}
            <div className="grid grid-cols-2 gap-3">
              <Field label="Full name *" className="col-span-2">
                <input value={fullName} onChange={e => setFullName(e.target.value)}
                  className="w-full h-10 px-3 rounded-lg text-sm outline-none" style={INPUT} placeholder="e.g. Kefilwe Moremi" />
              </Field>
              <Field label="Employee ID">
                <input value={empNo} onChange={e => setEmpNo(e.target.value)}
                  className="w-full h-10 px-3 rounded-lg text-sm outline-none" style={INPUT} placeholder="auto if blank" />
              </Field>
              <Field label="Department">
                <input value={dept} onChange={e => setDept(e.target.value)} list="pa-depts"
                  className="w-full h-10 px-3 rounded-lg text-sm outline-none" style={INPUT} />
                <datalist id="pa-depts">{(meta?.departments || []).map(d => <option key={d} value={d} />)}</datalist>
              </Field>
              <Field label="Entity">
                <select value={companyId} onChange={e => setCompanyId(e.target.value)}
                  className="w-full h-10 px-3 rounded-lg text-sm" style={INPUT}>
                  {(meta?.companies || []).map(c => <option key={c.id} value={c.id}>{c.code || c.name}</option>)}
                </select>
              </Field>
              <Field label="Payroll period">
                <select value={periodId} onChange={e => setPeriodId(e.target.value)}
                  className="w-full h-10 px-3 rounded-lg text-sm" style={INPUT}>
                  {(meta?.periods || []).map(p => <option key={p.id} value={p.id}>{p.period_name}</option>)}
                  {meta && meta.periods.length === 0 && <option value="">No open periods</option>}
                </select>
              </Field>
            </div>

            {/* pay components */}
            <div className="grid grid-cols-3 gap-3">
              <Field label="Basic salary *"><Amount v={basic} set={setBasic} /></Field>
              <Field label="Commission"><Amount v={commission} set={setCommission} /></Field>
              <Field label="Incentive"><Amount v={incentive} set={setIncentive} /></Field>
            </div>

            {/* allowances */}
            <div>
              <div className="flex items-center justify-between mb-1.5">
                <span className="text-[11px] uppercase tracking-wider text-[#9bb3c9] font-semibold">Allowances</span>
                <button onClick={() => setAllow([...allow, { code: meta?.allowance_types[0]?.code || '', amount: '' }])}
                  className="inline-flex items-center gap-1 text-[12px] px-2 py-1 rounded-md"
                  style={{ background: 'rgba(244,166,35,.14)', color: '#F4A623' }}>
                  <Plus className="w-3 h-3" /> Add allowance
                </button>
              </div>
              {allow.length === 0 && <p className="text-[12px] text-[#9bb3c9]">None.</p>}
              <div className="space-y-2">
                {allow.map((a, i) => (
                  <div key={i} className="flex items-center gap-2">
                    <select value={a.code} onChange={e => setAllow(allow.map((x, j) => j === i ? { ...x, code: e.target.value } : x))}
                      className="flex-1 h-9 px-2 rounded-lg text-sm" style={INPUT}>
                      {(meta?.allowance_types || []).map(t => <option key={t.code} value={t.code}>{t.name}</option>)}
                    </select>
                    <div className="w-32"><Amount v={a.amount} set={val => setAllow(allow.map((x, j) => j === i ? { ...x, amount: val } : x))} /></div>
                    <button onClick={() => setAllow(allow.filter((_, j) => j !== i))} className="text-[#9bb3c9] hover:text-[#fca5a5]">
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                ))}
              </div>
            </div>

            <div className="flex items-center justify-between rounded-lg px-3 py-2" style={{ background: '#0b1a2c', border: '1px solid #21384f' }}>
              <span className="text-[12px] text-[#9bb3c9]">Indicative gross (PAYE computed on approval)</span>
              <span className="font-bold tabular-nums">{pula(grossEstimate)}</span>
            </div>

            {err && (
              <div className="rounded-lg p-3 text-sm flex items-center gap-2 bg-[#FEF2F2] border border-[#FECACA] text-[#991B1B]">
                <AlertCircle className="w-4 h-4 shrink-0" /> {err}
              </div>
            )}

            <div className="flex items-center gap-2 pt-1">
              <Button size="sm" onClick={submit} disabled={busy}
                leftIcon={busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <UserPlus className="w-3.5 h-3.5" />}>
                {busy ? 'Sending…' : 'Send for sign-off'}
              </Button>
              <Button size="sm" variant="outline" onClick={onClose} disabled={busy}>Cancel</Button>
            </div>
          </div>
        )}
      </div>
    </div>,
    document.body,
  )
}

function Field({ label, className, children }: { label: string; className?: string; children: React.ReactNode }) {
  return (
    <label className={`block ${className || ''}`}>
      <span className="block text-[11px] uppercase tracking-wider text-[#9bb3c9] font-semibold mb-1.5">{label}</span>
      {children}
    </label>
  )
}
function Amount({ v, set }: { v: string; set: (s: string) => void }) {
  return (
    <input type="number" step="0.01" min="0" value={v} onChange={e => set(e.target.value)}
      className="w-full h-10 px-3 rounded-lg text-sm outline-none tabular-nums" style={INPUT} placeholder="0.00" />
  )
}

// ─── Finance approver queue + badge ──────────────────────────────────────────
export function PendingAdditionsCard({ card, reloadKey, onDone }:
  { card: React.CSSProperties; reloadKey: number; onDone: () => void }) {
  const [rows, setRows] = useState<PayrollAdditionRow[] | null>(null)
  const [canApprove, setCanApprove] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [rejecting, setRejecting] = useState<string | null>(null)
  const [comment, setComment] = useState('')

  const load = useCallback(async () => {
    setErr(null)
    try {
      const r = await listPayrollAdditions({ status: 'pending' })
      setRows(r.results); setCanApprove(r.can_approve)
    } catch (e) { setErr(e instanceof Error ? e.message : 'Failed to load') }
  }, [])
  useEffect(() => { load() }, [load, reloadKey])

  async function approve(id: string) {
    setBusy(id); setErr(null)
    try { await approvePayrollAddition(id); await load(); onDone() }
    catch (e) { setErr(e instanceof Error ? e.message : 'Approve failed') }
    finally { setBusy(null) }
  }
  async function reject(id: string) {
    if (!comment.trim()) { setErr('A reason is required to reject.'); return }
    setBusy(id); setErr(null)
    try { await rejectPayrollAddition(id, comment.trim()); setRejecting(null); setComment(''); await load() }
    catch (e) { setErr(e instanceof Error ? e.message : 'Reject failed') }
    finally { setBusy(null) }
  }

  // Nothing pending and nothing to show → keep the dashboard clean.
  if (rows !== null && rows.length === 0) return null

  return (
    <div className="rounded-2xl p-5 border" style={card}>
      <div className="flex items-center justify-between mb-1">
        <div className="text-xs uppercase tracking-widest text-[#9bb3c9] font-semibold">
          Employee additions — awaiting sign-off
        </div>
        {rows && rows.length > 0 && (
          <span className="text-[11px] px-2 py-0.5 rounded-full font-semibold"
            style={{ background: 'rgba(244,166,35,.16)', color: '#F4A623' }}>{rows.length} pending</span>
        )}
      </div>
      <p className="text-[11px] text-[#9bb3c9] mb-3">
        {canApprove
          ? 'Review and approve or reject. You cannot approve a request you submitted.'
          : 'These are staged and waiting for a Finance Manager to sign off.'}
      </p>

      {err && (
        <div className="rounded-lg p-3 text-sm flex items-center gap-2 mb-3 bg-[#FEF2F2] border border-[#FECACA] text-[#991B1B]">
          <AlertCircle className="w-4 h-4 shrink-0" /> {err}
        </div>
      )}
      {rows === null ? <p className="text-sm text-[#9bb3c9]">Loading…</p> : (
        <div className="space-y-2">
          {rows.map(r => (
            <div key={r.id} className="rounded-xl p-3 border" style={{ background: '#0f2236', borderColor: '#21384f' }}>
              <div className="flex items-start justify-between gap-3 flex-wrap">
                <div>
                  <div className="font-semibold text-[#eaf1f8]">{r.full_name}
                    <span className="text-[#9bb3c9] font-normal"> · {r.company.code || r.company.name} · {r.period.period_name}</span>
                  </div>
                  <div className="text-[12px] text-[#9bb3c9] mt-0.5 tabular-nums">
                    Basic {pula(r.basic)}
                    {parseFloat(r.commission) > 0 && <> · Comm {pula(r.commission)}</>}
                    {parseFloat(r.incentive) > 0 && <> · Inc {pula(r.incentive)}</>}
                    {r.allowances.length > 0 && <> · Allow {pula(r.allowances.reduce((s, a) => s + (parseFloat(a.amount) || 0), 0))}</>}
                    <> · <span className="text-[#eaf1f8] font-semibold">≈ {pula(r.gross_estimate)}</span> gross</>
                  </div>
                  <div className="text-[11px] text-[#9bb3c9] mt-0.5">
                    Requested by {r.requested_by || '—'}{r.department ? ` · ${r.department}` : ''}
                  </div>
                </div>
                {r.can_action ? (
                  <div className="flex items-center gap-2">
                    <Button size="sm" onClick={() => approve(r.id)} disabled={busy !== null}
                      leftIcon={busy === r.id ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}>
                      Approve
                    </Button>
                    <Button size="sm" variant="outline" onClick={() => { setRejecting(rejecting === r.id ? null : r.id); setComment('') }}
                      disabled={busy !== null} leftIcon={<Ban className="w-3.5 h-3.5" />}>
                      Reject
                    </Button>
                  </div>
                ) : r.blocked_reason ? (
                  <div className="flex items-start gap-1.5 text-[12px] max-w-[17rem]" style={{ color: '#F4A623' }}>
                    <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" /> {r.blocked_reason}
                  </div>
                ) : null}
              </div>
              {r.can_action && rejecting === r.id && (
                <div className="mt-2 flex items-center gap-2">
                  <input value={comment} onChange={e => setComment(e.target.value)} autoFocus
                    placeholder="Reason (required) — returned to the requester"
                    className="flex-1 h-9 px-3 rounded-lg text-sm outline-none" style={INPUT} />
                  <Button size="sm" variant="outline" onClick={() => reject(r.id)} disabled={busy !== null || !comment.trim()}>
                    Send back
                  </Button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
