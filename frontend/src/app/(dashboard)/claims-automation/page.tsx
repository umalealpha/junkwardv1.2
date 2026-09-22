'use client'

// Claims Automation (CFO plan, 19-Sep-2026). Graphite fires the claim events;
// Omni reads the claim, drafts the purchase orders, the Agreement of Loss and
// any repudiation. Nothing on this screen pays anything, and no letter reaches
// a client until a person presses Approve here.

import { Suspense, useCallback, useEffect, useMemo, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { apiFetch, apiFetchBinary, getToken } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Button } from '@/components/ui/button'
import { AlertCircle, CheckCircle2, FileText, ShieldAlert, Sparkles, XCircle } from 'lucide-react'

type Letter = {
  id: string; kind: 'aol' | 'repudiation'; kind_label: string; status: string; status_label: string
  figures: { lines?: { label: string; amount: string }[]; net_display?: string; note?: string }
  reasons: string[]; insured_name: string; has_email: boolean; decided_by: string
  decided_at: string | null; decision_note: string; sent_to: string; created_at: string
  can_decide: boolean; wording_approved: boolean
}
type Handover = {
  items: { key: string; label: string; answer: string | null }[]
  complete: boolean; overridden: boolean; override_reason: string; note: string
  declared_by: string; declared_at: string | null
  may_settle: boolean; blocked_because: string
  veritas_charge: { amount: string; settlement: string; status: string; note: string; invoice: string | null } | null
}
type Case = {
  claim_ref: string; claim_type: string; stage: string; stage_label: string
  triage: '' | 'straight_through' | 'exception'; triage_reasons: string[]; premium_light: string
  insured_name: string; ai_summary: string; ai_next_step: string
  flags: { code: string; label: string; detail: string }[]
  updated_at: string; po_assessment_id: string | null; letters: Letter[]; handover?: Handover
}
type ListResp = { results: Case[]; awaiting_letters: number; wording_approved: boolean
  counts: { total: number; straight_through: number; exception: number } }

const LIGHT: Record<string, string> = { green: '#16A34A', amber: '#D97706', red: '#DC2626' }

function money(v?: string) {
  const n = parseFloat((v || '').replace(/,/g, ''))
  if (Number.isNaN(n)) return v || ''
  return new Intl.NumberFormat('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n)
}

function TriageChip({ t }: { t: Case['triage'] }) {
  if (!t) return <span className="text-xs text-[#9CA3AF]">not read yet</span>
  const ok = t === 'straight_through'
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold ${ok ? 'bg-[#DCFCE7] text-[#166534]' : 'bg-[#FEF3C7] text-[#92400E]'}`}>
      {ok ? <CheckCircle2 className="w-3 h-3" /> : <ShieldAlert className="w-3 h-3" />}
      {ok ? 'Straight through' : 'Needs a person'}
    </span>
  )
}

function LetterCard({ letter, claimRef, handover, onDone }:
  { letter: Letter; claimRef: string; handover?: Handover; onDone: () => void }) {
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const [declining, setDeclining] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [override, setOverride] = useState('')
  const [needsOverride, setNeedsOverride] = useState(false)
  const isAol = letter.kind === 'aol'
  const blocked = isAol && handover && !handover.may_settle

  const view = async () => {
    const r = await apiFetchBinary(`/claims-automation/letters/${letter.id}/pdf/`)
    const blob = await r.blob()
    window.open(URL.createObjectURL(blob), '_blank', 'noopener')
  }
  const act = async (path: 'approve' | 'decline') => {
    setBusy(true); setMsg(null)
    try {
      const out = await apiFetch<{ outcome: string }>(`/claims-automation/letters/${letter.id}/${path}/`, {
        method: 'POST',
        body: JSON.stringify(path === 'decline' ? { note } : (override ? { override_reason: override } : {})),
      })
      setMsg(out.outcome); setNeedsOverride(false); onDone()
    } catch (e) {
      const text = e instanceof Error ? e.message : 'Could not save'
      if (/yard|possession|confirm/i.test(text)) setNeedsOverride(true)
      setMsg(text)
    } finally { setBusy(false) }
  }

  return (
    <article className="bg-white border border-[#E5E7EB] rounded-xl p-5 shadow-[0_1px_2px_rgba(13,27,42,0.04)]">
      <header className="flex items-start justify-between gap-4">
        <div>
          <p className={`text-[11px] font-bold uppercase tracking-[0.08em] ${isAol ? 'text-[#1D3270]' : 'text-[#B91C1C]'}`}>
            {letter.kind_label}
          </p>
          <h3 className="text-lg font-semibold text-[#0D1B2A] mt-1">{claimRef} · {letter.insured_name}</h3>
          <p className="text-xs text-[#6B7280] mt-0.5">Prepared {new Date(letter.created_at).toLocaleString('en-GB', { timeZone: 'Africa/Gaborone' })}</p>
        </div>
        <Button variant="outline" size="sm" leftIcon={<FileText className="w-3.5 h-3.5" />} onClick={view}>View letter</Button>
      </header>

      {isAol && letter.figures?.lines && (
        <table className="mt-4 w-full max-w-md text-sm tabular-nums">
          <tbody>
            {letter.figures.lines.map(l => (
              <tr key={l.label}><td className="py-1 text-[#374151]">{l.label}</td>
                <td className="py-1 text-right">{money(l.amount)}</td></tr>
            ))}
            <tr className="border-t border-[#E5E7EB]"><td className="pt-2 font-semibold">Net settlement</td>
              <td className="pt-2 text-right font-bold text-[#0D1B2A]">P {letter.figures.net_display}</td></tr>
          </tbody>
        </table>
      )}
      {!isAol && (
        <ul className="mt-4 space-y-2 text-sm text-[#374151] list-disc pl-5">
          {letter.reasons.map(r => <li key={r}>{r}</li>)}
        </ul>
      )}
      {letter.figures?.note && <p className="mt-3 text-sm text-[#B45309]">{letter.figures.note}</p>}

      {isAol && handover && (
        <div className={`mt-4 rounded-lg border p-3 text-sm ${handover.may_settle ? 'border-[#BBF7D0] bg-[#F0FDF4]' : 'border-[#FED7AA] bg-[#FFF7ED]'}`}>
          <p className="font-semibold text-[#0D1B2A]">
            {handover.overridden ? 'Possession overridden' : handover.may_settle ? 'Veritas holds the vehicle' : 'Waiting for the salvage yard'}
          </p>
          <ul className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-[#374151]">
            {handover.items.map(i => (
              <li key={i.key}>{i.label}: <b>{i.answer === 'yes' ? 'yes' : i.answer === 'na' ? 'not applicable' : i.answer === 'no' ? 'no' : '—'}</b></li>
            ))}
          </ul>
          {handover.overridden && <p className="mt-1 text-xs text-[#92400E]">Reason: {handover.override_reason}</p>}
          {handover.veritas_charge && (
            <p className="mt-2 text-xs text-[#1D3270]">
              Veritas charge P {handover.veritas_charge.amount} (20% of {handover.veritas_charge.settlement})
              {' · '}{handover.veritas_charge.status}
              {handover.veritas_charge.invoice ? ` · ${handover.veritas_charge.invoice}` : ''}
            </p>
          )}
        </div>
      )}

      {letter.status === 'awaiting' ? (
        letter.can_decide ? (
          <div className="mt-5 flex flex-wrap items-center gap-2">
            <Button variant="accent" size="sm" disabled={busy} onClick={() => act('approve')}>
              {letter.wording_approved && letter.has_email ? 'Approve and send to client' : 'Approve'}
            </Button>
            {!declining ? (
              <Button variant="outline" size="sm" disabled={busy} onClick={() => setDeclining(true)}>Decline</Button>
            ) : (
              <span className="flex items-center gap-2">
                <input value={note} onChange={e => setNote(e.target.value)} placeholder="Reason"
                  className="h-8 w-64 rounded-md border border-[#D1D5DB] px-2 text-sm focus:outline-none focus:ring-2 focus:ring-[#F47C20]/40" />
                <Button variant="outline" size="sm" disabled={busy || !note.trim()} onClick={() => act('decline')}>Confirm decline</Button>
              </span>
            )}
            {(needsOverride || (blocked && letter.can_decide)) && (
              <span className="flex w-full items-center gap-2">
                <input value={override} onChange={e => setOverride(e.target.value)}
                  placeholder="Reason for settling without the vehicle (Claims Manager only)"
                  className="h-8 flex-1 min-w-[18rem] rounded-md border border-[#FDBA74] px-2 text-sm focus:outline-none focus:ring-2 focus:ring-[#F47C20]/40" />
              </span>
            )}
            {!letter.wording_approved && (
              <span className="text-xs text-[#6B7280]">Letter wording awaits the Claims Manager&apos;s sign-off, so approval will not email the client yet.</span>
            )}
          </div>
        ) : (
          <p className="mt-5 text-xs text-[#6B7280]">
            Waiting for {isAol ? 'a claims senior' : 'the Claims Manager'} to decide.
          </p>
        )
      ) : (
        <p className="mt-5 text-sm text-[#374151]"><b>{letter.status_label}</b>{letter.decided_by ? ` by ${letter.decided_by}` : ''}. {letter.decision_note}</p>
      )}
      {msg && <p className="mt-3 text-sm text-[#1D3270]">{msg}</p>}
    </article>
  )
}

function ClaimsAutomationInner() {
  const router = useRouter()
  const params = useSearchParams()
  const focus = (params.get('claim') || '').toUpperCase()
  const [data, setData] = useState<ListResp | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState<'all' | 'exception' | 'straight_through'>('all')

  const load = useCallback(() => {
    apiFetch<ListResp>('/claims-automation/cases/')
      .then(d => { setData(d); setError(null) })
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load'))
  }, [])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load()
  }, [router, load])

  const cases = useMemo(() => {
    const rows = data?.results || []
    return filter === 'all' ? rows : rows.filter(c => c.triage === filter)
  }, [data, filter])
  const awaiting = useMemo(() => (data?.results || []).flatMap(c =>
    c.letters.filter(l => l.status === 'awaiting').map(l => ({ l, ref: c.claim_ref, h: c.handover }))), [data])
  // Server-side counts over every case (the list itself is capped).
  const count = (t: 'straight_through' | 'exception') => data?.counts?.[t] ?? '—'

  return (
    <div className="flex flex-col min-h-screen bg-[#F8FAFC]">
      <TopBar title="Claims Automation" breadcrumbs={[{ label: 'Claims Automation' }]} />
      <div className="flex-1 p-6 space-y-8 max-w-6xl">
        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-[#DC2626]" /><p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}

        <section className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {[
            { label: 'Letters waiting for a decision', value: data?.awaiting_letters ?? '—', tone: '#B91C1C' },
            { label: 'Straight through', value: count('straight_through'), tone: '#166534' },
            { label: 'Needs a person', value: count('exception'), tone: '#92400E' },
            { label: 'Claims followed', value: data?.counts?.total ?? '—', tone: '#1D3270' },
          ].map(k => (
            <div key={k.label} className="bg-white border border-[#E5E7EB] rounded-xl p-4">
              <p className="text-xs text-[#6B7280]">{k.label}</p>
              <p className="text-3xl font-bold tabular-nums mt-1" style={{ color: k.tone }}>{k.value}</p>
            </div>
          ))}
        </section>

        <section>
          <h2 className="text-sm font-bold uppercase tracking-[0.08em] text-[#0D1B2A] mb-3">Waiting for your decision</h2>
          {awaiting.length === 0 ? (
            <p className="text-sm text-[#6B7280] bg-white border border-dashed border-[#D1D5DB] rounded-xl p-6">
              Nothing waiting. Agreements of Loss and repudiations appear here the moment the system drafts one.
            </p>
          ) : (
            <div className="grid gap-4">
              {awaiting.map(({ l, ref, h }) => <LetterCard key={l.id} letter={l} claimRef={ref} handover={h} onDone={load} />)}
            </div>
          )}
        </section>

        <section>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-bold uppercase tracking-[0.08em] text-[#0D1B2A]">Claims</h2>
            <div className="flex gap-1 bg-white border border-[#E5E7EB] rounded-lg p-1">
              {(['all', 'exception', 'straight_through'] as const).map(f => (
                <button key={f} onClick={() => setFilter(f)}
                  className={`px-3 py-1 rounded-md text-xs font-medium transition-colors ${filter === f ? 'bg-[#1D3270] text-white' : 'text-[#374151] hover:bg-[#F3F4F6]'}`}>
                  {f === 'all' ? 'All' : f === 'exception' ? 'Needs a person' : 'Straight through'}
                </button>
              ))}
            </div>
          </div>
          <div className="bg-white border border-[#E5E7EB] rounded-xl divide-y divide-[#F3F4F6]">
            {cases.length === 0 && <p className="p-6 text-sm text-[#6B7280]">{data ? 'No claims yet — they arrive from Graphite once the switch is on.' : 'Loading…'}</p>}
            {cases.map(c => (
              <div key={c.claim_ref} className={`p-4 ${focus === c.claim_ref ? 'bg-[#FFF7ED]' : ''}`}>
                <div className="flex flex-wrap items-center gap-3">
                  <span className="font-semibold text-[#0D1B2A] tabular-nums">{c.claim_ref}</span>
                  <span className="text-xs text-[#6B7280]">{c.claim_type}</span>
                  <span className="text-xs text-[#374151]">{c.stage_label}</span>
                  <TriageChip t={c.triage} />
                  {c.premium_light && (
                    <span className="inline-flex items-center gap-1 text-xs text-[#374151]">
                      <span className="w-2 h-2 rounded-full" style={{ background: LIGHT[c.premium_light] || '#9CA3AF' }} />
                      premium {c.premium_light}
                    </span>
                  )}
                  {c.po_assessment_id && (
                    <button onClick={() => router.push(`/claims-po/${c.po_assessment_id}`)}
                      className="text-xs font-medium text-[#F47C20] hover:underline">Draft POs →</button>
                  )}
                </div>
                {c.ai_summary && (
                  <p className="mt-2 text-sm text-[#374151] flex gap-2">
                    <Sparkles className="w-4 h-4 text-[#F47C20] shrink-0 mt-0.5" />
                    <span>{c.ai_summary}{c.ai_next_step && <><br /><b>Next step:</b> {c.ai_next_step}</>}</span>
                  </p>
                )}
                {c.triage === 'exception' && c.triage_reasons.length > 0 && (
                  <p className="mt-1 text-xs text-[#92400E] flex items-center gap-1">
                    <XCircle className="w-3 h-3" /> {c.triage_reasons.join(' · ')}
                  </p>
                )}
              </div>
            ))}
          </div>
          <p className="mt-2 text-xs text-[#9CA3AF]">AI summaries are advice only, written from claim facts with the customer&apos;s details removed. Triage and flags are set by fixed rules.</p>
        </section>
      </div>
    </div>
  )
}

export default function ClaimsAutomationPage() {
  return (
    <Suspense fallback={<p className="p-6 text-sm text-[#6B7280]">Loading…</p>}>
      <ClaimsAutomationInner />
    </Suspense>
  )
}
