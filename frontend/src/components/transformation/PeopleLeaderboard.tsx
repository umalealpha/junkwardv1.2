'use client'

import type { Theme } from '@/lib/themes'
import type { TransformationLeaderEntry } from '@/lib/api'
import { TrendingUp, TrendingDown, Info } from 'lucide-react'

interface Props {
  theme: Theme
  pushing: TransformationLeaderEntry[]
  lagging: TransformationLeaderEntry[]
  unscored: TransformationLeaderEntry[]
}

function PersonRow({ theme, person, scoreColor }: { theme: Theme; person: TransformationLeaderEntry; scoreColor: string }) {
  return (
    <li className="flex items-start justify-between gap-3 py-2.5 border-t first:border-t-0" style={{ borderColor: theme.cardBdr }}>
      <div className="min-w-0">
        <div className="text-sm font-medium" style={{ color: theme.text }}>{person.name}</div>
        <div className="text-xs mt-0.5" style={{ color: theme.t3 }}>{person.department}</div>
        <div className="text-xs mt-1" style={{ color: theme.t2 }}>{person.evidence}</div>
      </div>
      <div
        className="flex-shrink-0 text-sm font-bold tabular-nums w-10 h-10 rounded-full flex items-center justify-center"
        style={{ background: `${scoreColor}1A`, color: scoreColor }}
        aria-label={`Automation score ${person.automation_score}`}
      >
        {person.automation_score}
      </div>
    </li>
  )
}

export function PeopleLeaderboard({ theme, pushing, lagging, unscored }: Props) {
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div>
          <h3 className="text-sm font-semibold mb-1 flex items-center gap-2" style={{ color: theme.navy }}>
            <TrendingUp className="w-4 h-4" style={{ color: theme.ok }} strokeWidth={1.7} />
            Pushing automation ({pushing.length})
          </h3>
          {pushing.length === 0 ? (
            <p className="text-sm" style={{ color: theme.t3 }}>No one scored yet.</p>
          ) : (
            <ul>{pushing.map((p) => <PersonRow key={p.email} theme={theme} person={p} scoreColor={theme.ok} />)}</ul>
          )}
        </div>
        <div>
          <h3 className="text-sm font-semibold mb-1 flex items-center gap-2" style={{ color: theme.navy }}>
            <TrendingDown className="w-4 h-4" style={{ color: theme.er }} strokeWidth={1.7} />
            Lagging ({lagging.length})
          </h3>
          {lagging.length === 0 ? (
            <p className="text-sm" style={{ color: theme.t3 }}>No one scored yet.</p>
          ) : (
            <ul>{lagging.map((p) => <PersonRow key={p.email} theme={theme} person={p} scoreColor={theme.er} />)}</ul>
          )}
        </div>
      </div>

      {/* Unscored — a strictly neutral block. Not enough work assigned, or on
          approved leave, is never a performance signal, so this never borrows
          the lagging list's red/amber styling. */}
      {unscored.length > 0 && (
        <div className="rounded-lg p-4" style={{ background: theme.g50, border: `1px solid ${theme.cardBdr}` }}>
          <h3 className="text-sm font-semibold mb-2 flex items-center gap-2" style={{ color: theme.navy }}>
            <Info className="w-4 h-4" style={{ color: theme.inf }} strokeWidth={1.7} />
            Not scored ({unscored.length}) — not a judgement
          </h3>
          <p className="text-xs mb-3" style={{ color: theme.t2 }}>
            Nobody here is a laggard. Each name is left out of the score for a stated reason —
            not enough assigned work to judge, or on approved leave.
          </p>
          <ul className="grid grid-cols-1 sm:grid-cols-2 gap-x-6">
            {unscored.map((p) => (
              <li key={p.email} className="flex items-center justify-between gap-3 py-1.5 text-sm">
                <span style={{ color: theme.text }}>{p.name}</span>
                <span className="text-xs text-right" style={{ color: theme.t3 }}>{p.why_unscored}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
