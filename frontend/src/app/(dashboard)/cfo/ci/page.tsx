'use client'

/**
 * /cfo/ci — the live gate board (CFO 2026-09-09).
 *
 * "see what's happening in my omni github real time, how many different
 *  branches are pushing, eta, time left."
 *
 * He runs several chats at once, each building a different part of omni, and
 * until now the only way to see it was four browser tabs on GitHub. This is the
 * one screen: every branch building now, what it is, who started it, and how
 * long is left.
 *
 * WHY A WEB PAGE AND NOT A WINDOWS APP. He works on a Windows PC and a Mac
 * Mini and reads on his phone. A desktop app would cover one of the three.
 *
 * WHAT THE COUNTDOWN IS. Each runner's median of its own recent successful
 * runs, held server-side. It is an ESTIMATE and the page says so — a number
 * presented as certainty is the thing that makes a board untrustworthy the
 * first time it is wrong. Where there is not enough history it shows no number
 * at all rather than inventing one.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { Card } from '@/components/ui/card'
import { useTheme } from '@/contexts/ThemeContext'
import { API_BASE } from '@/lib/api'
import {
  AlertTriangle, CheckCircle2, GitBranch, Loader2, PlugZap, Timer, XCircle,
} from 'lucide-react'

/* ─────────────────────────────── shapes ─────────────────────────────────── */

interface Job {
  name: string; status: string
  elapsed_seconds: number; typical_seconds: number | null
  seconds_left: number | null; percent: number | null; url: string
}
interface Run {
  run_id: number; branch: string; title: string; actor: string; event: string
  status: string; url: string; machine: string; started_at: string | null
  elapsed_seconds: number; seconds_left: number | null; jobs: Job[]
}
interface Board {
  now: string
  building_now: Run[]
  branches_building: number
  recently_finished: Run[]
  ever_recorded: boolean
  recent_window_hours: number
}

function token(): string | null {
  try {
    const t = localStorage.getItem('alpha_token')
    return t ? `Token ${t}` : null
  } catch { return null }
}

/** 0s → "0s", 95s → "1m 35s", 530s → "8m 50s". Minutes and seconds, because
 *  "8.8 minutes" is not how anyone reads a countdown. */
function clock(total: number | null): string {
  if (total == null) return ''
  const s = Math.max(Math.round(total), 0)
  const m = Math.floor(s / 60)
  return m ? `${m}m ${String(s % 60).padStart(2, '0')}s` : `${s}s`
}

/* ─────────────────────────────── page ───────────────────────────────────── */

const LIVE_REFRESH_MS = 4000
const IDLE_REFRESH_MS = 20000

export default function GateBoardPage() {
  const { theme: T } = useTheme()
  const [data, setData] = useState<Board | null>(null)
  const [denied, setDenied] = useState(false)
  const [loading, setLoading] = useState(true)
  // Ticks once a second so the clocks move between fetches. Without it the
  // countdown freezes for four seconds at a time and reads as broken.
  const [, setTick] = useState(0)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const load = useCallback(async () => {
    const auth = token()
    if (!auth) { setLoading(false); setDenied(true); return }
    try {
      const r = await fetch(`${API_BASE}/cfo/ci/`, { headers: { Authorization: auth } })
      if (r.status === 401 || r.status === 403) { setDenied(true); setData(null) }
      else if (r.ok) { setDenied(false); setData(await r.json()) }
    } catch {
      /* keep the last good board on screen — a blink to empty on one dropped
         request looks exactly like "everything finished", which is a lie. */
    } finally { setLoading(false) }
  }, [])

  useEffect(() => { void load() }, [load])

  // Fast while something is building, slow when nothing is.
  useEffect(() => {
    const busy = (data?.building_now?.length ?? 0) > 0
    timer.current = setTimeout(() => { void load() },
      busy ? LIVE_REFRESH_MS : IDLE_REFRESH_MS)
    return () => { if (timer.current) clearTimeout(timer.current) }
  }, [data, load])

  useEffect(() => {
    const i = setInterval(() => setTick(t => t + 1), 1000)
    return () => clearInterval(i)
  }, [])

  if (denied) {
    return (
      <>
        <TopBar title="Gate board" />
        <Card style={{ margin: 20 }}>
          <div style={{ padding: 40, textAlign: 'center', color: T.t2 }}>
            <AlertTriangle className="w-8 h-8" style={{ margin: '0 auto 12px', color: T.wr }} />
            <div style={{ fontSize: 15, fontWeight: 600, color: T.text }}>
              This screen is the CFO&rsquo;s own.
            </div>
            <div style={{ marginTop: 6, fontSize: 13 }}>
              Sign in as yourself — the shared exco mailbox will not open it.
            </div>
          </div>
        </Card>
      </>
    )
  }

  const live = data?.building_now ?? []
  const done = data?.recently_finished ?? []

  return (
    <>
      <TopBar title="Gate board" />
      <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 16 }}>

        <Headline T={T} live={live} loading={loading}
                  branches={data?.branches_building ?? 0} />

        {live.map(run => <RunCard key={run.run_id} T={T} run={run} live />)}

        {!live.length && !loading && (
          <Card>
            <div style={{ padding: 32, textAlign: 'center' }}>
              {data?.ever_recorded ? (
                <>
                  <CheckCircle2 className="w-9 h-9"
                    style={{ margin: '0 auto 10px', color: T.ok }} />
                  <div style={{ fontSize: 15, fontWeight: 600, color: T.text }}>
                    Nothing building. All quiet.
                  </div>
                </>
              ) : (
                <>
                  {/* An empty board because nothing is running and an empty board
                      because GitHub was never connected look identical. Saying
                      which is the difference between a calm screen and a broken one. */}
                  <PlugZap className="w-9 h-9" style={{ margin: '0 auto 10px', color: T.wr }} />
                  <div style={{ fontSize: 15, fontWeight: 600, color: T.text }}>
                    Nothing has ever been recorded here.
                  </div>
                  <div style={{ marginTop: 6, fontSize: 13, color: T.t2 }}>
                    That means GitHub is not yet sending its updates — not that the
                    gate is quiet.
                  </div>
                </>
              )}
            </div>
          </Card>
        )}

        {!!done.length && (
          <div>
            <div style={{ fontSize: 12.5, fontWeight: 600, color: T.t3,
                          textTransform: 'uppercase', letterSpacing: 0.6,
                          margin: '4px 0 8px' }}>
              Finished in the last {data?.recent_window_hours ?? 6} hours
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {done.map(run => <RunCard key={run.run_id} T={T} run={run} />)}
            </div>
          </div>
        )}
      </div>
    </>
  )
}

/* ───────────────────────────── headline ─────────────────────────────────── */

function Headline({ T, live, branches, loading }:
  { T: any; live: Run[]; branches: number; loading: boolean }) {
  const jobs = live.reduce((n, r) => n + r.jobs.filter(j => j.status === 'running').length, 0)
  // The whole board is done when its slowest run is.
  const longest = live.reduce<number | null>((max, r) =>
    r.seconds_left == null ? max : Math.max(max ?? 0, r.seconds_left), null)

  return (
    <Card>
      <div style={{ padding: '18px 20px', display: 'flex', alignItems: 'center',
                    gap: 22, flexWrap: 'wrap' }}>
        <Stat T={T} value={String(branches)} label={branches === 1 ? 'branch building' : 'branches building'}
              colour={branches ? T.orange : T.t3} />
        <Stat T={T} value={String(jobs)} label={jobs === 1 ? 'machine running' : 'machines running'}
              colour={jobs ? T.teal : T.t3} />
        {longest != null && (
          <Stat T={T} value={clock(longest)} label="left, about" colour={T.inf} />
        )}
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
          {loading
            ? <Loader2 className="w-4 h-4 animate-spin" style={{ color: T.t3 }} />
            : <LivePulse T={T} on={!!live.length} />}
        </div>
      </div>
    </Card>
  )
}

function Stat({ T, value, label, colour }:
  { T: any; value: string; label: string; colour: string }) {
  return (
    <div>
      <div style={{ fontSize: 30, fontWeight: 700, lineHeight: 1, color: colour,
                    fontVariantNumeric: 'tabular-nums' }}>{value}</div>
      <div style={{ fontSize: 12, color: T.t2, marginTop: 4 }}>{label}</div>
    </div>
  )
}

function LivePulse({ T, on }: { T: any; on: boolean }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7,
                   fontSize: 12, color: on ? T.ok : T.t3 }}>
      <span className={on ? 'animate-pulse' : ''}
            style={{ width: 8, height: 8, borderRadius: 999,
                     background: on ? T.ok : T.g200 }} />
      {on ? 'live' : 'idle'}
    </span>
  )
}

/* ───────────────────────────── one run ──────────────────────────────────── */

function RunCard({ T, run, live = false }: { T: any; run: Run; live?: boolean }) {
  const tone =
    run.status === 'success' ? T.ok :
    run.status === 'failure' ? T.er :
    run.status === 'cancelled' ? T.t3 : T.orange

  const Icon =
    run.status === 'success' ? CheckCircle2 :
    run.status === 'failure' ? XCircle :
    run.status === 'cancelled' ? XCircle : Loader2

  return (
    <Card>
      <div style={{ padding: '14px 16px' }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, flexWrap: 'wrap' }}>
          <Icon className={`w-4 h-4 ${run.status === 'running' ? 'animate-spin' : ''}`}
                style={{ color: tone, marginTop: 2, flexShrink: 0 }} />
          <div style={{ minWidth: 0, flex: 1 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 7, flexWrap: 'wrap' }}>
              <GitBranch className="w-3.5 h-3.5" style={{ color: T.t3 }} />
              <a href={run.url} target="_blank" rel="noreferrer"
                 style={{ fontSize: 13.5, fontWeight: 600, color: T.text,
                          textDecoration: 'none' }}>
                {run.branch || 'unknown branch'}
              </a>
              {run.machine && <Pill T={T} text={run.machine} tone={T.teal} bg={T.tealL} />}
              {run.event === 'pull_request' && <Pill T={T} text="request" tone={T.inf} bg={T.inB} />}
            </div>
            {/* The commit subject, not the workflow name — this is how he knows
                WHICH piece of work is on the machine. */}
            {run.title && (
              <div style={{ fontSize: 12.5, color: T.t2, marginTop: 3,
                            overflow: 'hidden', textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap' }}>
                {run.title}
              </div>
            )}
          </div>
          <div style={{ textAlign: 'right', flexShrink: 0 }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: tone,
                          fontVariantNumeric: 'tabular-nums' }}>
              {clock(run.elapsed_seconds)}
            </div>
            {live && run.seconds_left != null && (
              <div style={{ fontSize: 11.5, color: T.t3, marginTop: 2 }}>
                <Timer className="w-3 h-3" style={{ display: 'inline', marginRight: 3 }} />
                ~{clock(run.seconds_left)} left
              </div>
            )}
          </div>
        </div>

        {!!run.jobs.length && (
          <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 7 }}>
            {run.jobs.map(job => <JobBar key={job.name} T={T} job={job} />)}
          </div>
        )}
      </div>
    </Card>
  )
}

function JobBar({ T, job }: { T: any; job: Job }) {
  const tone =
    job.status === 'success' ? T.ok :
    job.status === 'failure' ? T.er :
    job.status === 'cancelled' ? T.t3 :
    job.status === 'queued' ? T.t3 : T.orange

  const pct = job.percent ?? (job.status === 'queued' ? 0 : 6)

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
      <div style={{ width: 118, fontSize: 12, color: T.t2, flexShrink: 0,
                    overflow: 'hidden', textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap' }}>
        {job.name}
      </div>
      <div style={{ flex: 1, height: 8, borderRadius: 999, background: T.g100,
                    overflow: 'hidden', minWidth: 60 }}>
        <div style={{ width: `${pct}%`, height: '100%', background: tone,
                      borderRadius: 999, transition: 'width 900ms ease-out' }} />
      </div>
      <div style={{ width: 96, textAlign: 'right', fontSize: 11.5, color: T.t3,
                    fontVariantNumeric: 'tabular-nums', flexShrink: 0 }}>
        {job.status === 'queued'
          ? 'waiting'
          : job.seconds_left != null
            ? `~${clock(job.seconds_left)} left`
            : clock(job.elapsed_seconds)}
      </div>
    </div>
  )
}

function Pill({ T, text, tone, bg }: { T: any; text: string; tone: string; bg: string }) {
  return (
    <span style={{ fontSize: 10.5, fontWeight: 600, color: tone, background: bg,
                   padding: '1px 7px', borderRadius: 999, letterSpacing: 0.2 }}>
      {text}
    </span>
  )
}
