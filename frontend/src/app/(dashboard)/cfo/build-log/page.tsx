'use client'

/**
 * /cfo/build-log — the CFO's end-of-day read (CFO 2026-09-09).
 *
 * "what i have done for the whole day and what finished and what to finish."
 *
 * Four bands, in the order he reads them, and the fourth is the one most
 * dashboards would leave out: work that shipped with no request recorded
 * against it. Capture only happens through /goal, /code, /lane-b and /fabe, so
 * anything asked in plain conversation is invisible to the table. Hiding that
 * would make the page look complete and be wrong; showing it turns the gap
 * into the useful part.
 *
 * The day is Botswana's, not the server's — the box runs UTC, and a day ending
 * at 02:00 local would file a late session under tomorrow.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { Card } from '@/components/ui/card'
import { useTheme } from '@/contexts/ThemeContext'
import { API_BASE } from '@/lib/api'
import {
  AlertTriangle, Bug, CalendarDays, CheckCircle2, ChevronDown, ChevronRight,
  Clock, Filter, Loader2, Rocket, Search, User as UserIcon, X,
} from 'lucide-react'

/* ─────────────────────────────── shapes ─────────────────────────────────── */

interface BugRef { id: string; ref: string; status: string }
interface Item {
  id: string; title: string; asked_text: string; status: string
  status_label: string; asked_at: string; live_at: string | null
  machine: string; source: string; area: string; success_criteria: string
  age_hours: number | null; who_asked: string; bug: BugRef | null
  commits: { sha: string; subject: string }[]
  reason?: string
}
interface Unlinked { sha: string; subject: string; author: string; at: string }
interface Person { who: string; open: number; live: number }
interface Day {
  day: string
  went_live: Item[]; in_flight: Item[]; still_to_do: Item[]
  parked: Item[]; looks_done: Item[]
  unlinked_commits: Unlinked[]
  deploys: { sha: string; at: string; commits: number; ok: boolean }[]
  counts: {
    asked_today: number; went_live_today: number; still_open: number
    open_all_time: number; looks_done: number
    deploys_today: number; shipped_without_a_request: number
  }
  by_person: Person[]
  filter_options: { people: string[]; areas: string[]; sources: string[] }
  recording_since: string | null
}

function token(): string | null {
  try {
    const t = localStorage.getItem('alpha_token')
    return t ? `Token ${t}` : null
  } catch { return null }
}

const fmtTime = (s: string | null) =>
  s ? new Date(s).toLocaleTimeString('en-GB',
      { hour: '2-digit', minute: '2-digit', timeZone: 'Africa/Gaborone' }) : ''
const fmtDate = (s: string | null) =>
  s ? new Date(s).toLocaleDateString('en-GB',
      { day: 'numeric', month: 'short', timeZone: 'Africa/Gaborone' }) : ''

function age(h: number | null): string {
  if (h == null) return ''
  if (h < 1) return 'just now'
  if (h < 24) return `${Math.round(h)}h`
  const d = Math.round(h / 24)
  return `${d} day${d === 1 ? '' : 's'}`
}

/* ─────────────────────────────── page ───────────────────────────────────── */

export default function BuildLogPage() {
  const { theme: T } = useTheme()
  const [data, setData] = useState<Day | null>(null)
  const [loading, setLoading] = useState(true)
  const [denied, setDenied] = useState(false)
  const [date, setDate] = useState('')
  const [who, setWho] = useState('')
  const [area, setArea] = useState('')
  const [bugsOnly, setBugsOnly] = useState(false)
  const [q, setQ] = useState('')
  const [stage, setStage] = useState('')
  const [answering, setAnswering] = useState<string | null>(null)
  const [open, setOpen] = useState<Record<string, boolean>>({})

  const load = useCallback(async () => {
    const auth = token()
    if (!auth) { setLoading(false); setDenied(true); return }
    setLoading(true)
    const p = new URLSearchParams()
    if (date) p.set('date', date)
    if (who) p.set('who', who)
    if (area) p.set('area', area)
    if (bugsOnly) p.set('bugs', '1')
    if (q) p.set('q', q)
    if (stage) p.set('stage', stage)
    try {
      const r = await fetch(`${API_BASE}/cfo/build-log/?${p}`, { headers: { Authorization: auth } })
      if (r.status === 403 || r.status === 401) { setDenied(true); setData(null) }
      else if (r.ok) { setDenied(false); setData(await r.json()) }
    } catch { /* leave the last good view on screen */ }
    finally { setLoading(false) }
  }, [date, who, area, bugsOnly, q, stage])

  useEffect(() => { void load() }, [load])

  const anyFilter = !!(who || area || bugsOnly || q || stage)
  const clear = () => { setWho(''); setArea(''); setBugsOnly(false); setQ(''); setStage('') }

  // His yes/no on a row that looks finished. Yes = live now; no = stop asking.
  const answer = async (id: string, a: 'yes' | 'no') => {
    const auth = token()
    if (!auth) return
    setAnswering(id)
    try {
      const r = await fetch(`${API_BASE}/cfo/build-log/confirm/`, {
        method: 'POST',
        headers: { Authorization: auth, 'Content-Type': 'application/json' },
        body: JSON.stringify({ id, answer: a }),
      })
      if (r.ok) await load()
    } finally { setAnswering(null) }
  }

  if (denied) {
    return (
      <div>
        <TopBar title="Build log" />
        <div style={{ padding: 40, textAlign: 'center', color: T.t2 }}>
          <AlertTriangle className="w-8 h-8" style={{ margin: '0 auto 12px', color: T.wr }} />
          <div style={{ fontSize: 15, fontWeight: 600, color: T.text }}>This screen is the CFO&rsquo;s own.</div>
          <div style={{ fontSize: 13, marginTop: 8, maxWidth: 420, marginInline: 'auto', lineHeight: 1.6 }}>
            It opens only for <b>pganesharajah</b>. If you are signed in as the shared
            excoboard account, sign in as yourself and it will appear.
          </div>
        </div>
      </div>
    )
  }

  return (
    <div>
      <TopBar title="Build log" />
      <div style={{ padding: '18px 20px 40px', maxWidth: 1180, margin: '0 auto' }}>

        {/* ── the four numbers, biggest first ─────────────────────────────── */}
        <div style={{
          display: 'grid', gap: 12, marginBottom: 16,
          gridTemplateColumns: 'repeat(auto-fit,minmax(168px,1fr))',
        }}>
          <Stat T={T} label="Finished today" value={data?.counts.went_live_today ?? 0}
                tone="ok" icon={<CheckCircle2 className="w-4 h-4" />} big />
          <Stat T={T} label="Open — all time" value={data?.counts.open_all_time ?? 0}
                tone="wr" icon={<Clock className="w-4 h-4" />} big />
          <Stat T={T} label="Asked today" value={data?.counts.asked_today ?? 0}
                tone="inf" icon={<UserIcon className="w-4 h-4" />} />
          <Stat T={T} label="Releases today" value={data?.counts.deploys_today ?? 0}
                tone="inf" icon={<Rocket className="w-4 h-4" />} />
        </div>

        {/* ── filters ─────────────────────────────────────────────────────── */}
        <Card style={{ padding: '11px 13px', marginBottom: 16 }}>
          <div style={{ display: 'flex', gap: 9, flexWrap: 'wrap', alignItems: 'center' }}>
            <Filter className="w-4 h-4" style={{ color: T.t3 }} />
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12.5, color: T.t2 }}>
              <CalendarDays className="w-3.5 h-3.5" />
              <input type="date" value={date} onChange={e => setDate(e.target.value)}
                     style={inp(T)} />
            </label>
            <select value={who} onChange={e => setWho(e.target.value)} style={inp(T)}>
              <option value="">Anyone who asked</option>
              {(data?.filter_options.people || []).map(p => <option key={p} value={p}>{p}</option>)}
            </select>
            <select value={area} onChange={e => setArea(e.target.value)} style={inp(T)}>
              <option value="">Any area</option>
              {(data?.filter_options.areas || []).map(a => <option key={a} value={a}>{a}</option>)}
            </select>
            <select value={stage} onChange={e => setStage(e.target.value)} style={inp(T)}
                    aria-label="Stage">
              <option value="">Any stage</option>
              <option value="asked">Asked</option>
              <option value="building">Being built</option>
              <option value="waiting">Waiting to go live</option>
              <option value="live">Live</option>
              <option value="parked">Parked</option>
            </select>
            <button onClick={() => setBugsOnly(v => !v)}
                    style={{
                      ...inp(T), cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 5,
                      background: bugsOnly ? T.oL : 'transparent',
                      borderColor: bugsOnly ? T.orange : T.cardBdr,
                      color: bugsOnly ? T.orangeText : T.t2, fontWeight: bugsOnly ? 600 : 400,
                    }}>
              <Bug className="w-3.5 h-3.5" /> From a bug
            </button>
            <div style={{ position: 'relative', flex: '1 1 180px', minWidth: 150 }}>
              <Search className="w-3.5 h-3.5" style={{ position: 'absolute', left: 8, top: 9, color: T.t3 }} />
              <input value={q} onChange={e => setQ(e.target.value)} placeholder="Search what was asked…"
                     style={{ ...inp(T), width: '100%', paddingLeft: 27 }} />
            </div>
            {anyFilter && (
              <button onClick={clear} style={{ ...inp(T), cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 5 }}>
                <X className="w-3.5 h-3.5" /> Clear
              </button>
            )}
            {loading && <Loader2 className="w-4 h-4 animate-spin" style={{ color: T.t3 }} />}
          </div>
        </Card>

        {/* ── band 0: looks done — his yes/no ──────────────────────────────── */}
        {!!data?.looks_done?.length && (
          <Band T={T} title="Looks done — is it?" tone={T.orange} icon={<AlertTriangle className="w-4 h-4" />}
                sub="Evidence says these are finished. Yes marks it live; No keeps it open and stops asking."
                count={data.looks_done.length}>
            {data.looks_done.map(i =>
              <Row key={i.id} T={T} i={i} open={!!open[i.id]}
                   toggle={() => setOpen(o => ({ ...o, [i.id]: !o[i.id] }))}
                   right={<span style={{ color: T.t3 }}>{i.reason}</span>}
                   actions={<>
                     <button disabled={answering === i.id} onClick={() => void answer(i.id, 'yes')}
                             aria-label={`Yes, ${i.title} is done`}
                             style={{ ...inp(T), cursor: 'pointer', color: T.ok, fontWeight: 600 }}>Yes</button>
                     <button disabled={answering === i.id} onClick={() => void answer(i.id, 'no')}
                             aria-label={`No, ${i.title} is not done`}
                             style={{ ...inp(T), cursor: 'pointer' }}>No</button>
                   </>} />)}
          </Band>
        )}

        {/* ── band 1: finished ────────────────────────────────────────────── */}
        <Band T={T} title="Finished today" tone={T.ok} icon={<CheckCircle2 className="w-4 h-4" />}
              sub="Live on Omni — not just written."
              count={data?.went_live.length ?? 0}>
          {(data?.went_live || []).map(i =>
            <Row key={i.id} T={T} i={i} open={!!open[i.id]}
                 toggle={() => setOpen(o => ({ ...o, [i.id]: !o[i.id] }))}
                 right={<span style={{ color: T.ok, fontWeight: 600 }}>{fmtTime(i.live_at)}</span>} />)}
        </Band>

        {/* ── band 2: in flight ───────────────────────────────────────────── */}
        <Band T={T} title="Being built" tone={T.inf} icon={<Loader2 className="w-4 h-4" />}
              sub="Picked up, not live yet."
              count={data?.in_flight.length ?? 0}>
          {(data?.in_flight || []).map(i =>
            <Row key={i.id} T={T} i={i} open={!!open[i.id]}
                 toggle={() => setOpen(o => ({ ...o, [i.id]: !o[i.id] }))}
                 right={<span style={{ color: (i.age_hours ?? 0) > 24 ? T.wr : T.t3 }}>{age(i.age_hours)}</span>} />)}
        </Band>

        {/* ── band 3: still to finish ─────────────────────────────────────── */}
        <Band T={T} title="Still to finish" tone={T.wr} icon={<Clock className="w-4 h-4" />}
              sub="Asked for, nothing built against it yet. Oldest first."
              count={data?.still_to_do.length ?? 0}>
          {(data?.still_to_do || []).map(i =>
            <Row key={i.id} T={T} i={i} open={!!open[i.id]}
                 toggle={() => setOpen(o => ({ ...o, [i.id]: !o[i.id] }))}
                 right={<span style={{ color: (i.age_hours ?? 0) > 48 ? T.er : T.t3 }}>{age(i.age_hours)}</span>} />)}
        </Band>

        {!!data?.parked?.length && (
          <Band T={T} title="Parked" tone={T.t3} icon={<Clock className="w-4 h-4" />}
                sub="Put on hold on purpose." count={data.parked.length}>
            {data.parked.map(i =>
              <Row key={i.id} T={T} i={i} open={!!open[i.id]}
                   toggle={() => setOpen(o => ({ ...o, [i.id]: !o[i.id] }))}
                   right={<span style={{ color: T.t3 }}>{age(i.age_hours)}</span>} />)}
          </Band>
        )}

        {/* ── band 4: the honest gap ──────────────────────────────────────── */}
        {!!data?.unlinked_commits.length && (
          <Band T={T} title="Shipped, but no request recorded" tone={T.t3}
                icon={<AlertTriangle className="w-4 h-4" />}
                sub="Work that went live today with nothing on record asking for it — usually asked in plain chat rather than through /goal, /code, /lane-b or /fabe."
                count={data.unlinked_commits.length}>
            {data.unlinked_commits.map(c => (
              <div key={c.sha} style={{
                display: 'flex', gap: 10, padding: '9px 13px',
                borderTop: `1px solid ${T.cardBdr}`, fontSize: 13, alignItems: 'baseline',
              }}>
                <code style={{ color: T.t3, fontSize: 11.5 }}>{c.sha}</code>
                <span style={{ flex: 1, color: T.t2 }}>{c.subject}</span>
                <span style={{ color: T.t3, fontSize: 11.5 }}>{fmtTime(c.at)}</span>
              </div>
            ))}
          </Band>
        )}

        {/* ── who is waiting on you ───────────────────────────────────────── */}
        {!!data?.by_person.length && (
          <Card style={{ marginTop: 16, overflow: 'hidden' }}>
            <div style={{ padding: '11px 14px', background: T.g50, borderBottom: `1px solid ${T.cardBdr}`,
                          fontSize: 13, fontWeight: 700, color: T.text }}>
              Who is waiting on you
            </div>
            {data.by_person.map(p => (
              <button key={p.who} onClick={() => setWho(p.who)}
                      style={{
                        width: '100%', display: 'flex', alignItems: 'center', gap: 10,
                        padding: '8px 14px', borderTop: `1px solid ${T.cardBdr}`,
                        background: 'transparent', cursor: 'pointer', textAlign: 'left',
                      }}>
                <span style={{ flex: 1, fontSize: 13, color: T.text }}>{p.who}</span>
                {p.open > 0 && <Pill T={T} tone="wr">{p.open} open</Pill>}
                {p.live > 0 && <Pill T={T} tone="ok">{p.live} done</Pill>}
              </button>
            ))}
          </Card>
        )}

        {/* ── the honesty footer ──────────────────────────────────────────── */}
        <div style={{ marginTop: 18, fontSize: 11.5, color: T.t3, lineHeight: 1.65 }}>
          {data?.recording_since
            ? <>Recording since {fmtDate(data.recording_since)}. Anything asked before that,
                or asked in plain chat rather than through <b>/goal</b>, <b>/code</b>,
                <b> /lane-b</b> or <b>/fabe</b>, is not on this page.</>
            : <>Nothing recorded yet. Rows appear as soon as <b>/goal</b>, <b>/code</b>,
                <b> /lane-b</b> or <b>/fabe</b> is used.</>}
          {' '}&ldquo;Finished&rdquo; means live on Omni, never just written. It does not
          say whether a change works — the nightly quality check answers that.
        </div>
      </div>
    </div>
  )
}

/* ───────────────────────────── pieces ───────────────────────────────────── */

const inp = (T: any) => ({
  fontSize: 12.5, padding: '6px 9px', borderRadius: 8, color: T.text,
  border: `1px solid ${T.cardBdr}`, background: 'transparent', fontFamily: 'inherit',
})

function Pill({ T, tone, children }: { T: any; tone: 'ok' | 'wr'; children: React.ReactNode }) {
  return <span style={{
    fontSize: 11, padding: '2px 8px', borderRadius: 999, fontWeight: 600,
    background: tone === 'ok' ? T.okB : T.wrB, color: tone === 'ok' ? T.ok : T.wr,
  }}>{children}</span>
}

function Stat({ T, label, value, tone, icon, big }: {
  T: any; label: string; value: number; tone: 'ok' | 'wr' | 'inf'; icon: React.ReactNode; big?: boolean
}) {
  const c = tone === 'ok' ? T.ok : tone === 'wr' ? T.wr : T.inf
  return (
    <Card style={{ padding: '13px 15px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: c, marginBottom: 5 }}>
        {icon}
        <span style={{ fontSize: 11.5, letterSpacing: .3, textTransform: 'uppercase',
                       fontWeight: 700, color: T.t3 }}>{label}</span>
      </div>
      <div style={{ fontSize: big ? 34 : 24, fontWeight: 700, lineHeight: 1, color: T.text }}>{value}</div>
    </Card>
  )
}

function Band({ T, title, sub, tone, icon, count, children }: {
  T: any; title: string; sub: string; tone: string; icon: React.ReactNode
  count: number; children: React.ReactNode
}) {
  return (
    <Card style={{ marginBottom: 14, overflow: 'hidden' }}>
      <div style={{ padding: '11px 14px', borderLeft: `3px solid ${tone}`, background: T.g50 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ color: tone, display: 'flex' }}>{icon}</span>
          <span style={{ fontSize: 13.5, fontWeight: 700, color: T.text }}>{title}</span>
          <span style={{ fontSize: 12, color: T.t3 }}>({count})</span>
        </div>
        <div style={{ fontSize: 11.5, color: T.t3, marginTop: 3 }}>{sub}</div>
      </div>
      {count === 0
        ? <div style={{ padding: '14px', fontSize: 12.5, color: T.t3 }}>Nothing here.</div>
        : children}
    </Card>
  )
}

function Row({ T, i, open, toggle, right, actions }: {
  T: any; i: Item; open: boolean; toggle: () => void; right: React.ReactNode
  actions?: React.ReactNode
}) {
  return (
    <div style={{ borderTop: `1px solid ${T.cardBdr}` }}>
      <div style={{ display: 'flex', alignItems: 'center' }}>
      <button onClick={toggle} style={{
        flex: 1, minWidth: 0, display: 'flex', gap: 9, alignItems: 'baseline', textAlign: 'left',
        padding: '10px 13px', background: 'transparent', cursor: 'pointer',
      }}>
        <span style={{ color: T.t3, marginTop: 2 }}>
          {open ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
        </span>
        <span style={{ flex: 1, minWidth: 0 }}>
          <span style={{ fontSize: 13.5, color: T.text, lineHeight: 1.45 }}>{i.title}</span>
          <span style={{ display: 'flex', gap: 7, flexWrap: 'wrap', marginTop: 4, alignItems: 'center' }}>
            <span style={{ fontSize: 11.5, color: T.t3 }}>asked by <b style={{ color: T.t2 }}>{i.who_asked}</b></span>
            {i.area && <span style={{ fontSize: 11, color: T.t3 }}>· {i.area}</span>}
            {i.source && <span style={{ fontSize: 11, color: T.t3 }}>· /{i.source}</span>}
            {i.bug && (
              <a href={`/bug-reports`} onClick={e => e.stopPropagation()}
                 style={{
                   fontSize: 11, padding: '1px 7px', borderRadius: 999, textDecoration: 'none',
                   background: T.erB, color: T.er, display: 'inline-flex', alignItems: 'center', gap: 3,
                 }}>
                <Bug className="w-3 h-3" /> {i.bug.ref}
              </a>
            )}
          </span>
        </span>
        <span style={{ fontSize: 11.5, whiteSpace: 'nowrap' }}>{right}</span>
      </button>
      {actions && <span style={{ display: 'flex', gap: 6, paddingRight: 13 }}>{actions}</span>}
      </div>
      {open && (
        <div style={{ padding: '2px 13px 13px 35px', fontSize: 12.5, color: T.t2, lineHeight: 1.6 }}>
          <div style={{
            padding: '9px 11px', borderRadius: 8, background: T.g50,
            borderLeft: `2px solid ${T.orange}`, whiteSpace: 'pre-wrap',
          }}>
            <div style={{ fontSize: 10.5, textTransform: 'uppercase', letterSpacing: .4,
                          color: T.t3, fontWeight: 700, marginBottom: 4 }}>In their words</div>
            {i.asked_text}
          </div>
          {i.success_criteria && (
            <div style={{ marginTop: 9 }}>
              <div style={{ fontSize: 10.5, textTransform: 'uppercase', letterSpacing: .4,
                            color: T.t3, fontWeight: 700, marginBottom: 3 }}>How we know it is done</div>
              <div style={{ whiteSpace: 'pre-wrap' }}>{i.success_criteria}</div>
            </div>
          )}
          {!!i.commits.length && (
            <div style={{ marginTop: 9 }}>
              {i.commits.map(c => (
                <div key={c.sha} style={{ display: 'flex', gap: 8, fontSize: 11.5, color: T.t3 }}>
                  <code>{c.sha}</code><span>{c.subject}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
