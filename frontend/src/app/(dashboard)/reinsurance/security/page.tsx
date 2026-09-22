'use client'

/**
 * /reinsurance/security — the security panel and concentration drill-down.
 *
 * THE RULE THIS SCREEN EXISTS TO HOLD: a national-scale rating and an
 * international-scale rating are not the same measure. AA on a national scale
 * can sit below BBB internationally. So the scale is shown beside every rating,
 * an unverified rating is labelled unverified, and the two scales are never
 * ranked against each other.
 *
 * Concentration is measured per GROUP as well as per carrier: two lines written
 * by two subsidiaries of one group are one exposure to that group.
 */

import { useCallback, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { getToken, getReinsuranceSecurityPanel } from '@/lib/api'
import type { SecurityPanelResponse } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  ShieldCheck, RefreshCw, AlertTriangle, ArrowLeft, Info, BadgeCheck, CircleSlash,
} from 'lucide-react'

const NAVY = 'var(--ad-navy, #0B0B3B)'
const ORANGE = 'var(--ad-orange, #F07F00)'

function money(s: string | null | undefined): string {
  if (s == null) return 'not stated'
  const v = Number(s)
  if (!isFinite(v)) return 'not stated'
  return `BWP ${v.toLocaleString(undefined,
    { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`
}

const SCALE_LABEL: Record<string, string> = {
  international: 'international scale',
  national: 'national scale',
  unknown: 'scale not stated',
}

export default function SecurityPanelPage() {
  const router = useRouter()
  const [data, setData] = useState<SecurityPanelResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      setData(await getReinsuranceSecurityPanel())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load the security panel')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    void load()
  }, [load, router])

  return (
    <div className="min-h-screen bg-[var(--ad-bg-page,#F8F9FB)]">
      <TopBar title="Security Panel" />
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">

        <Link href="/reinsurance"
          className="inline-flex items-center gap-1 text-sm mb-4 transition-colors
                     hover:underline text-[var(--ad-text-secondary,#6B7280)]">
          <ArrowLeft className="w-4 h-4" /> Reinsurer Controls
        </Link>

        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div className="flex items-center gap-3">
            <div className="w-11 h-11 rounded-xl flex items-center justify-center shrink-0"
              style={{ background: NAVY }}>
              <ShieldCheck className="w-5 h-5" style={{ color: ORANGE }} />
            </div>
            <div>
              <h1 className="text-2xl font-semibold tracking-tight" style={{ color: NAVY }}>
                Security Panel
              </h1>
              <p className="text-sm text-[var(--ad-text-secondary,#6B7280)]">
                Counterparty ratings and where our facultative exposure sits
                {data ? ` · as at ${data.as_of}` : ''}
              </p>
            </div>
          </div>
          <Button variant="outline" onClick={() => void load()} disabled={loading}
            className="border-[var(--ad-border,#D1D5DB)]">
            <RefreshCw className={`w-4 h-4 mr-1.5 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </Button>
        </div>

        {error && (
          <div className="mt-4 rounded-lg px-4 py-3 text-sm flex items-start gap-2
                          bg-[#FEE2E2] text-[#991B1B]">
            <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" /> {error}
          </div>
        )}

        {data && (
          <div className="mt-5 rounded-lg px-4 py-3 text-sm flex items-start gap-2
                          border border-[var(--ad-border,#E5E7EB)] bg-white">
            <Info className="w-4 h-4 mt-0.5 shrink-0" style={{ color: ORANGE }} />
            <span className="text-[var(--ad-text-secondary,#4B5563)]">
              {data.scale_warning}
            </span>
          </div>
        )}

        {/* Concentration by group -------------------------------------- */}
        <div className="mt-6">
          <div className="flex items-baseline gap-2 mb-2">
            <h2 className="text-sm font-semibold" style={{ color: NAVY }}>
              Concentration by carrier group
            </h2>
            <span className="text-xs text-[var(--ad-text-secondary,#6B7280)]">
              total {money(data?.total_fac_amount)}
            </span>
          </div>
          <Card>
            <CardContent className="py-5">
              {loading ? (
                <div className="py-6 text-center text-sm text-[var(--ad-text-secondary,#6B7280)]">
                  Loading…
                </div>
              ) : !data?.concentration.length ? (
                <div className="py-6 text-center text-sm text-[var(--ad-text-secondary,#6B7280)]">
                  No facultative exposure recorded, so there is nothing to
                  concentrate yet.
                </div>
              ) : (
                <div className="space-y-3">
                  {data.concentration.slice(0, 12).map(g => {
                    const share = g.share_of_fac ?? 0
                    return (
                      <div key={g.group}>
                        <div className="flex items-baseline justify-between gap-4 text-sm">
                          <span className="font-medium truncate" style={{ color: NAVY }}>
                            {g.group}
                            {g.unapproved > 0 && (
                              <span className="ml-2 text-xs font-normal"
                                style={{ color: 'var(--ad-error, #B91C1C)' }}>
                                {g.unapproved} not approved
                              </span>
                            )}
                          </span>
                          <span className="tabular-nums shrink-0">
                            {money(g.fac_amount)}
                            <span className="ml-2 text-xs text-[var(--ad-text-secondary,#6B7280)]">
                              {g.share_of_fac == null ? '' : `${share.toFixed(1)}%`}
                            </span>
                          </span>
                        </div>
                        <div className="mt-1.5 h-2 rounded-full overflow-hidden
                                        bg-[var(--ad-gray-100,#F3F4F6)]">
                          <div className="h-full rounded-full transition-[width] duration-500"
                            style={{
                              width: `${Math.min(100, Math.max(share, share > 0 ? 1.5 : 0))}%`,
                              background: g.unapproved > 0
                                ? 'var(--ad-warning, #AE6005)' : NAVY,
                            }} />
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
            </CardContent>
          </Card>
        </div>

        {/* The panel ---------------------------------------------------- */}
        <div className="mt-6">
          <h2 className="text-sm font-semibold mb-2" style={{ color: NAVY }}>
            Counterparties
          </h2>
          <Card>
            <CardContent className="p-0">
              {loading ? (
                <div className="py-10 text-center text-sm text-[var(--ad-text-secondary,#6B7280)]">
                  Loading…
                </div>
              ) : !data?.panel.length ? (
                <div className="py-10 text-center text-sm text-[var(--ad-text-secondary,#6B7280)]">
                  No counterparties on file.
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-[var(--ad-border,#E5E7EB)]
                                     text-[11px] uppercase tracking-wider
                                     text-[var(--ad-text-secondary,#6B7280)]">
                        <th className="text-left font-medium px-5 py-3">Counterparty</th>
                        <th className="text-left font-medium px-5 py-3">Group</th>
                        <th className="text-left font-medium px-5 py-3">Rating</th>
                        <th className="text-left font-medium px-5 py-3">Tier</th>
                        <th className="text-right font-medium px-5 py-3">Share</th>
                        <th className="text-right font-medium px-5 py-3">FAC amount</th>
                        <th className="text-left font-medium px-5 py-3">Standing</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.panel.map(r => (
                        <tr key={r.short_code}
                          className="border-b border-[var(--ad-border,#F3F4F6)] last:border-0
                                     transition-colors hover:bg-[var(--ad-orange-50,#FFF7ED)]">
                          <td className="px-5 py-3.5">
                            <div className="font-medium" style={{ color: NAVY }}>{r.reinsurer}</div>
                            <div className="text-xs text-[var(--ad-text-secondary,#6B7280)]">
                              {r.short_code}{r.domicile ? ` · ${r.domicile}` : ''}
                            </div>
                          </td>
                          <td className="px-5 py-3.5 text-[var(--ad-text-secondary,#6B7280)]">
                            {r.carrier_group ?? '—'}
                          </td>
                          <td className="px-5 py-3.5">
                            {!r.has_assessment ? (
                              <span className="inline-flex items-center gap-1 text-xs font-medium"
                                style={{ color: 'var(--ad-warning, #AE6005)' }}>
                                <CircleSlash className="w-3.5 h-3.5" /> never assessed
                              </span>
                            ) : (
                              <>
                                <div className="font-medium" style={{ color: NAVY }}>
                                  {r.rating ?? 'unrated'}
                                </div>
                                <div className="text-xs text-[var(--ad-text-secondary,#6B7280)]">
                                  {SCALE_LABEL[r.rating_scale ?? 'unknown'] ?? 'scale not stated'}
                                  {r.rating_verified
                                    ? <span className="ml-1 inline-flex items-center gap-0.5"
                                        style={{ color: 'var(--ad-success, #059669)' }}>
                                        <BadgeCheck className="w-3 h-3" /> verified
                                      </span>
                                    : <span className="ml-1"
                                        style={{ color: 'var(--ad-warning, #AE6005)' }}>
                                        not verified
                                      </span>}
                                </div>
                              </>
                            )}
                          </td>
                          <td className="px-5 py-3.5 text-[var(--ad-text-secondary,#6B7280)]">
                            {r.internal_tier ?? '—'}
                          </td>
                          <td className="px-5 py-3.5 text-right tabular-nums
                                         text-[var(--ad-text-secondary,#6B7280)]">
                            {/* A panel that states no share stays blank here —
                                printing 0% would read as "no participation". */}
                            {r.participation_share == null
                              ? 'not stated'
                              : `${Number(r.participation_share).toFixed(2)}%`}
                          </td>
                          <td className="px-5 py-3.5 text-right tabular-nums font-medium"
                            style={{ color: NAVY }}>
                            {money(r.fac_amount)}
                          </td>
                          <td className="px-5 py-3.5">
                            <span className="text-xs px-2.5 py-1 rounded-full font-medium"
                              style={r.approved
                                ? { background: 'var(--ad-success-bg, #ECFDF5)',
                                    color: 'var(--ad-success, #059669)' }
                                : { background: 'var(--ad-warning-light, #FEF3C7)',
                                    color: 'var(--ad-warning, #AE6005)' }}>
                              {r.approved ? 'Approved' : 'Not placeable'}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}
