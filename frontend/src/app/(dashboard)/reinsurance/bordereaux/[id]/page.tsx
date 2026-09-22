'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams, useRouter } from 'next/navigation'
import {
  getToken,
  getBordereauImports,
  getCessions,
  type BordereauImport,
  type Cession,
} from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { ArrowLeft, FileText, AlertTriangle } from 'lucide-react'

function fmtMoney(s: string | null | undefined): string {
  if (s === null || s === undefined || s === '') return '—'
  const n = Number(s)
  if (!isFinite(n)) return s
  return n.toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function fmtDate(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })
}

const STATUS_BADGE: Record<string, { bg: string; fg: string }> = {
  uploaded:  { bg: '#F3F4F6', fg: '#6B7280' },
  parsed:    { bg: '#EFF6FF', fg: '#1D4ED8' },
  committed: { bg: '#ECFDF5', fg: '#047857' },
  rejected:  { bg: '#FEF2F2', fg: '#B91C1C' },
}

const CESSION_STATUS_BADGE: Record<string, { bg: string; fg: string }> = {
  draft:  { bg: '#F3F4F6', fg: '#374151' },
  posted: { bg: '#ECFDF5', fg: '#047857' },
  voided: { bg: '#FEF2F2', fg: '#B91C1C' },
}

export default function BordereauDetailPage() {
  const router = useRouter()
  const params = useParams<{ id: string }>()
  const id = params?.id as string

  const [bordereau, setBordereau] = useState<BordereauImport | null>(null)
  const [cessions, setCessions] = useState<Cession[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notFound, setNotFound] = useState(false)

  const load = useCallback(async () => {
    setLoading(true); setError(null); setNotFound(false)
    try {
      const [importsRes, cessionsRes] = await Promise.all([
        getBordereauImports(),
        getCessions({}),
      ])
      const found = importsRes.results.find((b) => b.id === id) || null
      if (!found) {
        setNotFound(true)
        setBordereau(null)
        setCessions([])
        return
      }
      setBordereau(found)
      setCessions(cessionsRes.results.filter((c) => c.bordereau === id))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load bordereau')
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
          title="Bordereau"
          breadcrumbs={[
            { label: 'Reinsurance', href: '/reinsurance/bordereaux' },
            { label: 'Bordereaux', href: '/reinsurance/bordereaux' },
            { label: '…' },
          ]}
        />
        <div className="flex-1 p-6 text-sm text-[#6B7280]">Loading…</div>
      </div>
    )
  }

  if (notFound || !bordereau) {
    return (
      <div className="flex flex-col min-h-screen">
        <TopBar
          title="Bordereau not found"
          breadcrumbs={[
            { label: 'Reinsurance', href: '/reinsurance/bordereaux' },
            { label: 'Bordereaux', href: '/reinsurance/bordereaux' },
            { label: 'Not found' },
          ]}
          actions={
            <Button
              variant="outline" size="sm"
              leftIcon={<ArrowLeft className="w-3.5 h-3.5" />}
              onClick={() => router.push('/reinsurance/bordereaux')}
            >
              Back
            </Button>
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
              <FileText className="w-10 h-10 mx-auto text-[#D1D5DB] mb-3" strokeWidth={1.5} />
              <p className="text-sm">No bordereau matches this id.</p>
            </CardContent>
          </Card>
        </div>
      </div>
    )
  }

  const statusColour = STATUS_BADGE[bordereau.status] || STATUS_BADGE.uploaded

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title={bordereau.bordereau_number}
        subtitle={bordereau.file_name || undefined}
        breadcrumbs={[
          { label: 'Reinsurance', href: '/reinsurance/bordereaux' },
          { label: 'Bordereaux', href: '/reinsurance/bordereaux' },
          { label: bordereau.bordereau_number },
        ]}
        actions={
          <Button
            variant="outline" size="sm"
            leftIcon={<ArrowLeft className="w-3.5 h-3.5" />}
            onClick={() => router.push('/reinsurance/bordereaux')}
          >
            Back
          </Button>
        }
      />

      <div className="flex-1 p-6 max-w-5xl space-y-4">
        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 text-[#DC2626] flex-shrink-0 mt-0.5" />
            <p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}

        {/* Status banner pill */}
        <div className="flex items-center gap-3">
          <span
            className="inline-flex text-xs font-semibold uppercase tracking-wider px-2.5 py-1 rounded border"
            style={{
              background: statusColour.bg,
              color: statusColour.fg,
              borderColor: `${statusColour.fg}30`,
            }}
          >
            {bordereau.status_display}
          </span>
          <span className="text-xs text-[#6B7280] font-mono">
            Created {fmtDate(bordereau.created_at)}
          </span>
        </div>

        {/* Bordereau details card */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <FileText className="w-4 h-4 text-[#0B0B3B]" />
              Bordereau details
            </CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-3 text-sm">
              <div>
                <dt className="text-[#6B7280]">Treaty</dt>
                <dd className="text-[#111827] font-mono">{bordereau.treaty_number}</dd>
              </div>
              <div>
                <dt className="text-[#6B7280]">Period</dt>
                <dd className="text-[#111827] font-mono text-xs">
                  {fmtDate(bordereau.period_start)} → {fmtDate(bordereau.period_end)}
                </dd>
              </div>
              <div>
                <dt className="text-[#6B7280]">Received date</dt>
                <dd className="text-[#111827] font-mono text-xs">
                  {fmtDate(bordereau.received_date)}
                </dd>
              </div>
              <div>
                <dt className="text-[#6B7280]">File name</dt>
                <dd className="text-[#111827] text-xs">{bordereau.file_name || '—'}</dd>
              </div>
              <div>
                <dt className="text-[#6B7280]">Line count</dt>
                <dd className="text-[#111827] tabular-nums">{bordereau.line_count}</dd>
              </div>
              <div>
                <dt className="text-[#6B7280]">Total gross premium</dt>
                <dd className="text-[#111827] font-mono tabular-nums text-right">
                  {fmtMoney(bordereau.total_gross_premium)}
                </dd>
              </div>
              <div>
                <dt className="text-[#6B7280]">Total ceded premium</dt>
                <dd className="text-[#111827] font-mono tabular-nums text-right">
                  {fmtMoney(bordereau.total_ceded_premium)}
                </dd>
              </div>
              <div>
                <dt className="text-[#6B7280]">Total commission</dt>
                <dd className="text-[#111827] font-mono tabular-nums text-right">
                  {fmtMoney(bordereau.total_commission)}
                </dd>
              </div>
              <div className="md:col-span-2">
                <dt className="text-[#6B7280]">Notes</dt>
                <dd className="text-[#111827] whitespace-pre-wrap">
                  {bordereau.notes || '—'}
                </dd>
              </div>
            </dl>
          </CardContent>
        </Card>

        {/* Linked cessions card */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <FileText className="w-4 h-4 text-[#0B0B3B]" />
              Linked cessions
              <span className="text-xs font-normal text-[#6B7280] ml-1">
                ({cessions.length})
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            {cessions.length === 0 ? (
              <div
                className="m-4 rounded-lg p-4 flex items-start gap-3"
                style={{ background: '#FFFBEB', border: '1px solid #FDE68A' }}
              >
                <AlertTriangle className="w-5 h-5 text-[#B45309] flex-shrink-0 mt-0.5" />
                <p className="text-sm text-[#78350F]">
                  No cessions linked to this bordereau yet. v1 only stores
                  bordereau headers; line-level cession creation from the
                  bordereau file is a v2 feature.
                </p>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="bg-[#F9FAFB] border-b border-[#E5E7EB]">
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Cession #</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Date</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Policy Ref</th>
                      <th className="text-right px-4 py-2.5 font-semibold text-[#374151]">Gross Premium</th>
                      <th className="text-right px-4 py-2.5 font-semibold text-[#374151]">Ceded Premium</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Status</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">JE #</th>
                    </tr>
                  </thead>
                  <tbody>
                    {cessions.map((c) => {
                      const sc = CESSION_STATUS_BADGE[c.status] || CESSION_STATUS_BADGE.draft
                      return (
                        <tr key={c.id} className="border-b border-[#F3F4F6] hover:bg-[#F9FAFB]">
                          <td className="px-4 py-2.5 font-mono text-xs text-[#0B0B3B]">
                            {c.cession_number}
                          </td>
                          <td className="px-4 py-2.5 text-[#6B7280] text-xs font-mono">
                            {fmtDate(c.cession_date)}
                          </td>
                          <td className="px-4 py-2.5 text-[#374151] text-xs font-mono">
                            {c.policy_reference}
                          </td>
                          <td className="px-4 py-2.5 text-right font-mono tabular-nums text-[#374151]">
                            {fmtMoney(c.gross_premium)}
                          </td>
                          <td className="px-4 py-2.5 text-right font-mono tabular-nums text-[#374151]">
                            {fmtMoney(c.ceded_premium)}
                          </td>
                          <td className="px-4 py-2.5">
                            <span
                              className="inline-flex text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded border"
                              style={{
                                background: sc.bg,
                                color: sc.fg,
                                borderColor: `${sc.fg}30`,
                              }}
                            >
                              {c.status_display}
                            </span>
                          </td>
                          <td className="px-4 py-2.5 font-mono text-xs text-[#374151]">
                            {c.je_number || '—'}
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
