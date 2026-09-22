/**
 * saveBlob delivers a file two ways, and the split must never regress.
 *
 * Ordinary browsers get a hidden <a download> anchor. The OmniDesktop Windows
 * app (WebView2) silently drops that anchor — the host wrapper does not handle
 * WebView2's DownloadStarting — so a staff bug report (2026-08-27) saw payslip
 * PDFs fetched 200-OK but never saved. saveBlob now detects the WebView2 host
 * (window.chrome.webview, undefined in every normal browser) and renders
 * viewable files (PDF/image) in an in-app overlay instead.
 *
 * These tests pin BOTH branches: the browser path stays exactly the anchor
 * download (the no-regression claim), and WebView2 gets the overlay for
 * viewable files while still using the anchor for everything else.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { saveBlob } from '../api'

let clicks: HTMLAnchorElement[]
const realClick = HTMLAnchorElement.prototype.click

beforeEach(() => {
  // jsdom implements neither of these — stub them so saveBlob can run.
  ;(URL as unknown as { createObjectURL: unknown }).createObjectURL = vi.fn(() => 'blob:mock')
  ;(URL as unknown as { revokeObjectURL: unknown }).revokeObjectURL = vi.fn()
  document.body.innerHTML = ''
  delete (window as unknown as { chrome?: unknown }).chrome
  clicks = []
  HTMLAnchorElement.prototype.click = function (this: HTMLAnchorElement) { clicks.push(this) }
})

afterEach(() => {
  HTMLAnchorElement.prototype.click = realClick
})

describe('saveBlob', () => {
  it('normal browser: saves via a download anchor, no overlay', () => {
    saveBlob(new Blob(['x'], { type: 'application/pdf' }), 'payslip-2026-08.pdf')
    expect(clicks.length).toBe(1)
    expect(clicks[0].download).toBe('payslip-2026-08.pdf')
    expect(document.querySelector('[role="dialog"]')).toBeNull()
  })

  it('OmniDesktop (WebView2): a PDF opens in an in-app viewer, not an anchor download', () => {
    ;(window as unknown as { chrome?: unknown }).chrome = { webview: {} }
    saveBlob(new Blob(['x'], { type: 'application/pdf' }), 'payslip-2026-08.pdf')
    expect(clicks.length).toBe(0)
    const dialog = document.querySelector('[role="dialog"]')
    expect(dialog).not.toBeNull()
    expect(dialog?.querySelector('iframe')).not.toBeNull()
  })

  it('OmniDesktop (WebView2): a non-viewable file still uses the anchor download', () => {
    ;(window as unknown as { chrome?: unknown }).chrome = { webview: {} }
    saveBlob(new Blob(['a,b'], { type: 'text/csv' }), 'report.csv')
    expect(clicks.length).toBe(1)
    expect(document.querySelector('[role="dialog"]')).toBeNull()
  })
})
