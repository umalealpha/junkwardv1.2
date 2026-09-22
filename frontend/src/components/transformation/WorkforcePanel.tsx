'use client'

import type { Theme } from '@/lib/themes'
import type { TransformationWorkforce } from '@/lib/api'
import { AlertTriangle, Info } from 'lucide-react'
import { fmtDate, fmtMoney, safeNum, safePct } from './format'

export function WorkforcePanel({ theme, workforce }: { theme: Theme; workforce: TransformationWorkforce }) {
  const td = workforce.time_doctor

  return (
    <div className="space-y-5">
      {/* Time Doctor snapshot — never presented as "today" when it isn't. */}
      <div
        className="rounded-lg p-4 flex items-center justify-between flex-wrap gap-3"
        style={{ background: td.stale ? theme.wrB : theme.okB, border: `1px solid ${td.stale ? theme.wr : theme.ok}30` }}
      >
        <div className="flex items-center gap-2">
          {td.stale ? (
            <AlertTriangle className="w-4 h-4 flex-shrink-0" style={{ color: theme.wr }} strokeWidth={1.7} />
          ) : (
            <Info className="w-4 h-4 flex-shrink-0" style={{ color: theme.ok }} strokeWidth={1.7} />
          )}
          <span className="text-sm font-medium" style={{ color: td.stale ? theme.wr : theme.ok }}>
            Time Doctor — as at {fmtDate(td.as_of)}
            {td.stale ? ` (${td.days_old} day${td.days_old === 1 ? '' : 's'} old — not today)` : ''}
          </span>
        </div>
        {td.unmatched_people > 0 && (
          <span className="text-xs" style={{ color: theme.t3 }}>{td.unmatched_people} people unmatched to a department</span>
        )}
      </div>

      <div className="overflow-x-auto -mx-5 px-5">
        <table className="w-full text-sm min-w-[760px]">
          <caption className="sr-only">Per-department attendance, short days and excuse record, last {workforce.window_days} days</caption>
          <thead>
            <tr className="text-xs uppercase tracking-wide" style={{ color: theme.t2 }}>
              <th scope="col" className="text-left py-2">Department</th>
              <th scope="col" className="text-right py-2">Attendance</th>
              <th scope="col" className="text-right py-2">Short days</th>
              <th scope="col" className="text-right py-2">Unexplained days</th>
              <th scope="col" className="text-right py-2">Awaiting manager</th>
              <th scope="col" className="text-right py-2">Leave days</th>
              <th scope="col" className="text-right py-2">Salary at risk</th>
            </tr>
          </thead>
          <tbody>
            {workforce.departments.map((d) => (
              <tr key={d.department} className="border-t" style={{ borderColor: theme.cardBdr }}>
                <td className="py-2.5 font-medium" style={{ color: theme.navy }}>{d.department}</td>
                <td className="py-2.5 text-right tabular-nums" style={{ color: theme.text }}>{safePct(d.attendance_percent)}</td>
                <td className="py-2.5 text-right tabular-nums" style={{ color: theme.text }}>
                  {safeNum(d.short_days)} <span style={{ color: theme.t3 }}>({safePct(d.short_day_rate)})</span>
                </td>
                <td className="py-2.5 text-right tabular-nums" style={{ color: theme.text }}>{safeNum(d.unexplained_days)}</td>
                <td className="py-2.5 text-right tabular-nums" style={{ color: theme.text }}>{safeNum(d.awaiting_manager)}</td>
                <td className="py-2.5 text-right tabular-nums" style={{ color: theme.text }}>{safeNum(d.leave_days, 1)}</td>
                <td className="py-2.5 text-right tabular-nums font-medium" style={{ color: theme.er }}>{fmtMoney(d.salary_at_risk)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {workforce.departments.length === 0 && (
          <p className="text-sm py-4" style={{ color: theme.t3 }}>No workforce record for this window yet.</p>
        )}
      </div>

      {/* The fairness guarantee — must always be visible, not collapsed. */}
      <div className="space-y-2 pt-3 border-t" style={{ borderColor: theme.cardBdr }}>
        <p className="text-xs flex items-start gap-1.5" style={{ color: theme.t2 }}>
          <Info className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" strokeWidth={1.7} />
          {workforce.late_coming_note}
        </p>
        <p className="text-xs flex items-start gap-1.5" style={{ color: theme.t2 }}>
          <Info className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" strokeWidth={1.7} />
          {workforce.fairness_note}
        </p>
      </div>
    </div>
  )
}
