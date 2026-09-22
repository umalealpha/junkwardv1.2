'use client'

/**
 * FyPresetChips — one-click ADIC fiscal-period selectors.
 *
 * CFO directive 2026-05-24: clicking a chip must (a) update the date
 * inputs visibly AND (b) trigger the report regenerate. Pages pass an
 * `onApply(from, to)` callback that should both update local state
 * AND fire the fetch synchronously with the passed args — relying on
 * React state would re-render after the fetch and use stale dates.
 *
 * ADIC fiscal year = 1 Jul → 30 Jun.
 *   FY2025  = 2024-07-01 → 2025-06-30
 *   FY2026  = 2025-07-01 → 2026-06-30
 *   FY2026 9M = 2025-07-01 → 2026-03-31 (Mar YTD, matches MA-Mar2026 workbook)
 *   FY-to-date = current FY start → today
 *
 * Pass `mode="as-of"` on Balance Sheet pages — chip will call
 * onApply(from='', to=<end-date>). Default `mode="range"` is for P&L.
 */

import { Calendar } from 'lucide-react'
import { localYmd } from '@/lib/utils'

export interface FyPreset {
  id:     string
  label:  string
  from:   string    // ISO date
  to:     string    // ISO date
}

const todayIso = () => localYmd(new Date())

function currentFyStart(): string {
  const t = new Date()
  const y = t.getMonth() + 1 >= 7 ? t.getFullYear() : t.getFullYear() - 1
  return `${y}-07-01`
}

export const FY_PRESETS: FyPreset[] = [
  { id: 'fy25',     label: 'FY2025',       from: '2024-07-01', to: '2025-06-30' },
  { id: 'fy26',     label: 'FY2026',       from: '2025-07-01', to: '2026-06-30' },
  { id: 'fy26_9m',  label: 'FY26 9M (Mar26)', from: '2025-07-01', to: '2026-03-31' },
  { id: 'fy_ytd',   label: 'FY YTD',       from: currentFyStart(), to: todayIso() },
]

export function FyPresetChips({
  activeFrom, activeTo, onApply, mode = 'range',
}: {
  activeFrom?: string
  activeTo:    string
  onApply:     (from: string, to: string) => void
  mode?:       'range' | 'as-of'
}) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <Calendar className="w-3.5 h-3.5 text-[#6B7280] mr-1" />
      <span className="text-[10px] uppercase tracking-wider font-semibold text-[#6B7280] mr-1">
        Quick periods:
      </span>
      {FY_PRESETS.map(p => {
        const active = mode === 'as-of'
          ? activeTo === p.to
          : activeFrom === p.from && activeTo === p.to
        return (
          <button
            key={p.id}
            type="button"
            onClick={() => onApply(p.from, p.to)}
            className={`px-2.5 py-1 rounded-full text-xs font-semibold transition-colors border ${
              active
                ? 'bg-[#F07F00] text-white border-[#F07F00]'
                : 'bg-white text-[#374151] border-[#D1D5DB] hover:bg-[#F9FAFB]'
            }`}
          >
            {p.label}
          </button>
        )
      })}
    </div>
  )
}
