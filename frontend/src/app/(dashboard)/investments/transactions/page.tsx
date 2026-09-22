'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import {
  getToken,
  getInvestments,
  getInvestmentTransactions,
  postInvestmentTransaction,
  type Investment,
  type InvestmentTransaction,
} from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { TrendingUp, AlertTriangle, RefreshCw } from 'lucide-react'

function fmtMoney(s: string | null | undefined): string {
  if (s === null || s === undefined || s === '') return '—'
  const n = Number(s)
  return isFinite(n)
    ? n.toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : s
}

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return '—'
  return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })
}

function truncate(s: string | null | undefined, max = 50): string {
  if (!s) return ''
  return s.length > max ? `${s.slice(0, max - 1)}…` : s
}

const TYPE_BADGE: Record<string, { bg: string; fg: string }> = {
  purchase:   { bg: '#EFF6FF', fg: '#1D4ED8' },
  sale:       { bg: '#FEF3C7', fg: '#92400E' },
  coupon:     { bg: '#ECFDF5', fg: '#047857' },
  fair_value: { bg: '#FAF5FF', fg: '#6B21A8' },
  maturity:   { bg: '#F3F4F6', fg: '#6B7280' },
}

const STATUS_BADGE: Record<string, { bg: string; fg: string }> = {
  draft:  { bg: '#F3F4F6', fg: '#6B7280' },
  posted: { bg: '#ECFDF5', fg: '#047857' },
}

export default function InvestmentTransactionsPage() {
  const router = useRouter()
  const [items, setItems] = useState<InvestmentTransaction[]>([])
  const [investments, setInvestments] = useState<Investment[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState('')
  const [investmentFilter, setInvestmentFilter] = useState('')
  const [postingId, setPostingId] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const res = await getInvestmentTransactions({
        status: statusFilter || undefined,
        investment: investmentFilter || undefined,
      })
      setItems(res.results)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load transactions')
    } finally {
      setLoading(false)
    }
  }, [statusFilter, investmentFilter])

  const loadInvestments = useCallback(async () => {
    try {
      const res = await getInvestments()
      setInvestments(res.results)
    } catch {
      // non-fatal — filter dropdown just stays empty
    }
  }, [])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    void load()
  }, [router, load])

  useEffect(() => {
    if (!getToken()) return
    void loadInvestments()
  }, [loadInvestments])

  const handlePost = async (id: string) => {
    setPostingId(id); setError(null)
    try {
      await postInvestmentTransaction(id)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to post transaction')
    } finally {
      setPostingId(null)
    }
  }

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Investment Transactions"
        breadcrumbs={[
          { label: 'Investments', href: '/investments' },
          { label: 'Transactions' },
        ]}
        actions={
          <Button
            variant="outline" size="sm"
            leftIcon={<RefreshCw className="w-3.5 h-3.5" />}
            onClick={load} disabled={loading}
          >
            Refresh
          </Button>
        }
      />

      <div className="flex-1 p-6 space-y-4">
        {/* Status filter pills */}
        <Card>
          <CardContent className="p-4 flex flex-wrap items-center gap-3">
            <span className="text-xs font-medium text-[#374151]">Status:</span>
            {[
              { value: '',       label: 'All' },
              { value: 'draft',  label: 'Draft' },
              { value: 'posted', label: 'Posted' },
            ].map((opt) => (
              <button
                key={opt.value}
                onClick={() => setStatusFilter(opt.value)}
                className={`text-xs px-2.5 py-1 rounded border transition-colors ${
                  statusFilter === opt.value
                    ? 'bg-[#0B0B3B] text-white border-[#0B0B3B]'
                    : 'bg-white text-[#374151] border-[#D1D5DB] hover:bg-[#F9FAFB]'
                }`}
              >
                {opt.label}
              </button>
            ))}

            <span className="text-xs font-medium text-[#374151] ml-4">Investment:</span>
            <select
              value={investmentFilter}
              onChange={(e) => setInvestmentFilter(e.target.value)}
              className="text-xs px-2.5 py-1 rounded border border-[#D1D5DB] bg-white text-[#374151] hover:bg-[#F9FAFB] focus:outline-none focus:ring-1 focus:ring-[#0B0B3B] focus:border-[#0B0B3B]"
            >
              <option value="">All investments</option>
              {investments.map((inv) => (
                <option key={inv.id} value={inv.id}>
                  {inv.investment_number} — {inv.name}
                </option>
              ))}
            </select>
          </CardContent>
        </Card>

        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-[#DC2626]" />
            <p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}

        <Card>
          <CardContent className="p-0">
            {loading && (
              <p className="px-6 py-8 text-center text-sm text-[#6B7280]">Loading…</p>
            )}
            {!loading && items.length === 0 && (
              <div className="px-6 py-12 text-center text-[#6B7280]">
                <TrendingUp className="w-10 h-10 mx-auto text-[#D1D5DB] mb-3" strokeWidth={1.5} />
                <p className="text-sm">No transactions yet.</p>
                <p className="text-xs text-[#9CA3AF] mt-2">
                  Add transactions via Django admin under{' '}
                  <code>/admin/investments/investmenttransaction/</code>
                </p>
              </div>
            )}
            {!loading && items.length > 0 && (
              <div className="overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="bg-[#F9FAFB] border-b border-[#E5E7EB]">
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Transaction #</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Date</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Type</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Investment</th>
                      <th className="text-right px-4 py-2.5 font-semibold text-[#374151]">Amount</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Description</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Status</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">JE #</th>
                      <th className="text-right px-4 py-2.5 font-semibold text-[#374151]">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((tx) => {
                      const t = TYPE_BADGE[tx.transaction_type] || TYPE_BADGE.purchase
                      const s = STATUS_BADGE[tx.status] || STATUS_BADGE.draft
                      const amt = Number(tx.amount || 0)
                      const isFV = tx.transaction_type === 'fair_value'
                      const isPosting = postingId === tx.id
                      return (
                        <tr key={tx.id} className="border-b border-[#F3F4F6] hover:bg-[#F9FAFB]">
                          <td className="px-4 py-2.5 font-mono text-xs text-[#0B0B3B]">
                            {tx.transaction_number}
                          </td>
                          <td className="px-4 py-2.5 text-[#374151] text-xs">
                            {fmtDate(tx.transaction_date)}
                          </td>
                          <td className="px-4 py-2.5">
                            <span
                              className="inline-flex text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded border"
                              style={{ background: t.bg, color: t.fg, borderColor: `${t.fg}30` }}
                            >
                              {tx.transaction_type_display}
                            </span>
                          </td>
                          <td className="px-4 py-2.5 text-[#374151]">
                            <div className="text-xs font-mono text-[#0B0B3B]">{tx.investment_number}</div>
                            <div className="text-xs text-[#9CA3AF]">{tx.investment_name}</div>
                          </td>
                          <td
                            className="px-4 py-2.5 text-right font-mono tabular-nums"
                            style={{
                              color: isFV
                                ? (amt >= 0 ? '#047857' : '#B91C1C')
                                : '#374151',
                            }}
                          >
                            {isFV && amt >= 0 ? '+' : ''}{fmtMoney(tx.amount)}
                          </td>
                          <td className="px-4 py-2.5 text-xs text-[#374151]">
                            {truncate(tx.description, 50)}
                          </td>
                          <td className="px-4 py-2.5">
                            <span
                              className="inline-flex text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded border"
                              style={{ background: s.bg, color: s.fg, borderColor: `${s.fg}30` }}
                            >
                              {tx.status_display}
                            </span>
                          </td>
                          <td className="px-4 py-2.5 font-mono text-xs text-[#374151]">
                            {tx.je_number || '—'}
                          </td>
                          <td className="px-4 py-2.5 text-right">
                            {tx.status === 'draft' ? (
                              <Button
                                size="sm"
                                onClick={() => handlePost(tx.id)}
                                disabled={isPosting || postingId !== null}
                              >
                                {isPosting ? 'Posting…' : 'Post to GL'}
                              </Button>
                            ) : (
                              <span className="text-xs text-[#9CA3AF]">—</span>
                            )}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
