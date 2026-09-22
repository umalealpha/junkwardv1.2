/**
 * The guards that keep staff able to sign in.
 *
 * Two lockouts reached live because a guard was added to /login and missed on
 * the root landing page, and nothing in the build could see it: the backend
 * never receives a request, and TypeScript is perfectly happy with a page that
 * simply forgets to ask. These tests are the thing that notices.
 *
 * If you are here because a test failed: do not relax the assertion. Every case
 * below is a lockout that actually happened, or the exact shape of one.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { decideRouteHome, type RouteState } from '../routeDecision';
import { SIGNED_OUT_KEY, clearSignedOut, isSignedOut, markSignedOut } from '../signedOut';

const base: RouteState = {
  signedOut: false,
  hasToken: false,
  ssoConfigured: true,
  hasCachedAccount: false,
};

describe('decideRouteHome — the signed-out gate beats everything', () => {
  // Bug d1ff7181: the COO was bounced out, correctly held on /login, then typed
  // the bare address. The root page saw the still-cached Microsoft account and
  // waved him back into a dead session, which threw him out again. Round and
  // round, for five days, looking exactly like a rejected password.
  it('holds a signed-out tab even when MSAL still has a cached account', () => {
    expect(decideRouteHome({ ...base, signedOut: true, hasCachedAccount: true }))
      .toBe('hold');
  });

  it('holds a signed-out tab even when a credential is still in storage', () => {
    expect(decideRouteHome({ ...base, signedOut: true, hasToken: true }))
      .toBe('hold');
  });

  it('holds a signed-out tab when both are present', () => {
    expect(decideRouteHome({
      ...base, signedOut: true, hasToken: true, hasCachedAccount: true,
    })).toBe('hold');
  });
});

describe('decideRouteHome — but it must not become a lockout', () => {
  it('routes a normal signed-in visitor home', () => {
    expect(decideRouteHome({ ...base, hasToken: true })).toBe('route-home');
  });

  it('routes a visitor whose Microsoft account is cached and session is live', () => {
    expect(decideRouteHome({ ...base, hasCachedAccount: true })).toBe('route-home');
  });

  it('waits while MSAL is still waking up, rather than parking the user', () => {
    expect(decideRouteHome(base)).toBe('wait');
  });

  it('holds when SSO is not configured and there is no token', () => {
    expect(decideRouteHome({ ...base, ssoConfigured: false })).toBe('hold');
  });

  it('still routes home without SSO when a password token is present', () => {
    expect(decideRouteHome({ ...base, ssoConfigured: false, hasToken: true }))
      .toBe('route-home');
  });
});

describe('the sticky signed-out gate', () => {
  beforeEach(() => {
    sessionStorage.clear();
    vi.restoreAllMocks();
  });

  it('is off by default', () => {
    expect(isSignedOut()).toBe(false);
  });

  it('remembers a sign-out, and an explicit sign-in clears it', () => {
    markSignedOut();
    expect(isSignedOut()).toBe(true);
    expect(sessionStorage.getItem(SIGNED_OUT_KEY)).toBe('1');

    clearSignedOut();
    expect(isSignedOut()).toBe(false);
  });

  // Private/incognito windows throw on sessionStorage. The gate must fail OPEN:
  // a browser that cannot remember the flag should behave like the old code, not
  // trap the user on the sign-in screen with no way past it.
  it('fails open when sessionStorage throws, never stuck', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('private mode');
    });
    expect(isSignedOut()).toBe(false);
  });

  it('does not throw when the gate cannot be written', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('private mode');
    });
    expect(() => markSignedOut()).not.toThrow();
  });

  it('does not throw when the gate cannot be cleared', () => {
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
      throw new Error('private mode');
    });
    expect(() => clearSignedOut()).not.toThrow();
  });
});
