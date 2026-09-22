'use client'

// Alpha Rewards summary header — LIGHT skin, ported from the Google Stitch
// rewards_summary screen (2026-06-25). Warm-white card, orange hero figures,
// navy ink, tier ladder. Brand orange standardised to #F4A623.

import { Bell } from 'lucide-react'
import type { RewardsSummary } from '@/lib/api'

const NAVY = '#0D1B2A', ORANGE = '#F4A623', ORANGE_DK = '#9A640A'
const MUT = '#6B7280', HAIR = '#ECEEF2', CANVAS = '#F7F8FB', TEAL = '#3FA7B8'
const CARD_SHADOW = '0 1px 2px rgba(13,27,42,.05), 0 10px 28px rgba(13,27,42,.07)'

const TIERS = [
  { name: 'Bronze', at: 0, color: '#A97142' },
  { name: 'Silver', at: 300, color: '#9CA3AF' },
  { name: 'Gold', at: 600, color: '#D4A017' },
  { name: 'Platinum', at: 1000, color: '#6B7280' },
]

function Tile({ value, label }: { value: string; label: string }) {
  return (
    <div className="rounded-2xl px-4 py-3 text-left" style={{ background: CANVAS, border: `1px solid ${HAIR}` }}>
      <div style={{ fontSize: 26, fontWeight: 600, color: ORANGE, lineHeight: 1 }}>{value}</div>
      <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '.06em', color: MUT, marginTop: 6 }}>{label}</div>
    </div>
  )
}

export default function RewardsHero({ summary }: { summary: RewardsSummary | null }) {
  const nf = (n: number) => n.toLocaleString()
  return (
    <div className="space-y-4">
      {/* live banner */}
      <div className="flex items-center gap-2 rounded-2xl px-4 py-3"
           style={{ background: '#FEF6E7', border: '1px solid #FBE7BF', color: '#6B4A0E', fontSize: 13.5, fontWeight: 500 }}>
        <Bell className="w-4 h-4 flex-shrink-0" style={{ color: ORANGE }} />
        Alpha Rewards is live — drive well, stay claims-free, earn points, redeem with partners.
      </div>

      {/* summary card */}
      <div className="rounded-[20px] p-5" style={{ background: '#fff', border: `1px solid ${HAIR}`, boxShadow: CARD_SHADOW, color: NAVY }}>
        <div className="flex items-center justify-between">
          <div style={{ fontSize: 17, fontWeight: 600 }}>Your rewards summary</div>
          <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '.2em', color: MUT, fontWeight: 700 }}>Project Nexus</div>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-4">
          <Tile value={nf(summary?.points_balance ?? 0)} label="Points in play" />
          <Tile value={nf(summary?.members_total ?? 0)} label="Members" />
          <Tile value={summary?.avg_driving_score != null ? `${summary.avg_driving_score}` : '—'} label="Avg driving score" />
          <Tile value={nf(summary?.points_redeemed ?? 0)} label="Points redeemed" />
        </div>
        <div className="flex gap-3 mt-4">
          <button className="px-5 py-2.5 rounded-full font-semibold" style={{ fontSize: 13.5, background: ORANGE, color: '#3A2A04' }}>Earn more points</button>
          <button className="px-5 py-2.5 rounded-full font-semibold" style={{ fontSize: 13.5, background: '#fff', color: NAVY, border: `1px solid ${NAVY}` }}>Redeem rewards</button>
        </div>
      </div>

      {/* tier ladder */}
      <div className="rounded-[20px] p-5" style={{ background: '#fff', border: `1px solid ${HAIR}`, boxShadow: CARD_SHADOW }}>
        <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '.12em', color: MUT, fontWeight: 700, marginBottom: 12 }}>Membership status</div>
        <div className="flex items-end justify-between">
          {TIERS.map(t => (
            <div key={t.name} className="text-center" style={{ flex: 1 }}>
              <div style={{ width: 14, height: 14, borderRadius: '50%', background: t.color, margin: '0 auto 6px' }} />
              <div style={{ fontSize: 11, color: MUT }}>{t.name}</div>
              <div style={{ fontSize: 13, fontWeight: 600, color: NAVY }}>{t.at.toLocaleString()}</div>
            </div>
          ))}
        </div>
        <div className="rounded-full overflow-hidden" style={{ height: 5, background: '#F0F1F4', marginTop: 10 }}>
          <div style={{ height: '100%', width: '50%', background: TEAL }} />
        </div>
      </div>
    </div>
  )
}
