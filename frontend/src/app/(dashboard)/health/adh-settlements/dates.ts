/**
 * The two date renderings the ADH settlement runs screen needs, kept out of the
 * page so they can be tested. Both exist because of the same trap: a settlement
 * Saturday is a DATE, the load is an INSTANT, and treating either as the other
 * silently moves the day.
 */

/** A plain calendar date the server wrote (YYYY-MM-DD).
 *
 *  Shown as written. `new Date('2026-09-12')` is parsed as UTC midnight and then
 *  rendered in the reader's own zone, which lands on the 11th for anyone behind
 *  UTC — so the parts are pulled out by hand and rebuilt as a LOCAL date, which
 *  no zone can slide. */
export function formatDay(isoDate: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(isoDate ?? '')
  if (!m) return isoDate ?? ''
  return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]))
    .toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })
}

/** A real timestamp — read in Gaborone time, which is when it happened here.
 *  The loader runs about 01:20 CAT, which is the previous evening in UTC; left
 *  to the browser's own zone the run would appear to have happened on Friday. */
export function formatTime(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleTimeString('en-GB', {
    hour: '2-digit', minute: '2-digit', timeZone: 'Africa/Gaborone',
  })
}
