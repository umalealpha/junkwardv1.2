'use client'

/**
 * Shared furniture for the BONU screens.
 *
 * Four small things kept in one place so the four pages read as one product rather than
 * four dashboards: the palette, money formatting, the tab strip, and the section header.
 * Spacing sticks to a 4px scale and type to three sizes — 11px label, 13px body, 21px
 * figure — so nothing has to be eyeballed twice.
 */

import Link from 'next/link'
import { useId } from 'react'

export const NAVY = '#0D1B2A'
export const ORANGE = '#F4A623'
export const RED = '#DC2626'
export const AMBER = '#B45309'
export const GREEN = '#059669'
export const LINE = '#EAEEF3'

export const SEV_COLOR: Record<string, string> = { high: RED, medium: AMBER, low: '#6B7280' }

// A non-breaking space after the P: with a normal space a narrow table column wrapped the
// figure onto a second line, so "P 486,500" read as "P" then "486,500".
export const money = (n: number | null | undefined) =>
  n === null || n === undefined ? '—' : `P\u00A0${Math.round(n).toLocaleString()}`

export const money2 = (n: number | null | undefined) =>
  n === null || n === undefined
    ? '—'
    : `P\u00A0${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

/** How a stored schedule cell should READ on screen.
 *
 * The workbook's underlying values carry Excel's full float tail \u2014 a VAT cell the
 * spreadsheet displays as -81,393.43 is stored as -81393.42842105262 \u2014 and the
 * schedule is read side by side with the Excel it came from, so that tail looks
 * like a mismatch when the two figures are identical.
 *
 * DISPLAY ONLY. The stored string stays exact: sheet totals are summed from it and
 * must keep footing to the workbook's own Totals row, and the edit box is seeded
 * from `r.cells`, so saving a row never quietly truncates precision.
 *
 * Only values that actually carry a decimal point are touched. An integer is left
 * verbatim because it is as likely to be an invoice reference ('000861'), a year or
 * a count as an amount, and reformatting those corrupts them.
 */
export const cellText = (v: unknown): string => {
  const s = String(v ?? '')
  if (!/^-?\d+\.\d+$/.test(s.trim())) return s
  const n = Number(s)
  return Number.isFinite(n)
    ? n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : s
}

const TABS = [
  { href: '/bonu', label: 'Overview' },
  // Call-centre claim intake — a matter is opened and allocated to a firm the
  // moment the member calls, not only when the first bill arrives (Phase 1).
  { href: '/bonu/intake', label: 'Claim intake' },
  // Guided capture — the accountant records revenue / bills / fees / expenses
  // here without touching the raw grid (CFO, 17 Aug 2026).
  { href: '/bonu/capture', label: 'Capture' },
  // The membership roll — who the scheme may actually pay for. Without it every
  // claim is paid on trust (CFO, 18 Aug 2026).
  { href: '/bonu/members', label: 'Members' },
  { href: '/bonu/schedule', label: 'Schedule' },
  // The in-house legal office's own book — matters Alpha Law handled itself and
  // external bills argued down (Claims Legal Office, 18 Aug 2026).
  { href: '/bonu/legal', label: 'Legal office' },
  // Where a legal bill is entered and tied to the client whose matter it is
  // for, so the 80,000 per-client spend ceiling builds itself and is seen
  // coming rather than discovered afterwards (Kelvin Kimani, 9 Sep 2026).
  { href: '/bonu/legal-bills', label: 'Legal bills' },
  // Lawyer payment area — raise a payment for a confirmed bill through the vault,
  // then load it to FNB for the CFO to release (Phase 2).
  { href: '/bonu/payments', label: 'Lawyer payments' },
  { href: '/bonu/insights', label: 'Insights' },
  { href: '/bonu/inbox', label: 'Bills to check' },
  { href: '/bonu/panel', label: 'Panel' },
  { href: '/bonu/queries', label: 'Queries' },
  // The transactions behind the totals (Kutlo Keitumele, 11 Aug 2026) — its own
  // place, so the overview stays a summary and Finance still gets to the detail.
  { href: '/bonu/lines', label: 'Billed detail' },
]

/** One tab strip across every screen, so the module never feels like separate pages. */
export function BonuTabs({ active }: { active: string }) {
  return (
    <div className="flex flex-wrap gap-1 border-b" style={{ borderColor: LINE }}>
      {TABS.map((t) => {
        const on = t.href === active
        return (
          <Link
            key={t.href}
            href={t.href}
            className="px-4 py-2 text-[13px] font-medium transition-colors duration-150"
            style={{
              color: on ? NAVY : '#6B7280',
              borderBottom: `2px solid ${on ? ORANGE : 'transparent'}`,
            }}
          >
            {t.label}
          </Link>
        )
      })}
    </div>
  )
}

/** A figure with its label above it. Used for every tile so the eye lands in one place. */
export function Stat({
  label,
  value,
  tone,
  sub,
}: {
  label: string
  value: string
  tone?: string
  sub?: string
}) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide" style={{ color: '#6B7280' }}>
        {label}
      </div>
      <div className="mt-1 text-[21px] font-bold leading-none" style={{ color: tone || NAVY }}>
        {value}
      </div>
      {sub ? (
        <div className="mt-1 text-[12px]" style={{ color: '#6B7280' }}>
          {sub}
        </div>
      ) : null}
    </div>
  )
}

/** A short explanation the reader needs BEFORE the numbers, not in a footnote. */
export function Note({
  tone = 'info',
  title,
  children,
}: {
  tone?: 'info' | 'warn' | 'good'
  title?: string
  children: React.ReactNode
}) {
  const style =
    tone === 'warn'
      ? { bg: '#FEF7F7', border: '#F3D6D6', head: RED }
      : tone === 'good'
        ? { bg: '#F0FDF4', border: '#C7EBD9', head: GREEN }
        : { bg: '#F8FAFC', border: LINE, head: NAVY }
  return (
    <div
      className="rounded-xl px-4 py-3 text-[13px] leading-relaxed"
      style={{ background: style.bg, border: `1px solid ${style.border}` }}
    >
      {title ? (
        <div className="mb-1 text-[13px] font-bold" style={{ color: style.head }}>
          {title}
        </div>
      ) : null}
      <div style={{ color: '#374151' }}>{children}</div>
    </div>
  )
}

/** Table shell: one border colour, one padding rhythm, scrolls on a narrow screen. */
export function Table({ head, children }: { head: string[]; children: React.ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[13px]" style={{ borderCollapse: 'collapse' }}>
        <thead>
          <tr>
            {head.map((h, i) => (
              <th
                key={h + i}
                className="px-3 py-2 text-left text-[11px] font-semibold uppercase tracking-wide"
                style={{ color: '#6B7280', borderBottom: `1px solid ${LINE}` }}
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  )
}

export const td = 'px-3 py-2 align-top'
// Figures must never wrap mid-number.
export const tdNum = 'px-3 py-2 align-top text-right whitespace-nowrap'
export const trBorder = { borderBottom: `1px solid ${LINE}` }


/**
 * A labelled form control, with the label ACTUALLY TIED to the control.
 *
 * Why this exists. Every BONU form was written as a bare `<label>Text</label>`
 * sitting next to a bare `<input>` or `<select>`, with nothing joining the two.
 * On a text box that just about survives, because the placeholder gives the
 * control a name. On a DATE box and on every `<select>` there is no
 * placeholder, so the control has no name at all: a screen reader announces
 * "edit text, blank", and clicking the words does not put the cursor in the
 * field. The QC battery found 154 such controls across the BONU screens
 * (CFO instruction 9 Sep 2026 to fix them all).
 *
 * `useId()` gives a stable id that survives server rendering, so the label and
 * the control are joined without anybody having to invent unique ids by hand —
 * and the label text stays in ONE place, which an `aria-label` copy would not.
 *
 * Use it wherever a control has a VISIBLE label:
 *
 *     <Field label="Date of loss">
 *       {(id) => <input id={id} type="date" ... />}
 *     </Field>
 *
 * Where a control has NO visible label (a bare filter dropdown, an icon-only
 * button) there is nothing to tie it to, so give that control its own
 * `aria-label` instead.
 */
export function Field({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: (id: string) => React.ReactNode
}) {
  const id = useId()
  return (
    <div>
      <label htmlFor={id} className="mb-1 block text-[11px] font-medium"
        style={{ color: '#6B7280' }}>
        {label}
      </label>
      {children(id)}
      {hint ? (
        <div className="mt-0.5 text-[10px]" style={{ color: '#9CA3AF' }}>{hint}</div>
      ) : null}
    </div>
  )
}
