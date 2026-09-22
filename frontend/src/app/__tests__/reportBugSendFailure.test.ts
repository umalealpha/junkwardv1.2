/**
 * What a failed send tells the person who just lost their work.
 *
 * Oratile Tlhomelang filed a feature request on 2026-09-08 with ten
 * attachments — two Word briefs, a spreadsheet and seven screenshots — and all
 * the screen said was **"Failed to fetch"**. Her own screenshots are stamped
 * 15:01 and 15:02, the two minutes a deploy was replacing the frontend
 * underneath her, and the "A new version of Omni is ready" bar is visible in
 * the same shot. The backend was never the problem: the identical ten files,
 * 1.92 MB, post in 2.5 seconds and email fine.
 *
 * So the fault was the message, not the upload. "Failed to fetch" is the
 * browser's phrase for "the request never arrived" and gives the reader
 * nothing to do. These tests pin that we name the likely cause and the one
 * button that fixes it, in words that would survive being read on a phone.
 *
 * The helper lives in sendFailure.ts, not in page.tsx: a Next.js App Router
 * page may only export a component and its known fields, so a named export
 * there fails the production build. `tsc --noEmit` does not check that rule —
 * only `next build` does, which is how it got past the first review.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

import { sendFailureMessage } from '../(dashboard)/report-bug/sendFailure'

const pageSource = readFileSync(
  join(__dirname, '..', '(dashboard)', 'report-bug', 'page.tsx'), 'utf8')

describe('report-bug — the message when a send fails', () => {
  it('never shows the browser’s raw "Failed to fetch" to a person', () => {
    const msg = sendFailureMessage(new TypeError('Failed to fetch'))
    expect(msg).not.toMatch(/failed to fetch/i)
  })

  it('names the likely cause — Omni updated mid-form', () => {
    const msg = sendFailureMessage(new TypeError('Failed to fetch'))
    expect(msg).toMatch(/updated while you were typing/i)
  })

  it('says the work is not lost, because it is not', () => {
    const msg = sendFailureMessage(new TypeError('Failed to fetch'))
    expect(msg).toMatch(/nothing is lost/i)
    expect(msg).toMatch(/still on this page/i)
  })

  it('points at the one button that fixes it', () => {
    const msg = sendFailureMessage(new TypeError('Failed to fetch'))
    expect(msg).toMatch(/refresh now/i)
  })

  it('covers Safari and the other browsers’ wording for the same failure', () => {
    for (const raw of ['Load failed', 'NetworkError when attempting to fetch resource',
                       'Network request failed']) {
      expect(sendFailureMessage(new TypeError(raw))).toMatch(/updated while you were typing/i)
    }
  })

  it('still shows a real server message when the server actually answered', () => {
    // A 400 from the backend carries words worth reading — do not bury it.
    const msg = sendFailureMessage(
      new Error('"installer.exe" is not an image or a document'))
    expect(msg).toMatch(/installer\.exe/)
    expect(msg).not.toMatch(/updated while you were typing/i)
  })

  it('falls back to something useful when there is no message at all', () => {
    const msg = sendFailureMessage(null)
    expect(msg).toMatch(/still on this page/i)
  })
})

describe('report-bug — the message is actually WIRED to the form', () => {
  it('the submit handler routes its failure through sendFailureMessage', () => {
    // Testing the helper alone proves nothing: the helper can be perfect and
    // still not be connected, which is exactly how the raw browser error
    // reached Oratile. This asserts the catch block uses it.
    expect(pageSource).toMatch(/catch\s*\([^)]*\)\s*\{\s*\n\s*setError\(sendFailureMessage\(/)
  })

  it('the raw browser message is no longer passed straight to the screen', () => {
    expect(pageSource).not.toMatch(/setError\(\s*e\?\.message/)
  })
})
