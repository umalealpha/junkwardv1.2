'use client'

/**
 * MyRequestsBlock — the Welcome-page summary of "what is happening with my
 * requests" (CFO Workstream A, board items A1-A7, 15-Sep-2026).
 *
 * Deliberately a SUMMARY. The full list already lives at /my-requests and is
 * not duplicated here: this block shows what needs the employee's own action,
 * their leave balance, and the five most recent requests, then links through.
 * Data: /api/v1/my-requests/summary/.
 */
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { apiFetch } from '@/lib/api'
import {
  CalendarDays, Banknote, HandCoins, Wallet, Landmark, Receipt,
  ArrowRight, AlertTriangle, Loader2, Inbox, Plus,
} from 'lucide-react'

const NAVY = '#1D3270'
const ORANGE = '#F47C20'

interface Req {
  kind: string; kind_label: string; id: string; title: string
  amount: number | null; currency: string; bucket: string
  status_label: string; holder_label: string; holder_standard: string
  href: string; stuck: boolean; status_line: string
}
interface Action { kind: string; label: string; instruction: string; href: string }
interface Balance { label?: string; name?: string; available?: number }
interface Payload {
  active: number
  by_kind: Record<string, number>
  stuck: number
  recent: Req[]
  actions: Action[]
  actions_unavailable: string[]
  leave_balances: Balance[]
  leave_error: string
}

const KIND_ICON: Record<string, typeof Inbox> = {
  leave: CalendarDays, leave_pay: Banknote, loan: HandCoins,
  petty_cash: Wallet, payment: Landmark, expense: Receipt,
}

const money = (n: number | null, ccy: string) =>
  n == null ? '' : `${ccy || 'BWP'} ${n.toLocaleString(undefined, {
    minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

export function MyRequestsBlock() {
  const [data, setData] = useState<Payload | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    apiFetch<Payload>('/my-requests/summary/')
      .then(r => { if (alive) { setData(r); setLoading(false) } })
      .catch(() => { if (alive) { setError('Your requests could not be loaded just now.'); setLoading(false) } })
    return () => { alive = false }
  }, [])

  if (loading) {
    return (
      <section aria-labelledby="my-requests-heading" className="rounded-lg border border-gray-200 bg-white p-4">
        <h2 id="my-requests-heading" className="text-sm font-semibold" style={{ color: NAVY }}>My Requests</h2>
        <div className="flex items-center gap-2 text-sm text-gray-500 mt-3">
          <Loader2 className="w-4 h-4 animate-spin" /> Loading your requests…
        </div>
      </section>
    )
  }

  // An error state that says so, rather than an empty box that reads as "nothing outstanding".
  if (error || !data) {
    return (
      <section aria-labelledby="my-requests-heading" className="rounded-lg border border-gray-200 bg-white p-4">
        <h2 id="my-requests-heading" className="text-sm font-semibold" style={{ color: NAVY }}>My Requests</h2>
        <p className="text-sm text-gray-600 mt-3">{error || 'Your requests could not be loaded just now.'}</p>
        <Link href="/my-requests" className="text-sm font-medium mt-2 inline-block" style={{ color: ORANGE }}>
          Open My Requests →
        </Link>
      </section>
    )
  }

  const { actions, actions_unavailable, leave_balances, leave_error, recent } = data
  const annual = leave_balances?.find(b => /annual/i.test(b.label || b.name || ''))

  return (
    <section aria-labelledby="my-requests-heading" className="rounded-lg border border-gray-200 bg-white p-4">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <h2 id="my-requests-heading" className="text-sm font-semibold" style={{ color: NAVY }}>My Requests</h2>
        <div className="flex items-center gap-2">
          {/* A2 — opens the EXISTING leave workflow. No second leave flow. */}
          <Link
            href="/hris/leave"
            className="inline-flex items-center gap-1 rounded-md px-2.5 py-1.5 text-xs font-medium text-white"
            style={{ background: ORANGE }}
          >
            <Plus className="w-3.5 h-3.5" /> Request leave
          </Link>
          <Link href="/my-requests" className="text-xs font-medium" style={{ color: NAVY }}>
            See all →
          </Link>
        </div>
      </div>

      {/* A1 — leave balance, or an honest reason it is missing. */}
      <div className="mt-2 text-xs text-gray-600">
        {leave_error
          ? <span>{leave_error}</span>
          : annual
            ? <span>Annual leave available: <strong style={{ color: NAVY }}>{annual.available ?? 0} day(s)</strong></span>
            : <span>Leave balance not available.</span>}
      </div>

      {/* A6 — Needs your action. Only things the requester can act on. */}
      {actions.length > 0 && (
        <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 p-3">
          <div className="flex items-center gap-1.5 text-xs font-semibold text-amber-900">
            <AlertTriangle className="w-3.5 h-3.5" /> Needs your action
          </div>
          <ul className="mt-2 space-y-1.5">
            {actions.map((a, i) => (
              <li key={i} className="text-xs text-amber-900">
                <Link href={a.href} className="font-medium underline underline-offset-2">{a.label}</Link>
                <span className="text-amber-800"> — {a.instruction}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Never a fake all-clear: say which parts could not be checked. */}
      {actions_unavailable.length > 0 && (
        <p className="mt-2 text-[11px] text-gray-500">
          Could not check {actions_unavailable.join(', ')} just now, so this list may be incomplete.
        </p>
      )}

      {recent.length === 0 ? (
        <p className="mt-3 text-sm text-gray-500">You have not submitted any requests yet.</p>
      ) : (
        <ul className="mt-3 divide-y divide-gray-100">
          {recent.map(r => {
            const Icon = KIND_ICON[r.kind] ?? Inbox
            return (
              <li key={`${r.kind}-${r.id}`} className="py-2 flex items-start gap-2.5">
                <Icon className="w-4 h-4 mt-0.5 shrink-0" style={{ color: NAVY }} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline gap-2 flex-wrap">
                    <span className="text-sm text-gray-900 truncate">{r.title}</span>
                    {r.amount != null && (
                      <span className="text-xs text-gray-500">{money(r.amount, r.currency)}</span>
                    )}
                  </div>
                  {/* A7 — the standard phrase first, the precise desk after it. */}
                  <div className="text-xs text-gray-600">
                    {r.holder_standard || r.status_label}
                    {r.holder_standard && r.holder_label && r.holder_label !== r.holder_standard
                      ? <span className="text-gray-400"> · {r.holder_label}</span>
                      : null}
                    {r.stuck && <span className="ml-1 text-amber-700">· waiting a while</span>}
                  </div>
                </div>
                <Link href={r.href} aria-label={`Open ${r.kind_label}`} className="shrink-0 mt-0.5">
                  <ArrowRight className="w-4 h-4 text-gray-300" />
                </Link>
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}
