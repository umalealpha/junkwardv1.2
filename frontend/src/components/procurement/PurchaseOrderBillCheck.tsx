'use client'

/**
 * PurchaseOrderBillCheck — surfaces the DeepSeek bill-vs-PO verdict that ALREADY
 * runs on every matched vendor bill (BillAIVerification) but was invisible until
 * now (CFO 2026-07-14). Read-only: it just reads the stored verdict back and
 * renders it in plain English. No amounts are sent anywhere — the check ran
 * server-side when the bill was matched; this only displays the result.
 *
 * Fails silent (renders nothing) if the caller can't read matches or the PO has
 * no matched bill yet, so it never clutters a fresh PO or errors on the page.
 */
import { useEffect, useState, type ReactNode } from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { getPOBillMatches, type POBillMatchExtended, type BillAIVerification } from '@/lib/api'
import { ShieldCheck, ShieldAlert, ShieldQuestion, Info } from 'lucide-react'

const FLAG_TEXT: Record<string, string> = {
  vendor_mismatch:  'the supplier name on the bill differs from the PO',
  currency_mismatch:'the currency differs from the PO',
  total_variance:   'the bill total differs from the PO',
  line_drift:       'the line items differ from the PO',
  back_dated_bill:  'the bill is dated before the PO',
  tax_anomaly:      'the VAT looks unusual',
  bank_detail_drift:'the banking details look changed',
  suspicious:       'something looks off and needs a human eye',
}

const STYLE: Record<string, { bg: string; border: string; text: string; icon: ReactNode; lead: string }> = {
  clean:       { bg: '#ECFDF5', border: '#A7F3D0', text: '#065F46', icon: <ShieldCheck className="w-4 h-4" />,  lead: 'No concerns.' },
  variance:    { bg: '#FFFBEB', border: '#FDE68A', text: '#92400E', icon: <Info className="w-4 h-4" />,          lead: 'Small difference, within policy tolerance.' },
  anomaly:     { bg: '#FFF7ED', border: '#FED7AA', text: '#9A3412', icon: <ShieldQuestion className="w-4 h-4" />, lead: 'Please review before paying.' },
  fraud_cue:   { bg: '#FEF2F2', border: '#FECACA', text: '#991B1B', icon: <ShieldAlert className="w-4 h-4" />,   lead: 'Possible fraud signal — review before paying.' },
  unavailable: { bg: '#F3F4F6', border: '#E5E7EB', text: '#6B7280', icon: <Info className="w-4 h-4" />,          lead: 'Automatic check was unavailable.' },
}

function reasons(v: BillAIVerification): string[] {
  const out: string[] = []
  if (!v.vendor_match) out.push(FLAG_TEXT.vendor_mismatch)
  if (!v.currency_match) out.push(FLAG_TEXT.currency_mismatch)
  if (!v.total_match) out.push(FLAG_TEXT.total_variance)
  for (const f of v.flags || []) {
    const t = FLAG_TEXT[f] || f.replace(/_/g, ' ')
    if (!out.includes(t)) out.push(t)
  }
  return out
}

export default function PurchaseOrderBillCheck({ poId }: { poId: string }) {
  const [matches, setMatches] = useState<POBillMatchExtended[]>([])
  const [hidden, setHidden] = useState(false)
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    if (!poId) return
    let cancelled = false
    getPOBillMatches({ purchase_order: poId })
      .then((r) => { if (!cancelled) { setMatches(r.results || []); setLoaded(true) } })
      .catch(() => { if (!cancelled) setHidden(true) })
    return () => { cancelled = true }
  }, [poId])

  if (hidden || !loaded) return null
  // Only show matches that actually carry an AI verdict.
  const withAI = matches.filter((m) => (m.ai_verifications || []).length > 0)
  if (withAI.length === 0) return null

  return (
    <Card>
      <CardContent className="p-4">
        <div className="font-semibold text-sm mb-3 flex items-center gap-2">
          <ShieldCheck className="w-4 h-4 text-[#1D3270]" /> Bill check (Aria)
        </div>
        <div className="space-y-3">
          {withAI.map((m) => {
            const v = m.ai_verifications[0]   // newest first (ordering -created_at)
            const s = STYLE[v.verdict] || STYLE.unavailable
            const rs = reasons(v)
            return (
              <div key={m.id} className="rounded-lg border p-3 text-sm"
                   style={{ background: s.bg, borderColor: s.border, color: s.text }}>
                <div className="flex items-center gap-2 font-medium">
                  {s.icon}
                  <span>Bill {m.bill_number || ''} vs this PO — {v.verdict_display}</span>
                </div>
                <p className="mt-1">
                  {v.verdict === 'clean'
                    ? `Aria compared the supplier's bill against the PO — ${s.lead}`
                    : `Aria flagged this: ${s.lead}`}
                </p>
                {/* Only list concerns for a non-clean verdict — never show a
                    concern under a green "no concerns" header (an inconsistent
                    AI response must not render a self-contradicting card). */}
                {v.verdict !== 'clean' && rs.length > 0 && (
                  <ul className="mt-1.5 list-disc pl-5 space-y-0.5">
                    {rs.map((r, i) => <li key={i}>{r}</li>)}
                  </ul>
                )}
                {v.notes && <p className="mt-1.5 italic opacity-80">{v.notes}</p>}
                <div className="mt-1.5 text-xs opacity-70">
                  PO {fmtMoney(m.po_total)} · Bill {fmtMoney(m.bill_total)}
                  {v.confidence ? ` · confidence ${v.confidence}%` : ''}
                </div>
              </div>
            )
          })}
        </div>
        <p className="mt-2 text-[11px] text-[#6B7280]">
          This is an automatic advisory check — it does not approve or block payment.
        </p>
      </CardContent>
    </Card>
  )
}

function fmtMoney(v: string | number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  const n = typeof v === 'string' ? parseFloat(v) : v
  if (Number.isNaN(n)) return '—'
  return 'P ' + new Intl.NumberFormat('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n)
}
