'use client';

// Cache busting for /login is handled by the headers() block in
// `frontend/next.config.ts` (no-store, no-cache, must-revalidate). Route
// Segment Config exports (`dynamic`, `revalidate`) are illegal in
// `'use client'` files in Next 15 — they break `next build` with
// "Invalid revalidate value … must be a non-negative number or false".

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import {
  clearLocalMsalState,
  getMsalInstance,
  isSSOConfigured,
  loginScopes,
  resetSignIn,
} from '@/auth/msal';
import { exchangeSsoForToken, getToken, removeToken, setToken, SSO_SENTINEL_TOKEN } from '@/lib/api';
import { clearSignedOut, isSignedOut, markSignedOut } from '@/auth/signedOut';
import AccessPortalShell, { OAP, MicrosoftMark } from '@/app/_components/AccessPortalShell';

// Sticky signed-out marker. Persists across React Strict Mode double-effects
// + the cache-busting reload Next.js does after `signOut()` returns from
// Microsoft. Cleared only when the user explicitly clicks the Sign in CTA.
// The gate now lives in one shared module so the root landing page gets it too
// (see auth/signedOut.ts).

// No-login problem/idea form (core/bug_quick.py). The token signs a constant,
// so this URL is permanent. Regenerate with quick_bug_url() on the server.
const NO_LOGIN_REPORT_URL =
  'https://omni.alphadirect.co.bw/api/v1/report-problem/eyJrIjoib21uaS1xdWljay1idWcifQ:1wsJpv:WPHvzTXhI1TR9io-GHcJ33AUx-sFcAc85buGM1vBmIw/'

export default function LoginPage() {
  const router = useRouter();

  // CFO directive 2026-07-29: show BOTH ways in, and say plainly which one is
  // quick. Email + password stays the recommended path — it is the reliable one
  // (Microsoft's own re-auth cadence kept interrupting live sessions) and, since
  // the 2026-07-29 fix in auth/msal.ts, the fast one: a password session no
  // longer waits on a Microsoft check that cannot succeed, which was costing
  // about ten seconds on every page. Microsoft sits below for whoever prefers it.
  // (Was hidden entirely from 2026-07-18 to 2026-07-29.)
  const SHOW_SSO = true;

  // Auto-route when MSAL has already silently authenticated us.
  // MsalAuthProvider runs handleRedirectPromise() + ssoSilent on mount; if
  // the user has an active Microsoft session in this browser (e.g. they
  // were signed into OneDesk in another tab) MSAL writes the access/id
  // token to localStorage and sets an active account. Without this hook the
  // /login page just sits there even though the user is authenticated.
  //
  // PREVIEW MODE: appending `?preview=1` (or `?landing=1`) suppresses the
  // auto-route so admins can verify the landing visual without signing out.
  // The Sign-in button itself still works in preview mode.
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const params = new URLSearchParams(window.location.search);
    if (params.has('preview') || params.has('landing')) return;

    // Sticky signed-out gate. We set this once when the user (or the
    // sentinel-stuck recovery) lands on /login?signedout=1, then we never
    // auto-route again until the user explicitly clicks Sign in. Without
    // this, the cached MSAL account on the next page-load still satisfies
    // tryRouteHome() and the user ricochets back to /dashboard.
    if (params.has('signedout')) {
      markSignedOut();
      // Hard scrub of every credential surface so the next interval tick
      // (in case React Strict Mode double-fires the effect) can't find
      // anything to ricochet on.
      try { removeToken(); } catch { /* best effort */ }
      try { localStorage.removeItem('alpha_user'); } catch { /* best effort */ }
      try { localStorage.removeItem('alpha_company_id'); } catch { /* best effort */ }
      void clearLocalMsalState();
      return;
    }
    try {
      if (isSignedOut()) {
        // Same session, user already signed out — keep auto-route off
        // until they click Sign in. Don't even start the MSAL probe; it
        // would race the click handler if the Microsoft cookie is still
        // alive (which it is, until logoutRedirect lands on MS's side).
        return;
      }
    } catch { /* private mode — fall through */ }

    const tryRouteHome = () => {
      // Triple-check the kill switch on every poll. If the user clicked
      // Logout while the interval was already running, this short-circuit
      // stops the very next tick from ricocheting them back to /dashboard.
      try {
        if (isSignedOut()) return true;
      } catch { /* fall through */ }
      if (getToken()) {
        router.replace('/');
        return true;
      }
      if (!isSSOConfigured()) return false;
      try {
        const msal = getMsalInstance();
        const account = msal.getActiveAccount() || msal.getAllAccounts()[0];
        if (account) {
          msal.setActiveAccount(account);
          // Graphite-style: swap the Microsoft sign-in for Omni's own token
          // ONCE, then route. Fall back to the sentinel if the exchange fails,
          // so sign-in never regresses. (CFO 2026-08-24.)
          void (async () => {
            if (!(await exchangeSsoForToken())) setToken(SSO_SENTINEL_TOKEN);
            router.replace('/');
          })();
          return true;
        }
      } catch {
        /* MSAL not initialized yet — fall through to polling */
      }
      return false;
    };

    if (tryRouteHome()) return;

    let attempts = 0;
    const id = window.setInterval(() => {
      attempts += 1;
      if (tryRouteHome() || attempts > 20) window.clearInterval(id);
    }, 300);
    return () => window.clearInterval(id);
  }, [router]);

  // "Fix it & try again" — clears the stale MSAL cache + omni storage and
  // restarts login with an account picker (CFO 2026-07-09, for staff who
  // can't clear cookies themselves).
  const handleFixSignIn = async () => {
    clearSignedOut();
    try { removeToken(); } catch { /* ignore */ }
    await resetSignIn();
  };

  const handleSignIn = async () => {
    // Clicking Sign in is the ONLY thing that lifts the sticky signed-out
    // gate. Drop it before any MSAL call so the auto-route logic on the
    // next render (post-loginRedirect) is allowed to fire again.
    clearSignedOut();

    if (!isSSOConfigured()) {
      // eslint-disable-next-line no-console
      console.error('MSAL: Azure SSO is not configured in this build.');
      return;
    }

    try {
      const msal = getMsalInstance();
      // MSAL v4 REQUIRES initialize() before any other API call. The provider
      // also inits on mount, but a click that races the provider effect (or a
      // freshly-reloaded /login after signOut) would otherwise throw
      // `uninitialized_public_client_application`. initialize() is idempotent.
      try { await msal.initialize(); } catch { /* already initialized */ }
      // Drain any pending redirect interaction. Without this, the FIRST click
      // after a sign-out throws `interaction_in_progress` (the logout redirect
      // left the flag set) — caught below, button looks dead, only a manual
      // reload fixes it. That was the CFO's "can't sign in" repro.
      try { await msal.handleRedirectPromise(); } catch { /* no pending */ }

      const account = msal.getActiveAccount() || msal.getAllAccounts()[0];
      if (account) {
        msal.setActiveAccount(account);
        // Graphite-style: swap the Microsoft sign-in for Omni's own token once,
        // then route; fall back to the sentinel if the exchange fails.
        if (!(await exchangeSsoForToken())) setToken(SSO_SENTINEL_TOKEN);
        router.replace('/');
        return;
      }
      await msal.loginRedirect({ scopes: loginScopes() });
    } catch (err) {
      const e = err as { errorCode?: string; message?: string };
      const stuck =
        e?.errorCode === 'interaction_in_progress' ||
        /interaction_in_progress/i.test(e?.message || '');
      if (stuck) {
        // A prior redirect left the interaction flag set. Wipe the local MSAL
        // cache (clears `msal.*.interaction.status`) and retry once.
        try {
          await clearLocalMsalState();
          const msal2 = getMsalInstance();
          try { await msal2.initialize(); } catch { /* already initialized */ }
          await msal2.loginRedirect({ scopes: loginScopes() });
          return;
        } catch (retryErr) {
          // eslint-disable-next-line no-console
          console.error('MSAL loginRedirect retry failed:', retryErr);
        }
      }
      // eslint-disable-next-line no-console
      console.error('MSAL loginRedirect failed:', err);
    }
  };

  return (
    <AccessPortalShell>
      <h1 className="oap-h1">Sign in to Omni</h1>
      {/* Wording from the CFO's supplied design (2026-09-12 handoff). FROZEN —
          verbatim from reference/Omni Sign In.dc.html, em-dash and all. It
          already says which way in is the quick one, which is what the
          2026-07-29 directive asked for, so the separate "Recommended" note the
          old skin carried under the button is gone. */}
      <p className="oap-sub">
        One source of truth for Alpha Direct. Email &amp; password is the quick,
        reliable way in — Microsoft is here too.
      </p>

      {/* Email + password is the recommended path (CFO 2026-07-29). It routes
          to the full sign-in surface at /staff-login. */}
      <a href="/staff-login" className={OAP.primaryBtn}>Sign in with email</a>

      {SHOW_SSO && (
        <>
          <div className={OAP.orModule}>or</div>

          <button type="button" onClick={handleSignIn} className={OAP.msBtn}>
            <MicrosoftMark />
            Sign in with Microsoft
          </button>

          {/* One-click fix for a stuck Microsoft sign-in (CFO 2026-07-09):
              wipes the stale MSAL cache + omni storage and restarts login
              with an account picker. Kept next to the Microsoft option since
              it only applies to that path. */}
          <button
            type="button"
            onClick={handleFixSignIn}
            className={OAP.msBtn}
            style={{ marginTop: 10, fontSize: 12.5, fontWeight: 500, color: '#55657a' }}
          >
            ↻ Microsoft sign-in stuck? Fix it &amp; try again
          </button>
        </>
      )}

      {/* THE CATCH-22 FIX (CFO 2026-08-07). /report-bug carries a "Report a
          Login Issue" card — and it sits behind the login, so anyone actually
          locked out cannot reach it. This link opens the no-login report form
          (core/bug_quick.py), which needs no session at all. It must stay on
          THIS page: it is the only place a locked-out person will look. */}
      <a
        href={NO_LOGIN_REPORT_URL}
        className={OAP.msBtn}
        style={{ marginTop: 10, fontSize: 12.5, fontWeight: 500, color: '#55657a' }}
      >
        Still cannot get in? Tell us — no sign-in needed
      </a>
    </AccessPortalShell>
  );
}
