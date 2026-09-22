/**
 * The company has to travel with a supplier-list upload.
 *
 * apiFetch adds the chosen company to GET requests only. These uploads are
 * POSTs, so they carry it themselves. Getting this wrong is not a visible bug:
 * the server would fall back to nothing and refuse, or — before that fallback
 * was removed — silently stamp the person's default company, so an ADSA list
 * would land on ADIC and report success.
 *
 * Fable's review found this exact hole: the endpoint tests appended ?company=
 * by hand, so they were green over a request the browser never sent.
 */
import { describe, expect, it, beforeEach, vi } from 'vitest'
import { withChosenCompany } from '../api'

const store: Record<string, string> = {}

beforeEach(() => {
  for (const k of Object.keys(store)) delete store[k]
  vi.stubGlobal('localStorage', {
    getItem: (k: string) => store[k] ?? null,
    setItem: (k: string, v: string) => { store[k] = v },
    removeItem: (k: string) => { delete store[k] },
    clear: () => { for (const k of Object.keys(store)) delete store[k] },
    key: () => null,
    length: 0,
  })
})

describe('withChosenCompany', () => {
  it('adds the company the person picked in the topbar', () => {
    store['alpha_company_id'] = 'abc-123'
    expect(withChosenCompany('/vendor-bank-accounts/upload/load/'))
      .toBe('/vendor-bank-accounts/upload/load/?company=abc-123')
  })

  it('joins onto a path that already has a query', () => {
    store['alpha_company_id'] = 'abc-123'
    expect(withChosenCompany('/x/?page=2')).toBe('/x/?page=2&company=abc-123')
  })

  it('leaves a company already on the path alone', () => {
    store['alpha_company_id'] = 'abc-123'
    expect(withChosenCompany('/x/?company=other')).toBe('/x/?company=other')
  })

  it('changes nothing when no company is chosen', () => {
    expect(withChosenCompany('/x/')).toBe('/x/')
  })

  it('escapes the value rather than pasting it in raw', () => {
    store['alpha_company_id'] = 'a b&c'
    expect(withChosenCompany('/x/')).toBe('/x/?company=a%20b%26c')
  })
})
