/**
 * navDomains — regression tests for M-NAV-DOMAINS (CFO be335dfe + 3f2b2411,
 * 19-Sep-2026): sidebar rail reorganised into 9 business domains. Pins:
 *   (a) each explicitly-moved item's final domain
 *   (b) the full route set reachable from the sidebar is IDENTICAL before/after
 *   (c) every role gate still hides/shows exactly what it did before
 *   (d) Sidebar and CommandPalette agree on domain grouping for moved items
 *
 * (a)/(c)/(d) exercise the REAL production code (buildNavModules() from
 * lib/navModules.ts, DESTS from components/CommandPalette.tsx) — not a
 * hand-copied duplicate, so this suite cannot drift from what actually ships.
 * (b) additionally frozen-checks the OLD route set, copied from
 * origin/main's Sidebar.tsx before this batch (git show
 * origin/main:frontend/src/components/layout/Sidebar.tsx), against the NEW
 * route set read straight off the current source files on disk.
 */
import { describe, it, expect } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import {
  buildNavModules, RAIL_DOMAINS, MOVED_ITEM_DOMAIN,
  type NavGates, type NavModule,
} from '../navModules'
import { DESTS } from '@/components/CommandPalette'

// ─── Helpers ─────────────────────────────────────────────────────────────

/** Every literal href in the built modules, query strings intact (matching
 *  how the source hrefs are written) — for comparing against OLD_ROUTES,
 *  which is also extracted verbatim from source, not route-matched. */
function collectHrefs(mods: NavModule[]): string[] {
  const hrefs: string[] = []
  for (const m of mods) {
    for (const s of m.sections) {
      for (const it of s.items) {
        if (it.href && it.href !== '#') hrefs.push(it.href)
      }
    }
  }
  return [...new Set(hrefs)]
}

/** Which module key (domain) holds a given href, when built with the gates
 *  supplied. Fails loudly (via the caller's assertion) if it is in zero or
 *  more than one module — either is a placement bug. */
function moduleKeysHolding(mods: NavModule[], href: string): string[] {
  return mods
    .filter(m => m.sections.some(s => s.items.some(it => it.href.split('?')[0] === href)))
    .map(m => m.key)
}

const ME_ALL = {
  can_manage_periods: true, can_administer_users: true,
  can_view_internal_audit: true, is_access_delegate: true,
} as unknown as NavGates['me']

const GATES_ALL_TRUE: NavGates = {
  isTheCfo: true, canSeeForgiveness: true, me: ME_ALL,
  canAccessHris: true, canSelfServeHris: true, canAccessSalvage: true,
  canAccessDpa: true, canAccessCompliance: true, canAccessSecurity: true,
  canReadSpeakerFeedback: true, planVisible: true, canAccessAdhSettlements: true,
  canManageUnicoin: true,
}

const GATES_ALL_FALSE: NavGates = {
  isTheCfo: false, canSeeForgiveness: false, me: null,
  canAccessHris: false, canSelfServeHris: false, canAccessSalvage: false,
  canAccessDpa: false, canAccessCompliance: false, canAccessSecurity: false,
  canReadSpeakerFeedback: false, planVisible: true, canAccessAdhSettlements: false,
  canManageUnicoin: false,
}

// ─── (a) each moved item's final domain ─────────────────────────────────────

describe('(a) every CFO-named item lands in its named domain', () => {
  const mods = buildNavModules(GATES_ALL_TRUE)
  const railKeys = new Set(RAIL_DOMAINS.map(d => d.key))

  it('MOVED_ITEM_DOMAIN only names real rail domains', () => {
    for (const domain of Object.values(MOVED_ITEM_DOMAIN)) {
      expect(railKeys.has(domain)).toBe(true)
    }
  })

  for (const [href, domain] of Object.entries(MOVED_ITEM_DOMAIN)) {
    it(`${href} -> ${domain}`, () => {
      const holders = moduleKeysHolding(mods, href)
      expect(holders).toEqual([domain])
    })
  }
})

// ─── (b) route-set parity: old vs new ───────────────────────────────────────

describe('(b) the full sidebar route set is unchanged by the domain regroup', () => {
  // Frozen from origin/main's Sidebar.tsx, captured 19-Sep-2026 before this
  // batch via: git show origin/main:frontend/src/components/layout/Sidebar.tsx
  // then extracting every href: '...' literal (see extractHrefs below).
  //
  // A genuinely NEW page is added here deliberately, with a line saying what
  // it is — that is the whole point of the freeze. It catches a route that
  // appeared without anyone deciding to add it; it is not meant to block a
  // feature the CFO asked for.
  //   /transformation — Transformation Board (CFO 2026-09-20), the four-month
  //   AI programme screen for the CEO, CFO, COO and Chief Human Capital Officer.
  const OLD_ROUTES: string[] = [
    "/accounts",
    "/adoption",
    "/approvals",
    "/assets",
    "/assets/approvals",
    "/assets/import",
    "/assets/my-assets",
    "/assets/new",
    "/assets/reconciliation",
    "/assets/requisitions",
    "/assets/spare-pool",
    "/audit-log",
    "/aware",
    "/bank-accounts",
    "/bank-feeds",
    "/bank-proofs",
    "/banking",
    "/banking/fnb",
    "/banking/fx-planning",
    "/banking/realpay",
    "/banking/realpay/collections/analytics",
    "/banking/realpay/collections/dashboard",
    "/banking/realpay/collections/debit-tracker",
    "/banking/realpay/collections/recon",
    "/banking/realpay/failed-debits",
    "/banking/realpay/monitoring",
    "/banking/voucher-clearing",
    "/bills",
    "/bonu",
    "/bonu/inbox",
    "/bonu/intake",
    "/bonu/legal",
    "/bonu/panel",
    "/bonu/payments",
    "/bonu/queries",
    "/budgets",
    "/budgets/five-year",
    "/budgets/five-year/library",
    "/budgets/simulator",
    "/bug-reports",
    "/cfo-upload",
    "/cfo/build-log",
    "/cfo/ci",
    "/cfo/forgiveness",
    "/cfo/jobs",
    "/claims-automation",
    "/claims-po",
    "/claims-po/analytics",
    "/claims/forms",
    "/claims/register",
    "/claims/salvages",
    "/claims/subrogations",
    "/commandments",
    "/commissions",
    "/commissions/brokers",
    "/company-cards",
    "/compliance/aml/board-reports",
    "/compliance/aml/sanctions",
    "/compliance/aml/suppliers",
    "/compliance/aml/training",
    "/compliance/dpo",
    "/compliance/iso",
    "/compliance/iso/audit-pack",
    "/compliance/iso/capas",
    "/compliance/iso/policies",
    "/compliance/iso/risks",
    "/compliance/iso/soa",
    "/compliance/nbfira",
    "/compliance/nbfira/annual",
    "/compliance/nbfira/capital-adequacy",
    "/compliance/nbfira/prudential",
    "/compliance/nbfira/quarterly",
    "/compliance/nbfira/settings",
    "/compliance/nbfira/submissions",
    "/compliance/overview",
    "/compliance/policy-library",
    "/compliance/ropa",
    "/compliance/ropa-fields",
    "/compliance/ropa/vendors",
    "/compliance/security",
    "/contacts/new?type=vendor",
    "/contacts?type=customer",
    "/dashboard",
    "/data-protection",
    "/data-protection#breaches",
    "/data-protection#checklist",
    "/data-protection#dpia",
    "/data-protection#dsr",
    "/data-protection#notices",
    "/data-protection#ropa",
    "/data-protection#scorecard",
    "/debtors",
    "/equity",
    "/exceptions",
    "/fiscal-periods",
    "/fleet",
    "/fx-revaluation",
    "/goods-received",
    "/graphite-feeds",
    "/health-care/cover",
    "/health-care/providers",
    "/health/adh-dashboard",
    "/health/adh-settlements",
    "/health/afa-load-file",
    "/health/dashboard",
    "/health/provider-dashboard",
    "/health/quick-quote",
    "/health/quotes",
    "/health/service-providers",
    "/health/vendor-onboarding",
    "/healthcare/claims",
    "/healthcare/revenue",
    "/healthcare/treaty",
    "/help",
    "/helpdesk/",
    "/hr-analytics",
    "/hris",
    "/hris/ai-readiness",
    "/hris/amendments",
    "/hris/career-track",
    "/hris/command-center",
    "/hris/contracts",
    "/hris/directory",
    "/hris/disciplinary",
    "/hris/documents",
    "/hris/excuses",
    "/hris/flight-risk",
    "/hris/idp",
    "/hris/inbox",
    "/hris/incentives",
    "/hris/joiners",
    "/hris/induction",
    "/hris/training",
    "/hris/leave",
    "/hris/leave-encashment",
    "/hris/leave-excuse",
    "/hris/letters",
    "/hris/manager-scorecard",
    "/hris/monthly-feedback",
    "/hris/monthly-return",
    "/hris/my-brief",
    "/hris/my-dialogue",
    "/hris/ninebox",
    "/hris/offboarding",
    "/hris/okr-tree",
    "/hris/onboard",
    "/hris/payroll",
    "/hris/payroll-setup",
    "/hris/payslips",
    "/hris/performance",
    "/hris/profile",
    "/hris/pulse",
    "/hris/rewards",
    "/hris/roster-flags",
    "/hris/screen-integrity",
    "/hris/settings",
    "/hris/skills",
    "/hris/staff-loans",
    "/hris/succession",
    "/hris/talent-cockpit",
    "/hris/team-dialogues",
    "/hris/time-doctor",
    "/ifrs17",
    "/instant-insurance",
    "/integrations",
    "/intel-summary",
    "/internal-audit",
    "/internal-audit/findings",
    "/internal-audit/follow-up",
    "/investments",
    "/investments/transactions",
    "/invoices",
    "/journal-entries",
    "/leases",
    "/manus-activity",
    "/my-approvals",
    "/my-equity",
    "/my-league",
    "/my-omni",
    "/my-requests",
    "/my-signoffs",
    "/nexus",
    "/payables/aging",
    "/payables/recon",
    "/payment-requests",
    "/payments/approvals",
    "/payments?type=payment",
    "/payments?type=receipt",
    "/payroll/backlog",
    "/payroll/dashboard",
    "/payroll/employees",
    "/payroll/group-report",
    "/payroll/monthly-pack",
    "/payroll/payslips",
    "/payroll/run",
    "/payroll/sign-off",
    "/payroll/staff-loans",
    "/period-close",
    "/periods",
    "/petty-cash",
    "/petty-cash/new",
    "/petty-cash/reimbursements",
    "/petty-cash/reimbursements/new",
    "/procurement",
    "/purchase-orders",
    "/purchase-orders/new",
    "/quick-entry",
    "/reconciliation",
    "/records",
    "/recruitment",
    "/recruitment/authorities",
    "/recruitment/offers",
    "/recruitment/tiers",
    "/refunds",
    "/reinsurance",
    "/reinsurance/bordereaux",
    "/reinsurance/cessions",
    "/reinsurance/fac-risk",
    "/reinsurance/history",
    "/reinsurance/recoveries",
    "/reinsurance/reinsurers",
    "/reinsurance/renewal",
    "/reinsurance/security",
    "/reinsurance/treaties",
    "/renewals",
    "/report-bug",
    "/reports/age-analysis",
    "/reports/ai-integrity",
    "/reports/ap-aging",
    "/reports/ar-aging",
    "/reports/asset-movement",
    "/reports/asset-register",
    "/reports/audit-pack",
    "/reports/balance-sheet",
    "/reports/budget-setup",
    "/reports/budget-vs-actual",
    "/reports/cash-flow",
    "/reports/cash-position",
    "/reports/claims-payment-movement",
    "/reports/consolidated",
    "/reports/draft-triage",
    "/reports/entity-pl",
    "/reports/exceptions",
    "/reports/expense-analysis",
    "/reports/general-ledger",
    "/reports/ma-trace",
    "/reports/management-pack",
    "/reports/peer-benchmark",
    "/reports/profit-loss",
    "/reports/regulatory-capital",
    "/reports/related-party-transactions",
    "/reports/tax-reconciliation",
    "/reports/trial-balance",
    "/reports/vat-return",
    "/reverse-charge",
    "/rewards",
    "/rewards/leaderboard",
    "/rewards/nexus-testers",
    "/rewards/redeem",
    "/rooms",
    "/salvage",
    "/salvage/inventory",
    "/salvage/possession",
    "/salvage/parts",
    "/settings",
    "/settings/api-keys",
    "/settings/chart-of-accounts",
    "/settings/digital-assistant",
    "/settings/frozen-controls",
    "/settings/fx-rates",
    "/settings/m365-active-users",
    "/settings/recurring-journal-entries",
    "/settings/reinsurance-treaties",
    "/settings/roles",
    "/settings/secrets",
    "/settings/user-access",
    "/settings/user-emails",
    "/settings/user-titles",
    "/settings/users",
    "/sop-bank",
    "/speaker-feedback",
    "/spend-requests",
    "/staff-rewards",
    "/task-dashboard",
    "/tasks",
    "/tax-calendar",
    "/tax/vat-recon",
    "/thrive",
    "/transformation",
    "/underwriting",
    "/underwriting/adoption",
    "/underwriting/quotes",
    "/unicoin",
    "/upload",
    "/vehicle-register",
    "/vendor-banking",
    "/vendors",
    "/vendors/addresses",
    "/whatsapp"
  ]

  function extractHrefs(source: string): string[] {
    const re = /href:\s*(?:'([^']*)'|"([^"]*)"|ADH_SETTLEMENTS_HREF)/g
    const set = new Set<string>()
    let m: RegExpExecArray | null
    while ((m = re.exec(source))) {
      const h = m[1] || m[2] || '/health/adh-settlements'
      if (!h || h === '#') continue
      set.add(h)
    }
    return [...set]
  }

  it('the routes referenced in Sidebar.tsx + navModules.ts equal the frozen origin/main set', () => {
    const sidebarSrc = fs.readFileSync(path.join(__dirname, '../../components/layout/Sidebar.tsx'), 'utf8')
    const navModulesSrc = fs.readFileSync(path.join(__dirname, '../navModules.ts'), 'utf8')
    const newRoutes = extractHrefs(sidebarSrc + '\n' + navModulesSrc).sort()
    expect(newRoutes).toEqual([...OLD_ROUTES].sort())
  })

  it('every route buildNavModules can produce (all gates true) was already reachable before', () => {
    const mods = buildNavModules(GATES_ALL_TRUE)
    const oldSet = new Set(OLD_ROUTES)
    const produced = collectHrefs(mods)
    const notInOld = produced.filter(h => !oldSet.has(h))
    expect(notInOld).toEqual([])
  })
})

// ─── (c) role gates preserved ───────────────────────────────────────────────

describe('(c) role-gated items keep their exact gate', () => {
  it('Platform Operations (Build log / Gate board / Scheduled jobs) is CFO-only', () => {
    const off = buildNavModules({ ...GATES_ALL_FALSE })
    const on = buildNavModules({ ...GATES_ALL_FALSE, isTheCfo: true })
    for (const href of ['/cfo/build-log', '/cfo/ci', '/cfo/jobs']) {
      expect(moduleKeysHolding(off, href)).toEqual([])
      expect(moduleKeysHolding(on, href)).toEqual(['system'])
    }
  })

  it('Forgiveness watch shows for the CFO or can_see_forgiveness, never otherwise', () => {
    const neither = buildNavModules({ ...GATES_ALL_FALSE })
    const cfoOnly = buildNavModules({ ...GATES_ALL_FALSE, isTheCfo: true })
    const forgivenessOnly = buildNavModules({ ...GATES_ALL_FALSE, canSeeForgiveness: true })
    expect(moduleKeysHolding(neither, '/cfo/forgiveness')).toEqual([])
    expect(moduleKeysHolding(cfoOnly, '/cfo/forgiveness')).toEqual(['people'])
    expect(moduleKeysHolding(forgivenessOnly, '/cfo/forgiveness')).toEqual(['people'])
  })

  it('Adoption is ungated — visible even with every other gate false', () => {
    const mods = buildNavModules({ ...GATES_ALL_FALSE })
    expect(moduleKeysHolding(mods, '/adoption')).toEqual(['people'])
  })

  it('People module still renders (Management Insight only) with no HRIS access at all', () => {
    const mods = buildNavModules({ ...GATES_ALL_FALSE })
    const people = mods.find(m => m.key === 'people')
    expect(people).toBeDefined()
    expect(people!.sections.map(s => s.title)).toEqual(['Management Insight'])
  })

  it('Data Protection section is gated on canAccessDpa', () => {
    const off = buildNavModules({ ...GATES_ALL_FALSE })
    const on = buildNavModules({ ...GATES_ALL_FALSE, canAccessDpa: true })
    expect(moduleKeysHolding(off, '/data-protection')).toEqual([])
    expect(moduleKeysHolding(on, '/data-protection')).toEqual(['risk'])
  })

  it('Salvage Yard is gated on canAccessSalvage', () => {
    const off = buildNavModules({ ...GATES_ALL_FALSE })
    const on = buildNavModules({ ...GATES_ALL_FALSE, canAccessSalvage: true })
    expect(moduleKeysHolding(off, '/salvage')).toEqual([])
    expect(moduleKeysHolding(on, '/salvage')).toEqual(['insurance'])
  })

  it('Internal Audit is gated on me.can_view_internal_audit', () => {
    const off = buildNavModules({ ...GATES_ALL_FALSE })
    const on = buildNavModules({ ...GATES_ALL_FALSE, me: { can_view_internal_audit: true } as unknown as NavGates['me'] })
    expect(moduleKeysHolding(off, '/internal-audit')).toEqual([])
    expect(moduleKeysHolding(on, '/internal-audit')).toEqual(['risk'])
  })

  it('admin Settings section is gated on can_administer_users / is_access_delegate', () => {
    const off = buildNavModules({ ...GATES_ALL_FALSE })
    const on = buildNavModules({ ...GATES_ALL_FALSE, me: { can_administer_users: true } as unknown as NavGates['me'] })
    expect(moduleKeysHolding(off, '/settings/users')).toEqual([])
    expect(moduleKeysHolding(on, '/settings/users')).toEqual(['system'])
  })

  it('Audience Feedback is gated on canReadSpeakerFeedback', () => {
    const off = buildNavModules({ ...GATES_ALL_FALSE })
    const on = buildNavModules({ ...GATES_ALL_FALSE, canReadSpeakerFeedback: true })
    expect(moduleKeysHolding(off, '/speaker-feedback')).toEqual([])
    expect(moduleKeysHolding(on, '/speaker-feedback')).toEqual(['system'])
  })

  it('UniCoin stays hidden regardless of canManageUnicoin (dead false && branch preserved)', () => {
    const mods = buildNavModules({ ...GATES_ALL_TRUE, canManageUnicoin: true })
    expect(moduleKeysHolding(mods, '/unicoin')).toEqual([])
  })
})

// ─── (d) Sidebar / CommandPalette agreement ─────────────────────────────────

describe('(d) command palette grouping agrees with the sidebar domain', () => {
  const labelFor = (key: string) => RAIL_DOMAINS.find(d => d.key === key)!.label
  const destsByHref = new Map(DESTS.map(d => [d.href, d]))

  for (const [href, domain] of Object.entries(MOVED_ITEM_DOMAIN)) {
    const dest = destsByHref.get(href)
    if (!dest) continue // not every route is indexed in the palette yet
    it(`palette group for ${href} matches the sidebar domain (${labelFor(domain)})`, () => {
      expect(dest.group).toBe(labelFor(domain))
    })
  }

  it('at least one moved item is actually indexed in the palette (sanity: this test suite is not vacuous)', () => {
    const indexed = Object.keys(MOVED_ITEM_DOMAIN).filter(h => destsByHref.has(h))
    expect(indexed.length).toBeGreaterThan(0)
  })
})
