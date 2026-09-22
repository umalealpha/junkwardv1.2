// Requested-leave day estimate for the apply form's on-screen heads-up.
//
// CFO 2026-09-02: an employee should see how many days they have — and whether
// they are asking for more than they hold — BEFORE they press Submit, not as a
// red error after (Snehal, 2 Sep, only learned she had 0 annual days at submit).
//
// This is a ROUGH Mon–Fri count for the warning ONLY. The server is the exact
// gate and stays the source of truth (it knows public holidays, the accrual
// cut-off, etc.); this never blocks a submit. Kept in lib/ so it is unit-tested
// — a page.tsx may not export a helper (next build rejects it).

/** Approximate working days (Mon–Fri) in the request, or null when the dates
 *  are not yet a valid range. Half days trim 0.5 off each chosen boundary. */
export function estimateWorkingDays(
  startISO: string, endISO: string,
  startHalf: boolean, endHalf: boolean,
): number | null {
  if (!startISO || !endISO) return null
  const s = new Date(startISO + 'T00:00:00')
  const e = new Date(endISO + 'T00:00:00')
  if (Number.isNaN(s.getTime()) || Number.isNaN(e.getTime()) || e < s) return null
  let n = 0
  for (const d = new Date(s); d <= e; d.setDate(d.getDate() + 1)) {
    const wd = d.getDay()                 // 0 Sun … 6 Sat
    if (wd !== 0 && wd !== 6) n += 1
  }
  if (n === 0) return 0
  // A single-day request carries its half on the start boundary only (the end
  // stays full so 0.5 is never deducted twice) — mirrors the submit payload.
  if (startISO === endISO) return startHalf ? 0.5 : n
  if (startHalf) n -= 0.5
  if (endHalf) n -= 0.5
  return n
}
