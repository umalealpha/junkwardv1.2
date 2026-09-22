import type { Metadata } from 'next'

/**
 * Server layout for /thrive — exists only to override page metadata so iOS
 * "Add to Home Screen" from this page installs a dedicated **Alpha Thrive**
 * app (own name + icon) that launches straight to /thrive, instead of the
 * generic Omni manifest (which starts at /dashboard). The full omni PWA stays
 * installable from elsewhere via the root manifest. No UI wrapper — the
 * (dashboard) layout already provides the shell.
 */
export const metadata: Metadata = {
  title: 'Alpha Thrive',
  manifest: '/thrive.webmanifest',
  appleWebApp: {
    capable: true,
    title: 'Alpha Thrive',
    statusBarStyle: 'black-translucent',
  },
}

export default function ThriveLayout({ children }: { children: React.ReactNode }) {
  return children
}
