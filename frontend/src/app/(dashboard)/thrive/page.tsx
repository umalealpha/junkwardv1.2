'use client'

/**
 * /thrive — Alpha Thrive wellness.
 *
 * Consent gate (DPA) → finger-PPG camera scan → Alpha Thrive score gauge +
 * factor breakdown → rewards/premium-discount hook → trend sparkline →
 * AI wellness coach nudge.
 *
 * WELLNESS ONLY, NEVER DIAGNOSIS. Vitals are HR + HRV-stress + respiration,
 * recovered from a fingertip PPG scan. No blood pressure. Camera frames never
 * leave the device — only derived NUMBERS (RR intervals) reach the server.
 * The coach receives ANONYMISED buckets and runs on non-Anthropic engines.
 */
import { useCallback, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  getToken, getRewardMembers, postHealthConsent, postThriveScan,
  getThriveAlphaScore, getThriveRiskTrend, postThriveCoach,
  type RewardMember, type ThriveScore, type ThriveTrend, type ThriveVitals,
} from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { FingerScan } from './FingerScan'
import { ScoreGauge } from './ScoreGauge'
import type { PpgResult } from './ppg'
import { ShieldCheck, HeartPulse, Sparkles, TrendingUp, Award, Activity } from 'lucide-react'

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'

// Tier → premium-discount hook (illustrative; final % is a CFO decision).
const TIER_DISCOUNT: Record<string, number> = { bronze: 0, silver: 5, gold: 10, platinum: 15 }
const NEXT_TIER: Record<string, string> = { bronze: 'Silver', silver: 'Gold', gold: 'Platinum', platinum: 'Platinum' }

function Sparkline({ trend }: { trend: ThriveTrend }) {
  const pts = trend.points
  if (pts.length < 2) return <p className="text-sm text-gray-400">Scan a few days to build your trend.</p>
  const w = 280, h = 60
  const xs = pts.map((_, i) => (i / (pts.length - 1)) * w)
  const ys = pts.map(p => h - (p.score / 100) * (h - 8) - 4)
  const d = xs.map((x, i) => `${i ? 'L' : 'M'} ${x.toFixed(1)} ${ys[i].toFixed(1)}`).join(' ')
  const color = trend.direction === 'declining' ? '#dc2626' : trend.direction === 'improving' ? '#16a34a' : ORANGE
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full" role="img" aria-label={`Trend ${trend.direction}`}>
      <path d={d} fill="none" stroke={color} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
      {xs.map((x, i) => <circle key={i} cx={x} cy={ys[i]} r="2.5" fill={color} />)}
    </svg>
  )
}

export default function ThrivePage() {
  const router = useRouter()
  const [members, setMembers] = useState<RewardMember[]>([])
  const [memberId, setMemberId] = useState<string>('')
  const [consented, setConsented] = useState(false)
  const [consentChecked, setConsentChecked] = useState(false)
  const [vitals, setVitals] = useState<ThriveVitals | null>(null)
  const [score, setScore] = useState<ThriveScore | null>(null)
  const [trend, setTrend] = useState<ThriveTrend | null>(null)
  const [coach, setCoach] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const member = members.find(m => m.id === memberId) || null

  // Load reward members (staff demo: pick whose wellness to view/scan).
  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    getRewardMembers()
      .then(r => {
        const list = r.results || []
        setMembers(list)
        const first = list.find(m => m.is_active) || list[0]
        if (first) setMemberId(first.id)
      })
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load members'))
  }, [router])

  // When a member is selected, pull any existing score + trend.
  const loadMemberData = useCallback(async (id: string) => {
    try {
      const [s, t] = await Promise.all([getThriveAlphaScore(id), getThriveRiskTrend(id)])
      setScore(s.score); setTrend(t)
    } catch { /* a member with no scans yet — fine, leave empty */ }
  }, [])

  useEffect(() => { if (memberId) { setVitals(null); setCoach(null); loadMemberData(memberId) } }, [memberId, loadMemberData])

  const acceptConsent = useCallback(async () => {
    if (!memberId || !consentChecked) return
    setBusy('consent'); setError(null)
    try {
      await postHealthConsent(memberId, ['vitals', 'steps'])
      setConsented(true)
    } catch (e) { setError(e instanceof Error ? e.message : 'Consent failed') }
    finally { setBusy(null) }
  }, [memberId, consentChecked])

  const onScanComplete = useCallback(async (result: PpgResult) => {
    if (!memberId) return
    if (result.beats < 2 || !result.hr) {
      setError('That scan was too noisy. Keep your fingertip still over the lens and try again.')
      return
    }
    setBusy('scan'); setError(null)
    try {
      const res = await postThriveScan({
        memberId,
        rrIntervals: result.ibis,
        respirationRate: result.respiration ?? undefined,
      })
      setVitals(res.vitals); setScore(res.alphaScore)
      // Refresh trend + fetch a coach nudge off the new reading.
      getThriveRiskTrend(memberId).then(setTrend).catch(() => {})
      setBusy('coach')
      const c = await postThriveCoach(memberId)
      setCoach(c.nudge)
    } catch (e) { setError(e instanceof Error ? e.message : 'Scan could not be saved') }
    finally { setBusy(null) }
  }, [memberId])

  const discount = member ? (TIER_DISCOUNT[member.tier] ?? 0) : 0

  return (
    <div className="min-h-screen" style={{ background: '#F9FAFB' }}>
      <TopBar title="Alpha Thrive" breadcrumbs={[{ label: 'Rewards' }, { label: 'Thrive' }]} />
      <main className="p-4 md:p-6 max-w-5xl mx-auto space-y-6">

        {/* Hero */}
        <div className="rounded-2xl p-6 text-white flex items-center gap-4" style={{ background: NAVY }}>
          <HeartPulse size={40} style={{ color: ORANGE }} />
          <div>
            <h1 className="text-2xl font-bold">Alpha Thrive</h1>
            <p className="text-white/70 text-sm">Wellness check-in — earn rewards for looking after yourself. Not a medical diagnosis.</p>
          </div>
        </div>

        {/* Member selector (staff view) */}
        <div className="flex items-center gap-3 flex-wrap">
          <label className="text-sm font-medium" style={{ color: NAVY }}>Member</label>
          <select value={memberId} onChange={e => { setMemberId(e.target.value); setConsented(false); setConsentChecked(false) }}
                  className="border rounded-lg px-3 py-2 text-sm bg-white min-w-[220px]">
            {members.length === 0 && <option value="">No members enrolled</option>}
            {members.map(m => <option key={m.id} value={m.id}>{m.customer_name} · {m.tier_display}</option>)}
          </select>
        </div>

        {error && <div className="rounded-lg bg-red-50 border border-red-200 text-red-700 text-sm px-4 py-3">{error}</div>}

        {/* Consent gate (DPA) */}
        {!consented ? (
          <div className="rounded-2xl bg-white border p-6">
            <div className="flex items-start gap-3">
              <ShieldCheck size={28} style={{ color: ORANGE }} />
              <div className="flex-1">
                <h2 className="font-semibold text-lg" style={{ color: NAVY }}>Your consent (Data Protection Act, 2024)</h2>
                <p className="text-sm text-gray-600 mt-2">
                  Alpha Thrive reads your heartbeat from the camera to estimate heart rate, heart-rate
                  variability (a stress signal) and breathing rate. <b>Only the resulting numbers are stored</b> —
                  the camera images never leave your phone. This data is used for <b>wellness rewards only</b>,
                  never for underwriting, pricing or claims, and you can withdraw any time.
                </p>
                <label className="flex items-center gap-2 mt-4 text-sm" style={{ color: NAVY }}>
                  <input type="checkbox" checked={consentChecked} onChange={e => setConsentChecked(e.target.checked)} />
                  I understand and consent to share my wellness numbers for rewards.
                </label>
                <button onClick={acceptConsent} disabled={!consentChecked || !memberId || busy === 'consent'}
                        className="mt-4 px-6 py-2.5 rounded-full font-semibold disabled:opacity-40"
                        style={{ background: ORANGE, color: NAVY }}>
                  {busy === 'consent' ? 'Saving…' : 'Accept & continue'}
                </button>
              </div>
            </div>
          </div>
        ) : (
          <div className="grid md:grid-cols-2 gap-6">
            {/* Left: scanner + vitals */}
            <div className="space-y-4">
              <FingerScan onComplete={onScanComplete} />
              {busy === 'scan' && <p className="text-sm text-gray-500">Saving your scan…</p>}
              {vitals && (
                <div className="rounded-2xl bg-white border p-4 grid grid-cols-3 gap-3 text-center">
                  <Vital label="Heart rate" value={vitals.restingHr} unit="bpm" icon={<HeartPulse size={18} />} />
                  <Vital label="Stress (HRV)" value={vitals.stressBand || '—'} icon={<Activity size={18} />} text />
                  <Vital label="Breathing" value={vitals.respirationRate} unit="/min" icon={<Sparkles size={18} />} />
                </div>
              )}
            </div>

            {/* Right: score + rewards + trend + coach */}
            <div className="space-y-4">
              {score ? <ScoreGauge score={score} /> : (
                <div className="rounded-2xl bg-white border p-6 text-center text-gray-500 text-sm">
                  Do a 30-second scan to see your Alpha Thrive score.
                </div>
              )}

              {member && (
                <div className="rounded-2xl p-5 text-white" style={{ background: NAVY }}>
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2"><Award size={20} style={{ color: ORANGE }} />
                      <span className="font-semibold">{member.tier_display} · {member.points_balance.toLocaleString()} pts</span></div>
                    <span className="text-2xl font-bold" style={{ color: ORANGE }}>{discount}%</span>
                  </div>
                  <p className="text-white/70 text-sm mt-2">
                    {member.tier === 'platinum'
                      ? 'You are at the top tier — enjoy your maximum premium discount.'
                      : `Your wellness premium discount. Reach ${NEXT_TIER[member.tier]} for a bigger cut.`}
                  </p>
                </div>
              )}

              {trend && trend.points.length > 0 && (
                <div className="rounded-2xl bg-white border p-4">
                  <div className="flex items-center gap-2 mb-2"><TrendingUp size={18} style={{ color: NAVY }} />
                    <span className="font-medium text-sm" style={{ color: NAVY }}>Your trend ({trend.direction})</span></div>
                  <Sparkline trend={trend} />
                </div>
              )}

              {coach && (
                <div className="rounded-2xl p-5" style={{ background: '#FFF7ED', border: `1px solid ${ORANGE}` }}>
                  <div className="flex items-start gap-2">
                    <Sparkles size={20} style={{ color: ORANGE }} />
                    <div><p className="font-semibold text-sm" style={{ color: NAVY }}>Your coach</p>
                      <p className="text-sm text-gray-700 mt-1">{coach}</p></div>
                  </div>
                </div>
              )}
              {busy === 'coach' && <p className="text-sm text-gray-500">Your coach is thinking…</p>}
            </div>
          </div>
        )}
      </main>
    </div>
  )
}

function Vital({ label, value, unit, icon, text }: {
  label: string; value: number | string | null; unit?: string; icon: React.ReactNode; text?: boolean
}) {
  return (
    <div>
      <div className="flex items-center justify-center text-gray-400 mb-1">{icon}</div>
      <div className="text-lg font-bold" style={{ color: NAVY }}>
        {value == null ? '—' : text ? String(value) : value}{!text && value != null && unit ? <span className="text-xs text-gray-400 ml-0.5">{unit}</span> : null}
      </div>
      <div className="text-[11px] text-gray-500">{label}</div>
    </div>
  )
}
