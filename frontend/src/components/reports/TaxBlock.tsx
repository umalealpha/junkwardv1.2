'use client'

/**
 * OMNI ERP — Expanded Tax Block (TAX-001).
 *
 * Replaces single Taxation line on /reports/profit-loss with an
 * IAS 12-structured disclosure: current/deferred/WHT split, ETR pill,
 * statutory-rate reconciliation, ETR warning banner.
 *
 * Source blueprint: TAX_DEFECT_BLUEPRINT.md (Internal Audit 2026-05-21).
 * Two modes:
 *   - taxTotal only (legacy) — degraded view + amber developer notice
 *   - currentTax / deferredTax / withholdingTax split — full view
 */
import { useState } from 'react'

const STATUTORY_RATE = 0.22
const ETR_BAND_LOW   = 0.15
const ETR_BAND_HIGH  = 0.35

export interface TaxBlockGL {
  taxTotal?:       number
  currentTax?:     number
  deferredTax?:    number
  withholdingTax?: number
  permanentDiffs?: number
  temporaryDiffs?: number
}

interface Props {
  glBalances?: TaxBlockGL
  pbt?: number
  period?: string
  currency?: string
  isExpanded?: boolean
}

const fmt = (n: number, currency: string) =>
  new Intl.NumberFormat('en-BW', {
    style: 'currency',
    currency,
    minimumFractionDigits: 2,
  }).format(n ?? 0)

const fmtPct = (n: number) =>
  isFinite(n) ? `${(n * 100).toFixed(1)}%` : 'N/A'

type EtrStatus = 'low' | 'high' | 'normal' | 'unknown'

function etrStatus(etr: number): EtrStatus {
  if (!isFinite(etr)) return 'unknown'
  if (etr < ETR_BAND_LOW)  return 'low'
  if (etr > ETR_BAND_HIGH) return 'high'
  return 'normal'
}

function TaxRow({
  label, value, indent = false, bold = false, borderTop = false, note, currency,
}: {
  label: string; value: number; indent?: boolean; bold?: boolean;
  borderTop?: boolean; note?: string; currency: string
}) {
  return (
    <tr className={borderTop ? 'border-t border-gray-300' : ''}>
      <td className={`py-1 pr-4 text-sm ${indent ? 'pl-8' : 'pl-2'} ${bold ? 'font-semibold text-gray-900' : 'text-gray-600'}`}>
        {label}
        {note && <span className="ml-2 text-xs text-gray-400 italic">{note}</span>}
      </td>
      <td className={`py-1 text-right text-sm tabular-nums ${bold ? 'font-semibold text-gray-900' : 'text-gray-600'} ${value < 0 ? 'text-red-600' : ''}`}>
        {fmt(value, currency)}
      </td>
    </tr>
  )
}

function ETRBadge({ etr, pbt, currency }: { etr: number; pbt: number; currency: string }) {
  const status = etrStatus(etr)
  const statutoryTax = pbt * STATUTORY_RATE
  const variance = (pbt !== 0 && isFinite(etr)) ? Math.abs(etr - STATUTORY_RATE) * pbt : 0

  const config: Record<EtrStatus, { bg: string; icon: string; iconColor: string; title: string; body: string }> = {
    low: {
      bg: 'bg-amber-50 border-amber-300',
      icon: '⚠',
      iconColor: 'text-amber-500',
      title: 'ETR Below Expected Range',
      body: `Effective tax rate of ${fmtPct(etr)} is below the expected band of ${fmtPct(ETR_BAND_LOW)}–${fmtPct(ETR_BAND_HIGH)}. Tax at the statutory rate of ${fmtPct(STATUTORY_RATE)} would be ${fmt(statutoryTax, currency)}. Difference: ${fmt(variance, currency)}. Possible causes: assessed losses b/f, exempt income, or tax not yet accrued. Finance to confirm and document.`,
    },
    high: {
      bg: 'bg-red-50 border-red-300',
      icon: '⚠',
      iconColor: 'text-red-500',
      title: 'ETR Above Expected Range',
      body: `Effective tax rate of ${fmtPct(etr)} significantly exceeds the statutory rate of ${fmtPct(STATUTORY_RATE)}. Tax at statutory rate would be ${fmt(statutoryTax, currency)}. Excess: ${fmt(variance, currency)}. Likely causes: non-deductible provisions (IBNR, UPR, bad debts), deferred tax movement bundled into current tax line, or prior-year under-provision. Finance to provide tax computation breakdown.`,
    },
    normal: {
      bg: 'bg-green-50 border-green-300',
      icon: '✓',
      iconColor: 'text-green-600',
      title: 'ETR Within Expected Range',
      body: `Effective tax rate of ${fmtPct(etr)} is within the expected band of ${fmtPct(ETR_BAND_LOW)}–${fmtPct(ETR_BAND_HIGH)}.`,
    },
    unknown: {
      bg: 'bg-gray-50 border-gray-300',
      icon: '–',
      iconColor: 'text-gray-400',
      title: 'ETR Cannot Be Computed',
      body: 'PBT is zero or negative. ETR is not meaningful for this period.',
    },
  }

  const c = config[status]
  return (
    <div className={`mt-3 rounded border p-3 text-sm ${c.bg}`}>
      <div className="flex items-start gap-2">
        <span className={`mt-0.5 text-base font-bold ${c.iconColor}`}>{c.icon}</span>
        <div>
          <p className="font-semibold text-gray-800">{c.title}</p>
          <p className="mt-0.5 text-gray-600">{c.body}</p>
        </div>
      </div>
    </div>
  )
}

function TaxReconciliation({ pbt, totalTax, currency }: { pbt: number; totalTax: number; currency: string }) {
  const statutory      = pbt * STATUTORY_RATE
  const reconcVariance = totalTax - statutory
  return (
    <div className="mt-4 rounded border border-gray-200 bg-gray-50 p-3">
      <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
        Tax Reconciliation — IAS 12
      </p>
      <table className="w-full">
        <tbody>
          <TaxRow label={`Tax at statutory rate (${fmtPct(STATUTORY_RATE)} × PBT)`} value={statutory} currency={currency} />
          <TaxRow label="Variance (non-deductibles, deferred tax, prior-year adj.)"
                  value={reconcVariance} note="Finance to itemise" currency={currency} />
          <TaxRow label="Total Tax Expense per GL" value={totalTax} bold borderTop currency={currency} />
        </tbody>
      </table>
      <p className="mt-2 text-xs text-gray-400 italic">
        Variance line requires Finance to provide breakdown: non-deductible provisions, deferred
        tax on IBNR/UPR reserves, WHT on reinsurance cessions, prior-year under/over-provision.
      </p>
    </div>
  )
}

export default function TaxBlock({
  glBalances = {}, pbt = 0, period = '', currency = 'BWP', isExpanded: initialExpanded = true,
}: Props) {
  const [expanded, setExpanded] = useState(initialExpanded)

  const hasSplitAccounts =
    glBalances.currentTax !== undefined ||
    glBalances.deferredTax !== undefined

  const currentTax     = glBalances.currentTax     ?? 0
  const deferredTax    = glBalances.deferredTax    ?? 0
  const withholdingTax = glBalances.withholdingTax ?? 0
  const permanentDiffs = glBalances.permanentDiffs ?? 0
  const temporaryDiffs = glBalances.temporaryDiffs ?? 0

  const totalTax = hasSplitAccounts
    ? currentTax + deferredTax + withholdingTax
    : (glBalances.taxTotal ?? 0)

  const taxableIncome = pbt + permanentDiffs + temporaryDiffs
  const etr           = pbt !== 0 ? totalTax / pbt : Infinity
  const pat           = pbt - totalTax

  const pillClass =
    etrStatus(etr) === 'normal' ? 'bg-green-100 text-green-700' :
    etrStatus(etr) === 'low'    ? 'bg-amber-100 text-amber-700' :
    etrStatus(etr) === 'high'   ? 'bg-red-100 text-red-700' :
                                  'bg-gray-100 text-gray-500'

  return (
    <div className="my-2">
      {/* Header row */}
      <div
        className="flex cursor-pointer items-center justify-between rounded px-2 py-1.5 hover:bg-gray-50"
        onClick={() => setExpanded(v => !v)}
        title="Click to expand/collapse tax detail"
      >
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-gray-800">Taxation</span>
          <span className="rounded bg-gray-100 px-1.5 py-0.5 text-xs text-gray-500">
            Statutory rate: {fmtPct(STATUTORY_RATE)}
          </span>
          <span className={`rounded px-1.5 py-0.5 text-xs font-semibold ${pillClass}`}>
            ETR: {fmtPct(etr)}
          </span>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-sm font-semibold tabular-nums text-gray-900">
            {fmt(totalTax, currency)}
          </span>
          <span className="text-xs text-gray-400">{expanded ? '▲' : '▼'}</span>
        </div>
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div className="ml-2 mt-1 rounded border border-gray-100 bg-white p-3 shadow-sm">
          {period && (
            <p className="mb-2 text-xs font-medium uppercase tracking-wide text-gray-400">{period}</p>
          )}

          {hasSplitAccounts ? (
            <>
              <table className="w-full">
                <tbody>
                  <TaxRow label="Profit Before Tax" value={pbt} bold currency={currency} />
                  <TaxRow label="Add: Permanent differences (non-deductible expenses)"
                          value={permanentDiffs} indent note="GL source required" currency={currency} />
                  <TaxRow label="Add/(Less): Temporary differences"
                          value={temporaryDiffs} indent note="IBNR, UPR, provisions" currency={currency} />
                  <TaxRow label="Taxable Income" value={taxableIncome} bold borderTop currency={currency} />
                  <TaxRow label={`Current Tax @ ${fmtPct(STATUTORY_RATE)}`} value={currentTax}
                          indent note="GL account" currency={currency} />
                  <TaxRow label="Deferred Tax (IBNR / UPR / provision movements)" value={deferredTax}
                          indent note="GL account" currency={currency} />
                  <TaxRow label="Withholding Tax (reinsurance cessions / commissions)" value={withholdingTax}
                          indent note="GL account" currency={currency} />
                  <TaxRow label="Total Tax Expense" value={totalTax} bold borderTop currency={currency} />
                </tbody>
              </table>
              <TaxReconciliation pbt={pbt} totalTax={totalTax} currency={currency} />
            </>
          ) : (
            <>
              <div className="mb-3 rounded border border-amber-200 bg-amber-50 p-2 text-xs text-amber-700">
                <strong>Developer note:</strong> Tax GL has not yet been split into current /
                deferred / WHT accounts. Showing single GL balance only. To enable full
                breakdown, implement GL account split per TAX-002 (see blueprint).
              </div>
              <table className="w-full">
                <tbody>
                  <TaxRow label="Profit Before Tax" value={pbt} bold currency={currency} />
                  <TaxRow label="Total Tax Expense (single GL — breakdown pending)"
                          value={totalTax} indent note="GL account: expand per TAX-002" currency={currency} />
                  <TaxRow label="Total Tax Expense" value={totalTax} bold borderTop currency={currency} />
                </tbody>
              </table>
              <TaxReconciliation pbt={pbt} totalTax={totalTax} currency={currency} />
            </>
          )}

          <ETRBadge etr={etr} pbt={pbt} currency={currency} />

          <div className="mt-3 flex items-center justify-between border-t border-gray-200 pt-2">
            <span className="text-sm font-bold text-gray-900">Profit After Tax</span>
            <span className={`text-sm font-bold tabular-nums ${pat >= 0 ? 'text-gray-900' : 'text-red-600'}`}>
              {fmt(pat, currency)}
            </span>
          </div>
        </div>
      )}
    </div>
  )
}

// Helper for KPI tile (TAX-003) — exported so the P&L page can render
// a small ETR pill in the metrics bar.
export function computeETRStatus(pbt: number, tax: number): { etr: number; label: string; tone: 'normal' | 'low' | 'high' | 'unknown' } {
  const etr = pbt !== 0 ? tax / pbt : Infinity
  return { etr, label: fmtPct(etr), tone: etrStatus(etr) }
}
