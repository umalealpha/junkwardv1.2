'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams, useRouter } from 'next/navigation'
import Link from 'next/link'
import {
  getToken,
  getReinsurers,
  getReinsuranceTreaties,
  type Reinsurer,
  type ReinsuranceTreaty,
} from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { ArrowLeft, Building2, Shield, AlertTriangle } from 'lucide-react'
// KYC & evidence register (control brief section 5, 16-Sep-2026). Its own
// component so this page keeps its shape and the register has one home.
import { KycPanel } from '@/components/reinsurance/KycPanel'

function fmtPct(v: string | null): string {
  if (v === null || v === undefined || v === '') return '—'
  const n = Number(v)
  if (!isFinite(n)) return '—'
  return `${n.toFixed(2)}%`
}

function fmtDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('en-GB', {
    day: '2-digit', month: 'short', year: 'numeric',
  })
}

const TREATY_STATUS_PILL: Record<string, { bg: string; fg: string }> = {
  draft:     { bg: '#F3F4F6', fg: '#374151' },
  active:    { bg: '#ECFDF5', fg: '#047857' },
  expired:   { bg: '#FEF3C7', fg: '#92400E' },
  cancelled: { bg: '#FEF2F2', fg: '#B91C1C' },
}

export default function ReinsurerDetailPage() {
  const router = useRouter()
  const params = useParams<{ id: string }>()
  const id = params?.id as string

  const [reinsurer, setReinsurer] = useState<Reinsurer | null>(null)
  const [treaties, setTreaties] = useState<ReinsuranceTreaty[]>([])
  const [loading, setLoading] = useState(true)
  const [notFound, setNotFound] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null); setNotFound(false)
    try {
      const [reinsurersRes, treatiesRes] = await Promise.all([
        getReinsurers(),
        getReinsuranceTreaties({ reinsurer: id }),
      ])
      const found = reinsurersRes.results.find((r) => r.id === id)
      if (!found) {
        setNotFound(true)
        setReinsurer(null)
        setTreaties([])
      } else {
        setReinsurer(found)
        setTreaties(treatiesRes.results)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load reinsurer')
    } finally {
      setLoading(false)
    }
  }, [id])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    void load()
  }, [router, load])

  if (loading) {
    return (
      <div className="flex flex-col min-h-screen">
        <TopBar
          title="Reinsurer"
          breadcrumbs={[
            { label: 'Reinsurance', href: '/reinsurance/reinsurers' },
            { label: 'Reinsurers', href: '/reinsurance/reinsurers' },
            { label: '…' },
          ]}
          actions={
            <Link href="/reinsurance/reinsurers">
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

  if (notFound || !reinsurer) {
    return (
      <div className="flex flex-col min-h-screen">
        <TopBar
          title="Reinsurer not found"
          breadcrumbs={[
            { label: 'Reinsurance', href: '/reinsurance/reinsurers' },
            { label: 'Reinsurers', href: '/reinsurance/reinsurers' },
            { label: 'Not found' },
          ]}
          actions={
            <Link href="/reinsurance/reinsurers">
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
            <CardContent className="px-6 py-12 text-center text-[#6B7280]">
              <Building2 className="w-10 h-10 mx-auto text-[#D1D5DB] mb-3" strokeWidth={1.5} />
              <p className="text-sm">No reinsurer matches that ID.</p>
              <p className="text-xs text-[#9CA3AF] mt-2">
                Return to the reinsurers list to pick another counterparty.
              </p>
            </CardContent>
          </Card>
        </div>
      </div>
    )
  }

  const statusPill = reinsurer.is_active
    ? { bg: '#ECFDF5', fg: '#047857', label: 'Active' }
    : { bg: '#F3F4F6', fg: '#6B7280', label: 'Inactive' }

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title={reinsurer.name}
        subtitle={reinsurer.short_code}
        breadcrumbs={[
          { label: 'Reinsurance', href: '/reinsurance/reinsurers' },
          { label: 'Reinsurers', href: '/reinsurance/reinsurers' },
          { label: reinsurer.name },
        ]}
        actions={
          <Link href="/reinsurance/reinsurers">
            <Button variant="outline" size="sm" leftIcon={<ArrowLeft className="w-3.5 h-3.5" />}>
              Back
            </Button>
          </Link>
        }
      />

      <div className="flex-1 p-6 max-w-5xl space-y-4">
        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 text-[#DC2626] flex-shrink-0 mt-0.5" />
            <p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}

        {/* Header card — Counterparty details */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Building2 className="w-4 h-4 text-[#0B0B3B]" />
              Counterparty details
            </CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-3 text-sm">
              <div>
                <dt className="text-[#6B7280]">Short code</dt>
                <dd className="text-[#0B0B3B] font-mono">{reinsurer.short_code}</dd>
              </div>
              <div>
                <dt className="text-[#6B7280]">Name</dt>
                <dd className="text-[#111827]">{reinsurer.name}</dd>
              </div>
              <div>
                <dt className="text-[#6B7280]">Country</dt>
                <dd className="text-[#111827] font-mono text-xs uppercase">
                  {reinsurer.country || '—'}
                </dd>
              </div>
              <div>
                <dt className="text-[#6B7280]">Credit rating</dt>
                <dd className="text-[#111827] font-mono text-xs">
                  {reinsurer.credit_rating || '—'}
                </dd>
              </div>
              <div>
                <dt className="text-[#6B7280]">Status</dt>
                <dd>
                  <span
                    className="inline-flex text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded border"
                    style={{
                      background: statusPill.bg,
                      color: statusPill.fg,
                      borderColor: `${statusPill.fg}30`,
                    }}
                  >
                    {statusPill.label}
                  </span>
                </dd>
              </div>
              <div>
                <dt className="text-[#6B7280]">Treaties</dt>
                <dd className="text-[#374151]">
                  <span className="font-mono tabular-nums">{reinsurer.treaty_count}</span>
                  <span className="ml-1 text-[#9CA3AF] text-xs">
                    {reinsurer.treaty_count === 1 ? 'treaty' : 'treaties'}
                  </span>
                </dd>
              </div>
              <div className="md:col-span-2">
                <dt className="text-[#6B7280]">Notes</dt>
                <dd className="text-[#111827] whitespace-pre-wrap">
                  {reinsurer.notes?.trim() ? reinsurer.notes : '—'}
                </dd>
              </div>
            </dl>
          </CardContent>
        </Card>

        {/* Treaties card */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Shield className="w-4 h-4 text-[#0B0B3B]" />
              Treaties with this reinsurer
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            {treaties.length === 0 ? (
              <div className="px-6 py-12 text-center text-[#6B7280]">
                <Shield className="w-10 h-10 mx-auto text-[#D1D5DB] mb-3" strokeWidth={1.5} />
                <p className="text-sm">No treaties under this reinsurer yet.</p>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="bg-[#F9FAFB] border-b border-[#E5E7EB]">
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Treaty #</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Description</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Type</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Line of Business</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Period</th>
                      <th className="text-right px-4 py-2.5 font-semibold text-[#374151]">Cession %</th>
                      <th className="text-right px-4 py-2.5 font-semibold text-[#374151]">Commission %</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {treaties.map((t) => {
                      const pill = TREATY_STATUS_PILL[t.status] || TREATY_STATUS_PILL.draft
                      return (
                        <tr key={t.id} className="border-b border-[#F3F4F6] hover:bg-[#F9FAFB]">
                          <td className="px-4 py-2.5 font-mono text-xs text-[#0B0B3B]">
                            {t.treaty_number}
                          </td>
                          <td className="px-4 py-2.5 text-[#374151]">{t.description || '—'}</td>
                          <td className="px-4 py-2.5 text-[#374151]">{t.treaty_type_display}</td>
                          <td className="px-4 py-2.5 text-[#374151]">{t.line_of_business || '—'}</td>
                          <td className="px-4 py-2.5 text-[#6B7280] text-xs whitespace-nowrap">
                            {fmtDate(t.inception_date)}
                            <span className="mx-1 text-[#9CA3AF]">→</span>
                            {fmtDate(t.expiry_date)}
                          </td>
                          <td className="px-4 py-2.5 text-right text-[#374151] font-mono tabular-nums text-xs">
                            {fmtPct(t.cession_share_percent)}
                          </td>
                          <td className="px-4 py-2.5 text-right text-[#374151] font-mono tabular-nums text-xs">
                            {fmtPct(t.commission_percent)}
                          </td>
                          <td className="px-4 py-2.5">
                            <span
                              className="inline-flex text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded border"
                              style={{
                                background: pill.bg,
                                color: pill.fg,
                                borderColor: `${pill.fg}30`,
                              }}
                            >
                              {t.status_display}
                            </span>
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

        <KycPanel reinsurerId={String(id)} />
      </div>
    </div>
  )
}
