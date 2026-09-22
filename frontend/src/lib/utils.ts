import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

// ─── Class name utility ────────────────────────────────────────────────────────

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}

// ─── Currency formatting ───────────────────────────────────────────────────────

// CFO directive 2026-05-21 (Prompt Currency Fix): every amount must
// display the 3-letter functional-currency code, not a glyph. "USD 168,394"
// not "$168,394". Keeps cross-currency consolidations unambiguous.
const CURRENCY_SYMBOLS: Record<string, string> = {
  BWP: 'BWP',
  USD: 'USD',
  ZAR: 'ZAR',
  EUR: 'EUR',
  GBP: 'GBP',
  ZMW: 'ZMW',
  MWK: 'MWK',
  TZS: 'TZS',
  KES: 'KES',
  NAD: 'NAD',
  INR: 'INR',
  SGD: 'SGD',
}

export function formatCurrency(amount: string | number, currency: string = 'BWP'): string {
  const num = typeof amount === 'string' ? parseFloat(amount) : amount
  if (isNaN(num)) return `${CURRENCY_SYMBOLS[currency] || currency} 0.00`
  const symbol = CURRENCY_SYMBOLS[currency] || currency
  const formatted = Math.abs(num).toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
  return num < 0 ? `(${symbol} ${formatted})` : `${symbol} ${formatted}`
}

/** Format large amounts in millions for KPI display: "BWP 4.9M" */
export function formatMillions(amount: string | number, currency: string = 'BWP'): string {
  const num = typeof amount === 'string' ? parseFloat(amount) : amount
  if (isNaN(num)) return `${CURRENCY_SYMBOLS[currency] || currency} 0.0M`
  const symbol = CURRENCY_SYMBOLS[currency] || currency
  const abs = Math.abs(num)
  if (abs >= 1_000_000) {
    const m = (abs / 1_000_000).toFixed(1)
    return num < 0 ? `(${symbol} ${m}M)` : `${symbol} ${m}M`
  }
  if (abs >= 1_000) {
    const k = (abs / 1_000).toFixed(0)
    return num < 0 ? `(${symbol} ${k}K)` : `${symbol} ${k}K`
  }
  return formatCurrency(amount, currency)
}

/**
 * Format amounts based on a display mode.
 *
 *   'millions'  → BWP 4.9M / BWP 900K / BWP 50
 *   'thousands' → BWP 4,900.0K / BWP 900.0K / BWP 50
 *   'full'      → BWP 4,900,000.00 (same as formatCurrency)
 */
export type NumberMode = 'millions' | 'thousands' | 'full'

// Global display mode for formatAmount. Kept in sync by NumberFormatContext
// (the Full/K/M toggle in the TopBar). Every formatAmount() call that does NOT
// pass an explicit mode follows this — so the one toggle drives every report
// page without each call site threading the mode. Default Full per the spec
// (Legakwa 2026-06-09: detail/reconciliation needs exact numbers).
let _globalNumberMode: NumberMode = 'full'
export function setGlobalNumberMode(m: NumberMode): void { _globalNumberMode = m }
export function getGlobalNumberMode(): NumberMode { return _globalNumberMode }

/**
 * Currency-prefixed amount in the chosen (or global) display mode.
 * Spec (Legakwa 2026-06-09):
 *   full       → BWP 125,345.00   · zero BWP 0.00   · neg (BWP 991,203.44)
 *   thousands  → BWP 125K (round to whole) · zero BWP 0 · neg (BWP 991K)
 *   millions   → BWP 0.13M (/1e6, 2dp)     · zero BWP 0.00M · neg (BWP 0.99M)
 * Currency prefix always shown; negatives always in brackets.
 */
export function formatAmount(
  amount: string | number,
  currency: string = 'BWP',
  mode?: NumberMode,
): string {
  const m = mode ?? _globalNumberMode
  const symbol = CURRENCY_SYMBOLS[currency] || currency
  const num = typeof amount === 'string' ? parseFloat(amount) : amount
  if (isNaN(num)) return `${symbol} 0.00`
  const abs = Math.abs(num)

  let body: string
  if (m === 'thousands') {
    body = abs === 0 ? '0' : `${Math.round(abs / 1_000).toLocaleString('en-US')}K`
  } else if (m === 'millions') {
    body = `${(abs / 1_000_000).toFixed(2)}M`
  } else {
    body = abs.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  }
  return num < 0 ? `(${symbol} ${body})` : `${symbol} ${body}`
}

// ─── Date formatting ───────────────────────────────────────────────────────────
// House style: DD-MMM-YYYY, uppercase 3-letter month (e.g. 13-MAY-2026).
// Avoids ambiguity between DD/MM and MM/DD numeric forms.

const MONTHS = [
  'JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN',
  'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC',
]

function pad2(n: number): string {
  return n < 10 ? `0${n}` : String(n)
}

/**
 * Today (or any Date) as YYYY-MM-DD in the BROWSER'S OWN timezone.
 *
 * Use this for a date INPUT default or anything that will be stored as a
 * calendar date. `new Date().toISOString().slice(0, 10)` converts to UTC
 * first, so between midnight and 02:00 Botswana time (UTC+2) it returns
 * YESTERDAY — and a claim opened at 00:30 is then recorded as having arrived
 * the previous day, which throws days-to-process out from the first minute.
 */
export function localYmd(d: Date = new Date()): string {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`
}

export function formatDate(dateStr: string): string {
  if (!dateStr) return '—'
  const d = new Date(dateStr + (dateStr.includes('T') ? '' : 'T00:00:00'))
  if (isNaN(d.getTime())) return dateStr
  return `${pad2(d.getDate())}-${MONTHS[d.getMonth()]}-${d.getFullYear()}`
}

export function formatDateTime(dateStr: string): string {
  if (!dateStr) return '—'
  const d = new Date(dateStr)
  if (isNaN(d.getTime())) return dateStr
  return `${pad2(d.getDate())}-${MONTHS[d.getMonth()]}-${d.getFullYear()} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`
}

// ─── Status colors (light theme — Alpha Direct Design System) ─────────────────
//
// Use background color classes: background at opacity + text in full color
// Follows design system semantic tokens:
//   Success #059669  bg #ECFDF5
//   Warning #D97706  bg #FFFBEB
//   Error   #DC2626  bg #FEF2F2
//   Info    #2563EB  bg #EFF6FF
//   Neutral #6B7280  bg #F3F4F6

export function getStatusColor(status: string): string {
  const s = status?.toLowerCase()
  switch (s) {
    case 'posted':
    case 'confirmed':
    case 'reconciled':
      return 'text-[#2563EB]'
    case 'paid':
    case 'matched':
    case 'auto_matched':
    case 'active':
      return 'text-[#059669]'
    case 'overdue':
    case 'unmatched':
    case 'failed':
      return 'text-[#DC2626]'
    case 'draft':
    case 'pending':
    case 'partial':
      return 'text-[#D97706]'
    case 'excluded':
    case 'voided':
    case 'cancelled':
    case 'inactive':
      return 'text-[#6B7280]'
    case 'open':
      return 'text-[#CC6C00]'
    default:
      return 'text-[#6B7280]'
  }
}

export function getStatusBgColor(status: string): string {
  const s = status?.toLowerCase()
  switch (s) {
    case 'posted':
    case 'confirmed':
    case 'reconciled':
      return 'bg-[#EFF6FF] text-[#2563EB]'
    case 'paid':
    case 'matched':
    case 'auto_matched':
    case 'active':
      return 'bg-[#ECFDF5] text-[#059669]'
    case 'overdue':
    case 'unmatched':
    case 'failed':
      return 'bg-[#FEF2F2] text-[#DC2626]'
    case 'draft':
    case 'pending':
      return 'bg-[#FFFBEB] text-[#D97706]'
    case 'partial':
      return 'bg-[#FFF7ED] text-[#CC6C00]'
    case 'excluded':
    case 'voided':
    case 'cancelled':
    case 'inactive':
      return 'bg-[#F3F4F6] text-[#6B7280]'
    case 'open':
      return 'bg-[#FFF7ED] text-[#CC6C00]'
    default:
      return 'bg-[#F3F4F6] text-[#6B7280]'
  }
}

// ─── Aging bucket colors ───────────────────────────────────────────────────────

export function getBucketColor(bucket: string): string {
  const b = bucket?.toLowerCase()
  switch (b) {
    case 'current':       return 'text-[#059669]'
    case '31-60':
    case '31_60':         return 'text-[#D97706]'
    case '61-90':
    case '61_90':         return 'text-[#CC6C00]'
    case '91-120':
    case '91_120':        return 'text-[#EA580C]'
    case 'over_120':
    case '120+':
    case 'over120':       return 'text-[#DC2626]'
    default:              return 'text-[#6B7280]'
  }
}

export function getBucketBgColor(bucket: string): string {
  const b = bucket?.toLowerCase()
  switch (b) {
    case 'current':       return 'bg-[#ECFDF5] text-[#059669]'
    case '31-60':
    case '31_60':         return 'bg-[#FFFBEB] text-[#D97706]'
    case '61-90':
    case '61_90':         return 'bg-[#FFF7ED] text-[#CC6C00]'
    case '91-120':
    case '91_120':        return 'bg-[#FFF7ED] text-[#EA580C]'
    case 'over_120':
    case '120+':
    case 'over120':       return 'bg-[#FEF2F2] text-[#DC2626]'
    default:              return 'bg-[#F3F4F6] text-[#6B7280]'
  }
}

// ─── Date utilities ────────────────────────────────────────────────────────────

export function isOverdue(dueDateStr: string): boolean {
  if (!dueDateStr) return false
  const due = new Date(dueDateStr + 'T00:00:00')
  const now = new Date()
  now.setHours(0, 0, 0, 0)
  return due < now
}

export function getFyStart(): string {
  const now = new Date()
  const year = now.getFullYear()
  const month = now.getMonth() + 1
  return month >= 7 ? `${year}-07-01` : `${year - 1}-07-01`
}

export function getFyEnd(): string {
  const now = new Date()
  const year = now.getFullYear()
  const month = now.getMonth() + 1
  return month >= 7 ? `${year + 1}-06-30` : `${year}-06-30`
}

export function today(): string {
  // Local date, not UTC — toISOString() flips to yesterday between midnight
  // and 02:00 Botswana time (UTC+2), which then trips the future-date guard.
  const d = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

// ─── CSV Export ────────────────────────────────────────────────────────────────

export function exportToCsv(data: Record<string, any>[], filename: string): void {
  if (!data || data.length === 0) return

  const headers = Object.keys(data[0])
  const csvContent = [
    headers.join(','),
    ...data.map((row) =>
      headers
        .map((header) => {
          const value = row[header]
          const str = value === null || value === undefined ? '' : String(value)
          if (str.includes(',') || str.includes('"') || str.includes('\n')) {
            return `"${str.replace(/"/g, '""')}"`
          }
          return str
        })
        .join(',')
    ),
  ].join('\n')

  const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' })
  const link = document.createElement('a')
  link.href = URL.createObjectURL(blob)
  link.download = filename.endsWith('.csv') ? filename : `${filename}.csv`
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(link.href)
}

// ─── Number parsing ────────────────────────────────────────────────────────────

export function parseAmount(value: string | number | null | undefined): number {
  if (value === null || value === undefined) return 0
  const num = typeof value === 'string' ? parseFloat(value) : value
  return isNaN(num) ? 0 : num
}

export function formatNumber(value: string | number, decimals: number = 2): string {
  const num = typeof value === 'string' ? parseFloat(value) : value
  if (isNaN(num)) return '0.00'
  return num.toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
}

export function sumAmounts(amounts: (string | number)[]): number {
  return amounts.reduce((acc: number, val) => acc + parseAmount(val), 0)
}
