'use client'

/** /m/get/android — public Android install page. APK download first, then steps. */
import Link from 'next/link'
import { Download, Smartphone } from 'lucide-react'
import { C, serif, sans, h, card, headerPad } from '../../../ui'

const STEPS = [
  'Tap the green button above to download AlphaNexus.apk (about 31 MB).',
  'When it finishes, open it (tap the download notification, or find it in Files / Downloads).',
  'Android will warn "can’t install unknown apps from this source" — tap Settings, turn on "Allow from this source", then go Back.',
  'Tap Install. If "Google Play Protect — App blocked to protect your device" appears, tap "More details", then "Install anyway". (It says "unsafe" only because it is a private test build, not on the Play Store yet — it is safe.)',
  'Open Alpha Nexus. On first launch, tap Allow for Camera, Microphone and Location (needed for the pulse scan and Click & Drive).',
  'Enter any email (a personal Gmail is fine — no Office 365 needed). You get a 6-digit code by email — type it in. You’re in.',
]

export default function GetAndroid() {
  return (
    <div style={{ background: C.surface, minHeight: '100vh', fontFamily: sans }}>
      <header style={{ textAlign: 'center', padding: headerPad, background: C.card, borderBottom: `1px solid ${C.line}` }}>
        <span style={{ fontFamily: serif, fontWeight: 800, fontSize: 21, color: C.ink }}>Alpha Nexus</span>
      </header>
      <main style={{ padding: 20, maxWidth: 480, margin: '0 auto' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, margin: '6px 0 16px' }}>
          <Smartphone size={26} style={{ color: C.teal }} />
          <h1 style={h(26)}>Install on Android</h1>
        </div>

        {/* Download FIRST */}
        <a href="/AlphaNexus.apk" download
           style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10, width: '100%', boxSizing: 'border-box',
                    padding: '17px', borderRadius: 16, textDecoration: 'none',
                    background: 'linear-gradient(135deg,#16a34a,#0f7a37)', color: '#fff', fontWeight: 700, fontSize: 17,
                    boxShadow: '0 12px 26px rgba(16,122,55,0.32)' }}>
          <Download size={20} /> Download Alpha Nexus (APK)
        </a>
        <p style={{ textAlign: 'center', color: C.inkSoft, fontSize: 12, margin: '8px 0 22px' }}>~31 MB · Android 7+ · safe test build</p>

        <h2 style={{ ...h(18), marginBottom: 12 }}>Then install it</h2>
        <ol style={{ margin: 0, paddingLeft: 0, listStyle: 'none' }}>
          {STEPS.map((s, i) => (
            <li key={i} style={{ ...card, padding: 14, marginBottom: 10, display: 'flex', gap: 12 }}>
              <span style={{ flexShrink: 0, width: 26, height: 26, borderRadius: 999, background: C.navy, color: '#fff', fontWeight: 700, fontSize: 13, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>{i + 1}</span>
              <span style={{ fontSize: 14, lineHeight: 1.5, color: C.ink }}>{s}</span>
            </li>
          ))}
        </ol>

        <p style={{ textAlign: 'center', marginTop: 18, fontSize: 14 }}>
          On an iPhone instead? <Link href="/m/get/ios" style={{ color: C.teal, fontWeight: 600 }}>iPhone install →</Link>
        </p>
        <p style={{ textAlign: 'center', marginTop: 8, fontSize: 13 }}>
          <Link href="/m/login" style={{ color: C.inkSoft }}>Already installed? Sign in</Link>
        </p>
      </main>
    </div>
  )
}
