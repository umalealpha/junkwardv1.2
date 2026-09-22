'use client'

/**
 * /hris/career-track — Career Tracks (promotion-readiness records).
 *
 * CFO directive 2026-07-13: the path to the next level lives ON the
 * employee record — target role, status, and the concrete business
 * milestones that must be achieved before the move is considered.
 * HR-tier users can advance milestone status; talent-tier users view.
 */
import { useCallback, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { ChevronLeft, Route } from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { useHrisAccess } from '@/hooks/useHrisAccess'
import { authedHrisFetch } from '../_shared'

interface Milestone {
  id: string
  order: number
  description: string
  status: string
  evidence: string
}

interface Track {
  id: string
  employee: { id: string; name: string; number: string; title: string; department: string }
  target_role: string
  status: string
  context: string
  created_by: string
  created_at: string | null
  milestones: Milestone[]
}

const MS_LABEL: Record<string, string> = {
  pending: 'Pending', in_progress: 'In progress', done: 'Done',
}
const MS_CHIP: Record<string, string> = {
  pending: 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300',
  in_progress: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
  done: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300',
}
const MS_NEXT: Record<string, string> = {
  pending: 'in_progress', in_progress: 'done', done: 'pending',
}
const TRACK_CHIP: Record<string, string> = {
  active: 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300',
  achieved: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300',
  on_hold: 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300',
}

export default function CareerTrackPage() {
  const router = useRouter()
  const allowed = useHrisAccess()
  const [tracks, setTracks] = useState<Track[]>([])
  const [canManage, setCanManage] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')

  useEffect(() => {
    if (allowed === false) router.replace('/dashboard')
  }, [allowed, router])

  const load = useCallback(() => {
    authedHrisFetch('/hris/api/talent/career-tracks/')
      .then(async r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then(d => {
        setTracks(d.tracks || [])
        setCanManage(!!d.can_manage)
        setError('')
      })
      .catch(e => setError(String(e?.message || e)))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (allowed !== true) return
    setLoading(true)
    load()
  }, [allowed, load])

  const cycleStatus = async (m: Milestone) => {
    if (!canManage || busy) return
    setBusy(m.id)
    try {
      const r = await authedHrisFetch(
        `/hris/api/talent/career-tracks/milestones/${m.id}/`,
        {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ status: MS_NEXT[m.status] || 'pending' }),
        },
      )
      if (r.ok) load()
    } finally {
      setBusy('')
    }
  }

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-950">
      <TopBar />
      <main className="mx-auto max-w-5xl px-4 py-6">
        <div className="mb-6 flex items-center gap-3">
          <Link
            href="/hris"
            className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-gray-800 dark:hover:text-gray-200"
          >
            <ChevronLeft className="h-4 w-4" /> HRIS
          </Link>
          <h1 className="flex items-center gap-2 text-xl font-semibold text-gray-900 dark:text-gray-100">
            <Route className="h-5 w-5 text-blue-600" /> Career Tracks
          </h1>
        </div>
        <p className="mb-6 text-sm text-gray-500 dark:text-gray-400">
          The path to the next level, on the record: target role and the
          business milestones that must be achieved before the move.
          {canManage ? ' Click a milestone status to advance it.' : ''}
        </p>

        {loading && (
          <div className="rounded-xl border border-gray-200 bg-white p-6 text-sm text-gray-500 dark:border-gray-800 dark:bg-gray-900">
            Loading…
          </div>
        )}
        {!loading && error && (
          <div className="rounded-xl border border-red-200 bg-red-50 p-6 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
            Could not load career tracks ({error}).
          </div>
        )}
        {!loading && !error && tracks.length === 0 && (
          <div className="rounded-xl border border-gray-200 bg-white p-6 text-sm text-gray-500 dark:border-gray-800 dark:bg-gray-900">
            No career tracks recorded yet.
          </div>
        )}

        <div className="space-y-5">
          {tracks.map(t => (
            <div
              key={t.id}
              className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm dark:border-gray-800 dark:bg-gray-900"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <div className="text-base font-semibold text-gray-900 dark:text-gray-100">
                    {t.employee.name}
                    <span className="ml-2 text-sm font-normal text-gray-500">
                      {t.employee.title}
                      {t.employee.department ? ` · ${t.employee.department}` : ''}
                    </span>
                  </div>
                  <div className="mt-1 text-sm text-gray-600 dark:text-gray-300">
                    Target role:{' '}
                    <span className="font-medium text-gray-900 dark:text-gray-100">
                      {t.target_role}
                    </span>
                  </div>
                </div>
                <span
                  className={`rounded-full px-3 py-1 text-xs font-medium ${TRACK_CHIP[t.status] || TRACK_CHIP.on_hold}`}
                >
                  {t.status === 'on_hold' ? 'On hold' : t.status.charAt(0).toUpperCase() + t.status.slice(1)}
                </span>
              </div>

              {t.context && (
                <p className="mt-3 whitespace-pre-line text-sm leading-relaxed text-gray-600 dark:text-gray-300">
                  {t.context}
                </p>
              )}

              <div className="mt-4 space-y-2">
                {t.milestones.map(m => (
                  <div
                    key={m.id}
                    className="flex items-start justify-between gap-3 rounded-lg border border-gray-100 bg-gray-50 px-3 py-2 dark:border-gray-800 dark:bg-gray-950"
                  >
                    <div className="text-sm text-gray-800 dark:text-gray-200">
                      <span className="mr-2 font-semibold text-gray-400">{m.order}.</span>
                      {m.description}
                      {m.evidence && (
                        <div className="mt-1 text-xs text-gray-500">
                          Evidence: {m.evidence}
                        </div>
                      )}
                    </div>
                    <button
                      type="button"
                      onClick={() => cycleStatus(m)}
                      disabled={!canManage || busy === m.id}
                      className={`shrink-0 rounded-full px-3 py-1 text-xs font-medium ${MS_CHIP[m.status] || MS_CHIP.pending} ${canManage ? 'cursor-pointer hover:opacity-80' : 'cursor-default'}`}
                      title={canManage ? 'Click to advance status' : undefined}
                    >
                      {MS_LABEL[m.status] || m.status}
                    </button>
                  </div>
                ))}
              </div>

              {t.created_by && (
                <div className="mt-3 text-xs text-gray-400">
                  Opened by {t.created_by}
                  {t.created_at ? ` on ${t.created_at.slice(0, 10)}` : ''}
                </div>
              )}
            </div>
          ))}
        </div>
      </main>
    </div>
  )
}
