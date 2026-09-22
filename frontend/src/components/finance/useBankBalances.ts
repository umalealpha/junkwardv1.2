'use client'

/**
 * The one fetch for the Morning Bank Balances screens.
 *
 * Access is decided by the SERVER: `/banking/balances/` carries
 * CanViewFinancials and answers 403 to everyone else, exactly as the CFO
 * control band does (see components/CfoControlsSection.tsx). `forbidden` lets a
 * caller self-hide rather than advertise a screen that 403s. A client-side role
 * guess is never used in its place — guessing the rule is how a screen ends up
 * hidden from the CFO himself.
 *
 * There is no fixture fallback. If the call fails the screens say so; they
 * never substitute an invented balance.
 */
import { useCallback, useEffect, useState } from 'react'
import { getBankBalances } from '@/lib/api'
import type { BankBalances } from '@/lib/bankBalances'
import { mockScenarios } from '@/lib/bankBalancesMock'

export interface BankBalancesState {
  data: BankBalances | null
  loading: boolean
  /** A plain sentence for the viewer, or null. */
  error: string | null
  /** True when the server refused — the caller should render nothing. */
  forbidden: boolean
  reload: () => void
}

/**
 * Development-only preview.
 * `?mock=mixed|healthy|troubled|none` renders a
 * fixture so the failure states can be looked at before the endpoint is live.
 * `process.env.NODE_ENV` is inlined at build time, so the production bundle
 * drops this branch and the fixture module with it.
 */
function devMock(): BankBalances | null {
  if (process.env.NODE_ENV !== 'development') return null
  if (typeof window === 'undefined') return null
  const key = new URLSearchParams(window.location.search).get('mock')
  if (!key) return null
  return mockScenarios[key as keyof typeof mockScenarios] ?? null
}

export function useBankBalances(enabled = true): BankBalancesState {
  const [data, setData] = useState<BankBalances | null>(null)
  const [loading, setLoading] = useState(enabled)
  const [error, setError] = useState<string | null>(null)
  const [forbidden, setForbidden] = useState(false)
  const [nonce, setNonce] = useState(0)

  const reload = useCallback(() => setNonce((n) => n + 1), [])

  useEffect(() => {
    if (!enabled) { setLoading(false); return }
    const mock = devMock()
    if (mock) { setData(mock); setLoading(false); setError(null); return }

    let alive = true
    setLoading(true)
    getBankBalances()
      .then((d) => {
        if (!alive) return
        setData(d); setError(null); setForbidden(false)
      })
      .catch((err: unknown) => {
        if (!alive) return
        // apiFetch hangs the HTTP status on the thrown Error (api.ts, CFO
        // directive 2026-05-20). Read that, never the message text — the
        // wording of a 403 is the backend's to change.
        const status = (err as { status?: number } | null)?.status
        if (status === 403) { setForbidden(true); setError(null) }
        else setError('The bank balances could not be read just now.')
      })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [enabled, nonce])

  return { data, loading, error, forbidden, reload }
}
