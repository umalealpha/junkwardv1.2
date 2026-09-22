'use client'

/** /app/my-requests · /m/staff/my-requests — everything I have SUBMITTED, in one
 * list (CFO pick 2026-09-03). READ ONLY — the phone twin of the desktop
 * /my-requests page, driving the SAME endpoint:
 *   GET /api/v1/my-requests/  → { items, active, stuck, total }   (core/request_tracker.py)
 * Amounts and status lines are the server's, echoed verbatim — nothing is
 * computed here. */
import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { Inbox } from 'lucide-react'
import { reauthOn401, sfetch } from '@/app/(customer)/api'
import { C, pill } from '@/app/(customer)/ui'
import { useStaffBase } from './useStaffBase'
import { Card, RetryBanner, ScreenFrame, errText } from './StaffFormKit'

interface Step { label: string; done: boolean; at: string | null }
interface Req {
  kind: string; kind_label: string; id: string; title: string
  amount: number | null; currency: string
  bucket: 'pending' | 'approved' | 'rejected' | 'paid' | 'cancelled' | 'draft' | string
  status_label: string; holder_label: string; next_label: string
  submitted_at: string | null; updated_at: string | null
  days_waiting: number | null; href: string
  timeline: Step[]; stuck: boolean; status_line: string
}
interface Payload { items: Req[]; active: number; stuck: number; total: number }

const IN_FLIGHT = new Set(['pending', 'approved'])
const BUCKET_LABEL: Record<string, { label: string; bg: string; fg: string }> = {
  pending:   { label: 'In progress', bg: '#FEF3C7', fg: '#92400E' },
  approved:  { label: 'In progress', bg: '#FEF3C7', fg: '#92400E' },
  paid:      { label: 'Done',        bg: '#D1FAE5', fg: '#065F46' },
  rejected:  { label: 'Rejected',    bg: '#FEE2E2', fg: '#991B1B' },
  cancelled: { label: 'Cancelled',   bg: '#F0F2F5', fg: C.inkSoft },
  draft:     { label: 'Draft',       bg: '#F0F2F5', fg: C.inkSoft },
}

// Desktop href (from the server) → the phone screen that shows the same thing.
// Anything without a phone twin opens on the computer.
const PHONE_PATH: Record<string, string> = {
  '/hris/leave': '/leave',
  '/hris/staff-loans': '/staff-loan',
  '/petty-cash': '/petty-cash',
  '/payment-requests': '/raise-payment',
  '/hris/leave-encashment': '/leave-encashment',
}

// Formatting only — the number itself is the server's.
const money = (n: number | null, ccy: string) =>
  n == null ? '' : `${ccy || 'BWP'} ${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

function Stepper({ steps }: { steps: Step[] }) {
  return (
    <ol aria-label="Progress" style={{ listStyle: 'none', margin: '12px 0 0', padding: 0, display: 'flex', flexWrap: 'wrap', gap: '6px 0', alignItems: 'center' }}>
      {steps.map((s, i) => (
        <li key={i} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span aria-hidden="true" style={{ width: 18, height: 18, borderRadius: 999, display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 10, fontWeight: 800, background: s.done ? C.navy : '#E5E7EB', color: s.done ? '#fff' : '#6B7280' }}>{s.done ? '✓' : ''}</span>
          <span style={{ fontSize: 11.5, fontWeight: s.done ? 700 : 500, color: s.done ? C.ink : C.inkSoft }}>
            {s.label}<span style={{ position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)' }}>{s.done ? ' — done' : ' — not yet'}</span>
          </span>
          {i < steps.length - 1 && <span aria-hidden="true" style={{ color: '#CBD5E1', margin: '0 6px' }}>›</span>}
        </li>
      ))}
    </ol>
  )
}

export default function MyRequestsScreen() {
  const base = useStaffBase()
  const [data, setData] = useState<Payload | null>(null)
  const [loadErr, setLoadErr] = useState<string | null>(null)
  const [tab, setTab] = useState<'active' | 'done'>('active')

  const load = useCallback(() => {
    setLoadErr(null)
    sfetch<Payload>('/my-requests/').then(setData)
      .catch(e => { if (!reauthOn401(e)) setLoadErr(errText(e, 'Could not load your requests.')) })
  }, [])
  useEffect(() => { load() }, [load])

  const items = data?.items ?? []
  const shown = items.filter(r => IN_FLIGHT.has(r.bucket) === (tab === 'active'))
  const activeCount = items.filter(r => IN_FLIGHT.has(r.bucket)).length

  return (
    <ScreenFrame title="My requests" base={base}>
      {loadErr && <RetryBanner message={loadErr} onRetry={load} />}
      <p style={{ color: C.inkSoft, fontSize: 13, margin: 0, lineHeight: 1.5 }}>
        Everything you have asked for — leave, leave pay, loans, petty cash, payments — and whose desk it is on right now.
      </p>

      <div role="tablist" aria-label="Filter requests" style={{ display: 'flex', gap: 8 }}>
        {([['active', `Active${data ? ` (${activeCount})` : ''}`], ['done', `Done${data ? ` (${items.length - activeCount})` : ''}`]] as const).map(([k, label]) => (
          <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)}
            style={{ flex: 1, minHeight: 44, padding: '10px 4px', borderRadius: 999, border: 'none', fontWeight: 700, fontSize: 13, cursor: 'pointer',
              background: tab === k ? C.navy : '#fff', color: tab === k ? '#fff' : C.ink, boxShadow: tab === k ? 'none' : `inset 0 0 0 1px ${C.line}` }}>
            {label}
          </button>
        ))}
      </div>

      {data && data.stuck > 0 && tab === 'active' && (
        <p role="status" style={{ margin: 0, fontSize: 12.5, color: '#991B1B', background: '#FEF2F2', border: '1px solid #FCA5A5', borderRadius: 12, padding: '9px 12px' }}>
          {data.stuck} of these {data.stuck === 1 ? 'has' : 'have'} been waiting a while on one desk.
        </p>
      )}

      {data === null && !loadErr && <p style={{ color: C.inkSoft, fontSize: 13, margin: 0 }}>Loading…</p>}
      {data && shown.length === 0 && (
        <div style={{ textAlign: 'center', color: C.inkSoft, fontSize: 14, padding: '28px 0' }}>
          <Inbox size={28} style={{ color: '#CBD5E1' }} aria-hidden="true" />
          <p style={{ margin: '8px 0 0' }}>{tab === 'active' ? 'Nothing in progress right now.' : 'No finished requests yet.'}</p>
        </div>
      )}

      {shown.map(r => {
        const b = BUCKET_LABEL[r.bucket] ?? BUCKET_LABEL.pending
        const phone = PHONE_PATH[r.href]
        return (
          <Card key={`${r.kind}-${r.id}`} style={{ padding: 14 }}>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
              <span style={pill('#FFF7ED', '#9A3412')}>{r.kind_label}</span>
              <span style={pill(b.bg, b.fg)}>{b.label}</span>
              {r.stuck && <span style={pill('#FEE2E2', '#991B1B')}>⚠ Stuck</span>}
            </div>
            <p style={{ margin: '8px 0 0', fontWeight: 700, color: C.ink, fontSize: 14.5, lineHeight: 1.3 }}>
              {r.title}{r.amount != null && <span style={{ color: C.inkSoft, fontWeight: 600 }}> · {money(r.amount, r.currency)}</span>}
            </p>
            {r.status_line && <p style={{ margin: '4px 0 0', fontSize: 13, color: C.inkSoft, lineHeight: 1.45 }}>{r.status_line}</p>}
            {IN_FLIGHT.has(r.bucket) && r.holder_label && (
              <p style={{ margin: '4px 0 0', fontSize: 12.5, color: C.inkSoft }}>
                With {r.holder_label}{r.days_waiting != null && ` · ${r.days_waiting} day${r.days_waiting === 1 ? '' : 's'}`}
                {r.next_label && ` · next: ${r.next_label}`}
              </p>
            )}
            <Stepper steps={r.timeline} />
            <div style={{ marginTop: 10 }}>
              {phone
                ? <Link href={`${base}${phone}`} style={{ display: 'inline-flex', alignItems: 'center', minHeight: 44, padding: '0 14px', borderRadius: 999, border: `1px solid ${C.line}`, background: C.card, color: C.head, fontWeight: 700, fontSize: 13, textDecoration: 'none' }}>Open →</Link>
                : <a href={r.href} style={{ display: 'inline-flex', alignItems: 'center', minHeight: 44, padding: '0 14px', borderRadius: 999, border: `1px solid ${C.line}`, background: C.card, color: C.head, fontWeight: 700, fontSize: 13, textDecoration: 'none' }}>Open on computer →</a>}
            </div>
          </Card>
        )
      })}
    </ScreenFrame>
  )
}
