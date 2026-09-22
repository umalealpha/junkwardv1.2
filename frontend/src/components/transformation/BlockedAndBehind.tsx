'use client'

import type { Theme } from '@/lib/themes'
import type { TransformationBlockedItem, TransformationBehindItem } from '@/lib/api'
import { Building2, Clock, TrendingDown } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { fmtMoney, fmtDate } from './format'

interface Props {
  theme: Theme
  blocked: TransformationBlockedItem[]
  behind: TransformationBehindItem[]
}

export function BlockedAndBehind({ theme, blocked, behind }: Props) {
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <div>
        <h3 className="text-sm font-semibold mb-3 flex items-center gap-2" style={{ color: theme.navy }}>
          <Clock className="w-4 h-4" style={{ color: theme.er }} strokeWidth={1.7} />
          Blocked ({blocked.length})
        </h3>
        {blocked.length === 0 ? (
          <p className="text-sm" style={{ color: theme.t3 }}>Nothing blocked right now.</p>
        ) : (
          <ul className="space-y-2">
            {blocked.map((b) => (
              <li
                key={b.code}
                className="rounded-lg p-3"
                style={{
                  background: b.is_vendor ? theme.inB : theme.erB,
                  border: `1px solid ${b.is_vendor ? theme.inf : theme.er}30`,
                }}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="text-sm font-medium truncate" style={{ color: theme.text }}>{b.title}</div>
                    <div className="text-xs mt-0.5" style={{ color: theme.t2 }}>
                      {b.is_vendor ? 'Waiting on vendor' : 'Waiting on'}: {b.blocked_on || 'not recorded'}
                    </div>
                    <div className="text-xs mt-0.5" style={{ color: theme.t3 }}>
                      Owner: {b.manager_name || '—'}
                    </div>
                  </div>
                  <div className="flex-shrink-0 text-right">
                    {b.is_vendor ? (
                      <Badge variant="info" size="sm">
                        <Building2 className="w-3 h-3 mr-1" strokeWidth={1.7} />
                        Vendor
                      </Badge>
                    ) : (
                      <Badge variant="danger" size="sm">{b.days}d</Badge>
                    )}
                  </div>
                </div>
                <div className="flex items-center justify-between mt-2 text-xs">
                  <span style={{ color: theme.t3 }}>{b.days} day{b.days === 1 ? '' : 's'} waiting</span>
                  <span className="font-medium" style={{ color: theme.navy }}>
                    {fmtMoney(b.annual_saving_bwp)}/yr at stake
                  </span>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div>
        <h3 className="text-sm font-semibold mb-3 flex items-center gap-2" style={{ color: theme.navy }}>
          <TrendingDown className="w-4 h-4" style={{ color: theme.wr }} strokeWidth={1.7} />
          Behind the clock ({behind.length})
        </h3>
        {behind.length === 0 ? (
          <p className="text-sm" style={{ color: theme.t3 }}>Nothing is running behind time right now.</p>
        ) : (
          <ul className="space-y-2">
            {behind.map((b) => (
              <li
                key={b.code}
                className="rounded-lg p-3"
                style={{ background: theme.wrB, border: `1px solid ${theme.wr}30` }}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="text-sm font-medium truncate" style={{ color: theme.text }}>{b.title}</div>
                    <div className="text-xs mt-0.5" style={{ color: theme.t2 }}>
                      {b.department} · {b.manager_name || 'unassigned'}
                    </div>
                  </div>
                  <Badge variant="warning" size="sm">{b.percent}%</Badge>
                </div>
                <div className="text-xs mt-2" style={{ color: theme.t3 }}>
                  Target: {fmtDate(b.target_date)} · Month {b.month}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
