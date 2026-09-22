// Alpha Direct Financial Management System - API Client

import { isQaReadOnly } from '@/lib/qaView'
import { localYmd } from '@/lib/utils'
import type { BankBalances } from '@/lib/bankBalances'

// Empty string → use same-origin (Caddy proxies /api/* and /api-token-auth/* upstream).
// For local dev outside Docker, set NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000 in .env.local.
const BASE_URL = process.env.NEXT_PUBLIC_API_BASE ?? ''
export const API_BASE = `${BASE_URL}/api/v1`

// Token is obtained via login - never hardcode tokens in source

// ─── TypeScript Interfaces ────────────────────────────────────────────────────

export interface PaginatedResponse<T> {
  count: number
  next: string | null
  previous: string | null
  results: T[]
}

export interface Account {
  id: string
  code: string
  name: string
  account_type: string
  sub_type: string
  is_bank_account: boolean
  parent: string | null
  parent_code: string | null
  parent_name: string | null
  currency: string
  is_active: boolean
  balance: string
  // CFO directive 2026-05-24: MA mapping fields, editable on /accounts.
  statement_class?: string       // BS | PNL | ''
  fs_line_item?: string          // canonical MA workbook label
  normal_balance_dc?: string     // D | C
  is_archived?: boolean
  owner_company?: string         // company id
  owner_company_code?: string    // company code (for fs-line-options ?company=)
}

// /api/v1/accounts/fs-line-options/ — canonical labels grouped by section.
export interface FsLineOptionGroup {
  group:   string
  side:    'asset' | 'liability' | 'equity' | 'pl'
  options: string[]
}
export async function getFsLineOptions(companyCode?: string): Promise<{ groups: FsLineOptionGroup[] }> {
  const qs = companyCode ? `?company=${encodeURIComponent(companyCode)}` : ''
  return apiFetch<{ groups: FsLineOptionGroup[] }>(`/accounts/fs-line-options/${qs}`)
}
export interface ClassifySuggestion {
  suggestion: string | null
  rule:       string | null
  source:     string | null
}
export async function getClassifySuggestion(id: string): Promise<ClassifySuggestion> {
  return apiFetch<ClassifySuggestion>(`/accounts/${id}/classify-suggest/`)
}
export interface NextUnmapped {
  next_id:   string | null
  next_code: string | null
  next_name?: string
  remaining: number
}
export async function getNextUnmapped(id: string): Promise<NextUnmapped> {
  return apiFetch<NextUnmapped>(`/accounts/${id}/next-unmapped/`)
}
// /api/v1/claims/insight/ — read-only "facts + AI narrative" for a Graphite claim,
// used by the claims description assist box. Every figure is Omni's arithmetic;
// the AI only writes the plain-English read of it.
export interface ClaimInsight {
  found: boolean
  claim_number: string
  customer_name: string
  is_company: boolean
  policy_number: string
  product_name: string
  claim_type: string
  status: string
  claim_handler: string
  date_of_loss: string | null
  damage_cause: string
  total_reserve: string
  total_payment: string
  balance: string
  currency: string
  facts_text: string
  flags: { level: 'danger' | 'warning' | 'info'; code: string; label: string }[]
  ai_summary: string
  ai_suggestion: 'PAY' | 'HOLD' | ''
  ai_reason: string
  ai_summary_at: string | null
  stale: boolean
  last_synced: string | null
}
export async function getClaimInsight(
  claimNumber: string,
  opts?: { amount?: number | string; entity?: string; payee?: string },
): Promise<ClaimInsight> {
  const amount = opts?.amount
  const entity = opts?.entity
  const payee = opts?.payee
  return apiFetch<ClaimInsight>('/claims/insight/?claim=' + encodeURIComponent(claimNumber)
    + (amount ? '&amount=' + encodeURIComponent(String(amount)) : '')
    + (entity ? '&entity=' + encodeURIComponent(entity) : '')
    + (payee ? '&payee=' + encodeURIComponent(payee) : ''))
}

// /api/v1/bug-reports/ — "Report a System Bug" (CFO directive 2026-06-10).
// Multipart: description (>=50 words) + screenshots[] (>=3 images) + page_url.
// On success the report is stored and emailed to excoboard@.
export interface BugReportResult {
  id: string
  emailed: boolean
  message: string
}
export async function submitBugReport(
  description: string, screenshots: File[], pageUrl: string,
): Promise<BugReportResult> {
  const form = new FormData()
  form.append('description', description)
  form.append('page_url', pageUrl)
  for (const f of screenshots) form.append('screenshots', f)
  return apiFetch<BugReportResult>('/bug-reports/', { method: 'POST', body: form })
}

// Bug-report status board + feedback (CFO directive 2026-06-10).
export interface BugReport {
  id: string
  status: string
  status_label: string
  description: string
  word_count: number
  screenshot_count: number
  page_url: string
  reporter_email: string
  resolution_note: string
  resolved_at: string | null
  triaged_by: string | null
  triage_requested: boolean
  triage_requested_at: string | null
  triage_pr_url: string
  // Manus QC channel (CFO 2026-08-29)
  qc_requested: boolean
  qc_requested_at: string | null
  qc_requested_by: string | null
  qc_picked_up_at: string | null
  qc_result_note: string
  qc_result_pr_url: string
  qc_result_at: string | null
  created_at: string
  updated_at: string
}
export interface BugReportList {
  is_triager: boolean
  is_cfo?: boolean
  statuses: { value: string; label: string }[]
  results: BugReport[]
}
export async function getBugReports(statusFilter?: string): Promise<BugReportList> {
  const qs = statusFilter ? `?status=${encodeURIComponent(statusFilter)}` : ''
  return apiFetch<BugReportList>(`/bug-reports/${qs}`)
}

export interface BugTriageRunResult {
  ok: boolean
  processed: number                 // NEW reports the run looked at (capped at 10/run)
  remaining?: number                // NEW reports still left after the cap
  output: string                    // triager log (plain text)
  counts: Record<string, number>    // status -> count after the run
  moved: Record<string, number>     // status -> delta this run
  detail?: string
}
// CFO-only: fire the safe triage runner now instead of waiting for the hourly job.
export async function runBugTriage(): Promise<BugTriageRunResult> {
  return apiFetch<BugTriageRunResult>('/bug-reports/run-triage/', { method: 'POST' })
}
export async function updateBugReport(
  id: string,
  payload: { status?: string; resolution_note?: string; triage_requested?: boolean; triage_pr_url?: string; qc_requested?: boolean },
): Promise<BugReport & { feedback_emailed?: boolean }> {
  return apiFetch(`/bug-reports/${id}/`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

// /api/v1/employees/birthdays-{today,upcoming}/  — CFO directive 2026-06-09.
export interface BirthdayEmployee {
  employee_id: string
  full_name:   string
  department:  string
  job_title:   string
  email:       string
  company:     string
  initials:    string
  dob_md:      string   // 'MM-DD' — NO year
  days_until:  number
}
export interface BirthdaysTodayResponse {
  date:      string
  count:     number
  employees: BirthdayEmployee[]
}
export interface BirthdaysUpcomingResponse {
  window_days: number
  today:       string
  count:       number
  employees:   BirthdayEmployee[]
}
export async function getBirthdaysToday(): Promise<BirthdaysTodayResponse> {
  return apiFetch<BirthdaysTodayResponse>('/employees/birthdays-today/')
}
export async function getBirthdaysUpcoming(inDays = 7): Promise<BirthdaysUpcomingResponse> {
  return apiFetch<BirthdaysUpcomingResponse>(`/employees/birthdays-upcoming/?in_days=${inDays}`)
}

// /api/v1/manual/whats-new/ — the /help "What's New" feed, refreshed nightly.
export interface WhatsNewEntry {
  date:    string
  title:   string
  summary: string
  area:    string
}
export interface WhatsNewResponse {
  last_updated: string | null
  count:        number
  entries:      WhatsNewEntry[]
}
export async function getWhatsNew(): Promise<WhatsNewResponse> {
  return apiFetch<WhatsNewResponse>('/manual/whats-new/')
}

export async function patchAccount(id: string, payload: Partial<Account>): Promise<Account> {
  return apiFetch<Account>(`/accounts/${id}/`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(payload),
  })
}

export interface JournalEntry {
  id: string
  entry_number: string
  entry_date: string
  description: string
  journal_type: string
  status: string
  currency: string
  exchange_rate: string
  posted_date: string | null
  source_type: string
  line_count: number
  created_at: string
  // Approval workflow
  created_by_username?: string | null
  submitted_by_username?: string | null
  submitted_at?: string | null
  approved_by_username?: string | null
  approved_at?: string | null
  rejection_reason?: string | null
  // IAS 24 related-party classification — null = not yet answered
  is_related_party?: boolean | null
}

export interface JournalEntryLine {
  id: string
  account: string
  account_name: string
  account_type: string
  debit_amount: string
  credit_amount: string
  debit_bwp: string
  credit_bwp: string
  description: string
}

export interface JournalEntryDetail extends JournalEntry {
  lines: JournalEntryLine[]
  reversal_of_number: string | null
  reversed_by_number: string | null
  created_by_id?: string | null
}

export interface Contact {
  id: string
  contact_type: string
  name: string
  registration_number: string | null
  tax_id: string | null
  email: string | null
  phone: string | null
  address: string | null
  currency_code: string
  currency_name?: string
  payment_terms_days: number
  is_resident: boolean
  wht_exempt: boolean
  is_active: boolean
  graphite_id?: string | null
  company?: string | null
  company_code?: string | null
  created_at: string
  updated_at?: string
}

export interface Invoice {
  id: string
  invoice_number: string
  invoice_type: string
  contact: string
  contact_name: string
  company: string | null
  company_code: string | null
  company_name: string | null
  issue_date: string
  due_date: string
  /** ISO 4217 code (legacy backend field). */
  currency: string
  /**
   * Alias retained for UI components that read the FK column name directly
   * (Contact uses `currency_code`). Backend returns both keys in detail
   * serializers — keep both optional so the type matches list + detail.
   */
  currency_code?: string
  exchange_rate: string
  subtotal: string
  tax_total: string
  total_amount: string
  amount_paid: string
  balance_due: string
  status: string
  je_number: string | null
  created_at: string
}

export interface InvoiceLine {
  id: string
  account: string
  account_name: string
  description: string
  quantity: string
  unit_price: string
  tax_code: string
  tax_rate: string
  tax_amount: string
  line_total: string
}

export interface InvoiceDetail extends Invoice {
  lines: InvoiceLine[]
  subtotal: string
  tax_total: string
  exchange_rate: string
  description: string
  approved_at?: string | null
  approved_by?: string | null
  approved_by_name?: string | null
}

export interface Payment {
  id: string
  payment_number: string
  payment_type: string
  contact: string
  contact_name: string
  company: string | null
  company_code: string | null
  company_name: string | null
  bank_account: string
  bank_account_code: string
  payment_date: string
  currency: string
  amount: string
  amount_bwp: string
  payment_method: string
  reference: string
  status: string
  approval_status?: string         // not_required | pending | approved | rejected
  approval_tier?: number | null
  je_number: string | null
  created_at: string
}

export interface PaymentAllocation {
  id: string
  payment: string
  invoice: string
  invoice_number: string
  invoice_total: string
  invoice_status: string
  amount_allocated: string
  created_at: string
}

export interface PaymentDetail extends Payment {
  allocations: PaymentAllocation[]
  wht_record: any
  description?: string | null
  // PAY-003 tier maker-checker
  approval_comment?: string
  submitted_for_approval_at?: string | null
  submitted_by_name?: string | null
  approval_decided_at?: string | null
  approval_decided_by_name?: string | null
  bank_submitted_at?: string | null   // stamped when loaded into the FNB queue
}

export interface BankAccount {
  id: string
  bank_name: string
  account_name: string
  account_number: string
  branch_code: string
  currency: string
  gl_account: string
  gl_account_code: string
  gl_account_name: string
  is_active: boolean
  hide_in_banking_ui?: boolean
  current_balance: string
  last_reconciled_date: string | null
  // CFO directive 2026-05-25 (BANK-001 + BANK-006)
  book_balance?: string                  // GL truth, JEL aggregate
  statement_balance?: string | null      // latest statement closing, or null
  statement_as_of?: string | null
  // BANK-007: book_balance is as-of-TODAY, statement_balance is as-of
  // statement_as_of. Never subtract one from the other in the UI — use
  // statement_difference, which the backend cuts at the statement date.
  statement_gl_balance?: string | null   // GL at statement_as_of (like-for-like)
  statement_difference?: string | null   // bank closing - GL at that same date
  statement_reconciled?: boolean | null
  statement_unmatched?: number | null
  statement_id?: string | null
  bank_name_display?: string             // falls back to GL name first token
  account_number_display?: string        // falls back to digits in GL name
}

export interface BankStatementLine {
  id: string
  line_number: number
  transaction_date: string
  description: string
  reference: string
  amount: string
  running_balance: string
  match_status: string
  matched_payment: string | null
  matched_payment_number: string | null
  matched_journal_entry: string | null
  matched_je_number: string | null
  match_confidence: number
  notes: string
}

export interface MatchExplanation {
  amount_equal: boolean
  statement_amount: string
  candidate_amount: string
  date_gap_days: number
  reference_match: boolean
  confidence: number
}

export interface MatchDryRunResponse {
  dry_run: true
  explanation: MatchExplanation
}

export interface BankStatement {
  id: string
  statement_number: string
  bank_account: string
  bank_account_name: string
  bank_name: string
  statement_date: string
  opening_balance: string
  closing_balance: string
  file_name: string
  import_date: string
  status: string
  line_count: number
}

export interface BankStatementDetail extends BankStatement {
  lines: BankStatementLine[]
}

export interface TrialBalanceAccount {
  code: string
  name: string
  account_type: string
  sub_type: string
  opening_balance: string
  period_debits: string
  period_credits: string
  closing_balance: string
}

export interface TrialBalanceReport {
  as_of: string
  period_start: string
  accounts: TrialBalanceAccount[]
  totals: {
    total_debits: string
    total_credits: string
    balanced: boolean
  }
}

export interface PLAccount {
  code: string
  name: string
  sub_type: string
  balance: string
}

export interface ProfitLossReport {
  from_date: string
  to_date: string
  revenue: { accounts: PLAccount[]; total: string }
  cost_of_insurance: { accounts: PLAccount[]; total: string }
  gross_result: string
  operating_expenses: { accounts: PLAccount[]; total: string }
  net_profit: string
  is_profit: boolean
}

export interface BalanceSheetReport {
  as_of: string
  fiscal_year_start: string
  assets: any
  liabilities: any
  equity: any
  totals: {
    total_assets: string
    liabilities_and_equity: string
    balanced: boolean
  }
}

/** CFO directive 2026-05-20: BS rendered against the MA workbook
 *  layout (reporting/ma_bs_spec.py). Each section is a flat list of
 *  fs_line_item labels with their aggregated amount.
 */
export interface MaBalanceSheetReport {
  as_of: string
  sections: {
    id: string
    label: string
    side: 'asset' | 'liability' | 'equity'
    lines: { label: string; amount: string }[]
    subtotal_label: string
    subtotal: string
  }[]
  totals: {
    total_assets: string
    total_liabilities: string
    total_equity: string
    liabilities_and_equity: string
    balanced: boolean
  }
}

export interface AgingInvoice {
  invoice_number: string
  due_date: string
  total_amount: string
  amount_paid: string
  balance_due: string
  age_bucket: string
  days_past_due: number
}

export interface AgingCustomer {
  contact_name: string
  contact_type?: string
  invoices: AgingInvoice[]
  totals?: Record<string, string>
}

export interface AgingReconciliation {
  aging_total: string
  bs_ap_balance: string
  variance: string
  reconciled: boolean
  ap_accounts: string[]
}

export interface AgingReport {
  as_of: string
  currency_code?: string
  buckets?: string[]
  customers: AgingCustomer[]
  totals: { total_outstanding: string } & Record<string, string>
  reconciliation?: AgingReconciliation   // PAY-002 — AP only
}

export interface CashAccount {
  account_code: string
  account_name: string
  currency: string
  balance_native: string
  balance_bwp: string
  exchange_rate: string
  has_activity: boolean
}

export interface CashPositionReport {
  as_of: string
  accounts: CashAccount[]
  total_bwp: string
}

export interface GLLine {
  date: string
  entry_number: string
  description: string
  journal_type: string
  debit: string
  credit: string
  running_balance: string
  is_related_party_line?: boolean
  is_related_party_entry?: boolean
  contact_name?: string | null
}

export interface GeneralLedgerReport {
  account: { code: string; name: string; account_type: string; sub_type: string }
  from_date: string
  to_date: string
  opening_balance: string
  lines: GLLine[]
  totals: { total_debits: string; total_credits: string; closing_balance: string }
}

export interface Currency {
  code: string
  name: string
  symbol: string
  decimal_places: number
  is_active: boolean
}

export type EntityType = 'insurance' | 'trading' | 'consolidated'

export interface Company {
  id: string
  code: string
  name: string
  legal_name: string | null
  registration_number: string | null
  tax_id: string | null
  country: string
  base_currency: string
  base_currency_name: string
  address: string | null
  is_active: boolean
  is_default: boolean
  // CFO directive 2026-05-19: drives the dashboard layout.
  // Optional in the type because old cached payloads may not have it yet.
  entity_type?: EntityType
  created_at: string
  updated_at: string
}

export interface CreateCompanyInput {
  code: string
  name: string
  legal_name?: string
  registration_number?: string
  tax_id?: string
  country?: string
  base_currency?: string
  address?: string
  is_default?: boolean
}

export interface TaxRate {
  id: string
  tax_code: string
  name: string
  rate: string
  is_active: boolean
}

export interface FiscalPeriod {
  id: string
  period_name: string
  start_date: string
  end_date: string
  status: string                       // open | locked | closing | closed
  closed_at?: string | null
  // Period Management page (CFO directive 2026-05-28)
  company?: string | null
  company_code?: string | null
  company_name?: string | null
  fiscal_year?: string | null
  fiscal_year_label?: string | null
  locked_by_cfo?: string | null
  locked_by_cfo_name?: string | null
  locked_by_cfo_at?: string | null
  locked_by_fm?: string | null
  locked_by_fm_name?: string | null
  locked_by_fm_at?: string | null
  lock_reason?: string
  has_full_lock_signoff?: boolean
  updated_at?: string
}

export interface PeriodAuditEntry {
  id: string
  created_at: string
  user: string | null
  period_id: string
  period_name: string | null
  company_code: string | null
  action: string
  description: string
  new_values: Record<string, any>
}

export interface DashboardData {
  cashPosition: CashPositionReport
  arAging: AgingReport
  apAging: AgingReport
  recentEntries: PaginatedResponse<JournalEntry>
  profitLoss: ProfitLossReport
  maProfitLoss: MAProfitLossReport
  balanceSheet: BalanceSheetReport
  overdueAr: PaginatedResponse<Invoice>
  overdueAp: PaginatedResponse<Invoice>
  // CFO directive 2026-05-24 — canonical "Total Receivables" used by
  // tile, BS receivables line, AR summary; null if endpoint failed.
  receivablesSummary: ReceivablesSummary | null
  // BUG-010 (2026-06-08): real counts for the Action Center, replacing the
  // hardcoded "—". 0 is a legitimate value (shown as "0", never "—").
  draftJeCount: number
  unmatchedBankCount: number
  // External-audit follow-up 2026-05-19: surface which endpoints failed
  // so the dashboard can show "endpoint down" instead of zeros that
  // look like real values. UI consumes endpoint_errors to badge tiles.
  endpoint_errors: Record<keyof Omit<DashboardData, 'endpoint_errors'>, string | null>
}

export interface CreateJournalEntryInput {
  entry_date: string
  description: string
  journal_type: string
  currency_code: string
  exchange_rate?: string
  // BLOCKER-001 fix 2026-05-28: every JE must carry the topbar entity so
  // the per-company sequence emits JE-{CO}-YYYY-NNNNNN and the detail row
  // is reachable through the company-scoped list.
  company?: string
  lines: {
    account: string
    debit_amount?: string
    credit_amount?: string
    description?: string
  }[]
}

export interface CreateInvoiceInput {
  invoice_type: string
  contact: string
  company?: string  // Company UUID; defaults to system default
  issue_date: string
  due_date: string
  currency_code: string
  description?: string
  // Required when invoice_type === 'vendor_bill' — bill cannot post without a PO ref.
  purchase_order?: string | null
  lines: {
    account: string
    description: string
    quantity: string
    unit_price: string
    tax_code: string
  }[]
}

export interface CreatePaymentInput {
  payment_type: string
  contact: string
  company?: string  // Company UUID; FE always sends the topbar selection
  bank_account: string
  vendor_bank_account?: string | null  // Required for outbound electronic payments
  payment_date: string
  currency_code: string  // writable field name on the serializer ('currency' is read-only)
  amount: string
  payment_method: string
  reference?: string
  description?: string
  remittance_email?: string  // Where FNB emails the POP (CFO 2026-08-22)
}

// ─── Token helpers ─────────────────────────────────────────────────────────────

// Sentinel value written by the SSO sign-in flow so that legacy code paths
// of the shape `if (!getToken()) router.replace('/login')` (sprinkled across
// 20+ pages) continue to pass for Microsoft-authenticated users. apiFetch
// never sends this string as an actual auth header — it's a "yes, I'm signed
// in via SSO" marker.
export const SSO_SENTINEL_TOKEN = '__sso__'

// Processor DPAs awaiting the signed-in user's e-signature (CFO 2026-07-29).
export interface MyDpaItem {
  reference: string
  processor: string
  purpose: string
  status: string
  status_label: string
  signed_at: string | null
  sign_url: string
}
export interface MyDpasResponse { pending: MyDpaItem[]; all: MyDpaItem[]; count_pending: number }
export async function getMyDpas(): Promise<MyDpasResponse> {
  return apiFetch<MyDpasResponse>('/my-dpas/')
}

export function getToken(): string | null {
  if (typeof window === 'undefined') return null
  return localStorage.getItem('alpha_token')
}

export function setToken(token: string): void {
  if (typeof window !== 'undefined') {
    localStorage.setItem('alpha_token', token)
  }
}

export function removeToken(): void {
  if (typeof window !== 'undefined') {
    localStorage.removeItem('alpha_token')
    localStorage.removeItem('alpha_me_cache_v1')
    localStorage.removeItem('alpha_companies_cache_v1')
  }
}

/**
 * Turn a fresh Microsoft sign-in into an Omni session — ONCE.
 *
 * This is the Graphite-style fix (CFO 2026-08-24): after Microsoft has
 * authenticated the browser, take the Microsoft access token and swap it for
 * Omni's OWN token via /auth/sso/exchange/, then store that as the real
 * session token. From then on every request uses the Omni token
 * (Authorization: Token <t>) — the same robust path email+password login uses —
 * instead of asking MSAL for a fresh Microsoft token on every call (which
 * stranded the user the moment MSAL's browser cache was lost).
 *
 * Returns true if a real Omni token was stored. On ANY failure it returns false
 * and stores nothing, so the caller can fall back to the SSO sentinel and
 * sign-in never regresses.
 */
export async function exchangeSsoForToken(): Promise<boolean> {
  if (typeof window === 'undefined') return false
  try {
    const { acquireApiToken, SSO_API_CALLS_READY } = await import('@/auth/msal')
    if (!SSO_API_CALLS_READY) return false
    const msToken = await acquireApiToken()
    if (!msToken) return false
    const resp = await fetch(`${API_BASE}/auth/sso/exchange/`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${msToken}`, Accept: 'application/json' },
    })
    // A real failure here (misconfigured endpoint, server error, bad response)
    // must be VISIBLE, not silent: without a warning the whole app quietly
    // reverts every user to the fragile per-call Microsoft path and nobody
    // notices the exchange broke (panel H6, 2026-08-24). The graceful fallback
    // stays — this only makes the failure detectable. (A missing Microsoft
    // session above returns false quietly; that is normal, not a failure.)
    if (!resp.ok) {
      console.warn(`[sso] token exchange failed (HTTP ${resp.status}); falling back to per-call Microsoft tokens`)
      return false
    }
    const data = await resp.json().catch(() => null)
    if (data && typeof data.token === 'string' && data.token) {
      setToken(data.token)
      return true
    }
    console.warn('[sso] token exchange returned no usable token; falling back to per-call Microsoft tokens')
    return false
  } catch (e) {
    console.warn('[sso] token exchange errored; falling back to per-call Microsoft tokens', e)
    return false
  }
}

// ─── Base fetch wrapper ────────────────────────────────────────────────────────

/**
 * Recover from a sign-in that is no longer valid: clear the dead key and bounce
 * to /login. Exported because EVERY authed fetch path needs it, not just
 * apiFetch — the HRIS pages have their own helper (authedHrisFetch) that used a
 * bare fetch, so an expired sign-in showed "Access restricted." on Monthly
 * Feedback and "Could not download the payslip (error 403)" on Payslips, with no
 * route back to the sign-in screen. Two staff bug reports on 4-Aug-2026, one
 * root cause.
 *
 * Safe to call on any response: it does nothing unless this is a genuine
 * not-authenticated answer AND a key is stored locally.
 */
export async function recoverFromDeadSignIn(response: Response, bearer?: string | null): Promise<void> {
  // Self-healing path for the "Chrome 3rd-party cookie / stale MSAL token"
  // failure mode: the cached MSAL access token can't be silently refreshed
  // (iframe to login.microsoftonline.com blocked), so acquireApiToken()
  // returns null, apiFetch falls back to the sentinel, the server rejects
  // with 401 "Invalid token", and the user sees BWP 0.00 everywhere because
  // every read silently failed. Detect this combination once per page load
  // and bounce to /login to force a fresh MSAL loginRedirect.
  //
  // We also clear MSAL's LOCAL cache before redirecting so /login's
  // tryRouteHome() can't see a stale account and ricochet us straight
  // back to /dashboard, where the same 401 would fire again — that
  // ricochet was the "in and out" glitch reported on 2026-05-18.
  if ((response.status === 401 || response.status === 403) && typeof window !== 'undefined') {
    const tokenNow = getToken()
    const isSentinelStuck = tokenNow === SSO_SENTINEL_TOKEN && !bearer
    // The same stuck state can also surface as DRF's 403 "Authentication
    // credentials were not provided." (Bharath, 2026-07-18) — recover from
    // that too, but ONLY that exact body, so a real permission denial
    // (also 403) never logs anyone out.
    let notAuthenticated = response.status === 401
    // A REAL but dead DRF token (rotated / deleted / from an old login) is NOT
    // the sentinel, so the recovery below used to skip it — the page just
    // printed "Invalid token." forever and a refresh could not help, because the
    // dead key stayed in localStorage (Ikanyeng Sechele could not log Saturday,
    // 2026-07-27). DRF emits exactly "Invalid token." ONLY when the presented key
    // matches no row, so keying on that exact body is safe: a genuine permission
    // denial carries a different message and never logs anyone out.
    // The status is 401 OR 403 depending on which authenticator sits first in
    // DRF's chain (the first one's authenticate_header decides, and the Nexus
    // staff bridge returned none until 4-Aug-2026 — so the same dead key came
    // back as 403 and this recovery never ran, locking a staff member out of
    // spend requests all morning). Key on the body, not the status: DRF emits
    // exactly "Invalid token." ONLY when the presented key matches no row.
    let deadToken = false
    if ((response.status === 401 || response.status === 403) && tokenNow && !bearer) {
      try {
        const d = await response.clone().json() as { detail?: unknown }
        deadToken = d?.detail === 'Invalid token.'
      } catch { /* not JSON — leave false */ }
    }
    if (!notAuthenticated && isSentinelStuck) {
      try {
        const d = await response.clone().json() as { detail?: unknown }
        notAuthenticated = d?.detail === 'Authentication credentials were not provided.'
      } catch { /* not JSON — leave false */ }
    }
    if ((isSentinelStuck && notAuthenticated) || deadToken) {
      const w = window as unknown as { __omniAuthRecovered?: boolean }
      if (!w.__omniAuthRecovered) {
        w.__omniAuthRecovered = true
        // Clear the sentinel so /login can't auto-route back to dashboard
        // before the loginRedirect ceremony actually completes.
        localStorage.removeItem('alpha_token')
        // Wipe MSAL's local account/token cache too. Without this, the
        // auto-route fires on /login before the user can re-authenticate.
        try {
          const { clearLocalMsalState } = await import('@/auth/msal')
          await clearLocalMsalState()
        } catch { /* best effort */ }
        // Don't await navigation — let the redirect happen, the caller's
        // promise rejects, and React unmounts the page mid-flight.
        // `signedout=1` suppresses /login's tryRouteHome auto-route.
        // A read-only quality-check session goes back to /qa, which reopens
        // from the remembered key — it has no /login credentials to offer.
        window.location.replace(
          isQaReadOnly()
            ? '/qa'
            : `/login?signedout=1&returnTo=${encodeURIComponent(window.location.pathname + window.location.search)}`
        )
      }
    }
  }

}

export async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  // ── Auto-inject the globally selected company on read requests ─────────
  // The TopBar company switcher writes to localStorage('alpha_company_id').
  // Any GET that doesn't already carry an explicit company filter inherits
  // the current selection so every page in the app follows the switcher
  // without each component having to wire it up. POST/PUT/PATCH/DELETE are
  // never auto-filtered (they target a specific record).
  const method = (options?.method || 'GET').toUpperCase()

  // ── Read-only quality-check view (/qa) ─────────────────────────────────
  // The server already refuses every write from this identity. Catch it here
  // too so a blocked button reads as "this view can't change things" instead
  // of an authentication error a quality check would file as a bug.
  if (typeof window !== 'undefined'
      && !['GET', 'HEAD', 'OPTIONS'].includes(method)
      && isQaReadOnly()) {
    throw new Error('Read-only quality-check view — nothing here can be changed. '
      + 'Sign in normally if you need to make a change.')
  }

  let resolvedPath = path
  if (method === 'GET' && typeof window !== 'undefined' && !path.startsWith('http')) {
    const selectedCompany = localStorage.getItem('alpha_company_id')
    if (selectedCompany && !path.includes('company=')) {
      resolvedPath = path + (path.includes('?') ? '&' : '?') + `company=${encodeURIComponent(selectedCompany)}`
    }
  }
  const url = resolvedPath.startsWith('http') ? resolvedPath : `${API_BASE}${resolvedPath}`

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options?.headers as Record<string, string>),
  }

  // Prefer an Azure AD-issued Bearer token (SSO mode); fall back to the
  // legacy DRF Token from localStorage (password login). Both keep working.
  // acquireApiToken() returns null immediately for an email+password session —
  // see the performance note in auth/msal.ts.
  let bearer: string | null = null
  try {
    // Dynamic import so the MSAL bundle doesn't load when SSO is off
    const { acquireApiToken, SSO_API_CALLS_READY } = await import('@/auth/msal')
    if (SSO_API_CALLS_READY) {
      bearer = await acquireApiToken()
      // First-paint token race (BUG 2026-06-11 — dashboard tiles all 401'd with
      // "Authentication credentials were not provided"): on the very first calls
      // after navigation, MSAL may not have finished handleRedirectPromise /
      // populated an account yet, so acquireApiToken() returns null and we'd
      // otherwise fire an UNAUTHENTICATED request. The dashboard fans out ~12
      // parallel calls on mount, so they all lost this race at once. Retry
      // briefly (bounded ~1.6s) to let MSAL warm up and hand us a real bearer.
      // Harmless when the user is genuinely signed out: still resolves to null
      // and falls through to the 401 -> /login recovery below.
      if (!bearer && getToken() === SSO_SENTINEL_TOKEN) {
        for (let i = 0; i < 4 && !bearer; i++) {
          await new Promise((r) => setTimeout(r, 400))
          bearer = await acquireApiToken()
        }
      }
    }
  } catch {
    bearer = null
  }
  if (bearer) {
    headers['Authorization'] = `Bearer ${bearer}`
  } else {
    const token = getToken()
    // Skip the SSO sentinel — it's not a real DRF token, just a marker.
    if (token && token !== SSO_SENTINEL_TOKEN) {
      headers['Authorization'] = `Token ${token}`
    }
  }

  // Don't set Content-Type for FormData
  if (options?.body instanceof FormData) {
    delete headers['Content-Type']
  }

  const response = await fetch(url, {
    ...options,
    headers,
  })

  // Expired / rotated sign-in: clear it and go to /login (see the helper above).
  await recoverFromDeadSignIn(response, bearer)

  if (!response.ok) {
    let errorMessage = `HTTP ${response.status}: ${response.statusText}`
    let errorBody: unknown = undefined
    try {
      errorBody = await response.json()
      const errorData = errorBody as Record<string, unknown>
      // BLOCKER-002 fix 2026-05-28: peel the DRF error envelope before
      // showing it to users — previously a {"detail":[...]} or a field-level
      // error object was JSON.stringify'd straight into the toast, which
      // looked like an internal error dump. Now we flatten to plain text.
      const flatten = (v: unknown): string => {
        if (typeof v === 'string') return v
        if (Array.isArray(v)) return v.map(flatten).filter(Boolean).join(', ')
        if (v && typeof v === 'object') {
          return Object.entries(v as Record<string, unknown>)
            .map(([k, vv]) => `${k}: ${flatten(vv)}`)
            .join('; ')
        }
        return v == null ? '' : String(v)
      }
      if (typeof errorData.detail === 'string') errorMessage = errorData.detail
      else if (Array.isArray(errorData.detail)) errorMessage = (errorData.detail as unknown[]).map(flatten).join(', ')
      else if (Array.isArray(errorData.non_field_errors)) errorMessage = (errorData.non_field_errors as string[]).join(', ')
      else if (typeof errorData.error === 'string') errorMessage = errorData.error
      else {
        const flat = flatten(errorData)
        if (flat) errorMessage = flat
      }
    } catch {}
    // Gateway failure — the backend was not there to answer (bug f33a411f,
    // Kakale Botana, 2026-07-30). A deploy recreates the backend container and
    // it takes ~60s to come back; every click in that window used to surface the
    // raw "HTTP 502: Bad Gateway", which reads like the user did something
    // wrong.
    //
    // What Caddy does with the gap (from its own `tryAgain` source, not memory):
    //   * failed at the DIAL stage — nothing was transmitted, so it replays the
    //     request whatever the method, POSTs included. That replay cannot
    //     double-post, because the first attempt never left.
    //   * broke MID-FLIGHT — it rides a GET across the gap (its idempotency
    //     default), and will NOT replay a non-GET, because the request may
    //     already have been delivered.
    // A container restart usually presents as the mid-flight case: Docker still
    // accepts the connection and then breaks it. Measured on prod during a real
    // restart — GET 200 after 35.9s, POST 502 in 0.07s.
    // So a submit really can land in the gap, and the only honest thing to do is
    // say so in plain words and tell the person their typing is still on screen.
    //
    // The wording turns on whether RE-TRYING IS SAFE, not on the status code
    // (Fable review 2026-07-30). A 502 does NOT prove the request never arrived:
    // Caddy returns 502 both when it cannot dial the backend at all AND when the
    // connection breaks after the request was already sent — which is exactly what
    // recreating the container does to an in-flight request. From the browser the
    // two are indistinguishable. So we never claim a write did not land:
    //   * a read (GET/HEAD) changed nothing, so "try again" is always true;
    //   * a write may or may not have landed — telling someone to press Submit
    //     again would post a payment, PO or journal entry twice, which is the
    //     very thing we refused to let Caddy do by not replaying POSTs.
    const gatewayDown = (
      response.status === 502 || response.status === 503 || response.status === 504
    )
    const readOnlyCall = method === 'GET' || method === 'HEAD'
    if (gatewayDown) {
      errorMessage = readOnlyCall
        ? 'Omni was updating for a moment, so this did not load. '
          + 'Nothing has changed — please try again.'
        : 'Omni was updating, so we cannot confirm whether this saved. '
          + 'Nothing you typed has been lost — please check whether it went through '
          + 'before submitting again.'
    }
    // CFO structural-audit directive 2026-05-19: stop swallowing API errors.
    // Surface every non-2xx via toast so the user knows something failed.
    // Skip:
    //   * 401 — handled above with login redirect, toast would be noise
    //   * 403 / 404 on probe endpoints that legitimately probe for absence
    //     (HRIS access, frozen-drift, salvage access, document confirm).
    //     These are called on every page load just to check capability;
    //     a 404 means "feature off for this user", not "request failed".
    const isProbePath = (
      path.includes('/hris/lock-status/')      ||
      path.includes('/admin/hris-access/')     ||
      path.includes('/salvage/me-can-access/') ||
      path.includes('/frozen-drift/')          ||
      path.includes('/cfo-upload-status/')     ||
      path.includes('/dashboard/cfo/')         ||
      // Morning Bank Balances. The panel is mounted on the shared welcome page
      // and self-hides on 403, so for every non-finance user this call is a
      // capability probe fired on every dashboard visit — exactly the case
      // this list exists for. Without it they get a "Permission denied" toast
      // on a panel they cannot even see.
      path.includes('/banking/balances/')      ||
      // The global ChatWidget fetches the full user directory on every page
      // load to build the @mention + task-assignee picker. That admin-only
      // endpoint 403s for non-admin roles (e.g. finance_manager); the widget
      // already falls back gracefully (.catch), so the only effect was a
      // spurious "Permission denied" toast on every dashboard visit. The
      // admin settings page (/settings/user-emails) renders its own error
      // card, so suppressing this global toast doesn't hide a real failure
      // there (Oprah, 2026-07-24).
      path.includes('/admin/user-emails/')
    )
    // Background polling paths that retry silently — don't toast on 502/503/504
    // (these endpoints retry automatically so the toast is pure noise).
    // NOTE 2026-07-30: the old comment here blamed "gunicorn worker cycling".
    // That was wrong, and believing it is why the bursts went unfixed. Measured
    // over three days of Caddy logs: 3,028 502s across 89 separate minutes, and
    // every one of those minutes lines up with a `git pull` + container recreate
    // on the box (30 deploys in office hours on 07-30 alone). Worker recycling
    // never caused it — deploying on top of working users did.
    const isPollingPath = (
      path.includes('/presence/online/')       ||
      path.includes('/notifications/pending/') ||
      path.includes('/chat/messages/')         ||
      path.includes('/aria/urgent-deadlines/') ||
      path.includes('/aria/popups/')
    )
    // Capability denial, not a fault (2026-07-24). The dashboard fans out
    // finance-report reads on every visit; a non-finance role (e.g. the
    // Internal Auditor) gets a deliberate 403 carrying CanViewFinancials'
    // exact message. The tiles already render empty via allSettled, so the
    // only effect of the toast was to flash "Permission denied" — spurious
    // noise that trains people to ignore real denials. Suppress the toast
    // ONLY for this known capability message; a genuine auth regression
    // carries a different error and still surfaces.
    const isFinancialCapabilityDenial = (
      response.status === 403 &&
      errorMessage === 'Financial data is restricted to finance and management staff.'
    )
    // A payment control refusal (PAY-DUP-01 and any later PAY-* control) is a
    // multi-line finding — a headline, one bullet per clashing line, then the
    // action to take — and the toast renders 240 characters. On the CFO's
    // PAY-DUP-01 block (3 Aug 2026) it cut off mid-word ("Remove t"), and because
    // it floats bottom-right it sat on top of the very box the message named.
    //
    // The toast is NOT suppressed for these. It was, in the first cut of this fix,
    // on the grounds that the page renders the detail itself — but that is a
    // promise about every current AND future screen that can raise, sign off or
    // pay a request (DeepSeek review, 3 Aug 2026). One screen that forgets to
    // render `err` would refuse a payment in total silence, which is far worse
    // than a truncated toast. Instead the toast carries the HEADLINE only — the
    // first paragraph, which is a complete sentence and fits — so it can never
    // truncate mid-word, and the page still renders the full finding underneath.
    const isPaymentControlRefusal = (
      response.status >= 400 && response.status < 500 &&
      typeof (errorBody as { control?: unknown } | undefined)?.control === 'string' &&
      String((errorBody as { control?: unknown }).control).startsWith('PAY-')
    )
    const noisyStatus = (
      response.status === 401 ||
      (response.status === 403 && isProbePath) ||
      (response.status === 404 && isProbePath) ||
      isFinancialCapabilityDenial ||
      (isPollingPath && (response.status === 502 || response.status === 503 || response.status === 504))
    )
    if (typeof window !== 'undefined' && !noisyStatus) {
      try {
        // 502/503/504 are transient gateway states — the backend is briefly
        // unreachable (e.g. a deploy/restart window), NOT a real application
        // error. Say so, so a routine ~60s deploy doesn't read as a crash
        // (Oprah bug 2712d5ad). A genuine 500 stays "Server error".
        // The HEADLINE has to agree with the description above (Fable review
        // 2026-07-30). It used to say "please retry in a moment" for every gateway
        // status, so on a failed payment the bold title told the user to retry while
        // the description underneath told them to check first — and the title is the
        // line people actually read. Same read-vs-write rule as the description.
        const mod = await import('@/components/Toaster')
        mod.pushToast({
          type: response.status >= 500 ? 'error' : 'warning',
          message: gatewayDown
            ? (readOnlyCall
                ? 'Omni was updating — please try again'
                : 'Omni was updating — check before submitting again')
            : response.status >= 500
              ? 'Server error — please try again'
              : isPaymentControlRefusal
                ? 'Payment stopped by a control check'
                : 'Request failed',
          // A control refusal's first paragraph is its headline and a whole
          // sentence; the bullets and the action follow after a blank line and
          // belong on the page, not in a floating popup that would cut them off.
          description: (isPaymentControlRefusal
            ? errorMessage.split('\n\n')[0]
            : errorMessage).slice(0, 240),
        })
      } catch { /* toast lib failed to load; never block */ }
    }
    // CFO directive 2026-05-20: preserve the parsed response body on the
    // thrown error so callers can render server-supplied detail
    // (e.g. smart-upload's `ai_explanation`) instead of just the
    // truncated `detail` string.
    const err = new Error(errorMessage) as Error & {
      status?: number; body?: unknown;
    }
    err.status = response.status
    err.body   = errorBody
    throw err
  }

  // Handle empty responses (204 No Content)
  if (response.status === 204) {
    return {} as T
  }

  return response.json()
}

// ─── Raw fetch for non-/api/v1 endpoints (e.g. /hris/api/employees/) ───────────
//
// HR-analytics 401 bug (2026-06-08): the HR page hand-rolled its own
// `fetch('/hris/api/employees/', {credentials:'include'})` that acquired the
// SSO Bearer ONCE on mount. Because the page's access gate resolves from a
// cached value, the fetch fired BEFORE MSAL's access token was ready —
// acquireApiToken() returned null, no Authorization header went out, the
// server returned 401, and there was no retry, so the page stuck on
// "Couldn't load HR data — HTTP 401" for SSO users. apiFetch never showed
// this because it has cold-token recovery; the bespoke helper didn't.
//
// apiFetchRaw mirrors apiFetch's auth resolution (Bearer JWT → legacy Token,
// skip the '__sso__' sentinel) for endpoints OUTSIDE the /api/v1 namespace
// (path used as-is, no prefix, no company injection) and adds a one-shot
// retry: if the first call 401s with no bearer, re-acquire once (MSAL is
// ready by then) and retry. Returns the raw Response.
export async function apiFetchRaw(path: string, options?: RequestInit): Promise<Response> {
  const buildHeaders = async (): Promise<{ headers: Record<string, string>; bearer: string | null }> => {
    const headers: Record<string, string> = { ...(options?.headers as Record<string, string>) }
    let bearer: string | null = null
    try {
      const { acquireApiToken, SSO_API_CALLS_READY } = await import('@/auth/msal')
      if (SSO_API_CALLS_READY) bearer = await acquireApiToken()
    } catch { bearer = null }
    if (bearer) {
      headers['Authorization'] = `Bearer ${bearer}`
    } else {
      const token = getToken()
      if (token && token !== SSO_SENTINEL_TOKEN) headers['Authorization'] = `Token ${token}`
    }
    if (options?.body instanceof FormData) delete headers['Content-Type']
    return { headers, bearer }
  }

  const first = await buildHeaders()
  let response = await fetch(path, { ...options, headers: first.headers })
  let bearer = first.bearer

  // Cold-MSAL retry: SSO session whose access token wasn't ready on first paint.
  if (response.status === 401 && !first.bearer && typeof window !== 'undefined') {
    const retry = await buildHeaders()
    if (retry.bearer) {
      response = await fetch(path, { ...options, headers: retry.headers })
      bearer = retry.bearer
    }
  }

  // Expired / rotated sign-in — the same recovery apiFetch has run since the
  // Ikanyeng Sechele lockout, and authedHrisFetch since 4-Aug. apiFetchRaw
  // never ran it, so a dead key in localStorage left its callers —
  // hr-analytics and the binary payslip / report downloads — printing
  // "Invalid token." with no bounce to /login and no way for a refresh to
  // help, because nothing cleared the dead key.
  //
  // The bearer handed to the recovery MUST be the one that produced THIS
  // response, not `first.bearer`. Passing the stale first attempt makes the
  // recovery see `sentinel && !bearer` on a retry that DID authenticate, and
  // it then reads a legitimate 401 — e.g. _gate()'s "HRIS is locked,
  // requires_unlock" — as a dead sign-in and logs out a user who is perfectly
  // signed in. authedHrisFetch already carries that exact fix and its comment;
  // this is the same rule, not a new one.
  await recoverFromDeadSignIn(response, bearer)

  return response
}

// ─── Binary fetch (PDFs / CSV exports / EFT files) ─────────────────────────────
//
// External-audit follow-up 2026-05-19: previously every PDF / CSV / EFT
// download did `fetch(url, { headers: { Authorization: 'Token ' + getToken() } })`
// directly. For SSO-only users `getToken()` returns the sentinel
// '__sso__', so the header literally read "Token __sso__" and the server
// rejected with 401. apiFetchBinary mirrors apiFetch's auth resolution
// (Bearer JWT first, fallback to legacy Token, skip sentinel) and returns
// the raw Response so callers can call .blob() / .arrayBuffer().

export async function apiFetchBinary(
  path: string,
  options?: RequestInit,
): Promise<Response> {
  const url = path.startsWith('http')
    ? path
    : (path.startsWith('/api/') ? `${BASE_URL}${path}` : `${API_BASE}${path}`)

  const headers: Record<string, string> = {
    ...(options?.headers as Record<string, string>),
  }

  // Try MSAL Bearer first
  let bearer: string | null = null
  try {
    const { acquireApiToken, SSO_API_CALLS_READY } = await import('@/auth/msal')
    if (SSO_API_CALLS_READY) {
      bearer = await acquireApiToken()
    }
  } catch { bearer = null }
  if (bearer) {
    headers['Authorization'] = `Bearer ${bearer}`
  } else {
    const token = getToken()
    if (token && token !== SSO_SENTINEL_TOKEN) {
      headers['Authorization'] = `Token ${token}`
    }
  }

  let response = await fetch(url, { ...options, headers })

  // Cold-MSAL retry first (as apiFetchRaw does): a download clicked within a
  // second of a hard load can go out before the access token is ready, and
  // without this the recovery below would mistake that for a dead sign-in and
  // log the user out mid-download.
  if ((response.status === 401 || response.status === 403) && !bearer
      && typeof window !== 'undefined') {
    let retry: string | null = null
    try {
      const { acquireApiToken, SSO_API_CALLS_READY } = await import('@/auth/msal')
      if (SSO_API_CALLS_READY) retry = await acquireApiToken()
    } catch { retry = null }
    if (retry) {
      bearer = retry
      response = await fetch(url, {
        ...options,
        headers: { ...headers, Authorization: `Bearer ${retry}` },
      })
    }
  }

  // Same expired-sign-in recovery as apiFetch — a PDF or CSV download must not
  // be the one path that leaves a dead key in place and just fails.
  await recoverFromDeadSignIn(response, bearer)
  return response
}


/**
 * Show a viewable Blob (PDF/image) in an in-app overlay for the OmniDesktop
 * (WebView2) app, where a `<a download>` click is silently dropped. WebView2's
 * built-in PDF/image viewer renders it in the iframe and gives the user Save /
 * Print from its own toolbar — and it stays INSIDE the app (no navigating the
 * whole window away, which the desktop app has no back button to undo).
 * Brand-styled (navy #0D1B2A / orange #F4A623), Esc or Close to dismiss.
 */
function showBlobInline(url: string, name: string): void {
  const overlay = document.createElement('div')
  overlay.setAttribute('role', 'dialog')
  overlay.setAttribute('aria-label', name)
  overlay.style.cssText = 'position:fixed;inset:0;z-index:999999;background:rgba(13,27,42,0.92);display:flex;flex-direction:column'
  const bar = document.createElement('div')
  bar.style.cssText = 'display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 14px;background:#0D1B2A;color:#fff;font:600 14px/1.3 Montserrat,"Segoe UI",Arial,sans-serif'
  const label = document.createElement('span')
  label.textContent = name
  label.style.cssText = 'overflow:hidden;text-overflow:ellipsis;white-space:nowrap'
  const close = document.createElement('button')
  close.type = 'button'; close.textContent = 'Close'
  close.style.cssText = 'flex:0 0 auto;background:#F4A623;color:#0D1B2A;border:0;border-radius:8px;padding:8px 18px;font:600 14px Montserrat,"Segoe UI",Arial,sans-serif;cursor:pointer'
  const frame = document.createElement('iframe')
  frame.src = url; frame.title = name
  frame.style.cssText = 'flex:1;width:100%;border:0;background:#fff'
  const done = () => {
    overlay.remove()
    document.removeEventListener('keydown', onKey)
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  function onKey(e: KeyboardEvent) { if (e.key === 'Escape') done() }
  close.onclick = done
  document.addEventListener('keydown', onKey)
  bar.appendChild(label); bar.appendChild(close)
  overlay.appendChild(bar); overlay.appendChild(frame)
  document.body.appendChild(overlay)
}

/**
 * Save a Blob to the user's device.
 *
 * Ordinary browsers (Chrome, Edge, Firefox, Safari, mobile) — a hidden download
 * anchor, the standard reliable path. The anchor MUST be appended to the DOM
 * before click() for it to be honoured; if `filename` has no extension one is
 * added from the blob type.
 *
 * OmniDesktop (the Windows WebView2 app) — the host wrapper does not handle
 * WebView2's DownloadStarting event, so an `<a download>` click is accepted by
 * the page but the file is NEVER written to disk: the click silently does
 * nothing (staff bug report 2026-08-27 — the desktop app fetched the payslip
 * PDF 30+ times, every request HTTP 200 with the full file, yet nothing saved).
 * `window.open(blobUrl)` is likewise a no-op there. For viewable files (PDF /
 * image) we therefore show them in an in-app overlay (see showBlobInline) using
 * WebView2's built-in viewer, which offers Save / Print. Non-viewable types
 * (CSV, xlsx…) keep the anchor path.
 *
 * `window.chrome.webview` exists ONLY inside a WebView2-hosted app and is
 * undefined in every ordinary browser (desktop Edge included), so the anchor
 * path is provably unchanged for normal users.
 */
export function saveBlob(blob: Blob, filename = 'download'): void {
  let name = filename || 'download'
  if (!/\.[a-z0-9]{2,5}$/i.test(name)) {
    const ext = ({
      'application/pdf': 'pdf', 'image/jpeg': 'jpg', 'image/png': 'png',
      'image/webp': 'webp', 'image/gif': 'gif',
    } as Record<string, string>)[blob.type] || ''
    if (ext) name += `.${ext}`
  }
  const url = URL.createObjectURL(blob)

  const inWebView2 = typeof window !== 'undefined'
    && !!(window as unknown as { chrome?: { webview?: unknown } }).chrome?.webview
  const viewable = /^(application\/pdf|image\/)/.test(blob.type)
  if (inWebView2 && viewable) {
    showBlobInline(url, name)   // do NOT revoke here — the viewer needs the URL
    return
  }

  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.rel = 'noopener'
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 60_000)
}


// ─── Auth ──────────────────────────────────────────────────────────────────────

export async function login(username: string, password: string): Promise<{ token: string }> {
  const response = await apiFetchBinary(`${BASE_URL}/api-token-auth/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })

  if (!response.ok) {
    let errorMessage = 'Invalid credentials'
    try {
      const errorData = await response.json()
      if (errorData.non_field_errors) errorMessage = errorData.non_field_errors.join(', ')
      else if (errorData.detail) errorMessage = errorData.detail
    } catch {}
    throw new Error(errorMessage)
  }

  return response.json()
}

// ─── Accounts ─────────────────────────────────────────────────────────────────

export async function getAccounts(params?: {
  account_type?: string
  search?: string
  bank_only?: string
  page_size?: string
  page?: string
  ordering?: string
  company?: string
  as_of?: string
}): Promise<PaginatedResponse<Account>> {
  const query = new URLSearchParams(params as Record<string, string>).toString()
  return apiFetch<PaginatedResponse<Account>>(`/accounts/${query ? `?${query}` : ''}`)
}

/**
 * Fetch ALL accounts, paginating past core.pagination's max_page_size=100 cap.
 * Pages with `count` from the first response so screens that need the full CoA
 * (e.g. /hris/payroll-setup GL picker) aren't silently truncated to 100 rows
 * (bug c369818a). Hard-stops at 50 pages (5,000 accounts) as a safety bound.
 */
export async function getAllAccounts(params?: {
  account_type?: string
  ordering?: string
  company?: string
}): Promise<Account[]> {
  const out: Account[] = []
  for (let page = 1; page <= 50; page++) {
    const r = await getAccounts({ ...params, page: String(page), page_size: '100' })
    out.push(...r.results)
    if (!r.next || r.results.length === 0) break
  }
  return out
}

export async function getAccount(id: string): Promise<Account> {
  return apiFetch<Account>(`/accounts/${id}/`)
}

export interface CreateAccountInput {
  code: string
  name: string
  account_type: 'asset' | 'liability' | 'equity' | 'revenue' | 'expense'
  sub_type: string
  parent?: string | null
  is_bank_account?: boolean
}

export async function createAccount(data: CreateAccountInput): Promise<Account> {
  return apiFetch<Account>('/accounts/', {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

// ─── Journal Entries ──────────────────────────────────────────────────────────

export async function getJournalEntries(
  params?: Record<string, string>
): Promise<PaginatedResponse<JournalEntry>> {
  const query = new URLSearchParams(params).toString()
  return apiFetch<PaginatedResponse<JournalEntry>>(`/journal-entries/${query ? `?${query}` : ''}`)
}

export async function getJournalEntry(id: string): Promise<JournalEntryDetail> {
  return apiFetch<JournalEntryDetail>(`/journal-entries/${id}/`)
}

export async function createJournalEntry(
  data: CreateJournalEntryInput
): Promise<JournalEntryDetail> {
  return apiFetch<JournalEntryDetail>('/journal-entries/', {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

export async function postJournalEntry(id: string): Promise<JournalEntryDetail> {
  return apiFetch<JournalEntryDetail>(`/journal-entries/${id}/post/`, {
    method: 'POST',
  })
}

// ─── Contacts ─────────────────────────────────────────────────────────────────

export async function getContacts(
  params?: Record<string, string>
): Promise<PaginatedResponse<Contact>> {
  const query = new URLSearchParams(params).toString()
  return apiFetch<PaginatedResponse<Contact>>(`/contacts/${query ? `?${query}` : ''}`)
}

export async function getContact(id: string): Promise<Contact> {
  return apiFetch<Contact>(`/contacts/${id}/`)
}

export async function createContact(data: Partial<Contact>): Promise<Contact> {
  return apiFetch<Contact>('/contacts/', {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

export async function updateContact(id: string, data: Partial<Contact>): Promise<Contact> {
  return apiFetch<Contact>(`/contacts/${id}/`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  })
}

// ─── Invoices ─────────────────────────────────────────────────────────────────

export async function getInvoices(
  params?: Record<string, string>
): Promise<PaginatedResponse<Invoice>> {
  const query = new URLSearchParams(params).toString()
  return apiFetch<PaginatedResponse<Invoice>>(`/invoices/${query ? `?${query}` : ''}`)
}

export async function getInvoice(id: string): Promise<InvoiceDetail> {
  return apiFetch<InvoiceDetail>(`/invoices/${id}/`)
}

export async function createInvoice(data: CreateInvoiceInput): Promise<InvoiceDetail> {
  return apiFetch<InvoiceDetail>('/invoices/', {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

export async function postInvoice(id: string): Promise<InvoiceDetail> {
  return apiFetch<InvoiceDetail>(`/invoices/${id}/post/`, {
    method: 'POST',
  })
}

export async function updateInvoice(
  id: string,
  data: Partial<CreateInvoiceInput>,
): Promise<InvoiceDetail> {
  return apiFetch<InvoiceDetail>(`/invoices/${id}/`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  })
}

export async function deleteInvoice(id: string): Promise<void> {
  const res = await apiFetchBinary(`${API_BASE}/invoices/${id}/`, {
    method: 'DELETE',
    headers: {
},
  })
  if (!res.ok && res.status !== 204) {
    let msg = `Delete failed: ${res.status}`
    try { const j = await res.json(); if (j?.error) msg = j.error } catch {}
    throw new Error(msg)
  }
}

export async function duplicateInvoice(id: string): Promise<InvoiceDetail> {
  return apiFetch<InvoiceDetail>(`/invoices/${id}/duplicate/`, { method: 'POST' })
}

export async function reverseInvoice(id: string, reason?: string): Promise<InvoiceDetail> {
  return apiFetch<InvoiceDetail>(`/invoices/${id}/reverse/`, {
    method: 'POST',
    body: JSON.stringify({ reason: reason || '' }),
  })
}

// Bill approval workflow (vendor_bill only)
export async function submitBillForApproval(id: string): Promise<InvoiceDetail> {
  return apiFetch<InvoiceDetail>(`/invoices/${id}/submit-for-approval/`, { method: 'POST' })
}
export async function approveBill(id: string): Promise<InvoiceDetail> {
  return apiFetch<InvoiceDetail>(`/invoices/${id}/approve-bill/`, { method: 'POST' })
}
export async function rejectBill(id: string, reason: string): Promise<InvoiceDetail> {
  return apiFetch<InvoiceDetail>(`/invoices/${id}/reject-bill/`, {
    method: 'POST',
    body: JSON.stringify({ reason }),
  })
}

export async function resetInvoiceToDraft(id: string): Promise<InvoiceDetail> {
  return apiFetch<InvoiceDetail>(`/invoices/${id}/reset-to-draft/`, {
    method: 'POST',
  })
}

export function invoicePdfUrl(id: string): string {
  return `${API_BASE}/invoices/${id}/pdf/`
}

/** Open the PDF in a new tab using a fetched blob (auth header isn't sent on direct GET). */
export async function openInvoicePdf(id: string): Promise<void> {
  const res = await apiFetchBinary(`${API_BASE}/invoices/${id}/pdf/`, {
    headers: {
},
  })
  if (!res.ok) throw new Error(`PDF failed: ${res.status}`)
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  // Save via anchor — window.open() on a blob: URL fails in the OmniDesktop
  // wrapper with "Get an app to open this 'blob' link".
  const a = document.createElement('a')
  a.href = url
  a.download = `invoice-${id}.pdf`
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 30000)
}

// ─── Payments ─────────────────────────────────────────────────────────────────

export async function getPayments(
  params?: Record<string, string>
): Promise<PaginatedResponse<Payment>> {
  const query = new URLSearchParams(params).toString()
  return apiFetch<PaginatedResponse<Payment>>(`/payments/${query ? `?${query}` : ''}`)
}

export async function getPayment(id: string): Promise<PaymentDetail> {
  return apiFetch<PaymentDetail>(`/payments/${id}/`)
}

// The topbar entity selection — POST bodies must carry it explicitly
// (apiFetch only auto-injects ?company= on GET).
export function selectedCompanyId(): string | null {
  if (typeof window === 'undefined') return null
  return localStorage.getItem('alpha_company_id')
}

export async function createPayment(data: CreatePaymentInput): Promise<PaymentDetail> {
  const company = data.company || selectedCompanyId() || undefined
  return apiFetch<PaymentDetail>('/payments/', {
    method: 'POST',
    body: JSON.stringify({ ...data, company }),
  })
}

export async function confirmPayment(id: string): Promise<PaymentDetail> {
  return apiFetch<PaymentDetail>(`/payments/${id}/confirm/`, {
    method: 'POST',
  })
}

export async function updatePayment(
  id: string,
  data: Partial<CreatePaymentInput>,
): Promise<PaymentDetail> {
  return apiFetch<PaymentDetail>(`/payments/${id}/`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  })
}

export async function deletePayment(id: string): Promise<void> {
  const res = await apiFetchBinary(`${API_BASE}/payments/${id}/`, {
    method: 'DELETE',
    headers: {
},
  })
  if (!res.ok && res.status !== 204) {
    let msg = `Delete failed: ${res.status}`
    try { const j = await res.json(); if (j?.error) msg = j.error } catch {}
    throw new Error(msg)
  }
}

export async function duplicatePayment(id: string): Promise<PaymentDetail> {
  return apiFetch<PaymentDetail>(`/payments/${id}/duplicate/`, { method: 'POST' })
}

export async function resetPaymentToDraft(id: string): Promise<PaymentDetail> {
  return apiFetch<PaymentDetail>(`/payments/${id}/reset-to-draft/`, { method: 'POST' })
}

// ── PAY-003 tier maker-checker ──────────────────────────────────────────────

export async function submitPaymentForApproval(
  id: string,
): Promise<PaymentDetail & { assigned_tier?: number; assigned_role?: string }> {
  return apiFetch(`/payments/${id}/submit-for-approval/`, { method: 'POST' })
}

export async function approvePayment(
  id: string,
  comment: string,
): Promise<PaymentDetail & { bs_tie?: { reconciled: boolean; variance: string | null } }> {
  return apiFetch(`/payments/${id}/approve/`, {
    method: 'POST',
    body: JSON.stringify({ comment }),
  })
}

export async function rejectPayment(id: string, comment: string): Promise<PaymentDetail> {
  return apiFetch<PaymentDetail>(`/payments/${id}/reject/`, {
    method: 'POST',
    body: JSON.stringify({ comment }),
  })
}

export async function getPaymentApprovalQueue(
  params?: Record<string, string>,
): Promise<PaymentDetail[]> {
  const query = new URLSearchParams(params).toString()
  return apiFetch<PaymentDetail[]>(`/payments/approval-queue/${query ? `?${query}` : ''}`)
}

export interface CreatePaymentFromBillInput {
  bill_id: string
  bank_account_id: string
  payment_date: string
  amount?: string
  reference?: string
  payment_method?: string
}

export async function createPaymentFromBill(
  data: CreatePaymentFromBillInput,
): Promise<PaymentDetail & { assigned_tier?: number; assigned_role?: string }> {
  return apiFetch(`/payments/create-from-bill/`, {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

export async function createPaymentFromBankLine(
  lineId: string,
  data: { contact_id: string; reference?: string; payment_method?: string; bill_id?: string },
): Promise<PaymentDetail & { assigned_tier?: number; assigned_role?: string }> {
  return apiFetch(`/bank-statement-lines/${lineId}/create-payment/`, {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

/**
 * EFT batch export — POST a list of confirmed outbound payment IDs and a
 * source account number; returns a Blob (text/plain bank file) and the
 * server's per-row skip summary parsed from X-EFT-Summary header.
 *
 * Triggers a browser download as well.
 */
export async function exportEftBatch(args: {
  payment_ids: string[]
  source_account_number: string
  batch_ref: string
  format?: 'fnb_bol'
}): Promise<{ filename: string; summary: any }> {
  const res = await apiFetchBinary(`${API_BASE}/payments/eft-export/`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
},
    body: JSON.stringify({ format: 'fnb_bol', ...args }),
  })
  if (!res.ok) {
    let msg = `EFT export failed: ${res.status}`
    try { const j = await res.json(); if (j?.error) msg = j.error } catch {}
    throw new Error(msg)
  }
  const summaryHeader = res.headers.get('X-EFT-Summary')
  const summary = summaryHeader ? JSON.parse(summaryHeader) : null
  const disposition = res.headers.get('Content-Disposition') || ''
  const m = disposition.match(/filename="([^"]+)"/)
  const filename = m ? m[1] : `fnb_bol_${args.batch_ref}.txt`
  const blob = await res.blob()
  // Trigger download
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  setTimeout(() => URL.revokeObjectURL(url), 30000)
  return { filename, summary }
}

// ─── Banking ──────────────────────────────────────────────────────────────────

export async function getBankAccounts(): Promise<PaginatedResponse<BankAccount>> {
  // Pull a large page so dropdowns on /banking/fnb get the full set.
  // Default DRF page_size truncates choices.
  return apiFetch<PaginatedResponse<BankAccount>>('/bank-accounts/?page_size=500')
}

export interface BankReconciliationReport {
  statement_id: string
  statement_date: string
  bank_closing_balance: string
  gl_balance: string
  difference: string
  is_reconciled: boolean
  lines: {
    total: number
    auto_matched: number
    manually_matched: number
    excluded: number
    unmatched: number
  }
  unmatched_lines: BankStatementLine[]
}

// BANK-007: explains WHY the GL and the bank differ, for one statement.
export async function getBankReconciliation(
  statementId: string
): Promise<BankReconciliationReport> {
  return apiFetch<BankReconciliationReport>(
    `/bank-statements/${statementId}/reconciliation/`
  )
}

export async function getBankStatements(
  params?: Record<string, string>
): Promise<PaginatedResponse<BankStatement>> {
  const query = new URLSearchParams(params).toString()
  return apiFetch<PaginatedResponse<BankStatement>>(`/bank-statements/${query ? `?${query}` : ''}`)
}

export async function getBankStatement(id: string): Promise<BankStatementDetail> {
  return apiFetch<BankStatementDetail>(`/bank-statements/${id}/`)
}

export async function importBankStatement(formData: FormData): Promise<BankStatementDetail> {
  return apiFetch<BankStatementDetail>('/bank-statements/import/', {
    method: 'POST',
    body: formData,
  })
}

export async function runMatching(
  statementId: string
): Promise<{ matched: number; unmatched: number }> {
  return apiFetch<{ matched: number; unmatched: number }>(
    `/bank-statements/${statementId}/run-matching/`,
    { method: 'POST' }
  )
}

export async function getBankStatementLines(
  params?: Record<string, string>
): Promise<PaginatedResponse<BankStatementLine>> {
  const query = new URLSearchParams(params).toString()
  return apiFetch<PaginatedResponse<BankStatementLine>>(
    `/bank-statement-lines/${query ? `?${query}` : ''}`
  )
}

export async function matchStatementLine(
  lineId: string,
  data: { payment_id?: string; journal_entry_id?: string; dry_run?: boolean }
): Promise<BankStatementLine | MatchDryRunResponse> {
  return apiFetch<BankStatementLine | MatchDryRunResponse>(`/bank-statement-lines/${lineId}/match/`, {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

// ─── Reports ──────────────────────────────────────────────────────────────────

// Build a query string from a record, skipping undefined / null / '' values.
function _qs(params: Record<string, string | number | undefined | null>): string {
  const pairs = Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
  return pairs.length ? `?${pairs.join('&')}` : ''
}

/**
 * Trial Balance.
 *
 * CFO directive 2026-05-21: a TB is ALWAYS for a period (opening + movements
 * + closing). The legacy single-`asOf` signature is preserved for callers
 * that pass only one positional arg — those go through as `?as_of=...` and
 * the backend derives `from_date` from the fiscal-year start. New callers
 * should pass `{from, to}` for full control.
 */
export async function getTrialBalance(
  asOfOrParams?: string | { from?: string; to?: string; company?: string | null },
  company?: string | null,
): Promise<TrialBalanceReport> {
  // New shape: object with from + to.
  if (asOfOrParams && typeof asOfOrParams === 'object') {
    const { from, to, company: co } = asOfOrParams
    return apiFetch<TrialBalanceReport>(
      `/reports/trial-balance/${_qs({ from, to, company: co ?? undefined })}`,
    )
  }
  // Legacy shape: single as_of string.
  return apiFetch<TrialBalanceReport>(
    `/reports/trial-balance/${_qs({ as_of: asOfOrParams as string | undefined, company: company ?? undefined })}`,
  )
}

export async function getProfitLoss(from: string, to: string, company?: string | null): Promise<ProfitLossReport> {
  return apiFetch<ProfitLossReport>(`/reports/profit-loss/${_qs({ from, to, company: company ?? undefined })}`)
}

/** P&L in the CFO's Management Accounts workbook layout — GWP separated from
 *  other inflows, claims block (gross/recovered/subrog), acquisition costs,
 *  EBITDA → EBIT → PBT → PAT. The dashboard 'Revenue' tile must read
 *  totals.gross_written_premium from this endpoint, not the lumped 'revenue'
 *  total from getProfitLoss(). See reporting/ma_pl_spec.py. */
export interface MAProfitLossReport {
  from_date: string
  to_date: string
  sections: {
    id: string
    label: string
    lines: { id: string; label: string; amount: string; sign: 'income' | 'expense' }[]
    subtotal_label: string
    subtotal: string
  }[]
  totals: {
    gross_written_premium: string
    premiums_ceded: string
    change_in_upr: string
    net_earned_premium: string
    gross_claims: string
    ri_claims_recovered: string
    subrogations_salvages: string
    net_claim_incurred: string
    gross_loss_ratio: string
    net_loss_ratio: string
    net_acquisition: string
    gross_profit: string
    total_other_income: string
    total_operating_expenses: string
    total_provisions: string
    ebitda: string
    depreciation: string
    ebit: string
    finance_cost: string
    pbt: string
    taxation: string
    pat: string
  }
}

/** One insurer on the Botswana short-term market benchmark. Every ratio is a
 *  percentage and is `null` when the insurer never disclosed the input — the
 *  screen must print an em dash for those, never a zero. */
export interface BenchmarkInsurer {
  name: string
  short: string
  year_end: string
  fiscal_year: string
  insurance_revenue: number
  gross_written_premium: number | null
  reinsurance_ceded: number | null
  reinsurance_recoveries: number | null
  reinsurance_commission: number | null
  net_reinsurance_cost: number | null
  claims_incurred: number | null
  acquisition_costs: number | null
  operating_expenses: number | null
  staff_costs: number | null
  insurance_service_result: number | null
  investment_and_other_income: number | null
  profit_before_tax: number | null
  taxation: number | null
  profit_after_tax: number | null
  total_assets: number | null
  total_equity: number | null
  cash: number | null
  note: string
  is_us: boolean
  source: string
  net_revenue: number | null
  underwriting_profit: number | null
  combined_ratio: number | null
  cession_ratio: number | null
  staff_to_revenue: number | null
  staff_to_net_revenue: number | null
  expense_to_revenue: number | null
  expense_to_net_revenue: number | null
  gross_loss_ratio: number | null
  acquisition_ratio: number | null
  insurance_service_margin: number | null
  pat_margin: number | null
  return_on_equity: number | null
  return_on_assets: number | null
  effective_tax_rate: number | null
  reinsurance_commission_rate: number | null
  underwriting_share_of_profit: number | null
  solvency_ratio: number | null
  operating_leverage: number | null
}

export interface BenchmarkHistoryRow {
  period: string
  gwp: number
  nep: number
  ceded: number
  net_claims: number
  opex: number
  pat: number
}

export interface BenchmarkCostRow {
  name: string
  revenue: number
  staff_ise: number | null
  staff_below: number | null
  staff_total: number | null
  other_ise: number | null
  other_below: number | null
  below_line_only: number | null
  total_cost: number | null
  staff_pct: number | null
  total_pct: number | null
  below_only_pct: number | null
  note: string
  is_us: boolean
}

export interface BenchmarkExpenseLine {
  line: string
  fy25: number
  fy26: number
  change: number
  change_pct: number | null
}

export interface BenchmarkExpenseGroup {
  group: string
  fy25: number
  fy26: number
  change: number
  share: number
  lines: BenchmarkExpenseLine[]
}

export interface BenchmarkClassRow {
  name: string
  premium: number
  claims: number
  loss_ratio: number | null
  motor: boolean
  margin: number
  share_of_book: number
}

export interface BenchmarkSegment {
  premium: number
  claims: number
  loss_ratio: number
  share: number
}

export interface BenchmarkTreaty {
  name: string
  cession: string
  commission: string
  reinsurers: string
}

/** One competitor's statement, transcribed at the level it publishes. Units
 *  differ by insurer — `units` says which, and nothing here is scaled. */
export interface BenchmarkProfile {
  key: string
  full: string
  ye: string
  units: string
  flag: string
  headline: string
  pct_note: string
  revenue: number
  gwp?: number | null
  nwp?: number | null
  ise: number
  net_ri: number
  isr: number
  pbt: number
  tax: number
  pat: number
  ta: number | null
  te: number | null
  cash: number | null
  ise_components: [string, number][]
  ise_note?: string
  staff: [string, number][]
  staff_total: number | null
  staff_ise: number | null
  staff_below: number | null
  opex_below: [string, number][]
  opex_below_total: number
  opex_ise: [string, number][]
  opex_ise_total: number
  mgmt_fee_split?: [string, number][]
  classes?: [string, number, number | null][] | null
  class_note?: string
  cession?: [string, number, number, number][] | null
  treaty_cession?: [string, number, number][]
  ri_components?: [string, number][]
  ri_other?: [string, number][]
  ri_note?: string
  ri_commission: number | null
  ceded: number | null
  recovered: number | null
  commission_income?: number
  commission_expense?: number
}

export interface PeerBenchmarkDetail {
  profiles: BenchmarkProfile[]
  units: string
  cost_base: {
    rows: BenchmarkCostRow[]
    median_staff_pct: number | null
    median_total_pct: number | null
  }
  expenses: {
    basis: string
    total_fy25: number
    total_fy26: number
    groups: BenchmarkExpenseGroup[]
  }
  classes: {
    basis: string
    classes: BenchmarkClassRow[]
    total_premium: number
    total_claims: number
    blended_loss_ratio: number
    motor: BenchmarkSegment
    non_motor: BenchmarkSegment
  }
  bic_cession: { cls: string; revenue: number; ceded: number; pct: number }[]
  hollard_cession: { treaty: string; ceded: number; recovered: number; net: number }[]
  adic_treaties: BenchmarkTreaty[]
  adic_ceding_commission_rate: number
  motor_entitlement: {
    loss_ratio: number
    commission: number
    at_floor: boolean
    above_cap: boolean
    provisional: number
    note: string
  }
}

export interface PeerBenchmarkReport {
  units: string
  as_at: string
  us: BenchmarkInsurer
  insurers: BenchmarkInsurer[]
  peer_median: Record<string, number | null>
  history: BenchmarkHistoryRow[]
  peer_count: number
  sources: string
  detail: PeerBenchmarkDetail
}

export async function getPeerBenchmark(): Promise<PeerBenchmarkReport> {
  return apiFetch<PeerBenchmarkReport>('/reports/peer-benchmark/')
}

export async function getMAProfitLoss(from: string, to: string, company?: string | null): Promise<MAProfitLossReport> {
  return apiFetch<MAProfitLossReport>(`/reports/ma-profit-loss/${_qs({ from, to, company: company ?? undefined })}`)
}

// ─── Entity P&L (CFO directive 2026-05-21 — per-entity templates) ─────────────

export interface EntityPLLine {
  id: string
  label: string
  amount: string
}

export interface EntityPLSection {
  id: string
  label: string
  sign: 'income' | 'expense'
  subtotal_label: string
  subtotal: string
  lines: EntityPLLine[]
  has_activity: boolean
}

export interface EntityPLTotal {
  id: string
  label: string
  formula: string
  amount: string
}

export interface EntityPLReport {
  format: 'entity_pl' | 'ma_pl'
  entity_code: string
  pdf_alias?: string
  entity_name?: string
  report_title?: string
  currency?: string
  from_date: string
  to_date: string
  sections?: EntityPLSection[]
  totals?: EntityPLTotal[] | Record<string, string>
  nonzero_section_ids?: string[]
}

export async function getEntityPL(from: string, to: string, company: string): Promise<EntityPLReport> {
  return apiFetch<EntityPLReport>(`/reports/entity-pl/${_qs({ from, to, company })}`)
}

export async function getBalanceSheet(asOf?: string, company?: string | null): Promise<BalanceSheetReport> {
  // CFO directive 2026-05-20: existing callers get the LEGACY view
  // (account-type/sub-type breakdown) so dashboards that haven't moved
  // to the MA shape yet keep rendering. Param is `view`, NOT `format`
  // — `format` is DRF's content-negotiation reserved word.
  return apiFetch<BalanceSheetReport>(`/reports/balance-sheet/${_qs({ as_of: asOf, view: 'legacy', company: company ?? undefined })}`)
}

export async function getMaBalanceSheet(asOf?: string, company?: string | null): Promise<MaBalanceSheetReport> {
  return apiFetch<MaBalanceSheetReport>(`/reports/balance-sheet/${_qs({ as_of: asOf, view: 'ma', company: company ?? undefined })}`)
}

export async function getArAging(asOf?: string, company?: string | null): Promise<AgingReport> {
  return apiFetch<AgingReport>(`/reports/ar-aging/${_qs({ as_of: asOf, company: company ?? undefined })}`)
}

// ─── Source-to-Ledger Reconciliation Hub (Phase 1) ─────────────────────────────
export interface ReconLine {
  id: string
  metric_key: string
  label: string
  unit: string
  source_total: string | null
  source_system: string
  omni_posted: string | null
  variance: string | null
  variance_pct: string | null
  status: 'matched' | 'breach' | 'no_source' | 'no_omni_side'
  note: string
}
export interface ReconAgeing {
  id: string
  bucket: string
  graphite_total: string | null
  omni_total: string | null
  portal_total: string | null
  variance_graphite_vs_omni: string | null
  variance_pct: string | null
  status: string
}
export interface ReconRunMeta {
  id: string
  company: string
  company_code: string | null
  period_label: string
  period_start: string | null
  period_end: string
  tolerance_pct: string
  status: 'completed' | 'partial' | 'failed'
  run_at: string
  run_by_name: string | null
}
export interface ReconDashboard {
  run: ReconRunMeta | null
  rows: ReconLine[]
  ageing: ReconAgeing[]
  scope_note: string
  hint?: string
}
export interface ReconRunListItem {
  id: string
  company_code: string | null
  period_label: string
  period_start: string | null
  period_end: string
  tolerance_pct: string
  status: string
  run_at: string
}

export async function getReconDashboard(period?: string, company?: string | null): Promise<ReconDashboard> {
  return apiFetch<ReconDashboard>(`/recon/dashboard/${_qs({ period, company: company ?? undefined })}`)
}
export async function getReconRuns(): Promise<ReconRunListItem[]> {
  return apiFetch<ReconRunListItem[]>(`/recon/runs/`)
}
export async function runReconciliation(body: {
  period_label: string
  period_end: string
  period_start?: string
  tolerance_pct?: number
}): Promise<ReconRunMeta> {
  return apiFetch<ReconRunMeta>(`/recon/run/`, { method: 'POST', body: JSON.stringify(body) })
}

// CFO directive 2026-05-24: canonical "Total Receivables" — everything
// (dashboard tile, BS "Receivables" line, AR aging summary, CFO
// dashboard) must read this so all the tables agree.
export interface ReceivablesSummary {
  as_of:      string
  company:    string | null
  total_bwp:  string
  buckets:    { related_party: string; trade_other: string; subrogation: string; reinsurance: string; other: string }
  accounts:   { code: string; name: string; bucket: string; balance_bwp: string }[]
}
export async function getReceivablesSummary(asOf?: string, company?: string | null): Promise<ReceivablesSummary> {
  return apiFetch<ReceivablesSummary>(`/reports/receivables-summary/${_qs({ as_of: asOf, company: company ?? undefined })}`)
}

// CFO directive 2026-05-24: CoA-as-SSOT-viewer. Tree mirrors the MA
// workbook (Current Assets → MA line → leaf GL accounts with balances).
// Dashboard tiles deep-link into a node so 'all the tables talk the same'.
export interface CoaMaAccount {
  code:         string
  name:         string
  sub_type:     string
  balance_bwp:  string
}
export interface CoaMaLine {
  label:        string
  subtotal_bwp: string
  accounts:     CoaMaAccount[]
}
export interface CoaMaSection {
  id:             string
  label:          string
  side:           'asset' | 'liability' | 'equity' | 'pl'
  subtotal_label: string
  subtotal_bwp:   string
  lines:          CoaMaLine[]
}
export interface CoaMaTree {
  as_of:     string
  from_date: string
  company:   string | null
  bs:        CoaMaSection[]
  pl:        CoaMaSection[]
  unmapped:  { code: string; name: string; sub_type: string; account_type: string; fs_line_item: string; balance_bwp: string }[]
}
// BS subtotals are cumulative at as_of. P&L subtotals are activity in
// [from_date, as_of]. Omitting from_date defaults to FY-start of as_of.
export async function getCoaMaTree(asOf?: string, company?: string | null, fromDate?: string): Promise<CoaMaTree> {
  return apiFetch<CoaMaTree>(`/reports/coa-ma-tree/${_qs({ as_of: asOf, from_date: fromDate, company: company ?? undefined })}`)
}

export async function getApAging(asOf?: string, company?: string | null): Promise<AgingReport> {
  return apiFetch<AgingReport>(`/reports/ap-aging/${_qs({ as_of: asOf, company: company ?? undefined })}`)
}

export async function getCashPosition(
  company?: string | null,
  asOf?: string,
): Promise<CashPositionReport> {
  return apiFetch<CashPositionReport>(`/reports/cash-position/${_qs({
    company: company ?? undefined,
    as_of: asOf,
  })}`)
}

// ─── Frozen-figure drift detection (CFO directive 2026-05-17) ────────────────

export interface FrozenDriftEntry {
  label: string
  period: string
  frozen: string
  actual: string
  diff: string
  diff_pct: string
  tolerance_pct: string
  acknowledged: boolean
}

export interface FrozenDriftResponse {
  period: string
  from_date?: string
  to_date?: string
  company?: string | null
  drifts: FrozenDriftEntry[]
  all_ok: boolean
  actuals?: Record<string, string>
  reason?: string
}

/** Compare displayed dashboard tile values to the CFO-locked figures. */
export async function getFrozenDrift(
  period: string,
  company?: string | null,
): Promise<FrozenDriftResponse> {
  return apiFetch<FrozenDriftResponse>(`/reports/frozen-drift/${_qs({
    period,
    company: company ?? undefined,
  })}`)
}

// ─── Frozen-drift NARRATOR (feature #4 — self-explaining dashboard) ──────────

export interface DriftTopEntry {
  entry_number: string
  entry_date: string
  account_code: string
  amount_bwp: string
}

export interface DriftNarrative extends FrozenDriftEntry {
  narrative: string
  top_entries: DriftTopEntry[]
}

export interface FrozenDriftNarrativeResponse {
  period: string
  from_date?: string
  to_date?: string
  company?: string | null
  narratives: DriftNarrative[]
  all_ok: boolean
  reason?: string
}

/** Frozen-drift set enriched with a plain-English 'why' per drifting tile. */
export async function getFrozenDriftNarrative(
  period: string,
  company?: string | null,
): Promise<FrozenDriftNarrativeResponse> {
  return apiFetch<FrozenDriftNarrativeResponse>(`/reports/frozen-drift/narrative/${_qs({
    period,
    company: company ?? undefined,
  })}`)
}

// ─── Premium lapse early-warning (wow feature #6) ───────────────────────────

export interface LapseRow {
  policy_number: string
  product_name: string
  tier: 'tier1' | 'tier2' | 'tier3'
  consecutive_misses: number
  est_monthly_bwp: string
  last_seen: string
}

export interface PremiumLapseResponse {
  as_of: string
  window_months: number
  counts: { tier1: number; tier2: number; tier3: number }
  at_risk_policies: number
  at_risk_monthly_bwp: string
  rows: LapseRow[]
  row_cap: number
}

/** Policies with a trailing run of failed debits + monthly premium at risk. */
export async function getPremiumLapse(months = 6): Promise<PremiumLapseResponse> {
  return apiFetch<PremiumLapseResponse>(`/reports/premium-lapse/${_qs({ months })}`)
}

// ─── Cost-per-productive-hour league (wow feature #9, sensitive) ────────────

export interface CostDept {
  department: string
  total_cost_bwp: string
  productive_hours: string
  headcount: number
  cost_per_productive_hour: string | null
}
export interface CostPerson {
  full_name: string
  department: string
  cost_bwp: string
  productive_hours: string
  cost_per_hour: string | null
}
export interface CostPerHourResponse {
  month: string
  company_scoped: boolean
  departments: CostDept[]
  people: CostPerson[]
  error?: string
}

/** Department + person payroll cost per Time Doctor productive hour. */
export async function getCostPerHour(month?: string): Promise<CostPerHourResponse> {
  return apiFetch<CostPerHourResponse>(`/reports/cost-per-hour/${_qs({ month: month || undefined })}`)
}

// ─── Transformation Board ───────────────────────────────────────────────────
// /api/v1/transformation/board/ — the CEO/CFO/COO/CHCO screen for the "First
// AI Insurance Company in Botswana" programme (20-Sep-2026 → 20-Jan-2027).
// Shapes mirror transformation/pulse.py build_board() + workforce.py
// workforce_block() exactly — see those modules, not this comment, for what
// each field means. Server-gated (CanViewTransformationBoard); a non-viewer
// gets a plain 403 with `detail` set to a calm explanation.
export interface TransformationClock {
  start: string
  end: string
  today: string
  days_total: number
  days_elapsed: number
  days_remaining: number
  time_percent: number
}
export interface TransformationMonth {
  month: number
  count: number
  done: number
  percent: number
  saving_bwp: number
  fte_released: number
}
export interface TransformationDept {
  department: string
  manager_name: string
  manager_email: string
  headcount_now: number
  headcount_target: number
  cost_now: number
  cost_per_head: number
  reallocate_to_acquisition: number
  initiatives: number
  initiatives_done: number
  initiatives_blocked: number
  delivery_percent: number | null
  shipped_for_them: number
  confirmed_used: number
  awaiting_confirmation: number
  adoption_percent: number | null
  automation_score: number | null
  scored?: boolean
  why_unscored?: string
  cost_measurable?: boolean
  monthly_cost_of_not_using: number
  automation_note: string
  manual_work_note: string
  attendance_percent: number | null
  short_day_rate: number | null
  unexplained_days: number | null
  awaiting_manager: number | null
  manager_sla_days: number | null
  leave_days: number | null
  salary_at_risk: number | null
  time_doctor: Record<string, unknown>
}
export interface TransformationLeaderEntry {
  name: string
  email: string
  department: string
  owned: number
  done: number
  blocked: number
  weighted: number
  progress: number
  blocked_days: number
  open_tasks: number
  overdue_tasks: number
  delivery_percent: number | null
  adoption_percent: number | null
  response_percent: number | null
  automation_score: number
  on_leave: boolean
  evidence: string
  why_unscored?: string
}
export interface TransformationLeaderboard {
  pushing: TransformationLeaderEntry[]
  lagging: TransformationLeaderEntry[]
  unscored: TransformationLeaderEntry[]
}
export interface TransformationBlockedItem {
  code: string
  title: string
  blocked_on: string | null
  days: number
  is_vendor: boolean
  manager_name: string
  annual_saving_bwp: number
}
export interface TransformationBehindItem {
  code: string
  title: string
  manager_name: string
  department: string
  percent: number
  month: number
  target_date: string | null
}
export interface TransformationStaffCost {
  period: string
  now: number
  target: number
  saving: number
  headcount_now: number
  headcount_target: number
  note: string
}
export interface TransformationCostOfDelay {
  monthly: number
  annual: number
  basis: string
}
export interface TransformationTimeDoctor {
  as_of: string | null
  days_old: number | null
  stale: boolean | null
  company_totals: Record<string, unknown> | null
  unmatched_people: number
}
export interface TransformationWorkforceDept {
  department: string
  headcount: number
  cost_now: number
  cost_per_hour: number
  attendance_percent: number | null
  short_days: number
  short_day_rate: number
  absence_days: number
  unexplained_days: number
  unexplained_rate: number
  unexplained_hours: number
  awaiting_manager: number
  manager_sla_days: number | null
  leave_days: number
  time_doctor: {
    people?: number
    hours_tracked?: number
    productive_hours?: number
    productive_percent?: number | null
  }
  salary_at_risk: number
}
export interface TransformationWorkforce {
  window_days: number
  time_doctor: TransformationTimeDoctor
  departments: TransformationWorkforceDept[]
  salary_at_risk_month: number
  salary_at_risk_year: number
  worst: string | null
  late_coming_note: string
  fairness_note: string
}
export interface TransformationAssignment {
  name: string
  email: string
  role: string
  is_owner: boolean
  due_date: string | null
  task_id: string | null
  task_status: 'pending' | 'in_progress' | 'done' | 'partial' | 'blocked' | 'cancelled' | null
  overdue: boolean
}
export interface TransformationInitiative {
  code: string
  title: string
  plain_summary: string
  track: string
  track_label: string
  month: number
  department: string
  manager_name: string
  manager_email: string
  status: 'not_started' | 'in_progress' | 'blocked' | 'done'
  status_label: string
  percent: number
  target_date: string | null
  annual_saving_bwp: number
  fte_released: number
  blocked_on: string | null
  is_vendor: boolean
  improves_customer_service: boolean
  assignments: TransformationAssignment[]
}
export interface TransformationAi {
  narrative: string
  source: string
  judge: string
  judge_source: string
  note: string
}
// The "March of Progress" strip (transformation/evolution.py). Optional on
// the type — an older cached PulseSnapshot payload (dict stored before this
// field existed) won't carry it, and EvolutionStrip must render nothing
// rather than crash when it's missing.
export interface TransformationStage {
  index: number
  key: string
  from_percent: number
  name: string
  nickname: string
  caption: string
  tagline: string
  reached: boolean
  is_current: boolean
}
export interface TransformationEvolution {
  percent: number
  current_index: number
  percent_into_stage: number
  points_to_next: number
  is_final: boolean
  headline: string
  current: TransformationStage
  next: TransformationStage | null
  stages: TransformationStage[]
}

export interface TransformationBoard {
  headline: string
  clock: TransformationClock
  overall_percent: number
  time_percent: number
  months: TransformationMonth[]
  departments: TransformationDept[]
  leaderboard: TransformationLeaderboard
  blocked: TransformationBlockedItem[]
  behind: TransformationBehindItem[]
  staff_cost: TransformationStaffCost
  cost_of_delay: TransformationCostOfDelay
  workforce: TransformationWorkforce
  initiatives: TransformationInitiative[]
  ai: TransformationAi
  evolution?: TransformationEvolution
  as_of: string
  source: string
  stale_days?: number
}
export async function getTransformationBoard(live = false): Promise<TransformationBoard> {
  return apiFetch<TransformationBoard>(`/transformation/board/${live ? '?live=1' : ''}`)
}

export interface TransformationHistoryPoint {
  date: string
  overall_percent: number
  days_remaining: number
  staff_cost_now: number
  staff_cost_target: number
  headcount_now: number
  headcount_target: number
  cost_of_delay: number
}
export interface TransformationHistoryResponse {
  points: TransformationHistoryPoint[]
}
export async function getTransformationHistory(): Promise<TransformationHistoryResponse> {
  return apiFetch<TransformationHistoryResponse>('/transformation/history/')
}

export interface TransformationProgressUpdate {
  percent?: number
  status?: 'not_started' | 'in_progress' | 'blocked' | 'done'
  note?: string
  blocked_on?: string
}
export interface TransformationProgressResult {
  code: string
  percent: number
  status: string
  blocked_on: string | null
}
export async function setTransformationProgress(
  code: string,
  payload: TransformationProgressUpdate,
): Promise<TransformationProgressResult> {
  return apiFetch<TransformationProgressResult>(
    `/transformation/initiatives/${encodeURIComponent(code)}/progress/`,
    { method: 'POST', body: JSON.stringify(payload) },
  )
}

// Who can be put on a step — active Omni logins only (transformation/assign.py
// assignable_people()). A typed-in address is refused server-side; the picker
// only offers names from this list so that refusal should never fire from the
// UI's own suggestions.
export interface TransformationPerson {
  email: string
  name: string
  username: string
  department: string
  on_payroll: boolean
}
export async function getTransformationPeople(): Promise<{ people: TransformationPerson[] }> {
  return apiFetch<{ people: TransformationPerson[] }>('/transformation/people/')
}

export interface TransformationAssignPayload {
  email: string
  due_date?: string
  role?: string
  is_owner?: boolean
  note?: string
}
export interface TransformationAssignResult {
  code: string
  assigned: { name: string; email: string; due_date: string | null; task_id: string | null }
  assignments: TransformationAssignment[]
}
export async function assignToInitiative(
  code: string,
  payload: TransformationAssignPayload,
): Promise<TransformationAssignResult> {
  return apiFetch<TransformationAssignResult>(
    `/transformation/initiatives/${encodeURIComponent(code)}/assign/`,
    { method: 'POST', body: JSON.stringify(payload) },
  )
}

export interface TransformationUnassignResult {
  code: string
  assignments: TransformationAssignment[]
}
export async function unassignFromInitiative(
  code: string,
  email: string,
): Promise<TransformationUnassignResult> {
  return apiFetch<TransformationUnassignResult>(
    `/transformation/initiatives/${encodeURIComponent(code)}/assign/`,
    { method: 'DELETE', body: JSON.stringify({ email }) },
  )
}

export async function getGeneralLedger(
  account: string,
  from: string,
  to: string,
  company?: string | null,
): Promise<GeneralLedgerReport> {
  return apiFetch<GeneralLedgerReport>(
    `/reports/general-ledger/${_qs({ account, from, to, company: company ?? undefined })}`
  )
}

/**
 * Pull the full GL dump as a single CSV string (in-memory) — used by
 * the GL page's "Extract all → XLSX" path. Fetches the streamed CSV
 * and returns it as text so SheetJS can convert it to .xlsx.
 */
export async function fetchGeneralLedgerExtractAllCsv(
  from: string,
  to: string,
  company?: string | null,
): Promise<string> {
  const url = `${API_BASE}/reports/general-ledger/extract-all/${_qs({
    from, to, company: company ?? undefined,
  })}`
  const res = await apiFetchBinary(url, { headers: {} })
  if (!res.ok) {
    const txt = await res.text().catch(() => '')
    throw new Error(`Extract failed (${res.status}): ${txt.slice(0, 200)}`)
  }
  return await res.text()
}

/**
 * Stream the full GL dump (every account, every posted JE line in window)
 * as CSV. Browser triggers a file download via Blob URL.
 *
 * CFO directive 2026-05-21: "Extract all" on the GL page.
 */
export async function downloadGeneralLedgerExtractAll(
  from: string,
  to: string,
  company?: string | null,
): Promise<void> {
  const url = `${API_BASE}/reports/general-ledger/extract-all/${_qs({
    from, to, company: company ?? undefined,
  })}`
  const res = await apiFetchBinary(url, { headers: {} })
  if (!res.ok) {
    const txt = await res.text().catch(() => '')
    throw new Error(`Extract failed (${res.status}): ${txt.slice(0, 200)}`)
  }
  const blob = await res.blob()
  // Pull filename from Content-Disposition if present.
  let filename = `general_ledger_all_${from}_to_${to}.csv`
  const cd = res.headers.get('content-disposition') || ''
  const m = cd.match(/filename="([^"]+)"/)
  if (m) filename = m[1]
  const objectUrl = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = objectUrl
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(objectUrl), 30000)
}

// ─── Companies (subsidiaries) ──────────────────────────────────────────────────
//
// CFO directive 2026-05-22: backend now filters /companies/ to ONLY the
// entities the caller has been granted on UserCompanyAccess. Superusers
// / administrators / CFO still see everything. The new /me/companies/
// endpoint is preferred because it ALSO returns the per-company write
// flag, which the frontend needs to gate upload / approve / delete UIs.

export async function getCompanies(params?: {
  is_active?: string
  search?: string
}): Promise<PaginatedResponse<Company>> {
  const qs = new URLSearchParams(params as Record<string, string>).toString()
  return apiFetch<PaginatedResponse<Company>>(`/companies/${qs ? `?${qs}` : ''}`)
}

export interface MeCompany {
  id: string
  code: string
  name: string
  base_currency: string
  is_active: boolean
  can_write: boolean
}

export interface MeCompaniesResponse {
  unrestricted: boolean
  companies: MeCompany[]
}

export async function getMyCompanies(): Promise<MeCompaniesResponse> {
  return apiFetch<MeCompaniesResponse>('/me/companies/')
}

// ─── Monthly payroll pack + control check (CFO 2026-08-24) ──────────────────
// (period list reuses the existing getPayrollPeriods() further down this file)

export interface MonthlyPackTotals {
  headcount: number
  gross: string; paye: string; net: string; ctc: string
  basic: string; commission: string; incentive: string; bonus: string; allowances: string
  by_component: Record<string, string>
}
export interface IncentiveReconRow { name: string; dept: string; amount: string; flag: string; note: string }
export interface AtrCheckRow { person: string; change: string; authority: string; kind: string; flag: string; note: string }
export interface MoveRow {
  name: string; dept: string
  basic_prior: string; basic_cur: string; basic_change: string
  gross_prior: string; gross_cur: string; gross_change: string
}
export interface MonthlyPack {
  meta: { company: string; company_id: string; period: string; prior_period: string | null; period_start: string | null }
  totals: { current: MonthlyPackTotals; prior: MonthlyPackTotals | null }
  incentive_recon: {
    rows: IncentiveReconRow[]
    lumps: { name: string; amount: string; status: string }[]
    not_paid: { name: string; amount: string; req: string; processed: boolean }[]
    pay_total: string; mod_approved_total: string; mod_processed_total: string
    mod_pending_total: string; mod_rejected_total: string
  }
  atr_check: AtrCheckRow[]
  major_changes: {
    joiners: { name: string; dept: string; basic: string }[]
    leavers: { name: string; dept: string; basic: string }[]
    moves: MoveRow[]
  }
}

export async function getMonthlyPayrollPack(period: string, company: string): Promise<MonthlyPack> {
  return apiFetch<MonthlyPack>(`/payroll/monthly-pack/${_qs({ period, company })}`)
}

// Backlog release (CFO payroll.docx §5, 2026-09-16). The automatic feeds may
// only write into the CURRENT month, so an earlier month's approved incentives
// and commissions wait for Finance to look at them and release them on purpose.
export interface BacklogRow {
  employee_id: string
  employee_name: string
  employee_number: string
  company: string
  amount: string
  source_count: number
  already_fed: boolean
  already_settled: boolean
  duplicate_reason: string
  releasable: boolean
}
export interface BacklogPreview {
  source_period: string
  kind: 'incentive' | 'commission'
  target_period: string
  rows: BacklogRow[]
  releasable_count: number
  releasable_total: string
  held_count: number
  /** Non-empty when the current month cannot take a release at all. */
  blocked: string
}
export interface BacklogReleaseResult {
  status: string
  source_period: string
  target_period: string
  kind: string
  count: number
  total: string
  released: { employee: string; amount: string }[]
}

export async function getPayrollBacklog(
  period: string, kind: 'incentive' | 'commission'): Promise<BacklogPreview> {
  return apiFetch<BacklogPreview>(`/payroll/backlog/${_qs({ period, kind })}`)
}

export async function releasePayrollBacklog(
  period: string, kind: 'incentive' | 'commission',
  employeeIds: string[]): Promise<BacklogReleaseResult> {
  return apiFetch<BacklogReleaseResult>('/payroll/backlog/release/', {
    method: 'POST',
    body: JSON.stringify({ period, kind, employee_ids: employeeIds }),
  })
}

export async function downloadMonthlyPayrollPack(period: string, company: string): Promise<void> {
  const url = `${API_BASE}/payroll/monthly-pack/export/${_qs({ period, company })}`
  const res = await apiFetchBinary(url, { headers: {} })
  if (!res.ok) {
    const txt = await res.text().catch(() => '')
    throw new Error(`Export failed (${res.status}): ${txt.slice(0, 200)}`)
  }
  const blob = await res.blob()
  let filename = `payroll_pack_${period}.xlsx`
  const cd = res.headers.get('content-disposition') || ''
  const m = cd.match(/filename="([^"]+)"/)
  if (m) filename = m[1]
  const objectUrl = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = objectUrl
  a.download = filename
  document.body.appendChild(a); a.click(); a.remove()
  setTimeout(() => URL.revokeObjectURL(objectUrl), 30000)
}

// ─── IFRS 17 exception register (CFO directive 2026-08-26) ─────────────────

export async function downloadIfrs17ExceptionReport(year = 'FY2026'): Promise<void> {
  const url = `${API_BASE}/ifrs17/exceptions/export/xlsx/${_qs({ year })}`
  const res = await apiFetchBinary(url, { headers: {} })
  if (!res.ok) {
    const txt = await res.text().catch(() => '')
    throw new Error(`Export failed (${res.status}): ${txt.slice(0, 200)}`)
  }
  const blob = await res.blob()
  let filename = `IFRS17-Exception-Report-${year}.xlsx`
  const cd = res.headers.get('content-disposition') || ''
  const m = cd.match(/filename="([^"]+)"/)
  if (m) filename = m[1]
  const objectUrl = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = objectUrl
  a.download = filename
  document.body.appendChild(a); a.click(); a.remove()
  setTimeout(() => URL.revokeObjectURL(objectUrl), 30000)
}

// ─── FNB proof of payment (CFO directive 2026-08-26) ───────────────────────

export async function downloadFnbProofPdf(id: string, reference = 'proof'): Promise<void> {
  const res = await apiFetchBinary(`${API_BASE}/fnb/proofs/${id}/pdf/`, { headers: {} })
  if (!res.ok) {
    const txt = await res.text().catch(() => '')
    throw new Error(`Download failed (${res.status}): ${txt.slice(0, 200)}`)
  }
  const blob = await res.blob()
  let filename = `FNB-POP-${reference.replace(/\//g, '-')}.pdf`
  const cd = res.headers.get('content-disposition') || ''
  const m = cd.match(/filename="([^"]+)"/)
  if (m) filename = m[1]
  const objectUrl = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = objectUrl
  a.download = filename
  document.body.appendChild(a); a.click(); a.remove()
  setTimeout(() => URL.revokeObjectURL(objectUrl), 30000)
}

// ─── User-company access admin (CFO directive 2026-05-22) ──────────────────

export interface UserAccessMatrixUser {
  username: string
  email: string
  full_name: string
  title: string
  is_superuser: boolean
}

export interface UserAccessMatrixCompany {
  id: string
  code: string
  name: string
}

export interface UserAccessMatrix {
  users: UserAccessMatrixUser[]
  companies: UserAccessMatrixCompany[]
  grants: Record<string, Record<string, { can_view: boolean; can_write: boolean }>>
}

export async function getUserAccessMatrix(): Promise<UserAccessMatrix> {
  return apiFetch<UserAccessMatrix>('/admin/user-company-access/matrix/')
}

export interface BulkGrantResult {
  action: 'grant' | 'revoke'
  users_resolved: string[]
  companies_resolved: string[]
  users_unresolved: string[]
  companies_unresolved: string[]
  created: number
  updated: number
  revoked: number
}

export async function bulkUserCompanyAccess(args: {
  users: string[]
  companies: string[]
  action: 'grant' | 'revoke'
  can_view?: boolean
  can_write?: boolean
}): Promise<BulkGrantResult> {
  return apiFetch<BulkGrantResult>('/admin/user-company-access/bulk/', {
    method: 'POST',
    body: JSON.stringify(args),
  })
}

export interface AccessUploadReport {
  rows_processed: number
  created: number
  updated: number
  revoked: number
  report: Array<{
    row: number
    status: 'created' | 'updated' | 'revoked' | 'skipped'
    user?: string
    company?: string
    access?: string
    reason?: string
    raw?: Record<string, string>
  }>
}

export async function uploadUserAccessFile(file: File): Promise<AccessUploadReport> {
  const form = new FormData()
  form.append('file', file)
  const url = `${API_BASE}/admin/user-company-access/upload/`
  const res = await apiFetchBinary(url, {
    method: 'POST',
    body:   form,
    headers: {},
  })
  if (!res.ok) {
    const txt = await res.text().catch(() => '')
    throw new Error(`Upload failed (${res.status}): ${txt.slice(0, 200)}`)
  }
  return await res.json()
}

export function userAccessTemplateUrl(): string {
  return `${API_BASE}/admin/user-company-access/template/`
}

// ─── User Titles (CFO directive 2026-05-22) ─────────────────────────────────

export interface TitleChoice { value: string; label: string }
export interface TitleUserRow {
  username: string
  email: string
  full_name: string
  title: string
  is_superuser: boolean
}

export interface UserTitlesResponse {
  users:   TitleUserRow[]
  choices: TitleChoice[]
}

export interface TitleUpdateReport {
  updated: number
  skipped: number
  report: Array<{
    row: number
    status: 'updated' | 'skipped'
    username?: string
    title?: string
    reason?: string
    raw?: Record<string, string>
  }>
}

export async function getUserTitles(): Promise<UserTitlesResponse> {
  return apiFetch<UserTitlesResponse>('/admin/user-titles/')
}

export async function setUserTitle(args: {
  username?: string; email?: string; name?: string; title: string
}): Promise<TitleUpdateReport> {
  return apiFetch<TitleUpdateReport>('/admin/user-titles/', {
    method: 'POST',
    body: JSON.stringify(args),
  })
}

export async function bulkUserTitles(rows: Array<{
  username?: string; email?: string; name?: string; title: string
}>): Promise<TitleUpdateReport> {
  return apiFetch<TitleUpdateReport>('/admin/user-titles/', {
    method: 'POST',
    body: JSON.stringify({ rows }),
  })
}

export async function uploadUserTitlesFile(file: File): Promise<TitleUpdateReport> {
  const form = new FormData()
  form.append('file', file)
  const url = `${API_BASE}/admin/user-titles/upload/`
  const res = await apiFetchBinary(url, {
    method: 'POST', body: form, headers: {},
  })
  if (!res.ok) {
    const txt = await res.text().catch(() => '')
    throw new Error(`Upload failed (${res.status}): ${txt.slice(0, 200)}`)
  }
  return await res.json()
}

export function userTitlesTemplateUrl(): string {
  return `${API_BASE}/admin/user-titles/template/`
}

// ─── User Emails / Master List (CFO directive 2026-05-22) ───────────────────

export interface EmailUserRow {
  username:     string
  email:        string
  first_name:   string
  last_name:    string
  full_name:    string
  title:        string
  is_superuser: boolean
  last_login:   string | null
}

export interface UserEmailsResponse { users: EmailUserRow[] }

export interface EmailUpdateReport {
  updated: number
  created: number
  skipped: number
  report:  Array<{
    row: number
    status: 'updated' | 'created' | 'skipped' | 'noop'
    username?: string
    email?:    string
    reason?:   string
    raw?:      Record<string, string>
  }>
}

export async function getUserEmails(): Promise<UserEmailsResponse> {
  return apiFetch<UserEmailsResponse>('/admin/user-emails/')
}

export async function setUserEmail(args: {
  username?: string; email?: string; name?: string
  first_name?: string; last_name?: string
  create_if_missing?: boolean
}): Promise<EmailUpdateReport> {
  return apiFetch<EmailUpdateReport>('/admin/user-emails/', {
    method: 'POST',
    body: JSON.stringify(args),
  })
}

export async function bulkUserEmails(rows: Array<{
  username?: string; email?: string; name?: string
  first_name?: string; last_name?: string
}>, create_if_missing = true): Promise<EmailUpdateReport> {
  return apiFetch<EmailUpdateReport>('/admin/user-emails/', {
    method: 'POST',
    body: JSON.stringify({ rows, create_if_missing }),
  })
}

export async function uploadUserEmailsFile(file: File): Promise<EmailUpdateReport> {
  const form = new FormData()
  form.append('file', file)
  const url = `${API_BASE}/admin/user-emails/upload/`
  const res = await apiFetchBinary(url, {
    method: 'POST', body: form, headers: {},
  })
  if (!res.ok) {
    const txt = await res.text().catch(() => '')
    throw new Error(`Upload failed (${res.status}): ${txt.slice(0, 200)}`)
  }
  return await res.json()
}

export function userEmailsTemplateUrl(): string {
  return `${API_BASE}/admin/user-emails/template/`
}

export async function getCompany(id: string): Promise<Company> {
  return apiFetch<Company>(`/companies/${id}/`)
}

export async function createCompany(data: CreateCompanyInput): Promise<Company> {
  return apiFetch<Company>('/companies/', {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

export async function updateCompany(id: string, data: Partial<CreateCompanyInput>): Promise<Company> {
  return apiFetch<Company>(`/companies/${id}/`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  })
}

// ─── Currencies, Tax Rates, Fiscal Periods ────────────────────────────────────

export async function getCurrencies(): Promise<PaginatedResponse<Currency>> {
  return apiFetch<PaginatedResponse<Currency>>('/currencies/')
}

// Latest APPROVED FX rate for a pair — powers the New PO form auto-fill so a
// foreign-currency PO defaults to the real BoB mid-rate, not 1.0 (bug c9355408).
// rate is null when no approved rate exists yet (caller keeps manual entry).
export async function getLatestExchangeRate(
  fromCurrency: string, toCurrency = 'BWP',
): Promise<{ rate: string | null; effective_date: string | null; source: string | null }> {
  const qs = new URLSearchParams({ from_currency: fromCurrency, to_currency: toCurrency })
  return apiFetch(`/exchange-rates/latest/?${qs.toString()}`)
}

export async function getTaxRates(): Promise<PaginatedResponse<TaxRate>> {
  return apiFetch<PaginatedResponse<TaxRate>>('/tax-rates/')
}

export interface CFODashboard {
  as_of: string
  user: { username: string; is_approver: boolean; is_admin: boolean; title: string }
  pending_approvals: {
    count_total: number
    awaiting_me_je: Array<{ id: string; entry_number: string; description: string; total_amount?: string }>
    awaiting_my_second_import: Array<{ id: string; source: string; rows_total: number }>
  }
  related_party_this_week: Array<{ id: string; entry_number: string; description: string; entry_date: string }>
  overdue_invoices: { count: number; total_amount: string }
  kpis: {
    cash_position_bwp: string
    ar_outstanding_bwp: string
    ap_outstanding_bwp: string
    mtd_revenue_bwp: string
    mtd_expenses_bwp: string
    mtd_net_profit_bwp: string
    open_recurring_due: number
  }
  audit_recent: Array<{ id: string; table_name: string; record_id: string; action: string; description: string; created_at: string; user__username: string }>
  asset_signoffs: { open: any[]; overdue_count: number; next_count_due: string | null }
  po_operational_authorizations?: {
    count: number
    fm_only: number
    total_bwp: string
    items: Array<{ po_number: string; supplier: string; department: string; total_bwp: string; fm_only: boolean }>
  }
}

export async function getCFODashboard(): Promise<CFODashboard> {
  return apiFetch<CFODashboard>('/dashboard/cfo/')
}

/**
 * Morning Bank Balances (CFO 2026-09-20) — read-only, GET only, forever.
 * Server-gated on CanViewFinancials; callers self-hide on 403. Shape and the
 * rules behind it: lib/bankBalances.ts + BALANCES_CONTRACT.md.
 */
export async function getBankBalances(): Promise<BankBalances> {
  return apiFetch<BankBalances>('/banking/balances/')
}

/**
 * Audit Pack — fetch the JSON summary, or stream/download the PDF.
 */
export async function getAuditPackJson(from: string, to: string, company?: string | null): Promise<any> {
  return apiFetch(`/reports/audit-pack/${_qs({ from, to, company: company ?? undefined })}`)
}

export async function downloadAuditPackPdf(from: string, to: string, company?: string | null): Promise<string> {
  const url = `${API_BASE}/reports/audit-pack/pdf/${_qs({ from, to, company: company ?? undefined })}`
  const res = await fetch(url, { headers: {
} })
  if (!res.ok) {
    let msg = `Audit pack PDF failed: ${res.status}`
    try { const j = await res.json(); if (j?.error) msg = j.error } catch {}
    throw new Error(msg)
  }
  const blob = await res.blob()
  const objectUrl = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = objectUrl
  a.download = `audit_pack_${from}_to_${to}.pdf`
  document.body.appendChild(a); a.click(); document.body.removeChild(a)
  setTimeout(() => URL.revokeObjectURL(objectUrl), 30000)
  return a.download
}

export interface PeriodCloseChecks {
  period_name: string
  status: string
  ready_to_close: boolean
  issues: Record<string, string[]>
}

export async function getPeriodCloseChecks(id: string): Promise<PeriodCloseChecks> {
  return apiFetch<PeriodCloseChecks>(`/fiscal-periods/${id}/close-checks/`)
}

export async function closePeriod(id: string, overridePassword: string, reviewerSignoff = ''): Promise<FiscalPeriod> {
  return apiFetch<FiscalPeriod>(`/fiscal-periods/${id}/close/`, {
    method: 'POST',
    body: JSON.stringify({
      override_password: overridePassword,
      reviewer_signoff:  reviewerSignoff,
    }),
  })
}

export async function reopenPeriod(id: string, overridePassword: string, reason: string): Promise<FiscalPeriod> {
  return apiFetch<FiscalPeriod>(`/fiscal-periods/${id}/reopen/`, {
    method: 'POST',
    body: JSON.stringify({
      override_password: overridePassword,
      reason,
    }),
  })
}

export async function getFiscalPeriods(
  params?: { status?: string; start_date_gte?: string; company?: string; fiscal_year?: string; page_size?: string },
): Promise<PaginatedResponse<FiscalPeriod>> {
  const q = new URLSearchParams()
  if (params?.status) q.set('status', params.status)
  // Default cutoff = FY25 start. Pass '' to disable.
  const startGte = params?.start_date_gte ?? '2024-07-01'
  if (startGte) q.set('start_date_gte', startGte)
  if (params?.company) q.set('company', params.company)
  if (params?.fiscal_year) q.set('fiscal_year', params.fiscal_year)
  q.set('page_size', params?.page_size ?? '500')
  const qs = q.toString()
  return apiFetch<PaginatedResponse<FiscalPeriod>>(`/fiscal-periods/${qs ? '?' + qs : ''}`)
}

// ─── Period Management (CFO directive 2026-05-28) ───────────────────────────

export async function signPeriodLock(
  id: string, role: 'cfo' | 'fm', reason = '',
): Promise<{ period_name: string; status: string; cfo_signed: boolean; fm_signed: boolean; fully_locked: boolean; lock_reason: string }> {
  return apiFetch(`/fiscal-periods/${id}/sign-lock/`, {
    method: 'POST',
    body: JSON.stringify({ role, reason }),
  })
}

export async function clearPeriodLock(
  id: string, role: 'cfo' | 'fm',
): Promise<{ period_name: string; status: string; cfo_signed: boolean; fm_signed: boolean; fully_locked: boolean }> {
  return apiFetch(`/fiscal-periods/${id}/clear-lock/`, {
    method: 'POST',
    body: JSON.stringify({ role }),
  })
}

export async function getPeriodAuditLog(limit = 20): Promise<PeriodAuditEntry[]> {
  return apiFetch<PeriodAuditEntry[]>(`/fiscal-periods/audit-log/?limit=${limit}`)
}

// ─── Dashboard ────────────────────────────────────────────────────────────────

function getFyStartDate(): string {
  const now = new Date()
  const year = now.getFullYear()
  const month = now.getMonth() + 1 // 1-based
  // Botswana fiscal year: April to March
  // But using July 1 as specified
  if (month >= 7) {
    return `${year}-07-01`
  } else {
    return `${year - 1}-07-01`
  }
}

function getFyEndDate(): string {
  const now = new Date()
  const year = now.getFullYear()
  const month = now.getMonth() + 1
  if (month >= 7) {
    return `${year + 1}-06-30`
  } else {
    return `${year}-06-30`
  }
}

function todayStr(): string {
  return localYmd(new Date())
}

const emptyPaginated = <T>(): PaginatedResponse<T> => ({
  count: 0,
  next: null,
  previous: null,
  results: [],
})

const emptyCashPosition = (): CashPositionReport => ({
  as_of: todayStr(),
  accounts: [],
  total_bwp: '0.00',
})

const emptyAging = (): AgingReport => ({
  as_of: todayStr(),
  customers: [],
  totals: { total_outstanding: '0.00' },
})

const emptyPL = (): ProfitLossReport => ({
  from_date: getFyStartDate(),
  to_date: todayStr(),
  revenue: { accounts: [], total: '0.00' },
  cost_of_insurance: { accounts: [], total: '0.00' },
  gross_result: '0.00',
  operating_expenses: { accounts: [], total: '0.00' },
  net_profit: '0.00',
  is_profit: true,
})

export async function getDashboardData(
  options?: { company?: string | null; dateFrom?: string; dateTo?: string }
): Promise<DashboardData> {
  const fyStart = options?.dateFrom || getFyStartDate()
  const fyEnd = options?.dateTo || getFyEndDate()
  const today = options?.dateTo || todayStr()
  const co = options?.company ?? undefined

  const [cashPosition, arAging, apAging, recentEntries, profitLoss, maProfitLoss, balanceSheet, overdueAr, overdueAp, receivablesSummary, draftJes, unmatchedBankLines] =
    await Promise.allSettled([
      // Cash tile is now period-aware — `fyEnd` is the selected period's
      // closing date, so FY25 button → 2025-06-30 balance (12.88M), FY26
      // button → 2026-06-30 (projected, may equal Mar26-9M close until
      // post-Mar26 data is imported).
      getCashPosition(co, fyEnd),
      getArAging(today, co),
      getApAging(today, co),
      getJournalEntries({ page_size: '10', ordering: '-entry_date', ...(co ? { company: co } : {}) }),
      getProfitLoss(fyStart, fyEnd, co),
      getMAProfitLoss(fyStart, fyEnd, co),
      getBalanceSheet(today, co),
      getInvoices({ status: 'overdue', invoice_type: 'customer_invoice', page_size: '50', ...(co ? { company: co } : {}) }),
      getInvoices({ status: 'overdue', invoice_type: 'vendor_bill', page_size: '50', ...(co ? { company: co } : {}) }),
      // CFO directive 2026-05-24 — canonical AR figure (replaces the
      // broken `current_assets − cash` heuristic). One number, used by
      // tile + BS receivables line + AR aging summary so they agree.
      getReceivablesSummary(today, co),
      // BUG-010 (Oprah QA, 2026-06-08): Action Center counters were hardcoded
      // to "—". Wire them to real counts. page_size=1 → cheap, we only read
      // `.count`. Drafts honour the company filter; bank lines are not
      // company-scoped on the viewset, so org-wide unmatched count.
      getJournalEntries({ status: 'draft', page_size: '1', ...(co ? { company: co } : {}) }),
      getBankStatementLines({ match_status: 'unmatched', page_size: '1' }),
    ])

  // External-audit follow-up 2026-05-19: previous default claimed
  // balanced=true with all zeros, indistinguishable from a real empty
  // balance sheet. Set balanced=false so the dashboard surfaces the
  // failure as "BS endpoint down" rather than "books balanced, all zero".
  const emptyBS = (): BalanceSheetReport => ({
    as_of: today,
    fiscal_year_start: fyStart,
    assets: {},
    liabilities: {},
    equity: {},
    totals: { total_assets: '0.00', liabilities_and_equity: '0.00', balanced: false },
  })

  const emptyMAPL = (): MAProfitLossReport => ({
    from_date: fyStart, to_date: fyEnd, sections: [],
    totals: {
      gross_written_premium: '0.00', premiums_ceded: '0.00', change_in_upr: '0.00',
      net_earned_premium: '0.00', gross_claims: '0.00', ri_claims_recovered: '0.00',
      subrogations_salvages: '0.00', net_claim_incurred: '0.00',
      gross_loss_ratio: '0.0000', net_loss_ratio: '0.0000',
      net_acquisition: '0.00', gross_profit: '0.00', total_other_income: '0.00',
      total_operating_expenses: '0.00', total_provisions: '0.00',
      ebitda: '0.00', depreciation: '0.00', ebit: '0.00',
      finance_cost: '0.00', pbt: '0.00', taxation: '0.00', pat: '0.00',
    },
  })

  // External-audit follow-up 2026-05-19: capture which endpoints failed
  // so the dashboard can show "endpoint down" instead of zeros that
  // look like real values.
  const errMsg = (r: PromiseSettledResult<unknown>) =>
    r.status === 'rejected'
      ? (r.reason instanceof Error ? r.reason.message : String(r.reason))
      : null

  return {
    cashPosition: cashPosition.status === 'fulfilled' ? cashPosition.value : emptyCashPosition(),
    arAging: arAging.status === 'fulfilled' ? arAging.value : emptyAging(),
    apAging: apAging.status === 'fulfilled' ? apAging.value : emptyAging(),
    recentEntries:
      recentEntries.status === 'fulfilled' ? recentEntries.value : emptyPaginated<JournalEntry>(),
    profitLoss: profitLoss.status === 'fulfilled' ? profitLoss.value : emptyPL(),
    maProfitLoss: maProfitLoss.status === 'fulfilled' ? maProfitLoss.value : emptyMAPL(),
    balanceSheet: balanceSheet.status === 'fulfilled' ? balanceSheet.value : emptyBS(),
    overdueAr: overdueAr.status === 'fulfilled' ? overdueAr.value : emptyPaginated<Invoice>(),
    overdueAp: overdueAp.status === 'fulfilled' ? overdueAp.value : emptyPaginated<Invoice>(),
    receivablesSummary:
      receivablesSummary.status === 'fulfilled' ? receivablesSummary.value : null,
    draftJeCount:
      draftJes.status === 'fulfilled' ? (draftJes.value.count || 0) : 0,
    unmatchedBankCount:
      unmatchedBankLines.status === 'fulfilled' ? (unmatchedBankLines.value.count || 0) : 0,
    endpoint_errors: {
      cashPosition:  errMsg(cashPosition),
      arAging:       errMsg(arAging),
      apAging:       errMsg(apAging),
      recentEntries: errMsg(recentEntries),
      profitLoss:    errMsg(profitLoss),
      maProfitLoss:  errMsg(maProfitLoss),
      balanceSheet:  errMsg(balanceSheet),
      overdueAr:     errMsg(overdueAr),
      overdueAp:     errMsg(overdueAp),
      receivablesSummary: errMsg(receivablesSummary),
      draftJeCount:  errMsg(draftJes),
      unmatchedBankCount: errMsg(unmatchedBankLines),
    },
  }
}

// ─── Document Upload API ─────────────────────────────────────────────────────

export interface DocumentUploadRecord {
  id: string
  status: string
  document_type: string | null
  classification_confidence: string | null
  extracted_data: Record<string, any> | null
  suggested_action: { action_type: string; details?: Record<string, any> } | null
  ai_explanation: string | null
  error_message: string | null
  resulting_object_id: string | null
  resulting_object_kind: string | null  // 'invoice' | 'payment' | null
  extraction_method: string
  original_filename: string
  file_size: number
  created_at: string
}

export async function uploadDocument(file: File): Promise<DocumentUploadRecord> {
  const form = new FormData()
  form.append('file', file)
  const res = await apiFetchBinary(`${API_BASE}/documents/upload/`, {
    method: 'POST',
    headers: {
},
    body: form,
  })
  if (!res.ok) throw new Error(`Upload failed: ${res.status}`)
  return res.json()
}

export async function getDocuments(
  params: Record<string, string> = {}
): Promise<PaginatedResponse<DocumentUploadRecord>> {
  const qs = new URLSearchParams(params).toString()
  return apiFetch<PaginatedResponse<DocumentUploadRecord>>(
    `/documents/${qs ? `?${qs}` : ''}`
  )
}

export async function getDocument(id: string): Promise<DocumentUploadRecord> {
  return apiFetch<DocumentUploadRecord>(`/documents/${id}/`)
}

export interface DocumentConfirmPayload {
  edited_data?: Record<string, any>
  edited_action_type?: string
  edited_document_type?: string
}

export async function confirmDocument(
  id: string,
  edits?: DocumentConfirmPayload,
): Promise<DocumentUploadRecord> {
  return apiFetch<DocumentUploadRecord>(`/documents/${id}/confirm/`, {
    method: 'POST',
    body: edits ? JSON.stringify(edits) : undefined,
  })
}

export async function rejectDocument(id: string): Promise<DocumentUploadRecord> {
  return apiFetch<DocumentUploadRecord>(`/documents/${id}/reject/`, {
    method: 'POST',
  })
}

export async function deleteDocument(id: string): Promise<void> {
  const res = await apiFetchBinary(`${API_BASE}/documents/${id}/`, {
    method: 'DELETE',
    headers: {
},
  })
  if (!res.ok && res.status !== 204) {
    throw new Error(`Delete failed: ${res.status}`)
  }
}

// ─── Budget Module ──────────────────────────────────────────────────────────

export interface Budget {
  id: string
  fiscal_period: string
  period_name: string
  period_start: string
  period_end: string
  department: string
  department_display: string
  status: string
  description: string
  created_by_name: string
  line_count: number
  total_revenue: string
  total_expenses: string
  created_at: string
  updated_at: string
}

export interface BudgetLine {
  id: string
  account_code: string
  account_name: string
  account_type: string
  sub_type: string
  amount: string
  notes: string
}

export interface BudgetDetail extends Budget {
  lines: BudgetLine[]
  approved_by_name: string | null
  approved_at: string | null
}

export async function getBudgets(
  params?: Record<string, string>
): Promise<PaginatedResponse<Budget>> {
  const query = new URLSearchParams(params).toString()
  return apiFetch<PaginatedResponse<Budget>>(`/budgets/${query ? `?${query}` : ''}`)
}

export async function getBudget(id: string): Promise<BudgetDetail> {
  return apiFetch<BudgetDetail>(`/budgets/${id}/`)
}

export async function createBudget(data: {
  fiscal_period: string
  department: string
  description?: string
  lines?: { account: string; amount: string; notes?: string }[]
}): Promise<BudgetDetail> {
  return apiFetch<BudgetDetail>('/budgets/', {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

export async function approveBudget(id: string): Promise<BudgetDetail> {
  return apiFetch<BudgetDetail>(`/budgets/${id}/approve/`, {
    method: 'POST',
  })
}

export async function addBudgetLine(
  budgetId: string,
  data: { account: string; amount: string; notes?: string }
): Promise<BudgetLine> {
  return apiFetch<BudgetLine>(`/budgets/${budgetId}/add-line/`, {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

export interface BudgetSaveResult extends BudgetDetail {
  applied: number
  errors: { row: number; error: string }[]
  message: string
}

/** Bulk grid save: upsert the budget for (period, department) and replace its
 *  lines. Each line carries `account` (id) OR `account_code`. */
export async function setBudgetLines(data: {
  fiscal_period: string
  department: string
  description?: string
  lines: { account?: string; account_code?: string; amount: string; notes?: string }[]
}): Promise<BudgetSaveResult> {
  return apiFetch<BudgetSaveResult>('/budgets/set-lines/', {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

/** Unlock an approved (locked) budget so Finance can revise it. Audited. */
export async function reviseBudget(id: string): Promise<BudgetDetail> {
  return apiFetch<BudgetDetail>(`/budgets/${id}/revise/`, { method: 'POST' })
}

/** Upload a CSV/XLSX budget (columns: account_code | amount [| notes]).
 *  Uses apiFetchBinary so it gets the SAME robust SSO-bearer/DRF-token auth as
 *  every other upload — the raw-fetch version 401/403'd when the SSO token
 *  wasn't warm yet (bug 34ef9acd: "upload button not working"). */
export async function uploadBudget(
  fiscalPeriod: string, department: string, file: File
): Promise<BudgetSaveResult> {
  const fd = new FormData()
  fd.append('file', file)
  fd.append('fiscal_period', fiscalPeriod)
  fd.append('department', department)
  const res = await apiFetchBinary(`${API_BASE}/budgets/upload/`, {
    method: 'POST', headers: {}, body: fd,
  })
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(body.detail || `Upload failed (HTTP ${res.status})`)
  return body as BudgetSaveResult
}

// ─── Phase 1 Reports ────────────────────────────────────────────────────────

export interface BudgetVsActualReport {
  from_date: string
  to_date: string
  department: string
  periods_covered: string[]
  revenue: {
    accounts: {
      code: string; name: string; account_type: string; sub_type: string
      budget: string; actual: string; variance: string; variance_pct: string; favorable: boolean
    }[]
    total_budget: string; total_actual: string; total_variance: string
  }
  expenses: {
    accounts: {
      code: string; name: string; account_type: string; sub_type: string
      budget: string; actual: string; variance: string; variance_pct: string; favorable: boolean
    }[]
    total_budget: string; total_actual: string; total_variance: string
  }
  net_result: {
    budget: string; actual: string; variance: string; favorable: boolean
  }
}

export interface VATReturnReport {
  from_date: string
  to_date: string
  output_vat: {
    invoices: {
      invoice_number: string; contact_name: string; issue_date: string
      net_amount: string; vat_amount: string; total_amount: string
    }[]
    total_sales: string; total_vat: string
  }
  credit_notes: {
    invoices: {
      invoice_number: string; contact_name: string; issue_date: string
      net_amount: string; vat_amount: string; total_amount: string
    }[]
    total_sales: string; total_vat: string
  }
  input_vat: {
    invoices: {
      invoice_number: string; contact_name: string; issue_date: string
      net_amount: string; vat_amount: string; total_amount: string
    }[]
    total_purchases: string; total_vat: string
  }
  // Reverse charge on imported remote services (VAT Amendment Act No.16 of
  // 2025, effective 1 June 2026) — filed as two separate self-assessed
  // lines, distinct from the domestic output_vat/input_vat above.
  reverse_charge_vat: {
    entries: {
      vendor: string; category: string; category_label: string
      invoice_date: string; bwp_amount: string
      output_vat: string; input_vat_recoverable: string; net_vat_cost: string
    }[]
    total_output_vat: string
    total_input_vat_recoverable: string
    total_net_vat_cost: string
  }
  summary: {
    output_vat: string; credit_note_vat: string; adjusted_output_vat: string
    input_vat: string; net_vat: string; vat_payable: boolean; status_label: string
    reverse_charge_output_vat: string; reverse_charge_input_vat: string
    reverse_charge_net_vat_cost: string
  }
}

export interface ExpenseAnalysisReport {
  from_date: string
  to_date: string
  categories: {
    category: string; category_label: string
    accounts: { code: string; name: string; amount: string }[]
    total: string; previous_total: string; change: string
    change_pct: string; pct_of_total: string
  }[]
  totals: {
    current_total: string; previous_total: string
    previous_from: string; previous_to: string
    change: string; change_pct: string
  }
}

export interface ManagementPackReport {
  from_date: string
  to_date: string
  profit_loss: {
    total_revenue: string; cost_of_insurance: string; gross_result: string
    operating_expenses: string; net_profit: string; is_profit: boolean
  }
  balance_sheet: {
    total_assets: string; total_liabilities: string; total_equity: string; balanced: boolean
  }
  cash_position: {
    total_cash_bwp: string
    accounts: { account_code: string; account_name: string; currency: string; balance_bwp: string }[]
  }
  insurance_kpis: {
    earned_premium: string; claims_incurred: string; operating_expenses: string
    loss_ratio: string; expense_ratio: string; combined_ratio: string
    profit_margin: string; underwriting_result: string
  }
  budget_comparison: {
    revenue_budget: string; revenue_actual: string; revenue_variance: string
    expense_budget: string; expense_actual: string; expense_variance: string
    net_budget: string; net_actual: string; net_variance: string
  } | null
}

export async function getBudgetVsActual(
  from: string, to: string, department?: string
): Promise<BudgetVsActualReport> {
  const dept = department ? `&department=${department}` : ''
  return apiFetch<BudgetVsActualReport>(
    `/reports/budget-vs-actual/?from=${from}&to=${to}${dept}`
  )
}

export async function getVATReturn(
  from: string, to: string
): Promise<VATReturnReport> {
  return apiFetch<VATReturnReport>(`/reports/vat-return/?from=${from}&to=${to}`)
}

// ─── Reverse-Charge VAT (imported remote services) ─────────────────────────
// VAT Amendment Act No.16 of 2025, effective 1 June 2026. Capture page:
// /reverse-charge. Feeds the two extra self-assessed lines on the VAT
// return above (report.reverse_charge_vat / report.summary.reverse_charge_*).

export type ReverseChargeCategory =
  | 'cloud' | 'ai_saas' | 'productivity' | 'design'
  | 'devtools' | 'hris' | 'email_api' | 'other'

export interface ReverseChargeEntryRow {
  id: string
  vendor: string
  category: ReverseChargeCategory
  category_label: string
  invoice_date: string
  foreign_currency: string
  foreign_amount: string
  bwp_amount: string
  reverse_charge_applies: boolean
  // Server-computed — always ignore any locally-held value once a save
  // round-trips; these are the authoritative figures.
  output_vat: string
  input_vat_recoverable: string
  net_vat_cost: string
  note: string
  company: string | null
  company_code: string | null
  company_name: string | null
  created_by_username: string | null
  created_at: string
  updated_at: string
}

export async function getReverseChargeEntries(
  params?: Record<string, string>
): Promise<PaginatedResponse<ReverseChargeEntryRow>> {
  const query = new URLSearchParams(params).toString()
  return apiFetch<PaginatedResponse<ReverseChargeEntryRow>>(
    `/reverse-charge-entries/${query ? `?${query}` : ''}`
  )
}

export async function createReverseChargeEntry(
  data: Partial<ReverseChargeEntryRow>
): Promise<ReverseChargeEntryRow> {
  // Mirrors createPayment: the topbar's globally-selected entity rides
  // along explicitly so a POST scopes to the same company as the GETs.
  const company = data.company || selectedCompanyId() || undefined
  return apiFetch<ReverseChargeEntryRow>('/reverse-charge-entries/', {
    method: 'POST',
    body: JSON.stringify({ ...data, company }),
  })
}

export async function updateReverseChargeEntry(
  id: string, data: Partial<ReverseChargeEntryRow>
): Promise<ReverseChargeEntryRow> {
  return apiFetch<ReverseChargeEntryRow>(`/reverse-charge-entries/${id}/`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  })
}

export async function getExpenseAnalysis(
  from: string, to: string
): Promise<ExpenseAnalysisReport> {
  return apiFetch<ExpenseAnalysisReport>(`/reports/expense-analysis/?from=${from}&to=${to}`)
}

// ─── Expense Analysis v2 (CFO directive 2026-05-21) ────────────────────────

export interface ExpenseDetailCategory {
  id: string
  label: string
  codes: string[]
  amount: string
  prev_amount: string
  change: string
  change_pct: string
  by_account:  Array<{ code: string; name: string; amount: string }>
  by_supplier: Array<{ contact_id: string | null; contact_name: string; amount: string; line_count: number }>
  top_lines:   Array<{
    date: string; entry_number: string; description: string;
    account_code: string; account_name: string; contact_name: string;
    debit: string; credit: string; net: string;
  }>
}

export interface ExpenseAnalysisDetail {
  from_date: string
  to_date: string
  previous_from: string
  previous_to: string
  categories: ExpenseDetailCategory[]
  totals: {
    current_total: string
    previous_total: string
    change: string
    change_pct: string
  }
}

export async function getExpenseAnalysisDetail(
  from: string, to: string, company?: string | null,
): Promise<ExpenseAnalysisDetail> {
  return apiFetch<ExpenseAnalysisDetail>(
    `/reports/expense-analysis/detail/${_qs({ from, to, company: company ?? undefined })}`,
  )
}

export async function downloadExpenseAnalysisXlsx(
  from: string, to: string, company?: string | null,
): Promise<void> {
  const url = `${API_BASE}/reports/expense-analysis/export-xlsx/${_qs({
    from, to, company: company ?? undefined,
  })}`
  const res = await apiFetchBinary(url, { headers: {} })
  if (!res.ok) {
    const txt = await res.text().catch(() => '')
    throw new Error(`Export failed (${res.status}): ${txt.slice(0, 200)}`)
  }
  const blob = await res.blob()
  let filename = `expense_analysis_${from}_to_${to}.xlsx`
  const cd = res.headers.get('content-disposition') || ''
  const m = cd.match(/filename="([^"]+)"/)
  if (m) filename = m[1]
  const objectUrl = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = objectUrl
  a.download = filename
  document.body.appendChild(a); a.click(); a.remove()
  setTimeout(() => URL.revokeObjectURL(objectUrl), 30000)
}

export interface ExpenseAnalysisAIResult {
  ai_available: boolean
  fallback?: boolean
  reason?: string
  analysis?: string
  data_summary: unknown
  message?: string
}

export async function analyseExpenseWithAI(
  from: string, to: string, company?: string | null,
): Promise<ExpenseAnalysisAIResult> {
  return apiFetch<ExpenseAnalysisAIResult>(
    `/reports/expense-analysis/analyse/${_qs({ from, to, company: company ?? undefined })}`,
    { method: 'POST' },
  )
}

export async function getManagementPack(
  from: string, to: string, company?: string | null,
): Promise<ManagementPackReport> {
  return apiFetch<ManagementPackReport>(
    `/reports/management-pack/${_qs({ from, to, company: company ?? undefined })}`
  )
}

// ─── Financial Statements for Auditors (IFRS 17 PAA scaffold) ───────────────

export type AuditPackStatus = 'READY' | 'PARTIAL' | 'TBD'

export interface IfrsLine {
  value: string | null
  comparison: string | null
  _status: AuditPackStatus
  note?: number
}

export interface AuditPackNote {
  number: number
  title: string
  status: AuditPackStatus
  feeds_from?: string
  depends_on?: string
  // body is a structured dict whose shape varies per note. Loose typing
  // is intentional while the IFRS 17 note bodies are still maturing.
  body?: Record<string, any>
}

export interface AuditPackReport {
  meta: {
    standard: string
    reporting_entity: string
    country_of_incorporation: string
    functional_currency: string
    presentation_currency: string
    period: { start: string; end: string }
    comparison_period: { start: string; end: string }
    pack_version: string
    status: string
    notes_for_auditor: string
  }
  front_matter: {
    directors_report: { status: AuditPackStatus; body: string | null }
    directors_responsibility_statement: { status: AuditPackStatus; body: string | null }
    independent_auditors_report: {
      status: AuditPackStatus; body: string | null
      audit_firm: string | null; partner: string | null
      opinion: string | null; date_signed: string | null
    }
  }
  // Each statement is a nested record of IfrsLine objects mixed with
  // string/boolean metadata. Loose typing is intentional while the
  // structure is still in scaffold state.
  statement_of_financial_position: Record<string, any>
  statement_of_comprehensive_income: Record<string, any>
  statement_of_changes_in_equity: Record<string, any>
  statement_of_cash_flows: Record<string, any>
  notes_to_financial_statements: AuditPackNote[]
  roadmap: {
    description: string
    wireable_now: string[]
    needs_module: string[]
    requires_input: string[]
  }
}

export async function getAuditPack(
  periodEnd: string,
  periodStart?: string,
  company?: string | null,
): Promise<AuditPackReport> {
  return apiFetch<AuditPackReport>(
    `/reports/audit-pack/${_qs({
      period_end: periodEnd,
      period_start: periodStart,
      company: company ?? undefined,
    })}`,
  )
}

// ─── Petty Cash ─────────────────────────────────────────────────────────────

export type PettyCashVoucherStatus =
  | 'draft' | 'pending_approval' | 'one_signature' | 'posted' | 'reimbursed' | 'rejected'

export type PettyCashReimbursementStatus =
  | 'draft' | 'pending_fm' | 'pending_cfo' | 'posted' | 'rejected'

export interface PettyCashGLPreview {
  debit: { code: string; name: string; amount: string }
  credit: { code: string; name: string; amount: string }
  total: string
}

export interface PettyCashLocation {
  id: string
  name: string
  address: string
  float_amount: string
  custodian: string | null
  custodian_username: string | null
  company: string | null
  company_name: string | null
  petty_cash_account: string
  petty_cash_account_code: string
  petty_cash_account_name: string
  reimbursing_bank_account: string
  reimbursing_bank_account_code: string
  reimbursing_bank_account_name: string
  is_active: boolean
  cash_on_hand: string
  available_for_voucher: string
  total_unreimbursed: string
  // Ring-fence notice for a tin with a per-entity access override (e.g.
  // Unicoin); null for an ordinary tin. Server-owned — drives the red banner.
  access_notice: {
    level: string
    approvers: string
    inputters: string
    text: string
  } | null
  created_at: string
  updated_at: string
}

export interface PettyCashVoucher {
  id: string
  voucher_number: string
  voucher_date: string
  location: string
  location_name: string
  payee: string
  amount: string
  expense_account: string
  expense_account_code: string
  expense_account_name: string
  description: string
  receipt_reference: string
  receipt_attached: boolean
  receipt_count?: number
  status: PettyCashVoucherStatus
  status_display: string
  rejection_reason: string
  submitted_by_username: string | null
  submitted_at: string | null
  first_approved_by_username: string | null
  first_approved_at: string | null
  approved_by_username: string | null
  approved_at: string | null
  je_number: string | null
  reimbursement_number: string | null
  created_at: string
  // True only for the petty-cash coders (Keetile / Pako / Legakwa / Tlamelo or
  // a superuser). Gates the code/sign/reject controls so the requester never
  // sees a coding step (CFO directive 2026-08-14). Backend enforces it too.
  viewer_can_code?: boolean
  // Ring-fence theft-warning notice inherited from this voucher's tin
  // (Unicoin); null for an ordinary tin. Server-owned.
  location_access_notice?: {
    level: string
    approvers: string
    inputters: string
    text: string
  } | null
}

export interface PettyCashReimbursement {
  id: string
  reimbursement_number: string
  location: string
  location_name: string
  period: string | null
  period_name: string | null
  period_start: string
  period_end: string
  reimbursement_date: string
  total_amount: string
  voucher_count: number
  status: PettyCashReimbursementStatus
  status_display: string
  notes: string
  rejection_reason: string
  created_by_username: string | null
  submitted_by_username: string | null
  submitted_at: string | null
  fm_reviewed_by_username: string | null
  fm_reviewed_at: string | null
  posted_by_username: string | null
  posted_at: string | null
  je_number: string | null
  created_at: string
  vouchers?: PettyCashVoucher[]
  gl_preview?: PettyCashGLPreview
}

export interface PettyCashReimbursementPreview {
  location_id: string
  location_name: string
  period_start: string
  period_end: string
  voucher_count: number
  total_amount: string
  cash_on_hand_before: string
  cash_on_hand_after: string
  float_amount: string
  vouchers: PettyCashVoucher[]
}

export async function getPettyCashLocations(): Promise<PaginatedResponse<PettyCashLocation>> {
  return apiFetch<PaginatedResponse<PettyCashLocation>>('/petty-cash-locations/')
}

export async function getPettyCashVouchers(
  filters: { location?: string; status?: string } = {}
): Promise<PaginatedResponse<PettyCashVoucher>> {
  const qs = _qs({
    location: filters.location,
    status: filters.status,
    // Cash-control register must not silently drop older vouchers. Raised from
    // 100; a full paginator is the proper long-term fix (tracked separately).
    page_size: '500',
  })
  return apiFetch<PaginatedResponse<PettyCashVoucher>>(`/petty-cash-vouchers/${qs}`)
}

export interface PettyCashAccountSuggestion {
  code: string
  name: string
  reason: string
}

export async function suggestPettyCashAccount(payload: {
  description: string
  payee?: string
}): Promise<{ suggestions: PettyCashAccountSuggestion[] }> {
  return apiFetch<{ suggestions: PettyCashAccountSuggestion[] }>(
    '/petty-cash-vouchers/suggest_account/',
    { method: 'POST', body: JSON.stringify(payload) },
  )
}

export async function getPettyCashVoucher(id: string): Promise<PettyCashVoucher> {
  return apiFetch<PettyCashVoucher>(`/petty-cash-vouchers/${id}/`)
}

export async function createPettyCashVoucher(payload: {
  location: string
  voucher_date: string
  payee: string
  amount: string
  // Optional: the person raising the voucher does not code the GL — the
  // petty-cash finance team sets it when they post (CFO directive 2026-08-10).
  expense_account?: string
  description: string
  receipt_reference?: string
  receipt_attached?: boolean
}): Promise<PettyCashVoucher> {
  return apiFetch<PettyCashVoucher>('/petty-cash-vouchers/', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function submitPettyCashVoucher(id: string): Promise<PettyCashVoucher> {
  return apiFetch<PettyCashVoucher>(`/petty-cash-vouchers/${id}/submit/`, {
    method: 'POST',
  })
}

export async function approvePettyCashVoucher(id: string): Promise<PettyCashVoucher> {
  return apiFetch<PettyCashVoucher>(`/petty-cash-vouchers/${id}/approve/`, {
    method: 'POST',
  })
}

export async function amendPettyCashVoucher(
  id: string,
  body: { amount?: string; expense_account?: string; reason: string },
): Promise<PettyCashVoucher> {
  // The custodian correcting the amount or the GL line before signing (Keetile's request, CFO
  // approved 2026-08-05). The reason is compulsory server-side: the requester is told what
  // changed and why, and their original figure is kept on the voucher.
  return apiFetch<PettyCashVoucher>(`/petty-cash-vouchers/${id}/amend/`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export async function returnPettyCashVoucherToDraft(
  id: string,
  reason: string,
): Promise<PettyCashVoucher> {
  // A posted voucher that is wrong in more than its amount — wrong payee, wrong date, or
  // should never have been paid — comes back to draft for a full edit (Keetile's request,
  // CFO approved 2026-08-07). The journal entry is reversed, not rewritten, and the
  // signatures are cleared. Refused once the replenishment has paid it out.
  return apiFetch<PettyCashVoucher>(`/petty-cash-vouchers/${id}/return-to-draft/`, {
    method: 'POST',
    body: JSON.stringify({ reason }),
  })
}

export async function rejectPettyCashVoucher(id: string, reason: string): Promise<PettyCashVoucher> {
  return apiFetch<PettyCashVoucher>(`/petty-cash-vouchers/${id}/reject/`, {
    method: 'POST',
    body: JSON.stringify({ reason }),
  })
}

export async function reopenPettyCashVoucher(id: string): Promise<PettyCashVoucher> {
  return apiFetch<PettyCashVoucher>(`/petty-cash-vouchers/${id}/reopen/`, {
    method: 'POST',
  })
}

export async function getPettyCashReimbursements(
  filters: { location?: string; status?: string } = {}
): Promise<PaginatedResponse<PettyCashReimbursement>> {
  const qs = _qs({ location: filters.location, status: filters.status })
  return apiFetch<PaginatedResponse<PettyCashReimbursement>>(`/petty-cash-reimbursements/${qs}`)
}

export async function getPettyCashReimbursement(id: string): Promise<PettyCashReimbursement> {
  return apiFetch<PettyCashReimbursement>(`/petty-cash-reimbursements/${id}/`)
}

export async function previewPettyCashReimbursement(payload: {
  location: string
  period_start: string
  period_end: string
}): Promise<PettyCashReimbursementPreview> {
  return apiFetch<PettyCashReimbursementPreview>('/petty-cash-reimbursements/preview/', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function createPettyCashReimbursement(payload: {
  location: string
  period_start: string
  period_end: string
  period?: string | null
  notes?: string
}): Promise<PettyCashReimbursement> {
  return apiFetch<PettyCashReimbursement>('/petty-cash-reimbursements/', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

// Maker -> FM review
export async function submitPettyCashReimbursement(id: string): Promise<PettyCashReimbursement> {
  return apiFetch<PettyCashReimbursement>(`/petty-cash-reimbursements/${id}/submit/`, {
    method: 'POST',
  })
}

// FM review -> CFO
export async function fmReviewPettyCashReimbursement(id: string): Promise<PettyCashReimbursement> {
  return apiFetch<PettyCashReimbursement>(`/petty-cash-reimbursements/${id}/fm_review/`, {
    method: 'POST',
  })
}

export async function rejectPettyCashReimbursement(id: string, reason: string): Promise<PettyCashReimbursement> {
  return apiFetch<PettyCashReimbursement>(`/petty-cash-reimbursements/${id}/reject/`, {
    method: 'POST',
    body: JSON.stringify({ reason }),
  })
}

export async function reopenPettyCashReimbursement(id: string): Promise<PettyCashReimbursement> {
  return apiFetch<PettyCashReimbursement>(`/petty-cash-reimbursements/${id}/reopen/`, {
    method: 'POST',
  })
}

// CFO's final "approve in the bank" — posts the DR petty cash / CR bank JE.
export async function postPettyCashReimbursement(id: string): Promise<PettyCashReimbursement> {
  return apiFetch<PettyCashReimbursement>(`/petty-cash-reimbursements/${id}/post_reimbursement/`, {
    method: 'POST',
  })
}

// Receipts — the digital replacement for the physical voucher pad / Excel log.
export interface PettyCashReceipt {
  id: string
  voucher: string
  filename: string
  file_size_bytes: number
  content_type: string
  uploaded_by_username: string | null
  download_url: string | null
  created_at: string
}

export async function getPettyCashVoucherReceipts(id: string): Promise<PettyCashReceipt[]> {
  return apiFetch<PettyCashReceipt[]>(`/petty-cash-vouchers/${id}/receipts/`)
}

export async function downloadPettyCashReceipt(
  voucherId: string, receiptId: string, filename: string,
): Promise<void> {
  // A plain <a href> cannot download this. Our token lives in localStorage, so a browser
  // navigation sends no Authorization header and the endpoint answers 401 — verified live.
  // Fetching it as a blob attaches the header, then we hand the browser a local URL to save.
  // Same pattern as downloadPlanPackFile.
  const res = await apiFetchBinary(
    `${API_BASE}/petty-cash-vouchers/${voucherId}/receipts/${receiptId}/download/`,
    { headers: {} },
  )
  if (!res.ok) throw new Error(`Could not download the receipt (HTTP ${res.status})`)
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename || 'receipt'
  a.click()
  URL.revokeObjectURL(url)
}

export async function uploadPettyCashVoucherReceipt(id: string, file: File): Promise<PettyCashReceipt> {
  const fd = new FormData()
  fd.append('file', file)
  const res = await apiFetchBinary(`${API_BASE}/petty-cash-vouchers/${id}/receipts/`, {
    method: 'POST', headers: {}, body: fd,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: `Upload failed: ${res.status}` }))
    throw new Error(err.detail || err.error || `Upload failed: ${res.status}`)
  }
  return res.json()
}

export async function deletePettyCashVoucherReceipt(voucherId: string, receiptId: string): Promise<void> {
  const res = await apiFetchBinary(`${API_BASE}/petty-cash-vouchers/${voucherId}/receipts/${receiptId}`, {
    method: 'DELETE', headers: {},
  })
  if (!res.ok && res.status !== 204) {
    const err = await res.json().catch(() => ({ detail: `Delete failed: ${res.status}` }))
    throw new Error(err.detail || err.error || `Delete failed: ${res.status}`)
  }
}

// "Can't find it" — email the record's link to a chosen group recipient.
export async function emailPettyCashVoucherLink(id: string, recipient: string, note?: string): Promise<{ sent: boolean; recipient: string }> {
  return apiFetch(`/petty-cash-vouchers/${id}/email_link/`, {
    method: 'POST', body: JSON.stringify({ recipient, note: note || '' }),
  })
}

export async function emailPettyCashReimbursementLink(id: string, recipient: string, note?: string): Promise<{ sent: boolean; recipient: string }> {
  return apiFetch(`/petty-cash-reimbursements/${id}/email_link/`, {
    method: 'POST', body: JSON.stringify({ recipient, note: note || '' }),
  })
}

export async function deletePettyCashReimbursement(id: string): Promise<void> {
  const res = await apiFetchBinary(`${API_BASE}/petty-cash-reimbursements/${id}/`, {
    method: 'DELETE',
  })
  if (!res.ok && res.status !== 204) {
    let msg = `Delete failed: ${res.status}`
    try { const j = await res.json(); if (j?.detail) msg = j.detail } catch {}
    throw new Error(msg)
  }
}

export async function batchUploadJournals(file: File, autoPost = true): Promise<{
  created: number
  entries: { entry_number: string; entry_date: string; description: string; status: string; line_count: number }[]
  warnings?: string[]
}> {
  const form = new FormData()
  form.append('file', file)
  form.append('auto_post', autoPost ? 'true' : 'false')
  const res = await apiFetchBinary(`${API_BASE}/journal-entries/batch-upload/`, {
    method: 'POST',
    headers: {
},
    body: form,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: `Upload failed: ${res.status}` }))
    throw new Error(err.error || err.detail || `Upload failed: ${res.status}`)
  }
  return res.json()
}

// ─── Fixed Assets ──────────────────────────────────────────────────────────────

export type AssetMethod = 'straight_line' | 'reducing_balance' | 'full_year_one'
export type VatTreatment = 'standard' | 'denied' | 'zero_exempt'

export interface AssetCategory {
  id: string
  code: string
  name: string
  cost_account: string
  cost_account_code: string
  accum_depr_account: string
  accum_depr_account_code: string
  depreciation_expense_account: string
  depreciation_expense_account_code: string
  default_method: AssetMethod
  method_display: string
  default_useful_life_months: number
  default_salvage_pct: string
  is_passenger_vehicle: boolean
  default_capital_allowance_method: AssetMethod
  default_capital_allowance_rate: string
  default_tax_cost_cap: string | null
  is_active: boolean
}

export interface AssetListItem {
  id: string
  tag_number: string
  external_ref: string
  name: string
  company: string
  company_code: string
  category: string
  category_code: string
  category_name: string
  cost: string
  salvage_value: string
  accumulated_depreciation: string
  net_book_value: string
  method: AssetMethod
  method_display: string
  useful_life_months: number
  purchase_date: string
  in_service_date: string
  last_depreciation_date: string | null
  location: string
  custodian: string
  custodian_employee: string | null
  custodian_employee_name: string | null
  status: 'active' | 'disposed' | 'written_off' | 'transferred'
  status_display: string
  condition: 'functional' | 'broken' | 'unknown'
  condition_display: string
  custody_status: 'in_stock' | 'issued' | 'in_use' | 'returned_spare' | 'retired'
  custody_status_display: string
  // Tax / VAT
  vat_treatment: VatTreatment
  vat_treatment_display: string
  purchase_vat_amount: string
  capital_allowance_method: AssetMethod
  capital_allowance_rate: string
  tax_cost_cap: string | null
  tax_cost: string
  cost_capped_by_tax_rule: boolean
  created_at: string
  updated_at: string
}

export interface DepreciationEntry {
  id: string
  asset: string
  asset_tag: string
  asset_name: string
  period: string
  period_name: string
  period_end_date: string
  amount: string
  journal_entry: string | null
  journal_entry_number: string | null
  reversed_at: string | null
  created_at: string
}

export interface AssetDisposal {
  id: string
  asset: string
  asset_tag: string
  asset_name: string
  disposal_type: 'sale' | 'write_off' | 'transfer' | 'trade_in'
  disposal_type_display: string
  disposal_date: string
  proceeds: string
  bank_account: string | null
  cost_at_disposal: string
  accumulated_depr_at_disposal: string
  nbv_at_disposal: string
  gain_loss: string
  journal_entry: string | null
  journal_entry_number: string | null
  notes: string
  created_at: string
}

export interface AssetAssignment {
  id: string
  from_custodian: string
  to_custodian: string
  from_location: string
  to_location: string
  reason: string
  transferred_at: string
  transferred_by: string | null
  transferred_by_name: string | null
  created_at: string
}

export interface AssetDetail extends AssetListItem {
  description: string
  serial_number: string
  barcode: string
  opening_accumulated_depreciation: string
  notes: string
  depreciation_entries: DepreciationEntry[]
  disposal: AssetDisposal | null
  assignments: AssetAssignment[]
}

export interface AssetEmployeeOption {
  id: string
  full_name: string
  email: string
  employee_number: string
  company_code: string
}

export async function getAssetEmployees(q?: string): Promise<AssetEmployeeOption[]> {
  const qs = q ? `?q=${encodeURIComponent(q)}` : ''
  const r = await apiFetch<{ employees: AssetEmployeeOption[] }>(`/assets/employees/${qs}`)
  return r.employees
}

export async function transferAsset(id: string, body: {
  to_employee?: string | null
  to_location?: string
  reason?: string
  transferred_at?: string
}): Promise<AssetDetail> {
  return apiFetch<AssetDetail>(`/assets/${id}/transfer/`, {
    method: 'POST', body: JSON.stringify(body),
  })
}

// ===========================================================================
//  Asset Control & Handover module (CFO spec 2026-09-02)
// ===========================================================================

export type RequisitionType = 'new_purchase' | 'reissue'
export type RequisitionStatus =
  | 'draft' | 'pending_fm_approval' | 'pending_cfo_approval'
  | 'approved' | 'fulfilled' | 'rejected' | 'cancelled'

export interface AssetRequisition {
  id: string
  requisition_number: string
  req_type: RequisitionType
  req_type_display: string
  company: string
  company_code: string
  category: string
  category_name: string
  description: string
  estimated_value: string
  spare_asset: string | null
  spare_asset_tag: string | null
  recipient: string
  recipient_name: string
  recipient_email: string
  reason: string
  status: RequisitionStatus
  status_display: string
  requires_full_gate: boolean
  requested_by_name: string
  submitted_at: string | null
  fm_approved_by_name: string
  fm_approved_at: string | null
  fm_comment: string
  cfo_approved_by_name: string
  cfo_approved_at: string | null
  cfo_comment: string
  rejected_at: string | null
  rejection_reason: string
  resulting_asset: string | null
  handover_id: string | null
  created_at: string
  updated_at: string
}

export interface CreateRequisitionInput {
  req_type: RequisitionType
  category: string
  description: string
  estimated_value?: number | string
  reason: string
  recipient: string          // Employee id — picked from the directory
  spare_asset?: string | null
}

export async function getAssetRequisitions(params?: {
  status?: string; req_type?: string
}): Promise<PaginatedResponse<AssetRequisition>> {
  const qs = _qs({ status: params?.status, req_type: params?.req_type })
  return apiFetch<PaginatedResponse<AssetRequisition>>(`/asset-requisitions/${qs}`)
}

export async function getAssetRequisition(id: string): Promise<AssetRequisition> {
  return apiFetch<AssetRequisition>(`/asset-requisitions/${id}/`)
}

export async function createAssetRequisition(body: CreateRequisitionInput): Promise<AssetRequisition> {
  return apiFetch<AssetRequisition>('/asset-requisitions/', { method: 'POST', body: JSON.stringify(body) })
}

export async function getRequisitionQueue(): Promise<AssetRequisition[]> {
  const r = await apiFetch<{ requisitions: AssetRequisition[] }>('/asset-requisitions/queue/')
  return r.requisitions
}

export async function fmApproveRequisition(id: string, comment = ''): Promise<AssetRequisition> {
  return apiFetch<AssetRequisition>(`/asset-requisitions/${id}/fm-approve/`, {
    method: 'POST', body: JSON.stringify({ comment }),
  })
}

export async function cfoApproveRequisition(id: string, comment = ''): Promise<AssetRequisition> {
  return apiFetch<AssetRequisition>(`/asset-requisitions/${id}/cfo-approve/`, {
    method: 'POST', body: JSON.stringify({ comment }),
  })
}

export async function rejectRequisition(id: string, reason: string): Promise<AssetRequisition> {
  return apiFetch<AssetRequisition>(`/asset-requisitions/${id}/reject/`, {
    method: 'POST', body: JSON.stringify({ reason }),
  })
}

export async function cancelRequisition(id: string, reason = ''): Promise<AssetRequisition> {
  return apiFetch<AssetRequisition>(`/asset-requisitions/${id}/cancel/`, {
    method: 'POST', body: JSON.stringify({ reason }),
  })
}

export type HandoverStatus =
  | 'pending' | 'it_released' | 'finance_recorded' | 'accepted' | 'cancelled'

export interface AssetHandover {
  id: string
  handover_number: string
  requisition: string
  requisition_number: string
  asset: string
  asset_tag: string
  asset_name: string
  recipient: string
  recipient_name: string
  recipient_email: string
  status: HandoverStatus
  status_display: string
  condition_on_issue: string
  accessories: string
  it_released_by_name: string
  it_released_at: string | null
  finance_recorded_by_name: string
  finance_recorded_at: string | null
  employee_accepted_by_name: string
  employee_accepted_at: string | null
  is_complete: boolean
  notes: string
  created_at: string
  updated_at: string
}

export async function getAssetHandover(id: string): Promise<AssetHandover> {
  return apiFetch<AssetHandover>(`/asset-handovers/${id}/`)
}

export async function createHandover(body: {
  requisition: string; asset: string; condition_on_issue?: string; accessories?: string
}): Promise<AssetHandover> {
  return apiFetch<AssetHandover>('/asset-handovers/', { method: 'POST', body: JSON.stringify(body) })
}

export async function handoverItRelease(id: string, signature = ''): Promise<AssetHandover> {
  return apiFetch<AssetHandover>(`/asset-handovers/${id}/it-release/`, {
    method: 'POST', body: JSON.stringify({ signature }),
  })
}

export async function handoverFinanceRecord(id: string, signature = ''): Promise<AssetHandover> {
  return apiFetch<AssetHandover>(`/asset-handovers/${id}/finance-record/`, {
    method: 'POST', body: JSON.stringify({ signature }),
  })
}

export async function handoverAccept(id: string, signature = ''): Promise<AssetHandover> {
  return apiFetch<AssetHandover>(`/asset-handovers/${id}/accept/`, {
    method: 'POST', body: JSON.stringify({ signature }),
  })
}

export function getHandoverPdfUrl(id: string): string {
  return `${API_BASE}/asset-handovers/${id}/pdf/`
}

export async function getSparePool(): Promise<AssetListItem[]> {
  const r = await apiFetch<{ assets: AssetListItem[] }>('/asset-control/spare-pool/')
  return r.assets
}

export async function uploadSparePoolCsv(file: File): Promise<{ created: number; errors: string[] }> {
  const form = new FormData()
  form.append('file', file)
  return apiFetch<{ created: number; errors: string[] }>('/asset-control/spare-pool/upload/', {
    method: 'POST',
    body: form,
  })
}

export async function updateAssetCondition(id: string, condition: string): Promise<void> {
  await apiFetch(`/assets/${id}/condition/`, { method: 'PATCH', body: JSON.stringify({ condition }) })
}

export async function getMyAssets(): Promise<{ employee: { id: string; full_name: string } | null; assets: AssetListItem[] }> {
  return apiFetch('/asset-control/my-assets/')
}

export interface AssetReconciliation {
  register_count: number
  counted_assets: number | null
  variance: number | null
  ties_out: boolean | null
  count_period: string | null
  count_completed_at: string | null
  discrepancies: string
  truncated: boolean
  register: AssetListItem[]
}

export async function getAssetReconciliation(): Promise<AssetReconciliation> {
  return apiFetch<AssetReconciliation>('/asset-control/reconciliation/')
}

export interface AssetControlPolicy {
  id: string
  material_threshold_bwp: string
  is_active: boolean
  updated_at: string
}

export async function getAssetControlPolicy(): Promise<AssetControlPolicy> {
  return apiFetch<AssetControlPolicy>('/asset-control-policy/')
}

export async function updateAssetControlPolicy(material_threshold_bwp: number | string): Promise<AssetControlPolicy> {
  return apiFetch<AssetControlPolicy>('/asset-control-policy/0/', {
    method: 'PATCH', body: JSON.stringify({ material_threshold_bwp }),
  })
}

export interface CreateAssetInput {
  tag_number: string
  external_ref?: string
  name: string
  description?: string
  serial_number?: string
  barcode?: string
  company: string
  category: string
  cost: string
  salvage_value?: string
  method?: AssetMethod
  useful_life_months: number
  purchase_date: string
  in_service_date?: string
  opening_accumulated_depreciation?: string
  last_depreciation_date?: string | null
  location?: string
  custodian?: string
  notes?: string
  // Tax / VAT
  vat_treatment?: VatTreatment
  purchase_vat_amount?: string
  capital_allowance_method?: AssetMethod
  capital_allowance_rate?: string
  tax_cost_cap?: string | null
}

export interface AssetImportBatch {
  id: string
  source: string
  file_name: string
  cutover_date: string
  rows_total: number
  rows_valid: number
  rows_invalid: number
  rows_imported: number
  parsed_rows: Record<string, any>[]
  validation_errors: { row_index: number; field: string; message: string }[]
  status: 'draft' | 'committed' | 'failed'
  status_display: string
  company: string
  company_code: string
  created_at: string
  committed_at: string | null
}

export interface AssetRegisterReport {
  as_of: string
  count: number
  items: {
    id: string
    tag_number: string
    external_ref: string
    name: string
    category_code: string
    category_name: string
    company_code: string
    cost: string
    accumulated_depr: string
    net_book_value: string
    salvage_value: string
    method: string
    useful_life_months: number
    purchase_date: string | null
    in_service_date: string | null
    last_depr_date: string | null
    location: string
    custodian: string
    status: string
  }[]
  by_category: {
    category_code: string
    category_name: string
    count: number
    cost: string
    accumulated_depr: string
    net_book_value: string
  }[]
  totals: {
    cost: string
    accumulated_depr: string
    net_book_value: string
  }
}

export interface DepreciationRunResult {
  period: string
  assets_considered: number
  assets_depreciated: number
  assets_skipped: number
  assets_fully_depreciated: number
  total_amount: string
  errors: { tag_number: string; error: string }[]
  dry_run: boolean
}

export async function getAssetCategories(): Promise<PaginatedResponse<AssetCategory>> {
  return apiFetch<PaginatedResponse<AssetCategory>>('/asset-categories/')
}

export async function getAssets(params?: {
  page?: number
  page_size?: number
  status?: string
  category?: string
  company?: string
  search?: string
  purchase_date_from?: string
  purchase_date_to?: string
}): Promise<PaginatedResponse<AssetListItem>> {
  const qs = _qs({
    page: params?.page ? String(params.page) : undefined,
    page_size: params?.page_size ? String(params.page_size) : undefined,
    status: params?.status,
    category: params?.category,
    company: params?.company,
    search: params?.search,
    purchase_date_from: params?.purchase_date_from,
    purchase_date_to: params?.purchase_date_to,
  })
  return apiFetch<PaginatedResponse<AssetListItem>>(`/assets/${qs}`)
}

export interface AssetSummary {
  totals: { count: number; cost: number; accumulated_depreciation: number; net_book_value: number }
  by_category: { category: string; count: number; cost: number; accumulated_depreciation: number; net_book_value: number }[]
  movements: { month: string; additions: number; depreciation: number }[]
}
export async function getAssetSummary(company?: string): Promise<AssetSummary> {
  const qs = company ? `?company=${encodeURIComponent(company)}` : ''
  return apiFetch<AssetSummary>(`/assets/summary/${qs}`)
}

export function getAssetExportCsvUrl(params?: {
  status?: string
  category?: string
  company?: string
  search?: string
  purchase_date_from?: string
  purchase_date_to?: string
}): string {
  const qs = _qs({
    status: params?.status,
    category: params?.category,
    company: params?.company,
    search: params?.search,
    purchase_date_from: params?.purchase_date_from,
    purchase_date_to: params?.purchase_date_to,
  })
  return `/api/v1/assets/export_csv/${qs}`
}

/**
 * Generic Excel export for every CFO report. The backend dispatcher
 * lives in reporting/xlsx_export.py and switches on `?report=<key>`.
 * Triggers a token-authenticated binary download in a new tab, so
 * the browser keeps the filename from Content-Disposition.
 *
 * Supported keys (CFO directive 2026-05-20):
 *   trial_balance | profit_loss | balance_sheet | ma_balance_sheet |
 *   general_ledger | ar_aging | ap_aging | cash_position | cash_flow
 */
export async function downloadReportXlsx(
  report: string,
  params: Record<string, string | number | undefined | null>,
  filenameHint?: string,
): Promise<void> {
  const qs = _qs({
    report,
    ...Object.fromEntries(
      Object.entries(params).map(([k, v]) => [k, v == null ? undefined : String(v)]),
    ),
  })
  const url = `${API_BASE}/reports/export-xlsx/${qs}`
  const headers: Record<string, string> = {}
  try {
    const { acquireApiToken, SSO_API_CALLS_READY } = await import('@/auth/msal')
    if (SSO_API_CALLS_READY) {
      const t = await acquireApiToken()
      if (t) headers['Authorization'] = `Bearer ${t}`
    }
  } catch { /* fall through */ }
  if (!headers['Authorization']) {
    const t = getToken()
    if (t && t !== SSO_SENTINEL_TOKEN) headers['Authorization'] = `Token ${t}`
  }
  const res = await fetch(url, { headers })
  if (!res.ok) {
    const txt = await res.text().catch(() => '')
    throw new Error(`Excel export failed: HTTP ${res.status} ${txt.slice(0, 200)}`)
  }
  const blob = await res.blob()
  // Try the server-supplied filename first; fall back to the hint or a default.
  const disp = res.headers.get('content-disposition') || ''
  const m = /filename="([^"]+)"/.exec(disp)
  const filename = (m && m[1]) || filenameHint || `${report}.xlsx`
  const a = document.createElement('a')
  const dlUrl = URL.createObjectURL(blob)
  a.href = dlUrl
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(dlUrl), 60_000)
}

// ─── Renewal report (Workstream B / B1) ──────────────────────────────────────
// Policies renewing in a chosen month, read live from Graphite. Finance-gated on
// the server; this client just calls it.
export interface RenewalReport {
  columns: string[]
  rows: Record<string, string>[]
  count: number
  month: number
  month_label: string
  section: string
  meta: string[]
}

export async function getRenewalReport(
  params: { month: number; section?: string },
): Promise<RenewalReport> {
  const qs = _qs({ month: params.month, section: params.section || undefined })
  return apiFetch<RenewalReport>(`/reports/renewals/${qs}`)
}

// Authenticated file download (Excel or CSV). Mirrors downloadReportXlsx: SSO
// bearer if present, else the legacy Token, then save the blob under the
// server-supplied filename.
export async function downloadRenewalReport(
  params: { month: number; section?: string },
  format: 'xlsx' | 'csv',
): Promise<void> {
  const qs = _qs({ month: params.month, section: params.section || undefined, export: format })
  const url = `${API_BASE}/reports/renewals/${qs}`
  const headers: Record<string, string> = {}
  try {
    const { acquireApiToken, SSO_API_CALLS_READY } = await import('@/auth/msal')
    if (SSO_API_CALLS_READY) {
      const t = await acquireApiToken()
      if (t) headers['Authorization'] = `Bearer ${t}`
    }
  } catch { /* fall through */ }
  if (!headers['Authorization']) {
    const t = getToken()
    if (t && t !== SSO_SENTINEL_TOKEN) headers['Authorization'] = `Token ${t}`
  }
  const res = await fetch(url, { headers })
  if (!res.ok) {
    const txt = await res.text().catch(() => '')
    throw new Error(`Download failed: HTTP ${res.status} ${txt.slice(0, 200)}`)
  }
  const blob = await res.blob()
  const disp = res.headers.get('content-disposition') || ''
  const m = /filename="([^"]+)"/.exec(disp)
  const filename = (m && m[1]) || `renewals.${format}`
  const a = document.createElement('a')
  const dlUrl = URL.createObjectURL(blob)
  a.href = dlUrl
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(dlUrl), 60_000)
}

// ─── CR-002 Claims Payment Movement (Bontle Tendani) ─────────────────────────
// Finance-only. The gate is permission_classes on the Django view; this client
// just calls it, and a non-finance user gets a 403 from the server whether or
// not the sidebar shows the entry.
export interface ClaimsPaymentMovementBucket {
  key: string
  label: string
  count: number
  amount_gross: string
  amount_excl_vat: string
  vat_amount: string
}

export interface ClaimsPaymentMovementRow {
  transaction_date: string
  statement_number: string
  payee: string
  claim_reference: string
  invoice_reference: string
  payment_basis: string
  payment_basis_label: string
  settlement: 'supplier_invoice' | 'individual_claimant'
  amount_gross: string
  amount_excl_vat: string
  vat_amount: string
  was_degrossed: boolean
}

export interface ClaimsPaymentMovementReport {
  from: string
  to: string
  vat_rate: string
  total: ClaimsPaymentMovementBucket
  by_type: ClaimsPaymentMovementBucket[]
  by_settlement: ClaimsPaymentMovementBucket[]
  degrossed_count: number
  left_gross_count: number
  rows: ClaimsPaymentMovementRow[]
  notes: string[]
}

export async function getClaimsPaymentMovement(
  from: string,
  to: string,
  bankAccount?: string | null,
): Promise<ClaimsPaymentMovementReport> {
  return apiFetch<ClaimsPaymentMovementReport>(
    `/reports/claims-payment-movement/${_qs({
      from,
      to,
      bank_account: bankAccount ?? undefined,
    })}`,
  )
}

export async function downloadClaimsPaymentMovementXlsx(
  from: string,
  to: string,
  bankAccount?: string | null,
): Promise<void> {
  const url = `${API_BASE}/reports/claims-payment-movement/${_qs({
    from,
    to,
    bank_account: bankAccount ?? undefined,
    format: 'xlsx',
  })}`
  const headers: Record<string, string> = {}
  try {
    const { acquireApiToken, SSO_API_CALLS_READY } = await import('@/auth/msal')
    if (SSO_API_CALLS_READY) {
      const t = await acquireApiToken()
      if (t) headers['Authorization'] = `Bearer ${t}`
    }
  } catch { /* fall through */ }
  if (!headers['Authorization']) {
    const t = getToken()
    if (t && t !== SSO_SENTINEL_TOKEN) headers['Authorization'] = `Token ${t}`
  }
  const res = await fetch(url, { headers })
  if (!res.ok) {
    const txt = await res.text().catch(() => '')
    throw new Error(`Excel export failed: HTTP ${res.status} ${txt.slice(0, 200)}`)
  }
  const blob = await res.blob()
  const disp = res.headers.get('content-disposition') || ''
  const m = /filename="([^"]+)"/.exec(disp)
  const filename = (m && m[1]) || `claims_payment_movement_${from}_${to}.xlsx`
  const a = document.createElement('a')
  const dlUrl = URL.createObjectURL(blob)
  a.href = dlUrl
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(dlUrl), 60_000)
}

export async function getAsset(id: string): Promise<AssetDetail> {
  return apiFetch<AssetDetail>(`/assets/${id}/`)
}

export async function createAsset(data: CreateAssetInput): Promise<AssetDetail> {
  return apiFetch<AssetDetail>('/assets/', {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

export async function depreciateAsset(id: string, period: string): Promise<DepreciationEntry> {
  return apiFetch<DepreciationEntry>(`/assets/${id}/depreciate/`, {
    method: 'POST',
    body: JSON.stringify({ period }),
  })
}

export async function disposeAsset(id: string, payload: {
  disposal_type: 'sale' | 'write_off' | 'transfer' | 'trade_in'
  disposal_date: string
  proceeds: string
  bank_account?: string | null
  notes?: string
}): Promise<AssetDisposal> {
  return apiFetch<AssetDisposal>(`/assets/${id}/dispose/`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function runMonthlyDepreciation(payload: {
  period_name: string
  company?: string | null
  dry_run?: boolean
}): Promise<DepreciationRunResult> {
  return apiFetch<DepreciationRunResult>('/assets/run-monthly-depreciation/', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function previewAssetImport(formData: FormData): Promise<AssetImportBatch> {
  const res = await apiFetchBinary(`${API_BASE}/asset-imports/preview/`, {
    method: 'POST',
    headers: {
},
    body: formData,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: `Upload failed: ${res.status}` }))
    throw new Error(err.error || err.detail || `Upload failed: ${res.status}`)
  }
  return res.json()
}

export async function commitAssetImport(id: string): Promise<{
  created: number
  errors: { row_index: number; field: string; message: string }[]
  batch: AssetImportBatch
}> {
  return apiFetch(`/asset-imports/${id}/commit/`, { method: 'POST' })
}

export async function getAssetRegister(params?: {
  as_of?: string
  category?: string
  status?: string
  company?: string | null
}): Promise<AssetRegisterReport> {
  return apiFetch<AssetRegisterReport>(
    `/reports/asset-register/${_qs({
      as_of: params?.as_of,
      category: params?.category,
      status: params?.status,
      company: params?.company ?? undefined,
    })}`,
  )
}

// ─── Asset Movement (IAS 16 roll-forward) ────────────────────────────────────

export interface AssetMovementBucket {
  opening: string
  additions?: string
  charge?: string
  disposals?: string
  closing?: string
}

export interface AssetMovementRow {
  category_code: string
  category_name: string
  gross_cost:        { opening: string; additions: string; disposals: string; closing: string }
  accumulated_depr:  { opening: string; charge:    string; disposals: string; closing: string }
  nbv:               { opening: string; closing:   string }
}

export interface AssetMovementReport {
  from_date: string
  to_date:   string
  rows:      AssetMovementRow[]
  totals: {
    gross_cost:       { opening: string; additions: string; disposals: string; closing: string }
    accumulated_depr: { opening: string; charge:    string; disposals: string; closing: string }
    nbv:              { opening: string; closing:   string }
  }
}

export async function getAssetMovement(params: {
  from: string
  to: string
  company?: string | null
}): Promise<AssetMovementReport> {
  return apiFetch<AssetMovementReport>(
    `/reports/asset-movement/${_qs({
      from:    params.from,
      to:      params.to,
      company: params.company ?? undefined,
    })}`,
  )
}

// ─── User profiles & RBAC ─────────────────────────────────────────────────────

export type UserTitle =
  | 'ceo'
  | 'coo'
  | 'cfo'
  | 'finance_manager'
  | 'financial_controller'
  | 'accountant'
  | 'bookkeeper'
  | 'finance_analyst'
  | 'auditor'
  | 'executive'
  | 'operations'
  | 'senior_operations'
  | 'system_api'

export type UserRole =
  | 'finance_admin'
  | 'accountant'
  | 'finance_reviewer'
  | 'operations_staff'
  | 'executive'
  | 'system_api'

export interface UserProfile {
  /**
   * False when the signed-in user has no profile row — /user-profiles/me/ then
   * answers 200 with this same shape and EVERY permission false, instead of the
   * old 404 that surfaced as a red "Request failed" toast (58 of 188 active
   * users on prod, 2026-07-29). Absent on list/detail responses, where a row
   * always exists. Treat a missing profile as no authority, never as an error.
   */
  has_profile?: boolean
  id: string
  username: string
  first_name: string
  last_name: string
  email: string
  role: UserRole
  role_display: string
  title: UserTitle
  title_display: string
  department: string | null
  job_title: string
  is_administrator: boolean
  is_access_delegate?: boolean
  is_active: boolean
  is_user_active: boolean
  can_approve_journal_entries: boolean
  can_create_journal_entries: boolean
  can_approve_payroll?: boolean
  is_payroll_processor?: boolean
  can_administer_users: boolean
  can_post_directly: boolean
  can_manage_periods?: boolean
  can_view_internal_audit?: boolean
  can_edit_internal_audit?: boolean
  created_at: string
  updated_at: string
}

export interface CreateUserProfileInput {
  username: string
  password?: string
  first_name?: string
  last_name?: string
  email?: string
  role: UserRole
  title: UserTitle
  department?: string
  is_administrator?: boolean
  is_access_delegate?: boolean
  is_active?: boolean
}

export interface UpdateUserProfileInput {
  username?: string
  password?: string
  first_name?: string
  last_name?: string
  email?: string
  role?: UserRole
  title?: UserTitle
  department?: string
  is_administrator?: boolean
  is_access_delegate?: boolean
  is_active?: boolean
}

// Lightweight localStorage cache so the sidebar/admin-gating doesn't block
// first paint waiting on /user-profiles/me/ every navigation. The freshness
// window is short — a stale cache only mis-renders the admin nav group for
// a few seconds after a role change.
const ME_CACHE_KEY = 'alpha_me_cache_v1'
const ME_CACHE_TTL_MS = 5 * 60 * 1000  // 5 minutes

interface CachedMe { profile: UserProfile; ts: number }

function readMeCache(): UserProfile | null {
  if (typeof window === 'undefined') return null
  try {
    const raw = localStorage.getItem(ME_CACHE_KEY)
    if (!raw) return null
    const c = JSON.parse(raw) as CachedMe
    if (Date.now() - c.ts > ME_CACHE_TTL_MS) return null
    return c.profile
  } catch { return null }
}

function writeMeCache(profile: UserProfile) {
  if (typeof window === 'undefined') return
  try {
    localStorage.setItem(ME_CACHE_KEY, JSON.stringify({ profile, ts: Date.now() } satisfies CachedMe))
  } catch { /* quota / private mode — ignore */ }
}

export function clearMeCache() {
  if (typeof window !== 'undefined') localStorage.removeItem(ME_CACHE_KEY)
}

export async function getMe(opts?: { force?: boolean }): Promise<UserProfile> {
  if (!opts?.force) {
    const cached = readMeCache()
    if (cached) {
      // Refresh in the background without blocking the caller.
      apiFetch<UserProfile>('/user-profiles/me/')
        .then(writeMeCache)
        .catch(() => { /* leave stale cache in place */ })
      return cached
    }
  }
  const profile = await apiFetch<UserProfile>('/user-profiles/me/')
  writeMeCache(profile)
  return profile
}

export async function getUserProfiles(): Promise<PaginatedResponse<UserProfile>> {
  return apiFetch<PaginatedResponse<UserProfile>>('/user-profiles/')
}

export async function getUserProfile(id: string): Promise<UserProfile> {
  return apiFetch<UserProfile>(`/user-profiles/${id}/`)
}

export async function createUserProfile(data: CreateUserProfileInput): Promise<UserProfile> {
  return apiFetch<UserProfile>('/user-profiles/', {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

export async function updateUserProfile(id: string, data: UpdateUserProfileInput): Promise<UserProfile> {
  return apiFetch<UserProfile>(`/user-profiles/${id}/`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  })
}

export async function deactivateUserProfile(id: string): Promise<void> {
  await apiFetch<void>(`/user-profiles/${id}/`, { method: 'DELETE' })
}

// ─── Journal Entry approval workflow ─────────────────────────────────────────

export async function submitJournalEntry(id: string): Promise<JournalEntryDetail> {
  return apiFetch<JournalEntryDetail>(`/journal-entries/${id}/submit/`, {
    method: 'POST',
  })
}

export interface ApproveTestConfirm { requires_confirmation: true; warning: string; entry_number?: string }
/** BUG-001: a production test/QA entry returns 409 requires_confirmation; the
 *  caller confirms then re-calls with confirmTest=true. */
export async function approveJournalEntry(
  id: string, confirmTest = false,
): Promise<JournalEntryDetail | ApproveTestConfirm> {
  const res = await apiFetchBinary(`${API_BASE}/journal-entries/${id}/approve/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ confirm_test: confirmTest }),
  })
  if (res.status === 409) {
    const b = await res.json().catch(() => ({}))
    return { requires_confirmation: true, warning: b.warning || 'This looks like a test entry — confirm posting to the live ledger?', entry_number: b.entry_number }
  }
  if (!res.ok) {
    let m = 'Approve failed'
    try { const e = await res.json(); m = e.error || e.detail || m } catch {}
    throw new Error(m)
  }
  return res.json()
}

export async function rejectJournalEntry(id: string, reason: string): Promise<JournalEntryDetail> {
  return apiFetch<JournalEntryDetail>(`/journal-entries/${id}/reject/`, {
    method: 'POST',
    body: JSON.stringify({ reason }),
  })
}

export async function reopenJournalEntry(id: string): Promise<JournalEntryDetail> {
  return apiFetch<JournalEntryDetail>(`/journal-entries/${id}/reopen/`, {
    method: 'POST',
  })
}

export async function classifyRelatedParty(id: string, is_related_party: boolean): Promise<JournalEntryDetail> {
  return apiFetch<JournalEntryDetail>(`/journal-entries/${id}/classify-related-party/`, {
    method: 'POST',
    body: JSON.stringify({ is_related_party }),
  })
}

// ─── Related Party report ─────────────────────────────────────────────────────

export interface RelatedPartyRow {
  entry_number: string
  entry_date: string
  status: string
  description: string
  account_code: string
  account_name: string
  contact_name: string
  relationship: string
  debit_bwp: string
  credit_bwp: string
  line_description: string
}

export interface RelatedPartyByContact {
  contact_id: string | null
  contact_name: string
  relationship: string
  count: number
  debit_bwp: string
  credit_bwp: string
}

export interface RelatedPartyReport {
  window: {
    from: string
    to: string
    count: number
    rows: RelatedPartyRow[]
    by_contact: RelatedPartyByContact[]
  }
  fiscal_year_to_date: {
    from: string
    to: string
    count: number
    rows: RelatedPartyRow[]
    by_contact: RelatedPartyByContact[]
  }
  summary: {
    window_count: number
    fiscal_year_count: number
    window_debit_total: string
    window_credit_total: string
    fy_debit_total: string
    fy_credit_total: string
  }
}

export async function getRelatedPartyReport(from: string, to: string): Promise<RelatedPartyReport> {
  return apiFetch<RelatedPartyReport>(
    `/reports/related-party-transactions/${_qs({ from, to })}`,
  )
}

// ─── JE supporting documents ──────────────────────────────────────────────────

export interface JournalEntryAttachment {
  id: string
  journal_entry: string
  filename: string
  file_size_bytes: number
  content_type: string
  description: string
  uploaded_by_username: string
  download_url: string | null
  created_at: string
}

export async function getJournalEntryAttachments(id: string): Promise<JournalEntryAttachment[]> {
  return apiFetch<JournalEntryAttachment[]>(`/journal-entries/${id}/attachments/`)
}

export async function uploadJournalEntryAttachment(id: string, file: File, description = ''): Promise<JournalEntryAttachment> {
  const fd = new FormData()
  fd.append('file', file)
  if (description) fd.append('description', description)
  const res = await apiFetchBinary(`${API_BASE}/journal-entries/${id}/attachments/`, {
    method: 'POST',
    headers: {
},
    body: fd,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: `Upload failed: ${res.status}` }))
    throw new Error(err.error || err.detail || `Upload failed: ${res.status}`)
  }
  return res.json()
}

export async function deleteJournalEntryAttachment(je_id: string, att_id: string): Promise<void> {
  const res = await apiFetchBinary(`${API_BASE}/journal-entries/${je_id}/attachments/${att_id}`, {
    method: 'DELETE',
    headers: {
},
  })
  if (!res.ok && res.status !== 204) {
    const err = await res.json().catch(() => ({ error: `Delete failed: ${res.status}` }))
    throw new Error(err.error || err.detail || `Delete failed: ${res.status}`)
  }
}

// ─── Recurring journal entries ────────────────────────────────────────────────

export type RecurringFrequency = 'monthly' | 'quarterly' | 'semiannual' | 'annual'

export interface RecurringJEListItem {
  id: string
  name: string
  description: string
  journal_type: string
  frequency: RecurringFrequency
  frequency_display: string
  day_of_period: number
  company: string | null
  company_code: string | null
  currency_code: string
  currency: string
  start_date: string
  end_date: string | null
  last_generated_for: string | null
  is_active: boolean
  line_count: number
  created_at: string
  updated_at: string
}

export interface RecurringJELineInput {
  account: string
  description?: string
  debit_amount: string
  credit_amount: string
  contact?: string | null
}

export interface RecurringJELine extends RecurringJELineInput {
  id: string
  account_code: string
  account_name: string
}

export interface RecurringJEDetail extends RecurringJEListItem {
  lines: RecurringJELine[]
}

export interface CreateRecurringJEInput {
  name: string
  description: string
  journal_type: string
  frequency: RecurringFrequency
  day_of_period: number
  currency_code: string
  start_date: string
  end_date?: string | null
  company?: string | null
  is_active?: boolean
  lines: RecurringJELineInput[]
}

export async function getRecurringJEs(params?: { is_active?: boolean }): Promise<PaginatedResponse<RecurringJEListItem>> {
  return apiFetch<PaginatedResponse<RecurringJEListItem>>(
    `/recurring-journal-entries/${_qs({ is_active: params?.is_active === undefined ? undefined : String(params.is_active) })}`,
  )
}

export async function getRecurringJE(id: string): Promise<RecurringJEDetail> {
  return apiFetch<RecurringJEDetail>(`/recurring-journal-entries/${id}/`)
}

export async function createRecurringJE(data: CreateRecurringJEInput): Promise<RecurringJEDetail> {
  return apiFetch<RecurringJEDetail>('/recurring-journal-entries/', {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

export async function updateRecurringJE(id: string, data: Partial<CreateRecurringJEInput>): Promise<RecurringJEDetail> {
  return apiFetch<RecurringJEDetail>(`/recurring-journal-entries/${id}/`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  })
}

export async function deleteRecurringJE(id: string): Promise<void> {
  await apiFetch<void>(`/recurring-journal-entries/${id}/`, { method: 'DELETE' })
}

export async function runRecurringJEs(target_date?: string): Promise<{
  period_end: string
  templates_considered: number
  entries_generated: number
  entries_skipped: number
  errors: { template: string; error: string }[]
}> {
  return apiFetch(`/recurring-journal-entries/run-due/`, {
    method: 'POST',
    body: JSON.stringify(target_date ? { target_date } : {}),
  })
}

// ─── Claims recoveries: subrogation + salvage ────────────────────────────────

export type SubrogationStatus = 'pending' | 'partial' | 'fully_recovered' | 'written_off' | 'in_litigation'
export type SalvageStatus     = 'pending' | 'for_sale' | 'sold' | 'scrapped' | 'retained'

export type PrescriptionRisk = 'SAFE' | 'WATCH' | 'URGENT' | 'CRITICAL' | 'EXPIRED' | 'UNKNOWN'

export interface PrescriptionInfo {
  risk: PrescriptionRisk
  expires_on: string | null
  days_remaining: number | null
  expired: boolean
  basis: string | null
  needs_attention: boolean
}

export interface Subrogation {
  id: string
  claim_reference: string
  incident_date: string | null
  company: string | null
  company_code: string | null
  claim_type: string
  claim_type_display: string
  third_party_name: string
  third_party_insurer: string
  third_party_contact: string | null
  third_party_contact_name: string | null
  appointed_to: string | null
  appointed_to_name: string | null
  date_appointed: string | null
  assessor_fees: string | null
  repair_costs: string | null
  client_excess: string | null
  towing_fees: string | null
  legal_fees: string | null
  salvage_amount: string | null
  claim_paid_amount: string
  expected_recovery: string
  actual_recovery: string
  total_recoverable: string
  amount_recovered: string
  outstanding_balance: string
  outstanding: string
  recovery_pct: string
  age_days: number | null
  age_bucket_label: string
  prescription: PrescriptionInfo
  last_recovery_date: string | null
  status: SubrogationStatus
  status_display: string
  notes: string
  graphite_id: string
  created_by_username: string | null
  created_at: string
  updated_at: string
}

export type PanelKind = 'external_lawyer' | 'internal_lawyer' | 'debt_collector' | 'internal_accountant'

export interface SubrogationPanelMember {
  id: string
  name: string
  kind: PanelKind
  kind_display: string
  contact_name: string
  contact_email: string
  contact_phone: string
  is_active: boolean
  notes: string
  open_case_count: number
  created_at: string
  updated_at: string
}

export type ReceiptMethod = 'realpay' | 'cash' | 'eft' | 'pos' | 'other'

export interface SubrogationReceipt {
  id: string
  subrogation: string
  claim_reference: string
  amount: string
  received_date: string
  method: ReceiptMethod
  method_display: string
  reference: string
  realpay_txn_id: string
  notes: string
  created_by_username: string | null
  created_at: string
}

export interface Salvage {
  id: string
  claim_reference: string
  incident_date: string | null
  company: string | null
  company_code: string | null
  asset_description: string
  estimated_value: string
  sale_proceeds: string
  gain_loss: string
  sale_date: string | null
  buyer_name: string
  buyer_contact: string | null
  buyer_contact_name: string | null
  status: SalvageStatus
  status_display: string
  notes: string
  graphite_id: string
  created_by_username: string | null
  created_at: string
  updated_at: string
}

export interface RecoveryImportBatch {
  id: string
  kind: 'subrogation' | 'salvage'
  kind_display: string
  file_name: string
  rows_total: number
  rows_valid: number
  rows_invalid: number
  rows_skipped_dup: number
  rows_imported: number
  parsed_rows: Record<string, any>[]
  validation_errors: { row_index: number; field: string; message: string }[]
  status: 'draft' | 'committed' | 'failed'
  status_display: string
  company: string | null
  company_code: string | null
  created_at: string
  committed_at: string | null
}

export async function getSubrogations(params?: { status?: string; search?: string; page?: number }): Promise<PaginatedResponse<Subrogation>> {
  return apiFetch<PaginatedResponse<Subrogation>>(
    `/subrogations/${_qs({ status: params?.status, search: params?.search, page: params?.page ? String(params.page) : undefined })}`,
  )
}

export async function getSalvages(params?: { status?: string; search?: string; page?: number }): Promise<PaginatedResponse<Salvage>> {
  return apiFetch<PaginatedResponse<Salvage>>(
    `/salvages/${_qs({ status: params?.status, search: params?.search, page: params?.page ? String(params.page) : undefined })}`,
  )
}

export interface SubrogationSummary {
  open_count: number
  open_recoverable: string
  recovered_total: string
  recovered_mtd: string
  recovered_ytd: string
  recovery_rate: string
  unassigned: { count: number; balance: string }
  at_risk: { count: number; balance: string }
  ageing: { bucket: string; count: number; balance: string }[]
  prescription: Record<string, { count: number; balance: string }>
  top_counterparties: { name: string; count: number; balance: string }[]
}

export async function getSubrogationSummary(): Promise<SubrogationSummary> {
  return apiFetch<SubrogationSummary>('/subrogations/summary/')
}

// ─── Subrogation GL-account assignment ───────────────────────────────────────
export interface SubrogationGLConfig {
  recovery_income_account: string | null
  recovery_income_account_code: string | null
  recovery_income_account_name: string | null
  revenue_accounts: { id: string; code: string; name: string }[]
}

export async function getSubrogationGLConfig(): Promise<SubrogationGLConfig> {
  return apiFetch<SubrogationGLConfig>('/subrogation-gl-config/')
}

export async function setSubrogationGLConfig(accountId: string | null): Promise<SubrogationGLConfig> {
  return apiFetch<SubrogationGLConfig>('/subrogation-gl-config/', {
    method: 'PUT', body: JSON.stringify({ recovery_income_account: accountId }),
  })
}

// ─── RealPay import space ────────────────────────────────────────────────────
export interface RealPayImportResult {
  committed: boolean
  merchant: string
  stats: {
    total: number; other_merchant: number; not_successful: number
    matched: number; unmatched: number; duplicate: number; posted: number; zero_amount: number
  }
  posted_amount: string
  unmatched_client_numbers: string[]
  unmatched_total: number
}

export async function importSubrogationRealPay(file: File, commit: boolean): Promise<RealPayImportResult> {
  const fd = new FormData()
  fd.append('file', file)
  if (commit) fd.append('commit', 'true')
  const res = await apiFetchBinary(`${API_BASE}/subrogation-realpay-import/`, { method: 'POST', headers: {}, body: fd })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: `Upload failed: ${res.status}` }))
    throw new Error(err.error || err.detail || `Upload failed: ${res.status}`)
  }
  return res.json()
}

export async function getSubrogation(id: string): Promise<Subrogation> {
  return apiFetch<Subrogation>(`/subrogations/${id}/`)
}

export async function createSubrogation(data: Partial<Subrogation>): Promise<Subrogation> {
  return apiFetch<Subrogation>('/subrogations/', { method: 'POST', body: JSON.stringify(data) })
}

export async function updateSubrogation(id: string, data: Partial<Subrogation>): Promise<Subrogation> {
  return apiFetch<Subrogation>(`/subrogations/${id}/`, { method: 'PATCH', body: JSON.stringify(data) })
}

// ─── Subrogation panel (who we appoint to collect) ───────────────────────────
export async function getSubrogationPanel(params?: { kind?: string; active?: boolean; search?: string }): Promise<PaginatedResponse<SubrogationPanelMember>> {
  return apiFetch<PaginatedResponse<SubrogationPanelMember>>(
    `/subrogation-panel/${_qs({ kind: params?.kind, active: params?.active ? '1' : undefined, search: params?.search })}`,
  )
}

export async function createPanelMember(data: Partial<SubrogationPanelMember>): Promise<SubrogationPanelMember> {
  return apiFetch<SubrogationPanelMember>('/subrogation-panel/', { method: 'POST', body: JSON.stringify(data) })
}

// ─── Subrogation receipts (money collected) ──────────────────────────────────
export async function getSubrogationReceipts(subrogationId: string): Promise<PaginatedResponse<SubrogationReceipt>> {
  return apiFetch<PaginatedResponse<SubrogationReceipt>>(`/subrogation-receipts/${_qs({ subrogation: subrogationId })}`)
}

export async function createSubrogationReceipt(data: Partial<SubrogationReceipt>): Promise<SubrogationReceipt> {
  return apiFetch<SubrogationReceipt>('/subrogation-receipts/', { method: 'POST', body: JSON.stringify(data) })
}

export async function createSalvage(data: Partial<Salvage>): Promise<Salvage> {
  return apiFetch<Salvage>('/salvages/', { method: 'POST', body: JSON.stringify(data) })
}

export async function updateSalvage(id: string, data: Partial<Salvage>): Promise<Salvage> {
  return apiFetch<Salvage>(`/salvages/${id}/`, { method: 'PATCH', body: JSON.stringify(data) })
}

export async function previewRecoveryImport(kind: 'subrogation' | 'salvage', file: File, company?: string): Promise<RecoveryImportBatch> {
  const fd = new FormData()
  fd.append('file', file)
  if (company) fd.append('company', company)
  const res = await apiFetchBinary(`${API_BASE}/recovery-imports/preview/${kind}/`, {
    method: 'POST',
    headers: {
},
    body: fd,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: `Upload failed: ${res.status}` }))
    throw new Error(err.error || err.detail || `Upload failed: ${res.status}`)
  }
  return res.json()
}

export async function commitRecoveryImport(id: string): Promise<{
  created: number
  skipped_duplicates: number
  errors: { row_index: number; field: string; message: string }[]
  batch: RecoveryImportBatch
}> {
  return apiFetch(`/recovery-imports/${id}/commit/`, { method: 'POST' })
}

// ─── Payroll ─────────────────────────────────────────────────────────────────

export interface Employee {
  id: string
  employee_number: string
  full_name: string
  department: string
  job_title: string
  email: string
  phone: string
  hire_date: string | null
  termination_date: string | null
  company: string | null
  company_code: string | null
  status: 'active' | 'on_leave' | 'suspended' | 'terminated'
  status_display: string
  bank_name: string
  bank_account_no: string
  bank_branch: string
  external_ref: string
  // Terminated Employee Archive (Oprah Mogomotsi feature request, 2026-08-13)
  keep_access_after_exit: boolean
  is_archived: boolean
  archived_at: string | null
  archived_by_name: string | null
  archive_reason: string
  retention_years: number
  retention_expiry_date: string | null
  created_at: string
  updated_at: string
}

export interface PayrollPeriod {
  id: string
  period_name: string
  start_date: string
  end_date: string
  pay_date: string | null
  status: 'open' | 'locked' | 'approved' | 'paid'
  status_display: string
  payslip_count: number
  notes: string
  created_at: string
  updated_at: string
}

export interface PayslipLine {
  id: string
  component: string
  component_code: string
  component_name: string
  component_kind: string
  amount: string
  notes: string
}

export interface Payslip {
  id: string
  employee: string
  employee_name: string
  department: string
  period: string
  period_name: string
  company: string | null
  gross_amount: string
  paye_amount: string
  net_amount: string
  ctc_amount: string
  status: 'draft' | 'approved' | 'paid' | 'cancelled'
  status_display: string
  notes: string
  lines: PayslipLine[]
  created_at: string
  updated_at: string
}

export async function getEmployees(params?: { status?: string; department?: string; search?: string; page?: number; archived?: boolean }): Promise<PaginatedResponse<Employee>> {
  return apiFetch<PaginatedResponse<Employee>>(
    `/employees/${_qs({
      status: params?.status,
      department: params?.department,
      search: params?.search,
      page: params?.page ? String(params.page) : undefined,
      archived: params?.archived ? '1' : undefined,
    })}`,
  )
}

// ── Terminated Employee Archive (Oprah Mogomotsi feature request, bug report
// d0f05edc-06f5-4084-94ce-b613e54e7665, 2026-08-13) ─────────────────────────
// Archive/unarchive are manual HR actions, both reason-carrying and audit-
// logged server-side. Restricted to HR / Finance Managers — the backend
// enforces this independently (403 for anyone else).
export async function archiveEmployee(id: string, reason: string, retentionYears?: number): Promise<Employee> {
  return apiFetch<Employee>(`/employees/${id}/archive/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason, retention_years: retentionYears }),
  })
}

// Terminate Employee (D. Ikgopoleng feature request, bug dfc0b768; CFO-authorised
// 2026-09-10). Records the exit and flips the status — never deletes the profile.
// Restricted to HR / Finance Managers; the backend enforces this independently.
/**
 * Keep a leaver's Omni login open after their exit date (external contractor).
 * CFO 2026-09-18 — the automatic leaver close-off skips anyone ticked here.
 */
export async function setEmployeeKeepAccess(id: string, keep: boolean): Promise<Employee> {
  return apiFetch<Employee>(`/employees/${id}/`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ keep_access_after_exit: keep }),
  })
}

export async function terminateEmployee(
  id: string, terminationDate: string, reason: string, notes?: string, archive?: boolean,
): Promise<Employee> {
  return apiFetch<Employee>(`/employees/${id}/terminate/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ termination_date: terminationDate, reason, notes, archive }),
  })
}

export async function unarchiveEmployee(id: string, reason: string): Promise<Employee> {
  return apiFetch<Employee>(`/employees/${id}/unarchive/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason }),
  })
}

// Bulk archive (Unami Butale feature request, 2026-08-22): archive several
// terminated employees in one HR action, one shared reason. Each still goes
// through the single-record service, so a leaver without a termination date is
// reported as skipped rather than silently dropped.
export interface BulkArchiveResult {
  archived: number
  skipped: number
  results: { id: string; name: string | null; ok: boolean; detail?: string }[]
}

export async function bulkArchiveEmployees(
  ids: string[], reason: string, retentionYears?: number,
): Promise<BulkArchiveResult> {
  return apiFetch<BulkArchiveResult>(`/employees/bulk-archive/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids, reason, retention_years: retentionYears }),
  })
}

export interface ArchiveExpiringResult {
  count: number
  within_days: number
  employees: Employee[]
}

export async function getArchiveExpiring(days = 90): Promise<ArchiveExpiringResult> {
  return apiFetch<ArchiveExpiringResult>(`/employees/archive-expiring/${_qs({ days: String(days) })}`)
}

export async function getPayrollPeriods(): Promise<PaginatedResponse<PayrollPeriod>> {
  return apiFetch<PaginatedResponse<PayrollPeriod>>('/payroll-periods/')
}

// ── Payroll "Add Employee" with Finance sign-off (Pako Kago 2026-08-12) ───────
// Stage a new employee for a period; it stays PENDING (no effect on headcount
// or totals) until a Finance signer approves — maker-checker with SoD.
export interface PayrollAdditionMeta {
  periods: { id: string; period_name: string }[]
  companies: { id: string; name: string; code: string }[]
  departments: string[]
  allowance_types: { code: string; name: string }[]
  can_approve: boolean
}
export interface PayrollAdditionRow {
  id: string
  status: string
  status_display: string
  full_name: string
  employee_number: string
  department: string
  company: { id: string; name: string; code: string }
  period: { id: string; period_name: string }
  basic: string; commission: string; incentive: string
  allowances: { code: string; amount: string }[]
  gross_estimate: string
  requested_by: string; requested_at: string | null
  decided_by: string; decided_at: string | null
  rejection_comment: string
  linked_existing: boolean
  created_payslip_id: string | null
  can_action?: boolean   // this viewer may Approve/Reject this row (Finance signer, not the submitter)
  blocked_reason?: string // set when a pending row can't be approved yet (e.g. period signed off — reopen first)
}
export interface PayrollAdditionListResp {
  results: PayrollAdditionRow[]
  can_approve: boolean
  pending_for_me: number
}
export async function getPayrollAdditionMeta(): Promise<PayrollAdditionMeta> {
  return apiFetch<PayrollAdditionMeta>('/payroll/additions/meta/')
}
export async function listPayrollAdditions(
  params?: { status?: string; period_id?: string; company_id?: string },
): Promise<PayrollAdditionListResp> {
  return apiFetch<PayrollAdditionListResp>(
    `/payroll/additions/${_qs({ status: params?.status, period_id: params?.period_id, company_id: params?.company_id })}`)
}
export async function createPayrollAddition(body: {
  period_id: string; company_id: string; full_name: string; employee_number?: string
  department?: string; basic: string; commission?: string; incentive?: string
  allowances?: { code: string; amount: string }[]
}): Promise<{ request: PayrollAdditionRow; warnings: string[] }> {
  return apiFetch(`/payroll/additions/`, { method: 'POST', body: JSON.stringify(body) })
}
export async function approvePayrollAddition(id: string): Promise<{ request: PayrollAdditionRow }> {
  return apiFetch(`/payroll/additions/${id}/approve/`, { method: 'POST', body: JSON.stringify({}) })
}
export async function rejectPayrollAddition(id: string, comment: string): Promise<{ request: PayrollAdditionRow }> {
  return apiFetch(`/payroll/additions/${id}/reject/`, { method: 'POST', body: JSON.stringify({ comment }) })
}

// ── Records file requests (Tshepo Maswabi 2026-08-11) ────────────────────────
// Staff request a physical file → Human Capital / Records approve or deny → a
// documented trail. Maker-checker + SoD enforced server-side.
export interface RecordFileRequestRow {
  id: string
  status: string
  status_display: string
  record: { id: string; reference: string; title: string; company: string }
  reason: string
  requested_by: string
  requested_at: string | null
  decided_by: string
  decided_at: string | null
  decision_note: string
}
export async function createRecordFileRequest(record_id: string, reason: string): Promise<{ request: RecordFileRequestRow }> {
  return apiFetch('/records/file-requests/', { method: 'POST', body: JSON.stringify({ record_id, reason }) })
}
export async function listRecordFileRequests(
  scope: 'mine' | 'to_approve' = 'mine',
): Promise<{ results: RecordFileRequestRow[]; can_approve: boolean }> {
  return apiFetch(`/records/file-requests/?scope=${scope}`)
}
export async function approveRecordFileRequest(id: string): Promise<{ request: RecordFileRequestRow }> {
  return apiFetch(`/records/file-requests/${id}/approve/`, { method: 'POST', body: JSON.stringify({}) })
}
export async function denyRecordFileRequest(id: string, note: string): Promise<{ request: RecordFileRequestRow }> {
  return apiFetch(`/records/file-requests/${id}/deny/`, { method: 'POST', body: JSON.stringify({ note }) })
}

// ── Commission statement download (Bokani Makosha 2026-08-12) ─────────────────
// The reviewer fetches the original uploaded workbook (authed, via apiFetchBinary).
export function commissionStatementPath(id: string): string {
  return `/commissions/submissions/${id}/statement/`
}

// ── Payroll sign-off (CFO directive 2026-07-26) ──────────────────────────────
// The board deliberately shows EVERY entity for the month, not just the one in
// the company switcher — the point is to see the whole group in one place. The
// backend ignores the auto-injected company filter.
export interface PayrollSignOffRow {
  id?: string | null
  company_id: string
  company: string
  period: string
  live_headcount: number
  live_gross: string
  live_paye: string
  live_net: string
  // Dual sign-off (2026-07-28): HR (Unami/Dorothy) + Finance (Kago/Pako).
  hr_signed_by?: string | null
  hr_signed_at?: string | null
  fin_signed_by?: string | null
  fin_signed_at?: string | null
  needs_hr: boolean
  needs_fin: boolean
  closed: boolean
  rejected_by?: string | null
  rejection_reason?: string
  drift?: { submitted: Record<string, string>; live: Record<string, string> } | null
}

export interface PayrollSignOffBoard {
  period: string | null
  period_id?: string
  periods: string[]
  rows: PayrollSignOffRow[]
  can_sign_hr: boolean
  can_sign_fin: boolean
  my_side: 'hr' | 'finance' | 'both' | null
}

export async function getPayrollSignOffBoard(period?: string): Promise<PayrollSignOffBoard> {
  const q = period ? `?period=${encodeURIComponent(period)}` : ''
  return apiFetch<PayrollSignOffBoard>(`/payroll/sign-off/${q}`)
}

/** HR or Finance signs a company's month. side is required only for a CFO/superuser back-stop. */
export async function signPayroll(
  period: string, company: string, side?: 'hr' | 'finance',
): Promise<PayrollSignOffRow> {
  return apiFetch<PayrollSignOffRow>('/payroll/sign-off/sign/', {
    method: 'POST',
    body: JSON.stringify({ period, company, ...(side ? { side } : {}) }),
  })
}

export async function rejectPayrollSignOff(
  period: string, company: string, reason: string,
): Promise<PayrollSignOffRow> {
  return apiFetch<PayrollSignOffRow>('/payroll/sign-off/reject/', {
    method: 'POST',
    body: JSON.stringify({ period, company, reason }),
  })
}

export interface NewPayrollPeriodInput {
  period_name: string      // e.g. "2026-07"
  start_date: string       // ISO date
  end_date: string         // ISO date
  pay_date?: string        // ISO date (optional)
  status?: string          // defaults to "open" server-side
  notes?: string
}

// Create a payroll period (e.g. open July 2026). Restricted server-side to
// approver titles (CFO / Finance Manager / Financial Controller).
export async function createPayrollPeriod(input: NewPayrollPeriodInput): Promise<PayrollPeriod> {
  return apiFetch<PayrollPeriod>('/payroll-periods/', {
    method: 'POST',
    body: JSON.stringify(input),
  })
}

// One-button reveal of an employee's FULL (decrypted) bank account + national
// ID. The fields are encrypted at rest and masked in every list/detail; this
// discloses them on demand for an authorised viewer (HR / C-suite / Finance)
// and the reveal is recorded in the audit trail server-side. CFO 2026-07-20.
export interface EmployeePiiReveal {
  id: string
  bank_name: string
  bank_account_no: string
  bank_branch: string
  national_id: string
}

export async function revealEmployeePii(id: string): Promise<EmployeePiiReveal> {
  return apiFetch<EmployeePiiReveal>(`/employees/${id}/reveal/`)
}

export interface PayrollImportBatch {
  id: string
  source: string
  file_name: string
  period: string | null
  period_name?: string | null
  company: string | null
  rows_total: number
  rows_valid: number
  rows_invalid: number
  rows_imported: number
  rows_skipped_dup: number
  status: string
  status_display?: string
  created_by_username?: string | null
  first_approver_username?: string | null
  validation_errors: { row_index: number; field: string; message: string }[]
}

// List payroll import batches (newest first) — used to surface pending batches
// awaiting approval / commit on the Run Payroll page.
export async function listPayrollImportBatches(): Promise<PaginatedResponse<PayrollImportBatch>> {
  return apiFetch<PaginatedResponse<PayrollImportBatch>>('/payroll-imports/')
}

// Approve a payroll import (single approval, CFO 2026-06-20). The uploader
// cannot approve their own batch — a different HR/Finance Manager or the CFO must.
export async function approvePayrollImport(batchId: string): Promise<PayrollImportBatch> {
  return apiFetch<PayrollImportBatch>(`/payroll-imports/${batchId}/approve/`, { method: 'POST' })
}

// Discard a not-yet-approved payroll import so a wrong upload can be replaced.
// The uploader can discard their own draft; an approver can discard any. The
// backend blocks discarding an approved/committed batch.
export async function discardPayrollImport(batchId: string): Promise<{ discarded: boolean; file_name: string }> {
  return apiFetch(`/payroll-imports/${batchId}/discard/`, { method: 'POST' })
}

/** Upload a payroll file for preview. Creates a DRAFT batch (dual-approval still required before commit). */
export async function previewPayrollImport(
  file: File, periodId: string, companyId?: string,
): Promise<PayrollImportBatch> {
  const form = new FormData()
  form.append('file', file)
  form.append('period', periodId)
  if (companyId) form.append('company', companyId)
  return apiFetch<PayrollImportBatch>('/payroll-imports/preview/', { method: 'POST', body: form })
}

export async function commitPayrollImport(
  batchId: string,
): Promise<{ created: number; skipped_duplicates: number; errors: unknown[]; batch: PayrollImportBatch }> {
  return apiFetch(`/payroll-imports/${batchId}/commit/`, { method: 'POST' })
}

// ─── Bank-details bulk upload (CFO 2026-07-15) ───────────────────────────────
// Uploader files the salary bank file → bank name derived from branch code →
// Dorothy (HR Manager) approves → commits to the employee bank fields.
export interface BankImportRow {
  row: number
  name: string
  account: string
  branch: string
  matched: boolean
  employee: string | null
  employee_number: string | null
  bank_name: string
  bank_source: string
  status: 'ready' | 'ready_no_bank' | 'no_match' | 'incomplete' | 'account_check'
  warning: string
}
export interface BankImportBatch {
  id: string
  file_name: string
  status: 'pending' | 'approved' | 'rejected'
  status_display: string
  rows_total: number
  rows_matched: number
  rows_committed: number
  created_by: string | null
  created_at: string
  approved_by: string | null
  approved_at: string | null
  rejected_by: string | null
  rejection_reason: string
  can_approve: boolean
  rows?: BankImportRow[]
}
export async function uploadBankImport(
  file: File,
): Promise<{ id: string; rows_total: number; rows_matched: number; rows_committable: number; rows: BankImportRow[] }> {
  const form = new FormData()
  form.append('file', file)
  return apiFetch('/payroll/bank-imports/upload/', { method: 'POST', body: form })
}
export async function listBankImports(): Promise<{ results: BankImportBatch[]; can_approve: boolean }> {
  return apiFetch('/payroll/bank-imports/')
}
export async function getBankImport(id: string): Promise<BankImportBatch> {
  return apiFetch(`/payroll/bank-imports/${id}/`)
}
export async function approveBankImport(id: string): Promise<BankImportBatch> {
  return apiFetch(`/payroll/bank-imports/${id}/approve/`, { method: 'POST' })
}
export async function rejectBankImport(id: string, reason: string): Promise<BankImportBatch> {
  return apiFetch(`/payroll/bank-imports/${id}/reject/`, { method: 'POST', body: JSON.stringify({ reason }) })
}

// ─── Component-line backfill (CFO 2026-07-14) ────────────────────────────────
// Fixes "payroll report component fields not populating": totals-only payslips
// have no component lines. The payroll team uploads the register here and Omni
// loads the per-component detail, guarded so an approved Gross never changes.
export interface PayrollCoverageRow {
  period: string; company: string; payslips: number
  with_components: number; totals_only: number; complete: boolean
}
export async function getPayrollComponentCoverage(): Promise<{ coverage: PayrollCoverageRow[] }> {
  return apiFetch('/payroll/component-coverage/')
}

export interface RegisterBackfillSummary {
  ok: boolean; period: string; company: string; commit: boolean
  payslips: number; matched: number; reconciled: number; written: number
  recon_fail: number; unmatched: number
  fails: { employee: string; register_gross: string; system_gross: string }[]
  unmatched_names: string[]
}
/** Ask the Payroll Assistant a natural-language question (PII-free snapshot only). */
export async function askPayrollAssistant(question: string): Promise<{ answer: string; runs: number }> {
  return apiFetch('/payroll/assistant/', { method: 'POST', body: JSON.stringify({ question }) })
}

/** Upload a payroll register to backfill component lines. Dry-run unless commit. */
export async function uploadPayrollRegister(
  file: File, period: string, company: string, commit: boolean,
): Promise<RegisterBackfillSummary> {
  const form = new FormData()
  form.append('file', file)
  form.append('period', period)
  form.append('company', company)
  if (commit) form.append('commit', 'true')
  return apiFetch('/payroll/register-upload/', { method: 'POST', body: form })
}

export async function getPayslips(params?: { period?: string; period_name?: string; status?: string; search?: string; page?: number }): Promise<PaginatedResponse<Payslip>> {
  return apiFetch<PaginatedResponse<Payslip>>(
    `/payslips/${_qs({
      period: params?.period,
      period_name: params?.period_name,
      status: params?.status,
      search: params?.search,
      page: params?.page ? String(params.page) : undefined,
    })}`,
  )
}

/** Email one payslip to its employee (per-row 'Send' button). Backend respects
 *  the CFO sign-off release gate and returns a plain reason if it refuses. */
export async function sendPayslipEmail(id: string): Promise<{ sent: boolean; email?: string; reason?: string; detail?: string }> {
  return apiFetch(`/payslips/${id}/send-email/`, { method: 'POST' })
}

export interface SendPayslipsBatchResult {
  sent_count: number
  failed_count: number
  sent: { id: string; employee: string; email: string }[]
  failed: { id: string; employee: string; reason: string; detail: string }[]
}
/** Email a chosen set of payslips (ticked rows). Reports per-slip who got it. */
export async function sendPayslipsBatch(payslipIds: string[]): Promise<SendPayslipsBatchResult> {
  return apiFetch('/payslips/send-batch/', { method: 'POST', body: JSON.stringify({ payslip_ids: payslipIds }) })
}

// ─── Procurement ─────────────────────────────────────────────────────────────

// Purchase orders are raised by Claims, Admin, and HR only.
// Finance does NOT raise POs — it verifies via the FM + CFO approval workflow.
export type PODepartment = 'admin' | 'claims' | 'hr'
export type POStatus =
  | 'draft' | 'pending_fm_approval' | 'pending_cfo_approval' | 'rejected'
  | 'approved' | 'partially_received' | 'fully_received' | 'closed' | 'cancelled'

export interface PurchaseOrderListItem {
  id: string
  po_number: string
  department: PODepartment
  department_display: string
  supplier: string
  supplier_name: string
  issue_date: string
  expected_delivery_date: string | null
  currency_code: string
  total_amount: string
  total_bwp: string
  status: POStatus
  status_display: string
  related_claim_reference: string
  /** "Sent ✓" stamp — set by the email-to-supplier endpoint. */
  last_emailed_at: string | null
  last_emailed_to: string
}

export interface POLine {
  sequence?: number
  id: string
  description: string
  account: string
  account_code: string
  account_name: string
  quantity: string
  unit_price: string
  tax_code: string | null
  tax_rate: string
  tax_amount: string
  discount_amount: string
  line_total: string
  quantity_received: string
  quantity_billed: string
  quantity_outstanding: string
  is_fully_received: boolean
  is_fully_billed: boolean
}

export interface PurchaseOrderDetail extends PurchaseOrderListItem {
  /** Supplier's email on file ('' when none) — prefills the email form. */
  supplier_email: string
  company: string | null
  company_name: string | null
  exchange_rate: string
  discount_percent: string
  discount_total: string
  subtotal: string
  tax_total: string
  related_claim_recovery: string | null
  justification: string
  fiscal_period: string | null
  fiscal_period_name: string | null
  submitted_by: string | null
  submitted_by_username: string | null
  submitted_at: string | null
  fm_approved_by: string | null
  fm_approved_by_username: string | null
  fm_approved_at: string | null
  cfo_approved_by: string | null
  cfo_approved_by_username: string | null
  cfo_approved_at: string | null
  rejection_reason: string
  cancelled_by: string | null
  cancelled_at: string | null
  cancellation_reason: string
  created_by: string
  created_by_username: string
  created_at: string
  updated_at: string
  lines: POLine[]
  /** True only when the current user may cancel this PO (CFO-only, cancellable
   *  state, no goods received). Drives whether the Cancel PO button shows. */
  can_cancel?: boolean
  /** True only when the current user may APPROVE this PO's current leg — mirrors
   *  the fm_approve/cfo_approve guard incl. segregation of duties (the raiser of
   *  a >=10k operational PO is barred). Gates the Approve button so it never
   *  dead-ends on a 400. (2026-09-06.) */
  can_approve?: boolean
  /** True only when the current user may REJECT this PO — approval authority but
   *  NO SoD bar (the raiser may reject their own PO). Kept separate from
   *  can_approve so tightening approve never hides a working Reject. */
  can_reject?: boolean
}

export interface CreatePOLineInput {
  description: string
  /**
   * Optional GL account code. CFO directive 2026-05-22: POs are no longer
   * linked to GL lines at creation — invoices bind to accounts instead.
   * Kept here for the few legacy callers that still pre-bind an account.
   */
  account?: string | null
  quantity: string | number
  unit_price: string | number
  tax_code?: string | null
}

export interface CreatePOInput {
  department: PODepartment
  supplier: string
  company?: string | null
  issue_date: string
  expected_delivery_date?: string | null
  currency_code: string
  exchange_rate?: string | number
  discount_percent?: string | number
  related_claim_reference?: string
  justification?: string
  lines: CreatePOLineInput[]
}

export async function getPurchaseOrders(params?: {
  status?: string; department?: string; supplier?: string; search?: string; page?: number;
  issue_date_from?: string; issue_date_to?: string;
}): Promise<PaginatedResponse<PurchaseOrderListItem>> {
  return apiFetch<PaginatedResponse<PurchaseOrderListItem>>(
    `/purchase-orders/${_qs({
      status: params?.status,
      department: params?.department,
      supplier: params?.supplier,
      search: params?.search,
      issue_date_from: params?.issue_date_from,
      issue_date_to: params?.issue_date_to,
      page: params?.page ? String(params.page) : undefined,
    })}`,
  )
}

export async function getPurchaseOrder(id: string): Promise<PurchaseOrderDetail> {
  return apiFetch<PurchaseOrderDetail>(`/purchase-orders/${id}/`)
}

export async function createPurchaseOrder(data: CreatePOInput): Promise<PurchaseOrderDetail> {
  return apiFetch<PurchaseOrderDetail>('/purchase-orders/', {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

// Amend an existing PO (CFO directive 2026-07-08). The backend allows this
// while the PO is DRAFT or still in the approval queue; amending a submitted
// PO resets it to DRAFT and voids its in-flight approval (re-approval needed).
// Approved POs are locked as a control.
export async function updatePurchaseOrder(id: string, data: CreatePOInput): Promise<PurchaseOrderDetail> {
  return apiFetch<PurchaseOrderDetail>(`/purchase-orders/${id}/`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  })
}

export async function submitPO(id: string): Promise<PurchaseOrderDetail> {
  return apiFetch<PurchaseOrderDetail>(`/purchase-orders/${id}/submit/`, { method: 'POST' })
}

/**
 * Open the PO PDF in a new browser tab so the CFO sees it immediately
 * and can print / Cmd-S. CFO feedback 2026-05-21: the blob-anchor-click
 * pattern saved silently to disk, which felt like "the button does
 * nothing". Now we open inline and only fall back to a forced download
 * when the popup is blocked.
 */
export async function downloadPurchaseOrderPdf(id: string, poNumber?: string): Promise<void> {
  const url = `${API_BASE}/purchase-orders/${id}/pdf/`
  const headers: Record<string, string> = {}
  try {
    const { acquireApiToken, SSO_API_CALLS_READY } = await import('@/auth/msal')
    if (SSO_API_CALLS_READY) {
      const t = await acquireApiToken()
      if (t) headers['Authorization'] = `Bearer ${t}`
    }
  } catch { /* fall through */ }
  if (!headers['Authorization']) {
    const t = getToken()
    if (t && t !== SSO_SENTINEL_TOKEN) headers['Authorization'] = `Token ${t}`
  }
  const res = await fetch(url, { headers, credentials: 'include' })
  if (!res.ok) {
    const txt = await res.text().catch(() => '')
    throw new Error(`PDF failed: HTTP ${res.status} ${txt.slice(0, 200)}`)
  }
  const blob = await res.blob()
  const dlUrl = URL.createObjectURL(blob)
  const filename = `${poNumber || `PO-${id}`}.pdf`

  // Force a real file download via an anchor. Do NOT window.open() a blob: URL —
  // in the OmniDesktop wrapper (and some Edge/Windows configs) the OS is asked to
  // handle the blob: scheme and throws "Get an app to open this 'blob' link".
  const a = document.createElement('a')
  a.href = dlUrl
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(dlUrl), 30000)
}
// One-click "email PO to supplier" — the backend renders the PDF and sends it
// from the logged-in user's own mailbox (CFO directive 2026-07-07).
export interface EmailPoResult {
  sent: boolean
  to: string
  cc: string | null
  from: string
  po_number: string
}
export async function emailPurchaseOrder(
  id: string, to: string, cc?: string, note?: string,
): Promise<EmailPoResult> {
  return apiFetch<EmailPoResult>(`/purchase-orders/${id}/email/`, {
    method: 'POST',
    body: JSON.stringify({ to, cc: cc || '', note: note || '' }),
  })
}

// ── Supplier line-item history — quick-fill on the new-PO form (CFO 2026-07-14)
export interface SupplierHistoryItem {
  description: string
  unit_price: string
  tax_code: string
  last_po_number: string
  last_issue_date: string | null
}
export async function getSupplierPOHistory(
  supplierId: string, companyId?: string,
): Promise<SupplierHistoryItem[]> {
  const qs = new URLSearchParams({ supplier: supplierId })
  if (companyId) qs.set('company', companyId)
  const data = await apiFetch<{ supplier: string; items: SupplierHistoryItem[] }>(
    `/purchase-orders/supplier-history/?${qs.toString()}`,
  )
  return data.items || []
}
// NB: the bill ↔ PO AI verification (BillAIVerification / POBillMatchExtended /
// getPOBillMatches) already exists further down this file — reused, not
// re-declared, by PurchaseOrderBillCheck.

export interface AuditLogEntry {
  id: string
  action: string
  action_display?: string
  user_username: string | null
  description: string | null
  old_values: Record<string, unknown> | null
  new_values: Record<string, unknown> | null
  created_at: string
}

/** Who changed this PO, what changed, and when — CFO/Wangu directive 2026-07-09. */
export async function getPurchaseOrderHistory(poId: string): Promise<AuditLogEntry[]> {
  const data = await apiFetch<{ results?: AuditLogEntry[] } | AuditLogEntry[]>(
    `/audit-log/?table_name=PurchaseOrder&record_id=${poId}`,
  )
  return Array.isArray(data) ? data : (data.results || [])
}
export async function fmApprovePO(id: string): Promise<PurchaseOrderDetail> {
  return apiFetch<PurchaseOrderDetail>(`/purchase-orders/${id}/fm-approve/`, { method: 'POST' })
}
export async function cfoApprovePO(id: string): Promise<PurchaseOrderDetail> {
  return apiFetch<PurchaseOrderDetail>(`/purchase-orders/${id}/cfo-approve/`, { method: 'POST' })
}
export async function rejectPO(id: string, reason: string): Promise<PurchaseOrderDetail> {
  return apiFetch<PurchaseOrderDetail>(`/purchase-orders/${id}/reject/`, {
    method: 'POST',
    body: JSON.stringify({ reason }),
  })
}
export async function cancelPO(id: string, reason: string): Promise<PurchaseOrderDetail> {
  return apiFetch<PurchaseOrderDetail>(`/purchase-orders/${id}/cancel/`, {
    method: 'POST',
    body: JSON.stringify({ reason }),
  })
}

// ─── GRN ────────────────────────────────────────────────────────────────────

export interface GRNListItem {
  id: string
  grn_number: string
  purchase_order: string
  po_number: string
  supplier_name: string
  receipt_date: string
  status: 'draft' | 'posted' | 'cancelled'
  status_display: string
}

export interface GRNLine {
  id: string
  po_line: string
  po_line_description: string
  po_line_quantity: string
  quantity_received: string
  condition: 'good' | 'damaged' | 'rejected'
  notes: string
}

export interface GRNDetail extends GRNListItem {
  delivery_note_reference: string
  notes: string
  received_by: string
  received_by_username: string
  created_by: string
  created_by_username: string
  journal_entry: string | null
  journal_entry_number: string | null
  created_at: string
  updated_at: string
  lines: GRNLine[]
}

export interface CreateGRNLineInput {
  po_line: string
  quantity_received: string | number
  condition?: 'good' | 'damaged' | 'rejected'
  notes?: string
}

export interface CreateGRNInput {
  purchase_order: string
  receipt_date: string
  delivery_note_reference?: string
  received_by: string
  notes?: string
  lines: CreateGRNLineInput[]
}

export async function getGRNs(params?: { purchase_order?: string; status?: string; page?: number }): Promise<PaginatedResponse<GRNListItem>> {
  return apiFetch<PaginatedResponse<GRNListItem>>(
    `/goods-receipt-notes/${_qs({
      purchase_order: params?.purchase_order,
      status: params?.status,
      page: params?.page ? String(params.page) : undefined,
    })}`,
  )
}

export async function getGRN(id: string): Promise<GRNDetail> {
  return apiFetch<GRNDetail>(`/goods-receipt-notes/${id}/`)
}

// The create endpoint re-serialises through GoodsReceiptNoteCreateSerializer,
// which returns ONLY these fields — not the full GRNDetail shape. Typing it
// honestly (was Promise<GRNDetail>) stops callers assuming status/supplier_name
// are present, and keeps `id` — the field whose earlier absence sent `undefined`
// into the post_grn URL (Oprah, 2026-08-29).
export interface CreateGRNResult {
  id: string
  grn_number: string
  purchase_order: string
  receipt_date: string
  delivery_note_reference: string
  received_by: string
  notes: string
  lines: GRNLine[]
}

export async function createGRN(data: CreateGRNInput): Promise<CreateGRNResult> {
  return apiFetch<CreateGRNResult>('/goods-receipt-notes/', {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

export async function postGRN(id: string): Promise<{ grn: GRNDetail; journal_entry_id: string; journal_entry_number: string }> {
  return apiFetch(`/goods-receipt-notes/${id}/post_grn/`, { method: 'POST' })
}

// ─── 3-way match ─────────────────────────────────────────────────────────────

export interface POBillMatch {
  id: string
  purchase_order: string
  po_number: string
  bill: string
  bill_number: string
  bill_total: string
  match_status: 'matched' | 'variance_quantity' | 'variance_price' | 'variance_both' | 'override'
  match_status_display: string
  quantity_variance: string
  price_variance: string
  override_reason: string
  matched_by: string
  matched_by_username: string
  matched_at: string
}

export async function createPOBillMatch(data: {
  purchase_order: string
  bill: string
  override_reason?: string
}): Promise<POBillMatch> {
  return apiFetch('/po-bill-matches/create-match/', {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

// ─── FX Revaluation ──────────────────────────────────────────────────────────

export interface FXRevaluationLineDTO {
  id: string
  account: string
  account_code: string
  account_name: string
  currency_code: string
  balance_foreign: string
  closing_rate: string
  book_bwp: string
  revalued_bwp: string
  revaluation_amount: string
}

export interface FXRevaluationListItem {
  id: string
  period: string
  period_name: string
  company: string | null
  run_date: string
  status: 'draft' | 'posted' | 'reversed' | 'cancelled'
  status_display: string
  total_gain_bwp: string
  total_loss_bwp: string
  net_bwp: string
}

export interface FXRevaluationDetail extends FXRevaluationListItem {
  journal_entry: string | null
  journal_entry_number: string | null
  notes: string
  created_by: string
  created_by_username: string
  created_at: string
  updated_at: string
  lines: FXRevaluationLineDTO[]
}

export async function getFXRevaluations(): Promise<PaginatedResponse<FXRevaluationListItem>> {
  return apiFetch('/fx-revaluations/')
}

export async function getFXRevaluation(id: string): Promise<FXRevaluationDetail> {
  return apiFetch(`/fx-revaluations/${id}/`)
}

export async function runFXRevaluation(period_id: string, opts?: { company_id?: string; dry_run?: boolean }): Promise<FXRevaluationDetail> {
  return apiFetch('/fx-revaluations/run/', {
    method: 'POST',
    body: JSON.stringify({
      period_id,
      company_id: opts?.company_id,
      dry_run: opts?.dry_run ?? false,
    }),
  })
}

// ─── FX-001 Exchange Rates (Bank of Botswana mid-rates + FM approval) ──────

export interface ExchangeRate {
  id: string
  from_currency: string
  from_currency_name: string | null
  to_currency: string
  to_currency_name: string | null
  rate: string
  effective_date: string
  source: 'manual' | 'bank_of_botswana'
  notes?: string
  loaded_by: string | null
  loaded_by_name: string | null
  approved_by: string | null
  approved_by_name: string | null
  approved_at: string | null
  is_approved: boolean
  created_at: string
  updated_at?: string
}

export async function getExchangeRates(params?: {
  from_currency?: string; to_currency?: string; effective_date?: string;
  approved_only?: boolean; page_size?: string;
}): Promise<PaginatedResponse<ExchangeRate>> {
  const q = new URLSearchParams()
  if (params?.from_currency) q.set('from_currency', params.from_currency)
  if (params?.to_currency) q.set('to_currency', params.to_currency)
  if (params?.effective_date) q.set('effective_date', params.effective_date)
  if (params?.approved_only) q.set('approved_only', 'true')
  q.set('page_size', params?.page_size ?? '500')
  return apiFetch<PaginatedResponse<ExchangeRate>>(`/exchange-rates/?${q.toString()}`)
}

export interface CreateExchangeRateInput {
  from_currency: string
  to_currency: string
  rate: string
  effective_date: string
  source?: 'manual' | 'bank_of_botswana'
  notes?: string
}

export async function createExchangeRate(data: CreateExchangeRateInput): Promise<ExchangeRate> {
  const body: CreateExchangeRateInput = { source: 'bank_of_botswana', ...data }
  return apiFetch<ExchangeRate>('/exchange-rates/', {
    method: 'POST', body: JSON.stringify(body),
  })
}

export async function updateExchangeRate(id: string, data: Partial<CreateExchangeRateInput>): Promise<ExchangeRate> {
  return apiFetch<ExchangeRate>(`/exchange-rates/${id}/`, {
    method: 'PATCH', body: JSON.stringify(data),
  })
}

export async function approveExchangeRate(id: string): Promise<ExchangeRate> {
  return apiFetch<ExchangeRate>(`/exchange-rates/${id}/approve/`, { method: 'POST' })
}

export async function clearExchangeRateApproval(id: string): Promise<ExchangeRate> {
  return apiFetch<ExchangeRate>(`/exchange-rates/${id}/clear-approval/`, { method: 'POST' })
}

// ─── Vendor Bank Accounts (maker-checker) ────────────────────────────────────

export type VendorBankStatus = 'draft' | 'pending_approval' | 'active' | 'rejected' | 'retired'

export interface VendorBankAccountListItem {
  id: string
  contact: string
  contact_name: string
  bank_name: string
  account_holder_name: string
  masked_account: string
  currency_code: string
  is_default: boolean
  email?: string          // remembered POP email (CFO 2026-08-22)
  status: VendorBankStatus
  status_display: string
  name_mismatch: boolean
  created_at: string
}

export interface VendorBankAccountDetail extends VendorBankAccountListItem {
  account_number: string
  branch_code: string
  branch_name: string
  swift_bic: string
  iban: string
  proof_document: string | null
  notes: string
  submitted_by: string | null
  submitted_by_username: string | null
  submitted_at: string | null
  approved_by: string | null
  approved_by_username: string | null
  approved_at: string | null
  rejected_by: string | null
  rejected_by_username: string | null
  rejected_at: string | null
  rejection_reason: string
  retired_by: string | null
  retired_by_username: string | null
  retired_at: string | null
  retirement_reason: string
  replaces: string | null
  created_by: string
  created_by_username: string
  updated_at: string
}

export interface CreateVendorBankAccountInput {
  contact: string
  bank_name: string
  account_holder_name: string
  account_number: string
  branch_code?: string
  branch_name?: string
  swift_bic?: string
  iban?: string
  email?: string          // remembered POP email (CFO 2026-08-22)
  currency_code: string
  is_default?: boolean
  notes?: string
  replaces?: string | null
}

export async function getVendorBankAccounts(params?: {
  status?: string; contact?: string; search?: string; page?: number;
}): Promise<PaginatedResponse<VendorBankAccountListItem>> {
  return apiFetch<PaginatedResponse<VendorBankAccountListItem>>(
    `/vendor-bank-accounts/${_qs({
      status: params?.status,
      contact: params?.contact,
      search: params?.search,
      page: params?.page ? String(params.page) : undefined,
    })}`,
  )
}

export async function getVendorBankAccount(id: string): Promise<VendorBankAccountDetail> {
  return apiFetch<VendorBankAccountDetail>(`/vendor-bank-accounts/${id}/`)
}

export async function createVendorBankAccount(data: CreateVendorBankAccountInput): Promise<VendorBankAccountDetail> {
  return apiFetch<VendorBankAccountDetail>('/vendor-bank-accounts/', {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

// ── Loading a whole supplier list at once ──────────────────────────────────
// Two calls on purpose: the preview writes nothing and reports every row, then
// the load does only what the preview showed.
export interface SupplierBankUploadRow {
  line: number
  verdict: string
  label: string
  reason: string
  supplier: string
  bank: string
  account_holder: string
  account_ends: string
  branch_code: string
  currency: string
  existing_account_ends: string
  will_load: boolean
}

export interface SupplierBankUploadPreview {
  total_rows: number
  will_load: number
  held: number
  already_on_file: number
  rejected: number
  columns_understood: Record<string, string>
  columns_ignored: string[]
  missing_columns: string[]
  rows: SupplierBankUploadRow[]
  detail?: string
}

export interface SupplierBankUploadResult {
  created: number
  suppliers_created: number
  held: number
  already_on_file: number
  not_loaded: number
  problems: string[]
  message: string
}

// apiFetch adds the chosen company to GETs only, and these are POSTs — so they
// have to carry it themselves. Without it the server either refuses ("choose the
// company first", while the topbar plainly has one) or, worse, silently stamps
// the person's DEFAULT company: load an ADSA list while your default is ADIC and
// every supplier lands on ADIC, reported as a success.
export function withChosenCompany(path: string): string {
  if (typeof window === 'undefined') return path
  const chosen = localStorage.getItem('alpha_company_id')
  if (!chosen || path.includes('company=')) return path
  return path + (path.includes('?') ? '&' : '?') + `company=${encodeURIComponent(chosen)}`
}

export async function previewSupplierBankUpload(file: File): Promise<SupplierBankUploadPreview> {
  const form = new FormData()
  form.append('file', file)
  return apiFetch<SupplierBankUploadPreview>(
    withChosenCompany('/vendor-bank-accounts/upload/preview/'), {
      method: 'POST',
      body: form,
    })
}

export async function loadSupplierBankUpload(file: File): Promise<SupplierBankUploadResult> {
  const form = new FormData()
  form.append('file', file)
  return apiFetch<SupplierBankUploadResult>(
    withChosenCompany('/vendor-bank-accounts/upload/load/'), {
      method: 'POST',
      body: form,
    })
}

export async function submitVendorBank(id: string): Promise<VendorBankAccountDetail> {
  return apiFetch<VendorBankAccountDetail>(`/vendor-bank-accounts/${id}/submit/`, { method: 'POST' })
}
export async function approveVendorBank(id: string): Promise<VendorBankAccountDetail> {
  return apiFetch<VendorBankAccountDetail>(`/vendor-bank-accounts/${id}/approve/`, { method: 'POST' })
}
export async function rejectVendorBank(id: string, reason: string): Promise<VendorBankAccountDetail> {
  return apiFetch<VendorBankAccountDetail>(`/vendor-bank-accounts/${id}/reject/`, {
    method: 'POST', body: JSON.stringify({ reason }),
  })
}
export async function retireVendorBank(id: string, reason: string): Promise<VendorBankAccountDetail> {
  return apiFetch<VendorBankAccountDetail>(`/vendor-bank-accounts/${id}/retire/`, {
    method: 'POST', body: JSON.stringify({ reason }),
  })
}

// ─── PO ↔ Bill Match (auto-match + tier-1/tier-2 approval) ──────────────────

export type MatchStatus =
  | 'matched' | 'needs_tier1_approval' | 'needs_tier2_approval'
  | 'tier1_approved' | 'rejected' | 'override'
  | 'variance_quantity' | 'variance_price' | 'variance_both'

export type AIVerdict = 'clean' | 'variance' | 'anomaly' | 'fraud_cue' | 'unavailable'

export interface BillAIVerification {
  id: string
  verdict: AIVerdict
  verdict_display: string
  vendor_match: boolean
  currency_match: boolean
  total_match: boolean
  flags: string[]
  notes: string
  confidence: number
  model_used: string
  elapsed_seconds: string
  error_message: string
  created_at: string
}

export interface POBillMatchExtended {
  id: string
  purchase_order: string
  po_number: string
  po_department: string
  po_total: string
  bill: string
  bill_number: string
  bill_total: string
  match_status: MatchStatus
  match_status_display: string
  quantity_variance: string
  price_variance: string
  variance_pct: string
  tier1_approved_by: string | null
  tier1_approved_by_username: string | null
  tier1_approved_at: string | null
  tier2_approved_by: string | null
  tier2_approved_by_username: string | null
  tier2_approved_at: string | null
  rejection_reason: string
  override_reason: string
  matched_by: string
  matched_by_username: string
  matched_at: string
  is_payable: boolean
  ai_verifications: BillAIVerification[]
}

export async function getPOBillMatches(params?: {
  status?: string; bill?: string; purchase_order?: string;
  pending_only?: boolean; page?: number;
}): Promise<PaginatedResponse<POBillMatchExtended>> {
  return apiFetch<PaginatedResponse<POBillMatchExtended>>(
    `/po-bill-matches/${_qs({
      status: params?.status,
      bill: params?.bill,
      purchase_order: params?.purchase_order,
      pending_only: params?.pending_only ? '1' : undefined,
      page: params?.page ? String(params.page) : undefined,
    })}`,
  )
}

// ─── Exceptions Engine ──────────────────────────────────────────────────────

export type ExceptionSeverity = 'low' | 'medium' | 'high' | 'critical'
export type ExceptionStatus =
  | 'open' | 'acknowledged' | 'in_progress' | 'resolved' | 'dismissed'
export type ExceptionType =
  | 'po_bill_mismatch' | 'banking_change' | 'ai_fraud_cue' | 'ai_anomaly'
  | 'unmatched_payment' | 'back_dated_bill' | 'vendor_mismatch' | 'bill_no_po'
  | 'open_po_at_close' | 'fx_rate_stale' | 'manual' | 'other'
export type ExceptionRequiresRole = 'any' | 'dept_manager' | 'finance_manager' | 'cfo'

export interface ExceptionListItem {
  id: string
  exception_type: ExceptionType
  exception_type_display: string
  severity: ExceptionSeverity
  severity_display: string
  status: ExceptionStatus
  status_display: string
  requires_role: ExceptionRequiresRole
  requires_role_display: string
  title: string
  source_label: string
  created_at: string
}

export interface ExceptionDetail extends ExceptionListItem {
  description: string
  source_app: string
  source_model: string
  source_id: string
  metadata: Record<string, any>
  acknowledged_by: string | null
  acknowledged_by_username: string | null
  acknowledged_at: string | null
  resolved_by: string | null
  resolved_by_username: string | null
  resolved_at: string | null
  resolution_notes: string
  dismissed_by: string | null
  dismissed_by_username: string | null
  dismissed_at: string | null
  dismissal_reason: string
  linker_notified_at: string | null
  linker_response: string
  created_by: string | null
  created_by_username: string | null
  is_open: boolean
  updated_at: string
}

export interface ExceptionCounts {
  total: number; critical: number; high: number; medium: number; low: number
}

export async function getExceptions(params?: {
  status?: string; exception_type?: string; severity?: string;
  source_app?: string; search?: string; open_only?: boolean; page?: number;
}): Promise<PaginatedResponse<ExceptionListItem>> {
  return apiFetch<PaginatedResponse<ExceptionListItem>>(
    `/exceptions/${_qs({
      status: params?.status,
      exception_type: params?.exception_type,
      severity: params?.severity,
      source_app: params?.source_app,
      search: params?.search,
      open_only: params?.open_only ? '1' : undefined,
      page: params?.page ? String(params.page) : undefined,
    })}`,
  )
}

export async function tier1ApproveMatch(id: string): Promise<POBillMatchExtended> {
  return apiFetch(`/po-bill-matches/${id}/tier1-approve/`, { method: 'POST' })
}
export async function tier2ApproveMatch(id: string): Promise<POBillMatchExtended> {
  return apiFetch(`/po-bill-matches/${id}/tier2-approve/`, { method: 'POST' })
}
export async function rejectMatch(id: string, reason: string): Promise<POBillMatchExtended> {
  return apiFetch(`/po-bill-matches/${id}/reject/`, {
    method: 'POST', body: JSON.stringify({ reason }),
  })
}
export async function retryMatchAI(id: string): Promise<POBillMatchExtended> {
  return apiFetch(`/po-bill-matches/${id}/retry-ai/`, { method: 'POST' })
}

export interface VariancePolicy {
  id: string
  tier1_ceiling_pct: string
  notes: string
  updated_by: string | null
  updated_by_username: string | null
  created_at: string
  updated_at: string
}

export async function getVariancePolicy(): Promise<PaginatedResponse<VariancePolicy>> {
  return apiFetch('/variance-policy/')
}

export async function getException(id: string): Promise<ExceptionDetail> {
  return apiFetch(`/exceptions/${id}/`)
}

export async function getExceptionCounts(): Promise<ExceptionCounts> {
  return apiFetch('/exceptions/counts/')
}

export async function acknowledgeException(id: string): Promise<ExceptionDetail> {
  return apiFetch(`/exceptions/${id}/acknowledge/`, { method: 'POST' })
}
export async function resolveException(id: string, notes: string): Promise<ExceptionDetail> {
  return apiFetch(`/exceptions/${id}/resolve/`, {
    method: 'POST', body: JSON.stringify({ notes }),
  })
}
export async function dismissException(id: string, reason: string): Promise<ExceptionDetail> {
  return apiFetch(`/exceptions/${id}/dismiss/`, {
    method: 'POST', body: JSON.stringify({ reason }),
  })
}
export async function retryLinker(id: string): Promise<{
  success: boolean; response: string; notified_at: string | null;
}> {
  return apiFetch(`/exceptions/${id}/retry-linker/`, { method: 'POST' })
}

// ─── Reinsurance ────────────────────────────────────────────────────────────

export interface Reinsurer {
  id: string
  name: string
  short_code: string
  country: string
  credit_rating: string
  is_active: boolean
  notes: string
  treaty_count: number
  created_at: string
  updated_at: string
}

export interface ReinsuranceTreaty {
  id: string
  treaty_number: string
  description: string
  reinsurer: string
  reinsurer_name: string
  reinsurer_short_code: string
  treaty_type: 'quota_share' | 'surplus' | 'xl' | 'stop_loss' | 'facultative'
  treaty_type_display: string
  line_of_business: string
  inception_date: string
  expiry_date: string
  currency_code: string
  cession_share_percent: string | null
  commission_percent: string | null
  retention_amount: string | null
  limit_amount: string | null
  status: 'draft' | 'active' | 'expired' | 'cancelled'
  status_display: string
  notes: string
  created_at: string
}

export interface Cession {
  id: string
  cession_number: string
  cession_date: string
  treaty: string
  treaty_number: string
  reinsurer_short_code: string
  policy_reference: string
  risk_description: string
  gross_premium: string
  ceded_premium: string
  commission_amount: string
  bordereau: string | null
  status: 'draft' | 'posted' | 'voided'
  status_display: string
  je_number: string | null
  posted_by_username: string | null
  posted_at: string | null
  created_at: string
}

export interface ReinsuranceRecovery {
  id: string
  recovery_number: string
  recovery_date: string
  treaty: string
  treaty_number: string
  reinsurer_short_code: string
  claim_reference: string
  gross_loss: string
  ceded_recovery: string
  notes: string
  status: 'draft' | 'posted' | 'settled' | 'voided'
  status_display: string
  je_number: string | null
  posted_by_username: string | null
  posted_at: string | null
  created_at: string
}

export interface BordereauImport {
  id: string
  bordereau_number: string
  treaty: string
  treaty_number: string
  period_start: string
  period_end: string
  received_date: string
  file_name: string
  line_count: number
  total_gross_premium: string
  total_ceded_premium: string
  total_commission: string
  status: 'uploaded' | 'parsed' | 'committed' | 'rejected'
  status_display: string
  notes: string
  created_at: string
}

export async function getReinsurers(): Promise<PaginatedResponse<Reinsurer>> {
  return apiFetch<PaginatedResponse<Reinsurer>>('/reinsurers/')
}

// KYC / evidence register (Arun P. Iyer control brief §5, 16-Sep-2026).
// The file is never a URL: /media/ is not served here, so it is streamed
// through the download endpoint behind the reinsurance permission.
export interface ReinsurerDocument {
  id: string
  kind: string
  kind_label: string
  title: string
  original_filename: string
  content_type: string
  size_bytes: number
  issue_date: string | null
  expiry_date: string | null
  expired: boolean
  source: string | null
  uploaded_by: string | null
  uploaded_at: string | null
  verification_status: 'pending' | 'verified' | 'rejected'
  verification_label: string
  verified_by: string | null
  verified_at: string | null
  verification_note: string | null
  counts_as_evidence: boolean
  required: boolean
  download_url: string
}
export interface ReinsurerDocumentRegister {
  reinsurer: { id: string; name: string; short_code: string; approval_status: string }
  documents: ReinsurerDocument[]
  /** Required evidence missing, unverified or expired. Empty means complete. */
  gaps: string[]
  kinds: { value: string; label: string; required: boolean }[]
}

export async function getReinsurerDocuments(
  reinsurerId: string): Promise<ReinsurerDocumentRegister> {
  return apiFetch<ReinsurerDocumentRegister>(
    `/reinsurance/counterparties/${reinsurerId}/documents/`)
}

export async function uploadReinsurerDocument(
  reinsurerId: string, form: FormData): Promise<ReinsurerDocument> {
  return apiFetch<ReinsurerDocument>(
    `/reinsurance/counterparties/${reinsurerId}/documents/`,
    { method: 'POST', body: form })
}

/** Open a KYC document.
 *
 * NOT a plain `<a href>`. Omni authenticates with an Authorization header, not
 * a session cookie, so a bare link to the streaming endpoint comes back 401 —
 * the "secure open/download" in the brief would have been a dead link for every
 * real user while the test passed with force_authenticate. Caught by the
 * 16-Sep-2026 ship-gate (checklist L49). Same route every other Omni file
 * download takes: fetch with the header, then hand the browser a blob.
 */
export async function openReinsurerDocument(doc: {
  download_url: string; original_filename: string; content_type: string
}): Promise<void> {
  const res = await apiFetchRaw(doc.download_url.replace(/^\/api\/v1/, ''))
  if (!res.ok) {
    throw new Error(res.status === 403
      ? 'You do not have access to this document.'
      : `The document could not be opened (${res.status}).`)
  }
  saveBlob(await res.blob(), doc.original_filename || 'document')
}

export async function verifyReinsurerDocument(
  docId: string, decision: 'verified' | 'rejected',
  note = ''): Promise<ReinsurerDocument> {
  return apiFetch<ReinsurerDocument>(`/reinsurance/documents/${docId}/verify/`, {
    method: 'POST',
    body: JSON.stringify({ status: decision, note }),
  })
}

export async function getReinsuranceTreaties(
  filters: { status?: string; reinsurer?: string } = {}
): Promise<PaginatedResponse<ReinsuranceTreaty>> {
  return apiFetch<PaginatedResponse<ReinsuranceTreaty>>(
    `/reinsurance-treaties/${_qs({ status: filters.status, reinsurer: filters.reinsurer })}`
  )
}

export async function getCessions(
  filters: { status?: string; treaty?: string } = {}
): Promise<PaginatedResponse<Cession>> {
  return apiFetch<PaginatedResponse<Cession>>(
    `/reinsurance-cessions/${_qs({ status: filters.status, treaty: filters.treaty })}`
  )
}

export async function postCession(id: string): Promise<Cession> {
  return apiFetch<Cession>(`/reinsurance-cessions/${id}/post_cession/`, { method: 'POST' })
}

export async function getReinsuranceRecoveries(
  filters: { status?: string; treaty?: string } = {}
): Promise<PaginatedResponse<ReinsuranceRecovery>> {
  return apiFetch<PaginatedResponse<ReinsuranceRecovery>>(
    `/reinsurance-recoveries/${_qs({ status: filters.status, treaty: filters.treaty })}`
  )
}

export async function postReinsuranceRecovery(id: string): Promise<ReinsuranceRecovery> {
  return apiFetch<ReinsuranceRecovery>(
    `/reinsurance-recoveries/${id}/post_recovery/`, { method: 'POST' },
  )
}

export async function getBordereauImports(): Promise<PaginatedResponse<BordereauImport>> {
  return apiFetch<PaginatedResponse<BordereauImport>>('/reinsurance-bordereaux/')
}

// ─── Investments ────────────────────────────────────────────────────────────

export interface Investment {
  id: string
  investment_number: string
  name: string
  isin_or_ref: string
  instrument_type: string
  instrument_type_display: string
  classification: 'fvtpl' | 'fvoci' | 'amortised_cost'
  classification_display: string
  issuer: string
  custodian: string
  currency_code: string
  face_value: string
  cost: string
  current_fair_value: string
  coupon_rate_percent: string | null
  purchase_date: string
  maturity_date: string | null
  investment_account: string
  investment_account_code: string
  investment_account_name: string
  status: 'open' | 'matured' | 'disposed'
  status_display: string
  notes: string
  transaction_count: number
  unrealised_pl: string
  created_at: string
  updated_at: string
}

export interface InvestmentTransaction {
  id: string
  transaction_number: string
  investment: string
  investment_number: string
  investment_name: string
  transaction_type: 'purchase' | 'sale' | 'coupon' | 'fair_value' | 'maturity'
  transaction_type_display: string
  transaction_date: string
  amount: string
  cash_account: string | null
  description: string
  status: 'draft' | 'posted'
  status_display: string
  je_number: string | null
  posted_by_username: string | null
  posted_at: string | null
  created_at: string
}

export async function getInvestments(
  filters: { classification?: string; status?: string } = {}
): Promise<PaginatedResponse<Investment>> {
  return apiFetch<PaginatedResponse<Investment>>(
    `/investments/${_qs({ classification: filters.classification, status: filters.status })}`,
  )
}

export async function getInvestmentTransactions(
  filters: { investment?: string; status?: string } = {}
): Promise<PaginatedResponse<InvestmentTransaction>> {
  return apiFetch<PaginatedResponse<InvestmentTransaction>>(
    `/investment-transactions/${_qs({ investment: filters.investment, status: filters.status })}`,
  )
}

export async function postInvestmentTransaction(id: string): Promise<InvestmentTransaction> {
  return apiFetch<InvestmentTransaction>(
    `/investment-transactions/${id}/post_transaction/`, { method: 'POST' },
  )
}

// ─── Bank feeds ─────────────────────────────────────────────────────────────

export interface BankFeedConfig {
  id: string
  name: string
  bank_account: string
  bank_account_code: string
  bank_account_name: string
  protocol: 'sftp_csv' | 'sftp_ofx' | 'sftp_bai2' | 'api_fnb' | 'manual'
  protocol_display: string
  env_prefix: string
  remote_path: string
  file_pattern: string
  schedule_cron: string
  last_run_at: string | null
  last_run_status: string
  status: 'active' | 'paused' | 'error'
  status_display: string
  notes: string
  created_at: string
  updated_at: string
}

export interface BankFeedRun {
  id: string
  config: string
  config_name: string
  triggered_by: string | null
  triggered_by_username: string | null
  started_at: string
  finished_at: string | null
  outcome: 'success' | 'empty' | 'error'
  outcome_display: string
  files_seen: number
  files_processed: number
  statements_created: number
  lines_created: number
  duplicates_skipped: number
  error_message: string
  file_hashes: string[]
  created_at: string
}

export async function getBankFeedConfigs(): Promise<PaginatedResponse<BankFeedConfig>> {
  return apiFetch<PaginatedResponse<BankFeedConfig>>('/bank-feed-configs/')
}

export async function getBankFeedRuns(
  filters: { config?: string } = {}
): Promise<PaginatedResponse<BankFeedRun>> {
  return apiFetch<PaginatedResponse<BankFeedRun>>(
    `/bank-feed-runs/${_qs({ config: filters.config })}`,
  )
}

export async function runBankFeedNow(id: string): Promise<BankFeedRun> {
  return apiFetch<BankFeedRun>(`/bank-feed-configs/${id}/run_now/`, { method: 'POST' })
}

// ─── Stubs for in-progress features (backend endpoints not yet implemented) ──
// These keep the build green and let pages render empty state instead of crashing.
// Remove and replace with real implementations as backend support lands.

export type ImportBatchSummary = any
export type AssetDisposalForApproval = any
export type AssetSignOff = any
export interface FNBStatus {
  configured: boolean
  auth_mode: string
  api_base: string
  last_sync_at: string | null
  last_sync_status: string
  pending_batches: number
  failed_calls_24h: number
}

export interface FNBSyncLogItem {
  id: string
  direction: 'outbound' | 'inbound'
  direction_display: string
  service: string
  service_display: string
  status: string
  status_display: string
  endpoint: string
  http_method: string
  http_status: number | null
  elapsed_ms: number
  request_summary: string
  error_message: string
  triggered_by_user: number | null
  triggered_by_username: string | null
  created_at: string
}

export interface FNBBatchSubmissionItem {
  id: string
  idempotency_key: string
  source_account: string
  source_account_name: string
  recipients: { holder: string; bank: string; account_number: string }[]
  payment_count: number
  total_amount_bwp: string
  currency_code: string
  status: string
  status_display: string
  submitted_at: string | null
  acknowledged_at: string | null
  settled_at: string | null
  fnb_reference: string
  failure_reason: string
  submitted_by: number | null
  submitted_by_username: string | null
  created_at: string
  updated_at: string
}
export type CapitalCheckResult = any
export type CapitalSnapshot = any
export type CapitalParameter = any

export const getAllImportBatches = async (): Promise<any[]> => []
export const getPendingDisposals = async (): Promise<any[]> => []
export const approveImportBatch = async (_kind: string, _id: string): Promise<any> => { throw new Error('approveImportBatch: backend not implemented') }
export const commitImportBatch = async (_kind: string, _id: string): Promise<any> => { throw new Error('commitImportBatch: backend not implemented') }
export const rejectImportBatch = async (_kind: string, _id: string, _reason: string): Promise<any> => { throw new Error('rejectImportBatch: backend not implemented') }
export const approveDisposal = async (_id: string): Promise<any> => { throw new Error('approveDisposal: backend not implemented') }
export const rejectDisposal = async (_id: string, _reason: string): Promise<any> => { throw new Error('rejectDisposal: backend not implemented') }

export const getAssetSignOffs = async (
  _opts: { open_only?: boolean } = {},
): Promise<{ results: any[] }> => ({ results: [] })
export const signAssetFirst = async (_id: string): Promise<any> => { throw new Error('signAssetFirst: backend not implemented') }
export const signAssetSecond = async (_id: string): Promise<any> => { throw new Error('signAssetSecond: backend not implemented') }

// ─── FNB Integration ──────────────────────────────────────────────────────
// Live endpoints under /api/v1/fnb/ — see fnb/api_views.py
export const getFNBStatus = async (): Promise<FNBStatus> =>
  apiFetch<FNBStatus>('/fnb/status/')

// ─── Aria — plain-English AI read of the bank connection ──────────────────
export interface FNBHealthSignals {
  connection: { last_call: string; last_call_status: string; failed_calls_24h: number }
  alerts_feed: {
    current_state: string; last_checked: string; last_working: string
    failed_checks_24h: number; last_alert_received: string
  }
  statements: { imported_ok_24h: number; failed_24h: number; rejected_accounts: string[] }
}
export interface FNBHealthSummary {
  summary: string
  signals: FNBHealthSignals
  engine_ok: boolean
}
export const getFNBHealthSummary = async (refresh = false): Promise<FNBHealthSummary> =>
  apiFetch<FNBHealthSummary>(`/fnb/health-summary/${refresh ? '?refresh=1' : ''}`)

export const askFNBHealth = async (question: string): Promise<{ ok: boolean; answer: string }> =>
  apiFetch('/fnb/health-ask/', { method: 'POST', body: JSON.stringify({ question }) })

export const getFNBSyncLogs = async (
  opts: { page?: number; direction?: string; service?: string; status?: string } = {},
): Promise<PaginatedResponse<FNBSyncLogItem>> =>
  apiFetch<PaginatedResponse<FNBSyncLogItem>>(
    `/fnb/sync-logs/${_qs({
      page: opts.page,
      direction: opts.direction,
      service: opts.service,
      status: opts.status,
    })}`,
  )

export const getFNBBatchSubmissions = async (
  opts: { page?: number } = {},
): Promise<PaginatedResponse<FNBBatchSubmissionItem>> =>
  apiFetch<PaginatedResponse<FNBBatchSubmissionItem>>(
    `/fnb/batch-submissions/${_qs({ page: opts.page })}`,
  )

export const testFNBConnection = async (): Promise<{
  success: boolean
  http_status?: number
  elapsed_ms?: number
  reason?: string
  detail?: string
}> => apiFetch('/fnb/test-connection/', { method: 'POST' })

// ─── Pull statement (FNB → us) ───────────────────────────────────────────
export interface FNBPullStatementResult {
  success: boolean
  statement_id?: string
  statement_number?: string
  statement_date?: string
  opening_balance?: string
  closing_balance?: string
  line_count?: number
  file_name?: string
  reason?: string
  detail?: string
  http_status?: number
}

export const pullFNBStatement = async (body: {
  bank_account_id: string
  from_date?: string
  to_date?: string
}): Promise<FNBPullStatementResult> =>
  apiFetch('/fnb/pull-statements/', {
    method: 'POST',
    body: JSON.stringify(body),
  })

// ─── Quick transfer (one-shot single payment to FNB) ─────────────────────
export interface FNBQuickTransferResult {
  success: boolean
  batch?: FNBBatchSubmissionItem
  reference?: string
  amount?: string
  currency?: string
  beneficiary?: { name: string; account_number: string; bic: string }
  reason?: string
  detail?: string
  http_status?: number
}

export const fnbQuickTransfer = async (body: {
  source_account_id: string
  beneficiary_account: string
  beneficiary_name: string
  beneficiary_bic?: string
  amount: number | string
  currency?: string
  reference?: string
  service_level?: 'SDVA' | 'NURG' | 'RGTS'
}): Promise<FNBQuickTransferResult> =>
  apiFetch('/fnb/quick-transfer/', {
    method: 'POST',
    body: JSON.stringify(body),
  })

// ─── Refresh batch status (us → FNB, ask for ack) ────────────────────────
export const refreshFNBBatch = async (batchId: string): Promise<{
  success: boolean
  batch?: FNBBatchSubmissionItem
  fnb_status?: string
  fnb_reasons?: any[]
  detail?: string
}> =>
  apiFetch(`/fnb/batches/${batchId}/refresh/`, { method: 'POST' })

// ─── Submit approved-payments batch to FNB ───────────────────────────────
export const submitFNBBatch = async (body: {
  payment_ids: string[]
  source_account_id: string
  service_level_code?: 'SDVA' | 'NURG' | 'RGTS'
  requested_execution_date?: string
}): Promise<{
  success: boolean
  batch?: FNBBatchSubmissionItem
  skipped_non_approved?: number
  detail?: string
}> =>
  apiFetch('/fnb/submit-batch/', {
    method: 'POST',
    body: JSON.stringify(body),
  })

// ─── Patch BankAccount.account_number (one-time mapping fix) ─────────────
export const setBankAccountNumber = async (
  bankAccountId: string,
  accountNumber: string,
): Promise<{ success: boolean; old: string; new: string }> =>
  apiFetch(`/banking/bank-accounts/${bankAccountId}/set-account-number/`, {
    method: 'PATCH',
    body: JSON.stringify({ account_number: accountNumber }),
  })

// ─── Toggle BankAccount.hide_in_banking_ui (FNB-page show/hide) ──────────
export const toggleBankAccountHidden = async (
  bankAccountId: string,
  hide?: boolean,
): Promise<{ success: boolean; hide_in_banking_ui: boolean }> =>
  apiFetch(`/banking/bank-accounts/${bankAccountId}/toggle-hide/`, {
    method: 'PATCH',
    body: JSON.stringify(hide === undefined ? {} : { hide }),
  })

// ─── Voucher / TV Clearing (maker-checker JE reversal queue) ────────────
// CFO directive 2026-05-26. Pako + Legakwa submit. Kago approves.

export interface JEClearingBulkScope {
  company: string
  from_date: string
  to_date: string
  source_type?: string | null
}

export interface JEClearingRequest {
  id: string
  status: 'pending' | 'approved' | 'rejected'
  /** Single-mode target. Null when this row is a bulk wipe. */
  journal_entry: string | null
  je_number: string | null
  je_description: string | null
  je_date: string | null
  je_amount: string | null
  /** Bulk-mode scope. Null when this row clears a single JE. */
  bulk_scope: JEClearingBulkScope | null
  is_bulk: boolean
  reason: string
  submitted_by: number | null
  submitted_by_name: string | null
  submitted_at: string
  decided_by: number | null
  decided_by_name: string | null
  decided_at: string | null
  decision_note: string
  /** The posted reversal JE for a single-mode approval (Finance directive 2026-07-02). */
  reversal_entry: string | null
  reversal_entry_number: string | null
  /** Number of JEs reversed on approval (1 for single, N for bulk). */
  deleted_count: number
}

export interface JEClearingRoles {
  is_maker: boolean
  is_approver: boolean
}

export async function getJEClearings(
  opts: { status?: 'pending' | 'approved' | 'rejected'; search?: string } = {},
): Promise<PaginatedResponse<JEClearingRequest>> {
  return apiFetch<PaginatedResponse<JEClearingRequest>>(
    `/je-clearings/${_qs({ status: opts.status, search: opts.search })}`,
  )
}

export async function submitJEClearing(body:
  | { journal_entry: string; reason: string }
  | { bulk_scope: JEClearingBulkScope; reason: string },
): Promise<JEClearingRequest> {
  return apiFetch<JEClearingRequest>('/je-clearings/', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export interface JEClearingSnapshot {
  status: 'pending' | 'approved' | 'rejected'
  deleted_count: number
  /** Frozen JSON dump of the deleted JE(s). Shape: {entries: [...] }. */
  snapshot: { entries: unknown[]; note?: string } | null
}

export async function getJEClearingSnapshot(
  id: string,
): Promise<JEClearingSnapshot> {
  return apiFetch<JEClearingSnapshot>(`/je-clearings/${id}/snapshot/`)
}

export async function approveJEClearing(
  id: string, decision_note: string = '',
): Promise<JEClearingRequest> {
  return apiFetch<JEClearingRequest>(`/je-clearings/${id}/approve/`, {
    method: 'POST',
    body: JSON.stringify({ decision_note }),
  })
}

export async function rejectJEClearing(
  id: string, decision_note: string = '',
): Promise<JEClearingRequest> {
  return apiFetch<JEClearingRequest>(`/je-clearings/${id}/reject/`, {
    method: 'POST',
    body: JSON.stringify({ decision_note }),
  })
}

export async function getJEClearingRoles(): Promise<JEClearingRoles> {
  return apiFetch<JEClearingRoles>('/je-clearings/roles/')
}

// ─── Health Care Quick Quote ─────────────────────────────────────────────
// CFO directive 2026-05-26 — drop a doc with age/DOB, get office + RI rate.

export interface HCPlan { code: string; name: string }

export interface HCQuoteLife {
  first_name: string | null
  last_name:  string | null
  dob:        string | null
  age_years:  number
  gender:     'M' | 'F' | 'U'
  life_category: 'MAIN' | 'ADULT_DEP' | 'CHILD_DEP'
  extraction_confidence: number
  plan_code: string
  plan_name: string
  office_monthly_bwp: string
  ri_monthly_bwp:     string
  margin_monthly_bwp: string
  margin_pct:         string
}

export interface HCQuoteResult {
  success: boolean
  upload_id?: string
  source?: { mime: string; name: string; pages: number; text_chars: number }
  plan_code?: string
  plan_name?: string
  quote_date?: string
  lives?: HCQuoteLife[]
  totals?: {
    office_monthly_bwp: string
    ri_monthly_bwp: string
    gross_margin_monthly_bwp: string
    broker_commission_bwp: string
    nbfira_levy_bwp: string
    net_ad_margin_bwp: string
    broker_pct: string
    nbfira_pct: string
  }
  python_code?: string
  warnings?: string[]
  errors?: string[]
  detail?: string
}

export async function getHealthCarePlans(): Promise<{ plans: HCPlan[] }> {
  return apiFetch<{ plans: HCPlan[] }>('/health/quick-quote/plans/')
}

export async function submitHealthQuickQuote(args: {
  file?: File
  raw_text?: string
  plan_code?: string
  quote_date?: string
}): Promise<HCQuoteResult> {
  const fd = new FormData()
  if (args.file) fd.append('file', args.file)
  if (args.raw_text)   fd.append('raw_text', args.raw_text)
  if (args.plan_code)  fd.append('plan_code', args.plan_code)
  if (args.quote_date) fd.append('quote_date', args.quote_date)
  return apiFetch<HCQuoteResult>('/health/quick-quote/', {
    method: 'POST',
    body:   fd,
  })
}

// --- Healthcare vendor onboarding (Ankete-only) ---------------------------
export interface VendorExtractField { path: string; value: string; confidence: number }
export interface VendorExtractResult { success: boolean; fields: VendorExtractField[]; deepseek_used: boolean }
export interface VendorSubmitResult { success: boolean; reference_number: string; agreement_email_sent: boolean }

export async function extractVendorFields(args: {
  file?: File; raw_text?: string; doc_type?: string
}): Promise<VendorExtractResult> {
  const fd = new FormData()
  if (args.file) fd.append('file', args.file)
  if (args.raw_text) fd.append('raw_text', args.raw_text)
  fd.append('doc_type', args.doc_type || 'CoI')
  return apiFetch<VendorExtractResult>('/health/vendor-onboarding/extract/', {
    method: 'POST', body: fd,
  })
}

export async function submitVendorOnboarding(
  payload: Record<string, unknown>,
): Promise<VendorSubmitResult> {
  return apiFetch<VendorSubmitResult>('/health/vendor-onboarding/submit/', {
    method: 'POST',
    body: JSON.stringify(payload),
    headers: { 'Content-Type': 'application/json' },
  })
}

export async function vendorOnboardingAccess(): Promise<{ authorised: boolean }> {
  return apiFetch<{ authorised: boolean }>('/health/vendor-onboarding/access/')
}

// ─── Self-service onboarding by invite (CFO 2026-07-28) ─────────────────────
export interface VendorInviteCreateResult {
  success: boolean; link: string; expires_at: string; email_sent: boolean
}
// Staff (Ankete/CFO) mint a single-use link for a provider to fill in themselves.
export async function createVendorInvite(args: {
  invited_email?: string; invited_name?: string; expires_days?: number; send_email?: boolean
}): Promise<VendorInviteCreateResult> {
  return apiFetch<VendorInviteCreateResult>('/health/vendor-onboarding/invite/', {
    method: 'POST', body: JSON.stringify(args), headers: { 'Content-Type': 'application/json' },
  })
}

export interface VendorInviteInfo { success: boolean; invited_name: string; invited_email: string }
// PUBLIC — no login. Plain fetch (no auth header) since the provider has no token.
export async function getVendorInvite(token: string): Promise<VendorInviteInfo> {
  const res = await fetch(`${API_BASE}/health/vendor-invite/${encodeURIComponent(token)}/`,
    { headers: { Accept: 'application/json' } })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error((data && (data as any).detail) || `HTTP ${res.status}`)
  return data as VendorInviteInfo
}
export async function submitVendorInvite(
  token: string, payload: Record<string, unknown>,
): Promise<VendorSubmitResult> {
  const res = await fetch(`${API_BASE}/health/vendor-invite/${encodeURIComponent(token)}/submit/`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error((data && (data as any).detail) || `HTTP ${res.status}`)
  return data as VendorSubmitResult
}

// ---------------------------------------------------------------------------
// Speaker audience feedback (CFO 2026-08-03)
// The two form calls are PUBLIC — plain fetch, no token, because the audience
// has no omni login. Reading the answers goes through apiFetch and is gated
// server-side to the speaker alone.
// ---------------------------------------------------------------------------
export interface SpeakerFeedbackPain { key: string; statement: string }
export interface SpeakerFeedbackOpenQuestion {
  key: string; label: string; required: boolean
}
export interface SpeakerFeedbackForm {
  slug: string
  title: string
  event: string
  session_date: string
  speaker: string
  intro: string
  confidentiality: string
  scale_low: string
  scale_high: string
  pains: SpeakerFeedbackPain[]
  open_questions: SpeakerFeedbackOpenQuestion[]
  headcount_bands: string[]
  require_company?: boolean
  closes_on?: string
  closed: boolean
}
export interface SpeakerFeedbackSummaryRow {
  key: string
  statement: string
  answered: number
  average: number | null
  top_box: number
  top_box_pct: number | null
}
export interface SpeakerFeedbackResponse {
  id: string
  submitted_at: string
  pain_ratings: Record<string, number>
  biggest_question: string
  wish_ai_did: string
  tried_already: string
  respondent_name: string
  respondent_email: string
  respondent_role: string
  company_name: string
  industry: string
  country: string
  headcount_band: string
  may_quote: boolean
}
export interface SpeakerFeedbackReport {
  slug: string
  title: string
  event: string
  session_date: string
  public_link: string
  response_count: number
  scale_low: string
  scale_high: string
  summary: SpeakerFeedbackSummaryRow[]
  responses: SpeakerFeedbackResponse[]
}

export async function getSpeakerFeedbackForm(slug: string): Promise<SpeakerFeedbackForm> {
  const res = await fetch(`${API_BASE}/speaker-feedback/form/${encodeURIComponent(slug)}/`,
    { headers: { Accept: 'application/json' } })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error((data && (data as any).detail) || `HTTP ${res.status}`)
  return data as SpeakerFeedbackForm
}

export async function submitSpeakerFeedback(
  slug: string, payload: Record<string, unknown>,
): Promise<{ success: boolean }> {
  const res = await fetch(`${API_BASE}/speaker-feedback/form/${encodeURIComponent(slug)}/submit/`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error((data && (data as any).detail) || `HTTP ${res.status}`)
  return data as { success: boolean }
}

export async function getSpeakerFeedbackAccess(): Promise<{ allowed: boolean }> {
  return apiFetch<{ allowed: boolean }>('/speaker-feedback/access/')
}

export async function getSpeakerFeedbackReport(slug: string): Promise<SpeakerFeedbackReport> {
  return apiFetch<SpeakerFeedbackReport>(
    `/speaker-feedback/responses/${encodeURIComponent(slug)}/`)
}

// The NBFIRA capital page was never wired to the backend: every one of these was
// a stub returning null or an empty list, so the page rendered an empty shell and
// the caller's `.catch(() => null)` hid it. The 612% Manus found was only ever in
// the API response — which is what a statutory return or a script reads — and the
// screen was silent about it. Wired to the real endpoints 2026-08-10 so the
// "Under revision — not for regulatory or board use" state actually reaches a
// human looking at the page.
export const getCapitalCheck = async (asOf?: string): Promise<any> =>
  apiFetch<any>(`/regulatory/capital-check/${asOf ? `?as_of=${encodeURIComponent(asOf)}` : ''}`)
export const getCapitalSnapshots = async (): Promise<{ results: any[] }> =>
  apiFetch<{ results: any[] }>('/capital-snapshots/')
export const getCapitalParameters = async (): Promise<{ results: any[] }> =>
  apiFetch<{ results: any[] }>('/capital-parameters/')
export const takeCapitalSnapshot = async (asOf?: string, note?: string): Promise<any> =>
  apiFetch<any>('/capital-snapshots/take/', {
    method: 'POST',
    body: JSON.stringify({ as_of: asOf, notes: note || '' }),
  })
export const approveCapitalSnapshot = async (id: string): Promise<any> =>
  apiFetch<any>(`/capital-snapshots/${id}/approve/`, { method: 'POST' })
export const updateCapitalParameter = async (id: string, data: any): Promise<any> =>
  apiFetch<any>(`/capital-parameters/${id}/`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  })

// ─── RBAC ──────────────────────────────────────────────────────────────────

export type RBACDepartment =
  | 'executive' | 'finance' | 'claims' | 'underwriting' | 'reinsurance'
  | 'compliance' | 'hr' | 'it' | 'operations' | 'external' | 'system'

export interface RBACPermission {
  code: string
  category: string
  description: string
  is_active: boolean
}

export interface RBACRole {
  id: string
  code: string
  name: string
  description: string
  level: number
  department: RBACDepartment | null
  department_label: string | null
  is_system: boolean
  is_active: boolean
  permission_count: number
}

export interface RBACRoleDetail extends RBACRole {
  permissions: RBACPermission[]
}

export interface RBACUserMini {
  id: number
  username: string
  email: string
  first_name: string
  last_name: string
  is_active: boolean
}

export interface RBACAssignment {
  id: string
  user: RBACUserMini
  role: RBACRole
  scope_department: RBACDepartment | null
  effective_department: RBACDepartment | null
  justification: string
  notes: string
  assigned_by: RBACUserMini | null
  assigned_at: string
  expires_at: string | null
  revoked_by: RBACUserMini | null
  revoked_at: string | null
  revocation_reason: string
  is_currently_active: boolean
}

export interface RBACUserView {
  id: number
  username: string
  email: string
  first_name: string
  last_name: string
  is_active: boolean
  last_login: string | null
  date_joined: string
  active_assignments: RBACAssignment[]
  max_authority_level: number
  legacy_title: string | null
}

export interface RBACMeResponse {
  user: RBACUserMini
  is_superuser: boolean
  roles: RBACRole[]
  permissions: string[]
  max_authority_level: number
  legacy_admin: boolean
}

export interface RBACAuditEntry {
  id: string
  table_name: string
  record_id: string
  action: string
  old_values: any
  new_values: any
  user: RBACUserMini | null
  description: string
  ip_address: string | null
  created_at: string
}

export async function getRBACRoles(): Promise<PaginatedResponse<RBACRole>> {
  return apiFetch<PaginatedResponse<RBACRole>>('/rbac/roles/?page_size=100')
}

export async function getRBACRole(id: string): Promise<RBACRoleDetail> {
  return apiFetch<RBACRoleDetail>(`/rbac/roles/${id}/`)
}

export async function getRBACPermissions(): Promise<PaginatedResponse<RBACPermission>> {
  return apiFetch<PaginatedResponse<RBACPermission>>('/rbac/permissions/?page_size=200')
}

export async function getRBACUsers(): Promise<PaginatedResponse<RBACUserView>> {
  return apiFetch<PaginatedResponse<RBACUserView>>('/rbac/users/?page_size=100')
}

export async function getRBACMe(): Promise<RBACMeResponse> {
  return apiFetch<RBACMeResponse>('/rbac/users/me/')
}

export async function getRBACAssignments(params?: {
  user?: number
  role?: string
  active?: boolean
}): Promise<PaginatedResponse<RBACAssignment>> {
  const qs = new URLSearchParams()
  if (params?.user != null) qs.append('user', String(params.user))
  if (params?.role)         qs.append('role', params.role)
  if (params?.active != null) qs.append('active', params.active ? 'true' : 'false')
  const tail = qs.toString() ? `?${qs.toString()}` : ''
  return apiFetch<PaginatedResponse<RBACAssignment>>(`/rbac/assignments/${tail}`)
}

export async function assignRBACRole(input: {
  user: number
  role: string
  scope_department?: RBACDepartment | null
  justification?: string
  notes?: string
  expires_at?: string | null
}): Promise<RBACAssignment> {
  return apiFetch<RBACAssignment>('/rbac/assignments/', {
    method: 'POST',
    body: JSON.stringify(input),
  })
}

export async function revokeRBACAssignment(id: string, reason: string): Promise<RBACAssignment> {
  return apiFetch<RBACAssignment>(`/rbac/assignments/${id}/revoke/`, {
    method: 'POST',
    body: JSON.stringify({ reason }),
  })
}

// ── Bulk role assignment + Aria plain-English proposal (CFO 2026-06-30) ──
export interface BulkUserMini { id: number; username: string; email: string; first_name: string; last_name: string; is_active: boolean }
export interface AriaProposal {
  source: string
  role: { id: string; code: string; name: string } | null
  department: string
  resolved: BulkUserMini[]
  ambiguous: { name: string; candidates: { id: number; email: string; name: string }[] }[]
  unmatched_names: string[]
}
export interface BulkResolve { resolved: BulkUserMini[]; count: number; unmatched_emails: string[]; already_have_role: number[] }
export interface BulkAssignResult {
  role: string; granted_by: string
  totals: { granted: number; skipped: number; failed: number; unmatched_emails: number }
  granted: { id: number; username: string; email: string }[]
  skipped: { id: number; email: string; reason: string }[]
  failed: { id: number; email: string; reason: string }[]
  unmatched_emails: string[]
}
/** Aria turns plain English into a grant PROPOSAL (role + people). Read-only — never grants. */
export async function parseAccessRequest(text: string): Promise<AriaProposal> {
  return apiFetch<AriaProposal>('/rbac/assignments/parse-request/', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }),
  })
}
/** Preview who a bulk grant would affect (read-only). */
export async function resolveBulkTargets(role: string, emails: string[]): Promise<BulkResolve> {
  return apiFetch<BulkResolve>('/rbac/assignments/resolve-targets/', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ role, emails }),
  })
}
/** Grant ONE role to MANY users in one confirmed action (audit-logged as the operator). */
export async function bulkAssignRole(role: string, emails: string[], justification?: string): Promise<BulkAssignResult> {
  return apiFetch<BulkAssignResult>('/rbac/assignments/bulk-assign/', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ role, emails, justification: justification || '' }),
  })
}

export async function getRBACAudit(): Promise<PaginatedResponse<RBACAuditEntry>> {
  return apiFetch<PaginatedResponse<RBACAuditEntry>>('/rbac/audit/?page_size=50')
}

// ─── Internal tasking + presence (CFO directive 2026-05-24) ────────────────

export interface OnlineUserRow {
  username:  string
  full_name: string
  email:     string
  last_seen: string
  is_me:     boolean
}
export interface OnlineUsersResponse {
  users:          OnlineUserRow[]
  cutoff_minutes: number
}
export async function getOnlineUsers(): Promise<OnlineUsersResponse> {
  return apiFetch<OnlineUsersResponse>('/presence/online/')
}

export type OmniTaskStatus =
  'pending' | 'in_progress' | 'done' | 'partial' | 'blocked' | 'cancelled'
export type OmniTaskPriority = 'low' | 'normal' | 'high' | 'urgent'

export interface OmniTaskListRow {
  id:           string
  title:        string
  body:         string
  priority:     OmniTaskPriority
  status:       OmniTaskStatus
  due_at:       string | null
  created_at:   string
  completed_at: string | null
  seen_at:      string | null
  source?:      string
  is_payment?:  boolean
  assigner:     { username: string; full_name: string }
  assignee:     { username: string; full_name: string }
}
export interface OmniTaskListResponse {
  inbox:        OmniTaskListRow[]
  outbox:       OmniTaskListRow[]
  unread_count: number
}

export interface OmniTaskComment {
  id:            string
  author:        string
  author_name:   string
  body:          string
  new_status:    OmniTaskStatus | ''
  created_at:    string
  has_evidence?: boolean
  evidence_name?: string
  /** Server says this viewer may take the file down (uploader / task owner /
   *  admin). Bug 83594e5d — a wrongly attached confidential doc must be
   *  removable without an IT ticket. */
  can_remove_evidence?: boolean
}
export interface OmniTaskDetail extends OmniTaskListRow {
  comments: OmniTaskComment[]
  completion_pct?: number | null
}

export async function getOmniTasks(includeDone = false): Promise<OmniTaskListResponse> {
  return apiFetch<OmniTaskListResponse>(
    `/tasks/${includeDone ? '?include_done=1' : ''}`,
  )
}

export async function createOmniTask(input: {
  assignee_username:   string
  title:               string
  body?:               string
  due_at?:             string
  priority?:           OmniTaskPriority
  attachment?:         File          // optional screenshot / doc (CFO 2026-07-15)
  attachment_caption?: string
}): Promise<{ id: string }> {
  const { attachment, ...rest } = input
  if (attachment) {
    const fd = new FormData()
    Object.entries(rest).forEach(([k, v]) => {
      if (v != null && v !== '') fd.append(k, String(v))
    })
    fd.append('attachment', attachment)
    return apiFetch<{ id: string }>('/tasks/', { method: 'POST', body: fd })
  }
  return apiFetch<{ id: string }>('/tasks/', {
    method: 'POST',
    body:   JSON.stringify(rest),
  })
}

export async function getOmniTask(id: string): Promise<OmniTaskDetail> {
  return apiFetch<OmniTaskDetail>(`/tasks/${id}/`)
}

/** People a task can be handed to — payroll only, no system accounts
 *  (CFO 2026-07-15 "task issues"). Each row carries title + department so the
 *  search can tell near-duplicate names apart. */
export interface TaskAssignee {
  username:   string
  full_name:  string
  title:      string
  department: string
  // Viewer-relative relevance (CFO 2026-08-31): 1 = your team, 2 = executives,
  // 3 = managers, 4 = everyone else. recent_count = tasks THIS viewer handed
  // this person in the last 90 days (the "Frequent" row). Optional so an old
  // cached response still renders (flat list) if the fields are absent.
  tier?:         1 | 2 | 3 | 4
  recent_count?: number
}
export async function getTaskAssignees(): Promise<TaskAssignee[]> {
  const r = await apiFetch<{ assignees: TaskAssignee[] }>('/tasks/assignees/')
  return r.assignees || []
}

/** Fetch a task comment's attachment as a Blob (auth-gated — can't be a bare
 *  <img src>, so callers create an object URL). */
export async function getTaskCommentFile(taskId: string, commentId: string): Promise<Blob> {
  const r = await apiFetchBinary(`/tasks/${taskId}/comments/${commentId}/file/`)
  if (!r.ok) throw new Error('Could not load attachment')
  return r.blob()
}

/** Remove a task attachment (bug 83594e5d). Same URL as the read — the server
 *  deletes the bytes, keeps the activity line, and writes an audit row. */
export async function removeTaskCommentFile(
  taskId: string, commentId: string,
): Promise<{ removed: string }> {
  return apiFetch<{ removed: string }>(
    `/tasks/${taskId}/comments/${commentId}/file/`, { method: 'DELETE' },
  )
}

// ─── IFRS 16 leases (CFO / Kago 2026-07-15) ──────────────────────────────────
export interface LeaseSummary {
  initial_liability: number; rou_cost: number; depreciation_per_month: number
  total_interest: number; this_fy: number
  closing_liability: number | null; current_portion: number | null; non_current: number | null
  fy_interest: number | null; fy_depreciation: number | null; nbv: number | null
}
export interface Lease {
  id: string; name: string; property_ref: string; company: string | null
  commencement_date: string; term_months: number; monthly_payment: string
  escalation_pct: string; discount_rate_pct: string; fye_month: number; payment_timing: string
  incentives: string; initial_direct_costs: string; prepaid: string; dismantle: string
  notes: string; summary: LeaseSummary | null
}
export interface LeaseInputs {
  monthly_payment: number; discount_rate_pct: number; term_months: number; escalation_pct: number
  commencement_date: string; fye_month: number; incentives: number; payment_timing?: string
  initial_direct_costs?: number; prepaid?: number; dismantle?: number
}
export interface FYRow {
  fy: number; payments: number; interest: number; principal: number
  closing_liability: number; current_portion: number; non_current: number
  depreciation: number; nbv: number
}
export interface LeaseCompute {
  initial_liability: number; rou_cost: number; depreciation_per_month: number
  total_payments: number; total_interest: number; fy_summary: FYRow[]
  schedule: { m: number; date: string; fy: number; payment: number; opening: number; interest: number; closing: number; principal: number; depreciation: number; accum_dep: number; carrying: number }[]
}
export const getLeases = async (): Promise<Lease[]> => {
  // DRF ModelViewSet list is paginated → {results:[…]}; tolerate a bare array too.
  const r = await apiFetch<Lease[] | { results: Lease[] }>('/leases/')
  return Array.isArray(r) ? r : (r?.results ?? [])
}
export const createLease = (body: Partial<Lease>) => apiFetch<Lease>('/leases/', { method: 'POST', body: JSON.stringify(body) })
export const updateLease = (id: string, body: Partial<Lease>) => apiFetch<Lease>(`/leases/${id}/`, { method: 'PATCH', body: JSON.stringify(body) })
export const deleteLease = (id: string) => apiFetch<void>(`/leases/${id}/`, { method: 'DELETE' })
export const computeLease = (inputs: LeaseInputs) => apiFetch<LeaseCompute>('/leases/compute/', { method: 'POST', body: JSON.stringify(inputs) })

export async function updateOmniTask(id: string, payload: {
  status?:          OmniTaskStatus
  priority?:        OmniTaskPriority
  due_at?:          string
  body?:            string
  comment?:         string
  completion_pct?:  number
  reassign_to?:     string
  reassign_reason?: string
  evidence?:        File            // optional attachment on a reply (CFO 2026-07-15)
}): Promise<{ id: string; status: OmniTaskStatus }> {
  const { evidence, ...rest } = payload
  if (evidence) {
    const fd = new FormData()
    Object.entries(rest).forEach(([k, v]) => {
      if (v != null && v !== '') fd.append(k, String(v))
    })
    fd.append('evidence', evidence)
    return apiFetch<{ id: string; status: OmniTaskStatus }>(`/tasks/${id}/`, {
      method: 'PATCH', body: fd,
    })
  }
  return apiFetch<{ id: string; status: OmniTaskStatus }>(`/tasks/${id}/`, {
    method: 'PATCH',
    body:   JSON.stringify(rest),
  })
}

/** Hand ALL my open tasks to someone else (going-on-leave). */
export async function handoverTasks(toUsername: string, reason?: string): Promise<{ reassigned: number; to: string }> {
  return apiFetch<{ reassigned: number; to: string }>('/tasks/handover/', {
    method: 'POST',
    body:   JSON.stringify({ to_username: toUsername, reason: reason || '' }),
  })
}

// ─── Payment authorisation requests (CFO 2026-07-15 "task issues") ───────────
export interface PaymentLine {
  description: string
  gl_code?:    string
  ref?:        string
  amount:      number | string
  /** A BUNDLED run (a commission or salary run) carries each payee's OWN bank
   *  account on its own line, so the FNB file Omni writes pays each person their
   *  own money. A single-payee request leaves these empty and uses the account on
   *  the request itself. (Legakwa Ntabeni 2026-09-11) */
  payee?:                string
  account_number?:       string
  account_type?:         string
  branch_code?:          string
  recipient_reference?:  string
  email?:                string
  // Supplier terms control PAY-SUP-01 (CFO 2026-07-28). Required on every line
  // of a SUPPLIER request — the backend refuses the request without them.
  // Ignored for claims and operational payments.
  invoice_number?:   string
  invoice_date?:     string
  /** 'statement' (default — terms run from month-end) or 'invoice'. */
  terms_basis?:      string
  terms_days?:       string
  due_date?:         string
  discount_checked?: boolean
  // Which claim this line settles. Required on every line of a CLAIM request
  // (CFO 2026-07-29) — the backend refuses the request without it. Ignored for
  // supplier / vendor / operational payments.
  claim_number?:     string
  // Who receives the proof of payment for THIS line (Finance spec 2026-09-08).
  // Per line, because one claim can pay a panel beater, a parts supplier and
  // the claimant. Blank falls back to Accounts on the server, the standing
  // default since CFO 2026-08-22. pop_recipient_source is stamped BY THE SERVER
  // from its own lookup — sending it is pointless, it is overwritten.
  pop_recipient_name?:   string
  pop_recipient_email?:  string
  pop_recipient_source?: string
  // Per-line CFO authorisation — "approve 9, hold 1" (CFO 2026-08-31). '' /
  // absent = pending; 'approved' / 'held' / 'rejected' once the CFO decides.
  line_status?:      '' | 'approved' | 'held' | 'rejected'
  decided_by?:       string
  decision_note?:    string
  // Reference to the original payment line this was copied from (reload feature).
  copied_from_ref?:  string
}
export interface PaymentLineProgress {
  total: number; approved: number; held: number; rejected: number; pending: number
}
// B8: 'erroneous_refund' and 'excess_refund' join 'premium_refund' — the three
// kinds of the Refund payment type (taskboard.models.PaymentRequest.Category).
export type PaymentCategory = 'claim' | 'supplier' | 'vendor' | 'petty_cash' | 'premium_refund' | 'erroneous_refund' | 'excess_refund' | 'unicoin' | 'quantum' | 'rsa' | 'veritas' | 'adh' | 'gce' | 'other' | ''
export type PaymentRequestStatus = 'exception' | 'pending_finance' | 'pending_cfo' | 'rejected' | 'paid' | 'cancelled'
export interface PaymentRequestRow {
  id:          string
  ref:         string
  entity:      string
  category?:       PaymentCategory
  category_label?: string
  currency:    string
  subject:     string
  payee:       string
  total:       string
  line_progress?:  PaymentLineProgress
  status?:         PaymentRequestStatus
  status_label?:   string
  // The bank rejected this payment (FNB batch failed) even if the workflow reads
  // "paid" — the register flags it red and counts it.
  bank_rejected?:  boolean
  // Plain-English reason an OPEN request is still open (CFO 2026-09-05): e.g.
  // "Waiting for your authorisation in the FNB app." / "Rejected by FNB — …" /
  // "Looks already PAID outside Omni — check before re-paying." Empty for closed
  // rows. `why_open_tone` is a colour tag: wait|reject|check|paid|exception.
  why_open?:       string
  why_open_tone?:  'wait' | 'reject' | 'check' | 'paid' | 'exception'
  first_approver?: string
  can_approve?:    boolean
  can_clear?:      boolean
  created_at:  string
  created_by:  string
  first_approved_at?: string | null
  rejected_at?:       string | null
  days_to_authorise?: number | null
  task_id:     string | null
  task_status: OmniTaskStatus | null
}
export interface PaymentRequestAttachment {
  id:          string
  name:        string
  uploaded_by?: string
  created_at?:  string
}
/** PAY-BANK-01 — this payee was previously paid into a different account. */
export interface PaymentBankChange {
  control:            string
  payee:              string
  known_account_tail: string
  new_account_tail:   string
  known_source:       string
  detail:             string
}
/** Beneficiary-account safety flags shown on the authorisation screen so a
 *  payment is never approved without seeing the account it goes to (Kago 2026-08-29). */
export interface PaymentBankBlock {
  first_payment: boolean            // never paid this payee before
  name_mismatch: boolean            // account-holder name ≠ payee name
  change:        PaymentBankChange | null
  change_reason: string
  has_flag:      boolean
}
export interface PaymentRequestDetail extends PaymentRequestRow {
  line_items:      PaymentLine[]
  // B8 — so the approver sees a refund IS a refund and which payment it
  // reverses. B7 — and that a request was copied from another one.
  is_refund?:            boolean
  original_payment_ref?: string
  duplicated_from_ref?:  string
  // The heading the proof of payment will carry, inherited from the request
  // SUBJECT as it stood at submission (Kelvin Kimani spec 2026-09-08).
  pop_subject?:    string
  // Bulk or Individual, and the payment count it implies (Kelvin Kimani spec
  // 2026-09-08). processing_choice_shown is false where the toggle was never a
  // live decision — a single-line request is one payment either way.
  processing_method?:       'bulk' | 'individual'
  processing_method_label?: string
  payment_count?:           number
  processing_choice_shown?: boolean
  /** Lines naming a POP recipient Omni does not hold. Non-empty means the
   *  finance approver must tick to clear it before sign-off (PAY-POP-ACK). */
  pop_off_list?: { line: number; name: string; email: string }[]
  /** PAY-BANK-04 (Finance spec 2026-09-08): the bank, branch code or account
   *  number differs from what Omni holds for this payee. Present means the
   *  details must be confirmed against the supporting document before
   *  sign-off, by somebody other than the preparer. */
  bank_details_change?: {
    control: string; payee: string; fields: string[]
    known_source: string; detail: string
  } | null
  // Amend / cancel before sign-off (Kelvin Kimani spec 2026-09-08).
  /** True while the request is pre-sign-off. It locks the moment finance signs
   *  off — changing a signed-off request is recall/reject by finance. */
  is_amendable?:      boolean
  /** Plain-English reason it cannot be amended. '' when it can. */
  amend_lock_reason?: string
  /** In the window AND this viewer is allowed to act. */
  can_amend?:         boolean
  changes?:           PaymentRequestChange[]
  cancelled_reason?:  string
  cancelled_by?:      string
  cancelled_at?:      string | null
  account_name:    string
  account_number:  string
  bank_name:       string
  branch_code?:    string
  account_type?:   string
  bank?:           PaymentBankBlock
  opening_balance: string | null
  due_date:        string | null
  payment_date?:         string | null
  early_payment_reason?: string
  funds_already_moved?:  boolean
  inputter:        string
  verifier:        string
  summary:         string
  formatted_html:  string
  // Why the bank rejected it (FNB failure reason), shown in the red banner.
  bank_reject_reason?: string
  // Whether THIS viewer may correct the branch code, decided by the server so
  // the screen can never offer a button the server then refuses.
  can_correct_branch_code?: boolean
  /** May this user record that FNB rejected it in the app? Server-decided. */
  can_mark_fnb_rejected?: boolean
  first_approved_at?: string | null
  decision_notes?:  string
  attachments?:    PaymentRequestAttachment[]
  // Per-line CFO authorisation (CFO 2026-08-31). can_decide_lines is true for the
  // CFO on a PENDING_CFO request with more than one line; line_progress drives the
  // "9 of 10 authorised" badge.
  // Exception committee (CFO 2026-09-02): present when the request tripped a
  // fraud-risk control; the drawer shows where the decision stands.
  exception_control?:  string
  exception_reason?:   string
  exception_decision?: string
  exception_signoffs?: { signer: string; decision: 'approve' | 'reject'; at: string }[]
  can_decide_lines?: boolean
  line_progress?:    PaymentLineProgress
  // The CFO or a finance approver may reject this payment at the CFO stage
  // (PENDING_CFO), not only "Clear" it.
  can_reject?:       boolean
}
/** How much of the list you were actually given. `total` is the real number of
 *  matching requests, NOT the number returned — the screen must be able to say
 *  "200 of 378" rather than implying 200 is all of them (CFO 2026-09-15). */
export interface PaymentRequestPage {
  total: number; limit: number; offset: number; returned: number; has_more: boolean
}
export async function getPaymentRequests(all = false, offset = 0): Promise<{ requests: PaymentRequestRow[]; is_cfo: boolean; is_first_approver?: boolean; showing?: string; bank_rejected_count?: number; page?: PaymentRequestPage }> {
  const q = new URLSearchParams()
  if (all) q.set('all', '1')
  if (offset) q.set('offset', String(offset))
  const qs = q.toString()
  return apiFetch(`/payment-requests/${qs ? `?${qs}` : ''}`)
}

/** Payment register (detective-monitoring) filters — the read-only history view
 *  over the whole payment estate (Kago Tshutlhedi 2026-08-29). Every field is
 *  optional; `search` matches reference, subject OR payee. */
export interface PaymentRegisterFilters {
  search?: string; entity?: string
  since?: string; until?: string; min?: string; max?: string
  limit?: number; offset?: number
}
export interface PaymentRegisterPage {
  total: number; limit: number; offset: number
  returned: number; has_more: boolean
}
export interface PaymentRegisterResult {
  requests: PaymentRequestRow[]
  is_cfo: boolean
  is_first_approver?: boolean
  showing?: string
  register?: PaymentRegisterPage
}
function paymentRegisterQuery(f: PaymentRegisterFilters): URLSearchParams {
  const p = new URLSearchParams({ register: '1' })
  if (f.search && f.search.trim()) p.set('payee', f.search.trim())   // matches ref/subject/payee
  if (f.entity && f.entity.trim()) p.set('entity', f.entity.trim())
  if (f.since) p.set('since', f.since)
  if (f.until) p.set('until', f.until)
  if (f.min && String(f.min).trim()) p.set('min', String(f.min).trim())
  if (f.max && String(f.max).trim()) p.set('max', String(f.max).trim())
  if (f.limit != null) p.set('limit', String(f.limit))
  if (f.offset != null) p.set('offset', String(f.offset))
  return p
}
/** The whole payment register (all statuses/entities), paginated — CFO and
 *  finance approvers only; ordinary staff stay scoped to their own rows. */
export async function getPaymentRegister(
  f: PaymentRegisterFilters = {},
): Promise<PaymentRegisterResult> {
  return apiFetch(`/payment-requests/?${paymentRegisterQuery(f).toString()}`)
}
/** Download the filtered register as an Excel workbook (NBFIRA audit packs). */
export async function downloadPaymentRegister(f: PaymentRegisterFilters = {}): Promise<void> {
  const p = paymentRegisterQuery(f)
  p.delete('limit'); p.delete('offset')      // export is the whole filtered set
  p.set('export', 'xlsx')
  const res = await apiFetchBinary(`/payment-requests/?${p.toString()}`)
  if (!res.ok) {
    let detail = 'Export failed.'
    try { detail = (await res.json()).detail || detail } catch { /* not JSON */ }
    throw new Error(detail)
  }
  const disposition = res.headers.get('Content-Disposition') || ''
  const m = /filename="?([^"]+)"?/.exec(disposition)
  saveBlob(await res.blob(), m ? m[1] : 'payment-register.xlsx')
}
// ── Payment History, per LINE (Finance spec 2026-09-08) ──────────────────────
// The register above reads request-by-request. A claim is queried at LINE grain
// — claim number, invoice number, amount, payee — so this is the same records
// seen the way Finance actually asks for them. Read-only.
export interface PaymentHistoryRow {
  request_id:     string
  ref:            string
  date:           string | null
  entity:         string
  line:           number
  claim_number:   string
  payee:          string
  invoice_number: string
  description:    string
  currency:       string
  amount:         string
  status:         string
  status_label:   string
  /** A pulled line is SHOWN as cancelled, never dropped. */
  cancelled:      boolean
  /** Whether this line can be copied as a new line (reload feature). */
  copyable?:      boolean
  /** Data to copy when reloading this line. */
  copy?:          {
    description?: string
    gl_code?: string
    ref?: string
    terms_basis?: string
    terms_days?: string
    claim_number?: string
    pop_recipient_name?: string
    pop_recipient_email?: string
    copied_from_ref?: string
  }
}
export interface PaymentHistoryTotal {
  currency:         string
  amount:           string
  count:            number
  cancelled_amount: string
  cancelled_count:  number
}
export interface PaymentHistoryResult {
  window:    string
  scope:     'all' | 'mine'
  rows:      PaymentHistoryRow[]
  row_count: number
  totals:    PaymentHistoryTotal[]
  truncated: boolean
}
export interface PaymentHistoryFilters {
  /** Custom range, either end may stand alone. */
  from?:  string
  to?:    string
  /** Month/Year quick-select. A month needs its year — the server refuses a
   *  bare month rather than guessing which year was meant. */
  year?:  string
  month?: string
  payee?: string
  /** '1' adds each row's copy-forward payload (reload a previous payment). */
  copy?:  '1'
}
export async function getPaymentHistory(
  f: PaymentHistoryFilters = {},
): Promise<PaymentHistoryResult> {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(f)) if (v) p.set(k, String(v))
  const q = p.toString()
  return apiFetch(`/payment-requests/history/${q ? `?${q}` : ''}`)
}

/** CFO clears a signed-off request out of the queue without paying it
 *  (expired / paid outside Omni / duplicate). Requires a reason. */
export async function clearPaymentRequest(
  id: string, notes: string,
): Promise<{ status: PaymentRequestStatus; status_label: string }> {
  return apiFetch(`/payment-requests/${id}/clear/`, {
    method: 'POST', body: JSON.stringify({ notes }),
  })
}

// Record that a person DECLINED the authorisation inside the FNB app. FNB never
// tells Omni, so the queue used to read "Waiting for your authorisation in the
// FNB app" for ever (Leano Makwapa / Koketso Kgetse, 2026-09-11). This moves no
// money and closes nothing — the payment re-opens as "Rejected by FNB" so the
// bank details can be fixed and it can be loaded again.
export async function markPaymentRejectedOnFnb(
  id: string, reason: string,
): Promise<{ batch_status: string; failure_reason: string; message: string }> {
  return apiFetch(`/payment-requests/${id}/mark-rejected-fnb/`, {
    method: 'POST', body: JSON.stringify({ reason }),
  })
}

// ── Bulk payment upload (Legakwa Ntabeni 2026-09-11) ─────
// READS a file and says what is in it. Creates NOTHING: the rows are then created
// through the ordinary gated create endpoint, one call each, so every money
// control applies to every row. See components/payments/BulkPaymentUpload.tsx.
export interface BulkParsedRow {
  line: number
  name: string
  account_number: string
  account_type: string
  branch_code: string
  amount: string
  own_reference: string
  recipient_reference: string
  email: string
  problems: string[]
  ok: boolean
}
export interface BulkParseResult {
  rows: BulkParsedRow[]
  source_account: string
  total: string
  ok: number
  bad: number
  category: string
  /** True ONLY when a run was positively asked for. Anything else is separate. */
  bundled: boolean
  arrive_as: 'run' | 'separate'
  how_they_will_arrive: string
  message: string
}
export async function bulkParsePaymentFile(
  file: File, category: string, arriveAs: 'run' | 'separate',
): Promise<BulkParseResult> {
  const fd = new FormData()
  fd.append('file', file)
  fd.append('category', category)
  fd.append('arrive_as', arriveAs)
  return apiFetch('/payment-requests/bulk-parse/', { method: 'POST', body: fd })
}

// ── FNB reconcile: match the open queue against the bank's pending list ───────
export interface FnbReconcileLine { amount: string; ref: string; in_fnb: boolean; why: string }
export interface FnbReconcileRow { id: string; ref: string; subject: string; total: string; category: string; lines: FnbReconcileLine[]; closed?: boolean }
export interface FnbDuplicate { amount: string; count: number; names: string[] }
export interface FnbClosedRow { ref: string; subject: string; total: string }
export interface FnbSkippedRow { ref: string; why: string }
export interface FnbReconcileResult {
  already_paid: FnbReconcileRow[]
  still_pending: FnbReconcileRow[]
  no_lines: FnbReconcileRow[]
  duplicates: FnbDuplicate[]
  closed: FnbClosedRow[]
  skipped: FnbSkippedRow[]
  intelligence: string
  ai_source: string
  auto_close: boolean
  auto_close_applied: boolean
  warning: string
  fnb_lines_read: number
  requests_checked: number
}
/** Reconcile the open payment queue against the FNB "Batch Payments" pending list.
 *  Pass a PDF (the FNB export) or pasted text. With autoClose (default) the clearly
 *  already-paid requests are closed server-side and a DeepSeek note explains what
 *  happened. */
export async function reconcilePaymentsAgainstFnb(
  opts: { pdf?: File | null; text?: string; autoClose?: boolean },
): Promise<FnbReconcileResult> {
  const autoClose = opts.autoClose !== false
  if (opts.pdf) {
    const form = new FormData()
    form.append('fnb_pdf', opts.pdf)
    form.append('auto_close', String(autoClose))
    return apiFetch('/payment-requests/fnb-reconcile/', { method: 'POST', body: form })
  }
  return apiFetch('/payment-requests/fnb-reconcile/', {
    method: 'POST', body: JSON.stringify({ fnb_text: opts.text || '', auto_close: autoClose }),
  })
}
// ── Email catch-up: the bank's "Fully Processed" emails vs the stuck queue ────
export interface EmailCatchupMatch { ref: string; amount: string; date: string }
export interface EmailCatchupRow {
  id: string; ref: string; entity: string; category: string; subject: string
  total: string; made: string; can_clear: boolean
  confidence: 'confident' | 'review' | 'ambiguous'; matches: EmailCatchupMatch[]
}
export interface EmailCatchupResult {
  rows: EmailCatchupRow[]; emails_read: number; lookback_days: number; is_cfo: boolean
}
/** Read the FNB "Fully Processed" emails and show which stuck (PENDING_CFO)
 *  requests the bank has confirmed paid — for one-tap catch-up. Read-only. */
export async function getFnbEmailCatchup(): Promise<EmailCatchupResult> {
  return apiFetch('/payment-requests/email-catchup/')
}
/** Mark one request PAID because the CFO confirmed the bank's "Fully Processed"
 *  email on the catch-up screen. Terminal state PAID (keeps duplicate control). */
export async function markPaymentPaidFromBank(
  id: string, reason: string,
): Promise<{ status: PaymentRequestStatus; status_label: string }> {
  return apiFetch(`/payment-requests/${id}/mark-paid-bank/`, {
    method: 'POST', body: JSON.stringify({ reason }),
  })
}
/** One row in the "upload FNB list" reconcile result. */
export interface FnbListRow {
  id: string; ref: string; payee: string; total: string; currency: string
  status: PaymentRequestStatus; bank_rejected: boolean; not_loaded: boolean; in_list: boolean
}
export interface FnbListReconcileResult {
  mode: 'preview' | 'apply'
  entries_read: number
  keep_count?: number
  close_count?: number
  close_total?: string
  rejected_in_close?: number
  // Requests the bank rejected or never confirmed. They are NOT closed by this
  // button — the money did not necessarily move, so they stay open for a
  // person (CFO's FNB brief, control 3).
  rejected_will_stay_open?: number
  would_stay_open?: FnbListRow[]
  stay_open_reason?: string
  left_open_rejected_count?: number
  left_open_rejected?: FnbListRow[]
  left_open_reason?: string
  not_loaded_in_close?: number
  skipped_count?: number
  would_close?: FnbListRow[]
  would_keep?: FnbListRow[]
  skipped_own?: FnbListRow[]
  closed_count?: number
  closed_total?: string
  kept_count?: number
  rejected_closed?: number
  not_loaded_closed?: number
  closed?: FnbListRow[]
  failed_count?: number
}
/** Upload the FNB "Batch Payments" PDF. mode='preview' shows what would close
 *  (writes nothing); mode='apply' closes every open request no longer on the list
 *  (marks them PAID with an audit note — reversible). CFO / finance approver only. */
export async function reconcileFnbList(
  file: File, mode: 'preview' | 'apply',
): Promise<FnbListReconcileResult> {
  const form = new FormData()
  form.append('file', file)
  form.append('mode', mode)
  return apiFetch('/payment-requests/reconcile-fnb-list/', { method: 'POST', body: form })
}
/** Stage-1 finance sign-off (Pako / Kago / Legakwa).
 *
 *  `bankAck` is sent only after the server refused the sign-off under
 *  PAY-BANK-ACK — the approver has positively acknowledged a changed account.
 *  (The duplicate override was removed on 2026-09-02: a hard duplicate is
 *  refused outright, never cleared with a reason.) */
export async function decidePaymentRequest(
  id: string, decision: 'approve' | 'reject', notes = '',
  bankAck = false, popAck = false, bankDocAck = false,
): Promise<{ status: PaymentRequestStatus; status_label: string; cfo_task_id?: string }> {
  return apiFetch(`/payment-requests/${id}/decide/`, {
    method: 'POST',
    body: JSON.stringify({
      decision, notes,
      // Set only after the server refused the sign-off under PAY-BANK-ACK: the
      // finance approver has positively acknowledged the changed account.
      ...(bankAck ? { bank_ack: true } : {}),
      // Likewise PAY-POP-ACK (Finance spec 2026-09-08): the proof of payment
      // names an address Omni does not hold, and a second person — never the
      // raiser — has checked it.
      ...(popAck ? { pop_ack: true } : {}),
      // PAY-BANK-DOC (Finance spec 2026-09-08): Finance has confirmed the bank
      // details on this request against the beneficiary details on the attached
      // supporting document. Never the preparer — the raiser cannot sign off
      // their own request at all.
      ...(bankDocAck ? { bank_doc_ack: true } : {}),
    }),
  })
}
// ── Amend / cancel before finance sign-off (Kelvin Kimani spec 2026-09-08) ───
// The inputter's own window to fix a wrong amount or drop an invoice that
// should not be paid, without abandoning and re-raising the whole request.
// NOTHING IS EVER HARD-DELETED: a cancel flags the line or the request and
// keeps it in full, with who pulled it, when and why.
export interface PaymentRequestChange {
  id:           string
  action:       'amend' | 'cancel_line' | 'cancel_request'
  action_label: string
  line:         number | null
  field:        string
  value_before: string
  value_after:  string
  reason:       string
  /** "Amended by Kago Tshutlhedi · Finance" — read from the logged-in user at
   *  the time, never typed. */
  attribution:  string
  at:           string
}
export interface PaymentLineChange {
  line:  number
  field: 'amount' | 'invoice_number' | 'invoice_date' | 'due_date' | 'gl_code' | 'description'
  value: string
}
export interface PaymentAmendResult {
  total:         string
  currency:      string
  payment_count: number
  changes:       PaymentRequestChange[]
  /** What a completed cross-check this amend invalidated, in plain English. */
  cross_check_cleared: string[]
  /** Present when the PAYEE was changed — the classic payment-redirection risk,
   *  flagged distinctly rather than treated as an ordinary field edit. */
  payee_changed?: { control: string; message: string }
}
export interface BranchCodeCorrectionResult {
  ref: string
  field: string
  before: string
  after: string
  by: string
  note: string
  bank_for_code?: string
  changes?: unknown[]
}

/** Correct the branch code the bank rejected. ONE field — never the account
 *  number, which is what identifies the payee. The server refuses a code that
 *  belongs to a different bank, because the first two digits of a Botswana
 *  branch code choose the bank. */
export async function correctPaymentRequestBranchCode(
  id: string,
  branchCode: string,
): Promise<BranchCodeCorrectionResult> {
  return apiFetch(`/payment-requests/${id}/correct-branch-code/`, {
    method: 'POST',
    body: JSON.stringify({ branch_code: branchCode }),
  })
}

export async function amendPaymentRequest(
  id: string,
  lineChanges: PaymentLineChange[] = [],
  requestChanges: Record<string, string> = {},
): Promise<PaymentAmendResult> {
  return apiFetch(`/payment-requests/${id}/amend/`, {
    method: 'POST',
    body: JSON.stringify({ line_changes: lineChanges, request_changes: requestChanges }),
  })
}
/** Cancel ONE line, or the whole request when `line` is omitted. A reason is
 *  required either way — a pulled payment is what an auditor asks "why" about. */
export async function cancelPaymentRequest(
  id: string, reason: string, line?: number,
): Promise<{ status: PaymentRequestStatus; status_label: string; total: string
             currency: string; emptied: boolean; changes: PaymentRequestChange[] }> {
  return apiFetch(`/payment-requests/${id}/cancel/`, {
    method: 'POST',
    body: JSON.stringify({ reason, ...(line ? { line } : {}) }),
  })
}

export async function getPaymentRequest(id: string): Promise<PaymentRequestDetail> {
  return apiFetch(`/payment-requests/${id}/`)
}
/** Bulk authorise (CFO 2026-08-31): preview then approve every CLEAN pack awaiting
 *  the CFO — optionally scoped to a category tab. The server decides the list;
 *  the CFO echoes back confirm_total so what he read is what he releases. Packs
 *  with a duplicate clash come back as `blocked` and are never swept in. */
export interface BulkPackRow {
  id: string; ref: string; entity: string; subject: string; currency: string
  total: string; lines: number; signed_off_by: string
  clashes?: number; clash_total?: string; clash_detail?: string[]
}
export interface BulkPaymentPreview {
  ready: BulkPackRow[]; blocked: BulkPackRow[]
  ready_total: string; ready_count: number; blocked_count: number
}
export interface BulkPaymentResult {
  authorised_count: number; authorised_total: string
  refused: { ref: string; reason: string }[]; refused_count: number
  still_blocked: BulkPackRow[]
}
export async function previewPaymentBulk(categories?: string[]): Promise<BulkPaymentPreview> {
  const q = categories && categories.length ? `?categories=${encodeURIComponent(categories.join(','))}` : ''
  return apiFetch(`/payment-requests/bulk/preview/${q}`)
}
export async function approvePaymentBulk(confirmTotal: string, categories?: string[]): Promise<BulkPaymentResult> {
  return apiFetch('/payment-requests/bulk/approve/', {
    method: 'POST',
    body: JSON.stringify({ confirm_total: confirmTotal, ...(categories && categories.length ? { categories } : {}) }),
  })
}
/** Per-line CFO authorisation — "approve 9, hold 1" (CFO 2026-08-31). Sets the
 *  status of the chosen lines; the request auto-closes once every line is
 *  approved or rejected. A held line keeps it open on that line. Money never
 *  moves here (the batch was loaded to FNB at finance sign-off). */
export async function decidePaymentRequestLines(
  id: string, lineIndexes: number[], action: 'approve' | 'hold' | 'reject', note = '',
): Promise<{ status: PaymentRequestStatus; status_label: string;
             line_progress: PaymentLineProgress; closed: boolean }> {
  return apiFetch(`/payment-requests/${id}/decide-lines/`, {
    method: 'POST',
    body: JSON.stringify({ line_indexes: lineIndexes, action, ...(note.trim() ? { note: note.trim() } : {}) }),
  })
}
/** Smart fill (CFO 2026-07-29): paste messy text, omni's AI-cleanup engine
 *  returns suggested payee + lines. Advisory only — nothing is saved; the form
 *  is filled and the create endpoint still validates. Never throws on an AI
 *  outage: returns {ok:false, reason} so the clerk just types by hand. */
export interface SmartFillLine {
  description?: string; amount?: string
  invoice_number?: string; invoice_date?: string; claim_number?: string
}
export async function parsePaymentPaste(text: string): Promise<{
  ok: boolean; reason?: string; payee?: string; lines?: SmartFillLine[]
  note?: string; redactions?: number
}> {
  return apiFetch('/payment-requests/parse/', {
    method: 'POST', body: JSON.stringify({ text }),
  })
}

// What did we last pay this payee into? A lookup, not a model — the account
// number decides who gets the money, so it comes from our own records.
export interface PayeeBankHistory {
  found: boolean
  source?: string
  source_label?: string
  account_name?: string
  account_number?: string
  /** True when the digits are masked to the last four (ordinary raisers). The
   *  form then sends use_known_account=true and the server fills the real
   *  number in itself — the browser never sees it. */
  account_masked?: boolean
  bank_name?: string
  branch_code?: string
  account_type?: string
  last_ref?: string
}

export const lookupPayeeBank = (payee: string) =>
  apiFetch<PayeeBankHistory>(
    `/payment-requests/payee-bank/?payee=${encodeURIComponent(payee)}`)

export interface PayeeBankChange {
  changed: boolean
  detail?: string
  known_account_tail?: string
  new_account_tail?: string
  known_source?: string
  /** PAY-BANK-03 (CFO 2026-09-01): we have never paid this payee, so there is
   *  no previous account to compare against and `changed` is false. The form
   *  must still ask the raiser to confirm the digits — a first-time payee was
   *  previously challenged by nothing at all. */
  first_payment?: boolean
}

export const checkPayeeBankChange = (payee: string, account_number: string) =>
  apiFetch<PayeeBankChange>('/payment-requests/payee-bank-check/', {
    method: 'POST',
    body: JSON.stringify({ payee, account_number }),
  })

// ── POP Recipient options for one payment line (Finance spec 2026-09-08) ─────
// A lookup, never a model: the vendor register's remembered POP address, the
// contact book, the claimant named on the claim, and the Accounts default.
export interface PopRecipientOption {
  source:       string
  name:         string
  email:        string
  source_label: string
}
export async function getPopRecipients(
  claim_number = '', payee = '',
): Promise<{ options: PopRecipientOption[]; default: PopRecipientOption }> {
  const p = new URLSearchParams()
  if (claim_number) p.set('claim_number', claim_number)
  if (payee) p.set('payee', payee)
  const q = p.toString()
  return apiFetch(`/payment-requests/pop-recipients/${q ? `?${q}` : ''}`)
}

export async function createPaymentRequest(input: {
  // Idempotency (CFO 2026-09-14): one key per submit attempt, generated
  // client-side. The server enforces uniqueness with a database constraint
  // so a double-click or a retried request can never raise the payment twice.
  client_request_id?: string
  entity:           string
  category?:        PaymentCategory
  // Required when category is 'claim' (CFO 2026-07-29): a claim paid to a
  // repairer or other provider carries a real invoice and is gated like any
  // supplier payment; one paid direct to the policyholder is not.
  claim_payee_type?: 'client' | 'provider'
  currency:         string
  subject:          string
  payee?:           string
  line_items:       PaymentLine[]
  /** How the money should leave (Kelvin Kimani spec 2026-09-08): one bulk
   *  payment for the supplier total, or one payment per invoice. Defaults to
   *  'bulk'; with a single line the server forces 'bulk' because the two are
   *  then the same one payment. Amounts never change either way. */
  processing_method?: 'bulk' | 'individual'
  account_name?:    string
  account_number?:  string
  bank_name?:       string
  // Captured on the request so the payment is never retyped in FNB
  // (CFO 2026-08-20). account_name already existed above and was never sent.
  branch_code?:     string
  account_type?:    string
  bank_change_reason?: string
  /** PAY-BANK-03: the raiser confirmed the account of a payee we have never
   *  paid before. The server refuses the request without it. */
  new_payee_confirmed?: boolean
  opening_balance?: string
  due_date?:        string
  inputter?:        string
  verifier?:        string
  approver_id?:     string
  // Supplier terms control PAY-SUP-01 (CFO 2026-07-28).
  payment_date?:         string
  early_payment_reason?: string
  funds_already_moved?:  boolean
  // Duplicate control PAY-DUP-01 (CFO 2026-08-03). Sent ONLY after the server
  // has refused the pack as a duplicate — the written justification for paying
  // something already raised or paid (e.g. the bank rejected the first attempt).
  // It is stored on the request and printed on the authorisation pack.
  // Server-side autofill (audit H4): use the account Omni already holds for
  // this payee — the lookup hands an ordinary raiser a masked number.
  use_known_account?: boolean
}): Promise<{ id: string; ref: string; task_id: string; status?: PaymentRequestStatus; assigned_to?: string;
              // Set when a control routed the request to the exception committee.
              exception?: { control: string; message: string } }> {
  return apiFetch('/payment-requests/', { method: 'POST', body: JSON.stringify(input) })
}

// ── Payment loading window override, PAY-WIN-02 (CFO 2026-09-01) ────────────
export interface LoadWindow {
  open: string
  close: string
  is_open: boolean
  exempt: boolean
  has_override: boolean
}
export interface LoadOverride {
  id: string
  requested_by: string
  requested_by_email: string
  reason: string
  for_date: string
  status: 'pending' | 'approved' | 'declined'
  requested_at: string
  decided_by: string | null
  decided_at: string | null
  decision_note: string
}
export interface LoadOverrideList {
  overrides: LoadOverride[]
  is_cfo: boolean
  pending_count?: number
  window: LoadWindow
}

/** Ask the CFO for permission to load a payment outside the morning window. */
export async function requestLoadOverride(
  reason: string,
): Promise<{ override: LoadOverride; already: boolean; window: LoadWindow }> {
  return apiFetch('/payment-requests/load-override/', {
    method: 'POST', body: JSON.stringify({ reason }),
  })
}

/** List load-override requests. CFO sees the pending queue (all=1 for history). */
export async function listLoadOverrides(all = false): Promise<LoadOverrideList> {
  return apiFetch(`/payment-requests/load-override/${all ? '?all=1' : ''}`)
}

/** CFO approves or declines a load-override request. */
export async function decideLoadOverride(
  id: string, decision: 'approve' | 'decline', note?: string,
): Promise<{ override: LoadOverride }> {
  return apiFetch(`/payment-requests/load-override/${id}/decide/`, {
    method: 'POST', body: JSON.stringify({ decision, note: note || '' }),
  })
}

/** Upload one optional supporting document to a payment request. */
export async function uploadPaymentRequestAttachment(
  reqId: string, file: File,
): Promise<PaymentRequestAttachment> {
  const form = new FormData()
  form.append('file', file)
  return apiFetch(`/payment-requests/${reqId}/attachments/`, { method: 'POST', body: form })
}

/** Download a payment-request attachment through the authenticated endpoint. */
export async function downloadPaymentRequestAttachment(
  attId: string, name: string,
): Promise<void> {
  const res = await apiFetchBinary(`${API_BASE}/payment-request-attachments/${attId}/file/`)
  if (!res.ok) throw new Error(`Download failed: ${res.status}`)
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url; a.download = name || 'attachment'; a.click()
  setTimeout(() => URL.revokeObjectURL(url), 30000)
}

/** The FNB bulk-payment file for one request — the template Finance loads into
 *  the bank. The endpoint existed since PR #48d6ec39 with NO caller, so nobody
 *  could reach it (CFO 2026-09-15). The server names the file, so we keep its
 *  Content-Disposition rather than inventing one, and we surface its own refusal
 *  (409 "no payment lines", 403 "not allowed") instead of a blank download. */
export async function downloadPaymentRequestFnbFile(reqId: string, ref?: string): Promise<void> {
  const res = await apiFetchBinary(`${API_BASE}/payment-requests/${reqId}/fnb-file/`)
  if (!res.ok) {
    let detail = `Could not build the bank file (${res.status})`
    try { const j = await res.json(); if (j?.detail) detail = j.detail } catch { /* not json */ }
    throw new Error(detail)
  }
  const cd = res.headers.get('Content-Disposition') || ''
  const m = cd.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i)
  const fname = m && m[1] ? decodeURIComponent(m[1]) : `Payment_CSV_Template_All - ${ref || reqId}.csv`
  // saveBlob, not a hand-rolled anchor: the OmniDesktop Windows app (WebView2)
  // silently drops a bare <a download> and the file is never saved.
  saveBlob(await res.blob(), fname)
}

// ─── Taskboard reminder engine + completion gate (CFO 2026-07-09, PR #292) ──

export interface TaskReminder {
  id: string
  task: string
  task_title: string
  task_due_at: string | null
  type: 'assign_day' | 'due_day' | 'overdue'
  acknowledged: boolean
  created_at: string
}

export async function getTaskReminders(): Promise<TaskReminder[]> {
  return apiFetch<TaskReminder[]>('/taskboard/notifications/')
}

export async function ackTaskReminder(id: string): Promise<void> {
  await apiFetch(`/taskboard/notifications/${id}/ack/`, { method: 'POST' })
}

export interface AriaPopupMessage {
  id: string
  message: string
  sender: string
  created_at: string
}

export async function getAriaPopups(): Promise<AriaPopupMessage[]> {
  const r = await apiFetch<{ popups: AriaPopupMessage[] }>('/aria/popups/')
  return r.popups ?? []
}

export async function markAriaPopupRead(id: string): Promise<void> {
  await apiFetch(`/aria/popups/${id}/read/`, { method: 'POST' })
}

// The server re-validates the note-length gate (>=30 chars) — the modal's
// meter is a mirror, not the enforcement. Seconds are recorded for audit only
// (dwell gate removed, CFO 2026-07-18).
export async function completeTaskWithNote(
  taskId: string, body: string, interactionSeconds: number,
): Promise<{ detail: string; completed_at: string }> {
  return apiFetch(`/taskboard/tasks/${taskId}/complete/`, {
    method: 'POST',
    body: JSON.stringify({ body, interaction_seconds: interactionSeconds }),
  })
}

// ─── Staff privacy notice — login pop-up + acknowledgement (CFO 2026-07-09) ──

export interface PrivacyNotice {
  version: string
  title: string
  html: string
  checkbox_label?: string
  cta_label?: string
  acknowledged: boolean
}

export async function getPrivacyNotice(): Promise<PrivacyNotice> {
  return apiFetch<PrivacyNotice>('/privacy-notice/')
}

export async function ackPrivacyNotice(): Promise<{ acknowledged: boolean; version: string }> {
  return apiFetch('/privacy-notice/ack/', { method: 'POST' })
}

// ─── RealPay monthly reports (CFO directive 2026-05-25) ────────────────────

export interface RealPayReportRow {
  id:                   string
  period_year:          number
  period_month:         number
  period_label:         string
  beneficiary_user_id:  string
  beneficiary_label:    string
  status:               'pending' | 'pulled' | 'analysed' | 'failed'
  txn_count_total:      number
  txn_count_successful: number
  txn_count_failed:     number
  amount_collected:     string
  amount_failed:        string
  amount_net:           string
  ai_commentary:        string
  xlsx_url:             string | null
  pulled_at:            string | null
  analysed_at:          string | null
  error_log:            string
}

export async function listRealPayReports(): Promise<{ reports: RealPayReportRow[] }> {
  return apiFetch<{ reports: RealPayReportRow[] }>('/realpay/reports/')
}

export async function pullRealPayMonth(input: {
  year: number; month: number;
  beneficiary_user_id: string; label?: string;
}): Promise<RealPayReportRow> {
  return apiFetch<RealPayReportRow>('/realpay/pull-month/', {
    method: 'POST',
    body:   JSON.stringify(input),
  })
}

export async function backfillRealPay(input: {
  beneficiary_user_id: string; label?: string;
  from_year?: number; from_month?: number;
}): Promise<{ count: number; reports: RealPayReportRow[] }> {
  return apiFetch<{ count: number; reports: RealPayReportRow[] }>(
    '/realpay/backfill/', { method: 'POST', body: JSON.stringify(input) },
  )
}

export async function regenerateRealPayCommentary(id: string): Promise<RealPayReportRow> {
  return apiFetch<RealPayReportRow>(`/realpay/reports/${id}/`, {
    method: 'PATCH',
    body:   JSON.stringify({ regenerate_commentary: true }),
  })
}

// ─── RealPay analytics (CFO directive 2026-05-25) ──────────────────────────

export interface RealPayAnalyticsSummary {
  months_loaded:        number
  total_txns:           number
  total_successful:     number
  total_failed:         number
  total_collected:      string
  total_failed_amount:  string
}
export interface RealPayDefaulter {
  name: string; account: string;
  months_failed: number; months_paid: number;
  amount_failed: string; last_status: string; last_month: string;
}
export interface RealPayMultiDebit {
  account: string; holder: string;
  contract_count: number; contracts: string[];
}
export interface RealPayTopCollected {
  name: string; account: string;
  amount_paid: string; months_paid: number;
}
export interface RealPayTopFailed {
  name: string; account: string;
  amount_failed: string; months_failed: number;
}
export interface RealPayDeclineReason {
  code: string; count: number; amount: string;
}
export interface RealPayTrendPoint {
  period: string; collected: string; failed: string;
  ok_count: number; fail_count: number;
}
export interface RealPayMandateFlag {
  name: string; account: string;
  failure_rate: number; months_failed: number;
}
export interface RealPayAnalyticsResponse {
  summary:            RealPayAnalyticsSummary
  not_paying:         RealPayDefaulter[]
  multi_debit_payers: RealPayMultiDebit[]
  top10_collected:    RealPayTopCollected[]
  top10_failed:       RealPayTopFailed[]
  decline_reasons:    RealPayDeclineReason[]
  month_trend:        RealPayTrendPoint[]
  mandate_flags:      RealPayMandateFlag[]
}

export async function getRealPayAnalytics(
  months = 12, beneficiary_user_id = '',
): Promise<RealPayAnalyticsResponse> {
  const q = new URLSearchParams()
  q.set('months', String(months))
  if (beneficiary_user_id) q.set('beneficiary_user_id', beneficiary_user_id)
  return apiFetch<RealPayAnalyticsResponse>(`/realpay/analytics/?${q.toString()}`)
}

// ─── RealPay LIVE from Graphite (source of truth, no vendor key) ───────────
export interface RealPayGraphiteMonth {
  ym: string
  collected_count: number
  collected_amount: number
  failed_count: number
  failed_amount: number
  processing_count: number
}
export interface RealPayGraphiteLive {
  configured: boolean
  months?: number
  rows: RealPayGraphiteMonth[]
  totals: { collected_count?: number; collected_amount?: number; failed_count?: number; failed_amount?: number }
  last30: { collected_count?: number; collected_amount?: number; failed_count?: number }
  source?: string
  error?: string
}
export async function getRealPayGraphiteLive(months = 6): Promise<RealPayGraphiteLive> {
  return apiFetch<RealPayGraphiteLive>(`/realpay/graphite-live/?months=${months}`)
}

// ─── RealPay Collections module (CFO directive 2026-06-05) ─────────────────
// Objective 1 — Failed / Error Debit Tracker; Objective 2 — Collections Dashboard.

export interface RealPayDebitRow {
  txn_date: string | null
  client_number: string
  contract_number: string
  total_amount: string
  collected_amount: string
  current_status: string
  result_code: string
  reason: string
  matched: boolean
  ambiguous: boolean
  client_bank: string
}

export interface RealPayDebitTracker {
  summary: Record<string, number>          // SUCCESSFUL/FAILED/PROCESSING/ERROR/CANCELLED/OTHER/TOTAL
  rows: RealPayDebitRow[]
  row_count_filtered: number
  rows_truncated: boolean
  unmatched_reason_count: number
  filters: { start: string | null; end: string | null; status: string }
  assumptions: string[]
  data_present: boolean
}

export interface RealPayCollectionGroup {
  key: string
  label: string
  collected: string
  collected_lines: number
  attempts: number
  /** Debits raised in the window whose result never came back. */
  awaiting_result?: number
  /** True when NOTHING was attempted but debits were raised — the collected
   *  figure is then an absence of information, not a zero. */
  no_result_received?: boolean
  count?: number
  pct: number
}

export interface RealPayCollectionsDashboard {
  groups: RealPayCollectionGroup[]
  total_collected: string
  total_collected_lines: number
  total_attempts: number
  total_count: number
  undated_rows: number
  reconciled: boolean | null
  control_total: string | null
  variance: string | null
  filters: { start: string | null; end: string | null }
  unmapped_prefixes: { prefix: string; count: number }[]
  awaiting_result_total?: number
  groups_with_no_result?: string[]
  assumptions: string[]
  data_present: boolean
  source?: string
  // Where the figure came from, in words — a total with no provenance is a
  // number Finance cannot defend.
  source_label?: string
  cross_check?: { graphite_total: string | null; difference: string | null
                  note?: string } | null
  // Billing rows loaded before the tracking date existed: in the window, but
  // NOT in the total. Surfaced so the gap is visible rather than silent.
  legacy_undated_billing_rows?: number
  legacy_undated_note?: string | null
  live?: boolean
  as_of?: string
}

export async function uploadRealPayBilling(file: File): Promise<{
  rows_read: number; created?: number; updated?: number
  collected_total?: string
  file?: { name: string; sheet?: string | null; rows_skipped?: number }
}> {
  const fd = new FormData()
  fd.append('file', file)
  return apiFetch('/realpay/collections/billing-upload/', { method: 'POST', body: fd })
}

export async function getRealPayDebitTracker(params: {
  start?: string; end?: string; status?: string; limit?: number
}): Promise<RealPayDebitTracker> {
  const q = new URLSearchParams()
  if (params.start) q.set('start', params.start)
  if (params.end) q.set('end', params.end)
  if (params.status) q.set('status', params.status)
  if (params.limit) q.set('limit', String(params.limit))
  return apiFetch<RealPayDebitTracker>(`/realpay/collections/debit-tracker/?${q.toString()}`)
}

export async function getRealPayCollectionsDashboard(params: {
  start?: string; end?: string
}): Promise<RealPayCollectionsDashboard> {
  const q = new URLSearchParams()
  if (params.start) q.set('start', params.start)
  if (params.end) q.set('end', params.end)
  return apiFetch<RealPayCollectionsDashboard>(`/realpay/collections/dashboard/?${q.toString()}`)
}

async function _downloadCsv(path: string, filename: string): Promise<void> {
  const res = await apiFetchBinary(`${API_BASE}${path}`)
  if (!res.ok) throw new Error(`Export failed (${res.status})`)
  const blob = await res.blob()
  const url = window.URL.createObjectURL(blob)
  const a = document.createElement('a')
  // Installed Omni app (Android TWA / iOS home-screen WebView) silently blocks
  // a script-triggered `a.click()` download — the spinner runs and no file ever
  // saves ("loads and returns nothing", bug fb4647f7 / Bokani 2026-09-09). In
  // standalone display-mode, open the blob in a new tab instead so the OS
  // save/share sheet handles it. Desktop browsers keep the direct download
  // (byte-identical to before), so there is no change to the normal path.
  const standalone =
    (typeof window !== 'undefined' &&
      window.matchMedia?.('(display-mode: standalone)').matches) ||
    (typeof navigator !== 'undefined' &&
      (navigator as unknown as { standalone?: boolean }).standalone === true)
  if (standalone || !('download' in a)) {
    window.open(url, '_blank', 'noopener')
    setTimeout(() => window.URL.revokeObjectURL(url), 10000)
    return
  }
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  window.URL.revokeObjectURL(url)
}

/** Download the debit-tracker export as CSV (audit-logged server-side). */
// ── RealPay upload-and-reconcile (Keetile Mokhendo, 2026-08-17) ─────────────
export interface RealPayReconItem {
  contract_number: string; contract_raw: string; client_name: string
  lines: number; collected: string; successful: number; failed: number
  last_date?: string | null
  expected?: string; difference?: string
  graphite?: { policy_number: string; status: string; premium: string } | null
}
export interface RealPayReconResult {
  graphite_available: boolean
  counts: {
    realpay_rows: number; realpay_contracts: number; matched: number
    amount_differs: number; only_in_realpay: number; only_in_graphite: number
    unverified?: number
  }
  totals: Record<string, string>
  matched: RealPayReconItem[]
  amount_differs: RealPayReconItem[]
  only_in_realpay: RealPayReconItem[]
  unverified?: RealPayReconItem[]
  only_in_graphite: { contract_number: string; policy_number: string; status: string; expected: string }[]
  only_in_graphite_checked?: boolean
  truncated: boolean
  file: { name: string; sheet: string; rows_read: number; rows_skipped_no_contract: number }
  commentary: { ok: boolean; text: string; reason: string }
}

/** Upload a RealPay export and get it reconciled against Graphite.
 *  Multipart — apiFetch passes FormData through without forcing a JSON header. */
export async function uploadRealPayRecon(file: File, opts?: { commentary?: boolean }
): Promise<RealPayReconResult> {
  const fd = new FormData()
  fd.append('file', file)
  if (opts?.commentary === false) fd.append('commentary', '0')
  return apiFetch<RealPayReconResult>('/realpay/collections/recon-upload/',
    { method: 'POST', body: fd })
}

/* ── Keetile's six Finance monitoring reports (handover note 17 Aug 2026) ──── */

export interface FinanceMonitoringReport {
  slug: string
  title: string
  cadence: string
  owner: string
}

export interface FinanceMonitoringResult {
  slug: string
  title: string
  cadence: string
  rows: Record<string, unknown>[]
  summary: Record<string, number>
  meta: { title: string; source: string; generated_at: string; note?: string
          date_from?: string; date_to?: string }
}

export async function listFinanceMonitoringReports(
): Promise<{ reports: FinanceMonitoringReport[] }> {
  return apiFetch<{ reports: FinanceMonitoringReport[] }>('/finance-monitoring/')
}

export async function getFinanceMonitoringReport(
  slug: string, params?: Record<string, string>,
): Promise<FinanceMonitoringResult> {
  const q = new URLSearchParams(params || {}).toString()
  return apiFetch<FinanceMonitoringResult>(
    `/finance-monitoring/${slug}/${q ? `?${q}` : ''}`)
}

/** Download a report as CSV.
 *
 *  Goes through the fetch-blob helper, NOT a plain `<a href>`. The token lives
 *  in localStorage / MSAL and is sent as an Authorization header, so a bare
 *  anchor carries no credential and the endpoint 401s. This has bitten before —
 *  see `project_authed_download_needs_fetch`. */
export async function downloadFinanceMonitoringCsv(slug: string): Promise<void> {
  await _downloadCsv(`/finance-monitoring/${slug}/?download=csv`, `${slug}.csv`)
}

export async function downloadRealPayDebitExport(params: {
  start?: string; end?: string; status?: string
}): Promise<void> {
  const q = new URLSearchParams()
  if (params.start) q.set('start', params.start)
  if (params.end) q.set('end', params.end)
  if (params.status) q.set('status', params.status)
  q.set('export', '1')
  await _downloadCsv(`/realpay/collections/debit-tracker/?${q.toString()}`,
    `realpay_debit_tracker_${params.start || 'all'}_${params.end || 'all'}.csv`)
}

/** Download the collections dashboard export as CSV (audit-logged server-side). */
export async function downloadRealPayCollectionsExport(params: {
  start?: string; end?: string
}): Promise<void> {
  const q = new URLSearchParams()
  if (params.start) q.set('start', params.start)
  if (params.end) q.set('end', params.end)
  q.set('export', '1')
  await _downloadCsv(`/realpay/collections/dashboard/?${q.toString()}`,
    `realpay_collections_${params.start || 'all'}_${params.end || 'all'}.csv`)
}

// ─── Broker Commission register (Rose Mokgware / CFO 2026-09-08) ────────────

export interface BrokerRow {
  id: string; name: string; short_name: string; is_active: boolean; notes: string
  aliases: string[]
  graphite_policies: number; live_policies: number; annual_premium: number
  added_rows: number
}
export interface UnclaimedAgency {
  agency_id: string; agency: string; policies: number
  live_policies: number; annual_premium: number
}
export interface BrokerDuplicate {
  a: { id: string; name: string }; b: { id: string; name: string }; shared_key: string
}
export interface BrokerListResp {
  brokers: BrokerRow[]; graphite_live: boolean; unclaimed_agencies: UnclaimedAgency[]
  possible_duplicates: BrokerDuplicate[]
}
export interface BrokerClientRow {
  id?: string
  policy_number: string; insured_name: string
  premium: number; annual_premium: number
  policy_active: boolean | null; term_start_date: string
  source: string; period_label?: string
  motor_premium?: number; motor_commission?: number
  non_motor_premium?: number; non_motor_commission?: number
  commission_payable?: number; vat?: number; amount_received?: number
  status: string; status_label: string
  collected_amount: number; last_attempt: string; status_reason: string
  /** C3d — stored on rows a person added; absent on Graphite-only rows. */
  graphite_verified?: boolean
}
export interface BrokerDetailResp {
  broker: { id: string; name: string; short_name: string; notes: string
            is_active: boolean; aliases: string[] }
  rows: BrokerClientRow[]; count: number; tally: Record<string, number>
  status_live: boolean; graphite_live: boolean; period: string
}
export interface BrokerUploadResp {
  matched: { sheet: string; broker: string; rows: number }[]
  unmatched: { sheet: string; rows: number }[]
  sheets_without_a_table: string[]
  /** C3b — rows NOT imported because another broker already holds the policy. */
  refused: { sheet: string; policy_number: string; held_by: string }[]
  rows_written: number; committed: boolean
}

// ─── Broker commission summary (C7) — READ ONLY, preview ───────────────────
// `commission_available` is false on a row when the summary deliberately
// refuses to state a figure (no rates configured, or no motor/non-motor split
// set for that broker). The money fields are ABSENT on those rows rather than
// zero, so a caller cannot accidentally render "P0.00" for "we do not know".
export interface BrokerCommissionRow {
  broker_id: string
  broker: string
  collected_gross: number
  collected_count: number
  previous_collected: number
  withholding_tax: boolean
  motor_collected: number
  non_motor_collected: number
  commission_available: boolean
  blocked_reason: string
  net_premium?: number
  motor_net?: number
  non_motor_net?: number
  commission_excl_vat?: number
  wht?: number
  vat?: number
  current_payable?: number
  previous_payable?: number
  /** 'closed' = last month's payable as captured at its Close month. */
  previous_payable_source?: 'closed' | 'live'
  growth_pct?: number | null
  /** C7 — manual, Full Access role only. */
  compliance: string
}
export interface BrokerCommissionSummary {
  period: string
  previous_period: string
  preview: boolean
  signed_off: boolean
  preview_note: string
  rates_configured: boolean
  month_closed: boolean
  closed_at: string | null
  rates: {
    motor_pct: number; non_motor_pct: number; vat_pct: number
    admin_pct: number; wht_pct: number; effective_from: string
  } | null
  feed: { reporting: boolean; resolved: number; awaiting: number
          window_rows: number; reason: string }
  figures_complete: boolean
  rows: BrokerCommissionRow[]
  blocked: { broker: string; reason: string }[]
  unmapped_agencies: string[]
  totals: {
    collected_gross: number; commission_excl_vat: number; wht: number
    motor_collected: number; non_motor_collected: number
    vat: number; current_payable: number
    brokers_with_commission: number; brokers_blocked: number
  }
}
export async function getBrokerCommissionSummary(period?: string,
): Promise<BrokerCommissionSummary> {
  const qs = period ? `?period=${encodeURIComponent(period)}` : ''
  return apiFetch<BrokerCommissionSummary>(`/commissions/brokers/summary/${qs}`)
}

export async function listBrokers(): Promise<BrokerListResp> {
  return apiFetch<BrokerListResp>('/commissions/brokers/')
}
export async function getBroker(id: string, opts: { activeOnly?: boolean; period?: string } = {},
): Promise<BrokerDetailResp> {
  const q = new URLSearchParams()
  if (opts.activeOnly) q.set('active_only', '1')
  if (opts.period) q.set('period', opts.period)
  const qs = q.toString()
  return apiFetch<BrokerDetailResp>(`/commissions/brokers/${id}/${qs ? '?' + qs : ''}`)
}
export async function syncBrokers(): Promise<{ configured: boolean; created: number
                                               aliases: number; skipped_direct: number }> {
  return apiFetch('/commissions/brokers/sync/', { method: 'POST' })
}
export async function absorbBroker(keeperId: string, otherId: string) {
  return apiFetch<{ kept: string; absorbed: string; aliases_moved: number; rows_moved: number }>(
    `/commissions/brokers/${keeperId}/absorb/`,
    { method: 'POST', body: JSON.stringify({ other_id: otherId }) })
}
export async function addBrokerPolicy(id: string, body: Record<string, unknown>) {
  return apiFetch<{ id: string; created: boolean; policy_number: string
                    graphite_verified: boolean }>(
    `/commissions/brokers/${id}/policies/`,
    { method: 'POST', body: JSON.stringify(body) })
}
export interface BrokerPolicyLookup {
  reachable: boolean
  found: boolean
  policy: { policy_number: string; insured_name: string; agency: string
            premium: string; annual_premium: string } | null
  held_by: { id: string; name: string } | null
  detail?: string
}
/** C3 — check a policy in Graphite BEFORE adding it to a broker's register. */
export async function lookupBrokerPolicy(policyNumber: string) {
  return apiFetch<BrokerPolicyLookup>(
    `/commissions/brokers/policy-lookup/?policy_number=${encodeURIComponent(policyNumber)}`)
}
export async function deleteBrokerPolicy(id: string, policyId: string) {
  return apiFetch<{ deleted: boolean; policy_number: string }>(
    `/commissions/brokers/${id}/policies/${policyId}/`, { method: 'DELETE' })
}
/** Upload a multi-tab broker workbook. `preview` parses without writing. */
export async function uploadBrokerWorkbook(
  file: File, opts: { period?: string; preview?: boolean } = {},
): Promise<BrokerUploadResp> {
  const fd = new FormData()
  fd.append('file', file)
  if (opts.period) fd.append('period_label', opts.period)
  if (opts.preview) fd.append('preview', '1')
  const res = await apiFetchRaw(`${API_BASE}/commissions/brokers/upload/`,
                                { method: 'POST', body: fd })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error((data as { detail?: string })?.detail || `Upload failed (${res.status})`)
  return data as BrokerUploadResp
}

// ─── C4 status by broker, C5 close month, C7 compliance (19-Sep-2026) ───────
export interface BrokerStatusRow {
  policy_number: string; source: string
  /** SUCCESSFUL · FAILED · ERROR · PROCESSING · NO RESULT · CANCELLED ·
   *  NOT FOUND · EFT SUCCESS · UNKNOWN */
  status: string; raw_status: string; last_attempt: string
  amount: string; eft_amount: string | null; previous_status: string
}
export interface BrokerStatusResp {
  broker: { id: string; name: string }
  period: string; previous_period: string; refused: false
  rows: BrokerStatusRow[]; count: number; tally: Record<string, number>
}
/** C4 — throws with the server's reason when it REFUSES (dead DOM/COM feed). */
export async function getBrokerStatus(id: string, period: string) {
  return apiFetch<BrokerStatusResp>(
    `/commissions/brokers/${id}/status/?period=${encodeURIComponent(period)}`)
}
export interface BrokerCloseResp {
  period: string; already_closed: boolean; brokers_closed: number
  statuses_recorded: number; late_resolved: number; payable_changes: number
}
/** C5 — Finance's Close month (Full Access role only, server-enforced). */
export async function closeBrokerMonth(period: string) {
  return apiFetch<BrokerCloseResp>('/commissions/brokers/close-month/',
    { method: 'POST', body: JSON.stringify({ period }) })
}
/** C7 — set the manual Compliance field (Full Access role only). */
export async function setBrokerCompliance(id: string, period: string, compliance: string) {
  return apiFetch<{ broker_id: string; period: string; compliance: string }>(
    `/commissions/brokers/${id}/compliance/`,
    { method: 'POST', body: JSON.stringify({ period, compliance }) })
}

// ─── RealPay Collections Analytics (CFO directive 2026-06-05) ───────────────

export interface RealPayMonthlyPoint {
  month: string; label: string; collected: string
  paying_clients: number; attempts: number
  successful: number; failed: number; resolved: number; success_rate: number
  partial: boolean
}
export interface RealPayAnalytics {
  coverage: { months: string[]; min: string | null; max: string | null; partial_latest_month: string | null; note: string }
  monthly: RealPayMonthlyPoint[]
  by_status: Record<string, number>
  by_merchant: { merchant: string; collected: string; attempts: number; paying_clients: number }[]
  by_bank: { bank: string; collected: string; attempts: number }[]
  by_grouping: { key: string; label: string; collected: string; collected_lines: number }[]
  totals: { collected: string; attempts: number; paying_clients: number; successful: number; resolved: number; success_rate: number }
  fy_summary: { fy: string; collected: string; months: number; attempts: number; paying_peak: number }[]
  undated_rows: number
  notes: string[]
  charges: { available: boolean; note: string }
  data_present: boolean
}
export async function getRealPayCollectionsAnalytics(params: {
  start?: string; end?: string
}): Promise<RealPayAnalytics> {
  const q = new URLSearchParams()
  if (params.start) q.set('start', params.start)
  if (params.end) q.set('end', params.end)
  return apiFetch<RealPayAnalytics>(`/realpay/collections/analytics/?${q.toString()}`)
}

// ─── Admin: API Keys console (CFO-only) — CFO directive 2026-06-08 ──────────
export interface ApiKeyRow {
  id: string
  label: string
  key_prefix: string
  allowed_scopes: string[]
  is_active: boolean
  last_used_at: string | null
  last_used_ip: string | null
  created_at: string
  service_user__username?: string
  service_user__email?: string
  created_by__username?: string
}
export interface ApiKeyCreated {
  id: string
  label: string
  service_user: string
  allowed_scopes: string[]
  key_prefix: string
  key: string            // plaintext — shown ONCE
  created_at: string
  note: string
}
export async function listApiKeys(): Promise<{ keys: ApiKeyRow[]; count: number }> {
  return apiFetch<{ keys: ApiKeyRow[]; count: number }>('/admin/api-keys/')
}
export async function createApiKey(input: {
  label: string; allowed_scopes: string[]; service_user?: string
}): Promise<ApiKeyCreated> {
  return apiFetch<ApiKeyCreated>('/admin/api-keys/', {
    method: 'POST',
    body: JSON.stringify(input),
  })
}
export async function revokeApiKey(id: string): Promise<void> {
  await apiFetch(`/admin/api-keys/${id}/`, { method: 'DELETE' })
}

// ─── CFO Secrets Vault (encrypted credential store) ────────────────────────
export interface VaultSecretRow {
  id: string
  name: string
  category: string
  category_display: string
  username: string
  url: string
  notes: string
  has_secret: boolean
  created_by?: string | null
  created_at?: string | null
  updated_at?: string | null
  last_revealed_at?: string | null
  last_revealed_by?: string | null
}
export interface VaultSecretInput {
  name?: string
  category?: string
  username?: string
  url?: string
  notes?: string
  secret?: string
}
export async function listVaultSecrets(): Promise<{ secrets: VaultSecretRow[]; count: number }> {
  return apiFetch<{ secrets: VaultSecretRow[]; count: number }>('/admin/vault/')
}
export async function createVaultSecret(input: VaultSecretInput): Promise<VaultSecretRow> {
  return apiFetch<VaultSecretRow>('/admin/vault/', { method: 'POST', body: JSON.stringify(input) })
}
export async function updateVaultSecret(id: string, input: VaultSecretInput): Promise<VaultSecretRow> {
  return apiFetch<VaultSecretRow>(`/admin/vault/${id}/`, { method: 'PATCH', body: JSON.stringify(input) })
}
export async function deleteVaultSecret(id: string): Promise<void> {
  await apiFetch(`/admin/vault/${id}/`, { method: 'DELETE' })
}
export async function revealVaultSecret(id: string): Promise<{ id: string; name: string; username: string; secret: string }> {
  return apiFetch<{ id: string; name: string; username: string; secret: string }>(`/admin/vault/${id}/reveal/`, { method: 'POST' })
}

// ─── Payslip Components (payroll → GL account mapping) ─────────────────────
export interface PayslipComponentRow {
  id: string
  code: string
  name: string
  kind: string
  kind_display: string
  sort_order: number
  is_taxable: boolean
  is_active: boolean
  posting_account_code: string
  posting_account_name?: string   // server-resolved (bug c369818a) — pagination-immune
}
export async function listPayslipComponents(): Promise<PayslipComponentRow[]> {
  const r = await apiFetch<unknown>('/payslip-components/?page_size=200')
  if (Array.isArray(r)) return r as PayslipComponentRow[]
  return ((r as { results?: PayslipComponentRow[] })?.results) ?? []
}
export async function updatePayslipComponent(id: string, patch: Partial<PayslipComponentRow>): Promise<PayslipComponentRow> {
  return apiFetch<PayslipComponentRow>(`/payslip-components/${id}/`, { method: 'PATCH', body: JSON.stringify(patch) })
}

// ─── Team chat (polled). "/task @user <desc>" → OmniTask on assignee dashboard ─
export interface ChatMsg {
  id: string
  sender: string
  sender_name: string
  body: string
  is_task: boolean
  task_id: string | null
  created_at: string
  edited?: boolean
  deleted?: boolean
}
export interface ChatPeer { username: string; full_name: string }
/** `dm` = the OTHER person's username for a private 1:1 thread; omit for the shared team room. */
export async function listChatMessages(since?: string, dm?: string): Promise<{ messages: ChatMsg[]; server_time: string; peer?: ChatPeer | null }> {
  const qs = new URLSearchParams()
  if (since) qs.set('since', since)
  if (dm) qs.set('dm', dm)
  const q = qs.toString()
  return apiFetch<{ messages: ChatMsg[]; server_time: string; peer?: ChatPeer | null }>(
    `/chat/messages/${q ? `?${q}` : ''}`)
}
export async function postChatMessage(body: string, dm?: string): Promise<ChatMsg> {
  return apiFetch<ChatMsg>('/chat/messages/', {
    method: 'POST',
    body: JSON.stringify(dm ? { body, dm } : { body }),
  })
}

/** Ask Omni — a private Q&A with Omni's assistant (CFO 2026-08-31). Reuses the
 *  existing Aria endpoint (DeepSeek, PII-firewalled, grounded on the SOP bank +
 *  live figures). Answers land only for the person who asked — never the team
 *  room — so "how do I apply leave?" stops cluttering team chat. Never throws a
 *  useful outage as a crash: returns {ok:false, reason} the widget can show. */
export async function askOmni(
  message: string, history: { role: 'user' | 'assistant'; content: string }[] = [],
): Promise<{ ok: boolean; reply?: string; reason?: string }> {
  try {
    const r = await apiFetch<{ ok: boolean; reply?: string; reason?: string }>(
      '/ai/aria/chat/', {
        method: 'POST',
        body: JSON.stringify({ message, history: history.slice(-6) }),
      })
    return r
  } catch (e) {
    return { ok: false, reason: e instanceof Error ? e.message : 'Ask Omni is unavailable right now.' }
  }
}

// ── Time Doctor workforce report (CFO 2026-06-16) ──────────────────────────
export interface TimeDoctorMember {
  user_id: string
  name: string
  email: string
  role: string
  timezone: string
  last_seen: string | null
  last_track: string | null
  hours_tracked: number
  productive_pct: number | null
  unproductive_pct: number | null
  tracked_today: boolean
}
export interface TimeDoctorTotals {
  as_of: string
  user_count: number
  active_users: number
  total_hours: number
  productive_pct: number | null
  unproductive_pct: number | null
  project_count: number
  task_count: number
}
export interface TimeDoctorDaily {
  as_of?: string
  totals: TimeDoctorTotals | null
  members: TimeDoctorMember[]
  emailed?: boolean
  detail?: string
}
export async function getTimeDoctorDaily(date?: string): Promise<TimeDoctorDaily> {
  const q = date ? `?date=${encodeURIComponent(date)}` : ''
  return apiFetch<TimeDoctorDaily>(`/timedoctor/daily/${q}`)
}
export async function getTimeDoctorHistory(days = 30): Promise<{ days: number; series: Array<{ as_of: string; total_hours: number | null; active_users: number | null; productive_pct: number | null }> }> {
  return apiFetch(`/timedoctor/history/?days=${days}`)
}

// ── Time Doctor reconciliation (CFO 2026-06-16) ────────────────────────────
export interface TDReconRow {
  employee: string; department: string; job_title: string
  tracked_hours: number; productive_hours: number; expected_hours: number; utilization_pct: number | null
  idle_pct: number | null; unproductive_pct: number | null
  gross_paid: number; cost_per_tracked_hour: number | null
  is_tracking: boolean; payroll_months: number
}
export interface TDExceptionRow {
  employee: string; department: string
  tracked_hours: number; expected_hours: number | null
  utilization_pct: number | null; idle_pct: number | null; unproductive_pct: number | null
}
export interface TDExceptions {
  thresholds: { util_critical: number; util_warn: number; idle_pct: number; unproductive_pct: number }
  groups: {
    not_tracking: TDExceptionRow[]; critical_low_hours: TDExceptionRow[]
    low_hours_warning: TDExceptionRow[]; high_idle: TDExceptionRow[]; high_unproductive: TDExceptionRow[]
  }
  counts: Record<string, number>
}
export interface TDReconTotals {
  months: number; snapshot_days: number; td_users_seen: number
  employees: number; employees_tracking: number; idle_employees: number
  total_tracked_hours: number; total_productive_hours: number; total_gross_paid: number
  avg_utilization_pct: number | null; matched: number
}
export interface TDDeptRow {
  department: string; headcount: number; tracking: number; with_payroll: number
  tracked_hours: number; productive_hours: number; gross_paid: number
  cost_per_tracked_hour: number | null; utilization_pct: number | null
}
export interface TDCoverage {
  trackers: number; trackers_without_payroll: number; paid_not_tracking: number; pct_costed: number | null
}
export interface TDMonth {
  month: string; tracked_hours: number; productive_hours: number; productive_pct: number | null; active_users: number
}
export interface TDReconciliation {
  totals: TDReconTotals
  rows: TDReconRow[]
  exceptions?: TDExceptions
  by_department?: TDDeptRow[]
  coverage?: TDCoverage
  monthly?: TDMonth[]
  insight: { ok: boolean; text: string; reason?: string; engine?: string } | null
}
export async function getTimeDoctorReconciliation(months = 3, insight = false): Promise<TDReconciliation> {
  return apiFetch(`/timedoctor/reconciliation/?months=${months}${insight ? '&insight=1' : ''}`)
}
// Workforce Daily Brief on/off switch (CFO 2026-07-14). Toggle server-gated to
// CFO / Arun / Arjun; can_toggle tells the UI whether to show the control.
export type WorkforceBriefToggle = { enabled: boolean; can_toggle: boolean }
export async function getWorkforceBriefToggle(): Promise<WorkforceBriefToggle> {
  return apiFetch<WorkforceBriefToggle>('/timedoctor/brief-toggle/')
}
export async function setWorkforceBriefToggle(enabled: boolean): Promise<WorkforceBriefToggle> {
  return apiFetch<WorkforceBriefToggle>('/timedoctor/brief-toggle/', {
    method: 'POST', body: JSON.stringify({ enabled }),
  })
}
// CFO Excuses feed — read-only view over staff explanations for missed hours.
export interface ExcuseRow {
  id: string; profile_id: string; employee: string; work_date: string;
  reason: string; explanation: string; required: string; tracked: string;
  shortfall_hours: string; would_be_leave_hours: string; status: string;
  flagged: boolean; flag_terms: string[]; responded_at: string | null;
}
export interface ExcusesFeed {
  window_days: number; enforcement_active: boolean; strict_from: string;
  total: number; flagged_count: number; by_reason: Record<string, number>;
  top_people: Array<[string, number]>; top_words: Array<[string, number]>;
  items: ExcuseRow[];
}
export async function getExcusesFeed(days = 30, person?: string): Promise<ExcusesFeed> {
  const q = new URLSearchParams({ days: String(days) })
  if (person) q.set('person', person)
  return apiFetch<ExcusesFeed>(`/timedoctor/excuses/?${q.toString()}`)
}
// Employee self-service: the days I'm short on + submitting a justification.
export type MyBriefDay = { work_date: string; required: string; tracked: string; shortfall: string }
export type MyBrief = { employee: string | null; days: MyBriefDay[] }
export async function getMyBrief(): Promise<MyBrief> {
  return apiFetch<MyBrief>('/timedoctor/my-brief/')
}
export type JustifyPayload = {
  work_date: string; reason: string; justification?: string; meeting_minutes?: number; location?: string
  client_name?: string; client_reason?: string; client_outcome?: string; amount?: number
}
export async function submitJustification(p: JustifyPayload): Promise<{ work_date: string; status: string; pending_review?: boolean }> {
  return apiFetch('/timedoctor/justify/', { method: 'POST', body: JSON.stringify(p) })
}
// Plan an upcoming out-of-office day in advance (CFO 2026-07-15).
export type PlanAbsencePayload = {
  work_date: string; reason: string; justification?: string; location?: string; client_name?: string; client_reason?: string
}
export async function planAbsence(p: PlanAbsencePayload): Promise<{ work_date: string; status: string; planned: boolean }> {
  return apiFetch('/timedoctor/plan-absence/', { method: 'POST', body: JSON.stringify(p) })
}
// Manager review of self-reported explanations (status "explained").
export type PendingJustification = {
  id: string; employee: string; work_date: string; required: string; tracked: string
  justified_hours: string; reason: string; justification: string; location: string
  meeting_minutes: number | null; responded_at: string | null
}
export async function getPendingJustifications(): Promise<{ items: PendingJustification[]; aria_summary?: string }> {
  return apiFetch('/timedoctor/justifications/pending/')
}
export async function reviewJustification(id: string, decision: 'approve' | 'reject', note?: string): Promise<{ id: string; status: string }> {
  return apiFetch('/timedoctor/justifications/review/', {
    method: 'POST', body: JSON.stringify({ id, decision, note }),
  })
}
// Punctuality dashboard — frequent latecomers over the last ~month (manager/HR).
export type Latecomer = { name: string; late_days: number }
export type Latecomers = { configured: boolean; window_days: number; count: number; total_late_days: number; latecomers: Latecomer[] }
export async function getTimeDoctorLatecomers(): Promise<Latecomers> {
  return apiFetch<Latecomers>('/timedoctor/latecomers/')
}
// Who-tracks setup (CFO / Arun / Arjun): confirm the TD↔staff match + click track/don't-track.
export type TrackingRow = {
  employee_id: string; name: string; department: string; job_title: string
  expected: boolean; source: 'directive' | 'auto'
  matched_td: string | null; td_user_id: string | null; confirmed: boolean
}
export type UnmatchedTd = { td_user_id: string; td_name: string }
export type TrackingSetup = { configured: boolean; items: TrackingRow[]; unmatched_td: UnmatchedTd[]; can_edit: boolean; can_toggle?: boolean }
export async function getTrackingSetup(): Promise<TrackingSetup> {
  return apiFetch<TrackingSetup>('/timedoctor/tracking-setup/')
}
export async function setTracking(employee_id: string, expected_to_track: boolean): Promise<{ employee_id: string; expected_to_track: boolean }> {
  return apiFetch('/timedoctor/set-tracking/', { method: 'POST', body: JSON.stringify({ employee_id, expected_to_track }) })
}
export async function confirmTdMatch(employee_id: string | null, td_user_id: string, td_name?: string): Promise<{ td_user_id: string; employee_id: string | null; confirmed: boolean }> {
  return apiFetch('/timedoctor/confirm-match/', { method: 'POST', body: JSON.stringify({ employee_id, td_user_id, td_name }) })
}
// Gamified leaderboard for the Time Doctor dashboard (manager/HR only).
export type LbRow = { name: string; hours: number }
export type Leaderboard = {
  period: string; configured: boolean; count: number; total_hours: number
  winner: LbRow | null; runner_up: LbRow | null; lowest: LbRow | null; top: LbRow[]
}
export async function getTimeDoctorLeaderboard(period = 'week'): Promise<Leaderboard> {
  return apiFetch<Leaderboard>(`/timedoctor/leaderboard/?period=${period}`)
}
export async function editChatMessage(id: string, body: string): Promise<ChatMsg> {
  return apiFetch<ChatMsg>(`/chat/messages/${id}/`, { method: 'PATCH', body: JSON.stringify({ body }) })
}
export async function deleteChatMessage(id: string): Promise<ChatMsg> {
  return apiFetch<ChatMsg>(`/chat/messages/${id}/`, { method: 'DELETE' })
}

// ─── Group Health Quotations (CFO/Tlamelo 2026-06-17) ───────────────────────

export interface HQMember {
  id?: string
  full_name: string
  member_type: 'main' | 'adult_dep' | 'child_dep'
  member_type_label?: string
  gender: string
  date_of_birth: string | null
  age?: number
  age_band?: string
  tier: string
  tier_label?: string
  premium_excl?: string
  vat?: string
  premium_incl?: string
}
export interface HQTier {
  tier: string; tier_label: string; lives: number
  subtotal_excl: string; vat: string; total_incl: string
  // Per-tier discount (Tlamelo 2026-06-25). subtotal_excl/vat/total_incl stay
  // RACK; these carry the discounted (net) figures + the discount applied.
  discount_pct?: string; discount_gate?: string
  gross_excl?: string; discount_excl?: string
  net_excl?: string; net_vat?: string; net_total_incl?: string
  members: HQMember[]
}
export interface HealthQuote {
  id: string; ref: string; client_name: string; client_address: string
  contact_name: string; contact_email: string; contact_phone: string; vat_no: string
  benefit_start: string | null; billing_period: string; underwriting: string
  status: 'draft' | 'submitted' | 'approved' | 'invoiced' | 'rejected'; status_label: string
  subtotal_excl: string; vat: string; total_incl: string; lives: number
  // gross_excl/discount_excl = rack + discount; subtotal_excl/vat/total_incl are NET.
  gross_excl?: string; discount_excl?: string
  tier_discounts?: Record<string, string>
  invoice_no: string; invoice_date: string | null
  created_by: string | null; approved_by: string | null; approved_at: string | null
  created_at: string; updated_at: string
  review_stage?: string; review_stage_label?: string
  submitted_by?: string | null; review1_by?: string | null; review2_by?: string | null
  reject_reason?: string
  company?: { bank: string; account_name: string; account_no: string; branch_code: string; swift: string; vat_reg: string }
  tiers?: HQTier[]; members?: HQMember[]
}
export interface HQRateCard {
  tiers: { value: string; label: string }[]
  member_types: { value: string; label: string }[]
  age_bands: string[]; vat_rate: string
  tier_discount_defaults?: Record<string, string>
  tier_discount_gates?: Record<string, string>
}
type HQBody = Partial<Omit<HealthQuote, 'members'>> & { members?: Partial<HQMember>[] }

export async function getHealthQuotes(params?: Record<string, string>): Promise<{ count: number; quotes: HealthQuote[] }> {
  const qs = new URLSearchParams(params).toString()
  return apiFetch(`/health/quotes/${qs ? `?${qs}` : ''}`)
}
export async function getHealthQuote(id: string): Promise<HealthQuote> {
  return apiFetch(`/health/quotes/${id}/`)
}
export async function createHealthQuote(body: HQBody): Promise<HealthQuote> {
  return apiFetch('/health/quotes/', { method: 'POST', body: JSON.stringify(body) })
}
export async function updateHealthQuote(id: string, body: HQBody): Promise<HealthQuote> {
  return apiFetch(`/health/quotes/${id}/`, { method: 'PATCH', body: JSON.stringify(body) })
}
export async function submitHealthQuote(id: string): Promise<HealthQuote> {
  return apiFetch(`/health/quotes/${id}/submit/`, { method: 'POST' })
}
export async function reviewHealthQuote(id: string): Promise<HealthQuote> {
  return apiFetch(`/health/quotes/${id}/review/`, { method: 'POST' })
}
export async function rejectHealthQuote(id: string, reason: string): Promise<HealthQuote> {
  return apiFetch(`/health/quotes/${id}/reject/`, { method: 'POST', body: JSON.stringify({ reason }) })
}
export async function approveHealthQuote(id: string): Promise<HealthQuote> {
  return apiFetch(`/health/quotes/${id}/approve/`, { method: 'POST' })
}
export async function invoiceHealthQuote(id: string): Promise<HealthQuote> {
  return apiFetch(`/health/quotes/${id}/invoice/`, { method: 'POST' })
}
export async function deleteHealthQuote(id: string): Promise<void> {
  await apiFetchBinary(`${API_BASE}/health/quotes/${id}/`, { method: 'DELETE', headers: {} })
}
export async function getHealthQuoteRateCard(): Promise<HQRateCard> {
  return apiFetch('/health/quotes/rate-card/')
}
export interface HQDashboard {
  funnel: Record<string, number>
  active_quotes: number; active_lives: number
  monthly_premium_incl: string; gwp_annualised_incl: string
  tiers: { tier: string; tier_label: string; lives: number; monthly_incl: string; gwp_annual: string }[]
  this_month: { label: string; invoiced_count: number; invoiced_total: string }
  renewals_due: number
}
export async function getHealthQuoteDashboard(): Promise<HQDashboard> {
  return apiFetch('/health/quotes/dashboard/')
}
export interface HealthSummaryMonth {
  period_year: number; period_month: number; label: string
  uploads: number; rows: number; lives: number; gross: string; paid: string
}
export interface HealthSummary { kinds: { revenue: HealthSummaryMonth[]; claims: HealthSummaryMonth[]; treaty: HealthSummaryMonth[] } }
export async function getHealthSummary(): Promise<HealthSummary> {
  return apiFetch('/health/summary/')
}

/**
 * The live Alpha Direct Health dashboard feed (/health/dashboard/).
 * Every money figure is a number in BWP; `null` means "no data", never zero.
 * gwpIncl is VAT-inclusive as stored on the bordereaux; gwpExcl is derived.
 */
export interface HealthDashMonth {
  month: string; label: string; fy: number
  gwpExcl: number | null; gwpIncl: number | null
  claims: number | null; lossRatioPct: number | null
}
export interface HealthDashFyTotal {
  fy: number; label: string
  gwpExcl: number | null; gwpIncl: number | null; claims: number | null
  isCurrent: boolean
}
export interface HealthDashGroup {
  group: string; lives: number; in_month: number | null
  main_reason: string | null; active_lives: number
}
export interface HealthDashboard {
  asOf: string
  lastUpdated: string
  fy: { label: string; start: string; end: string }
  kpi: {
    gwpYtdIncl: number | null; gwpYtdExcl: number | null
    gwpMonthIncl: number | null; gwpMonthExcl: number | null
    gwpMonthLabel: string | null; gwpMonMoMPct: number | null
    annualisedIncl: number | null; objective: number | null
    activeLives: number | null; policyholders: number | null; dependants: number | null
    livesTarget: number
    lossRatioYtdPct: number | null; lossRatioMonthPct: number | null
  }
  itd: {
    gwpIncl: number | null; gwpExcl: number | null
    claims: number | null; lossRatioPct: number | null; firstMonth: string | null
  }
  monthly: HealthDashMonth[]
  fyTotals: HealthDashFyTotal[]
  cancellations: {
    reportMonth: string; available: boolean
    totalLives: number | null; inMonth: number | null
    reasonsAvailable: boolean
    reasons: { reason: string; total: number; inMonth: number | null }[]
    byGroup: HealthDashGroup[]
    note: string
  }
  pipeline: { draft: number; inReview: number; approved: number; invoiced: number; renewals: number }
  gap: {
    registerLives: number | null; quoteBookLives: number
    difference: number | null; reconciled: boolean; note: string
  }
  sources: {
    premium: string; claims: string; lives: string; pipeline: string
    vatRate: number; registerGroups: number | null
    registerUnavailableReason: string | null
    registerLagSeconds: number | null
  }
}
/** Build Brief 1: the Instant Insurance book, read live from the Graphite replica. */
export interface InstantProduct {
  product: string; active: number; paying: number
  paying_rate_pct: number | null
  expired_but_active: number; no_expiry_date: number; payment_cancelled: number
}
export interface InstantBook {
  book: string; active: number; paying: number
  paying_rate_pct: number | null
  products: InstantProduct[]
}
export interface InstantSummary {
  available: boolean
  reason?: string
  asof?: string
  paying_window?: { from: string; to: string; months: number }
  books?: InstantBook[]
  exceptions?: { expired_but_active: number; no_expiry_date: number; payment_cancelled: number }
  rules?: Record<string, string>
  caveats?: string[]
}
export async function getInstantInsuranceSummary(asof?: string): Promise<InstantSummary> {
  return apiFetch('/integrations/instant-insurance-summary/' + (asof ? `?asof=${encodeURIComponent(asof)}` : ''))
}

/** The RealPay-vs-ledger check. Same calculation the daily email uses. */
export interface ReconMonth {
  month: string; feed: number; ledger: number; ledger_amount: number
  shortfall: number; shortfall_pct: number; estimated_value: number
  settling: boolean; breach: boolean
}
export interface ReconResult {
  available: boolean
  reason?: string
  asof?: string
  window_months?: number
  rows?: ReconMonth[]
  breaches?: ReconMonth[]
  worst?: ReconMonth | null
  verdict?: 'ok' | 'break'
  thresholds?: { min_rows: number; min_pct: number }
  note?: string
  summary?: string
  /** 'the daily check' when served from this morning's saved answer. */
  served_from?: string
  computed_at?: string | null
  live?: boolean
  cached?: boolean
}
export async function getRealpayLedgerReconciliation(
  months = 6, live = false,
): Promise<ReconResult> {
  return apiFetch(
    `/integrations/realpay-ledger-reconciliation/?months=${months}${live ? '&live=1' : ''}`)
}

export async function getHealthDashboard(asof?: string): Promise<HealthDashboard> {
  return apiFetch('/health/dashboard/' + (asof ? `?asof=${encodeURIComponent(asof)}` : ''))
}
export interface HQParseResult {
  members: HQMember[]; count: number
  errors: { row: number; error: string }[]
  warnings: { row: number; warning: string }[]
  message: string
  /** The group plan the backend applied, and how many members it covered. */
  group_tier?: string
  group_tier_applied?: number
}
/** `groupTier` puts every member with no plan of their own onto one plan for the
 *  whole group — most employer groups are on a single plan, and requiring the
 *  Tier column on every row made a normal census price to zero (bug 03a2b875). */
export async function parseHealthQuoteMembers(file: File, groupTier = ''): Promise<HQParseResult> {
  const fd = new FormData(); fd.append('file', file)
  if (groupTier) fd.append('group_tier', groupTier)
  const res = await apiFetchBinary(`${API_BASE}/health/quotes/parse-members/`, { method: 'POST', headers: {}, body: fd })
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error((body as any).detail || `Parse failed (HTTP ${res.status})`)
  return body as HQParseResult
}
export async function downloadHealthQuotePdf(id: string, filename: string): Promise<void> {
  const res = await apiFetchBinary(`${API_BASE}/health/quotes/${id}/pdf/`, { headers: {} })
  if (!res.ok) throw new Error(`PDF download failed (HTTP ${res.status})`)
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url; a.download = filename; a.click()
  URL.revokeObjectURL(url)
}

export async function downloadHealthQuoteXlsx(id: string, filename: string): Promise<void> {
  const res = await apiFetchBinary(`${API_BASE}/health/quotes/${id}/xlsx/`, { headers: {} })
  if (!res.ok) throw new Error(`Excel download failed (HTTP ${res.status})`)
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url; a.download = filename; a.click()
  URL.revokeObjectURL(url)
}

// Consolidated all-tiers quote (Tlamelo 2026-06-30): Terms + one schedule per tier.
export async function downloadHealthQuoteConsolidatedXlsx(id: string, filename: string): Promise<void> {
  const res = await apiFetchBinary(`${API_BASE}/health/quotes/${id}/consolidated-xlsx/`, { headers: {} })
  if (!res.ok) throw new Error(`Consolidated Excel download failed (HTTP ${res.status})`)
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url; a.download = filename; a.click()
  URL.revokeObjectURL(url)
}

// ── Equity / Cap Table ("Capital Story", OCF-aligned) ────────────────────────
export interface EquityCapitalStory {
  issuer: { legal_name: string; country: string; formation_date: string; uen: string; currency: string }
  kpis: { key: string; label: string; value: string; sub: string }[]
  instruments: { name: string; class_type: string; label: string; cash_usd: number; shares: number; price: number; status: string }[]
  instruments_total: { cash_usd: number; shares: number }
  timeline: { date: string; title: string; detail: string; tag: string }[]
  cap_table: { holder: string; shares: number; pct: number; klass: string; usd: number; note: string }[]
  cap_table_total: { shares: number; pct: number; usd: number }
  esop: {
    pool_size: number; granted: number; available: number; pct_of_capital: number; pct_utilized: number
    option_holders: number; fd_ownership_pct: number; fd_shares_basis: number
    tiers: { name: string; pct: number; detail: string }[]
    plan_terms: { k: string; v: string; note: string }[]
    grants: EquityGrantRow[]
    granted_total: { units: number; pct_pool: number; pct_fd: number }
  }
  scenario: { current_fd_shares: number; pre_money_usd: number; round_size_usd: number; esop_topup_pct: number }
  odoo_recon: { equity_account: string; note: string }
  sources: string[]
  ocf_aligned: boolean
  // Added by the live register (equity app):
  security_holder_count?: number
  current_share_price_usd?: number
  register_source?: 'live-db' | 'seed-constants'
}

export interface VestingSummary {
  has_schedule: boolean
  vested_units: number | null
  unvested_units: number | null
  pct_vested: number | null
  next_vest_date: string | null
  next_vest_units: number | null
}
export interface EquityGrantRow {
  grantee: string; units: number; pct_pool: number; pct_fd: number
  status?: string; worth_usd?: number; vesting?: VestingSummary
}

export async function getEquityCapitalStory(): Promise<EquityCapitalStory> {
  return apiFetch('/reports/equity/')
}

// ── My Equity (self-service, any logged-in holder) ──────────────────────────
export interface MyEquity {
  has_equity: boolean
  detail?: string
  holder?: string
  is_current?: boolean
  holdings?: { klass: string; shares: number; usd_invested: number; note: string }[]
  grants?: {
    units: number; status: string; grant_date: string | null; expiry_date: string | null
    exercise_price_usd: number | null; letter_ref: string; pct_pool: number; pct_fd: number
    worth_usd: number; vested_worth_usd: number | null; vesting: VestingSummary
  }[]
  current_share_price_usd?: number
  totals?: { option_units: number; option_worth_usd: number; shares: number; share_worth_usd: number }
  issuer?: { legal_name: string; country: string; uen: string }
  price_basis?: string
}
export async function getMyEquity(): Promise<MyEquity> {
  return apiFetch('/equity/my-equity/')
}

/** Download the one-page My Equity statement (auth header can't ride a plain GET). */
export async function openMyEquityStatement(): Promise<void> {
  const res = await apiFetchBinary(`${API_BASE}/equity/my-equity/statement.pdf`, { headers: {} })
  if (!res.ok) throw new Error(`Statement failed: ${res.status}`)
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'my-equity-statement.pdf'
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 30000)
}

// ── Register CRUD (Finance / EXCO) ──────────────────────────────────────────
export interface EquityStakeholder {
  id: number; name: string; kind: string; employee: number | null; employee_name?: string
  email: string; is_current: boolean; note: string
  holdings?: { id: number; stakeholder: number; klass: string; shares: number; usd_invested: number; note: string }[]
  grants?: { id: number; stakeholder: number; grantee?: string; units: number; grant_date: string | null
    expiry_date: string | null; exercise_price_usd: number | null; pct_pool: number; pct_fd: number
    status: string; letter_ref: string; note: string
    tranches?: { id: number; grant: number; vest_date: string; units: number; note: string }[] }[]
}
export async function listStakeholders(): Promise<EquityStakeholder[]> {
  return apiFetch('/equity/stakeholders/')
}
export async function saveStakeholder(body: Partial<EquityStakeholder>, id?: number) {
  return apiFetch(`/equity/stakeholders/${id ? id + '/' : ''}`, { method: id ? 'PATCH' : 'POST', body: JSON.stringify(body) })
}
export async function deleteStakeholder(id: number) {
  return apiFetch(`/equity/stakeholders/${id}/`, { method: 'DELETE' })
}
export async function saveHolding(body: any, id?: number) {
  return apiFetch(`/equity/holdings/${id ? id + '/' : ''}`, { method: id ? 'PATCH' : 'POST', body: JSON.stringify(body) })
}
export async function deleteHolding(id: number) {
  return apiFetch(`/equity/holdings/${id}/`, { method: 'DELETE' })
}
export async function saveGrant(body: any, id?: number) {
  return apiFetch(`/equity/grants/${id ? id + '/' : ''}`, { method: id ? 'PATCH' : 'POST', body: JSON.stringify(body) })
}
export async function deleteGrant(id: number) {
  return apiFetch(`/equity/grants/${id}/`, { method: 'DELETE' })
}
export async function previewVestingSchedule(grantId: number): Promise<{ tranches: { vest_date: string; units: number }[] }> {
  return apiFetch(`/equity/grants/${grantId}/generate-schedule/`, { method: 'POST', body: '{}' })
}
export async function applyVestingSchedule(grantId: number, tranches: { vest_date: string; units: number }[]) {
  return apiFetch(`/equity/grants/${grantId}/apply-schedule/`, { method: 'POST', body: JSON.stringify({ tranches }) })
}

// ── Alpha Rewards (Project Nexus) ──────────────────────────────────────────
export interface RewardsSummary {
  members_total: number; members_active: number; points_balance: number
  points_earned: number; points_redeemed: number; avg_driving_score: number | null
  programs_active: number; partners_total: number; tiers: Record<string, number>
}
export interface RewardMember {
  id: string; company: string | null; policy_number: string; customer_name: string
  tier: string; tier_display: string; points_balance: number; claims_free_months: number
  enrolled_at: string | null; is_active: boolean
}
export interface RewardProgram {
  id: string; code: string; name: string; description: string; is_active: boolean
  config: Record<string, unknown>
}
export interface RewardPartner {
  id: string; name: string; kind: string; kind_display: string; status: string; notes: string
}
export interface PointsTransaction {
  id: string; member: string; member_name: string; program: string | null
  program_name: string | null; partner: string | null; partner_name: string | null
  kind: string; kind_display: string; points: number; detail: string; occurred_at: string
}
export interface DrivingScore {
  id: string; member: string; member_name: string; period: string; vehicle_reg: string
  distance_km: string; idle_minutes: number; harsh_events: number; score: number
  points_awarded: number; source: string
}

export async function getRewardsSummary(): Promise<RewardsSummary> {
  return apiFetch('/rewards/summary/')
}
export async function getRewardMembers(): Promise<PaginatedResponse<RewardMember>> {
  return apiFetch('/reward-members/')
}
export async function getRewardPrograms(): Promise<PaginatedResponse<RewardProgram>> {
  return apiFetch('/reward-programs/')
}
export async function getRewardPartners(): Promise<PaginatedResponse<RewardPartner>> {
  return apiFetch('/reward-partners/')
}
export async function getPointsTransactions(): Promise<PaginatedResponse<PointsTransaction>> {
  return apiFetch('/points-transactions/')
}
export async function getDrivingScores(): Promise<PaginatedResponse<DrivingScore>> {
  return apiFetch('/driving-scores/')
}

// ── Alpha Thrive — finger-PPG wellness (2026-06-26) ─────────────────────────
// Wellness only, never diagnosis. The client sends DERIVED NUMBERS only
// (RR intervals in ms) — camera frames never leave the device.
export interface ThriveFactor {
  key: string; label: string; score: number; weight: number; note: string
}
export interface ThriveScore {
  score: number; band: string; factors: ThriveFactor[]; peer_avg: number
}
export interface ThriveVitals {
  restingHr: number | null; hrvSdnn: number | null; hrvRmssd: number | null
  hrvPnn50: number | null; respirationRate: number | null; stressBand: string
  beats: number; scanConfidence: number
}
export interface ThriveScanResult {
  vitals: ThriveVitals; alphaScore: ThriveScore; date: string
}
export interface ThriveAlphaScore {
  score: ThriveScore; tier: string; points: number; asOf: string | null; hasVitals: boolean
}
export interface ThriveTrend {
  points: { date: string; score: number }[]; slope: number; direction: string
}
export interface ThriveCoach {
  nudge: string; engine: string; buckets: Record<string, string>; scoreBand: string
}

export async function postHealthConsent(memberId: string, dataTypes: string[]): Promise<{ ok: boolean; consentId: string }> {
  return apiFetch('/rewards/health-consent/', {
    method: 'POST',
    body: JSON.stringify({ memberId, dataTypes, grantedAt: new Date().toISOString() }),
  })
}
export async function postThriveScan(payload: {
  memberId: string; rrIntervals: number[]; restingHr?: number
  respirationRate?: number; age?: number; gender?: string; date?: string
}): Promise<ThriveScanResult> {
  return apiFetch('/rewards/thrive/scan/', { method: 'POST', body: JSON.stringify(payload) })
}
export async function getThriveAlphaScore(member: string, age?: number): Promise<ThriveAlphaScore> {
  const q = new URLSearchParams({ member }); if (age) q.set('age', String(age))
  return apiFetch(`/rewards/thrive/alpha-score/?${q.toString()}`)
}
export async function getThriveRiskTrend(member: string): Promise<ThriveTrend> {
  return apiFetch(`/rewards/thrive/risk-trend/?member=${encodeURIComponent(member)}`)
}
export async function postThriveCoach(memberId: string, age?: number): Promise<ThriveCoach> {
  return apiFetch('/rewards/thrive/coach/', { method: 'POST', body: JSON.stringify({ memberId, age }) })
}

// ── Claims register (Graphite V2 mirror) — Bokani 2026-06-24 ────────────────
export interface ClaimsRegisterRow {
  id: string; graphite_id: number; claim_number: string; status: string;
  claim_type: string; product_name: string; policy_number: string;
  customer_name: string; is_company: boolean; claim_handler: string;
  registered_date: string | null; date_of_loss: string | null; damage_cause?: string;
  total_reserve: string; total_payment: string; balance: string;
}
export interface ClaimsBreakdown { key: string; count: number }
export interface ClaimsRegister {
  total: number; page: number; per_page: number;
  total_reserve: string; total_payment: string; total_balance: string;
  by_status: ClaimsBreakdown[]; by_type: ClaimsBreakdown[];
  by_product: ClaimsBreakdown[]; by_handler: ClaimsBreakdown[];
  results: ClaimsRegisterRow[]; last_synced: string | null; source: string;
}
export interface ClaimsRegisterFilters {
  status?: string; claim_type?: string; product?: string; handler?: string;
  search?: string; from?: string; to?: string; page?: number; per_page?: number;
}
function claimsQuery(f: ClaimsRegisterFilters): string {
  const q = new URLSearchParams()
  if (f.status) q.set('status', f.status)
  if (f.claim_type) q.set('claim_type', f.claim_type)
  if (f.product) q.set('product', f.product)
  if (f.handler) q.set('handler', f.handler)
  if (f.search) q.set('search', f.search)
  if (f.from) q.set('from', f.from)
  if (f.to) q.set('to', f.to)
  if (f.page) q.set('page', String(f.page))
  if (f.per_page) q.set('per_page', String(f.per_page))
  return q.toString()
}
export async function getClaimsRegister(f: ClaimsRegisterFilters = {}): Promise<ClaimsRegister> {
  return apiFetch<ClaimsRegister>(`/claims-register/?${claimsQuery(f)}`)
}
export interface ClaimsBridge {
  omni_total: string; less_omni_only: string; add_graphite_only: string
  add_differences_on_matched: string; expected_graphite: string; actual_graphite: string
  unexplained_variance: string; headline_gap: string; ties: boolean
}
export interface ClaimsDiffRow { claim_number: string; omni: string; graphite: string; difference: string }
export interface ClaimsReconciliation {
  date_from: string; date_to: string; as_of: string; omni_copy_synced_at: string | null
  graphite_available: boolean; note: string
  counts: {
    omni: number; graphite?: number; matched?: number; omni_only?: number
    graphite_only?: number; matched_reserve_difference?: number; matched_payment_difference?: number
  }
  reserve_bridge: ClaimsBridge | null; payment_bridge: ClaimsBridge | null
  restated: ClaimsDiffRow[]; payment_differences: ClaimsDiffRow[]
  graphite_only: { claim_number: string; reserve: string; paid: string }[]
  omni_only: { claim_number: string; reserve: string; reported_date: string | null }[]
}
export async function getClaimsReconciliation(from?: string, to?: string): Promise<ClaimsReconciliation> {
  const q = new URLSearchParams()
  if (from) q.set('from', from)
  if (to) q.set('to', to)
  return apiFetch<ClaimsReconciliation>(`/claims-register/reconciliation/?${q}`)
}
export async function downloadClaimsRegister(f: ClaimsRegisterFilters = {}): Promise<void> {
  const q = claimsQuery(f)
  const res = await apiFetchBinary(`${API_BASE}/claims-register/?${q}${q ? '&' : ''}export=1`)
  if (!res.ok) throw new Error(`Export failed (${res.status})`)
  const blob = await res.blob()
  const url = window.URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url; a.download = 'claims_register.xlsx'
  document.body.appendChild(a); a.click(); a.remove()
  window.URL.revokeObjectURL(url)
}

// ─── HR document vault (Dorothy 2026-06-26) ────────────────────────────────
export interface HRDocument {
  id: string
  title: string
  category: string
  category_label: string
  description: string
  is_personal: boolean
  employee_name: string
  filename: string
  size: number
  download_url: string
  uploaded_by: string | null
  created_at: string
}
export interface HRDocumentList {
  count: number
  documents: HRDocument[]
  categories: { value: string; label: string }[]
}
export async function getHRDocuments(category?: string): Promise<HRDocumentList> {
  const qs = category ? `?category=${encodeURIComponent(category)}` : ''
  return apiFetch<HRDocumentList>(`/hris/documents/${qs}`)
}
export async function uploadHRDocument(form: FormData): Promise<HRDocument> {
  return apiFetch<HRDocument>(`/hris/documents/`, { method: 'POST', body: form })
}
export async function downloadHRDocument(id: string, filename: string): Promise<void> {
  const res = await apiFetchBinary(`${API_BASE}/hris/documents/${id}/download/`, { headers: {} })
  if (!res.ok) throw new Error(`Download failed (HTTP ${res.status})`)
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url; a.download = filename; a.click()
  URL.revokeObjectURL(url)
}

// ─── Budget Library (CFO 2026-06-27 — a place to keep our budgets) ──────────
export interface BudgetPackFile {
  id: string; kind: string; kind_label: string; label: string
  filename: string; size: number; download_url: string
}
export interface BudgetPack {
  id: string; fy_label: string; company: string | null; company_name: string
  period_start: string | null; period_end: string | null
  status: string; status_label: string; scenario: string
  gwp_target: number | null; ebitda: number | null; pat: number | null; ebitda_margin: number | null
  notes: string; source: string; created_at: string; files: BudgetPackFile[]
}
export interface BudgetPackList { count: number; packs: BudgetPack[] }

export async function getBudgetPacks(): Promise<BudgetPackList> {
  return apiFetch<BudgetPackList>('/budgets/packs/')
}
export async function createBudgetPack(data: Record<string, unknown>): Promise<BudgetPack> {
  return apiFetch<BudgetPack>('/budgets/packs/', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
  })
}
export async function uploadBudgetPackFile(packId: string, form: FormData): Promise<BudgetPack> {
  return apiFetch<BudgetPack>(`/budgets/packs/${packId}/files/`, { method: 'POST', body: form })
}
export async function downloadBudgetPackFile(id: string, filename: string): Promise<void> {
  const res = await apiFetchBinary(`${API_BASE}/budgets/pack-files/${id}/download/`, { headers: {} })
  if (!res.ok) throw new Error(`Download failed (HTTP ${res.status})`)
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url; a.download = filename; a.click()
  URL.revokeObjectURL(url)
}

// ─── Strategic Plan Library (Finance 2026-08-04) ────────────────────────────
// The archive behind the 5-Year Plan Cockpit. Same shape as the Budget Library.
export interface PlanPackFile {
  id: string; kind: string; kind_label: string; label: string
  filename: string; size: number; download_url: string
}
export interface PlanAssumption {
  label: string; segment: string
  fy26: string; fy27: string; fy28: string; fy29: string; fy30: string
  source: string
}
export interface PlanPack {
  id: string; entity: string; label: string
  scenario_slug: 'base' | 'conservative' | 'aggressive'; scenario_label: string
  status: 'draft' | 'approved' | 'archived'; status_label: string
  prepared_by: string; prepared_date: string | null; department: string
  /** Empty on a draft — the page computes from the shared model instead. */
  base_figures: Record<string, number>
  figures_locked: boolean
  assumptions: PlanAssumption[]
  narrative: string; approved_at: string | null; created_at: string
  files: PlanPackFile[]
}
export interface PlanPackList { count: number; can_manage: boolean; packs: PlanPack[] }

export async function getPlanPacks(): Promise<PlanPackList> {
  return apiFetch<PlanPackList>('/plan-packs/')
}
export async function updatePlanPack(id: string, data: Record<string, unknown>): Promise<PlanPack> {
  return apiFetch<PlanPack>(`/plan-packs/${id}/`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
  })
}
export async function uploadPlanPackFile(packId: string, form: FormData): Promise<PlanPack> {
  return apiFetch<PlanPack>(`/plan-packs/${packId}/files/`, { method: 'POST', body: form })
}
export async function downloadPlanPackFile(id: string, filename: string): Promise<void> {
  const res = await apiFetchBinary(`${API_BASE}/plan-packs/files/${id}/download/`, { headers: {} })
  if (!res.ok) throw new Error(`Download failed (HTTP ${res.status})`)
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url; a.download = filename; a.click()
  URL.revokeObjectURL(url)
}

// --- Budget simulator advisor (Aria, advisory only) — CFO 2026-06-28 ---
export interface BudgetAdvice { ok: boolean; source: string; text: string }
export async function askBudgetAdvisor(payload: {
  levers: Record<string, number>
  outputs: Record<string, number>
  question?: string
  /** Per-lever sensitivity tables (x, pat, ebitda) so the advisor can answer
   *  break-even / what-if questions from deterministic engine output. */
  sweeps?: Record<string, { x: number; pat: number; ebitda: number }[]>
}): Promise<BudgetAdvice> {
  return apiFetch<BudgetAdvice>('/budgets/advisor/', {
    method: 'POST', body: JSON.stringify(payload),
  })
}

// ─── Graphite V2 age analysis (read-only replica read; omni-only) ─────────────

export interface GraphiteAgeRow {
  policy_number: string
  client_name: string
  agent_name: string
  product_name: string
  policy_status: string
  frequency: string
  invoice_total: number
  payment_total: number
  refund_total: number
  balance_outstanding: number
  days_30: number
  days_60: number
  days_90: number
  days_120_plus: number
  policy_created_at: string | null
}

export interface GraphiteAgeReport {
  report: string
  source: string
  filters: { search: string | null; status: string | null }
  summary: {
    policy_count: number
    total_invoice: number
    total_paid: number
    total_balance: number
    buckets: Record<string, number>
  }
  rows: GraphiteAgeRow[]
}

export async function getGraphiteAgeAnalysis(
  params: { search?: string; status?: string; limit?: number } = {},
): Promise<GraphiteAgeReport> {
  return apiFetch<GraphiteAgeReport>(
    `/reports/graphite-age-analysis/${_qs({
      search: params.search || undefined,
      status: params.status || undefined,
      limit: params.limit,
    })}`,
  )
}

// ── Agent Portal (UNICOIN sales-agent commissions) ────────────────────────
export interface AgentPortalStream { key: string; name: string; tag: string; uses_cutoff: boolean }
export interface AgentPortalCycle { id: string; label: string; start_date: string; end_date: string; status: string; created_at?: string }
export interface AgentPortalPayrun {
  cycle: string; total_payable_bwp: string
  streams: Record<string, { name: string; approved: number; rejected: number; payable_bwp: string }>
  agents: { agent: string; payable_bwp: string; items?: number }[]
}
export interface AgentPortalLine { id: string; agent_name: string; stream: string; policy_ref?: string; basis: string; commission: string; payable: boolean; reason: string; graphite_status?: string; graphite_note?: string; graphite_checked_at?: string | null }
export interface AgentPortalAccess { email: string; name: string; is_manager: boolean }
export interface AgentPortalMyProfile { matched: boolean; agent: string | null; cycle: string | null; summary: { total_lines?: number; approved?: number; rejected?: number; payable_bwp?: string }; lines: AgentPortalLine[] }
export interface AgentPortalVerifyResult { lines_checked: number; ok: number; mismatch: number; not_found: number; skipped: number; unavailable: boolean; checked_at: string }
export interface AgentPortalPayout {
  ready: { agent: string; bank: string; account: string; branch: string; amount_bwp: string }[]
  held: { agent: string; amount_bwp: string }[]
  ready_count: number; held_count: number
}

export const agentPortalStreams = () =>
  apiFetch<AgentPortalStream[]>('/agent-portal/cycles/streams/')
export const agentPortalCycles = () =>
  apiFetch<PaginatedResponse<AgentPortalCycle>>('/agent-portal/cycles/')
export const agentPortalCreateCycle = (body: { label: string; start_date: string; end_date: string }) =>
  apiFetch<AgentPortalCycle>('/agent-portal/cycles/', { method: 'POST', body: JSON.stringify(body) })
export interface AgentPortalIngestExtras { agent_name?: string; report_date?: string; source?: 'paste' | 'sheet' | 'hand' }
export const agentPortalIngest = (cycleId: string, stream: string, text: string, extras?: AgentPortalIngestExtras) =>
  apiFetch<Record<string, unknown>>(`/agent-portal/cycles/${cycleId}/ingest/`, {
    method: 'POST', body: JSON.stringify({ stream, text, ...(extras || {}) }),
  })
export interface AgentPortalSubmission {
  id: string; stream: string; agent_name: string; report_date: string | null; source: string
  rows_count: number; approved_count: number; rejected_count: number; payable_bwp: string
  submitted_by_name: string; created_at: string
}
export const agentPortalSubmissions = (cycleId: string) =>
  apiFetch<AgentPortalSubmission[]>(`/agent-portal/cycles/${cycleId}/submissions/`)
export interface AgentPortalSourceReport {
  id: string; kind: string; kind_display: string; rows_count: number
  uploaded_by_name: string; created_at: string
}
export const agentPortalSourceReports = (cycleId: string) =>
  apiFetch<AgentPortalSourceReport[]>(`/agent-portal/cycles/${cycleId}/source-reports/`)
export const agentPortalAddSourceReport = (cycleId: string, kind: string, text: string) =>
  apiFetch<AgentPortalSourceReport>(`/agent-portal/cycles/${cycleId}/source-reports/`, {
    method: 'POST', body: JSON.stringify({ kind, text }),
  })
export interface AgentPortalStatusIssue { policy: string; agent: string; stream: string; verdict: string; note: string }
export interface AgentPortalStatusCheck {
  available: boolean; detail?: string; report_rows?: number; checked?: number
  ok?: number; cancelled?: number; not_in_report?: number; premium_differs?: number
  issues?: AgentPortalStatusIssue[]
}
export const agentPortalStatusCheck = (cycleId: string) =>
  apiFetch<AgentPortalStatusCheck>(`/agent-portal/cycles/${cycleId}/policy-status-check/`)
export const agentPortalPayrun = (cycleId: string) =>
  apiFetch<AgentPortalPayrun>(`/agent-portal/cycles/${cycleId}/payrun/`)
export const agentPortalLines = (cycleId: string, stream?: string, payable?: boolean, agent?: string) => {
  const p = new URLSearchParams()
  if (stream) p.set('stream', stream)
  if (payable !== undefined) p.set('payable', String(payable))
  if (agent) p.set('agent', agent)
  const qs = p.toString()
  return apiFetch<AgentPortalLine[]>(`/agent-portal/cycles/${cycleId}/lines/${qs ? '?' + qs : ''}`)
}
export const agentPortalPayout = (cycleId: string) =>
  apiFetch<AgentPortalPayout>(`/agent-portal/cycles/${cycleId}/payout/`)
export const agentPortalAccess = () =>
  apiFetch<AgentPortalAccess>('/agent-portal/cycles/access/')
export const agentPortalMyProfile = () =>
  apiFetch<AgentPortalMyProfile>('/agent-portal/cycles/my-profile/')
export const agentPortalVerifyGraphite = (cycleId: string) =>
  apiFetch<AgentPortalVerifyResult>(`/agent-portal/cycles/${cycleId}/verify-graphite/`, { method: 'POST' })

// ── UniCoin Instant Insurance commission payslips (net of tax) ────────────────
export interface AgentPortalPayslip {
  id: string; number: string; agent_name: string; agent_ref: string
  gross: string; ex_vat: string; annual: string; tax: string; net: string
  breakdown: [string, string][]; not_paid: string[][]
  emailed_at: string | null; emailed_to: string; has_email: boolean
}
export interface AgentPortalPayslipBuild {
  cycle: string; payslips: number; created: number; updated: number
  gross_total_bwp: string; tax_total_bwp: string; net_total_bwp: string
}
export const agentPortalPayslips = (cycleId: string) =>
  apiFetch<AgentPortalPayslip[]>(`/agent-portal/cycles/${cycleId}/payslips/`)
export const agentPortalBuildPayslips = (cycleId: string) =>
  apiFetch<AgentPortalPayslipBuild>(`/agent-portal/cycles/${cycleId}/payslips/`, { method: 'POST' })
export async function agentPortalPayslipPdf(id: string): Promise<Blob> {
  const res = await apiFetchBinary(`${API_BASE}/agent-portal/payslips/${id}/pdf/`, { headers: {} })
  if (!res.ok) throw new Error(`Could not fetch payslip PDF (${res.status})`)
  return res.blob()
}
export const agentPortalEmailPayslip = (id: string) =>
  apiFetch<{ sent: number; to: string; emailed_at: string }>(`/agent-portal/payslips/${id}/email/`, { method: 'POST' })
export async function agentPortalAgentReviewXlsx(cycleId: string): Promise<Blob> {
  const res = await apiFetchBinary(`${API_BASE}/agent-portal/cycles/${cycleId}/agent-review-xlsx/`, { headers: {} })
  if (!res.ok) throw new Error(`Could not download the review (${res.status})`)
  return res.blob()
}
export const agentPortalApprove = (cycleId: string) =>
  apiFetch<AgentPortalCycle>(`/agent-portal/cycles/${cycleId}/approve/`, { method: 'POST' })
export const agentPortalReopen = (cycleId: string) =>
  apiFetch<AgentPortalCycle>(`/agent-portal/cycles/${cycleId}/reopen/`, { method: 'POST' })
export interface AgentPortalAgent { id: string; name: string; is_active: boolean; streams: string[]; has_bank: boolean }
// Pagination caps page_size at 100 (CappedPageNumberPagination) — loop pages so
// the Agents & Banking tab never silently truncates past 100 agents.
export async function agentPortalAgents(): Promise<{ results: AgentPortalAgent[] }> {
  const all: AgentPortalAgent[] = []
  let page = 1
  for (;;) {
    const r = await apiFetch<PaginatedResponse<AgentPortalAgent>>(`/agent-portal/agents/?page_size=100&page=${page}`)
    all.push(...r.results)
    if (!r.next || page >= 50) break
    page += 1
  }
  return { results: all }
}
export interface AgentPortalBank { bank_name?: string; account_name?: string; account_number?: string; branch_code?: string; updated_at?: string }
export const agentPortalGetBank = (agentId: string) =>
  apiFetch<AgentPortalBank>(`/agent-portal/agents/${agentId}/bank/`)
export const agentPortalSaveBank = (agentId: string, body: AgentPortalBank) =>
  apiFetch<AgentPortalBank>(`/agent-portal/agents/${agentId}/bank/`, { method: 'PUT', body: JSON.stringify(body) })
export interface AgentPortalBankRow {
  agent_id: string; agent_name: string; bank_name: string
  account_number: string; branch_code: string; account_name: string; updated_at?: string
}
export interface AgentPortalBankAccounts { submitted: number; total_agents: number; accounts: AgentPortalBankRow[]; revealed?: boolean; reveal_configured?: boolean; unlock_hours?: number }
export const agentPortalBankAccounts = () =>
  apiFetch<AgentPortalBankAccounts>('/agent-portal/agents/bank-accounts/')
// Second-factor reveal of full account numbers (Motlatsi 2026-07-22).
export const agentPortalBankUnlock = (password: string) =>
  apiFetch<{ revealed: boolean; reveal_configured?: boolean; unlock_hours?: number }>(
    '/agent-portal/agents/bank-unlock/', { method: 'POST', body: JSON.stringify({ password }) })
export const agentPortalBankLock = () =>
  apiFetch<{ revealed: boolean }>('/agent-portal/agents/bank-lock/', { method: 'POST', body: JSON.stringify({}) })

// ── Claims PO (assessment PDF → draft repairer + parts POs) ────────────────
// Phase-4 review screen for the /api/v1/claims-po/ pipeline (CFO 2026-07-06).
// The SERVER owns all money math (split / excess / VAT) — the UI never
// recomputes; it PATCHes inputs then re-GETs /plan/ for fresh numbers.

export type ClaimsAssessmentStatus =
  | 'uploaded' | 'parsing' | 'ready_for_review'
  | 'parse_failed' | 'pos_created' | 'failed'

export interface ClaimsPoResult {
  id: string
  po_number: string
  supplier: string
  supplier_id?: string
  kind: 'repairer' | 'parts'
  total: number | string
  currency: string
  supplier_currency_choice: string
  status: string
}

export type ClaimsProgressStage = 'uploaded' | 'parsing' | 'generating' | 'done' | 'failed'

export interface ClaimsAssessment {
  id: string
  status: ClaimsAssessmentStatus
  status_display: string
  parse_error: string
  progress_stage: ClaimsProgressStage
  stage_display?: string
  progress_pct: number
  eta_ms?: number
  total_ms?: number | null
  claim_number: string
  policy_number: string
  assessment_number: string
  client_name: string
  registration: string
  vehicle: string
  contact_details: string
  po_results: ClaimsPoResult[]
  created_at: string
  updated_at: string
}

/** One vendor override per allocation row the user reassigned. */
export interface ClaimsLineAllocation { id: string; vendor: string }

/** Per-vendor-label VAT / currency choice. Omitted keys fall back to the
 *  server default (SA-named suppliers → no VAT + ZAR record; else VAT + BWP).
 *  The currency choice is RECORDED ONLY — the PO itself stays BWP (no FX). */
export interface ClaimsSupplierSetting { vat?: boolean; currency?: 'BWP' | 'ZAR' }
export type ClaimsSupplierSettings = Record<string, ClaimsSupplierSetting>

export interface ClaimsMeta {
  claims_type: string
  policy_number: string
  claim_number: string
  assessment_number: string
  claim_description: string
  ad_note: string
  client_name: string
  registration: string
  vehicle: string
  contact_details: string
}

export interface ClaimsAssessmentDetail extends ClaimsAssessment, ClaimsMeta {
  line_allocations: ClaimsLineAllocation[]
  supplier_settings: ClaimsSupplierSettings
  excess_pct: number | string | null
  excess_min: number | string | null
  excess_amount: number | string | null
  markup_pct: number | string | null
}

export interface ClaimsPlanRow {
  id: string
  category: string
  label: string
  amount: number
  default_vendor: string
  lines: { description: string; qty: number; unit_price: number }[]
}

/** The REPAIRER PO block. `excess` is the deduction amount (shown negative).
 *  `repairer_parts` is the base value of parts the repairer supplied itself
 *  (billed on the repairer PO, not the parts PO). */
export interface ClaimsRepairerSplit {
  repairer_parts?: number
  labour: number
  paint: number
  markup: number
  sundries: number
  anti_corrosion: number
  underside_paint: number
  gross_excl: number
  excess: number
  excess_pct: number
  excess_min: number
  excl: number
  vat: number
  incl: number
}

/** The PARTS PO block. */
export interface ClaimsPartsSplit {
  parts_total: number
  excl: number
  vat: number
  incl: number
}

export interface ClaimsPlanResponse {
  id: string
  status: ClaimsAssessmentStatus
  plan: {
    rows: ClaimsPlanRow[]
    vendor_options: string[]
    repairer: string
  }
  split: {
    specialised: ClaimsRepairerSplit
    motor_centre: ClaimsPartsSplit
    grand: { excl: number; vat: number; incl: number }
  }
  saved: {
    line_allocations: ClaimsLineAllocation[]
    supplier_settings: ClaimsSupplierSettings
    excess_pct: number | null
    excess_min: number | null
    excess_amount: number | null
    markup_pct: number | null
  }
  meta: ClaimsMeta
}

/** PATCH body — writable fields only (user inputs + the 10 claim-meta fields). */
export interface ClaimsAssessmentPatch extends Partial<ClaimsMeta> {
  line_allocations?: ClaimsLineAllocation[]
  supplier_settings?: ClaimsSupplierSettings
  excess_pct?: number | null
  excess_min?: number | null
  excess_amount?: number | null
  markup_pct?: number | null
}

export interface CreateClaimsPosResponse {
  id: string
  status: ClaimsAssessmentStatus
  results: ClaimsPoResult[]
  errors: { error: string; vendor?: string; kind?: string }[]
}

/** Multipart upload — mirrors submitBugReport (apiFetch handles FormData). */
export async function uploadClaimsAssessment(
  file: File,
): Promise<{ id: string; status: ClaimsAssessmentStatus; progress_stage: ClaimsProgressStage; progress_pct: number; eta_ms: number; parse_error: string | null }> {
  const form = new FormData()
  form.append('file', file)
  const co = selectedCompanyId()
  if (co) form.append('company', co)
  return apiFetch('/claims-po/', { method: 'POST', body: form })
}

export async function listClaimsAssessments(): Promise<PaginatedResponse<ClaimsAssessment>> {
  return apiFetch<PaginatedResponse<ClaimsAssessment>>('/claims-po/')
}

export async function getClaimsAssessment(id: string): Promise<ClaimsAssessmentDetail> {
  return apiFetch<ClaimsAssessmentDetail>(`/claims-po/${id}/`)
}

export async function getClaimsPlan(id: string): Promise<ClaimsPlanResponse> {
  return apiFetch<ClaimsPlanResponse>(`/claims-po/${id}/plan/`)
}

export async function patchClaimsAssessment(
  id: string,
  body: ClaimsAssessmentPatch,
): Promise<ClaimsAssessmentDetail> {
  return apiFetch<ClaimsAssessmentDetail>(`/claims-po/${id}/`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

/** Generates the two DRAFT POs. 409 if already created — callers should
 *  guard on status === 'pos_created' and surface err.status === 409. */
export async function createClaimsPos(id: string): Promise<CreateClaimsPosResponse> {
  return apiFetch<CreateClaimsPosResponse>(`/claims-po/${id}/create-pos/`, { method: 'POST' })
}

/** Aria's pre-send checks — deterministic sanity flags before POs go out. */
export interface ClaimsReviewFlag {
  severity: 'info' | 'warn' | 'high'
  message: string
  source: string
}
export interface ClaimsReviewCheck {
  flags: ClaimsReviewFlag[]
  ai_ok: boolean
  checks_ran: boolean
}
export async function claimsReviewCheck(id: string): Promise<ClaimsReviewCheck> {
  return apiFetch<ClaimsReviewCheck>(`/claims-po/${id}/review-check/`, { method: 'POST' })
}

// ── Bulk payment upload (Record Payment → Upload) ─────────────────────────
export interface PaymentUploadRow {
  vendor: string; vendor_id: string | null; amount: string | null
  currency: string; method: string; reference: string; ok: boolean; error: string | null
}
export interface PaymentUploadPreview {
  mapping: Record<string, number>; has_header: boolean; via: string; row_count: number
  rows: PaymentUploadRow[]; ok_count: number; error_count: number
}
export interface PaymentUploadResult {
  created: { vendor: string; amount: string; payment_number: string; id: string }[]
  errors: { vendor: string; error: string }[]
  created_count: number; error_count: number; mapping_via: string
}
export const previewPaymentUpload = (text: string) =>
  apiFetch<PaymentUploadPreview>('/payments/bulk-upload/', {
    method: 'POST',
    body: JSON.stringify({ text, dry_run: true, company: selectedCompanyId() }),
  })
export const bulkUploadPayments = (text: string, bank_account: string, submit = true) =>
  apiFetch<PaymentUploadResult>('/payments/bulk-upload/', {
    method: 'POST',
    body: JSON.stringify({ text, bank_account, submit, company: selectedCompanyId() }),
  })
export async function downloadPaymentTemplate(): Promise<void> {
  const r = await apiFetchBinary('/payments/upload-template/')
  if (!r.ok) throw new Error(`template download failed (HTTP ${r.status})`)
  const b = await r.blob()
  const a = document.createElement('a')
  a.href = URL.createObjectURL(b); a.download = 'payment_upload_template.csv'; a.click()
}

// One-off (ad-hoc) payment — payee NOT in the vendor master, bank inline.
export const createOnceOffPayment = (body: {
  payee_name: string; account_number: string; bank_name?: string; branch_code?: string
  amount: string; currency?: string; payment_method?: string; reference?: string
  bank_account: string; submit?: boolean
  // What the bank is told. Blank = derived as before (CFO 2026-08-20).
  bank_beneficiary_name?: string; bank_our_reference?: string; bank_narration?: string
  remittance_email?: string  // Where FNB emails the POP (CFO 2026-08-22)
}) =>
  apiFetch<{ id?: string; payment_number?: string; detail?: string }>(
    '/payments/once-off/', {
      method: 'POST',
      body: JSON.stringify({ ...body, company: selectedCompanyId() }),
    })

// ─── Read an invoice to pre-fill a payment (CFO 2026-08-22, raised by Tlamelo) ─
export interface InvoiceReadResult {
  ok: boolean
  tier: string        // 'text' | 'vision' | 'none'
  message: string
  fields: {
    total_amount: string | null
    account_number: string | null
    bank_name: string | null
    branch_code: string | null
    invoice_number: string | null
    payee_name: string | null
  }
}

// Uploads the invoice for OCR pre-fill, reporting upload progress (0-100).
// XMLHttpRequest, not fetch: only XHR exposes upload progress. Auth is resolved
// the same way as apiFetch (MSAL Bearer → legacy Token, skip the SSO sentinel).
export async function readInvoiceForPayment(
  file: File,
  onProgress?: (pct: number) => void,
): Promise<InvoiceReadResult> {
  let authHeader: string | null = null
  try {
    const { acquireApiToken, SSO_API_CALLS_READY } = await import('@/auth/msal')
    if (SSO_API_CALLS_READY) {
      const bearer = await acquireApiToken()
      if (bearer) authHeader = `Bearer ${bearer}`
    }
  } catch { /* fall through to the legacy token */ }
  if (!authHeader) {
    const token = getToken()
    if (token && token !== SSO_SENTINEL_TOKEN) authHeader = `Token ${token}`
  }

  const form = new FormData()
  form.append('file', file)

  return new Promise<InvoiceReadResult>((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${API_BASE}/payments/read-invoice/`)
    if (authHeader) xhr.setRequestHeader('Authorization', authHeader)
    xhr.upload.onprogress = (e) => {
      if (onProgress && e.lengthComputable) {
        onProgress(Math.round((e.loaded / e.total) * 100))
      }
    }
    xhr.onload = () => {
      let data: unknown = null
      try { data = JSON.parse(xhr.responseText) } catch { /* non-JSON body */ }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(data as InvoiceReadResult)
      } else {
        const detail = (data as { detail?: string } | null)?.detail
          || `Could not read the invoice (${xhr.status}).`
        reject(new Error(detail))
      }
    }
    xhr.onerror = () => reject(new Error('Network error while uploading the invoice.'))
    xhr.send(form)
  })
}

/** Read an invoice to pre-fill a PAYMENT REQUEST (CFO 2026-09-01).
 *
 *  Same reading engine as readInvoiceForPayment above and the same result
 *  shape — a DIFFERENT endpoint only because the two screens have different
 *  gates: /payments/read-invoice/ is maker-only (Financial Controller / Senior
 *  Accountant / Accountant), while raising a payment REQUEST needs no maker
 *  title. Pointing this screen at the maker-gated one would 403 the Finance
 *  Managers and others who legitimately raise requests every day.
 *
 *  Saves nothing — the fields come back for a human to check.
 */
export async function readInvoiceForPaymentRequest(
  file: File,
  onProgress?: (pct: number) => void,
): Promise<InvoiceReadResult> {
  let authHeader: string | null = null
  try {
    const { acquireApiToken, SSO_API_CALLS_READY } = await import('@/auth/msal')
    if (SSO_API_CALLS_READY) {
      const bearer = await acquireApiToken()
      if (bearer) authHeader = `Bearer ${bearer}`
    }
  } catch { /* fall through to the legacy token */ }
  if (!authHeader) {
    const token = getToken()
    if (token && token !== SSO_SENTINEL_TOKEN) authHeader = `Token ${token}`
  }

  const form = new FormData()
  form.append('file', file)

  return new Promise<InvoiceReadResult>((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${API_BASE}/payment-requests/read-invoice/`)
    if (authHeader) xhr.setRequestHeader('Authorization', authHeader)
    xhr.upload.onprogress = (e) => {
      if (onProgress && e.lengthComputable) {
        onProgress(Math.round((e.loaded / e.total) * 100))
      }
    }
    xhr.onload = () => {
      let data: unknown = null
      try { data = JSON.parse(xhr.responseText) } catch { /* non-JSON body */ }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(data as InvoiceReadResult)
      } else {
        const detail = (data as { detail?: string } | null)?.detail
          || `Could not read the invoice (${xhr.status}).`
        reject(new Error(detail))
      }
    }
    xhr.onerror = () => reject(new Error('Network error while uploading the invoice.'))
    xhr.send(form)
  })
}

// ─── Drop Box (CFO handover 2026-09-02) ──────────────────────────────────────
// Drop invoice(s) or a ZIP; Omni reads each and creates a filled DRAFT payment
// request to check + submit. Reuses the invoice reader + supplier memory server
// side. Submit goes through the normal create endpoint (every control fires).
export interface PaymentDraft {
  id: string; ref: string; status: string; subject: string; payee: string;
  category: string; entity: string; currency: string;
  account_name: string; account_number: string; bank_name: string; branch_code: string;
  line_items: Array<Record<string, unknown>>; needs_check: string[];
  source_file: string; created_at: string;
  // Safety catch (Feature C): the amount the reader found, whether the entered
  // total now disagrees with it, and any reason given for a changed bank.
  read_amount?: string | null; amount_mismatch?: boolean; bank_change_reason?: string;
  // B8: the payment a hand-raised refund reverses. B7: where a copy came from.
  original_payment_ref?: string; duplicated_from_ref?: string;
}

// What the human can send with a submit to clear a control that fired.
export interface DraftSubmitAnswer {
  confirm_amount?: boolean; bank_change_reason?: string; new_payee_confirmed?: boolean
  // PAY-SUP-01: a supplier invoice not yet due — the payment date (on/after the
  // due date) or the CFO-approved reason for paying early.
  payment_date?: string; early_payment_reason?: string; discount_checked?: boolean
}
// The submit outcome — ok on 201, else the control that blocked it and its message.
export interface DraftSubmitResult {
  ok: boolean; status: number; control?: string; detail?: string; ref?: string
  // Set when the submitted draft went to the exception committee (201).
  exception?: { control?: string; message?: string }
}

export async function dropBoxInvoices(
  files: File[], onProgress?: (pct: number) => void,
): Promise<{ created: number; drafts: PaymentDraft[]; unreadable: string[] }> {
  let authHeader: string | null = null
  try {
    const { acquireApiToken, SSO_API_CALLS_READY } = await import('@/auth/msal')
    if (SSO_API_CALLS_READY) { const b = await acquireApiToken(); if (b) authHeader = `Bearer ${b}` }
  } catch { /* fall through to the legacy token */ }
  if (!authHeader) { const t = getToken(); if (t && t !== SSO_SENTINEL_TOKEN) authHeader = `Token ${t}` }
  const form = new FormData()
  files.forEach((f) => form.append('files', f))
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${API_BASE}/payment-requests/drop-box/`)
    if (authHeader) xhr.setRequestHeader('Authorization', authHeader)
    xhr.upload.onprogress = (e) => {
      if (onProgress && e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100))
    }
    xhr.onload = () => {
      let data: unknown = null
      try { data = JSON.parse(xhr.responseText) } catch { /* non-JSON */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data as { created: number; drafts: PaymentDraft[]; unreadable: string[] })
      else reject(new Error((data as { detail?: string } | null)?.detail || `Drop Box failed (${xhr.status}).`))
    }
    xhr.onerror = () => reject(new Error('Network error while uploading the invoices.'))
    xhr.send(form)
  })
}

export async function listPaymentDrafts(): Promise<{ drafts: PaymentDraft[] }> {
  return apiFetch('/payment-requests/drafts/')
}
export async function updatePaymentDraft(id: string, fields: Partial<PaymentDraft>): Promise<PaymentDraft> {
  return apiFetch(`/payment-requests/drafts/${id}/`, { method: 'PATCH', body: JSON.stringify(fields) })
}
export async function deletePaymentDraft(id: string): Promise<void> {
  return apiFetch(`/payment-requests/drafts/${id}/`, { method: 'DELETE' })
}
export async function submitPaymentDraft(
  id: string, answer: DraftSubmitAnswer = {},
): Promise<DraftSubmitResult> {
  // A manual fetch (not apiFetch) so a blocked submit (409/400 from a control)
  // returns its control + message instead of throwing — the raiser needs to see
  // which flag fired so they can clear it.
  let authHeader: string | null = null
  try {
    const { acquireApiToken, SSO_API_CALLS_READY } = await import('@/auth/msal')
    if (SSO_API_CALLS_READY) { const b = await acquireApiToken(); if (b) authHeader = `Bearer ${b}` }
  } catch { /* fall through to the legacy token */ }
  if (!authHeader) { const t = getToken(); if (t && t !== SSO_SENTINEL_TOKEN) authHeader = `Token ${t}` }
  const res = await fetch(`${API_BASE}/payment-requests/drafts/${id}/submit/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(authHeader ? { Authorization: authHeader } : {}) },
    body: JSON.stringify(answer),
  })
  let data: { control?: string; detail?: string; ref?: string;
              exception?: { control?: string; message?: string } } | null = null
  try { data = await res.json() } catch { /* non-JSON */ }
  return {
    ok: res.status >= 200 && res.status < 300, status: res.status,
    control: data?.control, detail: data?.detail, ref: data?.ref,
    exception: data?.exception,
  }
}

// ── Copy / duplicate a payment request (B7, CFO Build Spec) ─────────────────
// Any request — whatever its own status — may be the source. Opens a NEW
// request in DRAFT with a freshly generated reference; nothing is submitted.
// The copy carries the same controls as any other draft: it appears on the
// caller's own Drafts list and is submitted with submitPaymentDraft(), so
// PAY-DUP-01 and every other create-time control fires on it exactly as it
// would on a hand-typed request.
export async function duplicatePaymentRequest(
  id: string,
): Promise<PaymentDraft & { source_ref: string }> {
  return apiFetch(`/payment-requests/${id}/duplicate/`, { method: 'POST' })
}

// ── Payment exception committee (CFO 2026-09-02) ─────────────────────────────
export interface ExceptionSignoff {
  signer: string
  decision: 'approve' | 'reject'
  is_independent: boolean
  at: string
}
export interface PaymentExceptionRow {
  id: string; ref: string; entity: string; payee: string; currency: string
  total: string; status: string
  exception_control: string; exception_reason: string
  bank_change_reason: string
  exception_raised_at: string | null; exception_decision: string
  exception_decided_at: string | null; exception_cleared_at: string | null
  raised_by: string; raised_by_id: string | null
  signoffs: ExceptionSignoff[]; approvals: number; required: number
  attachments?: { id: string; name: string; uploaded_by: string; at: string }[]
}
export interface PaymentExceptionsBoard {
  open: PaymentExceptionRow[]; to_clear: PaymentExceptionRow[]
  required: number; is_committee_member: boolean; is_cfo: boolean
}
export async function listPaymentExceptions(): Promise<PaymentExceptionsBoard> {
  return apiFetch('/payment-requests/exceptions/')
}
export async function signPaymentException(
  id: string,
  body: { decision: 'approve' | 'reject'; called_who?: string; called_number?: string; note?: string; proof_attachment_id?: string },
): Promise<{ ok: boolean; decided?: boolean; approvals?: number; detail?: string }> {
  // Manual fetch so a 400/403/409 returns its message instead of throwing.
  let authHeader: string | null = null
  try {
    const { acquireApiToken, SSO_API_CALLS_READY } = await import('@/auth/msal')
    if (SSO_API_CALLS_READY) { const b = await acquireApiToken(); if (b) authHeader = `Bearer ${b}` }
  } catch { /* fall through to the legacy token */ }
  if (!authHeader) { const t = getToken(); if (t && t !== SSO_SENTINEL_TOKEN) authHeader = `Token ${t}` }
  const res = await fetch(`${API_BASE}/payment-requests/${id}/exception-signoff/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(authHeader ? { Authorization: authHeader } : {}) },
    body: JSON.stringify(body),
  })
  let data: { decided?: boolean; approvals?: number; detail?: string } | null = null
  try { data = await res.json() } catch { /* non-JSON */ }
  return { ok: res.ok, decided: data?.decided, approvals: data?.approvals, detail: data?.detail }
}
export async function clearPaymentException(
  id: string, body?: { close?: boolean; reason?: string },
): Promise<{ cleared: boolean; closed?: boolean; status?: string }> {
  return apiFetch(`/payment-requests/${id}/exception-clear/`, {
    method: 'POST', body: JSON.stringify(body || {}),
  })
}

// ─── What the bank will actually show for a payment ──────────────────────────
// The server folds these to FNB's permitted character set. Never reimplement
// that folding here: two copies of the rule drift, and the operator would then
// be shown something different from what is sent.
export interface BankViewPreview {
  beneficiary_name: string
  our_reference: string
  narration: string
}

export const previewBankView = (body: {
  payment_id?: string
  payee_name?: string
  // The once-off create defaults a blank reference to "One-off - <payee>", so
  // the preview must be told the reference to show what will really be sent.
  reference?: string
  bank_beneficiary_name?: string
  bank_our_reference?: string
  bank_narration?: string
}) =>
  apiFetch<BankViewPreview>('/fnb/bank-view-preview/', {
    method: 'POST',
    body: JSON.stringify(body),
  })

// ─── Underwriting documents (Cover Notes + WCA certificates) ────────────────
export interface UnderwritingDocument {
  id: string
  doctype: string; doctype_label: string
  fmt: string; fmt_label: string
  policy_number: string; insured_name: string
  company: string | null; company_code: string | null
  status: string; pdf_size: number
  issued_by: string | null; issued_by_name: string | null; issued_at: string | null
  emailed_to: string; emailed_at: string | null; created_at: string
  emailed?: boolean; email_error?: string
}
export async function getUnderwritingDocuments(
  params?: Record<string, string>,
): Promise<PaginatedResponse<UnderwritingDocument>> {
  const q = new URLSearchParams(params).toString()
  return apiFetch<PaginatedResponse<UnderwritingDocument>>(`/underwriting/documents/${q ? `?${q}` : ''}`)
}
export async function issueUnderwritingDocument(payload: {
  doctype: string; fmt?: string; fields: Record<string, string>; email_to?: string
}): Promise<UnderwritingDocument> {
  return apiFetch<UnderwritingDocument>('/underwriting/issue/', {
    method: 'POST',
    body: JSON.stringify({ ...payload, company: selectedCompanyId() }),
  })
}
export async function extractUnderwriting(payload: {
  file_b64: string; filename: string; content_type: string
}): Promise<{ ok: boolean; fields?: Record<string,string>; doctype?: string; via?: string; message?: string }> {
  return apiFetch('/underwriting/extract/', { method: 'POST', body: JSON.stringify(payload) })
}
export async function openUnderwritingPdf(id: string): Promise<void> {
  // Auth header isn't sent on a plain <a> GET — fetch as a blob (mirrors openInvoicePdf).
  const res = await apiFetchBinary(`${API_BASE}/underwriting/documents/${id}/pdf/`, { headers: {} })
  if (!res.ok) throw new Error(`PDF failed: ${res.status}`)
  const url = URL.createObjectURL(await res.blob())
  // Save via anchor — window.open() on a blob: URL fails in the OmniDesktop
  // wrapper with "Get an app to open this 'blob' link".
  const a = document.createElement('a')
  a.href = url
  a.download = `document-${id}.pdf`
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 30000)
}
export async function getUnderwritingToolToken(): Promise<string> {
  const r = await apiFetch<{ token: string }>('/underwriting/documents/tool-token/')
  return r.token
}
export function underwritingPdfUrl(id: string): string {
  return `${API_BASE}/underwriting/documents/${id}/pdf/`
}

// ── Commissions — monthly agent commission submissions ─────────────────────
export interface CommissionGroup {
  id: string; key: string; name: string; withholding_rate: string
  pays_via: string; owner_name: string; is_active: boolean
}
export interface CommissionLine {
  id?: string; policy_number: string; client_name: string; transaction_type: string
  frequency?: string
  amount_collected: string; annualised_premium: string; commission_rate: string
  amount_applicable?: string
  collection_date?: string | null; is_policy_closed: boolean; commission_amount: string
}
export interface CommissionSubmission {
  id: string; agent: string; agent_name: string; group: string; group_name: string
  period_label: string; status: string; status_label?: string
  submitted_at?: string | null; review_note?: string
  first_reviewed_at?: string | null; first_reviewed_by_name?: string
  second_reviewed_at?: string | null; second_reviewed_by_name?: string
  final_at?: string | null; final_by_name?: string
  paid_at?: string | null; paid_by_name?: string; notified_at?: string | null
  gross_commission: string; withholding_rate: string; withholding_amount: string
  net_payable: string; lines: CommissionLine[]; amendments?: CommissionAmendment[]; has_statement_file?: boolean; created_at?: string
  // At-a-glance sanity checks for the reviewer (server-computed, review states only).
  review_flags?: { level: 'clean' | 'check' | 'unknown'; items: string[] }
}
export interface CommissionAmendment {
  id: string; actor_name: string; created_at: string
  old_gross: string; new_gross: string; note: string
}
export interface CommissionAccess {
  email: string; name: string
  is_reviewer: boolean; stages: string[]; can_export: boolean; is_payroll: boolean   // roles
  agent: string | null; agent_id?: string | null; group: string | null
  group_name?: string | null; pays_via?: string | null; works_via_company?: boolean | null
  withholding_rate?: string | null   // effective rate for this agent (0 if via-company / payroll)
}
export interface CommissionSummaryRow {
  id: string; agent: string; group: string; period: string; status: string
  gross: string; withholding: string; net: string; lines: number
}

const commissionListToArray = <T,>(r: PaginatedResponse<T> | T[]): T[] =>
  Array.isArray(r) ? r : (r?.results ?? [])

export const commissionGroups = () =>
  apiFetch<PaginatedResponse<CommissionGroup> | CommissionGroup[]>('/commissions/groups/')
    .then(commissionListToArray)
export const commissionAccess = () =>
  apiFetch<CommissionAccess>('/commissions/submissions/access/')
// Walks every page so a review queue / history longer than one page is never
// silently truncated. `mine` forces own-scope (the "My commission" tab).
export const commissionAgents = () =>
  apiFetch<{ id: string; name: string; code: string }[]>('/commissions/submissions/agents/')

// ── Adoption scoreboard (who is actually using Omni vs Excel) — management-only ──
export interface AdoptionRow {
  name: string; email: string; company: string; title: string
  last_login: string | null; last_action: string | null
  actions_window: number; status: 'active' | 'dormant' | 'never'
}
export interface AdoptionReport {
  window_days: number
  summary: { total: number; active: number; dormant: number; never: number; adoption_pct: number }
  rows: AdoptionRow[]
}
export const adoptionScoreboard = (days = 30, company?: string) =>
  apiFetch<AdoptionReport>(`/adoption/?days=${days}${company ? `&company=${company}` : ''}`)

// Screen-usage telemetry (CFO 2026-09-03) — top screens by distinct users.
export type ScreenSurface = 'app' | 'm' | 'desktop'
export interface ScreenUsageRow {
  screen: string
  surface: ScreenSurface
  users: number
  hits: number
}
export interface ScreenUsageReport {
  days: number
  rows: ScreenUsageRow[]
}
export const adoptionScreens = (days = 30) =>
  apiFetch<ScreenUsageReport>(`/adoption/screens/?days=${days}`)

export const commissionSubmissions = async (
  params?: { group?: string; period?: string; mine?: boolean; queue?: boolean; agent?: string },
): Promise<CommissionSubmission[]> => {
  const q = new URLSearchParams()
  if (params?.group) q.set('group', params.group)
  if (params?.period) q.set('period', params.period)
  if (params?.mine) q.set('mine', '1')
  if (params?.agent) q.set('agent', params.agent)   // reviewer: focus one agent (incl drafts)
  if (params?.queue) q.set('queue', '1')   // only items awaiting THIS reviewer's stage
  q.set('page_size', '200')
  let url: string | null = `/commissions/submissions/?${q.toString()}`
  const out: CommissionSubmission[] = []
  for (let guard = 0; url && guard < 50; guard++) {
    const r: PaginatedResponse<CommissionSubmission> | CommissionSubmission[] = await apiFetch(url)
    if (Array.isArray(r)) { out.push(...r); break }
    out.push(...(r.results ?? []))
    url = r.next ?? null
  }
  return out
}
// The server binds the agent (own agent) — the client never sends it.
export const commissionCreateSubmission =
  (body: { period_label: string; lines: CommissionLine[] }) =>
    apiFetch<CommissionSubmission>('/commissions/submissions/',
      { method: 'POST', body: JSON.stringify(body) })
export const commissionUpdateSubmission = (id: string, body: { lines: CommissionLine[] }) =>
  apiFetch<CommissionSubmission>(`/commissions/submissions/${id}/`,
    { method: 'PATCH', body: JSON.stringify(body) })
export const commissionSubmit = (id: string) =>
  apiFetch<CommissionSubmission>(`/commissions/submissions/${id}/submit/`, { method: 'POST' })
export const commissionReview = (id: string, approve: boolean, note?: string) =>
  apiFetch<CommissionSubmission>(`/commissions/submissions/${id}/review/`,
    { method: 'POST', body: JSON.stringify({ approve, note: note || '' }) })
export const commissionMarkProcessed = (id: string) =>
  apiFetch<CommissionSubmission>(`/commissions/submissions/${id}/mark-processed/`, { method: 'POST' })
// Approve several at once — the server still runs each through the same review()
// (stage + separation-of-duties), so this only batches what you could approve singly.
export const commissionBulkReview = (ids: string[]) =>
  apiFetch<{ ok: boolean; approved: string[]; skipped: { id: string; reason: string }[] }>(
    '/commissions/submissions/bulk-review/', { method: 'POST', body: JSON.stringify({ ids }) })
// Aria's on-demand second opinion on one commission (read-only, best-effort).
export const commissionAiCheck = (id: string) =>
  apiFetch<{ note: string; source: 'aria' | 'checks' }>(
    `/commissions/submissions/${id}/ai-check/`, { method: 'POST' })
// CFO direct approve / send-back from any stage (CFO 2026-08-18).
export const commissionFinalApprove = (id: string, approve: boolean, note?: string) =>
  apiFetch<CommissionSubmission>(`/commissions/submissions/${id}/final-approve/`,
    { method: 'POST', body: JSON.stringify({ approve, note: note || '' }) })
// Upload a commission workbook → server parses its per-policy table → creates the
// agent's submission for the month (draft). No retyping. FormData (not JSON).
export const commissionUpload = (
  file: File, period: string,
  opts?: { agent?: string; group?: string; inhouse?: boolean; submit?: boolean; own?: boolean },
) => {
  const fd = new FormData()
  fd.append('file', file); fd.append('period', period)
  if (opts?.agent) fd.append('agent', opts.agent)
  if (opts?.group) fd.append('group', opts.group)
  if (opts?.inhouse) fd.append('inhouse', '1')   // in-house summary: one sheet → many agents
  if (opts?.submit) fd.append('submit', '1')     // push loaded rows straight into review
  if (opts?.own) fd.append('own', '1')           // "My commission" tab: force the signed-in user's own agent
  // per-policy → { agent, lines }; in-house → { agents, created, gross }
  return apiFetch<{ ok: boolean; agent?: string; lines?: number; status?: string;
                    agents?: number; created?: number; gross?: string }>(
    '/commissions/submissions/upload/', { method: 'POST', body: fd })
}
export interface CommissionPreviewRow {
  name?: string; gross?: string            // in-house
  policy_number?: string; client_name?: string; transaction_type?: string
  amount_collected?: string; commission_amount?: string   // per-policy
}
export interface CommissionPreview {
  ok: boolean; mode: 'inhouse' | 'policy'; count: number; total: string
  agent?: string; rows: CommissionPreviewRow[]; truncated?: boolean
}
// Dry-run: parse the sheet and return what we read WITHOUT saving — the loader's
// "check before you commit" step.
export const commissionUploadPreview = (
  file: File, period: string, opts?: { group?: string; inhouse?: boolean },
) => {
  const fd = new FormData()
  fd.append('file', file); fd.append('period', period); fd.append('preview', '1')
  if (opts?.group) fd.append('group', opts.group)
  if (opts?.inhouse) fd.append('inhouse', '1')
  return apiFetch<CommissionPreview>('/commissions/submissions/upload/', { method: 'POST', body: fd })
}
export const commissionSummary = (params?: { group?: string; period?: string }) => {
  const q = new URLSearchParams()
  if (params?.group) q.set('group', params.group)
  if (params?.period) q.set('period', params.period)
  const qs = q.toString()
  return apiFetch<CommissionSummaryRow[]>(`/commissions/submissions/summary/${qs ? `?${qs}` : ''}`)
}
// Month-close: email each approved agent their statement. preview=true is a
// dry-run (returns who would be emailed, sends nothing).
export const commissionEmailStatements = (group: string, period: string, preview: boolean) =>
  apiFetch<{ ok: boolean; preview: boolean; count?: number; would_email?: string[];
             emailed?: number; emailed_names?: string[]; no_email: string[]; failed?: string[] }>(
    '/commissions/submissions/email-statements/',
    { method: 'POST', body: JSON.stringify({ group, period, preview: preview ? '1' : '0' }) })
export const commissionPayoutExportPath = (group: string, period: string) =>
  `/commissions/submissions/payout-export/?group=${encodeURIComponent(group)}&period=${encodeURIComponent(period)}`

// ---------------------------------------------------------------------------
// Supplier Payables Reconciliation (/payables/recon)
// Monthly per-supplier "who was paid, who was not, and why". Ids are UUID
// strings; money arrives as strings so a Pula figure is never a JS float.
// ---------------------------------------------------------------------------

export type SupRunStatus = 'open' | 'in_progress' | 'finalised' | 'reopened'
export type SupPaymentStatus = 'paid' | 'partially_paid' | 'unpaid' | 'held'
export type SupMatchStatus =
  | 'matched' | 'partial' | 'no_po' | 'no_grn' | 'no_claim' | 'unmatched'

export interface SupplierReconRun {
  id: string
  company: string
  company_name: string
  company_code: string
  period_label: string
  period_start: string
  period_end: string
  status: SupRunStatus
  status_display: string
  is_locked: boolean
  total_invoiced: string
  total_paid: string
  total_unpaid: string
  total_held: string
  total_escalated: string
  total_not_posted: string
  pct_paid: number
  prepared_by_name: string
  reviewed_by_name: string
  owner_name: string
  finalised_at: string | null
  last_built_at: string | null
  created_at: string
}

export interface SupEscalation {
  id: string
  justification: string
  amount: string
  status: 'open' | 'acknowledged' | 'resolved' | 'waived'
  status_display: string
  raised_by_name: string
  raised_to_name: string
  created_at: string
  resolved_at: string | null
  resolution_note: string
}

export interface SupActionLogRow {
  id: string
  action: string
  from_status: string
  to_status: string
  note: string
  actor_name: string
  created_at: string
}

export interface SupReconItem {
  id: string
  invoice: string
  invoice_number: string
  issue_date: string
  due_date: string | null
  currency: string
  face_amount: string
  supplier_name: string
  category: string
  amount: string
  amount_paid: string
  amount_outstanding: string
  payment_status: SupPaymentStatus
  payment_status_display: string
  match_status: SupMatchStatus
  match_status_display: string
  ledger_stage: 'posted' | 'draft'
  ledger_stage_display: string
  is_posted: boolean
  po_number: string | null
  grn_number: string | null
  claim_reference: string
  reason_code: string | null
  reason_code_label: string
  justification: string
  actioned: boolean
  actioned_by_name: string
  actioned_by_department: string
  actioned_at: string | null
  days_past_due: number
  ageing_bucket: string
  is_overdue: boolean
  due_state: 'paid' | 'not_due' | 'overdue' | 'overdue_30'
  requires_escalation: boolean
  needs_justification: boolean
  escalations: SupEscalation[]
  action_logs?: SupActionLogRow[]
}

export interface SupReconLine {
  id: string
  run: string
  supplier: string
  supplier_name: string
  category: string
  category_display: string
  status: 'pending' | 'exception' | 'cleared'
  status_display: string
  invoiced: string
  paid: string
  unpaid: string
  held: string
  overdue: string
  max_days_past_due: number
  invoice_count: number
  unactioned_count: number
  assigned_to: string | null
  assigned_to_name: string
}

export interface SupReasonCode {
  id: string
  code: string
  label: string
  group: string
  group_display: string
  requires_escalation: boolean
  active: boolean
}

export interface SupReconDashboard {
  run: SupplierReconRun
  kpis: {
    total_invoiced: string
    total_paid: string
    total_unpaid: string
    total_held: string
    total_escalated: string
    total_not_posted: string
    pct_paid: number
    invoice_count: number
    supplier_count: number
    unactioned_count: number
    overdue_count: number
    overdue_30_count: number
    open_escalations: number
    unmatched_count: number
    no_claim_count: number
    no_po_count: number
    not_posted_count: number
  }
  by_category: { category: string; invoiced: string; paid: string; unpaid: string; held: string; suppliers: number }[]
  by_reason: { reason_code__code: string; reason_code__label: string; bills: number; amount: string }[]
  ageing: Record<string, string>
  ageing_group?: 'all' | 'claims' | 'operational'
  claim_categories?: string[]
  unclassified_vendors: { contact_id: string; contact__name: string; bills: number; invoiced: string }[]
}

export interface SupReconBlocker {
  item_id: string
  invoice_number: string
  supplier: string
  amount_outstanding: string
  problems: string[]
}

const unwrapSupRecon = <T,>(r: T[] | { results: T[] }): T[] =>
  Array.isArray(r) ? r : (r?.results ?? [])

export const getSupplierReconRuns = async (params?: { period?: string; status?: string }) => {
  const q = new URLSearchParams()
  if (params?.period) q.set('period', params.period)
  if (params?.status) q.set('status', params.status)
  const qs = q.toString()
  return unwrapSupRecon(await apiFetch<SupplierReconRun[] | { results: SupplierReconRun[] }>(
    `/supplier-recon/runs/${qs ? `?${qs}` : ''}`))
}

export const buildSupplierReconRun = (company: string, period_label: string) =>
  apiFetch<SupplierReconRun>('/supplier-recon/runs/build/', {
    method: 'POST', body: JSON.stringify({ company, period_label }),
  })

export const getSupplierReconDashboard = (
  runId: string, group?: 'all' | 'claims' | 'operational',
) =>
  apiFetch<SupReconDashboard>(
    `/supplier-recon/runs/${runId}/dashboard/${group && group !== 'all'
      ? `?group=${group}` : ''}`)

export const getSupplierReconBlockers = (runId: string) =>
  apiFetch<{ count: number; items: SupReconBlocker[] }>(
    `/supplier-recon/runs/${runId}/blockers/`)

export const finaliseSupplierReconRun = (runId: string) =>
  apiFetch<SupplierReconRun>(`/supplier-recon/runs/${runId}/finalise/`, { method: 'POST' })

export const reopenSupplierReconRun = (runId: string, reason: string) =>
  apiFetch<SupplierReconRun>(`/supplier-recon/runs/${runId}/reopen/`, {
    method: 'POST', body: JSON.stringify({ reason }),
  })

export const getSupplierReconLines = async (runId: string) =>
  unwrapSupRecon(await apiFetch<SupReconLine[] | { results: SupReconLine[] }>(
    `/supplier-recon/lines/?run=${encodeURIComponent(runId)}&page_size=500`))

export const getSupplierReconItems = async (params: {
  run?: string; line?: string; payment_status?: string; match_status?: string
  open?: boolean; overdue?: boolean; unactioned?: boolean; escalated?: boolean
  invoiced_actioned?: boolean
}) => {
  const q = new URLSearchParams()
  if (params.run) q.set('run', params.run)
  if (params.line) q.set('line', params.line)
  if (params.payment_status) q.set('payment_status', params.payment_status)
  if (params.match_status) q.set('match_status', params.match_status)
  if (params.open) q.set('open', 'true')
  if (params.overdue) q.set('overdue', 'true')
  if (params.unactioned) q.set('unactioned', 'true')
  if (params.escalated) q.set('escalated', 'true')
  if (params.invoiced_actioned) q.set('invoiced_actioned', 'true')
  q.set('page_size', '500')
  return unwrapSupRecon(await apiFetch<SupReconItem[] | { results: SupReconItem[] }>(
    `/supplier-recon/items/?${q.toString()}`))
}

export const getSupplierReconReasonCodes = async () =>
  unwrapSupRecon(await apiFetch<SupReasonCode[] | { results: SupReasonCode[] }>(
    '/supplier-recon/reason-codes/'))

export const actionSupplierReconItem = (
  itemId: string,
  body: { reason_code?: string | null; justification: string; hold?: boolean },
) => apiFetch<SupReconItem>(`/supplier-recon/items/${itemId}/action/`, {
  method: 'POST', body: JSON.stringify(body),
})

export const escalateSupplierReconItem = (itemId: string, justification: string) =>
  apiFetch<SupEscalation>(`/supplier-recon/items/${itemId}/escalate/`, {
    method: 'POST', body: JSON.stringify({ justification }),
  })

export const supplierReconExportPath = (runId: string) =>
  `/supplier-recon/runs/${runId}/export/`

export const resolveSupplierReconEscalation = (
  escId: string, status: 'resolved' | 'waived' | 'acknowledged', note: string,
) => apiFetch<SupEscalation>(`/supplier-recon/escalations/${escId}/resolve/`, {
  method: 'POST', body: JSON.stringify({ status, note }),
})

// ── Supplier statement ingestion + matching ──────────────────────────────────
// Upload a supplier's own statement of account against a supplier month, then
// reconcile it against Omni's vendor bills. Omni proposes what to pay; the CFO
// authorises the payment in FNB — this moves no money.
export interface SupStatementLine {
  id: string
  row_number: number
  line_type: string
  reference: string
  doc_date: string | null
  description: string
  amount: string
  running_balance: string | null
}
export interface SupStatementMatch {
  id: string
  match_type: string
  proposal: 'pay_now' | 'hold' | 'do_not_pay' | 'investigate'
  variance: string
  proposed_amount: string
  reason: string
  statement_reference: string | null
  omni_invoice: string | null
  omni_invoice_item: string | null
}
export interface SupStatementDetail {
  id: string
  line_id: string
  supplier: string
  period: string
  file_name: string
  currency: string
  statement_date: string | null
  opening_balance: string | null
  closing_balance: string | null
  status: string
  parse_error: string
  uploaded_by: string | null
  uploaded_at: string
  matched_at: string | null
  lines: SupStatementLine[]
  matches: SupStatementMatch[]
  summary: {
    total_lines: number
    by_match_type: Record<string, number>
    by_proposal: Record<string, number>
    proposed_pay_total: string
  }
  parse_warnings?: { row: number; message: string }[]
}
export interface SupStatementListItem {
  id: string
  file_name: string
  status: string
  currency: string
  statement_date: string | null
  closing_balance: string | null
  uploaded_at: string
  uploaded_by: string | null
  line_count: number
}

export const uploadSupplierStatement = (
  lineId: string, file: File, currency?: string,
): Promise<SupStatementDetail> => {
  const fd = new FormData()
  fd.append('file', file)
  if (currency) fd.append('currency', currency)
  return apiFetch<SupStatementDetail>(
    `/supplier-recon/lines/${lineId}/statements/upload/`,
    { method: 'POST', headers: {}, body: fd })
}

export const listSupplierStatements = (lineId: string) =>
  apiFetch<{ line_id: string; statements: SupStatementListItem[] }>(
    `/supplier-recon/lines/${lineId}/statements/`)

export const getSupplierStatement = (statementId: string) =>
  apiFetch<SupStatementDetail>(`/supplier-recon/statements/${statementId}/`)

export const rematchSupplierStatement = (statementId: string) =>
  apiFetch<SupStatementDetail>(
    `/supplier-recon/statements/${statementId}/match/`, { method: 'POST' })

export const deleteSupplierStatement = (statementId: string) =>
  apiFetch<void>(`/supplier-recon/statements/${statementId}/`, { method: 'DELETE' })

// ── Intelligence Summary (/intel-summary) ────────────────────────────────────
// Omni's own figures next to Alpha Brain's, so the two can be read together.
// Counts and totals only — there is no customer row in this payload.
export interface IntelPolicies {
  definition: string
  active_total: number
  active_by_category: Record<string, number>
  not_activated_total: number
  not_activated_by_category: Record<string, number>
  lapsed_or_inactive_total: number
  reconciles_to_brain_active: number
  reconciliation_note: string
  source: string
}
export interface IntelPremium {
  currency: string
  month: {
    gwp: string; net_earned_premium: string; gross_profit: string; pat: string
    gross_loss_ratio: string; net_loss_ratio: string
  }
  financial_year_to_date: { from: string; gwp: string | null; pat: string | null }
  basis: string
  source: string
}
export interface IntelSummary {
  generated_at: string
  period: { month: string; from: string; to: string }
  company: string
  month_posted: boolean
  errors: Record<string, string>
  policies: IntelPolicies | null
  premium: IntelPremium | null
  brain: {
    activated: boolean
    as_of: string | null
    note: string
    narrative: string
    their_active_total: number | null
    our_same_basis_total: number | null
    agree: 'green' | 'amber' | 'red'
    basis_note: string
  }
}
export const intelStaffSummary = (month?: string, company?: string) => {
  const q = new URLSearchParams()
  if (month) q.set('month', month)
  if (company) q.set('company', company)
  const qs = q.toString()
  return apiFetch<IntelSummary>(`/intel/staff-summary/${qs ? `?${qs}` : ''}`)
}

// ---------------------------------------------------------------------------
// Records register — physical files, storeroom and chain of custody
// ---------------------------------------------------------------------------

export interface RecordCategory {
  id: string
  name: string
  description: string
  retention_months: number | null
  active: boolean
}

export interface RecordItem {
  id: string
  reference: string
  title: string
  category: string
  category_name: string
  department: string
  confidentiality: 'public' | 'internal' | 'confidential' | 'restricted'
  status: 'in_store' | 'issued' | 'archived' | 'destroyed' | 'lost'
  current_holder: string | null
  holder_name: string
  current_custodian: string
  current_location: string
  held_by: string
  opened_on: string | null
  closed_on: string | null
  retention_until: string | null
  legal_hold: boolean
  legal_hold_note: string
  notes: string
  is_out: boolean
  /** Days the physical file has been out of the repository; null when it is in. */
  days_out: number | null
  out_since: string | null
  may_be_destroyed: boolean
}

export interface RecordMovement {
  id: string
  record: string
  kind: 'issue' | 'return' | 'transfer' | 'archive' | 'destroy'
  moved_at: string
  due_back_on: string | null
  reason: string
  from_name: string
  from_custodian: string
  from_location: string
  to_name: string
  to_custodian: string
  to_location: string
  recorded_by_name: string
  created_at: string
}

export async function getRecords(params?: {
  status?: string
  category?: string
  q?: string
  out?: string
}): Promise<PaginatedResponse<RecordItem> & {
  // Present only when the caller's role is not cleared for restricted personal
  // data, so the screen can say rows are withheld instead of looking empty.
  restricted_hidden?: number
  restricted_notice?: string
}> {
  const qs = _qs({
    status: params?.status,
    category: params?.category,
    q: params?.q,
    out: params?.out,
  })
  return apiFetch<PaginatedResponse<RecordItem> & {
    restricted_hidden?: number
    restricted_notice?: string
  }>(`/records/${qs}`)
}

export async function getRecordCategories(): Promise<PaginatedResponse<RecordCategory>> {
  return apiFetch<PaginatedResponse<RecordCategory>>('/record-categories/')
}

export async function getRecordMovements(
  id: string,
): Promise<{ record: string; movements: RecordMovement[] }> {
  return apiFetch(`/records/${id}/movements/`)
}

/** Issue, return, transfer, archive or destroy — one entry point, as the API has. */
export async function moveRecord(id: string, body: {
  kind: string
  moved_at: string
  to_employee?: string
  to_custodian?: string
  to_location?: string
  reason?: string
  due_back_on?: string
}): Promise<RecordMovement> {
  return apiFetch<RecordMovement>(`/records/${id}/move/`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export async function getOverdueRecords(): Promise<{ count: number; results: RecordItem[] }> {
  return apiFetch('/records/overdue/')
}

export async function getRecordsDueForDestruction(): Promise<{ count: number; results: RecordItem[] }> {
  return apiFetch('/records/due_for_destruction/')
}

export async function createRecord(body: {
  reference: string
  title: string
  category: string
  department?: string
  confidentiality?: string
  opened_on?: string
  retention_until?: string
  current_location?: string
  notes?: string
}): Promise<RecordItem> {
  return apiFetch<RecordItem>('/records/', {
    method: 'POST', body: JSON.stringify(body),
  })
}

export async function updateRecord(id: string, body: Partial<{
  title: string
  category: string
  confidentiality: string
  opened_on: string
  closed_on: string
  retention_until: string
  notes: string
}>): Promise<RecordItem> {
  return apiFetch<RecordItem>(`/records/${id}/`, {
    method: 'PATCH', body: JSON.stringify(body),
  })
}

/** Legal hold has its own endpoint: it is deliberately not PATCHable. */
export async function setRecordHold(id: string, body: {
  legal_hold: boolean
  legal_hold_note?: string
}): Promise<RecordItem> {
  return apiFetch<RecordItem>(`/records/${id}/hold/`, {
    method: 'POST', body: JSON.stringify(body),
  })
}

export async function createRecordCategory(body: {
  name: string
  retention_months?: number
}): Promise<RecordCategory> {
  return apiFetch<RecordCategory>('/record-categories/', {
    method: 'POST', body: JSON.stringify(body),
  })
}

// ── ADH → AFA member load file ───────────────────────────────────────────
export interface AfaRun {
  id: string
  runDate: string
  status: 'built' | 'released' | 'sent' | 'failed' | 'aborted'
  statusLabel: string
  rowCount: number
  newCount: number
  changedCount: number
  departureCount: number
  heldCount: number
  heldReasons: Record<string, number>
  fileName: string
  sha256: string
  sourceRowCount: number
  replicaLagSeconds: number | null
  abortReason: string
  builtAt: string | null
  releasedAt: string | null
  releasedBy: string
  sentAt: string | null
  sendError: string
  ackStatus: string
  autosendEnabled: boolean
  transferConfigured: boolean
  preview?: string[]
  previewTruncated?: boolean
  masked?: boolean
}
export interface AfaGroupMap {
  id: string
  employerGroupId: string
  graphiteName: string
  imedGroupName: string
  regionName: string
  isActive: boolean
  billingContactId: string | null
  confirmedBy: string
}

export async function getAfaRuns(): Promise<{ results: AfaRun[]; autosendEnabled: boolean; transferConfigured: boolean }> {
  return apiFetch('/health/afa/runs/')
}
export async function getAfaRun(id: string, unmasked = false): Promise<AfaRun> {
  return apiFetch(`/health/afa/runs/${id}/${unmasked ? '?unmasked=1' : ''}`)
}
export async function buildAfaRun(): Promise<AfaRun> {
  return apiFetch('/health/afa/runs/build/', { method: 'POST' })
}
export async function releaseAfaRun(id: string): Promise<AfaRun> {
  return apiFetch(`/health/afa/runs/${id}/release/`, { method: 'POST' })
}
export async function getAfaGroups(): Promise<{ results: AfaGroupMap[] }> {
  return apiFetch('/health/afa/groups/')
}
export async function saveAfaGroup(body: {
  employerGroupId: string; imedGroupName: string; graphiteName?: string
  regionName?: string; isActive?: boolean
}): Promise<{ id: string }> {
  return apiFetch('/health/afa/groups/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

// ── ADH claims EFT settlement runs (B4) ───────────────────────────────────
// Read-only. Four near-identical files land from AFA each Saturday and exactly
// one is processed; `notProcessed` carries the other three with the single
// condition each failed. No claimant names are in this payload.
export interface AdhSettlementReject {
  name: string
  reason: string
}
export interface AdhSettlementProblem {
  claim_number: string
  reason: string
}
export interface AdhSettlementRun {
  id: string
  loadedOn: string
  status: 'loaded' | 'partial' | 'nothing' | 'failed'
  statusLabel: string
  fileName: string
  sha256: string
  notProcessed: AdhSettlementReject[]
  lineCount: number
  createdCount: number
  skippedCount: number
  failedCount: number
  problems: AdhSettlementProblem[]
  error: string
  createdAt: string
}

export async function getAdhSettlementRuns(): Promise<{ runs: AdhSettlementRun[] }> {
  return apiFetch<{ runs: AdhSettlementRun[] }>('/health/adh-settlements/runs/')
}

// ── Quotations — one standard template, filled from plain English ──────────
// CFO/EXCO decision 2026-08-08: all underwriting quotes are produced in Omni on
// ONE template. VAT and the total are always computed server-side, never sent
// up from here, so a quote cannot leave with the VAT worked out a different way.

export interface QuoteSection {
  group?: string
  name: string
  note?: string
  sum_insured?: string
  basis?: string
  excess?: string
  rate?: string
  // Motor: whether the vehicle is Imported or Local (CFO 2026-08-12, Motlatsi
  // item 3). Optional and blank for non-motor cover; rides in the section JSON.
  origin?: string
  // Per-cover/section notes (Gomolemo Sebudula, 17 Aug 2026): benefits,
  // exclusions, extensions, conditions, warranties for this WHOLE section.
  // Set on the section's heading row; printed once under that section on the
  // quotation, separate from the quote's general Notes. Rides in the JSON.
  section_note?: string
}

export interface Quote {
  id: string
  quote_number: string
  version: number
  client_name: string
  client_attn: string
  class_of_business: string
  period: string
  broker: string
  agent?: string
  agent_email?: string
  sections: QuoteSection[]
  rate_pct?: string
  rate_incl_vat?: boolean
  notes?: string
  premium: string
  vat: string
  total: string
  premium_is_suggested: boolean
  valid_until: string | null
  status: string
  status_label: string
  underwriter_name?: string | null
  issued_by_name?: string | null
  issued_at?: string | null
  converted_policy_number?: string
  drafted_by_ai?: boolean
  pdf_size?: number
  // Server returns these on every quote (see QuoteSerializer). The frontend
  // needs company so a row-click on the register can pass ?company= to the
  // scoped retrieve endpoint — a quote may belong to a different entity than
  // the one selected in the topbar.
  company?: string | null
  company_code?: string | null
  created_at: string
}

export interface QuoteDraftResult {
  ok: boolean
  source: string
  draft: {
    client_name: string
    client_attn: string
    class_of_business: string
    period: string
    broker: string
    sections: QuoteSection[]
  }
  premium: string
  vat: string
  vat_rate_pct: string
  total: string
  premium_is_suggested: boolean
  premium_basis: string
  warnings: string[]
}

/** The ask box: plain English in, a filled draft back. Nothing is saved yet. */
export async function draftQuoteFromText(text: string): Promise<QuoteDraftResult> {
  return apiFetch<QuoteDraftResult>('/underwriting/quotes/draft-from-text/', {
    method: 'POST',
    body: JSON.stringify({ text }),
  })
}

export async function getQuotes(params?: Record<string, string>): Promise<PaginatedResponse<Quote>> {
  const q = new URLSearchParams(params).toString()
  return apiFetch<PaginatedResponse<Quote>>(`/underwriting/quotes/${q ? `?${q}` : ''}`)
}

// Manus QC 14-Aug-2026: the register listed quotes but no row was clickable
// to open one for preview or download. This fetches a single quote so the
// builder above can be re-populated with it — same download panel then works
// against the row you picked.
//
// The retrieve endpoint is CompanyScoped and only returns rows for the topbar
// entity; the register list is broader and can show rows from another entity
// the user has access to (verified on prod 15-Aug: register showed Lulu at
// company 05b8f627..., but retrieve with ?company=ADIC returned 404). Pass the
// quote's OWN company explicitly so we scope to where the row actually lives.
export async function getQuote(id: string, company?: string | null): Promise<Quote> {
  const path = company
    ? `/underwriting/quotes/${id}/?company=${encodeURIComponent(company)}`
    : `/underwriting/quotes/${id}/`
  return apiFetch<Quote>(path)
}

export interface QuoteRateFloor { class_of_business: string; min_rate_pct: string }

export async function getQuoteRateFloors(): Promise<QuoteRateFloor[]> {
  return apiFetch<QuoteRateFloor[]>('/underwriting/quotes/rate-floors/')
}

export interface QuotePolicyLookup {
  found: boolean
  error?: string
  policy_number?: string
  client_name?: string
  class_of_business?: string
  prior_annual_premium?: string
  sum_insured?: string
  claims?: { count: number; reserve: string; paid: string }
}

export async function loadQuoteFromPolicy(policy: string): Promise<QuotePolicyLookup> {
  return apiFetch<QuotePolicyLookup>(`/underwriting/quotes/from-policy/?policy=${encodeURIComponent(policy)}`)
}

export interface QuoteTemplateOption {
  id: number
  name: string
  class_of_business: string
  sections: QuoteSection[]
  rate_pct: string
  rate_incl_vat: boolean
}

export async function getQuoteTemplates(): Promise<QuoteTemplateOption[]> {
  return apiFetch<QuoteTemplateOption[]>('/underwriting/quotes/templates/')
}

export interface LearnedExclusion { text: string; used: number }

/** Exclusions most used for a class of business, learned from past quotes. */
export async function getLearnedExclusions(cls: string): Promise<LearnedExclusion[]> {
  return apiFetch<LearnedExclusion[]>(
    `/underwriting/quotes/exclusions/?class=${encodeURIComponent(cls)}`)
}

export async function createQuote(payload: Partial<Quote>): Promise<Quote> {
  // The entity has to ride ON THE POST. apiFetch only auto-appends ?company= to
  // GETs, so without this the server resolves no company and refuses with "pick
  // the company at the top of the screen" — an instruction the user cannot act
  // on, because the topbar selection was never sent. Fable caught this: every
  // create failed, for every kind of user.
  const company = selectedCompanyId()
  const path = company
    ? `/underwriting/quotes/?company=${encodeURIComponent(company)}`
    : '/underwriting/quotes/'
  return apiFetch<Quote>(path, { method: 'POST', body: JSON.stringify(payload) })
}

/** Open an issued quotation's PDF. The endpoint needs the auth header, so a
 *  plain <a href> gets a 401 — fetch it and hand the browser a blob instead. */
export async function openQuotePdf(id: string): Promise<void> {
  const res = await apiFetchBinary(`/underwriting/quotes/${id}/pdf/`)
  if (!res.ok) throw new Error('Could not open the quotation PDF.')
  const url = URL.createObjectURL(await res.blob())
  window.open(url, '_blank', 'noopener')
  setTimeout(() => URL.revokeObjectURL(url), 60_000)
}

/** Download the quotation as PDF, Excel or Word, detailed or simplified.
 *  Uses a real download anchor, not window.open(blob:) — a blob URL opened in a
 *  new tab is blocked in some browsers and shows an empty tab (house rule). */
export async function downloadQuoteFile(
  id: string, format: 'pdf' | 'xlsx' | 'docx', style: 'detailed' | 'simple', filename: string,
): Promise<void> {
  // `fmt`, NOT `format`: `format` is DRF's reserved content-negotiation param,
  // so `?format=xlsx` 404'd before the endpoint ran — every Excel/Word download
  // failed (proven on prod 2026-08-12, Motlatsi item 2). `?fmt=` reaches the code.
  const res = await apiFetchBinary(
    `/underwriting/quotes/${id}/export/?fmt=${format}&style=${style}`)
  if (!res.ok) throw new Error('Could not build that file.')
  const url = URL.createObjectURL(await res.blob())
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 60_000)
}

export async function updateQuote(id: string, payload: Partial<Quote>): Promise<Quote> {
  return apiFetch<Quote>(`/underwriting/quotes/${id}/`, {
    method: 'PATCH', body: JSON.stringify(payload),
  })
}

/** Accept (or replace) a premium Aria suggested. Until this runs, issue refuses. */
export async function confirmQuotePremium(id: string, premium?: string): Promise<Quote> {
  return apiFetch<Quote>(`/underwriting/quotes/${id}/confirm-premium/`, {
    method: 'POST', body: JSON.stringify(premium ? { premium } : {}),
  })
}

export async function issueQuote(id: string): Promise<Quote> {
  return apiFetch<Quote>(`/underwriting/quotes/${id}/issue/`, { method: 'POST' })
}

export async function setQuoteOutcome(
  id: string, result: 'won' | 'lost', policyNumber?: string,
): Promise<Quote> {
  return apiFetch<Quote>(`/underwriting/quotes/${id}/outcome/`, {
    method: 'POST',
    body: JSON.stringify({ result, policy_number: policyNumber || '' }),
  })
}

// ── Claim Forms Vault (CFO 2026-08-12) ────────────────────────────────────
// Omni's reference library of every blank claim form. Blank templates only,
// no PII. Downloads go through apiFetchBinary so the bearer/legacy token is
// attached — a plain <a href> to /media would 401 (and /media is not served).

export interface ClaimFormVaultItem {
  id: string
  slug: string
  title: string
  category: string
  categoryLabel: string
  description: string
  active: boolean
  sizeKb: number | null
  uploadedBy: string
  updatedAt: string | null
  downloadUrl: string
}

export interface ClaimFormVault {
  data: ClaimFormVaultItem[]
  summary: {
    total: number
    byCategory: Record<string, number>
    categories: { key: string; label: string }[]
  }
}

export async function getClaimForms(params?: { category?: string }): Promise<ClaimFormVault> {
  const q = params?.category ? `?category=${encodeURIComponent(params.category)}` : ''
  return apiFetch<ClaimFormVault>(`/claim-forms/${q}`)
}

/** Download a claim form as a blob and save it, with the auth token attached. */
export async function downloadClaimForm(item: ClaimFormVaultItem): Promise<void> {
  const res = await apiFetchBinary(item.downloadUrl)
  if (!res.ok) throw new Error(`Download failed (${res.status})`)
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${item.slug}.pdf`
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}


// ===========================================================================
// FX Payment Planning (/api/v1/fx-planning/...) — forward forecast + calendar
// for the USD/ZAR foreign payments captured on FNB. CFO 2026-08-24.
// ===========================================================================

export interface FxCcyTotal {
  amount: string; bwp: string; count: number
  bwp_stressed?: Record<string, string>   // { "5": "...", "10": "..." } — worst-case pula
}

export interface FxPlannedLine {
  id: string
  beneficiary: string
  currency: string
  expected_amount: string
  expected_value_date: string
  source_account: string
  driver: 'recurring' | 'reinsurance' | 'claim' | 'manual'
  status: 'planned' | 'requested' | 'paid' | 'skipped'
  estimated_rate: string | null
  estimated_bwp: string | null
  rate_is_estimate: boolean
  payment_request: string | null
  payment_request_ref: string | null
  notes: string
}

export interface FxWeek {
  week_start: string
  label: string
  by_currency: Record<string, FxCcyTotal>
  lines: FxPlannedLine[]
}

export interface FxAccountTotal {
  account: string; currency: string; amount: string; bwp: string; count: number
  bwp_stressed?: Record<string, string>
}

export interface FxCalendar {
  generated_at: string
  today: string
  weeks_ahead: number
  currencies: string[]
  stress_pcts: string[]
  totals: Record<string, FxCcyTotal>
  bwp_total: string
  bwp_total_stressed: Record<string, string>
  by_account: FxAccountTotal[]
  weeks: FxWeek[]
}

export interface FxPayee {
  id: string
  display_name: string
  beneficiary_key: string
  currency: string
  typical_amount: string
  cadence: 'monthly' | 'quarterly' | 'irregular'
  typical_day: number | null
  source_account: string
  occurrences: number
  months_active: number
  confidence: number
  last_seen: string | null
  active: boolean
  watch: boolean
  confirmed: boolean
  notes: string
  amount_min: string | null
  amount_max: string | null
  amount_varies: boolean
}

export interface FxBacktestPeriod {
  year: number
  month: number
  label: string
  predicted_count: number
  actual_count: number
  predicted_bwp: string
  actual_bwp: string
  matched_payees: number
  accuracy_pct: number
}

export interface FxBacktest {
  today: string
  months_back: number
  overall_accuracy_pct: number
  has_data: boolean
  periods: FxBacktestPeriod[]
}

export interface FxImportResult {
  import: { id: string; filename: string; row_count: number; imported_count: number;
            date_from: string | null; date_to: string | null }
  payees_detected: number
  planned_created: number
}

export function getFxCalendar(weeks = 12): Promise<FxCalendar> {
  return apiFetch<FxCalendar>(`/fx-planning/calendar/?weeks=${weeks}`)
}

export function getFxPayees(): Promise<FxPayee[]> {
  return apiFetch<FxPayee[]>('/fx-planning/payees/')
}

export function getFxBacktest(months = 3): Promise<FxBacktest> {
  return apiFetch<FxBacktest>(`/fx-planning/backtest/?months=${months}`)
}

export function updateFxPayee(id: string, patch: Partial<FxPayee>): Promise<FxPayee> {
  return apiFetch<FxPayee>(`/fx-planning/payees/${id}/`, {
    method: 'PATCH', body: JSON.stringify(patch),
  })
}

export function rebuildFxForecast(): Promise<{ payees_detected: number; planned_created: number }> {
  return apiFetch('/fx-planning/rebuild/', { method: 'POST', body: '{}' })
}

export async function importFxFile(file: File): Promise<FxImportResult> {
  const form = new FormData()
  form.append('file', file)
  const res = await apiFetchBinary(`${API_BASE}/fx-planning/import/`, {
    method: 'POST', body: form, headers: {},
  })
  if (!res.ok) {
    const txt = await res.text().catch(() => '')
    throw new Error(`Import failed (${res.status}): ${txt.slice(0, 200)}`)
  }
  return await res.json()
}

export function createFxPlanned(body: {
  beneficiary: string; currency: string; expected_amount: string;
  expected_value_date: string; source_account?: string; notes?: string
}): Promise<FxPlannedLine> {
  return apiFetch<FxPlannedLine>('/fx-planning/planned/', {
    method: 'POST', body: JSON.stringify(body),
  })
}

export function updateFxPlanned(id: string, patch: Partial<FxPlannedLine>): Promise<FxPlannedLine> {
  return apiFetch<FxPlannedLine>(`/fx-planning/planned/${id}/`, {
    method: 'PATCH', body: JSON.stringify(patch),
  })
}

export function deleteFxPlanned(id: string): Promise<void> {
  return apiFetch(`/fx-planning/planned/${id}/`, { method: 'DELETE' })
}

export function raiseFxPlanned(id: string): Promise<{
  payment_request_id: string; payment_request_ref: string; line: FxPlannedLine
}> {
  return apiFetch(`/fx-planning/planned/${id}/raise/`, { method: 'POST', body: '{}' })
}

// ─── Morning-brief note spaces (CFO 2026-09-10) ──────────────────────────────
// One short note per person per morning, written on the dashboard and carried
// into the CEO / CFO morning brief. The space itself says what this person may
// do (can_post / shared), so the card self-gates and never asks a role here.

export interface BriefNote {
  id: string
  author: string
  author_username: string
  is_mine: boolean
  body: string
  words: number
  status: string
  for_date: string
  locked: boolean
  created_at: string
}

export interface BriefNoteAccess {
  audiences: string[]
  can_post_ceo: boolean
  can_post_cfo: boolean
  word_limit: number
  next_brief_date: string
}

export interface BriefNoteSpace {
  audience: string
  for_date: string
  shared: boolean
  /** You may read every note in this space (the space owner). */
  can_read_all: boolean
  can_post: boolean
  word_limit: number
  my_note: BriefNote | null
  notes: BriefNote[]
}

export function getBriefNoteAccess(): Promise<BriefNoteAccess> {
  return apiFetch<BriefNoteAccess>('/brief-notes/access/')
}

export function getBriefNoteSpace(audience: 'ceo' | 'cfo'): Promise<BriefNoteSpace> {
  return apiFetch<BriefNoteSpace>(`/brief-notes/?audience=${audience}`)
}

// 201 on a first note, 200 when it replaces one already written for the same
// morning — either way the saved note comes back.
export function writeBriefNote(audience: 'ceo' | 'cfo', body: string): Promise<BriefNote> {
  return apiFetch<BriefNote>('/brief-notes/', {
    method: 'POST', body: JSON.stringify({ audience, body }),
  })
}

export function withdrawBriefNote(id: string): Promise<{ withdrawn: boolean; id: string }> {
  return apiFetch(`/brief-notes/${id}/`, { method: 'DELETE' })
}

// ─── Statutory tax compliance (Tax Calendar) ──────────────────────────────────
// Dates are computed on the SERVER (regulatory/tax_calendar.py) and never in the
// browser. The old page computed its own deadlines in TypeScript and every one
// of them was wrong under the 2026 Acts — so there is deliberately no date
// arithmetic on this side any more.

export interface TaxPerson { id: number; name: string; email: string }

export type TaxTaskStatus =
  | 'scheduled' | 'reminding' | 'preparer_complete' | 'verified' | 'late' | 'breach'

export interface TaxComplianceTask {
  id: string
  obligation_key: string
  tax_type: string
  tax_type_label: string
  label: string
  period_label: string
  period_start: string
  period_end: string
  due_date: string
  target_date: string
  status: TaxTaskStatus
  status_display: string
  owner_detail: TaxPerson | null
  completed_at: string | null
  completion_note: string
  verified_at: string | null
  verified_note: string
  late_reason: string
  breach_note: string
  reminder_count: number
  days_to_due: number
  is_open: boolean
  original_due_date: string | null
  date_change_reason: string
  date_changed_at: string | null
  date_changed_by_name: string | null
}

export interface TaxCalendarResponse {
  today: string
  can_verify: boolean
  can_edit_dates: boolean
  vat_cycle: string
  summary: {
    total: number
    open: number
    breach: number
    reminding: number
    awaiting_cfo: number
    unassigned: number
  }
  tasks: TaxComplianceTask[]
}

export async function getTaxCalendar(months = 12): Promise<TaxCalendarResponse> {
  return apiFetch<TaxCalendarResponse>(`/tax-compliance/calendar/?months=${months}`)
}

export async function markTaxTaskComplete(id: string, note: string): Promise<TaxComplianceTask> {
  return apiFetch<TaxComplianceTask>(`/tax-compliance/tasks/${id}/complete/`, {
    method: 'POST',
    body: JSON.stringify({ note }),
  })
}

export async function verifyTaxTask(
  id: string, note: string, lateReason = '',
): Promise<TaxComplianceTask> {
  return apiFetch<TaxComplianceTask>(`/tax-compliance/tasks/${id}/verify/`, {
    method: 'POST',
    body: JSON.stringify({ note, late_reason: lateReason }),
  })
}

export async function closeTaxBreach(id: string, breachNote: string): Promise<TaxComplianceTask> {
  return apiFetch<TaxComplianceTask>(`/tax-compliance/tasks/${id}/close-breach/`, {
    method: 'POST',
    body: JSON.stringify({ breach_note: breachNote }),
  })
}

// Move a statutory date. Open to the CFO (superuser) and ONLY the people named
// on /tax-compliance/date-editors/ (Oprah, Kago, Legakwa). Job title grants
// nothing on its own. The reason is
// mandatory and a breach can never be undone this way — the server enforces both.
export async function changeTaxDueDate(
  id: string, dueDate: string, reason: string,
): Promise<TaxComplianceTask> {
  return apiFetch<TaxComplianceTask>(`/tax-compliance/tasks/${id}/change-date/`, {
    method: 'POST',
    body: JSON.stringify({ due_date: dueDate, reason }),
  })
}

// ── Large Payment Authorisation (CFO 2026-09-11) ─────────────────────────────
// The "request large claim payment" button. Reads claim payments that have
// already been raised, signed off and loaded to FNB through the ordinary gated
// path. It creates no payment and amends none.
export interface LargePaymentCandidate {
  payment_request_id: string
  ref: string
  subject: string
  payee: string
  amount: string
  claim_number: string
  status: string
  fnb_loaded_at: string | null
  over_threshold: boolean
}

export interface LargePaymentFlag { code: string; message: string }

export interface LargePaymentLine {
  id: number
  payment_request_id: string
  payment_ref: string
  claim_number: string
  payee: string
  amount: string
  enrich_status: 'pending' | 'ok' | 'not_found' | 'no_claim' | 'error'
  insured_name: string
  policy_number: string
  claim_status: string
  claim_sub_status: string
  date_of_loss: string | null
  loss_description: string
  reserve_amount: string | null
  paid_amount: string | null
  flags: LargePaymentFlag[]
}

export interface LargePaymentRequest {
  id: string
  ref: string
  title: string
  status: 'enriching' | 'enrich_failed' | 'pending_cfo' | 'approved' | 'rejected'
        | 'sent_to_ceo' | 'question' | 'ceo_approved' | 'ceo_rejected' | 'cancelled'
  status_label: string
  threshold: string
  total: string
  progress_pct: number
  progress_note: string
  enrich_error: string
  line_count: number
  flagged_count: number
  raised_by: string
  created_at: string
  updated_at: string
  /** True when a collection has not moved for a while — offer Try again. */
  stalled: boolean
  decided_at: string | null
  decision_note: string
  cfo_task_id: string | null
  lines?: LargePaymentLine[]
  note_to_user?: string
  /** Piece 2 — the CEO leg. */
  sent_to_ceo_at?: string | null
  /** Who the send ACTUALLY reached, read back from the send, not assumed. */
  sent_recipients?: string[]
  send_error?: string
  send_failed?: boolean
  ceo_decided_at?: string | null
  ceo_questions?: { asked_at: string; of: string; name: string }[]
}

/** Send the CEO's authorisation email again after a failed or lost send. */
export async function resendLargePaymentToCeo(id: string) {
  return apiFetch<LargePaymentRequest>(`/large-payments/${id}/resend/`, { method: 'POST' })
}

export async function largePaymentCandidates(threshold?: string) {
  const qs = threshold ? `?threshold=${encodeURIComponent(threshold)}` : ''
  return apiFetch<{ threshold: string; count: number; ticked: number;
                    rows: LargePaymentCandidate[] }>(`/large-payments/candidates/${qs}`)
}

export async function largePaymentList() {
  return apiFetch<{ results: LargePaymentRequest[] }>('/large-payments/')
}

export async function createLargePaymentRequest(
  paymentRequestIds: string[], title: string,
) {
  return apiFetch<LargePaymentRequest>('/large-payments/', {
    method: 'POST',
    body: JSON.stringify({ payment_request_ids: paymentRequestIds, title }),
    headers: { 'Content-Type': 'application/json' },
  })
}

export async function largePaymentDetail(id: string) {
  return apiFetch<LargePaymentRequest>(`/large-payments/${id}/`)
}

export async function retryLargePaymentEnrich(id: string) {
  return apiFetch<LargePaymentRequest>(`/large-payments/${id}/retry/`, { method: 'POST' })
}

export async function decideLargePayment(
  id: string, decision: 'approve' | 'reject', note: string,
) {
  return apiFetch<LargePaymentRequest>(`/large-payments/${id}/decide/`, {
    method: 'POST',
    body: JSON.stringify({ decision, note }),
    headers: { 'Content-Type': 'application/json' },
  })
}

// ── Failed debits: the weekly chase list + the recipient lists Finance keep ──
// CFO 2026-09-11. The recipient endpoints are keyed by report slug, so the same
// screen also edits the distribution lists of the daily monitoring reports that
// until now lived in a Python dictionary and needed a deploy to change.

export interface ReportRecipientRow {
  id: string
  report_slug: string
  email: string
  name: string
  kind: 'to' | 'cc'
  active: boolean
  added_by: string
  created_at: string | null
}

export interface ReportRecipientList {
  report_slug: string
  report_label: string
  reports: { slug: string; label: string }[]
  results: ReportRecipientRow[]
}

export interface FailedDebitAgentRow {
  agent: string
  count: number
  amount: string
}

export interface FailedDebitRow {
  policy_number: string
  action_date: string
  amount: string
  retry_count: number
  reason: string
  policy_status: string
  premium: string
  agency: string
  agent: string
  bucket: string
}

export interface FailedDebitsPreview {
  available: boolean
  detail?: string
  days?: number
  start?: string
  end?: string
  count?: number
  amount?: string
  non_active?: number
  repeat?: number
  by_agent?: FailedDebitAgentRow[]
  recipients_to?: string[]
  recipients_cc?: string[]
  rows?: FailedDebitRow[]
}

export async function getFailedDebitsPreview(
  days?: number,
): Promise<FailedDebitsPreview> {
  const q = days ? `?days=${days}` : ''
  return apiFetch<FailedDebitsPreview>(`/realpay/failed-debits/preview/${q}`)
}

export async function listReportRecipients(
  slug: string,
): Promise<ReportRecipientList> {
  return apiFetch<ReportRecipientList>(
    `/report-recipients/?report_slug=${encodeURIComponent(slug)}`)
}

export async function addReportRecipient(
  slug: string, email: string, name: string, kind: 'to' | 'cc',
): Promise<ReportRecipientRow> {
  return apiFetch<ReportRecipientRow>('/report-recipients/', {
    method: 'POST',
    body: JSON.stringify({ report_slug: slug, email, name, kind }),
  })
}

export async function setReportRecipientActive(
  id: string, active: boolean,
): Promise<ReportRecipientRow> {
  return apiFetch<ReportRecipientRow>(`/report-recipients/${id}/`, {
    method: 'PATCH',
    body: JSON.stringify({ active }),
  })
}

// ── Payments exception cockpit ───────────────────────────────────────────────
// Where Omni's own payment status disagrees with what FNB says about the batch,
// plus the batches the bank has not settled. Read only — nothing on this screen
// changes a payment, and Omni never moves money.

export interface CockpitRow {
  ref: string
  entity: string
  subject: string
  payee: string
  total: string
  omni_status: string
  batch_status: string
  batch_key: string
  fnb_reference: string
  failure_reason: string
  age_days: number | null
}

export interface CockpitBatchRow {
  key: string
  payments: number
  total: string
  age_days: number | null
  owner: string
  fnb_reference: string
  last_word_from_fnb: string
}

export interface CockpitBatchGroup {
  count: number
  total: string
  over_threshold: number
  rows: CockpitBatchRow[]
}

// One payment request whose bank instructions were ALL rejected while the
// request itself is closed (Paid / Pending CFO / Cancelled). Grouped per
// request on purpose: a supplier request processed line-by-line becomes one
// FNB instruction per line, and four rejects from one supplier are one
// business problem, not four.
export interface CockpitRejectedRow {
  ref: string
  entity: string
  payee: string
  total: string
  omni_status: string
  processing_method: string
  bank_reasons: string[]
  evidence_recorded: boolean
  instructions: {
    batch_key: string
    batch_status: string
    amount: string
    age_days: number | null
    failure_reason: string
  }[]
}

export interface PaymentExceptionCockpit {
  checked_at: string
  clean: boolean
  contradiction_count: number
  contradictions: Record<string, CockpitRow[]>
  findings_meaning: Record<string, { headline: string; what_it_means: string }>
  batches: { stale_days: number; groups: Record<string, CockpitBatchGroup> }
  rejected_not_open: {
    rows: CockpitRejectedRow[]
    count: number
    instruction_count: number
    total: string
    note: string
  }
  notifications: {
    stuck: number
    failed: number
    stuck_hours: number
    backlog: number
    backlog_before: string
    backlog_note: string
  }
}

export async function getPaymentExceptionCockpit(
  staleDays?: number,
): Promise<PaymentExceptionCockpit> {
  const qs = staleDays ? `?stale_days=${staleDays}` : ''
  return apiFetch<PaymentExceptionCockpit>(`/fnb/exception-cockpit/${qs}`)
}

// ─── VAT reconciliation (build spec B2) ──────────────────────────────────────
// Output VAT, input VAT, the net payable or refundable, and the tie-out to the
// general ledger. READ-ONLY: the server posts nothing and changes no GL mapping.

export interface VatReconException {
  code: string
  severity: 'error' | 'warning'
  reference: string
  message: string
  expected: string | null
  actual: string | null
  difference: string | null
}

export interface VatReconLine {
  reference: string
  party: string
  doc_date: string
  net_amount: string
  vat_amount: string
  side: 'output' | 'input'
  kind: string
}

export interface VatReconResponse {
  period_start: string
  period_end: string
  vat_rate: string
  subledger: {
    output_net: string
    output_vat: string
    input_net: string
    input_vat: string
    net_vat: string
  }
  ledger: {
    output_vat: string
    input_vat: string
    net_vat: string
  }
  tie_out: {
    output_difference: string
    input_difference: string
    net_difference: string
    reconciled: boolean
  }
  position: 'payable' | 'refundable' | 'nil'
  amount_due: string
  due_date: string
  prepare_by_date: string
  exceptions: VatReconException[]
  lines: VatReconLine[]
  posts_nothing: boolean
}

export async function getVatReconciliation(
  year: number, month: number, companyId?: number | string,
): Promise<VatReconResponse> {
  const qs = new URLSearchParams({ year: String(year), month: String(month) })
  if (companyId) qs.set('company_id', String(companyId))
  return apiFetch<VatReconResponse>(`/tax-compliance/vat-recon/?${qs.toString()}`)
}

/* ── Reinsurer Controls, Security Oversight & FAC Risk Register ─────────────
 * Arun P. Iyer's control brief via the CFO, 15-Sep-2026.
 *
 * Read these types literally: a `string | null` money field is null when the
 * value is genuinely UNKNOWN, and the screens render that as "unknown" rather
 * than as zero. Four of the supplied security panels state no share at all, and
 * showing those as 0% would read as "no exposure".
 */

export interface ReinsurerSummary {
  id: string
  name: string
  short_code: string
  legal_name: string | null
  trading_name: string | null
  carrier_group: string | null
  domicile: string | null
  regulator: string | null
  broker: string | null
  credit_rating: string | null
  onboarding_purposes: string[]
  approval_status: string
  approval_status_label: string
  is_active: boolean
  effective_date: string | null
  expiry_date: string | null
  next_review_date: string | null
  is_expired: boolean
  may_be_placed: boolean
  placement_block_reason: string | null
  suspension_reason: string | null
}

export interface ReinsurerAction {
  target: string
  label: string
  allowed: boolean
  blocked_because: string | null
  reason_required: boolean
}

export interface ReinsuranceControlCentre {
  as_of: string
  counterparties: {
    total: number
    approved: number
    pending: number
    blocked: number
    draft: number
    by_status: Record<string, number>
  }
  evidence_gaps: {
    no_security_assessment: number
    rating_not_verified: number
    national_scale_only: number
    approval_expiring_60d: number
    note: string
  }
  fac_exposure: {
    active_count: number
    expired_count: number
    // Money never crosses a currency (reinsurance control QC, 16-Sep-2026:
    // BWP and USD were being added together and shown as one BWP total).
    // Read by_currency. The four flat figures below carry the real amount ONLY
    // when `currencies` holds exactly one entry; the moment there are two they
    // are null, so nothing can render a cross-currency sum by accident.
    currencies: string[]
    mixed_currency: boolean
    by_currency: {
      currency: string
      active_count: number
      active_placed: string
      active_sum_insured: string
      retained_unplaced: string
      expired_count: number
      expired_placed: string
    }[]
    active_placed: string | null
    active_sum_insured: string | null
    retained_unplaced: string | null
    expired_placed: string | null
    note: string
  }
  // The queue rows carry their own actions, so the screen can show the exact
  // buttons this user may press — and say why the others are refused.
  queue: (ReinsurerSummary & { available_actions: ReinsurerAction[] })[]
}

export interface FacAllocation {
  id: string
  reinsurer: string
  reinsurer_id: string
  short_code: string
  share_percent: string | null
  allocated_amount: string | null
  allocated_premium: string | null
  commission_amount: string | null
  slip_reference: string | null
  signed_date: string | null
  counterparty_approved: boolean
}

export interface FacExposure {
  id: string
  reference: string
  policy_number: string | null
  insured_name: string | null
  regulatory_class: string | null
  currency_code: string
  gross_sum_insured: string | null
  gross_premium: string | null
  net_retention: string | null
  autofac_capacity: string | null
  fac_placed_amount: string | null
  unplaced_retained_amount: string | null
  ceded_premium: string | null
  ceded_commission: string | null
  placement_date: string | null
  effective_date: string | null
  expiry_date: string | null
  status: string
  status_label: string
  status_overridden: boolean
  status_override_reason: string | null
  is_active: boolean
  is_expired: boolean
  allocations?: FacAllocation[]
  allocation_variance?: string | null
  import_warnings?: string[]
}

export interface FacRiskRegister {
  as_of: string
  state: string
  /** The whole matched set, not just this page. */
  count: number
  /** How many rows this page actually carries. */
  shown: number
  page: number
  page_size: number
  has_next: boolean
  truncated: boolean
  truncated_note: string | null
  results: FacExposure[]
  by_reinsurer: {
    reinsurer: string
    short_code: string
    carrier_group: string | null
    approved: boolean
    active_count: number
    expired_count: number
    // Per currency, never added across them — see the note on
    // ReinsuranceControlCentre.fac_exposure. active_amount/expired_amount are
    // null whenever mixed_currency is true.
    currencies: string[]
    mixed_currency: boolean
    by_currency: {
      currency: string
      active_count: number
      active_amount: string
      expired_count: number
      expired_amount: string
    }[]
    active_amount: string | null
    expired_amount: string | null
  }[]
  retained_unplaced_by_currency: { currency: string; amount: string }[]
  /** Null when more than one currency is retained. */
  retained_unplaced: string | null
  retained_note: string
}

export interface SecurityPanelRow {
  reinsurer: string
  short_code: string
  carrier_group: string | null
  domicile: string | null
  approval_status: string
  approved: boolean
  rating: string | null
  rating_scale: string | null
  rating_verified: boolean
  internal_tier: string | null
  participation_share: string | null
  fac_allocations: number
  fac_amount: string | null
  has_assessment: boolean
}

export interface SecurityPanelResponse {
  as_of: string
  panel: SecurityPanelRow[]
  concentration: {
    group: string
    carriers: number
    fac_amount: string | null
    unapproved: number
    share_of_fac: number | null
  }[]
  total_fac_amount: string | null
  scale_warning: string
}

export async function getReinsuranceControlCentre(): Promise<ReinsuranceControlCentre> {
  return apiFetch<ReinsuranceControlCentre>('/reinsurance/controls/')
}

export async function getFacRiskRegister(params?: {
  state?: string; regulatory_class?: string; currency?: string; q?: string
}): Promise<FacRiskRegister> {
  const qs = new URLSearchParams()
  Object.entries(params ?? {}).forEach(([k, v]) => { if (v) qs.set(k, v) })
  return apiFetch<FacRiskRegister>(`/reinsurance/fac-risk/?${qs.toString()}`)
}

export async function getReinsuranceSecurityPanel(): Promise<SecurityPanelResponse> {
  return apiFetch<SecurityPanelResponse>('/reinsurance/security-panel/')
}

/** What Underwriting types when raising a new counterparty. Everything else on
 *  the record is set by the workflow, by compliance, or by the KYC register. */
export interface NewReinsurer {
  name: string
  short_code: string
  country?: string
  legal_name?: string
  trading_name?: string
  carrier_group?: string
  domicile?: string
  registration_number?: string
  licence_number?: string
  regulator?: string
  broker?: string
  credit_rating?: string
  address?: string
  tax_id?: string
  notes?: string
  onboarding_purposes?: string[]
}

/** Raise a new counterparty. It lands as a DRAFT and is NOT placeable — it has
 *  to walk the onboarding chain first. The server enforces that; this only
 *  carries the form. */
export async function createReinsurer(
  body: NewReinsurer,
): Promise<ReinsurerSummary> {
  return apiFetch<ReinsurerSummary>('/reinsurance/counterparties/new/',
    { method: 'POST', body: JSON.stringify(body) })
}

/** Move a counterparty along the onboarding chain. The server decides whether
 *  it is allowed — this only carries the request. */
export async function transitionReinsurer(
  id: string, target: string, comment?: string,
): Promise<ReinsurerSummary> {
  return apiFetch<ReinsurerSummary>(`/reinsurance/counterparties/${id}/transition/`,
    { method: 'POST', body: JSON.stringify({ target, comment: comment ?? '' }) })
}
