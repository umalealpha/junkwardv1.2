/**
 * The two store-required public pages, and the install card that must not
 * appear inside a store build.
 *
 * Both stores open these URLs with no Alpha Direct account. If either one is
 * dropped from the public set the shell silently redirects the reviewer to
 * /app/login and the submission is rejected — a failure neither the type
 * checker nor the backend can see.
 */
import { afterEach, describe, expect, it } from 'vitest'

import { isPublicAppRoute } from '../publicRoutes'
import { isInAppShell } from '../ui'

describe('isPublicAppRoute — the store reviewer has no account', () => {
  it('lets a signed-out visitor read the privacy notice', () => {
    expect(isPublicAppRoute('/app/privacy')).toBe(true)
  })

  it('lets a signed-out visitor read the support page', () => {
    expect(isPublicAppRoute('/app/support')).toBe(true)
  })

  it('still lets a signed-out visitor reach sign-in', () => {
    expect(isPublicAppRoute('/app/login')).toBe(true)
  })

  it('ignores a trailing slash, which a store listing often carries', () => {
    expect(isPublicAppRoute('/app/privacy/')).toBe(true)
    expect(isPublicAppRoute('/app/support/')).toBe(true)
  })

  // The other half of the guard: opening the app up must not open the app.
  it('keeps the home screen behind sign-in', () => {
    expect(isPublicAppRoute('/app')).toBe(false)
  })

  it('keeps payslips behind sign-in', () => {
    expect(isPublicAppRoute('/app/payslips')).toBe(false)
  })

  it('does not treat a look-alike path as public', () => {
    expect(isPublicAppRoute('/app/privacy-settings')).toBe(false)
  })

  it('treats a missing pathname as the home screen, not as public', () => {
    expect(isPublicAppRoute(null)).toBe(false)
  })
})

describe('isInAppShell — hide "Add to Home Screen" inside a store build', () => {
  const set = (key: string, value: unknown) =>
    Object.defineProperty(key === 'referrer' ? document : window.navigator, key,
      { value, configurable: true })

  afterEach(() => {
    set('referrer', '')
    set('userAgent', BROWSER_UA)
    set('standalone', undefined)
  })

  // A plain mobile browser tab: the install instruction is correct there.
  it('is false in an ordinary browser tab', () => {
    set('userAgent', BROWSER_UA)
    set('referrer', 'https://omni.alphadirect.co.bw/')
    expect(isInAppShell()).toBe(false)
  })

  // Apple rejects a listing that points at another platform's install flow, so
  // the Android wrapper — which loads /app with an android-app:// referrer —
  // must be recognised as an installed app, not a browser.
  it('is true inside the Android store wrapper (android-app:// referrer)', () => {
    set('userAgent', BROWSER_UA)
    set('referrer', 'android-app://bw.co.alphadirect.omni')
    expect(isInAppShell()).toBe(true)
  })

  it('is true for an installed PWA on iOS (navigator.standalone)', () => {
    set('userAgent', BROWSER_UA)
    set('standalone', true)
    expect(isInAppShell()).toBe(true)
  })
})

const BROWSER_UA =
  'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 ' +
  '(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1'
