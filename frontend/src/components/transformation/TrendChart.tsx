'use client'

import {
  ResponsiveContainer, ComposedChart, Line, XAxis, YAxis, Tooltip, CartesianGrid, Legend,
} from 'recharts'
import type { Theme } from '@/lib/themes'
import type { TransformationHistoryPoint } from '@/lib/api'
import { fmtDate } from './format'

// Real Alpha Direct brand. #0D1B2A/#F4A623 is drift — the design
// system is the source of truth (prat-skill BRAND).
const NAVY = '#0B0B3B'
const ORANGE = '#F07F00'

interface Props {
  theme: Theme
  points: TransformationHistoryPoint[]
  daysTotal: number
}

export function TrendChart({ theme, points, daysTotal }: Props) {
  if (points.length < 2) {
    return (
      <p className="text-sm" style={{ color: theme.t3 }}>
        The trend appears after a few days of pulses — one point isn&apos;t a line yet.
      </p>
    )
  }

  const data = points.map((p) => ({
    date: p.date,
    label: fmtDate(p.date),
    overall_percent: p.overall_percent,
    // Derived from the fixed programme window so the "time used" line reads
    // against the same 20-Sep→20-Jan clock as the hero ring — never invented,
    // just the same days_total the server already computed for the board.
    time_percent: Math.round(Math.max(0, daysTotal - p.days_remaining) * 100 / daysTotal),
  }))

  return (
    <ResponsiveContainer width="100%" height={280}>
      <ComposedChart data={data} margin={{ top: 8, right: 12, left: -12, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={theme.cardBdr} />
        <XAxis dataKey="label" tick={{ fontSize: 11, fill: theme.t3 }} />
        <YAxis
          domain={[0, 100]}
          tickFormatter={(v) => `${v}%`}
          tick={{ fontSize: 11, fill: theme.t3 }}
        />
        <Tooltip formatter={(v, name) => [`${v}%`, name === 'overall_percent' ? 'Work done' : 'Time used']} />
        <Legend
          formatter={(value) => (value === 'overall_percent' ? 'Work done' : 'Time used')}
          wrapperStyle={{ fontSize: 12 }}
        />
        <Line type="monotone" dataKey="time_percent" name="time_percent" stroke={theme.t3} strokeDasharray="4 4" strokeWidth={2} dot={false} />
        <Line type="monotone" dataKey="overall_percent" name="overall_percent" stroke={NAVY} strokeWidth={2.5} dot={{ r: 3, fill: ORANGE }} />
      </ComposedChart>
    </ResponsiveContainer>
  )
}
