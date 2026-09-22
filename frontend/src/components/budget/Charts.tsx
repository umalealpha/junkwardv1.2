'use client'
/**
 * Charts.tsx — hand-rolled SVG charts for the budget cockpit.
 * Deliberately no chart library: recharts v3 + React 19 renders unreliably
 * (only the first chart mounts; sectors need animation off). Plain SVG always
 * renders, stays light, and matches the brand exactly.
 */
import type { ReactNode } from 'react'

/** Prophix-style ring gauge — arc = value/max, content (big number) centred. */
export function Gauge({ frac, color, size = 112, thickness = 11, children }: {
  frac: number; color: string; size?: number; thickness?: number; children?: ReactNode
}) {
  const r = (size - thickness) / 2, c = size / 2, C = 2 * Math.PI * r
  const f = Math.max(0, Math.min(1, frac))
  return (
    <div style={{ position: 'relative', width: size, height: size }}>
      <svg viewBox={`0 0 ${size} ${size}`} width="100%" height="100%">
        <circle cx={c} cy={c} r={r} fill="none" stroke="rgba(127,127,127,0.16)" strokeWidth={thickness} />
        <g transform={`rotate(-90 ${c} ${c})`}>
          <circle cx={c} cy={c} r={r} fill="none" stroke={color} strokeWidth={thickness} strokeLinecap="round"
                  strokeDasharray={`${f * C} ${C}`} />
        </g>
      </svg>
      <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>{children}</div>
    </div>
  )
}

export interface Slice { label: string; value: number; color: string }

/** Donut — proportions of a whole (e.g. GWP mix). */
export function Donut({ data, size = 176, thickness = 26 }: { data: Slice[]; size?: number; thickness?: number }) {
  const total = data.reduce((s, d) => s + Math.max(0, d.value), 0) || 1
  const r = (size - thickness) / 2
  const c = size / 2
  const C = 2 * Math.PI * r
  let acc = 0
  return (
    <svg viewBox={`0 0 ${size} ${size}`} width="100%" height="100%" role="img"
         aria-label={`Donut chart: ${data.map(d => `${d.label} ${d.value}`).join(', ')}`}>
      <g transform={`rotate(-90 ${c} ${c})`}>
        {data.map((d, i) => {
          const seg = (Math.max(0, d.value) / total) * C
          const el = (
            <circle key={i} cx={c} cy={c} r={r} fill="none" stroke={d.color} strokeWidth={thickness}
                    strokeDasharray={`${seg} ${C - seg}`} strokeDashoffset={-acc}>
              <title>{`${d.label}: ${d.value.toFixed(1)}`}</title>
            </circle>
          )
          acc += seg
          return el
        })}
      </g>
    </svg>
  )
}

/** Legend chips for a donut. */
export function Legend({ data, fmt }: { data: Slice[]; fmt: (v: number) => string }) {
  return (
    <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
      {data.map((d, i) => (
        <div key={i} className="flex items-center gap-1.5 min-w-0">
          <span className="w-2.5 h-2.5 rounded-sm shrink-0" style={{ background: d.color }} />
          <span className="truncate" style={{ opacity: 0.8 }}>{d.label}</span>
          <span className="ml-auto font-mono-nums shrink-0" style={{ opacity: 0.6 }}>{fmt(d.value)}</span>
        </div>
      ))}
    </div>
  )
}

export interface Bar { label: string; value: number; color: string; sub?: string }

/** Horizontal bars — magnitudes by category (diverging-aware via color). */
export function HBars({ data, fmt, labelW = 84, valueColor }: { data: Bar[]; fmt: (v: number) => string; labelW?: number; valueColor?: string }) {
  const max = Math.max(...data.map(d => Math.abs(d.value)), 0.0001)
  return (
    <div className="space-y-1.5">
      {data.map((d, i) => (
        <div key={i} className="flex items-center gap-2 text-xs">
          <div className="shrink-0 truncate text-right" style={{ width: labelW, opacity: 0.75 }}>{d.label}</div>
          <div className="flex-1 h-4 rounded-sm relative" style={{ background: 'rgba(127,127,127,0.10)' }}>
            <div className="h-4 rounded-sm absolute left-0 top-0" style={{ width: `${(Math.abs(d.value) / max) * 100}%`, background: d.color, minWidth: d.value !== 0 ? 2 : 0 }} />
          </div>
          <div className="shrink-0 text-right font-mono-nums" style={{ width: 52, color: valueColor }}>{fmt(d.value)}</div>
        </div>
      ))}
    </div>
  )
}
