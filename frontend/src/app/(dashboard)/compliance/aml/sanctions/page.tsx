'use client'

/**
 * /compliance/aml/sanctions — Sanctions & PEP screening register.
 *
 * Until this page existed the register could only be written from Django
 * admin, which the AML officer cannot reach. The counter therefore read zero
 * regardless of the work done.
 */

import { useCallback, useEffect, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { apiFetch } from '@/lib/api'
import { fetchAllPages } from '@/lib/fetchAllPages'
import { ShieldAlert, Plus, X } from 'lucide-react'

interface Screening {
  id: string
  subject_type: 'customer' | 'supplier' | 'claimant' | 'staff' | 'other'
  subject_type_label: string
  subject_name: string
  subject_ref: string
  list_source: string
  list_version: string
  result: 'clear' | 'possible' | 'match'
  result_label: string
  pep_status: 'none' | 'pep' | 'associate' | 'unchecked'
  pep_label: string
  screened_at: string
  screened_by_name: string
  reported_to_fia_at: string | null
  needs_fia_report: boolean
  notes: string
}

interface Summary {
  total: number; clear: number; possible: number; match: number
  awaiting_fia_report: number; pep_unassessed: number
}

const RESULT_TONE: Record<Screening['result'], string> = {
  clear: 'bg-emerald-100 text-emerald-800',
  possible: 'bg-amber-100 text-amber-800',
  match: 'bg-red-100 text-red-800',
}

export default function SanctionsScreeningPage() {
  const [rows, setRows] = useState<Screening[]>([])
  const [summary, setSummary] = useState<Summary | null>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [editing, setEditing] = useState<Screening | null>(null)
  const [creating, setCreating] = useState(false)

  const load = useCallback(async () => {
    setLoading(true); setErr(null)
    try {
      setRows(await fetchAllPages<Screening>('/aml/sanctions-screenings/'))
      try {
        setSummary(await apiFetch<Summary>('/aml/sanctions-screenings/summary/'))
      } catch { /* the list is the point; the tiles are a nicety */ }
    } catch (e: any) { setErr(e?.message || 'Load failed') }
    finally { setLoading(false) }
  }, [])
  useEffect(() => { void load() }, [load])

  const save = async (s: Partial<Screening> & { id?: string }) => {
    const isNew = !s.id
    try {
      await apiFetch(isNew ? '/aml/sanctions-screenings/' : `/aml/sanctions-screenings/${s.id}/`, {
        method: isNew ? 'POST' : 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(s),
      })
      setEditing(null); setCreating(false); await load()
    } catch (e: any) { setErr(e?.message || 'Save failed') }
  }

  return (
    <div className="min-h-screen bg-slate-50">
      <TopBar />
      <main className="mx-auto max-w-7xl px-6 py-8">
        <div className="mb-6 flex items-start justify-between">
          <div>
            <div className="flex items-center gap-3">
              <ShieldAlert size={28} style={{ color: '#0D1B2A' }} />
              <h1 className="text-2xl font-serif font-semibold" style={{ color: '#0D1B2A' }}>
                Sanctions &amp; PEP Screening
              </h1>
            </div>
            <p className="mt-1 text-sm text-slate-600">
              One row per party screened. Record the list and the version you searched, or the
              screening cannot be repeated or shown to a regulator.
            </p>
          </div>
          <button onClick={() => { setCreating(true); setEditing({} as Screening) }}
            className="flex items-center gap-2 rounded-md px-3 py-2 text-sm text-white"
            style={{ background: '#F4A623' }}>
            <Plus size={14} /> Record a screening
          </button>
        </div>

        {summary && (
          <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-5">
            <Tile label="Screened" value={summary.total} />
            <Tile label="Clear" value={summary.clear} />
            <Tile label="Possible match" value={summary.possible} tone={summary.possible ? 'amber' : undefined} />
            <Tile label="Confirmed match" value={summary.match} tone={summary.match ? 'red' : undefined} />
            <Tile label="Not reported to FIA" value={summary.awaiting_fia_report}
              tone={summary.awaiting_fia_report ? 'red' : undefined} />
          </div>
        )}

        {err && (
          <div className="mb-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
            {err}
          </div>
        )}

        <div className="overflow-x-auto rounded-md border border-slate-200 bg-white">
          <table className="min-w-full text-sm">
            <thead className="bg-slate-100 text-left text-xs uppercase tracking-wide text-slate-600">
              <tr>
                <th className="px-3 py-2">Screened</th>
                <th className="px-3 py-2">Party</th>
                <th className="px-3 py-2">Type</th>
                <th className="px-3 py-2">List searched</th>
                <th className="px-3 py-2">Result</th>
                <th className="px-3 py-2">PEP</th>
                <th className="px-3 py-2">By</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {loading ? (
                <tr><td colSpan={7} className="px-3 py-8 text-center text-slate-500">Loading…</td></tr>
              ) : rows.length === 0 ? (
                <tr><td colSpan={7} className="px-3 py-8 text-center text-slate-500">
                  Nothing recorded yet. Every screening you run belongs here — an unrecorded
                  screening cannot be evidenced.
                </td></tr>
              ) : rows.map((s) => (
                <tr key={s.id} onClick={() => setEditing(s)} className="cursor-pointer hover:bg-slate-50">
                  <td className="px-3 py-2 text-xs text-slate-600">
                    {s.screened_at ? s.screened_at.slice(0, 10) : '—'}
                  </td>
                  <td className="px-3 py-2">
                    <div className="font-medium" style={{ color: '#0D1B2A' }}>{s.subject_name}</div>
                    {s.subject_ref && <div className="text-xs text-slate-500">{s.subject_ref}</div>}
                  </td>
                  <td className="px-3 py-2 text-xs text-slate-600">{s.subject_type_label}</td>
                  <td className="px-3 py-2 text-xs text-slate-600">
                    {s.list_source}{s.list_version ? ` · ${s.list_version}` : ''}
                  </td>
                  <td className="px-3 py-2">
                    <span className={`inline-block rounded px-2 py-0.5 text-[11px] ${RESULT_TONE[s.result]}`}>
                      {s.result_label}
                    </span>
                    {s.needs_fia_report && (
                      <span className="ml-1 inline-block rounded bg-red-600 px-2 py-0.5 text-[11px] text-white">
                        FIA not notified
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-xs text-slate-600">{s.pep_label}</td>
                  <td className="px-3 py-2 text-xs text-slate-600">{s.screened_by_name || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {editing && (
          <ScreeningEditor row={editing} isNew={creating} onSave={save}
            onClose={() => { setEditing(null); setCreating(false) }} />
        )}
      </main>
    </div>
  )
}

function Tile({ label, value, tone }: { label: string; value: number; tone?: 'amber' | 'red' }) {
  const colour = tone === 'red' ? '#b91c1c' : tone === 'amber' ? '#b45309' : '#0D1B2A'
  return (
    <div className="rounded-md border border-slate-200 bg-white px-3 py-3">
      <div className="text-xs uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-1 text-2xl font-semibold" style={{ color: colour }}>{value}</div>
    </div>
  )
}

function ScreeningEditor({
  row, isNew, onSave, onClose,
}: {
  row: Screening; isNew: boolean
  onSave: (s: Partial<Screening> & { id?: string }) => void | Promise<void>
  onClose: () => void
}) {
  const [s, setS] = useState<Screening>(() => {
    const defaults: Screening = {
      id: '', subject_type: 'customer', subject_type_label: '', subject_name: '',
      subject_ref: '', list_source: 'UNSC Consolidated', list_version: '',
      result: 'clear', result_label: '', pep_status: 'unchecked', pep_label: '',
      screened_at: new Date().toISOString(), screened_by_name: '',
      reported_to_fia_at: null, needs_fia_report: false, notes: '',
    }
    return { ...defaults, ...row }
  })
  const set = <K extends keyof Screening>(k: K, v: Screening[K]) => setS((p) => ({ ...p, [k]: v }))

  return (
    <div className="fixed inset-0 z-40 flex items-end bg-slate-900/50 sm:items-center sm:justify-center">
      <div className="w-full max-w-3xl rounded-t-lg bg-white p-6 shadow-xl sm:rounded-lg">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="font-serif text-xl font-semibold" style={{ color: '#0D1B2A' }}>
            {isNew ? 'Record a screening' : 'Edit screening'}
          </h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-700"><X size={20} /></button>
        </div>
        <div className="grid grid-cols-2 gap-3 text-sm">
          <Field label="Party name">
            <input value={s.subject_name} onChange={(e) => set('subject_name', e.target.value)}
              className="w-full rounded border border-slate-300 px-3 py-2 text-sm" />
          </Field>
          <Field label="Type">
            <select value={s.subject_type}
              onChange={(e) => set('subject_type', e.target.value as Screening['subject_type'])}
              className="w-full rounded border border-slate-300 px-3 py-2 text-sm">
              <option value="customer">Customer / policyholder</option>
              <option value="supplier">Supplier / vendor</option>
              <option value="claimant">Claimant / third party</option>
              <option value="staff">Employee</option>
              <option value="other">Other counterparty</option>
            </select>
          </Field>
          <Field label="Reference (policy / supplier / claim — never an Omang)">
            <input value={s.subject_ref} onChange={(e) => set('subject_ref', e.target.value)}
              className="w-full rounded border border-slate-300 px-3 py-2 text-sm" />
          </Field>
          <Field label="Screened on">
            <input type="date" value={s.screened_at ? s.screened_at.slice(0, 10) : ''}
              onChange={(e) => set('screened_at', e.target.value)}
              className="w-full rounded border border-slate-300 px-3 py-2 text-sm" />
          </Field>
          <Field label="List searched">
            <input value={s.list_source} onChange={(e) => set('list_source', e.target.value)}
              placeholder="UNSC Consolidated"
              className="w-full rounded border border-slate-300 px-3 py-2 text-sm" />
          </Field>
          <Field label="List version or date (required)">
            <input value={s.list_version} onChange={(e) => set('list_version', e.target.value)}
              placeholder="e.g. 2026-09-12"
              className="w-full rounded border border-slate-300 px-3 py-2 text-sm" />
          </Field>
          <Field label="Result">
            <select value={s.result} onChange={(e) => set('result', e.target.value as Screening['result'])}
              className="w-full rounded border border-slate-300 px-3 py-2 text-sm">
              <option value="clear">Clear — no match</option>
              <option value="possible">Possible match — under review</option>
              <option value="match">Confirmed match</option>
            </select>
          </Field>
          <Field label="PEP status">
            <select value={s.pep_status}
              onChange={(e) => set('pep_status', e.target.value as Screening['pep_status'])}
              className="w-full rounded border border-slate-300 px-3 py-2 text-sm">
              <option value="unchecked">Not yet assessed</option>
              <option value="none">Not a PEP</option>
              <option value="pep">Politically Exposed Person</option>
              <option value="associate">Close associate / family of a PEP</option>
            </select>
          </Field>
          {s.result === 'match' && (
            <Field label="Reported to the FIA on" full>
              <input type="date" value={s.reported_to_fia_at ? s.reported_to_fia_at.slice(0, 10) : ''}
                onChange={(e) => set('reported_to_fia_at', e.target.value || null)}
                className="w-full rounded border border-slate-300 px-3 py-2 text-sm" />
              <p className="mt-1 text-xs text-red-700">
                A confirmed match must be reported to the Financial Intelligence Agency without delay.
              </p>
            </Field>
          )}
          <Field label="Notes" full>
            <textarea value={s.notes} onChange={(e) => set('notes', e.target.value)} rows={2}
              className="w-full rounded border border-slate-300 px-3 py-2 text-sm" />
          </Field>
        </div>
        <div className="mt-6 flex justify-end gap-2">
          <button onClick={onClose} className="rounded border border-slate-300 px-3 py-2 text-sm">Cancel</button>
          <button onClick={() => onSave(s)} className="rounded px-3 py-2 text-sm text-white"
            style={{ background: '#0D1B2A' }}>Save</button>
        </div>
      </div>
    </div>
  )
}

function Field({ label, full, children }: { label: string; full?: boolean; children: React.ReactNode }) {
  return (
    <div className={full ? 'col-span-2' : ''}>
      <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500">{label}</label>
      {children}
    </div>
  )
}
