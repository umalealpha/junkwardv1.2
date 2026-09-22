'use client'

/**
 * HRIS — Monthly Manager Feedback (CFO 2026-07-20).
 *
 * The MONTHLY companion to the 6-monthly Development Dialogue. Each manager gives
 * a short monthly note to every direct report — what they did well, what they did
 * not, where to improve — plus confirms whether they hit their monthly target.
 * A decision panel (Time Doctor hours, absence, leave, sick, task on-time record)
 * lets the manager judge on facts. The three boxes come pre-filled from those
 * facts so the manager edits rather than writes. Feedback is saved as the ELRA
 * MonthlyCheckIn record (locks + retention + escalation reused). C-suite see a
 * league table of which managers have and have not done it.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { useHrisAccessInfo } from '@/hooks/useHrisAccess'
import { authedHrisFetch } from '../_shared'
import { ShieldAlert, Loader2, Lock, CheckCircle2, XCircle, Clock, ChevronDown, Trophy, Flag } from 'lucide-react'
import { localYmd } from '@/lib/utils'

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const RATINGS: [string, string][] = [
  ['EX', 'Exceeds expectations'], ['ME', 'Meets expectations'], ['PA', 'Partially meets'],
  ['BE', 'Below expectations'], ['SB', 'Significantly below'],
]
const LOW = new Set(['BE', 'SB'])
const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'

interface Target {
  target_id: string; metric: string; target_value: number; unit: string
  source: string; actual_value: number | null
  actual_excl?: number | null; actual_vat?: number | null; actual_incl?: number | null
  achieved: boolean | null; confirmed: boolean; note: string
}
interface TrendPt { year: number; month: number; rating: string | null; targets_hit: number; targets_total: number; feedback_given: boolean }
interface Panel {
  tracked_hours: number; absent_days: number; leave_days: number; sick_days: number
  tasks_assigned: number; tasks_completed: number; tasks_on_time: number; tasks_on_time_pct: number | null
}
interface Draft { strengths: string; concerns: string; support_provided: string }
interface Emp {
  profile_id: string; name: string; position: string; panel: Panel; targets: Target[]
  trend: TrendPt[]; feedback_given: boolean; checkin_id: string | null; checkin_locked: boolean; draft: Draft | null
}
interface LeagueRow { manager: string; manager_id: string; reports: number; given: number; outstanding: number; complete: boolean }

function fmt(v: number | null | undefined, unit: string) {
  if (v === null || v === undefined) return '—'
  if (unit === 'BWP') return `BWP ${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
  if (unit === 'percent') return `${v}%`
  return `${v}`
}

export default function MonthlyFeedbackPage() {
  const router = useRouter()
  const { theme } = useTheme()
  const info = useHrisAccessInfo()
  const allowed = info === undefined ? undefined : (info.allowed || info.role === 'mgr')

  const now = new Date()
  const [year, setYear] = useState(now.getFullYear())
  const [month, setMonth] = useState(now.getMonth() + 1)
  const [tier, setTier] = useState<string>('')
  const [emps, setEmps] = useState<Emp[]>([])
  const [league, setLeague] = useState<LeagueRow[]>([])
  const [drill, setDrill] = useState<{ id: string; name: string } | null>(null)
  const [loading, setLoading] = useState(true)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => { if (allowed === false) router.replace('/dashboard') }, [allowed, router])

  // Deep link from the morning exceptions email: /hris/monthly-feedback?manager=<id>
  // pre-selects that manager's team, so the named person lands on their own
  // people instead of a blank screen (CFO 2026-08-07). Read from window rather
  // than useSearchParams() — this is a client component and useSearchParams
  // forces a Suspense boundary at build time (same reason as /report-bug).
  // The id alone is enough; the name fills in from the league table below.
  useEffect(() => {
    const wanted = new URLSearchParams(window.location.search).get('manager')
    if (wanted) setDrill(d => d ?? { id: wanted, name: '' })
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const q = `year=${year}&month=${month}` + (drill ? `&manager=${drill.id}` : '')
      const r = await authedHrisFetch(`/hris/api/performance/monthly/?${q}`)
      if (r.ok) {
        const d = await r.json()
        setTier(d.tier); setEmps(d.employees || [])
        if (d.tier === 'full') {
          const lr = await authedHrisFetch(`/hris/api/performance/monthly/league/?year=${year}&month=${month}`)
          if (lr.ok) setLeague((await lr.json()).managers || [])
        }
      } else if (r.status === 403) {
        setMsg({ ok: false, text: (await r.json()).detail || 'Access restricted.' })
      }
    } finally { setLoading(false) }
  }, [year, month, drill])
  useEffect(() => { if (allowed === true) load() }, [allowed, load])

  const card = { background: theme.card, border: `1px solid ${theme.cardBdr}` }

  if (allowed !== true) return null

  const pending = emps.filter(e => !e.feedback_given).length

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar title="Monthly Feedback" breadcrumbs={[{ label: 'People' }, { label: 'Monthly Feedback' }]} />
      <main className="flex-1 overflow-y-auto p-5 space-y-5">
        <div className="rounded-xl px-4 py-3 flex items-center gap-2 text-sm"
             style={{ background: '#FFFBEB', border: '1px solid #FDE08A', color: '#7A5B00' }}>
          <ShieldAlert className="w-4 h-4 shrink-0" />
          A short monthly note for each of your team — what went well, what didn’t, where to improve —
          and confirm their monthly target. Judge on the facts shown. Confidential to the manager, HR and the C-suite.
        </div>

        {msg && (
          <div className="rounded-xl px-4 py-3 text-sm"
               style={{ background: msg.ok ? '#ECFDF5' : '#FEF2F2', border: `1px solid ${msg.ok ? '#A7F3D0' : '#FECACA'}`, color: msg.ok ? '#065F46' : '#991B1B' }}>
            {msg.text}
          </div>
        )}

        {/* period + progress */}
        <section className="rounded-2xl p-4 flex items-center gap-3 flex-wrap" style={card}>
          <div className="text-[10px] uppercase tracking-wider font-semibold" style={{ color: ORANGE }}>Period</div>
          <select value={month} onChange={e => setMonth(+e.target.value)} className="rounded-lg px-3 py-1.5 text-sm"
                  style={{ background: theme.g100, color: theme.text, border: `1px solid ${theme.cardBdr}` }}>
            {MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
          </select>
          <select value={year} onChange={e => setYear(+e.target.value)} className="rounded-lg px-3 py-1.5 text-sm"
                  style={{ background: theme.g100, color: theme.text, border: `1px solid ${theme.cardBdr}` }}>
            {[year - 1, year, year + 1].filter((v, i, a) => a.indexOf(v) === i).map(y => <option key={y} value={y}>{y}</option>)}
          </select>
          {!loading && emps.length > 0 && (
            <span className="ml-auto text-sm font-semibold" style={{ color: pending ? '#B42318' : '#15803D' }}>
              {pending ? `${pending} of ${emps.length} still to do` : 'All feedback given ✓'}
            </span>
          )}
        </section>

        {/* C-suite league table */}
        {tier === 'full' && league.length > 0 && (
          <section className="rounded-2xl p-5" style={card}>
            <div className="flex items-center gap-2 mb-1">
              <Trophy className="w-4 h-4" style={{ color: ORANGE }} />
              <h2 className="text-sm font-bold" style={{ color: theme.text }}>Manager league — {MONTHS[month - 1]} {year}</h2>
            </div>
            <p className="text-[11px] mb-3" style={{ color: theme.t2 }}>Click a manager to review their team.</p>
            <div className="space-y-1">
              {league.map(r => (
                <button key={r.manager_id} onClick={() => setDrill({ id: r.manager_id, name: r.manager })}
                  className="w-full flex items-center gap-3 rounded-lg px-3 py-2 text-sm text-left transition-colors hover:brightness-95"
                  style={{ background: drill?.id === r.manager_id ? '#FFF7E8' : theme.g100 }}>
                  <span className="font-semibold min-w-[180px]" style={{ color: theme.text }}>{r.manager}</span>
                  <div className="flex-1 h-2 rounded-full overflow-hidden" style={{ background: theme.cardBdr }}>
                    <div className="h-full rounded-full transition-all duration-500"
                         style={{ width: `${r.reports ? Math.round(100 * r.given / r.reports) : 0}%`, background: r.complete ? '#15803D' : ORANGE }} />
                  </div>
                  <span className="text-[12px] tabular-nums" style={{ color: theme.t2 }}>{r.given}/{r.reports}</span>
                  {r.complete
                    ? <CheckCircle2 className="w-4 h-4" style={{ color: '#15803D' }} />
                    : <span className="text-[11px] px-2 py-0.5 rounded-full font-semibold" style={{ background: '#FEF2F2', color: '#B42318' }}>{r.outstanding} owing</span>}
                </button>
              ))}
            </div>
          </section>
        )}

        {tier === 'full' && drill && (
          <div className="rounded-xl px-4 py-2.5 flex items-center gap-2 text-sm" style={{ background: '#FFF7E8', border: '1px solid #FDE08A', color: '#7A5B00' }}>
            {/* Arriving from the email link we have the id but not yet the
                name — take it from the league table once that loads, so the
                banner never reads "Viewing ’s team." (CFO 2026-08-07). */}
            Viewing <b>{drill.name || league.find(r => r.manager_id === drill.id)?.manager || 'this'}</b>’s team.
            <button onClick={() => setDrill(null)} className="ml-auto font-semibold" style={{ color: '#B04E00' }}>Clear</button>
          </div>
        )}

        {loading ? (
          <div className="flex items-center gap-2 text-sm" style={{ color: theme.t2 }}>
            <Loader2 className="w-4 h-4 animate-spin" /> Loading your team…
          </div>
        ) : emps.length === 0 ? (
          <p className="text-sm" style={{ color: theme.t2 }}>
            {tier === 'full' && !drill ? 'Pick a manager above to review their team.' : 'No direct reports found for you.'}
          </p>
        ) : emps.map(e => (
          <EmployeeCard key={e.profile_id} emp={e} theme={theme} year={year} month={month}
                        onSaved={(t) => { setMsg({ ok: true, text: t }); load() }}
                        onError={(t) => setMsg({ ok: false, text: t })} />
        ))}
      </main>
    </div>
  )
}

/* ── one report's card ─────────────────────────────────────────────────────── */
function EmployeeCard({ emp, theme, year, month, onSaved, onError }: {
  emp: Emp; theme: any; year: number; month: number  // eslint-disable-line @typescript-eslint/no-explicit-any
  onSaved: (t: string) => void; onError: (t: string) => void
}) {
  const [open, setOpen] = useState(!emp.feedback_given)
  const [rating, setRating] = useState('ME')
  const [well, setWell] = useState(emp.draft?.strengths || '')
  const [notwell, setNotwell] = useState(emp.draft?.concerns || '')
  const [improve, setImprove] = useState(emp.draft?.support_provided || '')
  const [actuals, setActuals] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [flagged, setFlagged] = useState(false)
  const isLow = LOW.has(rating)

  // CFO 2026-09-05: "This person doesn't report to me" — one click raises the
  // same roster flag the Monthly Return uses. HR (Unami or Dorothy) moves them;
  // the person stays on the list until HR does.
  async function notMine() {
    if (!confirm(`Tell HR that ${emp.name} does not report to you? They stay on your list until HR moves them.`)) return
    try {
      const r = await authedHrisFetch('/hris/api/roster-flags/', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ profile_id: emp.profile_id, kind: 'not_mine', note: 'Flagged from Monthly Feedback' }),
      })
      if (r.ok || r.status === 200 || r.status === 201) { setFlagged(true); onSaved(`Sent to HR: ${emp.name} does not report to you.`) }
      else onError((await r.json().catch(() => ({}))).detail || 'Could not flag.')
    } catch { onError('Could not flag.') }
  }
  // Low rating needs a "not well" note (backend requires concerns+evidence);
  // otherwise any one of the three boxes filled is enough.
  const canSave = !emp.feedback_given && (isLow ? !!notwell.trim() : !!(well.trim() || notwell.trim() || improve.trim()))

  const card = { background: theme.card, border: `1px solid ${theme.cardBdr}` }
  const input = { background: theme.g100, color: theme.text, border: `1px solid ${theme.cardBdr}` }

  async function confirmTarget(t: Target, achieved: boolean) {
    const raw = actuals[t.target_id]
    const actual = raw !== undefined && raw !== '' ? Number(raw) : t.actual_value
    try {
      const r = await authedHrisFetch('/hris/api/performance/monthly/confirm-target/', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_id: t.target_id, year, month, achieved, actual_value: actual }),
      })
      if (r.ok) onSaved(`Target recorded for ${emp.name}.`)
      else onError((await r.json()).detail || 'Could not record target.')
    } catch { onError('Could not record target.') }
  }

  async function save() {
    if (!canSave || busy) return
    setBusy(true)
    try {
      const objectives = emp.targets.map(t => ({
        description: t.metric,
        target: fmt(t.target_value, t.unit),
        result: t.actual_value === null ? '' : fmt(t.actual_value, t.unit),
      }))
      const r = await authedHrisFetch('/hris/api/performance/checkins/', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          profile: emp.profile_id, period_month: month, period_year: year,
          conversation_date: localYmd(new Date()),
          overall_rating: rating, objectives,
          strengths: well, concerns: notwell, support_provided: improve,
          evidence: isLow ? notwell : '',
        }),
      })
      const d = await r.json()
      if (r.ok) onSaved(`Feedback saved for ${emp.name}.`)
      else onError(typeof d === 'object' ? Object.values(d).flat().join(' ') : 'Could not save.')
    } catch (e) { onError(e instanceof Error ? e.message : 'Failed') }
    finally { setBusy(false) }
  }

  const p = emp.panel
  const chips: [string, string, string?][] = [
    ['Tracked', `${p.tracked_hours.toFixed(1)}h`],
    ['Absence', `${p.absent_days}d`, p.absent_days > 0 ? '#B42318' : undefined],
    ['Leave', `${p.leave_days}d`],
    ['Sick', `${p.sick_days}d`, p.sick_days > 0 ? '#B45309' : undefined],
    ['Tasks on time', p.tasks_completed ? `${p.tasks_on_time}/${p.tasks_completed} (${p.tasks_on_time_pct}%)` : '—',
      p.tasks_on_time_pct !== null && p.tasks_on_time_pct < 60 ? '#B42318' : undefined],
  ]

  return (
    <section className="rounded-2xl p-5" style={card}>
      <div className="flex items-center gap-3 flex-wrap">
        <div>
          <div className="text-sm font-bold" style={{ color: theme.text }}>{emp.name}</div>
          <div className="text-[11px]" style={{ color: theme.t2 }}>{emp.position || '—'}</div>
        </div>
        {emp.feedback_given
          ? <span className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full font-semibold" style={{ background: '#ECFDF5', color: '#15803D' }}><CheckCircle2 className="w-3 h-3" /> Feedback given</span>
          : <span className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full font-semibold" style={{ background: '#FEF2F2', color: '#B42318' }}><Clock className="w-3 h-3" /> Outstanding</span>}
        {emp.checkin_locked && <Lock className="w-3.5 h-3.5" style={{ color: theme.t2 }} />}
        {!emp.feedback_given && (
          flagged
            ? <span className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full font-semibold" style={{ background: '#FFFBEB', color: '#92400E' }}><Flag className="w-3 h-3" /> Waiting on HR</span>
            : <button onClick={notMine} title="This person does not report to me"
                className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full font-semibold"
                style={{ border: `1px solid ${theme.cardBdr}`, color: theme.t2 }}><Flag className="w-3 h-3" /> Not my report</button>
        )}
        <Sparkline trend={emp.trend} />
        <button onClick={() => setOpen(o => !o)} className="ml-auto inline-flex items-center gap-1 text-[12px] font-semibold" style={{ color: ORANGE }}>
          {open ? 'Hide' : (emp.feedback_given ? 'View' : 'Give feedback')}
          <ChevronDown className={`w-4 h-4 transition-transform duration-300 ${open ? 'rotate-180' : ''}`} />
        </button>
      </div>

      {/* decision panel */}
      <div className="mt-3 grid grid-cols-2 md:grid-cols-5 gap-2">
        {chips.map(([label, val, color]) => (
          <div key={label} className="rounded-xl px-3 py-2" style={{ background: theme.g100 }}>
            <div className="text-[10px] uppercase tracking-wide" style={{ color: theme.t2 }}>{label}</div>
            <div className="text-[15px] font-extrabold" style={{ color: color || theme.text }}>{val}</div>
          </div>
        ))}
      </div>

      {/* targets */}
      {emp.targets.length > 0 && (
        <div className="mt-3 space-y-2">
          {emp.targets.map(t => (
            <div key={t.target_id} className="flex items-center gap-3 rounded-xl px-3 py-2 flex-wrap" style={{ background: theme.g100 }}>
              <span className="text-sm font-semibold" style={{ color: theme.text }}>{t.metric}</span>
              <span className="text-[12px] flex flex-wrap items-center gap-x-2" style={{ color: theme.t2 }}>
                <span>target {fmt(t.target_value, t.unit)} (excl)</span>
                {t.actual_incl != null ? (
                  <span>
                    · actual — Excl <b style={{ color: theme.text }}>{fmt(t.actual_excl ?? t.actual_value, t.unit)}</b>
                    {' · '}VAT <b style={{ color: theme.text }}>{fmt(t.actual_vat ?? 0, t.unit)}</b>
                    {' · '}Incl <b style={{ color: ORANGE }}>{fmt(t.actual_incl, t.unit)}</b>
                  </span>
                ) : (
                  <span>· actual {fmt(t.actual_value, t.unit)}</span>
                )}
                {t.source === 'health_quotes' && <span className="opacity-70">(auto)</span>}
              </span>
              <div className="ml-auto flex items-center gap-1.5">
                {t.source !== 'health_quotes' && (
                  <input type="number" inputMode="decimal" placeholder="actual"
                    value={actuals[t.target_id] ?? (t.actual_value != null ? String(t.actual_value) : '')}
                    onChange={e => setActuals(a => ({ ...a, [t.target_id]: e.target.value }))}
                    className="w-24 rounded-lg px-2 py-1 text-[12px]" style={input} />
                )}
                <TargetBadge achieved={t.achieved} confirmed={t.confirmed} />
                <button onClick={() => confirmTarget(t, true)}
                  className="text-[11px] px-2 py-1 rounded-lg font-semibold" style={{ background: '#ECFDF5', color: '#15803D' }}>✓ Hit</button>
                <button onClick={() => confirmTarget(t, false)}
                  className="text-[11px] px-2 py-1 rounded-lg font-semibold" style={{ background: '#FEF2F2', color: '#B42318' }}>✗ Missed</button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* feedback form */}
      <div className={`grid transition-all duration-300 ${open ? 'grid-rows-[1fr] opacity-100 mt-4' : 'grid-rows-[0fr] opacity-0'}`}
           style={{ overflow: 'hidden' }}>
        <div className="min-h-0 space-y-3">
          {emp.feedback_given ? (
            <p className="text-sm" style={{ color: theme.t2 }}>Feedback for this month is recorded and locked in the performance trail.</p>
          ) : (
            <>
              <div>
                <label className="text-[11px] font-medium" style={{ color: theme.t2 }}>Overall rating</label>
                <select value={rating} onChange={e => setRating(e.target.value)} className="w-full rounded-lg px-3 py-2 text-sm" style={input}>
                  {RATINGS.map(([c, l]) => <option key={c} value={c}>{l}</option>)}
                </select>
              </div>
              <FbBox label="What they did well" value={well} onChange={setWell} input={input} theme={theme} />
              <FbBox label={`What they did not do well${isLow ? ' (required for a low rating)' : ''}`} value={notwell} onChange={setNotwell} input={input} theme={theme} />
              <FbBox label="Areas to improve / support" value={improve} onChange={setImprove} input={input} theme={theme} />
              <div className="flex items-center justify-between">
                <span className="text-[11px]" style={{ color: theme.t2 }}>Drafted from the facts above — edit freely.</span>
                <button onClick={save} disabled={!canSave || busy}
                  className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold"
                  style={{ background: NAVY, color: '#fff', opacity: !canSave || busy ? 0.5 : 1 }}>
                  {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : null} Save feedback
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </section>
  )
}

function FbBox({ label, value, onChange, input, theme }: {
  label: string; value: string; onChange: (v: string) => void; input: object; theme: any  // eslint-disable-line @typescript-eslint/no-explicit-any
}) {
  return (
    <div>
      <label className="text-[11px] font-medium" style={{ color: theme.t2 }}>{label}</label>
      <textarea value={value} onChange={e => onChange(e.target.value)} rows={2}
        className="w-full rounded-lg px-3 py-2 text-sm" style={input} />
    </div>
  )
}

function TargetBadge({ achieved, confirmed }: { achieved: boolean | null; confirmed: boolean }) {
  if (achieved === true) return <span className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full font-semibold" style={{ background: '#ECFDF5', color: '#15803D' }}><CheckCircle2 className="w-3 h-3" /> {confirmed ? 'Hit' : 'On track'}</span>
  if (achieved === false) return <span className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full font-semibold" style={{ background: '#FEF2F2', color: '#B42318' }}><XCircle className="w-3 h-3" /> Missed</span>
  return <span className="text-[11px] px-2 py-0.5 rounded-full font-semibold" style={{ background: '#F3F4F6', color: '#6B7280' }}>—</span>
}

function Sparkline({ trend }: { trend: TrendPt[] }) {
  const color = (r: string | null) => r === 'EX' || r === 'ME' ? '#15803D' : r === 'PA' ? '#B45309' : (r ? '#B42318' : '#D1D5DB')
  return (
    <div className="flex items-end gap-1" title="Last 3 months">
      {trend.map((t, i) => (
        <div key={i} className="w-2.5 rounded-sm transition-all duration-500"
             style={{ height: t.rating ? 18 : 6, background: color(t.rating) }} title={`${MONTHS[t.month - 1]}: ${t.rating || 'no feedback'}`} />
      ))}
    </div>
  )
}
