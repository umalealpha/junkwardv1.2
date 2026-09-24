import type { Metadata, Viewport } from 'next'
import './globals.css'
import { ThemeProvider } from '@/contexts/ThemeContext'
import { QuoteProvider } from '@/contexts/QuoteContext'
import { NumberFormatProvider } from '@/contexts/NumberFormatContext'
import { MsalAuthProvider } from '@/auth/MsalAuthProvider'
import ErrorBoundary from '@/components/ErrorBoundary'
// LOCAL TEST ENV: next/font/google fetch removed — this machine builds behind a
// TLS-intercepting proxy that blocks the build-time Google Fonts download. The
// fonts are only used as CSS-variable classes with a system-ui fallback, so we
// stub .variable to '' and fall back to system fonts. Restore next/font when
// building off-proxy.
import { Toaster } from '@/components/Toaster'
import UpdateChecker from '@/components/UpdateChecker'

// CFO directive 2026-05-17: Book Antiqua across the whole ERP. Loaded via
// system fonts (no Google Fonts fetch) — Book Antiqua ships with Windows
// (Office) and macOS has Palatino as a near-identical fallback. Defined in
// tailwind.config.ts + globals.css.
//
// The one exception is the sign-in wordmark: the CFO's supplied design
// (2026-09-08) sets it in Caveat, and no system font is close to a handwriting
// face. next/font downloads it AT BUILD TIME and serves it from our own origin,
// so there is still no runtime call to Google.
const caveat = { variable: '' }

// The 2026-09-12 sign-in design (design_handoff_omni_signin) replaces the
// Caveat wordmark with Kaushan Script and sets the whole access portal in
// Inter. The handoff is explicit that Kaushan is the brand signature and must
// not be substituted, so both faces come down through next/font — downloaded at
// BUILD TIME and served from our own origin, exactly as Caveat already is.
// Caveat stays declared: it is still the face asserted by the design-freeze
// test for the older surfaces and costs nothing to keep.
const kaushan = { variable: '' }
const inter = { variable: '' }

export const metadata: Metadata = {
  title: {
    default: 'Alpha Direct Financial Management',
    template: '%s | Alpha Direct',
  },
  description: 'Alpha Direct Insurance Financial Management System — Botswana',
  keywords: ['finance', 'accounting', 'insurance', 'Botswana', 'Alpha Direct'],
  // PWA — installable as a desktop/mobile app (CFO 2026-06-21). Next serves the
  // manifest from app/manifest.ts at /manifest.webmanifest; declare it + the
  // icons so Edge/Chrome offer "Install Omni" with the right name + icon.
  manifest: '/manifest.webmanifest',
  applicationName: 'Omni — Alpha Direct',
  appleWebApp: { capable: true, title: 'Omni', statusBarStyle: 'black-translucent' },
  icons: {
    icon: [
      { url: '/icon-192.png', sizes: '192x192', type: 'image/png' },
      { url: '/icon-512.png', sizes: '512x512', type: 'image/png' },
    ],
    apple: [{ url: '/apple-touch-icon.png', sizes: '180x180', type: 'image/png' }],
  },
}

// Mobile viewport — CFO directive 2026-05-16 to support iPhone + Samsung
// browsing of omni.alphadirect.co.bw. Without this, mobile browsers render
// at desktop 980px width and shrink everything to ~30% of intended size.
// user-scalable kept enabled so users can pinch-zoom legal text.
export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  maximumScale: 5,
  userScalable: true,
  themeColor: '#0D1B2A',
  viewportFit: 'cover',
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    /* suppressHydrationWarning: the staff app writes its chosen theme onto <html>
       before the first paint (see (app)/layout.tsx) so Night does not flash white.
       That makes the client's attributes differ from the server's by design — this
       suppresses the attribute warning on this element only, nothing deeper. */
    <html lang="en" suppressHydrationWarning>
      <body className={`font-sans antialiased ${caveat.variable} ${kaushan.variable} ${inter.variable}`}>
        <ErrorBoundary surface="root">
          <MsalAuthProvider>
            <ThemeProvider>
              <NumberFormatProvider>
                <QuoteProvider>
                  {children}
                </QuoteProvider>
              </NumberFormatProvider>
            </ThemeProvider>
          </MsalAuthProvider>
        </ErrorBoundary>
        <Toaster />
        <UpdateChecker />
      </body>
    </html>
  )
}
