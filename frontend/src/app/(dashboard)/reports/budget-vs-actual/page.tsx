'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { getBudgetVsActual, getToken } from '@/lib/api'
import type { BudgetVsActualReport } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
// Card components handled inline
import { Button } from '@/components/ui/button'
import { LoadingTable } from '@/components/ui/loading'
import { formatAmount, parseAmount, exportToCsv, getFyStart, today, cn } from '@/lib/utils'
import { useTheme } from '@/contexts/ThemeContext'
import { useNumberFormat } from '@/contexts/NumberFormatContext'
import {
  Download,
  RefreshCw,
  TrendingUp,
  TrendingDown,
  AlertCircle,
  BarChart3,
  CheckCircle,
  MinusCircle,
} from 'lucide-react'

// ─── Constants ───────────────────────────────────────────────────────────────

const DEPARTMENTS = [
  { value: '', label: 'All / Master' },
  { value: 'FINANCE', label: 'Finance' },
  { value: 'CLAIMS', label: 'Claims' },
  { value: 'UNDERWRITING', label: 'Underwriting' },
  { value: 'HEALTH', label: 'Health' },
  { value: 'BD', label: 'Business Development' },
  { value: 'OPERATIONS', label: 'Operations' },
  { value: 'IT', label: 'IT' },
]

// ─── Account Row Type ────────────────────────────────────────────────────────

interface BvaAccount {
  code: string
  name: string
  account_type: string
  sub_type: string
  budget: string
  actual: string
  variance: string
  variance_pct: string
  favorable: boolean
}

// ─── Variance Bar ────────────────────────────────────────────────────────────

function VarianceBar({
  pct,
  favorable,
  theme,
}: {
  pct: number
  favorable: boolean
  theme: any
}) {
  const clampedPct = Math.min(Math.abs(pct), 100)
  return (
    <div
      className="w-16 h-1.5 rounded-full overflow-hidden"
      style={{ background: theme.g200 }}
    >
      <div
        className="h-full rounded-full transition-all"
        style={{
          width: `${clampedPct}%`,
          background: favorable ? theme.ok : theme.er,
        }}
      />
    </div>
  )
}

// ─── Summary Card ────────────────────────────────────────────────────────────

function SummaryCard({
  title,
  budget,
  actual,
  variance,
  favorable,
  icon: Icon,
  theme,
}: {
  title: string
  budget: string
  actual: string
  variance: string
  favorable: boolean
  icon: React.ComponentType<any>
  theme: any
}) {
  const { mode } = useNumberFormat()
  const fmt = (amount: string | number, currency = 'BWP') => formatAmount(amount, currency, mode)
  const varianceNum = parseAmount(variance)
  const varianceColor = favorable ? theme.ok : theme.er
  const varianceBg = favorable ? theme.okB : theme.erB

  return (
    <div
      className="rounded-xl p-5"
      style={{
        background: theme.card,
        border: `1px solid ${theme.cardBdr}`,
        boxShadow: theme.cardSh,
      }}
    >
      <div className="flex items-center gap-3 mb-4">
        <div
          className="w-9 h-9 rounded-lg flex items-center justify-center"
          style={{ background: theme.oL }}
        >
          <Icon className="w-4.5 h-4.5" style={{ color: theme.orange }} strokeWidth={1.5} />
        </div>
        <h3 className="text-sm font-semibold" style={{ color: theme.text }}>
          {title}
        </h3>
      </div>

      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-xs" style={{ color: theme.t3 }}>Budget</span>
          <span className="text-sm font-mono-nums font-medium" style={{ color: theme.t2 }}>
            {fmt(budget)}
          </span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-xs" style={{ color: theme.t3 }}>Actual</span>
          <span className="text-sm font-mono-nums font-semibold" style={{ color: theme.text }}>
            {fmt(actual)}
          </span>
        </div>
        <div
          className="mt-3 pt-3 flex items-center justify-between"
          style={{ borderTop: `1px solid ${theme.g200}` }}
        >
          <span className="text-xs font-medium" style={{ color: theme.t2 }}>Variance</span>
          <span
            className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-bold font-mono-nums"
            style={{ background: varianceBg, color: varianceColor }}
          >
            {favorable ? (
              <TrendingUp className="w-3 h-3" />
            ) : varianceNum === 0 ? (
              <MinusCircle className="w-3 h-3" />
            ) : (
              <TrendingDown className="w-3 h-3" />
            )}
            {fmt(variance)}
          </span>
        </div>
      </div>
    </div>
  )
}

// ─── Account Table ───────────────────────────────────────────────────────────

function AccountTable({
  title,
  accounts,
  totalBudget,
  totalActual,
  totalVariance,
  theme,
}: {
  title: string
  accounts: BvaAccount[]
  totalBudget: string
  totalActual: string
  totalVariance: string
  theme: any
}) {
  const { mode } = useNumberFormat()
  const fmt = (amount: string | number, currency = 'BWP') => formatAmount(amount, currency, mode)
  return (
    <div
      className="rounded-xl overflow-hidden"
      style={{
        background: theme.card,
        border: `1px solid ${theme.cardBdr}`,
        boxShadow: theme.cardSh,
      }}
    >
      <div
        className="px-5 py-4"
        style={{ borderBottom: `1px solid ${theme.g200}` }}
      >
        <h3 className="text-sm font-semibold" style={{ color: theme.text }}>
          {title}
        </h3>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr style={{ background: theme.g100 }}>
              <th
                className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider"
                style={{ color: theme.t2 }}
              >
                Code
              </th>
              <th
                className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider"
                style={{ color: theme.t2 }}
              >
                Account Name
              </th>
              <th
                className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wider"
                style={{ color: theme.t2 }}
              >
                Budget
              </th>
              <th
                className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wider"
                style={{ color: theme.t2 }}
              >
                Actual
              </th>
              <th
                className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wider"
                style={{ color: theme.t2 }}
              >
                Variance
              </th>
              <th
                className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wider"
                style={{ color: theme.t2 }}
              >
                Var %
              </th>
              <th
                className="px-4 py-3 text-center text-xs font-semibold uppercase tracking-wider"
                style={{ color: theme.t2 }}
              >
                Status
              </th>
            </tr>
          </thead>
          <tbody>
            {accounts.length === 0 ? (
              <tr>
                <td
                  colSpan={7}
                  className="px-4 py-8 text-center text-sm"
                  style={{ color: theme.t3 }}
                >
                  No budgeted accounts in this section
                </td>
              </tr>
            ) : (
              accounts.map((acc, idx) => {
                const varianceColor = acc.favorable ? theme.ok : theme.er
                const pct = parseAmount(acc.variance_pct)
                return (
                  <tr
                    key={idx}
                    className="transition-colors"
                    style={{ borderBottom: `1px solid ${theme.g200}` }}
                    onMouseEnter={e => (e.currentTarget.style.background = theme.oL)}
                    onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                  >
                    <td
                      className="px-4 py-2.5 font-mono text-xs"
                      style={{ color: theme.t3 }}
                    >
                      {acc.code}
                    </td>
                    <td
                      className="px-4 py-2.5 font-medium"
                      style={{ color: theme.text }}
                    >
                      {acc.name}
                    </td>
                    <td
                      className="px-4 py-2.5 text-right font-mono-nums text-xs"
                      style={{ color: theme.t2 }}
                    >
                      {parseAmount(acc.budget) !== 0 ? fmt(acc.budget) : '\u2014'}
                    </td>
                    <td
                      className="px-4 py-2.5 text-right font-mono-nums font-medium"
                      style={{ color: theme.text }}
                    >
                      {parseAmount(acc.actual) !== 0 ? fmt(acc.actual) : '\u2014'}
                    </td>
                    <td
                      className="px-4 py-2.5 text-right font-mono-nums font-semibold"
                      style={{ color: varianceColor }}
                    >
                      {fmt(acc.variance)}
                    </td>
                    <td className="px-4 py-2.5 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <VarianceBar pct={pct} favorable={acc.favorable} theme={theme} />
                        <span
                          className="text-xs font-mono-nums font-medium w-12 text-right"
                          style={{ color: varianceColor }}
                        >
                          {pct !== 0 ? `${pct.toFixed(1)}%` : '\u2014'}
                        </span>
                      </div>
                    </td>
                    <td className="px-4 py-2.5 text-center">
                      {acc.favorable ? (
                        <CheckCircle className="w-4 h-4 mx-auto" style={{ color: theme.ok }} />
                      ) : parseAmount(acc.variance) === 0 ? (
                        <MinusCircle className="w-4 h-4 mx-auto" style={{ color: theme.t3 }} />
                      ) : (
                        <AlertCircle className="w-4 h-4 mx-auto" style={{ color: theme.er }} />
                      )}
                    </td>
                  </tr>
                )
              })
            )}
          </tbody>
          {accounts.length > 0 && (
            <tfoot>
              <tr style={{ background: theme.g100, borderTop: `2px solid ${theme.g200}` }}>
                <td
                  colSpan={2}
                  className="px-4 py-3 text-xs font-bold uppercase tracking-wider"
                  style={{ color: theme.t2 }}
                >
                  Total {title}
                </td>
                <td
                  className="px-4 py-3 text-right font-semibold font-mono-nums"
                  style={{ color: theme.t2 }}
                >
                  {fmt(totalBudget)}
                </td>
                <td
                  className="px-4 py-3 text-right font-semibold font-mono-nums"
                  style={{ color: theme.text }}
                >
                  {fmt(totalActual)}
                </td>
                <td
                  className="px-4 py-3 text-right font-bold font-mono-nums"
                  style={{
                    color: parseAmount(totalVariance) >= 0 ? theme.ok : theme.er,
                  }}
                >
                  {fmt(totalVariance)}
                </td>
                <td colSpan={2} />
              </tr>
            </tfoot>
          )}
        </table>
      </div>
    </div>
  )
}

// ─── Budget vs Actual Page ───────────────────────────────────────────────────

export default function BudgetVsActualPage() {
  const router = useRouter()
  const { theme } = useTheme()
  const { mode } = useNumberFormat()
  const fmt = (amount: string | number, currency = 'BWP') => formatAmount(amount, currency, mode)
  const [fromDate, setFromDate] = useState(getFyStart())
  const [toDate, setToDate] = useState(today())
  const [department, setDepartment] = useState('')
  const [report, setReport] = useState<BudgetVsActualReport | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const token = getToken()
    if (!token) {
      router.replace('/login')
      return
    }
    handleGenerate()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const runReport = async (from: string, to: string, dept: string) => {
    setLoading(true)
    setError(null)
    try {
      const data = await getBudgetVsActual(from, to, dept || undefined)
      setReport(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to generate Budget vs Actual report')
    } finally {
      setLoading(false)
    }
  }

  const handleGenerate = () => runReport(fromDate, toDate, department)

  // FY-aligned reporting periods (Bug 6ed2e50c — Oprah). Alpha Direct FY = Jul–Jun,
  // so budgets compare on the same calendar as the management accounts (monthly)
  // and the board pack (quarterly). Presets snap from/to to those boundaries.
  const periodPresets = useMemo(() => {
    const n = new Date()
    const y = n.getFullYear()
    const m = n.getMonth() + 1
    const fySY = m >= 7 ? y : y - 1            // current FY starts 1 Jul fySY, ends 30 Jun fySY+1
    const d = (yy: number, mm: number, dd: number) =>
      `${yy}-${String(mm).padStart(2, '0')}-${String(dd).padStart(2, '0')}`
    const fy = (sy: number) => ({ label: `FY${sy + 1}`, from: d(sy, 7, 1), to: d(sy + 1, 6, 30) })
    // Q1 Jul–Sep, Q2 Oct–Dec (calendar year sy); Q3 Jan–Mar, Q4 Apr–Jun (year sy+1)
    const quarters: Array<[number, number, number, number, number]> = [
      [7, 1, 9, 30, 0], [10, 1, 12, 31, 0], [1, 1, 3, 31, 1], [4, 1, 6, 30, 1],
    ]
    const q = (sy: number, qi: number) => {
      const [sm, sd, em, ed, off] = quarters[qi]
      return { label: `Q${qi + 1} FY${sy + 1}`, from: d(sy + off, sm, sd), to: d(sy + off, em, ed) }
    }
    const lastDay = new Date(y, m, 0).getDate()
    return [
      fy(fySY),
      { label: 'YTD', from: d(fySY, 7, 1), to: today() },
      q(fySY, 0), q(fySY, 1), q(fySY, 2), q(fySY, 3),
      { label: 'This month', from: d(y, m, 1), to: d(y, m, lastDay) },
      fy(fySY - 1),
    ]
  }, [])

  const applyPreset = (p: { from: string; to: string }) => {
    setFromDate(p.from)
    setToDate(p.to)
    runReport(p.from, p.to, department)
  }

  const handleExport = () => {
    if (!report) return
    const rows: Record<string, any>[] = []

    // Revenue section
    rows.push({ Section: 'REVENUE', Code: '', Account: '', Budget: '', Actual: '', Variance: '', 'Variance %': '', Favorable: '' })
    report.revenue.accounts.forEach((acc) =>
      rows.push({
        Section: 'Revenue',
        Code: acc.code,
        Account: acc.name,
        Budget: acc.budget,
        Actual: acc.actual,
        Variance: acc.variance,
        'Variance %': acc.variance_pct,
        Favorable: acc.favorable ? 'Yes' : 'No',
      })
    )
    rows.push({
      Section: 'Revenue Total',
      Code: '',
      Account: '',
      Budget: report.revenue.total_budget,
      Actual: report.revenue.total_actual,
      Variance: report.revenue.total_variance,
      'Variance %': '',
      Favorable: '',
    })

    // Expenses section
    rows.push({ Section: 'EXPENSES', Code: '', Account: '', Budget: '', Actual: '', Variance: '', 'Variance %': '', Favorable: '' })
    report.expenses.accounts.forEach((acc) =>
      rows.push({
        Section: 'Expenses',
        Code: acc.code,
        Account: acc.name,
        Budget: acc.budget,
        Actual: acc.actual,
        Variance: acc.variance,
        'Variance %': acc.variance_pct,
        Favorable: acc.favorable ? 'Yes' : 'No',
      })
    )
    rows.push({
      Section: 'Expenses Total',
      Code: '',
      Account: '',
      Budget: report.expenses.total_budget,
      Actual: report.expenses.total_actual,
      Variance: report.expenses.total_variance,
      'Variance %': '',
      Favorable: '',
    })

    // Net result
    rows.push({
      Section: 'NET RESULT',
      Code: '',
      Account: '',
      Budget: report.net_result.budget,
      Actual: report.net_result.actual,
      Variance: report.net_result.variance,
      'Variance %': '',
      Favorable: report.net_result.favorable ? 'Yes' : 'No',
    })

    const deptLabel = department
      ? DEPARTMENTS.find((d) => d.value === department)?.label || department
      : 'All'
    exportToCsv(rows, `budget_vs_actual_${deptLabel}_${fromDate}_${toDate}`)
  }

  // Net result helpers
  const netFavorable = report?.net_result.favorable ?? true

  return (
    <div className="flex flex-col min-h-screen" style={{ background: theme.bg }}>
      <TopBar
        title="Budget vs Actual"
        breadcrumbs={[{ label: 'Reports', href: '/reports' }, { label: 'Budget vs Actual' }]}
        actions={
          <div className="flex items-center gap-2">
            <Link href="/reports/budget-setup">
              <Button variant="primary" size="sm">
                Set up budget
              </Button>
            </Link>
            {report && (
              <Button
                variant="secondary"
                size="sm"
                leftIcon={<Download className="w-3.5 h-3.5" />}
                onClick={handleExport}
              >
                Export CSV
              </Button>
            )}
          </div>
        }
      />

      <div className="flex-1 p-6 space-y-6">
        {/* Controls */}
        <div
          className="rounded-xl p-4"
          style={{
            background: theme.card,
            border: `1px solid ${theme.cardBdr}`,
            boxShadow: theme.cardSh,
          }}
        >
          {/* FY-aligned reporting periods (Bug 6ed2e50c) — Alpha Direct FY = Jul–Jun */}
          <div className="mb-4">
            <label className="block text-xs font-medium mb-1.5" style={{ color: theme.t2 }}>
              Reporting period — FY runs 1 Jul → 30 Jun (monthly = MA · quarterly = Board)
            </label>
            <div className="flex flex-wrap gap-2">
              {periodPresets.map((p) => {
                const active = p.from === fromDate && p.to === toDate
                return (
                  <button
                    key={p.label}
                    type="button"
                    onClick={() => applyPreset(p)}
                    className="px-2.5 py-1 rounded-lg text-xs font-semibold transition-all"
                    style={{
                      background: active ? theme.orange : theme.g100,
                      color: active ? '#fff' : theme.text,
                      border: `1px solid ${active ? theme.orange : theme.g200}`,
                    }}
                  >
                    {p.label}
                  </button>
                )
              })}
            </div>
          </div>
          <div className="flex flex-wrap items-end gap-4">
            <div>
              <label
                className="block text-xs font-medium mb-1"
                style={{ color: theme.t2 }}
              >
                From Date
              </label>
              <input
                type="date"
                value={fromDate}
                onChange={(e) => setFromDate(e.target.value)}
                className="rounded-lg px-3 py-2 text-sm focus:outline-none transition-all"
                style={{
                  background: theme.card,
                  border: `1px solid ${theme.g200}`,
                  color: theme.text,
                }}
                onFocus={e => {
                  e.currentTarget.style.borderColor = theme.orange
                  e.currentTarget.style.boxShadow = `0 0 0 3px ${theme.orange}1A`
                }}
                onBlur={e => {
                  e.currentTarget.style.borderColor = theme.g200
                  e.currentTarget.style.boxShadow = 'none'
                }}
              />
            </div>
            <div>
              <label
                className="block text-xs font-medium mb-1"
                style={{ color: theme.t2 }}
              >
                To Date
              </label>
              <input
                type="date"
                value={toDate}
                onChange={(e) => setToDate(e.target.value)}
                className="rounded-lg px-3 py-2 text-sm focus:outline-none transition-all"
                style={{
                  background: theme.card,
                  border: `1px solid ${theme.g200}`,
                  color: theme.text,
                }}
                onFocus={e => {
                  e.currentTarget.style.borderColor = theme.orange
                  e.currentTarget.style.boxShadow = `0 0 0 3px ${theme.orange}1A`
                }}
                onBlur={e => {
                  e.currentTarget.style.borderColor = theme.g200
                  e.currentTarget.style.boxShadow = 'none'
                }}
              />
            </div>
            <div>
              <label
                className="block text-xs font-medium mb-1"
                style={{ color: theme.t2 }}
              >
                Department
              </label>
              <select
                value={department}
                onChange={(e) => setDepartment(e.target.value)}
                className="rounded-lg px-3 py-2 text-sm focus:outline-none transition-all cursor-pointer"
                style={{
                  background: theme.card,
                  border: `1px solid ${theme.g200}`,
                  color: theme.text,
                  minWidth: 160,
                }}
                onFocus={e => {
                  e.currentTarget.style.borderColor = theme.orange
                  e.currentTarget.style.boxShadow = `0 0 0 3px ${theme.orange}1A`
                }}
                onBlur={e => {
                  e.currentTarget.style.borderColor = theme.g200
                  e.currentTarget.style.boxShadow = 'none'
                }}
              >
                {DEPARTMENTS.map((dept) => (
                  <option key={dept.value} value={dept.value}>
                    {dept.label}
                  </option>
                ))}
              </select>
            </div>
            <Button variant="primary" size="md" onClick={handleGenerate} loading={loading}>
              <RefreshCw className={cn('w-4 h-4 mr-1.5', loading && 'animate-spin')} />
              Generate
            </Button>

            {report && (
              <div
                className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium"
                style={{
                  background: netFavorable ? theme.okB : theme.erB,
                  color: netFavorable ? theme.ok : theme.er,
                  border: `1px solid ${netFavorable ? theme.ok : theme.er}30`,
                }}
              >
                {netFavorable ? (
                  <TrendingUp className="w-4 h-4" />
                ) : (
                  <TrendingDown className="w-4 h-4" />
                )}
                Net: {fmt(report.net_result.actual)} ({netFavorable ? 'Favorable' : 'Unfavorable'})
              </div>
            )}
          </div>
        </div>

        {/* Error */}
        {error && (
          <div
            className="rounded-xl p-3 flex items-center gap-2"
            style={{
              background: theme.erB,
              border: `1px solid ${theme.er}30`,
            }}
          >
            <AlertCircle className="w-4 h-4" style={{ color: theme.er }} />
            <p className="text-sm" style={{ color: theme.er }}>{error}</p>
          </div>
        )}

        {/* Loading */}
        {loading && (
          <div
            className="rounded-xl overflow-hidden"
            style={{
              background: theme.card,
              border: `1px solid ${theme.cardBdr}`,
            }}
          >
            <LoadingTable rows={12} cols={7} />
          </div>
        )}

        {/* Empty state */}
        {!loading && !report && !error && (
          <div
            className="rounded-xl py-16 text-center"
            style={{
              background: theme.card,
              border: `1px solid ${theme.cardBdr}`,
              boxShadow: theme.cardSh,
            }}
          >
            <BarChart3 className="w-10 h-10 mx-auto mb-3" style={{ color: theme.g200 }} />
            <p className="text-sm" style={{ color: theme.t3 }}>
              Select date range and department, then click Generate
            </p>
          </div>
        )}

        {/* Report content */}
        {!loading && report && (
          <>
            {/* Summary Cards */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <SummaryCard
                title="Revenue"
                budget={report.revenue.total_budget}
                actual={report.revenue.total_actual}
                variance={report.revenue.total_variance}
                favorable={parseAmount(report.revenue.total_variance) >= 0}
                icon={TrendingUp}
                theme={theme}
              />
              <SummaryCard
                title="Expenses"
                budget={report.expenses.total_budget}
                actual={report.expenses.total_actual}
                variance={report.expenses.total_variance}
                favorable={parseAmount(report.expenses.total_variance) >= 0}
                icon={TrendingDown}
                theme={theme}
              />
              <SummaryCard
                title="Net Result"
                budget={report.net_result.budget}
                actual={report.net_result.actual}
                variance={report.net_result.variance}
                favorable={report.net_result.favorable}
                icon={BarChart3}
                theme={theme}
              />
            </div>

            {/* Revenue Table */}
            <AccountTable
              title="Revenue"
              accounts={report.revenue.accounts}
              totalBudget={report.revenue.total_budget}
              totalActual={report.revenue.total_actual}
              totalVariance={report.revenue.total_variance}
              theme={theme}
            />

            {/* Expenses Table */}
            <AccountTable
              title="Expenses"
              accounts={report.expenses.accounts}
              totalBudget={report.expenses.total_budget}
              totalActual={report.expenses.total_actual}
              totalVariance={report.expenses.total_variance}
              theme={theme}
            />

            {/* Net Result Summary */}
            <div
              className="rounded-xl p-5"
              style={{
                background: netFavorable ? theme.okB : theme.erB,
                border: `1px solid ${netFavorable ? theme.ok : theme.er}30`,
              }}
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  {netFavorable ? (
                    <TrendingUp className="w-6 h-6" style={{ color: netFavorable ? theme.ok : theme.er }} />
                  ) : (
                    <TrendingDown className="w-6 h-6" style={{ color: theme.er }} />
                  )}
                  <div>
                    <p
                      className="text-sm font-bold uppercase tracking-wider"
                      style={{ color: netFavorable ? theme.ok : theme.er }}
                    >
                      Net Result: {netFavorable ? 'Favorable' : 'Unfavorable'}
                    </p>
                    <p className="text-xs mt-0.5" style={{ color: netFavorable ? theme.ok : theme.er, opacity: 0.8 }}>
                      Budget: {fmt(report.net_result.budget)} | Actual: {fmt(report.net_result.actual)}
                    </p>
                  </div>
                </div>
                <div className="text-right">
                  <p className="text-xs" style={{ color: netFavorable ? theme.ok : theme.er, opacity: 0.7 }}>
                    Variance
                  </p>
                  <p
                    className="text-xl font-bold font-mono-nums"
                    style={{ color: netFavorable ? theme.ok : theme.er }}
                  >
                    {fmt(report.net_result.variance)}
                  </p>
                </div>
              </div>
            </div>

            {/* Periods covered */}
            {report.periods_covered && report.periods_covered.length > 0 && (
              <p className="text-xs text-center" style={{ color: theme.t3 }}>
                Periods covered: {report.periods_covered.join(', ')} | Department: {report.department || 'All'}
              </p>
            )}
          </>
        )}
      </div>
    </div>
  )
}
