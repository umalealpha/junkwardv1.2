import { describe, it, expect } from 'vitest'
import { nextStepSyncAction, stepSyncIntentUrl, isCodeLive, shouldReportNoHandoff, HANDOFF_GRACE_MS, NEXUS_PACKAGE } from '../stepSync'

const PLAY = 'https://play.google.com/store/apps/details?id=' + NEXUS_PACKAGE
const live = { code: 'ABC123', expiresAt: 10_000 }
const dead = { code: 'ABC123', expiresAt: 1_000 }

describe('stepSyncIntentUrl', () => {
  it('targets the native pairing activity in our own package', () => {
    const url = stepSyncIntentUrl('ABC123', PLAY)
    expect(url.startsWith('intent://pair?code=ABC123#Intent;')).toBe(true)
    expect(url).toContain('scheme=alphanexus')
    expect(url).toContain(`package=${NEXUS_PACKAGE}`)
    expect(url.endsWith(';end')).toBe(true)
  })

  it('carries a Play fallback so a phone without the app is not stranded', () => {
    expect(stepSyncIntentUrl('X', PLAY)).toContain(
      `S.browser_fallback_url=${encodeURIComponent(PLAY)}`)
  })

  it('escapes the pair code instead of splicing it in raw', () => {
    expect(stepSyncIntentUrl('a b&c', PLAY)).toContain('code=a%20b%26c')
  })
})

describe('nextStepSyncAction', () => {
  it('tap 1 on Android fetches a code', () => {
    expect(nextStepSyncAction({ isAndroid: true, isAppleShell: false, pairCode: null }))
      .toBe('fetchCode')
  })

  // THE REGRESSION GUARD. If this ever returns 'fetchCode' while a live code
  // is held, the intent goes out after an await again and the button dies.
  it('tap 2 opens the app with NO network call, so activation survives', () => {
    expect(nextStepSyncAction({ isAndroid: true, isAppleShell: false, pairCode: live, now: 5_000 }))
      .toBe('openApp')
  })

  it('re-fetches once the server has expired the code', () => {
    expect(nextStepSyncAction({ isAndroid: true, isAppleShell: false, pairCode: dead, now: 5_000 }))
      .toBe('fetchCode')
  })

  it('the packaged Apple shell never touches the Android intent path', () => {
    expect(nextStepSyncAction({ isAndroid: false, isAppleShell: true, pairCode: live, now: 0 }))
      .toBe('apple')
  })

  it('a plain browser is told plainly, not left silent', () => {
    expect(nextStepSyncAction({ isAndroid: false, isAppleShell: false, pairCode: null }))
      .toBe('unsupported')
  })
})

describe('isCodeLive', () => {
  it('is false with no code, at the boundary, and after it', () => {
    expect(isCodeLive(null)).toBe(false)
    expect(isCodeLive(live, 10_000)).toBe(false)
    expect(isCodeLive(live, 10_001)).toBe(false)
    expect(isCodeLive(live, 9_999)).toBe(true)
  })
})

describe('shouldReportNoHandoff', () => {
  // THE REGRESSION GUARD for the wrong-information fault Fable caught: a member
  // whose sync SUCCEEDED must never be told the app did not open, and must
  // never be re-routed to Google Play.
  it('stays quiet while the app is in front', () => {
    expect(shouldReportNoHandoff({ hidden: true, elapsedMs: 2500 })).toBe(false)
  })

  it('stays quiet when the timer was suspended and fired late', () => {
    expect(shouldReportNoHandoff({ hidden: false, elapsedMs: 30_000 })).toBe(false)
  })

  it('speaks up when the page never went away', () => {
    expect(shouldReportNoHandoff({ hidden: false, elapsedMs: 2500 })).toBe(true)
  })

  it('treats the grace boundary as on time', () => {
    expect(shouldReportNoHandoff({ hidden: false, elapsedMs: HANDOFF_GRACE_MS })).toBe(true)
    expect(shouldReportNoHandoff({ hidden: false, elapsedMs: HANDOFF_GRACE_MS + 1 })).toBe(false)
  })
})
