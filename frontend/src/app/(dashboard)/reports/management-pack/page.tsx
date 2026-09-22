'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { getManagementPack, getToken } from '@/lib/api'
import type { ManagementPackReport } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { LoadingTable } from '@/components/ui/loading'
import { formatAmount, formatDate, parseAmount, exportToCsv, getFyStart, today, cn } from '@/lib/utils'
import { useTheme } from '@/contexts/ThemeContext'
import { useNumberFormat } from '@/contexts/NumberFormatContext'
import { useCompany } from '@/contexts/CompanyContext'
import {
  Download,
  RefreshCw,
  Briefcase,
  TrendingUp,
  TrendingDown,
  Shield,
  DollarSign,
  BarChart3,
  PieChart,
  AlertCircle,
} from 'lucide-react'

// ─── KPI color helpers ──────────────────────────────────────────────────────

function lossRatioColor(value: number, theme: any): { color: string; bg: string } {
  if (value < 70) return { color: theme.ok, bg: theme.okB }
  if (value <= 85) return { color: theme.wr, bg: theme.wrB }
  return { color: theme.er, bg: theme.erB }
}

function combinedRatioColor(value: number, theme: any): { color: string; bg: string } {
  if (value < 100) return { color: theme.ok, bg: theme.okB }
  return { color: theme.er, bg: theme.erB }
}

// ─── KPI Card ───────────────────────────────────────────────────────────────

function KpiCard({
  label,
  value,
  subtitle,
  icon: Icon,
  color,
  bg,
  theme,
}: {
  label: string
  value: string
  subtitle?: string
  icon: React.ComponentType<any>
  color: string
  bg: string
  theme: any
}) {
  return (
    <div
      className="rounded-xl p-5"
      style={{
        background: bg,
        border: `1px solid ${color}30`,
      }}
    >
      <div className="flex items-center gap-3 mb-3">
        <div
          className="w-9 h-9 rounded-lg flex items-center justify-center"
          style={{ background: `${color}20` }}
        >
          <Icon className="w-4.5 h-4.5" style={{ color }} strokeWidth={1.5} />
        </div>
        <span className="text-xs font-semibold uppercase tracking-wider" style={{ color: theme.t2 }}>
          {label}
        </span>
      </div>
      <p className="text-3xl font-bold font-mono-nums" style={{ color }}>
        {value}
      </p>
      {subtitle && (
        <p className="text-xs mt-1.5" style={{ color: theme.t3 }}>
          {subtitle}
        </p>
      )}
    </div>
  )
}

// ─── Financial Summary Line ─────────────────────────────────────────────────

function SummaryLine({
  label,
  amount,
  bold = false,
  color,
  theme,
  topBorder = false,
}: {
  label: string
  amount: string
  bold?: boolean
  color?: string
  theme: any
  topBorder?: boolean
}) {
  const { mode } = useNumberFormat()
  const fmt = (amt: string | number, currency = 'BWP') => formatAmount(amt, currency, mode)
  return (
    <div
      className={cn('flex items-center justify-between py-2', bold && 'pt-3')}
      style={topBorder ? { borderTop: `1px solid ${theme.g200}` } : undefined}
    >
      <span
        className={cn('text-sm', bold ? 'font-semibold' : '')}
        style={{ color: bold ? theme.text : theme.t2 }}
      >
        {label}
      </span>
      <span
        className={cn(
          'text-sm font-mono-nums',
          bold ? 'font-bold text-base' : 'font-medium'
        )}
        style={{ color: color || theme.text }}
      >
        {fmt(amount)}
      </span>
    </div>
  )
}

// ─── Management Accounts Pack Page ──────────────────────────────────────────

export default function ManagementPackPage() {
  const router = useRouter()
  const { theme } = useTheme()
  const { mode } = useNumberFormat()
  const fmt = (amount: string | number, currency = 'BWP') => formatAmount(amount, currency, mode)
  const { selectedId: companyId, selected: selectedCompany } = useCompany()
  const [fromDate, setFromDate] = useState(getFyStart())
  const [toDate, setToDate] = useState(today())
  const [report, setReport] = useState<ManagementPackReport | null>(null)
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
  }, [companyId])

  const handleGenerate = async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await getManagementPack(fromDate, toDate, companyId)
      setReport(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to generate management pack')
    } finally {
      setLoading(false)
    }
  }

  const handleExport = () => {
    if (!report) return
    const rows: Record<string, any>[] = []

    // Insurance KPIs
    rows.push({ Section: 'INSURANCE KPIs', Item: '', Value: '' })
    rows.push({ Section: 'KPI', Item: 'Earned Premium', Value: report.insurance_kpis.earned_premium })
    rows.push({ Section: 'KPI', Item: 'Claims Incurred', Value: report.insurance_kpis.claims_incurred })
    rows.push({ Section: 'KPI', Item: 'Operating Expenses', Value: report.insurance_kpis.operating_expenses })
    rows.push({ Section: 'KPI', Item: 'Loss Ratio', Value: report.insurance_kpis.loss_ratio })
    rows.push({ Section: 'KPI', Item: 'Expense Ratio', Value: report.insurance_kpis.expense_ratio })
    rows.push({ Section: 'KPI', Item: 'Combined Ratio', Value: report.insurance_kpis.combined_ratio })
    rows.push({ Section: 'KPI', Item: 'Profit Margin', Value: report.insurance_kpis.profit_margin })
    rows.push({ Section: 'KPI', Item: 'Underwriting Result', Value: report.insurance_kpis.underwriting_result })

    // P&L
    rows.push({ Section: 'PROFIT & LOSS', Item: '', Value: '' })
    rows.push({ Section: 'P&L', Item: 'Total Revenue', Value: report.profit_loss.total_revenue })
    rows.push({ Section: 'P&L', Item: 'Cost of Insurance', Value: report.profit_loss.cost_of_insurance })
    rows.push({ Section: 'P&L', Item: 'Gross Result', Value: report.profit_loss.gross_result })
    rows.push({ Section: 'P&L', Item: 'Operating Expenses', Value: report.profit_loss.operating_expenses })
    rows.push({ Section: 'P&L', Item: 'Net Profit', Value: report.profit_loss.net_profit })

    // Balance Sheet
    rows.push({ Section: 'BALANCE SHEET', Item: '', Value: '' })
    rows.push({ Section: 'BS', Item: 'Total Assets', Value: report.balance_sheet.total_assets })
    rows.push({ Section: 'BS', Item: 'Total Liabilities', Value: report.balance_sheet.total_liabilities })
    rows.push({ Section: 'BS', Item: 'Total Equity', Value: report.balance_sheet.total_equity })
    rows.push({ Section: 'BS', Item: 'Balanced', Value: report.balance_sheet.balanced ? 'Yes' : 'No' })

    // Cash Position
    rows.push({ Section: 'CASH POSITION', Item: '', Value: '' })
    report.cash_position.accounts.forEach((acc) =>
      rows.push({ Section: 'Cash', Item: `${acc.account_code} ${acc.account_name}`, Value: acc.balance_bwp })
    )
    rows.push({ Section: 'Cash Total', Item: 'Total Cash (BWP)', Value: report.cash_position.total_cash_bwp })

    // Budget Comparison
    if (report.budget_comparison) {
      rows.push({ Section: 'BUDGET COMPARISON', Item: '', Value: '' })
      rows.push({ Section: 'Budget', Item: 'Revenue Budget', Value: report.budget_comparison.revenue_budget })
      rows.push({ Section: 'Budget', Item: 'Revenue Actual', Value: report.budget_comparison.revenue_actual })
      rows.push({ Section: 'Budget', Item: 'Revenue Variance', Value: report.budget_comparison.revenue_variance })
      rows.push({ Section: 'Budget', Item: 'Expense Budget', Value: report.budget_comparison.expense_budget })
      rows.push({ Section: 'Budget', Item: 'Expense Actual', Value: report.budget_comparison.expense_actual })
      rows.push({ Section: 'Budget', Item: 'Expense Variance', Value: report.budget_comparison.expense_variance })
      rows.push({ Section: 'Budget', Item: 'Net Budget', Value: report.budget_comparison.net_budget })
      rows.push({ Section: 'Budget', Item: 'Net Actual', Value: report.budget_comparison.net_actual })
      rows.push({ Section: 'Budget', Item: 'Net Variance', Value: report.budget_comparison.net_variance })
    }

    exportToCsv(rows, `management_pack_${fromDate}_${toDate}`)
  }

  // Parse KPI values
  const lossRatio = report ? parseAmount(report.insurance_kpis.loss_ratio) : 0
  const expenseRatio = report ? parseAmount(report.insurance_kpis.expense_ratio) : 0
  const combinedRatio = report ? parseAmount(report.insurance_kpis.combined_ratio) : 0
  const profitMargin = report ? parseAmount(report.insurance_kpis.profit_margin) : 0

  return (
    <div className="flex flex-col min-h-screen" style={{ background: theme.bg }}>
      <TopBar
        title="Management Accounts Pack"
        breadcrumbs={[{ label: 'Reports', href: '/reports' }, { label: 'Management Pack' }]}
        actions={
          report && (
            <Button
              variant="secondary"
              size="sm"
              leftIcon={<Download className="w-3.5 h-3.5" />}
              onClick={handleExport}
            >
              Export CSV
            </Button>
          )
        }
      />

      <div className="flex-1 p-6 space-y-6">
        {/* Page Header */}
        <div>
          <h1 className="text-2xl font-bold" style={{ color: theme.navy }}>
            Management Accounts Pack
          </h1>
          <p className="text-sm mt-1" style={{ color: theme.t3 }}>
            Monthly EXCO Report Package
          </p>
        </div>

        {/* Controls */}
        <div
          className="rounded-xl p-4"
          style={{
            background: theme.card,
            border: `1px solid ${theme.cardBdr}`,
            boxShadow: theme.cardSh,
          }}
        >
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
                onFocus={(e) => {
                  e.currentTarget.style.borderColor = theme.orange
                  e.currentTarget.style.boxShadow = `0 0 0 3px ${theme.orange}1A`
                }}
                onBlur={(e) => {
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
                onFocus={(e) => {
                  e.currentTarget.style.borderColor = theme.orange
                  e.currentTarget.style.boxShadow = `0 0 0 3px ${theme.orange}1A`
                }}
                onBlur={(e) => {
                  e.currentTarget.style.borderColor = theme.g200
                  e.currentTarget.style.boxShadow = 'none'
                }}
              />
            </div>
            <Button variant="primary" size="md" onClick={handleGenerate} loading={loading}>
              <RefreshCw className={cn('w-4 h-4 mr-1.5', loading && 'animate-spin')} />
              Generate
            </Button>
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
            <LoadingTable rows={12} cols={4} />
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
            <Briefcase className="w-10 h-10 mx-auto mb-3" style={{ color: theme.g200 }} />
            <p className="text-sm" style={{ color: theme.t3 }}>
              Select a date range and click Generate to build the management pack
            </p>
          </div>
        )}

        {/* Report Content */}
        {!loading && report && (
          <>
            {/* ── Section 1: Insurance KPIs ───────────────────────────────── */}
            <div>
              <div className="flex items-center gap-2 mb-4">
                <Shield className="w-5 h-5" style={{ color: theme.orange }} />
                <h2 className="text-lg font-semibold" style={{ color: theme.text }}>
                  Insurance KPIs
                </h2>
                <span className="text-xs px-2 py-0.5 rounded-full font-medium" style={{ background: theme.oL, color: theme.orange }}>
                  Key Metrics for EXCO
                </span>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                <KpiCard
                  label="Loss Ratio"
                  value={`${lossRatio.toFixed(1)}%`}
                  subtitle={`Claims / Earned Premium (${fmt(report.insurance_kpis.claims_incurred)} / ${fmt(report.insurance_kpis.earned_premium)})`}
                  icon={TrendingDown}
                  color={lossRatioColor(lossRatio, theme).color}
                  bg={lossRatioColor(lossRatio, theme).bg}
                  theme={theme}
                />
                <KpiCard
                  label="Expense Ratio"
                  value={`${expenseRatio.toFixed(1)}%`}
                  subtitle={`OpEx / Earned Premium (${fmt(report.insurance_kpis.operating_expenses)} / ${fmt(report.insurance_kpis.earned_premium)})`}
                  icon={BarChart3}
                  color={theme.inf}
                  bg={theme.inB}
                  theme={theme}
                />
                <KpiCard
                  label="Combined Ratio"
                  value={`${combinedRatio.toFixed(1)}%`}
                  subtitle={combinedRatio < 100 ? 'Profitable -- below 100%' : 'Unprofitable -- above 100%'}
                  icon={PieChart}
                  color={combinedRatioColor(combinedRatio, theme).color}
                  bg={combinedRatioColor(combinedRatio, theme).bg}
                  theme={theme}
                />
                <KpiCard
                  label="Profit Margin"
                  value={`${profitMargin.toFixed(1)}%`}
                  subtitle={`Underwriting result: ${fmt(report.insurance_kpis.underwriting_result)}`}
                  icon={DollarSign}
                  color={profitMargin >= 0 ? theme.ok : theme.er}
                  bg={profitMargin >= 0 ? theme.okB : theme.erB}
                  theme={theme}
                />
              </div>
            </div>

            {/* ── Section 2: Financial Summary ────────────────────────────── */}
            <div>
              <div className="flex items-center gap-2 mb-4">
                <Briefcase className="w-5 h-5" style={{ color: theme.orange }} />
                <h2 className="text-lg font-semibold" style={{ color: theme.text }}>
                  Financial Summary
                </h2>
              </div>
              <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
                {/* P&L Card */}
                <Card>
                  <CardHeader>
                    <div className="flex items-center gap-2">
                      <TrendingUp className="w-4 h-4" style={{ color: theme.orange }} />
                      <CardTitle>Profit & Loss</CardTitle>
                    </div>
                  </CardHeader>
                  <CardContent className="space-y-0">
                    <SummaryLine
                      label="Revenue"
                      amount={report.profit_loss.total_revenue}
                      theme={theme}
                      color={theme.ok}
                    />
                    <SummaryLine
                      label="Cost of Insurance"
                      amount={report.profit_loss.cost_of_insurance}
                      theme={theme}
                      color={theme.er}
                    />
                    <SummaryLine
                      label="Gross Result"
                      amount={report.profit_loss.gross_result}
                      bold
                      theme={theme}
                      topBorder
                      color={parseAmount(report.profit_loss.gross_result) >= 0 ? theme.ok : theme.er}
                    />
                    <SummaryLine
                      label="Operating Expenses"
                      amount={report.profit_loss.operating_expenses}
                      theme={theme}
                      color={theme.er}
                    />
                    <SummaryLine
                      label={report.profit_loss.is_profit ? 'Net Profit' : 'Net Loss'}
                      amount={report.profit_loss.net_profit}
                      bold
                      theme={theme}
                      topBorder
                      color={report.profit_loss.is_profit ? theme.ok : theme.er}
                    />
                  </CardContent>
                </Card>

                {/* Balance Sheet Card */}
                <Card>
                  <CardHeader>
                    <div className="flex items-center gap-2">
                      <BarChart3 className="w-4 h-4" style={{ color: theme.orange }} />
                      <CardTitle>Balance Sheet</CardTitle>
                    </div>
                  </CardHeader>
                  <CardContent className="space-y-0">
                    <SummaryLine
                      label="Total Assets"
                      amount={report.balance_sheet.total_assets}
                      theme={theme}
                      color={theme.inf}
                    />
                    <SummaryLine
                      label="Total Liabilities"
                      amount={report.balance_sheet.total_liabilities}
                      theme={theme}
                      color={theme.wr}
                    />
                    <SummaryLine
                      label="Total Equity"
                      amount={report.balance_sheet.total_equity}
                      bold
                      theme={theme}
                      topBorder
                      color={theme.navy}
                    />
                    <div
                      className="flex items-center justify-between py-2 mt-2"
                      style={{ borderTop: `1px solid ${theme.g200}` }}
                    >
                      <span className="text-xs font-medium" style={{ color: theme.t3 }}>
                        Balance Check
                      </span>
                      <span
                        className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold"
                        style={{
                          background: report.balance_sheet.balanced ? theme.okB : theme.erB,
                          color: report.balance_sheet.balanced ? theme.ok : theme.er,
                        }}
                      >
                        {report.balance_sheet.balanced ? 'Balanced' : 'Imbalanced'}
                      </span>
                    </div>
                  </CardContent>
                </Card>

                {/* Cash Position Card */}
                <Card>
                  <CardHeader>
                    <div className="flex items-center gap-2">
                      <DollarSign className="w-4 h-4" style={{ color: theme.orange }} />
                      <CardTitle>Cash Position</CardTitle>
                    </div>
                  </CardHeader>
                  <CardContent className="space-y-0">
                    {report.cash_position.accounts.map((acc, idx) => (
                      <div
                        key={idx}
                        className="flex items-center justify-between py-2"
                        style={{
                          borderBottom:
                            idx < report.cash_position.accounts.length - 1
                              ? `1px solid ${theme.g200}`
                              : undefined,
                        }}
                      >
                        <div>
                          <span className="text-xs font-mono" style={{ color: theme.t3 }}>
                            {acc.account_code}
                          </span>
                          <span className="text-sm ml-2" style={{ color: theme.t2 }}>
                            {acc.account_name}
                          </span>
                          {acc.currency !== 'BWP' && (
                            <span className="text-xs ml-1" style={{ color: theme.t3 }}>
                              ({acc.currency})
                            </span>
                          )}
                        </div>
                        <span
                          className="text-sm font-mono-nums font-medium"
                          style={{
                            color: parseAmount(acc.balance_bwp) >= 0 ? theme.ok : theme.er,
                          }}
                        >
                          {fmt(acc.balance_bwp)}
                        </span>
                      </div>
                    ))}
                    <div
                      className="flex items-center justify-between pt-3 mt-1"
                      style={{ borderTop: `2px solid ${theme.g200}` }}
                    >
                      <span className="text-sm font-semibold" style={{ color: theme.text }}>
                        Total Cash
                      </span>
                      <span
                        className="text-base font-bold font-mono-nums"
                        style={{ color: theme.orange }}
                      >
                        {fmt(report.cash_position.total_cash_bwp)}
                      </span>
                    </div>
                  </CardContent>
                </Card>
              </div>
            </div>

            {/* ── Section 3: Budget Comparison ────────────────────────────── */}
            <div>
              <div className="flex items-center gap-2 mb-4">
                <BarChart3 className="w-5 h-5" style={{ color: theme.orange }} />
                <h2 className="text-lg font-semibold" style={{ color: theme.text }}>
                  Budget Comparison
                </h2>
              </div>

              {report.budget_comparison ? (
                <Card>
                  <CardContent className="p-0">
                    <div className="overflow-x-auto">
                      <table className="w-full text-sm">
                        <thead>
                          <tr style={{ background: theme.g100 }}>
                            <th
                              className="px-5 py-3 text-left text-xs font-semibold uppercase tracking-wider"
                              style={{ color: theme.t2 }}
                            >
                              Category
                            </th>
                            <th
                              className="px-5 py-3 text-right text-xs font-semibold uppercase tracking-wider"
                              style={{ color: theme.t2 }}
                            >
                              Budget
                            </th>
                            <th
                              className="px-5 py-3 text-right text-xs font-semibold uppercase tracking-wider"
                              style={{ color: theme.t2 }}
                            >
                              Actual
                            </th>
                            <th
                              className="px-5 py-3 text-right text-xs font-semibold uppercase tracking-wider"
                              style={{ color: theme.t2 }}
                            >
                              Variance
                            </th>
                            <th
                              className="px-5 py-3 text-center text-xs font-semibold uppercase tracking-wider"
                              style={{ color: theme.t2 }}
                            >
                              Status
                            </th>
                          </tr>
                        </thead>
                        <tbody>
                          {[
                            {
                              label: 'Revenue',
                              budget: report.budget_comparison.revenue_budget,
                              actual: report.budget_comparison.revenue_actual,
                              variance: report.budget_comparison.revenue_variance,
                              favorableWhen: 'positive' as const,
                            },
                            {
                              label: 'Expenses',
                              budget: report.budget_comparison.expense_budget,
                              actual: report.budget_comparison.expense_actual,
                              variance: report.budget_comparison.expense_variance,
                              favorableWhen: 'positive' as const,
                            },
                            {
                              label: 'Net Result',
                              budget: report.budget_comparison.net_budget,
                              actual: report.budget_comparison.net_actual,
                              variance: report.budget_comparison.net_variance,
                              favorableWhen: 'positive' as const,
                            },
                          ].map((row, idx) => {
                            const varianceNum = parseAmount(row.variance)
                            const favorable = varianceNum >= 0
                            const isNetRow = row.label === 'Net Result'
                            return (
                              <tr
                                key={idx}
                                style={{
                                  borderBottom: `1px solid ${theme.g200}`,
                                  background: isNetRow ? theme.g100 : 'transparent',
                                }}
                                onMouseEnter={(e) => {
                                  if (!isNetRow) e.currentTarget.style.background = theme.oL
                                }}
                                onMouseLeave={(e) => {
                                  if (!isNetRow) e.currentTarget.style.background = 'transparent'
                                }}
                              >
                                <td
                                  className={cn('px-5 py-3', isNetRow ? 'font-bold' : 'font-medium')}
                                  style={{ color: theme.text }}
                                >
                                  {row.label}
                                </td>
                                <td
                                  className="px-5 py-3 text-right font-mono-nums"
                                  style={{ color: theme.t2 }}
                                >
                                  {fmt(row.budget)}
                                </td>
                                <td
                                  className={cn(
                                    'px-5 py-3 text-right font-mono-nums',
                                    isNetRow ? 'font-bold' : 'font-medium'
                                  )}
                                  style={{ color: theme.text }}
                                >
                                  {fmt(row.actual)}
                                </td>
                                <td
                                  className="px-5 py-3 text-right font-mono-nums font-semibold"
                                  style={{ color: favorable ? theme.ok : theme.er }}
                                >
                                  {fmt(row.variance)}
                                </td>
                                <td className="px-5 py-3 text-center">
                                  <span
                                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium"
                                    style={{
                                      background: favorable ? theme.okB : theme.erB,
                                      color: favorable ? theme.ok : theme.er,
                                    }}
                                  >
                                    {favorable ? (
                                      <TrendingUp className="w-3 h-3" />
                                    ) : (
                                      <TrendingDown className="w-3 h-3" />
                                    )}
                                    {favorable ? 'Favorable' : 'Unfavorable'}
                                  </span>
                                </td>
                              </tr>
                            )
                          })}
                        </tbody>
                      </table>
                    </div>
                  </CardContent>
                </Card>
              ) : (
                <div
                  className="rounded-xl py-10 text-center"
                  style={{
                    background: theme.card,
                    border: `1px solid ${theme.cardBdr}`,
                    boxShadow: theme.cardSh,
                  }}
                >
                  <BarChart3 className="w-8 h-8 mx-auto mb-2" style={{ color: theme.g200 }} />
                  <p className="text-sm" style={{ color: theme.t3 }}>
                    No budget data available for this period
                  </p>
                  <p className="text-xs mt-1" style={{ color: theme.t3 }}>
                    Budget data will appear once budgets are configured for the selected fiscal period
                  </p>
                </div>
              )}
            </div>

            {/* Report footer */}
            <p className="text-xs text-center" style={{ color: theme.t3 }}>
              Report period: {formatDate(report.from_date)} to {formatDate(report.to_date)} | Generated {formatDate(today())}
            </p>
          </>
        )}
      </div>
    </div>
  )
}
