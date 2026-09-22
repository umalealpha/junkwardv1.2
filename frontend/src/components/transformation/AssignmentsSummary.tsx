'use client'

import { useMemo } from 'react'
import type { Theme } from '@/lib/themes'
import type { TransformationInitiative } from '@/lib/api'
import { Users, AlertTriangle, UserX } from 'lucide-react'

// The CFO's ask: "so I can monitor" — the three numbers that answer that
// directly. Computed over EVERY step, not just the visible/filtered ones, so
// the count never quietly drops when someone toggles the customer-service
// filter below it.
export function AssignmentsSummary({ theme, initiatives }: { theme: Theme; initiatives: TransformationInitiative[] }) {
  const stats = useMemo(() => {
    const people = new Set<string>()
    let overdue = 0
    let noOwner = 0
    for (const item of initiatives) {
      const assignments = item.assignments || []
      let hasOwner = false
      for (const a of assignments) {
        people.add(a.email)
        if (a.overdue) overdue += 1
        if (a.is_owner) hasOwner = true
      }
      if (!hasOwner) noOwner += 1
    }
    return { peopleCount: people.size, overdue, noOwner }
  }, [initiatives])

  const tiles = [
    { label: 'People assigned', value: stats.peopleCount, icon: Users, warn: false },
    { label: 'Overdue assignments', value: stats.overdue, icon: AlertTriangle, warn: stats.overdue > 0 },
    { label: 'Steps with no owner', value: stats.noOwner, icon: UserX, warn: stats.noOwner > 0 },
  ]

  return (
    <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-4" role="group" aria-label="Assignment monitoring summary">
      {tiles.map((t) => {
        const Icon = t.icon
        return (
          <div
            key={t.label}
            className="rounded-lg p-3 flex items-center gap-3"
            style={{
              background: t.warn ? theme.erB : theme.g50,
              border: `1px solid ${t.warn ? theme.er + '30' : theme.cardBdr}`,
            }}
          >
            <Icon className="w-5 h-5 flex-shrink-0" style={{ color: t.warn ? theme.er : theme.t2 }} strokeWidth={1.7} />
            <div>
              <div className="text-lg font-bold tabular-nums" style={{ color: t.warn ? theme.er : theme.navy }}>
                {t.value}
              </div>
              <div className="text-xs" style={{ color: t.warn ? theme.er : theme.t2 }}>{t.label}</div>
            </div>
          </div>
        )
      })}
    </div>
  )
}
