'use client'

/**
 * /m/get/omni — public install page for the OMNI STAFF app (Omni.apk, the TWA).
 *
 * Why this exists: Omni.apk is a private, self-hosted build, so Google Play
 * Protect shows "App blocked to protect your device — Play Protect hasn't seen
 * an app from this developer before", and Android then reports "App not
 * installed". Staff read that as a broken download and raise a bug (Meduduetso
 * Tlagae, 2026-09-08). The APK is fine — it is signed and verified from the edge
 * (MACHINE-TALK 2026-09-04). What was missing was a page telling people which
 * two buttons to press. Same shape as /m/get/android (Nexus), so the steps read
 * the same for anyone who has installed either app.
 */
import Link from 'next/link'
import { Download, Smartphone, ShieldAlert } from 'lucide-react'
import { C, serif, sans, h, card, headerPad } from '../../../ui'

const STEPS = [
  'Tap the orange button above to download Omni.apk (about 1.2 MB — it is small because the app runs the live Omni site).',
  'When it finishes, open it (tap the download notification, or find Omni.apk in Files / Downloads).',
  'If Android says "can’t install unknown apps from this source" — tap Settings, turn on "Allow from this source", then go Back.',
  'Tap Install.',
  'If "Google Play Protect — App blocked to protect your device" appears, tap "More details", then "Install anyway". It says that only because this is our own private build and not on the Play Store — it is our app and it is safe.',
  'Open Omni and sign in with your normal Alpha Direct work account (the same Microsoft sign-in you use on the computer).',
]

export default function GetOmni() {
  return (
    <div style={{ background: C.surface, minHeight: '100vh', fontFamily: sans }}>
      <header style={{ textAlign: 'center', padding: headerPad, background: C.card, borderBottom: `1px solid ${C.line}` }}>
        <span style={{ fontFamily: serif, fontWeight: 800, fontSize: 21, color: C.ink }}>Omni</span>
      </header>
      <main style={{ padding: 20, maxWidth: 480, margin: '0 auto' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, margin: '6px 0 16px' }}>
          <Smartphone size={26} style={{ color: C.orangeDeep }} />
          <h1 style={h(26)}>Install Omni on Android</h1>
        </div>

        <a href="/Omni.apk" download
           style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10, width: '100%', boxSizing: 'border-box',
                    padding: '17px', borderRadius: 16, textDecoration: 'none',
                    background: 'linear-gradient(135deg,#F4A623,#E2700B)', color: '#fff', fontWeight: 700, fontSize: 17,
                    boxShadow: '0 12px 26px rgba(226,112,11,0.32)' }}>
          <Download size={20} /> Download Omni (APK)
        </a>
        <p style={{ textAlign: 'center', color: C.inkSoft, fontSize: 12, margin: '8px 0 22px' }}>
          ~1.2 MB · Android 7+ · our own signed build
        </p>

        {/* The warning people actually hit, said before they hit it. */}
        <div style={{ ...card, padding: 14, marginBottom: 18, display: 'flex', gap: 12, borderColor: C.orange }}>
          <ShieldAlert size={22} style={{ color: C.orangeDeep, flexShrink: 0 }} />
          <span style={{ fontSize: 14, lineHeight: 1.5, color: C.ink }}>
            Android will warn you that this app is <b>unsafe</b> or say <b>“App not installed”</b>.
            That is normal for a company app that is not on the Play Store. Tap
            <b> More details</b>, then <b>Install anyway</b>.
          </span>
        </div>

        <h2 style={{ ...h(18), marginBottom: 12 }}>Then install it</h2>
        <ol style={{ margin: 0, paddingLeft: 0, listStyle: 'none' }}>
          {STEPS.map((s, i) => (
            <li key={i} style={{ ...card, padding: 14, marginBottom: 10, display: 'flex', gap: 12 }}>
              <span style={{ flexShrink: 0, width: 26, height: 26, borderRadius: 999, background: C.navy, color: '#fff', fontWeight: 700, fontSize: 13, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>{i + 1}</span>
              <span style={{ fontSize: 14, lineHeight: 1.5, color: C.ink }}>{s}</span>
            </li>
          ))}
        </ol>

        <h2 style={{ ...h(18), margin: '22px 0 10px' }}>Don’t want the APK?</h2>
        <div style={{ ...card, padding: 14, marginBottom: 10 }}>
          <span style={{ fontSize: 14, lineHeight: 1.6, color: C.ink }}>
            You do not need it. Open <b>omni.alphadirect.co.bw/app</b> in Chrome, tap the
            menu <b>⋮</b> then <b>Install app</b>. You get the same icon on your home screen with no
            warning at all. On an iPhone: open it in Safari, tap <b>Share</b> then
            <b> Add to Home Screen</b>.
          </span>
        </div>

        <p style={{ textAlign: 'center', marginTop: 18, fontSize: 13 }}>
          <Link href="/app" style={{ color: C.inkSoft }}>Already installed? Open Omni</Link>
        </p>
      </main>
    </div>
  )
}
