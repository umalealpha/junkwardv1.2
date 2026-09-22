'use client'

/**
 * /tasks — Internal tasking inbox + outbox (CFO directive 2026-05-24).
 *
 * Replaces internal email. Anyone can hand a task to anyone — privacy is
 * enforced on the backend (assignee or assigner only). Status transitions
 * (in_progress → done | partial | blocked) post an OmniTaskComment so the
 * audit trail records who said what when. Phase-1 polling refreshes every
 * 30s; Phase-2 will pipe new-task events into ARIA.
 */

import { useEffect, useMemo, useState, useCallback, useRef } from 'react'
import { useRouter } from 'next/navigation'
import {
  getOmniTasks, createOmniTask, getOmniTask, updateOmniTask, completeTaskWithNote,
  getOnlineUsers, getToken, handoverTasks, getTaskCommentFile,
  removeTaskCommentFile,
} from '@/lib/api'
import type {
  OmniTaskListResponse, OmniTaskListRow, OmniTaskDetail, OmniTaskComment,
  OmniTaskStatus, OmniTaskPriority, OnlineUserRow,
} from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { FormattedTaskBody } from '@/components/FormattedTaskBody'
import { FnbPreSelectButton, isPaymentAuthBody } from '@/components/FnbPreSelectButton'
import { PersonPicker } from '@/components/PersonPicker'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  RefreshCw, Plus, Inbox, ArrowUpRight, CheckCircle2, Circle,
  X, Send, MessageSquare, Paperclip, Download, Banknote, ExternalLink, Trash2,
} from 'lucide-react'

// CFO 2026-08-31: "we should not mix Tasks and payments." Payment-authorisation
// tasks (source='payment_request') get their OWN tab, out of the work inbox.
type Tab = 'inbox' | 'payments' | 'outbox' | 'done'
const isPaymentRow = (t: OmniTaskListRow) =>
  t.is_payment === true || t.source === 'payment_request'

const STATUS_COLOR: Record<OmniTaskStatus, string> = {
  pending:     'bg-[#FEF3C7] text-[#92400E]',
  in_progress: 'bg-[#DBEAFE] text-[#1E40AF]',
  done:        'bg-[#D1FAE5] text-[#065F46]',
  partial:     'bg-[#FED7AA] text-[#9A3412]',
  blocked:     'bg-[#FEE2E2] text-[#991B1B]',
  cancelled:   'bg-[#F3F4F6] text-[#374151]',
}
const PRIORITY_COLOR: Record<OmniTaskPriority, string> = {
  low:    'text-[#6B7280]',
  normal: 'text-[#374151]',
  high:   'text-[#B45309]',
  urgent: 'text-[#B91C1C] font-bold',
}

export default function TasksPage() {
  const router = useRouter()
  const [data, setData] = useState<OmniTaskListResponse | null>(null)
  const [tab, setTab] = useState<Tab>('inbox')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showNew, setShowNew] = useState(false)
  const [showHandover, setShowHandover] = useState(false)
  const [activeId, setActiveId] = useState<string | null>(null)
  const [online, setOnline] = useState<OnlineUserRow[]>([])

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const [d, o] = await Promise.all([
        getOmniTasks(tab === 'done'),
        getOnlineUsers().catch(() => ({ users: [], cutoff_minutes: 5 })),
      ])
      setData(d)
      setOnline(o.users)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load tasks')
    } finally {
      setLoading(false)
    }
  }, [tab])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load()
    const t = setInterval(load, 30000)  // 30s poll
    return () => clearInterval(t)
  }, [load, router])

  const rows: OmniTaskListRow[] = useMemo(() => {
    if (!data) return []
    const open = (t: OmniTaskListRow) => !['done','cancelled'].includes(t.status)
    // Inbox = real work only (payments moved to their own tab); Payments = the
    // payment-authorisation tasks pulled out of the inbox.
    if (tab === 'inbox')    return data.inbox.filter(t => open(t) && !isPaymentRow(t))
    if (tab === 'payments') return data.inbox.filter(t => open(t) && isPaymentRow(t))
    if (tab === 'outbox')   return data.outbox.filter(open)
    return [...data.inbox.filter(t => ['done','partial','cancelled'].includes(t.status)),
            ...data.outbox.filter(t => ['done','partial','cancelled'].includes(t.status))]
  }, [data, tab])

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Tasks"
        breadcrumbs={[{ label: 'Tasks' }]}
        actions={
          <div className="flex gap-2">
            <Button variant="secondary" size="sm" onClick={load} loading={loading}>
              <RefreshCw className={`w-4 h-4 mr-1 ${loading ? 'animate-spin' : ''}`} /> Refresh
            </Button>
            <Button variant="secondary" size="sm" onClick={() => setShowHandover(true)} title="Going on leave? Hand all your open tasks to someone">
              <ArrowUpRight className="w-4 h-4 mr-1" /> Hand over
            </Button>
            <Button variant="primary" size="sm" onClick={() => setShowNew(true)}>
              <Plus className="w-4 h-4 mr-1" /> New task
            </Button>
          </div>
        }
      />
      <main className="flex-1 p-6 grid grid-cols-1 lg:grid-cols-4 gap-6">

        <div className="lg:col-span-3 space-y-4">
          {/* Tabs */}
          <div className="flex gap-2 items-center">
            {([
              { id: 'inbox',  label: 'Inbox',  icon: Inbox,
                count: data ? data.inbox.filter(t=>!['done','cancelled'].includes(t.status) && !isPaymentRow(t)).length : 0,
                badge: data?.unread_count as number | undefined },
              { id: 'payments', label: 'Payments', icon: Banknote,
                count: data ? data.inbox.filter(t=>!['done','cancelled'].includes(t.status) && isPaymentRow(t)).length : 0,
                badge: undefined as number | undefined },
              { id: 'outbox', label: 'Sent',   icon: ArrowUpRight,
                count: data ? data.outbox.filter(t=>!['done','cancelled'].includes(t.status)).length : 0,
                badge: undefined as number | undefined },
              { id: 'done',   label: 'Done',   icon: CheckCircle2,
                count: data ? [...data.inbox, ...data.outbox].filter(t=>['done','partial'].includes(t.status)).length : 0,
                badge: undefined as number | undefined },
            ] as const).map(t => {
              const Icon = t.icon
              const active = tab === t.id
              return (
                <button
                  key={t.id}
                  onClick={() => setTab(t.id as Tab)}
                  className={`flex items-center gap-1.5 px-3 py-1.5 rounded text-sm border transition ${
                    active ? 'bg-[#F4A623] text-white border-[#F4A623]'
                           : 'bg-white text-[#374151] border-[#D1D5DB] hover:bg-[#F9FAFB]'
                  }`}
                >
                  <Icon className="w-4 h-4" /> {t.label}
                  <span className="ml-1 text-xs opacity-75">({t.count})</span>
                  {t.badge ? (
                    <span className="ml-1 inline-flex items-center justify-center px-1.5 py-0.5 text-[10px] bg-[#DC2626] text-white rounded-full">
                      {t.badge}
                    </span>
                  ) : null}
                </button>
              )
            })}
          </div>

          {tab === 'payments' && (
            <Card className="border-[#F4A623]/40 bg-[#FFF7ED]">
              <CardContent className="py-3 text-sm text-[#92400E] flex items-center justify-between gap-3">
                <span>
                  Payment authorisations, kept separate from your work tasks. To
                  authorise a batch <strong>line by line</strong> (approve some, hold one),
                  open the full Payments page.
                </span>
                <a href="/payment-requests"
                   className="shrink-0 inline-flex items-center gap-1 rounded bg-[#0D1B2A] text-white px-3 py-1.5 text-xs font-semibold hover:opacity-90">
                  Open Payments <ExternalLink className="w-3.5 h-3.5" />
                </a>
              </CardContent>
            </Card>
          )}

          {error && (
            <Card className="border-red-300 bg-red-50/40">
              <CardContent className="py-3 text-sm text-red-700">{error}</CardContent>
            </Card>
          )}

          <Card>
            <CardContent className="p-0">
              {rows.length === 0 ? (
                <div className="py-10 text-center text-sm text-[#9CA3AF]">
                  <Inbox className="w-8 h-8 mx-auto mb-2 opacity-40" />
                  No tasks here yet.
                </div>
              ) : (
                <table className="w-full text-sm">
                  <thead className="bg-[#F9FAFB] text-left text-xs uppercase text-[#6B7280] border-b">
                    <tr>
                      <th className="py-2 px-3">Title</th>
                      <th className="py-2 px-3">{tab === 'outbox' ? 'To' : 'From'}</th>
                      <th className="py-2 px-3">Priority</th>
                      <th className="py-2 px-3">Status</th>
                      <th className="py-2 px-3">Due</th>
                      <th className="py-2 px-3"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map(t => {
                      const counterpart = tab === 'outbox' ? t.assignee : t.assigner
                      const unread = tab === 'inbox' && t.seen_at == null
                      return (
                        <tr
                          key={t.id}
                          onClick={() => setActiveId(t.id)}
                          className={`border-b last:border-0 hover:bg-[#FFF7ED] cursor-pointer ${unread ? 'font-semibold' : ''}`}
                        >
                          <td className="py-2 px-3">
                            {unread && <Circle className="inline w-2 h-2 text-[#DC2626] fill-current mr-1" />}
                            {t.title}
                          </td>
                          <td className="py-2 px-3 text-xs text-[#6B7280]">
                            {counterpart.full_name || counterpart.username}
                          </td>
                          <td className={`py-2 px-3 text-xs ${PRIORITY_COLOR[t.priority]}`}>
                            {t.priority}
                          </td>
                          <td className="py-2 px-3">
                            <span className={`px-2 py-0.5 rounded text-xs ${STATUS_COLOR[t.status]}`}>
                              {t.status.replace('_', ' ')}
                            </span>
                          </td>
                          <td className="py-2 px-3 text-xs text-[#6B7280]">
                            {t.due_at || '—'}
                          </td>
                          <td className="py-2 px-3 text-right text-[#9CA3AF]">›</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              )}
            </CardContent>
          </Card>
        </div>

        {/* Online presence sidebar */}
        <div>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-[#10B981]" />
                Online now ({online.length})
              </CardTitle>
            </CardHeader>
            <CardContent className="text-sm space-y-1">
              {online.length === 0 ? (
                <p className="text-xs text-[#9CA3AF]">Nobody online in the last 5 minutes.</p>
              ) : online.map(u => (
                <div key={u.username} className="flex items-center justify-between text-xs">
                  <span className="flex items-center gap-2">
                    <span className="w-1.5 h-1.5 rounded-full bg-[#10B981]" />
                    {u.full_name || u.username}{u.is_me && <span className="text-[#9CA3AF]"> (you)</span>}
                  </span>
                </div>
              ))}
            </CardContent>
          </Card>
        </div>
      </main>

      {showNew && (
        <NewTaskModal
          onClose={() => setShowNew(false)}
          onSaved={async () => { setShowNew(false); await load() }}
        />
      )}
      {showHandover && (
        <HandoverModal
          onClose={() => setShowHandover(false)}
          onDone={async () => { setShowHandover(false); await load() }}
        />
      )}
      {activeId && (
        <TaskDetailDrawer
          id={activeId}
          onClose={() => setActiveId(null)}
          onChanged={load}
        />
      )}
    </div>
  )
}


function NewTaskModal({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [assignee, setAssignee] = useState('')
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [priority, setPriority] = useState<OmniTaskPriority>('normal')
  const [dueAt, setDueAt] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function submit() {
    setBusy(true); setErr(null)
    try {
      await createOmniTask({
        assignee_username: assignee, title, body,
        priority, due_at: dueAt || undefined,
        attachment: file || undefined,
      })
      onSaved()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not create task')
    } finally { setBusy(false) }
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-lg">
        <CardHeader className="flex flex-row items-center justify-between pb-2">
          <CardTitle className="text-base">New task</CardTitle>
          <button onClick={onClose} className="text-[#9CA3AF] hover:text-[#374151]">
            <X className="w-4 h-4" />
          </button>
        </CardHeader>
        <CardContent className="space-y-3">
          <div>
            <label className="block text-xs text-[#6B7280] mb-1">Assign to</label>
            <PersonPicker value={assignee} onChange={setAssignee}
                          placeholder="Search a name…" autoFocus />
          </div>
          <div>
            <label className="block text-xs text-[#6B7280] mb-1">Title</label>
            <input value={title} onChange={e => setTitle(e.target.value)}
                   className="w-full border rounded px-3 py-1.5 text-sm bg-background"
                   placeholder="Short summary of what is needed" />
          </div>
          <div>
            <label className="block text-xs text-[#6B7280] mb-1">Details</label>
            <textarea value={body} onChange={e => setBody(e.target.value)}
                      rows={4}
                      className="w-full border rounded px-3 py-1.5 text-sm bg-background" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-[#6B7280] mb-1">Priority</label>
              <select value={priority} onChange={e => setPriority(e.target.value as OmniTaskPriority)}
                      className="w-full border rounded px-3 py-1.5 text-sm bg-background">
                <option value="low">Low</option>
                <option value="normal">Normal</option>
                <option value="high">High</option>
                <option value="urgent">Urgent</option>
              </select>
            </div>
            <div>
              <label className="block text-xs text-[#6B7280] mb-1">Due date</label>
              <input type="date" value={dueAt} onChange={e => setDueAt(e.target.value)}
                     className="w-full border rounded px-3 py-1.5 text-sm bg-background" />
            </div>
          </div>
          <AttachmentField file={file} onChange={setFile} />
          {err && <p className="text-xs text-red-700">{err}</p>}
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="secondary" size="sm" onClick={onClose}>Cancel</Button>
            <Button variant="primary" size="sm" onClick={submit}
                    loading={busy} disabled={busy || !assignee || !title}>
              <Send className="w-4 h-4 mr-1" /> Send task
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}


/** File picker used on the New-task modal and the reply box. Screenshots or
 *  docs — max 10 MB, enforced again server-side. */
function AttachmentField({ file, onChange, label = 'Attach a screenshot or file' }:
  { file: File | null; onChange: (f: File | null) => void; label?: string }) {
  const inputRef = useRef<HTMLInputElement>(null)
  return (
    <div>
      <input
        ref={inputRef}
        type="file"
        className="hidden"
        accept="image/*,.pdf,.doc,.docx,.xls,.xlsx,.csv,.txt,.msg,.eml"
        onChange={e => onChange(e.target.files?.[0] || null)}
      />
      {file ? (
        <div className="flex items-center gap-2 border rounded px-3 py-1.5 text-sm bg-[#F9FAFB]">
          <Paperclip className="w-4 h-4 text-[#6B7280] shrink-0" />
          <span className="flex-1 min-w-0 truncate">{file.name}</span>
          <span className="text-xs text-[#9CA3AF] shrink-0">{(file.size / 1024).toFixed(0)} KB</span>
          <button type="button" onClick={() => { onChange(null); if (inputRef.current) inputRef.current.value = '' }}
                  className="text-[#9CA3AF] hover:text-[#374151] shrink-0" aria-label="Remove attachment">
            <X className="w-4 h-4" />
          </button>
        </div>
      ) : (
        <button type="button" onClick={() => inputRef.current?.click()}
                className="flex items-center gap-2 text-sm text-[#B45309] hover:text-[#92400E]">
          <Paperclip className="w-4 h-4" /> {label}
        </button>
      )}
    </div>
  )
}


function HandoverModal({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [to, setTo] = useState('')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [result, setResult] = useState<string | null>(null)

  async function submit() {
    if (!to) return
    setBusy(true); setErr(null)
    try {
      const r = await handoverTasks(to, reason)
      setResult(`Handed ${r.reassigned} open task(s) to ${r.to}.`)
      setTimeout(onDone, 1400)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not hand over')
    } finally { setBusy(false) }
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-md">
        <CardHeader className="flex flex-row items-center justify-between pb-2">
          <CardTitle className="text-base">Hand over my tasks</CardTitle>
          <button onClick={onClose} className="text-[#9CA3AF] hover:text-[#374151]"><X className="w-4 h-4" /></button>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-xs text-[#6B7280]">Going on leave? Move <strong>all your open tasks</strong> to a colleague. They&apos;ll appear in their inbox; each move is recorded.</p>
          <div>
            <label className="block text-xs text-[#6B7280] mb-1">Hand over to</label>
            <PersonPicker value={to} onChange={setTo} placeholder="Search a colleague…" />
          </div>
          <input value={reason} onChange={e => setReason(e.target.value)}
                 placeholder="Reason (optional, e.g. on leave 15–19 Jul)"
                 className="w-full border rounded px-3 py-1.5 text-sm bg-background" />
          {err && <p className="text-xs text-red-700">{err}</p>}
          {result && <p className="text-xs text-emerald-700">{result}</p>}
          <div className="flex justify-end gap-2 pt-1">
            <Button variant="secondary" size="sm" onClick={onClose}>Cancel</Button>
            <Button variant="primary" size="sm" onClick={submit} loading={busy} disabled={busy || !to || !!result}>
              Hand over
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}


function TaskDetailDrawer({ id, onClose, onChanged }:
  { id: string; onClose: () => void; onChanged: () => void }) {
  const [t, setT] = useState<OmniTaskDetail | null>(null)
  const [comment, setComment] = useState('')
  const [newStatus, setNewStatus] = useState<OmniTaskStatus | ''>('')
  const [pct, setPct] = useState<number>(50)
  const [replyFile, setReplyFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [reTo, setReTo] = useState('')
  const [reReason, setReReason] = useState('')
  const initialLoad = useRef(false)
  const openedAt = useRef(Date.now())   // dwell time (audit only) for a one-tap Done

  const reload = useCallback(async () => {
    try {
      const d = await getOmniTask(id)
      setT(d)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not load task')
    }
  }, [id])

  useEffect(() => {
    if (!initialLoad.current) { initialLoad.current = true; reload() }
  }, [reload])

  async function reassign() {
    if (!reTo) return
    setBusy(true); setErr(null)
    try {
      await updateOmniTask(id, { reassign_to: reTo, reassign_reason: reReason || undefined })
      setReTo(''); setReReason('')
      await reload(); onChanged(); onClose()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not reassign')
    } finally { setBusy(false) }
  }

  async function submitUpdate() {
    setBusy(true); setErr(null)
    try {
      if (newStatus === 'done') {
        // CFO 2026-08-03: Done completes RIGHT HERE — no pop-up, no forced note.
        // The completion note is optional (server MIN_NOTE_CHARS = 0); the inline
        // comment box carries it if one was typed. 'done' must go through the
        // complete endpoint (the plain PATCH path rejects status=done).
        await completeTaskWithNote(id, comment.trim(),
          Math.floor((Date.now() - openedAt.current) / 1000))
      } else {
        await updateOmniTask(id, {
          status:  newStatus || undefined,
          comment: comment || undefined,
          completion_pct: newStatus === 'partial' ? pct : undefined,
          evidence: replyFile || undefined,
        })
      }
      setComment(''); setNewStatus(''); setReplyFile(null)
      await reload()
      onChanged()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not save')
    } finally { setBusy(false) }
  }

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex justify-end" onClick={onClose}>
      <div className="bg-white dark:bg-[#0F172A] w-full max-w-xl h-full overflow-y-auto p-6"
           onClick={e => e.stopPropagation()}>
        {!t ? (
          <p className="text-sm text-[#6B7280]">Loading…</p>
        ) : (
          <>
            <div className="flex items-start justify-between mb-3">
              <div>
                <h2 className="text-lg font-bold">{t.title}</h2>
                <p className="text-xs text-[#6B7280] mt-1">
                  From <strong>{t.assigner.full_name || t.assigner.username}</strong>
                  {' → '}
                  <strong>{t.assignee.full_name || t.assignee.username}</strong>
                </p>
              </div>
              <button onClick={onClose} className="text-[#9CA3AF] hover:text-[#374151]">
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="flex flex-wrap gap-2 mb-3 text-xs">
              <span className={`px-2 py-0.5 rounded ${STATUS_COLOR[t.status]}`}>{t.status.replace('_',' ')}{t.status === 'partial' && t.completion_pct != null ? ` ${t.completion_pct}%` : ''}</span>
              <span className={PRIORITY_COLOR[t.priority]}>{t.priority}</span>
              {t.due_at && <span className="text-[#6B7280]">due {t.due_at}</span>}
              <span className="text-[#9CA3AF]">created {t.created_at.slice(0,10)}</span>
            </div>
            {t.body && <FormattedTaskBody text={t.body} />}
            {/* Payment authorisation → offer the FNB batch pre-selector (CFO 2026-08-03).
                Ticks only; it can never authorise, delete or reject. */}
            {isPaymentAuthBody(t.title, t.body) && <FnbPreSelectButton body={t.body} />}

            <div className="mb-4 space-y-2">
              <h3 className="text-xs uppercase tracking-wider text-[#6B7280] flex items-center gap-1">
                <MessageSquare className="w-3 h-3" /> Activity
              </h3>
              {t.comments.length === 0 ? (
                <p className="text-xs text-[#9CA3AF]">No comments yet.</p>
              ) : t.comments.map(c => (
                <div key={c.id} className="border-l-2 border-[#E5E7EB] pl-3 py-1 text-sm">
                  <div className="text-xs text-[#6B7280]">
                    <strong>{c.author_name}</strong> · {c.created_at.slice(0,16).replace('T',' ')}
                    {c.new_status && (
                      <span className={`ml-2 px-1.5 py-0.5 rounded text-[10px] ${STATUS_COLOR[c.new_status]}`}>
                        → {c.new_status.replace('_',' ')}
                      </span>
                    )}
                  </div>
                  {c.body && <div className="mt-1"><FormattedTaskBody text={c.body} /></div>}
                  {c.has_evidence && (
                    <CommentAttachment taskId={id} comment={c} onRemoved={reload} />
                  )}
                </div>
              ))}
            </div>

            <Card className="mt-4">
              <CardContent className="space-y-2 pt-4">
                <h3 className="text-xs uppercase tracking-wider text-[#6B7280]">Update / reply</h3>
                <div className="grid grid-cols-2 gap-2">
                  <select value={newStatus} onChange={e => setNewStatus(e.target.value as OmniTaskStatus)}
                          className="border rounded px-3 py-1.5 text-sm bg-background">
                    <option value="">— change status —</option>
                    <option value="in_progress">In progress</option>
                    <option value="done">Done</option>
                    <option value="partial">Partially complete</option>
                    <option value="blocked">Blocked</option>
                    <option value="cancelled">Cancelled</option>
                  </select>
                  {newStatus === 'partial' && (
                    <div className="flex items-center gap-2">
                      <label htmlFor="taskpct" className="text-xs text-[#6B7280] whitespace-nowrap">How complete?</label>
                      <select id="taskpct" value={pct} onChange={e => setPct(Number(e.target.value))}
                              className="border rounded px-3 py-1.5 text-sm bg-background">
                        {[10, 25, 50, 75, 90].map(p => <option key={p} value={p}>{p}%</option>)}
                      </select>
                    </div>
                  )}
                </div>
                <textarea value={comment} onChange={e => setComment(e.target.value)}
                          rows={3} placeholder="Add a comment / completion notes…"
                          className="w-full border rounded px-3 py-1.5 text-sm bg-background" />
                <AttachmentField file={replyFile} onChange={setReplyFile}
                                 label="Attach proof (screenshot / file)" />
                {/* A payment-control refusal (PAY-DUP-01) is a headline, a bullet
                    per clashing line, then the action to take — so it must keep
                    its line breaks. Without whitespace-pre-line the CFO's block
                    on 3 Aug 2026 collapsed into one run-on paragraph and the
                    clashing line numbers were unreadable. */}
                {err && <p className="text-xs text-red-700 whitespace-pre-line">{err}</p>}
                <div className="flex justify-end">
                  <Button variant="primary" size="sm" onClick={submitUpdate}
                          loading={busy} disabled={busy || (!comment && !newStatus && !replyFile)}>
                    Save
                  </Button>
                </div>
              </CardContent>
            </Card>

            <Card className="mt-4">
              <CardContent className="space-y-2 pt-4">
                <h3 className="text-xs uppercase tracking-wider text-[#6B7280]">Reassign this task</h3>
                <p className="text-xs text-[#6B7280]">Not yours to do? Hand it to the right person — they&apos;ll see it in their inbox.</p>
                <div className="grid grid-cols-2 gap-2">
                  <PersonPicker value={reTo} onChange={setReTo} placeholder="Reassign to…" />
                  <input value={reReason} onChange={e => setReReason(e.target.value)}
                         placeholder="Reason (optional)"
                         className="border rounded px-3 py-1.5 text-sm bg-background" />
                </div>
                <div className="flex justify-end">
                  <Button variant="secondary" size="sm" onClick={reassign}
                          loading={busy} disabled={busy || !reTo}>
                    Reassign
                  </Button>
                </div>
              </CardContent>
            </Card>
          </>
        )}
      </div>
    </div>
  )
}


/** Renders a task comment's attachment. The file endpoint is auth-gated so it
 *  can't be a plain <img src> — we fetch the blob (with the auth header) and
 *  show an inline preview for images, a download chip otherwise.
 *
 *  Bug 83594e5d (D. Ikgopoleng, 2026-09-03): a wrong or confidential file could
 *  only be taken down by IT. The uploader and the task owner now get a Remove
 *  control — gated on the server's own can_remove_evidence flag, and confirmed
 *  once because the file is destroyed, not archived. */
function CommentAttachment({ taskId, comment, onRemoved }:
  { taskId: string; comment: OmniTaskComment; onRemoved: () => void }) {
  const [url, setUrl] = useState<string | null>(null)
  const [isImage, setIsImage] = useState(false)
  const [err, setErr] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [removing, setRemoving] = useState(false)
  const [removeErr, setRemoveErr] = useState<string | null>(null)
  const name = comment.evidence_name || 'attachment'

  async function remove() {
    setRemoving(true); setRemoveErr(null)
    try {
      await removeTaskCommentFile(taskId, comment.id)
      onRemoved()
    } catch (e) {
      setRemoveErr(e instanceof Error ? e.message : 'Could not remove the file')
      setRemoving(false)
    }
  }

  const removeControl = !comment.can_remove_evidence ? null : (
    <span className="inline-flex items-center gap-2 ml-2 align-middle">
      {confirming ? (
        <>
          <span className="text-[11px] text-[#B91C1C]">Remove for good?</span>
          <button type="button" onClick={remove} disabled={removing}
                  className="text-[11px] font-semibold text-[#B91C1C] hover:underline disabled:opacity-50">
            {removing ? 'Removing…' : 'Yes, remove'}
          </button>
          <button type="button" onClick={() => setConfirming(false)} disabled={removing}
                  className="text-[11px] text-[#6B7280] hover:underline disabled:opacity-50">
            Keep
          </button>
        </>
      ) : (
        <button type="button" onClick={() => setConfirming(true)}
                className="inline-flex items-center gap-1 text-[11px] text-[#6B7280] hover:text-[#B91C1C]"
                aria-label={`Remove attachment ${name}`}>
          <Trash2 className="w-3 h-3" /> Remove
        </button>
      )}
      {removeErr && <span className="text-[11px] text-red-700">{removeErr}</span>}
    </span>
  )

  useEffect(() => {
    let alive = true
    let objUrl: string | null = null
    getTaskCommentFile(taskId, comment.id)
      .then(blob => {
        if (!alive) return
        objUrl = URL.createObjectURL(blob)
        const imgByExt = /\.(png|jpe?g|gif|webp|bmp|heic)$/i.test(name)
        setUrl(objUrl)
        setIsImage(blob.type.startsWith('image/') || imgByExt)
      })
      .catch(() => { if (alive) setErr(true) })
    return () => { alive = false; if (objUrl) URL.revokeObjectURL(objUrl) }
  }, [taskId, comment.id, name])

  if (err) {
    return (
      <div className="mt-2 text-xs text-red-600 flex items-center gap-1">
        <Paperclip className="w-3 h-3" /> {name} (couldn&apos;t load)
        {removeControl}
      </div>
    )
  }
  if (!url) {
    return (
      <div className="mt-2 text-xs text-[#9CA3AF] flex items-center gap-1">
        <Paperclip className="w-3 h-3" /> loading attachment…
      </div>
    )
  }
  if (isImage) {
    return (
      <div className="mt-2">
        <a href={url} target="_blank" rel="noreferrer" className="inline-block">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={url} alt={name} className="max-h-40 rounded border" />
        </a>
        <div className="text-[10px] text-[#9CA3AF] mt-0.5">
          {name} · click to open
          {removeControl}
        </div>
      </div>
    )
  }
  return (
    <div className="mt-2 flex items-center flex-wrap">
      <a href={url} download={name}
         className="inline-flex items-center gap-1.5 text-xs text-[#B45309] hover:text-[#92400E] border rounded px-2 py-1">
        <Download className="w-3.5 h-3.5" /> {name}
      </a>
      {removeControl}
    </div>
  )
}
