'use client'

/**
 * thrive/ScoreGauge.tsx — Alpha Thrive wellness score gauge + factor breakdown.
 * Wellness only; the bands describe lifestyle wellness, not a medical reading.
 */
import type { ThriveScore } from '@/lib/api'

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'

function bandColor(score: number): string {
  if (score >= 80) return '#16a34a'   // green — excellent
  if (score >= 65) return ORANGE       // orange — good
  if (score >= 50) return '#d97706'    // amber — fair
  return '#dc2626'                     // red — needs attention
}

export function ScoreGauge({ score }: { score: ThriveScore }) {
  const v = Math.max(0, Math.min(100, score.score))
  const color = bandColor(v)
  // Semicircular gauge: 180° sweep, radius 80, centre (100,100).
  const r = 80
  const circumference = Math.PI * r            // half circle
  const dash = (v / 100) * circumference

  return (
    <div className="rounded-2xl p-6" style={{ background: '#fff', border: '1px solid #E5E7EB' }}>
      <div className="flex flex-col items-center">
        <svg viewBox="0 0 200 120" className="w-64 max-w-full" role="img"
             aria-label={`Wellness score ${v} out of 100, ${score.band}`}>
          {/* track */}
          <path d="M 20 100 A 80 80 0 0 1 180 100" fill="none" stroke="#E5E7EB" strokeWidth="16" strokeLinecap="round" />
          {/* value arc */}
          <path d="M 20 100 A 80 80 0 0 1 180 100" fill="none" stroke={color} strokeWidth="16"
                strokeLinecap="round" strokeDasharray={`${dash} ${circumference}`} />
          <text x="100" y="92" textAnchor="middle" fontSize="38" fontWeight="700" fill={NAVY}>{v}</text>
          <text x="100" y="112" textAnchor="middle" fontSize="13" fill="#6B7280">/ 100</text>
        </svg>
        <div className="mt-1 px-4 py-1 rounded-full text-sm font-semibold"
             style={{ background: color, color: '#fff' }}>{score.band}</div>
        <p className="mt-2 text-xs text-gray-500">
          Peer average for your age: <b>{Math.round(score.peer_avg)}</b>
        </p>
      </div>

      <div className="mt-5 space-y-3">
        {score.factors.map(f => (
          <div key={f.key}>
            <div className="flex items-center justify-between text-sm">
              <span className="font-medium" style={{ color: NAVY }}>{f.label}</span>
              <span className="tabular-nums text-gray-600">{f.score}</span>
            </div>
            <div className="mt-1 h-2 rounded-full bg-gray-100 overflow-hidden">
              <div className="h-full rounded-full" style={{ width: `${f.score}%`, background: bandColor(f.score) }} />
            </div>
            <p className="mt-0.5 text-xs text-gray-400">{f.note}</p>
          </div>
        ))}
      </div>
    </div>
  )
}
