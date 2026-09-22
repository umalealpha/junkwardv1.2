/**
 * The sticky signed-out gate — ONE definition, shared by every sign-in surface.
 *
 * Why this module exists. When a session goes stale, the recovery in apiFetch
 * (and the sidebar's Sign out) lands the browser on `/login?signedout=1`. That
 * page then sets a sessionStorage flag and stops auto-routing until the user
 * explicitly clicks Sign in. Without the flag, the still-cached MSAL account
 * satisfies tryRouteHome(), which sets the sentinel token and ricochets the
 * user to /dashboard — where the same failure fires and bounces them back.
 *
 * The flag was declared privately inside app/login/page.tsx, so the ROOT
 * landing page (app/page.tsx — what omni.alphadirect.co.bw actually serves, and
 * where every emailed link and bookmark lands) knew nothing about it. A user
 * whose Microsoft session had expired could be bounced to /login, correctly
 * gated there, then type the plain address and be ricocheted straight back into
 * the loop by the root page. From the user's side that is simply "I cannot log
 * in with my Microsoft credentials", forever, with nothing in the server logs
 * because no real token is ever requested (bug d1ff7181, reported 2026-08-08).
 *
 * This is the SECOND time a login recovery was applied to /login and missed the
 * root page — commit 6c76969 fixed the same shape of bug for the stuck
 * interaction_in_progress flag. Hence one shared module rather than a second
 * copy: any future guard belongs here, where both pages get it.
 */

export const SIGNED_OUT_KEY = 'omni_signed_out';

/** True when the user signed out (or was bounced out) in this browser tab. */
export function isSignedOut(): boolean {
  if (typeof window === 'undefined') return false;
  try {
    return sessionStorage.getItem(SIGNED_OUT_KEY) === '1';
  } catch {
    return false;                        // private mode — fail open, not stuck
  }
}

/** Remember that this tab is signed out, so no page auto-routes back in. */
export function markSignedOut(): void {
  if (typeof window === 'undefined') return;
  try {
    sessionStorage.setItem(SIGNED_OUT_KEY, '1');
  } catch {
    /* private mode — best effort */
  }
}

/** Clear the gate. Call this only when the user explicitly clicks Sign in. */
export function clearSignedOut(): void {
  if (typeof window === 'undefined') return;
  try {
    sessionStorage.removeItem(SIGNED_OUT_KEY);
  } catch {
    /* private mode — best effort */
  }
}
