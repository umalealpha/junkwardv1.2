'use client'

/**
 * Omni staff app — Money in the bank.
 *
 * The SAME <BankBalancesPanel/> the desktop renders, in one full-width column
 * with the headline pinned: the CEO reads only the pinned line, the accountant
 * scrolls the cards. One component serves the PWA, the Android TWA and the
 * iPhone build — it is never forked per platform.
 *
 * Colours come from the app's own tokens (appThemes.ts), NOT the desktop theme:
 * /app is brand-only and deliberately takes no theme-* repaint. That is why the
 * panel takes its palette as a prop.
 *
 * Read-only. The server's CanViewFinancials decides who sees anything; a 403
 * says so in plain words rather than showing an empty screen.
 */
import Link from 'next/link'
import { useEffect, useState } from 'react'
import { ChevronLeft, RefreshCw } from 'lucide-react'
import { AppApiError, getBankBalances } from '../../api'
import { C, card, headerPad, serif } from '../../ui'
import { BankBalancesPanel } from '@/components/finance/BankBalancesPanel'
import type { BalancesPalette } from '@/components/finance/balancesPalette'
import type { BankBalances } from '@/lib/bankBalances'
import { mockScenarios } from '@/lib/bankBalancesMock'

/** The app palette. `navy` is dark in all three app themes, so `onNavy` white
 *  holds everywhere; the accent is used as a ring, never as text on it. Note
 *  backgrounds are transparent here — at 375px the coloured text and the status
 *  rail mark the card clearly enough without another box inside it. */
const APP_PALETTE: BalancesPalette = {
  surface: C.surface,
  card: C.card,
  cardBorder: C.line,
  cardShadow: 'var(--ao-card-shadow, 0 6px 24px rgba(11,11,59,0.06))',
  line: C.line,
  ink: C.ink,
  inkSoft: C.inkSoft,
  heading: C.head,
  navy: C.navy,
  onNavy: '#FFFFFF',
  accent: C.orange,
  ok: C.green,
  warn: C.amber,
  warnBg: 'transparent',
  danger: C.red,
  dangerBg: 'transparent',
  headingFont: serif,
}

/** Dev-only preview of the failure states (`?mock=troubled`). Dropped from the
 *  production bundle — NODE_ENV is inlined at build time. */
function devMock(): BankBalances | null {
  if (process.env.NODE_ENV !== 'development') return null
  if (typeof window === 'undefined') return null
  const key = new URLSearchParams(window.location.search).get('mock')
  return key ? (mockScenarios[key as keyof typeof mockScenarios] ?? null) : null
}

export default function Balances() {
  const [data, setData] = useState<BankBalances | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [forbidden, setForbidden] = useState(false)
  const [nonce, setNonce] = useState(0)

  useEffect(() => {
    const mock = devMock()
    if (mock) { setData(mock); setLoading(false); return }
    let alive = true
    setLoading(true)
    getBankBalances()
      .then((d) => { if (alive) { setData(d); setError(null); setForbidden(false) } })
      .catch((err: unknown) => {
        if (!alive) return
        if (err instanceof AppApiError && err.status === 403) { setForbidden(true); setError(null) }
        // A dropped connection is not a reason to show a number: the balances
        // are deliberately NOT cached on the phone (see the feature registry's
        // online_only policy) — yesterday's cash read as today's is worse than
        // no answer at all.
        else setError(err instanceof AppApiError && err.status === 0
          ? err.message
          : 'The bank balances could not be read just now.')
      })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [nonce])

  return (
    <main style={{ paddingBottom: 24 }}>
      <header style={{
        background: C.navy, color: '#fff', padding: headerPad, paddingBottom: 12,
        display: 'flex', alignItems: 'center', gap: 8,
      }}>
        <Link href="/app" aria-label="Home" className="oa-press"
          style={{ color: '#fff', display: 'grid', placeItems: 'center', width: 36, height: 36, marginLeft: -8 }}>
          <ChevronLeft size={24} />
        </Link>
        <h1 style={{ flex: 1, fontFamily: serif, fontSize: 20, fontWeight: 700, margin: 0, lineHeight: 1 }}>
          Money in the bank
        </h1>
        <button onClick={() => setNonce((n) => n + 1)} aria-label="Read again" className="oa-press"
          disabled={loading}
          style={{
            border: 0, background: 'transparent', color: '#fff',
            display: 'grid', placeItems: 'center', width: 40, height: 40,
            opacity: loading ? 0.5 : 1,
          }}>
          <RefreshCw size={19} className={loading ? 'oa-spin' : undefined} />
        </button>
      </header>

      {forbidden ? (
        <section style={{ padding: 16 }}>
          <div style={{ ...card, padding: 18 }}>
            <p style={{ margin: 0, fontWeight: 700, color: C.head, fontSize: 15 }}>
              This is for finance and management
            </p>
            <p style={{ margin: '6px 0 0', color: C.inkSoft, fontSize: 13.5, lineHeight: 1.5 }}>
              Bank balances are restricted. Ask the CFO if you need them.
            </p>
          </div>
        </section>
      ) : (
        <div style={{ padding: '0 16px 16px' }}>
          <BankBalancesPanel
            data={data}
            loading={loading}
            error={error}
            onRetry={() => setNonce((n) => n + 1)}
            palette={APP_PALETTE}
            layout="single"
            stickyHeadline
          />
        </div>
      )}
    </main>
  )
}
