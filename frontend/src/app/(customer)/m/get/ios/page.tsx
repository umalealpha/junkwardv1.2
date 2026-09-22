'use client'

/** /m/get/ios — public iPhone install page.
 *
 * Since 2026-07-14 Alpha Nexus is a REAL App Store app (id 6786171177) — the
 * page now leads with the store button. The old Add-to-Home-Screen walkthrough
 * predated approval and sent people hunting the App Store by name, where
 * lookalike "Nexus" step-counter apps live (a tester installed one and thought
 * it was ours — CFO 2026-08-11). Kept as a small fallback for old iPhones.
 */
import Link from 'next/link'
import { Apple, Share } from 'lucide-react'
import { C, serif, sans, h, card, headerPad } from '../../../ui'

const APP_STORE_URL = 'https://apps.apple.com/bw/app/alpha-nexus/id6786171177'

const FALLBACK_STEPS = [
  'Open nexus.alphadirect.co.bw/m in Safari (not Chrome).',
  'Tap the Share button (the square with the up arrow), then "Add to Home Screen", then Add.',
]

export default function GetIos() {
  return (
    <div style={{ background: C.surface, minHeight: '100vh', fontFamily: sans }}>
      <header style={{ textAlign: 'center', padding: headerPad, background: C.card, borderBottom: `1px solid ${C.line}` }}>
        <span style={{ fontFamily: serif, fontWeight: 800, fontSize: 21, color: C.ink }}>Alpha Nexus</span>
      </header>
      <main style={{ padding: 20, maxWidth: 480, margin: '0 auto' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, margin: '6px 0 14px' }}>
          <Apple size={26} style={{ color: C.ink }} />
          <h1 style={h(26)}>Install on iPhone</h1>
        </div>

        <div style={{ ...card, padding: 22, textAlign: 'center', marginBottom: 16 }}>
          <p style={{ margin: '0 0 14px', fontSize: 15, lineHeight: 1.55, color: C.ink }}>
            Alpha Nexus is on the <b>App Store</b>. Use this button — don&apos;t search by name,
            other apps called &ldquo;Nexus&rdquo; are not us.
          </p>
          <a href={APP_STORE_URL}
            style={{ display: 'inline-flex', alignItems: 'center', gap: 10, background: C.ink, color: '#fff', textDecoration: 'none', fontWeight: 700, fontSize: 16, padding: '14px 26px', borderRadius: 999 }}>
            <Apple size={20} /> Get it on the App Store
          </a>
          <p style={{ margin: '12px 0 0', fontSize: 12, color: C.inkSoft }}>
            Free · by Alpha Direct (publisher: Arjun Parameswaran) · look for the orange ring on navy
          </p>
        </div>

        <div style={{ ...card, padding: 16, marginBottom: 10, display: 'flex', gap: 12, alignItems: 'flex-start', background: '#EAF6F4', border: `1px solid ${C.tealLight}` }}>
          <Share size={20} style={{ color: C.teal, flexShrink: 0 }} />
          <p style={{ margin: 0, fontSize: 13, lineHeight: 1.5, color: C.ink }}>
            <b>Old iPhone (before iOS 15)?</b> You can still add it from Safari instead:
            {' '}{FALLBACK_STEPS[0]} {FALLBACK_STEPS[1]}
          </p>
        </div>

        <p style={{ textAlign: 'center', marginTop: 18, fontSize: 14 }}>
          On Android instead? <Link href="/m/get/android" style={{ color: C.teal, fontWeight: 600 }}>Android install →</Link>
        </p>
        <p style={{ textAlign: 'center', marginTop: 8, fontSize: 13 }}>
          <Link href="/m/login" style={{ color: C.inkSoft }}>Already installed? Sign in</Link>
        </p>
      </main>
    </div>
  )
}
