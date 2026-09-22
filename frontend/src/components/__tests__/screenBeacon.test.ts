/**
 * The bug this pins: a stale Nexus token silencing the desktop beacon.
 *
 * Measured on prod 2026-09-09 — 31 of 32 beacons in 24h came back 401 because
 * authHeader() always preferred the rewards token, which any member of staff
 * who had opened the rewards area still had in localStorage. 108 of 156 staff
 * looked like they had never opened Omni.
 */
import { beforeEach, describe, expect, it } from 'vitest'
import { authHeaders } from '../ScreenBeacon'

const store: Record<string, string> = {}
beforeEach(() => {
  for (const k of Object.keys(store)) delete store[k]
  ;(globalThis as any).localStorage = {
    getItem: (k: string) => (k in store ? store[k] : null),
    setItem: (k: string, v: string) => { store[k] = v },
  }
})

describe('authHeaders', () => {
  it('THE BUG: a leftover rewards token must not outrank the desktop token', () => {
    store['alpha_rewards_token'] = 'nexus-stale'
    store['alpha_token'] = 'desktop-good'
    expect(authHeaders('desktop')[0]).toBe('Token desktop-good')
  })

  it('each surface leads with its own credential', () => {
    store['alpha_token'] = 'd'
    store['omni_app_token'] = 'a'
    store['alpha_rewards_token'] = 'r'
    expect(authHeaders('desktop')[0]).toBe('Token d')
    expect(authHeaders('app')[0]).toBe('Bearer a')
    expect(authHeaders('m')[0]).toBe('Bearer r')
  })

  it('the others stay available as fallbacks, so a 401 can retry', () => {
    store['alpha_token'] = 'd'
    store['omni_app_token'] = 'a'
    expect(authHeaders('desktop')).toEqual(['Token d', 'Bearer a'])
  })

  it('uses the DRF Token scheme for the desktop key and Bearer for the others', () => {
    store['alpha_token'] = 'd'
    expect(authHeaders('app')).toEqual(['Token d'])
    store['omni_app_token'] = 'a'
    expect(authHeaders('app')[0]).toBe('Bearer a')
  })

  it('signed out means no beacon at all, never an unauthenticated POST', () => {
    expect(authHeaders('desktop')).toEqual([])
  })

  it('storage blocked in private mode is survivable', () => {
    ;(globalThis as any).localStorage = {
      getItem() { throw new Error('blocked') },
    }
    expect(authHeaders('desktop')).toEqual([])
  })
})
