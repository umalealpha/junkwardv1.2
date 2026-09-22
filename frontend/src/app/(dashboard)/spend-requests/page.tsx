'use client'

/**
 * /spend-requests — pre-spend approval requests (CFO 2026-07-13). Staff submit a
 * request (event / golf / party / training / travel / travel-advance) with a
 * budget attached; DeepSeek reads the budget and shows a summary; the CFO / EXCO
 * approve or reject. One page: everyone submits + sees their own; approvers see
 * all and get Approve / Reject. Mirrors the /refunds pattern.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { apiFetch, apiFetchRaw, API_BASE, saveBlob } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { CalendarClock, Upload, CheckCircle2, XCircle, Paperclip, Sparkles, AlertTriangle } from 'lucide-react'

interface SR {
  id: string; request_type: string; request_type_display: string; title: string
  amount: string; event_date: string | null; within_budget: boolean
  status: string; status_display: string; requester: string
  ai_status: string; ai_summary: string; ai_extracted_total: string | null; ai_flags: string
  has_attachment: boolean; created_at: string
  training_provider: string; bqa_accredited: boolean | null
  bqa_recovery_status: string; bqa_recovery_display: string; levy_rate_pct: string
  actual_spent: string | null; actual_reference: string; variance: string | null
}
interface Levy { ceiling: string; used_recoverable: string; recovered: string; remaining_claimable: string; fy_start: string }

const TYPES: [string, string][] = [
  ['event', 'Event'], ['golf_day', 'Golf day'], ['entertainment', 'Custom entertainment'],
  ['staff_party', 'Staff birthday party'], ['broker_party', 'Broker party'],
  ['broker_birthday', 'Broker birthday'], ['training', 'Training'], ['travel', 'Travel'],
  ['travel_advance', 'Travel advance'], ['other', 'Other'],
]
const BADGE: Record<string, string> = {
  draft: 'bg-muted text-foreground', submitted: 'bg-blue-100 text-blue-700',
  approved: 'bg-emerald-100 text-emerald-700', rejected: 'bg-red-100 text-red-700',
}

export default function SpendRequestsPage() {
  const [rows, setRows] = useState<SR[]>([])
  const [canApprove, setCanApprove] = useState(false)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)

  const [rtype, setRtype] = useState('event')
  const [title, setTitle] = useState('')
  const [amount, setAmount] = useState('')
  const [eventDate, setEventDate] = useState('')
  const [within, setWithin] = useState('true')
  const [budgetNote, setBudgetNote] = useState('')
  const [description, setDescription] = useState('')
  const [trainingProvider, setTrainingProvider] = useState('')
  const [bqaAccredited, setBqaAccredited] = useState('')   // '' | 'true' | 'false'
  const [levy, setLevy] = useState<Levy | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const load = useCallback(async () => {
    try {
      const d = await apiFetch<{ can_approve: boolean; requests: SR[] }>('/spend-requests/')
      setRows(d.requests || []); setCanApprove(!!d.can_approve)
    } catch (e) {
      // Never fail silently — a swallowed error looks like "0 requests" and hides
      // items that are actually waiting for approval (CFO 2026-07-13).
      setMsg(e instanceof Error ? e.message.replace(/^HTTP \d+: /, '') : 'Could not load requests — try refreshing.')
    }
    apiFetch<Levy>('/spend-requests/levy-tracker/').then(setLevy).catch(() => {})
  }, [])
  useEffect(() => { load() }, [load])

  async function submit() {
    setBusy(true); setMsg(null)
    try {
      const fd = new FormData()
      fd.append('request_type', rtype); fd.append('title', title)
      fd.append('amount', amount); fd.append('event_date', eventDate)
      fd.append('within_budget', within); fd.append('budget_note', budgetNote)
      fd.append('description', description)
      if (rtype === 'training') {
        fd.append('training_provider', trainingProvider)
        if (bqaAccredited) fd.append('bqa_accredited', bqaAccredited)
      }
      if (fileRef.current?.files?.[0]) fd.append('attachment', fileRef.current.files[0])
      await apiFetch('/spend-requests/', { method: 'POST', body: fd })
      setMsg('Submitted — DeepSeek is reading the budget; it shows below.')
      setTitle(''); setAmount(''); setEventDate(''); setBudgetNote(''); setDescription('')
      setTrainingProvider(''); setBqaAccredited('')
      if (fileRef.current) fileRef.current.value = ''
      load()
    } catch (e) { setMsg(e instanceof Error ? e.message.replace(/^HTTP \d+: /, '') : 'Failed') }
    finally { setBusy(false) }
  }

  async function decide(id: string, decision: 'approve' | 'reject') {
    let notes = ''
    if (decision === 'reject') { notes = window.prompt('Reason (sent to the requester):') || ''; if (!notes) return }
    setBusy(true)
    try {
      await apiFetch(`/spend-requests/${id}/decide/`, { method: 'POST', body: JSON.stringify({ decision, notes }) })
      load()
    } catch (e) { setMsg(e instanceof Error ? e.message.replace(/^HTTP \d+: /, '') : 'Failed') }
    finally { setBusy(false) }
  }

  async function bqaClaim(id: string, status: 'claimed' | 'recovered') {
    setBusy(true)
    try {
      await apiFetch(`/spend-requests/${id}/bqa-claim/`, { method: 'POST', body: JSON.stringify({ status }) })
      load()
    } catch (e) { setMsg(e instanceof Error ? e.message.replace(/^HTTP \d+: /, '') : 'Failed') }
    finally { setBusy(false) }
  }

  async function recordActual(id: string) {
    const amt = window.prompt('Actual amount spent (BWP):'); if (!amt) return
    const ref = window.prompt('PO / payment reference (optional):') || ''
    setBusy(true)
    try {
      await apiFetch(`/spend-requests/${id}/actual/`, { method: 'POST', body: JSON.stringify({ actual_spent: amt, actual_reference: ref }) })
      load()
    } catch (e) { setMsg(e instanceof Error ? e.message.replace(/^HTTP \d+: /, '') : 'Failed') }
    finally { setBusy(false) }
  }

  async function claimPack(id: string) {
    try {
      const p = await apiFetch<Record<string, unknown>>(`/spend-requests/${id}/claim-pack/`)
      const w = window.open('', '_blank'); if (!w) return
      const detail: [string, string][] = [
        ['Provider', String(p.training_provider || '—')],
        ['BQA-accredited', p.bqa_accredited ? 'Yes' : p.bqa_accredited === false ? 'No' : '—'],
        ['Recovery status', String(p.recovery_status || '')],
        ['Amount', 'BWP ' + String(p.amount)],
        ['Event date', String(p.event_date || '—')],
        ['Requested by', String(p.requester || '')],
        ['Approved by', String(p.approver || '—')],
      ]
      const rowsHtml = detail.map(([k, v]) => `<tr><td style="padding:4px 12px 4px 0;color:#555">${k}</td><td style="padding:4px 0"><b>${v}</b></td></tr>`).join('')
      const checklist = ((p.checklist as string[]) || []).map(c => `<li>${c}</li>`).join('')
      w.document.write(`<title>${p.claim_title}</title><div style="font-family:Georgia,serif;max-width:640px;margin:32px auto;color:#0D1B2A"><h2 style="border-bottom:3px solid #F4A623;padding-bottom:6px">${p.claim_title}</h2><table>${rowsHtml}</table><p>${p.description || ''}</p><h3>Attach to the BQA claim:</h3><ul>${checklist}</ul><p style="color:#555;font-size:12px">Generated from Omni — the pre-spend approval is on file.</p></div>`)
      w.document.close()
    } catch (e) { setMsg(e instanceof Error ? e.message.replace(/^HTTP \d+: /, '') : 'Failed') }
  }

  async function openFile(id: string) {
    try {
      // Prefix API_BASE — apiFetchRaw doesn't add /api/v1, so a bare path hits
      // the Next frontend and returns HTML instead of the file. (2026-07-16)
      const res = await apiFetchRaw(`${API_BASE}/spend-requests/${id}/file/`)
      if (!res.ok) { setMsg('Could not open the attachment.'); return }
      // Save via a download anchor, NOT window.open(blob) — the latter silently
      // fails inside the OmniDesktop wrapper (2026-07-28). See api.saveBlob.
      saveBlob(await res.blob(), `attachment-${id}`)
    } catch { setMsg('Could not open the attachment.') }
  }

  const words = description.trim() ? description.trim().split(/\s+/).length : 0
  const pendingApproval = rows.filter(r => r.status === 'submitted')

  return (
    <div>
      <TopBar />
      <div className="p-6 max-w-5xl mx-auto space-y-6">
        <div>
          <div className="flex items-center gap-2 text-[10.5px] font-semibold uppercase tracking-[0.11em] text-[#3F58CC]">
            <span className="w-[5px] h-[5px] rounded-full bg-[#4F6BED]" /> Spend &amp; payments
          </div>
          <h1 className="mt-1.5 text-[25px] leading-tight font-bold tracking-[-0.03em] flex items-center gap-2.5">
            <CalendarClock className="h-6 w-6 text-[#F4A623]" /> Spend &amp; Event Requests
          </h1>
          <p className="text-sm text-muted-foreground mt-1.5">
            Ask for approval <b>before</b> you spend — events, golf days, parties, training, travel, travel advances.
            Attach your budget; DeepSeek reads it for the approver.
          </p>
        </div>
        {msg && <div className="text-sm rounded-md bg-muted/60 p-3">{msg}</div>}

        {/* Waiting for YOUR approval — top of the page so an approver arriving from
            "My Approvals" acts immediately instead of landing on the submit form. */}
        {canApprove && (
          <Card><CardContent className="p-5">
            <div className="font-medium mb-2 flex items-center gap-2">
              <CheckCircle2 className="h-4 w-4 text-[#F4A623]" /> Waiting for your approval ({pendingApproval.length})
            </div>
            {pendingApproval.length === 0 ? (
              <p className="text-sm text-muted-foreground">Nothing waiting — you&apos;re all caught up.</p>
            ) : (
              <div className="space-y-2">
                {pendingApproval.map(r => (
                  <div key={r.id} className="border rounded-lg p-3 flex flex-wrap items-center gap-x-3 gap-y-1.5">
                    <span className="text-[10px] uppercase tracking-wide font-semibold px-1.5 py-0.5 rounded bg-[#F4A623]/15 text-[#B04E00]">{r.request_type_display}</span>
                    <span className="font-medium flex-1 min-w-[160px]">{r.title} — BWP {r.amount}</span>
                    <span className="text-xs text-muted-foreground">{r.requester}</span>
                    <span className={'text-xs px-1.5 py-0.5 rounded ' + (r.within_budget ? 'bg-emerald-100 text-emerald-700' : 'bg-amber-100 text-amber-700')}>
                      {r.within_budget ? 'within budget' : 'out of budget'}
                    </span>
                    {r.has_attachment && <button onClick={() => openFile(r.id)} className="text-xs text-blue-600 underline flex items-center gap-0.5"><Paperclip className="h-3 w-3" />budget</button>}
                    <Button size="sm" disabled={busy} onClick={() => decide(r.id, 'approve')}><CheckCircle2 className="h-4 w-4 mr-1" />Approve</Button>
                    <Button size="sm" variant="outline" disabled={busy} onClick={() => decide(r.id, 'reject')}><XCircle className="h-4 w-4 mr-1" />Reject</Button>
                  </div>
                ))}
              </div>
            )}
          </CardContent></Card>
        )}

        {/* Submit */}
        <Card><CardContent className="p-5 space-y-3">
          <div className="font-medium">New request</div>
          <div className="grid sm:grid-cols-3 gap-3">
            <select value={rtype} onChange={e => setRtype(e.target.value)} className="px-3 py-2 text-sm border rounded-md bg-background">
              {TYPES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
            <input value={amount} onChange={e => setAmount(e.target.value)} placeholder="Amount (BWP)" className="px-3 py-2 text-sm border rounded-md bg-background" />
            <input type="date" value={eventDate} onChange={e => setEventDate(e.target.value)} className="px-3 py-2 text-sm border rounded-md bg-background" />
          </div>
          <input value={title} onChange={e => setTitle(e.target.value)} placeholder="Title (e.g. Baker Tilly golf — Hole 14 sponsorship)" className="w-full px-3 py-2 text-sm border rounded-md bg-background" />
          <div className="grid sm:grid-cols-3 gap-3">
            <select value={within} onChange={e => setWithin(e.target.value)} className="px-3 py-2 text-sm border rounded-md bg-background">
              <option value="true">Within budget</option>
              <option value="false">Out of budget</option>
            </select>
            <input value={budgetNote} onChange={e => setBudgetNote(e.target.value)} placeholder="Budget note (which budget / why out)" className="sm:col-span-2 px-3 py-2 text-sm border rounded-md bg-background" />
          </div>
          {/* Training only — BQA accreditation decides if the levy is recoverable */}
          {rtype === 'training' && (
            <div className="grid sm:grid-cols-2 gap-3 rounded-md border border-[#F4A623]/30 bg-[#F4A623]/5 p-3">
              <input value={trainingProvider} onChange={e => setTrainingProvider(e.target.value)} placeholder="Training provider / organisation" className="px-3 py-2 text-sm border rounded-md bg-background" />
              <select value={bqaAccredited} onChange={e => setBqaAccredited(e.target.value)} className="px-3 py-2 text-sm border rounded-md bg-background">
                <option value="">Is the provider BQA-accredited?</option>
                <option value="true">Yes — BQA-accredited (levy recoverable)</option>
                <option value="false">No — not accredited (out of pocket)</option>
              </select>
              <p className="sm:col-span-2 text-[11px] text-muted-foreground">
                The training levy is <b>0.5% of revenue (incl VAT)</b> — about <b>BWP 625,000 a year</b> — and is recovered from the <b>BQA</b>, but only for BQA-accredited providers. Trainings with non-accredited organisations are not recoverable.
              </p>
            </div>
          )}
          <textarea value={description} onChange={e => setDescription(e.target.value)} rows={3}
            placeholder="The business case — what it's for, why it's worth it."
            className="w-full px-3 py-2 text-sm border rounded-md bg-background" />
          <div className="flex flex-wrap items-center gap-3">
            <label className="text-xs text-muted-foreground flex items-center gap-1.5">
              <Paperclip className="h-3.5 w-3.5" /> Budget document:
              <input ref={fileRef} type="file" accept=".xlsx,.xlsm,.csv,.pdf,image/*" className="text-sm" />
            </label>
            <span className={'text-xs ' + (words >= 20 ? 'text-emerald-600' : 'text-muted-foreground')}>{words} words</span>
            <Button size="sm" disabled={busy || !title || !amount} onClick={submit}>
              <Upload className="h-4 w-4 mr-1" /> Submit request
            </Button>
          </div>
        </CardContent></Card>

        {/* Training-levy recovery tracker (#1) — annual claimable vs used */}
        {levy && (
          <Card><CardContent className="p-5">
            <div className="flex items-center gap-2 mb-2">
              <Sparkles className="h-4 w-4 text-[#F4A623]" />
              <div className="font-medium">Training levy — BQA recovery (this financial year)</div>
            </div>
            <div className="grid grid-cols-3 gap-3 text-center">
              <div className="rounded-md bg-muted/50 p-3">
                <div className="text-lg font-semibold">BWP {Number(levy.ceiling).toLocaleString()}</div>
                <div className="text-[11px] text-muted-foreground">Claimable / year (0.5% of revenue)</div>
              </div>
              <div className="rounded-md bg-muted/50 p-3">
                <div className="text-lg font-semibold">BWP {Number(levy.used_recoverable).toLocaleString()}</div>
                <div className="text-[11px] text-muted-foreground">Recoverable training approved</div>
              </div>
              <div className="rounded-md bg-emerald-50 p-3">
                <div className="text-lg font-semibold text-emerald-700">BWP {Number(levy.remaining_claimable).toLocaleString()}</div>
                <div className="text-[11px] text-muted-foreground">Still claimable</div>
              </div>
            </div>
            <div className="mt-3 h-2 rounded-full bg-muted overflow-hidden">
              <div className="h-full bg-[#F4A623]" style={{ width: Math.min(100, Math.round(100 * Number(levy.used_recoverable) / Math.max(1, Number(levy.ceiling)))) + '%' }} />
            </div>
            <p className="text-[11px] text-muted-foreground mt-2">Use it or lose it — recoverable only for BQA-accredited training. Recovered so far: BWP {Number(levy.recovered).toLocaleString()}.</p>
          </CardContent></Card>
        )}

        {/* List */}
        <Card><CardContent className="p-5">
          <div className="font-medium mb-2">{canApprove ? 'All requests' : 'My requests'} ({rows.length})</div>
          {rows.length === 0 ? <p className="text-sm text-muted-foreground">No requests yet.</p> :
            <div className="space-y-3">
              {rows.map(r => (
                <div key={r.id} className="border rounded-lg p-3">
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                    <span className="text-[10px] uppercase tracking-wide font-semibold px-1.5 py-0.5 rounded bg-[#F4A623]/15 text-[#B04E00]">{r.request_type_display}</span>
                    <span className="font-medium flex-1 min-w-[160px]">{r.title} — BWP {r.amount}</span>
                    <span className="text-xs text-muted-foreground">{r.requester}</span>
                    <span className={'text-xs px-1.5 py-0.5 rounded ' + (r.within_budget ? 'bg-emerald-100 text-emerald-700' : 'bg-amber-100 text-amber-700')}>
                      {r.within_budget ? 'within budget' : 'out of budget'}
                    </span>
                    {r.request_type === 'training' && r.bqa_recovery_status !== 'n/a' && (
                      <span className={'text-xs px-1.5 py-0.5 rounded ' + (r.bqa_recovery_status === 'not_recoverable' ? 'bg-red-100 text-red-700' : 'bg-emerald-100 text-emerald-700')}>
                        BQA: {r.bqa_recovery_display}
                      </span>
                    )}
                    <span className={'text-xs px-2 py-0.5 rounded ' + (BADGE[r.status] || '')}>{r.status_display}</span>
                    {r.has_attachment && <button onClick={() => openFile(r.id)} className="text-xs text-blue-600 underline flex items-center gap-0.5"><Paperclip className="h-3 w-3" />budget</button>}
                  </div>
                  {/* DeepSeek read of the budget */}
                  {(r.ai_summary || r.ai_status !== 'done') && (
                    <div className="mt-2 text-xs rounded-md bg-muted/50 p-2.5">
                      <div className="flex items-center gap-1.5 font-semibold text-[#0D1B2A] mb-1">
                        <Sparkles className="h-3.5 w-3.5 text-[#F4A623]" /> DeepSeek budget read
                        {r.ai_extracted_total && <span className="ml-1 font-normal text-muted-foreground">· total in doc: BWP {r.ai_extracted_total}</span>}
                      </div>
                      <p className="text-foreground/90">{r.ai_summary || (r.ai_status === 'pending' ? 'Analysing…' : '—')}</p>
                      {r.ai_flags && <p className="mt-1 flex items-start gap-1 text-amber-700"><AlertTriangle className="h-3 w-3 mt-0.5 shrink-0" />{r.ai_flags}</p>}
                    </div>
                  )}
                  {canApprove && r.status === 'submitted' && (
                    <div className="mt-2 flex gap-2">
                      <Button size="sm" disabled={busy} onClick={() => decide(r.id, 'approve')}><CheckCircle2 className="h-4 w-4 mr-1" />Approve</Button>
                      <Button size="sm" variant="outline" disabled={busy} onClick={() => decide(r.id, 'reject')}><XCircle className="h-4 w-4 mr-1" />Reject</Button>
                    </div>
                  )}
                  {/* Approved: record actual spend (#4) + BQA claim lifecycle/pack (#2) */}
                  {canApprove && r.status === 'approved' && (
                    <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
                      {r.actual_spent ? (
                        <span className="text-muted-foreground">
                          Actual: <b>BWP {r.actual_spent}</b>{r.actual_reference ? ` (${r.actual_reference})` : ''}
                          {r.variance && <span className={Number(r.variance) > 0 ? 'text-red-600' : 'text-emerald-600'}> · variance {Number(r.variance) > 0 ? '+' : ''}{r.variance}</span>}
                        </span>
                      ) : (
                        <Button size="sm" variant="outline" disabled={busy} onClick={() => recordActual(r.id)}>Record actual spend</Button>
                      )}
                      {r.request_type === 'training' && ['recoverable', 'claimed', 'recovered'].includes(r.bqa_recovery_status) && (
                        <>
                          <Button size="sm" variant="outline" disabled={busy} onClick={() => claimPack(r.id)}>BQA claim pack</Button>
                          {r.bqa_recovery_status === 'recoverable' && <Button size="sm" variant="outline" disabled={busy} onClick={() => bqaClaim(r.id, 'claimed')}>Mark claim submitted</Button>}
                          {r.bqa_recovery_status === 'claimed' && <Button size="sm" variant="outline" disabled={busy} onClick={() => bqaClaim(r.id, 'recovered')}>Mark recovered</Button>}
                        </>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>}
        </CardContent></Card>
      </div>
    </div>
  )
}
