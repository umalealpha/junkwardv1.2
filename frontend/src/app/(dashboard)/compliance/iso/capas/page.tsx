'use client'

/**
 * /compliance/iso/capas — Corrective + Preventive Actions register.
 */

import { useCallback, useEffect, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { apiFetch } from '@/lib/api'
import { ClipboardCheck, Plus, X } from 'lucide-react'

interface CAPA {
  id: string; ref: string; title: string
  nonconformity: string; root_cause: string
  corrective_action: string; preventive_action: string
  owner: string; due_date: string | null
  verifier: string; verified_at: string | null; closed_at: string | null
  status: 'draft' | 'open' | 'in_progress' | 'verification' | 'closed'
  status_label: string; created_at: string
  finding: string | null; finding_title: string | null
  commandment: number | null; commandment_number: number | null
}

const STATUS_TONE: Record<CAPA['status'], string> = {
  draft:        'bg-slate-200 text-slate-700',
  open:         'bg-amber-100 text-amber-800',
  in_progress:  'bg-sky-100 text-sky-800',
  verification: 'bg-violet-100 text-violet-800',
  closed:       'bg-emerald-100 text-emerald-800',
}

export default function CAPAPage() {
  const [rows, setRows] = useState<CAPA[]>([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [editing, setEditing] = useState<CAPA | null>(null)
  const [creating, setCreating] = useState(false)

  const load = useCallback(async () => {
    setLoading(true); setErr(null)
    try {
      const res = await apiFetch<any>('/iso/capas/?page_size=200')
      const list: CAPA[] = Array.isArray(res) ? res : (res?.results ?? [])
      setRows(list)
    } catch (e: any) { setErr(e?.message || 'Load failed') }
    finally { setLoading(false) }
  }, [])
  useEffect(() => { void load() }, [load])

  const save = async (c: Partial<CAPA> & { id?: string }) => {
    const isNew = !c.id
    await apiFetch(isNew ? '/iso/capas/' : `/iso/capas/${c.id}/`, {
      method: isNew ? 'POST' : 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(c),
    })
    setEditing(null); setCreating(false); await load()
  }
  const del = async (id: string) => {
    if (!confirm('Delete this CAPA?')) return
    await apiFetch(`/iso/capas/${id}/`, { method: 'DELETE' })
    setEditing(null); await load()
  }

  return (
    <div className="min-h-screen bg-slate-50">
      <TopBar />
      <main className="mx-auto max-w-7xl px-6 py-8">
        <div className="mb-6 flex items-start justify-between">
          <div>
            <div className="flex items-center gap-3">
              <ClipboardCheck size={28} style={{ color: '#0D1B2A' }} />
              <h1 className="text-2xl font-serif font-semibold" style={{ color: '#0D1B2A' }}>
                CAPA Register
              </h1>
            </div>
            <p className="mt-1 text-sm text-slate-600">
              Corrective + Preventive Action lifecycle. Each nonconformity gets a root cause, a
              corrective action, a verifier and a closure date.
            </p>
          </div>
          <button onClick={() => { setCreating(true); setEditing({} as CAPA) }}
            className="flex items-center gap-2 rounded-md px-3 py-2 text-sm text-white"
            style={{ background: '#F4A623' }}>
            <Plus size={14} /> New CAPA
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
                <th className="px-3 py-2">Ref</th>
                <th className="px-3 py-2">Title</th>
                <th className="px-3 py-2">Owner</th>
                <th className="px-3 py-2">Due</th>
                <th className="px-3 py-2">Status</th>
                <th className="px-3 py-2">Verifier</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {loading ? (
                <tr><td colSpan={6} className="px-3 py-8 text-center text-slate-500">Loading…</td></tr>
              ) : rows.length === 0 ? (
                <tr><td colSpan={6} className="px-3 py-8 text-center text-slate-500">
                  No CAPAs yet — open one against a finding.
                </td></tr>
              ) : rows.map((c) => (
                <tr key={c.id} onClick={() => setEditing(c)}
                    className="cursor-pointer hover:bg-slate-50">
                  <td className="px-3 py-2 font-mono text-xs">{c.ref}</td>
                  <td className="px-3 py-2">
                    <div className="font-medium" style={{ color: '#0D1B2A' }}>{c.title}</div>
                    {c.nonconformity && <div className="text-xs text-slate-500">{c.nonconformity}</div>}
                  </td>
                  <td className="px-3 py-2 text-xs text-slate-600">{c.owner}</td>
                  <td className="px-3 py-2 text-xs text-slate-600">{c.due_date || '—'}</td>
                  <td className="px-3 py-2">
                    <span className={`inline-block rounded px-2 py-0.5 text-[11px] ${STATUS_TONE[c.status]}`}>
                      {c.status_label}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-xs text-slate-600">{c.verifier || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {editing && (
          <CAPAEditor capa={editing} isNew={creating}
            onSave={save} onDelete={del} onClose={() => { setEditing(null); setCreating(false) }} />
        )}
      </main>
    </div>
  )
}

function CAPAEditor({
  capa, isNew, onSave, onDelete, onClose,
}: {
  capa: CAPA; isNew: boolean
  onSave: (c: Partial<CAPA> & { id?: string }) => void | Promise<void>
  onDelete: (id: string) => void | Promise<void>
  onClose: () => void
}) {
  const [c, setC] = useState<CAPA>(() => {
    const defaults: CAPA = {
      id: '', ref: '', title: '', nonconformity: '', root_cause: '',
      corrective_action: '', preventive_action: '',
      owner: '', due_date: null, verifier: '', verified_at: null, closed_at: null,
      status: 'draft', status_label: '', created_at: '',
      finding: null, finding_title: null, commandment: null, commandment_number: null,
    }
    return { ...defaults, ...capa }
  })
  const set = <K extends keyof CAPA>(k: K, v: CAPA[K]) => setC((p) => ({ ...p, [k]: v }))

  return (
    <div className="fixed inset-0 z-40 flex items-end bg-slate-900/50 sm:items-center sm:justify-center">
      <div className="w-full max-w-3xl rounded-t-lg bg-white p-6 shadow-xl sm:rounded-lg">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="font-serif text-xl font-semibold" style={{ color: '#0D1B2A' }}>
            {isNew ? 'New CAPA' : `Edit ${c.ref}`}
          </h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-700">
            <X size={20} />
          </button>
        </div>
        <div className="grid grid-cols-2 gap-3 text-sm">
          <Field label="Reference">
            <input value={c.ref} onChange={(e) => set('ref', e.target.value)}
              placeholder="CAPA-001"
              className="w-full rounded border border-slate-300 px-3 py-2 text-sm" />
          </Field>
          <Field label="Title">
            <input value={c.title} onChange={(e) => set('title', e.target.value)}
              className="w-full rounded border border-slate-300 px-3 py-2 text-sm" />
          </Field>
          <Field label="Nonconformity" full>
            <textarea value={c.nonconformity} onChange={(e) => set('nonconformity', e.target.value)}
              rows={2} className="w-full rounded border border-slate-300 px-3 py-2 text-sm" />
          </Field>
          <Field label="Root cause" full>
            <textarea value={c.root_cause} onChange={(e) => set('root_cause', e.target.value)}
              rows={2} className="w-full rounded border border-slate-300 px-3 py-2 text-sm"
              placeholder="5-whys / fishbone result" />
          </Field>
          <Field label="Corrective action" full>
            <textarea value={c.corrective_action} onChange={(e) => set('corrective_action', e.target.value)}
              rows={2} className="w-full rounded border border-slate-300 px-3 py-2 text-sm" />
          </Field>
          <Field label="Preventive action" full>
            <textarea value={c.preventive_action} onChange={(e) => set('preventive_action', e.target.value)}
              rows={2} className="w-full rounded border border-slate-300 px-3 py-2 text-sm" />
          </Field>
          <Field label="Owner">
            <input value={c.owner} onChange={(e) => set('owner', e.target.value)}
              className="w-full rounded border border-slate-300 px-3 py-2 text-sm" />
          </Field>
          <Field label="Due date">
            <input type="date" value={c.due_date ?? ''}
              onChange={(e) => set('due_date', e.target.value || null)}
              className="w-full rounded border border-slate-300 px-3 py-2 text-sm" />
          </Field>
          <Field label="Verifier">
            <input value={c.verifier} onChange={(e) => set('verifier', e.target.value)}
              className="w-full rounded border border-slate-300 px-3 py-2 text-sm" />
          </Field>
          <Field label="Status">
            <select value={c.status} onChange={(e) => set('status', e.target.value as CAPA['status'])}
              className="w-full rounded border border-slate-300 px-3 py-2 text-sm">
              <option value="draft">Draft</option>
              <option value="open">Open</option>
              <option value="in_progress">In progress</option>
              <option value="verification">Pending verification</option>
              <option value="closed">Closed</option>
            </select>
          </Field>
        </div>
        <div className="mt-6 flex justify-between">
          <div>{!isNew && (
            <button onClick={() => onDelete(c.id)}
              className="rounded border border-red-200 px-3 py-2 text-sm text-red-700 hover:bg-red-50">
              Delete
            </button>
          )}</div>
          <div className="flex gap-2">
            <button onClick={onClose} className="rounded border border-slate-300 px-3 py-2 text-sm">Cancel</button>
            <button onClick={() => onSave(c)} className="rounded px-3 py-2 text-sm text-white"
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
