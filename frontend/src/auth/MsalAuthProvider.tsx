'use client'

/**
 * MsalAuthProvider — initializes MSAL on the client and exposes auth state
 * to the rest of the app via MsalProvider. Mounted high enough in the tree
 * that the login page + dashboard layout can both consume it.
 *
 * Safe to mount even when SSO is not configured — it renders children directly
 * with no MSAL initialization, so legacy password login keeps working.
 */

import { useEffect, useState, type ReactNode } from 'react'
import { usePathname } from 'next/navigation'
import { MsalProvider } from '@azure/msal-react'
import { EventType, type EventMessage, type AuthenticationResult } from '@azure/msal-browser'
import { getMsalInstance, isSSOConfigured } from './msal'
import { setToken, SSO_SENTINEL_TOKEN } from '@/lib/api'

// Public routes that must render without MSAL initialisation — the
// "Initialising authentication…" splash blocks render until ssoSilent
// settles, which (a) breaks Chrome with 3rd-party cookies disabled and
// (b) is plain wrong for marketing / buyer-facing pages.
const PUBLIC_ROUTE_PREFIXES = ['/buy-salvage', '/m', '/app']

function isPublicPath(path: string | null): boolean {
  if (!path) return false
  return PUBLIC_ROUTE_PREFIXES.some(p => path === p || path.startsWith(p + '/'))
}

export function MsalAuthProvider({ children }: { children: ReactNode }) {
  const pathname = usePathname()
  const isPublic = isPublicPath(pathname)
  // SSO splash skipped for public routes regardless of MSAL state.
  const [ready, setReady] = useState(!isSSOConfigured() || isPublic)

  useEffect(() => {
    if (!isSSOConfigured()) return
    // Public routes: never attempt MSAL init so the page can be hit
    // without third-party cookies / login.microsoftonline.com calls.
    if (isPublic) return
    const msal = getMsalInstance()
    msal.initialize().then(async () => {
      // Pick up redirect response (after sign-in)
      try {
        const result = await msal.handleRedirectPromise()
        if (result && result.account) {
          msal.setActiveAccount(result.account)
        } else if (!msal.getActiveAccount()) {
          const accounts = msal.getAllAccounts()
          if (accounts.length > 0) msal.setActiveAccount(accounts[0])
        }
        // Write the SSO sentinel so legacy `if (!getToken())` checks across
        // the codebase pass when the user is signed in via Microsoft only.
        if (msal.getActiveAccount()) {
          setToken(SSO_SENTINEL_TOKEN)
        }
      } catch (e) {
        // swallow — login page will surface errors via the loginRedirect call
        // eslint-disable-next-line no-console
        console.warn('MSAL handleRedirectPromise:', e)
      }

      msal.addEventCallback((event: EventMessage) => {
        if (event.eventType === EventType.LOGIN_SUCCESS && event.payload) {
          const payload = event.payload as AuthenticationResult
          if (payload.account) {
            msal.setActiveAccount(payload.account)
            setToken(SSO_SENTINEL_TOKEN)
          }
        }
      })

      setReady(true)
    })
  }, [isPublic])

  if (!isSSOConfigured() || isPublic) {
    return <>{children}</>
  }
  if (!ready) {
    return (
      <div className="min-h-screen flex items-center justify-center text-gray-500">
        Initialising authentication…
      </div>
    )
  }
  return <MsalProvider instance={getMsalInstance()}>{children}</MsalProvider>
}
