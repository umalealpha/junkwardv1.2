'use client'

/**
 * A read-only assist box that sits under the claim-number field on a claims
 * form. The moment a valid Graphite claim number is typed, it looks the claim
 * up in Omni and shows the facts (customer, policy, reserve, paid, balance) plus
 * an AI plain-English read and an advisory PAY/HOLD.
 *
 * It is an assist, never a control: every figure is Omni's own arithmetic, the
 * AI only narrates it, and any failure is silent — the box must never break the
 * form. On a successful load it hands the parent the facts text so an empty
 * description field can be prefilled.
 */

import { useEffect, useRef, useState, type ReactNode } from 'react'
import { getClaimInsight, type ClaimInsight } from '@/lib/api'
import { Card, CardContent } from '@/components/ui/card'
import { Sparkles } from 'lucide-react'

// A valid Graphite claim number: G followed by at least six digits.
const CLAIM_RE = /^G\d{6,}$/

const money = (currency: string, v: string) =>
  `${currency} ${(Number(v) || 0).toLocaleString('en-GB', { minimumFractionDigits: 2 })}`

interface Props {
  claimNumber: string
  amount?: number | string
  entity?: string
  payee?: string
  onFacts?: (factsText: string) => void
}

export function ClaimInsightBox({ claimNumber, amount, entity, payee, onFacts }: Props) {
  const [data, setData] = useState<ClaimInsight | null>(null)
  const [loading, setLoading] = useState(false)

  // Keep the latest onFacts without making it a fetch dependency (a parent
  // passing an inline callback must not trigger a re-fetch on every render).
  const onFactsRef = useRef(onFacts)
  useEffect(() => { onFactsRef.current = onFacts }, [onFacts])

  useEffect(() => {
    // Not a valid claim number yet — clear and stay silent.
    if (!CLAIM_RE.test(claimNumber || '')) {
      setData(null)
      setLoading(false)
      return
    }
    // Debounce ~400ms; ignore a stale response via the cancelled flag, exactly
    // like PaymentSummaryPanel.
    let cancelled = false
    setLoading(true)
    const timer = setTimeout(async () => {
      try {
        const res = await getClaimInsight(claimNumber, { amount, entity, payee })
        if (cancelled) return
        setData(res)
        setLoading(false)
        if (res.found) onFactsRef.current?.(res.facts_text)
      } catch {
        // An assist box must never break the form — fail silent.
        if (cancelled) return
        setData(null)
        setLoading(false)
      }
    }, 400)
    return () => { cancelled = true; clearTimeout(timer) }
  }, [claimNumber, amount, entity, payee])

  if (!CLAIM_RE.test(claimNumber || '')) return null
  if (loading) {
    return <p className="mt-2 text-xs text-[#9CA3AF]">Looking up claim…</p>
  }
  if (!data) return null
  if (!data.found) {
    return (
      <p className="mt-2 text-xs text-[#9CA3AF]">
        No matching claim in Omni yet — it will fill once synced.
      </p>
    )
  }

  const custTag = (
    <span className="shrink-0 rounded-full bg-[#0D1B2A]/5 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-[#6B7280]">
      {data.is_company ? 'Company' : 'Individual'}
    </span>
  )

  return (
    <Card className="mt-2 border-[#0D1B2A]/15">
      <CardContent className="py-4">
        {/* Which claim this is. */}
        <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
          <p className="text-sm font-bold text-[#0D1B2A]">{data.claim_number}</p>
          <p className="text-xs text-[#6B7280]">
            {[data.claim_type, data.claim_handler].filter(Boolean).join(' · ')}
          </p>
        </div>

        {/* Facts — Omni's own record. */}
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          <Stat label="Customer" value={data.customer_name || '—'} tag={custTag} />
          <Stat label="Policy" value={data.policy_number || '—'} />
          <Stat label="Product" value={data.product_name || '—'} />
          <Stat label="Status" value={data.status || '—'} />
          <Stat label="Date of loss" value={data.date_of_loss || '—'} />
          <Stat label="Damage cause" value={data.damage_cause || '—'} />
        </div>

        {/* Money — every figure calculated by Omni. */}
        <div className="mt-3 grid grid-cols-3 gap-3">
          <Stat label="Reserve" value={money(data.currency, data.total_reserve)} />
          <Stat label="Paid" value={money(data.currency, data.total_payment)} />
          <Stat label="Balance" value={money(data.currency, data.balance)} accent />
        </div>

        {/* Hard flags raised by Omni. */}
        {data.flags && data.flags.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-1.5">
            {data.flags.map((f, i) => (
              <span key={f.code || i}
                    className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${flagClass(f.level)}`}>
                {f.label}
              </span>
            ))}
          </div>
        )}

        {/* AI read — advisory only, and only when there is a summary to show. */}
        {data.ai_summary ? (
          <div className="mt-4 rounded-lg border-l-4 border-[#0D1B2A] bg-[#F8FAFC] px-4 py-3">
            <p className="flex items-start gap-1.5 text-sm leading-relaxed text-[#1F2937]">
              <Sparkles className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#0D1B2A]" />
              <span>{data.ai_summary}</span>
            </p>
            {data.ai_suggestion && (
              <div className="mt-2 flex items-center gap-2">
                <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-semibold ${
                  data.ai_suggestion === 'PAY'
                    ? 'border-green-200 bg-green-50 text-green-700'
                    : 'border-amber-200 bg-amber-50 text-amber-700'}`}>
                  {data.ai_suggestion}
                </span>
                {data.ai_reason && <span className="text-xs text-[#6B7280]">{data.ai_reason}</span>}
              </div>
            )}
            <p className="mt-2 text-[11px] text-[#9CA3AF]">
              Advisory only — written by AI from the figures above. Every figure is
              calculated by Omni, never the AI.
              {data.ai_summary_at && (
                <> · AI read {new Date(data.ai_summary_at).toLocaleDateString('en-GB',
                  { day: 'numeric', month: 'short', year: 'numeric' })} (facts above are live)</>
              )}
            </p>
          </div>
        ) : (
          <p className="mt-4 text-[11px] text-[#9CA3AF]">
            AI summary will appear here once generated.
          </p>
        )}
      </CardContent>
    </Card>
  )
}

function Stat({ label, value, tag, accent }: {
  label: string; value: ReactNode; tag?: ReactNode; accent?: boolean
}) {
  return (
    <div className="rounded-lg border border-[#EAEEF3] bg-[#F8FAFC] px-3 py-2">
      <p className="text-[11px] uppercase tracking-wide text-[#6B7280]">{label}</p>
      <div className="mt-0.5 flex items-center gap-1.5">
        <span className={`min-w-0 truncate text-sm font-semibold ${accent ? 'text-[#F07F00]' : 'text-[#0D1B2A]'}`}>
          {value}
        </span>
        {tag}
      </div>
    </div>
  )
}

function flagClass(level: ClaimInsight['flags'][number]['level']): string {
  switch (level) {
    case 'danger':  return 'border-red-200 bg-red-50 text-red-700'
    case 'warning': return 'border-amber-200 bg-amber-50 text-amber-700'
    default:        return 'border-slate-200 bg-slate-100 text-slate-600'
  }
}
