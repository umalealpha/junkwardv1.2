'use client'

/**
 * HRIS — Monthly Performance Monitor (ELRA-2025).
 *
 * CFO directive 2026-07-02: applies to ALL employees. One light-touch monthly
 * check-in per employee: rating, KPI objectives (target vs result), evidence,
 * support given, employee response — building the contemporaneous written
 * trail the Employment & Labour Relations Act 2025 requires before any
 * performance-based action. Two consecutive low ratings recommend a warning;
 * three trigger a PIP (computed server-side).
 *
 * CONFIDENTIAL: gated by the HRIS whitelist + entity scope server-side
 * (HR Manager, Dorothy, CFO; managers via their entity grant). Do not widen.
 */
import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { useHrisAccessInfo } from '@/hooks/useHrisAccess'
import { authedHrisFetch } from '../_shared'
import { ShieldAlert, Plus, Loader2, Lock, AlertTriangle } from 'lucide-react'
import { localYmd } from '@/lib/utils'

const RATINGS: [string, string][] = [
  ['EX', 'Exceeds expectations'],
  ['ME', 'Meets expectations'],
  ['PA', 'Partially meets'],
  ['BE', 'Below expectations'],
  ['SB', 'Significantly below'],
]
// NR is DISPLAY-ONLY and deliberately absent from RATINGS above: Omni sets it
// when a manager did not respond, and no manager should ever be able to pick
// "not rated" from a dropdown.
const RATING_LABEL: Record<string, string> = {
  ...Object.fromEntries(RATINGS),
  NR: 'Not rated — posted by Omni, manager did not respond',
}
const LOW = new Set(['BE', 'SB'])

interface Objective { description: string; target: string; result: string }
interface CheckIn {
  id: string
  profile: string
  employee_name: string
  entity: string
  period_month: number
  period_year: number
  conversation_date: string
  overall_rating: string
  objectives: Objective[]
  evidence: string
  strengths: string
  concerns: string
  support_provided: string
  employee_response: string
  // The employee's right of reply, and whether Omni wrote this month because
  // the manager did not (CFO 2026-08-26).
  auto_posted: boolean
  employee_requested_comments: boolean
  manager_followup: string
  consecutive_low_count: number
  warning_recommended: boolean
  pip_triggered: boolean
  is_locked: boolean
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const MIN_ANSWER = 50   // matches hris.feedback_pushback.MIN_ANSWER_CHARS

export default function PerformanceMonitorPage() {
  // Answering an employee who does not accept their feedback. ONLY this clears
  // their request — ignoring the email is not enough (CFO 2026-08-26).
  const [answerFor, setAnswerFor] = useState<string | null>(null)
  const [answerText, setAnswerText] = useState('')
  const [answerBusy, setAnswerBusy] = useState(false)
  const [answerErr, setAnswerErr] = useState<string | null>(null)

  // The manager answers an employee who did not accept their feedback. The
  // backend refuses anything under 50 characters — a real answer, not "noted".
  async function answerRequest(id: string) {
    setAnswerBusy(true); setAnswerErr(null)
    try {
      const r = await authedHrisFetch(`/hris/api/performance/checkins/${id}/`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ manager_followup: answerText.trim() }),
      })
      const data = await r.json().catch(() => ({}))
      if (!r.ok) {
        setAnswerErr(data?.detail || 'Could not send.')
        return
      }
      setAnswerFor(null); setAnswerText('')
      await load()
    } catch (e) {
      setAnswerErr(e instanceof Error ? e.message : 'Could not send.')
    } finally { setAnswerBusy(false) }
  }
  const router = useRouter()
  const { theme } = useTheme()
  const info = useHrisAccessInfo()
  // Viewing tier (CFO 2026-07-05): exec/HR whitelist see all; a line manager
  // sees only their direct reports. Everyone else is bounced.
  const allowed = info === undefined ? undefined : (info.allowed || info.role === 'mgr')
  const isManagerOnly = !!info && !info.allowed && info.role === 'mgr'
  const [rows, setRows] = useState<CheckIn[]>([])
  const [emps, setEmps] = useState<{ pid: string; nm: string; company: string }[]>([])
  const [loading, setLoading] = useState(true)
  const [showForm, setShowForm] = useState(false)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const now = new Date()
  const [fPid, setFPid] = useState('')
  const [fMonth, setFMonth] = useState(now.getMonth() + 1)
  const [fYear, setFYear] = useState(now.getFullYear())
  const [fDate, setFDate] = useState(localYmd(now))
  const [fRating, setFRating] = useState('ME')
  const [fObjectives, setFObjectives] = useState<Objective[]>([{ description: '', target: '', result: '' }])
  const [fEvidence, setFEvidence] = useState('')
  const [fStrengths, setFStrengths] = useState('')
  const [fConcerns, setFConcerns] = useState('')
  const [fSupport, setFSupport] = useState('')
  const [fResponse, setFResponse] = useState('')

  useEffect(() => { if (allowed === false) router.replace('/dashboard') }, [allowed, router])

  async function load() {
    setLoading(true)
    try {
      const [cr, er] = await Promise.all([
        authedHrisFetch('/hris/api/performance/checkins/'),
        authedHrisFetch('/hris/api/performance/reportable/'),
      ])
      if (cr.ok) setRows((await cr.json()).checkins || [])
      else if (cr.status === 403) setMsg({ ok: false, text: (await cr.json()).detail || 'Access restricted.' })
      if (er.ok) setEmps(((await er.json()).employees || []).filter((e: { pid?: string }) => e.pid))
    } finally { setLoading(false) }
  }
  useEffect(() => { if (allowed === true) load() }, [allowed]) // eslint-disable-line react-hooks/exhaustive-deps

  const isLowRating = LOW.has(fRating)
  const canSubmit = fPid && fDate && fRating && (!isLowRating || (fEvidence.trim() && fConcerns.trim()))

  async function submit() {
    if (!canSubmit || busy) return
    setBusy(true); setMsg(null)
    try {
      const r = await authedHrisFetch('/hris/api/performance/checkins/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          profile: fPid,
          period_month: fMonth, period_year: fYear,
          conversation_date: fDate,
          overall_rating: fRating,
          objectives: fObjectives.filter(o => o.description.trim()),
          evidence: fEvidence, strengths: fStrengths, concerns: fConcerns,
          support_provided: fSupport, employee_response: fResponse,
        }),
      })
      const data = await r.json()
      if (r.ok) {
        setMsg({ ok: true, text: `Check-in saved for ${data.employee_name} — ${MONTHS[fMonth - 1]} ${fYear}.` })
        setShowForm(false)
        setFObjectives([{ description: '', target: '', result: '' }])
        setFEvidence(''); setFStrengths(''); setFConcerns(''); setFSupport(''); setFResponse('')
        await load()
      } else {
        const first = typeof data === 'object' ? Object.values(data).flat().join(' ') : String(data)
        setMsg({ ok: false, text: first || 'Could not save.' })
      }
    } catch (e) { setMsg({ ok: false, text: e instanceof Error ? e.message : 'Failed' }) }
    finally { setBusy(false) }
  }

  const card = { background: theme.card, border: `1px solid ${theme.cardBdr}` }
  const input = { background: theme.g100, color: theme.text, border: `1px solid ${theme.cardBdr}` }
  const ORANGE = '#F4A623'

  const ratingBadge = (code: string) => {
    const c = code === 'EX' ? '#0F7A37' : code === 'ME' ? '#15803D' : code === 'PA' ? '#B45309' : '#B42318'
    return (
      <span className="text-[11px] px-2 py-0.5 rounded-full font-semibold" style={{ background: `${c}18`, color: c }}>
        {RATING_LABEL[code] || code}
      </span>
    )
  }

  const grouped = useMemo(() => {
    const m = new Map<string, CheckIn[]>()
    rows.forEach(r => {
      const k = `${r.period_year}-${String(r.period_month).padStart(2, '0')}`
      m.set(k, [...(m.get(k) || []), r])
    })
    return [...m.entries()].sort((a, b) => b[0].localeCompare(a[0]))
  }, [rows])

  if (allowed !== true) return null

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar title="Performance Monitor" breadcrumbs={[{ label: 'People' }, { label: 'Performance Monitor' }]} />
      <main className="flex-1 overflow-y-auto p-5 space-y-5">
        {/* confidentiality banner */}
        <div className="rounded-xl px-4 py-3 flex items-center gap-2 text-sm"
             style={{ background: '#FFFBEB', border: '1px solid #FDE08A', color: '#7A5B00' }}>
          <ShieldAlert className="w-4 h-4 shrink-0" />
          {isManagerOnly
            ? 'Confidential — you are viewing performance check-ins for your direct reports only.'
            : 'Confidential — monthly performance check-ins for the ELRA-2025 written trail. Recorded for all staff; visible only to the employee’s manager, HR, and the CEO / CFO / COO.'}
        </div>

        {msg && (
          <div className="rounded-xl px-4 py-3 text-sm"
               style={{ background: msg.ok ? '#ECFDF5' : '#FEF2F2', border: `1px solid ${msg.ok ? '#A7F3D0' : '#FECACA'}`, color: msg.ok ? '#065F46' : '#991B1B' }}>
            {msg.text}
          </div>
        )}

        {/* new check-in */}
        <section className="rounded-2xl p-5" style={card}>
          <div className="flex items-center justify-between">
            <div>
              <div className="text-[10px] uppercase tracking-wider font-semibold" style={{ color: ORANGE }}>Monthly check-in</div>
              <h2 className="text-sm font-bold" style={{ color: theme.text }}>Record this month&rsquo;s conversation</h2>
            </div>
            <button onClick={() => setShowForm(s => !s)}
              className="inline-flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-semibold"
              style={{ background: ORANGE, color: '#fff' }}>
              <Plus className="w-4 h-4" /> {showForm ? 'Close' : 'New check-in'}
            </button>
          </div>

          {showForm && (
            <div className="mt-4 space-y-3">
              <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
                <select value={fPid} onChange={e => setFPid(e.target.value)} className="rounded-lg px-3 py-2 text-sm md:col-span-2" style={input}>
                  <option value="">Employee…</option>
                  {emps.map(e => <option key={e.pid} value={e.pid}>{e.nm} — {e.company}</option>)}
                </select>
                <select value={fMonth} onChange={e => setFMonth(+e.target.value)} className="rounded-lg px-3 py-2 text-sm" style={input}>
                  {MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
                </select>
                <select value={fYear} onChange={e => setFYear(+e.target.value)} className="rounded-lg px-3 py-2 text-sm" style={input}>
                  {[fYear - 1, fYear, fYear + 1].filter((v, i, a) => a.indexOf(v) === i).map(y => <option key={y} value={y}>{y}</option>)}
                </select>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                <div>
                  <label className="text-[11px] font-medium" style={{ color: theme.t2 }}>Conversation date</label>
                  <input type="date" value={fDate} onChange={e => setFDate(e.target.value)} className="w-full rounded-lg px-3 py-2 text-sm" style={input} />
                </div>
                <div className="md:col-span-2">
                  <label className="text-[11px] font-medium" style={{ color: theme.t2 }}>Overall rating</label>
                  <select value={fRating} onChange={e => setFRating(e.target.value)} className="w-full rounded-lg px-3 py-2 text-sm" style={input}>
                    {RATINGS.map(([c, l]) => <option key={c} value={c}>{l}</option>)}
                  </select>
                </div>
              </div>

              {/* KPI objectives */}
              <div>
                <label className="text-[11px] font-medium" style={{ color: theme.t2 }}>KPI objectives (target vs result)</label>
                {fObjectives.map((o, i) => (
                  <div key={i} className="grid grid-cols-1 md:grid-cols-3 gap-2 mt-1">
                    <input placeholder="Objective (e.g. New-business commission)" value={o.description}
                      onChange={e => setFObjectives(a => a.map((x, j) => j === i ? { ...x, description: e.target.value } : x))}
                      className="rounded-lg px-3 py-2 text-sm" style={input} />
                    <input placeholder="Target" value={o.target}
                      onChange={e => setFObjectives(a => a.map((x, j) => j === i ? { ...x, target: e.target.value } : x))}
                      className="rounded-lg px-3 py-2 text-sm" style={input} />
                    <input placeholder="Result" value={o.result}
                      onChange={e => setFObjectives(a => a.map((x, j) => j === i ? { ...x, result: e.target.value } : x))}
                      className="rounded-lg px-3 py-2 text-sm" style={input} />
                  </div>
                ))}
                <button onClick={() => setFObjectives(a => [...a, { description: '', target: '', result: '' }])}
                  className="text-[11px] mt-1 font-semibold" style={{ color: ORANGE }}>+ add objective</button>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <div>
                  <label className="text-[11px] font-medium" style={{ color: theme.t2 }}>
                    Evidence {isLowRating && <span style={{ color: '#B42318' }}>(required for a low rating)</span>}
                  </label>
                  <textarea value={fEvidence} onChange={e => setFEvidence(e.target.value)} rows={2}
                    className="w-full rounded-lg px-3 py-2 text-sm" style={input} />
                </div>
                <div>
                  <label className="text-[11px] font-medium" style={{ color: theme.t2 }}>
                    Areas of concern {isLowRating && <span style={{ color: '#B42318' }}>(required)</span>}
                  </label>
                  <textarea value={fConcerns} onChange={e => setFConcerns(e.target.value)} rows={2}
                    className="w-full rounded-lg px-3 py-2 text-sm" style={input} />
                </div>
                <div>
                  <label className="text-[11px] font-medium" style={{ color: theme.t2 }}>Strengths</label>
                  <textarea value={fStrengths} onChange={e => setFStrengths(e.target.value)} rows={2}
                    className="w-full rounded-lg px-3 py-2 text-sm" style={input} />
                </div>
                <div>
                  <label className="text-[11px] font-medium" style={{ color: theme.t2 }}>Support / opportunity to improve (ELRA)</label>
                  <textarea value={fSupport} onChange={e => setFSupport(e.target.value)} rows={2}
                    className="w-full rounded-lg px-3 py-2 text-sm" style={input} />
                </div>
              </div>
              <div>
                <label className="text-[11px] font-medium" style={{ color: theme.t2 }}>Employee response</label>
                <textarea value={fResponse} onChange={e => setFResponse(e.target.value)} rows={2}
                  className="w-full rounded-lg px-3 py-2 text-sm" style={input} />
              </div>
              <div className="flex justify-end">
                <button onClick={submit} disabled={!canSubmit || busy}
                  className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold"
                  style={{ background: '#0D1B2A', color: '#fff', opacity: !canSubmit || busy ? 0.5 : 1 }}>
                  {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : null} Save check-in
                </button>
              </div>
            </div>
          )}
        </section>

        {/* register */}
        <section className="rounded-2xl p-5" style={card}>
          <div className="text-[10px] uppercase tracking-wider font-semibold" style={{ color: ORANGE }}>Register</div>
          <h2 className="text-sm font-bold mb-3" style={{ color: theme.text }}>Check-in trail (newest month first)</h2>
          {loading ? (
            <div className="flex items-center gap-2 text-sm" style={{ color: theme.t2 }}>
              <Loader2 className="w-4 h-4 animate-spin" /> Loading…
            </div>
          ) : rows.length === 0 ? (
            <p className="text-sm" style={{ color: theme.t2 }}>No check-ins recorded yet. Use &ldquo;New check-in&rdquo; to log the first monthly conversation.</p>
          ) : grouped.map(([key, list]) => {
            const [y, m] = key.split('-')
            return (
              <div key={key} className="mb-4">
                <div className="text-[11px] uppercase tracking-[0.15em] font-semibold mb-1.5" style={{ color: theme.t2 }}>
                  {MONTHS[+m - 1]} {y}
                </div>
                <div className="space-y-1.5">
                  {list.map(r => (
                    <div key={r.id} className="flex items-center gap-3 rounded-xl px-3 py-2.5 flex-wrap"
                         style={{ background: theme.g100 }}>
                      <span className="text-sm font-semibold min-w-[160px]" style={{ color: theme.text }}>{r.employee_name}</span>
                      <span className="text-[11px]" style={{ color: theme.t2 }}>{r.entity}</span>
                      {ratingBadge(r.overall_rating)}
                      {Array.isArray(r.objectives) && r.objectives.length > 0 && (
                        <span className="text-[11px]" style={{ color: theme.t2 }}>
                          {r.objectives.length} KPI{r.objectives.length === 1 ? '' : 's'}
                        </span>
                      )}
                      {r.consecutive_low_count > 0 && (
                        <span className="text-[11px] px-2 py-0.5 rounded-full font-semibold"
                              style={{ background: '#FEF2F2', color: '#B42318' }}>
                          {r.consecutive_low_count} consecutive low
                        </span>
                      )}
                      {r.warning_recommended && (
                        <span className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full font-semibold"
                              style={{ background: '#FFF7ED', color: '#B45309' }}>
                          <AlertTriangle className="w-3 h-3" /> warning recommended
                        </span>
                      )}
                      {r.pip_triggered && (
                        <span className="text-[11px] px-2 py-0.5 rounded-full font-semibold"
                              style={{ background: '#B42318', color: '#fff' }}>PIP</span>
                      )}
                      {r.auto_posted && (
                        <span className="text-[11px] px-2 py-0.5 rounded-full font-semibold"
                              style={{ background: '#FDF3E3', color: '#8A5A00' }}
                              title="Omni recorded this month because no feedback came back from the manager">
                          posted by Omni
                        </span>
                      )}
                      {r.employee_requested_comments && (
                        <span className="text-[11px] px-2 py-0.5 rounded-full font-semibold"
                              style={{ background: '#FEF2F2', color: '#B42318' }}>
                          awaiting your comment
                        </span>
                      )}
                      {r.is_locked && <Lock className="w-3.5 h-3.5" style={{ color: theme.t2 }} />}
                      <span className="ml-auto text-[11px]" style={{ color: theme.t2 }}>{r.conversation_date}</span>

                      {/* The employee does not accept this and has asked the
                          manager to comment. ONLY answering clears it — the
                          fairness half of the CFO's 2026-08-26 instruction. */}
                      {r.employee_requested_comments && (
                        <div className="w-full mt-2 rounded-lg p-2.5" style={{ background: '#FFF7ED' }}>
                          <p className="text-[12px] mb-1" style={{ color: '#8A5A00' }}>
                            <b>{r.employee_name}</b> does not accept this and asked you to comment:
                          </p>
                          <p className="text-[12px] mb-2 italic" style={{ color: theme.text }}>
                            &ldquo;{r.employee_response}&rdquo;
                          </p>
                          {answerFor === r.id ? (
                            <>
                              <textarea value={answerText} rows={3}
                                onChange={e => setAnswerText(e.target.value)}
                                placeholder="Answer them properly - at least 50 characters."
                                className="w-full text-[12.5px] rounded-lg p-2 mb-1"
                                style={{ border: '1px solid #E5E7EB', color: theme.text }} />
                              <div className="text-[11px] mb-1"
                                   style={{ color: answerText.trim().length >= MIN_ANSWER ? '#047857' : '#B42318' }}>
                                {answerText.trim().length}/{MIN_ANSWER} characters
                              </div>
                              {answerErr && <p role="alert" className="text-[11px] mb-1" style={{ color: '#B42318' }}>{answerErr}</p>}
                              <button disabled={answerBusy || answerText.trim().length < MIN_ANSWER}
                                onClick={() => answerRequest(r.id)}
                                className="text-[12px] font-semibold px-3 py-1.5 rounded-lg disabled:opacity-40"
                                style={{ background: '#0D1B2A', color: '#fff' }}>
                                {answerBusy ? 'Sending…' : 'Send my comment'}
                              </button>
                            </>
                          ) : (
                            <button onClick={() => { setAnswerFor(r.id); setAnswerText(''); setAnswerErr(null) }}
                              className="text-[12px] font-semibold px-3 py-1.5 rounded-lg"
                              style={{ background: '#0D1B2A', color: '#fff' }}>
                              Answer them
                            </button>
                          )}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )
          })}
        </section>
      </main>
    </div>
  )
}
