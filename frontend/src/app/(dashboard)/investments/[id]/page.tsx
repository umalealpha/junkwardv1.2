'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams, useRouter } from 'next/navigation'
import Link from 'next/link'
import {
  getToken,
  getInvestments,
  getInvestmentTransactions,
  postInvestmentTransaction,
  type Investment,
  type InvestmentTransaction,
} from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  ArrowLeft,
  TrendingUp,
  ArrowUpRight,
  ArrowDownLeft,
  Send,
  AlertTriangle,
} from 'lucide-react'

function fmtMoney(s: string | number | null | undefined): string {
  if (s === null || s === undefined || s === '') return '—'
  const n = typeof s === 'number' ? s : Number(s)
  return isFinite(n)
    ? n.toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : String(s)
}

function fmtDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('en-GB', {
    day: '2-digit', month: 'short', year: 'numeric',
  })
}

const CLASSIFICATION_BADGE: Record<string, { bg: string; fg: string }> = {
  fvtpl:          { bg: '#FEF3C7', fg: '#92400E' },
  fvoci:          { bg: '#EFF6FF', fg: '#1D4ED8' },
  amortised_cost: { bg: '#ECFDF5', fg: '#047857' },
}

const STATUS_BADGE: Record<string, { bg: string; fg: string }> = {
  open:     { bg: '#ECFDF5', fg: '#047857' },
  matured:  { bg: '#EFF6FF', fg: '#1D4ED8' },
  disposed: { bg: '#F3F4F6', fg: '#374151' },
}

const TX_TYPE_BADGE: Record<string, { bg: string; fg: string }> = {
  purchase:   { bg: '#EFF6FF', fg: '#1D4ED8' },
  sale:       { bg: '#FEF3C7', fg: '#92400E' },
  coupon:     { bg: '#ECFDF5', fg: '#047857' },
  fair_value: { bg: '#FAF5FF', fg: '#6B21A8' },
  maturity:   { bg: '#F3F4F6', fg: '#374151' },
}

const TX_STATUS_BADGE: Record<string, { bg: string; fg: string }> = {
  draft:  { bg: '#F3F4F6', fg: '#374151' },
  posted: { bg: '#ECFDF5', fg: '#047857' },
}

function truncate(s: string, n: number): string {
  if (!s) return ''
  return s.length > n ? s.slice(0, n - 1) + '…' : s
}

export default function InvestmentDetailPage() {
  const router = useRouter()
  const params = useParams<{ id: string }>()
  const id = params?.id as string

  const [investment, setInvestment] = useState<Investment | null>(null)
  const [transactions, setTransactions] = useState<InvestmentTransaction[]>([])
  const [loading, setLoading] = useState(true)
  const [notFound, setNotFound] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [posting, setPosting] = useState(false)

  const load = useCallback(async () => {
    setLoading(true); setError(null); setNotFound(false)
    try {
      const [invRes, txRes] = await Promise.all([
        getInvestments(),
        getInvestmentTransactions({ investment: id }),
      ])
      const match = invRes.results.find((i) => i.id === id)
      if (!match) {
        setNotFound(true)
        setInvestment(null)
        setTransactions([])
      } else {
        setInvestment(match)
        setTransactions(txRes.results)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load investment')
    } finally {
      setLoading(false)
    }
  }, [id])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    void load()
  }, [router, load])

  async function handlePost(txId: string) {
    if (posting) return
    setPosting(true); setError(null)
    try {
      await postInvestmentTransaction(txId)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to post transaction')
    } finally {
      setPosting(false)
    }
  }

  if (loading && !investment) {
    return (
      <div className="flex flex-col min-h-screen">
        <TopBar
          title="Investment"
          breadcrumbs={[{ label: 'Investments', href: '/investments' }, { label: '…' }]}
          actions={
            <Link href="/investments">
              <Button variant="outline" size="sm" leftIcon={<ArrowLeft className="w-3.5 h-3.5" />}>
                Back
              </Button>
            </Link>
          }
        />
        <div className="flex-1 p-6 text-sm text-[#6B7280]">Loading…</div>
      </div>
    )
  }

  if (notFound || !investment) {
    return (
      <div className="flex flex-col min-h-screen">
        <TopBar
          title="Investment not found"
          breadcrumbs={[{ label: 'Investments', href: '/investments' }, { label: 'Not found' }]}
          actions={
            <Link href="/investments">
              <Button variant="outline" size="sm" leftIcon={<ArrowLeft className="w-3.5 h-3.5" />}>
                Back
              </Button>
            </Link>
          }
        />
        <div className="flex-1 p-6">
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-4 flex items-start gap-2 max-w-2xl">
            <AlertTriangle className="w-5 h-5 text-[#DC2626] flex-shrink-0 mt-0.5" />
            <div className="text-sm text-[#B91C1C]">
              <p className="font-semibold mb-1">No investment matches this id.</p>
              <p>It may have been removed, or the link is stale.</p>
            </div>
          </div>
        </div>
      </div>
    )
  }

  const cls = CLASSIFICATION_BADGE[investment.classification] || CLASSIFICATION_BADGE.fvtpl
  const stat = STATUS_BADGE[investment.status] || STATUS_BADGE.open
  const pl = Number(investment.unrealised_pl || 0)

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title={investment.name}
        subtitle={investment.investment_number}
        breadcrumbs={[
          { label: 'Investments', href: '/investments' },
          { label: investment.investment_number },
        ]}
        actions={
          <Link href="/investments">
            <Button variant="outline" size="sm" leftIcon={<ArrowLeft className="w-3.5 h-3.5" />}>
              Back
            </Button>
          </Link>
        }
      />

      <div className="flex-1 p-6 space-y-4">
        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 text-[#DC2626] flex-shrink-0 mt-0.5" />
            <p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}

        {/* KPI tiles */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <Card>
            <CardContent className="p-4">
              <p className="text-[10px] uppercase tracking-wider text-[#6B7280]">Cost</p>
              <p className="text-2xl font-bold font-mono tabular-nums text-[#374151]">
                BWP {fmtMoney(investment.cost)}
              </p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-4">
              <p className="text-[10px] uppercase tracking-wider text-[#6B7280]">Fair value</p>
              <p className="text-2xl font-bold font-mono tabular-nums text-[#0B0B3B]">
                BWP {fmtMoney(investment.current_fair_value)}
              </p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-4">
              <p className="text-[10px] uppercase tracking-wider text-[#6B7280]">Unrealised P&L</p>
              <p
                className="text-2xl font-bold font-mono tabular-nums flex items-center gap-1"
                style={{ color: pl >= 0 ? '#047857' : '#B91C1C' }}
              >
                {pl >= 0
                  ? <ArrowUpRight className="w-5 h-5" />
                  : <ArrowDownLeft className="w-5 h-5" />}
                {pl >= 0 ? '+' : '-'}P {fmtMoney(Math.abs(pl))}
              </p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-4">
              <p className="text-[10px] uppercase tracking-wider text-[#6B7280]">Transactions</p>
              <p className="text-2xl font-bold text-[#0B0B3B]">{investment.transaction_count}</p>
            </CardContent>
          </Card>
        </div>

        {/* Position details */}
        <Card>
          <CardHeader>
            <div className="flex items-baseline justify-between">
              <CardTitle className="flex items-center gap-2">
                <TrendingUp className="w-4 h-4 text-[#0B0B3B]" />
                Position details
              </CardTitle>
              <span
                className="inline-flex text-xs font-semibold uppercase tracking-wider px-2.5 py-1 rounded border"
                style={{ background: stat.bg, color: stat.fg, borderColor: `${stat.fg}30` }}
              >
                {investment.status_display}
              </span>
            </div>
          </CardHeader>
          <CardContent>
            <dl className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-3 text-sm">
              <div>
                <dt className="text-[#6B7280]">Instrument type</dt>
                <dd className="text-[#111827]">{investment.instrument_type_display}</dd>
              </div>
              <div>
                <dt className="text-[#6B7280]">IFRS 9 classification</dt>
                <dd>
                  <span
                    className="inline-flex text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded border"
                    style={{ background: cls.bg, color: cls.fg, borderColor: `${cls.fg}30` }}
                  >
                    {investment.classification.toUpperCase()}
                  </span>
                </dd>
              </div>
              <div>
                <dt className="text-[#6B7280]">Issuer</dt>
                <dd className="text-[#111827]">{investment.issuer || '—'}</dd>
              </div>
              <div>
                <dt className="text-[#6B7280]">Custodian</dt>
                <dd className="text-[#111827]">{investment.custodian || '—'}</dd>
              </div>
              <div>
                <dt className="text-[#6B7280]">Currency</dt>
                <dd className="text-[#111827] font-mono">{investment.currency_code}</dd>
              </div>
              <div>
                <dt className="text-[#6B7280]">Face value</dt>
                <dd className="text-[#111827] font-mono tabular-nums">{fmtMoney(investment.face_value)}</dd>
              </div>
              <div>
                <dt className="text-[#6B7280]">Cost</dt>
                <dd className="text-[#111827] font-mono tabular-nums">{fmtMoney(investment.cost)}</dd>
              </div>
              <div>
                <dt className="text-[#6B7280]">Current fair value</dt>
                <dd className="text-[#111827] font-mono tabular-nums">{fmtMoney(investment.current_fair_value)}</dd>
              </div>
              <div>
                <dt className="text-[#6B7280]">Coupon rate</dt>
                <dd className="text-[#111827] font-mono tabular-nums">
                  {investment.coupon_rate_percent ? `${investment.coupon_rate_percent}%` : '—'}
                </dd>
              </div>
              <div>
                <dt className="text-[#6B7280]">ISIN / reference</dt>
                <dd className="text-[#111827] font-mono text-xs">{investment.isin_or_ref || '—'}</dd>
              </div>
              <div>
                <dt className="text-[#6B7280]">Purchase date</dt>
                <dd className="text-[#111827]">{fmtDate(investment.purchase_date)}</dd>
              </div>
              <div>
                <dt className="text-[#6B7280]">Maturity date</dt>
                <dd className="text-[#111827]">{fmtDate(investment.maturity_date)}</dd>
              </div>
              <div className="md:col-span-2">
                <dt className="text-[#6B7280]">Investment account</dt>
                <dd className="text-[#111827]">
                  <span className="font-mono">{investment.investment_account_code}</span>
                  {' · '}
                  <span className="font-mono">{investment.investment_account_name}</span>
                </dd>
              </div>
              {investment.notes && (
                <div className="md:col-span-2">
                  <dt className="text-[#6B7280]">Notes</dt>
                  <dd className="text-[#111827] whitespace-pre-wrap">{investment.notes}</dd>
                </div>
              )}
            </dl>
          </CardContent>
        </Card>

        {/* Transactions */}
        <Card>
          <CardHeader>
            <CardTitle>Transactions</CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            {transactions.length === 0 ? (
              <p className="px-6 py-8 text-center text-sm text-[#6B7280]">
                No transactions on this position yet.
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="bg-[#F9FAFB] border-b border-[#E5E7EB]">
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Transaction #</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Date</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Type</th>
                      <th className="text-right px-4 py-2.5 font-semibold text-[#374151]">Amount</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Description</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Status</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">JE #</th>
                      <th className="text-right px-4 py-2.5 font-semibold text-[#374151]">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {transactions.map((tx) => {
                      const tType = TX_TYPE_BADGE[tx.transaction_type] || TX_TYPE_BADGE.purchase
                      const tStat = TX_STATUS_BADGE[tx.status] || TX_STATUS_BADGE.draft
                      const amt = Number(tx.amount || 0)
                      const isFV = tx.transaction_type === 'fair_value'
                      const amtColor = isFV
                        ? (amt >= 0 ? '#047857' : '#B91C1C')
                        : '#374151'
                      const amtPrefix = isFV && amt >= 0 ? '+' : ''
                      return (
                        <tr key={tx.id} className="border-b border-[#F3F4F6] hover:bg-[#F9FAFB]">
                          <td className="px-4 py-2.5 font-mono text-xs text-[#0B0B3B]">
                            {tx.transaction_number}
                          </td>
                          <td className="px-4 py-2.5 text-[#374151]">
                            {fmtDate(tx.transaction_date)}
                          </td>
                          <td className="px-4 py-2.5">
                            <span
                              className="inline-flex text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded border"
                              style={{ background: tType.bg, color: tType.fg, borderColor: `${tType.fg}30` }}
                            >
                              {tx.transaction_type_display}
                            </span>
                          </td>
                          <td
                            className="px-4 py-2.5 text-right font-mono tabular-nums"
                            style={{ color: amtColor }}
                          >
                            {amtPrefix}{fmtMoney(tx.amount)}
                          </td>
                          <td className="px-4 py-2.5 text-[#374151]">
                            {truncate(tx.description || '', 60)}
                          </td>
                          <td className="px-4 py-2.5">
                            <span
                              className="inline-flex text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded border"
                              style={{ background: tStat.bg, color: tStat.fg, borderColor: `${tStat.fg}30` }}
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
                                disabled={posting}
                                leftIcon={<Send className="w-3.5 h-3.5" />}
                              >
                                {posting ? 'Posting…' : 'Post to GL'}
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
