'use client'

/**
 * /reinsurance/fac-risk — the facultative risk register.
 *
 * The question the brief asks, answered on one screen: every current and
 * expired FAC risk, and the amount by reinsurer, policy, class and currency.
 *
 * Two things this screen must never do, and the reason each is spelled out in
 * the markup below:
 *   • present unplaced capacity as ceded — it is retained, and it is shown in
 *     amber, under its own heading, with the word "retained" in it;
 *   • print a missing share as 0% — a null share is rendered "not stated".
 */

import { useCallback, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { getToken, getFacRiskRegister } from '@/lib/api'
import type { FacRiskRegister } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  Layers, Search, RefreshCw, AlertTriangle, ArrowLeft, ShieldAlert,
} from 'lucide-react'

const NAVY = 'var(--ad-navy, #0B0B3B)'
const ORANGE = 'var(--ad-orange, #F07F00)'
const STATES = [
  { key: 'active', label: 'Active' },
  { key: 'expired', label: 'Expired' },
  { key: 'all', label: 'All' },
]

function money(s: string | null | undefined, currency = 'BWP'): string {
  if (s == null) return 'not stated'
  const v = Number(s)
  if (!isFinite(v)) return 'not stated'
  return `${currency} ${v.toLocaleString(undefined,
    { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

/**
 * One money line per currency.
 *
 * Reinsurance control QC, 16-Sep-2026: this table wrote BWP in front of an
 * amount the server had reached by adding pula to dollars. Each currency now
 * gets its own line, labelled with its own code. With a single currency it
 * reads exactly as it did before.
 */
function MoneyByCurrency({
  rows, field,
}: {
  rows: { currency: string; [k: string]: string | number }[] | undefined
  field: string
}) {
  if (!rows || rows.length === 0) return <>—</>
  return (
    <span className="flex flex-col items-end gap-0.5">
      {rows.map(r => (
        <span key={r.currency} className="tabular-nums whitespace-nowrap">
          {money(String(r[field] ?? ''), r.currency)}
        </span>
      ))}
    </span>
  )
}

function pct(s: string | null | undefined): string {
  if (s == null) return 'not stated'
  const v = Number(s)
  return isFinite(v) ? `${v.toFixed(2)}%` : 'not stated'
}

export default function FacRiskRegisterPage() {
  const router = useRouter()
  const [state, setState] = useState('active')
  const [q, setQ] = useState('')
  const [data, setData] = useState<FacRiskRegister | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      setData(await getFacRiskRegister({ state, q: q.trim() || undefined }))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load the register')
    } finally {
      setLoading(false)
    }
  }, [state, q])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    void load()
    // `q` is applied on submit, not on every keystroke — see the form below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state, router])

  return (
    <div className="min-h-screen bg-[var(--ad-bg-page,#F8F9FB)]">
      <TopBar title="FAC Risk Register" />
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">

        <Link href="/reinsurance"
          className="inline-flex items-center gap-1 text-sm mb-4 transition-colors
                     hover:underline text-[var(--ad-text-secondary,#6B7280)]">
          <ArrowLeft className="w-4 h-4" /> Reinsurer Controls
        </Link>

        <div className="flex items-center gap-3">
          <div className="w-11 h-11 rounded-xl flex items-center justify-center shrink-0"
            style={{ background: NAVY }}>
            <Layers className="w-5 h-5" style={{ color: ORANGE }} />
          </div>
          <div>
            <h1 className="text-2xl font-semibold tracking-tight" style={{ color: NAVY }}>
              FAC Risk Register
            </h1>
            <p className="text-sm text-[var(--ad-text-secondary,#6B7280)]">
              Facultative placements, policy by policy
              {data ? ` · as at ${data.as_of}` : ''}
            </p>
          </div>
        </div>

        {/* Filters */}
        <Card className="mt-5">
          <CardContent className="py-4">
            <div className="flex flex-wrap items-center gap-3">
              <div className="inline-flex rounded-lg border border-[var(--ad-border,#E5E7EB)]
                              overflow-hidden">
                {STATES.map(s => (
                  <button key={s.key} type="button" onClick={() => setState(s.key)}
                    className={`px-4 py-2 text-sm transition-colors ${
                      state === s.key
                        ? 'text-white'
                        : 'bg-white hover:bg-[var(--ad-orange-50,#FFF7ED)]'}`}
                    style={state === s.key
                      ? { background: NAVY }
                      : { color: NAVY }}>
                    {s.label}
                  </button>
                ))}
              </div>
              <form className="flex items-center gap-2"
                onSubmit={e => { e.preventDefault(); void load() }}>
                <input value={q} onChange={e => setQ(e.target.value)}
                  placeholder="Reference, policy or insured"
                  className="border border-[var(--ad-border,#D1D5DB)] rounded-md px-3 py-2
                             text-sm w-64 transition-colors
                             focus:outline-none focus:border-[var(--ad-orange,#F07F00)]" />
                <Button type="submit" variant="outline"
                  className="border-[var(--ad-border,#D1D5DB)]">
                  <Search className="w-4 h-4 mr-1.5" /> Find
                </Button>
              </form>
              <Button variant="outline" onClick={() => void load()} disabled={loading}
                className="border-[var(--ad-border,#D1D5DB)] ml-auto">
                <RefreshCw className={`w-4 h-4 mr-1.5 ${loading ? 'animate-spin' : ''}`} />
                Refresh
              </Button>
            </div>
          </CardContent>
        </Card>

        {error && (
          <div className="mt-4 rounded-lg px-4 py-3 text-sm flex items-start gap-2
                          bg-[#FEE2E2] text-[#991B1B]">
            <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" /> {error}
          </div>
        )}

        {/* Retained — its own panel, so it can never be read as ceded. */}
        {data && (
          <div className="mt-5 rounded-xl px-5 py-4 flex items-start gap-3
                          border border-[var(--ad-warning-light,#FEF3C7)]"
            style={{ background: 'var(--ad-warning-bg, #FFFBEB)' }}>
            <ShieldAlert className="w-5 h-5 mt-0.5 shrink-0"
              style={{ color: 'var(--ad-warning, #AE6005)' }} />
            <div>
              <div className="text-sm font-semibold"
                style={{ color: 'var(--ad-warning, #AE6005)' }}>
                Retained and not placed —{' '}
                {data.retained_unplaced_by_currency?.length
                  ? data.retained_unplaced_by_currency
                      .map(r => money(r.amount, r.currency)).join('  ·  ')
                  : '—'}
              </div>
              <p className="text-xs mt-1 text-[var(--ad-warning,#AE6005)]">
                {data.retained_note}
              </p>
            </div>
          </div>
        )}

        {/* By reinsurer */}
        <div className="mt-6">
          <h2 className="text-sm font-semibold mb-2" style={{ color: NAVY }}>
            FAC amount by reinsurer
          </h2>
          <Card>
            <CardContent className="p-0">
              {loading ? (
                <div className="py-10 text-center text-sm text-[var(--ad-text-secondary,#6B7280)]">
                  Loading…
                </div>
              ) : !data?.by_reinsurer.length ? (
                <div className="py-10 text-center text-sm text-[var(--ad-text-secondary,#6B7280)]">
                  No facultative allocations recorded yet.
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-[var(--ad-border,#E5E7EB)]
                                     text-[11px] uppercase tracking-wider
                                     text-[var(--ad-text-secondary,#6B7280)]">
                        <th className="text-left font-medium px-5 py-3">Reinsurer</th>
                        <th className="text-left font-medium px-5 py-3">Group</th>
                        <th className="text-right font-medium px-5 py-3">Active risks</th>
                        <th className="text-right font-medium px-5 py-3">Active amount</th>
                        <th className="text-right font-medium px-5 py-3">Expired risks</th>
                        <th className="text-right font-medium px-5 py-3">Expired amount</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.by_reinsurer.map(b => (
                        <tr key={b.short_code}
                          className="border-b border-[var(--ad-border,#F3F4F6)] last:border-0
                                     transition-colors hover:bg-[var(--ad-orange-50,#FFF7ED)]">
                          <td className="px-5 py-3">
                            <div className="font-medium" style={{ color: NAVY }}>
                              {b.reinsurer}
                            </div>
                            {!b.approved && (
                              <div className="text-xs mt-0.5 font-medium"
                                style={{ color: 'var(--ad-error, #B91C1C)' }}>
                                not approved for placement
                              </div>
                            )}
                          </td>
                          <td className="px-5 py-3 text-[var(--ad-text-secondary,#6B7280)]">
                            {b.carrier_group ?? '—'}
                          </td>
                          <td className="px-5 py-3 text-right tabular-nums">{b.active_count}</td>
                          <td className="px-5 py-3 text-right font-medium"
                            style={{ color: NAVY }}>
                            <MoneyByCurrency rows={b.by_currency} field="active_amount" />
                          </td>
                          <td className="px-5 py-3 text-right tabular-nums
                                         text-[var(--ad-text-secondary,#6B7280)]">
                            {b.expired_count}
                          </td>
                          <td className="px-5 py-3 text-right
                                         text-[var(--ad-text-secondary,#6B7280)]">
                            <MoneyByCurrency rows={b.by_currency} field="expired_amount" />
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

        {/* The risks themselves */}
        <div className="mt-6">
          <div className="flex items-baseline gap-2 mb-2">
            <h2 className="text-sm font-semibold" style={{ color: NAVY }}>
              Risks
            </h2>
            <span className="text-xs text-[var(--ad-text-secondary,#6B7280)]">
              {/* count is the WHOLE matched set, shown is this page — so
                  "<count> shown" becomes a lie the moment paging bites. */}
              {data ? `${data.shown} of ${data.count}` : ''}
            </span>
          </div>
          <Card>
            <CardContent className="p-0">
              {loading ? (
                <div className="py-10 text-center text-sm text-[var(--ad-text-secondary,#6B7280)]">
                  Loading…
                </div>
              ) : !data?.results.length ? (
                <div className="py-12 text-center">
                  <div className="text-sm font-medium" style={{ color: NAVY }}>
                    Nothing in the register yet
                  </div>
                  <div className="text-xs mt-1 text-[var(--ad-text-secondary,#6B7280)]">
                    Facultative placements appear here once they are imported or
                    captured. An empty register means no data has been loaded — it
                    does not mean there is no facultative exposure.
                  </div>
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-[var(--ad-border,#E5E7EB)]
                                     text-[11px] uppercase tracking-wider
                                     text-[var(--ad-text-secondary,#6B7280)]">
                        <th className="text-left font-medium px-5 py-3">Reference</th>
                        <th className="text-left font-medium px-5 py-3">Policy</th>
                        <th className="text-left font-medium px-5 py-3">Class</th>
                        <th className="text-right font-medium px-5 py-3">Sum insured</th>
                        <th className="text-right font-medium px-5 py-3">Placed</th>
                        <th className="text-right font-medium px-5 py-3">Retained</th>
                        <th className="text-left font-medium px-5 py-3">Cover to</th>
                        <th className="text-left font-medium px-5 py-3">Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.results.map(e => (
                        <tr key={e.id}
                          className="border-b border-[var(--ad-border,#F3F4F6)] last:border-0
                                     transition-colors hover:bg-[var(--ad-orange-50,#FFF7ED)]">
                          <td className="px-5 py-3 font-medium" style={{ color: NAVY }}>
                            {e.reference}
                          </td>
                          <td className="px-5 py-3 text-[var(--ad-text-secondary,#6B7280)]">
                            {e.policy_number ?? '—'}
                          </td>
                          <td className="px-5 py-3 text-[var(--ad-text-secondary,#6B7280)]">
                            {e.regulatory_class ?? '—'}
                          </td>
                          <td className="px-5 py-3 text-right tabular-nums">
                            {money(e.gross_sum_insured, e.currency_code)}
                          </td>
                          <td className="px-5 py-3 text-right tabular-nums font-medium"
                            style={{ color: NAVY }}>
                            {money(e.fac_placed_amount, e.currency_code)}
                          </td>
                          <td className="px-5 py-3 text-right tabular-nums font-medium"
                            style={{ color: 'var(--ad-warning, #AE6005)' }}>
                            {money(e.unplaced_retained_amount, e.currency_code)}
                          </td>
                          <td className="px-5 py-3 text-[var(--ad-text-secondary,#6B7280)]">
                            {e.expiry_date ?? 'not stated'}
                          </td>
                          <td className="px-5 py-3">
                            <span className="text-xs px-2.5 py-1 rounded-full font-medium"
                              style={e.is_active
                                ? { background: 'var(--ad-success-bg, #ECFDF5)',
                                    color: 'var(--ad-success, #059669)' }
                                : { background: 'var(--ad-gray-100, #F3F4F6)',
                                    color: 'var(--ad-text-secondary, #6B7280)' }}>
                              {e.status_label}
                            </span>
                            {e.status_overridden && (
                              <div className="text-[11px] mt-1"
                                style={{ color: 'var(--ad-warning, #AE6005)' }}
                                title={e.status_override_reason ?? undefined}>
                                set by hand
                              </div>
                            )}
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

        <p className="text-xs mt-6 text-[var(--ad-text-secondary,#6B7280)]">
          Amounts shown as “not stated” are genuinely unknown in the source — they
          are not zero. Percentage shares work the same way: {pct(null)}.
        </p>
      </div>
    </div>
  )
}
