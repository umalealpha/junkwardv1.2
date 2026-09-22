/**
 * The FNB bulk-payment file: the endpoint shipped with NO caller at all, so
 * Finance could not reach it and kept building the CSV by hand (CFO 2026-09-15).
 *
 * Three things must hold, and each one is a real failure we have already had:
 *  1. the request goes to the route the server actually registered
 *     (`v1-payment-request-fnb-file`), or the button downloads nothing;
 *  2. the file is delivered through saveBlob, NOT a hand-rolled <a download> —
 *     the OmniDesktop WebView2 app silently drops a bare anchor (saveBlob.test);
 *  3. the server's own refusal (409 "no payment lines", 403 "not allowed") is
 *     what the user is told, instead of a blank download and a shrug.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { downloadPaymentRequestFnbFile } from '../api'

const REQ = '11111111-2222-3333-4444-555555555555'
const store: Record<string, string> = { token: 'test-token' }
let clicks: HTMLAnchorElement[]
const realClick = HTMLAnchorElement.prototype.click
const realFetch = global.fetch

function csvResponse() {
  return new Response('BInSol - U ver 1.00\n', {
    status: 200,
    headers: {
      'Content-Type': 'text/csv',
      'Content-Disposition': 'attachment; filename="Payment_CSV_Template_All - PR-0042.csv"',
    },
  })
}

beforeEach(() => {
  ;(URL as unknown as { createObjectURL: unknown }).createObjectURL = vi.fn(() => 'blob:mock')
  ;(URL as unknown as { revokeObjectURL: unknown }).revokeObjectURL = vi.fn()
  document.body.innerHTML = ''
  delete (window as unknown as { chrome?: unknown }).chrome
  clicks = []
  HTMLAnchorElement.prototype.click = function (this: HTMLAnchorElement) { clicks.push(this) }
  // jsdom here has no localStorage; api.ts reads the token from it.
  vi.stubGlobal('localStorage', {
    getItem: (k: string) => store[k] ?? null,
    setItem: (k: string, v: string) => { store[k] = v },
    removeItem: (k: string) => { delete store[k] },
    clear: () => { /* the token must survive — these tests never clear it */ },
    key: () => null,
    length: 0,
  })
})

afterEach(() => {
  HTMLAnchorElement.prototype.click = realClick
  global.fetch = realFetch
  vi.restoreAllMocks()
})

describe('downloadPaymentRequestFnbFile', () => {
  it('calls the route the server registered, and saves the file the server named', async () => {
    const seen: string[] = []
    global.fetch = vi.fn(async (input: RequestInfo | URL) => {
      seen.push(String(input))
      return csvResponse()
    }) as typeof fetch

    await downloadPaymentRequestFnbFile(REQ, 'PR-0042')

    expect(seen.length).toBe(1)
    expect(seen[0]).toContain(`/payment-requests/${REQ}/fnb-file/`)
    expect(clicks.length).toBe(1)
    expect(clicks[0].download).toBe('Payment_CSV_Template_All - PR-0042.csv')
  })

  it('tells the user the server’s own reason when it refuses', async () => {
    global.fetch = vi.fn(async () => new Response(
      JSON.stringify({ detail: 'There are no payment lines on this request.' }),
      { status: 409, headers: { 'Content-Type': 'application/json' } },
    )) as typeof fetch

    await expect(downloadPaymentRequestFnbFile(REQ, 'PR-0042'))
      .rejects.toThrow('There are no payment lines on this request.')
    expect(clicks.length).toBe(0)
  })
})
