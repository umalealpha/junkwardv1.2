'use client'

// B6 Claims Payment Movement Report (Bontle Tendani).
//
// Accounting > Reporting. Every claims payment analysed by TYPE and by whether
// it settled a SUPPLIER INVOICE or went to an INDIVIDUAL CLAIMANT.
//
// It is NOT a bank reconciliation — the accounting reconciliation of the bank
// stays in Odoo. Nothing here moves, releases or posts anything.
//
// The screen shows whether each row was de-grossed for VAT, because the whole
// reason the report exists is VAT being stripped off payments that never
// carried it. A person reading the table can see the rule being applied rather
// than having to trust it.
//
// Access is enforced on the server (permission_classes on the view). This page
// being reachable is not the control; a non-finance user gets a 403.

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  downloadClaimsPaymentMovementXlsx,
  getClaimsPaymentMovement,
  getToken,
} from '@/lib/api'
import type {
  ClaimsPaymentMovementBucket,
  ClaimsPaymentMovementReport,
} from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { LoadingTable } from '@/components/ui/loading'
import { formatAmount, formatDate, cn } from '@/lib/utils'
import { useNumberFormat } from '@/contexts/NumberFormatContext'
import { AlertCircle, Download, RefreshCw, Receipt } from 'lucide-react'

function firstOfThisMonth(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-01`
}

function todayIso(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(
    d.getDate(),
  ).padStart(2, '0')}`
}

export default function ClaimsPaymentMovementPage() {
  const router = useRouter()
  const { mode } = useNumberFormat()
  const fmt = (amount: string | number) => formatAmount(amount, 'BWP', mode)

  const [from, setFrom] = useState(firstOfThisMonth())
  const [to, setTo] = useState(todayIso())
  const [report, setReport] = useState<ClaimsPaymentMovementReport | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!getToken()) {
      router.replace('/login')
      return
    }
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const load = async (silent = false) => {
    if (silent) setRefreshing(true)
    else setLoading(true)
    setError(null)
    try {
      setReport(await getClaimsPaymentMovement(from, to))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load the report')
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }

  const subtotal = (buckets: ClaimsPaymentMovementBucket[], title: string) => (
    <Card>
      <CardContent className="p-0">
        <div className="px-4 py-3 border-b border-[#E5E7EB]">
          <p className="text-xs font-semibold text-[#374151] uppercase tracking-wider">
            {title}
          </p>
        </div>
        <table className="w-full text-sm">
          <thead className="bg-[#F3F4F6] border-b border-[#E5E7EB]">
            <tr>
              <th className="px-4 py-2 text-left text-xs font-semibold text-[#374151]">
                Category
              </th>
              <th className="px-4 py-2 text-right text-xs font-semibold text-[#374151]">
                Payments
              </th>
              <th className="px-4 py-2 text-right text-xs font-semibold text-[#374151]">
                Gross
              </th>
              <th className="px-4 py-2 text-right text-xs font-semibold text-[#374151]">
                Excl. VAT
              </th>
              <th className="px-4 py-2 text-right text-xs font-semibold text-[#374151]">
                VAT
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[#E5E7EB] bg-white">
            {buckets.map((b) => (
              <tr key={b.key} className="table-row-alt">
                <td className="px-4 py-2 text-[#111827] font-medium">{b.label}</td>
                <td className="px-4 py-2 text-right font-mono-nums text-[#6B7280]">
                  {b.count}
                </td>
                <td className="px-4 py-2 text-right font-mono-nums">
                  {fmt(b.amount_gross)}
                </td>
                <td className="px-4 py-2 text-right font-mono-nums">
                  {fmt(b.amount_excl_vat)}
                </td>
                <td className="px-4 py-2 text-right font-mono-nums">
                  {fmt(b.vat_amount)}
                </td>
              </tr>
            ))}
            {buckets.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-6 text-center text-[#9CA3AF] text-sm">
                  No payments in this period
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </CardContent>
    </Card>
  )

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Claims Payment Movement Report"
        breadcrumbs={[
          { label: 'Reports', href: '/reports' },
          { label: 'Claims Payment Movement' },
        ]}
        actions={
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              loading={refreshing}
              leftIcon={<RefreshCw className="w-3.5 h-3.5" />}
              onClick={() => load(true)}
            >
              Refresh
            </Button>
            {report && (
              <Button
                variant="secondary"
                size="sm"
                leftIcon={<Download className="w-3.5 h-3.5" />}
                onClick={() => downloadClaimsPaymentMovementXlsx(from, to)}
              >
                Export Excel
              </Button>
            )}
          </div>
        }
      />

      <div className="flex-1 p-6 space-y-6">
        {/* Period picker */}
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label className="block text-xs text-[#6B7280] mb-1">From</label>
            <input
              type="date"
              value={from}
              onChange={(e) => setFrom(e.target.value)}
              className="border border-[#E5E7EB] rounded-lg px-3 py-2 text-sm"
            />
          </div>
          <div>
            <label className="block text-xs text-[#6B7280] mb-1">To</label>
            <input
              type="date"
              value={to}
              onChange={(e) => setTo(e.target.value)}
              className="border border-[#E5E7EB] rounded-lg px-3 py-2 text-sm"
            />
          </div>
          <Button size="sm" onClick={() => load(true)} loading={refreshing}>
            Run report
          </Button>
        </div>

        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-xl p-3 flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-[#DC2626]" />
            <p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}

        {!loading && report && (
          <>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <div className="bg-[#FFF7ED] border border-[#FED7AA] rounded-xl p-5">
                <p className="text-xs text-[#CC6C00] uppercase tracking-wider">
                  Total claims payments (gross)
                </p>
                <p className="text-3xl font-bold text-[#CC6C00] font-mono-nums mt-2">
                  {fmt(report.total.amount_gross)}
                </p>
                <p className="text-xs text-[#9CA3AF] mt-1">
                  {report.total.count} payments · {formatDate(report.from)} to{' '}
                  {formatDate(report.to)}
                </p>
              </div>
              <div className="bg-white border border-[#E5E7EB] rounded-xl p-5">
                <p className="text-xs text-[#6B7280] uppercase tracking-wider">
                  VAT de-grossed
                </p>
                <p className="text-2xl font-bold text-[#0B0B3B] font-mono-nums mt-2">
                  {fmt(report.total.vat_amount)}
                </p>
                <p className="text-xs text-[#9CA3AF] mt-1">
                  from {report.degrossed_count} invoice-basis payment
                  {report.degrossed_count === 1 ? '' : 's'}
                </p>
              </div>
              <div className="bg-white border border-[#E5E7EB] rounded-xl p-5">
                <p className="text-xs text-[#6B7280] uppercase tracking-wider">
                  Left gross
                </p>
                <p className="text-2xl font-bold text-[#0B0B3B] font-mono-nums mt-2">
                  {report.left_gross_count}
                </p>
                <p className="text-xs text-[#9CA3AF] mt-1">
                  repair, AOL, FOR, CIL, third party, ex-gratia and
                  unreadable lines are never de-grossed
                </p>
              </div>
            </div>

            {subtotal(report.by_type, 'By payment type')}
            {subtotal(report.by_settlement, 'Supplier invoice vs individual claimant')}
          </>
        )}

        <Card>
          <CardContent className="p-0">
            {loading ? (
              <LoadingTable rows={8} cols={8} />
            ) : !report || report.rows.length === 0 ? (
              <div className="py-12 text-center">
                <Receipt className="w-8 h-8 text-[#D1D5DB] mx-auto mb-2" />
                <p className="text-[#9CA3AF] text-sm">
                  No claims payments found in this period
                </p>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="bg-[#F3F4F6] border-b-2 border-[#E5E7EB]">
                    <tr>
                      {['Date', 'Payee', 'Claim', 'Invoice', 'Type', 'Settled against'].map(
                        (h) => (
                          <th
                            key={h}
                            className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider"
                          >
                            {h}
                          </th>
                        ),
                      )}
                      {['Gross', 'Excl. VAT', 'VAT'].map((h) => (
                        <th
                          key={h}
                          className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider"
                        >
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#E5E7EB] bg-white">
                    {report.rows.map((r, i) => (
                      <tr key={i} className="table-row-alt transition-colors">
                        <td className="px-4 py-3 text-[#6B7280] whitespace-nowrap">
                          {formatDate(r.transaction_date)}
                        </td>
                        <td className="px-4 py-3 text-[#111827] font-medium">
                          {r.payee}
                        </td>
                        <td className="px-4 py-3 font-mono text-xs text-[#6B7280]">
                          {r.claim_reference || '—'}
                        </td>
                        <td className="px-4 py-3 font-mono text-xs text-[#6B7280]">
                          {r.invoice_reference || '—'}
                        </td>
                        <td className="px-4 py-3">
                          <span
                            className={cn(
                              'text-xs px-2 py-0.5 rounded-full',
                              r.was_degrossed
                                ? 'bg-[#FFF7ED] text-[#CC6C00]'
                                : 'bg-[#F3F4F6] text-[#6B7280]',
                            )}
                          >
                            {r.payment_basis_label}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-[#6B7280] text-xs">
                          {r.settlement === 'supplier_invoice'
                            ? 'Supplier invoice'
                            : 'Individual claimant'}
                        </td>
                        <td className="px-4 py-3 text-right font-mono-nums">
                          {fmt(r.amount_gross)}
                        </td>
                        <td className="px-4 py-3 text-right font-mono-nums">
                          {fmt(r.amount_excl_vat)}
                        </td>
                        <td className="px-4 py-3 text-right font-mono-nums text-[#6B7280]">
                          {r.was_degrossed ? fmt(r.vat_amount) : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>

        {report && report.notes.length > 0 && (
          <div className="text-xs text-[#9CA3AF] space-y-1">
            {report.notes.map((n, i) => (
              <p key={i}>• {n}</p>
            ))}
            <p>
              • Contains payee names against claim amounts. Any wider distribution
              must be summarised or anonymised.
            </p>
          </div>
        )}
      </div>
    </div>
  )
}
