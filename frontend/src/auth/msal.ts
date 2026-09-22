/**
 * MSAL configuration + helpers for Azure AD sign-in.
 *
 * Activates only when NEXT_PUBLIC_AZURE_TENANT_ID and NEXT_PUBLIC_AZURE_CLIENT_ID
 * are baked into the build (via docker-compose build args). When either is
 * missing, isSSOConfigured() returns false and the login page falls back to
 * the existing username/password form.
 *
 * Cross-app session note: because we share the same Azure tenant with OneDesk
 * (and any other AD apps), Microsoft caches the user's authenticated session
 * in their browser. If a user has already signed into OneDesk in this browser,
 * acquireTokenSilent() returns a token for our API without any redirect or
 * prompt — that's the "seamless cross-app" behaviour the user requested.
 */

import {
  PublicClientApplication,
  type Configuration,
  type AccountInfo,
  type SilentRequest,
} from '@azure/msal-browser'

const tenantId   = process.env.NEXT_PUBLIC_AZURE_TENANT_ID   || ''
const clientId   = process.env.NEXT_PUBLIC_AZURE_CLIENT_ID   || ''
const apiScope   = process.env.NEXT_PUBLIC_AZURE_API_SCOPE   || ''

export const SSO_CONFIGURED = Boolean(tenantId && clientId)
export const SSO_API_CALLS_READY = Boolean(SSO_CONFIGURED && apiScope)

export function isSSOConfigured(): boolean {
  return SSO_CONFIGURED
}

// Singleton — MSAL must only be initialized once per page load.
let _msalInstance: PublicClientApplication | null = null

export function getMsalInstance(): PublicClientApplication {
  if (!SSO_CONFIGURED) {
    throw new Error('Azure SSO not configured (NEXT_PUBLIC_AZURE_TENANT_ID / NEXT_PUBLIC_AZURE_CLIENT_ID missing at build time)')
  }
  if (!_msalInstance) {
    // Build config lazily so we can read window.location.origin at runtime.
    // Pinning redirectUri to the origin (not window.location.href) keeps
    // the URI stable regardless of which page the user clicks 'Sign in'
    // from — only the registered origin needs to live in Entra.
    const origin = typeof window !== 'undefined' ? window.location.origin : undefined
    const config: Configuration = {
      auth: {
        clientId,
        authority: `https://login.microsoftonline.com/${tenantId}`,
        redirectUri:         origin,
        postLogoutRedirectUri: origin,
        // CFO structural-audit directive 2026-05-19 — IMDS exposure note:
        // the @azure/msal-browser bundle contains a string reference to
        // the Azure Instance Metadata Service URL
        // (http://169.254.169.254/metadata/...). MSAL only tries to fetch
        // it when running in Azure Functions / on an Azure VM with
        // `azureRegion` set in `azureCloudOptions`. We never set it, so
        // the URL stays dead code in the browser bundle — nothing to do.
      },
      cache: {
        // localStorage so the signed-in account record survives across tabs
        // and fresh page loads. Without this, opening any deep route (e.g. a
        // bookmark to /journal-entries) in a new tab leaves MSAL with zero
        // accounts and apiFetch sends no Authorization header → 401 on every
        // data call. The user's Microsoft browser cookie is still alive so
        // ssoSilent below would also work, but caching the account avoids
        // a round-trip to login.microsoftonline.com on every fresh tab.
        cacheLocation: 'localStorage',
        storeAuthStateInCookie: false,
      },
    }
    _msalInstance = new PublicClientApplication(config)
  }
  return _msalInstance
}

/**
 * Login scopes — what we ask Microsoft for at sign-in.
 *
 * `openid`, `profile`, `email`, `User.Read` are always included to get the user's
 * identity. The API scope is included when configured so the access token can
 * be sent to our backend.
 */
export function loginScopes(): string[] {
  const scopes = ['openid', 'profile', 'email', 'User.Read']
  if (apiScope) scopes.push(apiScope)
  return scopes
}

/** Resource-only scope for acquireTokenSilent / acquireTokenRedirect. */
export function apiScopes(): string[] {
  return apiScope ? [apiScope] : []
}

/**
 * resetSignIn — one-click "Fix sign-in" for non-technical staff (CFO 2026-07-09).
 *
 * The usual "stuck at sign-in" is a STALE MSAL cache in the browser's
 * localStorage (a half-finished redirect, an expired account record, a
 * cleared-but-not-really state). A web page cannot delete Microsoft's own
 * login.microsoftonline.com cookies (cross-origin) — but that is not what's
 * stuck; the app-side cache is. This wipes everything omni owns on the device
 * and starts a fresh interactive login with an account picker:
 *
 *   1. MSAL cache (all msal.* keys) via clearCache() + a manual sweep.
 *   2. omni's own token + app localStorage/sessionStorage.
 *   3. omni-domain cookies (best-effort; HttpOnly ones are dropped by the
 *      fresh login anyway).
 *   4. loginRedirect with prompt:'select_account' so Microsoft always shows
 *      the account chooser instead of silently reusing the broken session.
 *
 * Safe to call even when SSO isn't configured (it just clears + reloads).
 */
export async function resetSignIn(): Promise<void> {
  try {
    // 1 + 2. Blow away every local/session key omni or MSAL wrote.
    try {
      if (SSO_CONFIGURED) {
        const inst = getMsalInstance()
        await inst.initialize().catch(() => {})
        // clearCache() exists on msal-browser >=3; guard for older builds.
        const anyInst = inst as unknown as { clearCache?: () => Promise<void> }
        if (typeof anyInst.clearCache === 'function') await anyInst.clearCache()
      }
    } catch { /* keep clearing regardless */ }
    try {
      Object.keys(localStorage).forEach(k => {
        if (/^msal|^alpha_|token|account/i.test(k)) localStorage.removeItem(k)
      })
      // Nuke the rest too — omni holds no data worth keeping across a reset.
      localStorage.clear()
      sessionStorage.clear()
    } catch { /* storage may be blocked */ }

    // 3. Drop omni-domain cookies we can see (non-HttpOnly).
    try {
      document.cookie.split(';').forEach(c => {
        const name = c.split('=')[0].trim()
        if (!name) return
        document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/`
      })
    } catch { /* ignore */ }
  } finally {
    // 4. Fresh interactive login (account picker), or a plain reload if SSO
    // isn't configured on this build.
    if (SSO_CONFIGURED) {
      try {
        const inst = getMsalInstance()
        await inst.initialize().catch(() => {})
        await inst.loginRedirect({ scopes: loginScopes(), prompt: 'select_account' })
        return
      } catch { /* fall through to reload */ }
    }
    window.location.href = '/login'
  }
}

// The marker lib/api.ts stores instead of a real token when the session came
// from Microsoft SSO. Duplicated here rather than imported because api.ts only
// ever loads this module dynamically — a static import back would drag the whole
// MSAL bundle into every page. Keep the two in step (lib/api.ts SSO_SENTINEL_TOKEN).
const SSO_SENTINEL = '__sso__'

/**
 * Is the browser holding a real email+password session?
 *
 * WHY THIS EXISTS — measured on prod 2026-07-29, and it was costing about TEN
 * SECONDS on every page load. An email+password session stores a real DRF token
 * and has no Microsoft account in the browser. acquireApiToken() would then fall
 * through to msal.ssoSilent(), which opens a hidden frame to
 * login.microsoftonline.com and blocks until it times out — roughly 10s — before
 * finally returning null so the caller could use the DRF token it already had.
 * Every API call on the page queued behind that one await. Timed on /accounts:
 * page code ready at 0.3s, first request to our own server not until 10.5s, data
 * on screen at 21s — while the server answered each query in 0.25–0.6s.
 *
 * A real DRF token already authenticates every endpoint, so asking Microsoft
 * gains nothing. SSO sessions store the sentinel instead of a real token, so
 * they are unaffected and keep the full tier-1/tier-2 behaviour below.
 */
function hasPasswordSession(): boolean {
  if (typeof window === 'undefined') return false
  try {
    const t = localStorage.getItem('alpha_token')
    return Boolean(t) && t !== SSO_SENTINEL
  } catch {
    return false
  }
}

/**
 * Quietly acquire an access token for our backend API.
 * Returns null if not signed in or if no api scope is configured.
 *
 * Resilience tiers (in order):
 *   1. Use the active or first cached account → acquireTokenSilent.
 *   2. If MSAL has no cached account on this tab (fresh tab, cleared
 *      sessionStorage, etc.) BUT the user's Microsoft browser cookie is
 *      still alive, fall through to ssoSilent — that lights up the account
 *      without any redirect or UI, then retry.
 *
 * Returns null only when (a) the user really is signed out at Microsoft, or
 * (b) Entra requires interaction (consent change, MFA step-up, etc.) — in
 * those cases apiFetch falls back to the legacy DRF Token path, or the page
 * surfaces an auth error.
 *
 * ...or (c) this is an email+password session — see below. That case is checked
 * FIRST because it cost ~10 seconds on every single page load.
 */
export async function acquireApiToken(): Promise<string | null> {
  if (!SSO_CONFIGURED || !apiScope) return null
  if (hasPasswordSession()) return null
  const msal = getMsalInstance()

  // initialize() is idempotent in MSAL v3+. Calling it defensively here means
  // apiFetch works even if it fires before MsalAuthProvider's effect has run
  // (e.g. during the very first paint of a Server Component-driven page).
  try { await msal.initialize() } catch { /* already initialized */ }

  let account: AccountInfo | undefined = msal.getActiveAccount() ?? msal.getAllAccounts()[0]

  // Tier 2: ssoSilent if no cached account on this tab.
  if (!account) {
    try {
      const ssoResult = await msal.ssoSilent({ scopes: loginScopes() })
      if (ssoResult.account) {
        msal.setActiveAccount(ssoResult.account)
        account = ssoResult.account
      }
    } catch {
      // User is signed out at MS, or interaction is required — bail to null.
      return null
    }
  }

  if (!account) return null

  const req: SilentRequest = {
    account,
    scopes: [apiScope],
  }
  try {
    const result = await msal.acquireTokenSilent(req)
    return result.accessToken || null
  } catch {
    // InteractionRequiredAuthError → surface as null so apiFetch can decide
    // whether to redirect to login. Don't trigger a popup here — apiFetch
    // is called from data-fetching effects all over the app and a popup
    // mid-render would be jarring. The login page is the right place for
    // that ceremony.
    return null
  }
}

/**
 * Best-effort clear of MSAL's local state without contacting Microsoft.
 *
 * Used by apiFetch's sentinel-stuck recovery — we know the cached MSAL
 * tokens can't reach our API, so we wipe them locally before bouncing
 * to /login. Without this, /login's tryRouteHome() sees the stale
 * account, calls setToken(sentinel), and ricochets the user back to
 * /dashboard, where the same 401 fires again — that's the glitch loop
 * the CFO reported.
 *
 * Note: this does NOT log the user out of Microsoft itself; their
 * browser cookie still grants ssoSilent the next time they click
 * Sign in. Use `signOut()` below for an explicit, server-side logout.
 */
export async function clearLocalMsalState(): Promise<void> {
  if (!SSO_CONFIGURED) {
    // External-audit follow-up 2026-05-19: even on builds where SSO is
    // off, scrub the localStorage MSAL keys so a shared workstation
    // doesn't carry the previous user's token forward.
    _scrubMsalLocalStorage()
    return
  }
  try {
    const msal = getMsalInstance()
    try { await msal.initialize() } catch { /* already initialized */ }
    // clearCache() removes account + token entries from cacheLocation.
    // Cast through unknown because the MSAL types vary by minor version
    // (older builds expose it via the AccountInfo overload only).
    const m = msal as unknown as { clearCache?: (a?: AccountInfo) => Promise<void> | void }
    if (typeof m.clearCache === 'function') {
      await m.clearCache()
    } else {
      // Fallback: drop the active account record.
      const acct = msal.getActiveAccount()
      if (acct) msal.setActiveAccount(null)
    }
  } catch {
    // swallow — the recovery redirect is what matters
  }
  // External-audit follow-up 2026-05-19: belt-and-braces — wipe every
  // MSAL key from localStorage in addition to whatever clearCache()
  // managed to remove. This is critical for shared boardroom Macs where
  // the next signed-in user must not pick up a stale token.
  _scrubMsalLocalStorage()
}

function _scrubMsalLocalStorage(): void {
  if (typeof window === 'undefined') return
  // Scrub MSAL keys from BOTH web stores. localStorage holds the account +
  // token cache (cacheLocation 'localStorage'); but MSAL v4 keeps the
  // *interaction status* (msal.<clientId>.interaction.status) in
  // sessionStorage. A redirect that gets interrupted (closed MS tab,
  // double-click, network blip) leaves interaction.status =
  // 'interaction_in_progress' wedged in sessionStorage — after which every
  // loginRedirect throws `interaction_in_progress` and the user is stuck at
  // /login forever ("the website won't let me in"). Clearing only localStorage
  // never lifted that wedge. Scrub sessionStorage too. (CFO lockout 2026-06-19.)
  const _scrub = (store: Storage | undefined | null) => {
    if (!store) return
    try {
      const drop: string[] = []
      for (let i = 0; i < store.length; i++) {
        const k = store.key(i)
        if (!k) continue
        // MSAL v4 cacheLocation 'localStorage' writes keys prefixed
        // 'msal.', plus a per-clientId account key starting with the
        // clientId GUID, plus a few telemetry keys.
        if (
          k.startsWith('msal.') ||
          k.startsWith('msal-') ||
          k.includes('login.microsoftonline.com') ||
          k.includes('.b2clogin.') ||
          // Per-clientId entries (MSAL v3+) take the form
          // "<clientId>-<environment>-<credentialType>-...".
          k.includes('-login.microsoftonline.com-')
        ) {
          drop.push(k)
        }
      }
      for (const k of drop) store.removeItem(k)
    } catch {
      /* private mode / quota — best effort */
    }
  }
  _scrub(window.localStorage)
  _scrub(window.sessionStorage)   // <- the interaction.status wedge lives here
}

/**
 * Explicit, user-initiated logout.
 *
 * Calls logoutRedirect() so Microsoft ends the SSO session too, then
 * lands the browser at /login?signedout=1 (no auto-route back).
 * Falls back to clearing local state + a hard nav to /login when MSAL
 * is unavailable (e.g. SSO disabled build, error during init).
 */
export async function signOut(): Promise<void> {
  const origin = typeof window !== 'undefined' ? window.location.origin : ''
  const target = `${origin}/login?signedout=1`
  if (!SSO_CONFIGURED) {
    if (typeof window !== 'undefined') window.location.replace(target)
    return
  }
  try {
    const msal = getMsalInstance()
    try { await msal.initialize() } catch { /* already initialized */ }
    const account = msal.getActiveAccount() ?? msal.getAllAccounts()[0]
    await msal.logoutRedirect({
      account: account ?? undefined,
      postLogoutRedirectUri: target,
    })
  } catch {
    await clearLocalMsalState()
    if (typeof window !== 'undefined') window.location.replace(target)
  }
}
