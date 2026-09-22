'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { getToken, getRewardMembers } from '@/lib/api'
import type { RewardMember } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'

// ── Alpha light skin tokens (shared with nexus/rewards pages) ─────────────────
const NAVY = '#1A1D21', ORANGE = '#4F6BED', MUT = '#6B7280'
const HAIR = '#ECEEF2', CANVAS = '#F7F8FB'
const CARD_SHADOW = '0 1px 2px rgba(13,27,42,.05), 0 10px 28px rgba(13,27,42,.07)'

// Clean sans stack (Professional theme, 2026-09-02): no serif, matches the
// rebuilt Omni screens.
const SERIF = 'Inter, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif'

const TIER_COLOR: Record<string, string> = {
  bronze: '#A97142', silver: '#9CA3AF', gold: '#D4A017', platinum: '#6B7280',
}

const card: React.CSSProperties = {
  background: '#fff', borderRadius: 20, boxShadow: CARD_SHADOW, border: `1px solid ${HAIR}`,
}
const eyebrow: React.CSSProperties = {
  fontSize: 11, letterSpacing: '.12em', textTransform: 'uppercase', color: MUT, fontWeight: 700,
}

const initials = (name: string) =>
  (name || '?')
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() ?? '')
    .join('') || '?'

export default function RewardsLeaderboardPage() {
  const router = useRouter()
  const [members, setMembers] = useState<RewardMember[]>([])
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await getRewardMembers().catch(() => ({ results: [] as RewardMember[] }))
      // getRewardMembers already returns members ordered by points_balance desc;
      // sort defensively so the rank numbering is always correct.
      const ranked = [...r.results].sort((a, b) => b.points_balance - a.points_balance)
      setMembers(ranked)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load()
  }, [load, router])

  const nf = (n: number) => n.toLocaleString()

  return (
    <div className="flex flex-col min-h-screen" style={{ background: CANVAS }}>
      <TopBar title="Leaderboard" breadcrumbs={[{ label: 'Alpha Rewards', href: '/rewards' }, { label: 'Leaderboard' }]} />
      <div className="flex-1 p-6 max-w-3xl mx-auto w-full space-y-4" style={{ color: NAVY }}>
        {/* eyebrow + headline */}
        <div>
          <div style={eyebrow}>Global ranking</div>
          <h1 style={{ fontFamily: SERIF, fontSize: 34, fontWeight: 600, color: NAVY, marginTop: 4, letterSpacing: '-0.01em' }}>Leaderboard</h1>
          <p style={{ fontSize: 13, color: MUT, marginTop: 4 }}>Members ranked by reward points. Drive well and stay claims-free to climb.</p>
        </div>

        {loading && <p className="text-sm" style={{ color: MUT }}>Loading…</p>}

        {!loading && members.length === 0 && (
          <div className="p-10 text-center" style={card}>
            <div className="mx-auto grid place-items-center" style={{ width: 64, height: 64, borderRadius: '50%', background: '#EEF2FC', fontSize: 28, color: ORANGE }}>★</div>
            <h2 style={{ fontFamily: SERIF, fontSize: 22, fontWeight: 600, color: NAVY, marginTop: 14 }}>No members yet</h2>
            <p style={{ fontSize: 13, color: MUT, marginTop: 6, maxWidth: 300, marginLeft: 'auto', marginRight: 'auto' }}>
              Once customers are enrolled in Alpha Rewards they will appear here, ranked by points.
            </p>
          </div>
        )}

        {!loading && members.length > 0 && (
          <div className="space-y-3">
            {members.map((m, i) => {
              const rank = i + 1
              const topThree = rank <= 3
              const tierColor = TIER_COLOR[m.tier] ?? '#6B7280'
              const accent = rank === 1 ? '#D4A017' : rank === 2 ? '#9CA3AF' : rank === 3 ? '#A97142' : 'transparent'
              return (
                <div key={m.id} className="flex items-center gap-4 p-4"
                  style={{ ...card, borderLeft: topThree ? `4px solid ${accent}` : `1px solid ${HAIR}` }}>
                  {/* rank numeral — serif */}
                  <div className="text-center" style={{ minWidth: 34 }}>
                    <div style={{ fontFamily: SERIF, fontSize: topThree ? 26 : 22, fontWeight: 600, color: topThree ? accent : MUT, lineHeight: 1 }}>{rank}</div>
                  </div>
                  {/* initials avatar */}
                  <div className="grid place-items-center flex-none" style={{
                    width: 46, height: 46, borderRadius: '50%',
                    background: CANVAS, border: `1px solid ${HAIR}`,
                    fontSize: 15, fontWeight: 700, color: NAVY,
                  }}>{initials(m.customer_name)}</div>
                  {/* name + tier */}
                  <div className="min-w-0 flex-1">
                    <div className="truncate" style={{ fontSize: 15.5, fontWeight: 600, color: NAVY }}>{m.customer_name || 'Member'}</div>
                    <span className="inline-block mt-1.5 px-2 py-0.5 rounded-full" style={{
                      fontSize: 10.5, fontWeight: 700, letterSpacing: '.04em', textTransform: 'uppercase',
                      color: tierColor, background: `${tierColor}1A`, border: `1px solid ${tierColor}33`,
                    }}>{m.tier_display}</span>
                  </div>
                  {/* points */}
                  <div className="text-right flex-none">
                    <div style={{ fontFamily: SERIF, fontSize: 24, fontWeight: 600, color: ORANGE, lineHeight: 1 }}>{nf(m.points_balance)}</div>
                    <div style={{ fontSize: 10, letterSpacing: '.12em', color: MUT, fontWeight: 700, marginTop: 4 }}>PTS</div>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
