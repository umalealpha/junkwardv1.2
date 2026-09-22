'use client'

/** /m/staff — the Alpha Direct STAFF portal inside Alpha Nexus (CFO 2026-07-14).
 * Shown only to employees (work-email login resolves to a staff account via
 * the server's Nexus staff bridge). Everything here drives the SAME omni APIs
 * as the desktop — leave, approvals, refunds, spend — nothing is duplicated. */
import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { ArrowLeft, CalendarDays, CheckCircle2, Camera, CreditCard, Receipt, Banknote, Wallet, ChevronRight, ListChecks, FileText, Trophy, PenLine, Phone, Bug, LifeBuoy, ScanLine, Clock, Users, DoorOpen, Smile, Flag, Route, Zap, Search } from 'lucide-react'
import { getStaffApprovals, getPaymentBulkPreview, sfetch, ApiError, type MyApprovals } from '@/app/(customer)/api'
import { C, serif, sans, h, card, headerPad } from '@/app/(customer)/ui'
import { useStaffBase } from './useStaffBase'

// hrefs are RELATIVE to the shell base (useStaffBase): '/m/staff/x' in Nexus,
// '/app/x' in the Omni app — the same screen renders under both.
// Express Pay (CFO 2026-09-04) is shown only when /fnb/express-pay/can/ says so
// (CFO + CEO). Omni moves no money — the payment is released in the FNB app.
const EXPRESS_TILE = { href: 'express-pay', icon: Zap, title: 'Express Pay', desc: 'Pay anyone now — one payment, straight into FNB for your approval. CFO and CEO only.', badge: null }
const TILES = [
  { href: 'lookup', icon: Search, title: 'Look up a client', desc: 'Claim or policy status straight from Graphite — by claim number, policy number or name.', badge: null },
  { href: 'docs', icon: FileText, title: 'Quotes & certificates', desc: 'Describe it in a sentence; Omni fills the cover note, WCA certificate or quote and emails the PDF.', badge: null },
  { href: 'snap', icon: ScanLine, title: 'Snap anything', desc: 'Photo an invoice, receipt or quote — Omni sorts it and opens the right form.', badge: null },
  { href: 'team', icon: Users, title: 'My team', desc: "Who's in, on leave or gone dark; confirm tasks; flag your roster. Managers only.", badge: null },
  { href: 'explain-day', icon: Clock, title: 'My hours', desc: 'Explain a short day or plan a day off.', badge: null },
  { href: 'review-explanations', icon: CheckCircle2, title: 'Review explanations', desc: "Your team's short-day reasons to approve. Managers only.", badge: null },
  { href: 'my-requests', icon: Route, title: 'My requests', desc: 'Where each request is — leave, loans, petty cash, payments.', badge: null },
  { href: 'rooms', icon: DoorOpen, title: 'Rooms', desc: "Book a meeting room, see today's board.", badge: null },
  { href: 'pulse', icon: Smile, title: 'Pulse', desc: 'One-tap weekly check-in.', badge: null },
  { href: 'leave-encashment', icon: Banknote, title: 'Leave pay', desc: 'Cash out annual leave — approvers sign in Omni.', badge: null },
  { href: 'roster-flag', icon: Flag, title: 'Flag my roster', desc: "Someone on your list who shouldn't be? Tell HR. Managers only.", badge: null },
  { href: 'leave', icon: CalendarDays, title: 'Leave', desc: 'Apply for leave and track your requests. (Approvals are on the Approvals card.)', badge: 'leave' },
  { href: 'payments', icon: Banknote, title: 'Payments', desc: 'Authorise supplier, claim and operational payments — read each pack, then sign.', badge: 'payments' },
  { href: 'approvals', icon: CheckCircle2, title: 'Approvals', desc: 'Staff loans, leave, incentives and more — approve each on its own.', badge: 'money' },
  { href: 'tasks', icon: ListChecks, title: 'My tasks', desc: 'Your weekly tasks — complete, or flag blocked with a photo.', badge: 'tasks' },
  { href: 'receipt', icon: Camera, title: 'Snap a receipt', desc: 'Photograph a receipt and claim the money back.', badge: null },
  { href: 'refunds', icon: Receipt, title: 'My refunds', desc: 'Track the money you claimed back and its status.', badge: null },
  // Company card — NOT a refund. The company already paid; this is the receipt
  // and one line of what it was for (CFO 2026-08-07). Cardholders only; the
  // screen tells everyone else they have no card.
  { href: 'card-spend', icon: CreditCard, title: 'Company card', desc: 'Log what you spent on the company card — photo or Google Drive.', badge: null },
  { href: 'petty-cash', icon: Wallet, title: 'Petty cash', desc: 'Ask for petty cash before you spend it.', badge: null },
  { href: 'payslips', icon: FileText, title: 'My payslips', desc: 'Your pay, month by month. Only you can see it.', badge: null },
  { href: 'rewards', icon: Trophy, title: 'Staff Rewards', desc: 'Your points and pillars — log steps, snap a meal.', badge: null },
  { href: 'checkins', icon: PenLine, title: 'My check-ins', desc: 'Read and sign your monthly performance check-in.', badge: null },
  { href: 'reviews', icon: FileText, title: 'My Reviews', desc: 'Your Development Dialogues — confidential to you, your manager, HR and the execs.', badge: null },
  { href: 'phonebook', icon: Phone, title: 'Phonebook', desc: 'Tap to call or email a colleague.', badge: null },
  { href: 'bug', icon: Bug, title: 'Report a bug', desc: 'Something broken? Tell the board directly.', badge: null },
]

export default function StaffHome() {
  const router = useRouter()
  const base = useStaffBase()
  const [approvals, setApprovals] = useState<MyApprovals | null>(null)
  const [isAuthoriser, setIsAuthoriser] = useState(false)
  const [canExpress, setCanExpress] = useState(false)
  const [denied, setDenied] = useState(false)

  const [openTasks, setOpenTasks] = useState<number | null>(null)

  useEffect(() => {
    // Only a real 401 (this token isn't a staff account) means "not staff". A
    // timeout / 500 / offline blip must NOT eject a genuine employee (CFO 2026-07-14).
    getStaffApprovals().then(setApprovals)
      .catch(e => { if (e instanceof ApiError && e.status === 401) setDenied(true) })
    // The bulk preview is CFO-only (403 for everyone else) — use it purely as an
    // "am I the payment authoriser?" probe so the Payments tile routes the CFO's
    // payment-requests to itself, while a finance approver keeps them as a task.
    getPaymentBulkPreview().then(() => setIsAuthoriser(true)).catch(() => setIsAuthoriser(false))
    sfetch<{ allowed: boolean }>('/fnb/express-pay/can/', {}, true, true).then(r => setCanExpress(!!r.allowed)).catch(() => setCanExpress(false))
    // probe=true: this is the gate — a plain customer's 401 here must be caught
    // and sent home (below), never redirected to sign-in like a dead staff token.
    sfetch<{ id: string }[]>('/taskboard/my-tasks/', {}, true, true)
      .then(t => setOpenTasks(t.length)).catch(() => setOpenTasks(null))
  }, [])

  // A plain customer who typed the URL by hand: send them home, no drama.
  useEffect(() => { if (denied) router.replace(base === '/app' ? '/app/login' : '/m') }, [denied, router, base])

  // /my-approvals/ already counts the leave-to-approve stream for approvers, so
  // the hero + Approvals badge use approvals.total alone — no separate leave
  // fetch (that double-counted leave for the CFO/HR). The Leave tile is now
  // apply / my-requests only; leave approvals live on the Approvals card.
  // Both money streams live in /my-approvals: 'payments' (Payments to sign) and
  // 'payment_requests' (authorise). Both now belong to the Payments tile, so pull
  // them OUT of the Approvals badge (CFO 2026-08-29). payment_requests routes to
  // the Payments tile only for the CFO/authoriser — a finance first-approver keeps
  // theirs as a task, so it stays out of both approvals and payments badges here.
  const payStream = approvals?.streams.find(s => s.key === 'payments')?.count ?? 0
  const payReq = approvals?.streams.find(s => s.key === 'payment_requests')?.count ?? 0
  const paymentsBadge = payStream + (isAuthoriser ? payReq : 0)
  const approvalsBadge = approvals ? approvals.total - payStream - payReq : 0
  const badge = (key: string | null) => {
    if (key === 'payments' && paymentsBadge > 0) return paymentsBadge
    if (key === 'money' && approvalsBadge > 0) return approvalsBadge
    if (key === 'tasks' && openTasks) return openTasks
    return null
  }
  // Headline = the approvals universe, correctly split, no double-count.
  const heroTotal = approvalsBadge + paymentsBadge

  return (
    <div style={{ background: C.surface, minHeight: '100vh', fontFamily: sans }}>
      <header style={{ display: 'flex', alignItems: 'center', gap: 12, padding: headerPad, background: C.navy, color: '#fff' }}>
        <Link href={base === '/app' ? '/app' : '/m'} aria-label="Back" style={{ color: '#fff', display: 'grid', placeItems: 'center', minWidth: 44, minHeight: 44, margin: '-12px 0 -12px -12px' }}><ArrowLeft size={20} /></Link>
        <h1 style={{ fontFamily: serif, fontWeight: 800, fontSize: 20, margin: 0 }}>Staff Portal</h1>
      </header>

      <main style={{ padding: 16 }}>
        {/* Everything-waiting summary */}
        <div style={{ background: `linear-gradient(160deg, ${C.navy2}, ${C.navy})`, borderRadius: 20, padding: 20, color: '#fff', marginBottom: 16 }}>
          <p style={{ color: 'rgba(255,255,255,0.6)', fontSize: 11, letterSpacing: '0.12em', margin: 0 }}>WAITING FOR YOU</p>
          <p style={{ fontFamily: serif, fontWeight: 800, fontSize: 36, margin: '4px 0 6px' }}>
            {approvals == null ? '—' : heroTotal}
          </p>
          <p style={{ color: 'rgba(255,255,255,0.72)', fontSize: 13, margin: 0 }}>
            {heroTotal > 0
              ? 'Tap a card below to action them — it takes seconds.'
              : 'All caught up. Sharp sharp! 🎉'}
          </p>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {(canExpress ? [EXPRESS_TILE, ...TILES] : TILES).map(t => {
            const Icon = t.icon
            const n = badge(t.badge)
            return (
              <Link key={t.href} href={`${base}/${t.href}`} style={{ ...card, padding: 18, display: 'flex', alignItems: 'center', gap: 14, textDecoration: 'none' }}>
                <div style={{ width: 46, height: 46, borderRadius: 14, background: '#FFF7ED', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                  <Icon size={22} style={{ color: C.orange }} />
                </div>
                <div style={{ flex: 1 }}>
                  <h2 style={{ ...h(18), marginBottom: 2, display: 'flex', alignItems: 'center', gap: 8 }}>
                    {t.title}
                    {n != null && (
                      <span style={{ background: '#DC2626', color: '#fff', borderRadius: 999, fontSize: 11, fontWeight: 800, padding: '2px 8px', fontFamily: sans }}>{n}</span>
                    )}
                  </h2>
                  <p style={{ color: C.inkSoft, fontSize: 12.5, margin: 0, lineHeight: 1.45 }}>{t.desc}</p>
                </div>
                <ChevronRight size={18} style={{ color: '#C4C8CE' }} />
              </Link>
            )
          })}
        </div>

        {/* IT Help Desk lives on its own system — plain link, opens in the browser. */}
        <a href="/helpdesk/" target="_blank" rel="noreferrer"
          style={{ ...card, padding: 18, display: 'flex', alignItems: 'center', gap: 14, textDecoration: 'none', marginTop: 12 }}>
          <div style={{ width: 46, height: 46, borderRadius: 14, background: '#FFF7ED', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
            <LifeBuoy size={22} style={{ color: C.orange }} />
          </div>
          <div style={{ flex: 1 }}>
            <h2 style={{ ...h(18), marginBottom: 2 }}>IT Help Desk</h2>
            <p style={{ color: C.inkSoft, fontSize: 12.5, margin: 0 }}>Computer or login trouble? Log an IT ticket.</p>
          </div>
          <ChevronRight size={18} style={{ color: '#C4C8CE' }} />
        </a>

        <p style={{ textAlign: 'center', color: C.inkSoft, fontSize: 11, margin: '20px 0 4px', opacity: 0.8, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6 }}>
          <CheckCircle2 size={12} /> Same rules as Omni on your computer — approvals here are the real thing.
        </p>
      </main>
    </div>
  )
}
