/**
 * The induction is for NEW JOINERS — exactly the people who are not on the
 * five-person HRIS whitelist. The /fabe gate (20-Sep-2026) found the backend
 * correctly open (IsAuthenticated, no _gate) while the HRIS layout still showed
 * them the red "HRIS is restricted" wall, because the route was missing from
 * SELF_SERVICE_ROUTES. The feature was unreachable by every person it was built
 * for, and no test said so.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const read = (p: string) => readFileSync(join(process.cwd(), p), 'utf8')

describe('induction is reachable by an ordinary employee', () => {
  it('is on the HRIS self-service route list', () => {
    const layout = read('src/app/(dashboard)/hris/layout.tsx')
    const line = layout.split('\n').find((l) => l.includes('SELF_SERVICE_ROUTES = ['))
    expect(line, 'SELF_SERVICE_ROUTES not found').toBeTruthy()
    expect(line).toContain("'/hris/induction'")
  })

  it('does NOT put the HR-only Training Academy on that list', () => {
    const layout = read('src/app/(dashboard)/hris/layout.tsx')
    const line = layout.split('\n').find((l) => l.includes('SELF_SERVICE_ROUTES = ['))
    expect(line).not.toContain("'/hris/training'")
  })

  it('is in the SELF-SERVICE nav array, not only the HR-gated one', () => {
    // Round 1 of this test grepped the whole file and passed while the only
    // menu entry sat in hrisGroup, which renders only for the five-person HRIS
    // whitelist. Opening the route without a menu entry is half a fix.
    const nav = read('src/lib/navModules.ts')
    const start = nav.indexOf('const hrisSelfServiceItems')
    expect(start, 'hrisSelfServiceItems not found').toBeGreaterThan(-1)
    const block = nav.slice(start, nav.indexOf('\n]', start))
    expect(block).toContain("href: '/hris/induction'")
  })

  it('keeps the HR-only Training Academy out of the self-service array', () => {
    const nav = read('src/lib/navModules.ts')
    const start = nav.indexOf('const hrisSelfServiceItems')
    const block = nav.slice(start, nav.indexOf('\n]', start))
    expect(block).not.toContain("href: '/hris/training'")
  })
})
