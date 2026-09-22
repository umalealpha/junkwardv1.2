'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { downloadReportXlsx, getCashPosition, getToken } from '@/lib/api'
import type { CashPositionReport } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { LoadingTable } from '@/components/ui/loading'
import { formatAmount, formatDate, parseAmount, exportToCsv, today, cn } from '@/lib/utils'
import { useNumberFormat } from '@/contexts/NumberFormatContext'
import { useCompany } from '@/contexts/CompanyContext'
import { Download, AlertCircle, RefreshCw, Wallet } from 'lucide-react'

// ─── Cash Position Report ─────────────────────────────────────────────────────

export default function CashPositionPage() {
  const router = useRouter()
  const { mode } = useNumberFormat()
  const { selectedId: companyId, selected: selectedCompany } = useCompany()
  // CFO directive 2026-05-21: entity functional currency
  const currency = (selectedCompany?.base_currency || 'BWP') as string
  const fmt = (amount: string | number, c: string = currency) => formatAmount(amount, c, mode)
  const [report, setReport] = useState<CashPositionReport | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const token = getToken()
    if (!token) {
      router.replace('/login')
      return
    }
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [companyId])

  const load = async (silent = false) => {
    if (silent) setRefreshing(true)
    else setLoading(true)
    setError(null)
    try {
      const data = await getCashPosition(companyId)
      setReport(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load cash position')
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }

  const handleExport = () => {
    if (!report) return
    const rows = report.accounts.map((acc) => ({
      'Account Code': acc.account_code,
      'Account Name': acc.account_name,
      Currency: acc.currency,
      'Native Balance': acc.balance_native,
      'BWP Balance': acc.balance_bwp,
      'Exchange Rate': acc.exchange_rate,
    }))
    rows.push({
      'Account Code': 'TOTAL',
      'Account Name': 'Total Cash Position',
      Currency: 'BWP',
      'Native Balance': report.total_bwp,
      'BWP Balance': report.total_bwp,
      'Exchange Rate': '1.0000',
    })
    exportToCsv(rows, `cash_position_${today()}`)
  }

  const totalBwp = report ? parseAmount(report.total_bwp) : 0

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Cash Position"
        breadcrumbs={[{ label: 'Reports', href: '/reports' }, { label: 'Cash Position' }]}
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
              <>
                <Button
                  variant="secondary"
                  size="sm"
                  leftIcon={<Download className="w-3.5 h-3.5" />}
                  onClick={handleExport}
                >
                  Export CSV
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  leftIcon={<Download className="w-3.5 h-3.5" />}
                  onClick={() => downloadReportXlsx('cash_position', {
                    company: companyId ?? undefined,
                  })}
                >
                  Export Excel
                </Button>
              </>
            )}
          </div>
        }
      />

      <div className="flex-1 p-6 space-y-6">
        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-xl p-3 flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-[#DC2626]" />
            <p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}

        {/* Total Card */}
        {!loading && report && (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div className="sm:col-span-1 bg-[#FFF7ED] border border-[#FED7AA] rounded-xl p-5">
              <p className="text-xs text-[#CC6C00] uppercase tracking-wider">Total Cash Position</p>
              <p className="text-3xl font-bold text-[#CC6C00] font-mono-nums mt-2">
                {fmt(totalBwp)}
              </p>
              <p className="text-xs text-[#9CA3AF] mt-1">
                As of {formatDate(report.as_of)} · {report.accounts.length} accounts
              </p>
            </div>
            <div className="bg-white border border-[#E5E7EB] rounded-xl p-5">
              <p className="text-xs text-[#6B7280] uppercase tracking-wider">Accounts with Activity</p>
              <p className="text-2xl font-bold text-[#0B0B3B] mt-2">
                {report.accounts.filter((a) => a.has_activity).length}
              </p>
              <p className="text-xs text-[#9CA3AF] mt-1">of {report.accounts.length} total accounts</p>
            </div>
            <div className="bg-white border border-[#E5E7EB] rounded-xl p-5">
              <p className="text-xs text-[#6B7280] uppercase tracking-wider">Currencies</p>
              <p className="text-2xl font-bold text-[#0B0B3B] mt-2">
                {new Set(report.accounts.map((a) => a.currency)).size}
              </p>
              <p className="text-xs text-[#9CA3AF] mt-1">
                {[...new Set(report.accounts.map((a) => a.currency))].join(', ')}
              </p>
            </div>
          </div>
        )}

        <Card>
          <CardContent className="p-0">
            {loading ? (
              <LoadingTable rows={6} cols={6} />
            ) : !report ? (
              <div className="py-12 text-center">
                <Wallet className="w-8 h-8 text-[#D1D5DB] mx-auto mb-2" />
                <p className="text-[#9CA3AF] text-sm">No cash position data</p>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="bg-[#F3F4F6] border-b-2 border-[#E5E7EB]">
                    <tr>
                      <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">
                        Account Code
                      </th>
                      <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">
                        Account Name
                      </th>
                      <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider hidden md:table-cell">
                        Currency
                      </th>
                      <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider hidden lg:table-cell">
                        Native Balance
                      </th>
                      <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider hidden md:table-cell">
                        Rate
                      </th>
                      <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider">
                        BWP Balance
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#E5E7EB] bg-white">
                    {report.accounts.map((account, idx) => (
                      <tr
                        key={idx}
                        className={cn(
                          'table-row-alt transition-colors',
                          !account.has_activity && 'opacity-60'
                        )}
                      >
                        <td className="px-4 py-3 font-mono text-xs text-[#9CA3AF]">
                          {account.account_code}
                        </td>
                        <td className="px-4 py-3 text-[#111827] font-medium">
                          {account.account_name}
                          {!account.has_activity && (
                            <span className="ml-2 text-xs text-[#D1D5DB]">(no activity)</span>
                          )}
                        </td>
                        <td className="px-4 py-3 text-[#6B7280] text-xs hidden md:table-cell">
                          {account.currency}
                        </td>
                        <td className="px-4 py-3 text-right font-mono-nums text-[#6B7280] hidden lg:table-cell">
                          {account.currency !== 'BWP'
                            ? fmt(account.balance_native, account.currency)
                            : '—'}
                        </td>
                        <td className="px-4 py-3 text-right text-[#9CA3AF] text-xs hidden md:table-cell">
                          {account.currency !== 'BWP'
                            ? parseFloat(account.exchange_rate).toFixed(4)
                            : '1.0000'}
                        </td>
                        <td className="px-4 py-3 text-right font-mono-nums font-medium">
                          <span
                            className={
                              parseAmount(account.balance_bwp) >= 0
                                ? 'text-[#059669]'
                                : 'text-[#DC2626]'
                            }
                          >
                            {fmt(account.balance_bwp)}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot className="bg-[#F3F4F6] border-t-2 border-[#E5E7EB]">
                    <tr>
                      <td colSpan={5} className="px-4 py-3 text-sm font-bold text-[#111827] uppercase tracking-wider">
                        Total Cash Position
                      </td>
                      <td className="px-4 py-3 text-right text-lg font-bold font-mono-nums text-[#CC6C00]">
                        {fmt(report.total_bwp)}
                      </td>
                    </tr>
                  </tfoot>
                </table>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
