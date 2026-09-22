/**
 * cacheHeaders.test.ts
 *
 * Pins the one rule that decides whether an already-open Omni sees a new
 * deploy — and, just as importantly, that it does NOT stop caching the things
 * that make Omni fast.
 *
 * Why this exists (CFO 2026-09-09): next.config.ts carried a hand-kept
 * ALLOW-LIST of routes marked `no-store`, and every entry had been added AFTER
 * somebody reported "my change isn't showing" — the config's own comments record
 * Pako and Kago chasing an invisible GL banner. Anything nobody had complained
 * about yet kept Next's default for a statically-prerendered page,
 * `Cache-Control: s-maxage=31536000`: a ONE-YEAR shared-cache licence on an
 * authenticated ERP page. Measured on prod that day, /app and /app/work (the
 * whole staff phone app), /my-omni, /approvals, /payroll/payslips,
 * /banking/realpay and /m all served that, while /dashboard, /hris/* and
 * /reports/* were correctly no-store. The installed phone and desktop apps are
 * long-lived and cache hard, so they kept showing the old build while the web
 * asked for a refresh.
 *
 * The fix replaced the list with ONE pattern. The lookahead is load-bearing in
 * BOTH directions, which is what this test guards:
 *   - every HTML page must match, so no new route can ever be forgotten again;
 *   - nothing under /_next/ and no file with an extension may match. public/
 *     holds a 32 MB AlphaNexus.apk, a 17 MB aria-rat-3d.glb and 4 MB brand
 *     PNGs, and /_next/static holds the hashed bundle. No-storing those would
 *     re-download them constantly for all 126 staff — far worse than the bug
 *     being fixed.
 *
 * It compiles the REAL exported pattern with Next's OWN matcher, so a drift
 * between this test and the server cannot hide.
 */
import { describe, expect, it } from 'vitest'
import { pathToRegexp } from 'next/dist/compiled/path-to-regexp'

import { NO_STORE_PAGES } from '../../../next.config'

// Pages: an authenticated HTML document. None of these may ever be stored.
const MUST_BE_NO_STORE = [
  '/app',                          // the staff phone app shell
  '/app/work',
  '/app/purchase-orders/123',
  '/app/health-quote',
  '/my-omni',                      // where bug c82def7f was reported from
  '/approvals',
  '/payroll/payslips',
  '/banking/realpay',
  '/m',                            // Nexus customer app
  '/m/staff/approvals',
  '/claims',
  '/dashboard',
  '/compliance/ropa',
  '/compliance/policy-library',
  '/hris/leave',
  '/reports/entity-pl',
  '/login',
  '/staff-login',
  '/cfo-snapshot',
  '/health/quick-quote',
]

// Build output, public files and backend proxies. These MUST stay cacheable.
const MUST_STAY_CACHEABLE = [
  '/_next/static/chunks/main-abc123.js',
  '/_next/static/css/a.css',
  '/_next/static/media/f.woff2',
  '/_next/image',
  '/api/v1/users/',
  '/api-token-auth/',
  '/trpc/x',
  '/AlphaNexus.apk',               // 32 MB
  '/Omni.apk',
  '/aria-rat-3d.glb',              // 17 MB
  '/brand/icon-login-3d.png',      // 4 MB
  '/icon-512.png',
  '/favicon.ico',
  '/apple-touch-icon.png',
  '/app.webmanifest',
  '/app-sw.js',
  '/sw.js',
  '/m/sw.js',
  '/build-id.txt',                 // has its own explicit no-store rule
  '/helpdesk/index.html',
  '/aria-face.html',
]

describe('the no-store page rule in next.config.ts', () => {
  const re = pathToRegexp(NO_STORE_PAGES) as RegExp

  it.each(MUST_BE_NO_STORE)('marks the page %s as never-store', (path) => {
    expect(
      re.test(path),
      `${path} would keep Next's s-maxage=31536000 default — an open phone or ` +
      `desktop app could serve a year-old copy of it after a deploy`,
    ).toBe(true)
  })

  it.each(MUST_STAY_CACHEABLE)('leaves %s cacheable', (path) => {
    expect(
      re.test(path),
      `${path} would be marked no-store and re-downloaded on every page load — ` +
      `public/ holds files up to 32 MB, so this would make Omni markedly slower ` +
      `for everyone`,
    ).toBe(false)
  })

  it('excludes the hashed build output, which is what keeps Omni fast', () => {
    // Stated separately from the table above because it is THE constraint: the
    // bundle is content-addressed per build, so it is safe to cache for ever and
    // must be.
    expect(re.test('/_next/static/chunks/app/layout-deadbeef.js')).toBe(false)
  })

  it('covers a route nobody has thought of yet', () => {
    // The whole point of replacing the allow-list: a page added next month is
    // covered without anyone remembering to add it.
    expect(re.test('/some/module/nobody/has/built/yet')).toBe(true)
  })
})
