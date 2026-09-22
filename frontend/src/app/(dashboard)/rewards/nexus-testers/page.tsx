'use client'

/**
 * /rewards/nexus-testers — STAFF/CFO view of everyone testing Alpha Nexus.
 * Ranked by average Click & Drive score (safest driver = winner), then points.
 * All data comes from the backend (CustomerDriveTrip + HealthMetric + member).
 * Staff SSO only (sits in the (dashboard) group).
 */
import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { apiFetch, getToken } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Trophy, Download, Mail } from 'lucide-react'

interface Row {
  name: string; email: string; tier: string; points: number; trips: number
  totalKm: number; avgDriveScore: number | null; harshPer100km: number | null
  driveBand: string; wellnessScore: number | null; wellnessScans: number
  nexusScore: number; enrolledAt: string | null
}
interface Resp { members: Row[]; count: number; competitionStartLabel?: string }

const NAVY = '#0D1B2A', ORANGE = '#F4A623', TEAL = '#0E9488'

function bandColor(b: string) {
  return b === 'Dangerous' ? '#B91C1C' : b === 'Risky' ? '#C2410C'
    : b === 'Inconsistent' ? ORANGE : b === 'Solid' ? TEAL : b === 'Exemplary' ? '#16a34a' : '#9CA3AF'
}

export default function NexusTesters() {
  const router = useRouter()
  const [data, setData] = useState<Resp | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const [sentMsg, setSentMsg] = useState<string | null>(null)

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    apiFetch<Resp>('/rewards/nexus-leaderboard/').then(setData).catch(e => setError(e instanceof Error ? e.message : 'Failed to load'))
  }, [router])

  const emailStandings = async () => {
    if (!confirm('Email every tester their current standing + prize push?')) return
    setSending(true); setSentMsg(null)
    try {
      const r = await apiFetch<{ sent: number; total: number }>('/rewards/nexus-standings-send/', { method: 'POST' })
      setSentMsg(`Standings emailed to ${r.sent} of ${r.total} testers.`)
    } catch (e) { setSentMsg(e instanceof Error ? e.message : 'Send failed') }
    finally { setSending(false) }
  }

  const exportCsv = () => {
    if (!data) return
    const head = ['Rank', 'Name', 'Email', 'Drive band', 'Avg drive score', 'Trips', 'Total km', 'Harsh/100km', 'Wellness', 'Points', 'Tier']
    const lines = data.members.map((m, i) => [i + 1, m.name, m.email, m.driveBand, m.avgDriveScore ?? '', m.trips, m.totalKm, m.harshPer100km ?? '', m.wellnessScore ?? '', m.points, m.tier].join(','))
    const blob = new Blob([head.join(',') + '\n' + lines.join('\n')], { type: 'text/csv' })
    const url = URL.createObjectURL(blob); const a = document.createElement('a')
    a.href = url; a.download = 'alpha-nexus-testers.csv'; a.click(); URL.revokeObjectURL(url)
  }

  const winner = data?.members[0] || null

  return (
    <div className="min-h-screen" style={{ background: '#F9FAFB' }}>
      <TopBar title="Alpha Nexus — Testers" breadcrumbs={[{ label: 'Rewards' }, { label: 'Nexus Testers' }]} />
      <main className="p-4 md:p-6 max-w-6xl mx-auto">
        {error && <div className="rounded-lg bg-red-50 border border-red-200 text-red-700 text-sm px-4 py-3 mb-4">{error}</div>}

        {winner && (
          <div className="rounded-2xl p-5 mb-5 text-white flex items-center gap-4" style={{ background: NAVY }}>
            <Trophy size={36} style={{ color: ORANGE }} />
            <div className="flex-1">
              <p className="text-white/60 text-xs">Current leader (Nexus Score){data?.competitionStartLabel ? ` · counted from ${data.competitionStartLabel}` : ''}</p>
              <p className="text-xl font-bold">{winner.name}</p>
              <p className="text-white/70 text-sm">{winner.nexusScore.toLocaleString()} Nexus · {winner.trips} trips · {winner.wellnessScans} scans · {winner.avgDriveScore ?? '—'}/100 drive</p>
            </div>
            <div className="flex flex-col gap-2">
              <button onClick={emailStandings} disabled={sending} className="flex items-center gap-2 px-4 py-2 rounded-full text-sm font-semibold disabled:opacity-50" style={{ background: ORANGE, color: NAVY }}><Mail size={16} /> {sending ? 'Sending…' : 'Email standings'}</button>
              <button onClick={exportCsv} className="flex items-center gap-2 px-4 py-2 rounded-full text-sm font-semibold" style={{ background: 'rgba(255,255,255,0.14)', color: '#fff' }}><Download size={16} /> CSV</button>
            </div>
          </div>
        )}
        {sentMsg && <div className="rounded-lg bg-emerald-50 border border-emerald-200 text-emerald-700 text-sm px-4 py-2 mb-4">{sentMsg}</div>}

        <div className="rounded-2xl bg-white border overflow-hidden">
          <table className="w-full text-sm">
            <thead style={{ background: '#F3F4F6' }}>
              <tr className="text-left" style={{ color: '#6B7280' }}>
                {['#', 'Name', 'Nexus', 'Drive band', 'Avg score', 'Trips', 'Scans', 'Total km', 'Harsh/100km', 'Wellness', 'Points', 'Email'].map(h => (
                  <th key={h} className="px-3 py-2 font-semibold whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {!data && <tr><td colSpan={12} className="px-3 py-6 text-center text-gray-400">Loading…</td></tr>}
              {data && data.members.length === 0 && <tr><td colSpan={12} className="px-3 py-6 text-center text-gray-400">No testers yet.</td></tr>}
              {data?.members.map((m, i) => (
                <tr key={i} style={{ borderTop: '1px solid #F3F4F6', background: i === 0 ? '#FFFBEB' : undefined }}>
                  <td className="px-3 py-2 font-bold" style={{ color: i === 0 ? ORANGE : '#9CA3AF' }}>{i + 1}</td>
                  <td className="px-3 py-2 font-medium" style={{ color: NAVY }}>{m.name}{i === 0 ? ' 🏆' : ''}</td>
                  <td className="px-3 py-2 font-bold" style={{ color: ORANGE }}>{m.nexusScore.toLocaleString()}</td>
                  <td className="px-3 py-2"><span className="px-2 py-0.5 rounded-full text-xs font-semibold text-white" style={{ background: bandColor(m.driveBand) }}>{m.driveBand}</span></td>
                  <td className="px-3 py-2 font-bold" style={{ color: NAVY }}>{m.avgDriveScore ?? '—'}</td>
                  <td className="px-3 py-2">{m.trips}</td>
                  <td className="px-3 py-2">{m.wellnessScans}</td>
                  <td className="px-3 py-2">{m.totalKm}</td>
                  {/* null = too small a sample to call a rate; never paint an
                      invented number red next to a "Solid" band. */}
                  <td className="px-3 py-2" style={{ color: (m.harshPer100km ?? 0) >= 3 ? '#B91C1C' : '#374151' }}>{m.harshPer100km ?? '—'}</td>
                  <td className="px-3 py-2">{m.wellnessScore ?? '—'}</td>
                  <td className="px-3 py-2">{m.points.toLocaleString()}</td>
                  <td className="px-3 py-2 text-gray-500">{m.email || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="text-xs text-gray-400 mt-3">
          Ranked by Nexus Score{data?.competitionStartLabel ? <> &mdash; only trips, scans and points dated <b>{data.competitionStartLabel}</b> or later count, so everything from our testing period reads as zero. Nothing was deleted: members keep every reward point they earned.</> : '.'} Demo and app-review logins are hidden. Data is live from the backend.
        </p>
      </main>
    </div>
  )
}
