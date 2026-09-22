'use client'

import { useEffect, useState, useCallback } from 'react'
import type { ReactNode } from 'react'
import { useRouter } from 'next/navigation'
import RewardsHero from './RewardsHero'
import {
  getToken, getRewardsSummary, getRewardMembers, getRewardPrograms,
  getRewardPartners, getPointsTransactions, getDrivingScores,
} from '@/lib/api'
import type {
  RewardsSummary, RewardMember, RewardProgram, RewardPartner,
  PointsTransaction, DrivingScore,
} from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Award, Car, Coins, Users, Store, HeartPulse } from 'lucide-react'

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'

const PROGRAM_ICON: Record<string, ReactNode> = {
  premium_payment: <Coins className="w-4 h-4" />,
  claims_free:     <Award className="w-4 h-4" />,
  driving:         <Car className="w-4 h-4" />,
  health:          <HeartPulse className="w-4 h-4" />,
}
const TIER_COLOR: Record<string, string> = {
  bronze: '#A97142', silver: '#9CA3AF', gold: '#D4A017', platinum: '#6B7280',
}
const STATUS_COLOR: Record<string, string> = {
  active: '#059669', pending: '#D97706', future: '#6B7280',
}

export default function RewardsPage() {
  const router = useRouter()
  const [loading, setLoading] = useState(true)
  const [summary, setSummary] = useState<RewardsSummary | null>(null)
  const [members, setMembers] = useState<RewardMember[]>([])
  const [programs, setPrograms] = useState<RewardProgram[]>([])
  const [partners, setPartners] = useState<RewardPartner[]>([])
  const [txns, setTxns] = useState<PointsTransaction[]>([])
  const [scores, setScores] = useState<DrivingScore[]>([])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [s, m, pr, pa, tx, ds] = await Promise.all([
        getRewardsSummary().catch(() => null),
        getRewardMembers().catch(() => ({ results: [] as RewardMember[] })),
        getRewardPrograms().catch(() => ({ results: [] as RewardProgram[] })),
        getRewardPartners().catch(() => ({ results: [] as RewardPartner[] })),
        getPointsTransactions().catch(() => ({ results: [] as PointsTransaction[] })),
        getDrivingScores().catch(() => ({ results: [] as DrivingScore[] })),
      ])
      setSummary(s); setMembers(m.results); setPrograms(pr.results)
      setPartners(pa.results); setTxns(tx.results); setScores(ds.results)
    } finally { setLoading(false) }
  }, [])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load])

  const nf = (n: number) => n.toLocaleString()

  return (
    <div className="flex flex-col min-h-screen" style={{ background: '#F7F8FB' }}>
      <TopBar title="Alpha Rewards" breadcrumbs={[{ label: 'Alpha Rewards' }, { label: 'Dashboard' }]} />
      <div className="flex-1 p-6 space-y-5 max-w-6xl">
        <RewardsHero summary={summary} />

        {loading ? <p className="text-sm text-[#6B7280]">Loading…</p> : (
        <>
          {/* Programs */}
          <Card>
            <CardHeader><CardTitle className="flex items-center gap-2"><Award className="w-4 h-4" style={{ color: ORANGE }} /> Reward programs</CardTitle></CardHeader>
            <CardContent className="p-4 grid grid-cols-1 md:grid-cols-2 gap-3">
              {programs.map(p => (
                <div key={p.id} className="border border-[#E5E7EB] rounded-lg p-3">
                  <div className="flex items-center gap-2 font-medium" style={{ color: NAVY }}>
                    <span style={{ color: ORANGE }}>{PROGRAM_ICON[p.code]}</span>{p.name}
                    <span className={`ml-auto text-[10px] px-2 py-0.5 rounded-full ${p.is_active ? 'bg-[#ECFDF5] text-[#065F46]' : 'bg-[#F3F4F6] text-[#6B7280]'}`}>{p.is_active ? 'Active' : 'Off'}</span>
                  </div>
                  <p className="text-xs text-[#6B7280] mt-1">{p.description}</p>
                </div>
              ))}
              {programs.length === 0 && <p className="text-sm text-[#9CA3AF]">No programs yet.</p>}
            </CardContent>
          </Card>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
            {/* Members */}
            <Card>
              <CardHeader><CardTitle className="flex items-center gap-2"><Users className="w-4 h-4" style={{ color: ORANGE }} /> Members</CardTitle></CardHeader>
              <CardContent className="p-0">
                <table className="w-full text-sm">
                  <thead className="bg-[#F3F4F6] text-xs uppercase text-[#6B7280]">
                    <tr><th className="px-3 py-2 text-left">Member</th><th className="px-3 py-2 text-left">Tier</th><th className="px-3 py-2 text-right">Points</th><th className="px-3 py-2 text-right">Claims-free</th></tr>
                  </thead>
                  <tbody className="divide-y divide-[#E5E7EB]">
                    {members.map(m => (
                      <tr key={m.id}>
                        <td className="px-3 py-2">{m.customer_name}</td>
                        <td className="px-3 py-2"><span className="text-[10px] px-2 py-0.5 rounded-full text-white" style={{ background: TIER_COLOR[m.tier] ?? '#6B7280' }}>{m.tier_display}</span></td>
                        <td className="px-3 py-2 text-right font-mono">{nf(m.points_balance)}</td>
                        <td className="px-3 py-2 text-right">{m.claims_free_months} mo</td>
                      </tr>
                    ))}
                    {members.length === 0 && <tr><td colSpan={4} className="px-3 py-6 text-center text-[#9CA3AF]">No members yet.</td></tr>}
                  </tbody>
                </table>
              </CardContent>
            </Card>

            {/* Partners */}
            <Card>
              <CardHeader><CardTitle className="flex items-center gap-2"><Store className="w-4 h-4" style={{ color: ORANGE }} /> Redemption &amp; data partners</CardTitle></CardHeader>
              <CardContent className="p-4 space-y-2">
                {partners.map(p => (
                  <div key={p.id} className="flex items-center gap-2 text-sm">
                    <span className="font-medium" style={{ color: NAVY }}>{p.name}</span>
                    <span className="text-xs text-[#6B7280]">· {p.kind_display}</span>
                    <span className="ml-auto text-[10px] px-2 py-0.5 rounded-full text-white" style={{ background: STATUS_COLOR[p.status] ?? '#6B7280' }}>{p.status}</span>
                  </div>
                ))}
                {partners.length === 0 && <p className="text-sm text-[#9CA3AF]">No partners yet.</p>}
              </CardContent>
            </Card>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
            {/* Telematics */}
            <Card>
              <CardHeader><CardTitle className="flex items-center gap-2"><Car className="w-4 h-4" style={{ color: ORANGE }} /> Telematics (driving scores)</CardTitle></CardHeader>
              <CardContent className="p-0">
                <table className="w-full text-sm">
                  <thead className="bg-[#F3F4F6] text-xs uppercase text-[#6B7280]">
                    <tr><th className="px-3 py-2 text-left">Member</th><th className="px-3 py-2 text-left">Period</th><th className="px-3 py-2 text-right">Score</th><th className="px-3 py-2 text-right">Points</th></tr>
                  </thead>
                  <tbody className="divide-y divide-[#E5E7EB]">
                    {scores.map(s => (
                      <tr key={s.id}><td className="px-3 py-2">{s.member_name}</td><td className="px-3 py-2">{s.period}</td><td className="px-3 py-2 text-right font-mono">{s.score}</td><td className="px-3 py-2 text-right">+{s.points_awarded}</td></tr>
                    ))}
                    {scores.length === 0 && <tr><td colSpan={4} className="px-3 py-6 text-center text-[#9CA3AF]">No telematics data yet.</td></tr>}
                  </tbody>
                </table>
              </CardContent>
            </Card>

            {/* Ledger */}
            <Card>
              <CardHeader><CardTitle className="flex items-center gap-2"><Coins className="w-4 h-4" style={{ color: ORANGE }} /> Recent points activity</CardTitle></CardHeader>
              <CardContent className="p-0">
                <table className="w-full text-sm">
                  <thead className="bg-[#F3F4F6] text-xs uppercase text-[#6B7280]">
                    <tr><th className="px-3 py-2 text-left">Member</th><th className="px-3 py-2 text-left">Type</th><th className="px-3 py-2 text-right">Points</th></tr>
                  </thead>
                  <tbody className="divide-y divide-[#E5E7EB]">
                    {txns.slice(0, 12).map(t => (
                      <tr key={t.id}><td className="px-3 py-2">{t.member_name}</td><td className="px-3 py-2">{t.kind_display}{t.partner_name ? ` · ${t.partner_name}` : ''}</td><td className={`px-3 py-2 text-right font-mono ${t.points < 0 ? 'text-[#991B1B]' : 'text-[#065F46]'}`}>{t.points > 0 ? '+' : ''}{nf(t.points)}</td></tr>
                    ))}
                    {txns.length === 0 && <tr><td colSpan={3} className="px-3 py-6 text-center text-[#9CA3AF]">No activity yet.</td></tr>}
                  </tbody>
                </table>
              </CardContent>
            </Card>
          </div>
        </>
        )}
      </div>
    </div>
  )
}
