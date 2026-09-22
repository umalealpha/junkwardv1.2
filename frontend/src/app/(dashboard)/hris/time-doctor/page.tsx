'use client'

/**
 * /hris/time-doctor — Workforce · Time & Pay.
 *
 * CFO directive 2026-06-16 (redesigned 2026-06-19 per the Google Stitch
 * "Executive Reserve" design + the Manus "Daily Time Doctor Exceptions"
 * playbook). Time Doctor tracked hours reconciled against payroll, with an
 * adoption gauge + utilization distribution (replacing the old 3D scene), the
 * management EXCEPTION GROUPS (non-trackers first, critical/low hours, high
 * idle, high unproductive), a Aria insight (anonymised server-side), and
 * the tracked-vs-pay reconciliation. Privacy: raw activity titles never reach
 * here; Aria gets no names.
 */
import { useEffect, useMemo, useState } from 'react'
import {
  getTimeDoctorReconciliation, getWorkforceBriefToggle, setWorkforceBriefToggle,
  getTimeDoctorLeaderboard, getPendingJustifications, reviewJustification,
  getTimeDoctorLatecomers,
} from '@/lib/api'
import type {
  TDReconciliation, TDReconRow, TDExceptionRow, TDDeptRow, TDMonth,
  WorkforceBriefToggle, Leaderboard, PendingJustification, Latecomers,
} from '@/lib/api'
import Link from 'next/link'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  Clock, RefreshCw, AlertCircle, Users, Gauge, Sparkles, Zap,
  UserX, BatteryLow, Coffee, AlertTriangle, Power, Trophy, TrendingDown,
} from 'lucide-react'

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'
const fmtPct = (v: number | null | undefined) => (v == null ? '—' : `${v}%`)
const fmtN = (v: number | null | undefined) => (v == null ? '—' : v.toLocaleString())

function Tile({ icon, label, value, sub, accent }: { icon: React.ReactNode; label: string; value: string; sub?: string; accent?: boolean }) {
  return (
    <Card>
      <CardContent className="pt-5">
        <div className="flex items-center gap-2 text-[#6B7280] text-xs uppercase tracking-wide">{icon}{label}</div>
        <div className="text-2xl font-bold mt-1" style={{ color: accent ? ORANGE : NAVY, fontFamily: 'Georgia, "Book Antiqua", serif' }}>{value}</div>
        {sub && <div className="text-xs text-[#9CA3AF] mt-0.5">{sub}</div>}
      </CardContent>
    </Card>
  )
}

/** Adoption donut — SVG, no chart lib. */
function AdoptionDonut({ pct, tracking, total }: { pct: number; tracking: number; total: number }) {
  const r = 54, c = 2 * Math.PI * r
  const dash = Math.max(0, Math.min(100, pct)) / 100 * c
  return (
    <div className="flex flex-col items-center justify-center">
      <svg width="150" height="150" viewBox="0 0 150 150">
        <circle cx="75" cy="75" r={r} fill="none" stroke="#EEF0F3" strokeWidth="14" />
        <circle cx="75" cy="75" r={r} fill="none" stroke={ORANGE} strokeWidth="14" strokeLinecap="round"
          strokeDasharray={`${dash} ${c - dash}`} transform="rotate(-90 75 75)"
          style={{ transition: 'stroke-dasharray .8s cubic-bezier(.16,1,.3,1)' }} />
        <text x="75" y="72" textAnchor="middle" fontSize="30" fontWeight="700" fill={NAVY} fontFamily="Georgia, serif">{pct}%</text>
        <text x="75" y="92" textAnchor="middle" fontSize="10" fill="#6B7280">Active</text>
      </svg>
      <div className="text-xs text-[#6B7280] mt-1">{tracking} tracking · {total - tracking} not yet</div>
    </div>
  )
}

/** Utilization distribution histogram — SVG. Buckets the tracking rows. */
function UtilHistogram({ rows, avg }: { rows: TDReconRow[]; avg: number | null }) {
  const bins = useMemo(() => {
    const b = new Array(5).fill(0)  // 0-20,20-40,40-60,60-80,80-100+
    for (const r of rows) {
      if (!r.is_tracking || r.utilization_pct == null) continue
      const i = Math.min(4, Math.floor(r.utilization_pct / 20))
      b[i] += 1
    }
    return b
  }, [rows])
  const max = Math.max(1, ...bins)
  const labels = ['0–20', '20–40', '40–60', '60–80', '80%+']
  const W = 320, H = 150, pad = 22, bw = (W - pad * 2) / 5
  const avgX = avg == null ? null : pad + (Math.min(100, avg) / 20) * bw
  return (
    <svg width="100%" height={H + 24} viewBox={`0 0 ${W} ${H + 24}`} preserveAspectRatio="xMidYMid meet">
      {bins.map((v, i) => {
        const h = (v / max) * (H - 30)
        return (
          <g key={i}>
            <rect x={pad + i * bw + 6} y={H - 20 - h} width={bw - 12} height={h} rx="3" fill={NAVY} opacity={0.85} />
            <text x={pad + i * bw + bw / 2} y={H - 6} textAnchor="middle" fontSize="9" fill="#9CA3AF">{labels[i]}</text>
            {v > 0 && <text x={pad + i * bw + bw / 2} y={H - 26 - h} textAnchor="middle" fontSize="9" fill="#6B7280">{v}</text>}
          </g>
        )
      })}
      {avgX != null && (
        <g>
          <line x1={avgX} y1={6} x2={avgX} y2={H - 20} stroke={ORANGE} strokeWidth="2" strokeDasharray="4 3" />
          <text x={avgX} y={4} textAnchor="middle" fontSize="9" fill={ORANGE} fontWeight="700">avg {avg}%</text>
        </g>
      )}
    </svg>
  )
}

type GroupKey = 'not_tracking' | 'critical_low_hours' | 'low_hours_warning' | 'high_idle' | 'high_unproductive'
const GROUP_META: Record<GroupKey, { label: string; icon: React.ReactNode; color: string; bg: string; metric: (r: TDExceptionRow) => string }> = {
  not_tracking:       { label: 'Not tracking', icon: <UserX className="w-4 h-4" />, color: '#6B7280', bg: '#F3F4F6', metric: () => 'no data' },
  critical_low_hours: { label: 'Critical low hours', icon: <BatteryLow className="w-4 h-4" />, color: '#B91C1C', bg: '#FEF2F2', metric: r => `util ${fmtPct(r.utilization_pct)}` },
  low_hours_warning:  { label: 'Low hours (warning)', icon: <AlertTriangle className="w-4 h-4" />, color: '#B45309', bg: '#FFFBEB', metric: r => `util ${fmtPct(r.utilization_pct)}` },
  high_idle:          { label: 'High idle', icon: <Coffee className="w-4 h-4" />, color: '#C2410C', bg: '#FFF7ED', metric: r => `idle ${fmtPct(r.idle_pct)}` },
  high_unproductive:  { label: 'High unproductive', icon: <Zap className="w-4 h-4" />, color: '#7C3AED', bg: '#F5F3FF', metric: r => `unprod ${fmtPct(r.unproductive_pct)}` },
}

/** Monthly trend — tracked hours bars + productive% line markers. */
function MonthlyTrend({ months }: { months: TDMonth[] }) {
  if (!months.length) return <div className="text-xs text-[#9CA3AF] py-3">No monthly data yet — fills as daily snapshots accrue.</div>
  const max = Math.max(1, ...months.map(m => m.tracked_hours))
  return (
    <div className="flex items-end gap-3 h-40 pt-2">
      {months.map(m => {
        const h = (m.tracked_hours / max) * 120
        return (
          <div key={m.month} className="flex-1 flex flex-col items-center justify-end">
            <div className="text-[10px] text-[#6B7280]">{fmtPct(m.productive_pct)}</div>
            <div className="w-full rounded-t" style={{ height: h, background: NAVY, opacity: 0.85, transition: 'height .6s cubic-bezier(.16,1,.3,1)' }} />
            <div className="text-[10px] font-semibold mt-1" style={{ color: NAVY }}>{fmtN(Math.round(m.tracked_hours))}h</div>
            <div className="text-[10px] text-[#9CA3AF]">{m.month}</div>
          </div>
        )
      })}
    </div>
  )
}

function ExceptionCard({ k, rows }: { k: GroupKey; rows: TDExceptionRow[] }) {
  const [open, setOpen] = useState(k === 'not_tracking' || k === 'critical_low_hours')
  const m = GROUP_META[k]
  const cap = open ? rows.length : 0
  return (
    <Card>
      <CardContent className="pt-4">
        <button onClick={() => setOpen(o => !o)} className="w-full flex items-center gap-2 text-left">
          <span className="w-7 h-7 rounded-lg grid place-items-center" style={{ background: m.bg, color: m.color }}>{m.icon}</span>
          <span className="font-semibold text-sm" style={{ color: NAVY }}>{m.label}</span>
          <span className="ml-auto text-lg font-bold" style={{ color: m.color, fontFamily: 'Georgia, serif' }}>{rows.length}</span>
        </button>
        {open && (
          <div className="mt-2 max-h-56 overflow-y-auto divide-y divide-[#F3F4F6]">
            {rows.length === 0 && <div className="text-xs text-[#9CA3AF] py-2">None — all clear.</div>}
            {rows.slice(0, 100).map((r, i) => (
              <div key={i} className="flex items-center gap-2 py-1.5 text-xs">
                <span className="font-medium truncate" style={{ color: NAVY }}>{r.employee}</span>
                <span className="text-[#9CA3AF] truncate">{r.department}</span>
                <span className="ml-auto tabular-nums" style={{ color: m.color }}>{m.metric(r)}</span>
              </div>
            ))}
            {rows.length > 100 && <div className="text-[11px] text-[#9CA3AF] py-1">+{rows.length - 100} more</div>}
          </div>
        )}
        {!open && rows.length > 0 && <div className="text-[11px] text-[#9CA3AF] mt-1">click to view</div>}
      </CardContent>
    </Card>
  )
}

export default function TimeDoctorDashboard() {
  const [recon, setRecon] = useState<TDReconciliation | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [insightLoading, setInsightLoading] = useState(false)
  const [q, setQ] = useState('')
  // Workforce Daily Brief on/off switch (CFO 2026-07-14) — CFO / Arun / Arjun only.
  const [brief, setBrief] = useState<WorkforceBriefToggle | null>(null)
  const [briefSaving, setBriefSaving] = useState(false)
  const [lb, setLb] = useState<Leaderboard | null>(null)
  const [lbPeriod, setLbPeriod] = useState<'day' | 'week' | 'month'>('week')
  const [lbLoading, setLbLoading] = useState(false)
  // Self-reported hour explanations awaiting manager sign-off (Fable 2026-07-14).
  const [pending, setPending] = useState<PendingJustification[]>([])
  const [pendingAria, setPendingAria] = useState<string>('')
  const [reviewBusy, setReviewBusy] = useState<string | null>(null)
  const [late, setLate] = useState<Latecomers | null>(null)

  async function loadPending() {
    try {
      const r = await getPendingJustifications()
      setPending(r.items); setPendingAria(r.aria_summary || '')
    } catch { /* non-managers get 403 — card simply stays hidden */ }
  }
  useEffect(() => { loadPending() }, [])
  useEffect(() => { getTimeDoctorLatecomers().then(setLate).catch(() => {}) }, [])

  async function review(id: string, decision: 'approve' | 'reject') {
    setReviewBusy(id)
    try {
      await reviewJustification(id, decision)
      setPending(p => p.filter(r => r.id !== id))
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not save the review') }
    finally { setReviewBusy(null) }
  }

  async function loadLb(period: 'day' | 'week' | 'month') {
    setLbPeriod(period); setLbLoading(true)
    try { setLb(await getTimeDoctorLeaderboard(period)) }
    catch { /* surfaced as empty */ }
    finally { setLbLoading(false) }
  }
  useEffect(() => { loadLb('week') }, [])

  async function load(withInsight = false) {
    setLoading(true); setError(null)
    try { setRecon(await getTimeDoctorReconciliation(3, withInsight)) }
    catch (e) { setError(e instanceof Error ? e.message : 'Failed to load') }
    finally { setLoading(false) }
  }
  useEffect(() => { load(false) }, [])
  useEffect(() => { getWorkforceBriefToggle().then(setBrief).catch(() => {}) }, [])

  async function toggleBrief() {
    if (!brief) return
    setBriefSaving(true)
    try { setBrief(await setWorkforceBriefToggle(!brief.enabled)) }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not change the switch') }
    finally { setBriefSaving(false) }
  }

  async function loadInsight() {
    setInsightLoading(true)
    try { setRecon(await getTimeDoctorReconciliation(3, true)) } catch { /* surfaced below */ }
    finally { setInsightLoading(false) }
  }

  const t = recon?.totals
  const rows = recon?.rows ?? []
  const ex = recon?.exceptions
  const dept = recon?.by_department ?? []
  const coverage = recon?.coverage
  const monthly = recon?.monthly ?? []
  const feed = (recon as { feed?: { latest_as_of?: string | null; data_age_days?: number | null; snapshot_count?: number; live?: boolean } } | null)?.feed
  const stale = !!feed && feed.data_age_days != null && feed.data_age_days > 2
  const shown = useMemo(() => {
    const n = q.trim().toLowerCase()
    return n ? rows.filter(r => (r.employee || '').toLowerCase().includes(n)) : rows
  }, [rows, q])
  const adoptionPct = t && t.employees ? Math.round(100 * t.employees_tracking / t.employees) : 0
  const groupOrder: GroupKey[] = ['not_tracking', 'critical_low_hours', 'low_hours_warning', 'high_idle', 'high_unproductive']

  return (
    <div className="min-h-screen bg-[#F8F9FB]">
      {/* BUG ae88d99b (Oprah 2026-06-26): bare <TopBar/> fell back to getPageLabel
          → "Page" because /hris/time-doctor isn't in PAGE_LABELS. Pass title +
          breadcrumbs explicitly, like every other HRIS page. */}
      <TopBar title="Time & Pay" breadcrumbs={[{ label: 'HRIS', href: '/hris' }, { label: 'Time & Pay' }]} />
      <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        <div className="flex items-center gap-3 mb-4">
          <div className="w-10 h-10 rounded-lg flex items-center justify-center" style={{ background: NAVY }}>
            <Clock className="w-5 h-5" style={{ color: ORANGE }} />
          </div>
          <div>
            <h1 className="text-2xl font-bold" style={{ color: NAVY, fontFamily: 'Georgia, "Book Antiqua", serif' }}>Workforce · Time &amp; Pay</h1>
            <p className="text-sm text-[#6B7280]">Time Doctor hours reconciled with payroll · last {t?.months ?? 3} months</p>
          </div>
          <div className="ml-auto flex items-center gap-2">
            {brief?.can_toggle && (
              <Button
                variant={brief.enabled ? 'accent' : 'outline'}
                onClick={toggleBrief}
                disabled={briefSaving}
                title="Turn the employee Daily Brief email on or off"
              >
                <Power className="w-4 h-4 mr-1" /> Daily Brief: {brief.enabled ? 'ON' : 'OFF'}
              </Button>
            )}
            {brief?.can_toggle && (
              <Link href="/hris/tracking-setup"
                className="inline-flex items-center px-3 py-2 text-sm rounded-md border"
                style={{ borderColor: NAVY, color: NAVY }}
                title="Confirm who tracks time and correct Time Doctor matches">
                <Users className="w-4 h-4 mr-1" /> Who tracks
              </Link>
            )}
            <Button variant="outline" onClick={() => load(false)} disabled={loading}>
              <RefreshCw className={`w-4 h-4 mr-1 ${loading ? 'animate-spin' : ''}`} /> Refresh
            </Button>
          </div>
        </div>

        {error && <div className="mb-3 text-sm text-[#B91C1C] flex items-center gap-2"><AlertCircle className="w-4 h-4" />{error}</div>}

        <Card className="mb-4">
          <CardHeader className="pb-2">
            <div className="flex items-center gap-2">
              <Trophy className="w-5 h-5" style={{ color: ORANGE }} />
              <CardTitle style={{ color: NAVY }}>Leaderboard</CardTitle>
              <div className="ml-auto flex gap-1">
                {(['day', 'week', 'month'] as const).map(p => (
                  <Button key={p} size="sm" variant={lbPeriod === p ? 'accent' : 'outline'} onClick={() => loadLb(p)}>
                    {p === 'day' ? 'Daily' : p === 'week' ? 'Weekly' : 'Monthly'}
                  </Button>
                ))}
              </div>
            </div>
          </CardHeader>
          <CardContent>
            {lbLoading && <div className="text-sm text-[#6B7280]">Loading leaderboard…</div>}
            {!lbLoading && lb && lb.top.length > 0 && (
              <>
                <div className="grid grid-cols-2 gap-3 mb-4">
                  <div className="rounded-xl p-3" style={{ background: '#ECFDF5', border: '1px solid #C7EBD9' }}>
                    <div className="text-[11px] uppercase font-semibold" style={{ color: '#059669' }}>🏆 Top performer</div>
                    <div className="font-bold" style={{ color: NAVY }}>{lb.winner?.name}</div>
                    <div className="text-xs text-[#6B7280]">{lb.winner?.hours}h</div>
                  </div>
                  <div className="rounded-xl p-3" style={{ background: '#FEF2F2', border: '1px solid #F3C4C4' }}>
                    <div className="text-[11px] uppercase font-semibold flex items-center gap-1" style={{ color: '#B91C1C' }}><TrendingDown className="w-3.5 h-3.5" />Needs a boost</div>
                    <div className="font-bold" style={{ color: NAVY }}>{lb.lowest?.name}</div>
                    <div className="text-xs text-[#6B7280]">{lb.lowest?.hours}h</div>
                  </div>
                </div>
                {lb.top.map((r, i) => {
                  const max = lb.top[0].hours || 1
                  const w = Math.round(100 * r.hours / max)
                  return (
                    <div key={r.name + i} className="flex items-center gap-2 py-1">
                      <div className="w-5 text-xs text-[#6B7280]">{i + 1}</div>
                      <div className="w-40 text-sm truncate" style={{ color: NAVY }}>{r.name}</div>
                      <div className="flex-1 h-2 rounded" style={{ background: '#E5EAF0' }}>
                        <div className="h-2 rounded" style={{ width: `${w}%`, background: i === 0 ? ORANGE : NAVY }} />
                      </div>
                      <div className="w-12 text-right text-xs font-semibold" style={{ color: NAVY }}>{r.hours}h</div>
                    </div>
                  )
                })}
                <div className="text-xs text-[#9CA3AF] mt-3">{lb.count} staff tracking · {lb.total_hours}h total this {lbPeriod}.</div>
              </>
            )}
            {!lbLoading && lb && lb.top.length === 0 && <div className="text-sm text-[#6B7280]">No tracking data for this period yet.</div>}
          </CardContent>
        </Card>

        {pending.length > 0 && (
          <Card className="mb-4">
            <CardHeader className="pb-2">
              <div className="flex items-center gap-2">
                <AlertTriangle className="w-5 h-5" style={{ color: ORANGE }} />
                <CardTitle style={{ color: NAVY }}>Hour explanations awaiting your review ({pending.length})</CardTitle>
              </div>
            </CardHeader>
            <CardContent>
              {pendingAria && (
                <div className="mb-3 rounded-lg px-3 py-2 text-sm" style={{ background: '#F1F5FF', border: '1px solid #D8E2F5', color: NAVY }}>
                  <span className="font-semibold">💬 Aria&rsquo;s weekly read:</span> <span className="italic">{pendingAria}</span>
                </div>
              )}
              <div className="text-xs text-[#6B7280] mb-3">
                Staff explained a short day (meeting / client visit / other). Approve to count those hours as
                justified, or reject — leave days are verified automatically and never appear here.
                Best done on Mondays as a weekly sign-off.
              </div>
              <div className="space-y-2">
                {pending.map(r => (
                  <div key={r.id} className="rounded-lg border px-3 py-2 flex flex-wrap items-center gap-2"
                       style={{ borderColor: '#EEF0F3' }}>
                    <div className="flex-1 min-w-[220px]">
                      <div className="text-sm font-semibold" style={{ color: NAVY }}>
                        {r.employee} · {r.work_date}
                      </div>
                      <div className="text-xs text-[#6B7280]">
                        {r.tracked}h of {r.required}h · claims {r.justified_hours}h ·{' '}
                        {r.reason === 'external_meeting' ? `external meeting (${r.meeting_minutes ?? '?'} min)`
                          : r.reason === 'client_visit' ? 'client visit' : 'other'}
                        {r.location ? ` · ${r.location}` : ''}
                      </div>
                      {r.justification && <div className="text-xs mt-1 italic" style={{ color: NAVY }}>&ldquo;{r.justification}&rdquo;</div>}
                    </div>
                    <div className="flex gap-2">
                      <Button size="sm" variant="accent" disabled={reviewBusy === r.id}
                              onClick={() => review(r.id, 'approve')}>Approve</Button>
                      <Button size="sm" variant="outline" disabled={reviewBusy === r.id}
                              onClick={() => review(r.id, 'reject')}>Reject</Button>
                    </div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        )}

        {late && late.latecomers.length > 0 && (
          <Card className="mb-4">
            <CardHeader className="pb-2">
              <div className="flex items-center gap-2">
                <Clock className="w-5 h-5" style={{ color: ORANGE }} />
                <CardTitle style={{ color: NAVY }}>Frequent latecomers · last {late.window_days} days</CardTitle>
                <div className="ml-auto text-xs text-[#6B7280]">{late.total_late_days} late starts across {late.count} staff</div>
              </div>
            </CardHeader>
            <CardContent>
              <div className="text-xs text-[#6B7280] mb-2">Days each person&rsquo;s first tracked start was after 08:15 (Botswana time). Aggregates only.</div>
              {late.latecomers.map((r, i) => {
                const max = late.latecomers[0].late_days || 1
                const w = Math.round(100 * r.late_days / max)
                return (
                  <div key={r.name + i} className="flex items-center gap-2 py-1">
                    <div className="w-5 text-xs text-[#6B7280]">{i + 1}</div>
                    <div className="w-44 text-sm truncate" style={{ color: NAVY }}>{r.name}</div>
                    <div className="flex-1 h-2 rounded" style={{ background: '#E5EAF0' }}>
                      <div className="h-2 rounded" style={{ width: `${w}%`, background: i === 0 ? ORANGE : NAVY }} />
                    </div>
                    <div className="w-20 text-right text-xs font-semibold" style={{ color: NAVY }}>{r.late_days} day{r.late_days === 1 ? '' : 's'}</div>
                  </div>
                )
              })}
            </CardContent>
          </Card>
        )}

        {stale && (
          <div className="mb-4 rounded-lg px-4 py-3 text-sm flex items-start gap-2"
               style={{ background: '#FEF2F2', border: '1px solid #FCA5A5', color: '#991B1B' }}>
            <AlertCircle className="w-4 h-4 mt-0.5 shrink-0 text-[#DC2626]" />
            <span>
              <b>Time Doctor feed is not live.</b> These figures are the last pull
              from <b>{feed?.latest_as_of}</b> ({feed?.data_age_days} days ago){feed?.snapshot_count ? <> · {feed.snapshot_count} snapshot{feed.snapshot_count === 1 ? '' : 's'} on file</> : null}.
              Numbers will not change until the daily feed is reconnected.
            </span>
          </div>
        )}

        {t && t.employees_tracking < t.employees && (
          <div className="mb-4 rounded-lg px-4 py-3 text-sm flex items-center gap-2"
               style={{ background: '#FFFBEB', border: '1px solid #FDE68A', color: '#92400E' }}>
            <AlertCircle className="w-4 h-4 text-[#B45309]" />
            <span><b>{t.employees_tracking}</b> of <b>{t.employees}</b> employees are generating Time Doctor data
              ({adoptionPct}% adoption). {t.idle_employees} aren&apos;t tracking — the dashboard fills automatically as they do.</span>
          </div>
        )}

        {/* hero: adoption donut + utilization distribution (replaces the old 3D scene) */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
          <Card><CardHeader className="pb-1"><CardTitle className="text-xs uppercase tracking-wide text-[#6B7280]">Tracking adoption</CardTitle></CardHeader>
            <CardContent className="grid place-items-center py-4"><AdoptionDonut pct={adoptionPct} tracking={t?.employees_tracking ?? 0} total={t?.employees ?? 0} /></CardContent></Card>
          <Card><CardHeader className="pb-1"><CardTitle className="text-xs uppercase tracking-wide text-[#6B7280]">Utilization distribution</CardTitle></CardHeader>
            <CardContent className="py-4"><UtilHistogram rows={rows} avg={t?.avg_utilization_pct ?? null} /></CardContent></Card>
        </div>

        {/* KPI strip */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
          <Tile icon={<Users className="w-3.5 h-3.5" />} label="Tracking" value={`${t?.employees_tracking ?? 0}/${t?.employees ?? 0}`} sub={`${adoptionPct}% adoption`} accent />
          <Tile icon={<Clock className="w-3.5 h-3.5" />} label="Tracked hrs" value={fmtN(t?.total_tracked_hours)} sub={feed?.latest_as_of ? `as of ${feed.latest_as_of}` : `${t?.snapshot_days ?? 0} snapshots`} />
          <Tile icon={<Gauge className="w-3.5 h-3.5" />} label="Avg utilization" value={fmtPct(t?.avg_utilization_pct)} sub="of those tracking" />
          <Tile icon={<Zap className="w-3.5 h-3.5" />} label="Productive hrs" value={fmtN(t?.total_productive_hours)} sub="rated productive" />
        </div>

        {/* EXCEPTION GROUPS — the management report (Manus playbook, non-trackers first) */}
        {ex && (
          <div className="mb-4">
            <div className="flex items-center gap-2 mb-2">
              <h2 className="text-sm font-bold" style={{ color: NAVY }}>Exceptions to action</h2>
              <span className="text-xs text-[#9CA3AF]">
                util &lt;{ex.thresholds.util_critical}% critical · &lt;{ex.thresholds.util_warn}% warning ·
                idle ≥{ex.thresholds.idle_pct}% · unproductive ≥{ex.thresholds.unproductive_pct}%
              </span>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
              {groupOrder.map(k => <ExceptionCard key={k} k={k} rows={ex.groups[k] || []} />)}
            </div>
          </div>
        )}

        {/* Monthly analysis */}
        <Card className="mb-4">
          <CardHeader className="pb-1"><CardTitle className="text-sm">Monthly analysis — tracked hours &amp; productivity</CardTitle></CardHeader>
          <CardContent><MonthlyTrend months={monthly} /></CardContent>
        </Card>

        {/* Payroll vs Time Doctor — by department */}
        <Card className="mb-4">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Payroll vs Time Doctor — by department</CardTitle>
          </CardHeader>
          <CardContent>
            {coverage && (
              <div className="mb-3 rounded-lg px-3 py-2 text-xs flex flex-wrap items-center gap-x-4 gap-y-1"
                   style={{ background: '#F3F4F6', color: '#374151' }}>
                <span><b>{coverage.pct_costed ?? 0}%</b> of trackers costed against payroll</span>
                <span><b style={{ color: '#B45309' }}>{coverage.trackers_without_payroll}</b> tracking with no payroll loaded</span>
                <span><b>{coverage.paid_not_tracking}</b> paid but not tracking</span>
                {coverage.trackers_without_payroll > 0 && (
                  <span className="text-[#9CA3AF]">— missing-entity payroll (ADRG/AIZ/QIH/RSA/VCM) requested from Finance; completes when loaded.</span>
                )}
              </div>
            )}
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-[#6B7280] border-b border-[#F3F4F6]">
                    <th className="py-2 pr-3">Department</th>
                    <th className="py-2 pr-3 text-right">Head</th>
                    <th className="py-2 pr-3 text-right">Tracking</th>
                    <th className="py-2 pr-3 text-right">Tracked h</th>
                    <th className="py-2 pr-3 text-right">Productive h</th>
                    <th className="py-2 pr-3 text-right">Gross paid</th>
                    <th className="py-2 pr-3 text-right">Cost / tracked h</th>
                    <th className="py-2 pr-3 text-right">Utilization</th>
                  </tr>
                </thead>
                <tbody>
                  {dept.map((d: TDDeptRow, i) => (
                    <tr key={i} className="border-b border-[#F3F4F6] last:border-0">
                      <td className="py-1.5 pr-3 font-medium" style={{ color: NAVY }}>{d.department}</td>
                      <td className="py-1.5 pr-3 text-right">{d.headcount}</td>
                      <td className="py-1.5 pr-3 text-right">{d.tracking}</td>
                      <td className="py-1.5 pr-3 text-right tabular-nums">{fmtN(d.tracked_hours)}</td>
                      <td className="py-1.5 pr-3 text-right tabular-nums" style={{ color: '#059669' }}>{fmtN(d.productive_hours)}</td>
                      <td className="py-1.5 pr-3 text-right tabular-nums">{d.gross_paid ? `P ${fmtN(d.gross_paid)}` : '—'}</td>
                      <td className="py-1.5 pr-3 text-right tabular-nums">{d.cost_per_tracked_hour != null ? `P ${fmtN(d.cost_per_tracked_hour)}` : '—'}</td>
                      <td className="py-1.5 pr-3 text-right">{fmtPct(d.utilization_pct)}</td>
                    </tr>
                  ))}
                  {!dept.length && <tr><td colSpan={8} className="py-4 text-center text-[#9CA3AF] text-sm">No data yet.</td></tr>}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>

        {/* Aria insight */}
        <Card className="mb-4">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm flex items-center gap-2">
              <Sparkles className="w-4 h-4" style={{ color: ORANGE }} /> AI insight
              <span className="text-xs font-normal text-[#9CA3AF]">— Aria (anonymised, no names sent)</span>
              {!recon?.insight?.ok && (
                <Button size="sm" variant="outline" className="ml-auto" onClick={loadInsight} disabled={insightLoading}>
                  {insightLoading ? 'Thinking…' : 'Generate'}
                </Button>
              )}
            </CardTitle>
          </CardHeader>
          <CardContent>
            {recon?.insight?.ok ? (
              <div className="text-sm text-[#1F2937] whitespace-pre-line leading-relaxed">{recon.insight.text}</div>
            ) : recon?.insight && !recon.insight.ok ? (
              <div className="text-xs text-[#9CA3AF]">Insight unavailable: {recon.insight.reason}</div>
            ) : (
              <div className="text-xs text-[#9CA3AF]">Click <b>Generate</b> for an anonymised Aria read on adoption, utilization outliers and cost-per-hour.</div>
            )}
          </CardContent>
        </Card>

        {/* reconciliation table */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm flex items-center gap-2">Reconciliation — tracked vs pay
              <input value={q} onChange={e => setQ(e.target.value)} placeholder="Search employee…"
                     className="ml-auto h-8 px-3 border border-[#D1D5DB] rounded text-xs w-56 font-normal" />
            </CardTitle>
          </CardHeader>
          <CardContent>
            {loading ? <p className="text-sm text-[#6B7280]">Loading…</p> : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs text-[#6B7280] border-b border-[#F3F4F6]">
                      <th className="py-2 pr-3">Employee</th>
                      <th className="py-2 pr-3">Dept</th>
                      <th className="py-2 pr-3 text-right">Tracked h</th>
                      <th className="py-2 pr-3 text-right">Productive h</th>
                      <th className="py-2 pr-3 text-right">Idle</th>
                      <th className="py-2 pr-3 text-right">Utilization</th>
                      <th className="py-2 pr-3 text-right">Cost / tracked h</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((r: TDReconRow, i) => (
                      <tr key={i} className="border-b border-[#F3F4F6] last:border-0">
                        <td className="py-1.5 pr-3 font-medium" style={{ color: NAVY }}>
                          <span className="inline-block w-2 h-2 rounded-full mr-2 align-middle"
                                style={{ background: r.is_tracking ? '#10B981' : '#E5E7EB' }} />
                          {r.employee}
                        </td>
                        <td className="py-1.5 pr-3 text-[#6B7280] text-xs">{r.department}</td>
                        <td className="py-1.5 pr-3 text-right tabular-nums">{r.tracked_hours}</td>
                        <td className="py-1.5 pr-3 text-right tabular-nums" style={{ color: '#059669' }}>{r.productive_hours}</td>
                        <td className="py-1.5 pr-3 text-right tabular-nums text-[#9CA3AF]">{fmtPct(r.idle_pct)}</td>
                        <td className="py-1.5 pr-3 text-right">{fmtPct(r.utilization_pct)}</td>
                        <td className="py-1.5 pr-3 text-right tabular-nums">{r.cost_per_tracked_hour != null ? `P ${fmtN(r.cost_per_tracked_hour)}` : '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!shown.length && <p className="text-sm text-[#9CA3AF] py-4 text-center">No employees match.</p>}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
