'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { getExpenseAnalysis, getExpenseAnalysisDetail, downloadExpenseAnalysisXlsx, analyseExpenseWithAI, getToken } from '@/lib/api'
import type { ExpenseAnalysisReport, ExpenseAnalysisDetail, ExpenseAnalysisAIResult } from '@/lib/api'
import { useCompany } from '@/contexts/CompanyContext'
import { TopBar } from '@/components/layout/TopBar'
// Card components handled inline
import { Button } from '@/components/ui/button'
import { LoadingTable } from '@/components/ui/loading'
import { formatAmount, formatDate, parseAmount, exportToCsv, getFyStart, today, cn } from '@/lib/utils'
import { useTheme } from '@/contexts/ThemeContext'
import { useNumberFormat } from '@/contexts/NumberFormatContext'
import {
  Download,
  RefreshCw,
  TrendingUp,
  TrendingDown,
  PieChart,
  BarChart3,
  AlertCircle,
  ChevronDown,
  ChevronRight,
} from 'lucide-react'

// ─── Category Labels ────────────────────────────────────────────────────────

const CATEGORY_LABELS: Record<string, string> = {
  cost_of_insurance: 'Cost of Insurance',
  operating_expense: 'Operating Expenses',
  commission: 'Commission',
  claims: 'Claims',
  reinsurance: 'Reinsurance',
  administrative: 'Administrative',
  other: 'Other',
}

function getCategoryLabel(category: string): string {
  return CATEGORY_LABELS[category] || category.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
}

// ─── Percentage Bar ─────────────────────────────────────────────────────────

function PctBar({ pct, theme }: { pct: number; theme: any }) {
  const clampedPct = Math.min(Math.max(pct, 0), 100)
  return (
    <div
      className="w-24 h-2 rounded-full overflow-hidden"
      style={{ background: theme.g200 }}
    >
      <div
        className="h-full rounded-full transition-all"
        style={{
          width: `${clampedPct}%`,
          background: theme.orange,
        }}
      />
    </div>
  )
}

// ─── Summary Card ───────────────────────────────────────────────────────────

function SummaryCard({
  title,
  amount,
  subtitle,
  icon: Icon,
  color,
  bgColor,
  theme,
}: {
  title: string
  amount: string
  subtitle?: string
  icon: React.ComponentType<any>
  color: string
  bgColor: string
  theme: any
}) {
  const { mode } = useNumberFormat()
  const fmt = (amt: string | number, currency = 'BWP') => formatAmount(amt, currency, mode)
  return (
    <div
      className="rounded-xl p-5"
      style={{
        background: theme.card,
        border: `1px solid ${theme.cardBdr}`,
        boxShadow: theme.cardSh,
      }}
    >
      <div className="flex items-center gap-3 mb-3">
        <div
          className="w-9 h-9 rounded-lg flex items-center justify-center"
          style={{ background: bgColor }}
        >
          <Icon className="w-4.5 h-4.5" style={{ color }} strokeWidth={1.5} />
        </div>
        <h3 className="text-sm font-semibold" style={{ color: theme.text }}>
          {title}
        </h3>
      </div>
      <p className="text-xl font-bold font-mono-nums" style={{ color }}>
        {fmt(amount)}
      </p>
      {subtitle && (
        <p className="text-xs mt-1" style={{ color: theme.t3 }}>
          {subtitle}
        </p>
      )}
    </div>
  )
}

// ─── Category Section (collapsible) ─────────────────────────────────────────

function CategorySection({
  categoryLabel,
  accounts,
  total,
  pctOfTotal,
  change,
  changePct,
  theme,
}: {
  category?: string
  categoryLabel: string
  accounts: { code: string; name: string; amount: string }[]
  total: string
  pctOfTotal: string
  change: string
  changePct: string
  previousTotal?: string
  theme: any
}) {
  const { mode } = useNumberFormat()
  const fmt = (amount: string | number, currency = 'BWP') => formatAmount(amount, currency, mode)
  const [expanded, setExpanded] = useState(false)
  const changeNum = parseAmount(change)
  const changePctNum = parseAmount(changePct)
  const isIncrease = changeNum > 0
  // For expenses, increase is bad (red), decrease is good (green)
  const changeColor = changeNum === 0 ? theme.t3 : isIncrease ? theme.er : theme.ok
  const pctNum = parseAmount(pctOfTotal)

  return (
    <div
      className="rounded-xl overflow-hidden"
      style={{
        background: theme.card,
        border: `1px solid ${theme.cardBdr}`,
        boxShadow: theme.cardSh,
      }}
    >
      {/* Header row — clickable */}
      <button
        className="w-full px-5 py-4 flex items-center justify-between transition-colors"
        style={{ borderBottom: expanded ? `1px solid ${theme.g200}` : 'none' }}
        onClick={() => setExpanded(!expanded)}
        onMouseEnter={e => (e.currentTarget.style.background = theme.oL)}
        onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
      >
        <div className="flex items-center gap-3">
          {expanded ? (
            <ChevronDown className="w-4 h-4" style={{ color: theme.t3 }} />
          ) : (
            <ChevronRight className="w-4 h-4" style={{ color: theme.t3 }} />
          )}
          <h3 className="text-sm font-semibold" style={{ color: theme.text }}>
            {categoryLabel}
          </h3>
        </div>
        <div className="flex items-center gap-6">
          {/* % of total bar */}
          <div className="hidden md:flex items-center gap-2">
            <PctBar pct={pctNum} theme={theme} />
            <span className="text-xs font-mono-nums w-12 text-right" style={{ color: theme.t2 }}>
              {pctNum.toFixed(1)}%
            </span>
          </div>
          {/* MoM change */}
          <div className="flex items-center gap-1.5">
            {changeNum !== 0 && (
              isIncrease ? (
                <TrendingUp className="w-3.5 h-3.5" style={{ color: changeColor }} />
              ) : (
                <TrendingDown className="w-3.5 h-3.5" style={{ color: changeColor }} />
              )
            )}
            <span
              className="text-xs font-mono-nums font-medium"
              style={{ color: changeColor }}
            >
              {changePctNum !== 0 ? `${changePctNum > 0 ? '+' : ''}${changePctNum.toFixed(1)}%` : '--'}
            </span>
          </div>
          {/* Total */}
          <span className="text-sm font-bold font-mono-nums" style={{ color: theme.text }}>
            {fmt(total)}
          </span>
        </div>
      </button>

      {/* Account detail table */}
      {expanded && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr style={{ background: theme.g100 }}>
                <th
                  className="px-5 py-2.5 text-left text-xs font-semibold uppercase tracking-wider"
                  style={{ color: theme.t2 }}
                >
                  Code
                </th>
                <th
                  className="px-5 py-2.5 text-left text-xs font-semibold uppercase tracking-wider"
                  style={{ color: theme.t2 }}
                >
                  Account Name
                </th>
                <th
                  className="px-5 py-2.5 text-right text-xs font-semibold uppercase tracking-wider"
                  style={{ color: theme.t2 }}
                >
                  Amount
                </th>
              </tr>
            </thead>
            <tbody>
              {accounts.length === 0 ? (
                <tr>
                  <td
                    colSpan={3}
                    className="px-5 py-6 text-center text-sm"
                    style={{ color: theme.t3 }}
                  >
                    No accounts in this category
                  </td>
                </tr>
              ) : (
                accounts.map((acc, idx) => (
                  <tr
                    key={idx}
                    className="transition-colors"
                    style={{ borderBottom: `1px solid ${theme.g200}` }}
                    onMouseEnter={e => (e.currentTarget.style.background = theme.oL)}
                    onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                  >
                    <td className="px-5 py-2.5 font-mono text-xs" style={{ color: theme.t3 }}>
                      {acc.code}
                    </td>
                    <td className="px-5 py-2.5 font-medium" style={{ color: theme.text }}>
                      {acc.name}
                    </td>
                    <td
                      className="px-5 py-2.5 text-right font-mono-nums font-medium"
                      style={{ color: theme.text }}
                    >
                      {fmt(acc.amount)}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
            {accounts.length > 0 && (
              <tfoot>
                <tr style={{ background: theme.g100, borderTop: `2px solid ${theme.g200}` }}>
                  <td
                    colSpan={2}
                    className="px-5 py-3 text-xs font-bold uppercase tracking-wider"
                    style={{ color: theme.t2 }}
                  >
                    Total {categoryLabel}
                  </td>
                  <td
                    className="px-5 py-3 text-right font-bold font-mono-nums"
                    style={{ color: theme.text }}
                  >
                    {fmt(total)}
                  </td>
                </tr>
              </tfoot>
            )}
          </table>
        </div>
      )}
    </div>
  )
}

// ─── Expense Analysis Page ──────────────────────────────────────────────────

export default function ExpenseAnalysisPage() {
  const router = useRouter()
  const { theme } = useTheme()
  const { mode } = useNumberFormat()
  const fmt = (amount: string | number, currency = 'BWP') => formatAmount(amount, currency, mode)
  const [fromDate, setFromDate] = useState(getFyStart())
  const [toDate, setToDate] = useState(today())
  const [report, setReport] = useState<ExpenseAnalysisReport | null>(null)
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

  const handleGenerate = async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await getExpenseAnalysis(fromDate, toDate)
      setReport(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to generate Expense Analysis report')
    } finally {
      setLoading(false)
    }
  }

  const handleExport = () => {
    if (!report) return
    const rows: Record<string, any>[] = []

    // Category breakdown rows
    report.categories.forEach((cat) => {
      rows.push({
        Category: getCategoryLabel(cat.category),
        Code: '',
        Account: '',
        Amount: cat.total,
        '% of Total': cat.pct_of_total,
        'Previous Period': cat.previous_total,
        Change: cat.change,
        'Change %': cat.change_pct,
      })
      cat.accounts.forEach((acc) => {
        rows.push({
          Category: '',
          Code: acc.code,
          Account: acc.name,
          Amount: acc.amount,
          '% of Total': '',
          'Previous Period': '',
          Change: '',
          'Change %': '',
        })
      })
    })

    // Totals
    rows.push({
      Category: 'TOTAL',
      Code: '',
      Account: '',
      Amount: report.totals.current_total,
      '% of Total': '100%',
      'Previous Period': report.totals.previous_total,
      Change: report.totals.change,
      'Change %': report.totals.change_pct,
    })

    exportToCsv(rows, `expense_analysis_${fromDate}_${toDate}`)
  }

  // ── CFO directive 2026-05-21: detail / Excel / Aria ──────────────────
  const { selectedId: companyId } = useCompany()
  const [detail, setDetail] = useState<ExpenseAnalysisDetail | null>(null)
  const [openCat, setOpenCat] = useState<string | null>(null)
  const [xlsxBusy, setXlsxBusy] = useState(false)
  const [aiBusy, setAiBusy] = useState(false)
  const [aiResult, setAiResult] = useState<ExpenseAnalysisAIResult | null>(null)

  useEffect(() => {
    // Load drill-down detail alongside the summary
    if (!fromDate || !toDate) return
    getExpenseAnalysisDetail(fromDate, toDate, companyId)
      .then(setDetail)
      .catch(() => setDetail(null))
  }, [fromDate, toDate, companyId])

  const handleExportXlsx = async () => {
    setXlsxBusy(true)
    try { await downloadExpenseAnalysisXlsx(fromDate, toDate, companyId) }
    finally { setXlsxBusy(false) }
  }
  const handleAnalyseAI = async () => {
    setAiBusy(true)
    setAiResult(null)
    try { setAiResult(await analyseExpenseWithAI(fromDate, toDate, companyId)) }
    catch (e) { setAiResult({ ai_available: false, fallback: true, reason: String(e), data_summary: null, message: 'Network error' }) }
    finally { setAiBusy(false) }
  }

  // Summary helpers
  const totalChange = report ? parseAmount(report.totals.change) : 0
  const totalChangePct = report ? parseAmount(report.totals.change_pct) : 0
  const isIncrease = totalChange > 0

  return (
    <div className="flex flex-col min-h-screen" style={{ background: theme.bg }}>
      <TopBar
        title="Expense Analysis"
        breadcrumbs={[{ label: 'Reports', href: '/reports' }, { label: 'Expense Analysis' }]}
        actions={
          <div className="flex items-center gap-2">
            <Button
              variant="primary"
              size="sm"
              leftIcon={<Download className="w-3.5 h-3.5" />}
              onClick={handleExportXlsx}
              loading={xlsxBusy}
              disabled={xlsxBusy}
              title="Multi-sheet Excel: Summary, By Account, By Supplier, Line Detail"
            >
              Export Excel
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={handleAnalyseAI}
              loading={aiBusy}
              disabled={aiBusy}
              title="Aria variance commentary on the period's expenses"
            >
              Analyse with AI
            </Button>
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
            <Button variant="primary" size="md" onClick={handleGenerate} loading={loading}>
              <RefreshCw className={cn('w-4 h-4 mr-1.5', loading && 'animate-spin')} />
              Generate
            </Button>

            {report && (
              <div
                className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium"
                style={{
                  background: isIncrease ? theme.erB : theme.okB,
                  color: isIncrease ? theme.er : theme.ok,
                  border: `1px solid ${isIncrease ? theme.er : theme.ok}30`,
                }}
              >
                {isIncrease ? (
                  <TrendingUp className="w-4 h-4" />
                ) : totalChange === 0 ? (
                  <BarChart3 className="w-4 h-4" />
                ) : (
                  <TrendingDown className="w-4 h-4" />
                )}
                {isIncrease ? 'Expenses Up' : totalChange === 0 ? 'No Change' : 'Expenses Down'}{' '}
                {totalChangePct !== 0 ? `${Math.abs(totalChangePct).toFixed(1)}%` : ''}
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
            <LoadingTable rows={12} cols={5} />
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
            <PieChart className="w-10 h-10 mx-auto mb-3" style={{ color: theme.g200 }} />
            <p className="text-sm" style={{ color: theme.t3 }}>
              Select a date range and click Generate to view expense analysis
            </p>
          </div>
        )}

        {/* Report content */}
        {!loading && report && (
          <>
            {/* Summary Cards */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <SummaryCard
                title="Total Expenses (Current)"
                amount={report.totals.current_total}
                subtitle={`${formatDate(report.from_date)} - ${formatDate(report.to_date)}`}
                icon={PieChart}
                color={theme.orange}
                bgColor={theme.oL}
                theme={theme}
              />
              <SummaryCard
                title="Previous Period Total"
                amount={report.totals.previous_total}
                subtitle={`${formatDate(report.totals.previous_from)} - ${formatDate(report.totals.previous_to)}`}
                icon={BarChart3}
                color={theme.t2}
                bgColor={theme.g100}
                theme={theme}
              />
              <div
                className="rounded-xl p-5"
                style={{
                  background: theme.card,
                  border: `1px solid ${theme.cardBdr}`,
                  boxShadow: theme.cardSh,
                }}
              >
                <div className="flex items-center gap-3 mb-3">
                  <div
                    className="w-9 h-9 rounded-lg flex items-center justify-center"
                    style={{
                      background: totalChange === 0
                        ? theme.g100
                        : isIncrease ? theme.erB : theme.okB,
                    }}
                  >
                    {isIncrease ? (
                      <TrendingUp className="w-4.5 h-4.5" style={{ color: theme.er }} strokeWidth={1.5} />
                    ) : totalChange === 0 ? (
                      <BarChart3 className="w-4.5 h-4.5" style={{ color: theme.t3 }} strokeWidth={1.5} />
                    ) : (
                      <TrendingDown className="w-4.5 h-4.5" style={{ color: theme.ok }} strokeWidth={1.5} />
                    )}
                  </div>
                  <h3 className="text-sm font-semibold" style={{ color: theme.text }}>
                    Period-over-Period Change
                  </h3>
                </div>
                <p
                  className="text-xl font-bold font-mono-nums"
                  style={{
                    color: totalChange === 0
                      ? theme.t3
                      : isIncrease ? theme.er : theme.ok,
                  }}
                >
                  {totalChange > 0 ? '+' : ''}{fmt(report.totals.change)}
                </p>
                <p
                  className="text-xs mt-1 font-mono-nums"
                  style={{
                    color: totalChange === 0
                      ? theme.t3
                      : isIncrease ? theme.er : theme.ok,
                  }}
                >
                  {totalChangePct !== 0 ? `${totalChangePct > 0 ? '+' : ''}${totalChangePct.toFixed(1)}%` : 'No change'}
                </p>
              </div>
            </div>

            {/* Category Breakdown */}
            <div>
              <h2
                className="text-sm font-semibold uppercase tracking-wider mb-3"
                style={{ color: theme.t2 }}
              >
                Category Breakdown
              </h2>
              <div className="space-y-3">
                {report.categories.map((cat) => (
                  <CategorySection
                    key={cat.category}
                    category={cat.category}
                    categoryLabel={cat.category_label || getCategoryLabel(cat.category)}
                    accounts={cat.accounts}
                    total={cat.total}
                    pctOfTotal={cat.pct_of_total}
                    change={cat.change}
                    changePct={cat.change_pct}
                    previousTotal={cat.previous_total}
                    theme={theme}
                  />
                ))}
              </div>
            </div>

            {/* Comparison Table */}
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
                  Period Comparison
                </h3>
              </div>
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
                        Current Period
                      </th>
                      <th
                        className="px-5 py-3 text-right text-xs font-semibold uppercase tracking-wider"
                        style={{ color: theme.t2 }}
                      >
                        Previous Period
                      </th>
                      <th
                        className="px-5 py-3 text-right text-xs font-semibold uppercase tracking-wider"
                        style={{ color: theme.t2 }}
                      >
                        Change
                      </th>
                      <th
                        className="px-5 py-3 text-right text-xs font-semibold uppercase tracking-wider"
                        style={{ color: theme.t2 }}
                      >
                        Change %
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.categories.map((cat, idx) => {
                      const catChangeNum = parseAmount(cat.change)
                      const catChangePctNum = parseAmount(cat.change_pct)
                      const catIsIncrease = catChangeNum > 0
                      const catChangeColor = catChangeNum === 0 ? theme.t3 : catIsIncrease ? theme.er : theme.ok
                      return (
                        <tr
                          key={idx}
                          className="transition-colors"
                          style={{ borderBottom: `1px solid ${theme.g200}` }}
                          onMouseEnter={e => (e.currentTarget.style.background = theme.oL)}
                          onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                        >
                          <td className="px-5 py-2.5 font-medium" style={{ color: theme.text }}>
                            {cat.category_label || getCategoryLabel(cat.category)}
                          </td>
                          <td
                            className="px-5 py-2.5 text-right font-mono-nums font-medium"
                            style={{ color: theme.text }}
                          >
                            {fmt(cat.total)}
                          </td>
                          <td
                            className="px-5 py-2.5 text-right font-mono-nums text-xs"
                            style={{ color: theme.t2 }}
                          >
                            {fmt(cat.previous_total)}
                          </td>
                          <td
                            className="px-5 py-2.5 text-right font-mono-nums font-semibold"
                            style={{ color: catChangeColor }}
                          >
                            {catChangeNum > 0 ? '+' : ''}{fmt(cat.change)}
                          </td>
                          <td className="px-5 py-2.5 text-right">
                            <span
                              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-bold font-mono-nums"
                              style={{
                                background: catChangeNum === 0 ? theme.g100 : catIsIncrease ? theme.erB : theme.okB,
                                color: catChangeColor,
                              }}
                            >
                              {catChangeNum !== 0 && (
                                catIsIncrease ? (
                                  <TrendingUp className="w-3 h-3" />
                                ) : (
                                  <TrendingDown className="w-3 h-3" />
                                )
                              )}
                              {catChangePctNum !== 0 ? `${catChangePctNum > 0 ? '+' : ''}${catChangePctNum.toFixed(1)}%` : '--'}
                            </span>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                  <tfoot>
                    <tr style={{ background: theme.g100, borderTop: `2px solid ${theme.g200}` }}>
                      <td
                        className="px-5 py-3 text-xs font-bold uppercase tracking-wider"
                        style={{ color: theme.t2 }}
                      >
                        Total
                      </td>
                      <td
                        className="px-5 py-3 text-right font-bold font-mono-nums"
                        style={{ color: theme.text }}
                      >
                        {fmt(report.totals.current_total)}
                      </td>
                      <td
                        className="px-5 py-3 text-right font-semibold font-mono-nums"
                        style={{ color: theme.t2 }}
                      >
                        {fmt(report.totals.previous_total)}
                      </td>
                      <td
                        className="px-5 py-3 text-right font-bold font-mono-nums"
                        style={{
                          color: totalChange === 0 ? theme.t3 : isIncrease ? theme.er : theme.ok,
                        }}
                      >
                        {totalChange > 0 ? '+' : ''}{fmt(report.totals.change)}
                      </td>
                      <td className="px-5 py-3 text-right">
                        <span
                          className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-bold font-mono-nums"
                          style={{
                            background: totalChange === 0 ? theme.g100 : isIncrease ? theme.erB : theme.okB,
                            color: totalChange === 0 ? theme.t3 : isIncrease ? theme.er : theme.ok,
                          }}
                        >
                          {totalChange !== 0 && (
                            isIncrease ? (
                              <TrendingUp className="w-3 h-3" />
                            ) : (
                              <TrendingDown className="w-3 h-3" />
                            )
                          )}
                          {totalChangePct !== 0 ? `${totalChangePct > 0 ? '+' : ''}${totalChangePct.toFixed(1)}%` : '--'}
                        </span>
                      </td>
                    </tr>
                  </tfoot>
                </table>
              </div>
            </div>

            {/* Footer note */}
            <p className="text-xs text-center" style={{ color: theme.t3 }}>
              Period: {formatDate(report.from_date)} - {formatDate(report.to_date)} |{' '}
              Previous: {formatDate(report.totals.previous_from)} - {formatDate(report.totals.previous_to)}
            </p>
          </>
        )}

        {/* CFO directive 2026-05-21: AI variance commentary */}
        {aiResult && (
          <div className="rounded-xl p-4 border" style={{ background: theme.card, borderColor: theme.b1 }}>
            <h3 className="font-semibold mb-2" style={{ color: theme.t1 }}>
              AI variance commentary {aiResult.ai_available ? '' : '(degraded)'}
            </h3>
            {aiResult.analysis ? (
              <pre className="text-sm whitespace-pre-wrap" style={{ color: theme.t2 }}>
                {aiResult.analysis}
              </pre>
            ) : (
              <p className="text-sm" style={{ color: theme.t2 }}>
                {aiResult.message || aiResult.reason || 'No analysis returned.'}
              </p>
            )}
          </div>
        )}

        {/* Per-category drill-down: by Account / by Supplier / top JE lines */}
        {detail && detail.categories.length > 0 && (
          <div className="rounded-xl p-4 border" style={{ background: theme.card, borderColor: theme.b1 }}>
            <h3 className="font-semibold mb-3" style={{ color: theme.t1 }}>
              Category drill-down
            </h3>
            <p className="text-xs mb-3" style={{ color: theme.t3 }}>
              Click a category to expand the GL accounts, supplier breakdown, and the top 50 JE lines.
              Use "Export Excel" for the full audit pack.
            </p>
            <div className="space-y-2">
              {detail.categories.map(cat => {
                const isOpen = openCat === cat.id
                const amt = parseFloat(cat.amount)
                return (
                  <div key={cat.id} className="border rounded-lg" style={{ borderColor: theme.b1 }}>
                    <button
                      onClick={() => setOpenCat(isOpen ? null : cat.id)}
                      className="w-full flex items-center justify-between gap-3 py-2 px-3 text-sm min-w-0"
                      style={{ color: theme.t1 }}
                    >
                      <span className="min-w-0 truncate text-left">{isOpen ? '▼' : '▶'} {cat.label}</span>
                      <span className="font-mono flex-shrink-0 whitespace-nowrap">{formatAmount(amt, 'BWP', mode)}</span>
                    </button>
                    {isOpen && (
                      <div className="px-3 py-3 border-t space-y-3" style={{ borderColor: theme.b1 }}>
                        <div>
                          <div className="text-xs uppercase mb-1" style={{ color: theme.t3 }}>By GL account</div>
                          <table className="w-full text-xs">
                            <tbody>
                              {cat.by_account.map(a => (
                                <tr key={a.code}>
                                  <td className="py-0.5">{a.code} — {a.name}</td>
                                  <td className="py-0.5 text-right font-mono">{formatAmount(a.amount, 'BWP', mode)}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                        <div>
                          <div className="text-xs uppercase mb-1" style={{ color: theme.t3 }}>By supplier</div>
                          <table className="w-full text-xs">
                            <tbody>
                              {cat.by_supplier.slice(0, 25).map((s, i) => (
                                <tr key={i}>
                                  <td className="py-0.5">{s.contact_name}</td>
                                  <td className="py-0.5 text-right">{s.line_count} lines</td>
                                  <td className="py-0.5 text-right font-mono">{formatAmount(s.amount, 'BWP', mode)}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                          {cat.by_supplier.length > 25 && (
                            <p className="text-xs mt-1" style={{ color: theme.t3 }}>
                              Showing 25 of {cat.by_supplier.length} suppliers — Export Excel for the full list.
                            </p>
                          )}
                        </div>
                        <div>
                          <div className="text-xs uppercase mb-1" style={{ color: theme.t3 }}>
                            Top {cat.top_lines.length} JE lines
                          </div>
                          <table className="w-full text-xs">
                            <thead>
                              <tr className="text-left" style={{ color: theme.t3 }}>
                                <th>Date</th><th>JE #</th><th>Account</th><th>Supplier</th><th>Description</th><th className="text-right">Net</th>
                              </tr>
                            </thead>
                            <tbody>
                              {cat.top_lines.slice(0, 20).map((ln, i) => (
                                <tr key={i}>
                                  <td className="py-0.5">{ln.date}</td>
                                  <td className="py-0.5">{ln.entry_number}</td>
                                  <td className="py-0.5">{ln.account_code}</td>
                                  <td className="py-0.5">{ln.contact_name || '—'}</td>
                                  <td className="py-0.5 truncate max-w-[280px]">{ln.description}</td>
                                  <td className="py-0.5 text-right font-mono">{formatAmount(ln.net, 'BWP', mode)}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
