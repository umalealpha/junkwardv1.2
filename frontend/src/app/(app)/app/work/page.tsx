'use client'
// Omni Mobile — Work (Workstream C).
// The permission-driven workspace hub: each person sees ONLY the department
// areas their server capabilities grant (from the WS-A manifest), plus the
// common actions everyone has. Every tile links to a real existing screen —
// no dead links. Full per-department queues land as they are built.
import Link from 'next/link'
import type { ComponentType } from 'react'
import {
  FileText, Users, MessageSquareText, Search, Receipt, HeartPulse,
  ClipboardList, Inbox, CalendarDays, ChevronRight, ChevronLeft, Briefcase, Landmark,
} from 'lucide-react'
import { BALANCES_TILE } from '../../TileList'
import { C, card, serif, headerPad } from '../../ui'
import { useMobileCapabilities, type MobileCapability } from '../../capabilities'

type Icon = ComponentType<{ size?: number; color?: string }>
interface Area { href: string; icon: Icon; title: string; desc: string; cap?: MobileCapability }

// Department areas — shown only when the viewer holds the capability. Each points
// at a screen that exists today; richer per-department queues arrive with C.
const MY_WORK: Area[] = [
  { cap: 'view_claims_workspace',        href: '/app/lookup',           icon: Search,          title: 'Claims',                desc: 'Look up claims and clients' },
  { cap: 'view_underwriting_workspace',  href: '/app/docs',             icon: FileText,        title: 'Quotes & certificates', desc: 'Create and issue quotes and cover notes' },
  // Finance intentionally omitted here: view_finance_workspace is a broad reporting
  // flag, but /app/payments is the payment-authoriser screen — gating one on the
  // other advertises a tile that 403s (Fable WS-C review). A real finance workspace
  // gets its own capability + screen later.
  // Money in the bank — the CFO's morning cash read. view_finance_workspace is
  // the right gate here (unlike /app/payments): this screen only READS, and the
  // endpoint's own CanViewFinancials refuses anyone the manifest let through.
  { cap: 'view_finance_workspace',       href: BALANCES_TILE.href,      icon: Landmark,        title: BALANCES_TILE.title,     desc: BALANCES_TILE.desc },
  { cap: 'manage_team',                  href: '/app/team',             icon: Users,           title: 'My team',               desc: 'Attendance, approvals and roster' },
  { cap: 'give_monthly_feedback',        href: '/app/monthly-feedback', icon: MessageSquareText, title: 'Monthly feedback',    desc: 'Give your team their monthly feedback' },
]

// Everyone gets these (no capability gate).
const COMMON: Area[] = [
  { href: '/app/lookup',         icon: Search,        title: 'Look up a client', desc: 'Find a client or policy' },
  { href: '/app/health-quote',   icon: HeartPulse,    title: 'Health cover quote', desc: 'Price and email a health quote' },
  { href: '/app/raise-payment',  icon: Receipt,       title: 'Raise a payment',  desc: 'Start a payment request' },
  { href: '/app/raise-po',       icon: ClipboardList, title: 'Raise a PO',       desc: 'Create a purchase order' },
  { href: '/app/purchase-orders', icon: FileText,     title: 'Purchase orders',  desc: 'View, approve or cancel a PO' },
  { href: '/app/my-requests',    icon: Inbox,         title: 'My requests',      desc: 'Track what you have sent' },
  { href: '/app/leave',          icon: CalendarDays,  title: 'Leave',            desc: 'Apply for and track leave' },
]

export default function Work() {
  const { can, loading, error } = useMobileCapabilities()
  const mine = MY_WORK.filter(a => a.cap && can(a.cap))

  return (
    <main>
      <header style={{ background: C.navy, color: '#fff', padding: headerPad, paddingBottom: 44, display: 'flex', alignItems: 'center', gap: 10 }}>
        <Link href="/app" aria-label="Home" className="oa-press" style={{ color: '#fff', display: 'grid', placeItems: 'center', width: 36, height: 36, marginLeft: -8 }}><ChevronLeft size={24} /></Link>
        <div>
          <h1 style={{ fontFamily: serif, fontSize: 24, fontWeight: 700, lineHeight: 1, margin: 0 }}>Work</h1>
          <div style={{ opacity: 0.8, fontSize: 13, marginTop: 4 }}>Everything you can do, in one place</div>
        </div>
      </header>

      <section style={{ padding: '0 16px', marginTop: -28, display: 'grid', gap: 16 }}>
        {loading && mine.length === 0 && (
          <div className="oa-rise" style={{ ...card, padding: 18, color: C.inkSoft, fontSize: 14, textAlign: 'center' }}>Loading your work…</div>
        )}

        {mine.length > 0 && (
          <div>
            <SectionTitle icon={Briefcase}>Your work</SectionTitle>
            <div className="oa-rise" style={{ ...card, padding: 0, overflow: 'hidden' }}>
              {mine.map((a, i) => <AreaRow key={a.title} area={a} last={i === mine.length - 1} />)}
            </div>
          </div>
        )}

        <div>
          <SectionTitle>Everyone</SectionTitle>
          <div className="oa-rise oa-rise-2" style={{ ...card, padding: 0, overflow: 'hidden' }}>
            {COMMON.map((a, i) => <AreaRow key={a.title} area={a} last={i === COMMON.length - 1} />)}
          </div>
        </div>

        {error && mine.length === 0 ? (
          <p style={{ color: C.red, fontSize: 13, textAlign: 'center', margin: '2px 0 4px' }}>
            Couldn&apos;t check your access just now.{' '}
            <button onClick={() => location.reload()} style={{ color: C.head, fontWeight: 700, background: 'none', border: 0, textDecoration: 'underline', cursor: 'pointer', font: 'inherit' }}>Try again</button>
          </p>
        ) : (
          <p style={{ color: C.inkSoft, fontSize: 12.5, textAlign: 'center', margin: '2px 0 4px' }}>
            You only see the areas your access allows. Missing something? Ask your manager.
          </p>
        )}
      </section>
    </main>
  )
}

function SectionTitle({ icon: Icon, children }: { icon?: Icon; children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '0 4px 8px' }}>
      {Icon ? <Icon size={15} color={C.inkSoft} /> : null}
      <span style={{ fontSize: 12, letterSpacing: '0.08em', textTransform: 'uppercase', color: C.inkSoft, fontWeight: 700 }}>{children}</span>
    </div>
  )
}

function AreaRow({ area, last }: { area: Area; last: boolean }) {
  const { icon: Icon, href, title, desc } = area
  return (
    <Link href={href} className="oa-press" style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '14px 16px', textDecoration: 'none', color: C.ink, borderBottom: last ? undefined : `1px solid ${C.line}` }}>
      <span style={{ display: 'grid', placeItems: 'center', width: 44, height: 44, borderRadius: 14, background: 'var(--ao-navy-wash, rgba(11,11,59,0.06))', flexShrink: 0 }}>
        <Icon size={21} color={C.head} />
      </span>
      <span style={{ flex: 1, minWidth: 0 }}>
        <span style={{ display: 'block', fontWeight: 700, fontSize: 16 }}>{title}</span>
        <span style={{ display: 'block', fontSize: 13, color: C.inkSoft }}>{desc}</span>
      </span>
      <ChevronRight size={18} color={C.inkSoft} />
    </Link>
  )
}
