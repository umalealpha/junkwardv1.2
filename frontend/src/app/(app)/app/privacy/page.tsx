import type { Metadata } from 'next'
import Link from 'next/link'
import { C, h, serif, sans, headerPad, card } from '../../ui'

/** PUBLIC staff privacy notice — Google Play and the App Store both require a
 * privacy policy URL that opens with NO sign-in, so '/app/privacy' is listed in
 * AppShell's PUBLIC set. Wording is the approved v1.0 notice, verbatim; do not
 * reword it here. The customer notice is a different page (/m/privacy). */
export const metadata: Metadata = { title: 'Alpha Omni — Privacy Notice' }

const ROWS: [string, string, string][] = [
  ['Your name and work email address', 'To sign you in and show your work',
   'While you are employed, then as our records policy requires'],
  ['Your sign-in session for this phone', 'To keep you signed in for 30 days',
   '30 days, or until you or IT sign the device out'],
  ['Your approvals, tasks, requests and the actions you take', 'To run the business and keep an audit trail',
   'As our records policy requires'],
  ['Your payroll, leave and expense records shown to you', 'So you can see your own payslips, balances and claims',
   'As employment law requires'],
  ['Which screens you open in the app', 'To see which parts of the app are used, so we improve the right ones',
   '180 days, then deleted automatically'],
  ['Photographs you take of invoices, receipts or quotes', 'To read the document and fill in the form',
   'The photograph is read once and is not stored. Any file you deliberately attach to a request is kept with that request'],
  ['Your alert (push) subscription for this device', 'To send you the morning summary and approval nudges',
   'Until you turn alerts off or the device is signed out'],
]
const COLS = ['What', 'Why', 'How long']

export default function AppPrivacy() {
  return (
    <main style={{ minHeight: '100dvh', background: C.surface, fontFamily: sans, color: C.ink }}>
      <header style={{ padding: headerPad, background: C.navy, color: '#fff' }}>
        <h1 style={{ fontFamily: serif, fontWeight: 700, fontSize: 26, lineHeight: 1.15, margin: 0, color: '#fff' }}>
          Alpha Omni — Privacy Notice
        </h1>
        <p style={{ margin: '10px 0 0', fontSize: 14, fontWeight: 600, opacity: 0.92 }}>
          Alpha Direct Insurance Company (Pty) Ltd
        </p>
        <p style={{ margin: '2px 0 0', fontSize: 13, opacity: 0.78 }}>
          Version 1.0 · September 2026 · Gaborone, Botswana
        </p>
      </header>

      <div style={{ padding: '16px 16px 40px', display: 'flex', flexDirection: 'column', gap: 14 }}>
        <section style={{ ...card, padding: 18 }}>
          <h2 style={h(18)}>Who this is for</h2>
          <p style={P}>
            This notice explains what the Omni staff app does with information about <strong>Alpha Direct
            employees</strong>. If you are a customer, the notice that applies to you is at{' '}
            <a href="https://omni.alphadirect.co.bw/m/privacy" style={A}>omni.alphadirect.co.bw/m/privacy</a>.
          </p>
        </section>

        <section style={{ ...card, padding: 18 }}>
          <h2 style={h(18)}>Who is responsible</h2>
          <p style={P}>
            Alpha Direct Insurance Company (Pty) Ltd is the data controller. Contact:{' '}
            <a href="mailto:it@alphadirect.co.bw" style={A}>it@alphadirect.co.bw</a>.
          </p>
        </section>

        <section style={{ ...card, padding: 18 }}>
          <h2 style={h(18)}>What the app holds about you</h2>
          {/* Explicit ARIA roles so the stacked narrow-screen layout (display:block
              on rows/cells) keeps real table semantics for screen readers. */}
          <table role="table" className="pv-table">
            <thead role="rowgroup">
              <tr role="row">{COLS.map(c => <th key={c} role="columnheader" scope="col">{c}</th>)}</tr>
            </thead>
            <tbody role="rowgroup">
              {ROWS.map(r => (
                <tr role="row" key={r[0]}>
                  {r.map((cell, i) => (
                    <td role="cell" key={COLS[i]} data-label={COLS[i]}>{cell}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        <section style={{ ...card, padding: 18 }}>
          <h2 style={h(18)}>What the app does <strong>not</strong> do</h2>
          <ul style={UL}>
            <li style={LI}>It does not track your location. The app never asks for it.</li>
            <li style={LI}>It does not contain advertising, and no advertising or analytics company receives anything.</li>
            <li style={LI}>It does not sell or rent information about you to anyone.</li>
            <li style={LI}>It does not accept identity documents. Photographs of an ID, passport or bank card are refused.</li>
          </ul>
        </section>

        <section style={{ ...card, padding: 18 }}>
          <h2 style={h(18)}>Monitoring, stated plainly</h2>
          <p style={P}>
            Omni is a work system. Your sign-ins, the actions you take and the screens you open are
            recorded, for audit, security and support — the same as when you use Omni on an office
            computer. Screen records are deleted automatically after 180 days.
          </p>
        </section>

        <section style={{ ...card, padding: 18 }}>
          <h2 style={h(18)}>Automated document reading</h2>
          <p style={P}>
            When you photograph a document, or describe what you need in your own words, Omni uses an
            automated reading service to fill the form in. It suggests; it does not decide. You check
            every field before anything is issued. Personal identifiers are stripped before anything is
            sent for reading, and no customer identity information is sent to any outside service.
          </p>
        </section>

        <section style={{ ...card, padding: 18 }}>
          <h2 style={h(18)}>Who else sees it</h2>
          <p style={P}>
            Your information stays inside Alpha Direct and is seen by the people who need it — your
            manager, HR, Finance and IT, according to their role. We use technology suppliers to host and
            run the system under contract; they act only on our instructions and may not use the
            information for anything else.
          </p>
        </section>

        <section style={{ ...card, padding: 18 }}>
          <h2 style={h(18)}>Where it is kept</h2>
          <p style={P}>On servers operated for Alpha Direct, protected in transit by encryption.</p>
        </section>

        <section style={{ ...card, padding: 18 }}>
          <h2 style={h(18)}>Your rights under the Botswana Data Protection Act</h2>
          <p style={P}>
            You may ask what we hold about you, ask us to correct anything wrong, object to a particular
            use, or complain to the Information and Data Protection Commission.
          </p>
          <p style={P}>
            Some of this information is an employment record we are required to keep, so it cannot simply
            be deleted on request — but you can always ask, and we will explain what we can and cannot
            remove.
          </p>
          <p style={P}>
            To ask about any of this, or to have this phone signed out:{' '}
            <a href="mailto:it@alphadirect.co.bw" style={A}>it@alphadirect.co.bw</a>.
          </p>
        </section>

        <section style={{ ...card, padding: 18 }}>
          <h2 style={h(18)}>Changes</h2>
          <p style={P}>
            If this notice changes we will publish the new version on this page and tell staff in the app.
          </p>
        </section>

        <Link href="/app/support" style={LINK_BTN}>Support</Link>
      </div>
    </main>
  )
}

const P: React.CSSProperties = { color: C.inkSoft, fontSize: 15, lineHeight: 1.6, margin: '10px 0 0' }
const UL: React.CSSProperties = { margin: '10px 0 0', paddingLeft: 20 }
const LI: React.CSSProperties = { color: C.inkSoft, fontSize: 15, lineHeight: 1.6, marginBottom: 8 }
const A: React.CSSProperties = { color: C.head, fontWeight: 600, textDecoration: 'underline' }
const LINK_BTN: React.CSSProperties = {
  display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: 48, borderRadius: 12,
  background: C.navy, color: '#fff', fontWeight: 700, fontSize: 16, textDecoration: 'none',
}
