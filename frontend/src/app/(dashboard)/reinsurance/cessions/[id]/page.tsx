'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams, useRouter } from 'next/navigation'
import Link from 'next/link'
import {
  getToken,
  getCessions,
  postCession,
  type Cession,
} from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  ArrowLeft,
  Send,
  AlertTriangle,
  CheckCircle2,
  Receipt,
} from 'lucide-react'

function fmtMoney(s: string | null | undefined): string {
  if (s === null || s === undefined || s === '') return '—'
  const n = Number(s)
  if (!isFinite(n)) return s
  return n.toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
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

const STATUS_COLOURS: Record<string, { bg: string; fg: string; border: string }> = {
  draft:  { bg: '#F3F4F6', fg: '#374151', border: '#D1D5DB' },
  posted: { bg: '#ECFDF5', fg: '#047857', border: '#A7F3D0' },
  voided: { bg: '#FEF2F2', fg: '#B91C1C', border: '#FECACA' },
}

export default function CessionDetailPage() {
  const router = useRouter()
  const params = useParams<{ id: string }>()
  const id = params?.id as string

  const [cession, setCession] = useState<Cession | null>(null)
  const [loading, setLoading] = useState(true)
  const [notFound, setNotFound] = useState(false)
  const [posting, setPosting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    setNotFound(false)
    try {
      const page = await getCessions({})
      const found = page.results.find((c) => c.id === id)
      if (!found) {
        setNotFound(true)
        setCession(null)
      } else {
        setCession(found)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load cession')
    } finally {
      setLoading(false)
    }
  }, [id])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    void load()
  }, [router, load])

  async function handlePost() {
    if (!cession) return
    setPosting(true)
    setError(null)
    try {
      const updated = await postCession(cession.id)
      setCession(updated)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to post cession')
    } finally {
      setPosting(false)
    }
  }

  // Loading state
  if (loading) {
    return (
      <div className="flex flex-col min-h-screen">
        <TopBar
          title="Cession"
          breadcrumbs={[
            { label: 'Reinsurance', href: '/reinsurance/cessions' },
            { label: 'Cessions', href: '/reinsurance/cessions' },
            { label: '…' },
          ]}
          actions={
            <Link href="/reinsurance/cessions">
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

  // Not found
  if (notFound || !cession) {
    return (
      <div className="flex flex-col min-h-screen">
        <TopBar
          title="Cession"
          breadcrumbs={[
            { label: 'Reinsurance', href: '/reinsurance/cessions' },
            { label: 'Cessions', href: '/reinsurance/cessions' },
            { label: 'Not found' },
          ]}
          actions={
            <Link href="/reinsurance/cessions">
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
          <Card>
            <CardContent className="p-6 text-sm text-[#6B7280]">
              <p className="mb-3">Cession not found.</p>
              <Link href="/reinsurance/cessions">
                <Button variant="outline" size="sm" leftIcon={<ArrowLeft className="w-3.5 h-3.5" />}>
                  Back to cessions
                </Button>
              </Link>
            </CardContent>
          </Card>
        </div>
      </div>
    )
  }

  const colour = STATUS_COLOURS[cession.status] || STATUS_COLOURS.draft

  // Net premium payable to reinsurer = ceded - commission (computed for the GL preview).
  const cededNum = Number(cession.ceded_premium) || 0
  const commissionNum = Number(cession.commission_amount) || 0
  const netPayable = cededNum - commissionNum

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title={cession.cession_number}
        breadcrumbs={[
          { label: 'Reinsurance', href: '/reinsurance/cessions' },
          { label: 'Cessions', href: '/reinsurance/cessions' },
          { label: cession.cession_number },
        ]}
        actions={
          <Link href="/reinsurance/cessions">
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
        <div
          className="rounded-lg border p-4 flex items-center justify-between"
          style={{ background: colour.bg, borderColor: colour.border }}
        >
          <div className="flex items-center gap-3">
            <span
              className="inline-flex text-sm font-semibold uppercase tracking-wider px-3 py-1 rounded-full"
              style={{ background: 'white', color: colour.fg, border: `1px solid ${colour.border}` }}
            >
              {cession.status_display}
            </span>
            {cession.status === 'posted' && cession.je_number && (
              <span className="text-sm" style={{ color: colour.fg }}>
                Posted to journal entry{' '}
                <span className="font-mono font-semibold">{cession.je_number}</span>
              </span>
            )}
          </div>
          {cession.status === 'posted' && (
            <CheckCircle2 className="w-5 h-5" style={{ color: colour.fg }} />
          )}
        </div>

        {/* Header card — cession details */}
        <Card>
          <CardHeader>
            <CardTitle className="font-mono">{cession.cession_number}</CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-3 text-sm">
              <div>
                <dt className="text-[#6B7280]">Cession date</dt>
                <dd className="text-[#111827]">{fmtDate(cession.cession_date)}</dd>
              </div>
              <div>
                <dt className="text-[#6B7280]">Treaty</dt>
                <dd className="text-[#111827]">
                  <span className="font-mono">{cession.treaty_number}</span>
                  <span className="text-[#6B7280]"> · {cession.reinsurer_short_code}</span>
                </dd>
              </div>
              <div>
                <dt className="text-[#6B7280]">Policy reference</dt>
                <dd className="text-[#111827] font-mono">{cession.policy_reference || '—'}</dd>
              </div>
              <div>
                <dt className="text-[#6B7280]">Created</dt>
                <dd className="text-[#111827]">{fmtDateTime(cession.created_at)}</dd>
              </div>
              <div className="md:col-span-2">
                <dt className="text-[#6B7280]">Risk description</dt>
                <dd className="text-[#111827] whitespace-pre-wrap">
                  {cession.risk_description || '—'}
                </dd>
              </div>
              <div>
                <dt className="text-[#6B7280]">Gross premium</dt>
                <dd className="text-[#111827] font-mono tabular-nums text-right">
                  BWP {fmtMoney(cession.gross_premium)}
                </dd>
              </div>
              <div>
                <dt className="text-[#6B7280]">Ceded premium</dt>
                <dd className="text-[#111827] font-mono tabular-nums text-right text-lg">
                  BWP {fmtMoney(cession.ceded_premium)}
                </dd>
              </div>
              <div>
                <dt className="text-[#6B7280]">Commission</dt>
                <dd className="text-[#111827] font-mono tabular-nums text-right">
                  BWP {fmtMoney(cession.commission_amount)}
                </dd>
              </div>
              <div>
                <dt className="text-[#6B7280]">Net payable to reinsurer</dt>
                <dd className="text-[#111827] font-mono tabular-nums text-right">
                  BWP {fmtMoney(netPayable.toFixed(2))}
                </dd>
              </div>
            </dl>
          </CardContent>
        </Card>

        {/* Workflow card — only for drafts */}
        {cession.status === 'draft' && (
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Send className="w-4 h-4 text-[#F07F00]" />
                Post to general ledger
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-[#374151] leading-relaxed mb-4">
                Posting will create one journal entry:{' '}
                <span className="font-semibold">DR 4200 Reinsurance premium ceded</span>{' '}
                <span className="font-mono tabular-nums">BWP {fmtMoney(cession.ceded_premium)}</span>,{' '}
                <span className="font-semibold">CR 2120 Reinsurance premium payable</span>{' '}
                <span className="font-mono tabular-nums">BWP {fmtMoney(netPayable.toFixed(2))}</span>.
                {commissionNum > 0 && (
                  <>
                    {' '}If commission &gt; 0:{' '}
                    <span className="font-semibold">CR 4400 Commission income</span>{' '}
                    <span className="font-mono tabular-nums">
                      BWP {fmtMoney(cession.commission_amount)}
                    </span>.
                  </>
                )}
                {' '}After posting this cession is locked.
              </p>
              <Button
                onClick={handlePost}
                disabled={posting}
                leftIcon={<Send className="w-4 h-4" />}
              >
                {posting ? 'Posting…' : 'Post to GL'}
              </Button>
            </CardContent>
          </Card>
        )}

        {/* Workflow trail card — always visible */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Receipt className="w-4 h-4 text-[#0B0B3B]" />
              Workflow trail
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-2 text-sm">
              <li className="flex gap-3">
                <span className="text-[#9CA3AF] w-32 flex-shrink-0">Posted by</span>
                <span className="text-[#374151]">
                  {cession.posted_by_username || '—'}
                </span>
              </li>
              <li className="flex gap-3">
                <span className="text-[#9CA3AF] w-32 flex-shrink-0">Posted at</span>
                <span className="text-[#374151]">{fmtDateTime(cession.posted_at)}</span>
              </li>
              <li className="flex gap-3">
                <span className="text-[#9CA3AF] w-32 flex-shrink-0">Journal entry</span>
                <span className="text-[#374151] font-mono">
                  {cession.je_number || '—'}
                </span>
              </li>
              {cession.status === 'voided' && (
                <li className="flex gap-3">
                  <span className="text-[#B91C1C] w-32 flex-shrink-0">Status</span>
                  <span className="text-[#B91C1C]">Voided</span>
                </li>
              )}
            </ul>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
