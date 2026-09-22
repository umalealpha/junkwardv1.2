'use client'

/**
 * TaskReminderGuard — global reminder surface (taskboard Phase 1, CFO 2026-07-09).
 *
 * Mounted once in the dashboard layout. Polls /taskboard/notifications/ every
 * 90s + on window focus (cost discipline: no websockets, no extra infra):
 *
 *   assign_day  -> gentle dismissible toast (POST ack clears it for good)
 *   due_day /   -> force-action modal listing the tasks; it cannot be closed
 *   overdue        for the first 3 seconds, then offers "remind me later"
 *                  (2h client-side snooze — the server keeps the reminder
 *                  standing, so it returns; it only clears on completion).
 *
 * Completion goes through TaskCompletionModal (the server-side gate).
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  getTaskReminders, ackTaskReminder, getMe,
  type TaskReminder,
} from '@/lib/api'
import { classifyApprovalClass, type ApprovalClass } from '@/lib/approvalProfiles'
import { TaskCompletionModal } from '@/components/TaskCompletionModal'
import { AlarmClock, Bell, X } from 'lucide-react'

const POLL_MS = 90_000
const SNOOZE_MS = 2 * 60 * 60 * 1000
const SNOOZE_KEY = 'taskboard_snooze_until'
// CFO, 15-Sep-2026: 10 seconds held him on every single load, on top of the new
// Welcome page "My Requests" block it covers. 3 still takes over the screen so an
// overdue task cannot be missed — it just stops being a wall every morning.
const UNLOCK_AFTER_S = 3
// A "new task" toast is a gentle FYI — it must not camp over the bottom-right
// of the page forever. Left standing it sat on top of page buttons there and
// swallowed the click (reported: the Company Cards "Preview" button could not
// be selected). Auto-clear it after a few seconds; the task itself still lives
// on the Task Dashboard and, once due, in the force-action modal.
const TOAST_AUTO_DISMISS_MS = 12_000

export function TaskReminderGuard() {
  const [reminders, setReminders] = useState<TaskReminder[]>([])
  const [completing, setCompleting] = useState<TaskReminder | null>(null)
  const [approvalClass, setApprovalClass] = useState<ApprovalClass>('general')
  const [openSeconds, setOpenSeconds] = useState(0)
  const [snoozedUntil, setSnoozedUntil] = useState<number>(() => {
    if (typeof window === 'undefined') return 0
    return Number(localStorage.getItem(SNOOZE_KEY) || 0)
  })
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const load = useCallback(() => {
    getTaskReminders()
      .then(setReminders)
      .catch(() => { /* silent — reminder polling must never break the app */ })
  }, [])

  useEffect(() => {
    getMe().then(profile => setApprovalClass(classifyApprovalClass(profile))).catch(() => setApprovalClass('general'))
    load()
    const t = setInterval(load, POLL_MS)
    const onFocus = () => load()
    window.addEventListener('focus', onFocus)
    return () => { clearInterval(t); window.removeEventListener('focus', onFocus) }
  }, [load])

  const toasts = reminders.filter(r => r.type === 'assign_day')
  const forced = reminders.filter(r => r.type !== 'assign_day')
  const showModal = forced.length > 0 && Date.now() > snoozedUntil && !completing

  // Unlock countdown while the force modal is visible.
  useEffect(() => {
    if (!showModal) { setOpenSeconds(0); if (timerRef.current) clearInterval(timerRef.current); return }
    timerRef.current = setInterval(() => setOpenSeconds(s => s + 1), 1000)
    return () => { if (timerRef.current) clearInterval(timerRef.current) }
  }, [showModal])

  async function dismissToast(r: TaskReminder) {
    setReminders(cur => cur.filter(x => x.id !== r.id))
    try { await ackTaskReminder(r.id) } catch { /* re-appears on next poll */ }
  }

  function snooze() {
    const until = Date.now() + SNOOZE_MS
    localStorage.setItem(SNOOZE_KEY, String(until))
    setSnoozedUntil(until)
  }

  // Auto-clear each "new task" toast after a few seconds so it never sits over
  // (and blocks clicks to) page content beneath it. Each toast is scheduled
  // exactly once (guarded by the ref); handles are cleared only on unmount so a
  // later poll adding a new toast never cancels a pending dismissal.
  const autoDismissedRef = useRef<Set<string>>(new Set())
  const toastTimersRef = useRef<ReturnType<typeof setTimeout>[]>([])
  useEffect(() => {
    toasts.forEach(r => {
      if (autoDismissedRef.current.has(r.id)) return
      autoDismissedRef.current.add(r.id)
      toastTimersRef.current.push(setTimeout(() => { void dismissToast(r) }, TOAST_AUTO_DISMISS_MS))
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [toasts.map(r => r.id).join(',')])
  useEffect(() => () => { toastTimersRef.current.forEach(clearTimeout) }, [])

  return (
    <>
      {/* assign-day toasts */}
      {toasts.length > 0 && (
        <div className="fixed bottom-5 right-5 z-[60] space-y-2 max-w-sm pointer-events-none">
          {toasts.slice(0, 3).map(r => (
            <div key={r.id}
                 className="pointer-events-auto flex items-start gap-3 rounded-xl shadow-lg px-4 py-3 bg-white border border-[#E5E7EB]">
              <Bell className="w-4 h-4 mt-0.5 text-[#F4A623] shrink-0" />
              <div className="text-sm text-[#0D1B2A]">
                <b>New task:</b> {r.task_title}
                {r.task_due_at && <span className="text-[#6B7280]"> — due {r.task_due_at}</span>}
              </div>
              <button onClick={() => dismissToast(r)} aria-label="Dismiss"
                      className="text-[#9CA3AF] hover:text-[#374151] shrink-0">
                <X className="w-4 h-4" />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* due / overdue force-action modal */}
      {showModal && (
        <div className="fixed inset-0 bg-black/60 z-[65] flex items-center justify-center p-4">
          <div className="bg-white rounded-xl w-full max-w-xl p-6 shadow-2xl">
            <div className="flex items-center gap-2 mb-1">
              <AlarmClock className="w-5 h-5 text-[#B42318]" />
              <h3 className="text-base font-bold text-[#0D1B2A]">
                {forced.length === 1 ? 'A task needs your action' : `${forced.length} tasks need your action`}
              </h3>
              {/* Skip straight into the app (CFO 2026-07-13). The reminder is a
                  nudge, not a cage — it snoozes and returns; the tasks stay
                  visible on the Task Dashboard, which is the real accountability
                  surface. Always available, no wait. */}
              <button onClick={snooze} aria-label="Skip for now"
                      title="Skip for now — this will come back"
                      className="ml-auto text-[#9CA3AF] hover:text-[#374151]">
                <X className="w-4 h-4" />
              </button>
            </div>
            <p className="text-xs text-[#6B7280] mb-4">
              Due and overdue tasks stay here until they are completed with a note.
            </p>
            <div className="space-y-2 max-h-72 overflow-y-auto">
              {forced.map(r => (
                <div key={r.id}
                     className="flex items-center justify-between gap-3 rounded-lg px-3 py-2.5 bg-[#F9FAFB] dark:bg-[#1E293B]">
                  <div className="text-sm min-w-0">
                    <div className="font-semibold text-[#0D1B2A] truncate">{r.task_title}</div>
                    <div className="text-xs" style={{ color: r.type === 'overdue' ? '#B42318' : '#B45309' }}>
                      {r.type === 'overdue' ? 'OVERDUE' : 'Due today'}
                      {r.task_due_at ? ` — due ${r.task_due_at}` : ''}
                    </div>
                  </div>
                  <button onClick={() => setCompleting(r)}
                          className="shrink-0 px-3 py-1.5 rounded-lg text-xs font-semibold text-white"
                          style={{ background: '#0D1B2A' }}>
                    Complete now
                  </button>
                </div>
              ))}
            </div>
            <div className="flex justify-end mt-4 h-6">
              {openSeconds >= UNLOCK_AFTER_S ? (
                <button onClick={snooze} className="text-xs text-[#6B7280] underline">
                  Remind me later (2 hours)
                </button>
              ) : (
                <span className="text-[11px] text-[#9CA3AF]">…</span>
              )}
            </div>
          </div>
        </div>
      )}

      {completing && (
        <TaskCompletionModal
          taskId={completing.task}
          taskTitle={completing.task_title}
          approvalClass={approvalClass}
          onClose={() => setCompleting(null)}
          onDone={() => { setCompleting(null); load() }}
        />
      )}
    </>
  )
}

export default TaskReminderGuard
