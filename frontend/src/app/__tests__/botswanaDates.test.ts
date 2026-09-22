/**
 * No screen may turn a Date into a "YYYY-MM-DD" through UTC.
 *
 * `new Date().toISOString().slice(0, 10)` is the browser's UTC date, not the
 * user's. Botswana is UTC+2, so from 00:00 to 02:00 Gaborone it hands back
 * YESTERDAY — and every screen that pre-fills a date field, stamps a filename
 * or sends a date to the server was doing exactly that. 65 places across 56
 * files, found 9 Sep 2026.
 *
 * It never looks like a bug. A leave application opened at 00:30 defaults to
 * yesterday; a purchase order raised at 01:00 is dated the day before; an
 * export is named for the wrong day. Nobody reports it because nobody is
 * looking at the clock, and by 02:00 it has fixed itself.
 *
 * Same family of defect as `bonu/test_cases_timezone.py` and
 * `bonu/test_botswana_time.py` on the server side — this is the browser half.
 *
 * The fix everywhere is `localYmd(d)` from `@/lib/utils`, which reads
 * getFullYear/getMonth/getDate — the calendar date the person is actually
 * looking at.
 *
 * RED-FIRST: put the pattern back in any screen and this test names the file
 * and the line.
 */
import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative, sep } from 'node:path'

const SRC = join(process.cwd(), 'src')

const BAD = /\.toISOString\(\)\.(?:slice\(0,\s*10\)|split\('T'\)\[0\])/

/**
 * The four places UTC is DELIBERATE and right. Each one is listed with why,
 * because an allowlist nobody can audit is just a switched-off test.
 */
const ALLOWED: Record<string, string> = {
  // localYmd's own docstring quotes the bad pattern in order to explain it.
  'lib/utils.ts': 'the doc comment on localYmd itself',
  // Built with Date.UTC on purpose — the last day of a month, computed in UTC
  // and read back in UTC. Converting it would introduce the shift, not remove it.
  'app/(dashboard)/health/dashboard/page.tsx': 'Date.UTC round-trip',
  // Excel serial numbers are epoch-days measured in UTC. Reading an imported
  // cell back through local time moves every date in the file by one day.
  'app/(dashboard)/quick-entry/page.tsx': 'Excel serial-date import',
  'app/(dashboard)/salvage/inventory/import/page.tsx': 'Excel serial-date import',
  // This test names the pattern in order to look for it.
  'app/__tests__/botswanaDates.test.ts': 'the guard itself',
}

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name)
    if (statSync(full).isDirectory()) walk(full, out)
    else if (/\.tsx?$/.test(name)) out.push(full)
  }
  return out
}

describe('dates are Botswana dates, not UTC dates', () => {
  it('no screen builds a date string through toISOString()', () => {
    const offenders: string[] = []
    for (const file of walk(SRC)) {
      const rel = relative(SRC, file).split(sep).join('/')
      if (rel in ALLOWED) continue
      const lines = readFileSync(file, 'utf8').split('\n')
      lines.forEach((line, i) => {
        if (BAD.test(line)) offenders.push(`${rel}:${i + 1}  ${line.trim()}`)
      })
    }
    expect(
      offenders,
      `Use localYmd(d) from '@/lib/utils' instead — toISOString() gives the ` +
        `UTC date, which is yesterday between midnight and 02:00 in Gaborone:` +
        `\n\n${offenders.join('\n')}\n`,
    ).toEqual([])
  })

  it('every allowlisted file still exists and still holds the pattern', () => {
    // An allowlist that outlives its reason is how a guard quietly dies. If a
    // file is cleaned up or moved, its entry must go too.
    const stale: string[] = []
    for (const [rel, why] of Object.entries(ALLOWED)) {
      let text: string
      try {
        text = readFileSync(join(SRC, rel), 'utf8')
      } catch {
        stale.push(`${rel} — gone, but still allowlisted (${why})`)
        continue
      }
      if (!BAD.test(text)) stale.push(`${rel} — already fixed, drop it (${why})`)
    }
    expect(stale, `Stale entries in ALLOWED:\n${stale.join('\n')}`).toEqual([])
  })
})
