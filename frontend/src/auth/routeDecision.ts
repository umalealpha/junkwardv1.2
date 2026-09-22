/**
 * Should a sign-in screen send this visitor through to the app, or hold them?
 *
 * Both sign-in surfaces ask the same question, and twice now they have answered
 * it differently, locking people out:
 *
 *   - 6c76969 (Jun 2026): the stuck-interaction recovery went into /login and
 *     not the root landing.
 *   - d1ff7181 (Aug 2026): the sticky signed-out gate went into /login and not
 *     the root landing, so a stale Microsoft session bounced the user round in
 *     circles — indistinguishable from Microsoft refusing their password.
 *
 * Both were invisible to the backend (no request ever arrives) and invisible to
 * the type checker. The only thing that catches them is asking one shared,
 * testable function instead of hand-writing the branch on each page.
 *
 * Pure on purpose: no storage, no MSAL, no router. The caller reads the state,
 * this decides, the caller acts.
 */

export interface RouteState {
  /** The sticky signed-out gate is set for this tab. */
  signedOut: boolean;
  /** A credential is present in local storage (real token or the SSO sentinel). */
  hasToken: boolean;
  /** This build has Azure SSO configured. */
  ssoConfigured: boolean;
  /** MSAL is holding a cached account for this browser. */
  hasCachedAccount: boolean;
}

export type RouteDecision =
  /** Hold on the sign-in screen. The user must click Sign in. */
  | 'hold'
  /** Send them through to the app. */
  | 'route-home'
  /** Nothing decided yet — MSAL may still be waking up; poll again. */
  | 'wait';

export function decideRouteHome(state: RouteState): RouteDecision {
  // THE INVARIANT: a signed-out tab is never routed home, whatever else is
  // lying around in storage. A stale credential and a cached Microsoft account
  // both survive being signed out, so either one would otherwise wave the user
  // straight back into a session that no longer works.
  if (state.signedOut) return 'hold';

  if (state.hasToken) return 'route-home';

  // Without SSO there is nothing further to wait for.
  if (!state.ssoConfigured) return 'hold';

  // A cached Microsoft account is enough to go on: the caller mints the
  // sentinel and the API layer exchanges it for a real token.
  if (state.hasCachedAccount) return 'route-home';

  // MSAL may not have finished initialising — the caller polls briefly.
  return 'wait';
}
