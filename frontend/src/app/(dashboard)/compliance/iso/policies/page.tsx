'use client'

/**
 * /compliance/iso/policies — Policy register.
 */

import { useCallback, useEffect, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { apiFetch } from '@/lib/api'
import { FileText, Plus, X, ExternalLink } from 'lucide-react'

interface Policy {
  id: string; code: string; title: string; version: string
  summary: string; document_url: string
  owner: string; approver: string
  approved_at: string | null; review_due: string | null
  status: 'draft' | 'review' | 'approved' | 'retired'; status_label: string
  control_clauses: string[]
}

const STATUS_TONE: Record<Policy['status'], string> = {
  draft:    'bg-slate-200 text-slate-700',
  review:   'bg-amber-100 text-amber-800',
  approved: 'bg-emerald-100 text-emerald-800',
  retired:  'bg-red-100 text-red-700',
}

export default function PoliciesPage() {
  const [rows, setRows] = useState<Policy[]>([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [editing, setEditing] = useState<Policy | null>(null)
  const [creating, setCreating] = useState(false)

  const load = useCallback(async () => {
    setLoading(true); setErr(null)
    try {
      const res = await apiFetch<any>('/iso/policies/?page_size=200')
      setRows(Array.isArray(res) ? res : (res?.results ?? []))
    } catch (e: any) { setErr(e?.message || 'Load failed') }
    finally { setLoading(false) }
  }, [])
  useEffect(() => { void load() }, [load])

  const save = async (p: Partial<Policy> & { id?: string }) => {
    const isNew = !p.id
    await apiFetch(isNew ? '/iso/policies/' : `/iso/policies/${p.id}/`, {
      method: isNew ? 'POST' : 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(p),
    })
    setEditing(null); setCreating(false); await load()
  }
  const del = async (id: string) => {
    if (!confirm('Delete this policy?')) return
    await apiFetch(`/iso/policies/${id}/`, { method: 'DELETE' })
    setEditing(null); await load()
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
                Policy Register
              </h1>
            </div>
            <p className="mt-1 text-sm text-slate-600">
              Information-Security policies plus topic-specific ones (Access Control, BCP, Incident
              Response, Acceptable Use). Each with owner, approver, version and review date.
            </p>
          </div>
          <button onClick={() => { setCreating(true); setEditing({} as Policy) }}
            className="flex items-center gap-2 rounded-md px-3 py-2 text-sm text-white"
            style={{ background: '#F4A623' }}>
            <Plus size={14} /> New Policy
          </button>
        </div>

        {err && (
          <div className="mb-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
            {err}
          </div>
        )}

        <div className="overflow-x-auto rounded-md border border-slate-200 bg-white">
          <table className="min-w-full text-sm">
            <thead className="bg-slate-100 text-left text-xs uppercase tracking-wide text-slate-600">
              <tr>
                <th className="px-3 py-2">Code</th>
                <th className="px-3 py-2">Title</th>
                <th className="px-3 py-2">Version</th>
                <th className="px-3 py-2">Status</th>
                <th className="px-3 py-2">Owner / Approver</th>
                <th className="px-3 py-2">Review due</th>
                <th className="px-3 py-2">Document</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {loading ? (
                <tr><td colSpan={7} className="px-3 py-8 text-center text-slate-500">Loading…</td></tr>
              ) : rows.length === 0 ? (
                <tr><td colSpan={7} className="px-3 py-8 text-center text-slate-500">
                  No policies yet. Auditors will expect at least: Info-Sec, Access Control, BCP,
                  Acceptable Use, Incident Response, Backup, Change Mgmt.
                </td></tr>
              ) : rows.map((p) => (
                <tr key={p.id} onClick={() => setEditing(p)}
                    className="cursor-pointer hover:bg-slate-50">
                  <td className="px-3 py-2 font-mono text-xs">{p.code}</td>
                  <td className="px-3 py-2 font-medium" style={{ color: '#0D1B2A' }}>{p.title}</td>
                  <td className="px-3 py-2 font-mono text-xs">{p.version}</td>
                  <td className="px-3 py-2">
                    <span className={`inline-block rounded px-2 py-0.5 text-[11px] ${STATUS_TONE[p.status]}`}>
                      {p.status_label}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-xs text-slate-600">
                    <div>{p.owner}</div>
                    <div className="text-slate-400">{p.approver}</div>
                  </td>
                  <td className="px-3 py-2 text-xs text-slate-600">{p.review_due || '—'}</td>
                  <td className="px-3 py-2">
                    {p.document_url ? (
                      <a href={p.document_url} target="_blank" rel="noreferrer"
                         className="inline-flex items-center gap-1 text-xs text-blue-700 hover:underline"
                         onClick={(e) => e.stopPropagation()}>
                        Open <ExternalLink size={11} />
                      </a>
                    ) : <span className="text-xs text-slate-400">—</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {editing && (
          <PolicyEditor pol={editing} isNew={creating}
            onSave={save} onDelete={del} onClose={() => { setEditing(null); setCreating(false) }} />
        )}
      </main>
    </div>
  )
}

function PolicyEditor({
  pol, isNew, onSave, onDelete, onClose,
}: {
  pol: Policy; isNew: boolean
  onSave: (p: Partial<Policy> & { id?: string }) => void | Promise<void>
  onDelete: (id: string) => void | Promise<void>
  onClose: () => void
}) {
  const [p, setP] = useState<Policy>(() => {
    const defaults: Policy = {
      id: '', code: '', title: '', version: '1.0',
      summary: '', document_url: '',
      owner: '', approver: '',
      approved_at: null, review_due: null,
      status: 'draft', status_label: '', control_clauses: [],
    }
    return { ...defaults, ...pol }
  })
  const set = <K extends keyof Policy>(k: K, v: Policy[K]) => setP((q) => ({ ...q, [k]: v }))

  return (
    <div className="fixed inset-0 z-40 flex items-end bg-slate-900/50 sm:items-center sm:justify-center">
      <div className="w-full max-w-2xl rounded-t-lg bg-white p-6 shadow-xl sm:rounded-lg">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="font-serif text-xl font-semibold" style={{ color: '#0D1B2A' }}>
            {isNew ? 'New Policy' : `Edit ${p.code}`}
          </h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-700">
            <X size={20} />
          </button>
        </div>

        <div className="grid grid-cols-2 gap-3 text-sm">
          <Field label="Code"><input value={p.code} onChange={(e) => set('code', e.target.value)}
            placeholder="POL-INFOSEC-001"
            className="w-full rounded border border-slate-300 px-3 py-2 text-sm" /></Field>
          <Field label="Version"><input value={p.version} onChange={(e) => set('version', e.target.value)}
            className="w-full rounded border border-slate-300 px-3 py-2 text-sm" /></Field>
          <Field label="Title" full><input value={p.title} onChange={(e) => set('title', e.target.value)}
            className="w-full rounded border border-slate-300 px-3 py-2 text-sm" /></Field>
          <Field label="Owner"><input value={p.owner} onChange={(e) => set('owner', e.target.value)}
            className="w-full rounded border border-slate-300 px-3 py-2 text-sm" /></Field>
          <Field label="Approver"><input value={p.approver} onChange={(e) => set('approver', e.target.value)}
            className="w-full rounded border border-slate-300 px-3 py-2 text-sm" /></Field>
          <Field label="Status">
            <select value={p.status} onChange={(e) => set('status', e.target.value as Policy['status'])}
              className="w-full rounded border border-slate-300 px-3 py-2 text-sm">
              <option value="draft">Draft</option>
              <option value="review">Under review</option>
              <option value="approved">Approved</option>
              <option value="retired">Retired</option>
            </select>
          </Field>
          <Field label="Review due"><input type="date" value={p.review_due ?? ''}
            onChange={(e) => set('review_due', e.target.value || null)}
            className="w-full rounded border border-slate-300 px-3 py-2 text-sm" /></Field>
          <Field label="Document URL" full><input value={p.document_url}
            onChange={(e) => set('document_url', e.target.value)}
            placeholder="https://…"
            className="w-full rounded border border-slate-300 px-3 py-2 text-sm" /></Field>
          <Field label="Summary" full><textarea value={p.summary}
            onChange={(e) => set('summary', e.target.value)} rows={3}
            className="w-full rounded border border-slate-300 px-3 py-2 text-sm" /></Field>
        </div>

        <div className="mt-6 flex justify-between">
          <div>{!isNew && (
            <button onClick={() => onDelete(p.id)}
              className="rounded border border-red-200 px-3 py-2 text-sm text-red-700 hover:bg-red-50">
              Delete
            </button>
          )}</div>
          <div className="flex gap-2">
            <button onClick={onClose} className="rounded border border-slate-300 px-3 py-2 text-sm">Cancel</button>
            <button onClick={() => onSave(p)} className="rounded px-3 py-2 text-sm text-white"
              style={{ background: '#0D1B2A' }}>Save</button>
          </div>
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
