import type { Metadata } from 'next'
import Link from 'next/link'
import { C, h, serif, sans, headerPad, card } from '../../ui'

/** PUBLIC support page — Apple requires a support URL that opens with NO
 * sign-in, so '/app/support' is listed in AppShell's PUBLIC set. Keep it short
 * and free of anything that needs a token. */
export const metadata: Metadata = { title: 'Alpha Omni — Support' }

export default function AppSupport() {
  return (
    <main style={{ minHeight: '100dvh', background: C.surface, fontFamily: sans, color: C.ink }}>
      <header style={{ padding: headerPad, background: C.navy, color: '#fff' }}>
        <h1 style={{ fontFamily: serif, fontWeight: 700, fontSize: 28, lineHeight: 1.15, margin: 0, color: '#fff' }}>
          Alpha Omni — Support
        </h1>
        <p style={{ margin: '10px 0 0', fontSize: 14, opacity: 0.85 }}>
          Alpha Direct Insurance Company (Pty) Ltd · Gaborone, Botswana
        </p>
      </header>

      <div style={{ padding: '16px 16px 40px', display: 'flex', flexDirection: 'column', gap: 14 }}>
        <section style={{ ...card, padding: 18 }}>
          <h2 style={h(18)}>What the app is</h2>
          <p style={P}>
            Omni is the staff app of Alpha Direct Insurance Company (Pty) Ltd, Botswana, for its
            employees.
          </p>
        </section>

        <section style={{ ...card, padding: 18 }}>
          <h2 style={h(18)}>Who it is for</h2>
          <p style={P}>
            Alpha Direct employees with a work email address and an Omni password. Members of the
            public cannot use it.
          </p>
        </section>

        <section style={{ ...card, padding: 18 }}>
          <h2 style={h(18)}>How to get help</h2>
          <p style={P}>
            Email <a href="mailto:it@alphadirect.co.bw" style={A}>it@alphadirect.co.bw</a>.
          </p>
          <a href="mailto:it@alphadirect.co.bw" style={BTN}>Email IT support</a>
        </section>

        <section style={{ ...card, padding: 18 }}>
          <h2 style={h(18)}>Lost or stolen phone</h2>
          <p style={P}>
            Tell IT the same day, so the device can be signed out.
          </p>
        </section>

        <section style={{ ...card, padding: 18 }}>
          <h2 style={h(18)}>Privacy</h2>
          <p style={P}>What the app holds about you, and for how long, is set out in the privacy notice.</p>
          <Link href="/app/privacy" style={GHOST}>Read the privacy notice</Link>
        </section>
      </div>
    </main>
  )
}

const P: React.CSSProperties = { color: C.inkSoft, fontSize: 15, lineHeight: 1.6, margin: '10px 0 0' }
const A: React.CSSProperties = { color: C.head, fontWeight: 600, textDecoration: 'underline' }
const BTN: React.CSSProperties = {
  display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: 48, marginTop: 14,
  borderRadius: 12, background: C.orange, color: C.navy, fontWeight: 700, fontSize: 16, textDecoration: 'none',
}
const GHOST: React.CSSProperties = {
  display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: 48, marginTop: 14,
  borderRadius: 12, border: `1px solid ${C.line}`, background: C.card, color: C.head,
  fontWeight: 700, fontSize: 16, textDecoration: 'none',
}
