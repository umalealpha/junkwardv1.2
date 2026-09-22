// Omni Mobile — capability manifest (client mirror of core/mobile_capabilities.py)
//
// Server-truth: the phone renders its surfaces from these booleans and never
// infers authority from a title string. The list below MUST match
// MOBILE_CAPABILITY_KEYS in core/mobile_capabilities.py.
//
// Workstream A ships the reader + the typed feature registry; Workstream B wires
// the registry into the shells (AppShell / StaffHub / approve / do / me / more).
import { useEffect, useState } from 'react'
import { afetch } from './api'

export type MobileCapability =
  | 'view_personal_home'
  | 'manage_team'
  | 'give_monthly_feedback'
  | 'view_all_feedback'
  | 'view_claims_workspace'
  | 'capture_claims_action'
  | 'view_underwriting_workspace'
  | 'issue_underwriting_documents'
  | 'view_bonu'
  | 'capture_bonu_claim'
  | 'edit_bonu_legal'
  | 'view_finance_workspace'
  | 'create_finance_transaction'
  | 'approve_finance_transaction'
  | 'manage_fnb'
  | 'view_hr_workspace'
  | 'manage_leave'
  | 'manage_payroll'
  | 'manage_recruitment'
  | 'view_executive_dashboard'

export type Capabilities = Partial<Record<MobileCapability, boolean>>

type MeResponse = { mobile_capabilities?: Capabilities }

const CACHE_KEY = 'omni.mobile.caps.v1'

function readCache(): Capabilities | null {
  try {
    const raw = localStorage.getItem(CACHE_KEY)
    return raw ? (JSON.parse(raw) as Capabilities) : null
  } catch {
    return null
  }
}

function writeCache(c: Capabilities) {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(c))
  } catch {
    /* private mode / storage disabled — non-fatal */
  }
}

/**
 * Read the server capability manifest.
 *
 * A network failure is NEVER treated as "no access": on error we keep the
 * last-good manifest (from state or the on-device cache) and surface `error`,
 * so a transient blip does not silently hide features the user is allowed.
 */
export function useMobileCapabilities() {
  const [caps, setCaps] = useState<Capabilities | null>(() => readCache())
  const [loading, setLoading] = useState<boolean>(() => readCache() === null)
  const [error, setError] = useState(false)

  useEffect(() => {
    let alive = true
    afetch<MeResponse>('/user-profiles/me/')
      .then((me) => {
        if (!alive) return
        if (me && me.mobile_capabilities) {
          setCaps(me.mobile_capabilities)
          writeCache(me.mobile_capabilities)
        }
        setError(false)
      })
      .catch(() => {
        if (alive) setError(true) // keep last-good caps — do NOT clear to all-false
      })
      .finally(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [])

  /** True only when the manifest is loaded AND explicitly grants the capability. */
  const can = (k: MobileCapability): boolean => !!caps && caps[k] === true

  return { caps, loading, error, can }
}

// ── Typed feature registry ────────────────────────────────────────────────
// One source of truth for mobile features, replacing the hard-coded tile arrays
// scattered across AppShell / StaffHub / approve / do / me / more. An entry is
// shown only when the viewer holds `capability` (null = always visible).
export type FeatureCategory = 'work' | 'inbox' | 'me' | 'quick'
export type OfflinePolicy = 'cache_read' | 'online_only'

export interface MobileFeature {
  route: string
  label: string
  category: FeatureCategory
  capability: MobileCapability | null
  keywords: string[]
  badgeSource?: string
  offline: OfflinePolicy
}

export const mobileFeatureRegistry: MobileFeature[] = [
  { route: 'lookup', label: 'Look up a client', category: 'work', capability: null, keywords: ['client', 'policy', 'search'], offline: 'cache_read' },
  { route: 'claims', label: 'Claims', category: 'work', capability: 'view_claims_workspace', keywords: ['claim', 'assessment', 'repairer', 'subrogation'], offline: 'cache_read' },
  { route: 'docs', label: 'Quotes & certificates', category: 'work', capability: 'view_underwriting_workspace', keywords: ['quote', 'underwriting', 'certificate', 'policy'], offline: 'cache_read' },
  { route: 'bonu', label: 'Legal / BONU', category: 'work', capability: 'view_bonu', keywords: ['legal', 'bonu', 'matter', 'fee'], offline: 'cache_read' },
  { route: 'finance', label: 'Finance', category: 'work', capability: 'view_finance_workspace', keywords: ['finance', 'cash', 'ap', 'reconcile'], offline: 'cache_read' },
  // NOTE: the "Payments" tile is intentionally NOT here. The real payment-
  // authoriser gate is narrower than any finance-approval title, so Workstream B
  // must gate it on the actual payment-authoriser endpoint (the existing
  // /payment-requests/bulk/preview/ probe), not a capability title — otherwise
  // it advertises a tile that 403s (Fable WS-A review).
  // Money in the bank (CFO 2026-09-20). ONLINE ONLY, deliberately: a cash
  // balance read from a phone cache would show yesterday's money as today's,
  // and the whole point of this screen is what is there THIS morning. Every
  // other feature here caches; this one must not.
  { route: 'balances', label: 'Money in the bank', category: 'work', capability: 'view_finance_workspace', keywords: ['bank', 'balance', 'cash', 'money', 'fnb'], offline: 'online_only' },
  { route: 'hr', label: 'People / HR', category: 'work', capability: 'view_hr_workspace', keywords: ['hr', 'leave', 'people', 'roster'], offline: 'cache_read' },
  { route: 'team', label: 'My team', category: 'work', capability: 'manage_team', keywords: ['team', 'manager', 'roster', 'feedback'], offline: 'cache_read' },
  { route: 'recruitment', label: 'Recruitment', category: 'work', capability: 'manage_recruitment', keywords: ['recruit', 'authority', 'hire', 'onboard'], offline: 'cache_read' },
]

/** Features the given manifest permits (null-capability features always pass). */
export function permittedFeatures(caps: Capabilities | null): MobileFeature[] {
  return mobileFeatureRegistry.filter(
    (f) => f.capability === null || (!!caps && caps[f.capability] === true),
  )
}
