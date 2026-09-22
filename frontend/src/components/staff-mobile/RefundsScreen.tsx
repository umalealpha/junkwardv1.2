'use client'

/** /m/staff/refunds — a staff member's OWN refunds and their live status.
 * CFO 2026-08-03: staff could SUBMIT a refund on the phone ("Snap a receipt")
 * but had no screen to see it afterwards, so it looked like it vanished. This
 * reads the SAME /expense-claims/mine/ the desktop Refunds page uses — no new
 * data, just the missing window on mobile. Read-only (submit stays on Snap a
 * receipt). */
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { ArrowLeft, Camera, Receipt } from 'lucide-react'
import { sfetch, reauthOn401 } from '@/app/(customer)/api'
import { C, serif, sans, card, headerPad } from '@/app/(customer)/ui'
import { useStaffBase } from './useStaffBase'

interface Claim {
  id: string
  amount: string
  currency: string
  category: string
  status: string
  status_label: string
  reject_reason: string | null
  submitted_at: string | null
  created_at: string | null
}

// status key -> {chip background, chip text colour, plain meaning for the requester}
const LOOK: Record<string, { bg: string; fg: string; note: string }> = {
  submitted:   { bg: '#FEF3C7', fg: '#92400E', note: 'Waiting for the accountant to process it.' },
  pending_cfo: { bg: '#DBEAFE', fg: '#1E40AF', note: 'Processed — waiting for the CFO to approve.' },
  approved:    { bg: '#D1FAE5', fg: '#065F46', note: 'Approved — the money is being paid to you.' },
  paid:        { bg: '#D1FAE5', fg: '#065F46', note: 'Paid.' },
  rejected:    { bg: '#FEE2E2', fg: '#991B1B', note: 'Sent back to you — see the reason and re-submit.' },
  draft:       { bg: '#E5E7EB', fg: '#374151', note: 'Not submitted yet.' },
}

function when(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  return isNaN(d.getTime()) ? '' : d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
}

export default function StaffRefunds() {
  const base = useStaffBase()
  const [claims, setClaims] = useState<Claim[] | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    sfetch<Claim[]>('/expense-claims/mine/')
      .then(setClaims)
      .catch(e => {
        if (reauthOn401(e)) return   // session expired — bounce to sign-in, don't show an empty list
        setErr(e instanceof Error ? e.message : 'Could not load your refunds.'); setClaims([])
      })
  }, [])

  return (
    <div style={{ background: C.surface, minHeight: '100vh', fontFamily: sans }}>
      <header style={{ display: 'flex', alignItems: 'center', gap: 12, padding: headerPad, background: C.navy, color: '#fff' }}>
        <Link href={base} aria-label="Back" style={{ color: '#fff', display: 'grid', placeItems: 'center', minWidth: 44, minHeight: 44, margin: '-12px 0 -12px -12px' }}><ArrowLeft size={20} /></Link>
        <h1 style={{ fontFamily: serif, fontWeight: 800, fontSize: 20, margin: 0 }}>My refunds</h1>
      </header>

      <main style={{ padding: 16 }}>
        {/* new claim */}
        <Link href={`${base}/receipt`}
          style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, width: '100%', boxSizing: 'border-box', padding: 13, borderRadius: 999, textDecoration: 'none', marginBottom: 16, background: `linear-gradient(135deg, ${C.orange}, ${C.orangeDeep})`, color: C.head, fontWeight: 800, fontSize: 15 }}>
          <Camera size={16} /> Claim a new refund
        </Link>

        {claims === null && (
          <p style={{ color: C.inkSoft, fontSize: 14, textAlign: 'center', padding: 24 }}>Loading…</p>
        )}

        {claims !== null && claims.length === 0 && (
          <div style={{ ...card, padding: 28, textAlign: 'center' }}>
            <Receipt size={34} color={C.inkSoft} />
            <p style={{ color: C.ink, fontWeight: 700, fontSize: 15, margin: '12px 0 4px' }}>No refunds yet</p>
            <p style={{ color: C.inkSoft, fontSize: 13, lineHeight: 1.6, margin: 0 }}>
              {err || 'When you claim money back, it shows here with its status.'}
            </p>
          </div>
        )}

        {claims !== null && claims.length > 0 && claims.map(c => {
          const look = LOOK[c.status] || LOOK.submitted
          return (
            <div key={c.id} style={{ ...card, padding: 16, marginBottom: 12 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 10 }}>
                <div>
                  <p style={{ fontFamily: serif, fontWeight: 800, fontSize: 18, color: C.ink, margin: 0 }}>
                    {c.currency} {c.amount}
                  </p>
                  <p style={{ color: C.inkSoft, fontSize: 13, margin: '2px 0 0' }}>{c.category || 'Refund'}</p>
                </div>
                <span style={{ background: look.bg, color: look.fg, fontSize: 12, fontWeight: 700, padding: '4px 10px', borderRadius: 999, whiteSpace: 'nowrap' }}>
                  {c.status_label}
                </span>
              </div>
              <p style={{ color: C.inkSoft, fontSize: 13, lineHeight: 1.5, margin: '10px 0 0' }}>{look.note}</p>
              {c.status === 'rejected' && c.reject_reason && (
                <p style={{ background: '#FEF2F2', color: '#991B1B', fontSize: 13, lineHeight: 1.5, padding: '8px 10px', borderRadius: 8, margin: '10px 0 0' }}>
                  <b>Reason:</b> {c.reject_reason}
                </p>
              )}
              {when(c.submitted_at || c.created_at) && (
                <p style={{ color: C.inkSoft, fontSize: 11, margin: '10px 0 0' }}>
                  Submitted {when(c.submitted_at || c.created_at)}
                </p>
              )}
            </div>
          )
        })}
      </main>
    </div>
  )
}
