'use client'

/**
 * The CFO's one-look summary, pinned above the payment request list.
 *
 * CFO 2026-08-09: "I have more than seven accountants requesting for payments,
 * and it is too much overwhelming for me to go and check everything." He should
 * not have to open each request to know what is waiting.
 *
 * It reads /payment-requests/summary/, which calls the SAME function as the
 * 09:30 email — so the screen and the email can never tell him two different
 * things. Every figure is Omni's own arithmetic; the AI only writes the
 * plain-English read of it.
 */

import { useEffect, useState, type ReactNode } from 'react'
import { getToken, SSO_SENTINEL_TOKEN } from '@/lib/api'
import { acquireApiToken, SSO_API_CALLS_READY } from '@/auth/msal'
import { Card, CardContent } from '@/components/ui/card'
import { AlertTriangle, Clock, Sparkles, Landmark, CheckCircle2, type LucideIcon } from 'lucide-react'

interface Override {
  ref: string; payee: string; currency: string; total: string
  loader: string; countersigned: boolean; category: string
}
interface Group { name: string; count: number; total: string }
interface Waiting {
  ref: string; payee: string; currency: string; total: string
  loader: string; age_days: number; entity: string
}
interface Summary {
  generated_at: string
  narrative: string
  narrative_source: string
  open_count: number
  waiting_cfo_count: number
  waiting_cfo_total: string
  waiting_finance_count: number
  waiting_finance_total: string
  stale_count: number
  settled_24h: number
  overrides: Override[]
  by_loader: Group[]
  by_entity: Group[]
  waiting_cfo: Waiting[]
}

// Feature 557a7689 (CFO 2026-09-01): same rule as the list below it — every
// figure here is an outgoing payment, so it always shows positive, matching
// the money() fix already shipped on the list page.
const money = (v: string) => Math.abs(Number(v)).toLocaleString('en-GB', { minimumFractionDigits: 2 })

export function PaymentSummaryPanel({ onOpenRef }: { onOpenRef?: (ref: string) => void } = {}) {
  const [s, setS] = useState<Summary | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const headers: Record<string, string> = {}
        let bearer: string | null = null
        if (SSO_API_CALLS_READY) bearer = await acquireApiToken()
        if (bearer) headers['Authorization'] = `Bearer ${bearer}`
        else {
          const t = getToken()
          if (t && t !== SSO_SENTINEL_TOKEN) headers['Authorization'] = `Token ${t}`
        }
        const res = await fetch('/api/v1/payment-requests/summary/', { headers })
        if (res.status === 403) throw new Error('403')   // not an approver — stay silent
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const data: Summary = await res.json()
        if (!cancelled) setS(data)
      } catch (e) {
        if (!cancelled) setErr(e instanceof Error ? e.message : 'Could not load the summary')
      }
    })()
    return () => { cancelled = true }
  }, [])

  // 403 is correct for anyone who is not the CFO or a finance approver — say
  // nothing. Any OTHER failure must be visible: this panel carries the unsigned-
  // override warning, the control that failed on 7 Aug, and a control that
  // disappears without a word is worse than no control (Fable 2026-08-09).
  if (err === '403') return null
  if (err) {
    return (
      <div className="mb-4 rounded-lg border border-amber-300 bg-amber-50 px-4 py-2 text-sm text-amber-800">
        Payment summary unavailable — the list below is still live. ({err})
      </div>
    )
  }
  if (!s) return null

  return (
    <Card className="mb-4 border-[#0D1B2A]/15">
      <CardContent className="py-4">
        {/* What the AI makes of today, first. */}
        <div className="mb-4 rounded-lg border-l-4 border-[#0D1B2A] bg-[#F8FAFC] px-4 py-3">
          <p className="text-sm leading-relaxed text-[#1F2937]">{renderNarrative(s.narrative, onOpenRef)}</p>
          <p className="mt-2 flex items-center gap-1.5 text-[11px] text-[#9CA3AF]">
            <Sparkles className="h-3 w-3" />
            Written by {s.narrative_source} from the figures below. Every number is
            calculated by Omni, never by the AI.
          </p>
        </div>

        {/* The duplicate-override banner was removed with the override itself
            (CFO 2026-09-02): a hard duplicate is now refused outright, so there
            is no "override with no second signature" state to warn about. */}

        {/* Stat cards restyled to match the CFO-approved Stitch mockup (557a7689,
            2026-09-01): a small icon badge per card instead of a flat colour
            block. Same props, same figures — presentation only. */}
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Stat icon={Clock} label="Waiting on you" value={s.waiting_cfo_count} sub={money(s.waiting_cfo_total)} loud />
          <Stat icon={Landmark} label="With finance" value={s.waiting_finance_count} sub={money(s.waiting_finance_total)} />
          <Stat icon={AlertTriangle} label="Over 3 days" value={s.stale_count} sub="with you" warn={s.stale_count > 0} />
          <Stat icon={CheckCircle2} label="Settled" value={s.settled_24h} sub="last 24h" />
        </div>

        {/* Age heat-strip (CFO 2026-08-31, Fable idea #2): one block per request
            waiting on the CFO, width ∝ amount, colour by days waiting — so the
            biggest, oldest ones are impossible to miss. Tap a block to open it. */}
        <AgeHeatStrip rows={s.waiting_cfo} onOpenRef={onOpenRef} />

        {s.by_loader.length > 0 && (
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            <Breakdown title="Who has requests open" rows={s.by_loader} />
            <Breakdown title="By company" rows={s.by_entity} />
          </div>
        )}

        {s.waiting_cfo.length > 0 && (
          <div className="mt-4">
            <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-[#6B7280]">
              Longest waiting on you
            </p>
            <div className="space-y-1">
              {s.waiting_cfo.slice(0, 6).map(r => (
                <button key={r.ref} type="button"
                        onClick={() => onOpenRef?.(r.ref)}
                        disabled={!onOpenRef}
                        className={`flex w-full items-center justify-between gap-3 text-left text-sm rounded px-1 -mx-1 py-0.5 ${
                          onOpenRef ? 'hover:bg-[#FFF7ED] cursor-pointer' : 'cursor-default'}`}>
                  <span className="min-w-0 truncate">
                    <b className="text-[#0B0B3B]">{r.ref}</b>{' '}
                    <span className="text-[#6B7280]">{r.payee} · {r.loader}</span>
                  </span>
                  <span className="shrink-0 whitespace-nowrap">
                    {r.currency} {money(r.total)}
                    <span className={`ml-2 inline-flex items-center gap-1 text-xs ${
                      r.age_days >= 3 ? 'font-semibold text-red-600' : 'text-[#6B7280]'}`}>
                      <Clock className="h-3 w-3" />{r.age_days}d
                    </span>
                  </span>
                </button>
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function Stat({ icon: Icon, label, value, sub, loud, warn }: {
  icon: LucideIcon; label: string; value: number; sub?: string; loud?: boolean; warn?: boolean
}) {
  const accent = warn ? '#DC2626' : loud ? '#F47C20' : '#1D3270'
  return (
    <div className="rounded-xl border border-[#E5E7EB] bg-white px-4 py-3 shadow-sm">
      <div className="flex items-start justify-between">
        <p className="text-[11px] font-semibold uppercase tracking-wide text-[#6B7280]">{label}</p>
        {/* The badge tint is derived from currentColor, NOT from `accent`
            directly: the site-wide Professional theme remaps a hardcoded
            `color: rgb(...)` with !important, so currentColor resolves to
            whatever the theme actually painted and the tint follows it. Using
            `${accent}1A` here would serialise to an rgba() background that no
            theme rule matches, leaving a stray orange circle on an otherwise
            slate-blue page. Identical rendering when no theme is applied. */}
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full"
              style={{ background: 'color-mix(in srgb, currentColor 10%, transparent)', color: accent }}>
          <Icon className="h-3.5 w-3.5" />
        </span>
      </div>
      <p className="mt-1 text-2xl font-bold" style={{ color: accent }}>{value}</p>
      {sub && <p className="text-xs text-[#6B7280]">{sub}</p>}
    </div>
  )
}

// Colour by how long a request has waited on the CFO.
function ageColor(days: number): string {
  if (days >= 7) return '#DC2626'      // over a week — red
  if (days >= 3) return '#F07F00'      // over 3 days — deep orange
  if (days >= 2) return '#F59E0B'      // 2–3 days — amber
  return '#10B981'                     // fresh — green
}

// Make PAY/… references inside the AI narrative tappable, so the summary reads
// like a to-do list: tap a request → it opens (CFO 2026-08-31, Fable idea #3).
function renderNarrative(text: string, onOpenRef?: (ref: string) => void): ReactNode {
  if (!text || !onOpenRef) return text
  const parts = text.split(/(PAY\/[A-Z0-9/]+)/g)
  return parts.map((part, i) =>
    /^PAY\/[A-Z0-9/]+$/.test(part)
      ? <button key={i} type="button" onClick={() => onOpenRef(part)}
                className="font-semibold text-[#B45309] underline decoration-dotted underline-offset-2 hover:text-[#92400E]">{part}</button>
      : <span key={i}>{part}</span>,
  )
}

// The age heat-strip: one block per request waiting on the CFO — wider = bigger
// amount, redder = waiting longer. Tap a block to open it (Fable idea #2).
function AgeHeatStrip({ rows, onOpenRef }: { rows: Waiting[]; onOpenRef?: (ref: string) => void }) {
  if (!rows || rows.length === 0) return null
  const items = [...rows].sort((a, b) => b.age_days - a.age_days)
  const dot = (c: string) => <span className="inline-block h-2 w-2 rounded-full" style={{ background: c }} />
  return (
    <div className="mt-4">
      <div className="mb-1.5 flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs font-semibold uppercase tracking-wide text-[#6B7280]">Waiting on you — by age &amp; size</p>
        <div className="flex items-center gap-2 text-[10px] text-[#9CA3AF]">
          <span className="inline-flex items-center gap-1">{dot('#10B981')}fresh</span>
          <span className="inline-flex items-center gap-1">{dot('#F59E0B')}2d+</span>
          <span className="inline-flex items-center gap-1">{dot('#F07F00')}3d+</span>
          <span className="inline-flex items-center gap-1">{dot('#DC2626')}7d+</span>
        </div>
      </div>
      <div className="flex h-7 w-full gap-0.5 overflow-hidden rounded-md">
        {items.map(r => (
          <button key={r.ref} type="button" onClick={() => onOpenRef?.(r.ref)} disabled={!onOpenRef}
                  title={`${r.ref} · ${r.payee} · ${r.currency} ${money(r.total)} · ${r.age_days}d`}
                  aria-label={`Open ${r.ref} — ${r.payee}, ${r.currency} ${money(r.total)}, waiting ${r.age_days} days`}
                  style={{ flexGrow: Math.max(1, Number(r.total) || 1), background: ageColor(r.age_days) }}
                  className={`min-w-[6px] transition-opacity ${onOpenRef ? 'cursor-pointer hover:opacity-80' : 'cursor-default'}`} />
        ))}
      </div>
      <p className="mt-1 text-[10px] text-[#9CA3AF]">Each block is one request. Tap to open.</p>
    </div>
  )
}

function Breakdown({ title, rows }: { title: string; rows: Group[] }) {
  return (
    <div>
      <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-[#6B7280]">{title}</p>
      <div className="space-y-0.5">
        {rows.slice(0, 8).map(r => (
          <div key={r.name} className="flex items-center justify-between text-sm">
            <span className="min-w-0 truncate text-[#1F2937]">{r.name}</span>
            <span className="shrink-0 whitespace-nowrap text-[#6B7280]">
              {r.count} · {money(r.total)}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
