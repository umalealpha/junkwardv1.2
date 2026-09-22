'use client'

/**
 * TaxRowSlim — slim Taxation row for the P&L face.
 *
 * Source: TAX_FOLLOWUP_BLUEPRINT.md (TAX-005). The full TaxBlock with
 * IAS 12 reconciliation, statutory rate pill and developer notes was
 * too heavy to live between PBT and PAT on the Statement of
 * Comprehensive Income. This is the lightweight replacement:
 *
 *   Taxation          (BWP X,XXX,XXX)   [ETR 22.0%]   View tax reconciliation →
 *
 * Full disclosure now lives on /reports/tax-reconciliation (TAX-006).
 */
import Link from 'next/link'
import { ExternalLink } from 'lucide-react'

const STATUTORY_RATE = 0.22
const ETR_BAND_LOW   = 0.15
const ETR_BAND_HIGH  = 0.35

interface Props {
  totalTax: number
  pbt: number
  currency?: string
}

// OMNI-QA-014 (Lakshmi QA 2026-06-09): the en-BW currency style renders BWP
// as the "P" Pula glyph ("P 0.00"), inconsistent with the rest of the P&L
// which prefixes "BWP". Use a plain grouped number with the explicit code so
// the Taxation row matches every other line.
const fmtAmount = (n: number, currency: string) => {
  const num = new Intl.NumberFormat('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(Math.abs(n ?? 0))
  const sign = (n ?? 0) < 0 ? `(${currency} ${num})` : `${currency} ${num}`
  return sign
}

function etrPill(etr: number) {
  if (!isFinite(etr)) {
    return { label: 'ETR N/A', cls: 'bg-gray-100 text-gray-600 border-gray-200' }
  }
  const pct = `${(etr * 100).toFixed(1)}%`
  if (etr < ETR_BAND_LOW) {
    return { label: `ETR ${pct}`, cls: 'bg-amber-50 text-amber-700 border-amber-300' }
  }
  if (etr > ETR_BAND_HIGH) {
    return { label: `ETR ${pct}`, cls: 'bg-red-50 text-red-700 border-red-300' }
  }
  return { label: `ETR ${pct}`, cls: 'bg-emerald-50 text-emerald-700 border-emerald-300' }
}

export default function TaxRowSlim({
  totalTax,
  pbt,
  currency = 'BWP',
}: Props) {
  const etr = pbt !== 0 ? totalTax / pbt : NaN
  const pill = etrPill(etr)

  return (
    <div className="flex items-center justify-between py-2 border-t border-[#E5E7EB]">
      <span className="text-sm uppercase tracking-wider text-[#374151] font-semibold">
        Taxation
      </span>
      <div className="flex items-center gap-3">
        <span className="font-mono-nums text-sm font-semibold text-[#111827]">
          {fmtAmount(totalTax, currency)}
        </span>
        <span
          className={`inline-flex items-center px-2 py-0.5 text-[11px] font-medium rounded-full border ${pill.cls}`}
          title={`ETR = Total Tax ÷ PBT. Statutory rate Botswana CIT ${(STATUTORY_RATE * 100).toFixed(0)}%. Expected band ${ETR_BAND_LOW * 100}-${ETR_BAND_HIGH * 100}%.`}
        >
          {pill.label}
        </span>
        <Link
          href="/reports/tax-reconciliation"
          className="text-[11px] text-[#F07F00] hover:underline inline-flex items-center gap-0.5"
          title="View IAS 12 tax reconciliation — current vs deferred, ETR variance, statutory-rate reconciliation."
        >
          View tax reconciliation
          <ExternalLink className="w-3 h-3" />
        </Link>
      </div>
    </div>
  )
}
