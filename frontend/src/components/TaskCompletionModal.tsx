'use client'

/**
 * TaskCompletionModal — the completion gate UI (taskboard Phase 1, CFO 2026-07-09).
 *
 * CFO 2026-07-24: the completion note is OPTIONAL (MIN_NOTE_CHARS = 0) — Done is
 * one tap. The note field + presets stay for when detail is useful; nothing is
 * forced. Time on the modal is still measured and stored for audit.
 */
import { useRef, useState } from 'react'
import { completeTaskWithNote } from '@/lib/api'
import {
  completionProfileFor,
  type ApprovalClass,
} from '@/lib/approvalProfiles'
import { CheckCircle2, Loader2, X } from 'lucide-react'

// CFO 2026-07-24: the completion note is OPTIONAL — Done is one tap. 0 = no gate.
const MIN_NOTE_CHARS = 0


export function TaskCompletionModal({ taskId, taskTitle, approvalClass = 'general', onDone, onClose }: {
  taskId: string
  taskTitle: string
  approvalClass?: ApprovalClass
  onDone: () => void
  onClose: () => void
}) {
  const profile = completionProfileFor(approvalClass)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const startedAt = useRef(Date.now())
  const noteRef = useRef<HTMLTextAreaElement>(null)

  function applyPreset(text: string) {
    setNote(text)
    noteRef.current?.focus()
  }

  const charsLeft = Math.max(0, MIN_NOTE_CHARS - note.trim().length)
  const ready = charsLeft === 0

  async function submit() {
    if (!ready || busy) return
    setBusy(true); setErr(null)
    try {
      await completeTaskWithNote(taskId, note.trim(),
        Math.floor((Date.now() - startedAt.current) / 1000))
      onDone()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not complete the task.')
    } finally { setBusy(false) }
  }

  return (
    <div className="fixed inset-0 bg-black/50 z-[70] flex items-center justify-center p-4">
      <div className="bg-white dark:bg-[#0F172A] rounded-xl w-full max-w-lg p-6 shadow-2xl">
        <div className="flex items-start justify-between mb-1">
          <h3 className="text-base font-bold text-[#0D1B2A] dark:text-white">{profile.heading}</h3>
          <button onClick={onClose} className="text-[#9CA3AF] hover:text-[#374151]" aria-label="Close">
            <X className="w-4 h-4" />
          </button>
        </div>
        <p className="text-sm text-[#6B7280] mb-2">{taskTitle}</p>
        <p className="text-xs text-[#6B7280] mb-4">{profile.intro}</p>

        <label className="text-xs font-semibold uppercase tracking-wide text-[#6B7280]">
          Completion note — optional
        </label>
        <div className="flex flex-wrap gap-1.5 mt-2 mb-1">
          {profile.presets.map(p => (
            <button
              key={p.label} type="button" onClick={() => applyPreset(p.text)}
              className="px-2.5 py-1 rounded-full text-xs font-medium border border-[#D1D5DB] dark:border-[#334155] text-[#374151] dark:text-slate-200 bg-white dark:bg-[#1E293B] hover:border-[#F4A623] hover:text-[#B45309] dark:hover:text-[#F4A623]"
            >
              {p.label}
            </button>
          ))}
        </div>
        <textarea
          ref={noteRef}
          value={note} onChange={e => setNote(e.target.value)} rows={4} autoFocus
          placeholder="Describe what was done, the outcome, and anything the assigner should check…"
          className="w-full border border-[#D1D5DB] dark:border-[#334155] rounded-lg px-3 py-2 text-sm mt-1 bg-white dark:bg-[#1E293B] text-[#0D1B2A] dark:text-white placeholder:text-[#9CA3AF]"
        />
        <div className="mt-2 text-xs">
          <span className="text-[#6B7280]">Optional — a note helps, but you can just mark it done.</span>
        </div>
        {/* Payment-task controls remain server-authorised; this profile only changes
            the wording and visible completion choices for the current role. */}
        {err && <p className="text-xs text-red-700 mt-2 whitespace-pre-line">{err}</p>}
        <div className="flex justify-end mt-4">
          <button
            onClick={submit} disabled={!ready || busy}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold text-white"
            style={{ background: ready ? '#0D1B2A' : '#9CA3AF', cursor: ready ? 'pointer' : 'not-allowed' }}
          >
            {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <CheckCircle2 className="w-4 h-4" />}
            {profile.primaryLabel}
          </button>
        </div>
      </div>
    </div>
  )
}

export default TaskCompletionModal
