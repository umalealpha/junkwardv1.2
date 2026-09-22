'use client'

/**
 * /task-dashboard — CFO / manager task oversight (CFO directive 2026-07-13).
 *
 * "The Alpha League" (CFO 2026-08-26): recognition is by a FAIR Delivery Score
 * — what you finish in a rolling 14 days, priority-weighted, on-time bonus, with
 * a capped late-tax that never wipes out delivery. No more pct_done ranking (it
 * punished the high-volume person and over-rewarded a 1-of-1), and no more "wall
 * of shame" roast — instead a factual "Needs support" card. The completed list
 * is trimmed to ~2 weeks (older is hidden, not deleted); filters by name /
 * assignee / priority / status. Themed via useTheme() so it follows the app
 * theme (professional / light / fun), not a hardcoded dark cockpit.
 */

import { useEffect, useState, useCallback, useMemo } from 'react'
import { apiFetch } from '@/lib/api'
import { useTheme } from '@/contexts/ThemeContext'
import { TopBar } from '@/components/layout/TopBar'
import { Button } from '@/components/ui/button'
import {
  ListChecks, AlertTriangle, MessageSquare, Sparkles, X, CheckCircle2,
  Crown, LifeBuoy, RefreshCw, Search,
} from 'lucide-react'

interface Standing {
  assignee_id: number; assignee_name: string; score: number
  delivered: number; bonus: number; late_tax: number
  done_14d: number; ontime_rate: number
  open: number; overdue: number; blocked: number; total: number; done: number; pct_done: number
  reward_bwp: number; confirmed_month: number; points_month?: number
}
interface Champion extends Standing { comment: string }
interface Task {
  id: string; title: string; status: string; completion_pct: number | null
  assignee_name: string; assignee: number; priority: string
  due_at: string | null; due_time: string | null; is_overdue: boolean
}
interface Fame { champion: Champion | null; needs_support: Standing | null; standings: Standing[]; source: string }
interface Approval { key: string; label: string; count: number; href: string; oldest_days?: number }

const FB_STATUS = [
  { key: 'done' as const, label: 'Done' },
  { key: 'partial' as const, label: 'Partially done' },
  { key: 'not_done' as const, label: 'Not done' },
]
const PCT_OPTIONS = [25, 50, 75, 100]
const MIN_WORDS = 15
const REWARD_PER_TASK = 50   // BWP per manager-confirmed task above the monthly minimum
const REWARD_MIN_TASKS = 30
const wordCount = (s: string) => s.trim().split(/\s+/).filter(Boolean).length

const OPEN_STATUSES = ['pending', 'in_progress', 'partial', 'blocked']
const STATUS_LABEL: Record<string, string> = { pending: 'Not started', in_progress: 'In progress', partial: 'Partly done', blocked: 'Blocked', done: 'Done', cancelled: 'Cancelled' }
const PRIORITY_LABEL: Record<string, string> = { low: 'Low', normal: 'Normal', high: 'High', urgent: 'Urgent' }

export default function TaskDashboardPage() {
  const { theme: t, reduceMotion } = useTheme()
  const [standings, setStandings] = useState<Standing[]>([])
  const [tasks, setTasks] = useState<Task[]>([])
  const [windowDays, setWindowDays] = useState(14)
  const [reward, setReward] = useState({ per: REWARD_PER_TASK, min: REWARD_MIN_TASKS, cap: 2000, point_min: 60 })
  const [loading, setLoading] = useState(true)
  const [denied, setDenied] = useState(false)
  const [showOlder, setShowOlder] = useState(false)
  const [fbFor, setFbFor] = useState<Task | null>(null)
  const [fbText, setFbText] = useState('')
  const [fbBusy, setFbBusy] = useState(false)
  const [fbDone, setFbDone] = useState<string | null>(null)
  const [fbStatus, setFbStatus] = useState<'done' | 'partial' | 'not_done' | null>(null)
  const [fbPct, setFbPct] = useState<number>(50)
  const [ingest, setIngest] = useState('')
  const [ingestResult, setIngestResult] = useState<{ created_count: number; unmatched: string[]; commit: boolean } | null>(null)
  const [ingestBusy, setIngestBusy] = useState(false)
  const [fame, setFame] = useState<Fame | null>(null)
  const [fameBusy, setFameBusy] = useState(false)
  const [approvals, setApprovals] = useState<Approval[]>([])
  // filters
  const [fSearch, setFSearch] = useState('')
  const [fAssignee, setFAssignee] = useState('')
  const [fPriority, setFPriority] = useState('')
  const [fStatus, setFStatus] = useState('')

  const load = useCallback(async (silent = false, older = showOlder) => {
    if (!silent) setLoading(true)
    try {
      const r = await apiFetch<{ standings: Standing[]; tasks: Task[]; window_days: number; reward_per_task?: number; reward_min_tasks?: number; reward_cap?: number; reward_point_min?: number }>(
        '/taskboard/overview/' + (older ? '?all_done=1' : ''))
      setStandings(r.standings || []); setTasks(r.tasks); setWindowDays(r.window_days || 14)
      if (r.reward_per_task != null && r.reward_min_tasks != null) setReward({ per: r.reward_per_task, min: r.reward_min_tasks, cap: r.reward_cap ?? 2000, point_min: r.reward_point_min ?? 60 })
    } catch (e) {
      if (e instanceof Error && e.message.includes('403')) setDenied(true)
    } finally { if (!silent) setLoading(false) }
  }, [showOlder])
  useEffect(() => {
    load()
    const refresh = () => load(true)
    const onVis = () => { if (document.visibilityState === 'visible') load(true) }
    document.addEventListener('visibilitychange', onVis)
    window.addEventListener('focus', refresh)
    const id = window.setInterval(refresh, 120000)
    return () => {
      document.removeEventListener('visibilitychange', onVis)
      window.removeEventListener('focus', refresh)
      window.clearInterval(id)
    }
  }, [load])

  const loadFame = useCallback(async (refresh = false) => {
    setFameBusy(true)
    try {
      const r = await apiFetch<Fame>('/taskboard/hall-of-fame/' + (refresh ? '?refresh=1' : ''))
      setFame(r)
    } catch { /* non-fatal */ }
    finally { setFameBusy(false) }
  }, [])
  useEffect(() => { loadFame() }, [loadFame])

  useEffect(() => {
    apiFetch<{ streams: Approval[] }>('/my-approvals/')
      .then(r => setApprovals(r.streams || [])).catch(() => {})
  }, [])

  const fbWords = wordCount(fbText)
  const notDoneShort = fbStatus === 'not_done' && fbWords < MIN_WORDS
  const canSend = !!fbText.trim() && !notDoneShort && !fbBusy

  function openFeedback(t: Task) { setFbFor(t); setFbText(''); setFbStatus(null); setFbPct(50) }

  async function sendFeedback() {
    if (!fbFor || !canSend) return
    setFbBusy(true)
    try {
      const payload: Record<string, unknown> = { body: fbText.trim() }
      if (fbStatus) { payload.status = fbStatus; if (fbStatus === 'partial') payload.completion_pct = fbPct }
      await apiFetch(`/taskboard/tasks/${fbFor.id}/feedback/`, { method: 'POST', body: JSON.stringify(payload) })
      const hadStatus = !!fbStatus
      setFbDone(fbFor.id); setFbFor(null); setFbText(''); setFbStatus(null); setFbPct(50)
      setTimeout(() => setFbDone(null), 2500)
      if (hadStatus) { load(true); loadFame() }
    } finally { setFbBusy(false) }
  }

  async function runIngest(commit: boolean) {
    if (!ingest.trim()) return
    setIngestBusy(true)
    try {
      const r = await apiFetch<{ created_count: number; unmatched: string[]; commit: boolean }>(
        '/taskboard/ingest-plan/', { method: 'POST', body: JSON.stringify({ text: ingest, commit }) })
      setIngestResult(r)
      if (commit) { setIngest(''); load(true) }
    } finally { setIngestBusy(false) }
  }

  function toggleOlder() {
    const next = !showOlder
    setShowOlder(next); load(true, next)
  }

  // theme helpers
  const card: React.CSSProperties = { background: t.card, border: `1px solid ${t.cardBdr}`, boxShadow: t.cardSh, borderRadius: 16 }
  const inputStyle: React.CSSProperties = { background: t.input, border: `1px solid ${t.cardBdr}`, color: t.text }
  const anim = reduceMotion ? undefined : 'tdrise 240ms cubic-bezier(0.16,1,0.3,1) both'
  const priorityChip = (p: string) => {
    const map: Record<string, [string, string]> = {
      urgent: [t.erB, t.er], high: [t.wrB, t.wr], normal: [t.g100, t.t2], low: [t.g50, t.t3],
    }
    const [bg, fg] = map[p] || map.normal
    return { background: bg, color: fg }
  }
  const statusChip = (s: string): React.CSSProperties => {
    const map: Record<string, [string, string]> = {
      done: [t.okB, t.ok], blocked: [t.erB, t.er], partial: [t.wrB, t.wr],
      in_progress: [t.g100, t.inf], pending: [t.g100, t.t2],
    }
    const [bg, fg] = map[s] || [t.g100, t.t2]
    return { background: bg, color: fg, padding: '2px 8px', borderRadius: 999, fontSize: 11, fontWeight: 600 }
  }

  const filtered = useMemo(() => tasks.filter(tk =>
    (!fSearch || tk.title.toLowerCase().includes(fSearch.toLowerCase())) &&
    (!fAssignee || String(tk.assignee) === fAssignee) &&
    (!fPriority || tk.priority === fPriority) &&
    (!fStatus || (fStatus === 'open' ? OPEN_STATUSES.includes(tk.status) : tk.status === fStatus))
  ), [tasks, fSearch, fAssignee, fPriority, fStatus])

  const topScore = standings[0]?.score || 1
  // All three chips read off `filtered`, so they follow the assignee/priority
  // filter together. Before, Done and Overdue summed the whole unfiltered team
  // while Open followed the filter — so filtering to one person and reading the
  // Done chip showed a team total mistaken for that person's count
  // (reconciliation fix, 2026-09-01).
  const overdueCount = filtered.filter(tk => tk.is_overdue).length
  const doneWindow = filtered.filter(tk => tk.status === 'done').length

  if (denied) return (
    <div><TopBar />
      <div className="p-8 max-w-3xl mx-auto">
        <div style={{ ...card, padding: 24, color: t.text }} className="text-sm">
          This dashboard is for managers — it shows the tasks you assigned. Your own tasks live in <strong>Tasks</strong>, and your score, rank and reward are on <strong>My League</strong>.
        </div>
      </div>
    </div>
  )

  return (
    <div style={{ background: t.bg, minHeight: '100vh', color: t.text }}>
      <TopBar />
      <style>{`@keyframes tdrise{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}@keyframes tdbar{from{width:0}}`}</style>
      <div className="p-6 max-w-6xl mx-auto space-y-5">

        {/* Header + stat chips */}
        <div className="flex items-end justify-between flex-wrap gap-3 pt-1">
          <div>
            <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.22em]" style={{ color: t.orangeText }}>
              <span className="inline-block h-1.5 w-1.5 rounded-full" style={{ background: t.orange }} />
              The Alpha League
            </div>
            <h1 className="mt-1 text-2xl font-semibold flex items-center gap-2.5 tracking-tight" style={{ color: t.navy }}>
              <ListChecks className="h-6 w-6" style={{ color: t.orange }} /> Task Dashboard
            </h1>
            <p className="text-sm mt-1" style={{ color: t.t2 }}>Fair standings by what people finish — and who needs a hand. For the tasks you assigned.</p>
          </div>
          <div className="flex gap-3">
            {[
              { n: filtered.filter(tk => OPEN_STATUSES.includes(tk.status)).length, l: 'Open', c: t.navy },
              { n: doneWindow, l: `Done · ${windowDays}d`, c: t.ok },
              { n: overdueCount, l: 'Overdue', c: t.er },
            ].map(s => (
              <div key={s.l} style={{ ...card, padding: '8px 16px', textAlign: 'center' }}>
                <div className="text-2xl font-mono font-semibold tabular-nums" style={{ color: s.c }}>{s.n}</div>
                <div className="text-[10px] uppercase tracking-widest" style={{ color: t.t3 }}>{s.l}</div>
              </div>
            ))}
          </div>
        </div>

        {/* Approvals waiting on YOU */}
        {approvals.length > 0 && (
          <div style={{ ...card, padding: 20, animation: anim }}>
            <div className="text-xs font-semibold uppercase tracking-widest mb-3" style={{ color: t.t2 }}>⚠ Approvals waiting on you</div>
            <div className="space-y-2">
              {approvals.map(a => {
                const d = a.oldest_days || 0; const late = d >= 2
                return (
                  <a key={a.key} href={a.href} className="flex items-center gap-3 rounded-xl px-3 py-2.5 transition-colors"
                    style={{ background: t.g50 }}>
                    <span className="flex-1" style={{ color: t.text }}>{a.label}</span>
                    <span className="font-mono tabular-nums w-8 text-right" style={{ color: t.t2 }}>{a.count}</span>
                    <span className="text-xs font-medium w-32 text-right" style={{ color: late ? t.er : t.t3 }}>
                      {d > 0 ? `waiting ${d} day${d === 1 ? '' : 's'}` : 'just in'}{late ? ' ⚠' : ''}
                    </span>
                  </a>
                )
              })}
            </div>
          </div>
        )}

        {/* Champion + Needs support */}
        <div className="grid gap-4 md:grid-cols-2">
          <div style={{ ...card, padding: 18, borderColor: t.orange, animation: anim }}>
            <div className="flex items-center gap-2 text-[11px] uppercase tracking-widest" style={{ color: t.orangeText }}>
              <Crown className="h-4 w-4" /> League leader
              <button onClick={() => loadFame(true)} disabled={fameBusy} className="ml-auto flex items-center gap-1 text-xs disabled:opacity-50" style={{ color: t.t3 }}>
                <RefreshCw className={'h-3.5 w-3.5 ' + (fameBusy ? 'animate-spin' : '')} /> New line
              </button>
            </div>
            {fame?.champion ? (
              <>
                <div className="mt-1.5 flex items-baseline gap-2 flex-wrap">
                  <span className="text-xl font-semibold" style={{ color: t.navy }}>{fame.champion.assignee_name}</span>
                  <span className="text-sm font-mono tabular-nums" style={{ color: t.orange }}>{fame.champion.score} pts</span>
                </div>
                <div className="text-xs font-mono tabular-nums" style={{ color: t.t3 }}>
                  {fame.champion.done_14d} finished · {fame.champion.ontime_rate}% on time
                </div>
                <p className="mt-2 text-sm italic" style={{ color: t.text }}>&ldquo;{fame.champion.comment}&rdquo;</p>
              </>
            ) : <p className="text-sm py-3" style={{ color: t.t3 }}>{fameBusy ? 'Loading…' : 'No finished tasks to rank yet.'}</p>}
          </div>

          <div style={{ ...card, padding: 18, animation: anim }}>
            <div className="flex items-center gap-2 text-[11px] uppercase tracking-widest" style={{ color: t.inf }}>
              <LifeBuoy className="h-4 w-4" /> Needs support
            </div>
            {fame?.needs_support ? (
              <>
                <div className="mt-1.5 text-xl font-semibold" style={{ color: t.navy }}>{fame.needs_support.assignee_name}</div>
                <div className="text-xs mt-0.5" style={{ color: t.t2 }}>
                  {fame.needs_support.overdue > 0 && <span style={{ color: t.er }}>{fame.needs_support.overdue} overdue</span>}
                  {fame.needs_support.overdue > 0 && fame.needs_support.blocked > 0 && ' · '}
                  {fame.needs_support.blocked > 0 && <span style={{ color: t.wr }}>{fame.needs_support.blocked} blocked</span>}
                  {" — worth a check-in on what's stuck."}
                </div>
                <button onClick={() => { setFAssignee(String(fame!.needs_support!.assignee_id)); setFStatus('open') }}
                  className="mt-2 text-xs underline" style={{ color: t.inf }}>See their open tasks →</button>
              </>
            ) : <p className="text-sm py-3" style={{ color: t.ok }}>Nobody is stuck this fortnight. 🎉</p>}
          </div>
        </div>

        {/* Standings league table */}
        <div style={{ ...card, padding: 20, animation: anim }}>
          <div className="text-xs font-semibold uppercase tracking-widest mb-1" style={{ color: t.t2 }}>Standings · last {windowDays} days</div>
          <p className="text-[11px]" style={{ color: t.t3 }}>Points = tasks finished (harder + on-time earn more). Overdue work costs a little, capped — pending never counts against you.</p>
          <p className="text-[11px] mb-3" style={{ color: t.t3 }}>💰 Incentive (this month): P{reward.per} per normal task above {reward.min}/month — harder tasks (high/urgent) earn more per task, capped P{reward.cap.toLocaleString()} — managers &amp; ExCo excluded. Shown per person, pending CFO + HR approval.</p>
          {loading ? <p className="text-sm" style={{ color: t.t3 }}>Loading…</p> : standings.length === 0 ? (
            <p className="py-6 text-center" style={{ color: t.t3 }}>No tasks yet.</p>
          ) : (
            <div className="space-y-1.5">
              {standings.map((s, i) => (
                <div key={s.assignee_id} className="flex items-center gap-3 rounded-xl px-3 py-2.5" style={{ background: t.g50 }}>
                  <span className="w-6 text-center text-sm">{i === 0 ? '🥇' : i === 1 ? '🥈' : i === 2 ? '🥉' : <span style={{ color: t.t3 }}>{i + 1}</span>}</span>
                  <span className="flex-1 min-w-[130px] font-medium" style={{ color: t.text }}>{s.assignee_name}</span>
                  <span className="w-14 text-right font-mono tabular-nums font-semibold" style={{ color: t.navy }} title={`${s.delivered} delivered + ${s.bonus} on-time − ${s.late_tax} late`}>{s.score}</span>
                  <div className="w-40 h-2 rounded-full overflow-hidden hidden sm:block" style={{ background: t.g200 }}>
                    <div className="h-full rounded-full" style={{ width: `${Math.round(100 * s.score / topScore)}%`, background: t.orange, animation: reduceMotion ? undefined : 'tdbar 500ms cubic-bezier(0.16,1,0.3,1)' }} />
                  </div>
                  <span className="w-14 text-right text-xs font-mono tabular-nums" style={{ color: t.t2 }} title="finished in window">{s.done_14d} done</span>
                  <span className="w-16 text-right text-xs font-mono tabular-nums hidden md:inline" style={{ color: t.t3 }}>{s.ontime_rate}% on time</span>
                  <span className="w-16 text-right text-xs font-mono tabular-nums" style={{ color: s.overdue ? t.er : t.t3 }}>{s.overdue ? `${s.overdue} late` : `${s.open} open`}</span>
                  <span className="w-16 text-right text-xs font-mono tabular-nums font-semibold" style={{ color: s.reward_bwp > 0 ? t.teal : t.t3 }}
                    title={s.reward_bwp > 0 ? `${s.confirmed_month} manager-confirmed this month (${s.points_month ?? 0} priority points)` : `${s.confirmed_month} confirmed this month — earns above ${reward.point_min} points`}>
                    {s.reward_bwp > 0 ? `P${s.reward_bwp}` : '—'}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Tasks + filters */}
        <div style={{ ...card, padding: 20, animation: anim }}>
          <div className="flex items-center justify-between flex-wrap gap-2 mb-3">
            <div className="text-xs font-semibold uppercase tracking-widest" style={{ color: t.t2 }}>Tasks</div>
            <button onClick={toggleOlder} className="text-xs underline" style={{ color: t.t3 }}>
              {showOlder ? 'Hide older completed' : 'Show older completed'}
            </button>
          </div>
          {/* filter bar */}
          <div className="flex flex-wrap gap-2 mb-3">
            <div className="relative flex-1 min-w-[180px]">
              <Search className="h-3.5 w-3.5 absolute left-2.5 top-1/2 -translate-y-1/2" style={{ color: t.t3 }} />
              <input value={fSearch} onChange={e => setFSearch(e.target.value)} placeholder="Search task name…"
                className="w-full pl-8 pr-2 py-1.5 text-sm rounded-md" style={inputStyle} />
            </div>
            <select value={fAssignee} onChange={e => setFAssignee(e.target.value)} className="text-sm rounded-md px-2 py-1.5" style={inputStyle}>
              <option value="">All staff</option>
              {standings.map(s => <option key={s.assignee_id} value={s.assignee_id}>{s.assignee_name}</option>)}
            </select>
            <select value={fPriority} onChange={e => setFPriority(e.target.value)} className="text-sm rounded-md px-2 py-1.5" style={inputStyle}>
              <option value="">All priority</option>
              {['urgent', 'high', 'normal', 'low'].map(p => <option key={p} value={p}>{PRIORITY_LABEL[p]}</option>)}
            </select>
            <select value={fStatus} onChange={e => setFStatus(e.target.value)} className="text-sm rounded-md px-2 py-1.5" style={inputStyle}>
              <option value="">All status</option>
              <option value="open">Open (not done)</option>
              {['pending', 'in_progress', 'partial', 'blocked', 'done'].map(s => <option key={s} value={s}>{STATUS_LABEL[s]}</option>)}
            </select>
          </div>
          <div style={{ borderTop: `1px solid ${t.cardBdr}` }}>
            {filtered.map(tk => (
              <div key={tk.id} className="py-2.5 flex flex-wrap items-center gap-x-3 gap-y-1" style={{ borderBottom: `1px solid ${t.cardBdr}` }}>
                <span className="font-medium flex-1 min-w-[190px]" style={{ color: t.text }}>{tk.title}</span>
                <span style={priorityChip(tk.priority)} className="px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase">{PRIORITY_LABEL[tk.priority] || tk.priority}</span>
                <span className="text-xs w-36 truncate" style={{ color: t.t2 }}>{tk.assignee_name}</span>
                <span style={statusChip(tk.status)}>{STATUS_LABEL[tk.status] || tk.status}{tk.status === 'partial' && tk.completion_pct != null ? ` ${tk.completion_pct}%` : ''}</span>
                {tk.due_at && <span className="text-xs w-24" style={{ color: t.t3 }}>due {tk.due_time ? tk.due_time.slice(0, 5) : tk.due_at}</span>}
                {tk.is_overdue && <span className="text-xs flex items-center gap-1" style={{ color: t.er }}><AlertTriangle className="h-3 w-3" />overdue</span>}
                {tk.status !== 'done' && (
                  <Button size="sm" variant={fbDone === tk.id ? 'success' : 'outline'} onClick={() => openFeedback(tk)}>
                    {fbDone === tk.id ? <><CheckCircle2 className="h-4 w-4 mr-1" />sent</> : <><MessageSquare className="h-4 w-4 mr-1" />Feedback</>}
                  </Button>)}
              </div>))}
            {filtered.length === 0 && !loading && <p className="py-6 text-sm text-center" style={{ color: t.t3 }}>No tasks match.</p>}
          </div>
        </div>

        {/* Add this week's plan */}
        <div style={{ ...card, padding: 20 }} className="space-y-3">
          <div className="flex items-center gap-2 font-medium" style={{ color: t.text }}><Sparkles className="h-4 w-4" style={{ color: t.orange }} /> Add this week&apos;s plan</div>
          <p className="text-sm" style={{ color: t.t2 }}>Paste your planning message — one line per task, like <code style={{ color: t.orangeText }}>Legakwa - chase the vouchers</code> (LG / Kt shortcuts work). Preview, then create.</p>
          <textarea value={ingest} onChange={e => { setIngest(e.target.value); setIngestResult(null) }} rows={5}
            placeholder={'Medu - Project Nexus, follow the 9am instructions\nLG - why were the vouchers not paid\nKt - instant insurance matters'}
            className="w-full px-3 py-2 text-sm rounded-md font-mono" style={inputStyle} />
          <div className="flex items-center gap-3">
            <Button size="sm" variant="outline" disabled={!ingest.trim() || ingestBusy} onClick={() => runIngest(false)}>Preview</Button>
            <Button size="sm" disabled={!ingest.trim() || ingestBusy || !ingestResult} onClick={() => runIngest(true)}>Create tasks</Button>
          </div>
          {ingestResult && <p className="text-sm" style={{ color: t.text }}>{ingestResult.commit ? 'Created' : 'Would create'} <strong>{ingestResult.created_count}</strong> task(s).{ingestResult.unmatched.length > 0 && <span style={{ color: t.wr }}> Couldn&apos;t match: {ingestResult.unmatched.join(', ')}.</span>}</p>}
        </div>
      </div>

      {fbFor && (
        <div className="fixed inset-0 flex items-center justify-center z-50 p-4" style={{ background: 'rgba(0,0,0,0.5)' }} onClick={() => setFbFor(null)}>
          <div style={{ ...card, padding: 20, width: '100%', maxWidth: 448 }} className="space-y-3" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between"><div className="font-medium" style={{ color: t.text }}>Feedback — {fbFor.assignee_name}</div><button onClick={() => setFbFor(null)} aria-label="Close" style={{ color: t.t3 }}><X className="h-4 w-4" /></button></div>
            <p className="text-xs" style={{ color: t.t2 }}>On: {fbFor.title}. This goes to them and feeds their performance check-in.</p>
            <div>
              <div className="text-xs font-medium mb-1.5" style={{ color: t.t2 }}>Mark status</div>
              <div className="flex gap-2">
                {FB_STATUS.map(s => {
                  const active = fbStatus === s.key
                  const bg = active ? (s.key === 'not_done' ? t.er : s.key === 'partial' ? t.wr : t.ok) : t.g50
                  return (
                    <button key={s.key} type="button" onClick={() => setFbStatus(s.key)}
                      className="flex-1 text-sm py-1.5 rounded-md border transition-colors"
                      style={{ background: bg, borderColor: active ? bg : t.cardBdr, color: active ? '#fff' : t.text }}>
                      {s.label}
                    </button>)
                })}
              </div>
              <p className="text-[11px] mt-1.5" style={{ color: t.t3 }}>
                <span style={{ color: t.orange }}>★</span>{' '}
                {fbStatus === 'done' ? 'Adds Staff Rewards points to their Business Impact score.'
                  : fbStatus === 'partial' ? 'Adds a share of the points, based on how complete it is.'
                  : fbStatus === 'not_done' ? 'Earns no points — and removes any this task already earned.'
                  : 'Your status feeds their Staff Rewards score (gamification).'}
              </p>
            </div>
            {fbStatus === 'partial' && (
              <div className="flex items-center gap-2">
                <label htmlFor="fbpct" className="text-xs font-medium" style={{ color: t.t2 }}>How complete?</label>
                <select id="fbpct" value={fbPct} onChange={e => setFbPct(Number(e.target.value))} className="text-sm rounded-md px-2 py-1" style={inputStyle}>
                  {PCT_OPTIONS.map(p => <option key={p} value={p}>{p}%</option>)}
                </select>
              </div>)}
            <textarea value={fbText} onChange={e => setFbText(e.target.value)} rows={4} autoFocus
              placeholder={fbStatus === 'not_done' ? 'Explain why it is not done (at least 15 words)…' : 'Your feedback…'}
              className="w-full px-3 py-2 text-sm rounded-md" style={inputStyle} />
            {fbStatus === 'not_done' && (
              <p className="text-xs" style={{ color: notDoneShort ? t.er : t.ok }}>
                {fbWords}/{MIN_WORDS} words{notDoneShort ? ` — ${MIN_WORDS - fbWords} more needed` : ' ✓'}
              </p>)}
            <div className="flex justify-end gap-2"><Button size="sm" variant="outline" onClick={() => setFbFor(null)}>Cancel</Button><Button size="sm" disabled={!canSend} onClick={sendFeedback}>{fbStatus ? 'Save & send' : 'Send feedback'}</Button></div>
          </div>
        </div>)}
    </div>
  )
}
