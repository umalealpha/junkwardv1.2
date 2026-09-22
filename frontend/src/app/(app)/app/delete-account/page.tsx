import type { Metadata } from 'next'
import Link from 'next/link'
import { C, serif, sans, headerPad, card } from '../../ui'

/** PUBLIC data-deletion page. Google Play requires a deletion URL that opens
 * with NO sign-in for its Data safety form, and Apple rejected Alpha Nexus in
 * July 2026 under 5.1.1(v) for having no deletion route at all. Listed in
 * AppShell's PUBLIC set. Nothing here may need a token. */
export const metadata: Metadata = { title: 'Alpha Omni — Deleting your data' }

const S: React.CSSProperties = { ...card, padding: 18, display: 'flex', flexDirection: 'column', gap: 8 }
const H: React.CSSProperties = { fontFamily: serif, fontSize: 20, fontWeight: 700, color: C.head, margin: 0 }
const P: React.CSSProperties = { fontSize: 15, lineHeight: 1.55, color: C.ink, margin: 0 }
const A: React.CSSProperties = { color: C.head, fontWeight: 600, textDecoration: 'underline' }

export default function DeleteAccount() {
  return (
    <main style={{ minHeight: '100dvh', background: C.surface, fontFamily: sans, color: C.ink }}>
      <header style={{ padding: headerPad, background: C.navy, color: '#fff' }}>
        <h1 style={{ fontFamily: serif, fontWeight: 700, fontSize: 28, lineHeight: 1.15, margin: 0, color: '#fff' }}>
          Deleting your account and data
        </h1>
        <p style={{ margin: '10px 0 0', fontSize: 14, opacity: 0.85 }}>
          Alpha Omni · staff app · Alpha Direct Insurance Company (Pty) Ltd, Botswana
        </p>
      </header>

      <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 14, maxWidth: 720, margin: '0 auto' }}>
        <section style={S}>
          <h2 style={H}>Who this is for</h2>
          <p style={P}>
            Omni is the staff app of Alpha Direct Insurance Company (Pty) Ltd. Accounts are created by
            the company for its own employees — nobody can sign themselves up, so there is no public
            account to close.
          </p>
        </section>

        <section style={S}>
          <h2 style={H}>In the app</h2>
          <p style={P}>Sign in and open <strong>More → Your data</strong>. From there you can:</p>
          <ul style={{ ...P, paddingLeft: 20, display: 'flex', flexDirection: 'column', gap: 6 }}>
            <li>see exactly what Omni holds about you, and why;</li>
            <li><strong>sign out on every phone</strong> at once, immediately;</li>
            <li><strong>request deletion of your data</strong>, which goes straight to IT.</li>
          </ul>
        </section>

        <section style={S}>
          <h2 style={H}>If you cannot sign in</h2>
          <p style={P}>
            Email <a href="mailto:it@alphadirect.co.bw" style={A}>it@alphadirect.co.bw</a> from any
            address and say you want your data removed. Give your full name so they can find you.
          </p>
        </section>

        <section style={S}>
          <h2 style={H}>What gets deleted, and what cannot</h2>
          <p style={P}><strong>Removed on request:</strong> your sign-ins on every phone, your alert
            subscription, and the record of which screens you opened.</p>
          <p style={P}><strong>Deleted automatically:</strong> screen records after 180 days;
            photographs you take of an invoice or receipt are read once and never stored.</p>
          <p style={P}><strong>Kept, and we will tell you why:</strong> payroll, leave and the
            approvals you gave. These are employment records Alpha Direct is required by law to keep,
            and your approvals are part of the audit trail behind real payments — removing them would
            leave holes in the company&rsquo;s financial records. We will always tell you exactly what
            was removed and what was kept.</p>
        </section>

        <section style={S}>
          <h2 style={H}>How long it takes</h2>
          <p style={P}>
            IT will reply within 30 days, usually far sooner. You may also complain to the Information
            and Data Protection Commission of Botswana.
          </p>
        </section>

        <p style={{ fontSize: 14, color: C.inkSoft, textAlign: 'center', margin: '4px 0 32px' }}>
          <Link href="/app/privacy" style={A}>Privacy notice</Link>
          {' · '}
          <Link href="/app/support" style={A}>Support</Link>
        </p>
      </div>
    </main>
  )
}
