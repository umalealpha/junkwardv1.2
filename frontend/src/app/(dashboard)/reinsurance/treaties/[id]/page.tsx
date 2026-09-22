'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams, useRouter } from 'next/navigation'
import {
  getToken,
  getReinsuranceTreaties,
  getCessions,
  getReinsuranceRecoveries,
  type ReinsuranceTreaty,
  type Cession,
  type ReinsuranceRecovery,
} from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  ArrowLeft,
  Shield,
  ArrowDownLeft,
  ArrowUpRight,
  AlertTriangle,
} from 'lucide-react'

// ─── Helpers ────────────────────────────────────────────────────────────────

function fmtMoney(s: string | null | undefined): string {
  if (s === null || s === undefined || s === '') return '—'
  const n = Number(s)
  if (!isFinite(n)) return String(s)
  return n.toLocaleString('en-GB', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

function fmtDate(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  return d.toLocaleDateString('en-GB', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  })
}

function fmtPct(v: string | null): string {
  if (!v) return '—'
  const n = Number(v)
  return isFinite(n) ? `${n.toFixed(2)}%` : v
}

function sumStr<T>(items: readonly T[], key: keyof T): number {
  return items.reduce((acc, item) => {
    const n = Number(item[key])
    return acc + (isFinite(n) ? n : 0)
  }, 0)
}

function fmtNumber(n: number): string {
  return n.toLocaleString('en-GB', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

// ─── Status badges ──────────────────────────────────────────────────────────

const TREATY_STATUS_BADGE: Record<string, { bg: string; fg: string }> = {
  draft:     { bg: '#F3F4F6', fg: '#374151' },
  active:    { bg: '#ECFDF5', fg: '#047857' },
  expired:   { bg: '#FEF3C7', fg: '#92400E' },
  cancelled: { bg: '#FEF2F2', fg: '#B91C1C' },
}

const CESSION_STATUS_BADGE: Record<string, { bg: string; fg: string }> = {
  draft:  { bg: '#F3F4F6', fg: '#374151' },
  posted: { bg: '#ECFDF5', fg: '#047857' },
  voided: { bg: '#FEF2F2', fg: '#B91C1C' },
}

const RECOVERY_STATUS_BADGE: Record<string, { bg: string; fg: string }> = {
  draft:   { bg: '#F3F4F6', fg: '#374151' },
  posted:  { bg: '#ECFDF5', fg: '#047857' },
  settled: { bg: '#EFF6FF', fg: '#1D4ED8' },
  voided:  { bg: '#FEF2F2', fg: '#B91C1C' },
}

function StatusPill({
  map,
  status,
  label,
  size = 'sm',
}: {
  map: Record<string, { bg: string; fg: string }>
  status: string
  label: string
  size?: 'sm' | 'md'
}) {
  const c = map[status] || map.draft
  const cls =
    size === 'md'
      ? 'inline-flex text-xs font-semibold uppercase tracking-wider px-2.5 py-1 rounded border'
      : 'inline-flex text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded border'
  return (
    <span
      className={cls}
      style={{ background: c.bg, color: c.fg, borderColor: `${c.fg}30` }}
    >
      {label}
    </span>
  )
}

// ─── Page ───────────────────────────────────────────────────────────────────

export default function ReinsuranceTreatyDetailPage() {
  const router = useRouter()
  const params = useParams<{ id: string }>()
  const id = params?.id as string

  const [treaty, setTreaty] = useState<ReinsuranceTreaty | null>(null)
  const [cessions, setCessions] = useState<Cession[]>([])
  const [recoveries, setRecoveries] = useState<ReinsuranceRecovery[]>([])
  const [loading, setLoading] = useState(true)
  const [notFound, setNotFound] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    setNotFound(false)
    try {
      const [treatiesRes, cessionsRes, recoveriesRes] = await Promise.all([
        getReinsuranceTreaties(),
        getCessions({ treaty: id }),
        getReinsuranceRecoveries({ treaty: id }),
      ])
      const match = treatiesRes.results.find((t) => t.id === id)
      if (!match) {
        setNotFound(true)
        setTreaty(null)
      } else {
        setTreaty(match)
      }
      setCessions(cessionsRes.results)
      setRecoveries(recoveriesRes.results)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load treaty')
    } finally {
      setLoading(false)
    }
  }, [id])

  useEffect(() => {
    if (!getToken()) {
      router.replace('/login')
      return
    }
    void load()
  }, [router, load])

  // Loading state
  if (loading) {
    return (
      <div className="flex flex-col min-h-screen">
        <TopBar
          title="Treaty"
          breadcrumbs={[
            { label: 'Reinsurance', href: '/reinsurance/treaties' },
            { label: 'Treaties', href: '/reinsurance/treaties' },
            { label: '…' },
          ]}
        />
        <div className="flex-1 p-6 text-sm text-[#6B7280]">Loading…</div>
      </div>
    )
  }

  // Not found / error state
  if (notFound || !treaty) {
    return (
      <div className="flex flex-col min-h-screen">
        <TopBar
          title="Treaty not found"
          breadcrumbs={[
            { label: 'Reinsurance', href: '/reinsurance/treaties' },
            { label: 'Treaties', href: '/reinsurance/treaties' },
            { label: 'Not found' },
          ]}
          actions={
            <Button
              variant="outline"
              size="sm"
              leftIcon={<ArrowLeft className="w-3.5 h-3.5" />}
              onClick={() => router.push('/reinsurance/treaties')}
            >
              Back
            </Button>
          }
        />
        <div className="flex-1 p-6 max-w-3xl space-y-4">
          {error && (
            <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 text-[#DC2626] flex-shrink-0 mt-0.5" />
              <p className="text-[#DC2626] text-sm">{error}</p>
            </div>
          )}
          <Card>
            <CardContent className="px-6 py-12 text-center text-[#6B7280]">
              <Shield
                className="w-10 h-10 mx-auto text-[#D1D5DB] mb-3"
                strokeWidth={1.5}
              />
              <p className="text-sm">Treaty not found.</p>
              <p className="text-xs text-[#9CA3AF] mt-2">
                The treaty may have been removed, or you do not have access.
              </p>
            </CardContent>
          </Card>
        </div>
      </div>
    )
  }

  // ─── Activity totals ─────────────────────────────────────────────────────
  const cessionsCount = cessions.length
  const totalCededPremium = sumStr(cessions, 'ceded_premium')
  const recoveriesCount = recoveries.length
  const totalCededRecovery = sumStr(recoveries, 'ceded_recovery')
  const net = totalCededPremium - totalCededRecovery
  const netSign = net > 0 ? 'pos' : net < 0 ? 'neg' : 'zero'
  const netColor =
    netSign === 'pos' ? '#B91C1C' : netSign === 'neg' ? '#047857' : '#374151'

  const cessionsToShow = cessions.slice(0, 20)
  const recoveriesToShow = recoveries.slice(0, 20)

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title={treaty.treaty_number}
        subtitle={treaty.description}
        breadcrumbs={[
          { label: 'Reinsurance', href: '/reinsurance/treaties' },
          { label: 'Treaties', href: '/reinsurance/treaties' },
          { label: treaty.treaty_number },
        ]}
        actions={
          <Button
            variant="outline"
            size="sm"
            leftIcon={<ArrowLeft className="w-3.5 h-3.5" />}
            onClick={() => router.push('/reinsurance/treaties')}
          >
            Back
          </Button>
        }
      />

      <div className="flex-1 p-6 max-w-6xl space-y-4">
        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 text-[#DC2626] flex-shrink-0 mt-0.5" />
            <p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}

        {/* Status banner */}
        <div className="flex items-center gap-3">
          <Shield className="w-5 h-5 text-[#0B0B3B]" />
          <span className="text-sm text-[#374151]">Status:</span>
          <StatusPill
            map={TREATY_STATUS_BADGE}
            status={treaty.status}
            label={treaty.status_display}
            size="md"
          />
        </div>

        {/* Treaty terms card */}
        <Card>
          <CardHeader>
            <CardTitle>Treaty terms</CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-3 text-sm">
              <div>
                <dt className="text-[#6B7280] text-xs uppercase tracking-wider">
                  Treaty type
                </dt>
                <dd className="text-[#111827] mt-0.5">
                  {treaty.treaty_type_display}
                </dd>
              </div>
              <div>
                <dt className="text-[#6B7280] text-xs uppercase tracking-wider">
                  Reinsurer
                </dt>
                <dd className="text-[#111827] mt-0.5">
                  <span className="font-mono text-xs">
                    {treaty.reinsurer_short_code}
                  </span>
                  <span className="text-[#6B7280] ml-2">
                    {treaty.reinsurer_name}
                  </span>
                </dd>
              </div>
              <div>
                <dt className="text-[#6B7280] text-xs uppercase tracking-wider">
                  Line of business
                </dt>
                <dd className="text-[#111827] mt-0.5">
                  {treaty.line_of_business}
                </dd>
              </div>
              <div>
                <dt className="text-[#6B7280] text-xs uppercase tracking-wider">
                  Currency
                </dt>
                <dd className="text-[#111827] font-mono mt-0.5">
                  {treaty.currency_code}
                </dd>
              </div>
              <div>
                <dt className="text-[#6B7280] text-xs uppercase tracking-wider">
                  Period
                </dt>
                <dd className="text-[#111827] font-mono text-xs mt-0.5">
                  {fmtDate(treaty.inception_date)} → {fmtDate(treaty.expiry_date)}
                </dd>
              </div>
              <div>
                <dt className="text-[#6B7280] text-xs uppercase tracking-wider">
                  Cession share
                </dt>
                <dd className="text-[#111827] font-mono tabular-nums mt-0.5">
                  {fmtPct(treaty.cession_share_percent)}
                </dd>
              </div>
              <div>
                <dt className="text-[#6B7280] text-xs uppercase tracking-wider">
                  Commission
                </dt>
                <dd className="text-[#111827] font-mono tabular-nums mt-0.5">
                  {fmtPct(treaty.commission_percent)}
                </dd>
              </div>
              <div>
                <dt className="text-[#6B7280] text-xs uppercase tracking-wider">
                  Retention amount
                </dt>
                <dd className="text-[#111827] font-mono tabular-nums mt-0.5">
                  {fmtMoney(treaty.retention_amount)}
                </dd>
              </div>
              <div>
                <dt className="text-[#6B7280] text-xs uppercase tracking-wider">
                  Limit amount
                </dt>
                <dd className="text-[#111827] font-mono tabular-nums mt-0.5">
                  {fmtMoney(treaty.limit_amount)}
                </dd>
              </div>
              {treaty.notes && (
                <div className="md:col-span-2">
                  <dt className="text-[#6B7280] text-xs uppercase tracking-wider">
                    Notes
                  </dt>
                  <dd className="text-[#111827] whitespace-pre-wrap mt-0.5">
                    {treaty.notes}
                  </dd>
                </div>
              )}
            </dl>
          </CardContent>
        </Card>

        {/* Activity summary */}
        <Card>
          <CardHeader>
            <CardTitle>Activity summary</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div className="rounded-lg border border-[#E5E7EB] bg-[#F9FAFB] p-4">
                <div className="flex items-center gap-2 text-[#6B7280] text-xs uppercase tracking-wider">
                  <ArrowUpRight className="w-3.5 h-3.5" />
                  Cessions
                </div>
                <div className="mt-2 text-2xl font-semibold text-[#111827] tabular-nums">
                  {cessionsCount}
                </div>
                <div className="mt-1 text-xs text-[#6B7280]">
                  Total ceded premium
                </div>
                <div className="font-mono tabular-nums text-sm text-[#111827] mt-0.5">
                  {fmtNumber(totalCededPremium)}
                </div>
              </div>

              <div className="rounded-lg border border-[#E5E7EB] bg-[#F9FAFB] p-4">
                <div className="flex items-center gap-2 text-[#6B7280] text-xs uppercase tracking-wider">
                  <ArrowDownLeft className="w-3.5 h-3.5" />
                  Recoveries
                </div>
                <div className="mt-2 text-2xl font-semibold text-[#111827] tabular-nums">
                  {recoveriesCount}
                </div>
                <div className="mt-1 text-xs text-[#6B7280]">
                  Total ceded recovery
                </div>
                <div className="font-mono tabular-nums text-sm text-[#111827] mt-0.5">
                  {fmtNumber(totalCededRecovery)}
                </div>
              </div>

              <div className="rounded-lg border border-[#E5E7EB] bg-[#F9FAFB] p-4">
                <div className="flex items-center gap-2 text-[#6B7280] text-xs uppercase tracking-wider">
                  Net (cessions − recoveries)
                </div>
                <div
                  className="mt-2 text-2xl font-semibold tabular-nums font-mono"
                  style={{ color: netColor }}
                >
                  {fmtNumber(net)}
                </div>
                <div className="mt-1 text-xs text-[#6B7280]">
                  {netSign === 'pos' && 'Net outflow to reinsurer'}
                  {netSign === 'neg' && 'Net inflow from reinsurer'}
                  {netSign === 'zero' && 'Balanced'}
                </div>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Cessions table */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <ArrowUpRight className="w-4 h-4 text-[#0B0B3B]" />
              Cessions under this treaty
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            {cessions.length === 0 && (
              <div className="px-6 py-10 text-center text-[#6B7280]">
                <p className="text-sm">No cessions under this treaty yet.</p>
              </div>
            )}
            {cessions.length > 0 && (
              <>
                <div className="overflow-x-auto">
                  <table className="min-w-full text-sm">
                    <thead>
                      <tr className="bg-[#F9FAFB] border-b border-[#E5E7EB]">
                        <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">
                          Cession #
                        </th>
                        <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">
                          Date
                        </th>
                        <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">
                          Policy Ref
                        </th>
                        <th className="text-right px-4 py-2.5 font-semibold text-[#374151]">
                          Gross Premium
                        </th>
                        <th className="text-right px-4 py-2.5 font-semibold text-[#374151]">
                          Ceded Premium
                        </th>
                        <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">
                          Status
                        </th>
                        <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">
                          JE #
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {cessionsToShow.map((c) => (
                        <tr
                          key={c.id}
                          className="border-b border-[#F3F4F6] hover:bg-[#F9FAFB]"
                        >
                          <td className="px-4 py-2.5 font-mono text-xs text-[#0B0B3B]">
                            {c.cession_number}
                          </td>
                          <td className="px-4 py-2.5 text-[#6B7280] text-xs font-mono">
                            {fmtDate(c.cession_date)}
                          </td>
                          <td className="px-4 py-2.5 text-[#6B7280] text-xs">
                            {c.policy_reference || '—'}
                          </td>
                          <td className="px-4 py-2.5 text-right font-mono tabular-nums text-[#111827]">
                            {fmtMoney(c.gross_premium)}
                          </td>
                          <td className="px-4 py-2.5 text-right font-mono tabular-nums text-[#111827]">
                            {fmtMoney(c.ceded_premium)}
                          </td>
                          <td className="px-4 py-2.5">
                            <StatusPill
                              map={CESSION_STATUS_BADGE}
                              status={c.status}
                              label={c.status_display}
                            />
                          </td>
                          <td className="px-4 py-2.5 font-mono text-xs text-[#6B7280]">
                            {c.je_number || '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="px-4 py-2 border-t border-[#E5E7EB] bg-[#F9FAFB] text-xs text-[#6B7280]">
                  Showing {cessionsToShow.length} of {cessions.length}
                </div>
              </>
            )}
          </CardContent>
        </Card>

        {/* Recoveries table */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <ArrowDownLeft className="w-4 h-4 text-[#0B0B3B]" />
              Recoveries under this treaty
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            {recoveries.length === 0 && (
              <div className="px-6 py-10 text-center text-[#6B7280]">
                <p className="text-sm">No recoveries under this treaty yet.</p>
              </div>
            )}
            {recoveries.length > 0 && (
              <>
                <div className="overflow-x-auto">
                  <table className="min-w-full text-sm">
                    <thead>
                      <tr className="bg-[#F9FAFB] border-b border-[#E5E7EB]">
                        <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">
                          Recovery #
                        </th>
                        <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">
                          Date
                        </th>
                        <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">
                          Claim Ref
                        </th>
                        <th className="text-right px-4 py-2.5 font-semibold text-[#374151]">
                          Gross Loss
                        </th>
                        <th className="text-right px-4 py-2.5 font-semibold text-[#374151]">
                          Ceded Recovery
                        </th>
                        <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">
                          Status
                        </th>
                        <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">
                          JE #
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {recoveriesToShow.map((r) => (
                        <tr
                          key={r.id}
                          className="border-b border-[#F3F4F6] hover:bg-[#F9FAFB]"
                        >
                          <td className="px-4 py-2.5 font-mono text-xs text-[#0B0B3B]">
                            {r.recovery_number}
                          </td>
                          <td className="px-4 py-2.5 text-[#6B7280] text-xs font-mono">
                            {fmtDate(r.recovery_date)}
                          </td>
                          <td className="px-4 py-2.5 text-[#374151] font-mono text-xs">
                            {r.claim_reference || '—'}
                          </td>
                          <td className="px-4 py-2.5 text-right font-mono tabular-nums text-[#111827]">
                            {fmtMoney(r.gross_loss)}
                          </td>
                          <td className="px-4 py-2.5 text-right font-mono tabular-nums text-[#111827]">
                            {fmtMoney(r.ceded_recovery)}
                          </td>
                          <td className="px-4 py-2.5">
                            <StatusPill
                              map={RECOVERY_STATUS_BADGE}
                              status={r.status}
                              label={r.status_display}
                            />
                          </td>
                          <td className="px-4 py-2.5 font-mono text-xs text-[#6B7280]">
                            {r.je_number || '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="px-4 py-2 border-t border-[#E5E7EB] bg-[#F9FAFB] text-xs text-[#6B7280]">
                  Showing {recoveriesToShow.length} of {recoveries.length}
                </div>
              </>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
