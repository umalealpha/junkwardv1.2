import type { Metadata, Viewport } from 'next'
import { Inter, Kaushan_Script } from 'next/font/google'
import { AppShell } from './AppShell'
import { APP_THEMES, DEFAULT_THEME } from './appThemes'
import './app.css'

const inter = Inter({ subsets: ['latin'], weight: ['400', '500', '600', '700'], variable: '--font-inter', display: 'swap' })
// The splash wordmark's font, fixed by the CFO's supplied design (2026-09-08).
// Loaded through next/font so it is self-hosted at build time — the app must
// keep working with no call out to Google at runtime.
const kaushan = Kaushan_Script({ subsets: ['latin'], weight: '400', variable: '--font-kaushan', display: 'swap' })

/** Omni STAFF app (route /app). Own manifest so Add-to-Home-Screen installs
 * "Alpha Omni" launching at /app — not Alpha Nexus (/m) and not the desktop (/dashboard). */
export const metadata: Metadata = {
  title: 'Alpha Omni',
  manifest: '/app.webmanifest',
  icons: { icon: '/favicon.ico', apple: '/apple-touch-icon.png' },
  appleWebApp: { capable: true, title: 'Alpha Omni', statusBarStyle: 'black-translucent' },
  other: { 'apple-mobile-web-app-capable': 'yes' },   // iOS only honours the legacy tag
}
export const viewport: Viewport = {
  // No maximumScale: pinning zoom fails WCAG 1.4.4 (axe meta-viewport) and
  // stops a staff member with poor eyesight from pinching the screen bigger.
  themeColor: '#0B0B3B', width: 'device-width', initialScale: 1,
  viewportFit: 'cover',   // required or env(safe-area-inset-*) is 0 on iOS
}

/** Paint the staff member's chosen theme BEFORE the first frame. Without this the
 * app renders in the default palette for a beat and then snaps — which on Night
 * is a white flash in a dark room. Kept tiny and dependency-free on purpose. */
const THEME_BOOT = `(function(){try{
var T=${JSON.stringify(Object.fromEntries(Object.entries(APP_THEMES).map(([k, t]) => [k, t.vars])))};
var k=localStorage.getItem('omni_app_theme');if(!k||!T[k])k='${DEFAULT_THEME}';
var r=document.documentElement,v=T[k];for(var n in v)r.style.setProperty(n,v[n]);
r.setAttribute('data-omni-theme',k);r.style.colorScheme=k==='night'?'dark':'light';
}catch(e){}})();`

export default function OmniAppLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className={`${inter.variable} ${kaushan.variable}`} style={{ fontFamily: 'var(--font-inter), system-ui, sans-serif' }}>
      <script dangerouslySetInnerHTML={{ __html: THEME_BOOT }} />
      <AppShell>{children}</AppShell>
    </div>
  )
}
