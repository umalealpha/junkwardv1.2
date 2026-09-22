'use client'

import { createContext, useContext, useEffect, useState, useCallback } from 'react'
import { getCompanies, getToken } from '@/lib/api'
import type { Company } from '@/lib/api'

interface CompanyCtx {
  /** Currently-selected company id; null = "All companies" (global view). */
  selectedId: string | null
  /** Resolved Company object for the current selection (null when global). */
  selected: Company | null
  /** All known companies (loaded once). */
  companies: Company[]
  loaded: boolean
  /** Set the active subsidiary; pass null for "All". */
  setSelectedId: (id: string | null) => void
  /** Refresh the company list (e.g. after creating one). */
  reload: () => Promise<void>
  /** Convenience: append `company=<id>` to a URLSearchParams or object if selected. */
  withCompanyFilter: (params: Record<string, string>) => Record<string, string>
}

const Ctx = createContext<CompanyCtx | null>(null)

const STORAGE_KEY = 'alpha_company_id'
const COMPANIES_CACHE_KEY = 'alpha_companies_cache_v1'
const COMPANIES_CACHE_TTL_MS = 10 * 60 * 1000  // 10 minutes — list barely changes

function readCompaniesCache(): Company[] | null {
  if (typeof window === 'undefined') return null
  try {
    const raw = localStorage.getItem(COMPANIES_CACHE_KEY)
    if (!raw) return null
    const c = JSON.parse(raw) as { companies: Company[]; ts: number }
    if (Date.now() - c.ts > COMPANIES_CACHE_TTL_MS) return null
    return c.companies
  } catch { return null }
}

function writeCompaniesCache(companies: Company[]) {
  if (typeof window === 'undefined') return
  try {
    localStorage.setItem(COMPANIES_CACHE_KEY, JSON.stringify({ companies, ts: Date.now() }))
  } catch { /* quota / private mode — ignore */ }
}

// CFO directive 2026-05-15: every user logging in should land on ADIC by
// default. Hoisted to module scope so both the lazy state initializer (cache
// path) and reload() (network path) share the same priority list.
const PREFERRED_DEFAULT_CODES = ['ADIC', 'ADI']

function pickDefaultCompany(list: Company[]): Company | null {
  for (const code of PREFERRED_DEFAULT_CODES) {
    const found = list.find((c) => (c.code || '').toUpperCase() === code)
    if (found) return found
  }
  return null
}

export function CompanyProvider({ children }: { children: React.ReactNode }) {
  // Hydrate from cache so the topbar picker renders on first paint without a
  // network round-trip. We still kick off a background refresh below.
  const [companies, setCompanies] = useState<Company[]>(() => readCompaniesCache() ?? [])
  const [loaded, setLoaded] = useState<boolean>(() => readCompaniesCache() !== null)
  const [selectedId, setSelectedIdState] = useState<string | null>(() => {
    if (typeof window === 'undefined') return null
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored) return stored
    // No stored selection yet — try to seed from the cached company list
    // immediately so the first paint already reflects ADIC instead of "All
    // companies". The network reload() below will overwrite this if a
    // different cache state turns up, but in steady state it's a no-op.
    const cached = readCompaniesCache()
    if (cached) {
      const preferred = pickDefaultCompany(cached)
      if (preferred) {
        localStorage.setItem(STORAGE_KEY, preferred.id)
        return preferred.id
      }
    }
    return null
  })

  const setSelectedId = useCallback((id: string | null) => {
    setSelectedIdState(id)
    if (typeof window !== 'undefined') {
      if (id) localStorage.setItem(STORAGE_KEY, id)
      else localStorage.removeItem(STORAGE_KEY)
      // Tell every page that data needs refetching against the new company.
      // The dashboard listens for this and reloads in place; other pages
      // pick up the new filter automatically on next interaction because
      // apiFetch reads selectedId from localStorage on every call.
      window.dispatchEvent(new CustomEvent('alpha-company-changed', { detail: { companyId: id } }))
    }
  }, [])

  const reload = useCallback(async () => {
    if (!getToken()) return
    try {
      const res = await getCompanies({ is_active: 'true' })
      setCompanies(res.results)
      writeCompaniesCache(res.results)
      setLoaded(true)

      // CFO directive 2026-05-15: default the company switcher to ADIC on
      // first load. The "All Companies" consolidated view mixes 11 entities
      // and never matches the MA workbook standalone — surfacing ADIC by
      // default keeps the dashboard apples-to-apples with the MA pack.
      if (typeof window !== 'undefined' && !localStorage.getItem(STORAGE_KEY)) {
        const preferred = pickDefaultCompany(res.results)
        if (preferred) {
          setSelectedIdState(preferred.id)
          localStorage.setItem(STORAGE_KEY, preferred.id)
          // No 'alpha-company-changed' event — this is the initial seed, not
          // a user-driven switch, so we don't want to trigger a reload race.
        }
      }
    } catch { setLoaded(true) }
  }, [])

  useEffect(() => { reload() }, [reload])

  const selected = selectedId ? companies.find((c) => c.id === selectedId) || null : null

  const withCompanyFilter = useCallback((params: Record<string, string>) => {
    if (selectedId) return { ...params, company: selectedId }
    return params
  }, [selectedId])

  return (
    <Ctx.Provider value={{ selectedId, selected, companies, loaded, setSelectedId, reload, withCompanyFilter }}>
      {children}
    </Ctx.Provider>
  )
}

export function useCompany() {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error('useCompany must be used inside CompanyProvider')
  return ctx
}
