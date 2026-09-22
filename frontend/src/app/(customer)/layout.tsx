import type { Metadata, Viewport } from 'next'
import { Playfair_Display, Inter } from 'next/font/google'
import { CustomerShell } from './CustomerShell'

const playfair = Playfair_Display({ subsets: ['latin'], weight: ['600', '700', '800'], variable: '--font-playfair', display: 'swap' })
const inter = Inter({ subsets: ['latin'], weight: ['400', '500', '600', '700'], variable: '--font-inter', display: 'swap' })

/**
 * Customer app layout (route /m). A separate route group from (dashboard):
 * NO ERP sidebar, NO staff SSO — its own email-OTP gate lives in CustomerShell.
 * Sets the dedicated "Alpha Rewards" PWA manifest so iOS/Android "Add to Home
 * Screen" installs a customer app that launches at /m.
 */
export const metadata: Metadata = {
  title: 'Alpha Nexus',
  manifest: '/customer.webmanifest',
  appleWebApp: { capable: true, title: 'Alpha Nexus', statusBarStyle: 'default' },
  // iOS Safari ONLY honours the legacy `apple-mobile-web-app-capable` tag to
  // launch an "Add to Home Screen" install as a full-screen standalone app.
  // Next.js now emits just the modern `mobile-web-app-capable`, which iOS
  // ignores — so without this the iPhone icon opens a Safari-chrome bookmark,
  // not an app (the "iPhone app doesn't work" complaint). Force the Apple tag.
  other: { 'apple-mobile-web-app-capable': 'yes' },
}

export const viewport: Viewport = {
  themeColor: '#0D1B2A',
  // Lock the app to light. It has a fixed light palette; on a phone set to dark
  // mode the browser was force-inverting form fields (dark background) while the
  // inline dark text stayed dark — so typed text was invisible in every input
  // (bug d9557ab8, Babusi Rasenyai). color-scheme:light stops that auto-invert.
  colorScheme: 'light',
  width: 'device-width',
  initialScale: 1,
  // REQUIRED for the notch/Dynamic Island fix: without viewport-fit=cover, iOS
  // Safari + the Tauri WKWebView (TestFlight) return env(safe-area-inset-*)=0,
  // so the headerPad calc(16px + env(...)) collapsed to 16px and the logo sat
  // under the iPhone 17 camera. With cover, the real inset (~59px) applies and
  // every /m header (which already uses headerPad) clears the notch.
  viewportFit: 'cover',
}

export default function CustomerLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className={`${playfair.variable} ${inter.variable}`}
         style={{ fontFamily: 'var(--font-inter), system-ui, sans-serif' }}>
      <CustomerShell>{children}</CustomerShell>
    </div>
  )
}
