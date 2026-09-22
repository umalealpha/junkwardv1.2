'use client'

/**
 * /hris/team-dialogues — a manager's TEAM development dialogues (read-only).
 *
 * Self-service surface, server-scoped to the caller's downward reporting chain
 * (HRISProfile.manager). A non-manager sees an empty state; a manager sees only
 * their own reports — never sideways or up, and no payroll. Read-only.
 */
import { useEffect, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { DialogueView, type Person } from '@/components/hris/DialogueView'
import { authedHrisFetch } from '../_shared'

const ORANGE = '#F07F00'
const HISTORY_API = '/hris/api/talent/history/'

type HistRow = { ref: string; period?: string; is_current?: boolean; overall?: number | null; rating?: string }

export default function TeamDialoguesPage() {
  const [people, setPeople] = useState<Person[] | undefined>(undefined)
  const [open, setOpen] = useState<number | null>(0)
  const [err, setErr] = useState('')
  const [hist, setHist] = useState<Record<string, HistRow[]>>({})

  useEffect(() => {
    authedHrisFetch('/hris/api/talent/team/')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status))))
      .then((d) => setPeople(Array.isArray(d.people) ? d.people : []))
      .catch(() => setErr('Could not load your team. Please refresh.'))
  }, [])

  // CFO/Unami directive 2026-07-23 (item 5b): let a manager drill into the open
  // report's PRIOR periods, read-only. Lazy-loaded per personKey via the
  // existing history endpoint — the manager's scope already covers reports.
  useEffect(() => {
    if (open == null || !people || !people[open]) return
    const key = (people[open] as { personKey?: string }).personKey
    if (!key || key in hist) return
    authedHrisFetch(HISTORY_API + '?key=' + encodeURIComponent(key))
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status))))
      .then((d) => setHist((m) => ({ ...m, [key]: Array.isArray(d.periods) ? d.periods.filter((x: HistRow) => !x.is_current) : [] })))
      .catch(() => setHist((m) => ({ ...m, [key]: [] })))
  }, [open, people, hist])

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-950">
      <TopBar />
      <div className="flex items-center gap-3 border-b border-gray-200 bg-white px-4 py-3 dark:border-gray-800 dark:bg-gray-900 print:hidden">
        <div className="flex-1">
          <h1 className="text-[15px] font-bold text-[#0B0B3B] dark:text-gray-100">My Team&apos;s Development Dialogues</h1>
          <p className="text-[11px] text-gray-500">The people who report to you — their reviews, read-only</p>
        </div>
        {people && people.length > 0 && (
          <button onClick={() => window.print()} className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs font-semibold text-gray-600 dark:border-gray-700 dark:text-gray-300">
            Print / PDF
          </button>
        )}
      </div>

      <main className="mx-auto max-w-4xl px-4 py-6">
        {err && <div className="rounded-lg bg-red-50 p-4 text-sm text-red-700">{err}</div>}
        {!err && people === undefined && <div className="text-sm text-gray-500">Loading…</div>}
        {!err && people && people.length === 0 && (
          <div className="rounded-xl border border-gray-200 bg-white p-8 text-center text-sm text-gray-500 dark:border-gray-800 dark:bg-gray-900">
            You have no team reviews to view. When someone who reports to you has a Development Dialogue on file, it will appear here.
          </div>
        )}
        {people && people.length > 0 && (
          <div className="space-y-3">
            {people.map((p, i) => {
              const isOpen = open === i
              const personKey = (p as { personKey?: string }).personKey
              const prev = personKey ? hist[personKey] : undefined
              return (
                <div key={i} className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm dark:border-gray-800 dark:bg-gray-900">
                  <button
                    onClick={() => setOpen(isOpen ? null : i)}
                    className="flex w-full items-center gap-3 px-4 py-3 text-left"
                  >
                    <span className="flex h-9 w-9 flex-none items-center justify-center rounded-lg text-xs font-extrabold text-white"
                          style={{ background: ORANGE }}>
                      {(p.name || '?').split(' ').map((s) => s[0]).slice(0, 2).join('').toUpperCase()}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-bold text-[#0B0B3B] dark:text-gray-100">{p.name}</span>
                      <span className="block truncate text-[11px] text-gray-500">{p.position}</span>
                    </span>
                    <span className="flex-none text-sm font-extrabold text-[#0B0B3B] dark:text-gray-100">
                      {p.overall != null ? `${p.overall}%` : '—'}
                    </span>
                    <span className="flex-none text-gray-400">{isOpen ? '▾' : '▸'}</span>
                  </button>
                  {isOpen && (
                    <div className="border-t border-gray-100 bg-gray-50 p-4 dark:border-gray-800 dark:bg-gray-950">
                      <DialogueView person={p} />
                      {/* CFO/Unami directive 2026-07-23 (item 5b): this report's prior periods, read-only. */}
                      {prev && prev.length > 0 && (
                        <div className="mt-4 rounded-lg border border-gray-200 bg-white p-3 dark:border-gray-800 dark:bg-gray-900">
                          <div className="mb-2 text-[12px] font-bold text-[#0B0B3B] dark:text-gray-100">Previous periods</div>
                          <ul className="divide-y divide-gray-100 dark:divide-gray-800">
                            {prev.map((h) => (
                              <li key={h.ref} className="flex items-center justify-between py-1.5">
                                <span className="text-[12px] font-semibold text-gray-700 dark:text-gray-300">{h.period || '—'}</span>
                                <span className="flex items-center gap-3">
                                  {h.rating ? <span className="text-[11px] text-gray-500">{h.rating}</span> : null}
                                  <span className="text-[13px] font-extrabold text-[#0B0B3B] dark:text-gray-100">{h.overall != null ? `${h.overall}%` : '—'}</span>
                                </span>
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </main>
    </div>
  )
}
