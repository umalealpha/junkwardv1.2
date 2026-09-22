/**
 * Morning Bank Balances — the client mirror of BALANCES_CONTRACT.md.
 *
 * `GET /api/v1/banking/balances/` · permission CanViewFinancials · read-only.
 * The desktop call lives with every other endpoint in lib/api.ts; the phone's
 * lives in (app)/api.ts (device token). THIS module stays free of either, so
 * the staff app can use the helpers without pulling the whole desktop API
 * client onto a phone.
 *
 * Two rules from the contract are load-bearing and are enforced by the helpers
 * below rather than left to each caller:
 *
 *   1. `balance: null` means UNKNOWN. It is never rendered as "0.00". A real
 *      zero arrives as the string "0.00" with status `ok`. The screen has to
 *      keep those two apart at a glance, so `hasFigure()` is the only test a
 *      renderer is allowed to make — never `Number(balance) === 0`.
 *   2. `projected_floor` is a FLOOR, not a forecast: Omni knows what is
 *      committed to go out but nothing about what is coming in. The label is
 *      frozen here as FLOOR_LABEL so no screen can quietly call it a "closing
 *      balance" — that would misstate the CFO's own cash position.
 */
export type BalanceStatus = 'ok' | 'stale' | 'failed' | 'no_balance' | 'never_read'

export interface BalanceAccount {
  id: string
  /** Plain words, never the raw account_name. */
  label: string
  /** Masked tail only — the full number never leaves the server. */
  account_masked: string
  /** String decimal, or null when the figure is unknown. NEVER 0.00 for unknown. */
  balance: string | null
  balance_status: BalanceStatus
  taken_at: string | null
  age_hours: number | null
  /** Raised in Omni, not yet through it. */
  outgoing_omni: string
  /** Sent to the bank, unconfirmed. */
  outgoing_at_bank: string
  outgoing_unconfirmed_count: number
  /** balance − both outgoings; null when balance is null. */
  projected_floor: string | null
  /** One plain sentence, or "". */
  note: string
}

export interface BalanceGroup {
  company: string
  /** null when any account in the group is unknown. */
  subtotal_balance: string | null
  accounts: BalanceAccount[]
}

export interface BalancesHeadline {
  total_balance: string | null
  accounts_read: number
  /** Read AND current. An account falling back to an older good
   *  reading counts in accounts_read but not here. */
  accounts_fresh: number
  accounts_total: number
  worst_status: BalanceStatus
  taken_at: string | null
  sentence: string
}

export interface BankBalances {
  headline: BalancesHeadline
  groups: BalanceGroup[]
}

// ─── Wording the screens are not allowed to invent ──────────────────────────

/**
 * FROZEN. The third figure on every card. It is a floor, not a forecast —
 * "closing balance" would promise the CFO an end-of-day number Omni cannot
 * know, because money coming in never passes through Omni.
 */
export const FLOOR_LABEL = 'Lowest you could be left with'
export const BALANCE_LABEL = 'In the bank now'
export const OUTGOING_LABEL = 'Going out'
export const OUTGOING_OMNI_LABEL = 'Waiting in Omni'
export const OUTGOING_BANK_LABEL = 'At the bank, unconfirmed'

// ─── Status ────────────────────────────────────────────────────────────────

export type StatusTone = 'muted' | 'warn' | 'danger'

export interface StatusMeta {
  /** How the timestamp line is coloured (contract status table). */
  tone: StatusTone
  /** True when the figure shown is known to be out of date — struck through. */
  strike: boolean
  /** Fallback sentence when the server sends an empty `note`. */
  fallbackNote: string
}

const STATUS_META: Record<BalanceStatus, StatusMeta> = {
  ok: { tone: 'muted', strike: false, fallbackNote: '' },
  stale: { tone: 'warn', strike: false, fallbackNote: 'This reading is more than 8 hours old.' },
  failed: { tone: 'danger', strike: true, fallbackNote: 'The last attempt to read this account failed — this is an older figure.' },
  no_balance: { tone: 'warn', strike: false, fallbackNote: 'The bank sent no balance.' },
  never_read: { tone: 'warn', strike: false, fallbackNote: 'Not yet being read from the bank.' },
}

export function statusMeta(status: BalanceStatus): StatusMeta {
  return STATUS_META[status] ?? STATUS_META.never_read
}

/** Ascending seriousness. Used only for local ordering, never to second-guess
 *  the server's `worst_status`. */
const SEVERITY: Record<BalanceStatus, number> = {
  ok: 0, stale: 1, no_balance: 2, never_read: 3, failed: 4,
}
export function isMoreSerious(a: BalanceStatus, b: BalanceStatus): boolean {
  return (SEVERITY[a] ?? 0) > (SEVERITY[b] ?? 0)
}

/**
 * Does the headline need the amber/red treatment?
 *
 * Either some account did not report, OR the worst status across the set is
 * anything but `ok`. Both halves matter: six accounts can all "report" while
 * one of them is a day-old reading.
 */
export function needsAttention(headline: BalancesHeadline): boolean {
  return headline.accounts_fresh < headline.accounts_total || headline.worst_status !== 'ok'
}

/** True when there is a real figure to show. The ONLY unknown test a renderer
 *  may use — `"0.00"` is a confirmed zero and must pass. */
export function hasFigure(value: string | null | undefined): value is string {
  return typeof value === 'string' && value.trim() !== ''
}

// ─── Money ─────────────────────────────────────────────────────────────────

/**
 * Bank balances are always shown in full, to the thebe — never the dashboard's
 * millions/thousands shorthand. The CFO is reading this to decide what can be
 * paid today; "BWP 2.18M" cannot answer that.
 */
export function formatBwp(value: string | number, currency = 'BWP'): string {
  const n = typeof value === 'string' ? Number(value) : value
  if (!isFinite(n)) return `${currency} —`
  const body = Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  return n < 0 ? `(${currency} ${body})` : `${currency} ${body}`
}

// ─── Time ──────────────────────────────────────────────────────────────────

/** HH:MM in Gaborone (UTC+2), whatever timezone the viewer's laptop is set to.
 *  A CFO in Johannesburg must still read the Botswana morning. */
export function gaboroneTime(iso: string): string {
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  try {
    return new Intl.DateTimeFormat('en-GB', {
      timeZone: 'Africa/Gaborone', hour: '2-digit', minute: '2-digit', hour12: false,
    }).format(d)
  } catch {
    return ''
  }
}

/**
 * The timestamp line under each figure. Built from `age_hours` (a number the
 * server computed) rather than the browser clock, so it cannot drift or depend
 * on the viewer's timezone.
 */
export function readingLabel(account: Pick<BalanceAccount, 'taken_at' | 'age_hours' | 'balance_status'>): string {
  if (account.balance_status === 'never_read') return 'Never read from the bank'
  if (!account.taken_at) return 'No reading on record'
  const at = gaboroneTime(account.taken_at)
  const age = account.age_hours
  let when: string
  if (age === null || !isFinite(age)) when = 'Last read'
  else if (age < 1) when = 'Read less than an hour ago'
  else if (age < 2) when = 'Read an hour ago'
  else if (age < 24) when = `Read ${Math.round(age)} hours ago`
  else if (age < 48) when = 'Read yesterday'
  else when = `Read ${Math.round(age / 24)} days ago`
  return at ? `${when}, at ${at}` : when
}
