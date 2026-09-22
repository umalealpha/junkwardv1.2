'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams, useRouter } from 'next/navigation'
import Link from 'next/link'
import {
  getToken,
  getInvestmentTransactions,
  postInvestmentTransaction,
  type InvestmentTransaction,
} from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  ArrowLeft, Send, AlertTriangle, CheckCircle2, TrendingUp,
} from 'lucide-react'

function fmtMoney(s: string | null | undefined): string {
  if (s === null || s === undefined || s === '') return '—'
  const n = Number(s)
  if (!isFinite(n)) return s as string
  return Math.abs(n).toLocaleString('en-GB', {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  })
}
function fmtDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('en-GB', {
    day: '2-digit', month: 'short', year: 'numeric',
  })
}
function fmtDateTime(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('en-GB', {
    day: '2-digit', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

const STATUS_BADGE: Record<string, { bg: string; fg: string }> = {
  draft:  { bg: '#F3F4F6', fg: '#374151' },
  posted: { bg: '#ECFDF5', fg: '#047857' },
}

const TYPE_BADGE: Record<string, { bg: string; fg: string }> = {
  purchase:   { bg: '#EFF6FF', fg: '#1D4ED8' },
  sale:       { bg: '#FFFBEB', fg: '#92400E' },
  coupon:     { bg: '#ECFDF5', fg: '#047857' },
  fair_value: { bg: '#F5F3FF', fg: '#6D28D9' },
  maturity:   { bg: '#F3F4F6', fg: '#374151' },
}

export default function InvestmentTransactionDetailPage() {
  const router = useRouter()
  const params = useParams<{ id: string }>()
  const id = params?.id as string

  const [tx, setTx] = useState<InvestmentTransaction | null>(null)
  const [loading, setLoading] = useState(true)
  const [acting, setActing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notFound, setNotFound] = useState(false)

  const load = useCallback(async () => {
    setLoading(true); setError(null); setNotFound(false)
    try {
      const all = await getInvestmentTransactions()
      const found = all.results.find((t) => t.id === id)
      if (!found) {
        setNotFound(true)
        setTx(null)
      } else {
        setTx(found)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load transaction')
    } finally {
      setLoading(false)
    }
  }, [id])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    void load()
  }, [router, load])

  async function handlePost() {
    if (!tx) return
    setActing(true); setError(null)
    try {
      const updated = await postInvestmentTransaction(tx.id)
      setTx(updated)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to post transaction')
    } finally {
      setActing(false)
    }
  }

  if (loading) {
    return (
      <div className="flex flex-col min-h-screen">
        <TopBar
          title="Transaction"
          breadcrumbs={[
            { label: 'Investments', href: '/investments' },
            { label: 'Transactions', href: '/investments/transactions' },
            { label: '…' },
          ]}
        />
        <div className="flex-1 p-6 text-sm text-[#6B7280]">Loading…</div>
      </div>
    )
  }

  if (notFound || !tx) {
    return (
      <div className="flex flex-col min-h-screen">
        <TopBar
          title="Transaction not found"
          breadcrumbs={[
            { label: 'Investments', href: '/investments' },
            { label: 'Transactions', href: '/investments/transactions' },
            { label: 'Not found' },
          ]}
          actions={
            <Link href="/investments/transactions">
              <Button variant="outline" size="sm" leftIcon={<ArrowLeft className="w-3.5 h-3.5" />}>
                Back
              </Button>
            </Link>
          }
        />
        <div className="flex-1 p-6 max-w-4xl">
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 text-[#DC2626] flex-shrink-0 mt-0.5" />
            <p className="text-[#DC2626] text-sm">
              {error || 'No investment transaction with this id.'}
            </p>
          </div>
        </div>
      </div>
    )
  }

  const statusColour = STATUS_BADGE[tx.status] || STATUS_BADGE.draft
  const typeColour = TYPE_BADGE[tx.transaction_type] || TYPE_BADGE.purchase
  const amountNum = Number(tx.amount)
  const isFV = tx.transaction_type === 'fair_value'
  const fvPositive = isFV && isFinite(amountNum) && amountNum >= 0
  const fvNegative = isFV && isFinite(amountNum) && amountNum < 0

  function jePreview(): string {
    const abs = `P ${fmtMoney(tx!.amount)}`
    switch (tx!.transaction_type) {
      case 'purchase':
        return `DR investment / CR cash account — ${abs}`
      case 'sale':
        return `DR cash account / CR investment — ${abs}`
      case 'coupon':
        return `DR cash account / CR 4500 Investment income — ${abs}`
      case 'fair_value':
        if (fvPositive) return `DR investment / CR 4500 Investment income — ${abs} (FV uplift)`
        return `DR 4500 Investment income / CR investment — ${abs} (FV write-down)`
      case 'maturity':
        return `DR cash account / CR investment — ${abs} (full redemption; investment status flips to MATURED)`
      default:
        return `Posting transaction ${abs}`
    }
  }

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title={tx.transaction_number}
        subtitle={`${tx.transaction_type_display} — ${tx.investment_number}`}
        breadcrumbs={[
          { label: 'Investments', href: '/investments' },
          { label: 'Transactions', href: '/investments/transactions' },
          { label: tx.transaction_number },
        ]}
        actions={
          <Link href="/investments/transactions">
            <Button variant="outline" size="sm" leftIcon={<ArrowLeft className="w-3.5 h-3.5" />}>
              Back
            </Button>
          </Link>
        }
      />

      <div className="flex-1 p-6 max-w-4xl space-y-4">
        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 text-[#DC2626] flex-shrink-0 mt-0.5" />
            <p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}

        {/* Status banner */}
        <Card>
          <CardContent className="p-4">
            <div className="flex items-center justify-between">
              <span
                className="inline-flex text-xs font-semibold uppercase tracking-wider px-2.5 py-1 rounded border"
                style={{
                  background: statusColour.bg,
                  color: statusColour.fg,
                  borderColor: `${statusColour.fg}30`,
                }}
              >
                {tx.status_display}
              </span>
              {tx.status === 'posted' && tx.je_number && (
                <p className="text-xs text-[#047857]">
                  Posted to journal entry <span className="font-mono">{tx.je_number}</span>
                </p>
              )}
            </div>
          </CardContent>
        </Card>

        {/* Transaction details */}
        <Card>
          <CardHeader>
            <CardTitle className="font-mono">{tx.transaction_number}</CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-3 text-sm">
              <div>
                <dt className="text-[#6B7280]">Type</dt>
                <dd>
                  <span
                    className="inline-flex text-xs font-semibold uppercase tracking-wider px-2 py-0.5 rounded border"
                    style={{
                      background: typeColour.bg,
                      color: typeColour.fg,
                      borderColor: `${typeColour.fg}30`,
                    }}
                  >
                    {tx.transaction_type_display}
                  </span>
                </dd>
              </div>
              <div>
                <dt className="text-[#6B7280]">Date</dt>
                <dd className="text-[#111827]">{fmtDate(tx.transaction_date)}</dd>
              </div>
              <div>
                <dt className="text-[#6B7280]">Investment</dt>
                <dd>
                  <Link
                    href={`/investments/${tx.investment}`}
                    className="text-[#1D4ED8] hover:underline"
                  >
                    <span className="font-mono">{tx.investment_number}</span>
                    {tx.investment_name && (
                      <span className="text-[#374151]"> · {tx.investment_name}</span>
                    )}
                  </Link>
                </dd>
              </div>
              <div className="md:text-right">
                <dt className="text-[#6B7280] md:text-right">Amount</dt>
                <dd
                  className="font-mono tabular-nums text-lg md:text-right"
                  style={{
                    color: fvPositive ? '#047857' : fvNegative ? '#B91C1C' : '#111827',
                  }}
                >
                  {isFV && fvPositive ? '+' : ''}
                  {isFV && fvNegative ? '-' : ''}
                  BWP {fmtMoney(tx.amount)}
                </dd>
              </div>
              <div>
                <dt className="text-[#6B7280]">Cash account</dt>
                <dd className="text-[#111827]">
                  {isFV ? '—' : (tx.cash_account ? <span className="font-mono">{tx.cash_account}</span> : '—')}
                </dd>
              </div>
              <div className="md:col-span-2">
                <dt className="text-[#6B7280]">Description</dt>
                <dd className="text-[#111827] whitespace-pre-wrap">
                  {tx.description || '—'}
                </dd>
              </div>
            </dl>
          </CardContent>
        </Card>

        {/* Journal-entry preview (draft only) */}
        {tx.status === 'draft' && (
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <TrendingUp className="w-4 h-4 text-[#0B0B3B]" />
                Journal entry preview
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <p className="text-sm text-[#374151]">{jePreview()}</p>
              <div>
                <Button
                  onClick={handlePost}
                  disabled={acting}
                  leftIcon={<Send className="w-4 h-4" />}
                >
                  {acting ? 'Posting…' : 'Post to GL'}
                </Button>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Workflow trail */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4 text-[#0B0B3B]" />
              Workflow trail
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-2 text-sm">
              <li className="flex gap-3">
                <span className="text-[#9CA3AF] w-32 flex-shrink-0">Created</span>
                <span className="text-[#374151]">{fmtDateTime(tx.created_at)}</span>
              </li>
              <li className="flex gap-3">
                <span className="text-[#9CA3AF] w-32 flex-shrink-0">Posted by</span>
                <span className="text-[#374151]">
                  {tx.posted_by_username ? tx.posted_by_username : '—'}
                </span>
              </li>
              <li className="flex gap-3">
                <span className="text-[#9CA3AF] w-32 flex-shrink-0">Posted at</span>
                <span className="text-[#374151]">{fmtDateTime(tx.posted_at)}</span>
              </li>
              <li className="flex gap-3">
                <span className="text-[#9CA3AF] w-32 flex-shrink-0">Journal entry</span>
                <span className="text-[#374151] font-mono">
                  {tx.je_number || '—'}
                </span>
              </li>
            </ul>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
