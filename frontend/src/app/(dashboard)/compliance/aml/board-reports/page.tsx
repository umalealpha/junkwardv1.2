'use client'

/**
 * /compliance/aml/board-reports — the quarterly compliance report to the Board.
 *
 * The server refuses to mark a report sent without the pack attached, and the
 * objective only counts a report that has both. This page shows that state
 * honestly rather than letting a status be flipped to "sent" next to a counter
 * that still says outstanding.
 */

import { useCallback, useEffect, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { apiFetch } from '@/lib/api'
import { fetchAllPages } from '@/lib/fetchAllPages'
import { FileText, Plus, X } from 'lucide-react'

interface Report {
  id: string
  kind: 'aml' | 'data_prot'
  kind_label: string
  period_year: number
  period_quarter: number
  title: string
  summary: string
  document: string | null
  status: 'draft' | 'sent' | 'noted'
  status_label: string
  sent_at: string | null
  recipients: string
  due_on: string
  is_discharged: boolean
}

const STATUS_TONE: Record<Report['status'], string> = {
  draft: 'bg-slate-200 text-slate-700',
  sent: 'bg-sky-100 text-sky-800',
  noted: 'bg-emerald-100 text-emerald-800',
}

type Kind = 'aml' | 'data_prot'

const KIND_LABEL: Record<Kind, string> = {
  aml: 'AML / CFT',
  data_prot: 'Data protection',
}

export default function BoardReportsPage() {
  // Both quarterly reports live in one register and differ only by `kind`.
  // The Data Protection Officer had NO screen at all until 2026-09-16 — the
  // obligation was counted against her while nothing existed to file it on,
  // which is the same hole this whole page was built to close for AML.
  const [kind, setKind] = useState<Kind>('aml')
  const [rows, setRows] = useState<Report[]>([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [editing, setEditing] = useState<Report | null>(null)
  const [creating, setCreating] = useState(false)

  const load = useCallback(async () => {
    setLoading(true); setErr(null)
    try {
      setRows(await fetchAllPages<Report>(`/aml/board-reports/?kind=${kind}`))
    } catch (e: any) { setErr(e?.message || 'Load failed') }
    finally { setLoading(false) }
  }, [kind])
  useEffect(() => { void load() }, [load])

  const save = async (r: Report, file: File | null) => {
    const isNew = !r.id
    try {
      const form = new FormData()
      // The ROW's own kind wins over the selected tab. They cannot diverge
      // today, because each tab lists only its own rows, so an existing row is
      // always edited from its own tab. Keyed on the tab anyway, a future change
      // that lets the two lists mix would silently reclassify a filed AML pack as
      // a data-protection one - the exact collision the kind split exists to stop.
      form.append('kind', r.kind || kind)
      form.append('period_year', String(r.period_year))
      form.append('period_quarter', String(r.period_quarter))
      form.append('title', r.title)
      form.append('summary', r.summary)
      form.append('status', r.status)
      form.append('recipients', r.recipients)
      if (file) form.append('document', file)
      await apiFetch(isNew ? '/aml/board-reports/' : `/aml/board-reports/${r.id}/`, {
        method: isNew ? 'POST' : 'PATCH',
        body: form,
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
              <FileText size={28} style={{ color: '#0D1B2A' }} />
              <h1 className="text-2xl font-serif font-semibold" style={{ color: '#0D1B2A' }}>
                Quarterly Report to the Board
              </h1>
            </div>
            <p className="mt-1 text-sm text-slate-600">
              Due on the 5th of the month after each quarter ends. The report only counts as filed
              once the pack is attached and it has gone out.
            </p>
          </div>
          <button onClick={() => { setCreating(true); setEditing({} as Report) }}
            className="flex items-center gap-2 rounded-md px-3 py-2 text-sm text-white"
            style={{ background: '#F4A623' }}>
            <Plus size={14} /> New report
          </button>
        </div>

        {err && (
          <div className="mb-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
            {err}
          </div>
        )}

        <div className="mb-4 flex gap-2">
          {(['aml', 'data_prot'] as Kind[]).map((k) => (
            <button key={k}
              onClick={() => { setKind(k); setEditing(null); setCreating(false) }}
              className={`rounded-md px-3 py-2 text-sm ${k === kind ? 'text-white' : 'border border-slate-300 text-slate-700'}`}
              style={k === kind ? { background: '#0D1B2A' } : undefined}>
              {KIND_LABEL[k]}
            </button>
          ))}
        </div>

        <div className="overflow-x-auto rounded-md border border-slate-200 bg-white">
          <table className="min-w-full text-sm">
            <thead className="bg-slate-100 text-left text-xs uppercase tracking-wide text-slate-600">
              <tr>
                <th className="px-3 py-2">Period</th>
                <th className="px-3 py-2">Title</th>
                <th className="px-3 py-2">Due</th>
                <th className="px-3 py-2">Pack</th>
                <th className="px-3 py-2">Status</th>
                <th className="px-3 py-2">Counts as filed</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {loading ? (
                <tr><td colSpan={6} className="px-3 py-8 text-center text-slate-500">Loading…</td></tr>
              ) : rows.length === 0 ? (
                <tr><td colSpan={6} className="px-3 py-8 text-center text-slate-500">
                  No quarterly reports on record.
                </td></tr>
              ) : rows.map((r) => (
                <tr key={r.id} onClick={() => setEditing(r)} className="cursor-pointer hover:bg-slate-50">
                  <td className="px-3 py-2 font-mono text-xs">{r.period_year} Q{r.period_quarter}</td>
                  <td className="px-3 py-2 font-medium" style={{ color: '#0D1B2A' }}>{r.title || '—'}</td>
                  <td className="px-3 py-2 text-xs text-slate-600">{r.due_on}</td>
                  <td className="px-3 py-2 text-xs">
                    {r.document
                      ? <a href={r.document} target="_blank" rel="noreferrer"
                          onClick={(e) => e.stopPropagation()}
                          className="text-sky-700 underline">Open</a>
                      : <span className="text-red-700">Not attached</span>}
                  </td>
                  <td className="px-3 py-2">
                    <span className={`inline-block rounded px-2 py-0.5 text-[11px] ${STATUS_TONE[r.status]}`}>
                      {r.status_label}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-xs">
                    {r.is_discharged
                      ? <span className="text-emerald-700">Yes</span>
                      : <span className="text-red-700">No</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {editing && (
          <ReportEditor report={editing} isNew={creating} kind={kind} onSave={save}
            onClose={() => { setEditing(null); setCreating(false) }} />
        )}
      </main>
    </div>
  )
}

function ReportEditor({
  report, isNew, kind, onSave, onClose,
}: {
  report: Report; isNew: boolean; kind: Kind
  onSave: (r: Report, file: File | null) => void | Promise<void>
  onClose: () => void
}) {
  const now = new Date()
  const [r, setR] = useState<Report>(() => {
    const defaults: Report = {
      id: '', kind, kind_label: '', period_year: now.getFullYear(),
      period_quarter: Math.floor(now.getMonth() / 3) + 1,
      title: '', summary: '', document: null, status: 'draft', status_label: '',
      sent_at: null, recipients: '', due_on: '', is_discharged: false,
    }
    return { ...defaults, ...report }
  })
  const [file, setFile] = useState<File | null>(null)
  const set = <K extends keyof Report>(k: K, v: Report[K]) => setR((p) => ({ ...p, [k]: v }))

  const markingSent = (r.status === 'sent' || r.status === 'noted') && !r.document && !file

  return (
    <div className="fixed inset-0 z-40 flex items-end bg-slate-900/50 sm:items-center sm:justify-center">
      <div className="w-full max-w-2xl rounded-t-lg bg-white p-6 shadow-xl sm:rounded-lg">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="font-serif text-xl font-semibold" style={{ color: '#0D1B2A' }}>
            {isNew ? 'New quarterly report' : `${r.period_year} Q${r.period_quarter}`}
          </h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-700"><X size={20} /></button>
        </div>
        <div className="grid grid-cols-2 gap-3 text-sm">
          <Field label="Year">
            <input type="number" value={r.period_year}
              onChange={(e) => set('period_year', Number(e.target.value))}
              className="w-full rounded border border-slate-300 px-3 py-2 text-sm" />
          </Field>
          <Field label="Quarter">
            <select value={r.period_quarter}
              onChange={(e) => set('period_quarter', Number(e.target.value))}
              className="w-full rounded border border-slate-300 px-3 py-2 text-sm">
              <option value={1}>Q1</option><option value={2}>Q2</option>
              <option value={3}>Q3</option><option value={4}>Q4</option>
            </select>
          </Field>
          <Field label="Title" full>
            <input value={r.title} onChange={(e) => set('title', e.target.value)}
              className="w-full rounded border border-slate-300 px-3 py-2 text-sm" />
          </Field>
          <Field label="Summary" full>
            <textarea value={r.summary} onChange={(e) => set('summary', e.target.value)} rows={3}
              className="w-full rounded border border-slate-300 px-3 py-2 text-sm" />
          </Field>
          <Field label="The pack itself" full>
            <input type="file" onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="w-full text-sm" />
            {r.document && !file && (
              <p className="mt-1 text-xs text-slate-500">
                Already attached. Choosing a file replaces it.
              </p>
            )}
          </Field>
          <Field label="Sent to (one address per line)" full>
            <textarea value={r.recipients} onChange={(e) => set('recipients', e.target.value)} rows={2}
              className="w-full rounded border border-slate-300 px-3 py-2 text-sm" />
          </Field>
          <Field label="Status">
            <select value={r.status} onChange={(e) => set('status', e.target.value as Report['status'])}
              className="w-full rounded border border-slate-300 px-3 py-2 text-sm">
              <option value="draft">Draft</option>
              <option value="sent">Sent to EXCO</option>
              <option value="noted">Noted by the Board</option>
            </select>
          </Field>
        </div>
        {markingSent && (
          <p className="mt-3 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
            Attach the pack before marking this sent. A report with no document does not count as filed.
          </p>
        )}
        <div className="mt-6 flex justify-end gap-2">
          <button onClick={onClose} className="rounded border border-slate-300 px-3 py-2 text-sm">Cancel</button>
          <button onClick={() => onSave(r, file)} disabled={markingSent}
            className="rounded px-3 py-2 text-sm text-white disabled:opacity-40"
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
