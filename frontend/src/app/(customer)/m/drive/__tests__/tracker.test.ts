import { describe, it, expect, beforeEach, vi } from 'vitest'
import { TripTracker } from '../tracker'

/**
 * Guards the three ways Click & Drive silently refused to record a trip.
 *
 * The CFO reported it on a Galaxy S25 Ultra held to open sky (15-Aug-2026):
 * the screen showed "Could not read your location. Move to open sky and try
 * again." and dropped back to idle. The wrapper app was the main culprit (no
 * Android location permission), but the tracker also had to stop throwing a
 * live drive away on the first hiccup, and had to stop hiding behind a
 * message that pointed at the sky when the sky was never the problem.
 */

type SuccessCb = (p: GeolocationPosition) => void
type ErrorCb = (e: GeolocationPositionError) => void

const CODE = { PERMISSION_DENIED: 1, POSITION_UNAVAILABLE: 2, TIMEOUT: 3 } as const

let success: SuccessCb
let failure: ErrorCb
let lastOptions: PositionOptions | undefined

function fix(over: { accuracy?: number; t?: number; lat?: number; lng?: number; speed?: number | null }) {
  return {
    coords: {
      latitude: over.lat ?? -24.6282, longitude: over.lng ?? 25.9231,
      accuracy: over.accuracy ?? 8, speed: over.speed === undefined ? 0 : over.speed,
      altitude: null, altitudeAccuracy: null, heading: null,
    },
    timestamp: over.t ?? 1_000_000,
  } as unknown as GeolocationPosition
}

function err(code: 1 | 2 | 3) {
  return { code, PERMISSION_DENIED: 1, POSITION_UNAVAILABLE: 2, TIMEOUT: 3, message: '' } as unknown as GeolocationPositionError
}

beforeEach(() => {
  Object.defineProperty(globalThis.navigator, 'geolocation', {
    configurable: true,
    value: {
      watchPosition: (s: SuccessCb, e: ErrorCb, o?: PositionOptions) => { success = s; failure = e; lastOptions = o; return 1 },
      clearWatch: vi.fn(),
    },
  })
})

describe('TripTracker.start', () => {
  it('starts the trip on a COARSE first fix instead of hanging on "Getting your location…"', () => {
    const onUpdate = vi.fn(), onError = vi.fn(), onFirstFix = vi.fn()
    new TripTracker().start(onUpdate, onError, onFirstFix)

    // 120 m accuracy — a normal first fix while the GPS chip sharpens up.
    success(fix({ accuracy: 120 }))

    expect(onFirstFix).toHaveBeenCalledTimes(1)
    expect(onUpdate).toHaveBeenCalledTimes(1)
    expect(onError).not.toHaveBeenCalled()
  })

  it('never SCORES a coarse fix — jitter must not become distance', () => {
    const onUpdate = vi.fn(), onError = vi.fn()
    new TripTracker().start(onUpdate, onError)

    // Two coarse fixes ~450 m apart: pure cell-tower jitter, not a drive.
    success(fix({ accuracy: 400, t: 1_000_000, lat: -24.6282 }))
    success(fix({ accuracy: 400, t: 1_004_000, lat: -24.6322 }))

    const live = onUpdate.mock.calls.at(-1)![0]
    expect(live.distanceKm).toBe(0)
    expect(live.harshEvents).toBe(0)
  })

  it('keeps a live drive alive through a mid-trip GPS dropout', () => {
    const onUpdate = vi.fn(), onError = vi.fn()
    new TripTracker().start(onUpdate, onError)

    success(fix({ accuracy: 8, t: 1_000_000 }))   // trip is now live
    failure(err(CODE.TIMEOUT))                    // tunnel / underground parking
    failure(err(CODE.POSITION_UNAVAILABLE))

    // The old code surfaced this and page.tsx dropped the whole trip back to idle.
    expect(onError).not.toHaveBeenCalled()
  })

  it('reports a pre-fix failure with a message that names the real cause', () => {
    const cases: Array<[1 | 2 | 3, RegExp]> = [
      [CODE.PERMISSION_DENIED, /permission was blocked/i],
      [CODE.TIMEOUT, /open sky/i],
      [CODE.POSITION_UNAVAILABLE, /Location is switched on/i],
    ]
    for (const [code, expected] of cases) {
      const onError = vi.fn()
      new TripTracker().start(vi.fn(), onError)
      failure(err(code))
      expect(onError).toHaveBeenCalledTimes(1)
      expect(onError.mock.calls[0][0]).toMatch(expected)
    }
  })

  it('allows a cold GPS chip more than 20s to produce its first fix', () => {
    new TripTracker().start(vi.fn(), vi.fn())
    expect(lastOptions?.timeout ?? 0).toBeGreaterThan(20000)
    expect(lastOptions?.enableHighAccuracy).toBe(true)
  })
})
