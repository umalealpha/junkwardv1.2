// Time helpers for the Alpha Rooms board — ported from the standalone app.
export const DAY_START_MIN = 7 * 60 // 07:00
export const DAY_END_MIN = 19 * 60 // 19:00
export const SLOT_MIN = 30
export const SLOT_COUNT = (DAY_END_MIN - DAY_START_MIN) / SLOT_MIN

/** "2026-08-12" for the given date, in local time. */
export function dayKey(d: Date): string {
  const m = `${d.getMonth() + 1}`.padStart(2, '0')
  const day = `${d.getDate()}`.padStart(2, '0')
  return `${d.getFullYear()}-${m}-${day}`
}

export function addDays(d: Date, n: number): Date {
  const out = new Date(d)
  out.setDate(out.getDate() + n)
  return out
}

/** Minutes since midnight → "08:30". */
export function hhmm(min: number): string {
  const h = Math.floor(min / 60)
  const m = min % 60
  return `${`${h}`.padStart(2, '0')}:${`${m}`.padStart(2, '0')}`
}

export function minutesNow(): number {
  const n = new Date()
  return n.getHours() * 60 + n.getMinutes()
}

export function slotStart(index: number): number {
  return DAY_START_MIN + index * SLOT_MIN
}

export function isToday(d: Date): boolean {
  return dayKey(d) === dayKey(new Date())
}

export function longDate(d: Date): string {
  return d.toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' })
}

export function shortDate(d: Date): string {
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}

export function durationLabel(min: number): string {
  if (min < 60) return `${min} min`
  const h = min / 60
  return `${Number.isInteger(h) ? h : h.toFixed(1)} hr`
}
