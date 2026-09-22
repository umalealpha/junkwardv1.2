/**
 * The intro gate. Two failure modes it guards against, neither visible to the
 * type checker: the intro network
 * honoured), and the intro playing in front of a store reviewer or the
 * read-only QC robot, whose screenshots would then report the app as stuck.
 */
import { describe, expect, it } from 'vitest'

import { shouldPlayIntro } from '../introGate'

const fresh = { alreadyShown: false, qaReadOnly: false }

describe('shouldPlayIntro — once per app open, for real staff only', () => {
  it('plays on the home screen the first time the app is opened', () => {
    expect(shouldPlayIntro('/app', fresh)).toBe(true)
  })

  it('plays before sign-in too, so the launch is always branded', () => {
    expect(shouldPlayIntro('/app/login', fresh)).toBe(true)
  })

  it('does not play again once it has been shown this session', () => {
    expect(shouldPlayIntro('/app', { ...fresh, alreadyShown: true })).toBe(false)
  })

  it('never plays for the read-only quality-check identity', () => {
    expect(shouldPlayIntro('/app', { ...fresh, qaReadOnly: true })).toBe(false)
  })

  it('never plays on the store-reviewer pages, trailing slash or not', () => {
    expect(shouldPlayIntro('/app/privacy', fresh)).toBe(false)
    expect(shouldPlayIntro('/app/support/', fresh)).toBe(false)
    expect(shouldPlayIntro('/app/delete-account', fresh)).toBe(false)
  })

  it('treats a missing pathname as the home screen', () => {
    expect(shouldPlayIntro(null, fresh)).toBe(true)
  })
})
