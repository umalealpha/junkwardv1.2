'use client'
/** The tab index pages (Approve · Do · Me · More) are each a titled list of
 * tiles linking into the staff screens. Lives here, not in a page.tsx — Next
 * refuses non-page exports from a route file. */
import Link from 'next/link'
import { ChevronRight } from 'lucide-react'
import { C, h, headerPad, card } from './ui'
import type { MobileCapability } from './capabilities'

/** Money in the bank (CFO 2026-09-20). Declared here with the other tile data
 *  so there is ONE definition of the wording and the gate; the Work hub renders
 *  it, gated on `cap` exactly like its other areas. Anything else that wants
 *  the tile imports this rather than retyping it. */
export const BALANCES_TILE = {
  href: '/app/balances',
  title: 'Money in the bank',
  desc: 'This morning’s balances and what is going out',
  cap: 'view_finance_workspace' as MobileCapability,
}

export function List({ title, rows }: { title: string; rows: string[][] }) {
  return (
    <main>
      <header style={{ padding: headerPad }}><h1 style={h(26)}>{title}</h1></header>
      <section style={{ padding: '0 16px', display: 'grid', gap: 10 }}>
        {rows.map(([href, t, d], i) => (
          <Link key={href} href={href} className={`oa-press oa-rise${i < 2 ? '' : i < 4 ? ' oa-rise-2' : ' oa-rise-3'}`}
            style={{ ...card, padding: '14px 16px', textDecoration: 'none', color: C.ink, display: 'flex', alignItems: 'center', gap: 12 }}>
            <span style={{ flex: 1 }}>
              <span style={{ display: 'block', fontWeight: 600, fontSize: 16 }}>{t}</span>
              <span style={{ display: 'block', fontSize: 13, color: C.inkSoft, marginTop: 2 }}>{d}</span>
            </span>
            <ChevronRight size={18} color={C.inkSoft} />
          </Link>
        ))}
      </section>
    </main>
  )
}
