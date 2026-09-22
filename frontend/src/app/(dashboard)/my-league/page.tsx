'use client'

/**
 * /my-league — staff-facing "My League" (CFO 2026-08-26 v2).
 *
 * Every staff member sees THEIR OWN standing: fair Delivery Score + rank, this
 * month's incentive progress (priority-weighted points toward the bonus), the
 * BWP earned so far (pending CFO + HR approval — Omni never pays), and what they
 * have finished that is still waiting on a manager to confirm. The manager
 * dashboard (/task-dashboard) stays manager-only; this is the view a normal
 * staff member could never see before. Themed via useTheme() so it follows the
 * app theme, never a hardcoded cockpit.
 */

import { useEffect, useState, useCallback } from 'react'
import { apiFetch } from '@/lib/api'
import { useTheme } from '@/contexts/ThemeContext'
import { TopBar } from '@/components/layout/TopBar'
import { Trophy, Coins, Clock, RefreshCw, CheckCircle2, TrendingUp } from 'lucide-react'

interface Me {
  assignee_id: number; assignee_name: string; score: number
  done_14d: number; ontime_rate: number; open: number; overdue: number; blocked: number
}
interface Reward {
  confirmed_month: number; points_month: number; reward_bwp: number
  point_min: number; per_point: number; cap: number; points_to_bonus: number
}
interface Waiting { title: string; waiting_on: string }
interface League {
  as_of: string; window_days: number; me: Me; rank: number | null; players: number
  earns_incentive: boolean; reward: Reward; waiting_on_confirm: Waiting[]
}

export default function MyLeaguePage() {
  const { theme: t, reduceMotion } = useTheme()
  const [data, setData] = useState<League | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true)
    try {
      setData(await apiFetch<League>('/taskboard/my-league/'))
      setError(false)
    } catch {
      setError(true)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const trans = reduceMotion ? 'none' : 'transform .18s cubic-bezier(.16,1,.3,1), opacity .18s'

  const card: React.CSSProperties = {
    background: t.card, border: `1px solid ${t.cardBdr}`, borderRadius: 16,
  }

  return (
    <div style={{ background: t.bg, minHeight: '100vh' }}>
      <TopBar />
      <div className="max-w-3xl mx-auto px-4 py-6">
        {/* Hero */}
        <div className="flex items-center justify-between mb-1">
          <div className="flex items-center gap-2">
            <Trophy size={22} style={{ color: t.orange }} />
            <h1 className="text-2xl font-bold tracking-tight" style={{ color: t.text }}>The Alpha League</h1>
          </div>
          <button onClick={() => load()} className="p-2 rounded-lg" style={{ color: t.t3 }} aria-label="Refresh">
            <RefreshCw size={16} />
          </button>
        </div>
        <p className="text-sm mb-5" style={{ color: t.t3 }}>
          Your standing over the last {data?.window_days ?? 14} days. Finish more, finish harder tasks, finish on time.
        </p>

        {loading && <p className="text-sm" style={{ color: t.t3 }}>Loading your league…</p>}
        {error && !loading && (
          <div style={card} className="p-5" role="alert" aria-live="assertive">
            <p className="text-sm" style={{ color: t.t2 }}>Could not load your league right now. Try refresh.</p>
          </div>
        )}

        {data && !loading && (
          <div className="space-y-4">
            {/* Score + rank */}
            <div style={card} className="p-5">
              <div className="flex items-end justify-between">
                <div>
                  <div className="text-[11px] uppercase tracking-wider mb-1" style={{ color: t.t3 }}>Your score</div>
                  <div className="text-5xl font-bold tabular-nums leading-none" style={{ color: t.navy }}>{data.me.score}</div>
                </div>
                <div className="text-right">
                  {data.rank != null && (
                    <div className="inline-flex items-center gap-1 px-3 py-1 rounded-full text-sm font-semibold"
                      style={{ background: t.orange, color: t.navy }}>
                      #{data.rank} <span className="opacity-70 font-normal">of {data.players}</span>
                    </div>
                  )}
                  <div className="text-xs mt-2" style={{ color: t.t3 }}>
                    {data.me.done_14d} finished · {data.me.ontime_rate}% on time
                  </div>
                </div>
              </div>
              {(data.me.overdue > 0 || data.me.blocked > 0) && (
                <div className="text-xs mt-3" style={{ color: t.t3 }}>
                  {data.me.overdue > 0 && <span>{data.me.overdue} overdue costs a little (capped). </span>}
                  {data.me.blocked > 0 && <span>{data.me.blocked} blocked — flag it so it moves.</span>}
                </div>
              )}
            </div>

            {/* Incentive progress */}
            <div style={card} className="p-5">
              <div className="flex items-center gap-2 mb-3">
                <Coins size={18} style={{ color: t.teal }} />
                <h2 className="font-semibold" style={{ color: t.text }}>This month&apos;s reward</h2>
              </div>

              {!data.earns_incentive ? (
                <p className="text-sm" style={{ color: t.t2 }}>
                  Managers &amp; ExCo don&apos;t earn the task incentive — but your league score still counts. 👑
                </p>
              ) : (
                <>
                  <div className="flex items-baseline justify-between mb-2">
                    <span className="text-3xl font-bold tabular-nums" style={{ color: data.reward.reward_bwp > 0 ? t.teal : t.text }}>
                      P{data.reward.reward_bwp.toLocaleString()}
                    </span>
                    <span className="text-xs" style={{ color: t.t3 }}>
                      {data.reward.confirmed_month} confirmed · {data.reward.points_month} points
                    </span>
                  </div>
                  {/* progress bar to the bonus threshold */}
                  <div className="h-2.5 rounded-full overflow-hidden mb-2" style={{ background: t.cardBdr }}>
                    <div style={{
                      width: `${Math.min(100, Math.round(100 * data.reward.points_month / data.reward.point_min))}%`,
                      height: '100%', background: data.reward.reward_bwp > 0 ? t.teal : t.orange,
                      transition: trans, borderRadius: 999,
                    }} />
                  </div>
                  {data.reward.points_to_bonus > 0 ? (
                    <p className="text-sm" style={{ color: t.t2 }}>
                      <TrendingUp size={13} className="inline mb-0.5 mr-1" style={{ color: t.orange }} />
                      {data.reward.points_to_bonus} points to your first bonus — about {Math.ceil(data.reward.points_to_bonus / 2)} more normal tasks (harder tasks get you there faster).
                    </p>
                  ) : data.reward.points_month <= data.reward.point_min ? (
                    // Exactly on the line: the reward is still zero — the first
                    // P{per_point} is earned by the NEXT point (Manus QC 2026-08-27:
                    // "past the line" here wrongly implied money already earned).
                    <p className="text-sm" style={{ color: t.t2 }}>
                      <TrendingUp size={13} className="inline mb-0.5 mr-1" style={{ color: t.orange }} />
                      Threshold reached — your first P{data.reward.per_point} starts at {data.reward.point_min + 1} points.
                    </p>
                  ) : (
                    <p className="text-sm" style={{ color: t.t2 }}>
                      You&apos;re past the {data.reward.point_min}-point line — every extra point earns P{data.reward.per_point}
                      {data.reward.reward_bwp >= data.reward.cap ? ` (capped at P${data.reward.cap.toLocaleString()} this month).` : '.'}
                    </p>
                  )}
                  <p className="text-[11px] mt-2" style={{ color: t.t3 }}>
                    A normal task = 2 points = P{data.reward.per_point * 2}. Shown pending CFO + HR approval — paid via the bank, not by Omni.
                  </p>
                </>
              )}
            </div>

            {/* Waiting on a manager to confirm */}
            <div style={card} className="p-5">
              <div className="flex items-center gap-2 mb-3">
                <Clock size={18} style={{ color: t.orange }} />
                <h2 className="font-semibold" style={{ color: t.text }}>Waiting on a manager to confirm</h2>
              </div>
              {data.waiting_on_confirm.length === 0 ? (
                <p className="text-sm flex items-center gap-2" style={{ color: t.t2 }}>
                  <CheckCircle2 size={15} style={{ color: t.teal }} /> Nothing waiting — all your finished work is confirmed. 🎉
                </p>
              ) : (
                <>
                  <p className="text-xs mb-3" style={{ color: t.t3 }}>
                    Finished, but a manager hasn&apos;t confirmed yet — so it doesn&apos;t count toward your reward until they do.
                  </p>
                  <ul className="space-y-2">
                    {data.waiting_on_confirm.map((w, i) => (
                      <li key={i} className="flex items-center justify-between text-sm rounded-lg px-3 py-2"
                        style={{ background: t.bg, border: `1px solid ${t.cardBdr}` }}>
                        <span className="truncate mr-3" style={{ color: t.text }}>{w.title}</span>
                        <span className="text-xs whitespace-nowrap" style={{ color: t.t3 }}>waiting on {w.waiting_on}</span>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
