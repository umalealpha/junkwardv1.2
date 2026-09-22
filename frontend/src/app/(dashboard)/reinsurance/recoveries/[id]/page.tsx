'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams, useRouter } from 'next/navigation'
import Link from 'next/link'
import {
  getToken,
  getReinsuranceRecoveries,
  postReinsuranceRecovery,
  type ReinsuranceRecovery,
} from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  ArrowLeft, Send, AlertTriangle, CheckCircle2, Coins,
} from 'lucide-react'

function fmtMoney(s: string | null | undefined): string {
  if (!s) return '—'
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

const STATUS_BADGE: Record<string, { bg: string; fg: string }> = {
  draft:   { bg: '#F3F4F6', fg: '#374151' },
  posted:  { bg: '#ECFDF5', fg: '#047857' },
  settled: { bg: '#EFF6FF', fg: '#1D4ED8' },
  voided:  { bg: '#FEF2F2', fg: '#B91C1C' },
}

export default function ReinsuranceRecoveryDetailPage() {
  const router = useRouter()
  const params = useParams<{ id: string }>()
  const id = params?.id as string

  const [r, setR] = useState<ReinsuranceRecovery | null>(null)
  const [loading, setLoading] = useState(true)
  const [posting, setPosting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notFound, setNotFound] = useState(false)

  const load = useCallback(async () => {
    setLoading(true); setError(null); setNotFound(false)
    try {
      const list = await getReinsuranceRecoveries({})
      const found = list.results.find((x) => x.id === id)
      if (!found) {
        setNotFound(true)
        setR(null)
      } else {
        setR(found)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load recovery')
    } finally {
      setLoading(false)
    }
  }, [id])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    void load()
  }, [router, load])

  async function handlePost() {
    if (!r) return
    setPosting(true); setError(null)
    try {
      await postReinsuranceRecovery(r.id)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to post recovery')
    } finally {
      setPosting(false)
    }
  }

  if (loading) {
    return (
      <div className="flex flex-col min-h-screen">
        <TopBar
          title="Recovery"
          breadcrumbs={[
            { label: 'Reinsurance', href: '/reinsurance/recoveries' },
            { label: 'Recoveries', href: '/reinsurance/recoveries' },
            { label: '…' },
          ]}
        />
        <div className="flex-1 p-6 text-sm text-[#6B7280]">Loading…</div>
      </div>
    )
  }

  if (notFound || !r) {
    return (
      <div className="flex flex-col min-h-screen">
        <TopBar
          title="Recovery"
          breadcrumbs={[
            { label: 'Reinsurance', href: '/reinsurance/recoveries' },
            { label: 'Recoveries', href: '/reinsurance/recoveries' },
            { label: 'Not found' },
          ]}
          actions={
            <Link href="/reinsurance/recoveries">
              <Button variant="outline" size="sm" leftIcon={<ArrowLeft className="w-3.5 h-3.5" />}>
                Back
              </Button>
            </Link>
          }
        />
        <div className="flex-1 p-6 max-w-4xl">
          <Card>
            <CardContent className="p-6 flex items-start gap-3">
              <AlertTriangle className="w-5 h-5 text-[#B91C1C] flex-shrink-0 mt-0.5" />
              <div>
                <p className="text-sm font-medium text-[#111827]">Recovery not found</p>
                <p className="text-xs text-[#6B7280] mt-1">
                  The recovery with id <span className="font-mono">{id}</span> could not be located.
                </p>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    )
  }

  const c = STATUS_BADGE[r.status] || STATUS_BADGE.draft

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title={r.recovery_number}
        breadcrumbs={[
          { label: 'Reinsurance', href: '/reinsurance/recoveries' },
          { label: 'Recoveries', href: '/reinsurance/recoveries' },
          { label: r.recovery_number },
        ]}
        actions={
          <Link href="/reinsurance/recoveries">
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
          className="rounded-lg border px-4 py-3 flex items-center justify-between"
          style={{ background: c.bg, borderColor: `${c.fg}30` }}
        >
          <span
            className="inline-flex text-sm font-semibold uppercase tracking-wider"
            style={{ color: c.fg }}
          >
            {r.status_display}
          </span>
          {r.status === 'posted' && r.je_number && (
            <span className="text-xs" style={{ color: c.fg }}>
              Posted to journal entry <span className="font-mono">{r.je_number}</span>
            </span>
          )}
        </div>

        {/* Recovery details */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Coins className="w-4 h-4 text-[#0B0B3B]" />
              Recovery details
            </CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-3 text-sm">
              <div>
                <dt className="text-[#6B7280]">Recovery date</dt>
                <dd className="text-[#111827]">{fmtDate(r.recovery_date)}</dd>
              </div>
              <div>
                <dt className="text-[#6B7280]">Claim reference</dt>
                <dd className="text-[#111827] font-mono">{r.claim_reference}</dd>
              </div>
              <div className="md:col-span-2">
                <dt className="text-[#6B7280]">Treaty</dt>
                <dd className="text-[#111827]">
                  {r.treaty_number}
                  <span className="text-[#6B7280]"> · </span>
                  <span className="font-mono">{r.reinsurer_short_code}</span>
                </dd>
              </div>
              <div>
                <dt className="text-[#6B7280]">Gross loss</dt>
                <dd className="text-[#111827] font-mono tabular-nums text-right">
                  BWP {fmtMoney(r.gross_loss)}
                </dd>
              </div>
              <div>
                <dt className="text-[#6B7280]">Ceded recovery</dt>
                <dd className="text-[#111827] font-mono tabular-nums text-right text-lg">
                  BWP {fmtMoney(r.ceded_recovery)}
                </dd>
              </div>
              {r.notes && (
                <div className="md:col-span-2">
                  <dt className="text-[#6B7280]">Notes</dt>
                  <dd className="text-[#111827] whitespace-pre-wrap">{r.notes}</dd>
                </div>
              )}
            </dl>
          </CardContent>
        </Card>

        {/* Workflow card — draft only */}
        {r.status === 'draft' && (
          <Card>
            <CardHeader>
              <CardTitle>Post recovery to general ledger</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-[#374151]">
                Posting will create one journal entry: DR 1230 Reinsurance receivable
                {' '}P {fmtMoney(r.ceded_recovery)}, CR 5200 Claims recovered from reinsurers
                {' '}P {fmtMoney(r.ceded_recovery)}. After posting this recovery is locked.
              </p>
              <div className="mt-4">
                <Button
                  onClick={handlePost}
                  disabled={posting}
                  leftIcon={<Send className="w-4 h-4" />}
                  style={{ background: '#F07F00' }}
                >
                  {posting ? 'Posting…' : 'Post to GL'}
                </Button>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Workflow trail */}
        <Card>
          <CardHeader>
            <CardTitle>Workflow trail</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-2 text-sm">
              <li className="flex gap-3">
                <span className="text-[#9CA3AF] w-32 flex-shrink-0">Created</span>
                <span className="text-[#374151]">{fmtDateTime(r.created_at)}</span>
              </li>
              <li className="flex gap-3">
                <span className="text-[#9CA3AF] w-32 flex-shrink-0">Posted</span>
                <span className="text-[#374151]">
                  {r.posted_by_username
                    ? `${r.posted_by_username} · ${fmtDateTime(r.posted_at)}`
                    : '—'}
                </span>
              </li>
              <li className="flex gap-3">
                <span className="text-[#9CA3AF] w-32 flex-shrink-0">Journal entry</span>
                <span className="text-[#374151] font-mono">{r.je_number || '—'}</span>
              </li>
              {r.status === 'settled' && (
                <li className="flex gap-3 pt-2 border-t border-[#E5E7EB] mt-2">
                  <span className="w-32 flex-shrink-0 flex items-center gap-1.5 text-[#1D4ED8]">
                    <CheckCircle2 className="w-4 h-4" />
                    Settled
                  </span>
                  <span className="text-[#374151]">
                    Cash has been received from the reinsurer; this recovery is now closed.
                  </span>
                </li>
              )}
            </ul>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
