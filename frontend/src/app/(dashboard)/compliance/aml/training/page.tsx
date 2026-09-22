'use client'

/**
 * /compliance/aml/training — AML/CFT training register.
 *
 * Two tabs, because the register alone answers the wrong question. "Who has
 * been trained" looks healthy the moment one person is recorded; the number
 * that matters is who is still OUTSTANDING, and that is what the objective
 * counts.
 */

import { useCallback, useEffect, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { apiFetch } from '@/lib/api'
import { fetchAllPages } from '@/lib/fetchAllPages'
import { localYmd } from '@/lib/utils'
import { GraduationCap, Plus, X } from 'lucide-react'

interface TrainingRecord {
  id: string
  employee: number
  employee_name: string
  course: string
  completed_on: string
  score: number | null
  valid_until: string
  is_current: boolean
}

interface Outstanding { employee: number; name: string; last_training: string | null }

export default function AMLTrainingPage() {
  const [tab, setTab] = useState<'outstanding' | 'recorded'>('outstanding')
  const [rows, setRows] = useState<TrainingRecord[]>([])
  const [missing, setMissing] = useState<Outstanding[]>([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [recording, setRecording] = useState<Outstanding | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setErr(null)
    try {
      const [list, out] = await Promise.all([
        fetchAllPages<TrainingRecord>('/aml/training/'),
        apiFetch<{ count: number; results: Outstanding[] }>('/aml/training/outstanding/'),
      ])
      setRows(list)
      setMissing(out?.results ?? [])
    } catch (e: any) { setErr(e?.message || 'Load failed') }
    finally { setLoading(false) }
  }, [])
  useEffect(() => { void load() }, [load])

  const save = async (r: { employee: number; course: string; completed_on: string; score: string }) => {
    try {
      await apiFetch('/aml/training/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          employee: r.employee, course: r.course, completed_on: r.completed_on,
          score: r.score === '' ? null : Number(r.score),
        }),
      })
      setRecording(null); await load()
    } catch (e: any) { setErr(e?.message || 'Save failed') }
  }

  return (
    <div className="min-h-screen bg-slate-50">
      <TopBar />
      <main className="mx-auto max-w-7xl px-6 py-8">
        <div className="mb-6">
          <div className="flex items-center gap-3">
            <GraduationCap size={28} style={{ color: '#0D1B2A' }} />
            <h1 className="text-2xl font-serif font-semibold" style={{ color: '#0D1B2A' }}>
              AML Training Register
            </h1>
          </div>
          <p className="mt-1 text-sm text-slate-600">
            Training counts for twelve months. After that the person is outstanding again.
          </p>
        </div>

        {err && (
          <div className="mb-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
            {err}
          </div>
        )}

        <div className="mb-4 flex gap-2">
          <TabButton active={tab === 'outstanding'} onClick={() => setTab('outstanding')}>
            Outstanding ({missing.length})
          </TabButton>
          <TabButton active={tab === 'recorded'} onClick={() => setTab('recorded')}>
            Recorded ({rows.length})
          </TabButton>
        </div>

        <div className="overflow-x-auto rounded-md border border-slate-200 bg-white">
          {tab === 'outstanding' ? (
            <table className="min-w-full text-sm">
              <thead className="bg-slate-100 text-left text-xs uppercase tracking-wide text-slate-600">
                <tr>
                  <th className="px-3 py-2">Staff member</th>
                  <th className="px-3 py-2">Last trained</th>
                  <th className="px-3 py-2"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {loading ? (
                  <tr><td colSpan={3} className="px-3 py-8 text-center text-slate-500">Loading…</td></tr>
                ) : missing.length === 0 ? (
                  <tr><td colSpan={3} className="px-3 py-8 text-center text-emerald-700">
                    Everyone on the active payroll has current AML training.
                  </td></tr>
                ) : missing.map((m) => (
                  <tr key={m.employee} className="hover:bg-slate-50">
                    <td className="px-3 py-2 font-medium" style={{ color: '#0D1B2A' }}>{m.name}</td>
                    <td className="px-3 py-2 text-xs text-slate-600">
                      {m.last_training ? `${m.last_training} (lapsed)` : 'Never'}
                    </td>
                    <td className="px-3 py-2 text-right">
                      <button onClick={() => setRecording(m)}
                        className="inline-flex items-center gap-1 rounded px-2 py-1 text-xs text-white"
                        style={{ background: '#F4A623' }}>
                        <Plus size={12} /> Record training
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <table className="min-w-full text-sm">
              <thead className="bg-slate-100 text-left text-xs uppercase tracking-wide text-slate-600">
                <tr>
                  <th className="px-3 py-2">Staff member</th>
                  <th className="px-3 py-2">Course</th>
                  <th className="px-3 py-2">Completed</th>
                  <th className="px-3 py-2">Valid until</th>
                  <th className="px-3 py-2">Score</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {loading ? (
                  <tr><td colSpan={5} className="px-3 py-8 text-center text-slate-500">Loading…</td></tr>
                ) : rows.length === 0 ? (
                  <tr><td colSpan={5} className="px-3 py-8 text-center text-slate-500">
                    Nothing recorded yet.
                  </td></tr>
                ) : rows.map((r) => (
                  <tr key={r.id} className="hover:bg-slate-50">
                    <td className="px-3 py-2 font-medium" style={{ color: '#0D1B2A' }}>{r.employee_name}</td>
                    <td className="px-3 py-2 text-xs text-slate-600">{r.course}</td>
                    <td className="px-3 py-2 text-xs text-slate-600">{r.completed_on}</td>
                    <td className="px-3 py-2 text-xs">
                      <span className={r.is_current ? 'text-slate-600' : 'text-red-700'}>
                        {r.valid_until}{r.is_current ? '' : ' (lapsed)'}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-xs text-slate-600">{r.score ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {recording && (
          <RecordDialog person={recording} onSave={save} onClose={() => setRecording(null)} />
        )}
      </main>
    </div>
  )
}

function TabButton({ active, onClick, children }: {
  active: boolean; onClick: () => void; children: React.ReactNode
}) {
  return (
    <button onClick={onClick}
      className={`rounded-md px-3 py-2 text-sm ${active ? 'text-white' : 'border border-slate-300 text-slate-700'}`}
      style={active ? { background: '#0D1B2A' } : undefined}>
      {children}
    </button>
  )
}

function RecordDialog({
  person, onSave, onClose,
}: {
  person: Outstanding
  onSave: (r: { employee: number; course: string; completed_on: string; score: string }) => void | Promise<void>
  onClose: () => void
}) {
  const [course, setCourse] = useState('AML/CFT awareness')
  const [completedOn, setCompletedOn] = useState(localYmd())
  const [score, setScore] = useState('')

  return (
    <div className="fixed inset-0 z-40 flex items-end bg-slate-900/50 sm:items-center sm:justify-center">
      <div className="w-full max-w-lg rounded-t-lg bg-white p-6 shadow-xl sm:rounded-lg">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="font-serif text-xl font-semibold" style={{ color: '#0D1B2A' }}>
            Record training — {person.name}
          </h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-700"><X size={20} /></button>
        </div>
        <div className="space-y-3 text-sm">
          <div>
            <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500">Course</label>
            <input value={course} onChange={(e) => setCourse(e.target.value)}
              className="w-full rounded border border-slate-300 px-3 py-2 text-sm" />
          </div>
          <div>
            <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500">Completed on</label>
            <input type="date" value={completedOn} onChange={(e) => setCompletedOn(e.target.value)}
              className="w-full rounded border border-slate-300 px-3 py-2 text-sm" />
          </div>
          <div>
            <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500">Score (optional)</label>
            <input type="number" value={score} onChange={(e) => setScore(e.target.value)}
              className="w-full rounded border border-slate-300 px-3 py-2 text-sm" />
          </div>
        </div>
        <div className="mt-6 flex justify-end gap-2">
          <button onClick={onClose} className="rounded border border-slate-300 px-3 py-2 text-sm">Cancel</button>
          <button onClick={() => onSave({ employee: person.employee, course, completed_on: completedOn, score })}
            className="rounded px-3 py-2 text-sm text-white" style={{ background: '#0D1B2A' }}>Save</button>
        </div>
      </div>
    </div>
  )
}
