'use client'

/**
 * /hris/amendments — HRIS dual-approval amendments (CFO directive 2026-06-07).
 *
 * The HR team submits a proposed change to an employee record; it parks as a
 * PENDING amendment and the live record is NOT touched until a second person
 * (Unami; the CFO when Unami is the maker) approves. omni emails the approver
 * and the requestor automatically. This page is the submit form + the
 * approval queue. Backend: /hris/api/amendments/ (see hris/amendment_views.py).
 */
import { useEffect, useMemo, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import Link from 'next/link'
import {
  ChevronLeft, Send, Loader2, AlertCircle, CheckCircle2, Clock,
  ShieldCheck, XCircle, UserCog, Undo2,
} from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { useHrisAccess } from '@/hooks/useHrisAccess'
import { authedHrisFetch, fetchHrisEmployees, type HrisEmployee } from '../_shared'
import EmployeeSearchPicker from '@/components/hris/EmployeeSearchPicker'

// Employee-record fields the HR team may amend (mirrors the backend whitelist
// in hris/amendment_service._registry()).
const FIELDS: { key: string; label: string; from?: keyof HrisEmployee; combobox?: boolean; profile?: boolean }[] = [
  { key: 'full_name', label: 'Full name',  from: 'nm' },
  { key: 'job_title', label: 'Job title',  from: 'ps' },
  { key: 'department', label: 'Department', from: 'dp' },
  { key: 'phone',     label: 'Phone' },
  { key: 'bank_name', label: 'Bank' },
  { key: 'bank_account_no', label: 'Bank account no.' },
  // Conditions-of-service contract type (Unami 2026-07-01) — EMPLOYEE amendment.
  // Backend writes it onto the employee's active EmploymentContract.
  { key: 'contract_type', label: 'Contract type (permanent / fixed_term / probation / internship)' },
  // Personal / HR data on the HRIS profile (Unami 2026-07-01) — routed as
  // PROFILE amendments (dual-approval). Type a value to change it; blank = no change.
  { key: 'marital_status', label: 'Marital status (single / married / divorced / widowed)', profile: true },
  { key: 'passport_number', label: 'Passport number', profile: true },
  { key: 'permit_number', label: 'Permit number', profile: true },
  { key: 'disabilities', label: 'Disabilities (optional)', profile: true },
  { key: 'allergies', label: 'Allergies (optional)', profile: true },
  { key: 'emergency_contact_name', label: 'Emergency contact — name', profile: true },
  { key: 'emergency_contact_phone', label: 'Emergency contact — phone', profile: true },
  { key: 'emergency_contact_relationship', label: 'Emergency contact — relationship', profile: true },
  // Bugs f05470e2 / 6b12dde5 / HRIS-004 (Oprah): expose the reporting line as a
  // type-OR-pick combobox. HRIS-004: the old <select> rejected a typed name, so
  // manual entry never saved. Prefills the manager NAME; submit() resolves it
  // back to an employee id. Routed as a PROFILE amendment via dual approval.
  { key: 'manager_id', label: 'Reports to (manager / leave approver)', from: 'mg', combobox: true },
]

interface Amendment {
  id: string
  target_kind: string
  target_label: string
  changes: Record<string, { old: string; new: string; label: string }>
  reason: string
  status: string
  maker_email: string
  approver_email: string
  is_own?: boolean   // viewer is the maker — cannot approve own (BUG 6f53096f)
  created_at: string
  decided_at?: string | null
  reversal_of?: string | null   // this amendment undoes that one
  has_reversal?: boolean        // a reversal already exists — don't offer another
}

export default function HrisAmendmentsPage() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const { themeKey } = useTheme()
  const allowed = useHrisAccess()

  const [employees, setEmployees] = useState<HrisEmployee[]>([])
  const [eid, setEid] = useState('')
  const [vals, setVals] = useState<Record<string, string>>({})
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const [pending, setPending] = useState<Amendment[]>([])
  // Applied amendments, so a wrong change can be put back (2026-08-25). The
  // reversal is itself a normal amendment and goes through the same approval.
  const [applied, setApplied] = useState<Amendment[]>([])
  const [loadingPending, setLoadingPending] = useState(true)

  const selected = useMemo(
    () => employees.find(e => e.eid === eid), [employees, eid])

  useEffect(() => { if (allowed === false) router.replace('/dashboard') }, [allowed, router])

  useEffect(() => {
    if (allowed !== true) return
    fetchHrisEmployees().then(r => setEmployees((r.employees || []).filter(e => e.eid)))
    refreshPending()
  }, [allowed])

  // Preselect the employee passed from the People Directory's Edit link (?eid=).
  // CFO 2026-06-11 (Oprah report): HR edits employee data here from /hris/directory.
  useEffect(() => {
    const qeid = searchParams.get('eid')
    if (qeid && employees.some(e => e.eid === qeid)) setEid(qeid)
  }, [employees, searchParams])

  // Prefill editable fields when an employee is picked.
  useEffect(() => {
    if (!selected) { setVals({}); return }
    const next: Record<string, string> = {}
    for (const f of FIELDS) next[f.key] = f.from ? String(selected[f.from] ?? '') : ''
    setVals(next)
  }, [eid]) // eslint-disable-line react-hooks/exhaustive-deps

  function refreshPending() {
    setLoadingPending(true)
    Promise.all([
      authedHrisFetch('/hris/api/amendments/pending/')
        .then(async r => { if (r.ok) { const d = await r.json(); setPending(Array.isArray(d.amendments) ? d.amendments : []) } }),
      authedHrisFetch('/hris/api/amendments/pending/?status=approved')
        .then(async r => { if (r.ok) { const d = await r.json(); setApplied(Array.isArray(d.amendments) ? d.amendments : []) } }),
    ]).finally(() => setLoadingPending(false))
  }

  async function submit() {
    if (!eid) { setMsg({ ok: false, text: 'Pick an employee first.' }); return }
    // Only send fields with a value; the backend diffs against the live record.
    const changes: Record<string, string> = {}
    for (const f of FIELDS) { const v = (vals[f.key] ?? '').trim(); if (v) changes[f.key] = v }
    // HRISProfile personal fields (Unami 2026-07-01) live on the profile, not the
    // Employee record — pull them out of the employee changes into their own
    // PROFILE amendment (dual-approval), same as manager_id.
    const profChanges: Record<string, string> = {}
    for (const f of FIELDS) {
      if (f.profile && changes[f.key] !== undefined) { profChanges[f.key] = changes[f.key]; delete changes[f.key] }
    }
    // manager_id lives on the HRIS PROFILE (not the Employee record) — split it
    // into its own amendment with target_kind 'profile'. HRIS-004: the field is a
    // type-or-pick combobox holding the approver's NAME — resolve it back to an
    // employee id here. A typed name that doesn't match an employee is a clear,
    // named error, never a silent drop.
    delete changes.manager_id
    const typedMgr = (vals.manager_id ?? '').trim()
    let managerVal = ''
    if (typedMgr) {
      const norm = (s: string) => s.replace(/\./g, ' ').replace(/\s+/g, ' ').trim().toLowerCase()
      const tok  = (s: string) => { const p = norm(s).split(' '); return p.length ? `${p[0]} ${p[p.length - 1]}` : '' }
      const pool = employees.filter(e => e.eid && e.eid !== eid)
      let hits = pool.filter(e => norm(e.nm) === norm(typedMgr))
      if (hits.length === 0) hits = pool.filter(e => tok(e.nm) === tok(typedMgr))
      if (hits.length === 0) { setMsg({ ok: false, text: `Approver '${typedMgr}' not found in HRIS — please select from the employee list.` }); return }
      if (hits.length > 1)  { setMsg({ ok: false, text: `Approver '${typedMgr}' matches more than one employee — pick the exact name from the list.` }); return }
      managerVal = hits[0].eid!
    }
    // Only when it actually changed, so an untouched prefill never produces a no-op.
    const managerChanged = managerVal !== (selected?.mid ?? '')
    if (!Object.keys(changes).length && !Object.keys(profChanges).length && !managerChanged) { setMsg({ ok: false, text: 'Nothing to change — edit a field or change the approver before submitting.' }); return }
    setBusy(true); setMsg(null)
    try {
      let approver = ''
      if (Object.keys(changes).length) {
        const r = await authedHrisFetch('/hris/api/amendments/', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ target_kind: 'employee', target_id: eid, changes, reason: reason.trim() }),
        })
        const d = await r.json().catch(() => ({}))
        if (!r.ok) { setMsg({ ok: false, text: d.detail || `HTTP ${r.status}` }); return }
        approver = d.approver_email || ''
      }
      if (Object.keys(profChanges).length) {
        if (!selected?.pid) { setMsg({ ok: false, text: 'This employee has no HR profile record yet — the personal fields can\'t be saved until one is created.' }); return }
        const rp = await authedHrisFetch('/hris/api/amendments/', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ target_kind: 'profile', target_id: selected.pid, changes: profChanges, reason: reason.trim() }),
        })
        const dp = await rp.json().catch(() => ({}))
        if (!rp.ok) { setMsg({ ok: false, text: dp.detail || `HTTP ${rp.status}` }); return }
        approver = dp.approver_email || approver
      }
      if (managerChanged && selected?.pid) {
        const r2 = await authedHrisFetch('/hris/api/amendments/', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            target_kind: 'profile', target_id: selected.pid,
            changes: { manager_id: managerVal },
            reason: reason.trim() || 'Set reporting line / leave approver',
          }),
        })
        const d2 = await r2.json().catch(() => ({}))
        if (!r2.ok) { setMsg({ ok: false, text: d2.detail || `HTTP ${r2.status}` }); return }
        approver = d2.approver_email || approver
      }
      setMsg({ ok: true, text: `Submitted for approval by ${approver}. The record is unchanged until approved.` })
      setReason(''); refreshPending()
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : 'Network error' })
    } finally { setBusy(false) }
  }

  async function decide(id: string, action: 'approve' | 'reject') {
    setBusy(true); setMsg(null)
    try {
      const r = await authedHrisFetch(`/hris/api/amendments/${id}/${action}/`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) { setMsg({ ok: false, text: d.detail || `HTTP ${r.status}` }); return }
      setMsg({ ok: true, text: `Amendment ${action === 'approve' ? 'approved & applied' : 'rejected'}.` })
      refreshPending()
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : 'Network error' })
    } finally { setBusy(false) }
  }

  async function reverse(id: string) {
    setBusy(true); setMsg(null)
    try {
      const r = await authedHrisFetch(`/hris/api/amendments/${id}/reverse/`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) { setMsg({ ok: false, text: d.detail || `HTTP ${r.status}` }); return }
      setMsg({
        ok: true,
        text: d.status === 'approved'
          ? 'Reversed — the record is back to its previous value.'
          : 'Reversal submitted for approval. The record does not change until it is approved.',
      })
      refreshPending()
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : 'Network error' })
    } finally { setBusy(false) }
  }

  if (allowed !== true) return null
  const dark = themeKey === 'fun'
  const card = dark ? 'bg-[#0D1B2A] border-white/10' : 'bg-white border-slate-200'
  const inp = dark ? 'bg-white/5 border-white/10 text-white' : 'bg-white border-slate-300 text-slate-900'

  return (
    <div className="min-h-screen">
      <TopBar />
      <div className="max-w-5xl mx-auto px-4 py-6">
        <Link href="/hris" className="inline-flex items-center gap-1 text-sm text-slate-400 hover:text-[#F4A623] mb-4">
          <ChevronLeft size={16} /> HRIS
        </Link>
        <div className="flex items-center gap-2 mb-1">
          <UserCog className="text-[#F4A623]" size={22} />
          <h1 className="text-2xl font-semibold">HRIS Amendments</h1>
        </div>
        <p className="text-sm text-slate-400 mb-6">
          Every change is dual-approved. Your submission is reviewed by Unami (the CFO when Unami is the requestor)
          before it touches the live record. You and the approver both get an email.
        </p>

        {msg && (
          <div className={`mb-4 flex items-start gap-2 rounded-lg px-3 py-2 text-sm ${msg.ok ? 'bg-emerald-500/10 text-emerald-400' : 'bg-red-500/10 text-red-400'}`}>
            {msg.ok ? <CheckCircle2 size={16} /> : <AlertCircle size={16} />}<span>{msg.text}</span>
          </div>
        )}

        {/* Submit form */}
        <div className={`rounded-xl border p-5 mb-8 ${card}`}>
          <h2 className="font-semibold mb-3 flex items-center gap-2"><Send size={16} className="text-[#F4A623]" /> Propose an amendment</h2>
          <label className="block text-xs text-slate-400 mb-1">Employee</label>
          <EmployeeSearchPicker employees={employees} value={eid} onChange={setEid} dark={dark} />

          {selected && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {FIELDS.map(f => (
                <div key={f.key}>
                  <label className="block text-xs text-slate-400 mb-1">{f.label}</label>
                  {f.combobox ? (
                    <>
                      <input
                        list="hris-managers"
                        value={vals[f.key] ?? ''}
                        onChange={e => setVals(v => ({ ...v, [f.key]: e.target.value }))}
                        placeholder="Type or pick a name — leave blank for none"
                        className={`w-full rounded-lg border px-3 py-2 ${inp}`}
                      />
                      <datalist id="hris-managers">
                        {employees.filter(e => e.eid && e.eid !== eid).map(e => (
                          <option key={e.eid} value={e.nm}>{e.ps} · {e.dp}</option>
                        ))}
                      </datalist>
                    </>
                  ) : (
                    <input
                      value={vals[f.key] ?? ''}
                      onChange={e => setVals(v => ({ ...v, [f.key]: e.target.value }))}
                      placeholder={f.from ? '' : '(leave blank to keep)'}
                      className={`w-full rounded-lg border px-3 py-2 ${inp}`}
                    />
                  )}
                </div>
              ))}
              <div className="sm:col-span-2">
                <label className="block text-xs text-slate-400 mb-1">Reason for change</label>
                <input value={reason} onChange={e => setReason(e.target.value)}
                       placeholder="e.g. promotion, correction, bank update"
                       className={`w-full rounded-lg border px-3 py-2 ${inp}`} />
              </div>
              <div className="sm:col-span-2">
                <button onClick={submit} disabled={busy}
                        className="inline-flex items-center gap-2 rounded-lg bg-[#F4A623] px-4 py-2 font-medium text-[#0D1B2A] disabled:opacity-50">
                  {busy ? <Loader2 size={16} className="animate-spin" /> : <Send size={16} />} Submit for approval
                </button>
              </div>
            </div>
          )}
        </div>

        {/* Approval queue */}
        <div className={`rounded-xl border p-5 ${card}`}>
          <h2 className="font-semibold mb-3 flex items-center gap-2"><ShieldCheck size={16} className="text-[#F4A623]" /> Pending approvals</h2>
          {loadingPending ? (
            <div className="flex items-center gap-2 text-slate-400 text-sm"><Loader2 size={16} className="animate-spin" /> Loading…</div>
          ) : pending.length === 0 ? (
            <p className="text-sm text-slate-400">No amendments awaiting approval.</p>
          ) : (
            <div className="space-y-3">
              {pending.map(a => (
                <div key={a.id} className={`rounded-lg border p-3 ${dark ? 'border-white/10' : 'border-slate-200'}`}>
                  <div className="flex items-center justify-between mb-2">
                    <div className="font-medium flex items-center gap-2"><Clock size={14} className="text-amber-400" /> {a.target_label}</div>
                    <div className="text-xs text-slate-400">by {a.maker_email}</div>
                  </div>
                  <table className="text-xs w-full mb-2">
                    <tbody>
                      {Object.values(a.changes).map((c, i) => (
                        <tr key={i}>
                          <td className="pr-3 text-slate-400">{c.label}</td>
                          <td className="pr-2 text-slate-400 line-through">{c.old || '—'}</td>
                          <td className="font-medium text-[#F4A623]">{c.new || '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {a.reason && <p className="text-xs text-slate-400 mb-2 italic">Reason: {a.reason}</p>}
                  {/* BUG 6f53096f (Oprah 2026-06-26): segregation of duties — the
                      maker must not see Approve/Reject on their OWN amendment. The
                      backend already blocks self-approval; this hides the buttons so
                      the submitter isn't offered an action that would only error. */}
                  {a.is_own ? (
                    <p className="text-xs text-slate-400 italic flex items-center gap-1">
                      <ShieldCheck size={14} className="text-[#F4A623]" />
                      You submitted this — your approver must action it.
                    </p>
                  ) : (
                    <div className="flex gap-2">
                      <button onClick={() => decide(a.id, 'approve')} disabled={busy}
                              className="inline-flex items-center gap-1 rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50">
                        <CheckCircle2 size={14} /> Approve
                      </button>
                      <button onClick={() => decide(a.id, 'reject')} disabled={busy}
                              className="inline-flex items-center gap-1 rounded-md bg-red-600 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50">
                        <XCircle size={14} /> Reject
                      </button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Applied — with a way back. An amendment that has been applied could
            only be undone by hand before (Manus retest P2, 2026-08-25). Reverse
            proposes a compensating amendment through the SAME approval, so one
            person cannot unwind a dual-approved change on their own. */}
        <div className={`rounded-xl border p-5 ${card}`}>
          <h2 className="font-semibold mb-1 flex items-center gap-2">
            <CheckCircle2 size={16} className="text-emerald-500" /> Applied recently
          </h2>
          <p className="text-xs text-slate-400 mb-3">
            Changed something by mistake? Reverse puts the field back to what it was —
            as a new amendment that your approver signs off, the same as any other change.
          </p>
          {applied.length === 0 ? (
            <p className="text-sm text-slate-400">Nothing applied yet.</p>
          ) : (
            <div className="space-y-3">
              {applied.map(a => (
                <div key={a.id} className={`rounded-lg border p-3 ${dark ? 'border-white/10' : 'border-slate-200'}`}>
                  <div className="flex items-center justify-between mb-2">
                    <div className="font-medium">{a.target_label}</div>
                    <div className="text-xs text-slate-400">
                      by {a.maker_email}{a.decided_at ? ` · ${a.decided_at.slice(0, 10)}` : ''}
                    </div>
                  </div>
                  <table className="text-xs w-full mb-2">
                    <tbody>
                      {Object.values(a.changes).map((c, i) => (
                        <tr key={i}>
                          <td className="pr-3 text-slate-400">{c.label}</td>
                          <td className="pr-2 text-slate-400 line-through">{c.old || '—'}</td>
                          <td className="font-medium text-[#F4A623]">{c.new || '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {a.reversal_of ? (
                    <p className="text-xs text-slate-400 italic">This was itself a reversal.</p>
                  ) : a.has_reversal ? (
                    <p className="text-xs text-slate-400 italic flex items-center gap-1">
                      <Undo2 size={14} className="text-[#F4A623]" /> A reversal for this already exists.
                    </p>
                  ) : (
                    <button onClick={() => reverse(a.id)} disabled={busy}
                            className="inline-flex items-center gap-1 rounded-md border border-amber-500/60 px-3 py-1.5 text-xs font-medium text-amber-500 hover:bg-amber-500/10 disabled:opacity-50">
                      <Undo2 size={14} /> Reverse this change
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
