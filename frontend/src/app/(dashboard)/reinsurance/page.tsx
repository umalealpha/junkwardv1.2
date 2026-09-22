'use client'

/**
 * /reinsurance — Reinsurer Controls control centre.
 *
 * Arun P. Iyer's brief via the CFO, 15-Sep-2026: reinsurer security is a core
 * ERM risk, so this screen has to answer four questions at a glance —
 *   who is approved, who is stuck, what evidence is missing, and what are we
 *   exposed to facultatively.
 *
 * Two rules the layout obeys, because they are the point of the screen:
 *   1. A GAP IS NOT A ZERO. Missing evidence is rendered in warning colour with
 *      a count, never as a clean tick. Nothing here may imply that unknown data
 *      is good data.
 *   2. UNPLACED CAPACITY IS RETAINED, NOT CEDED. It gets its own panel, in
 *      amber, worded so it cannot be mistaken for reinsurance cover.
 *
 * This replaces a redirect to /reinsurance/treaties. The treaty screens are
 * untouched and still reachable from the sidebar.
 */

import { useCallback, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import {
  getToken, getReinsuranceControlCentre, transitionReinsurer,
} from '@/lib/api'
import type { ReinsuranceControlCentre, ReinsurerSummary } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  ShieldCheck, AlertTriangle, Clock, Ban, FileWarning, Layers,
  ArrowRight, RefreshCw, ChevronRight,
} from 'lucide-react'

const NAVY = 'var(--ad-navy, #0B0B3B)'
const ORANGE = 'var(--ad-orange, #F07F00)'

function money(s: string | null | undefined, currency = 'BWP'): string {
  if (s == null) return 'unknown'
  const v = Number(s)
  if (!isFinite(v)) return 'unknown'
  return `${currency} ${v.toLocaleString(undefined,
    { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`
}

/**
 * One money line per currency.
 *
 * Reinsurance control QC, 16-Sep-2026: this screen showed a single figure with
 * BWP written in front of it, and the server had reached that figure by adding
 * pula to dollars. Pick a field out of fac_exposure.by_currency and every
 * currency gets its own line, each labelled with its own code. With one
 * currency it reads exactly as it did before.
 */
function MoneyByCurrency({
  rows, field,
}: {
  rows: { currency: string; [k: string]: string | number }[] | undefined
  field: string
}) {
  if (!rows || rows.length === 0) return <>—</>
  return (
    <span className="flex flex-col gap-0.5">
      {rows.map(r => (
        <span key={r.currency} className="tabular-nums">
          {money(String(r[field] ?? ''), r.currency)}
        </span>
      ))}
    </span>
  )
}

/** A headline number. Scale contrast is what gives the screen its hierarchy —
 *  the figure is big, its label is small and quiet. */
function Stat({ label, value, tone = 'plain', hint }: {
  label: string
  // React.ReactNode, not just string | number: a money figure is now one line
  // per currency, so the value can be an element.
  value: React.ReactNode
  tone?: 'plain' | 'good' | 'warn' | 'bad'
  hint?: string
}) {
  const color = tone === 'good' ? 'var(--ad-success, #059669)'
    : tone === 'warn' ? 'var(--ad-warning, #AE6005)'
      : tone === 'bad' ? 'var(--ad-error, #B91C1C)'
        : NAVY
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wider font-medium text-[var(--ad-text-secondary,#6B7280)]">
        {label}
      </div>
      <div className="text-3xl font-semibold mt-1 tabular-nums leading-none"
        style={{ color }}>{value}</div>
      {hint && <div className="text-xs mt-1.5 text-[var(--ad-text-secondary,#6B7280)]">{hint}</div>}
    </div>
  )
}

export default function ReinsuranceControlCentrePage() {
  const router = useRouter()
  const [data, setData] = useState<ReinsuranceControlCentre | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      setData(await getReinsuranceControlCentre())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load the control centre')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    void load()
  }, [load, router])

  async function act(r: ReinsurerSummary, target: string) {
    // A return, rejection or suspension must carry a reason — the server
    // refuses a blank one, so ask for it here rather than letting it fail.
    const needsReason = ['returned', 'rejected', 'suspended'].includes(target)
    let comment = ''
    if (needsReason) {
      comment = window.prompt(`Why is ${r.name} being stopped or sent back?`) ?? ''
      if (!comment.trim()) return
    }
    setBusyId(r.id); setError(null)
    try {
      await transitionReinsurer(r.id, target, comment)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'That step was not allowed')
    } finally {
      setBusyId(null)
    }
  }

  const c = data?.counterparties
  const g = data?.evidence_gaps
  const f = data?.fac_exposure
  const gapTotal = g
    ? g.no_security_assessment + g.rating_not_verified + g.approval_expiring_60d
    : 0

  return (
    <div className="min-h-screen bg-[var(--ad-bg-page,#F8F9FB)]">
      <TopBar title="Reinsurer Controls" />
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">

        {/* Header ------------------------------------------------------- */}
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div className="flex items-center gap-3">
            <div className="w-11 h-11 rounded-xl flex items-center justify-center shrink-0"
              style={{ background: NAVY }}>
              <ShieldCheck className="w-5 h-5" style={{ color: ORANGE }} />
            </div>
            <div>
              <h1 className="text-2xl font-semibold tracking-tight" style={{ color: NAVY }}>
                Reinsurer Controls
              </h1>
              <p className="text-sm text-[var(--ad-text-secondary,#6B7280)]">
                Counterparty security, onboarding and facultative exposure
                {data ? ` · as at ${data.as_of}` : ''}
              </p>
            </div>
          </div>
          <Button variant="outline" onClick={() => void load()} disabled={loading}
            className="border-[var(--ad-border,#D1D5DB)] transition-colors">
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

        {/* Counterparty standing --------------------------------------- */}
        <div className="mt-6 rounded-2xl px-6 py-6 text-white shadow-sm"
          style={{ background: NAVY }}>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-6">
            <div>
              <div className="text-[11px] uppercase tracking-wider font-medium"
                style={{ color: ORANGE }}>Approved for placement</div>
              <div className="text-4xl font-semibold mt-1 tabular-nums leading-none">
                {loading ? '…' : c?.approved ?? 0}
              </div>
              <div className="text-xs mt-2 text-white/60">
                of {c?.total ?? 0} counterparties on file
              </div>
            </div>
            <div>
              <div className="text-[11px] uppercase tracking-wider font-medium text-white/60">
                In onboarding
              </div>
              <div className="text-3xl font-semibold mt-1 tabular-nums leading-none">
                {loading ? '…' : c?.pending ?? 0}
              </div>
              <div className="text-xs mt-2 text-white/50">awaiting a signature</div>
            </div>
            <div>
              <div className="text-[11px] uppercase tracking-wider font-medium text-white/60">
                Blocked
              </div>
              <div className="text-3xl font-semibold mt-1 tabular-nums leading-none">
                {loading ? '…' : c?.blocked ?? 0}
              </div>
              <div className="text-xs mt-2 text-white/50">
                suspended, rejected, expired or returned
              </div>
            </div>
            <div>
              <div className="text-[11px] uppercase tracking-wider font-medium text-white/60">
                Not yet submitted
              </div>
              <div className="text-3xl font-semibold mt-1 tabular-nums leading-none">
                {loading ? '…' : c?.draft ?? 0}
              </div>
              <div className="text-xs mt-2 text-white/50">still in draft</div>
            </div>
          </div>
        </div>

        {/* Evidence gaps ------------------------------------------------
            Deliberately loud. Paul Beka reported incomplete offshore financials
            and outstanding Munich Re / Kuwait Re / GIC Re information; the
            whole point is that those stay on screen until somebody closes
            them, and never quietly become "approved". */}
        <div className="mt-5">
          <div className="flex items-center gap-2 mb-2">
            <FileWarning className="w-4 h-4" style={{ color: 'var(--ad-warning, #AE6005)' }} />
            <h2 className="text-sm font-semibold" style={{ color: NAVY }}>
              Evidence gaps
            </h2>
            {gapTotal > 0 && (
              <span className="text-[11px] px-2 py-0.5 rounded-full font-medium
                               bg-[var(--ad-warning-light,#FEF3C7)] text-[var(--ad-warning,#AE6005)]">
                {gapTotal} open
              </span>
            )}
          </div>
          <Card className="border-[var(--ad-warning-light,#FEF3C7)]"
            style={{ background: 'var(--ad-warning-bg, #FFFBEB)' }}>
            <CardContent className="py-5">
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-6">
                <Stat label="No security assessment"
                  value={loading ? '…' : g?.no_security_assessment ?? 0}
                  tone={(g?.no_security_assessment ?? 0) > 0 ? 'warn' : 'good'}
                  hint="Never rated, never assessed" />
                <Stat label="Rating not verified"
                  value={loading ? '…' : g?.rating_not_verified ?? 0}
                  tone={(g?.rating_not_verified ?? 0) > 0 ? 'warn' : 'good'}
                  hint="Recorded but not checked to evidence" />
                <Stat label="National scale only"
                  value={loading ? '…' : g?.national_scale_only ?? 0}
                  tone={(g?.national_scale_only ?? 0) > 0 ? 'warn' : 'good'}
                  hint="Not comparable to an international rating" />
                <Stat label="Approval expiring in 60 days"
                  value={loading ? '…' : g?.approval_expiring_60d ?? 0}
                  tone={(g?.approval_expiring_60d ?? 0) > 0 ? 'warn' : 'good'}
                  hint="Review falls due" />
              </div>
              {g && (
                <p className="text-xs mt-4 pt-3 border-t border-[var(--ad-warning-light,#FEF3C7)]
                              text-[var(--ad-warning,#AE6005)]">
                  {g.note}
                </p>
              )}
            </CardContent>
          </Card>
        </div>

        {/* Facultative exposure — bento, because the three figures are not
            equal in weight: placed is the headline, retained is the warning,
            expired is context. */}
        <div className="mt-5 grid grid-cols-1 lg:grid-cols-3 gap-4">
          <Card className="lg:col-span-2">
            <CardContent className="py-5">
              <div className="flex items-center gap-2 mb-4">
                <Layers className="w-4 h-4" style={{ color: ORANGE }} />
                <h2 className="text-sm font-semibold" style={{ color: NAVY }}>
                  Facultative exposure — active
                </h2>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-6">
                <Stat label="Placed with reinsurers"
                  value={loading ? '…'
                    : <MoneyByCurrency rows={f?.by_currency} field="active_placed" />}
                  hint={f?.mixed_currency
                    ? 'Shown per currency — never added together'
                    : undefined} />
                <Stat label="Gross sum insured"
                  value={loading ? '…'
                    : <MoneyByCurrency rows={f?.by_currency} field="active_sum_insured" />} />
                <Stat label="Risks" value={loading ? '…' : f?.active_count ?? 0}
                  hint={`${f?.expired_count ?? 0} expired`} />
              </div>
              <Link href="/reinsurance/fac-risk"
                className="inline-flex items-center gap-1 text-sm mt-5 font-medium
                           transition-colors hover:underline focus:outline-none
                           focus-visible:ring-2 focus-visible:ring-offset-2 rounded"
                style={{ color: ORANGE }}>
                Open the FAC risk register <ArrowRight className="w-4 h-4" />
              </Link>
            </CardContent>
          </Card>

          <Card className="border-[var(--ad-warning-light,#FEF3C7)]"
            style={{ background: 'var(--ad-warning-bg, #FFFBEB)' }}>
            <CardContent className="py-5">
              <div className="flex items-center gap-2 mb-4">
                <AlertTriangle className="w-4 h-4"
                  style={{ color: 'var(--ad-warning, #AE6005)' }} />
                <h2 className="text-sm font-semibold"
                  style={{ color: 'var(--ad-warning, #AE6005)' }}>
                  Retained — not placed
                </h2>
              </div>
              <Stat label="On our own book"
                value={loading ? '…'
                  : <MoneyByCurrency rows={f?.by_currency} field="retained_unplaced" />}
                tone="warn" />
              <p className="text-xs mt-4 text-[var(--ad-warning,#AE6005)]">
                {f?.note ?? 'Capacity we sought and did not place. It stays with '
                  + 'Alpha Direct and is never shown as ceded.'}
              </p>
            </CardContent>
          </Card>
        </div>

        {/* Onboarding queue -------------------------------------------- */}
        <div className="mt-6">
          <div className="flex items-center gap-2 mb-2">
            <Clock className="w-4 h-4" style={{ color: NAVY }} />
            <h2 className="text-sm font-semibold" style={{ color: NAVY }}>
              Onboarding queue
            </h2>
            <span className="text-xs text-[var(--ad-text-secondary,#6B7280)]">
              waiting on a person
            </span>
          </div>
          <Card>
            <CardContent className="p-0">
              {loading ? (
                <div className="py-12 text-center text-sm text-[var(--ad-text-secondary,#6B7280)]">
                  Loading…
                </div>
              ) : !data?.queue.length ? (
                <div className="py-12 text-center">
                  <ShieldCheck className="w-8 h-8 mx-auto mb-2"
                    style={{ color: 'var(--ad-success, #059669)' }} />
                  <div className="text-sm font-medium" style={{ color: NAVY }}>
                    Nothing is waiting
                  </div>
                  <div className="text-xs mt-1 text-[var(--ad-text-secondary,#6B7280)]">
                    No counterparty is sitting in the approval chain.
                  </div>
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
                        <th className="text-left font-medium px-5 py-3">For</th>
                        <th className="text-left font-medium px-5 py-3">Waiting on</th>
                        <th className="text-right font-medium px-5 py-3">Decision</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.queue.map(r => (
                        <tr key={r.id}
                          className="border-b border-[var(--ad-border,#F3F4F6)] last:border-0
                                     transition-colors hover:bg-[var(--ad-orange-50,#FFF7ED)]">
                          <td className="px-5 py-3.5">
                            <div className="font-medium" style={{ color: NAVY }}>{r.name}</div>
                            <div className="text-xs text-[var(--ad-text-secondary,#6B7280)]">
                              {r.short_code}{r.domicile ? ` · ${r.domicile}` : ''}
                            </div>
                          </td>
                          <td className="px-5 py-3.5 text-[var(--ad-text-secondary,#6B7280)]">
                            {r.carrier_group ?? '—'}
                          </td>
                          <td className="px-5 py-3.5">
                            {r.onboarding_purposes.length
                              ? r.onboarding_purposes.join(', ')
                              : <span className="text-[var(--ad-warning,#AE6005)]">not stated</span>}
                          </td>
                          <td className="px-5 py-3.5">
                            <span className="text-xs px-2.5 py-1 rounded-full font-medium
                                             bg-[var(--ad-navy-50,#EDEDF5)]"
                              style={{ color: NAVY }}>
                              {r.approval_status_label}
                            </span>
                          </td>
                          <td className="px-5 py-3.5">
                            {/* The decision itself, on the row where it is read.
                                A step this person may not take is shown disabled
                                with the server's own reason on hover, never
                                hidden — a missing button reads as "nothing to
                                do here", which is how an approval sits for a
                                week waiting on someone who was never told. */}
                            <div className="flex flex-wrap justify-end gap-1.5">
                              {r.available_actions.map(a => (
                                <button key={a.target} type="button"
                                  disabled={!a.allowed || busyId === r.id}
                                  title={a.blocked_because ?? undefined}
                                  onClick={() => void act(r, a.target)}
                                  className="text-xs font-medium px-2.5 py-1.5 rounded-md border
                                             transition-colors
                                             disabled:opacity-40 disabled:cursor-not-allowed
                                             enabled:hover:bg-[var(--ad-orange-50,#FFF7ED)]"
                                  style={{
                                    borderColor: a.allowed ? ORANGE : 'var(--ad-border,#E5E7EB)',
                                    color: a.allowed ? ORANGE : 'var(--ad-text-secondary,#6B7280)',
                                  }}>
                                  {busyId === r.id ? 'Working…' : a.label}
                                </button>
                              ))}
                              <Link href={`/reinsurance/reinsurers/${r.id}`}
                                className="inline-flex items-center gap-0.5 text-sm font-medium
                                           px-1.5 py-1.5 transition-colors hover:underline"
                                style={{ color: NAVY }}>
                                Review <ChevronRight className="w-4 h-4" />
                              </Link>
                            </div>
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

        {/* Where else to go */}
        <div className="mt-6 flex flex-wrap gap-3">
          {[
            { href: '/reinsurance/security', label: 'Security panel & concentration' },
            { href: '/reinsurance/fac-risk', label: 'FAC risk register' },
            { href: '/reinsurance/reinsurers', label: 'All counterparties' },
            { href: '/reinsurance/treaties', label: 'Treaties' },
          ].map(l => (
            <Link key={l.href} href={l.href}
              className="text-sm px-4 py-2 rounded-lg border bg-white transition-colors
                         border-[var(--ad-border,#E5E7EB)]
                         hover:border-[var(--ad-orange,#F07F00)]
                         hover:bg-[var(--ad-orange-50,#FFF7ED)]"
              style={{ color: NAVY }}>
              {l.label}
            </Link>
          ))}
        </div>

        <p className="text-xs mt-6 text-[var(--ad-text-secondary,#6B7280)]">
          <Ban className="w-3 h-3 inline mr-1 align-[-1px]" />
          Nothing on this screen posts a journal, moves money or changes a policy.
        </p>
      </div>
    </div>
  )
}
