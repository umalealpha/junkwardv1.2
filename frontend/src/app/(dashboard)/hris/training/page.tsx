'use client'

/**
 * /hris/training — Human Capital's side of the Induction & Training Academy.
 *
 * CFO directive 2026-09-20: "an Upload Here button where they can upload any
 * information in PDF or Word and put AI to understand and create trainings."
 * That button is here. HR drops in a document, the model drafts the slides and
 * the question bank, HR reads the draft and publishes it. Publishing is what
 * cuts the 15 different exam papers.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import {
  CheckCircle2, ChevronLeft, FileUp, GraduationCap, Loader2, Users,
} from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { authedHrisFetch } from '../_shared'

interface Course {
  slug: string
  title: string
  summary: string
  status: string
  source: string
  is_induction: boolean
  duration_minutes: number
  questions_per_paper: number
  version_count: number
  pass_mark: number
  slide_count: number
  bank_size: number
  papers_built: number
  papers_issued: number
  ai_generated: boolean
  ai_notes: string
  source_filename: string
}

interface ResultRow {
  employee_id: string
  name: string
  status: string
  attempts: number
  percent: number | null
  passed: boolean
}

export default function TrainingAdminPage() {
  const [courses, setCourses] = useState<Course[]>([])
  const [loading, setLoading] = useState(true)
  const [denied, setDenied] = useState(false)
  const [busy, setBusy] = useState('')
  const [note, setNote] = useState('')
  const [error, setError] = useState('')
  const [results, setResults] = useState<{ slug: string; rows: ResultRow[] } | null>(null)
  const fileRef = useRef<HTMLInputElement | null>(null)

  const load = useCallback(async () => {
    try {
      const r = await authedHrisFetch('/hris/api/training/courses/')
      if (r.status === 403) { setDenied(true); return }
      if (!r.ok) throw new Error('HTTP ' + r.status)
      const d = await r.json()
      setCourses(d.courses || [])
    } catch {
      setError('Could not load the courses — refresh to retry.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  async function upload(file: File) {
    setBusy('upload'); setError(''); setNote('')
    const body = new FormData()
    body.append('file', file)
    try {
      const r = await authedHrisFetch('/hris/api/training/upload/', { method: 'POST', body })
      const d = await r.json()
      if (!r.ok) { setError(d.detail || 'The upload could not be turned into a course.'); return }
      const rep = d.report || {}
      setNote(
        `Draft course created from ${file.name}: ${rep.slides_kept} slides and ` +
        `${rep.questions_kept} questions kept, ${rep.questions_rejected} rejected. ` +
        `Read it through, then press Publish.`,
      )
      await load()
    } catch {
      setError('The upload failed. Please try again.')
    } finally {
      setBusy('')
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  async function publish(slug: string) {
    setBusy(slug); setError(''); setNote('')
    try {
      const r = await authedHrisFetch(`/hris/api/training/courses/${slug}/publish/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      })
      const d = await r.json()
      if (!r.ok) { setError(d.detail || 'Could not publish.'); return }
      setNote(`Published — ${d.papers_built} different exam papers are now in circulation.`)
      await load()
    } catch {
      setError('Could not publish — please try again.')
    } finally { setBusy('') }
  }

  async function assignEveryone(slug: string) {
    setBusy(slug); setError(''); setNote('')
    try {
      const r = await authedHrisFetch(`/hris/api/training/courses/${slug}/assign/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ everyone: true }),
      })
      const d = await r.json()
      if (!r.ok) { setError(d.detail || 'Could not assign.'); return }
      setNote(`Assigned to ${d.assigned} new people (${d.total} active staff in total).`)
    } catch {
      setError('Could not assign — please try again.')
    } finally { setBusy('') }
  }

  async function openResults(slug: string) {
    setBusy(slug)
    try {
      const r = await authedHrisFetch(`/hris/api/training/courses/${slug}/results/`)
      if (!r.ok) { setError('Could not load the results.'); return }
      const d = await r.json()
      setResults({ slug, rows: d.rows || [] })
    } finally { setBusy('') }
  }

  if (denied) {
    return (
      <div className="min-h-screen bg-[var(--t-bg)]">
        <TopBar />
        <div className="mx-auto max-w-3xl px-4 py-16 text-center text-sm text-[var(--t-muted)]">
          This page is for the Human Capital team. If you are looking for your own
          induction, it is at <Link className="underline" href="/hris/induction">Induction</Link>.
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-[var(--t-bg)]">
      <TopBar />
      <div className="mx-auto max-w-5xl px-4 py-6">
        <div className="mb-5 flex items-center gap-3">
          <Link href="/hris" className="inline-flex items-center gap-1 text-sm text-[var(--t-muted)] hover:text-[var(--t-text)]">
            <ChevronLeft className="h-4 w-4" />
            People
          </Link>
          <span className="text-[var(--t-muted)]">/</span>
          <span className="inline-flex items-center gap-2 text-sm font-medium">
            <GraduationCap className="h-4 w-4" />
            Training Academy
          </span>
        </div>

        {/* Upload Here */}
        <div className="mb-6 rounded-xl border border-dashed border-[var(--t-border)] bg-[var(--t-card)] p-6">
          <h2 className="text-base font-semibold">Upload Here</h2>
          <p className="mt-1 max-w-2xl text-sm text-[var(--t-muted)]">
            Drop in any policy, manual or handbook as a PDF or Word file. Omni reads it
            and drafts the training slides and the exam questions for you.{' '}
            <strong>
              The AI can still get a question wrong — read every one before you press
              Publish.
            </strong>{' '}
            Nothing is visible to staff until you do. Very long documents are read in
            part; the course notes say how much was used.
          </p>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <input
              ref={fileRef}
              type="file"
              accept=".pdf,.docx,.txt,.md"
              className="hidden"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f) }}
            />
            <button
              type="button"
              disabled={busy === 'upload'}
              onClick={() => fileRef.current?.click()}
              className="inline-flex items-center gap-2 rounded-lg bg-[#F07F00] px-4 py-2 text-sm font-semibold text-[#1b1000] transition hover:brightness-105 disabled:opacity-50"
            >
              {busy === 'upload'
                ? <><Loader2 className="h-4 w-4 animate-spin" /> Reading the document…</>
                : <><FileUp className="h-4 w-4" /> Upload a document</>}
            </button>
            <span className="text-xs text-[var(--t-muted)]">
              PDF or Word, up to 25 MB. A long manual takes a minute or two.
            </span>
          </div>
        </div>

        {note && (
          <div className="mb-4 rounded-lg border border-[#cfe8d9] bg-[#f2fbf6] px-4 py-3 text-sm text-[#0f5f43]">
            {note}
          </div>
        )}
        {error && (
          <div className="mb-4 rounded-lg border border-[#f3cfc8] bg-[#fdf1ef] px-4 py-3 text-sm text-[#8a2c19]">
            {error}
          </div>
        )}

        {loading && <div className="py-10 text-center text-sm text-[var(--t-muted)]">Loading…</div>}

        <div className="grid gap-4">
          {courses.map((c) => (
            <div key={c.slug} className="rounded-xl border border-[var(--t-border)] bg-[var(--t-card)] p-5">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="text-base font-semibold">{c.title}</h3>
                    {c.is_induction && (
                      <span className="rounded-full bg-[#0B0B3B] px-2 py-0.5 text-[11px] font-semibold text-white">
                        Induction
                      </span>
                    )}
                    <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${
                      c.status === 'published'
                        ? 'bg-[#e9f7f0] text-[#0f8a5f]'
                        : 'bg-[#f2f2f8] text-[var(--t-muted)]'
                    }`}>
                      {c.status === 'published' ? 'Live' : 'Draft'}
                    </span>
                    {c.ai_generated && (
                      <span className="rounded-full bg-[#fff3e3] px-2 py-0.5 text-[11px] font-semibold text-[#8a5200]">
                        Drafted by AI
                      </span>
                    )}
                  </div>
                  <p className="mt-1 text-sm text-[var(--t-muted)]">{c.summary}</p>
                  <dl className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs text-[var(--t-muted)]">
                    <div><dt className="inline">Slides </dt><dd className="inline font-semibold text-[var(--t-text)]">{c.slide_count}</dd></div>
                    <div><dt className="inline">Question bank </dt><dd className="inline font-semibold text-[var(--t-text)]">{c.bank_size}</dd></div>
                    <div><dt className="inline">Papers </dt><dd className="inline font-semibold text-[var(--t-text)]">{c.papers_built}</dd></div>
                    <div><dt className="inline">Per paper </dt><dd className="inline font-semibold text-[var(--t-text)]">{c.questions_per_paper}</dd></div>
                    <div><dt className="inline">Pass mark </dt><dd className="inline font-semibold text-[var(--t-text)]">{c.pass_mark}%</dd></div>
                    <div><dt className="inline">Sat so far </dt><dd className="inline font-semibold text-[var(--t-text)]">{c.papers_issued}</dd></div>
                  </dl>
                  {c.ai_notes && (
                    <p className="mt-2 whitespace-pre-line text-xs text-[var(--t-muted)]">{c.ai_notes}</p>
                  )}
                </div>

                <div className="flex flex-wrap gap-2">
                  {c.status !== 'published' && (
                    <button
                      type="button"
                      disabled={busy === c.slug}
                      onClick={() => publish(c.slug)}
                      className="inline-flex items-center gap-1.5 rounded-lg bg-[#0B0B3B] px-3 py-1.5 text-sm font-medium text-white transition hover:brightness-125 disabled:opacity-50"
                    >
                      <CheckCircle2 className="h-4 w-4" /> Publish
                    </button>
                  )}
                  <button
                    type="button"
                    disabled={busy === c.slug}
                    onClick={() => assignEveryone(c.slug)}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--t-border)] px-3 py-1.5 text-sm transition hover:bg-[var(--t-bg)] disabled:opacity-50"
                  >
                    <Users className="h-4 w-4" /> Assign to all staff
                  </button>
                  <button
                    type="button"
                    disabled={busy === c.slug}
                    onClick={() => openResults(c.slug)}
                    className="rounded-lg border border-[var(--t-border)] px-3 py-1.5 text-sm transition hover:bg-[var(--t-bg)] disabled:opacity-50"
                  >
                    Who has passed
                  </button>
                </div>
              </div>

              {results?.slug === c.slug && (
                <div className="mt-4 overflow-x-auto rounded-lg border border-[var(--t-border)]">
                  <table className="w-full text-sm">
                    <thead className="bg-[var(--t-bg)] text-left text-xs uppercase tracking-wide text-[var(--t-muted)]">
                      <tr>
                        <th className="px-3 py-2 font-medium">Name</th>
                        <th className="px-3 py-2 font-medium">Status</th>
                        <th className="px-3 py-2 font-medium">Attempts</th>
                        <th className="px-3 py-2 font-medium">Best</th>
                      </tr>
                    </thead>
                    <tbody>
                      {results.rows.length === 0 && (
                        <tr><td colSpan={4} className="px-3 py-4 text-center text-[var(--t-muted)]">
                          Nobody has been assigned this course yet.
                        </td></tr>
                      )}
                      {results.rows.map((r) => (
                        <tr key={r.employee_id} className="border-t border-[var(--t-border)]">
                          <td className="px-3 py-2">{r.name}</td>
                          <td className="px-3 py-2">
                            <span className={r.passed ? 'text-[#0f8a5f]' : 'text-[var(--t-muted)]'}>
                              {r.passed ? 'Passed' : r.status === 'failed' ? 'Must re-sit' : 'Not done yet'}
                            </span>
                          </td>
                          <td className="px-3 py-2 tabular-nums">{r.attempts}</td>
                          <td className="px-3 py-2 tabular-nums">{r.percent === null ? '—' : `${r.percent}%`}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
