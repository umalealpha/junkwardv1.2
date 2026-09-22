'use client'

import { useMemo, useState } from 'react'
import type { Theme } from '@/lib/themes'
import type { TransformationDept } from '@/lib/api'
import { ArrowUpDown } from 'lucide-react'
import { fmtMoney, safePct, safeNum } from './format'

interface Props {
  theme: Theme
  departments: TransformationDept[]
}

type SortKey =
  | 'department' | 'automation_score' | 'adoption_percent' | 'attendance_percent'
  | 'unexplained_days' | 'awaiting_manager' | 'leave_days' | 'salary_at_risk'
  | 'monthly_cost_of_not_using'

const COLUMNS: { key: SortKey; label: string; numeric?: boolean }[] = [
  { key: 'department', label: 'Department' },
  { key: 'automation_score', label: 'Automation score', numeric: true },
  { key: 'adoption_percent', label: 'Adoption', numeric: true },
  { key: 'attendance_percent', label: 'Attendance', numeric: true },
  { key: 'unexplained_days', label: 'Unexplained days', numeric: true },
  { key: 'awaiting_manager', label: 'Awaiting manager', numeric: true },
  { key: 'leave_days', label: 'Leave days', numeric: true },
  { key: 'salary_at_risk', label: 'Salary at risk', numeric: true },
  { key: 'monthly_cost_of_not_using', label: 'Cost of not automating /mo', numeric: true },
]

function scoreColor(theme: Theme, score: number | null): { bg: string; fg: string } {
  // A null is NOT a bad score. `null >= 70` and `null >= 40` are both false,
  // so without this branch an unmeasured department falls through to red —
  // the strongest "failing" signal on the table — handed to exactly the
  // departments we have no evidence about.
  if (score === null || score === undefined) return { bg: theme.g100, fg: theme.t3 }
  if (score >= 70) return { bg: theme.okB, fg: theme.ok }
  if (score >= 40) return { bg: theme.wrB, fg: theme.wr }
  return { bg: theme.erB, fg: theme.er }
}

export function DepartmentsHeatmap({ theme, departments }: Props) {
  const [sortKey, setSortKey] = useState<SortKey>('automation_score')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc')

  const sorted = useMemo(() => {
    const rows = [...departments]
    rows.sort((a, b) => {
      // Nulls sort last in BOTH directions — "not measured" is neither the
      // best nor the worst, so it must never head or tail the ranking.
      const av = a[sortKey as keyof TransformationDept]
      const bv = b[sortKey as keyof TransformationDept]
      if (av === null || av === undefined) return 1
      if (bv === null || bv === undefined) return -1
      // Nulls sort last regardless of direction — "not measured" is not "zero".
      if (av === null || av === undefined) return 1
      if (bv === null || bv === undefined) return -1
      if (typeof av === 'string' || typeof bv === 'string') {
        return String(av).localeCompare(String(bv)) * (sortDir === 'asc' ? 1 : -1)
      }
      return (Number(av) - Number(bv)) * (sortDir === 'asc' ? 1 : -1)
    })
    return rows
  }, [departments, sortKey, sortDir])

  const toggleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(key)
      setSortDir(key === 'department' ? 'asc' : 'desc')
    }
  }

  if (departments.length === 0) {
    return <p className="text-sm" style={{ color: theme.t3 }}>No department data yet.</p>
  }

  // Early in the programme most rows read "—", which looks broken unless we
  // say why. Computed from the data and shown only while it is true — a
  // written date would be a frozen number on a living screen.
  const unscored = departments.filter((d) => d.automation_score === null).length

  return (
    <div className="overflow-x-auto -mx-5 px-5">
      {unscored > 0 && (
        <p className="text-xs mb-3" style={{ color: theme.t3 }}>
          {unscored} of {departments.length} departments not scored yet — a score
          appears once a department&rsquo;s first step is far enough into its window
          to be judged.
        </p>
      )}
      <table className="w-full text-sm min-w-[880px]">
        <caption className="sr-only">
          Departments ranked by automation score, adoption, attendance and cost of not automating
        </caption>
        <thead>
          <tr>
            {COLUMNS.map((col) => (
              <th key={col.key} scope="col" className={col.numeric ? 'text-right' : 'text-left'}>
                <button
                  type="button"
                  onClick={() => toggleSort(col.key)}
                  className="inline-flex items-center gap-1 font-medium text-xs uppercase tracking-wide py-2"
                  style={{ color: theme.t2 }}
                  aria-label={`Sort by ${col.label}`}
                >
                  {col.label}
                  <ArrowUpDown className="w-3 h-3" strokeWidth={1.7} opacity={sortKey === col.key ? 1 : 0.35} />
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((d) => {
            const c = scoreColor(theme, d.automation_score)
            return (
              <tr key={d.department} className="border-t" style={{ borderColor: theme.cardBdr }}>
                <td className="py-2.5 pr-3 font-medium" style={{ color: theme.navy }}>
                  {d.department}
                  {d.manager_name && (
                    <div className="text-xs font-normal" style={{ color: theme.t3 }}>{d.manager_name}</div>
                  )}
                </td>
                <td className="py-2.5 text-right">
                  <span
                    className="inline-flex items-center justify-center min-w-[3rem] px-2 py-0.5 rounded-full font-semibold tabular-nums"
                    style={{ background: c.bg, color: c.fg }}
                    title={d.why_unscored || undefined}
                  >
                    {safeNum(d.automation_score)}
                  </span>
                </td>
                <td className="py-2.5 text-right tabular-nums" style={{ color: theme.text }}>
                  {safePct(d.adoption_percent)}
                </td>
                <td className="py-2.5 text-right tabular-nums" style={{ color: theme.text }}>
                  {safePct(d.attendance_percent)}
                </td>
                <td className="py-2.5 text-right tabular-nums" style={{ color: theme.text }}>
                  {safeNum(d.unexplained_days)}
                </td>
                <td className="py-2.5 text-right tabular-nums" style={{ color: theme.text }}>
                  {safeNum(d.awaiting_manager)}
                </td>
                <td className="py-2.5 text-right tabular-nums" style={{ color: theme.text }}>
                  {safeNum(d.leave_days, 1)}
                </td>
                <td className="py-2.5 text-right tabular-nums" style={{ color: theme.text }}>
                  {fmtMoney(d.salary_at_risk)}
                </td>
                <td
                  className="py-2.5 text-right tabular-nums font-medium"
                  style={{ color: theme.navy }}
                  title={d.cost_measurable === false
                    ? 'Not measurable yet — Omni has no record of a department confirming it uses what we shipped'
                    : undefined}
                >
                  {/* "P 0" here reads as "nobody is wasting anything". Until
                      there is evidence of adoption the honest answer is "—". */}
                  {fmtMoney(d.cost_measurable === false ? null : d.monthly_cost_of_not_using)}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
