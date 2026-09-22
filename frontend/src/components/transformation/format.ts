// Shared formatters for the Transformation Board. Every helper is null-safe —
// many board fields are legitimately `null` when there is no evidence yet
// (adoption_percent, attendance_percent, manager_sla_days, ...) and the whole
// point of this file is that no component has to remember to guard that
// itself: render "—", never "0", never NaN.

export function safePct(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined || !isFinite(v)) return '—'
  return `${v.toFixed(digits)}%`
}

export function safeNum(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined || !isFinite(v)) return '—'
  return new Intl.NumberFormat('en-BW', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(v)
}

export function fmtMoney(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined || !isFinite(v)) return '—'
  return `P ${safeNum(v, digits)}`
}

/** Compact money for big headline figures — "P 12.3M", "P 840K". */
export function fmtMoneyCompact(v: number | null | undefined): string {
  if (v === null || v === undefined || !isFinite(v)) return '—'
  const abs = Math.abs(v)
  if (abs >= 1_000_000) return `P ${(v / 1_000_000).toFixed(2)}M`
  if (abs >= 1_000) return `P ${(v / 1_000).toFixed(1)}K`
  return fmtMoney(v, 0)
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return '—'
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
}

/** Clamp a value into [0, 100] for a percentage bar/ring width — server
 *  numbers should already be in range, this is a last-line defence against a
 *  layout-breaking bar. */
export function clampPct(v: number | null | undefined): number {
  if (v === null || v === undefined || !isFinite(v)) return 0
  return Math.max(0, Math.min(100, v))
}
