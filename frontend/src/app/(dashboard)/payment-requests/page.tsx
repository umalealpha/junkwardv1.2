'use client'

/**
 * /payment-requests — structured payment authorisations (CFO 2026-07-15).
 *
 * Replaces the payment-authorisation emails. Finance fills a structured form;
 * the backend renders the exact authorisation table, writes a covering summary
 * with the AI, and drops it into the CFO's task inbox as a high-priority task
 * he can pay or reassign (e.g. escalate above his limit to the CEO via the
 * Reassign control on the task).
 */

import { useEffect, useState, useCallback , useRef } from 'react'
import { PaymentSummaryPanel } from '@/components/payments/PaymentSummaryPanel'
import { DropBox } from '@/components/payments/DropBox'
import { PaymentExceptions } from '@/components/payments/PaymentExceptions'
import { ClaimInsightBox } from '@/components/claims/ClaimInsightBox'
import { useRouter, useSearchParams } from 'next/navigation'
import {
  getPaymentRequests, getPaymentRequest, createPaymentRequest, decidePaymentRequest, decidePaymentRequestLines, clearPaymentRequest, getToken,
  markPaymentRejectedOnFnb,
  previewPaymentBulk, approvePaymentBulk, type BulkPaymentPreview, type BulkPaymentResult,
  type PaymentRequestPage,
  parsePaymentPaste, reconcilePaymentsAgainstFnb,
  getFnbEmailCatchup, markPaymentPaidFromBank,
  reconcileFnbList, type FnbListReconcileResult,
  uploadPaymentRequestAttachment, downloadPaymentRequestAttachment, downloadPaymentRequestFnbFile, updateOmniTask, getMyCompanies,
  type PaymentRequestRow, type PaymentRequestDetail, type PaymentLine, type PaymentLineProgress,
  type PaymentBankChange,
  type PaymentCategory, type PaymentRequestStatus, type MeCompany,
  type FnbReconcileResult, type FnbReconcileRow,
  type EmailCatchupResult, type EmailCatchupRow,
  lookupPayeeBank, checkPayeeBankChange, readInvoiceForPaymentRequest,
  getPopRecipients, type PopRecipientOption,
  amendPaymentRequest, cancelPaymentRequest, correctPaymentRequestBranchCode, duplicatePaymentRequest,
  type PaymentLineChange, type PaymentRequestChange,
  getPaymentRegister, downloadPaymentRegister,
  type PaymentRegisterFilters, type PaymentRegisterPage,
  getPaymentHistory,
  type PaymentHistoryFilters, type PaymentHistoryResult, type PaymentHistoryRow,
  requestLoadOverride, listLoadOverrides, decideLoadOverride, type LoadOverride,
} from '@/lib/api'
import { lineFromHistoryRow } from '@/app/(dashboard)/payment-requests/reloadLine'
import { TopBar } from '@/components/layout/TopBar'
import { TaskCompletionModal } from '@/components/TaskCompletionModal'
import { ModalPortal } from '@/components/ui/ModalPortal'
import { PersonPicker } from '@/components/PersonPicker'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { TableWrapper, Table, TableHead, TableBody, TableRow, TableCell, TableHeader } from '@/components/ui/table'
import BulkPaymentUpload from '@/components/payments/BulkPaymentUpload'
import { useTheme } from '@/contexts/ThemeContext'
import { cn, localYmd } from '@/lib/utils'
import { Plus, X, Trash2, Send, FileText, ArrowUpRight, Copy, Paperclip, Download, CheckCircle2, UserPlus, Landmark, AlertTriangle, Mail, ArrowUp, ArrowDown, ChevronsUpDown, History, Search, UploadCloud, ChevronDown } from 'lucide-react'

const CURRENCIES = ['BWP', 'ZAR', 'USD', 'INR']
const CATEGORIES: { value: Exclude<PaymentCategory, ''>; label: string }[] = [
  { value: 'claim',      label: 'Claim payments' },
  { value: 'supplier',   label: 'Supplier payments' },
  { value: 'vendor',     label: 'Vendor payments' },
  { value: 'petty_cash', label: 'Petty cash' },
  { value: 'unicoin',    label: 'Unicoin payments' },
  { value: 'quantum',    label: 'Quantum payments' },
  { value: 'rsa',        label: 'Risk Software Africa' },
  { value: 'veritas',    label: 'Veritas Capital Mgmt' },
  { value: 'adh',        label: 'Alpha Direct Health' },
  { value: 'gce',        label: 'GCE payments' },
  { value: 'other',      label: 'Other' },
]

// Every category label, including the ones nobody may raise by hand. The list the
// CFO authorises from renders whatever category a request carries, so a label is
// needed even where a picker entry is not.
// NOT exported: a Next.js page file may only export the page (and its route
// config), and `next build` rejects any other named export with "does not match
// the required types of a Next.js Page". `tsc --noEmit` passes it happily, which
// is why this only surfaced on the real build.
const CATEGORY_LABELS: Record<string, string> = {
  claim: 'Claim payments',
  supplier: 'Supplier payments',
  vendor: 'Vendor payments',
  petty_cash: 'Petty cash',
  premium_refund: 'Premium refunds',
  erroneous_refund: 'Refund — erroneous payment',
  excess_refund: 'Excess refunds',
  unicoin: 'Unicoin payments',
  staff_loan: 'Staff loan disbursement',
  quantum: 'Quantum payments',
  rsa: 'Risk Software Africa',
  veritas: 'Veritas Capital Mgmt',
  adh: 'Alpha Direct Health',
  gce: 'GCE payments',
  other: 'Other',
}

// Premium refunds are deliberately NOT in CATEGORIES above: the overnight importer
// owns them, and a hand-keyed refund alongside the feed is how the same refund gets
// paid twice.
//
// B8 (CFO spec 2026-09-13): Refund is now its own TOP-LEVEL payment type, beside
// Claims payment and Operations payment — not a category nested inside
// operations, where a reversal was being counted as an operational expense. The
// three refund kinds live in their OWN list below and are deliberately kept out
// of CATEGORIES, because CATEGORIES is what the Operations "Which kind?"
// dropdown renders: a refund must never be reachable from under Operations.
//
// Premium Refund appears in that list but cannot be chosen. It stays
// importer-owned exactly as the comment above says, and the server refuses it
// outright (PAY-REFUND-01) — this is the visible half of that rule, so a person
// looking for premium refunds is told where they come from rather than left
// wondering why the option is missing.
const REFUND_CATEGORY_LIST: {
  value: 'premium_refund' | 'erroneous_refund' | 'excess_refund'
  label: string; handRaised: boolean; hint: string
}[] = [
  { value: 'premium_refund', label: 'Premium Refund', handRaised: false,
    hint: 'Raised automatically overnight from the refunds Finance approved in Graphite — not keyed here.' },
  { value: 'erroneous_refund', label: 'Refund — Erroneous Payment', handRaised: true,
    hint: 'Money that went out in error and is being returned.' },
  { value: 'excess_refund', label: 'Excess Refund', handRaised: true,
    hint: 'An excess collected on a claim and now being returned.' },
]
const REFUND_CATEGORIES: ReadonlySet<string> = new Set<string>(REFUND_CATEGORY_LIST.map(r => r.value))

// The tabs across the top of the list. Until now the page was one flat table, so a
// premium refund arrived among the supplier payments with nothing to separate it
// (CFO 2026-08-11: "we currently have operational payments, claim payments, but we
// do not have a separate tab for premium refunds").
const ENTITY_CATEGORIES = new Set(['unicoin', 'quantum', 'rsa', 'veritas', 'gce'])

const TABS: { key: string; label: string; match: (c?: string) => boolean }[] = [
  { key: 'all',      label: 'All',                 match: () => true },
  { key: 'ops',      label: 'Operational',
    match: c => !!c && c !== 'claim' && !REFUND_CATEGORIES.has(c) && !ENTITY_CATEGORIES.has(c) },
  { key: 'claim',    label: 'Claims',              match: c => c === 'claim' },
  { key: 'entity',   label: 'Entity',              match: c => !!c && ENTITY_CATEGORIES.has(c) },
  { key: 'refund',   label: 'Refunds',             match: c => !!c && REFUND_CATEGORIES.has(c) },
]

// The categories behind each tab, for the "approve all clean in this category"
// bulk action (CFO 2026-08-31). 'all' = every category (undefined = no filter).
const TAB_CATEGORIES: Record<string, string[] | undefined> = {
  all:    undefined,
  ops:    ['supplier', 'vendor', 'petty_cash', 'other'],
  claim:  ['claim'],
  entity: ['unicoin', 'quantum', 'rsa', 'veritas', 'gce'],
  refund: ['premium_refund', 'erroneous_refund', 'excess_refund'],
}

// Status pills. Restyled to the Stitch mockup (557a7689, 2026-09-01): a soft
// tinted pill with a matching ring, rather than a flat block of colour. Same
// statuses, same meanings, same mapping — presentation only.
const PILL = 'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset'

const STATUS_COLOR: Record<string, string> = {
  pending:     `${PILL} bg-[#FEF3C7] text-[#92400E] ring-[#F59E0B]/30`,
  in_progress: `${PILL} bg-[#DBEAFE] text-[#1E40AF] ring-[#3B82F6]/30`,
  done:        `${PILL} bg-[#D1FAE5] text-[#065F46] ring-[#10B981]/30`,
  partial:     `${PILL} bg-[#FED7AA] text-[#9A3412] ring-[#F47C20]/30`,
  blocked:     `${PILL} bg-[#FEE2E2] text-[#991B1B] ring-[#DC2626]/30`,
  cancelled:   `${PILL} bg-[#F3F4F6] text-[#374151] ring-[#9CA3AF]/30`,
}

// Two-stage authorisation status (CFO 2026-07-23).
const REQ_STATUS_COLOR: Record<PaymentRequestStatus, string> = {
  exception:       `${PILL} bg-[#FFFBEB] text-[#92400E] ring-[#F4A623]/40`,
  pending_finance: `${PILL} bg-[#FEF3C7] text-[#92400E] ring-[#F59E0B]/30`,
  pending_cfo:     `${PILL} bg-[#E8EDFA] text-[#1D3270] ring-[#1D3270]/25`,
  rejected:        `${PILL} bg-[#FEE2E2] text-[#991B1B] ring-[#DC2626]/30`,
  paid:            `${PILL} bg-[#D1FAE5] text-[#065F46] ring-[#10B981]/30`,
  cancelled:       `${PILL} bg-[#F3F4F6] text-[#374151] ring-[#9CA3AF]/30`,
}

function money(cur: string, n: number) {
  // Feature 557a7689 (CFO 2026-09-01): every amount on this board is an outgoing
  // payment, so it always shows as a plain positive number — never a minus sign
  // and never accounting brackets. The sign carries no information here.
  return `${cur} ${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

/** One clash reported by the duplicate control (PAY-DUP-01, CFO 2026-08-03) —
 *  which line of this pack, and which request already carries it. */
type DuplicateRow = {
  line: number
  label: string
  amount: string
  clash_kind: string
  clash_ref: string
  clash_status: string
  detail: string
}

// A blank payment line. terms_days defaults to the standard 30-day supplier
// term (PAY-SUP-01) and is ignored for non-supplier categories.
function newLine(): PaymentLine {
  return {
    description: '', gl_code: '', ref: '', amount: '',
    // Who gets the proof of payment for this line (Finance spec 2026-09-08).
    pop_recipient_name: '', pop_recipient_email: '',
    invoice_number: '', invoice_date: '', terms_basis: 'statement',
    terms_days: '30', due_date: '', discount_checked: false,
    claim_number: '',
  }
}

/** Trade terms run from the STATEMENT the invoice lands on, not the invoice
 *  itself: a 24-Jul invoice on 30-day statement terms is due 30 Aug, not 23 Aug.
 *  The due date is derived here and re-derived independently on the server, so a
 *  clerk cannot shorten the term by typing an earlier date. */
function dueFromTerms(invoiceDate: string, termsDays: string, basis: string): string {
  if (!invoiceDate) return ''
  const d = new Date(invoiceDate)
  if (Number.isNaN(d.getTime())) return ''
  const anchor = basis === 'invoice'
    ? d
    : new Date(d.getFullYear(), d.getMonth() + 1, 0)   // month-end = statement
  anchor.setDate(anchor.getDate() + (Number(termsDays) || 30))
  return `${anchor.getFullYear()}-${String(anchor.getMonth() + 1).padStart(2, '0')}-${String(anchor.getDate()).padStart(2, '0')}`
}

// ─────────────────────────────────────────────────────────────────────────────
// CFO one-look brief (redesign 2026-08-26) — PRESENTATION ONLY.
// Every figure below is DERIVED client-side from the same list already fetched
// by getPaymentRequests(); no new endpoint, no changed value. `money()` above
// still renders every amount, byte-identical to before.
// ─────────────────────────────────────────────────────────────────────────────

// Sort keys for the list — DISPLAY ONLY. Sorting reorders rows on screen; it
// never changes which rows show (tab + hide-cleared still own that) or a value.
type SortKey = 'ref' | 'payee' | 'subject' | 'category' | 'entity' | 'total' | 'created_by' | 'status'

/** Row "line lights" (CFO 2026-08-31, Fable idea #1): a tiny segmented bar
 *  showing a batch's per-payee state — green approved, amber held, red rejected,
 *  grey pending — so a request stuck on one held payee is visible from the queue.
 *  Only for multi-payee packs; single-line requests show nothing. */
/**
 * POP Recipient for ONE payment line (Finance spec 2026-09-08).
 *
 * The dropdown is filled from contacts Omni already holds for this claim and
 * payee — the vendor register's remembered POP address, the contact book, the
 * claimant named on the claim, and the Accounts default. "Someone else" drops to
 * free text for a name and address.
 *
 * Anything off that list is NOT blocked here: the server stamps it off-list and
 * a second approver clears it at sign-off. The red banner below is the same
 * pattern the changed-account and first-payment flags already use, so the
 * warning reads as one system rather than a new idea.
 */
function PopRecipientField({ claimNumber, payee, name, email, onChange }: {
  claimNumber?: string
  payee: string
  name: string
  email: string
  onChange: (v: { pop_recipient_name: string; pop_recipient_email: string }) => void
}) {
  const [options, setOptions] = useState<PopRecipientOption[]>([])
  const [manual, setManual] = useState(false)

  // Re-read whenever the claim or the payee changes — those are what decide who
  // is linked. Debounced, because the payee is typed a character at a time.
  useEffect(() => {
    let alive = true
    const t = setTimeout(() => {
      getPopRecipients(claimNumber || '', payee || '')
        .then(r => { if (alive) setOptions(r.options || []) })
        .catch(() => { /* the list is a convenience; free text still works */ })
    }, 350)
    return () => { alive = false; clearTimeout(t) }
  }, [claimNumber, payee])

  const known = options.find(o => o.email.toLowerCase() === (email || '').trim().toLowerCase())
  const offList = !!email.trim() && !known

  return (
    <div className="space-y-1">
      <label className="block text-[11px] text-[#6B7280] mb-0.5">
        POP recipient <span className="text-[#991B1B]">*</span>
      </label>
      {manual ? (
        <div className="grid grid-cols-2 gap-2">
          <input value={name} placeholder="Name"
                 onChange={e => onChange({ pop_recipient_name: e.target.value,
                                           pop_recipient_email: email })}
                 className="w-full border rounded px-2 py-1 text-sm bg-background" />
          <input value={email} placeholder="Email" type="email"
                 onChange={e => onChange({ pop_recipient_name: name,
                                           pop_recipient_email: e.target.value })}
                 className="w-full border rounded px-2 py-1 text-sm bg-background" />
        </div>
      ) : (
        <select value={known ? known.email : ''}
                onChange={e => {
                  if (e.target.value === '__other__') { setManual(true); return }
                  const pick = options.find(o => o.email === e.target.value)
                  onChange({ pop_recipient_name: pick?.name || '',
                             pop_recipient_email: pick?.email || '' })
                }}
                className="w-full border rounded px-2 py-1 text-sm bg-background">
          <option value="">Choose who gets the proof of payment…</option>
          {options.map(o => (
            <option key={o.email} value={o.email}>
              {o.name || o.email} — {o.source_label}
            </option>
          ))}
          <option value="__other__">Someone else — type a name and email…</option>
        </select>
      )}
      {manual && (
        <button type="button" onClick={() => setManual(false)}
                className="text-[11px] text-[#B45309] hover:text-[#92400E]">
          ← pick from the contacts on file instead
        </button>
      )}
      {offList && (
        <div className="rounded border-2 border-[#B91C1C] bg-[#FEF2F2] px-2 py-1.5">
          <div className="text-[11px] font-bold text-[#B91C1C] uppercase tracking-wide">
            Not a contact on file
          </div>
          <p className="text-[11px] text-[#7F1D1D]">
            Omni holds no record of <strong>{email.trim()}</strong> for this claim or
            payee. You can still submit — a second approver has to check and clear it
            before the payment is signed off.
          </p>
        </div>
      )}
    </div>
  )
}

function LineLights({ lp }: { lp?: PaymentLineProgress }) {
  if (!lp || (lp.total || 0) <= 1) return null
  const approved = lp.approved || 0, held = lp.held || 0, rejected = lp.rejected || 0, pending = lp.pending || 0
  const decided = approved + held + rejected
  const seg = (n: number, color: string) =>
    n > 0 ? <span style={{ flexGrow: n, background: color }} /> : null
  return (
    <div className="mt-1"
         title={`${approved} approved · ${held} held · ${rejected} rejected · ${pending} pending`}>
      <div className="flex h-1.5 w-24 rounded-full overflow-hidden" style={{ background: '#E5E7EB' }}>
        {seg(approved, '#059669')}{seg(held, '#F59E0B')}{seg(rejected, '#DC2626')}{seg(pending, '#D1D5DB')}
      </div>
      {decided > 0 && (
        <div className="text-[10px] text-[#6B7280] mt-0.5">
          {approved}/{lp.total} authorised{held ? ` · ${held} held` : ''}{rejected ? ` · ${rejected} rejected` : ''}
        </div>
      )}
    </div>
  )
}

// "Why is this still open?" chip — a plain-English reason per open payment, so an
// unpaid queue never looks like a broken auto-closer (CFO 2026-09-05). Tone drives
// the colour: reject (red) · check (amber, needs your eye) · paid (green, closing) ·
// exception (orange) · wait (grey, just waiting on your FNB sign-off).
const WHY_OPEN_STYLE: Record<string, { bg: string; fg: string }> = {
  reject:    { bg: '#FEE2E2', fg: '#991B1B' },
  check:     { bg: '#FEF3C7', fg: '#92400E' },
  paid:      { bg: '#DCFCE7', fg: '#166534' },
  exception: { bg: '#FFEDD5', fg: '#9A3412' },
  wait:      { bg: '#F1F5F9', fg: '#475569' },
}
function WhyOpen({ text, tone }: { text?: string; tone?: string }) {
  if (!text) return null
  const s = WHY_OPEN_STYLE[tone || 'wait'] || WHY_OPEN_STYLE.wait
  const Icon = tone === 'paid' ? CheckCircle2
    : (tone === 'reject' || tone === 'check') ? AlertTriangle : null
  return (
    <span className="inline-flex items-start gap-1 rounded-md px-2 py-0.5 text-[11px] font-medium leading-tight mt-1 whitespace-normal max-w-[18rem]"
          style={{ background: s.bg, color: s.fg }}>
      {Icon && <Icon className="w-3 h-3 mt-[1px] shrink-0" />}
      <span>{text}</span>
    </span>
  )
}

function SortHeader({ label, sortField, sortKey, sortDir, onSort, align = 'left' }: {
  label: string; sortField: SortKey; sortKey: SortKey | null
  sortDir: 'asc' | 'desc'; onSort: (k: SortKey) => void; align?: 'left' | 'right'
}) {
  const active = sortKey === sortField
  return (
    <TableHeader>
      <button type="button" onClick={() => onSort(sortField)}
        className={cn('inline-flex items-center gap-1 hover:opacity-70 transition-opacity',
                      align === 'right' && 'w-full flex-row-reverse')}>
        {label}
        {active
          ? (sortDir === 'asc' ? <ArrowUp className="w-3 h-3" /> : <ArrowDown className="w-3 h-3" />)
          : <ChevronsUpDown className="w-3 h-3 opacity-40" />}
      </button>
    </TableHeader>
  )
}

export default function PaymentRequestsPage() {
  const router = useRouter()
  const [rows, setRows] = useState<PaymentRequestRow[]>([])
  const [isCfo, setIsCfo] = useState(false)
  const [isFirstApprover, setIsFirstApprover] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showNew, setShowNew] = useState(false)
  const [showDropBox, setShowDropBox] = useState(false)
  // B7 — copying a register line into a new Draft.
  const [copyingId, setCopyingId] = useState<string | null>(null)
  const [copyMsg, setCopyMsg] = useState<string | null>(null)
  const [showExceptions, setShowExceptions] = useState(false)
  // Deep link for committee members: /payment-requests?exceptions=1 opens the
  // committee board straight away (menu, search palette and the committee email).
  // useSearchParams (not window.location on mount) so the link also works when the
  // member is ALREADY on this page — Next only swaps the query then, no remount.
  const searchParams = useSearchParams()
  useEffect(() => {
    if (searchParams.get('exceptions') === '1') setShowExceptions(true)
  }, [searchParams])
  const [showFnb, setShowFnb] = useState(false)
  const [showCatchup, setShowCatchup] = useState(false)
  const [showFnbList, setShowFnbList] = useState(false)
  const [showRegister, setShowRegister] = useState(false)
  // Load a list of payments from a file instead of typing them one at a time
  // (Legakwa Ntabeni 2026-09-11). Separate-per-payee by default; see the panel.
  const [showBulk, setShowBulk] = useState(false)
  const [openId, setOpenId] = useState<string | null>(null)
  // Was useState(false), which rendered "No payment requests yet." on load and
  // hid the entire payment estate from the approver (CFO 2026-08-09). Inverted:
  // everything shows, and the CFO ticks a box to HIDE what is settled.
  const [hideCleared, setHideCleared] = useState(false)
  // What the list is actually scoped to, in the API's own words. Rendering it
  // ends the "By company says 17 but I see 2" confusion (Kago 2026-08-29): the
  // tile counts the whole company; this list is scoped to your role.
  const [showing, setShowing] = useState('')
  // Which slice of the list we are on. The server sends the TRUE total, so the
  // screen can never again imply that a truncated list is the whole list.
  const [offset, setOffset] = useState(0)
  const [pageInfo, setPageInfo] = useState<PaymentRequestPage | null>(null)
  // How many payments the bank rejected across everything this viewer can see —
  // usually still marked "paid", so they hide in the default list unless counted.
  const [bankRejected, setBankRejected] = useState(0)
  const [tab, setTab] = useState('all')
  // Bulk "approve all clean in this category" (CFO 2026-08-31).
  const [bulkPreview, setBulkPreview] = useState<BulkPaymentPreview | null>(null)
  const [bulkResult, setBulkResult] = useState<BulkPaymentResult | null>(null)
  const [bulkBusy, setBulkBusy] = useState(false)
  const [bulkErr, setBulkErr] = useState<string | null>(null)
  const { theme, reduceMotion } = useTheme()
  const [sortKey, setSortKey] = useState<SortKey | null>(null)
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc')
  // Feature 557a7689 (CFO 2026-09-01): a real filter bar over the list — text
  // search (payee / ref / subject / raiser / entity), a company filter, a status
  // filter and an amount range. Every filter defaults to empty, so an untouched
  // bar leaves the list byte-identical to before — this is purely additive.
  const [fltQ, setFltQ] = useState('')
  const [fltEntity, setFltEntity] = useState('')
  const [fltStatus, setFltStatus] = useState('')
  // Gap in the first pass at 557a7689: text search caught a person's name only
  // if it was typed exactly, so "filter by person" wasn't really answered. A
  // dedicated dropdown, same pattern as the company one below.
  const [fltCreatedBy, setFltCreatedBy] = useState('')
  const [fltMin, setFltMin] = useState('')
  const [fltMax, setFltMax] = useState('')
  const showCleared = !hideCleared
  // The filter changes the result set, so a stale offset would land on the wrong
  // page (or past the end of a shorter list). Always return to the first page.
  const setHideClearedPaged = (v: boolean) => { setHideCleared(v); setOffset(0) }
  const activeTab = TABS.find(t => t.key === tab) ?? TABS[0]
  const entityOptions = Array.from(new Set(rows.map(r => r.entity).filter(Boolean))).sort()
  const raiserOptions = Array.from(new Set(rows.map(r => r.created_by).filter(Boolean))).sort()
  const visible = rows.filter(r => activeTab.match(r.category)).filter(r => {
    if (fltQ.trim()) {
      const hay = `${r.ref} ${r.payee} ${r.subject} ${r.created_by} ${r.entity}`.toLowerCase()
      if (!hay.includes(fltQ.trim().toLowerCase())) return false
    }
    if (fltEntity && r.entity !== fltEntity) return false
    if (fltCreatedBy && r.created_by !== fltCreatedBy) return false
    if (fltStatus && r.status !== fltStatus) return false
    const amt = Math.abs(Number(r.total) || 0)
    if (fltMin && amt < Number(fltMin)) return false
    if (fltMax && amt > Number(fltMax)) return false
    return true
  })

  // Amount-bar scale (PRESENTATION ONLY) — the widest OPEN request drives the
  // inline bar width in the amount column. No value changed, no new request.
  const OPEN_STATUSES: PaymentRequestStatus[] = ['pending_finance', 'pending_cfo']
  const openRows = rows.filter(r => !!r.status && OPEN_STATUSES.includes(r.status))
  const maxOpenTotal = openRows.reduce((m, r) => Math.max(m, Number(r.total) || 0), 0)

  // Display-only sort of the visible rows (never mutates, never re-filters).
  const toggleSort = (k: SortKey) => {
    if (sortKey === k) setSortDir(d => (d === 'asc' ? 'desc' : 'asc'))
    else { setSortKey(k); setSortDir('asc') }
  }
  const sortValue = (r: PaymentRequestRow): string | number => {
    switch (sortKey) {
      case 'total':      return Number(r.total) || 0
      case 'ref':        return r.ref || ''
      case 'payee':      return r.payee || ''
      case 'subject':    return r.subject || ''
      case 'category':   return r.category_label || (r.category ? CATEGORY_LABELS[r.category] : '') || ''
      case 'entity':     return r.entity || ''
      case 'created_by': return r.created_by || ''
      case 'status':     return r.status_label || r.status || ''
      default:           return ''
    }
  }
  const sortedVisible = sortKey == null
    ? visible
    : [...visible].sort((a, b) => {
        const av = sortValue(a), bv = sortValue(b)
        const cmp = typeof av === 'number' && typeof bv === 'number'
          ? av - bv
          : String(av).localeCompare(String(bv))
        return sortDir === 'asc' ? cmp : -cmp
      })

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const d = await getPaymentRequests(showCleared, offset)
      setRows(d.requests); setIsCfo(d.is_cfo); setIsFirstApprover(!!d.is_first_approver)
      setShowing(d.showing || ''); setPageInfo(d.page ?? null)
      setBankRejected(d.bank_rejected_count || 0)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load payment requests')
    } finally { setLoading(false) }
  }, [showCleared, offset])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load()
  }, [load, router])

  // Preview the clean packs for the current category tab, then open the confirm.
  async function openBulk() {
    setBulkErr(null); setBulkResult(null); setBulkBusy(true)
    try {
      const p = await previewPaymentBulk(TAB_CATEGORIES[tab])
      setBulkPreview(p)
    } catch (e) {
      setBulkErr(e instanceof Error ? e.message : 'Could not load the bulk preview')
    } finally { setBulkBusy(false) }
  }
  // Authorise every clean pack in the tab, echoing the total the CFO was shown
  // (the server refuses if its recount differs — nothing changes underneath him).
  async function confirmBulk() {
    if (!bulkPreview) return
    setBulkBusy(true); setBulkErr(null)
    try {
      const res = await approvePaymentBulk(bulkPreview.ready_total, TAB_CATEGORIES[tab])
      setBulkResult(res); setBulkPreview(null)
      await load()
    } catch (e) {
      const body = (e as { body?: { detail?: string } })?.body
      setBulkErr(body?.detail || (e instanceof Error ? e.message : 'Could not authorise'))
    } finally { setBulkBusy(false) }
  }

  // B7 — copy a register line into a new Draft. The server does the copying and
  // the field-reset rules; this only asks it to and says where the draft went.
  // The endpoint returns the new draft's own fields flat (plus source_ref), not
  // a canned message, so the notice is built here from what it gave back.
  async function copyRequest(id: string) {
    setCopyingId(id); setCopyMsg(null); setError(null)
    try {
      const res = await duplicatePaymentRequest(id)
      setCopyMsg(
        `Copied ${res.source_ref} into a new draft, ${res.ref}. Key the new `
        + 'invoice number and amount, attach the new invoice, then submit it '
        + '— it goes through the full approval route from the start, and the '
        + `duplicate check runs on it again. Nothing on ${res.source_ref} has changed.`)
      setShowDropBox(true)   // the drafts list — where the copy is finished
    } catch (e) {
      // `copyMsg` is rendered ONLY as the Drop Box's notice, and the Drop Box
      // is opened only on the success path above — so a failure written there
      // would never reach the screen. Failures go to the page's own error
      // banner, which is always mounted.
      const body = (e as { body?: { detail?: string } })?.body
      setError(body?.detail || (e instanceof Error ? e.message : 'Could not copy that request.'))
    } finally { setCopyingId(null) }
  }

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Payment Requests"
        breadcrumbs={[{ label: 'Payment Requests' }]}
        actions={
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => setShowBulk(true)}
                    title="Load a list of payments from a file instead of typing them one at a time">
              <UploadCloud className="w-4 h-4 mr-1" /> Upload a list
            </Button>
            {/* Large Payment Authorisation (CFO 2026-09-11). Same audience as
                the other Finance-and-CFO actions on this bar, because raising one
                is limited to the payment approvers and the CFO. */}
            {(isCfo || isFirstApprover) && (
              <Button variant="outline" size="sm" onClick={() => router.push('/payments/large')}
                      title="Package the large claim payments already loaded to FNB into one authorisation request">
                <Landmark className="w-4 h-4 mr-1" /> Request large claim payment
              </Button>
            )}
            {(isCfo || isFirstApprover) && (
              <Button variant="outline" size="sm" onClick={() => setShowRegister(true)}
                      title="Search the whole payment register — any status, any date — and export it for audit">
                <History className="w-4 h-4 mr-1" /> Payment history
              </Button>
            )}
            {(isCfo || isFirstApprover) && (
              <Button variant="outline" size="sm" onClick={() => setShowCatchup(true)}
                      title="Read your FNB payment-confirmation emails and clear the requests the bank has already paid">
                <Mail className="w-4 h-4 mr-1" /> Check my email
              </Button>
            )}
            {(isCfo || isFirstApprover) && (
              <Button variant="outline" size="sm" onClick={() => setShowFnb(true)}
                      title="Paste your FNB pending list to auto-close payments already paid in the bank">
                <Landmark className="w-4 h-4 mr-1" /> Check against FNB
              </Button>
            )}
            {(isCfo || isFirstApprover) && (
              <Button variant="outline" size="sm" onClick={() => setShowFnbList(true)}
                      title="Upload your FNB Batch Payments PDF — Omni closes every payment that is no longer on it">
                <FileText className="w-4 h-4 mr-1" /> Close paid (FNB list)
              </Button>
            )}
            <Button variant="outline" size="sm" onClick={() => setShowDropBox(true)}
                    title="Drop invoices — Omni reads each and makes a DRAFT to check, so you check a request instead of typing it">
              <UploadCloud className="w-4 h-4 mr-1" /> Drop Box
            </Button>
            <Button variant="outline" size="sm" onClick={() => setShowExceptions(true)}
                    title="Payment exceptions (a changed bank account, a possible duplicate, a claim already closed in Graphite) the committee decides — never blocks a payment">
              <AlertTriangle className="w-4 h-4 mr-1" /> Exceptions
            </Button>
            <Button variant="primary" size="sm"
                    onClick={() => setShowNew(true)}>
              <Plus className="w-4 h-4 mr-1" /> New payment request
            </Button>
          </div>
        }
      />
      <main className="flex-1 p-6 space-y-4">
        <p className="text-sm text-[#6B7280] max-w-2xl">
          {isCfo
            ? 'Payment authorisations that have passed finance sign-off. Open one to see the full table, then record your decision — or reassign it (e.g. escalate above your limit) from the task. Money leaves only when you authorise it at FNB.'
            : isFirstApprover
            ? 'Finance sign-off queue. Open a request to review the full table, then approve it (it then goes to the CFO) or reject it back to the sender.'
            : 'Raise a payment authorisation here instead of emailing it. It goes to Finance (Pako, Kago or Legakwa) for sign-off first, then to the CFO for payment.'}
        </p>

        {/* Bank-rejected payments. These usually still read "paid" in the
            workflow, so they never surface on their own — this red strip is the
            one place Finance sees that the bank bounced a payment (no money
            moved). Opens the full history to find and fix them. */}
        {bankRejected > 0 && (
          <button onClick={() => setShowRegister(true)}
                  className="w-full flex items-center gap-2 rounded-md border-2 border-[#DC2626] bg-[#FEE2E2] px-3 py-2 text-left text-sm font-medium text-[#991B1B] hover:bg-[#FECACA] transition-colors">
            <AlertTriangle className="w-4 h-4 shrink-0" />
            {bankRejected} payment{bankRejected === 1 ? '' : 's'} rejected by the bank — no money moved. Click to view and fix.
          </button>
        )}

        {/* One-look summary, pinned above the list — the CFO approves for seven
            accountants and should not open each request to know what is waiting
            (CFO 2026-08-09). Same figures as the 09:30 email. */}
        <PaymentSummaryPanel onOpenRef={(ref) => {
          // Tap a request in the summary (heat-strip / longest-waiting / AI text)
          // → open its detail drawer. rows holds the full set, so the pending
          // ones the summary lists are always found.
          const r = rows.find(x => x.ref === ref)
          if (r) setOpenId(r.id)
        }} />

        <div className="flex flex-wrap items-center gap-1 border-b border-[#E5E7EB]">
          {TABS.map(t => {
            const n = rows.filter(r => t.match(r.category)).length
            return (
              <button key={t.key} onClick={() => setTab(t.key)}
                      className={`px-3 py-2 text-sm -mb-px border-b-2 transition-colors ${
                        tab === t.key
                          ? 'border-[#F47C20] text-[#1D3270] font-medium'
                          : 'border-transparent text-[#6B7280] hover:text-[#1D3270]'}`}>
                {t.label}
                <span className="ml-1.5 text-xs text-[#9CA3AF] tabular-nums">{n}</span>
              </button>
            )
          })}
        </div>

        {/* Filter bar (CFO 2026-09-01, 557a7689) — search + person + company +
            status + amount range, in the navy/orange house style. Boxed the
            same way as the Stitch mockup rather than floating loose on the
            page background. Sits over the active category tab; the Clear
            chip resets it to the full list. */}
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-[#E5E7EB] bg-white p-2 shadow-sm">
          <div className="relative min-w-[200px] flex-1">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[#9CA3AF]" />
            <input value={fltQ} onChange={e => setFltQ(e.target.value)}
                   placeholder="Search payee, ref, subject, raiser…" aria-label="Search payee, reference, subject or raiser"
                   className="w-full pl-8 pr-3 py-1.5 text-sm rounded-lg border border-[#D1D5DB] text-[#1D3270] placeholder:text-[#9CA3AF] focus:border-[#F47C20] focus:outline-none focus:ring-1 focus:ring-[#F47C20]" />
          </div>
          {/* QC 2026-09-01 (axe select-name): all three dropdowns below had no
              accessible name — a screen reader announced them as blank. The
              visible "Anyone raised it" / "All companies" / "All statuses"
              default option reads fine on screen but isn't a real label. */}
          <select value={fltCreatedBy} onChange={e => setFltCreatedBy(e.target.value)} aria-label="Filter by who raised it"
                  className="px-2 py-1.5 text-sm rounded-lg border border-[#D1D5DB] text-[#374151] bg-white focus:border-[#F47C20] focus:outline-none">
            <option value="">Anyone raised it</option>
            {raiserOptions.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
          <select value={fltEntity} onChange={e => setFltEntity(e.target.value)} aria-label="Filter by company"
                  className="px-2 py-1.5 text-sm rounded-lg border border-[#D1D5DB] text-[#374151] bg-white focus:border-[#F47C20] focus:outline-none">
            <option value="">All companies</option>
            {entityOptions.map(en => <option key={en} value={en}>{en}</option>)}
          </select>
          <select value={fltStatus} onChange={e => setFltStatus(e.target.value)} aria-label="Filter by status"
                  className="px-2 py-1.5 text-sm rounded-lg border border-[#D1D5DB] text-[#374151] bg-white focus:border-[#F47C20] focus:outline-none">
            <option value="">All statuses</option>
            <option value="exception">With the committee</option>
            <option value="pending_finance">Pending finance</option>
            <option value="pending_cfo">Pending CFO</option>
            <option value="paid">Paid</option>
            <option value="rejected">Rejected</option>
            <option value="cancelled">Cancelled</option>
          </select>
          <input value={fltMin} onChange={e => setFltMin(e.target.value)} inputMode="numeric"
                 placeholder="Min" aria-label="Minimum amount"
                 className="w-[92px] px-2 py-1.5 text-sm rounded-lg border border-[#D1D5DB] text-[#1D3270] placeholder:text-[#9CA3AF] focus:border-[#F47C20] focus:outline-none" />
          <input value={fltMax} onChange={e => setFltMax(e.target.value)} inputMode="numeric"
                 placeholder="Max" aria-label="Maximum amount"
                 className="w-[92px] px-2 py-1.5 text-sm rounded-lg border border-[#D1D5DB] text-[#1D3270] placeholder:text-[#9CA3AF] focus:border-[#F47C20] focus:outline-none" />
          {(fltQ || fltCreatedBy || fltEntity || fltStatus || fltMin || fltMax) && (
            <button type="button"
                    onClick={() => { setFltQ(''); setFltCreatedBy(''); setFltEntity(''); setFltStatus(''); setFltMin(''); setFltMax('') }}
                    className="px-2 py-1.5 text-xs font-medium text-[#6B7280] hover:text-[#1D3270]">
              Clear
            </button>
          )}
        </div>

        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-xs text-[#6B7280] cursor-pointer select-none">
              <input type="checkbox" checked={hideCleared}
                     onChange={e => setHideClearedPaged(e.target.checked)}
                     className="rounded border-[#D1D5DB]" />
              Hide paid &amp; cleared requests
            </label>
            {showing && (
              <p className="text-xs text-[#9CA3AF]">
                Showing: {showing}. The “By company” tile above counts the whole company.
              </p>
            )}
            {/* The honest count. This list used to stop at 200 rows and say
                nothing, so 178 of 378 requests could drop off the bottom
                unnoticed (CFO 2026-09-15). Same pager as the register below. */}
            {pageInfo && pageInfo.total > 0 && (
              <div className="flex items-center gap-3 text-xs text-[#6B7280]">
                <span>
                  Showing {pageInfo.offset + 1}–{pageInfo.offset + pageInfo.returned} of{' '}
                  {pageInfo.total.toLocaleString('en')}
                </span>
                {(pageInfo.has_more || pageInfo.offset > 0) && (
                  <div className="flex items-center gap-2">
                    <Button variant="outline" size="sm" disabled={pageInfo.offset === 0 || loading}
                            onClick={() => setOffset(Math.max(0, pageInfo.offset - pageInfo.limit))}>Previous</Button>
                    <Button variant="outline" size="sm" disabled={!pageInfo.has_more || loading}
                            onClick={() => setOffset(pageInfo.offset + pageInfo.limit)}>Next</Button>
                  </div>
                )}
              </div>
            )}
          </div>
          {/* Approve every CLEAN pack in this category, in one press (CFO
              2026-08-31). Previews first (count + total + any blocked); the CFO
              confirms the exact figure before anything is authorised. */}
          {isCfo && (
            <Button size="sm" variant="outline" onClick={openBulk}
                    loading={bulkBusy && !bulkPreview && !bulkResult}>
              <CheckCircle2 className="w-4 h-4 mr-1" />
              Approve all clean{tab !== 'all' ? ` · ${activeTab.label}` : ''}
            </Button>
          )}
        </div>

        {error && (
          <Card className="border-red-300 bg-red-50/40">
            <CardContent className="py-3 text-sm text-red-700">{error}</CardContent>
          </Card>
        )}

        {/* PAY-WIN-02 (CFO 2026-09-01): the CFO's queue of staff asking to load
            outside the morning window. Also the record of who keeps trying. */}
        {isCfo && <LoadOverrideQueue />}

        <Card>
          <CardContent className="p-0">
            {loading ? (
              <div className="py-10 text-center text-sm text-[#9CA3AF]">Loading…</div>
            ) : visible.length === 0 ? (
              <div className="py-10 text-center text-sm text-[#9CA3AF]">
                <FileText className="w-8 h-8 mx-auto mb-2 opacity-40" />
                {rows.length === 0
                  ? 'No payment requests yet.'
                  : 'Nothing in this tab.'}
              </div>
            ) : (
              <TableWrapper>
                <Table>
                  <TableHead>
                    <TableRow>
                      <SortHeader label="Ref"     sortField="ref"     sortKey={sortKey} sortDir={sortDir} onSort={toggleSort} />
                      {/* Who the money goes to — the most important thing to the
                          CFO when authorising (CFO 2026-08-31: "I need to know who
                          I am paying, that's more important"). 'From' is the raiser. */}
                      <SortHeader label="Paying"  sortField="payee"   sortKey={sortKey} sortDir={sortDir} onSort={toggleSort} />
                      <SortHeader label="Subject" sortField="subject" sortKey={sortKey} sortDir={sortDir} onSort={toggleSort} />
                      <SortHeader label="Type"    sortField="category" sortKey={sortKey} sortDir={sortDir} onSort={toggleSort} />
                      <SortHeader label="Entity"  sortField="entity"  sortKey={sortKey} sortDir={sortDir} onSort={toggleSort} />
                      <SortHeader label="Amount"  sortField="total"   sortKey={sortKey} sortDir={sortDir} onSort={toggleSort} align="right" />
                      {isCfo && <SortHeader label="From" sortField="created_by" sortKey={sortKey} sortDir={sortDir} onSort={toggleSort} />}
                      <SortHeader label="Status"  sortField="status"  sortKey={sortKey} sortDir={sortDir} onSort={toggleSort} />
                      <TableHeader />
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {sortedVisible.map(r => {
                      const pct = maxOpenTotal > 0 ? Math.min(100, (Number(r.total) / maxOpenTotal) * 100) : 0
                      return (
                        <TableRow key={r.id} clickable onClick={() => setOpenId(r.id)}>
                          <TableCell className="font-mono text-xs">
                            {r.ref}
                            <LineLights lp={r.line_progress} />
                          </TableCell>
                          {/* Who he is paying — the headline fact when authorising. */}
                          <TableCell className="whitespace-normal font-semibold" style={{ color: theme.text }}>{r.payee || '—'}</TableCell>
                          <TableCell className="whitespace-normal text-xs" style={{ color: theme.t2 }}>{r.subject}</TableCell>
                          <TableCell className="text-xs" style={{ color: theme.t2 }}>
                            {r.category_label
                              || (r.category ? CATEGORY_LABELS[r.category] : '')
                              || '—'}
                          </TableCell>
                          <TableCell className="text-xs" style={{ color: theme.t2 }}>{r.entity}</TableCell>
                          <TableCell className="text-right tabular-nums">
                            <div className="flex items-center justify-end gap-2">
                              <span className="hidden sm:block h-1.5 rounded-full overflow-hidden shrink-0"
                                    style={{ width: 56, background: theme.g100 }}>
                                <span className="block h-full rounded-full"
                                      style={{ width: `${pct}%`, background: theme.orange }} />
                              </span>
                              {money(r.currency, Number(r.total))}
                            </div>
                          </TableCell>
                          {isCfo && <TableCell className="text-xs" style={{ color: theme.t2 }}>{r.created_by}</TableCell>}
                          <TableCell>
                            <span className={r.status ? REQ_STATUS_COLOR[r.status] : STATUS_COLOR.pending}>
                              {r.status_label || (r.task_status || 'pending').replace('_', ' ')}
                            </span>
                            {/* Plain-English "why is this still open?" for open rows;
                                closed rows that the bank rejected keep the red pill. */}
                            {r.why_open
                              ? <div><WhyOpen text={r.why_open} tone={r.why_open_tone} /></div>
                              : r.bank_rejected && (
                                <span className={`ml-1 gap-0.5 font-semibold ${PILL} bg-[#FEE2E2] text-[#991B1B] ring-[#DC2626]/30`}>
                                  <AlertTriangle className="w-3 h-3" /> Bank rejected
                                </span>
                              )}
                          </TableCell>
                          {/* B7 — copy this line into a new Draft. Tied to the
                              row the user clicked, never to a blank form. It
                              must not open the row, hence stopPropagation. */}
                          <TableCell className="text-right whitespace-nowrap" style={{ color: theme.t3 }}>
                            <button type="button"
                                    onClick={e => { e.stopPropagation(); void copyRequest(r.id) }}
                                    disabled={copyingId === r.id}
                                    title="Copy this request into a new draft — new reference, blank amount and invoice number, full approval route from the start"
                                    className="mr-2 inline-flex items-center gap-1 rounded border border-[#E5E7EB] px-1.5 py-0.5 text-[11px] hover:bg-[#F9FAFB] disabled:opacity-40">
                              <Copy className="w-3 h-3" /> {copyingId === r.id ? 'Copying…' : 'Copy'}
                            </button>
                            ›
                          </TableCell>
                        </TableRow>
                      )
                    })}
                  </TableBody>
                </Table>
              </TableWrapper>
            )}
          </CardContent>
        </Card>
      </main>

      {showNew && (
        <NewPaymentModal
          onClose={() => setShowNew(false)}
          onSaved={async () => { setShowNew(false); await load() }}
        />
      )}
      {showDropBox && (
        <DropBox onClose={() => { setShowDropBox(false); setCopyMsg(null) }} onSubmitted={load}
                 notice={copyMsg} />
      )}
      {showExceptions && (
        <PaymentExceptions onClose={() => { setShowExceptions(false); if (searchParams.get('exceptions') === '1') router.replace('/payment-requests') }} onChanged={load} />
      )}
      {openId && (
        <PaymentDetailDrawer id={openId} isCfo={isCfo} onClose={() => setOpenId(null)} onChanged={load} />
      )}
      {showFnb && (
        <FnbReconcileModal onClose={() => setShowFnb(false)} onCleared={load} />
      )}
      {showCatchup && (
        <EmailCatchupModal onClose={() => setShowCatchup(false)} onCleared={load} />
      )}
      {showFnbList && (
        <FnbListModal onClose={() => setShowFnbList(false)} onCleared={load} />
      )}
      {showBulk && (
        <BulkPaymentUpload
          category={(tab as PaymentCategory) || 'supplier'}
          entity={entityOptions[0] || 'Alpha Direct Insurance Co. (Pty) Ltd'}
          onClose={() => setShowBulk(false)}
          onCreated={() => { load() }}
        />
      )}
      {showRegister && (
        <PaymentRegisterModal onClose={() => setShowRegister(false)}
                              onOpenRequest={(id) => { setShowRegister(false); setOpenId(id) }} />
      )}

      {/* Bulk approve-by-category: confirm (with the exact figure) then result. */}
      {(bulkPreview || bulkResult || bulkErr) && (
        <ModalPortal>
          <div className="fixed inset-0 bg-black/40 z-[200] flex items-center justify-center p-4"
               onClick={() => { if (!bulkBusy) { setBulkPreview(null); setBulkResult(null); setBulkErr(null) } }}>
            <div className="bg-white rounded-xl w-full max-w-lg p-6 shadow-2xl" onClick={e => e.stopPropagation()}>
              {bulkResult ? (
                <>
                  <h3 className="text-lg font-bold mb-2">Authorised</h3>
                  <p className="text-sm font-semibold text-emerald-700">
                    {bulkResult.authorised_count} pack{bulkResult.authorised_count === 1 ? '' : 's'} authorised
                    {' '}({Number(bulkResult.authorised_total).toLocaleString('en')} total).
                  </p>
                  {bulkResult.refused_count > 0 && (
                    <div className="mt-3">
                      <p className="text-sm font-semibold text-[#B45309]">
                        {bulkResult.refused_count} could not be authorised:
                      </p>
                      <ul className="mt-1 max-h-40 list-disc space-y-0.5 overflow-y-auto pl-5 text-xs text-[#6B7280]">
                        {bulkResult.refused.map((r, i) => <li key={i}><b>{r.ref}</b> — {r.reason}</li>)}
                      </ul>
                    </div>
                  )}
                  <div className="mt-4 flex justify-end">
                    <Button size="sm" onClick={() => setBulkResult(null)}>Done</Button>
                  </div>
                </>
              ) : bulkPreview ? (
                <>
                  <h3 className="mb-1 text-lg font-bold">
                    Approve all clean{tab !== 'all' ? ` — ${activeTab.label}` : ''}
                  </h3>
                  {bulkPreview.ready_count === 0 ? (
                    <p className="text-sm text-[#6B7280]">Nothing is ready to authorise here.</p>
                  ) : (
                    <p className="text-sm text-[#374151]">
                      You are about to authorise <b>{bulkPreview.ready_count}</b> clean pack{bulkPreview.ready_count === 1 ? '' : 's'},
                      total <b>{Number(bulkPreview.ready_total).toLocaleString('en')}</b>. Each still passes the
                      duplicate check as it is signed. This records your authorisation — you still release the money at the bank.
                    </p>
                  )}
                  {bulkPreview.blocked_count > 0 && (
                    <div className="mt-3 rounded border border-amber-300 bg-amber-50 p-3">
                      <p className="text-sm font-semibold text-[#92400E]">
                        {bulkPreview.blocked_count} held back (possible duplicate) — not included:
                      </p>
                      <ul className="mt-1 max-h-32 list-disc space-y-0.5 overflow-y-auto pl-5 text-xs text-[#92400E]">
                        {bulkPreview.blocked.slice(0, 8).map(b => <li key={b.id}><b>{b.ref}</b> — {b.clashes} clash(es)</li>)}
                      </ul>
                    </div>
                  )}
                  {bulkErr && <p className="mt-3 whitespace-pre-line text-xs text-red-700">{bulkErr}</p>}
                  <div className="mt-4 flex justify-end gap-2">
                    <Button size="sm" variant="outline" disabled={bulkBusy}
                            onClick={() => { setBulkPreview(null); setBulkErr(null) }}>Cancel</Button>
                    <Button size="sm" onClick={confirmBulk} loading={bulkBusy}
                            disabled={bulkBusy || bulkPreview.ready_count === 0}>
                      Authorise {bulkPreview.ready_count}
                    </Button>
                  </div>
                </>
              ) : (
                <>
                  <p className="text-sm text-red-700 whitespace-pre-line">{bulkErr}</p>
                  <div className="mt-4 flex justify-end">
                    <Button size="sm" onClick={() => setBulkErr(null)}>Close</Button>
                  </div>
                </>
              )}
            </div>
          </div>
        </ModalPortal>
      )}
    </div>
  )
}


/**
 * Email catch-up — read the FNB "Fully Processed" confirmation emails and show
 * which stuck (awaiting-CFO) requests the bank has already paid, so the CFO can
 * clear them in one tap. For the older payments that were paid straight in FNB
 * (never loaded through Omni), so the nightly auto-close has no reference to match
 * on. Nothing closes on its own — the CFO eyeballs the bank confirmation and taps
 * Mark paid (terminal state PAID, keeps the duplicate control). CFO 2026-08-24.
 */
/**
 * Payment register — the read-only detective-monitoring view (Kago Tshutlhedi
 * 2026-08-29). The action queue drops a request once it is signed off or paid,
 * so an authoriser could not retrieve a past payment. This searches the WHOLE
 * register (any status, any date, any entity), pages through it, and exports the
 * filtered result to Excel for NBFIRA packs. The backend (?register=1) already
 * exists and is CFO/finance-approver-gated; this is the screen for it.
 */
/* A paired segmented control, the same pattern as the All / Claims-related /
   Operational control in the Suppliers section, so a new choice reads as part
   of the same system rather than a new idea. The active option carries the
   house blue outline. */
const SEG_BASE = 'px-3 py-1.5 text-sm rounded-md transition-colors focus-visible:outline-none ' +
                 'focus-visible:ring-2 focus-visible:ring-[#1D3270]/40'
const SEG_ON  = `${SEG_BASE} bg-white text-[#1D3270] font-semibold ring-1 ring-[#1D3270] shadow-sm`
const SEG_OFF = `${SEG_BASE} text-[#6B7280] hover:text-[#1D3270] hover:bg-white/70`

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
                'August', 'September', 'October', 'November', 'December']

/**
 * Payment History, per LINE (Finance spec 2026-09-08 — Bontle Tendani,
 * Leano Makwapa). The register beside it reads request-by-request; a claim is
 * queried at line grain, so this shows date / claim number / payee / invoice
 * number / amount / status with a totals row. Reuses money() — the same
 * formatter the rest of this board uses — rather than a second one.
 *
 * A cancelled line is SHOWN, greyed and struck through, and left out of the
 * payable total. Nothing here is ever silently dropped.
 */
function PaymentHistoryLines() {
  const [f, setF] = useState<PaymentHistoryFilters>({})
  const [d, setD] = useState<PaymentHistoryResult | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const run = useCallback(async (filters: PaymentHistoryFilters) => {
    setLoading(true); setError(null)
    try { setD(await getPaymentHistory(filters)) }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not load the payment history') }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { run({}) }, [run])

  // The quick-select and the custom range are alternatives, not layers: picking
  // a month clears the typed range so the screen can never show a window that
  // disagrees with the control that is lit.
  const pickMonth = (year: string, month: string) => {
    const next: PaymentHistoryFilters = { year, month, payee: f.payee }
    setF(next); run(next)
  }
  const applyRange = () => {
    const next: PaymentHistoryFilters = { from: f.from, to: f.to, payee: f.payee }
    setF(next); run(next)
  }
  const clearAll = () => { setF({}); run({}) }

  const thisYear = new Date().getFullYear()
  const years = [thisYear, thisYear - 1, thisYear - 2].map(String)

  return (
    <div className="space-y-4">
      <p className="text-sm text-[#6B7280]">
        Every payment line raised — claim number, invoice number, payee and amount,
        from the same records the authorisation pack reads. Read-only: nothing here
        moves money or changes a request.
      </p>

      {/* Month / Year quick-select */}
      <div className="rounded-xl border border-[#E5E7EB] bg-white p-3 space-y-3 shadow-sm">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-semibold uppercase tracking-wide text-[#6B7280]">Year</span>
          <div className="inline-flex gap-1 rounded-lg bg-[#F3F4F6] p-1">
            {years.map(y => (
              <button key={y} type="button"
                      onClick={() => { const next = { year: y, payee: f.payee }; setF(next); run(next) }}
                      className={f.year === y && !f.month ? SEG_ON : SEG_OFF}>{y}</button>
            ))}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs font-semibold uppercase tracking-wide text-[#6B7280] mr-1">Month</span>
          {MONTHS.map((m, i) => {
            const mm = String(i + 1)
            const on = f.month === mm
            return (
              <button key={m} type="button"
                      onClick={() => pickMonth(f.year || String(thisYear), mm)}
                      className={`rounded-md px-2 py-1 text-xs transition-colors
                        focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1D3270]/40 ${
                        on ? 'bg-[#1D3270] text-white font-semibold'
                           : 'text-[#374151] hover:bg-[#F3F4F6] hover:text-[#1D3270]'}`}>
                {m.slice(0, 3)}
              </button>
            )
          })}
        </div>
      </div>

      {/* Custom date range */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 items-end">
        <div className="flex flex-col gap-1">
          <label className="text-xs text-[#6B7280]">From</label>
          <input type="date" value={f.from ?? ''}
                 onChange={e => setF(prev => ({ ...prev, from: e.target.value }))}
                 className="border border-[#D1D5DB] rounded px-2 py-1.5 text-sm" />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-[#6B7280]">To</label>
          <input type="date" value={f.to ?? ''}
                 onChange={e => setF(prev => ({ ...prev, to: e.target.value }))}
                 className="border border-[#D1D5DB] rounded px-2 py-1.5 text-sm" />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-[#6B7280]">Payee / reference</label>
          <input value={f.payee ?? ''}
                 onChange={e => setF(prev => ({ ...prev, payee: e.target.value }))}
                 onKeyDown={e => { if (e.key === 'Enter') applyRange() }}
                 className="border border-[#D1D5DB] rounded px-2 py-1.5 text-sm" />
        </div>
        <div className="flex items-center gap-2">
          <Button variant="primary" size="sm" onClick={applyRange}>
            <Search className="w-4 h-4 mr-1" /> Apply range
          </Button>
          <Button variant="outline" size="sm" onClick={clearAll}>Clear</Button>
        </div>
      </div>

      {d?.window && (
        <p className="text-xs text-[#6B7280]">
          Showing <span className="font-semibold text-[#0D1B2A]">{d.window}</span>
          {' · '}{d.row_count.toLocaleString('en')} line{d.row_count === 1 ? '' : 's'}
          {d.scope === 'mine' && ' · only the requests you raised'}
        </p>
      )}

      {error && (
        <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded px-3 py-2">{error}</div>
      )}

      <div className="border rounded overflow-x-auto max-h-[46vh] overflow-y-auto">
        {loading ? (
          <div className="py-10 text-center text-sm text-[#9CA3AF]">Loading…</div>
        ) : !d || d.rows.length === 0 ? (
          <div className="py-10 text-center text-sm text-[#9CA3AF]">No payment lines in this window.</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-[#F9FAFB] text-left text-xs uppercase text-[#6B7280] border-b sticky top-0">
              <tr>
                <th className="py-2 px-3">Date</th>
                <th className="py-2 px-3">Claim number</th>
                <th className="py-2 px-3">Payee</th>
                <th className="py-2 px-3">Invoice number</th>
                <th className="py-2 px-3 text-right">Amount</th>
                <th className="py-2 px-3">Status</th>
              </tr>
            </thead>
            <tbody>
              {d.rows.map(r => (
                <tr key={`${r.request_id}-${r.line}`}
                    className={`border-b last:border-0 ${
                      r.cancelled ? 'bg-[#FAFAFA] text-[#9CA3AF]' : 'hover:bg-[#FFF7ED]'}`}>
                  <td className="py-2 px-3 text-xs whitespace-nowrap">{r.date ?? '—'}</td>
                  <td className="py-2 px-3 font-mono text-xs">{r.claim_number || '—'}</td>
                  <td className="py-2 px-3 text-xs">
                    {r.payee || '—'}
                    <span className="block font-mono text-[10px] text-[#9CA3AF]">{r.ref}</span>
                  </td>
                  <td className="py-2 px-3 font-mono text-xs">{r.invoice_number || '—'}</td>
                  <td className={`py-2 px-3 text-right tabular-nums whitespace-nowrap ${
                        r.cancelled ? 'line-through' : ''}`}>
                    {money(r.currency, Number(r.amount))}
                  </td>
                  <td className="py-2 px-3">
                    <span className={REQ_STATUS_COLOR[r.status as PaymentRequestStatus]
                                     ?? STATUS_COLOR[r.status] ?? STATUS_COLOR.pending}>
                      {r.status_label}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* The totals row Finance asked for — one per currency, because a single
          figure spanning BWP and ZAR would be a meaningless number. Cancelled
          lines are reported apart: shown on the screen, out of the total. */}
      {d && d.totals.length > 0 && (
        <div className="rounded-xl border border-[#0D1B2A]/15 bg-[#F9FAFB] divide-y divide-[#E5E7EB]">
          {d.totals.map(t => (
            <div key={t.currency} className="flex flex-wrap items-baseline justify-between gap-2 px-4 py-2.5">
              <span className="text-xs font-semibold uppercase tracking-wide text-[#6B7280]">
                Total {t.currency}
                <span className="ml-1.5 font-normal normal-case text-[#9CA3AF]">
                  {t.count} line{t.count === 1 ? '' : 's'}
                </span>
              </span>
              <span className="text-base font-semibold tabular-nums text-[#0D1B2A]">
                {money(t.currency, Number(t.amount))}
              </span>
              {t.cancelled_count > 0 && (
                <span className="w-full text-[11px] text-[#6B7280]">
                  Plus {t.cancelled_count} cancelled line{t.cancelled_count === 1 ? '' : 's'} worth{' '}
                  {money(t.currency, Number(t.cancelled_amount))} — shown above, not payable,
                  and never deleted.
                </span>
              )}
            </div>
          ))}
        </div>
      )}

      {d?.truncated && (
        <p className="text-xs text-[#B45309]">
          This window is larger than one page of history. Narrow the dates to see the rest.
        </p>
      )}
    </div>
  )
}

const REG_PAGE = 100
function PaymentRegisterModal({ onClose, onOpenRequest }: {
  onClose: () => void; onOpenRequest: (id: string) => void
}) {
  const [filters, setFilters] = useState<PaymentRegisterFilters>({})
  const [applied, setApplied] = useState<PaymentRegisterFilters>({})
  const [rows, setRows] = useState<PaymentRequestRow[]>([])
  const [page, setPage] = useState<PaymentRegisterPage | null>(null)
  const [offset, setOffset] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [exporting, setExporting] = useState(false)
  // Two views of the same history (Finance spec 2026-09-08). 'request' is the
  // register that already existed; 'line' is the per-line view Finance asked
  // for, because a claim is queried at line grain.
  const [view, setView] = useState<'request' | 'line'>('request')

  const run = useCallback(async (f: PaymentRegisterFilters, off: number) => {
    setLoading(true); setError(null)
    try {
      const d = await getPaymentRegister({ ...f, limit: REG_PAGE, offset: off })
      setRows(d.requests); setPage(d.register ?? null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load the payment register')
    } finally { setLoading(false) }
  }, [])

  useEffect(() => { run({}, 0) }, [run])

  const search = () => { setApplied(filters); setOffset(0); run(filters, 0) }
  const clearAll = () => { setFilters({}); setApplied({}); setOffset(0); run({}, 0) }
  const goto = (off: number) => { setOffset(off); run(applied, off) }
  const set = (k: keyof PaymentRegisterFilters, v: string) =>
    setFilters(prev => ({ ...prev, [k]: v }))

  const doExport = async () => {
    setExporting(true); setError(null)
    try { await downloadPaymentRegister(applied) }
    catch (e) { setError(e instanceof Error ? e.message : 'Export failed') }
    finally { setExporting(false) }
  }

  const field = (k: keyof PaymentRegisterFilters, label: string, type = 'text') => (
    <div className="flex flex-col gap-1">
      <label className="text-xs text-[#6B7280]">{label}</label>
      <input type={type} value={(filters[k] as string) ?? ''}
             onChange={e => set(k, e.target.value)}
             onKeyDown={e => { if (e.key === 'Enter') search() }}
             className="border border-[#D1D5DB] rounded px-2 py-1.5 text-sm" />
    </div>
  )

  const total = page?.total ?? rows.length
  const from = total === 0 ? 0 : offset + 1
  const to = offset + rows.length

  return (
    <ModalPortal>
      <div className="fixed inset-0 z-[200] bg-black/40 flex items-start justify-center overflow-y-auto py-8" onClick={onClose}>
        <div className="bg-white rounded-lg shadow-xl w-full max-w-6xl mx-4" onClick={e => e.stopPropagation()}>
          <div className="flex items-center justify-between px-5 py-3 border-b">
            <div className="flex items-center gap-2 text-[#0D1B2A] font-semibold">
              <History className="w-5 h-5" /> Payment history
            </div>
            <button onClick={onClose} className="text-[#9CA3AF] hover:text-[#0D1B2A]"><X className="w-5 h-5" /></button>
          </div>

          <div className="p-5 space-y-4">
            <div className="inline-flex gap-1 rounded-lg bg-[#F3F4F6] p-1">
              <button type="button" onClick={() => setView('request')}
                      className={view === 'request' ? SEG_ON : SEG_OFF}>By request</button>
              <button type="button" onClick={() => setView('line')}
                      className={view === 'line' ? SEG_ON : SEG_OFF}>By payment line</button>
            </div>

            {view === 'line' ? <PaymentHistoryLines /> : (
            <>
            <p className="text-sm text-[#6B7280]">
              The whole payment register — every status, every date. Search it, then export the result to Excel for an audit or NBFIRA pack. Read-only.
            </p>

            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              {field('search', 'Search (ref / subject / payee)')}
              {field('entity', 'Entity')}
              {field('since', 'From (date raised)', 'date')}
              {field('until', 'To (date raised)', 'date')}
              <div className="grid grid-cols-2 gap-2 md:col-span-2">
                {field('min', 'Min amount', 'number')}
                {field('max', 'Max amount', 'number')}
              </div>
            </div>

            <div className="flex items-center gap-2 flex-wrap">
              <Button variant="primary" size="sm" onClick={search}>
                <Search className="w-4 h-4 mr-1" /> Search
              </Button>
              <Button variant="outline" size="sm" onClick={clearAll}>Clear</Button>
              <div className="flex-1" />
              <Button variant="outline" size="sm" onClick={doExport} disabled={exporting}>
                <Download className="w-4 h-4 mr-1" /> {exporting ? 'Exporting…' : 'Export to Excel'}
              </Button>
            </div>

            {error && (
              <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded px-3 py-2">{error}</div>
            )}

            <div className="border rounded overflow-x-auto max-h-[52vh] overflow-y-auto">
              {loading ? (
                <div className="py-10 text-center text-sm text-[#9CA3AF]">Loading…</div>
              ) : rows.length === 0 ? (
                <div className="py-10 text-center text-sm text-[#9CA3AF]">Nothing matches these filters.</div>
              ) : (
                <table className="w-full text-sm">
                  <thead className="bg-[#F9FAFB] text-left text-xs uppercase text-[#6B7280] border-b sticky top-0">
                    <tr>
                      <th className="py-2 px-3">Ref</th>
                      <th className="py-2 px-3">Raised</th>
                      <th className="py-2 px-3">Authorised</th>
                      <th className="py-2 px-3 text-right">Days</th>
                      <th className="py-2 px-3">Requester</th>
                      <th className="py-2 px-3">Authoriser</th>
                      <th className="py-2 px-3">Entity</th>
                      <th className="py-2 px-3">Payee</th>
                      <th className="py-2 px-3 text-right">Amount</th>
                      <th className="py-2 px-3">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map(r => (
                      <tr key={r.id} onClick={() => onOpenRequest(r.id)}
                          className="border-b last:border-0 hover:bg-[#FFF7ED] cursor-pointer">
                        <td className="py-2 px-3 font-mono text-xs whitespace-nowrap">{r.ref}</td>
                        <td className="py-2 px-3 text-xs whitespace-nowrap">{r.created_at ? r.created_at.slice(0, 10) : '—'}</td>
                        <td className="py-2 px-3 text-xs whitespace-nowrap">{r.first_approved_at ? r.first_approved_at.slice(0, 10) : '—'}</td>
                        <td className="py-2 px-3 text-right tabular-nums text-xs">{r.days_to_authorise ?? '—'}</td>
                        <td className="py-2 px-3 text-xs">{r.created_by}</td>
                        <td className="py-2 px-3 text-xs">{r.first_approver || '—'}</td>
                        <td className="py-2 px-3 text-xs text-[#6B7280]">{r.entity}</td>
                        <td className="py-2 px-3 text-xs">{r.payee || '—'}</td>
                        <td className="py-2 px-3 text-right tabular-nums whitespace-nowrap">{money(r.currency, Number(r.total))}</td>
                        <td className="py-2 px-3">
                          <span className={r.status ? REQ_STATUS_COLOR[r.status] : STATUS_COLOR.pending}>
                            {r.status_label || (r.status || 'pending').replace('_', ' ')}
                          </span>
                          {r.why_open
                            ? <div><WhyOpen text={r.why_open} tone={r.why_open_tone} /></div>
                            : r.bank_rejected && (
                              <span className={`ml-1 gap-0.5 font-semibold ${PILL} bg-[#FEE2E2] text-[#991B1B] ring-[#DC2626]/30`}>
                                <AlertTriangle className="w-3 h-3" /> Bank rejected
                              </span>
                            )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>

            <div className="flex items-center justify-between text-xs text-[#6B7280]">
              <span>{total === 0 ? 'No requests' : `Showing ${from}–${to} of ${total.toLocaleString('en')}`}</span>
              <div className="flex items-center gap-2">
                <Button variant="outline" size="sm" disabled={offset === 0 || loading}
                        onClick={() => goto(Math.max(0, offset - REG_PAGE))}>Previous</Button>
                <Button variant="outline" size="sm" disabled={!page?.has_more || loading}
                        onClick={() => goto(offset + REG_PAGE)}>Next</Button>
              </div>
            </div>
            </>
            )}
          </div>
        </div>
      </div>
    </ModalPortal>
  )
}

function EmailCatchupModal({ onClose, onCleared }: { onClose: () => void; onCleared: () => void }) {
  const [busy, setBusy] = useState(true)
  const [result, setResult] = useState<EmailCatchupResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [marking, setMarking] = useState<Record<string, boolean>>({})
  const [done, setDone] = useState<Record<string, boolean>>({})

  useEffect(() => {
    let alive = true
    ;(async () => {
      setBusy(true); setError(null)
      try {
        const r = await getFnbEmailCatchup()
        if (alive) setResult(r)
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : 'Could not read your FNB emails')
      } finally { if (alive) setBusy(false) }
    })()
    return () => { alive = false }
  }, [])

  const money = (n: string | number) => `P ${Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

  const markPaid = async (row: EmailCatchupRow) => {
    if (marking[row.id]) return
    setMarking(s => ({ ...s, [row.id]: true }))
    try {
      const m = row.matches[0]
      const reason = `Paid via FNB — CFO confirmed the bank "Fully Processed" email`
        + (m ? ` (${m.ref}, ${money(m.amount)}${m.date ? `, ${m.date}` : ''})` : '') + '.'
      await markPaymentPaidFromBank(row.id, reason)
      setDone(s => ({ ...s, [row.id]: true }))
      onCleared()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not mark this request paid')
    } finally { setMarking(s => ({ ...s, [row.id]: false })) }
  }

  const BADGE: Record<string, { label: string; cls: string }> = {
    confident: { label: 'Reference matches', cls: 'bg-[#ECFDF5] text-[#065F46] border-[#065F46]/30' },
    review:    { label: 'Amount matches — check the payee', cls: 'bg-[#FFFBEB] text-[#92400E] border-[#F59E0B]/40' },
    ambiguous: { label: 'Several payments at this amount — pick carefully', cls: 'bg-[#FEF2F2] text-[#991B1B] border-[#991B1B]/30' },
  }
  const rows = result?.rows ?? []

  return (
    <ModalPortal>
      <div className="fixed inset-0 z-[200] bg-black/40 flex items-start justify-center overflow-y-auto p-4">
        <Card className="w-full max-w-3xl my-6 max-h-[92vh] overflow-y-auto">
          <CardHeader className="flex flex-row items-center justify-between pb-2 sticky top-0 bg-white z-10 border-b">
            <CardTitle className="text-base flex items-center gap-2">
              <Mail className="w-4 h-4" /> Bank confirms these paid — from your FNB emails
            </CardTitle>
            <button onClick={onClose} className="text-[#9CA3AF] hover:text-[#374151]"><X className="w-4 h-4" /></button>
          </CardHeader>
          <CardContent className="space-y-4 pt-4">
            <p className="text-sm text-[#6B7280]">
              These payments are still waiting for your authorisation in Omni, but FNB has emailed you that
              they are <strong>Fully Processed</strong> (already paid). Check the bank line, then tap
              <strong> Mark paid</strong> to clear it. Nothing is closed until you tap.
            </p>

            {busy && <div className="py-8 text-center text-sm text-[#9CA3AF]">Reading your FNB emails…</div>}
            {error && <div className="rounded-md border border-red-300 bg-red-50/50 px-3 py-2 text-sm text-red-700">{error}</div>}

            {result && !busy && (
              <>
                <p className="text-xs text-[#9CA3AF]">
                  Read {result.emails_read} bank confirmation{result.emails_read === 1 ? '' : 's'} from the last {result.lookback_days} days.
                </p>
                {rows.length === 0 ? (
                  <p className="text-sm text-[#065F46]">
                    Nothing to catch up — no waiting request matches a bank "paid" email. Everything the bank has paid is already cleared.
                  </p>
                ) : (
                  <div className="space-y-3">
                    {rows.map(row => {
                      const badge = BADGE[row.confidence] ?? BADGE.review
                      const isDone = done[row.id]
                      return (
                        <div key={row.id} className="border rounded-md p-3">
                          <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0">
                              <div className="font-mono text-xs text-[#6B7280]">{row.ref} · {row.entity}</div>
                              <div className="truncate text-sm font-medium text-[#0D1B2A]">
                                {row.subject} · <span className="tabular-nums">{money(row.total)}</span>
                              </div>
                              <div className="text-xs text-[#9CA3AF]">raised {row.made}</div>
                            </div>
                            {isDone
                              ? <span className="text-xs text-[#065F46] flex items-center gap-1 shrink-0"><CheckCircle2 className="w-4 h-4" /> Marked paid</span>
                              : <Button variant="primary" size="sm" className="shrink-0"
                                        disabled={!row.can_clear || !!marking[row.id]}
                                        title={row.can_clear ? '' : 'You signed this off — another approver must clear it'}
                                        onClick={() => markPaid(row)}>
                                  {marking[row.id] ? 'Marking…' : 'Mark paid'}
                                </Button>}
                          </div>
                          <div className={`mt-2 rounded border px-2 py-1.5 text-xs ${badge.cls}`}>
                            <span className="font-medium">{badge.label}</span>
                            <ul className="mt-1 space-y-0.5">
                              {row.matches.map((m, i) => (
                                <li key={i} className="flex items-center gap-2">
                                  <span className="tabular-nums">{money(m.amount)}</span>
                                  <span className="text-[#6B7280]">·</span>
                                  <span className="truncate">{m.ref}</span>
                                  {m.date && <span className="text-[#9CA3AF]">· {m.date}</span>}
                                </li>
                              ))}
                            </ul>
                          </div>
                        </div>
                      )
                    })}
                  </div>
                )}
              </>
            )}

            <div className="flex justify-end pt-2 border-t">
              <Button variant="outline" size="sm" onClick={onClose}>Close</Button>
            </div>
          </CardContent>
        </Card>
      </div>
    </ModalPortal>
  )
}

// "Close paid (FNB list)" — upload the FNB Batch Payments PDF; Omni closes every
// open payment no longer on it. Two steps: preview (writes nothing) then confirm
// (CFO directive 2026-09-06). A hard rule by design — the preview is only so a
// mis-uploaded file is caught before it closes the whole queue.
function FnbListModal({ onClose, onCleared }: { onClose: () => void; onCleared: () => void }) {
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [preview, setPreview] = useState<FnbListReconcileResult | null>(null)
  const [applied, setApplied] = useState<FnbListReconcileResult | null>(null)
  const money = (n: string | number) => `P ${Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

  const runPreview = async (f: File) => {
    setBusy(true); setError(null); setPreview(null); setApplied(null)
    try { setPreview(await reconcileFnbList(f, 'preview')) }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not read that PDF') }
    finally { setBusy(false) }
  }
  const apply = async () => {
    if (!file) return
    setBusy(true); setError(null)
    try { setApplied(await reconcileFnbList(file, 'apply')); onCleared() }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not close the payments') }
    finally { setBusy(false) }
  }
  const closeRows = preview?.would_close ?? []

  return (
    <ModalPortal>
      <div className="fixed inset-0 z-[200] bg-black/40 flex items-start justify-center overflow-y-auto p-4">
        <Card className="w-full max-w-3xl my-6 max-h-[92vh] overflow-y-auto">
          <CardHeader className="flex flex-row items-center justify-between pb-2 sticky top-0 bg-white z-10 border-b">
            <CardTitle className="text-base flex items-center gap-2">
              <FileText className="w-4 h-4" /> Close paid — upload your FNB list
            </CardTitle>
            <button onClick={onClose} className="text-[#9CA3AF] hover:text-[#374151]"><X className="w-4 h-4" /></button>
          </CardHeader>
          <CardContent className="space-y-4 pt-4">
            {!applied && (
              <p className="text-sm text-[#6B7280]">
                Upload your FNB <strong>Batch Payments</strong> PDF — the list of payments still waiting for
                your authorisation. Omni closes every payment in your queue that is <strong>not</strong> on it.
                You will see exactly what closes before anything happens.
              </p>
            )}
            {!applied && (
              <input type="file" accept="application/pdf,.pdf" className="text-sm"
                     onChange={(e) => { const f = e.target.files?.[0] ?? null; setFile(f); if (f) runPreview(f) }} />
            )}

            {busy && <div className="py-6 text-center text-sm text-[#9CA3AF]">Working…</div>}
            {error && <div className="rounded-md border border-red-300 bg-red-50/50 px-3 py-2 text-sm text-red-700">{error}</div>}

            {preview && !applied && !busy && (
              <>
                <div className="rounded-md border bg-[#F9FAFB] px-3 py-2 text-sm">
                  Found <strong>{preview.entries_read}</strong> payment{preview.entries_read === 1 ? '' : 's'} still on your FNB list.{' '}
                  <span className="text-[#991B1B] font-medium">{preview.close_count} will close</span> ({money(preview.close_total ?? '0')});{' '}
                  <strong>{preview.keep_count}</strong> stay open.
                </div>
                {preview.keep_count === 0 && (preview.close_count ?? 0) > 0 && (
                  <div className="rounded-md border-2 border-[#DC2626] bg-[#FEE2E2] px-3 py-2 text-xs text-[#991B1B] flex items-start gap-2">
                    <AlertTriangle className="w-4 h-4 shrink-0" />
                    <span>This will close your <strong>ENTIRE</strong> queue and keep nothing. That usually means the wrong file — check it before you confirm.</span>
                  </div>
                )}
                {(preview.rejected_will_stay_open ?? 0) > 0 && (
                  <div className="rounded-md border border-[#F59E0B]/50 bg-[#FFFBEB] px-3 py-2 text-xs text-[#92400E] flex items-start gap-2">
                    <AlertTriangle className="w-4 h-4 shrink-0" />
                    <span>
                      {preview.rejected_will_stay_open} of these were{' '}
                      <strong>rejected by the bank, or the bank never confirmed them</strong>.
                      They are <strong>not</strong> included in the number above and will{' '}
                      <strong>stay open</strong> — they are missing from the FNB list because the
                      bank threw them out, not because they were paid. Check with FNB before
                      anything is sent again.
                    </span>
                  </div>
                )}
                {(preview.not_loaded_in_close ?? 0) > 0 && (
                  <div className="rounded-md border border-[#F59E0B]/50 bg-[#FFFBEB] px-3 py-2 text-xs text-[#92400E] flex items-start gap-2">
                    <AlertTriangle className="w-4 h-4 shrink-0" />
                    <span>{preview.not_loaded_in_close} were <strong>never loaded to FNB through Omni</strong> (paid by hand or a foreign-currency payment). They will still be closed.</span>
                  </div>
                )}
                {(preview.skipped_count ?? 0) > 0 && (
                  <div className="rounded-md border bg-[#F9FAFB] px-3 py-2 text-xs text-[#6B7280]">
                    {preview.skipped_count} left open because you signed them off yourself — another approver or the CFO must close those.
                  </div>
                )}
                {closeRows.length > 0 && (
                  <div className="border rounded-md divide-y max-h-64 overflow-y-auto">
                    {closeRows.map(r => (
                      <div key={r.id} className="flex items-center justify-between gap-2 px-3 py-1.5 text-xs">
                        <span className="font-mono text-[#6B7280] shrink-0">{r.ref}</span>
                        <span className="truncate flex-1">{r.payee || '—'}</span>
                        <span className="tabular-nums shrink-0">{money(r.total)}</span>
                        {r.bank_rejected && <span className="text-[#991B1B] font-medium shrink-0">rejected</span>}
                      </div>
                    ))}
                  </div>
                )}
                <div className="flex justify-end gap-2 pt-2 border-t">
                  <Button variant="outline" size="sm" onClick={onClose}>Cancel</Button>
                  <Button variant="primary" size="sm" disabled={busy || preview.close_count === 0} onClick={apply}>
                    Close {preview.close_count} payment{preview.close_count === 1 ? '' : 's'}
                  </Button>
                </div>
              </>
            )}

            {applied && (
              <>
                <div className="rounded-md border border-[#065F46]/30 bg-[#ECFDF5] px-3 py-3 text-sm text-[#065F46] flex items-center gap-2">
                  <CheckCircle2 className="w-5 h-5 shrink-0" />
                  <span>
                    Closed <strong>{applied.closed_count}</strong> payment
                    {applied.closed_count === 1 ? '' : 's'} ({money(applied.closed_total ?? '0')}).{' '}
                    {applied.kept_count} still on the FNB list.
                    {(applied.left_open_rejected_count ?? 0) > 0 && (
                      <>
                        {' '}<strong>{applied.left_open_rejected_count}</strong> left open — the
                        bank rejected them or never confirmed them, so the money may not have
                        moved. Check with FNB before sending anything again.
                      </>
                    )}
                  </span>
                </div>
                {(applied.failed_count ?? 0) > 0 && (
                  <div className="rounded-md border border-[#F59E0B]/50 bg-[#FFFBEB] px-3 py-2 text-xs text-[#92400E] flex items-start gap-2">
                    <AlertTriangle className="w-4 h-4 shrink-0" />
                    <span>{applied.failed_count} could not be closed (they stay in your queue) — try again or check them by hand.</span>
                  </div>
                )}
                <div className="flex justify-end pt-2 border-t">
                  <Button variant="primary" size="sm" onClick={() => { onCleared(); onClose() }}>Done</Button>
                </div>
              </>
            )}
          </CardContent>
        </Card>
      </div>
    </ModalPortal>
  )
}

/**
 * FNB reconcile — paste the bank's "Batch Payments" pending list and Omni tells
 * you which open payment requests are already paid (not in the bank list) vs
 * still waiting, plus any payment loaded to FNB twice. Nothing is closed
 * automatically — you press Close on the ones you choose (same audited action as
 * the Clear button in the detail view). CFO 2026-08-12.
 */
function FnbReconcileModal({ onClose, onCleared }: { onClose: () => void; onCleared: () => void }) {
  const [text, setText] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [autoClose, setAutoClose] = useState(true)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<FnbReconcileResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [clearing, setClearing] = useState<Record<string, boolean>>({})
  const [cleared, setCleared] = useState<Record<string, boolean>>({})

  const run = async () => {
    setBusy(true); setError(null); setResult(null)
    try {
      const r = await reconcilePaymentsAgainstFnb({ pdf: file, text, autoClose })
      setResult(r)
      if (r.closed.length > 0) onCleared()   // the queue changed — refresh behind the modal
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not check against FNB')
    } finally { setBusy(false) }
  }

  const clearOne = async (r: FnbReconcileRow) => {
    if (!r.id || clearing[r.id]) return
    setClearing(s => ({ ...s, [r.id]: true }))
    try {
      const today = localYmd(new Date())
      await clearPaymentRequest(r.id, `Paid via FNB — reconciled to bank pending list ${today}; not in the list, so already authorised.`)
      setCleared(s => ({ ...s, [r.id]: true }))
      onCleared()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not close this request')
    } finally { setClearing(s => ({ ...s, [r.id]: false })) }
  }

  const paid = result?.already_paid ?? []
  // Rows still needing a manual close (auto-close was off, or a row was skipped for SoD)
  const manualRemaining = paid.filter(r => r.id && !r.closed && !cleared[r.id])
  const money = (n: string | number) => `P ${Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

  return (
    <ModalPortal>
      <div className="fixed inset-0 z-[200] bg-black/40 flex items-start justify-center overflow-y-auto p-4">
        <Card className="w-full max-w-3xl my-6 max-h-[92vh] overflow-y-auto">
          <CardHeader className="flex flex-row items-center justify-between pb-2 sticky top-0 bg-white z-10 border-b">
            <CardTitle className="text-base flex items-center gap-2">
              <Landmark className="w-4 h-4" /> Check open payments against FNB
            </CardTitle>
            <button onClick={onClose} className="text-[#9CA3AF] hover:text-[#374151]"><X className="w-4 h-4" /></button>
          </CardHeader>
          <CardContent className="space-y-4 pt-4">
            {!result && (
              <>
                <p className="text-sm text-[#6B7280]">
                  Upload your FNB <strong>Batch Payments</strong> PDF (everything still waiting for your
                  authorisation). Omni matches it to the open requests, <strong>closes the ones already
                  paid</strong> in the bank, and gives you an intelligence summary.
                </p>

                <label className="flex flex-col gap-1">
                  <span className="text-xs font-medium text-[#374151]">FNB Batch Payments PDF</span>
                  <input type="file" accept="application/pdf,.pdf"
                         onChange={e => setFile(e.target.files?.[0] ?? null)}
                         className="text-sm file:mr-3 file:rounded-md file:border-0 file:bg-[#1D3270] file:text-white file:px-3 file:py-1.5 file:text-xs file:cursor-pointer" />
                  {file && <span className="text-xs text-[#065F46]">{file.name}</span>}
                </label>

                <details className="text-xs text-[#6B7280]">
                  <summary className="cursor-pointer">…or paste the list instead</summary>
                  <textarea
                    value={text} onChange={e => setText(e.target.value)}
                    rows={7} placeholder="Paste the FNB batch-payments list here…"
                    className="mt-2 w-full text-xs font-mono border border-[#D1D5DB] rounded-md p-3 focus:outline-none focus:ring-1 focus:ring-[#F4A623]"
                  />
                </details>

                <label className="flex items-center gap-2 text-sm cursor-pointer select-none">
                  <input type="checkbox" checked={autoClose} onChange={e => setAutoClose(e.target.checked)}
                         className="rounded border-[#D1D5DB]" />
                  <span>Close the already-paid ones automatically <span className="text-[#9CA3AF]">(recommended)</span></span>
                </label>

                <div className="flex justify-end">
                  <Button variant="primary" size="sm" onClick={run} disabled={busy || (!file && !text.trim())}>
                    {busy ? 'Working…' : autoClose ? 'Check & auto-close' : 'Check'}
                  </Button>
                </div>
              </>
            )}

            {error && <div className="rounded-md border border-red-300 bg-red-50/50 px-3 py-2 text-sm text-red-700">{error}</div>}

            {result && (
              <div className="space-y-4">
                {/* Safety hold — an unreadable/wrong/stale file did not auto-close anything */}
                {result.warning && (
                  <div className="rounded-md border border-[#F59E0B]/60 bg-[#FFFBEB] px-3 py-2 text-sm text-[#92400E] flex items-start gap-2">
                    <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
                    <span>{result.warning}</span>
                  </div>
                )}

                {/* Intelligence summary — the headline */}
                {result.intelligence && (
                  <div className="rounded-md border border-[#1D3270]/25 bg-[#1D3270]/5 p-3">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-sm font-semibold text-[#1D3270]">Intelligence summary</span>
                      <span className="text-[10px] uppercase tracking-wide text-[#9CA3AF]">
                        {result.ai_source === 'deepseek' ? 'DeepSeek' : result.ai_source === 'gemini' ? 'AI' : 'summary'}
                      </span>
                    </div>
                    <pre className="text-xs text-[#0D1B2A] whitespace-pre-wrap font-sans leading-relaxed">{result.intelligence}</pre>
                  </div>
                )}

                <p className="text-xs text-[#9CA3AF]">
                  Read {result.fnb_lines_read} bank lines · checked {result.requests_checked} open requests.
                </p>

                {result.duplicates.length > 0 && (
                  <div className="rounded-md border border-[#F59E0B]/50 bg-[#FFFBEB] p-3">
                    <div className="flex items-center gap-2 text-sm font-semibold text-[#92400E]">
                      <AlertTriangle className="w-4 h-4" /> Loaded more than once in FNB — check before you authorise
                    </div>
                    <ul className="mt-1 text-xs text-[#92400E] space-y-0.5">
                      {result.duplicates.map((d, i) => (
                        <li key={i}>{money(d.amount)} — appears {d.count}× ({d.names.filter(Boolean).join(', ')})</li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* Auto-closed */}
                {result.closed.length > 0 && (
                  <div>
                    <h3 className="text-sm font-semibold text-[#065F46] mb-1 flex items-center gap-1">
                      <CheckCircle2 className="w-4 h-4" /> Closed automatically ({result.closed.length})
                    </h3>
                    <div className="border rounded-md divide-y">
                      {result.closed.map(r => (
                        <div key={r.ref} className="px-3 py-2 text-sm flex items-center justify-between">
                          <div className="min-w-0">
                            <div className="font-mono text-xs text-[#6B7280]">{r.ref}</div>
                            <div className="truncate">{r.subject} · <span className="tabular-nums">{money(r.total)}</span></div>
                          </div>
                          <span className="text-xs text-[#065F46]">Closed</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Already-paid still needing a manual close (auto-close off, or SoD-skipped) */}
                {manualRemaining.length > 0 && (
                  <div>
                    <div className="flex items-center justify-between mb-1">
                      <h3 className="text-sm font-semibold text-[#065F46]">Already paid — close these ({manualRemaining.length})</h3>
                      <Button variant="outline" size="sm" onClick={async () => { for (const r of manualRemaining) await clearOne(r) }}>
                        Close all {manualRemaining.length}
                      </Button>
                    </div>
                    <div className="border rounded-md divide-y">
                      {manualRemaining.map(r => (
                        <div key={r.ref} className="flex items-center justify-between px-3 py-2 text-sm">
                          <div className="min-w-0">
                            <div className="font-mono text-xs text-[#6B7280]">{r.ref}</div>
                            <div className="truncate">{r.subject} · <span className="tabular-nums">{money(r.total)}</span></div>
                          </div>
                          {r.id && cleared[r.id]
                            ? <span className="text-xs text-[#065F46] flex items-center gap-1"><CheckCircle2 className="w-4 h-4" /> Closed</span>
                            : <Button variant="ghost" size="sm" disabled={!!r.id && clearing[r.id]} onClick={() => clearOne(r)}>
                                {r.id && clearing[r.id] ? 'Closing…' : 'Close'}
                              </Button>}
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {paid.length === 0 && (
                  <p className="text-xs text-[#9CA3AF]">Nothing already paid — every open request still has money waiting in the bank.</p>
                )}

                {/* Skipped for segregation of duties — you signed these off, so someone else must close them */}
                {result.skipped.length > 0 && (
                  <div className="rounded-md border border-[#D1D5DB] bg-[#F9FAFB] p-3">
                    <h3 className="text-sm font-semibold text-[#374151] mb-1">Not closed — needs another approver ({result.skipped.length})</h3>
                    <ul className="text-xs text-[#6B7280] space-y-0.5">
                      {result.skipped.map(s => (
                        <li key={s.ref}><span className="font-mono">{s.ref}</span> — {s.why}</li>
                      ))}
                    </ul>
                  </div>
                )}

                <details>
                  <summary className="text-sm font-semibold text-[#1E40AF] cursor-pointer">Still waiting in the bank — kept ({result.still_pending.length})</summary>
                  <div className="border rounded-md divide-y mt-1">
                    {result.still_pending.map(r => (
                      <div key={r.ref} className="px-3 py-2 text-sm">
                        <div className="font-mono text-xs text-[#6B7280]">{r.ref}</div>
                        <div className="truncate">{r.subject} · <span className="tabular-nums">{money(r.total)}</span></div>
                        <div className="text-xs text-[#9CA3AF]">{r.lines.filter(l => l.in_fnb).length} of {r.lines.length} lines still in the bank</div>
                      </div>
                    ))}
                  </div>
                </details>

                {result.no_lines.length > 0 && (
                  <details>
                    <summary className="text-sm font-semibold text-[#6B7280] cursor-pointer">Couldn&apos;t auto-judge — no line detail ({result.no_lines.length})</summary>
                    <div className="border rounded-md divide-y mt-1">
                      {result.no_lines.map(r => (
                        <div key={r.ref} className="px-3 py-2 text-sm">
                          <span className="font-mono text-xs text-[#6B7280]">{r.ref}</span> · {r.subject}
                        </div>
                      ))}
                    </div>
                  </details>
                )}

                <div className="flex justify-between pt-2 border-t">
                  <Button variant="ghost" size="sm" onClick={() => { setResult(null); setText(''); setFile(null) }}>Check another</Button>
                  <Button variant="primary" size="sm" onClick={onClose}>Done</Button>
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </ModalPortal>
  )
}


function NewPaymentModal({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [entity, setEntity] = useState('Alpha Direct Insurance Company')
  // Entity is a fixed drop-down of the companies this user may transact for —
  // never free text. Free typing stamped the wrong entity code on the payment
  // reference (bug lntabeni 2026-07-24): a typo or unlisted name fell back to
  // ADIC on the backend. Picking from the real list guarantees the right code.
  const [companies, setCompanies] = useState<MeCompany[]>([])
  // Claims or operations — asked FIRST and never auto-detected (CFO 2026-07-29).
  // A repairer's invoice settled through claims payable was being raised as a
  // claim, which skipped the invoice/due-date gate entirely. Forcing the answer
  // is the whole point: the two kinds of payment must not be confused.
  const [nature, setNature] = useState<'' | 'claims' | 'operations' | 'refund'>('')
  const [opsCategory, setOpsCategory] = useState<'' | 'supplier' | 'vendor' | 'petty_cash' | 'other'>('')
  // B8 — which refund, and the payment it reverses. Premium Refund is never a
  // value here: it is importer-owned and the option is disabled.
  const [refundCategory, setRefundCategory] = useState<'' | 'erroneous_refund' | 'excess_refund'>('')
  const [originalPaymentRef, setOriginalPaymentRef] = useState('')
  const [claimPayeeType, setClaimPayeeType] = useState<'' | 'client' | 'provider'>('')
  const [currency, setCurrency] = useState('BWP')
  const [subject, setSubject] = useState('')
  const [bankNarration, setBankNarration] = useState('')
  const [payee, setPayee] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [lines, setLines] = useState<PaymentLine[]>([newLine()])
  // How the money should leave (Kelvin Kimani spec 2026-09-08). Bulk = one
  // payment for the supplier total; Individual = one payment per invoice. The
  // amounts never change — it is a grouping decision over the same lines — and
  // the choice is only offered once there is more than one line, because with
  // one line the two ARE the same payment.
  const [processingMethod, setProcessingMethod] = useState<'bulk' | 'individual'>('bulk')
  const [bankName, setBankName] = useState('')
  // Entered here so the payment never has to be typed again in FNB
  // (CFO 2026-08-20).
  const [accountName, setAccountName] = useState('')
  const [branchCode, setBranchCode] = useState('')
  const [accountType, setAccountType] = useState('CACC')
  // Filled from what we last paid this payee, and challenged if it changes.
  const [bankPrefill, setBankPrefill] = useState<string | null>(null)
  const [bankChange, setBankChange] = useState<string | null>(null)
  const [bankChangeReason, setBankChangeReason] = useState('')
  // The lookup hands an ordinary raiser a MASKED account (audit H4). When it is
  // used untouched, the server fills the real digits in from its own history.
  const [useKnownAccount, setUseKnownAccount] = useState(false)
  // PAY-BANK-03 (CFO 2026-09-01): the first payment to a payee we have never
  // paid. PAY-BANK-01 above can only challenge a CHANGED account, so a
  // brand-new supplier used to be challenged by nothing whatsoever — the hole a
  // fabricated invoice walks through, and reading the account off the invoice
  // means nobody is even forced to look at the digits.
  const [firstPayment, setFirstPayment] = useState<string | null>(null)
  const [newPayeeConfirmed, setNewPayeeConfirmed] = useState(false)
  // Set when a control routes the request to the exception committee (the POP).
  const [exceptionNotice, setExceptionNotice] = useState<string | null>(null)
  // Invoice upload — reuses the reader already live on /payments/new.
  const [invReading, setInvReading] = useState(false)
  const [invMsg, setInvMsg] = useState<string | null>(null)
  const [invErr, setInvErr] = useState<string | null>(null)
  // What the last autofill put in, so changing the payee can clear it. Without
  // this, payee A's account number survives a switch to payee B — and if B has
  // no history there is nothing to challenge it (Fable F4).
  const prefilledRef = useRef<{ account_name: string; account_number: string;
                                bank_name: string; branch_code: string;
                                account_type: string } | null>(null)
  const accountTypeTouched = useRef(false)
  // Idempotency (CFO 2026-09-14): a lock so a genuine double-click cannot
  // call submit() twice concurrently, plus one key per submit attempt that
  // stays the same across any retry of that SAME attempt — the server keys
  // a database constraint on it, so a resend can never raise a second
  // payment request. A fresh key is drawn only once THIS attempt begins.
  const submitLockRef = useRef(false)
  const clientRequestIdRef = useRef('')
  const [accountNumber, setAccountNumber] = useState('')
  const [opening, setOpening] = useState('')
  const [dueAt, setDueAt] = useState('')
  // Supplier terms control PAY-SUP-01 (CFO 2026-07-28) — supplier payments only.
  const [paymentDate, setPaymentDate] = useState('')
  const [earlyReason, setEarlyReason] = useState('')
  const [fundsMoved, setFundsMoved] = useState(false)
  const [verifier, setVerifier] = useState('')
  // Smart fill (CFO 2026-07-29): paste messy text, the AI-cleanup engine fills
  // the lines. Advisory — the form still validates on submit.
  const [pasteText, setPasteText] = useState('')
  const [pasting, setPasting] = useState(false)
  const [pasteMsg, setPasteMsg] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  // Duplicate control PAY-DUP-01 (CFO 2026-08-03). Not pre-checked in the
  // browser: only the server can see every other request, and a client-side
  // guess that says "looks fine" would be worse than no guess. The server
  // refuses, and what it sends back is rendered here so the raiser can see
  // exactly which line clashes with which request.
  const [dupBlock, setDupBlock] = useState<{ detail: string; rows: DuplicateRow[] } | null>(null)
  // Loading window PAY-WIN-02 (CFO 2026-09-01). The server refuses a load
  // outside 08:00–09:15; what it sends back is shown as the box here, with the
  // one-click "ask the CFO" escape hatch.
  const [winBlock, setWinBlock] = useState<{ detail: string; pending: boolean } | null>(null)
  const [winReason, setWinReason] = useState('')
  const [winBusy, setWinBusy] = useState(false)
  const [winSent, setWinSent] = useState(false)

  // Reload a previous payment (bug 8d3f6dd0): panel state
  const [showReloadPanel, setShowReloadPanel] = useState(false)
  const [reloadPayeeSearch, setReloadPayeeSearch] = useState('')
  const [reloadResults, setReloadResults] = useState<PaymentHistoryResult | null>(null)
  const [reloadLoading, setReloadLoading] = useState(false)
  const [reloadError, setReloadError] = useState<string | null>(null)

  async function askCfoToLoad() {
    if (winReason.trim().length < 5) { setErr('Say briefly why you need to load now.'); return }
    setWinBusy(true); setErr(null)
    try {
      await requestLoadOverride(winReason.trim())
      setWinSent(true)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not send your request to the CFO')
    } finally { setWinBusy(false) }
  }

  // Load the entities this user may raise a payment for. Keep the current
  // default if it's in the list; otherwise fall back to the first entity so
  // the field is never left on a name the backend can't resolve.
  useEffect(() => {
    getMyCompanies()
      .then(r => {
        const list = r.companies || []
        setCompanies(list)
        if (list.length && !list.some(c => c.name === entity)) {
          // Prefer the main insurer (ADIC), never the legacy ADI row that
          // sorts first, and never a random subsidiary.
          const preferred = list.find(c => c.code === 'ADIC') || list[0]
          setEntity(preferred.name)
        }
      })
      .catch(() => { /* keep the default entity if the list can't load */ })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const total = lines.reduce((s, l) => s + (Number(l.amount) || 0), 0)
  // Entity-specific category: when the selected company IS a subsidiary,
  // payments automatically route through that entity's own FNB account.
  // CFO 2026-09-10: "each entity should only see its own account."
  const entityCode = companies.find(c => c.name === entity)?.code || ''
  const ENTITY_CATEGORY: Record<string, PaymentCategory> = {
    UNI: 'unicoin', QIH: 'quantum', RSA: 'rsa', VCM: 'veritas', GCX: 'gce',
  }
  const entityCategory = ENTITY_CATEGORY[entityCode] || ''
  // The category the backend stores. Entity-specific companies auto-resolve;
  // ADIC uses the nature/opsCategory picker as before.
  const category: PaymentCategory = entityCategory
    || (nature === 'claims' ? 'claim'
      : nature === 'refund' ? (refundCategory || '')
      : (opsCategory || ''))
  const claimsBlocked = Boolean(entityCode) && entityCode !== 'ADIC'
  // The terms gate: invoice number, invoice date, agreed term and due date on
  // every line, and the payment date may not fall before the last due date.
  // Supplier and vendor packs always. Claim packs only when a provider is being
  // paid — a settlement to the policyholder has no invoice to age.
  const isTermsGated = category === 'supplier' || category === 'vendor'
    || (category === 'claim' && claimPayeeType === 'provider')
  // Switching to a company that cannot settle claims must drop the claims
  // answer, not leave a stale one the backend will reject on submit.
  useEffect(() => {
    if (claimsBlocked && nature === 'claims') { setNature(''); setClaimPayeeType('') }
  }, [claimsBlocked, nature])

  const payOn = paymentDate || localYmd(new Date())
  // The date the whole request becomes payable — the LATEST due date on it,
  // because approving it pays every line.
  const bindingDue = isTermsGated
    ? lines.map(l => l.due_date || '').filter(Boolean).sort().slice(-1)[0] || ''
    : ''
  const isEarly = Boolean(bindingDue) && payOn < bindingDue
  const daysEarly = isEarly
    ? Math.round((new Date(bindingDue).getTime() - new Date(payOn).getTime()) / 86400000)
    : 0

  // Any edit to the lines invalidates a duplicate refusal — the raiser is most
  // likely removing the offending row, and leaving the red panel up would read
  // as if it were still blocked. The server re-checks on submit regardless.
  function setLine(i: number, patch: Partial<PaymentLine>) {
    setDupBlock(null)
    setLines(ls => ls.map((l, idx) => idx === i ? { ...l, ...patch } : l))
  }
  function addLine() { setDupBlock(null); setLines(ls => [...ls, newLine()]) }
  function removeLine(i: number) {
    setDupBlock(null)
    setLines(ls => ls.length > 1 ? ls.filter((_, idx) => idx !== i) : ls)
  }

  // Reload a previous payment (bug 8d3f6dd0): search history and add as a new line
  async function searchReloadPayments() {
    if (!reloadPayeeSearch.trim()) { setReloadError('Enter a supplier or payee name'); return }
    setReloadLoading(true); setReloadError(null)
    try {
      // 12 months before today, to today
      const today = new Date()
      const fromDate = new Date(today.getFullYear() - 1, today.getMonth(), today.getDate())
      const fromStr = `${fromDate.getFullYear()}-${String(fromDate.getMonth() + 1).padStart(2, '0')}-${String(fromDate.getDate()).padStart(2, '0')}`
      const toStr = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`

      const result = await getPaymentHistory({
        payee: reloadPayeeSearch.trim(),
        from: fromStr,
        to: toStr,
        // Without copy=1 the server sends no copy payload and every row reads
        // as not copyable — the panel would show no "Add" button at all.
        copy: '1',
      })
      setReloadResults(result)
    } catch (e) {
      setReloadError(e instanceof Error ? e.message : 'Could not search payments')
    } finally {
      setReloadLoading(false)
    }
  }

  function addReloadedLine(row: PaymentHistoryRow) {
    const newLine_ = lineFromHistoryRow(row)
    if (!newLine_) return
    setDupBlock(null)
    // If form has single blank line, replace it; otherwise append
    if (lines.length === 1 && !lines[0].description.trim() && !Number(lines[0].amount)) {
      setLines([newLine_])
    } else {
      setLines(ls => [...ls, newLine_])
    }
  }

  // Smart fill — paste messy text, let omni's cleanup engine fill the lines.
  // Only SUGGESTS values; the raiser reviews and the create endpoint validates.
  async function smartFill() {
    if (!pasteText.trim() || pasting) return
    setPasting(true); setPasteMsg(null)
    try {
      const r = await parsePaymentPaste(pasteText)
      if (!r.ok) { setPasteMsg(r.reason || 'Could not read that paste.'); return }
      if (r.payee && !payee.trim()) setPayee(r.payee)
      const filled = (r.lines || []).map(l => ({
        ...newLine(),
        description: l.description || '',
        amount: l.amount || '',
        invoice_number: l.invoice_number || '',
        invoice_date: l.invoice_date || '',
        claim_number: l.claim_number || '',
        due_date: dueFromTerms(l.invoice_date || '', '30', 'statement'),
      }))
      if (filled.length) setLines(filled)
      setPasteMsg(r.note || `Filled ${filled.length} line(s) — please review before sending.`)
    } catch {
      setPasteMsg('The cleanup helper is busy — type the lines in by hand.')
    } finally { setPasting(false) }
  }

  // Fill the bank details from the last payment to this payee. Debounced, and
  // never overwrites something already typed — a prefill must not quietly
  // replace a person's own entry.
  useEffect(() => {
    const name = payee.trim()
    // Drop anything the PREVIOUS payee's autofill left behind, so one
    // supplier's account can never ride along on another's request. A value
    // the person typed themselves differs from the snapshot and is kept.
    const prev = prefilledRef.current
    if (prev) {
      setAccountName(v => (v === prev.account_name ? '' : v))
      setAccountNumber(v => (v === prev.account_number ? '' : v))
      setBankName(v => (v === prev.bank_name ? '' : v))
      setBranchCode(v => (v === prev.branch_code ? '' : v))
      if (!accountTypeTouched.current) setAccountType('CACC')
      prefilledRef.current = null
      setUseKnownAccount(false)
    }
    if (name.length < 3) { setBankPrefill(null); return }
    let alive = true
    const t = setTimeout(() => {
      lookupPayeeBank(name).then(h => {
        if (!alive || !h.found) { if (alive) setBankPrefill(null); return }
        const fill = {
          account_name: h.account_name || '',
          account_number: h.account_number || '',
          bank_name: h.bank_name || '',
          branch_code: h.branch_code || '',
          account_type: h.account_type || '',
        }
        setAccountName(v => v || fill.account_name)
        setAccountNumber(v => v || fill.account_number)
        setBankName(v => v || fill.bank_name)
        setBranchCode(v => v || fill.branch_code)
        // The default 'CACC' is truthy, so `v || …` never applied history — a
        // savings supplier prefilled as cheque every time.
        if (fill.account_type && !accountTypeTouched.current) {
          setAccountType(fill.account_type)
        }
        prefilledRef.current = fill
        setUseKnownAccount(!!h.account_masked)
        setBankPrefill(
          `Filled in from ${h.source_label} — account ending ${fill.account_number.slice(-4)}. `
          + 'Change it only if the supplier really has moved banks.')
      }).catch(() => { if (alive) setBankPrefill(null) })
    }, 400)
    return () => { alive = false; clearTimeout(t) }
  }, [payee])

  // Challenge a changed account while they are still on the screen, rather
  // than refusing them at the end.
  useEffect(() => {
    // Use the SAME beneficiary the server blocks on. This modal has no separate
    // payee field — the raiser types who is being paid into "Account holder
    // name" (accountName), and `payee` is only filled by the invoice reader. On
    // submit the server falls back to the account holder (payee_for_bank =
    // body.payee or holder_in), so a brand-new payee typed as the account holder
    // was silently blocked (PAY-BANK-03) with NO tick-box ever shown to clear
    // it. Mirror that fallback here so the first-payment box appears (CFO 2026-09-02).
    const name = payee.trim() || accountName.trim()
    const acct = accountNumber.trim()
    if (name.length < 3 || acct.length < 4) {
      setBankChange(null); setFirstPayment(null); return
    }
    // The account came from Omni's own history (masked for this raiser): by
    // definition unchanged, and not a first payment.
    if (useKnownAccount && acct.includes('*')) {
      setBankChange(null); setFirstPayment(null); return
    }
    let alive = true
    const t = setTimeout(() => {
      checkPayeeBankChange(name, acct)
        .then(r => {
          if (!alive) return
          setBankChange(r.changed ? (r.detail || '') : null)
          // PAY-BANK-03: no history at all. Not a "change", so the warning
          // above stays silent — but this is the payee nothing used to check.
          setFirstPayment(r.first_payment ? (r.detail || '') : null)
        })
        .catch(() => { if (alive) { setBankChange(null); setFirstPayment(null) } })
    }, 500)
    return () => { alive = false; clearTimeout(t) }
  }, [payee, accountName, accountNumber, useKnownAccount])

  // Read an uploaded invoice and fill the form from it (CFO 2026-09-01).
  // Only SUGGESTS values — the raiser checks them and every server-side control
  // still runs on submit. Never overwrites something already typed.
  async function readInvoice(file: File | null) {
    if (!file || invReading) return
    setInvReading(true); setInvMsg(null); setInvErr(null)
    try {
      const res = await readInvoiceForPaymentRequest(file)
      if (!res.ok) { setInvErr(res.message || 'Could not read that invoice.'); return }
      const f = res.fields
      // Functional updates, NOT `if (!payee.trim())` against the closure. The
      // read takes seconds (it may go out to the AI ladder), and anything the
      // person types WHILE "Reading the invoice…" is showing is invisible to a
      // value captured when the call started. Checking the stale value would
      // let a late response overwrite what they just typed — worst case
      // replacing an account number they had corrected by hand with an OCR
      // misread, which is precisely what this screen promises never to do.
      const keep = (cur: string, next: string | null) => cur.trim() ? cur : (next || cur)
      if (f.payee_name)     setPayee(p => keep(p, f.payee_name))
      if (f.payee_name)     setAccountName(p => keep(p, f.payee_name))
      if (f.account_number) setAccountNumber(p => keep(p, f.account_number))
      if (f.bank_name)      setBankName(p => keep(p, f.bank_name))
      if (f.branch_code)    setBranchCode(p => keep(p, f.branch_code))
      // The amount and invoice number belong to the FIRST line — that is the
      // single-invoice case this is for. A multi-invoice pack is still keyed by
      // hand, and overwriting a filled line would lose someone's typing.
      if (f.total_amount || f.invoice_number) {
        setLines(ls => ls.map((l, i) => i !== 0 ? l : {
          ...l,
          amount: l.amount || f.total_amount || '',
          invoice_number: l.invoice_number || f.invoice_number || '',
        }))
      }
      setInvMsg((res.message || 'Read the invoice.')
        + (res.tier === 'vision' ? ' (read from the photo)' : '')
        + ' Check every figure against the invoice before you send it.')
    } catch (e) {
      setInvErr(e instanceof Error ? e.message : 'Could not read that invoice.')
    } finally { setInvReading(false) }
  }

  async function submit() {
    // A genuine double-click can fire this handler twice before React has
    // re-rendered the disabled button (CFO 2026-09-14: "a retry, a double
    // click ... cannot create the same record twice"). This lock is a plain
    // ref, checked and set synchronously, so the SECOND call returns before
    // it ever reaches the network — the button's own `busy`-driven disabled
    // state cannot do that because setState is not synchronous.
    if (submitLockRef.current) return
    submitLockRef.current = true
    // One key for this whole attempt, including any retry of it — cleared
    // only once the attempt truly ends (success, or a failure the user must
    // now correct and resubmit as a NEW attempt).
    if (!clientRequestIdRef.current) {
      clientRequestIdRef.current = (globalThis.crypto?.randomUUID?.()
        ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`)
    }
    setBusy(true); setErr(null)
    try {
      const cleaned = lines
        .filter(l => l.description.trim() || Number(l.amount) > 0)
        .map(l => ({ ...l, amount: Number(l.amount) || 0 }))
      const created = await createPaymentRequest({
        client_request_id: clientRequestIdRef.current,
        entity, category: category || undefined, currency, subject, payee,
        ...(nature === 'claims' && claimPayeeType
          ? { claim_payee_type: claimPayeeType } : {}),
        ...(nature === 'refund' && originalPaymentRef.trim()
          ? { original_payment_ref: originalPaymentRef.trim() } : {}),
        line_items: cleaned,
        processing_method: processingMethod,
        bank_name: bankName, account_number: accountNumber,
        account_name: accountName, branch_code: branchCode,
        account_type: accountType,
        ...(bankNarration.trim() ? { bank_narration: bankNarration.trim() } : {}),
        ...(bankChangeReason.trim() ? { bank_change_reason: bankChangeReason.trim() } : {}),
        ...(newPayeeConfirmed ? { new_payee_confirmed: true } : {}),
        ...(useKnownAccount ? { use_known_account: true } : {}),
        opening_balance: opening || undefined,
        due_date: dueAt || undefined,
        verifier,
        ...(isTermsGated ? {
          payment_date: paymentDate || undefined,
          early_payment_reason: earlyReason || undefined,
          funds_already_moved: fundsMoved,
        } : {}),
      })
      // Optional supporting documents — uploaded after the request exists.
      // A failed attachment must not lose the (already-saved) request.
      for (const f of files) {
        try { await uploadPaymentRequestAttachment(created.id, f) }
        catch { setErr(`Request saved, but "${f.name}" failed to attach — open it and re-add.`) }
      }
      const exc = (created as unknown as { exception?: { message?: string } }).exception
      if (exc?.message) { setExceptionNotice(exc.message); return }
      onSaved()
    } catch (e) {
      // A duplicate refusal is not a generic error — it carries the clashing
      // lines, and burying them in a toast is how the raiser ends up retrying
      // the same pack. Render them, and open the override box.
      // Unlock the button either way so the raiser can act again. The KEY is
      // different: a server refusal (a real response body — duplicate, window,
      // first-payee, or any other 4xx) means this attempt is over and truly
      // done with, so the next click is a NEW attempt and draws a fresh key.
      // A network failure or timeout carries NO body — the server may already
      // have saved the row — so the key must survive: the next click is a
      // RETRY of this same attempt, which is the entire reason the key exists
      // (Fable 5.1, 2026-09-14: clearing it here left a timeout-retry able to
      // raise the payment twice, the one case the guard was built for).
      submitLockRef.current = false
      const body = (e as { body?: { control?: string; detail?: string; duplicates?: DuplicateRow[]; override_pending?: boolean } })?.body
      if (body) clientRequestIdRef.current = ''
      if (body?.control === 'PAY-DUP-01') {
        setDupBlock({ detail: body.detail || 'This payment repeats lines already raised or paid.',
                      rows: body.duplicates || [] })
        setErr(null)
      } else if (body?.control === 'PAY-WIN-02') {
        setWinBlock({ detail: body.detail || 'Payments can only be loaded in the morning window.',
                      pending: !!body.override_pending })
        setErr(null)
      } else if (body?.control === 'PAY-BANK-03') {
        // The tick-box normally comes from the background check; if that missed
        // (a fast submit, a hiccup) the server's refusal must still raise it —
        // never a bare red line the raiser cannot get past (audit H9).
        setFirstPayment(body.detail || 'First payment to this payee — confirm the account number.')
        setErr(null)
      } else {
        setErr(e instanceof Error ? e.message : 'Could not submit payment request')
      }
    } finally { setBusy(false) }
  }

  // The backend is the real gate (PAY-SUP-01); this only saves the clerk a
  // round-trip by refusing to submit an obviously non-compliant pack.
  const supplierLinesReady = !isTermsGated || lines
    .filter(l => l.description.trim() || Number(l.amount) > 0)
    .every(l => l.invoice_number?.trim() && l.invoice_date && l.due_date && l.discount_checked)
  const earlyReasonReady = !isEarly || Boolean(earlyReason.trim())
  // Every claims line must name the claim it settles (CFO 2026-07-29).
  const claimNumbersReady = nature !== 'claims' || lines
    .filter(l => l.description.trim() || Number(l.amount) > 0)
    .every(l => l.claim_number?.trim())
  // PAY-REFUND-03 (CFO 2026-09-14): an excess only exists because of a claim,
  // so an excess refund must name it too — or the refund cannot be verified
  // later. Excess refunds ONLY: an erroneous payment has no claim behind it.
  const isExcessRefund = nature === 'refund' && refundCategory === 'excess_refund'
  const excessClaimNumbersReady = !isExcessRefund || lines
    .filter(l => l.description.trim() || Number(l.amount) > 0)
    .every(l => l.claim_number?.trim())
  // Both questions must be answered — that is the control, not a nicety.
  const natureReady = entityCategory
    ? true
    : nature === 'operations'
      ? Boolean(opsCategory)
      : nature === 'claims' ? Boolean(claimPayeeType) && !claimsBlocked
      // B8: a refund needs BOTH the kind and the payment it reverses. Both are
      // structural 400s on the server (PAY-REFUND-02), so both hold the button
      // — the same way the claims questions do.
      : nature === 'refund' ? Boolean(refundCategory) && Boolean(originalPaymentRef.trim())
          // PAY-REFUND-03 is a hard 400 too, so it belongs here rather than in
          // the amber hints — the field is always fillable, so this can never
          // dead-end the raiser.
          && excessClaimNumbersReady
      : false
  // PAY-BANK-03: a first-ever payee needs the confirmation ticked. The server
  // refuses without it either way; this only saves a wasted round-trip and
  // shows the person WHY the button is inactive.
  const newPayeeReady = !firstPayment || bankChange !== null || newPayeeConfirmed
  // POP recipient required per line (Finance spec 2026-09-08): "Make it required
  // per line before the '+ Add line' total can be submitted." An off-list
  // address is still allowed through — that is the second approver's call, not
  // this button's — so only a BLANK holds the submit.
  const popRecipientsReady = lines
    .filter(l => l.description.trim() || Number(l.amount) > 0)
    .every(l => (l.pop_recipient_email || '').trim().length > 0)
  // PAY-BANK-04 (Finance spec 2026-09-08): the beneficiary panel is flagging
  // something — a first-time payee, or an account that differs from the one we
  // hold. Either way "Verified by" stops being optional.
  const bankCrossCheck = Boolean(firstPayment || bankChange)
  const verifierReady = !bankCrossCheck || verifier.trim().length > 0
  // 🔴 NO BLOCKER (CFO 2026-09-09): "there should be no blocker — the committee
  // decides and the CFO is notified." The raiser is NEVER dead-ended. The only
  // things that stop Submit are structural — a subject and something to pay.
  // Every other check (supplier terms, early reason, claim numbers, first payee,
  // POP recipient, verifier, the nature answers) is now an amber HINT below, and
  // the server routes an incomplete or risky pack to the payment committee
  // instead of refusing it. The readiness flags survive only to drive those hints.
  // natureReady stays part of the gate: the kind of payment (claims vs
  // operations, the claim payee type, ADIC-only claims) is still a structural
  // 400 on the server — it is a required question, not a judgement block — and
  // the dropdowns always let it be answered. Everything else is an amber hint.
  const canSubmit = Boolean(subject.trim()) && total > 0 && !busy && natureReady
  // Amber notes shown to the raiser (not blockers) — what will go to the committee.
  const willGoToCommittee = !supplierLinesReady || !earlyReasonReady || !claimNumbersReady
    || !newPayeeReady || !popRecipientsReady || !verifierReady

  // The exception POP (CFO 2026-09-02): if a control routed this to the
  // committee, tell the raiser plainly — never a silent fail — before closing.
  if (exceptionNotice) {
    return (
      <ModalPortal>
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-[200] p-4">
          <Card className="w-full max-w-md">
            <CardContent className="p-5 space-y-3">
              <div className="flex items-center gap-2 text-[#92400E]">
                <AlertTriangle className="w-5 h-5" />
                <span className="font-semibold">This went to the committee</span>
              </div>
              <p className="text-sm text-[#374151] whitespace-pre-line">{exceptionNotice}</p>
              <div className="flex justify-end">
                <Button variant="primary" size="sm"
                        onClick={() => { setExceptionNotice(null); onSaved() }}>OK</Button>
              </div>
            </CardContent>
          </Card>
        </div>
      </ModalPortal>
    )
  }

  return (
    <ModalPortal>
      <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-[200] p-4">
        <Card className="w-full max-w-2xl max-h-[90vh] overflow-y-auto">
          <CardHeader className="flex flex-row items-center justify-between pb-2 sticky top-0 bg-white z-10">
            <CardTitle className="text-base">New payment request</CardTitle>
            <button onClick={onClose} className="text-[#9CA3AF] hover:text-[#374151]"><X className="w-4 h-4" /></button>
          </CardHeader>
          <CardContent className="space-y-3">
            {/* Upload the invoice (CFO 2026-09-01, 557a7689). "I was watching my
                employees loading an invoice today. It was actually horrible…
                these guys are manually typing these figures." The reader is the
                one already live on /payments/new since 2026-08-22 — it was
                simply never wired into THIS screen, the one finance actually
                raises payment authorisations on. Fills the form; the person
                still checks it, and every server-side control still runs. */}
            <div className="rounded-lg border border-[#1D3270]/25 bg-[#1D3270]/5 p-3 space-y-2">
              <label className="block text-xs font-semibold text-[#1D3270]">
                Upload the invoice — Omni reads it and fills this in
              </label>
              <div className="flex flex-wrap items-center gap-3">
                <input type="file" accept="application/pdf,.pdf,image/*"
                       disabled={invReading}
                       aria-label="Upload the invoice to fill this form"
                       onChange={e => { const f = e.target.files?.[0] ?? null
                                        e.target.value = ''    // same file twice
                                        readInvoice(f) }}
                       className="text-sm file:mr-3 file:rounded-md file:border-0 file:bg-[#1D3270] file:text-white file:px-3 file:py-1.5 file:text-xs file:cursor-pointer disabled:opacity-50" />
                {invReading && <span className="text-[11px] text-[#1D3270]">Reading the invoice…</span>}
              </div>
              <p className="text-[11px] text-[#6B7280]">
                PDF or a photo. It fills in the payee, the amount, the invoice
                number and the bank details — it never fills over something you
                already typed, and nothing is sent until you press Send.
              </p>
              {invMsg && <p className="text-[11px] text-[#065F46]">{invMsg}</p>}
              {invErr && <p className="text-[11px] text-[#B91C1C]">{invErr}</p>}
            </div>

            {/* Smart fill (CFO 2026-07-29) — paste messy text, omni's cleanup
                engine fills the lines. Advisory: the form still validates on
                submit, so a bad parse can never become a payment. */}
            <div className="rounded border border-dashed border-[#F4A623]/60 bg-[#F4A623]/5 p-3 space-y-2">
              <label className="block text-xs font-semibold text-[#374151]">
                Smart fill — paste messy text and let it tidy up
              </label>
              <textarea value={pasteText} onChange={e => setPasteText(e.target.value)}
                        rows={3} placeholder="Paste an email, WhatsApp or list here — e.g. Carfil, claim G2026004287, invoice KA-40118 dated 24/07, P5,307.32…"
                        className="w-full border rounded px-2 py-1.5 text-sm bg-background" />
              <div className="flex items-center gap-3">
                <Button type="button" variant="secondary" size="sm"
                        onClick={smartFill} disabled={!pasteText.trim() || pasting}>
                  {pasting ? 'Cleaning up…' : 'Clean up & fill'}
                </Button>
                <span className="text-[11px] text-[#6B7280]">
                  It only fills the fields — nothing is sent until you submit.
                </span>
              </div>
              {pasteMsg && <p className="text-[11px] text-[#92400E]">{pasteMsg}</p>}
            </div>

            <div className="grid grid-cols-3 gap-3">
              <div className="col-span-2">
                <label className="block text-xs text-[#6B7280] mb-1">Entity</label>
                <select value={entity} onChange={e => setEntity(e.target.value)}
                        className="w-full border rounded px-3 py-1.5 text-sm bg-background">
                  {companies.length === 0 && <option value={entity}>{entity}</option>}
                  {companies.map(c => <option key={c.id} value={c.name}>{c.name}</option>)}
                </select>
              </div>
              <div>
                <label className="block text-xs text-[#6B7280] mb-1">Currency</label>
                <select value={currency} onChange={e => setCurrency(e.target.value)}
                        className="w-full border rounded px-3 py-1.5 text-sm bg-background">
                  {CURRENCIES.map(c => <option key={c} value={c}>{c}</option>)}
                </select>
              </div>
            </div>
            {/* Question 1 — claims or operations. Asked before anything else so
                the two are never confused: a repairer paid through claims payable
                is still a supplier invoice and must carry its dates
                (CFO 2026-07-29). No auto-detect here on purpose.
                Entity-specific companies (Unicoin, Quantum, etc.) skip this — their
                category and FNB source account are auto-set (CFO 2026-09-10). */}
            {entityCategory ? (
              <div className="rounded border border-[#D1FAE5] bg-[#ECFDF5] p-3">
                <div className="text-xs font-semibold text-[#065F46]">
                  Paying from {entity}
                </div>
                <div className="text-[11px] text-[#047857] mt-0.5">
                  This payment will go through the {entity} FNB account automatically.
                </div>
              </div>
            ) : (
            <div className="rounded border border-[#E5E7EB] p-3 space-y-2">
              <div className="text-xs font-semibold text-[#374151]">
                What kind of payment is this? <span className="text-[#991B1B]">*</span>
              </div>
              <div className="grid grid-cols-3 gap-2">
                <button type="button"
                        onClick={() => { setNature('claims'); setOpsCategory(''); setRefundCategory('') }}
                        disabled={claimsBlocked}
                        className={`rounded border px-3 py-2 text-sm text-left transition-colors
                          ${nature === 'claims'
                            ? 'border-[#F4A623] bg-[#FFF7ED] text-[#92400E] font-semibold'
                            : 'border-[#E5E7EB] hover:bg-[#F9FAFB]'}
                          ${claimsBlocked ? 'opacity-40 cursor-not-allowed' : ''}`}>
                  Claims payment
                  <span className="block text-[11px] font-normal text-[#6B7280]">
                    Settling a claim — ADIC only
                  </span>
                </button>
                <button type="button"
                        onClick={() => { setNature('operations'); setClaimPayeeType(''); setRefundCategory('') }}
                        className={`rounded border px-3 py-2 text-sm text-left transition-colors
                          ${nature === 'operations'
                            ? 'border-[#F4A623] bg-[#FFF7ED] text-[#92400E] font-semibold'
                            : 'border-[#E5E7EB] hover:bg-[#F9FAFB]'}`}>
                  Operations payment
                  <span className="block text-[11px] font-normal text-[#6B7280]">
                    Suppliers, vendors, rent, petty cash
                  </span>
                </button>
                {/* B8 — the third card. A peer of the two above, NOT a category
                    inside operations: a refund reverses money that already went
                    out, and counting it as an expense overstated operations. */}
                <button type="button"
                        onClick={() => { setNature('refund'); setOpsCategory(''); setClaimPayeeType('') }}
                        className={`rounded border px-3 py-2 text-sm text-left transition-colors
                          ${nature === 'refund'
                            ? 'border-[#F4A623] bg-[#FFF7ED] text-[#92400E] font-semibold'
                            : 'border-[#E5E7EB] hover:bg-[#F9FAFB]'}`}>
                  Refund payment
                  <span className="block text-[11px] font-normal text-[#6B7280]">
                    Premium, erroneous and excess refunds
                  </span>
                </button>
              </div>
              {claimsBlocked && (
                <div className="text-[11px] text-[#991B1B]">
                  {entity} does not settle claims — only Alpha Direct Insurance
                  Company (ADIC) is the licensed insurer. Raise this as an
                  operations payment, or change the entity above.
                </div>
              )}

              {/* Question 2a — claims: who is actually being paid. This is what
                  decides whether the invoice/due-date gate applies. */}
              {nature === 'claims' && !claimsBlocked && (
                <div className="pt-1 space-y-1.5">
                  <div className="text-xs font-semibold text-[#374151]">
                    Who is being paid? <span className="text-[#991B1B]">*</span>
                  </div>
                  {([
                    ['client', 'The client / policyholder direct',
                     'No third-party invoice — settled straight to the insured.'],
                    ['provider', 'A supplier, repairer or service provider',
                     'Panel beater, parts, hospital. Invoice numbers and dates required.'],
                  ] as const).map(([val, label, hint]) => (
                    <label key={val}
                           className={`flex items-start gap-2 rounded border px-3 py-2 text-sm cursor-pointer
                             ${claimPayeeType === val
                               ? 'border-[#F4A623] bg-[#FFF7ED]'
                               : 'border-[#E5E7EB] hover:bg-[#F9FAFB]'}`}>
                      <input type="radio" name="claim_payee_type" className="mt-1"
                             checked={claimPayeeType === val}
                             onChange={() => setClaimPayeeType(val)} />
                      <span>
                        {label}
                        <span className="block text-[11px] text-[#6B7280]">{hint}</span>
                      </span>
                    </label>
                  ))}
                </div>
              )}

              {/* Question 2c — refund: which kind, and which payment it reverses.
                  Premium Refund is rendered but DISABLED: the overnight importer
                  owns it (CFO 2026-08-11), and the server refuses a hand-keyed
                  one (PAY-REFUND-01). Showing it disabled answers the question
                  "where do premium refunds go?" instead of hiding it. */}
              {nature === 'refund' && (
                <div className="pt-1 space-y-2">
                  <label className="block text-xs font-semibold text-[#374151] mb-1">
                    Which kind? <span className="text-[#991B1B]">*</span>
                  </label>
                  <select value={refundCategory}
                          onChange={e => setRefundCategory(e.target.value as typeof refundCategory)}
                          className="w-full border rounded px-3 py-1.5 text-sm bg-background">
                    <option value="">Choose…</option>
                    {REFUND_CATEGORY_LIST.map(r => (
                      <option key={r.value} value={r.handRaised ? r.value : ''}
                              disabled={!r.handRaised}>
                        {r.label}{r.handRaised ? '' : ' — raised automatically, not here'}
                      </option>
                    ))}
                  </select>
                  {refundCategory && (
                    <p className="text-[11px] text-[#6B7280]">
                      {REFUND_CATEGORY_LIST.find(r => r.value === refundCategory)?.hint}
                    </p>
                  )}
                  <div>
                    <label className="block text-xs font-semibold text-[#374151] mb-1">
                      Original payment reference <span className="text-[#991B1B]">*</span>
                    </label>
                    <input value={originalPaymentRef}
                           onChange={e => setOriginalPaymentRef(e.target.value)}
                           maxLength={64}
                           placeholder="The payment being refunded — request ref, bank reference or receipt number"
                           className="w-full border rounded px-3 py-1.5 text-sm bg-background" />
                    <p className="text-[11px] text-[#6B7280] mt-0.5">
                      A refund with no original payment is not a refund. Without
                      this it cannot be tied back to source, and nothing stops the
                      same amount being refunded twice.
                    </p>
                  </div>
                  <p className="text-[11px] text-[#6B7280]">
                    Premium refunds are not keyed here. Approve the refund in
                    Graphite and it arrives on the Refunds tab automatically
                    overnight — keying one alongside the feed is how the same
                    refund gets paid twice.
                  </p>
                </div>
              )}

              {/* Question 2b — operations: which kind. Only show non-entity categories. */}
              {nature === 'operations' && (
                <div className="pt-1">
                  <label className="block text-xs font-semibold text-[#374151] mb-1">
                    Which kind? <span className="text-[#991B1B]">*</span>
                  </label>
                  <select value={opsCategory}
                          onChange={e => setOpsCategory(e.target.value as typeof opsCategory)}
                          className="w-full border rounded px-3 py-1.5 text-sm bg-background">
                    <option value="">Choose…</option>
                    {CATEGORIES.filter(c => c.value !== 'claim' && !ENTITY_CATEGORIES.has(c.value)).map(c => (
                      <option key={c.value} value={c.value}>{c.label}</option>
                    ))}
                  </select>
                </div>
              )}
            </div>
            )}

            <div>
              <label className="block text-xs text-[#6B7280] mb-1">Subject / purpose</label>
              <input value={subject} onChange={e => setSubject(e.target.value)}
                     placeholder="e.g. PAYE June payroll + FAC premium cession"
                     className="w-full border rounded px-3 py-1.5 text-sm bg-background" />
            </div>

            <div>
              <label className="block text-xs text-[#6B7280] mb-1">
                Bank narration <span className="text-[#9CA3AF]">(optional — auto-fills if left blank)</span>
              </label>
              <input value={bankNarration} onChange={e => setBankNarration(e.target.value)}
                     placeholder="What appears on the bank statement — leave blank for the default"
                     maxLength={140}
                     className="w-full border rounded px-3 py-1.5 text-sm bg-background" />
              {bankNarration.trim() && (
                <p className="text-[11px] text-[#D97706] mt-0.5">Custom narration — this overrides the automatic one</p>
              )}
            </div>

            {/* Line items */}
            <div>
              <div className="flex flex-wrap items-end justify-between gap-2 mb-1">
                <label className="block text-xs text-[#6B7280]">Payment lines</label>
                {/* Processing method (Kelvin Kimani spec 2026-09-08). Same paired
                    segmented control as the Suppliers section, so it reads as part
                    of the same system. Deliberately absent on a single line: bulk
                    and individual are then the same one payment, and offering a
                    meaningless choice is how a form teaches people to click past
                    the ones that matter. */}
                {lines.length > 1 && (
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-[#6B7280]">How it leaves</span>
                    <div className="inline-flex gap-1 rounded-lg bg-[#F3F4F6] p-1">
                      {([['bulk', 'Bulk'], ['individual', 'Individual']] as const).map(([v, label]) => (
                        <button key={v} type="button" onClick={() => setProcessingMethod(v)}
                                aria-pressed={processingMethod === v}
                                className={processingMethod === v ? SEG_ON : SEG_OFF}>
                          {label}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
              {lines.length > 1 && (
                <p className="text-[11px] text-[#6B7280] mb-2">
                  {processingMethod === 'bulk'
                    ? `One payment of ${money(currency, total)} settling all ${lines.length} invoices.`
                    : `${lines.length} separate payments, each tied to its own invoice number — ${money(currency, total)} in total.`}
                  {' '}The total is the same either way; only the number of transactions changes.
                </p>
              )}
              <div className="space-y-2">
                {lines.map((l, i) => (
                  <div key={i} className="grid grid-cols-12 gap-2 items-center">
                    <input value={l.description} onChange={e => setLine(i, { description: e.target.value })}
                           placeholder="Description" className="col-span-5 border rounded px-2 py-1.5 text-sm bg-background" />
                    <input value={l.gl_code} onChange={e => setLine(i, { gl_code: e.target.value })}
                           placeholder="GL" className="col-span-2 border rounded px-2 py-1.5 text-sm bg-background" />
                    <input value={l.ref} onChange={e => setLine(i, { ref: e.target.value })}
                           placeholder="Ref #" className="col-span-2 border rounded px-2 py-1.5 text-sm bg-background" />
                    <input value={l.amount as string} onChange={e => setLine(i, { amount: e.target.value })}
                           type="number" step="0.01" placeholder="Amount"
                           className="col-span-2 border rounded px-2 py-1.5 text-sm bg-background text-right" />
                    <button type="button" onClick={() => removeLine(i)}
                            className="col-span-1 text-[#9CA3AF] hover:text-[#B91C1C] flex justify-center" aria-label="Remove line">
                      <Trash2 className="w-4 h-4" />
                    </button>

                    {/* POP recipient, per line (Finance spec 2026-09-08).
                        Identical on the Claims and the Operations version of
                        this modal, as the spec asks — an operations line simply
                        has no claim to draw contacts from. */}
                    <div className="col-span-12 pl-2 pb-2 border-l-2 border-[#0D1B2A]/20">
                      <PopRecipientField
                        claimNumber={l.claim_number || ''}
                        payee={payee || accountName}
                        name={l.pop_recipient_name || ''}
                        email={l.pop_recipient_email || ''}
                        onChange={v => setLine(i, v)} />
                    </div>

                    {/* Claim number — required on every line of a CLAIM request
                        (CFO 2026-07-29), whoever is being paid, AND on every
                        line of an EXCESS REFUND (CFO 2026-09-14): an excess
                        only exists because of a claim, so the refund must name
                        it. This is how either pack is tied back to Graphite. */}
                    {(nature === 'claims' || isExcessRefund) && (
                      <div className="col-span-12 grid grid-cols-12 gap-2 items-end pl-2 pb-2 border-l-2 border-[#F4A623]">
                        <div className="col-span-4">
                          <label className="block text-[11px] text-[#6B7280] mb-0.5">Claim number</label>
                          <input value={l.claim_number || ''}
                                 onChange={e => setLine(i, { claim_number: e.target.value })}
                                 placeholder="e.g. G2026004287 — required"
                                 className="w-full border rounded px-2 py-1 text-sm bg-background" />
                        </div>
                        {/* Claim insight — facts + hard flags + AI summary pulled
                            from the Graphite mirror the moment a claim number is
                            typed (CFO 2026-08-31). Prefills the line description if
                            it is still empty; never overwrites what was typed. */}
                        <div className="col-span-8">
                          <ClaimInsightBox
                            claimNumber={l.claim_number || ''}
                            amount={l.amount}
                            payee={payee || accountName}
                            onFacts={(txt) => {
                              if (!String(l.description || '').trim()) setLine(i, { description: txt })
                            }}
                          />
                        </div>
                      </div>
                    )}

                    {/* Supplier terms — required on every line of a supplier
                        request (PAY-SUP-01). The due date is derived from the
                        invoice date and the agreed term, never typed. */}
                    {isTermsGated && (
                      <div className="col-span-12 grid grid-cols-12 gap-2 items-end pl-2 pb-2 border-l-2 border-[#F4A623]">
                        <div className="col-span-3">
                          <label className="block text-[11px] text-[#6B7280] mb-0.5">Invoice number</label>
                          <input value={l.invoice_number || ''}
                                 onChange={e => setLine(i, { invoice_number: e.target.value })}
                                 placeholder="required"
                                 className="w-full border rounded px-2 py-1 text-sm bg-background" />
                        </div>
                        <div className="col-span-2">
                          <label className="block text-[11px] text-[#6B7280] mb-0.5">Invoice date</label>
                          <input type="date" value={l.invoice_date || ''}
                                 onChange={e => setLine(i, {
                                   invoice_date: e.target.value,
                                   due_date: dueFromTerms(e.target.value, l.terms_days || '30',
                                                          l.terms_basis || 'statement'),
                                 })}
                                 className="w-full border rounded px-2 py-1 text-sm bg-background" />
                        </div>
                        <div className="col-span-2">
                          <label className="block text-[11px] text-[#6B7280] mb-0.5">Terms run from</label>
                          <select value={l.terms_basis || 'statement'}
                                  onChange={e => setLine(i, {
                                    terms_basis: e.target.value,
                                    due_date: dueFromTerms(l.invoice_date || '', l.terms_days || '30',
                                                           e.target.value),
                                  })}
                                  className="w-full border rounded px-2 py-1 text-sm bg-background">
                            <option value="statement">Statement</option>
                            <option value="invoice">Invoice</option>
                          </select>
                        </div>
                        <div className="col-span-2">
                          <label className="block text-[11px] text-[#6B7280] mb-0.5">Terms (days)</label>
                          <input type="number" min="0" value={l.terms_days ?? '30'}
                                 onChange={e => setLine(i, {
                                   terms_days: e.target.value,
                                   due_date: dueFromTerms(l.invoice_date || '', e.target.value,
                                                          l.terms_basis || 'statement'),
                                 })}
                                 className="w-full border rounded px-2 py-1 text-sm bg-background text-right" />
                        </div>
                        <div className="col-span-3">
                          <label className="block text-[11px] text-[#6B7280] mb-0.5">Due date (derived)</label>
                          <input type="date" value={l.due_date || ''} readOnly
                                 className="w-full border rounded px-2 py-1 text-sm bg-[#F9FAFB] text-[#374151]" />
                        </div>
                        <label className="col-span-12 flex items-start gap-2 text-xs text-[#374151]">
                          <input type="checkbox" checked={!!l.discount_checked}
                                 onChange={e => setLine(i, { discount_checked: e.target.checked })}
                                 className="mt-0.5" />
                          <span>
                            I checked whether an early-settlement or offshore discount applies to this invoice.
                          </span>
                        </label>
                      </div>
                    )}
                  </div>
                ))}
              </div>

              {/* Reload a previous payment (bug 8d3f6dd0) */}
              <div className="mt-3 border-t border-[#0D1B2A]/10 pt-3">
                <button type="button" onClick={() => setShowReloadPanel(!showReloadPanel)}
                        className="flex items-center gap-2 text-sm text-[#374151] hover:text-[#0D1B2A] font-medium w-full p-2 rounded hover:bg-[#F3F4F6] transition-colors">
                  <ChevronDown className={`w-4 h-4 transition-transform ${showReloadPanel ? 'rotate-180' : ''}`} />
                  Reload a previous payment
                </button>

                {showReloadPanel && (
                  <div className="mt-2 p-3 bg-[#F9FAFB] border border-[#E5E7EB] rounded space-y-2">
                    <div className="flex gap-2">
                      <input
                        value={reloadPayeeSearch}
                        onChange={e => setReloadPayeeSearch(e.target.value)}
                        onKeyDown={e => e.key === 'Enter' && searchReloadPayments()}
                        placeholder="Supplier / payee name"
                        className="flex-1 border rounded px-2 py-1.5 text-sm bg-white"
                        aria-label="Search for previous supplier payments"
                      />
                      <button type="button" onClick={searchReloadPayments} disabled={reloadLoading}
                              className="text-sm px-3 py-1.5 rounded bg-[#0D1B2A] text-white hover:bg-[#1F2A3A] disabled:opacity-50 flex items-center gap-1">
                        <Search className="w-4 h-4" /> Search
                      </button>
                    </div>

                    {reloadError && (
                      <p className="text-[11px] text-[#B91C1C]">{reloadError}</p>
                    )}

                    {reloadResults && reloadResults.rows.length > 0 && (
                      <div className="max-h-[300px] overflow-y-auto border border-[#E5E7EB] rounded bg-white">
                        <table className="w-full text-xs">
                          <thead className="bg-[#F3F4F6] border-b border-[#E5E7EB] sticky top-0">
                            <tr>
                              <th className="text-left px-2 py-1.5 font-semibold text-[#374151]">Date</th>
                              <th className="text-left px-2 py-1.5 font-semibold text-[#374151]">Ref</th>
                              <th className="text-left px-2 py-1.5 font-semibold text-[#374151]">Payee</th>
                              <th className="text-left px-2 py-1.5 font-semibold text-[#374151]">Description</th>
                              <th className="text-left px-2 py-1.5 font-semibold text-[#374151]">Invoice #</th>
                              <th className="text-right px-2 py-1.5 font-semibold text-[#374151]">Amount</th>
                              <th className="text-left px-2 py-1.5 font-semibold text-[#374151]">Status</th>
                              <th className="text-center px-2 py-1.5 font-semibold text-[#374151]">Action</th>
                            </tr>
                          </thead>
                          <tbody>
                            {reloadResults.rows.slice(0, 50).map((row, idx) => (
                              <tr key={idx} className={`border-b border-[#E5E7EB] ${row.cancelled ? 'bg-[#FAFAFA] text-[#9CA3AF]' : ''}`}>
                                <td className="px-2 py-1 text-[#6B7280]">{row.date || '—'}</td>
                                <td className="px-2 py-1 text-[#6B7280]">{row.ref}</td>
                                <td className="px-2 py-1 text-[#6B7280]">{row.payee}</td>
                                <td className="px-2 py-1 text-[#6B7280] max-w-[200px] truncate">{row.description}</td>
                                <td className="px-2 py-1 text-[#6B7280]">{row.invoice_number || '—'}</td>
                                <td className="px-2 py-1 text-right text-[#6B7280] tabular-nums">{row.amount}</td>
                                <td className="px-2 py-1 text-[#6B7280]">{row.status_label}</td>
                                <td className="px-2 py-1 text-center">
                                  {row.copyable && !row.cancelled && (
                                    <button type="button" onClick={() => addReloadedLine(row)}
                                            className="text-[#0D1B2A] hover:text-[#B45309] font-medium text-xs">
                                      Add
                                    </button>
                                  )}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}

                    {reloadResults && reloadResults.rows.length === 0 && !reloadLoading && (
                      <p className="text-[11px] text-[#6B7280]">No payments found for that supplier in the past 12 months.</p>
                    )}
                  </div>
                )}
              </div>

              <div className="flex items-center justify-between mt-2">
                <button type="button" onClick={addLine}
                        className="text-sm text-[#B45309] hover:text-[#92400E] flex items-center gap-1">
                  <Plus className="w-4 h-4" /> Add line
                </button>
                <div className="text-sm font-semibold tabular-nums">
                  Total: {money(currency, total)}
                </div>
              </div>
            </div>

            <div className="rounded border border-[#0D1B2A]/15 bg-[#F9FAFB] p-3 mb-3">
              <div className="text-xs font-semibold text-[#0D1B2A] uppercase tracking-wide mb-1">
                Where the money goes
              </div>
              <p className="text-[11px] text-[#6B7280] mb-3">
                Fill this in once. When finance signs the request off it loads
                straight into FNB — nobody types it into the bank again. It still
                waits for the CFO to authorise it in FNB.
              </p>
              <div className="grid grid-cols-2 gap-3">
                <div className="col-span-2">
                  <label className="block text-xs text-[#6B7280] mb-1">
                    Account holder name <span className="text-[#6B7280]">(as the bank has it)</span>
                  </label>
                  <input value={accountName} onChange={e => setAccountName(e.target.value)}
                         placeholder="e.g. ABC Traders (Pty) Ltd"
                         className="w-full border rounded px-3 py-1.5 text-sm bg-background" />
                </div>
                <div>
                  <label className="block text-xs text-[#6B7280] mb-1">Bank</label>
                  <input value={bankName} onChange={e => setBankName(e.target.value)}
                         placeholder="e.g. FNB Botswana" className="w-full border rounded px-3 py-1.5 text-sm bg-background" />
                </div>
                <div>
                  <label className="block text-xs text-[#6B7280] mb-1">Account number</label>
                  <input value={accountNumber} onChange={e => { setAccountNumber(e.target.value); setUseKnownAccount(false) }}
                         className="w-full border rounded px-3 py-1.5 text-sm bg-background" />
                </div>
                <div>
                  <label className="block text-xs text-[#6B7280] mb-1">
                    Branch code {bankName && !/fnb|first national/i.test(bankName)
                      ? <span className="text-[#B45309]">— needed for this bank</span>
                      : <span className="text-[#6B7280]">(FNB payees can leave blank)</span>}
                  </label>
                  <input value={branchCode} onChange={e => setBranchCode(e.target.value)}
                         placeholder="e.g. 293567"
                         className="w-full border rounded px-3 py-1.5 text-sm bg-background" />
                </div>
                <div>
                  <label className="block text-xs text-[#6B7280] mb-1">Account type</label>
                  <select value={accountType}
                          onChange={e => { accountTypeTouched.current = true
                                           setAccountType(e.target.value) }}
                          className="w-full border rounded px-3 py-1.5 text-sm bg-background">
                    <option value="CACC">Cheque / current</option>
                    <option value="SVGS">Savings</option>
                  </select>
                </div>
              </div>

              {bankPrefill && !bankChange && (
                <p className="mt-3 text-[11px] text-[#065F46] bg-[#ECFDF5] border border-[#A7F3D0] rounded px-2 py-1.5">
                  {bankPrefill}
                </p>
              )}

              {bankChange && (
                <div className="mt-3 rounded border-2 border-[#B91C1C] bg-[#FEF2F2] p-3">
                  <div className="text-xs font-bold text-[#B91C1C] uppercase tracking-wide mb-1">
                    This is a different bank account
                  </div>
                  <p className="text-xs text-[#7F1D1D] whitespace-pre-line">{bankChange}</p>
                  <label className="block text-xs text-[#7F1D1D] mt-3 mb-1">
                    What happened, and who confirmed it? (the committee reads this — say who you phoned)
                  </label>
                  <textarea value={bankChangeReason}
                            onChange={e => setBankChangeReason(e.target.value)}
                            rows={3}
                            placeholder="e.g. Supplier moved from Absa to FNB. Confirmed by phone with Mr Dube on the number we already had for them, 20 Aug."
                            className="w-full border border-[#B91C1C]/40 rounded px-2 py-1.5 text-sm bg-white" />
                  {bankChangeReason.trim().length > 0 && bankChangeReason.trim().length < 20 && (
                    <p className="mt-1 text-[11px] text-[#B91C1C]">
                      Please say a little more - this is the record of why the account changed.
                    </p>
                  )}
                </div>
              )}

              {/* PAY-BANK-03 — the payee we have never paid. Deliberately a
                  tick, not an essay: there is no previous account to explain a
                  change from, and demanding a paragraph for every genuinely new
                  supplier just trains people to paste filler to get past it.
                  The server refuses the request without this. */}
              {firstPayment && !bankChange && (
                <div className="mt-3 rounded border-2 border-[#B45309] bg-[#FFFBEB] p-3">
                  <div className="text-xs font-bold text-[#B45309] uppercase tracking-wide mb-1">
                    First payment to this payee
                  </div>
                  <p className="text-xs text-[#78350F] whitespace-pre-line">{firstPayment}</p>
                  <label className="mt-3 flex items-start gap-2 text-xs text-[#78350F] cursor-pointer select-none">
                    <input type="checkbox" checked={newPayeeConfirmed}
                           onChange={e => setNewPayeeConfirmed(e.target.checked)}
                           className="mt-0.5" />
                    <span>
                      I checked the account number against the invoice, digit for
                      digit, and confirmed this supplier using a contact I already
                      had — not a number printed on the invoice. <b>(required)</b>
                    </span>
                  </label>
                </div>
              )}
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs text-[#6B7280] mb-1">Opening balance (optional)</label>
                <input value={opening} onChange={e => setOpening(e.target.value)}
                       type="number" step="0.01" className="w-full border rounded px-3 py-1.5 text-sm bg-background text-right" />
              </div>
              <div>
                <label className="block text-xs text-[#6B7280] mb-1">Due date (optional)</label>
                <input type="date" value={dueAt} onChange={e => setDueAt(e.target.value)}
                       className="w-full border rounded px-3 py-1.5 text-sm bg-background" />
              </div>
              {/* PAY-BANK-04 (Finance spec 2026-09-08). This was optional. It is
                  now REQUIRED whenever the beneficiary panel is flagging a
                  first-time payee or changed bank details, because that flag was
                  informational and nobody had to act on it. It must not be the
                  preparer — the server refuses a self-named verifier, and the
                  finance approver confirms the details against the attached
                  document at sign-off. */}
              <div className="col-span-2">
                <label className="block text-xs text-[#6B7280] mb-1">
                  Verified by{' '}
                  {bankCrossCheck
                    ? <span className="text-[#991B1B]">* required — the beneficiary account is flagged</span>
                    : <span className="text-[#6B7280]">(optional)</span>}
                </label>
                <input value={verifier} onChange={e => setVerifier(e.target.value)}
                       placeholder={bankCrossCheck
                         ? 'Someone in Finance who checked these details against the document'
                         : 'Name of the person who verified the payment'}
                       className={cn('w-full border rounded px-3 py-1.5 text-sm bg-background',
                                     bankCrossCheck && !verifier.trim()
                                       && 'border-[#B91C1C] ring-1 ring-[#B91C1C]/30')} />
                {bankCrossCheck && (
                  <p className="mt-1 text-[11px] text-[#7F1D1D]">
                    Not you — somebody else in Finance must check the bank name, branch
                    code and account number against the beneficiary details on the
                    supporting document. Attach that document below; the approver
                    confirms the match before this is signed off.
                  </p>
                )}
              </div>
            </div>

            {/* Supplier terms compliance (PAY-SUP-01, CFO 2026-07-28). Only for
                supplier payments — claims and operational payments skip this. */}
            {isTermsGated && (
              <div className="rounded border border-[#F4A623]/60 bg-[#FFFBEB] p-3 space-y-3">
                <div className="text-xs font-semibold text-[#92400E] uppercase tracking-wide">
                  Supplier terms compliance
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-xs text-[#6B7280] mb-1">Payment date (when money leaves)</label>
                    <input type="date" value={paymentDate} onChange={e => setPaymentDate(e.target.value)}
                           className="w-full border rounded px-3 py-1.5 text-sm bg-background" />
                  </div>
                  <div>
                    <label className="block text-xs text-[#6B7280] mb-1">Payable from</label>
                    <div className="px-3 py-1.5 text-sm tabular-nums">
                      {bindingDue || <span className="text-[#9CA3AF]">enter the invoice dates above</span>}
                    </div>
                  </div>
                </div>

                {isEarly && (
                  <div className="space-y-2">
                    <div className="text-sm text-[#991B1B]">
                      This payment is <strong>{daysEarly} day(s) early</strong>. It is only payable
                      from {bindingDue}. Move the payment date, or state the reason the CFO approved
                      settling early.
                    </div>
                    <textarea value={earlyReason} onChange={e => setEarlyReason(e.target.value)}
                              rows={2}
                              placeholder="Reason the CFO approved early settlement (e.g. 5% settlement discount taken)"
                              className="w-full border rounded px-3 py-1.5 text-sm bg-background" />
                  </div>
                )}

                <label className="flex items-start gap-2 text-xs text-[#374151]">
                  <input type="checkbox" checked={fundsMoved}
                         onChange={e => setFundsMoved(e.target.checked)} className="mt-0.5" />
                  <span>
                    Cash was already transferred between accounts to fund this request
                    (before it was authorised). This is shown to the approver.
                  </span>
                </label>
              </div>
            )}

            {/* Optional supporting documents — e.g. an agreement of loss for a
                claim payment, an invoice or a quote (CFO 2026-07-15). */}
            <div>
              <label className="block text-xs text-[#6B7280] mb-1">
                Supporting documents (optional)
              </label>
              <label className="flex items-center gap-2 text-sm text-[#B45309] hover:text-[#92400E] cursor-pointer w-fit">
                <Paperclip className="w-4 h-4" />
                <span>Attach files</span>
                <input type="file" multiple className="hidden"
                       onChange={e => setFiles(f => [...f, ...Array.from(e.target.files || [])])} />
              </label>
              {files.length > 0 && (
                <ul className="mt-2 space-y-1">
                  {files.map((f, i) => (
                    <li key={i} className="flex items-center justify-between text-xs bg-[#F9FAFB] rounded px-2 py-1">
                      <span className="truncate">{f.name}</span>
                      <button type="button" onClick={() => setFiles(fs => fs.filter((_, idx) => idx !== i))}
                              className="text-[#9CA3AF] hover:text-[#B91C1C]" aria-label="Remove file">
                        <X className="w-3.5 h-3.5" />
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            {/* Loading window PAY-WIN-02 (CFO 2026-09-01). Loading is limited to
                08:00–09:15. Outside it the raiser is stopped and offered the
                one-click "ask the CFO" escape hatch, which is recorded. */}
            {winBlock && (
              <div className="rounded border-2 border-amber-500 bg-amber-50 p-3 space-y-2">
                <p className="text-sm font-semibold text-amber-900 flex items-center gap-1.5">
                  <AlertTriangle className="h-4 w-4" /> Payments are closed for loading right now
                </p>
                <p className="text-xs text-amber-900 whitespace-pre-line">{winBlock.detail}</p>
                {winSent || winBlock.pending ? (
                  <p className="text-xs font-medium text-amber-900 bg-amber-100 rounded px-2 py-1.5">
                    Your request to load now has been sent to the CFO. You&apos;ll be able to load
                    once he approves it. Until then, please come back tomorrow morning.
                  </p>
                ) : (
                  <>
                    <p className="text-xs text-amber-900">
                      If this genuinely cannot wait until tomorrow, ask the CFO for approval to
                      load now. Say why below — it is kept on record.
                    </p>
                    <textarea value={winReason} onChange={e => setWinReason(e.target.value)}
                              rows={2}
                              placeholder="e.g. supplier cut-off is today at noon and the invoice only arrived now."
                              className="w-full border rounded px-2 py-1.5 text-sm bg-background" />
                    <Button size="sm" onClick={askCfoToLoad} disabled={winBusy}
                            className="bg-amber-600 hover:bg-amber-700 text-white">
                      {winBusy ? 'Sending…' : 'Request CFO approval to load now'}
                    </Button>
                  </>
                )}
              </div>
            )}
            {/* Duplicate control PAY-DUP-01 (CFO 2026-08-03). The server refused
                because these lines are already raised or paid. Removing them is
                the expected action; the override exists only for the real case
                where a payment must genuinely go out again (the bank rejected the
                first attempt), and it is written onto the authorisation pack. */}
            {dupBlock && (
              <div className="rounded border-2 border-red-600 bg-red-50 p-3 space-y-2">
                <p className="text-sm font-semibold text-red-800">
                  Already raised or paid — this request was not sent
                </p>
                {dupBlock.rows.length > 0 ? (
                  <ul className="text-xs text-red-800 space-y-1 list-disc pl-4">
                    {dupBlock.rows.map((r, i) => (
                      <li key={i}>
                        <span className="font-semibold">Line {r.line}</span> ({r.label}) — {r.detail}.
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-xs text-red-800 whitespace-pre-line">{dupBlock.detail}</p>
                )}
                <p className="text-xs text-red-800">
                  Delete those lines and submit the rest. If the bank rejected the
                  earlier payment, re-send it from the FNB rejects screen — a payment
                  is never raised twice here.
                </p>
              </div>
            )}

            {err && <p className="text-xs text-red-700">{err}</p>}
            {willGoToCommittee && (
              <p className="text-xs text-[#B45309] whitespace-pre-line">
                Some details are still open (supplier terms, dates, claim number,
                payee or verifier). You are not blocked — you can submit, and the
                payment committee will decide it. Nothing here can move money;
                that stays with the bank and the CFO&apos;s phone approval.
              </p>
            )}
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="secondary" size="sm" onClick={onClose}>Cancel</Button>
              <Button variant="primary" size="sm" onClick={submit} loading={busy} disabled={!canSubmit}>
                <Send className="w-4 h-4 mr-1" /> Submit for finance sign-off
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    </ModalPortal>
  )
}


function PaymentDetailDrawer({ id, isCfo, onClose, onChanged }: { id: string; isCfo: boolean; onClose: () => void; onChanged: () => void }) {
  const [d, setD] = useState<PaymentRequestDetail | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [showComplete, setShowComplete] = useState(false)
  const [showReassign, setShowReassign] = useState(false)
  const [reTo, setReTo] = useState('')
  const [reReason, setReReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [rejectNote, setRejectNote] = useState('')
  const [showReject, setShowReject] = useState(false)
  const [showClear, setShowClear] = useState(false)
  const [clearNote, setClearNote] = useState('')
  // Recording the bank's own answer when a person declined the authorisation
  // inside the FNB app — FNB never tells Omni (Leano Makwapa 2026-09-11).
  const [markingRejected, setMarkingRejected] = useState(false)
  const [fnbBusy, setFnbBusy] = useState(false)
  // Duplicate control PAY-DUP-01 (CFO 2026-08-03) — surfaced at sign-off for the
  // requests that predate the creation gate.
  const [dupBlock, setDupBlock] = useState<{ detail: string; rows: DuplicateRow[] } | null>(null)
  // PAY-BANK-ACK (Kago 2026-08-29) — the payee's account changed since last time;
  // the finance approver must positively tick that they have checked it before
  // the sign-off is allowed through.
  const [bankChange, setBankChange] = useState<PaymentBankChange | null>(null)
  const [bankAck, setBankAck] = useState(false)
  // PAY-POP-ACK (Finance spec 2026-09-08) — the proof of payment names an
  // address Omni does not hold for this claim or payee. The finance approver
  // is the second person the spec asks for: they can never be the raiser
  // (segregation of duties is enforced server-side), and the tick is logged.
  const [popOffList, setPopOffList] = useState<{ line: number; name: string; email: string }[] | null>(null)
  const [popAck, setPopAck] = useState(false)
  // PAY-BANK-04 / PAY-BANK-DOC (Finance spec 2026-09-08) — the bank details
  // differ from what Omni holds for this payee, so Finance must confirm them
  // against the beneficiary details on the attached supporting document
  // before this can pass to the CFO. Never the preparer: the raiser cannot
  // sign off their own request at all.
  const [bankDoc, setBankDoc] = useState<{ detail: string; needsDocument: boolean; needsVerifier: boolean } | null>(null)
  const [bankDocAck, setBankDocAck] = useState(false)
  // PAY-BANK-05 — the committee's way to FIX the branch code the bank rejected.
  // Without this box the endpoint exists and nobody can reach it.
  const [branchFix, setBranchFix] = useState('')
  const [branchBusy, setBranchBusy] = useState(false)
  const [branchMsg, setBranchMsg] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => {
    getPaymentRequest(id).then(x => {
      setD(x)
      setBranchFix(x.branch_code || '')
    }).catch(e =>
      setErr(e instanceof Error ? e.message : 'Could not load'))
  }, [id])

  // Once the CFO has paid or handed it off, the task is closed — show the state
  // instead of the action buttons.
  const taskDone = d?.task_status === 'done' || d?.task_status === 'cancelled'

  // Stage-1 finance sign-off (CFO 2026-07-23) — only a finance approver, on a
  // request still awaiting finance, sees Approve / Reject.
  async function decide(decision: 'approve' | 'reject') {
    if (!d) return
    if (decision === 'reject' && !rejectNote.trim()) { setErr('Give a reason to reject.'); return }
    setBusy(true); setErr(null)
    try {
      await decidePaymentRequest(d.id, decision, rejectNote.trim(), bankAck, popAck, bankDocAck)
      onChanged(); onClose()
    } catch (e) {
      // PAY-DUP-01 (CFO 2026-08-03): a sign-off refused as a duplicate must show
      // WHICH request already carries the money — the approver's next action is
      // to reject this one, and they need the reference to write in the reason.
      const body = (e as { body?: { control?: string; detail?: string; duplicates?: DuplicateRow[]; bank?: { change?: PaymentBankChange | null }; pop_off_list?: { line: number; name: string; email: string }[]; needs_document?: boolean; needs_verifier?: boolean } })?.body
      if (body?.control === 'PAY-DUP-01') {
        setDupBlock({ detail: body.detail || 'This request repeats lines already raised or paid.',
                      rows: body.duplicates || [] })
        setErr(null)
      } else if (body?.control === 'PAY-BANK-ACK') {
        // The account changed since the last payment — surface it and require a
        // positive tick before we retry the sign-off.
        setBankChange(body.bank?.change || null)
        setErr(null)
      } else if (body?.control === 'PAY-POP-ACK') {
        // The proof of payment goes somewhere we have no record of. Same shape
        // as the changed-account gate: show it, require the tick, then retry.
        setPopOffList(body.pop_off_list || [])
        setErr(null)
      } else if (body?.control === 'PAY-BANK-04' || body?.control === 'PAY-BANK-DOC') {
        // The bank cross-check. Three separate holds — nobody named as verifier,
        // no document to check against, or the confirmation not given — so the
        // approver is told which one it is rather than "something is wrong".
        setBankDoc({ detail: body.detail || 'The bank details need confirming.',
                     needsDocument: Boolean(body.needs_document),
                     needsVerifier: Boolean(body.needs_verifier) })
        setErr(null)
      } else {
        setErr(e instanceof Error ? e.message : 'Could not record your decision')
      }
    } finally { setBusy(false) }
  }

  async function reassign() {
    if (!reTo || !d?.task_id) return
    setBusy(true); setErr(null)
    try {
      await updateOmniTask(d.task_id, { reassign_to: reTo, reassign_reason: reReason || undefined })
      onChanged(); onClose()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not reassign')
    } finally { setBusy(false) }
  }

  // CFO clears a signed-off request out of the queue without paying it
  // (expired / paid outside Omni / duplicate). A reason is required.
  async function clearFromQueue() {
    if (!d || !clearNote.trim()) { setErr('Give a reason to clear this request.'); return }
    setBusy(true); setErr(null)
    try {
      await clearPaymentRequest(d.id, clearNote.trim())
      onChanged(); onClose()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not clear this request')
    } finally { setBusy(false) }
  }

  return (
    <ModalPortal>
      <div className="fixed inset-0 bg-black/40 z-[200] flex justify-end" onClick={onClose}>
        <div className="bg-white w-full max-w-2xl h-full overflow-y-auto p-6" onClick={e => e.stopPropagation()}>
          <div className="flex items-start justify-between mb-4">
            <h2 className="text-lg font-bold">Payment authorisation</h2>
            <button onClick={onClose} className="text-[#9CA3AF] hover:text-[#374151]"><X className="w-4 h-4" /></button>
          </div>
          {err && <p className="text-sm text-red-700">{err}</p>}
          {!d ? (
            <p className="text-sm text-[#6B7280]">Loading…</p>
          ) : (
            <>
              {/* The bank REJECTED this payment. The workflow can still read
                  "paid", so without this the screen looks like the money went
                  out when it never did (EOH Consulting, AC08 branch-code reject,
                  sat unnoticed). Loud, red, top of the screen. */}
              {d.bank_rejected && (
                <div className="mb-4 rounded-md border-2 border-[#DC2626] bg-[#FEE2E2] p-3">
                  <p className="flex items-center gap-2 text-sm font-bold text-[#991B1B]">
                    <AlertTriangle className="w-4 h-4 shrink-0" />
                    REJECTED BY THE BANK — no money moved
                  </p>
                  {d.bank_reject_reason && (
                    <p className="mt-1 text-xs text-[#991B1B] whitespace-pre-line">{d.bank_reject_reason}</p>
                  )}
                  <p className="mt-1 text-xs text-[#7F1D1D]">
                    Fix the payee details and re-load it to FNB. Check the bank statement it was not already paid before re-sending.
                  </p>
                </div>
              )}
              {/* Why is this still open? Reject is already shown loud above; here we
                  raise the OTHER reasons — most importantly "looks already paid
                  outside Omni", so a payment is never authorised (and paid) twice
                  (CFO 2026-09-05, Alex Forbes P613,885.92). */}
              {d.why_open && d.why_open_tone !== 'reject' && (
                d.why_open_tone === 'check' ? (
                  <div className="mb-4 rounded-md border-2 border-[#D97706] bg-[#FEF3C7] p-3">
                    <p className="flex items-center gap-2 text-sm font-bold text-[#92400E]">
                      <AlertTriangle className="w-4 h-4 shrink-0" /> {d.why_open}
                    </p>
                  </div>
                ) : (
                  <div className="mb-4"><WhyOpen text={d.why_open} tone={d.why_open_tone} /></div>
                )
              )}
              {/* FNB tells Omni NOTHING when a person declines an authorisation
                  inside the FNB app, so this line kept reading "waiting for your
                  authorisation" long after the bank had said no, and Finance had
                  no way to correct their own records (Leano Makwapa / Koketso
                  Kgetse, 2026-09-11). This records the bank's answer. It moves no
                  money and closes nothing — it re-opens the payment to be fixed
                  and loaded again. */}
              {d.can_mark_fnb_rejected && (
                <div className="mb-4 -mt-2">
                  <button
                    type="button"
                    disabled={markingRejected}
                    onClick={async () => {
                      const why = window.prompt(
                        'Rejected in the FNB app. Why? (the next person fixes it from this)')
                      if (!why?.trim()) return
                      setMarkingRejected(true)
                      try {
                        await markPaymentRejectedOnFnb(d.id, why.trim())
                        const fresh = await getPaymentRequest(d.id)
                        setD(fresh)
                        onChanged()
                      } catch (e) {
                        window.alert(e instanceof Error ? e.message : 'Could not record it.')
                      } finally {
                        setMarkingRejected(false)
                      }
                    }}
                    className="text-xs font-semibold underline text-[#991B1B] disabled:opacity-50"
                  >
                    {markingRejected ? 'Recording…' : 'It was rejected in the FNB app'}
                  </button>
                  <p className="mt-1 text-[11px] text-[#6B7280]">
                    Use this when someone declined the authorisation in FNB. Nothing is
                    paid or closed — the payment re-opens so the details can be fixed
                    and loaded again.
                  </p>
                </div>
              )}
              {d.summary && (
                <p className="text-sm bg-[#F9FAFB] rounded p-3 mb-4">{d.summary}</p>
              )}
              {/* Exact authorisation table (rendered server-side, house style) */}
              <div dangerouslySetInnerHTML={{ __html: d.formatted_html }} />

              {/* The POP heading, inherited from the request SUBJECT as it stood
                  at submission (Kelvin Kimani spec 2026-09-08). Shown here so the
                  approver reads the title the proof of payment will carry BEFORE
                  it goes out, instead of finding a mismatch afterwards. */}
              {d.pop_subject && (
                <div className="mt-5 rounded border border-[#E5E7EB] bg-[#F9FAFB] p-3">
                  <h3 className="text-xs uppercase tracking-wider text-[#6B7280] mb-1">
                    Proof of payment heading
                  </h3>
                  <p className="text-sm font-semibold text-[#0D1B2A]">{d.pop_subject}</p>
                  <p className="mt-1 text-[11px] text-[#6B7280]">
                    Taken from the request subject at submission — nobody re-keys it,
                    so the POP and this request can never disagree.
                  </p>
                </div>
              )}

              {/* Amend or cancel before finance sign-off (Kelvin Kimani spec
                  2026-09-08), plus the change history. Rendered whenever there
                  is something to say: the window is open, it is locked and the
                  reason is worth reading, the request was cancelled, or there
                  are changes on the record. */}
              {(d.can_amend || d.cancelled_reason || (d.changes || []).length > 0) && (
                <AmendPanel d={d}
                            onChanged={() => {
                              getPaymentRequest(d.id).then(setD).catch(() => { /* keep what we have */ })
                              onChanged()
                            }} />
              )}

              {/* B8 — the approver must be able to see that this is a REFUND and
                  which payment it reverses, on the screen they sign on. A refund
                  that reads as an ordinary supplier payment is the whole reason
                  this payment type exists. B7 adds the copy's provenance. */}
              {(d.is_refund || d.duplicated_from_ref) && (
                <div className="mt-5 rounded border border-[#E5E7EB] bg-[#F9FAFB] p-3">
                  <h3 className="text-xs uppercase tracking-wider text-[#6B7280] mb-2">
                    {d.is_refund ? 'Refund' : 'Copied request'}
                  </h3>
                  <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
                    {d.is_refund && (
                      <>
                        <dt className="text-[#6B7280]">Refund type</dt>
                        <dd className="font-medium">{d.category_label || (d.category ? CATEGORY_LABELS[d.category] : '') || '—'}</dd>
                        <dt className="text-[#6B7280]">Reverses payment</dt>
                        <dd className="font-mono font-medium">{d.original_payment_ref || '—'}</dd>
                      </>
                    )}
                    {d.duplicated_from_ref && (
                      <>
                        <dt className="text-[#6B7280]">Duplicated from</dt>
                        <dd className="font-mono font-medium">{d.duplicated_from_ref}</dd>
                      </>
                    )}
                  </dl>
                </div>
              )}

              {/* Beneficiary account, on the same screen the approver signs on
                  (Kago 2026-08-29) — a payment is never authorised without seeing
                  the account it goes to. Full number shown here only; masked in
                  lists and exports. This view is written to the access log. */}
              {(d.account_number || d.account_name || d.bank_name) && (
                <div className="mt-5 rounded border border-[#E5E7EB] bg-[#F9FAFB] p-3">
                  <h3 className="text-xs uppercase tracking-wider text-[#6B7280] mb-2">
                    Beneficiary bank account
                  </h3>
                  <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
                    <dt className="text-[#6B7280]">Account holder</dt><dd className="font-medium">{d.account_name || '—'}</dd>
                    <dt className="text-[#6B7280]">Bank</dt><dd className="font-medium">{d.bank_name || '—'}</dd>
                    <dt className="text-[#6B7280]">Branch code</dt><dd className="font-medium">{d.branch_code || '—'}</dd>
                    <dt className="text-[#6B7280]">Account type</dt><dd className="font-medium">{d.account_type || '—'}</dd>
                    <dt className="text-[#6B7280]">Account number</dt><dd className="font-mono font-medium">{d.account_number || '—'}</dd>
                  </dl>
                  {d.bank?.first_payment && (
                    <p className="mt-2 text-xs font-medium text-[#B45309] bg-amber-50 border border-amber-200 rounded px-2 py-1">
                      First payment to this payee — no previous account on record.
                    </p>
                  )}
                  {d.bank?.name_mismatch && (
                    <p className="mt-2 text-xs font-medium text-[#B45309] bg-amber-50 border border-amber-200 rounded px-2 py-1">
                      Account-holder name does not match the payee name on the request. Check before paying.
                    </p>
                  )}
                  {d.bank?.change && (
                    <div className="mt-2 text-xs text-red-800 bg-red-50 border border-red-200 rounded px-2 py-1.5 whitespace-pre-line">
                      <span className="font-semibold">Changed account (PAY-BANK-01): </span>
                      previously ending {d.bank.change.known_account_tail}, now {d.bank.change.new_account_tail} — from {d.bank.change.known_source}.
                      {d.bank.change_reason && (
                        <span className="block mt-1">Reason given: {d.bank.change_reason}</span>
                      )}
                    </div>
                  )}
                </div>
              )}
              {/* The bank-loading file. The endpoint has existed since 2026-09-11
                  with no caller at all, so Finance still built this CSV by hand
                  (CFO 2026-09-15). Shown ONLY while the request is with the CFO:
                  before that there is nothing to load, and after it is paid there
                  is nothing left to load — offering the bank file on a paid
                  request invites a second load, and this company has a P399k
                  paid-twice history (Fable, 2026-09-15). */}
              {d.status === 'pending_cfo' && (
                <div className="mt-5">
                  <h3 className="text-xs uppercase tracking-wider text-[#6B7280] mb-2 flex items-center gap-1">
                    <Landmark className="w-3.5 h-3.5" /> Bank loading file
                  </h3>
                  <button onClick={async () => {
                            if (fnbBusy) return
                            setFnbBusy(true); setErr(null)
                            try { await downloadPaymentRequestFnbFile(d.id, d.ref) }
                            catch (e) { setErr(e instanceof Error ? e.message : 'Could not build the bank file') }
                            finally { setFnbBusy(false) }
                          }}
                          disabled={fnbBusy}
                          className="flex items-center gap-2 text-sm text-[#B45309] hover:text-[#92400E] disabled:opacity-50">
                    <Download className="w-4 h-4" />
                    {fnbBusy ? 'Building…' : 'FNB bulk-payment file (.csv)'}
                  </button>
                  <p className="mt-1 text-xs text-[#6B7280]">
                    Every field is written as text, so a long account number cannot be shortened by Excel.
                  </p>
                </div>
              )}

              {d.attachments && d.attachments.length > 0 && (
                <div className="mt-5">
                  <h3 className="text-xs uppercase tracking-wider text-[#6B7280] mb-2 flex items-center gap-1">
                    <Paperclip className="w-3.5 h-3.5" /> Supporting documents
                  </h3>
                  <ul className="space-y-1">
                    {d.attachments.map(a => (
                      <li key={a.id}>
                        <button onClick={() => downloadPaymentRequestAttachment(a.id, a.name)}
                                className="flex items-center gap-2 text-sm text-[#B45309] hover:text-[#92400E]">
                          <Download className="w-4 h-4" /> {a.name}
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {d.status === 'exception' && (
                <div className="mb-4 rounded-md border-2 border-[#F4A623] bg-[#FFFBEB] p-3 space-y-1">
                  <p className="flex items-center gap-2 text-sm font-bold text-[#92400E]">
                    <AlertTriangle className="w-4 h-4 shrink-0" /> With the exception committee
                  </p>
                  <p className="text-xs text-[#78350F] whitespace-pre-line">{d.exception_reason}</p>
                  <p className="text-xs text-[#374151]">
                    Signed so far: {(d.exception_signoffs || []).length
                      ? (d.exception_signoffs || []).map(sg => `${sg.signer} (${sg.decision})`).join(', ')
                      : 'nobody yet'}. Three approvals release it; any reject ends it.
                    Committee members decide it here on Payment Requests: click the orange <strong>Exceptions</strong> button
                    at the top right of this screen (not Administration → Anomalies, which is a different list).
                  </p>
                </div>
              )}

              {/* PAY-BANK-05 — correct the branch code the bank rejected.
                  ONE field. Never the account number: that is what identifies
                  the payee. The server refuses a code belonging to a different
                  bank, because the first two digits of a Botswana branch code
                  choose the bank. */}
              {d.can_correct_branch_code && (
                <div className="mb-4 rounded-md border border-[#0D1B2A]/20 bg-[#F9FAFB] p-3 space-y-2">
                  <p className="text-sm font-semibold text-[#0D1B2A]">Correct the branch code</p>
                  <p className="text-xs text-[#6B7280]">
                    The bank rejects a payment whose branch code is missing or the wrong shape.
                    Six digits, and the leading zero counts — <strong>064967</strong> is not 64967.
                    Check it against the payee&apos;s own bank document. This changes the branch
                    code only; the account number is not touched.
                  </p>
                  <div className="flex items-center gap-2">
                    <input
                      value={branchFix}
                      onChange={e => { setBranchFix(e.target.value); setBranchMsg(null) }}
                      placeholder="064967"
                      inputMode="numeric"
                      maxLength={20}
                      className="w-40 rounded border border-[#D1D5DB] px-2 py-1 text-sm font-mono"
                    />
                    <button
                      type="button"
                      disabled={branchBusy || !branchFix.trim() || branchFix.trim() === (d.branch_code || '')}
                      onClick={async () => {
                        setBranchBusy(true); setBranchMsg(null)
                        try {
                          const r = await correctPaymentRequestBranchCode(id, branchFix.trim())
                          setBranchMsg({ ok: true,
                            text: `Changed from ${r.before || '(blank)'} to ${r.after}. ${r.bank_for_code || ''}` })
                          const fresh = await getPaymentRequest(id)
                          setD(fresh); setBranchFix(fresh.branch_code || '')
                          onChanged()
                        } catch (e) {
                          setBranchMsg({ ok: false,
                            text: e instanceof Error ? e.message : 'Could not correct it.' })
                        } finally { setBranchBusy(false) }
                      }}
                      className="rounded bg-[#0D1B2A] px-3 py-1 text-sm text-white disabled:opacity-40"
                    >
                      {branchBusy ? 'Saving…' : 'Correct it'}
                    </button>
                    <span className="text-xs text-[#6B7280]">
                      now: <span className="font-mono">{d.branch_code || '(blank)'}</span>
                    </span>
                  </div>
                  {branchMsg && (
                    <p className={`text-xs ${branchMsg.ok ? 'text-[#166534]' : 'text-[#991B1B]'}`}>
                      {branchMsg.text}
                    </p>
                  )}
                </div>
              )}

              {/* Stage 1 — finance sign-off (Pako / Kago / Legakwa). Only shown to
                  a finance approver on a request still awaiting sign-off. */}
              {d.can_approve && (
                <div className="mt-6 border-t pt-5 space-y-3">
                  <p className="text-xs text-[#6B7280]">
                    Finance sign-off. Approve to send this to the CFO for payment, or reject it back to the sender.
                  </p>
                  <div className="flex flex-wrap items-center gap-3">
                    <button onClick={() => decide('approve')} disabled={busy}
                            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold text-white bg-emerald-600 hover:bg-emerald-700 disabled:opacity-60">
                      <CheckCircle2 className="w-4 h-4" /> Approve — send to CFO
                    </button>
                    <button onClick={() => setShowReject(v => !v)} disabled={busy}
                            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold border border-[#E5E7EB] text-[#374151] hover:bg-[#F9FAFB]">
                      <X className="w-4 h-4" /> Reject
                    </button>
                  </div>
                  {/* PAY-DUP-01 (CFO 2026-08-03) — the money on this request is
                      already raised or paid elsewhere. Rejecting it is the normal
                      outcome; the override is for the genuine re-payment only. */}
                  {dupBlock && (
                    <div className="rounded border-2 border-red-600 bg-red-50 p-3 space-y-2">
                      <p className="text-sm font-semibold text-red-800">
                        Already raised or paid — not sent to the CFO
                      </p>
                      {dupBlock.rows.length > 0 ? (
                        <ul className="text-xs text-red-800 space-y-1 list-disc pl-4">
                          {dupBlock.rows.map((r, i) => (
                            <li key={i}>
                              <span className="font-semibold">Line {r.line}</span> ({r.label}) — {r.detail}.
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <p className="text-xs text-red-800 whitespace-pre-line">{dupBlock.detail}</p>
                      )}
                      <p className="text-xs text-red-800">
                        Reject this request, quoting the reference above. If the bank
                        rejected the earlier payment, it is re-sent from the FNB rejects
                        screen — a payment is never approved twice here.
                      </p>
                    </div>
                  )}
                  {/* PAY-BANK-ACK (Kago 2026-08-29) — the account changed since the
                      last payment. The approver must tick to confirm before this
                      can pass to the CFO; the acknowledgement is written to the
                      record. */}
                  {bankChange && (
                    <div className="rounded border-2 border-red-600 bg-red-50 p-3 space-y-2">
                      <p className="text-sm font-semibold text-red-800">
                        Beneficiary account has changed
                      </p>
                      <p className="text-xs text-red-800 whitespace-pre-line">{bankChange.detail}</p>
                      <label className="flex items-start gap-2 text-xs text-red-800 cursor-pointer select-none">
                        <input type="checkbox" checked={bankAck}
                               onChange={e => setBankAck(e.target.checked)}
                               className="mt-0.5 rounded border-red-400" />
                        I have checked and accept the new account (ending {bankChange.new_account_tail}) for this payee.
                      </label>
                      <div className="flex justify-end">
                        <Button variant="secondary" size="sm" onClick={() => decide('approve')}
                                loading={busy} disabled={busy || !bankAck}>
                          Approve — send to CFO
                        </Button>
                      </div>
                    </div>
                  )}
                  {/* PAY-POP-ACK (Finance spec 2026-09-08) — the proof of payment
                      goes to an address Omni does not hold for this claim or
                      payee. Same red-banner pattern as the changed-account gate
                      above, deliberately: it is the same kind of risk, so it
                      should look the same. The tick is written to the record. */}
                  {popOffList && popOffList.length > 0 && (
                    <div className="rounded border-2 border-red-600 bg-red-50 p-3 space-y-2">
                      <p className="text-sm font-semibold text-red-800">
                        Proof of payment goes to a contact we have no record of
                      </p>
                      <ul className="text-xs text-red-800 space-y-1 list-disc pl-4">
                        {popOffList.map(o => (
                          <li key={`${o.line}-${o.email}`}>
                            <span className="font-semibold">Line {o.line}</span> —{' '}
                            {o.name || 'no name given'} &lt;{o.email}&gt;
                          </li>
                        ))}
                      </ul>
                      <p className="text-xs text-red-800">
                        A payment confirmation reaching the wrong hands is how a payee gets
                        impersonated. Check the address against something you already had for
                        them — never an address on the invoice itself.
                      </p>
                      <label className="flex items-start gap-2 text-xs text-red-800 cursor-pointer select-none">
                        <input type="checkbox" checked={popAck}
                               onChange={e => setPopAck(e.target.checked)}
                               className="mt-0.5 rounded border-red-400" />
                        I have checked {popOffList.length === 1 ? 'this address' : 'these addresses'} and accept
                        {popOffList.length === 1 ? ' it' : ' them'} as the proof-of-payment recipient.
                      </label>
                      <div className="flex justify-end">
                        <Button variant="secondary" size="sm" onClick={() => decide('approve')}
                                loading={busy} disabled={busy || !popAck}>
                          Approve — send to CFO
                        </Button>
                      </div>
                    </div>
                  )}
                  {/* PAY-BANK-04 / PAY-BANK-DOC (Finance spec 2026-09-08) — the
                      beneficiary bank details differ from what Omni holds. Same
                      red-banner pattern as the flags above: it is the same kind
                      of risk, so it looks the same. Where nobody is named as
                      verifier, or there is no document to check against, there
                      is nothing to tick — the request goes back to the raiser. */}
                  {bankDoc && (
                    <div className="rounded border-2 border-red-600 bg-red-50 p-3 space-y-2">
                      <p className="text-sm font-semibold text-red-800">
                        {bankDoc.needsVerifier
                          ? 'Nobody is named as having verified these bank details'
                          : bankDoc.needsDocument
                            ? 'No supporting document to check the bank details against'
                            : 'Confirm the bank details against the supporting document'}
                      </p>
                      <p className="text-xs text-red-800 whitespace-pre-line">{bankDoc.detail}</p>
                      {bankDoc.needsVerifier || bankDoc.needsDocument ? (
                        <p className="text-xs text-red-800">
                          Reject this back to the raiser — quote what is missing, so it comes
                          back with it. Nothing here can be ticked away.
                        </p>
                      ) : (
                        <>
                          <label className="flex items-start gap-2 text-xs text-red-800 cursor-pointer select-none">
                            <input type="checkbox" checked={bankDocAck}
                                   onChange={e => setBankDocAck(e.target.checked)}
                                   className="mt-0.5 rounded border-red-400" />
                            I have compared the bank name, branch code and account number on
                            this request with the beneficiary details on the attached document,
                            and they match.
                          </label>
                          <div className="flex justify-end">
                            <Button variant="secondary" size="sm" onClick={() => decide('approve')}
                                    loading={busy} disabled={busy || !bankDocAck}>
                              Approve — send to CFO
                            </Button>
                          </div>
                        </>
                      )}
                    </div>
                  )}
                  {showReject && (
                    <div className="rounded-lg border border-[#E5E7EB] p-3 space-y-2">
                      <input value={rejectNote} onChange={e => setRejectNote(e.target.value)}
                             placeholder="Reason for rejecting (required)"
                             className="w-full border rounded px-3 py-1.5 text-sm bg-background" />
                      <div className="flex justify-end">
                        <Button variant="secondary" size="sm" onClick={() => decide('reject')}
                                loading={busy} disabled={busy || !rejectNote.trim()}>
                          Confirm reject
                        </Button>
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* Rejected at finance sign-off — show who + why, no further action. */}
              {d.status === 'rejected' && (
                <div className="mt-6 border-t pt-5">
                  <div className="rounded-lg bg-[#FEE2E2] text-[#991B1B] text-sm px-3 py-2">
                    Rejected at finance sign-off. {d.decision_notes}
                  </div>
                </div>
              )}

              {/* Reject after finance sign-off (CFO 2026-09-01). The CFO stage
                  previously offered only "Clear"; the CFO OR a finance approver
                  (Pako / Kago / Legakwa) can now turn a PENDING_CFO payment down
                  outright. Shown to finance approvers too, who otherwise see no
                  action once a request has passed to the CFO. */}
              {d.can_reject && d.status === 'pending_cfo' && (
                <div className="mt-6 border-t pt-5 space-y-2">
                  <button onClick={() => setShowReject(v => !v)} disabled={busy}
                          className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold border border-[#DC2626] text-[#991B1B] hover:bg-[#FEE2E2]">
                    <X className="w-4 h-4" /> Reject this payment
                  </button>
                  {showReject && (
                    <div className="rounded-lg border border-[#E5E7EB] p-3 space-y-2">
                      <p className="text-xs text-[#6B7280]">
                        Turn this payment down. It closes as <strong>rejected</strong> and is not paid. A reason is required.
                      </p>
                      <input value={rejectNote} onChange={e => setRejectNote(e.target.value)}
                             placeholder="Reason (required) — e.g. duplicate, EOH already handled"
                             className="w-full border rounded px-3 py-1.5 text-sm bg-background" />
                      <div className="flex justify-end">
                        <Button variant="secondary" size="sm" onClick={() => decide('reject')}
                                loading={busy} disabled={busy || !rejectNote.trim()}>
                          Reject payment
                        </Button>
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* Stage 2 — the CFO pays or reassigns, only once finance has signed
                  off (status pending_cfo). No jumping to the Tasks page (CFO 2026-07-16). */}
              {isCfo && d.status === 'pending_cfo' && d.task_id && (
                <div className="mt-6 border-t pt-5 space-y-3">
                  {d.first_approver && (
                    <p className="text-xs text-emerald-700">Finance sign-off by {d.first_approver}.</p>
                  )}
                  {taskDone ? (
                    <div className="inline-flex items-center gap-2 text-sm font-semibold text-emerald-700">
                      <CheckCircle2 className="w-4 h-4" /> This payment is marked {d.task_status}.
                    </div>
                  ) : (
                    <>
                      {/* Per-line authorisation — "approve 9, hold 1" (CFO 2026-08-31).
                          Only when the pack has more than one payee. The whole-pack
                          "Done — mark as paid" below still works as a shortcut. */}
                      {d.can_decide_lines && (
                        <PerLineAuthorise d={d} setD={setD}
                                          onChanged={onChanged} onClose={onClose} />
                      )}
                      <div className="flex flex-wrap items-center gap-3">
                        {/* Once any line is held/rejected the blanket "mark all
                            as paid" is hidden — it would sweep a held line into
                            PAID (Fable 5). Finish those in the panel above. */}
                        {!(d.line_progress && (d.line_progress.held > 0 || d.line_progress.rejected > 0)) && (
                          <button onClick={() => setShowComplete(true)}
                                  className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold text-white bg-emerald-600 hover:bg-emerald-700">
                            <CheckCircle2 className="w-4 h-4" /> Done — record all as authorised
                          </button>
                        )}
                        <button onClick={() => setShowReassign(v => !v)}
                                className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold border border-[#E5E7EB] text-[#374151] hover:bg-[#F9FAFB]">
                          <UserPlus className="w-4 h-4" /> Reassign to someone
                        </button>
                        <button onClick={() => setShowClear(v => !v)}
                                className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold border border-[#E5E7EB] text-[#374151] hover:bg-[#F9FAFB]">
                          <X className="w-4 h-4" /> Clear from queue
                        </button>
                      </div>
                      {showClear && (
                        <div className="rounded-lg border border-[#E5E7EB] p-3 space-y-2">
                          <p className="text-xs text-[#6B7280]">
                            Not paying this through Omni? Clear it out of the queue (e.g. already paid outside Omni, duplicate, or expired). This does <strong>not</strong> make a payment.
                          </p>
                          <input value={clearNote} onChange={e => setClearNote(e.target.value)}
                                 placeholder="Reason (required)"
                                 className="w-full border rounded px-3 py-1.5 text-sm bg-background" />
                          <div className="flex justify-end">
                            <Button variant="secondary" size="sm" onClick={clearFromQueue}
                                    loading={busy} disabled={busy || !clearNote.trim()}>
                              Clear from queue
                            </Button>
                          </div>
                        </div>
                      )}
                      {showReassign && (
                        <div className="rounded-lg border border-[#E5E7EB] p-3 space-y-2">
                          <p className="text-xs text-[#6B7280]">
                            Not yours to pay? Hand it to the right person (e.g. escalate above your limit) — they&apos;ll see it in their inbox.
                          </p>
                          <div className="grid grid-cols-2 gap-2">
                            <PersonPicker value={reTo} onChange={setReTo} placeholder="Reassign to…" />
                            <input value={reReason} onChange={e => setReReason(e.target.value)}
                                   placeholder="Reason (optional)"
                                   className="border rounded px-3 py-1.5 text-sm bg-background" />
                          </div>
                          <div className="flex justify-end">
                            <Button variant="secondary" size="sm" onClick={reassign}
                                    loading={busy} disabled={busy || !reTo}>
                              Reassign
                            </Button>
                          </div>
                        </div>
                      )}
                      <a href="/tasks" className="inline-flex items-center gap-1 text-xs text-[#9CA3AF] hover:text-[#6B7280]">
                        <ArrowUpRight className="w-3.5 h-3.5" /> Open the full task
                      </a>
                    </>
                  )}
                </div>
              )}

              {showComplete && d.task_id && (
                <TaskCompletionModal
                  taskId={d.task_id}
                  taskTitle={`Payment authorisation — ${d.ref}`}
                  approvalClass="cfo"
                  onClose={() => setShowComplete(false)}
                  onDone={() => { setShowComplete(false); onChanged(); onClose() }}
                />
              )}
            </>
          )}
        </div>
      </div>
    </ModalPortal>
  )
}


/** Per-line CFO authorisation — "approve 9, hold 1" (CFO 2026-08-31).
 *  The CFO ticks the payees he is happy with and authorises them; any he is not
 *  ready for he HOLDS (they stay here on their own) or REJECTS. The request
 *  auto-closes once every line is approved or rejected. Money is authorised at
 *  the bank — this records the decision so the Omni queue matches the bank. */
/**
 * Amend or cancel a payment BEFORE finance sign-off (Kelvin Kimani spec
 * 2026-09-08).
 *
 * The inputter's own window to fix a wrong amount or drop an invoice that
 * should not be paid, without abandoning and re-raising the whole request. It
 * closes the moment finance signs off.
 *
 * Nothing here deletes anything. A cancelled line stays on the request, struck
 * through and greyed, with who pulled it and why — that IS the record. And no
 * attribution is typed: the server stamps the name and department off the
 * logged-in user.
 */
function AmendPanel({ d, onChanged }: {
  d: PaymentRequestDetail
  onChanged: () => void
}) {
  const [open, setOpen] = useState(false)
  const [edits, setEdits] = useState<Record<string, string>>({})
  const [reqEdits, setReqEdits] = useState<Record<string, string>>({})
  const [cancelling, setCancelling] = useState<number | 'request' | null>(null)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const lines = d.line_items || []
  const key = (line: number, field: string) => `${line}::${field}`
  const cur = (line: number, field: string, fallback: string) => {
    const k = key(line, field)
    return k in edits ? edits[k] : fallback
  }
  const setEdit = (line: number, field: string, value: string) =>
    setEdits(prev => ({ ...prev, [key(line, field)]: value }))

  const pending: PaymentLineChange[] = Object.entries(edits).map(([k, value]) => {
    const [line, field] = k.split('::')
    return { line: Number(line), field: field as PaymentLineChange['field'], value }
  })
  const dirty = pending.length > 0 || Object.keys(reqEdits).length > 0

  async function save() {
    setBusy(true); setErr(null); setNotice(null)
    try {
      const r = await amendPaymentRequest(d.id, pending, reqEdits)
      setEdits({}); setReqEdits({})
      const bits = [`Total is now ${money(r.currency, Number(r.total))}.`]
      if (r.cross_check_cleared.length) {
        bits.push(`Cleared ${r.cross_check_cleared.join(' and ')} — it has to be done again.`)
      }
      if (r.payee_changed) bits.push(r.payee_changed.message)
      setNotice(bits.join(' '))
      onChanged()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not amend this request')
    } finally { setBusy(false) }
  }

  async function doCancel() {
    if (cancelling === null) return
    if (reason.trim().length < 5) { setErr('Say briefly why this is being pulled.'); return }
    setBusy(true); setErr(null); setNotice(null)
    try {
      const r = await cancelPaymentRequest(
        d.id, reason.trim(), cancelling === 'request' ? undefined : cancelling)
      setCancelling(null); setReason('')
      setNotice(r.emptied
        ? 'Every line is now cancelled, so the request itself is cancelled. Nothing was deleted.'
        : cancelling === 'request'
          ? 'The request is cancelled. Every line is kept on the record.'
          : `Line cancelled and kept on the record. Total is now ${money(r.currency, Number(r.total))}.`)
      onChanged()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not cancel')
    } finally { setBusy(false) }
  }

  const history = d.changes || []

  return (
    <div className="mt-5 rounded-xl border border-[#E5E7EB] bg-white">
      <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-3">
        <div>
          <h3 className="text-xs uppercase tracking-wider text-[#6B7280]">
            Correct or pull this payment
          </h3>
          <p className="mt-0.5 text-[11px] text-[#6B7280]">
            {d.can_amend
              ? 'Open while finance has not signed it off. Nothing is ever deleted — a pulled line is kept, marked cancelled.'
              : d.amend_lock_reason}
          </p>
        </div>
        {d.can_amend && (
          <Button variant="outline" size="sm" onClick={() => setOpen(v => !v)}>
            {open ? 'Close' : 'Amend or cancel'}
          </Button>
        )}
      </div>

      {/* The cancelled record — shown whether or not the panel is open, because
          "who pulled this and why" is the first thing anyone asks later. */}
      {d.cancelled_reason && (
        <div className="mx-4 mb-3 rounded border border-[#E5E7EB] bg-[#F9FAFB] px-3 py-2">
          <p className="text-xs font-semibold text-[#374151]">This request was cancelled</p>
          <p className="text-xs text-[#6B7280] whitespace-pre-line">{d.cancelled_reason}</p>
          <p className="mt-1 text-[11px] text-[#9CA3AF]">
            Cancelled by {d.cancelled_by || 'a removed account'}
            {d.cancelled_at ? ` · ${d.cancelled_at.slice(0, 16).replace('T', ' ')}` : ''}
          </p>
        </div>
      )}

      {open && d.can_amend && (
        <div className="border-t px-4 py-4 space-y-4">
          {err && (
            <div className="rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 whitespace-pre-line">{err}</div>
          )}
          {notice && (
            <div className="rounded border border-[#A7F3D0] bg-[#ECFDF5] px-3 py-2 text-sm text-[#065F46]">{notice}</div>
          )}

          {/* Per line */}
          <div className="space-y-3">
            {lines.map((l, i) => {
              const n = i + 1
              const off = Boolean((l as { cancelled?: boolean }).cancelled)
              const pulledBy = (l as { cancelled_by?: string }).cancelled_by
              const pulledWhy = (l as { cancelled_reason?: string }).cancelled_reason
              return (
                <div key={n}
                     className={`rounded-lg border px-3 py-2.5 ${
                       off ? 'border-[#E5E7EB] bg-[#FAFAFA]' : 'border-[#E5E7EB]'}`}>
                  <div className="flex items-start justify-between gap-2">
                    <span className={`text-xs font-semibold ${
                      off ? 'text-[#9CA3AF] line-through' : 'text-[#0D1B2A]'}`}>
                      Line {n} — {l.description || '(no description)'}
                    </span>
                    {off ? (
                      <span className={`${PILL} bg-[#F3F4F6] text-[#374151] ring-[#9CA3AF]/30`}>
                        Cancelled
                      </span>
                    ) : (
                      <button type="button" onClick={() => { setCancelling(n); setReason('') }}
                              className="text-[11px] text-[#B91C1C] hover:text-[#7F1D1D] hover:underline">
                        Cancel this line
                      </button>
                    )}
                  </div>

                  {off ? (
                    <p className="mt-1 text-[11px] text-[#6B7280]">
                      {pulledWhy}{pulledBy ? ` — pulled by ${pulledBy}` : ''}. Kept on the
                      record; {money(d.currency, Number(l.amount) || 0)} is not payable.
                    </p>
                  ) : (
                    <div className="mt-2 grid grid-cols-2 md:grid-cols-3 gap-2">
                      {([['description', 'Description', 'text'],
                         ['amount', 'Amount', 'number'],
                         ['gl_code', 'GL code', 'text'],
                         ['invoice_number', 'Invoice number', 'text'],
                         ['invoice_date', 'Invoice date', 'date'],
                         ['due_date', 'Due date', 'date']] as const).map(([f, label, type]) => (
                        <div key={f} className="flex flex-col gap-1">
                          <label className="text-[11px] text-[#6B7280]">{label}</label>
                          <input type={type} step={type === 'number' ? '0.01' : undefined}
                                 value={cur(n, f, String((l as unknown as Record<string, unknown>)[f] ?? ''))}
                                 onChange={e => setEdit(n, f, e.target.value)}
                                 className={cn('border rounded px-2 py-1 text-sm bg-background',
                                               type === 'number' && 'text-right tabular-nums',
                                               key(n, f) in edits
                                                 && 'border-[#F4A623] ring-1 ring-[#F4A623]/30')} />
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )
            })}
          </div>

          {/* Request level */}
          <div className="rounded-lg border border-[#E5E7EB] px-3 py-2.5">
            <p className="text-xs font-semibold text-[#0D1B2A] mb-2">The request itself</p>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
              <div className="flex flex-col gap-1 md:col-span-2">
                <label className="text-[11px] text-[#6B7280]">Subject (titles the POP)</label>
                <input value={reqEdits.subject ?? d.subject}
                       onChange={e => setReqEdits(p => ({ ...p, subject: e.target.value }))}
                       className="border rounded px-2 py-1 text-sm bg-background" />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-[11px] text-[#6B7280]">Payment date</label>
                <input type="date" value={reqEdits.payment_date ?? (d.payment_date || '')}
                       onChange={e => setReqEdits(p => ({ ...p, payment_date: e.target.value }))}
                       className="border rounded px-2 py-1 text-sm bg-background" />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-[11px] text-[#6B7280]">Processing method</label>
                <select value={reqEdits.processing_method ?? (d.processing_method || 'bulk')}
                        onChange={e => setReqEdits(p => ({ ...p, processing_method: e.target.value }))}
                        className="border rounded px-2 py-1 text-sm bg-background">
                  <option value="bulk">Bulk — one payment</option>
                  <option value="individual">Individual — one per invoice</option>
                </select>
              </div>
              <div className="flex flex-col gap-1 md:col-span-2">
                <label className="text-[11px] text-[#6B7280]">
                  Payee <span className="text-[#B91C1C]">— higher risk</span>
                </label>
                <input value={reqEdits.payee ?? (d.payee || '')}
                       onChange={e => setReqEdits(p => ({ ...p, payee: e.target.value }))}
                       className="border rounded px-2 py-1 text-sm bg-background" />
                <p className="text-[11px] text-[#7F1D1D]">
                  Changing who is paid re-triggers the bank cross-check — a finance
                  approver has to confirm the account again.
                </p>
              </div>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Button variant="primary" size="sm" onClick={save} loading={busy}
                    disabled={busy || !dirty}>
              Save changes
            </Button>
            <Button variant="outline" size="sm" disabled={busy || !dirty}
                    onClick={() => { setEdits({}); setReqEdits({}); setErr(null) }}>
              Discard
            </Button>
            <div className="flex-1" />
            <button type="button" onClick={() => { setCancelling('request'); setReason('') }}
                    className="text-xs text-[#B91C1C] hover:text-[#7F1D1D] hover:underline">
              Cancel the whole request
            </button>
          </div>
          <p className="text-[11px] text-[#6B7280]">
            Saving recalculates TOTAL PAYABLE and the liquidity position from the lines,
            and any cross-check already done has to be done again. Every change is logged
            with your name and the time — you never type either.
          </p>

          {/* The cancel dialog — one reason field, because a pulled payment is
              exactly what an auditor asks "why" about. */}
          {cancelling !== null && (
            <div className="rounded border-2 border-[#B91C1C] bg-[#FEF2F2] p-3 space-y-2">
              <p className="text-sm font-semibold text-[#B91C1C]">
                {cancelling === 'request'
                  ? 'Cancel the whole request?'
                  : `Cancel line ${cancelling}?`}
              </p>
              <p className="text-xs text-[#7F1D1D]">
                Nothing is deleted. {cancelling === 'request' ? 'Every line stays' : 'The line stays'} on
                the record, marked cancelled, with your name, the time and this reason.
              </p>
              <textarea value={reason} onChange={e => setReason(e.target.value)} rows={2}
                        placeholder="Why is this being pulled? e.g. the courier billed IN102982 twice."
                        className="w-full border border-[#B91C1C]/40 rounded px-2 py-1.5 text-sm bg-white" />
              <div className="flex items-center justify-end gap-2">
                <Button variant="outline" size="sm" disabled={busy}
                        onClick={() => { setCancelling(null); setReason('') }}>
                  Keep it
                </Button>
                <Button variant="secondary" size="sm" onClick={doCancel} loading={busy}
                        disabled={busy || reason.trim().length < 5}>
                  Cancel {cancelling === 'request' ? 'the request' : `line ${cancelling}`}
                </Button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* The readable history: what changed or was pulled, by whom, when, why. */}
      {history.length > 0 && (
        <div className="border-t px-4 py-3">
          <h4 className="text-xs uppercase tracking-wider text-[#6B7280] mb-2">
            Change history
          </h4>
          <ol className="space-y-2">
            {history.map((c: PaymentRequestChange) => (
              <li key={c.id} className="text-xs text-[#374151]">
                <span className="font-semibold">
                  {c.line ? `Line ${c.line}` : 'Request'}
                  {c.field ? ` · ${c.field.replace(/_/g, ' ')}` : ''}
                </span>{' '}
                {c.action === 'amend' ? (
                  <>
                    <span className="text-[#9CA3AF] line-through">{c.value_before || '—'}</span>
                    {' → '}
                    <span className="font-medium text-[#0D1B2A]">{c.value_after || '—'}</span>
                  </>
                ) : (
                  <span className={`${PILL} bg-[#F3F4F6] text-[#374151] ring-[#9CA3AF]/30`}>
                    {c.action_label}
                  </span>
                )}
                {c.reason && (
                  <span className="block text-[#6B7280]">Reason: {c.reason}</span>
                )}
                <span className="block text-[11px] text-[#9CA3AF]">
                  {c.attribution} · {c.at.slice(0, 16).replace('T', ' ')}
                </span>
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  )
}

function PerLineAuthorise({ d, setD, onChanged, onClose }: {
  d: PaymentRequestDetail
  setD: (x: PaymentRequestDetail) => void
  onChanged: () => void
  onClose: () => void
}) {
  const [sel, setSel] = useState<Set<number>>(new Set())
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const lines = d.line_items || []
  const prog = d.line_progress

  const isOpen = (st?: string) => st === '' || st === 'held' || st == null
  const toggle = (i: number) => setSel(s => {
    const n = new Set(s); if (n.has(i)) n.delete(i); else n.add(i); return n
  })
  const selectAllOpen = () =>
    setSel(new Set(lines.map((ln, i) => ({ ln, i })).filter(x => isOpen(x.ln.line_status)).map(x => x.i)))

  async function act(action: 'approve' | 'hold' | 'reject') {
    if (sel.size === 0) { setErr('Tick at least one payment first.'); return }
    if (action === 'reject' && !note.trim()) { setErr('Give a reason to reject.'); return }
    setBusy(true); setErr(null)
    try {
      const r = await decidePaymentRequestLines(d.id, [...sel], action, note.trim())
      setSel(new Set()); setNote('')
      if (r.closed) { onChanged(); onClose(); return }
      const fresh = await getPaymentRequest(d.id); setD(fresh)
    } catch (e) {
      const body = (e as { body?: { detail?: string } })?.body
      setErr(body?.detail || (e instanceof Error ? e.message : 'Could not save your decision'))
    } finally { setBusy(false) }
  }

  const fmt = (n: number | string) => {
    const v = typeof n === 'string' ? parseFloat(n) : n
    return isNaN(v) ? String(n)
      : v.toLocaleString('en', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  }
  const chip = (st?: string) => {
    if (st === 'approved') return <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#D1FAE5] text-[#065F46]">approved</span>
    if (st === 'held')     return <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#FEF3C7] text-[#92400E]">held</span>
    if (st === 'rejected') return <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#FEE2E2] text-[#991B1B]">rejected</span>
    return null
  }

  return (
    <div className="rounded-lg border-2 border-[#0D1B2A]/15 bg-[#F9FAFB] p-3 space-y-3">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm font-semibold text-[#0D1B2A]">Authorise line by line</p>
        {prog && (
          <span className="text-xs text-[#6B7280]">
            {prog.approved} of {prog.total} authorised
            {prog.held ? ` · ${prog.held} held` : ''}
            {prog.rejected ? ` · ${prog.rejected} rejected` : ''}
          </span>
        )}
      </div>
      <p className="text-xs text-[#6B7280]">
        Tick the payments you are happy with and authorise them. Hold any you are not ready for —
        they stay here on their own until you decide, so one held payment never stops you closing the rest.
        This records your decision; the money is authorised at the bank.
      </p>
      <div className="border rounded divide-y bg-white max-h-72 overflow-y-auto">
        {lines.map((ln, i) => {
          const open = isOpen(ln.line_status)
          return (
            <label key={i}
                   className={`flex items-center gap-2 px-3 py-2 text-sm ${open ? 'cursor-pointer hover:bg-[#FFF7ED]' : 'opacity-60'}`}>
              <input type="checkbox" disabled={!open} checked={sel.has(i)}
                     onChange={() => toggle(i)} className="rounded" />
              <span className="flex-1 min-w-0 truncate">{ln.description || ln.ref || `Line ${i + 1}`}</span>
              {chip(ln.line_status)}
              <span className="font-mono text-xs whitespace-nowrap">{d.currency} {fmt(ln.amount)}</span>
            </label>
          )
        })}
      </div>
      <div className="flex items-center justify-between">
        <button onClick={selectAllOpen} className="text-xs text-[#B45309] hover:text-[#92400E]">Select all open</button>
        <span className="text-xs text-[#6B7280]">{sel.size} selected</span>
      </div>
      <input value={note} onChange={e => setNote(e.target.value)}
             placeholder="Reason (required to hold or reject)"
             className="w-full border rounded px-3 py-1.5 text-sm bg-background" />
      {err && <p className="text-xs text-red-700 whitespace-pre-line">{err}</p>}
      <div className="flex flex-wrap gap-2">
        <button onClick={() => act('approve')} disabled={busy || sel.size === 0}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-semibold text-white bg-emerald-600 hover:bg-emerald-700 disabled:opacity-60">
          <CheckCircle2 className="w-4 h-4" /> Authorise selected
        </button>
        <button onClick={() => act('hold')} disabled={busy || sel.size === 0}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-semibold border border-[#E5E7EB] text-[#92400E] hover:bg-[#FFF7ED] disabled:opacity-60">
          Hold selected
        </button>
        <button onClick={() => act('reject')} disabled={busy || sel.size === 0}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-semibold border border-[#E5E7EB] text-[#991B1B] hover:bg-red-50 disabled:opacity-60">
          Reject selected
        </button>
      </div>
    </div>
  )
}


/**
 * LoadOverrideQueue — PAY-WIN-02 (CFO 2026-09-01).
 * The CFO's approval queue for staff asking to load a payment outside the
 * 08:00–09:15 morning window, and the running record of who keeps trying.
 * Only rendered for the CFO.
 */
function LoadOverrideQueue() {
  const [rows, setRows] = useState<LoadOverride[]>([])
  const [showAll, setShowAll] = useState(false)
  const [loading, setLoading] = useState(true)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setErr(null)
    try {
      const d = await listLoadOverrides(showAll)
      setRows(d.overrides)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not load the approval queue')
    } finally { setLoading(false) }
  }, [showAll])

  useEffect(() => { load() }, [load])

  async function decide(id: string, decision: 'approve' | 'decline') {
    setBusyId(id); setErr(null)
    try {
      await decideLoadOverride(id, decision)
      await load()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not save the decision')
    } finally { setBusyId(null) }
  }

  const pending = rows.filter(r => r.status === 'pending')
  // Nothing pending and not looking at history → stay out of the way.
  if (!loading && pending.length === 0 && !showAll) return null

  return (
    <Card className="border-amber-300 bg-amber-50/40">
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-base flex items-center gap-1.5 text-amber-900">
          <AlertTriangle className="h-4 w-4" />
          Load-now approval requests
          {pending.length > 0 && (
            <span className="ml-1 rounded-full bg-amber-600 text-white text-xs px-2 py-0.5">
              {pending.length}
            </span>
          )}
        </CardTitle>
        <button onClick={() => setShowAll(v => !v)}
                className="text-xs font-medium text-amber-800 hover:underline">
          {showAll ? 'Show pending only' : 'Show recent history'}
        </button>
      </CardHeader>
      <CardContent className="pt-0">
        {err && <p className="text-sm text-red-700 mb-2">{err}</p>}
        {loading ? (
          <p className="text-sm text-amber-800 py-2">Loading…</p>
        ) : rows.length === 0 ? (
          <p className="text-sm text-amber-800 py-2">No requests.</p>
        ) : (
          <div className="space-y-2">
            {rows.map(o => (
              <div key={o.id}
                   className="flex flex-col sm:flex-row sm:items-center gap-2 rounded border border-amber-200 bg-white px-3 py-2">
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-[#1D3270]">
                    {o.requested_by}
                    <span className="ml-2 text-xs font-normal text-[#6B7280]">
                      {new Date(o.requested_at).toLocaleString('en-GB', {
                        day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
                      })}
                    </span>
                  </p>
                  <p className="text-xs text-[#4B5563] break-words">{o.reason}</p>
                </div>
                {o.status === 'pending' ? (
                  <div className="flex gap-2 shrink-0">
                    <Button size="sm" disabled={busyId === o.id}
                            onClick={() => decide(o.id, 'approve')}
                            className="bg-green-600 hover:bg-green-700 text-white">
                      Approve
                    </Button>
                    <Button size="sm" variant="outline" disabled={busyId === o.id}
                            onClick={() => decide(o.id, 'decline')}
                            className="text-red-700 border-red-300 hover:bg-red-50">
                      Decline
                    </Button>
                  </div>
                ) : (
                  <span className={cn(
                    'shrink-0 text-xs font-semibold px-2 py-1 rounded',
                    o.status === 'approved' ? 'bg-green-100 text-green-800'
                                            : 'bg-red-100 text-red-800',
                  )}>
                    {o.status === 'approved' ? 'Approved' : 'Declined'}
                  </span>
                )}
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
