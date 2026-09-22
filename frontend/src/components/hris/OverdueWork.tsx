'use client'

/**
 * OverdueWork — tell staff WHY their leave / loan / incentive suddenly needs a
 * CEO or CFO signature, and exactly which work caused it (CFO 2026-08-07).
 *
 * The gate shipped earlier the same day told people the rule but not the work:
 * one grey line of text on the leave screen, nothing at all on loans and
 * incentives, and no warning until after the whole form was filled in. Nobody
 * can clear work they have not been shown.
 *
 * Two pieces, both fed by the same server payload so every screen says the
 * same thing:
 *
 *   <OverdueBanner/>  — reads /hris/api/my-overdue-tasks/ and warns BEFORE the
 *                       form is filled in. Renders nothing when nothing is
 *                       overdue, so it never nags a person who is up to date.
 *   <OverdueModal/>   — the pop-up AFTER submitting, listing each task by name
 *                       and how late it is. Portalled to <body>: the dashboard
 *                       <main> is `relative z-[1]`, so an inline modal paints
 *                       BEHIND the sidebar (bug 5f2fca79).
 */
import { useEffect, useState } from 'react'
import { AlertTriangle, Clock, X } from 'lucide-react'
import { ModalPortal, Z_MODAL } from '@/components/ui/ModalPortal'
// The HRIS fetch helper, not apiFetch — apiFetch prefixes /api/v1 and this
// endpoint lives under /hris/api/. Same import the nbfira pages already use.
import { authedHrisFetch } from '@/app/(dashboard)/hris/_shared'

const NAVY = '#0D1B2A'
const AMBER_BG = '#FFFBEB'
const AMBER_BDR = '#FDE68A'
const AMBER_INK = '#92400E'

export interface OverdueTask {
  id: string
  title: string
  due: string
  days_overdue: number
}

/** The block every submit response carries. */
export interface OverdueInfo {
  needs_exec_signoff?: boolean
  overdue_count?: number
  overdue_days_threshold?: number
  overdue_tasks?: OverdueTask[]
  overdue_message?: string
}

function TaskList({ tasks }: { tasks: OverdueTask[] }) {
  return (
    <ul className="mt-2 space-y-1.5">
      {tasks.map(t => (
        <li key={t.id} className="flex items-start gap-2 text-[13px]">
          <Clock className="w-3.5 h-3.5 mt-0.5 shrink-0" style={{ color: AMBER_INK }} />
          <span style={{ color: AMBER_INK }}>
            {t.title}
            <span className="opacity-70"> — due {t.due}, </span>
            <b>{t.days_overdue} {t.days_overdue === 1 ? 'day' : 'days'} late</b>
          </span>
        </li>
      ))}
    </ul>
  )
}

/**
 * Warn BEFORE the form is filled in. Silent when the person is up to date.
 * `what` names the thing they are about to apply for, e.g. "leave".
 */
export function OverdueBanner({ what }: { what: string }) {
  const [info, setInfo] = useState<{ count: number; days_threshold: number; tasks: OverdueTask[] } | null>(null)

  useEffect(() => {
    let live = true
    authedHrisFetch('/hris/api/my-overdue-tasks/')
      .then(async r => {
        if (!r.ok) return
        const d = await r.json()
        if (live && d?.count) setInfo(d)
      })
      .catch(() => { /* never block the form on this */ })
    return () => { live = false }
  }, [])

  if (!info?.count) return null
  return (
    <div className="rounded-xl px-4 py-3 mb-4"
         style={{ background: AMBER_BG, border: `1px solid ${AMBER_BDR}` }}>
      <div className="flex items-start gap-2">
        <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" style={{ color: AMBER_INK }} />
        <div>
          <div className="text-sm font-semibold" style={{ color: AMBER_INK }}>
            Heads up — this {what} will need a CEO or CFO signature
          </div>
          <p className="text-[13px] mt-1" style={{ color: AMBER_INK }}>
            You have {info.count} {info.count === 1 ? 'task' : 'tasks'} more than{' '}
            {info.days_threshold} days past the due date. You can still apply, but it
            cannot be approved until an executive signs it off.
          </p>
          <TaskList tasks={info.tasks} />
          <a href="/tasks" className="inline-block mt-2.5 text-[13px] font-semibold underline"
             style={{ color: '#B04E00' }}>
            Open my tasks →
          </a>
        </div>
      </div>
    </div>
  )
}

/**
 * The pop-up AFTER submitting. Render only when `info.needs_exec_signoff`.
 * The request WAS accepted — this explains what happens next, so the wording
 * never says "blocked" or "rejected".
 */
export function OverdueModal({ info, onClose }: { info: OverdueInfo; onClose: () => void }) {
  // Escape closes it, like every other modal in omni.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  if (!info?.needs_exec_signoff) return null
  const tasks = info.overdue_tasks || []

  return (
    <ModalPortal>
      <div className="fixed inset-0 flex items-center justify-center p-4"
           style={{ zIndex: Z_MODAL, background: 'rgba(13,27,42,.55)' }}
           role="dialog" aria-modal="true" aria-labelledby="overdue-title"
           onClick={onClose}>
        <div className="w-full max-w-md rounded-2xl bg-white overflow-hidden shadow-2xl"
             onClick={e => e.stopPropagation()}>
          <div className="flex items-center justify-between px-5 py-4" style={{ background: NAVY }}>
            <div className="flex items-center gap-2">
              <AlertTriangle className="w-4 h-4" style={{ color: '#F4A623' }} />
              <h2 id="overdue-title" className="text-[15px] font-bold" style={{ color: '#F4A623' }}>
                Sent — but it needs a signature first
              </h2>
            </div>
            <button onClick={onClose} aria-label="Close" className="p-1 rounded hover:bg-white/10">
              <X className="w-4 h-4 text-white" />
            </button>
          </div>

          <div className="px-5 py-4">
            <p className="text-sm" style={{ color: '#374151' }}>
              {info.overdue_message}
            </p>
            <div className="rounded-xl px-3.5 py-3 mt-3"
                 style={{ background: AMBER_BG, border: `1px solid ${AMBER_BDR}` }}>
              <div className="text-[12px] font-bold uppercase tracking-wide"
                   style={{ color: AMBER_INK }}>
                Work that is past its due date
              </div>
              <TaskList tasks={tasks} />
            </div>
            <div className="flex gap-2 mt-4">
              <a href="/tasks"
                 className="flex-1 text-center rounded-lg py-2.5 text-sm font-bold text-white"
                 style={{ background: NAVY }}>
                Open my tasks
              </a>
              <button onClick={onClose}
                      className="flex-1 rounded-lg py-2.5 text-sm font-bold"
                      style={{ border: '1.5px solid #D1D5DB', color: '#374151' }}>
                Got it
              </button>
            </div>
          </div>
        </div>
      </div>
    </ModalPortal>
  )
}
