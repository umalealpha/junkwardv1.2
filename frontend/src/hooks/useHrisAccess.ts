'use client'

/**
 * useHrisAccess — single-source probe for the HRIS whitelist gate + RBAC.
 *
 * CFO directive 2026-05-18: HRIS contains payroll details and is restricted
 * to Prathap, Arun, Kago, Pako, Unami (plus superusers / admins). Pages
 * and the Sidebar group call this hook to decide visibility.
 *
 * Returns:
 *   undefined  → still loading the probe
 *   true       → user is allowed
 *   false      → user is not allowed (page should redirect away,
 *                sidebar entry should hide)
 *
 * Companion hooks: useHrisRole() returns the role string (ess / mgr /
 * hr / admin / ceo / superadmin) and useHrisCan(capability) tells the
 * UI which buttons to render.
 */
import { useEffect, useState } from 'react'
import { apiFetch } from '@/lib/api'

export type HrisRole = 'anon' | 'ess' | 'mgr' | 'hr' | 'admin' | 'ceo' | 'superadmin'

export interface HrisAccessResponse {
  allowed: boolean
  email: string
  is_superuser: boolean
  role: HrisRole
  capabilities: string[]
  reason?: string
}

let _cache: HrisAccessResponse | undefined
let _inflight: Promise<HrisAccessResponse> | null = null

async function fetchHrisAccess(): Promise<HrisAccessResponse> {
  if (_inflight) return _inflight
  _inflight = (async () => {
    try {
      const r = await apiFetch<HrisAccessResponse>('/admin/hris-access/')
      _cache = {
        allowed:      !!r.allowed,
        email:        r.email || '',
        is_superuser: !!r.is_superuser,
        role:         (r.role || 'anon') as HrisRole,
        capabilities: Array.isArray(r.capabilities) ? r.capabilities : [],
        reason:       r.reason,
      }
      return _cache
    } catch {
      _cache = { allowed: false, email: '', is_superuser: false, role: 'anon', capabilities: [] }
      return _cache
    } finally {
      _inflight = null
    }
  })()
  return _inflight
}

export function useHrisAccess(): boolean | undefined {
  const [allowed, setAllowed] = useState<boolean | undefined>(_cache?.allowed)
  useEffect(() => {
    if (_cache !== undefined) {
      setAllowed(_cache.allowed)
      return
    }
    let mounted = true
    fetchHrisAccess().then(v => { if (mounted) setAllowed(v.allowed) })
    return () => { mounted = false }
  }, [])
  return allowed
}

export function useHrisAccessInfo(): HrisAccessResponse | undefined {
  const [info, setInfo] = useState<HrisAccessResponse | undefined>(_cache)
  useEffect(() => {
    if (_cache !== undefined) {
      setInfo(_cache)
      return
    }
    let mounted = true
    fetchHrisAccess().then(v => { if (mounted) setInfo(v) })
    return () => { mounted = false }
  }, [])
  return info
}

export function useHrisRole(): HrisRole | undefined {
  const info = useHrisAccessInfo()
  return info ? info.role : undefined
}

export function useHrisCan(capability: string): boolean | undefined {
  const info = useHrisAccessInfo()
  if (info === undefined) return undefined
  return info.capabilities.includes(capability)
}

/**
 * useHrisSelfService — true when the caller may use the Employee Self-Service
 * tier: log their OWN leave, view their OWN payslips / profile. Every
 * authenticated employee holds this (role 'ess' and up).
 *
 * CFO directive 2026-06-16 (Lakshmi Anand / ADRisk bug aec2f3ce): the
 * self-service tier is open to ALL staff, including subsidiaries. The
 * sensitive surfaces (payroll, People directory, compensation, talent) stay
 * behind useHrisAccess (the 5-person whitelist), so this is deliberately a
 * separate, narrower gate.
 *
 *   undefined → probe still loading
 *   true      → caller can use self-service
 *   false     → caller has no HRIS capability at all (e.g. anonymous)
 */
export function useHrisSelfService(): boolean | undefined {
  const info = useHrisAccessInfo()
  if (info === undefined) return undefined
  return info.capabilities.includes('view_self')
}
