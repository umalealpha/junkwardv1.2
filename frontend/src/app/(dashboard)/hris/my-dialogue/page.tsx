'use client'

/**
 * /hris/my-dialogue — a staff member's OWN Development Dialogue.
 *
 * Self-service: any logged-in employee, no HR whitelist, no payroll exposure —
 * the API returns ONLY the caller's own record. The employee can complete their
 * self-assessment (their own scores + comments) while the period is open, sign
 * it off, and print it. Manager scores/weights/rating stay read-only here.
 */
import { useCallback, useEffect, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { DialogueView, type Person } from '@/components/hris/DialogueView'
import { authedHrisFetch } from '../_shared'

const NAVY = '#0B0B3B'
const ORANGE = '#F07F00'
const API = '/hris/api/talent/my-dialogue/'
const HISTORY_API = '/hris/api/talent/history/'

type DraftRow = { perspective?: string; employee?: number | null; selfComment?: string }
type DraftVal = { value?: string; self?: number | null }
type HistRow = { ref: string; period?: string; is_current?: boolean; overall?: number | null; rating?: string }

export default function MyDialoguePage() {
  const [person, setPerson] = useState<Person | null | undefined>(undefined)
  const [draft, setDraft] = useState<Person | null>(null)
  const [status, setStatus] = useState('')
  const [err, setErr] = useState('')
  const [history, setHistory] = useState<HistRow[] | null>(null)

  const reload = useCallback(() => {
    authedHrisFetch(API)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status))))
      .then((d) => {
        setPerson(d.person ?? null)
        setDraft(d.person ? JSON.parse(JSON.stringify(d.person)) : null)
      })
      .catch(() => setErr('Could not load your dialogue. Please refresh.'))
  }, [])

  useEffect(() => { reload() }, [reload])

  // CFO/Unami directive 2026-07-23 (item 5b): surface the employee's OWN
  // prior-year dialogues, read-only. The history endpoint is keyed by
  // personKey and the backend lets an employee read only their own record.
  const personKey = (person as { personKey?: string })?.personKey
  useEffect(() => {
    if (!personKey) { setHistory(null); return }
    authedHrisFetch(HISTORY_API + '?key=' + encodeURIComponent(personKey))
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status))))
      .then((d) => setHistory(Array.isArray(d.periods) ? d.periods : []))
      .catch(() => setHistory(null))
  }, [personKey])

  const locked = !!(person && (person as { locked?: boolean }).locked)
  const signoff = (person && (person as { signoff?: Record<string, { by?: string; at?: string }> }).signoff) || {}
  const dd = (draft?.dd || {}) as { sections?: { name?: string; rows?: DraftRow[] }[]; values?: DraftVal[] }
  // Only PRIOR periods here — the current one is shown in full above.
  const prevReviews = (history || []).filter((h) => !h.is_current)

  function setRow(si: number, ri: number, key: 'employee' | 'selfComment', val: number | string) {
    setDraft((d) => {
      if (!d) return d
      const c = JSON.parse(JSON.stringify(d))
      c.dd.sections[si].rows[ri][key] = key === 'employee' ? (val === '' ? null : Number(val)) : val
      return c
    })
  }
  function setVal(vi: number, val: number | string) {
    setDraft((d) => {
      if (!d) return d
      const c = JSON.parse(JSON.stringify(d))
      c.dd.values[vi].self = val === '' ? null : Number(val)
      return c
    })
  }
  function setSummary(val: string) {
    setDraft((d) => (d ? { ...d, selfSummary: val } : d))
  }

  const save = useCallback(async () => {
    setStatus('Saving…')
    try {
      const r = await authedHrisFetch(API, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ person: draft }),
      })
      if (!r.ok) throw new Error('HTTP ' + r.status)
      const d = await r.json()
      setPerson(d.person)
      setDraft(JSON.parse(JSON.stringify(d.person)))
      setStatus('Saved ✓')
    } catch {
      setStatus('Save failed — retry')
    }
  }, [draft])

  const sign = useCallback(async () => {
    if (!confirm('Sign off your self-assessment for this period? You confirm your inputs are complete.')) return
    setStatus('Signing…')
    try {
      const ref = (person as { _ref?: string })?._ref
      const r = await authedHrisFetch('/hris/api/talent/sign/', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ref, role: 'employee' }),
      })
      if (!r.ok) throw new Error('HTTP ' + r.status)
      setStatus('Signed ✓'); reload()
    } catch {
      setStatus('Could not sign — retry')
    }
  }, [person, reload])

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-950">
      <TopBar />
      <div className="flex items-center gap-3 border-b border-gray-200 bg-white px-4 py-3 dark:border-gray-800 dark:bg-gray-900 print:hidden">
        <div className="flex-1">
          <h1 className="text-[15px] font-bold text-[#0B0B3B] dark:text-gray-100">My Development Dialogue</h1>
          <p className="text-[11px] text-gray-500">Your performance review — private to you and HR</p>
        </div>
        <span className="text-xs font-semibold text-gray-500">{status}</span>
        {person && (
          <button onClick={() => window.print()} className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs font-semibold text-gray-600 dark:border-gray-700 dark:text-gray-300">
            Print / PDF
          </button>
        )}
      </div>

      <main className="mx-auto max-w-4xl px-4 py-6">
        {err && <div className="rounded-lg bg-red-50 p-4 text-sm text-red-700">{err}</div>}
        {!err && person === undefined && <div className="text-sm text-gray-500">Loading…</div>}
        {!err && person === null && (
          <div className="rounded-xl border border-gray-200 bg-white p-8 text-center text-sm text-gray-500 dark:border-gray-800 dark:bg-gray-900">
            No Development Dialogue is on file for your account yet. Your manager or HR will set one up.
          </div>
        )}

        {person && (
          <>
            {locked ? (
              <div className="mb-5 rounded-xl border border-green-200 bg-green-50 p-4 text-sm text-green-800 print:hidden">
                This review is <b>signed off and locked</b>{signoff.employee?.at ? ` (you signed ${signoff.employee.at})` : ''}. It is read-only.
              </div>
            ) : (
              <div className="mb-5 rounded-xl border border-[#f2d3b3] bg-[#fff8f1] p-5 shadow-sm dark:border-gray-800 dark:bg-gray-900 print:hidden">
                <h2 className="mb-1 text-sm font-extrabold text-[#0B0B3B] dark:text-gray-100">My self-assessment</h2>
                <p className="mb-4 text-[12px] text-gray-500">Rate yourself 0–1 (e.g. 0.8 = 80%) and add your comments. Your manager reviews these.</p>
                {(dd.sections || []).map((sec, si) => (
                  <div key={si} className="mb-4">
                    <div className="mb-2 text-[12px] font-bold" style={{ color: ORANGE }}>{sec.name}</div>
                    {(sec.rows || []).map((r, ri) => (
                      <div key={ri} className="mb-3 rounded-lg border border-gray-100 p-3 dark:border-gray-800">
                        <div className="mb-1 text-[13px] font-semibold text-gray-800 dark:text-gray-200">{r.perspective}</div>
                        <div className="flex flex-wrap items-center gap-2">
                          <label className="text-[11px] text-gray-500">Your score</label>
                          <input type="number" min={0} max={1} step={0.05} value={r.employee ?? ''}
                            onChange={(e) => setRow(si, ri, 'employee', e.target.value)}
                            className="w-20 rounded border border-gray-300 px-2 py-1 text-sm bg-white text-gray-900 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100" />
                          <input type="text" placeholder="Your comment (optional)" value={r.selfComment ?? ''}
                            onChange={(e) => setRow(si, ri, 'selfComment', e.target.value)}
                            className="min-w-[200px] flex-1 rounded border border-gray-300 px-2 py-1 text-sm bg-white text-gray-900 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100" />
                        </div>
                      </div>
                    ))}
                  </div>
                ))}
                {(dd.values || []).length > 0 && (
                  <div className="mb-3">
                    <div className="mb-2 text-[12px] font-bold" style={{ color: ORANGE }}>Values — self score</div>
                    <div className="flex flex-wrap gap-3">
                      {(dd.values || []).map((v, vi) => (
                        <label key={vi} className="flex items-center gap-2 text-[12px] text-gray-700 dark:text-gray-300">
                          {v.value}
                          <input type="number" min={0} max={1} step={0.05} value={v.self ?? ''}
                            onChange={(e) => setVal(vi, e.target.value)}
                            className="w-16 rounded border border-gray-300 px-2 py-1 text-sm bg-white text-gray-900 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100" />
                        </label>
                      ))}
                    </div>
                  </div>
                )}
                <label className="mb-1 block text-[11px] font-bold uppercase tracking-wide text-gray-400">My overall comment</label>
                <textarea value={(draft as { selfSummary?: string })?.selfSummary ?? ''} onChange={(e) => setSummary(e.target.value)}
                  className="min-h-[70px] w-full rounded-lg border border-gray-300 p-2 text-sm bg-white text-gray-900 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100" />
                <div className="mt-3 flex gap-2">
                  <button onClick={save} className="rounded-lg px-4 py-2 text-sm font-bold text-white" style={{ background: NAVY }}>Save my self-assessment</button>
                  <button onClick={sign} className="rounded-lg border px-4 py-2 text-sm font-bold" style={{ borderColor: '#1f9d57', color: '#1f9d57' }}>Sign off</button>
                </div>
              </div>
            )}

            <DialogueView person={person} />

            {/* CFO/Unami directive 2026-07-23 (item 5b): prior-year reviews, read-only. */}
            {prevReviews.length > 0 && (
              <section className="mt-6 rounded-xl border border-gray-200 bg-white p-5 shadow-sm dark:border-gray-800 dark:bg-gray-900">
                <h2 className="mb-1 text-sm font-extrabold text-[#0B0B3B] dark:text-gray-100">Previous reviews</h2>
                <p className="mb-3 text-[12px] text-gray-500">Your earlier appraisal periods — read-only.</p>
                <ul className="divide-y divide-gray-100 dark:divide-gray-800">
                  {prevReviews.map((h) => (
                    <li key={h.ref} className="flex items-center justify-between py-2">
                      <span className="text-[13px] font-semibold text-gray-800 dark:text-gray-200">{h.period || '—'}</span>
                      <span className="flex items-center gap-3">
                        {h.rating ? <span className="text-[11px] text-gray-500">{h.rating}</span> : null}
                        <span className="text-sm font-extrabold text-[#0B0B3B] dark:text-gray-100">{h.overall != null ? `${h.overall}%` : '—'}</span>
                      </span>
                    </li>
                  ))}
                </ul>
              </section>
            )}
            <p className="pb-8 pt-5 text-center text-[11px] text-gray-400 print:hidden">
              This is your own review, private to you and HR. To discuss it, speak to your manager.
            </p>
          </>
        )}
      </main>
    </div>
  )
}
