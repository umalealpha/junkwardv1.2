'use client'

import type { Theme } from '@/lib/themes'
import type { TransformationStaffCost, TransformationCostOfDelay } from '@/lib/api'
import { ArrowRight } from 'lucide-react'
import { useCountUp } from './useCountUp'
import { fmtMoney, fmtMoneyCompact, safeNum } from './format'

interface Props {
  theme: Theme
  staffCost: TransformationStaffCost
  costOfDelay: TransformationCostOfDelay
  salaryAtRiskMonth: number
  reduceMotion: boolean
}

export function StaffCostBridge({ theme, staffCost, costOfDelay, salaryAtRiskMonth, reduceMotion }: Props) {
  const now = useCountUp(staffCost.now, 1000, !reduceMotion)
  const target = useCountUp(staffCost.target, 1000, !reduceMotion)
  const maxBar = Math.max(staffCost.now, staffCost.target, 1)
  const isReduction = staffCost.saving >= 0

  return (
    <div className="space-y-6">
      <div>
        <div className="flex items-center gap-4 flex-wrap">
          <div className="flex-1 min-w-[180px]">
            <div className="text-xs uppercase tracking-wide" style={{ color: theme.t3 }}>Now — {staffCost.period || 'latest period'}</div>
            <div className="text-2xl font-bold tabular-nums mt-1" style={{ color: theme.navy }}>{fmtMoney(now)}</div>
            <div className="h-2 rounded-full overflow-hidden mt-2" style={{ background: theme.g100 }}>
              <div
                className="tb-fill h-full rounded-full"
                style={{ background: theme.navy, width: `${(staffCost.now / maxBar) * 100}%`, animation: reduceMotion ? 'none' : 'tb-fill 800ms cubic-bezier(0.16,1,0.3,1) both' }}
              />
            </div>
            <div className="text-xs mt-1" style={{ color: theme.t2 }}>{safeNum(staffCost.headcount_now)} people</div>
          </div>

          <ArrowRight className="w-6 h-6 flex-shrink-0 hidden sm:block" style={{ color: theme.t3 }} strokeWidth={1.7} />

          <div className="flex-1 min-w-[180px]">
            <div className="text-xs uppercase tracking-wide" style={{ color: theme.t3 }}>Target</div>
            <div className="text-2xl font-bold tabular-nums mt-1" style={{ color: theme.orangeText }}>{fmtMoney(target)}</div>
            <div className="h-2 rounded-full overflow-hidden mt-2" style={{ background: theme.g100 }}>
              <div
                className="tb-fill h-full rounded-full"
                style={{ background: theme.orange, width: `${(staffCost.target / maxBar) * 100}%`, animation: reduceMotion ? 'none' : 'tb-fill 800ms cubic-bezier(0.16,1,0.3,1) 120ms both' }}
              />
            </div>
            <div className="text-xs mt-1" style={{ color: theme.t2 }}>{safeNum(staffCost.headcount_target)} people</div>
          </div>

          <div className="flex-1 min-w-[160px] text-right">
            <div className="text-xs uppercase tracking-wide" style={{ color: theme.t3 }}>{isReduction ? 'Saving' : 'Increase'}</div>
            <div
              className="text-2xl font-bold tabular-nums mt-1"
              style={{ color: isReduction ? theme.ok : theme.er }}
            >
              {fmtMoney(Math.abs(staffCost.saving))}
            </div>
            <div className="text-xs mt-1" style={{ color: theme.t2 }}>
              {(() => {
                const delta = staffCost.headcount_target - staffCost.headcount_now
                return `${delta > 0 ? '+' : ''}${delta} headcount`
              })()}
            </div>
          </div>
        </div>
        <p className="text-xs mt-4" style={{ color: theme.t3 }}>{staffCost.note}</p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 pt-4 border-t" style={{ borderColor: theme.cardBdr }}>
        <div className="rounded-lg p-3" style={{ background: theme.g50 }}>
          <div className="text-xs" style={{ color: theme.t2 }}>Cost of delay / month</div>
          <div className="text-lg font-bold mt-1" style={{ color: theme.navy }}>{fmtMoneyCompact(costOfDelay.monthly)}</div>
        </div>
        <div className="rounded-lg p-3" style={{ background: theme.g50 }}>
          <div className="text-xs" style={{ color: theme.t2 }}>Cost of delay / year</div>
          <div className="text-lg font-bold mt-1" style={{ color: theme.navy }}>{fmtMoneyCompact(costOfDelay.annual)}</div>
        </div>
        <div className="rounded-lg p-3" style={{ background: theme.g50 }}>
          <div className="text-xs" style={{ color: theme.t2 }}>Salary at risk / month</div>
          <div className="text-lg font-bold mt-1" style={{ color: theme.er }}>{fmtMoneyCompact(salaryAtRiskMonth)}</div>
        </div>
      </div>
      <p className="text-xs" style={{ color: theme.t3 }}>{costOfDelay.basis}</p>
    </div>
  )
}
