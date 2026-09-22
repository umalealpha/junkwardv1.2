'use client'

/** /m/delete-account — PUBLIC account-deletion page (no login gate).
 * Required by Google Play's Data-safety form: apps that allow account creation
 * must publish a web URL where users can delete their account without needing
 * the app installed. The web app IS the app, so deletion is self-service:
 * sign in here in any browser → Home → "Delete my account". This page explains
 * the flow and links straight into it. Also listed in CustomerShell.isPublic. */
import Link from 'next/link'
import { Trash2, ShieldCheck } from 'lucide-react'
import { C, serif, sans, h, card, headerPad } from '../../ui'

const STEPS = [
  'Tap "Sign in to delete" below (or open the Alpha Nexus app) and sign in with your email — a 6-digit code is sent to you.',
  'On the Home screen, scroll to the very bottom.',
  'Tap "Delete my account", then confirm with "Yes, delete everything".',
]

export default function DeleteAccount() {
  return (
    <div style={{ background: C.surface, minHeight: '100vh', fontFamily: sans }}>
      <header style={{ textAlign: 'center', padding: headerPad, background: C.card, borderBottom: `1px solid ${C.line}` }}>
        <span style={{ fontFamily: serif, fontWeight: 800, fontSize: 21, color: C.ink }}>Alpha Nexus</span>
      </header>
      <main style={{ padding: 20, maxWidth: 480, margin: '0 auto' }}>
        <div style={{ ...card, padding: 24 }}>
          <div style={{ width: 52, height: 52, borderRadius: 16, background: '#FEECEC', display: 'flex', alignItems: 'center', justifyContent: 'center', marginBottom: 14 }}>
            <Trash2 size={26} style={{ color: '#B91C1C' }} />
          </div>
          <h1 style={{ ...h(26), marginBottom: 10 }}>Delete your Alpha Nexus account</h1>
          <p style={{ color: C.inkSoft, fontSize: 14, lineHeight: 1.6, margin: '0 0 16px' }}>
            You can permanently delete your account yourself, from the app or from this website —
            no email or phone call needed. Deletion is immediate and removes <b style={{ color: C.ink }}>all
            your data</b>: profile, points, transactions, drive history, wellness results, activity logs
            and login sessions. It cannot be undone.
          </p>
          {/* listStyle set explicitly — the global CSS reset removes list markers,
              which left these steps unnumbered (Google reviewers read this page). */}
          <ol style={{ margin: '0 0 18px', paddingLeft: 22, listStyle: 'decimal' }}>
            {STEPS.map((s, i) => (
              <li key={i} style={{ fontSize: 14, lineHeight: 1.55, color: C.ink, marginBottom: 10, display: 'list-item' }}>{s}</li>
            ))}
          </ol>
          <Link href="/m/login" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, padding: '15px', borderRadius: 999, background: `linear-gradient(135deg, ${C.orange}, ${C.orangeDeep})`, color: '#fff', fontWeight: 700, fontSize: 15, textDecoration: 'none' }}>
            Sign in to delete your account
          </Link>
          <p style={{ display: 'flex', gap: 8, alignItems: 'flex-start', color: C.inkSoft, fontSize: 12, lineHeight: 1.5, margin: '16px 0 0' }}>
            <ShieldCheck size={16} style={{ color: C.teal, flexShrink: 0, marginTop: 1 }} />
            <span>No account data is retained after deletion. If you only want to stop using the app,
            you can simply sign out — but deletion is always available to you here. See the{' '}
            <Link href="/m/privacy" style={{ color: C.inkSoft, textDecoration: 'underline' }}>Privacy Policy</Link>.</span>
          </p>
        </div>
      </main>
    </div>
  )
}
