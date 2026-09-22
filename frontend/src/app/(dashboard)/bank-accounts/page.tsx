'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { getBankAccounts, getToken } from '@/lib/api'
import type { BankAccount } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { LoadingTable } from '@/components/ui/loading'
import { formatAmount, formatDate, parseAmount } from '@/lib/utils'
import { useNumberFormat } from '@/contexts/NumberFormatContext'
import { Landmark, AlertCircle, CheckCircle2, XCircle } from 'lucide-react'
import { useTheme } from '@/contexts/ThemeContext'
import SmartUpload from '@/components/SmartUpload'

export default function BankAccountsPage() {
  const router = useRouter()
  const { theme } = useTheme()
  const { mode } = useNumberFormat()
  const fmt = (amount: string | number, currency = 'BWP') => formatAmount(amount, currency, mode)
  const [accounts, setAccounts] = useState<BankAccount[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const token = getToken()
    if (!token) { router.replace('/login'); return }
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await getBankAccounts()
      setAccounts(res.results)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load bank accounts')
    } finally {
      setLoading(false)
    }
  }

  // QA-005 fix 2026-06-04: use GL-backed book_balance (same source as the
  // Cash Position report). Legacy current_balance is stale/0 → page showed
  // Total 0.00 while Cash Position showed 7.4M. Fall back to current_balance.
  const totalBalance = accounts.reduce((sum, a) => sum + parseAmount(a.book_balance ?? a.current_balance), 0)
  const activeCount = accounts.filter((a) => a.is_active).length

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Bank Accounts"
        breadcrumbs={[{ label: 'Banking' }, { label: 'Bank Accounts' }]}
      />

      <div className="flex-1 p-6 space-y-6">
        <SmartUpload section="bank_accounts" onCommitted={load} />
        {/* Summary cards */}
        {!loading && accounts.length > 0 && (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div
              className="rounded-lg p-5"
              style={{ background: theme.oL, border: `1px solid ${theme.orange}30` }}
            >
              <p className="text-xs font-medium uppercase tracking-wider" style={{ color: theme.orange }}>
                Total Balance
              </p>
              <p className="text-2xl font-bold font-mono-nums mt-2" style={{ color: theme.orange }}>
                {fmt(totalBalance)}
              </p>
              <p className="text-xs mt-1" style={{ color: theme.t3 }}>
                Across {accounts.length} account{accounts.length !== 1 ? 's' : ''}
              </p>
            </div>
            <div
              className="rounded-lg p-5"
              style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}
            >
              <p className="text-xs font-medium uppercase tracking-wider" style={{ color: theme.t2 }}>
                Active Accounts
              </p>
              <p className="text-2xl font-bold mt-2" style={{ color: theme.text }}>
                {activeCount}
              </p>
              <p className="text-xs mt-1" style={{ color: theme.t3 }}>
                of {accounts.length} total
              </p>
            </div>
            <div
              className="rounded-lg p-5"
              style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}
            >
              <p className="text-xs font-medium uppercase tracking-wider" style={{ color: theme.t2 }}>
                Currencies
              </p>
              <p className="text-2xl font-bold mt-2" style={{ color: theme.text }}>
                {new Set(accounts.map((a) => a.currency)).size}
              </p>
              <p className="text-xs mt-1" style={{ color: theme.t3 }}>
                {[...new Set(accounts.map((a) => a.currency))].join(', ')}
              </p>
            </div>
          </div>
        )}

        {/* Error */}
        {error && (
          <div
            className="rounded-lg p-4 flex items-center gap-3"
            style={{ background: theme.erB, border: `1px solid ${theme.er}30` }}
          >
            <AlertCircle className="w-5 h-5 flex-shrink-0" style={{ color: theme.er }} />
            <p className="text-sm" style={{ color: theme.er }}>{error}</p>
          </div>
        )}

        {/* Table */}
        <Card>
          <CardContent className="p-0">
            {loading ? (
              <LoadingTable rows={6} cols={7} />
            ) : accounts.length === 0 ? (
              <div className="py-16 text-center">
                <Landmark className="w-8 h-8 mx-auto mb-3" style={{ color: theme.g200 }} />
                <p className="font-medium" style={{ color: theme.t2 }}>No bank accounts found</p>
                <p className="text-sm mt-1" style={{ color: theme.t3 }}>
                  Run the setup commands to create bank accounts.
                </p>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead style={{ background: theme.g100, borderBottom: `2px solid ${theme.cardBdr}` }}>
                    <tr>
                      <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider" style={{ color: theme.g700 }}>
                        Bank
                      </th>
                      <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider" style={{ color: theme.g700 }}>
                        Account Name
                      </th>
                      <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider hidden md:table-cell" style={{ color: theme.g700 }}>
                        Account No.
                      </th>
                      <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider hidden lg:table-cell" style={{ color: theme.g700 }}>
                        GL Code
                      </th>
                      <th className="px-4 py-3 text-center text-xs font-semibold uppercase tracking-wider hidden sm:table-cell" style={{ color: theme.g700 }}>
                        Currency
                      </th>
                      <th className="px-4 py-3 text-center text-xs font-semibold uppercase tracking-wider hidden lg:table-cell" style={{ color: theme.g700 }}>
                        Status
                      </th>
                      <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wider" style={{ color: theme.g700 }}>
                        Balance
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {accounts.map((account, idx) => {
                      const bal = parseAmount(account.book_balance ?? account.current_balance)
                      return (
                        <tr
                          key={account.id}
                          className="transition-colors cursor-pointer"
                          style={{
                            background: theme.card,
                            borderBottom: idx < accounts.length - 1 ? `1px solid ${theme.cardBdr}` : 'none',
                          }}
                          onMouseEnter={(e) => { e.currentTarget.style.background = theme.oL }}
                          onMouseLeave={(e) => { e.currentTarget.style.background = theme.card }}
                          onClick={() => router.push(`/banking`)}
                        >
                          <td className="px-4 py-3">
                            <div className="flex items-center gap-2.5">
                              <div
                                className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0"
                                style={{ background: theme.g100 }}
                              >
                                <Landmark className="w-4 h-4" style={{ color: theme.orange }} strokeWidth={1.5} />
                              </div>
                              <span className="font-medium" style={{ color: theme.text }}>
                                {account.bank_name}
                              </span>
                            </div>
                          </td>
                          <td className="px-4 py-3">
                            <p className="font-medium" style={{ color: theme.text }}>{account.account_name}</p>
                            {account.last_reconciled_date && (
                              <p className="text-xs mt-0.5" style={{ color: theme.t3 }}>
                                Last reconciled: {formatDate(account.last_reconciled_date)}
                              </p>
                            )}
                          </td>
                          <td className="px-4 py-3 font-mono text-xs hidden md:table-cell" style={{ color: theme.t2 }}>
                            {account.account_number}
                            {account.branch_code && (
                              <span style={{ color: theme.t3 }}> / {account.branch_code}</span>
                            )}
                          </td>
                          <td className="px-4 py-3 hidden lg:table-cell">
                            <span className="font-mono text-xs px-2 py-0.5 rounded" style={{ background: theme.g100, color: theme.t2 }}>
                              {account.gl_account_code}
                            </span>
                          </td>
                          <td className="px-4 py-3 text-center text-xs hidden sm:table-cell" style={{ color: theme.t2 }}>
                            {account.currency}
                          </td>
                          <td className="px-4 py-3 text-center hidden lg:table-cell">
                            {account.is_active ? (
                              <span
                                className="inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded"
                                style={{ background: theme.okB, color: theme.ok }}
                              >
                                <CheckCircle2 className="w-3 h-3" />Active
                              </span>
                            ) : (
                              <span
                                className="inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded"
                                style={{ background: theme.g100, color: theme.t3 }}
                              >
                                <XCircle className="w-3 h-3" />Inactive
                              </span>
                            )}
                          </td>
                          <td className="px-4 py-3 text-right">
                            <span
                              className="font-mono-nums font-medium"
                              style={{ color: bal >= 0 ? theme.ok : theme.er }}
                            >
                              {fmt(account.book_balance ?? account.current_balance, account.currency)}
                            </span>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                  <tfoot style={{ background: theme.g100, borderTop: `2px solid ${theme.cardBdr}` }}>
                    <tr>
                      <td colSpan={6} className="px-4 py-3 text-sm font-bold uppercase tracking-wider" style={{ color: theme.text }}>
                        Total Balance
                      </td>
                      <td className="px-4 py-3 text-right text-lg font-bold font-mono-nums" style={{ color: theme.orange }}>
                        {fmt(totalBalance)}
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
