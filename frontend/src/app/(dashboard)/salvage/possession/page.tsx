'use client'

// Salvage possession (CFO 19-Sep-2026). The yard — Veritas / Motor Liquidators —
// declares what they physically hold for each written-off vehicle: the car, the
// blue book, the spare keys, the plates. Alpha Direct cannot settle the
// Agreement of Loss until this is complete, and the 20% invoice to Veritas is
// raised off the back of it. The yard sees the claim number and the vehicle
// only — never the customer's details.

import { useCallback, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { apiFetch, getToken } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Button } from '@/components/ui/button'
import { AlertCircle, CheckCircle2, PackageCheck } from 'lucide-react'

type Item = { key: string; label: string; answer: string | null }
type Handover = {
  items: Item[]; complete: boolean; overridden: boolean; override_reason: string
  note: string; declared_by: string; declared_at: string | null
  may_settle: boolean; blocked_because: string
  veritas_charge: { amount: string; settlement: string; status: string; invoice: string | null } | null
}
type Row = {
  claim_ref: string
  vehicle: { make?: string; model?: string; registration?: string; year?: string }
  agreement_status: string
  handover: Handover
}

const ANSWERS: { value: string; label: string }[] = [
  { value: 'yes', label: 'Yes' },
  { value: 'no', label: 'No' },
  { value: 'na', label: 'Not applicable' },
]

function Card({ row, onSaved }: { row: Row; onSaved: () => void }) {
  const [answers, setAnswers] = useState<Record<string, string>>(
    Object.fromEntries(row.handover.items.map(i => [i.key, i.answer || ''])))
  const [note, setNote] = useState(row.handover.note)
  const [ref, setRef] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const v = row.vehicle || {}
  const vehicle = [v.make, v.model, v.year, v.registration].filter(Boolean).join(' ') || 'Vehicle not recorded'
  const needsNote = Object.values(answers).includes('na') && !note.trim()

  const save = async () => {
    setBusy(true); setMsg(null)
    try {
      const out = await apiFetch<{ outcome: string }>(`/claims-automation/handover/${row.claim_ref}/`, {
        method: 'POST', body: JSON.stringify({ checklist: answers, note, yard_reference: ref }),
      })
      setMsg(out.outcome); onSaved()
    } catch (e) {
      setMsg(e instanceof Error ? e.message : 'Could not save')
    } finally { setBusy(false) }
  }

  return (
    <article className="bg-white border border-[#E5E7EB] rounded-xl p-5">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h3 className="text-lg font-semibold text-[#0D1B2A] tabular-nums">{row.claim_ref}</h3>
          <p className="text-sm text-[#6B7280]">{vehicle}</p>
        </div>
        <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${row.handover.complete ? 'bg-[#DCFCE7] text-[#166534]' : 'bg-[#FEF3C7] text-[#92400E]'}`}>
          {row.handover.complete ? 'Declared' : 'Waiting on you'}
        </span>
      </header>

      <div className="mt-4 grid gap-3">
        {row.handover.items.map(item => (
          <div key={item.key} className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-sm text-[#374151]">{item.label}</span>
            <span className="flex gap-1">
              {ANSWERS.map(a => (
                <button key={a.value} type="button"
                  onClick={() => setAnswers({ ...answers, [item.key]: a.value })}
                  className={`px-3 py-1 rounded-md text-xs font-medium border transition-colors ${
                    answers[item.key] === a.value
                      ? 'bg-[#1D3270] text-white border-[#1D3270]'
                      : 'bg-white text-[#374151] border-[#D1D5DB] hover:bg-[#F3F4F6]'}`}>
                  {a.label}
                </button>
              ))}
            </span>
          </div>
        ))}
      </div>

      <div className="mt-4 grid gap-2 sm:grid-cols-2">
        <input value={ref} onChange={e => setRef(e.target.value)} placeholder="Your yard reference (optional)"
          className="h-9 rounded-md border border-[#D1D5DB] px-2 text-sm focus:outline-none focus:ring-2 focus:ring-[#F47C20]/40" />
        <input value={note} onChange={e => setNote(e.target.value)}
          placeholder={needsNote ? 'Explain anything marked not applicable' : 'Note (optional)'}
          className={`h-9 rounded-md border px-2 text-sm focus:outline-none focus:ring-2 focus:ring-[#F47C20]/40 ${needsNote ? 'border-[#FDBA74]' : 'border-[#D1D5DB]'}`} />
      </div>

      <div className="mt-4 flex items-center gap-3">
        <Button variant="accent" size="sm" disabled={busy || needsNote} onClick={save}
          leftIcon={<PackageCheck className="w-3.5 h-3.5" />}>Save what we hold</Button>
        {row.handover.declared_by && (
          <span className="text-xs text-[#6B7280]">Last declared by {row.handover.declared_by}</span>
        )}
        {row.handover.veritas_charge && (
          <span className="text-xs text-[#1D3270]">Invoice to Veritas P {row.handover.veritas_charge.amount}</span>
        )}
      </div>
      {msg && <p className="mt-2 text-sm text-[#1D3270]">{msg}</p>}
    </article>
  )
}

export default function SalvagePossessionPage() {
  const router = useRouter()
  const [rows, setRows] = useState<Row[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    apiFetch<{ results: Row[] }>('/claims-automation/handovers/')
      .then(d => { setRows(d.results); setError(null) })
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load'))
  }, [])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load()
  }, [router, load])

  return (
    <div className="flex flex-col min-h-screen bg-[#F8FAFC]">
      <TopBar title="Salvage possession" breadcrumbs={[{ label: 'Salvage' }, { label: 'Possession' }]} />
      <div className="flex-1 p-6 space-y-4 max-w-4xl">
        <p className="text-sm text-[#374151]">
          Tell us what you have for each written-off vehicle. Alpha Direct cannot settle with the
          client until the car and its papers are accounted for.
        </p>
        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-[#DC2626]" /><p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}
        {rows === null && <p className="text-sm text-[#6B7280]">Loading…</p>}
        {rows?.length === 0 && (
          <p className="flex items-center gap-2 text-sm text-[#166534] bg-white border border-[#BBF7D0] rounded-xl p-6">
            <CheckCircle2 className="w-4 h-4" /> Nothing waiting. Written-off vehicles appear here as they come through.
          </p>
        )}
        {rows?.map(r => <Card key={r.claim_ref} row={r} onSaved={load} />)}
      </div>
    </div>
  )
}
