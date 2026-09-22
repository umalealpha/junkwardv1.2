/**
 * Entity scoping for the AD Insurtech 5-Year Plan pages.
 *
 * Bug reported by Finance 2026-08-06: the Strategic Plan Library and the
 * 5-Year Plan Cockpit stayed visible after switching the active company away
 * from AD Insurtech, so plan content specific to one entity showed under all of
 * them.
 *
 * The group view ("All companies", selectedId === null) deliberately still
 * shows them — the plan is part of the group picture, and hiding it there would
 * take it away from the people who consolidate. Only a specific *other* entity
 * hides them.
 */

/** Company code that owns the 5-Year Plan pages. */
export const ADIPL_CODE = 'ADIPL'

/** Routes that belong to AD Insurtech only. */
export const ADIPL_ONLY_HREFS = [
  '/budgets/five-year',
  '/budgets/five-year/library',
] as const

/**
 * Whether the AD Insurtech plan pages should be visible.
 *
 * @param selectedCode the active company's code, or null/undefined for the
 *   consolidated "All companies" view.
 */
export function isAdiplPlanVisible(selectedCode: string | null | undefined): boolean {
  // No specific entity chosen → group view → keep it visible.
  if (!selectedCode) return true
  return selectedCode.trim().toUpperCase() === ADIPL_CODE
}

/**
 * True while the company context has an id stored but has not yet resolved it
 * to a company (cold cache / list still fetching).
 *
 * During that window `selected` is null, which reads identically to the "All
 * companies" group view — so a gate computed from the code alone would default
 * OPEN and mount the real page under the wrong company until the fetch lands.
 * Callers must treat this as "not yet known" and show nothing.
 */
export function isCompanyResolving(
  selectedId: string | null | undefined,
  selected: { code?: string | null } | null | undefined,
): boolean {
  return Boolean(selectedId) && !selected
}
