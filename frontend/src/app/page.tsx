'use client';

// Cache busting for this landing route is handled by the headers() block in
// `frontend/next.config.ts` (no-store, no-cache, must-revalidate). Route
// Segment Config exports (`dynamic`, `revalidate`) are illegal in
// `'use client'` files in Next 15 — they break `next build`.

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { getMsalInstance, isSSOConfigured, loginScopes, clearLocalMsalState } from '@/auth/msal';
import { getToken, setToken, removeToken, SSO_SENTINEL_TOKEN } from '@/lib/api';
import { clearSignedOut, isSignedOut, markSignedOut } from '@/auth/signedOut';
import { decideRouteHome } from '@/auth/routeDecision';
import type { AccountInfo } from '@azure/msal-browser';
import AccessPortalShell, { OAP, MicrosoftMark } from '@/app/_components/AccessPortalShell';

export default function LoginPage() {
  const router = useRouter();

  // Auto-route when MSAL has already silently authenticated us.
  // MsalAuthProvider runs handleRedirectPromise() + ssoSilent on mount; if
  // the user has an active Microsoft session in this browser (e.g. they
  // were signed into OneDesk in another tab) MSAL writes the access/id
  // token to localStorage and sets an active account. Without this hook the
  // landing page just sits there even though the user is authenticated.
  //
  // PREVIEW MODE: appending `?preview=1` (or `?landing=1`) suppresses the
  // auto-route so admins can verify the landing visual without signing out.
  // The Sign-in button itself still works in preview mode.
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const params = new URLSearchParams(window.location.search);
    if (params.has('preview') || params.has('landing')) return;

    // Sticky signed-out gate — the same one /login honours. The root landing is
    // what omni.alphadirect.co.bw serves, so it is where every bookmark and
    // emailed link arrives; without this it would find the still-cached MSAL
    // account, set the sentinel token and ricochet a signed-out user straight
    // back to /dashboard, which bounces them out again. That loop is
    // indistinguishable from "my Microsoft login does not work" (bug d1ff7181).
    if (params.has('signedout')) {
      markSignedOut();
      try { removeToken(); } catch { /* best effort */ }
      try { localStorage.removeItem('alpha_user'); } catch { /* best effort */ }
      try { localStorage.removeItem('alpha_company_id'); } catch { /* best effort */ }
      void clearLocalMsalState();
      return;
    }
    if (isSignedOut()) return;

    // The decision itself lives in auth/routeDecision so both sign-in surfaces
    // answer it identically and it can be tested — the two lockouts this guards
    // against were each a page forgetting one branch. Returning true stops the
    // poller.
    const tryRouteHome = () => {
      let account: AccountInfo | null = null;
      const sso = isSSOConfigured();
      if (sso) {
        try {
          const msal = getMsalInstance();
          account = msal.getActiveAccount() || msal.getAllAccounts()[0] || null;
        } catch {
          /* MSAL not initialized yet — decideRouteHome will say 'wait' */
        }
      }

      const decision = decideRouteHome({
        signedOut: isSignedOut(),
        hasToken: Boolean(getToken()),
        ssoConfigured: sso,
        hasCachedAccount: Boolean(account),
      });

      if (decision === 'wait') return false;
      if (decision === 'hold') return true;

      if (!getToken() && account) {
        getMsalInstance().setActiveAccount(account);
        setToken(SSO_SENTINEL_TOKEN);
      }
      router.replace('/my-omni');
      return true;
    };

    if (tryRouteHome()) return;

    let attempts = 0;
    const id = window.setInterval(() => {
      attempts += 1;
      if (tryRouteHome() || attempts > 20) window.clearInterval(id);
    }, 300);
    return () => window.clearInterval(id);
  }, [router]);

  const handleSignIn = async () => {
    // The user asking to sign in is the only thing that lifts the gate.
    clearSignedOut();
    if (!isSSOConfigured()) {
      // eslint-disable-next-line no-console
      console.error('MSAL: Azure SSO is not configured in this build.');
      return;
    }
    try {
      const msal = getMsalInstance();
      // MSAL v4 REQUIRES initialize() before any other call; idempotent.
      try { await msal.initialize(); } catch { /* already initialized */ }
      // Drain any pending redirect interaction. Without this, a stuck
      // interaction_in_progress flag (left by an interrupted earlier redirect
      // — e.g. the user closed the MS tab, or a prior loop) makes the next
      // loginRedirect throw and the sign-in bounce straight back to this page.
      // This is the recurring "Initialising Authentication -> back to sign-in"
      // loop the CFO + Meduduetso hit (bug b9bf88bc). /login already had this
      // (commit 63061be); the root landing did not — now it does.
      try { await msal.handleRedirectPromise(); } catch { /* no pending */ }

      const account = msal.getActiveAccount() || msal.getAllAccounts()[0];
      if (account) {
        msal.setActiveAccount(account);
        setToken(SSO_SENTINEL_TOKEN);
        router.replace('/my-omni');
        return;
      }
      await msal.loginRedirect({ scopes: loginScopes() });
    } catch (err) {
      const e = err as { errorCode?: string; message?: string };
      const stuck =
        e?.errorCode === 'interaction_in_progress' ||
        /interaction_in_progress/i.test(e?.message || '');
      if (stuck) {
        // Wipe the local MSAL cache (clears the stuck interaction.status key)
        // and retry once — self-heals instead of looping.
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
      <p className="oap-sub">
        One source of truth for Alpha Direct. Email &amp; password is the quick,
        reliable way in — Microsoft is here too.
      </p>

      {/* Email + password is the recommended path (CFO 2026-07-29). It routes
          to the full sign-in surface at /staff-login. */}
      <a href="/staff-login" className={OAP.primaryBtn}>Sign in with email</a>

      <div className={OAP.orModule}>or</div>

      <button type="button" onClick={handleSignIn} className={OAP.msBtn}>
        <MicrosoftMark />
        Sign in with Microsoft
      </button>
    </AccessPortalShell>
  );
}
