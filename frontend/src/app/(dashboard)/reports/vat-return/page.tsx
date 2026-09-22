'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { getVATReturn, getToken } from '@/lib/api'
import type { VATReturnReport } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
// Card components handled inline
import { Button } from '@/components/ui/button'
import { LoadingTable } from '@/components/ui/loading'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { formatAmount, formatDate, exportToCsv, cn } from '@/lib/utils'
import { useTheme } from '@/contexts/ThemeContext'
import { useNumberFormat } from '@/contexts/NumberFormatContext'
import {
  Download,
  RefreshCw,
  Calendar,
  FileText,
  ArrowUpRight,
  ArrowDownRight,
  AlertCircle,
} from 'lucide-react'

// ─── Bi-monthly period helpers ──────────────────────────────────────────────

function getCurrentBiMonthlyPeriod(): { from: string; to: string } {
  const now = new Date()
  const year = now.getFullYear()
  const month = now.getMonth() + 1 // 1-based

  // BURS bi-monthly periods: Jan-Feb, Mar-Apr, May-Jun, Jul-Aug, Sep-Oct, Nov-Dec
  // Find which period we are in, then return the start and end dates
  const periodStart = month % 2 === 0 ? month - 1 : month
  const periodEnd = periodStart + 1

  const startDate = `${year}-${String(periodStart).padStart(2, '0')}-01`

  // Calculate the last day of the end month
  const lastDay = new Date(year, periodEnd, 0).getDate()
  const endDate = `${year}-${String(periodEnd).padStart(2, '0')}-${String(lastDay).padStart(2, '0')}`

  return { from: startDate, to: endDate }
}

const PERIOD_NAMES: Record<number, string> = {
  1: 'Jan-Feb',
  3: 'Mar-Apr',
  5: 'May-Jun',
  7: 'Jul-Aug',
  9: 'Sep-Oct',
  11: 'Nov-Dec',
}

function getPeriodLabel(from: string): string {
  const month = parseInt(from.split('-')[1], 10)
  const year = from.split('-')[0]
  const periodStartMonth = month % 2 === 0 ? month - 1 : month
  return `${PERIOD_NAMES[periodStartMonth] || ''} ${year}`
}

// ─── Invoice row type ───────────────────────────────────────────────────────

interface VATInvoice {
  invoice_number: string
  contact_name: string
  issue_date: string
  net_amount: string
  vat_amount: string
  total_amount: string
}

// ─── Invoice Table ──────────────────────────────────────────────────────────

function InvoiceTable({
  invoices,
  totalLabel,
  totalNet,
  totalVat,
  theme,
}: {
  invoices: VATInvoice[]
  totalLabel: string
  totalNet: string
  totalVat: string
  theme: any
}) {
  const { mode } = useNumberFormat()
  const fmt = (amount: string | number, currency = 'BWP') => formatAmount(amount, currency, mode)
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr style={{ background: theme.g100 }}>
            <th
              className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider"
              style={{ color: theme.t2 }}
            >
              Invoice #
            </th>
            <th
              className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider"
              style={{ color: theme.t2 }}
            >
              Contact
            </th>
            <th
              className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider"
              style={{ color: theme.t2 }}
            >
              Issue Date
            </th>
            <th
              className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wider"
              style={{ color: theme.t2 }}
            >
              Net Amount
            </th>
            <th
              className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wider"
              style={{ color: theme.t2 }}
            >
              VAT Amount
            </th>
            <th
              className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wider"
              style={{ color: theme.t2 }}
            >
              Total
            </th>
          </tr>
        </thead>
        <tbody>
          {invoices.length === 0 ? (
            <tr>
              <td
                colSpan={6}
                className="px-4 py-8 text-center text-sm"
                style={{ color: theme.t3 }}
              >
                No invoices in this period
              </td>
            </tr>
          ) : (
            invoices.map((inv, idx) => (
              <tr
                key={idx}
                className="transition-colors"
                style={{ borderBottom: `1px solid ${theme.g200}` }}
                onMouseEnter={(e) => (e.currentTarget.style.background = theme.oL)}
                onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
              >
                <td
                  className="px-4 py-2.5 font-mono text-xs font-medium"
                  style={{ color: theme.orange }}
                >
                  {inv.invoice_number}
                </td>
                <td
                  className="px-4 py-2.5 font-medium"
                  style={{ color: theme.text }}
                >
                  {inv.contact_name}
                </td>
                <td
                  className="px-4 py-2.5 text-xs"
                  style={{ color: theme.t2 }}
                >
                  {formatDate(inv.issue_date)}
                </td>
                <td
                  className="px-4 py-2.5 text-right font-mono-nums text-xs"
                  style={{ color: theme.t2 }}
                >
                  {fmt(inv.net_amount)}
                </td>
                <td
                  className="px-4 py-2.5 text-right font-mono-nums font-medium"
                  style={{ color: theme.text }}
                >
                  {fmt(inv.vat_amount)}
                </td>
                <td
                  className="px-4 py-2.5 text-right font-mono-nums font-medium"
                  style={{ color: theme.text }}
                >
                  {fmt(inv.total_amount)}
                </td>
              </tr>
            ))
          )}
        </tbody>
        {invoices.length > 0 && (
          <tfoot>
            <tr style={{ background: theme.g100, borderTop: `2px solid ${theme.g200}` }}>
              <td
                colSpan={3}
                className="px-4 py-3 text-xs font-bold uppercase tracking-wider"
                style={{ color: theme.t2 }}
              >
                {totalLabel}
              </td>
              <td
                className="px-4 py-3 text-right font-semibold font-mono-nums"
                style={{ color: theme.t2 }}
              >
                {fmt(totalNet)}
              </td>
              <td
                className="px-4 py-3 text-right font-bold font-mono-nums"
                style={{ color: theme.text }}
              >
                {fmt(totalVat)}
              </td>
              <td className="px-4 py-3" />
            </tr>
          </tfoot>
        )}
      </table>
    </div>
  )
}

// ─── Summary Stat Card ──────────────────────────────────────────────────────

function SummaryCard({
  title,
  amount,
  icon: Icon,
  iconColor,
  iconBg,
  highlighted,
  highlightColor,
  highlightBg,
  subtitle,
  theme,
}: {
  title: string
  amount: string
  icon: React.ComponentType<any>
  iconColor: string
  iconBg: string
  highlighted?: boolean
  highlightColor?: string
  highlightBg?: string
  subtitle?: string
  theme: any
}) {
  const { mode } = useNumberFormat()
  const fmt = (amt: string | number, currency = 'BWP') => formatAmount(amt, currency, mode)
  return (
    <div
      className="rounded-xl p-5"
      style={{
        background: highlighted ? highlightBg : theme.card,
        border: `1px solid ${highlighted ? `${highlightColor}30` : theme.cardBdr}`,
        boxShadow: theme.cardSh,
      }}
    >
      <div className="flex items-center gap-3 mb-3">
        <div
          className="w-9 h-9 rounded-lg flex items-center justify-center"
          style={{ background: iconBg }}
        >
          <Icon className="w-4.5 h-4.5" style={{ color: iconColor }} strokeWidth={1.5} />
        </div>
        <h3 className="text-sm font-semibold" style={{ color: highlighted ? highlightColor : theme.text }}>
          {title}
        </h3>
      </div>
      <p
        className="text-xl font-bold font-mono-nums"
        style={{ color: highlighted ? highlightColor : theme.text }}
      >
        {fmt(amount)}
      </p>
      {subtitle && (
        <p
          className="text-xs mt-1"
          style={{ color: highlighted ? highlightColor : theme.t3, opacity: highlighted ? 0.8 : 1 }}
        >
          {subtitle}
        </p>
      )}
    </div>
  )
}

// ─── VAT Return Preparation Page ────────────────────────────────────────────

export default function VATReturnPage() {
  const router = useRouter()
  const { theme } = useTheme()
  const { mode } = useNumberFormat()
  const fmt = (amount: string | number, currency = 'BWP') => formatAmount(amount, currency, mode)
  const defaultPeriod = getCurrentBiMonthlyPeriod()
  const [fromDate, setFromDate] = useState(defaultPeriod.from)
  const [toDate, setToDate] = useState(defaultPeriod.to)
  const [report, setReport] = useState<VATReturnReport | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState('output')

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
      const data = await getVATReturn(fromDate, toDate)
      setReport(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to generate VAT return')
    } finally {
      setLoading(false)
    }
  }

  const handleExport = () => {
    if (!report) return
    const rows: Record<string, any>[] = []

    // Output VAT section
    rows.push({ Section: 'OUTPUT VAT', 'Invoice #': '', Contact: '', 'Issue Date': '', 'Net Amount': '', 'VAT Amount': '', Total: '' })
    report.output_vat.invoices.forEach((inv) =>
      rows.push({
        Section: 'Output VAT',
        'Invoice #': inv.invoice_number,
        Contact: inv.contact_name,
        'Issue Date': inv.issue_date,
        'Net Amount': inv.net_amount,
        'VAT Amount': inv.vat_amount,
        Total: inv.total_amount,
      })
    )
    rows.push({
      Section: 'Output VAT Total',
      'Invoice #': '',
      Contact: '',
      'Issue Date': '',
      'Net Amount': report.output_vat.total_sales,
      'VAT Amount': report.output_vat.total_vat,
      Total: '',
    })

    // Credit notes section
    rows.push({ Section: 'CREDIT NOTES', 'Invoice #': '', Contact: '', 'Issue Date': '', 'Net Amount': '', 'VAT Amount': '', Total: '' })
    report.credit_notes.invoices.forEach((inv) =>
      rows.push({
        Section: 'Credit Notes',
        'Invoice #': inv.invoice_number,
        Contact: inv.contact_name,
        'Issue Date': inv.issue_date,
        'Net Amount': inv.net_amount,
        'VAT Amount': inv.vat_amount,
        Total: inv.total_amount,
      })
    )
    rows.push({
      Section: 'Credit Notes Total',
      'Invoice #': '',
      Contact: '',
      'Issue Date': '',
      'Net Amount': report.credit_notes.total_sales,
      'VAT Amount': report.credit_notes.total_vat,
      Total: '',
    })

    // Input VAT section
    rows.push({ Section: 'INPUT VAT', 'Invoice #': '', Contact: '', 'Issue Date': '', 'Net Amount': '', 'VAT Amount': '', Total: '' })
    report.input_vat.invoices.forEach((inv) =>
      rows.push({
        Section: 'Input VAT',
        'Invoice #': inv.invoice_number,
        Contact: inv.contact_name,
        'Issue Date': inv.issue_date,
        'Net Amount': inv.net_amount,
        'VAT Amount': inv.vat_amount,
        Total: inv.total_amount,
      })
    )
    rows.push({
      Section: 'Input VAT Total',
      'Invoice #': '',
      Contact: '',
      'Issue Date': '',
      'Net Amount': report.input_vat.total_purchases,
      'VAT Amount': report.input_vat.total_vat,
      Total: '',
    })

    // Summary
    rows.push({ Section: '', 'Invoice #': '', Contact: '', 'Issue Date': '', 'Net Amount': '', 'VAT Amount': '', Total: '' })
    rows.push({ Section: 'SUMMARY', 'Invoice #': 'Output VAT on Sales', Contact: '', 'Issue Date': '', 'Net Amount': '', 'VAT Amount': report.summary.output_vat, Total: '' })
    rows.push({ Section: '', 'Invoice #': 'Less: Credit Note VAT', Contact: '', 'Issue Date': '', 'Net Amount': '', 'VAT Amount': report.summary.credit_note_vat, Total: '' })
    rows.push({ Section: '', 'Invoice #': 'Adjusted Output VAT', Contact: '', 'Issue Date': '', 'Net Amount': '', 'VAT Amount': report.summary.adjusted_output_vat, Total: '' })
    rows.push({ Section: '', 'Invoice #': 'Less: Input VAT on Purchases', Contact: '', 'Issue Date': '', 'Net Amount': '', 'VAT Amount': report.summary.input_vat, Total: '' })
    rows.push({ Section: '', 'Invoice #': report.summary.status_label, Contact: '', 'Issue Date': '', 'Net Amount': '', 'VAT Amount': report.summary.net_vat, Total: '' })

    // Reverse charge — imported remote services (VAT Amendment Act No.16 of
    // 2025). Two separate self-assessed lines, per BURS filing requirements.
    rows.push({ Section: '', 'Invoice #': '', Contact: '', 'Issue Date': '', 'Net Amount': '', 'VAT Amount': '', Total: '' })
    rows.push({ Section: 'REVERSE CHARGE — IMPORTED SERVICES', 'Invoice #': 'Output VAT – Imported Services (Reverse Charge)', Contact: '', 'Issue Date': '', 'Net Amount': '', 'VAT Amount': report.summary.reverse_charge_output_vat, Total: '' })
    rows.push({ Section: '', 'Invoice #': 'Input VAT – Imported Services (Reverse Charge)', Contact: '', 'Issue Date': '', 'Net Amount': '', 'VAT Amount': report.summary.reverse_charge_input_vat, Total: '' })
    rows.push({ Section: '', 'Invoice #': 'Net VAT Cost — Reverse Charge', Contact: '', 'Issue Date': '', 'Net Amount': '', 'VAT Amount': report.summary.reverse_charge_net_vat_cost, Total: '' })

    exportToCsv(rows, `vat_return_${fromDate}_${toDate}`)
  }

  const isPayable = report?.summary.vat_payable ?? true

  return (
    <div className="flex flex-col min-h-screen" style={{ background: theme.bg }}>
      <TopBar
        title="VAT Return Preparation"
        breadcrumbs={[{ label: 'Reports', href: '/reports' }, { label: 'VAT Return' }]}
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

            {report && (
              <div
                className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium"
                style={{
                  background: isPayable ? theme.erB : theme.okB,
                  color: isPayable ? theme.er : theme.ok,
                  border: `1px solid ${isPayable ? theme.er : theme.ok}30`,
                }}
              >
                <Calendar className="w-4 h-4" />
                {getPeriodLabel(fromDate)} &mdash; {report.summary.status_label}
              </div>
            )}
          </div>

          {/* Subtitle */}
          <p className="text-xs mt-3" style={{ color: theme.t3 }}>
            BURS VAT Return &mdash; Bi-monthly filing (14% standard rate)
          </p>
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
            <LoadingTable rows={10} cols={6} />
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
            <FileText className="w-10 h-10 mx-auto mb-3" style={{ color: theme.g200 }} />
            <p className="text-sm" style={{ color: theme.t3 }}>
              Select a bi-monthly period and click Generate
            </p>
          </div>
        )}

        {/* Report content */}
        {!loading && report && (
          <>
            {/* Summary Cards */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
              <SummaryCard
                title="Output VAT"
                amount={report.summary.output_vat}
                icon={ArrowUpRight}
                iconColor={theme.orange}
                iconBg={theme.oL}
                subtitle={`From ${report.output_vat.invoices.length} customer invoice(s)`}
                theme={theme}
              />
              <SummaryCard
                title="Credit Note Adjustments"
                amount={report.summary.credit_note_vat}
                icon={ArrowDownRight}
                iconColor={theme.wr}
                iconBg={theme.wrB}
                subtitle={`From ${report.credit_notes.invoices.length} credit note(s)`}
                theme={theme}
              />
              <SummaryCard
                title="Input VAT"
                amount={report.summary.input_vat}
                icon={ArrowDownRight}
                iconColor={theme.inf}
                iconBg={theme.inB}
                subtitle={`From ${report.input_vat.invoices.length} vendor bill(s)`}
                theme={theme}
              />
              <SummaryCard
                title="NET VAT"
                amount={report.summary.net_vat}
                icon={FileText}
                iconColor={isPayable ? theme.er : theme.ok}
                iconBg={isPayable ? theme.erB : theme.okB}
                highlighted
                highlightColor={isPayable ? theme.er : theme.ok}
                highlightBg={isPayable ? theme.erB : theme.okB}
                subtitle={report.summary.status_label}
                theme={theme}
              />
            </div>

            {/* Tabbed Invoice Details */}
            <div
              className="rounded-xl overflow-hidden"
              style={{
                background: theme.card,
                border: `1px solid ${theme.cardBdr}`,
                boxShadow: theme.cardSh,
              }}
            >
              <Tabs value={activeTab} onValueChange={setActiveTab}>
                <div
                  className="px-5 pt-4"
                  style={{ borderBottom: `1px solid ${theme.g200}` }}
                >
                  <TabsList>
                    <TabsTrigger value="output" count={report.output_vat.invoices.length}>
                      Output VAT
                    </TabsTrigger>
                    <TabsTrigger value="credit" count={report.credit_notes.invoices.length}>
                      Credit Notes
                    </TabsTrigger>
                    <TabsTrigger value="input" count={report.input_vat.invoices.length}>
                      Input VAT
                    </TabsTrigger>
                  </TabsList>
                </div>

                <TabsContent value="output">
                  <InvoiceTable
                    invoices={report.output_vat.invoices}
                    totalLabel="Total Output VAT"
                    totalNet={report.output_vat.total_sales}
                    totalVat={report.output_vat.total_vat}
                    theme={theme}
                  />
                </TabsContent>

                <TabsContent value="credit">
                  <InvoiceTable
                    invoices={report.credit_notes.invoices}
                    totalLabel="Total Credit Note VAT"
                    totalNet={report.credit_notes.total_sales}
                    totalVat={report.credit_notes.total_vat}
                    theme={theme}
                  />
                </TabsContent>

                <TabsContent value="input">
                  <InvoiceTable
                    invoices={report.input_vat.invoices}
                    totalLabel="Total Input VAT"
                    totalNet={report.input_vat.total_purchases}
                    totalVat={report.input_vat.total_vat}
                    theme={theme}
                  />
                </TabsContent>
              </Tabs>
            </div>

            {/* VAT Return Summary Calculation */}
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
                  VAT Return Summary
                </h3>
                <p className="text-xs mt-0.5" style={{ color: theme.t3 }}>
                  Period: {formatDate(report.from_date)} to {formatDate(report.to_date)}
                </p>
              </div>

              <div className="p-5 space-y-3">
                {/* Output VAT on Sales */}
                <div className="flex items-center justify-between py-2">
                  <span className="text-sm font-medium" style={{ color: theme.text }}>
                    Output VAT on Sales
                  </span>
                  <span className="text-sm font-mono-nums font-semibold" style={{ color: theme.text }}>
                    {fmt(report.summary.output_vat)}
                  </span>
                </div>

                {/* Less: Credit Note VAT */}
                <div
                  className="flex items-center justify-between py-2"
                  style={{ borderBottom: `1px solid ${theme.g200}` }}
                >
                  <span className="text-sm" style={{ color: theme.t2 }}>
                    Less: Credit Note VAT
                  </span>
                  <span className="text-sm font-mono-nums" style={{ color: theme.er }}>
                    ({fmt(report.summary.credit_note_vat)})
                  </span>
                </div>

                {/* Adjusted Output VAT */}
                <div
                  className="flex items-center justify-between py-2"
                  style={{ borderBottom: `1px solid ${theme.g200}` }}
                >
                  <span className="text-sm font-semibold" style={{ color: theme.text }}>
                    = Adjusted Output VAT
                  </span>
                  <span className="text-sm font-mono-nums font-bold" style={{ color: theme.text }}>
                    {fmt(report.summary.adjusted_output_vat)}
                  </span>
                </div>

                {/* Less: Input VAT on Purchases */}
                <div
                  className="flex items-center justify-between py-2"
                  style={{ borderBottom: `1px solid ${theme.g200}` }}
                >
                  <span className="text-sm" style={{ color: theme.t2 }}>
                    Less: Input VAT on Purchases
                  </span>
                  <span className="text-sm font-mono-nums" style={{ color: theme.inf }}>
                    ({fmt(report.summary.input_vat)})
                  </span>
                </div>

                {/* Net VAT Payable / (Refundable) */}
                <div
                  className="flex items-center justify-between pt-4 mt-2"
                  style={{ borderTop: `2px solid ${isPayable ? theme.er : theme.ok}30` }}
                >
                  <div className="flex items-center gap-2">
                    {isPayable ? (
                      <ArrowUpRight className="w-5 h-5" style={{ color: theme.er }} />
                    ) : (
                      <ArrowDownRight className="w-5 h-5" style={{ color: theme.ok }} />
                    )}
                    <span
                      className="text-sm font-bold uppercase tracking-wider"
                      style={{ color: isPayable ? theme.er : theme.ok }}
                    >
                      {report.summary.status_label}
                    </span>
                  </div>
                  <span
                    className="text-xl font-bold font-mono-nums"
                    style={{ color: isPayable ? theme.er : theme.ok }}
                  >
                    {fmt(report.summary.net_vat)}
                  </span>
                </div>

                {/* Reverse Charge — Imported Remote Services (VAT Amendment
                    Act No.16 of 2025, effective 1 June 2026). BURS files
                    these self-assessed amounts as two separate lines —
                    distinct from the domestic Output/Input VAT above, not
                    netted into them. See /reverse-charge to capture entries. */}
                <div className="pt-4 mt-2" style={{ borderTop: `1px dashed ${theme.g200}` }}>
                  <p
                    className="text-xs font-semibold uppercase tracking-wider mb-2"
                    style={{ color: theme.t3 }}
                  >
                    Reverse Charge — Imported Remote Services
                  </p>

                  {/* Output VAT – Imported Services (Reverse Charge) */}
                  <div className="flex items-center justify-between py-2">
                    <span className="text-sm font-medium" style={{ color: theme.text }}>
                      Output VAT &ndash; Imported Services (Reverse Charge)
                    </span>
                    <span className="text-sm font-mono-nums font-semibold" style={{ color: theme.text }}>
                      {fmt(report.summary.reverse_charge_output_vat)}
                    </span>
                  </div>

                  {/* Input VAT – Imported Services (Reverse Charge) */}
                  <div
                    className="flex items-center justify-between py-2"
                    style={{ borderBottom: `1px solid ${theme.g200}` }}
                  >
                    <span className="text-sm" style={{ color: theme.t2 }}>
                      Input VAT &ndash; Imported Services (Reverse Charge)
                    </span>
                    <span className="text-sm font-mono-nums" style={{ color: theme.inf }}>
                      ({fmt(report.summary.reverse_charge_input_vat)})
                    </span>
                  </div>

                  {/* Net cost beneath the two lines above */}
                  <div className="flex items-center justify-between pt-3 mt-1">
                    <span className="text-sm font-bold uppercase tracking-wider" style={{ color: theme.t2 }}>
                      Net VAT Cost &mdash; Reverse Charge
                    </span>
                    <span
                      className="text-base font-bold font-mono-nums"
                      style={{
                        color: report.summary.reverse_charge_net_vat_cost === '0.00'
                          ? theme.ok
                          : theme.wr,
                      }}
                    >
                      {fmt(report.summary.reverse_charge_net_vat_cost)}
                    </span>
                  </div>
                  <p className="text-xs mt-1" style={{ color: theme.t3 }}>
                    Zero when input VAT is fully recovered. A cost only appears when
                    input recovery has been restricted (e.g. behind VAT-exempt insurance income).
                  </p>
                </div>
              </div>
            </div>

            {/* Footer note */}
            <p className="text-xs text-center" style={{ color: theme.t3 }}>
              VAT Return for period {formatDate(report.from_date)} to {formatDate(report.to_date)} &mdash; Rate: 14% (VAT_STD) &mdash; Filed with BURS
            </p>
          </>
        )}
      </div>
    </div>
  )
}
