/**
 * hrisRoutePrefix.test.ts — /hris/api/* must never be called through API_BASE.
 *
 * The HRIS endpoints are mounted at the site root (alpha_finance/urls.py:
 * path('hris/', include('hris.urls'))), NOT under /api/v1. API_BASE is
 * `${BASE_URL}/api/v1`, so `fetch(`${API_BASE}/hris/api/x/`)` asks the server
 * for /api/v1/hris/api/x/ and gets a 404 — forever, in production, with no
 * error anyone sees.
 *
 * It shipped that way in LateNoticeButton.tsx: the "I'm running late" tile
 * never rendered and the notice was never filed, which also starved rule 1b of
 * the performance engine. Found 2026-09-10 in the live access log, not by any
 * test — the same wrong-prefix class as CF-404-01 (the market benchmark).
 *
 * This walks the real source tree, so a new component with the same mistake
 * fails here instead of on someone's screen.
 */
import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

const SRC = join(__dirname, '..')

function sourceFiles(dir: string): string[] {
  const out: string[] = []
  for (const entry of readdirSync(dir)) {
    if (entry === 'node_modules' || entry === '__tests__') continue
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) out.push(...sourceFiles(full))
    else if (/\.tsx?$/.test(entry)) out.push(full)
  }
  return out
}

// Only the /hris/api/* family is root-mounted (hris/urls.py). Some hris
// endpoints ARE registered under /api/v1 — e.g. /api/v1/hris/documents/ and
// /api/v1/hris/unlock/ in alpha_finance/api_router.py — so those must keep
// their API_BASE prefix. This matches `${API_BASE}/hris/api/...` only.
const BAD = /(?:\$\{\s*API_BASE\s*\}|API_BASE\s*\+\s*['"`])\s*\/?hris\/api\//

describe('HRIS endpoints are root-relative', () => {
  it('no source file prefixes /hris/ with API_BASE', () => {
    const offenders: string[] = []
    for (const file of sourceFiles(SRC)) {
      const lines = readFileSync(file, 'utf8').split('\n')
      lines.forEach((line, i) => {
        const code = line.trim()
        if (code.startsWith('//') || code.startsWith('*')) return
        if (BAD.test(line)) offenders.push(`${file.slice(SRC.length + 1)}:${i + 1}`)
      })
    }
    expect(offenders).toEqual([])
  })
})
