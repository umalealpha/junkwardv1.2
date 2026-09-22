'use client'

/**
 * /my-omni — "My Omni", the personal employee home (CFO directive 2026-08-29).
 *
 * The friendly front door every employee lands on after login, instead of the
 * finance/ERP dashboard. It AGGREGATES existing self-service feeds onto one
 * phone-first screen — it does not re-implement any module:
 *   Today's 3 Actions · Time Doctor · My Tasks · My Requests · My Money ·
 *   My Growth · My Wins · What's New · plus My Leave / Explain-my-day ·
 *   Ask Aria (answers from data already on the page) · The Week Ahead ·
 *   Quick Actions · Give a Shout-out + Celebrations · Company Announcements.
 * Managers get a "Me / My Team" toggle (approvals, team work, dialogues) —
 * never staff pay or loans. The full ERP stays one click away via "Explore Omni".
 *
 * Every section fetches independently: one failing feed shows a small friendly
 * note, it never blanks the page. Colours come from the live theme tokens.
 */
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import Link from 'next/link'
import { apiFetch, getMe, getWhatsNew, type UserProfile } from '@/lib/api'
import { authedHrisFetch, fmtPula } from '../hris/_shared'
import { TopBar } from '@/components/layout/TopBar'
import { LateNoticeButton } from '@/components/LateNoticeButton'
import { Card } from '@/components/ui/card'
import { useTheme } from '@/contexts/ThemeContext'
import {
  Sun, Sunrise, Moon, CheckCircle2, Clock, ListTodo, Wallet, Banknote,
  Sparkles, TrendingUp, Award, Megaphone, CalendarDays,
  Rocket, Eye, EyeOff, ArrowRight, AlertTriangle, Loader2, Users, User,
  Plus, X, Heart, Gift, HelpCircle, ChevronRight, CloudSun,
} from 'lucide-react'

/* ─────────────────────────── shapes (partial, defensive) ─────────────────── */

interface HrisAccess { allowed?: boolean; role?: string; capabilities?: string[] }
interface HoursWindows {
  today: number; yesterday: number; this_week: number; last_week: number
  this_month: number; last_month: number; required_today: number; short_today: boolean
  month_required: number; month_pct: number | null
}
interface HoursResp { linked: boolean; employee?: string; windows: Partial<HoursWindows>; trend: { date: string; hours: number }[] }
interface TaskBrief { id?: string | number; title?: string; due_at?: string | null; status?: string; priority?: number }
interface PaymentsWaiting { waiting_cfo_count?: number; waiting_cfo_total?: string }
interface MyLeagueBrief { earns_incentive?: boolean; reward?: { reward_bwp?: number; confirmed_month?: number; points_month?: number } }
interface GrowthSummary { performance_pct?: number; potential_pct?: number; nine_box?: string }
interface ReqItem { kind_label?: string; title?: string; bucket?: string; status_line?: string; href?: string; stuck?: boolean; days_waiting?: number | null }
interface RequestsResp { items?: ReqItem[]; active?: number; stuck?: number; total?: number }
interface ApprovalsResp { streams?: { count?: number; label?: string }[]; total?: number }
interface Payslip { period?: string; period_end?: string; status?: string; net_amount?: number; gross_amount?: number; pdf_url?: string; hold_reason?: string }
interface LeaveBal { code?: string; label?: string; available?: number }
interface LeaveResp { balances?: LeaveBal[]; has_profile?: boolean }
interface Kudo { sender?: string; receiver?: string; message?: string; value_demonstrated?: string; is_public?: boolean; created_at?: string }
interface KudosResp { count?: number; kudos?: Kudo[] }
interface WhatsNewEntry { title?: string; summary?: string; href?: string; how_to?: string }
interface Announcement {
  id: string; title: string; body: string; category: string; category_label: string
  is_active: boolean; created_at: string; starts_on: string | null; ends_on: string | null
}
interface AnnouncementsResp { announcements: Announcement[]; can_make: boolean }
interface WeatherDay { date: string; high: number; low: number; rain_pct: number; wind_kmh: number; label: string; emoji: string; warn: string[] }
interface WeatherResp { ok: boolean; place: string; days: WeatherDay[] }

/* ─────────────────────────── helpers ─────────────────────────────────────── */

const HAND = "'Segoe Script','Bradley Hand','Brush Script MT',cursive"

function greeting(): { word: string; Icon: typeof Sun } {
  // Hours are Botswana local time on the browser. CFO 2026-08-30: the flat
  // "Good morning" from midnight to noon reads as broken to anyone looking
  // just after midnight — a working-late acknowledgement lands better.
  const h = new Date().getHours()
  if (h >= 5  && h < 12) return { word: 'Good morning',   Icon: Sunrise }
  if (h >= 12 && h < 17) return { word: 'Good afternoon', Icon: Sun }
  if (h >= 17 && h < 22) return { word: 'Good evening',   Icon: Moon }
  return { word: 'Working late', Icon: Moon }
}

const VIEW_KEY = 'my_omni_view'   // 'me' | 'team'

function useAsync<T>(fetcher: () => Promise<T>, deps: unknown[] = []): { data: T | null; loading: boolean; error: boolean } {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  useEffect(() => {
    let alive = true
    setLoading(true); setError(false)
    fetcher()
      .then(d => { if (alive) { setData(d); setLoading(false) } })
      .catch(() => { if (alive) { setError(true); setLoading(false) } })
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  return { data, loading, error }
}

async function hrisJson<T>(path: string): Promise<T> {
  const r = await authedHrisFetch(path)
  if (!r.ok) throw new Error(String(r.status))
  return r.json() as Promise<T>
}

/* ─────────────────────────── small UI atoms ──────────────────────────────── */

function Section({ title, icon, href, hrefLabel, children }: {
  title: string; icon: ReactNode; href?: string; hrefLabel?: string; children: ReactNode
}) {
  const { theme } = useTheme()
  return (
    <Card className="p-4 flex flex-col">
      <div className="flex items-center gap-2 mb-3">
        <span className="w-7 h-7 rounded-lg flex items-center justify-center shrink-0"
              style={{ background: theme.oL, color: theme.orangeText }}>{icon}</span>
        <h3 className="text-sm font-bold tracking-tight" style={{ color: theme.text }}>{title}</h3>
        {href && (
          <Link href={href} className="ml-auto text-xs font-medium inline-flex items-center gap-0.5"
                style={{ color: theme.orangeText }}>
            {hrefLabel || 'Open'} <ChevronRight className="w-3 h-3" />
          </Link>
        )}
      </div>
      <div className="flex-1">{children}</div>
    </Card>
  )
}

function Empty({ children }: { children: ReactNode }) {
  const { theme } = useTheme()
  return <p className="text-xs py-3" style={{ color: theme.t3 }}>{children}</p>
}

function Spin() {
  const { theme } = useTheme()
  return <div className="py-4 flex justify-center"><Loader2 className="w-4 h-4 animate-spin" style={{ color: theme.t3 }} /></div>
}

/* ─────────────────────────── page ────────────────────────────────────────── */

export default function MyOmniPage() {
  const { theme } = useTheme()
  // Pay is HIDDEN on every open (CFO 2026-09-01) so it can never pop up when
  // someone is standing behind the screen. Reveal is per-view only — never
  // persisted — so it resets to hidden the next time the page opens.
  const [hide, setHide] = useState(true)
  const [view, setView] = useState<'me' | 'team'>('me')

  useEffect(() => {
    try {
      setView(localStorage.getItem(VIEW_KEY) === 'team' ? 'team' : 'me')
    } catch { /* private mode */ }
  }, [])
  const toggleHide = () => setHide(h => !h)
  const setViewP = (v: 'me' | 'team') => { setView(v); try { localStorage.setItem(VIEW_KEY, v) } catch {} }
  const money = (n: number | null | undefined) => hide ? '••••••' : fmtPula(Number(n || 0))

  /* data */
  const me = useAsync<UserProfile>(() => getMe(), [])
  const access = useAsync<HrisAccess>(() => apiFetch<HrisAccess>('/admin/hris-access/'), [])
  const hours = useAsync<HoursResp>(() => apiFetch<HoursResp>('/timedoctor/my-hours/'), [])
  const weather = useAsync<WeatherResp>(() => apiFetch<WeatherResp>('/my-omni/weather/'), [])
  const tasks = useAsync<TaskBrief[]>(() => apiFetch<TaskBrief[]>('/taskboard/my-tasks/'), [])
  const requests = useAsync<RequestsResp>(() => apiFetch<RequestsResp>('/my-requests/'), [])
  const approvals = useAsync<ApprovalsResp>(() => apiFetch<ApprovalsResp>('/my-approvals/'), [])
  const payslips = useAsync<Payslip[]>(async () => {
    const j = await hrisJson<Payslip[] | { payslips?: Payslip[]; slips?: Payslip[] }>('/hris/api/my-payslips/')
    return Array.isArray(j) ? j : (j.payslips || j.slips || [])
  }, [])
  const leave = useAsync<LeaveResp>(() => hrisJson<LeaveResp>('/hris/api/leave-balances/'), [])
  const kudos = useAsync<KudosResp>(() => hrisJson<KudosResp>('/hris/api/kudos/'), [])
  const dialogue = useAsync<Record<string, unknown>>(() => hrisJson<Record<string, unknown>>('/hris/api/talent/my-dialogue/'), [])
  // Payments waiting on the CFO (CFO 2026-08-31) — the My Tasks box hides payment
  // approvals, so it goes blank; this points to where they went. 403 for non-CFO
  // staff → null, so the line only ever shows for someone who can act on them.
  const paymentsWaiting = useAsync<PaymentsWaiting | null>(
    () => apiFetch<PaymentsWaiting>('/payment-requests/summary/').catch(() => null), [])
  // This month's task incentive earned by the caller (CFO 2026-08-31 "show how
  // much they earned"). Managers/ExCo don't earn it → earns_incentive false.
  const myLeague = useAsync<MyLeagueBrief | null>(
    () => apiFetch<MyLeagueBrief>('/taskboard/my-league/').catch(() => null), [])
  const whatsNew = useAsync<{ entries?: WhatsNewEntry[] }>(() => getWhatsNew() as unknown as Promise<{ entries?: WhatsNewEntry[] }>, [])

  const firstName = (me.data?.first_name || '').trim() || 'there'
  const g = greeting()

  const isManager = useMemo(() => {
    const role = access.data?.role
    const caps = access.data?.capabilities || []
    return role === 'mgr' || role === 'hr' || role === 'admin' || role === 'ceo' || role === 'superadmin'
      || caps.includes('assess_team')
  }, [access.data])

  /* Today's 3 actions — pulled from the feeds already loaded */
  const actions = useMemo(() => {
    const out: { label: string; sub: string; href: string; tone: 'urgent' | 'warn' | 'info' }[] = []
    const now = Date.now()
    const overdue = (tasks.data || []).filter(t => t.due_at && new Date(t.due_at).getTime() < now)
    overdue.slice(0, 2).forEach(t => out.push({
      label: t.title || 'Overdue task', sub: 'Task overdue', href: '/tasks', tone: 'urgent',
    }))
    if ((approvals.data?.total || 0) > 0)
      out.push({ label: `${approvals.data?.total} waiting for your approval`, sub: 'Approvals', href: '/my-approvals', tone: 'warn' })
    if (hours.data?.windows?.short_today)
      out.push({ label: 'Explain today’s hours', sub: 'Time short today', href: '/hris/my-brief', tone: 'warn' })
    const stuck = (requests.data?.items || []).filter(r => r.stuck)
    stuck.slice(0, 1).forEach(r => out.push({
      label: r.title || 'A request is stuck', sub: `Waiting ${r.days_waiting || ''} days`, href: r.href || '/my-requests', tone: 'info',
    }))
    ;(tasks.data || []).filter(t => !(t.due_at && new Date(t.due_at).getTime() < now)).slice(0, 3).forEach(t => {
      if (out.length < 3) out.push({ label: t.title || 'Task', sub: 'Next task', href: '/tasks', tone: 'info' })
    })
    return out.slice(0, 3)
  }, [tasks.data, approvals.data, hours.data, requests.data])

  const toneColor = (t: 'urgent' | 'warn' | 'info') => t === 'urgent' ? theme.er : t === 'warn' ? theme.wr : theme.orangeText

  return (
    <>
      <TopBar title="My Omni" />
      <div className="px-4 sm:px-6 py-5 max-w-6xl mx-auto">

        {/* ── Greeting + controls ─────────────────────────────────────────── */}
        <div className="flex flex-wrap items-end gap-3 mb-5">
          <div className="flex-1 min-w-[220px]">
            <div className="flex items-center gap-2" style={{ color: theme.orangeText }}>
              <g.Icon className="w-5 h-5" />
              <span className="text-xs font-semibold uppercase tracking-wider">{g.word}</span>
            </div>
            <h1 className="mt-1 leading-none" style={{ fontFamily: HAND, fontSize: '2.6rem', color: theme.navy }}>
              Hello, {firstName}
            </h1>
            <p className="mt-1 text-sm" style={{ color: theme.t2 }}>Here is what needs your attention today.</p>
          </div>
          <div className="flex items-center gap-2">
            {isManager && (
              <div className="flex rounded-lg overflow-hidden border" style={{ borderColor: theme.cardBdr }}>
                {(['me', 'team'] as const).map(v => (
                  <button key={v} onClick={() => setViewP(v)}
                    className="px-3 py-1.5 text-xs font-semibold inline-flex items-center gap-1"
                    style={{ background: view === v ? theme.orange : theme.card, color: view === v ? '#fff' : theme.t2 }}>
                    {v === 'me' ? <User className="w-3.5 h-3.5" /> : <Users className="w-3.5 h-3.5" />}
                    {v === 'me' ? 'Me' : 'My Team'}
                  </button>
                ))}
              </div>
            )}
            <button onClick={toggleHide} title="Reveal or hide my pay"
              className="px-3 py-1.5 rounded-lg text-xs font-semibold inline-flex items-center gap-1 border"
              style={{ borderColor: hide ? theme.orange : theme.cardBdr, color: hide ? '#fff' : theme.t2, background: hide ? theme.orange : theme.card }}>
              {hide ? <Eye className="w-3.5 h-3.5" /> : <EyeOff className="w-3.5 h-3.5" />}
              {hide ? 'Reveal my pay' : 'Hide my pay'}
            </button>
            <Link href="/dashboard"
              className="px-3 py-1.5 rounded-lg text-xs font-semibold inline-flex items-center gap-1"
              style={{ background: theme.navy, color: '#fff' }}>
              <Rocket className="w-3.5 h-3.5" /> Explore Omni
            </Link>
          </div>
        </div>

        {/* ── Announcements ───────────────────────────────────────────────── */}
        <Announcements />

        {view === 'team' && isManager
          ? <TeamView approvals={approvals.data} />
          : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">

            {/* Today's 3 Actions */}
            <Section title="Today's 3 Actions" icon={<CheckCircle2 className="w-4 h-4" />}>
              {tasks.loading || approvals.loading ? <Spin/>
                : actions.length === 0 ? <Empty>Nothing urgent — nice work. 🎉</Empty>
                : <ul className="space-y-2">
                    {actions.map((a, i) => (
                      <li key={i}>
                        <Link href={a.href} className="flex items-start gap-2 rounded-lg p-2 -mx-1 hover:opacity-90"
                              style={{ background: theme.g50 }}>
                          <span className="w-1.5 h-1.5 rounded-full mt-1.5 shrink-0" style={{ background: toneColor(a.tone) }} />
                          <span className="min-w-0">
                            <span className="block text-xs font-semibold truncate" style={{ color: theme.text }}>{a.label}</span>
                            <span className="block text-[11px]" style={{ color: theme.t3 }}>{a.sub}</span>
                          </span>
                          <ArrowRight className="w-3.5 h-3.5 ml-auto shrink-0" style={{ color: theme.t3 }} />
                        </Link>
                      </li>
                    ))}
                  </ul>}
            </Section>

            {/* Time Doctor */}
            <Section title="Time Doctor" icon={<Clock className="w-4 h-4" />} href="/hris/my-brief" hrefLabel="Details">
              {hours.loading ? <Spin/>
                : hours.error || !hours.data?.linked ? <Empty>No Time Doctor account linked to your login.</Empty>
                : <HoursTile h={hours.data} />}
              {/* Rule 1b — tell us on the morning itself, before 09:00. The
                  component hides itself once the window shuts. */}
              <LateNoticeButton />
            </Section>

            {/* Weather — 5-day Gaborone forecast */}
            <Section title="Weather — Gaborone" icon={<CloudSun className="w-4 h-4" />}>
              {weather.loading ? <Spin/>
                : !weather.data?.ok || !(weather.data?.days?.length) ? <Empty>Forecast unavailable right now.</Empty>
                : <WeatherTile days={weather.data.days} />}
            </Section>

            {/* My Tasks */}
            <Section title="My Tasks" icon={<ListTodo className="w-4 h-4" />} href="/tasks">
              {tasks.loading ? <Spin/>
                : <TasksTile tasks={tasks.data || []} payments={paymentsWaiting.data} />}
            </Section>

            {/* My Requests */}
            <Section title="My Requests" icon={<Banknote className="w-4 h-4" />} href="/my-requests">
              {requests.loading ? <Spin/>
                : (requests.data?.total || 0) === 0 ? <Empty>You have no requests in flight.</Empty>
                : <RequestsTile r={requests.data!} />}
            </Section>

            {/* My Leave & Explain-my-day  (extra 1) */}
            <Section title="My Leave" icon={<CalendarDays className="w-4 h-4" />} href="/hris/leave">
              {leave.loading ? <Spin/>
                : <LeaveTile leave={leave.data} shortToday={!!hours.data?.windows?.short_today} />}
            </Section>

            {/* My Growth */}
            <Section title="My Growth" icon={<TrendingUp className="w-4 h-4" />} href="/hris/my-dialogue">
              {dialogue.loading ? <Spin/>
                : <GrowthTile d={dialogue.data} />}
            </Section>

            {/* My Wins */}
            <Section title="My Wins" icon={<Award className="w-4 h-4" />} href="/hris/rewards">
              {kudos.loading ? <Spin/>
                : <WinsTile kudos={kudos.data} meName={`${me.data?.first_name || ''} ${me.data?.last_name || ''}`.trim()} />}
            </Section>

            {/* Ask Aria (extra 2) */}
            <AskAria hours={hours.data} leave={leave.data} approvals={approvals.data} payslips={payslips.data || []} />

            {/* The Week Ahead (extra 3) */}
            <WeekAhead tasks={tasks.data || []} payslips={payslips.data || []} />

            {/* Quick Actions (extra 4) */}
            <QuickActions />

            {/* Give a Shout-out + Celebrations (extra 5) */}
            <ShoutOut onSent={() => { /* feed refreshes on next page load */ }} />

            {/* What's New */}
            <Section title="What's New in Omni" icon={<Sparkles className="w-4 h-4" />} href="/help" hrefLabel="More">
              {whatsNew.loading ? <Spin/>
                : <WhatsNewTile entries={whatsNew.data?.entries || []} />}
            </Section>

            {/* My Money — moved to the very bottom (CFO 2026-09-01) so pay no
                longer sits near the top; figures stay hidden until "Reveal my pay". */}
            <Section title="My Money" icon={<Wallet className="w-4 h-4" />} href="/hris/payslips" hrefLabel="Payslips">
              {payslips.loading ? <Spin/>
                : <MoneyTile payslips={payslips.data || []} money={money} hide={hide} league={myLeague.data} />}
            </Section>

          </div>
        )}
      </div>
    </>
  )
}

/* ─────────────────────────── tiles ───────────────────────────────────────── */

function WeatherTile({ days }: { days: WeatherDay[] }) {
  const { theme } = useTheme()
  const dayName = (iso: string) => {
    const d = new Date(iso + 'T00:00:00')
    return isNaN(d.getTime()) ? iso : d.toLocaleDateString('en-BW', { weekday: 'short' })
  }
  const heads = days.filter(d => d.warn && d.warn.length).slice(0, 2)
    .map(d => `${dayName(d.date)}: ${d.warn.join(' & ')}`)
  return (
    <div>
      <div className="grid grid-cols-5 gap-1.5">
        {days.map((d, i) => (
          <div key={i} className="rounded-lg p-2 text-center" style={{ background: theme.g50 }}
               title={`${d.label} · rain ${d.rain_pct}% · wind ${d.wind_kmh} km/h`}>
            <div className="text-[10px] font-semibold" style={{ color: theme.t3 }}>{i === 0 ? 'Today' : dayName(d.date)}</div>
            <div className="text-lg leading-tight my-0.5">{d.emoji}</div>
            <div className="text-xs font-bold font-mono-nums" style={{ color: theme.navy }}>{d.high}&deg;</div>
            <div className="text-[10px] font-mono-nums" style={{ color: theme.t3 }}>{d.low}&deg;</div>
            {d.warn && d.warn.length > 0 && (
              <div className="text-[9px] font-semibold mt-0.5" style={{ color: theme.orange }}>
                {d.warn.includes('rain') ? '🌧' : ''}{d.warn.includes('windy') ? '💨' : ''}
              </div>
            )}
          </div>
        ))}
      </div>
      {heads.length > 0 && (
        <div className="mt-2 flex items-center gap-1 rounded-lg py-1.5 px-2 text-[11px] font-medium"
             style={{ background: theme.wrB, color: theme.wr }}>
          <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0" /> Heads-up — {heads.join(', ')}
        </div>
      )}
      {/* Full forecast on Google (CFO master doc, 17-Sep-2026) — the tile is a
          5-day summary; this opens Google's live Gaborone weather in a new tab. */}
      <a href="https://www.google.com/search?q=weather+Gaborone"
         target="_blank" rel="noopener noreferrer"
         className="mt-2 inline-flex items-center gap-1 text-[11px] font-medium hover:underline"
         style={{ color: theme.orange }}>
        <CloudSun className="w-3 h-3" /> See the full forecast on Google
      </a>
    </div>
  )
}

function HoursTile({ h }: { h: HoursResp }) {
  const { theme } = useTheme()
  const w = h.windows || {}
  const cells: [string, number | undefined][] = [
    ['Today', w.today], ['Yesterday', w.yesterday], ['This week', w.this_week],
    ['Last week', w.last_week], ['This month', w.this_month], ['Last month', w.last_month],
  ]
  const max = Math.max(1, ...(h.trend || []).map(t => t.hours))
  return (
    <div>
      <div className="grid grid-cols-3 gap-2">
        {cells.map(([label, v]) => (
          <div key={label} className="rounded-lg p-2 text-center" style={{ background: theme.g50 }}>
            <div className="text-base font-bold font-mono-nums" style={{ color: theme.navy }}>{(v ?? 0).toFixed(1)}</div>
            <div className="text-[10px]" style={{ color: theme.t3 }}>{label}{label === 'Today' ? '*' : ''}</div>
          </div>
        ))}
      </div>
      <div className="mt-1.5 text-[10px] leading-snug" style={{ color: theme.t3 }}>
        *Today&rsquo;s hours sync from Time Doctor and can rise later in the day &mdash; a low figure may just mean today&rsquo;s tracking hasn&rsquo;t come through yet.
      </div>
      {typeof w.month_required === 'number' && w.month_required > 0 && (
        <div className="mt-3 rounded-lg p-2.5" style={{ background: theme.g50 }}>
          <div className="flex items-center justify-between text-[11px] mb-1.5">
            <span style={{ color: theme.t3 }}>This month &mdash; expected vs achieved</span>
            <span className="font-mono-nums font-semibold" style={{ color: (w.month_pct ?? 0) >= 100 ? theme.ok : theme.orange }}>
              {(w.this_month ?? 0).toFixed(1)} / {(w.month_required ?? 0).toFixed(1)}h{w.month_pct != null ? ` · ${w.month_pct}%` : ''}
            </span>
          </div>
          <div className="h-2 rounded-full overflow-hidden" style={{ background: 'rgba(0,0,0,0.08)' }}>
            <div className="h-full rounded-full" style={{ width: `${Math.min(100, Math.max(0, w.month_pct ?? 0))}%`, background: (w.month_pct ?? 0) >= 100 ? theme.ok : theme.orange }} />
          </div>
        </div>
      )}
      {(h.trend || []).length > 0 && (
        <div className="flex items-end gap-0.5 h-10 mt-3">
          {h.trend.map((t, i) => (
            <div key={i} className="flex-1 rounded-t" title={`${t.date}: ${t.hours}h`}
              style={{ height: `${Math.max(4, (t.hours / max) * 100)}%`, background: t.hours >= (w.required_today || 6.5) ? theme.ok : theme.orange, opacity: 0.85 }} />
          ))}
        </div>
      )}
      {w.short_today && (
        <Link href="/hris/my-brief" className="mt-3 flex items-center justify-center gap-1 rounded-lg py-1.5 text-xs font-semibold"
          style={{ background: theme.wrB, color: theme.wr }}>
          <AlertTriangle className="w-3.5 h-3.5" /> Explain today&rsquo;s hours
        </Link>
      )}
    </div>
  )
}

function TasksTile({ tasks, payments }: { tasks: TaskBrief[]; payments?: PaymentsWaiting | null }) {
  const { theme } = useTheme()
  const now = Date.now()
  const overdue = tasks.filter(t => t.due_at && new Date(t.due_at).getTime() < now).length
  const open = tasks.length
  // Payment approvals are deliberately kept OUT of tasks (CFO 2026-08-29). Point
  // to where they live so the box isn't a confusing blank when they're all that's
  // waiting. Only ever populated for someone who can act on them (403→null else).
  const payCount = payments?.waiting_cfo_count || 0
  const paymentsLine = payCount > 0 ? (
    <Link href="/payment-requests"
          className="flex items-center justify-between rounded-lg px-2.5 py-2 mt-2 text-xs font-semibold"
          style={{ background: theme.oL, color: theme.orangeText }}>
      <span>{payCount} payment approval{payCount === 1 ? '' : 's'} waiting{payments?.waiting_cfo_total ? ` · ${payments.waiting_cfo_total}` : ''}</span>
      <span aria-hidden>→</span>
    </Link>
  ) : null
  if (open === 0) return (
    <div>
      <Empty>No open tasks. Inbox zero. ✅</Empty>
      {paymentsLine}
    </div>
  )
  return (
    <div>
      <div className="grid grid-cols-2 gap-2 mb-2">
        <div className="rounded-lg p-2 text-center" style={{ background: theme.g50 }}>
          <div className="text-lg font-bold" style={{ color: theme.navy }}>{open}</div>
          <div className="text-[10px]" style={{ color: theme.t3 }}>Open</div>
        </div>
        <div className="rounded-lg p-2 text-center" style={{ background: overdue ? theme.erB : theme.g50 }}>
          <div className="text-lg font-bold" style={{ color: overdue ? theme.er : theme.navy }}>{overdue}</div>
          <div className="text-[10px]" style={{ color: theme.t3 }}>Overdue</div>
        </div>
      </div>
      <ul className="space-y-1">
        {tasks.slice(0, 3).map((t, i) => (
          <li key={i} className="text-xs truncate flex items-center gap-1.5" style={{ color: theme.t2 }}>
            <span className="w-1 h-1 rounded-full shrink-0" style={{ background: theme.orange }} />
            {t.title || 'Task'}
          </li>
        ))}
      </ul>
      {paymentsLine}
    </div>
  )
}

function RequestsTile({ r }: { r: RequestsResp }) {
  const { theme } = useTheme()
  return (
    <div>
      <div className="flex gap-2 mb-2">
        <span className="text-xs px-2 py-1 rounded-full font-medium" style={{ background: theme.oL, color: theme.orangeText }}>{r.active || 0} in progress</span>
        {(r.stuck || 0) > 0 && <span className="text-xs px-2 py-1 rounded-full font-medium" style={{ background: theme.erB, color: theme.er }}>{r.stuck} stuck</span>}
      </div>
      <ul className="space-y-1.5">
        {(r.items || []).slice(0, 3).map((it, i) => (
          <li key={i}>
            <Link href={it.href || '/my-requests'} className="block">
              <span className="text-xs font-medium truncate block" style={{ color: theme.text }}>{it.title || it.kind_label}</span>
              <span className="text-[11px]" style={{ color: theme.t3 }}>{it.status_line || it.bucket}</span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  )
}

function MoneyTile({ payslips, money, hide, league }: { payslips: Payslip[]; money: (n?: number | null) => string; hide: boolean; league?: MyLeagueBrief | null }) {
  const { theme } = useTheme()
  const latest = payslips[0]
  // How much they've earned from the task incentive this month (CFO 2026-08-31).
  // Only shown to those who actually earn it — managers/ExCo have earns_incentive
  // false and see nothing here.
  const earns = !!league?.earns_incentive
  const reward = league?.reward?.reward_bwp || 0
  const confirmed = league?.reward?.confirmed_month || 0
  return (
    <div>
      <div className="rounded-lg p-3 mb-2" style={{ background: theme.g50 }}>
        <div className="text-[10px] uppercase tracking-wider" style={{ color: theme.t3 }}>Latest net pay {latest?.period ? `· ${latest.period}` : ''}</div>
        <div className="text-xl font-bold font-mono-nums" style={{ color: theme.navy }}>{money(latest?.net_amount)}</div>
        {latest?.hold_reason && <div className="text-[11px] mt-0.5" style={{ color: theme.wr }}>On hold: {latest.hold_reason}</div>}
      </div>
      {earns && (
        <div className="rounded-lg p-3 mb-2" style={{ background: theme.oL }}>
          <div className="text-[10px] uppercase tracking-wider" style={{ color: theme.t3 }}>Task incentive · this month</div>
          <div className="text-lg font-bold font-mono-nums" style={{ color: theme.orangeText }}>{money(reward)}</div>
          <div className="text-[10px] mt-0.5" style={{ color: theme.t3 }}>{confirmed} task{confirmed === 1 ? '' : 's'} confirmed · pending CFO + HR sign-off</div>
        </div>
      )}
      <div className="text-xs mb-2" style={{ color: theme.t3 }}>Staff loan: <Link href="/hris/staff-loans" style={{ color: theme.orangeText }}>view balance</Link></div>
      {payslips.slice(0, 2).map((p, i) => (
        <div key={i} className="flex items-center justify-between text-xs py-1 border-t" style={{ borderColor: theme.cardBdr }}>
          <span style={{ color: theme.t2 }}>{p.period || 'Payslip'}</span>
          {p.pdf_url
            ? <a href={p.pdf_url} target="_blank" rel="noreferrer" style={{ color: theme.orangeText }}>Download</a>
            : <span style={{ color: theme.t3 }}>{p.status || '—'}</span>}
        </div>
      ))}
      {hide && <div className="text-[10px] mt-1" style={{ color: theme.t3 }}>Amounts hidden on this device.</div>}
    </div>
  )
}

function LeaveTile({ leave, shortToday }: { leave: LeaveResp | null; shortToday: boolean }) {
  const { theme } = useTheme()
  const annual = (leave?.balances || []).find(b => (b.code || b.label || '').toLowerCase().includes('annual')) || (leave?.balances || [])[0]
  return (
    <div>
      <div className="rounded-lg p-3 mb-2 text-center" style={{ background: theme.g50 }}>
        <div className="text-2xl font-bold" style={{ color: theme.navy }}>{annual ? Number(annual.available || 0).toFixed(1) : '—'}</div>
        <div className="text-[10px]" style={{ color: theme.t3 }}>days available{annual?.label ? ` · ${annual.label}` : ''}</div>
      </div>
      {shortToday
        ? <Link href="/hris/my-brief" className="flex items-center justify-center gap-1 rounded-lg py-1.5 text-xs font-semibold" style={{ background: theme.wrB, color: theme.wr }}>
            <AlertTriangle className="w-3.5 h-3.5" /> Explain my day
          </Link>
        : <Link href="/hris/leave" className="flex items-center justify-center gap-1 rounded-lg py-1.5 text-xs font-semibold" style={{ background: theme.oL, color: theme.orangeText }}>
            <Plus className="w-3.5 h-3.5" /> Apply for leave
          </Link>}
    </div>
  )
}

function GrowthTile({ d }: { d: Record<string, unknown> | null }) {
  const { theme } = useTheme()
  const nextAction = (d?.next_action || d?.next_dev_action || d?.next_step) as string | undefined
  const status = (d?.status || d?.stage) as string | undefined
  // Your latest talent-review summary (CFO 2026-08-31): the 9-box position + the
  // performance/potential scores. Falls back to the dialogue status when there is
  // no review on file yet.
  const growth = (d?.growth || null) as GrowthSummary | null
  return (
    <div className="space-y-2">
      {growth?.nine_box ? (
        <div className="rounded-lg p-2" style={{ background: theme.g50 }}>
          <div className="text-[10px] uppercase tracking-wider" style={{ color: theme.t3 }}>Your latest talent review</div>
          <div className="text-sm font-semibold" style={{ color: theme.navy }}>{growth.nine_box}</div>
          <div className="text-[11px] mt-0.5" style={{ color: theme.t2 }}>
            Performance {growth.performance_pct ?? '—'}% · Potential {growth.potential_pct ?? '—'}%
          </div>
        </div>
      ) : (
        <div className="rounded-lg p-2" style={{ background: theme.g50 }}>
          <div className="text-[10px] uppercase tracking-wider" style={{ color: theme.t3 }}>Development dialogue</div>
          <div className="text-xs font-medium" style={{ color: theme.text }}>{status || 'Open your latest dialogue'}</div>
        </div>
      )}
      {nextAction && (
        <div className="text-xs" style={{ color: theme.t2 }}><span style={{ color: theme.orangeText }}>Next:</span> {nextAction}</div>
      )}
      <div className="flex gap-2 text-[11px]">
        <Link href="/hris/monthly-feedback" style={{ color: theme.orangeText }}>Feedback</Link>
        <Link href="/hris/my-dialogue" style={{ color: theme.orangeText }}>Goals</Link>
      </div>
    </div>
  )
}

function WinsTile({ kudos, meName }: { kudos: KudosResp | null; meName: string }) {
  const { theme } = useTheme()
  const mine = (kudos?.kudos || []).filter(k => !meName || (k.receiver || '').toLowerCase() === meName.toLowerCase())
  const show = (mine.length ? mine : (kudos?.kudos || [])).slice(0, 3)
  if (show.length === 0) return <Empty>No shout-outs yet. Give one below and start the ripple. 💛</Empty>
  return (
    <ul className="space-y-2">
      {show.map((k, i) => (
        <li key={i} className="flex items-start gap-2">
          <Heart className="w-3.5 h-3.5 mt-0.5 shrink-0" style={{ color: theme.orange }} />
          <span className="min-w-0">
            <span className="block text-xs" style={{ color: theme.text }}>{k.message}</span>
            <span className="block text-[10px]" style={{ color: theme.t3 }}>from {k.sender}{k.value_demonstrated ? ` · ${k.value_demonstrated}` : ''}</span>
          </span>
        </li>
      ))}
    </ul>
  )
}

function WhatsNewTile({ entries }: { entries: WhatsNewEntry[] }) {
  const { theme } = useTheme()
  if (entries.length === 0)
    return (
      <div>
        <div className="text-xs font-semibold mb-1" style={{ color: theme.text }}>Discover Omni</div>
        <p className="text-xs" style={{ color: theme.t2 }}>Tip: your payslip, leave and requests all live under the <Link href="/hris/leave" style={{ color: theme.orangeText }}>My HR</Link> menu — nothing to chase by email.</p>
      </div>
    )
  const e = entries[0]
  return (
    <div>
      <div className="text-xs font-semibold mb-1" style={{ color: theme.text }}>{e.title}</div>
      <p className="text-xs mb-2" style={{ color: theme.t2 }}>{e.summary}</p>
      {e.href && <Link href={e.href} className="text-xs font-semibold" style={{ color: theme.orangeText }}>Try it →</Link>}
    </div>
  )
}

/* ─────────────────────────── extra features ──────────────────────────────── */

function AskAria({ hours, leave, approvals, payslips }: {
  hours: HoursResp | null; leave: LeaveResp | null; approvals: ApprovalsResp | null; payslips: Payslip[]
}) {
  const { theme } = useTheme()
  const [q, setQ] = useState('')
  const [a, setA] = useState<string | null>(null)

  const answer = useCallback((query: string) => {
    const s = query.toLowerCase()
    const annual = (leave?.balances || []).find(b => (b.code || b.label || '').toLowerCase().includes('annual')) || (leave?.balances || [])[0]
    if (s.includes('leave') || s.includes('holiday') || s.includes('day off'))
      return annual ? `You have ${Number(annual.available || 0).toFixed(1)} days of ${annual.label || 'annual'} leave available. Apply under My Leave.` : 'I could not read your leave balance — open My Leave to check.'
    if (s.includes('pay') || s.includes('salary') || s.includes('payslip') || s.includes('net'))
      return payslips[0]?.period ? `Your latest payslip is for ${payslips[0].period}. Open My Money to download it.` : 'Your payslips live under My Money.'
    if (s.includes('hour') || s.includes('time') || s.includes('track'))
      return hours?.linked ? `Today you have ${(hours.windows.today ?? 0).toFixed(1)}h tracked (needed ${(hours.windows.required_today ?? 0).toFixed(1)}h). This week: ${(hours.windows.this_week ?? 0).toFixed(1)}h.` : 'No Time Doctor account is linked to your login.'
    if (s.includes('approv') || s.includes('sign'))
      return (approvals?.total || 0) > 0 ? `You have ${approvals?.total} item(s) waiting for your approval. Open My Approvals.` : 'Nothing is waiting for your approval right now.'
    if (s.includes('who approves') || s.includes('manager') || s.includes('approver'))
      return 'Your leave and requests route to your line manager, then Finance. Open My Requests to see whose desk each one is on.'
    return null
  }, [hours, leave, approvals, payslips])

  const ask = (query: string) => { setQ(query); setA(answer(query) ?? '__miss__') }

  return (
    <Section title="Ask Aria" icon={<HelpCircle className="w-4 h-4" />}>
      <div className="flex gap-1 mb-2">
        <input value={q} onChange={e => setQ(e.target.value)} onKeyDown={e => e.key === 'Enter' && ask(q)} aria-label="Ask Aria a question"
          placeholder="How much leave do I have?"
          className="flex-1 text-xs rounded-lg px-2 py-1.5 border outline-none"
          style={{ borderColor: theme.cardBdr, background: theme.input, color: theme.text }} />
        <button onClick={() => ask(q)} className="px-2.5 rounded-lg text-xs font-semibold" style={{ background: theme.orange, color: '#fff' }}>Ask</button>
      </div>
      <div className="flex flex-wrap gap-1 mb-2">
        {['My leave', 'My hours today', 'Latest payslip', 'What needs my approval'].map(chip => (
          <button key={chip} onClick={() => ask(chip)} className="text-[11px] px-2 py-0.5 rounded-full" style={{ background: theme.g100, color: theme.t2 }}>{chip}</button>
        ))}
      </div>
      {a === '__miss__'
        ? <p className="text-xs" style={{ color: theme.t2 }}>I can answer leave, pay, hours and approvals from your own record. For anything else, use the Aria assistant (bottom-right).</p>
        : a && <p className="text-xs rounded-lg p-2" style={{ background: theme.tealL, color: theme.text }}>{a}</p>}
    </Section>
  )
}

function WeekAhead({ tasks, payslips }: { tasks: TaskBrief[]; payslips: Payslip[] }) {
  const { theme } = useTheme()
  const items: { label: string; when: string }[] = []
  const soon = tasks.filter(t => t.due_at).sort((x, y) => new Date(x.due_at!).getTime() - new Date(y.due_at!).getTime()).slice(0, 3)
  soon.forEach(t => items.push({ label: t.title || 'Task', when: new Date(t.due_at!).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' }) }))
  // Payday: end of the latest payslip's period, if in the future; else "end of month".
  const now = new Date()
  const eom = new Date(now.getFullYear(), now.getMonth() + 1, 0)
  items.push({ label: 'Payday', when: eom.toLocaleDateString(undefined, { day: 'numeric', month: 'short' }) })
  return (
    <Section title="The Week Ahead" icon={<CalendarDays className="w-4 h-4" />}>
      <ul className="space-y-1.5">
        {items.slice(0, 5).map((it, i) => (
          <li key={i} className="flex items-center justify-between text-xs">
            <span className="truncate" style={{ color: theme.text }}>{it.label}</span>
            <span className="shrink-0 ml-2 font-medium" style={{ color: theme.orangeText }}>{it.when}</span>
          </li>
        ))}
      </ul>
    </Section>
  )
}

function QuickActions() {
  const { theme } = useTheme()
  const items: { label: string; href: string; icon: ReactNode; external?: boolean }[] = [
    { label: 'Apply for leave', href: '/hris/leave', icon: <CalendarDays className="w-4 h-4" /> },
    { label: 'Raise a request', href: '/spend-requests', icon: <Banknote className="w-4 h-4" /> },
    { label: 'Explain my hours', href: '/hris/my-brief', icon: <Clock className="w-4 h-4" /> },
    { label: 'My payslip', href: '/hris/payslips', icon: <Wallet className="w-4 h-4" /> },
    { label: 'IT help desk', href: '/helpdesk/', icon: <HelpCircle className="w-4 h-4" />, external: true },
    { label: 'Request a letter', href: '/hris/letters', icon: <Sparkles className="w-4 h-4" /> },
  ]
  return (
    <Section title="Quick Actions" icon={<Rocket className="w-4 h-4" />}>
      <div className="grid grid-cols-2 gap-2">
        {items.map(it => {
          const inner = (
            <span className="flex items-center gap-2 rounded-lg p-2 text-xs font-medium h-full" style={{ background: theme.g50, color: theme.text }}>
              <span style={{ color: theme.orangeText }}>{it.icon}</span>{it.label}
            </span>
          )
          return it.external
            ? <a key={it.label} href={it.href} className="block">{inner}</a>
            : <Link key={it.label} href={it.href} className="block">{inner}</Link>
        })}
      </div>
    </Section>
  )
}

const KUDOS_VALUES: { v: string; label: string }[] = [
  { v: 'integrity', label: 'Integrity' },
  { v: 'excellence', label: 'Excellence' },
  { v: 'ownership', label: 'Ownership' },
  { v: 'teamwork', label: 'Teamwork' },
  { v: 'innovation', label: 'Innovation' },
  { v: 'customer', label: 'Customer Focus' },
]

function ShoutOut({ onSent }: { onSent: () => void }) {
  const { theme } = useTheme()
  const [to, setTo] = useState('')
  const [value, setValue] = useState('teamwork')
  const [msg, setMsg] = useState('')
  const [state, setState] = useState<'idle' | 'sending' | 'done' | 'err'>('idle')
  const [errMsg, setErrMsg] = useState('')

  const send = async () => {
    if (!to.trim() || !msg.trim()) return
    setState('sending')
    try {
      const r = await authedHrisFetch('/hris/api/kudos/', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ receiver: to.trim(), value_demonstrated: value, message: msg.trim() }),
      })
      if (!r.ok) {
        let detail = ''
        try { detail = (await r.json())?.detail || '' } catch {}
        setErrMsg(detail || 'Could not send — check the name and try again.')
        throw new Error(String(r.status))
      }
      setState('done'); setTo(''); setMsg(''); onSent()
    } catch { setState('err') }
  }

  return (
    <Section title="Give a Shout-out" icon={<Gift className="w-4 h-4" />} href="/hris/rewards" hrefLabel="Feed">
      {state === 'done'
        ? <div className="text-xs rounded-lg p-2" style={{ background: theme.okB, color: theme.ok }}>Sent! Kindness logged. 💛</div>
        : (
        <div className="space-y-1.5">
          <input value={to} onChange={e => setTo(e.target.value)} aria-label="Colleague's full name" placeholder="Colleague's full name"
            className="w-full text-xs rounded-lg px-2 py-1.5 border outline-none" style={{ borderColor: theme.cardBdr, background: theme.input, color: theme.text }} />
          <select value={value} onChange={e => setValue(e.target.value)} aria-label="Value demonstrated"
            className="w-full text-xs rounded-lg px-2 py-1.5 border outline-none" style={{ borderColor: theme.cardBdr, background: theme.input, color: theme.text }}>
            {KUDOS_VALUES.map(v => <option key={v.v} value={v.v}>{v.label}</option>)}
          </select>
          <textarea value={msg} onChange={e => setMsg(e.target.value)} aria-label="What did they do well" placeholder="What did they do well?" rows={2}
            className="w-full text-xs rounded-lg px-2 py-1.5 border outline-none resize-none" style={{ borderColor: theme.cardBdr, background: theme.input, color: theme.text }} />
          <button onClick={send} disabled={state === 'sending'} className="w-full py-1.5 rounded-lg text-xs font-semibold" style={{ background: theme.orange, color: '#fff' }}>
            {state === 'sending' ? 'Sending…' : 'Send shout-out'}
          </button>
          {state === 'err' && <p className="text-[11px]" style={{ color: theme.er }}>{errMsg || 'Could not send — check the name and try again.'}</p>}
        </div>
      )}
    </Section>
  )
}

/* ─────────────────────────── announcements ───────────────────────────────── */

function Announcements() {
  const { theme } = useTheme()
  const [data, setData] = useState<AnnouncementsResp | null>(null)
  const [compose, setCompose] = useState(false)

  const load = useCallback(() => {
    apiFetch<AnnouncementsResp>('/announcements/').then(setData).catch(() => setData({ announcements: [], can_make: false }))
  }, [])
  useEffect(() => { load() }, [load])

  const catColor = (c: string) => c === 'hr_matter' ? { bg: theme.oL, fg: theme.orangeText }
    : c === 'meeting' ? { bg: theme.inB, fg: theme.inf }
    : { bg: theme.tealL, fg: theme.teal }   // announcement

  const live = data?.announcements || []
  if (live.length === 0 && !data?.can_make) return null

  return (
    <div className="mb-4">
      <div className="flex items-center gap-2 mb-2">
        <Megaphone className="w-4 h-4" style={{ color: theme.orangeText }} />
        <span className="text-xs font-bold uppercase tracking-wider" style={{ color: theme.t2 }}>Announcements</span>
        {data?.can_make && (
          <button onClick={() => setCompose(true)} className="ml-auto text-xs font-semibold inline-flex items-center gap-1" style={{ color: theme.orangeText }}>
            <Plus className="w-3.5 h-3.5" /> Post
          </button>
        )}
      </div>
      {live.length === 0
        ? <p className="text-xs" style={{ color: theme.t3 }}>No announcements right now.</p>
        : (
        <div className="space-y-2">
          {live.map(a => {
            const cs = catColor(a.category)
            return (
              <Card key={a.id} className="p-3 flex items-start gap-3">
                <span className="text-[10px] font-bold uppercase px-2 py-0.5 rounded-full shrink-0" style={{ background: cs.bg, color: cs.fg }}>{a.category_label}</span>
                <div className="min-w-0 flex-1">
                  <h4 className="text-sm font-semibold" style={{ color: theme.text }}>{a.title}</h4>
                  {a.body && <p className="text-xs mt-0.5 whitespace-pre-wrap" style={{ color: theme.t2 }}>{a.body}</p>}
                  <p className="text-[10px] mt-1" style={{ color: theme.t3 }}>{new Date(a.starts_on || a.created_at).toLocaleDateString()}</p>
                </div>
              </Card>
            )
          })}
        </div>
      )}
      {compose && <ComposeAnnouncement onClose={() => setCompose(false)} onDone={() => { setCompose(false); load() }} />}
    </div>
  )
}

const ANN_CATS: { v: string; label: string }[] = [
  { v: 'announcement', label: 'Company announcement' },
  { v: 'hr_matter', label: 'HR matter' },
  { v: 'meeting', label: 'Meeting / schedule' },
]

function ComposeAnnouncement({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const { theme } = useTheme()
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [category, setCategory] = useState('announcement')
  const [endsOn, setEndsOn] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const submit = async () => {
    if (!title.trim()) { setErr('A title is required.'); return }
    setBusy(true); setErr(null)
    try {
      await apiFetch('/announcements/', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title, body, category, ends_on: endsOn || undefined }),
      })
      onDone()
    } catch { setErr('Could not post — please try again.'); setBusy(false) }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.4)' }} onClick={onClose}>
      <div className="w-full max-w-md rounded-xl p-4" style={{ background: theme.card }} onClick={e => e.stopPropagation()}>
        <div className="flex items-center mb-3">
          <h3 className="text-sm font-bold" style={{ color: theme.text }}>New announcement</h3>
          <button onClick={onClose} aria-label="Close" className="ml-auto" style={{ color: theme.t3 }}><X className="w-4 h-4" /></button>
        </div>
        <div className="space-y-2">
          <input value={title} onChange={e => setTitle(e.target.value)} aria-label="Announcement title" placeholder="Title" maxLength={140}
            className="w-full text-sm rounded-lg px-2.5 py-2 border outline-none" style={{ borderColor: theme.cardBdr, background: theme.input, color: theme.text }} />
          <textarea value={body} onChange={e => setBody(e.target.value)} aria-label="Announcement message" placeholder="Message" rows={4}
            className="w-full text-sm rounded-lg px-2.5 py-2 border outline-none resize-none" style={{ borderColor: theme.cardBdr, background: theme.input, color: theme.text }} />
          <div className="flex gap-2">
            <select value={category} onChange={e => setCategory(e.target.value)} aria-label="Category"
              className="flex-1 text-sm rounded-lg px-2 py-2 border outline-none" style={{ borderColor: theme.cardBdr, background: theme.input, color: theme.text }}>
              {ANN_CATS.map(c => <option key={c.v} value={c.v}>{c.label}</option>)}
            </select>
            <input type="date" value={endsOn} onChange={e => setEndsOn(e.target.value)} title="Show until (optional)"
              className="text-sm rounded-lg px-2 py-2 border outline-none" style={{ borderColor: theme.cardBdr, background: theme.input, color: theme.text }} />
          </div>
          <p className="text-[11px]" style={{ color: theme.t3 }}>Goes to everyone. Leave the date blank to show until you remove it.</p>
          {err && <p className="text-xs" style={{ color: theme.er }}>{err}</p>}
          <button onClick={submit} disabled={busy} className="w-full py-2 rounded-lg text-sm font-semibold" style={{ background: theme.orange, color: '#fff' }}>
            {busy ? 'Posting…' : 'Post announcement'}
          </button>
        </div>
      </div>
    </div>
  )
}

/* ─────────────────────────── team view (managers) ────────────────────────── */

function TeamView({ approvals }: { approvals: ApprovalsResp | null | undefined }) {
  const { theme } = useTheme()
  const links = [
    { label: 'Team Dialogues', href: '/hris/team-dialogues', icon: <TrendingUp className="w-4 h-4" />, sub: 'Development follow-ups' },
    { label: 'Team Tasks', href: '/task-dashboard', icon: <ListTodo className="w-4 h-4" />, sub: 'Standings & overdue work' },
    { label: 'Manager Scorecard', href: '/hris/manager-scorecard', icon: <Award className="w-4 h-4" />, sub: 'Your team at a glance' },
    { label: 'Team Leave', href: '/hris/leave', icon: <CalendarDays className="w-4 h-4" />, sub: 'Approvals & balances' },
  ]
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
      <Section title="Waiting for you" icon={<CheckCircle2 className="w-4 h-4" />} href="/my-approvals" hrefLabel="Approve">
        {(approvals?.total || 0) === 0
          ? <Empty>Nothing waiting for your approval. ✅</Empty>
          : <div>
              <div className="text-3xl font-bold mb-1" style={{ color: theme.navy }}>{approvals?.total}</div>
              <ul className="space-y-1">
                {(approvals?.streams || []).filter(s => (s.count || 0) > 0).slice(0, 5).map((s, i) => (
                  <li key={i} className="text-xs flex justify-between" style={{ color: theme.t2 }}>
                    <span>{s.label || 'Items'}</span><span className="font-semibold">{s.count}</span>
                  </li>
                ))}
              </ul>
            </div>}
      </Section>
      {links.map(l => (
        <Link key={l.label} href={l.href}>
          <Card className="p-4 h-full hover:shadow-md transition-shadow">
            <div className="flex items-center gap-2 mb-1">
              <span className="w-7 h-7 rounded-lg flex items-center justify-center" style={{ background: theme.oL, color: theme.orangeText }}>{l.icon}</span>
              <h3 className="text-sm font-bold" style={{ color: theme.text }}>{l.label}</h3>
            </div>
            <p className="text-xs" style={{ color: theme.t3 }}>{l.sub}</p>
          </Card>
        </Link>
      ))}
      <Card className="p-4 flex items-center gap-2" style={{ background: theme.g50 }}>
        <AlertTriangle className="w-4 h-4 shrink-0" style={{ color: theme.t3 }} />
        <p className="text-[11px]" style={{ color: theme.t3 }}>Team view never shows staff salaries or loan balances — only work, leave and development.</p>
      </Card>
    </div>
  )
}
