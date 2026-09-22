'use client'

import { useEffect, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  getBankAccounts,
  getBankStatements,
  getBankStatement,
  importBankStatement,
  runMatching,
  getPayments,
  matchStatementLine,
  getBankReconciliation,
  getToken,
  apiFetch,
} from '@/lib/api'
import type {
  BankAccount,
  BankStatement,
  BankStatementDetail,
  BankStatementLine,
  BankReconciliationReport,
  Payment,
  MatchExplanation,
  MatchDryRunResponse,
} from '@/lib/api'

interface MatchSuggestion {
  payment_id: string
  payment_number: string
  payee: string
  amount: string
  score: number
  deterministic_confidence: number
  memory_hits: number
  explanation: string
}
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { StatusBadge } from '@/components/ui/badge'
import { Modal, ModalBody, ModalFooter } from '@/components/ui/modal'
import { LoadingTable, LoadingSpinner } from '@/components/ui/loading'
import { FormField, Select } from '@/components/ui/input'
import { SearchableSelect } from '@/components/ui/SearchableSelect'
import { formatAmount, formatDate, parseAmount, cn } from '@/lib/utils'
import { useNumberFormat } from '@/contexts/NumberFormatContext'
import {
  Upload,
  Zap,
  Landmark,
  AlertCircle,
  CheckCircle,
  RefreshCw,
  Link2,
} from 'lucide-react'

// ─── Bank Reconciliation Page ─────────────────────────────────────────────────

export default function BankingPage() {
  const router = useRouter()
  const { mode } = useNumberFormat()
  const fmt = (amount: string | number, currency = 'BWP') => formatAmount(amount, currency, mode)

  const [bankAccounts, setBankAccounts] = useState<BankAccount[]>([])
  const [bankAccountsLoading, setBankAccountsLoading] = useState(true)
  const [statements, setStatements] = useState<BankStatement[]>([])
  const [statementsLoading, setStatementsLoading] = useState(false)
  const [selectedStatement, setSelectedStatement] = useState<BankStatementDetail | null>(null)
  const [statementLoading, setStatementLoading] = useState(false)
  const [matching, setMatching] = useState(false)
  const [matchResult, setMatchResult] = useState<{ matched: number; unmatched: number } | null>(null)
  const [showUpload, setShowUpload] = useState(false)
  const [uploadBankAccount, setUploadBankAccount] = useState('')
  const [uploadFile, setUploadFile] = useState<File | null>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [showMatchModal, setShowMatchModal] = useState(false)
  const [matchingLine, setMatchingLine] = useState<BankStatementLine | null>(null)
  const [unmatchedPayments, setUnmatchedPayments] = useState<Payment[]>([])
  const [selectedPaymentId, setSelectedPaymentId] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // CFO M6: dry-run explanation + confirmation
  const [matchExplanation, setMatchExplanation] = useState<MatchExplanation | null>(null)
  const [loadingExplanation, setLoadingExplanation] = useState(false)
  const [confirmChecked, setConfirmChecked] = useState(false)
  const latestPick = useRef('')
  // L-BANKAI: suggestions learned from earlier confirmed matches. A chip only
  // PRE-SELECTS a payment; the dry-run preview, the tick and Confirm still decide.
  const [suggestions, setSuggestions] = useState<MatchSuggestion[]>([])
  // Date filter over the transaction lines — lets you view only the transactions
  // in a chosen day or date range (Lefika Basotli, 14 Aug 2026). Filters the line
  // table only; the statement totals above stay the whole-statement figures.
  const [lineFrom, setLineFrom] = useState('')
  const [lineTo, setLineTo] = useState('')
  // BANK-008 (Lefika via Kelvin Kimani, 15 Sep 2026): the statements LIST had
  // no filter at all and was capped at the newest 20 across every account, so
  // older months were effectively invisible. These three drive the list query.
  const [stmtAccount, setStmtAccount] = useState('')
  const [stmtFrom, setStmtFrom] = useState('')
  const [stmtTo, setStmtTo] = useState('')
  // BANK-007: the reconciliation behind the Difference column, opened on click.
  const [recon, setRecon] = useState<BankReconciliationReport | null>(null)
  const [reconLoading, setReconLoading] = useState(false)
  const [reconAccountName, setReconAccountName] = useState('')

  useEffect(() => {
    const token = getToken()
    if (!token) { router.replace('/login'); return }
    loadBankAccounts()
    loadStatements()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Re-pull the statements list whenever its filter changes. Skips the first
  // render because the mount effect above already loaded it.
  const stmtFilterMounted = useRef(false)
  useEffect(() => {
    if (!stmtFilterMounted.current) { stmtFilterMounted.current = true; return }
    loadStatements()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stmtAccount, stmtFrom, stmtTo])

  // Clear the date filter whenever a different statement is opened, so a range
  // from the last statement never silently hides the new one's lines.
  useEffect(() => {
    setLineFrom('')
    setLineTo('')
  }, [selectedStatement?.id])

  // The transaction lines actually shown, after the date filter. transaction_date
  // is an ISO 'YYYY-MM-DD' string, so a plain string compare is a correct date
  // compare, and it matches the value an <input type="date"> gives us.
  const dateFilterOn = !!(lineFrom || lineTo)
  const visibleLines = selectedStatement
    ? selectedStatement.lines.filter((l) =>
        (!lineFrom || l.transaction_date >= lineFrom) &&
        (!lineTo || l.transaction_date <= lineTo))
    : []

  const loadBankAccounts = async () => {
    setBankAccountsLoading(true)
    try {
      const res = await getBankAccounts()
      setBankAccounts(res.results)
      if (res.results.length > 0) setUploadBankAccount(res.results[0].id)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load bank accounts')
    } finally { setBankAccountsLoading(false) }
  }

  const loadStatements = async () => {
    setStatementsLoading(true)
    try {
      const res = await getBankStatements({
        ordering: '-statement_date',
        page_size: '100',   // backend caps at core/pagination.max_page_size
        ...(stmtAccount ? { bank_account: stmtAccount } : {}),
        ...(stmtFrom ? { from_date: stmtFrom } : {}),
        ...(stmtTo ? { to_date: stmtTo } : {}),
      })
      setStatements(res.results)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load statements')
    } finally { setStatementsLoading(false) }
  }

  // BANK-007: pull the reconciliation for this account's latest statement and
  // show WHICH transactions cause the gap, not just the number.
  const openReconciliation = async (account: BankAccount) => {
    if (!account.statement_id) return
    setReconAccountName(account.account_name)
    setReconLoading(true)
    setRecon(null)
    try {
      setRecon(await getBankReconciliation(account.statement_id))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load the reconciliation')
      setReconAccountName('')
    } finally { setReconLoading(false) }
  }

  const loadStatementDetail = async (id: string) => {
    setStatementLoading(true)
    // RECON-002: clear the previous Auto-Match result text whenever we
    // switch to a different statement. The Lines/Matched/Unmatched
    // chips already refresh; this just stops the post-action banner
    // from carrying over (e.g. "0 matched, 1 unmatched" from the prior
    // statement).
    setMatchResult(null)
    try {
      const detail = await getBankStatement(id)
      setSelectedStatement(detail)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load statement')
    } finally { setStatementLoading(false) }
  }

  const handleRunMatching = async () => {
    if (!selectedStatement) return
    setMatching(true)
    setMatchResult(null)
    try {
      const result = await runMatching(selectedStatement.id)
      setMatchResult(result)
      await loadStatementDetail(selectedStatement.id)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Matching failed')
    } finally { setMatching(false) }
  }

  const handleUpload = async () => {
    if (!uploadFile || !uploadBankAccount) return
    setUploading(true)
    setUploadError(null)
    try {
      const formData = new FormData()
      formData.append('file', uploadFile)
      // Backend reads 'bank_account_id' — sending 'bank_account' produced the
      // "bank_account_id is required" failure (key mismatch, not the dropdown
      // binding). 2026-06-08.
      formData.append('bank_account_id', uploadBankAccount)
      const result = await importBankStatement(formData)
      setShowUpload(false)
      setUploadFile(null)
      await loadStatements()
      await loadStatementDetail(result.id)
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : 'Upload failed')
    } finally { setUploading(false) }
  }

  const openMatchModal = async (line: BankStatementLine) => {
    setMatchingLine(line)
    setSelectedPaymentId('')
    setMatchExplanation(null)
    setConfirmChecked(false)
    setSuggestions([])
    setShowMatchModal(true)
    apiFetch<{ suggestions: MatchSuggestion[] }>(`/bank-statement-lines/${line.id}/suggestions/`)
      .then(r => setSuggestions((r.suggestions || []).slice(0, 3)))
      .catch(() => setSuggestions([]))   // no suggestions ≠ broken picker
    try {
      const res = await getPayments({ status: 'confirmed', page_size: '100' })
      setUnmatchedPayments(res.results)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load payments for matching')
    }
  }

  // CFO M6: load dry-run explanation when a payment is selected
  const handlePaymentSelected = async (paymentId: string) => {
    latestPick.current = paymentId
    setSelectedPaymentId(paymentId)
    setMatchExplanation(null)
    setConfirmChecked(false)
    setLoadingExplanation(true)
    try {
      const response = await matchStatementLine(matchingLine!.id, {
        payment_id: paymentId,
        dry_run: true,
      })
      // A slower reply for an earlier pick must not paint its preview over
      // the payment now selected (Opus gate, 18-Sep).
      if (latestPick.current !== paymentId) return
      if ('dry_run' in response && response.dry_run) {
        const dryRunResp = response as MatchDryRunResponse
        setMatchExplanation(dryRunResp.explanation)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load match preview')
    } finally {
      setLoadingExplanation(false)
    }
  }

  const handleManualMatch = async () => {
    if (!matchingLine || !selectedPaymentId || !confirmChecked) return
    setSaving(true)
    try {
      await matchStatementLine(matchingLine.id, { payment_id: selectedPaymentId })
      setShowMatchModal(false)
      if (selectedStatement) await loadStatementDetail(selectedStatement.id)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Match failed')
    } finally { setSaving(false) }
  }

  const getMatchStatusColor = (status: string) => {
    switch (status) {
      case 'matched':      return 'bg-[#ECFDF5] text-[#059669] border border-[#A7F3D0]'
      case 'auto_matched': return 'bg-[#EFF6FF] text-[#2563EB] border border-[#BFDBFE]'
      case 'unmatched':    return 'bg-[#FEF2F2] text-[#DC2626] border border-[#FEE2E2]'
      case 'excluded':     return 'bg-[#F3F4F6] text-[#9CA3AF] border border-[#E5E7EB]'
      default:             return 'bg-[#F3F4F6] text-[#9CA3AF] border border-[#E5E7EB]'
    }
  }

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Bank Reconciliation"
        breadcrumbs={[{ label: 'Finance' }, { label: 'Bank Reconciliation' }]}
        actions={
          <div className="flex items-center gap-2">
            {/* RECON-004: surface the account picker on the panel header
                beside Upload Statement. ARIA's guidance refers to choosing
                the "Bank Account" here; selecting it pre-fills the upload
                dialog. Same validated dropdown of configured accounts —
                never a free-text account number. */}
            <Select
              aria-label="Bank Account"
              value={uploadBankAccount}
              onChange={(e) => setUploadBankAccount(e.target.value)}
              disabled={bankAccountsLoading || bankAccounts.length === 0}
              className="h-9 w-56 text-sm"
            >
              <option value="">Bank Account…</option>
              {bankAccounts.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.account_name} — {b.bank_name}
                </option>
              ))}
            </Select>
            <Button
              variant="accent"
              size="sm"
              leftIcon={<Upload className="w-3.5 h-3.5" />}
              onClick={() => setShowUpload(true)}
            >
              Upload Statement
            </Button>
          </div>
        }
      />

      <div className="flex-1 p-6 space-y-6">
        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-[#DC2626] flex-shrink-0" />
            <p className="text-[#DC2626] text-sm">{error}</p>
            <button aria-label="Dismiss error" onClick={() => setError(null)} className="ml-auto text-[#9CA3AF] hover:text-[#374151]">×</button>
          </div>
        )}

        {/* Bank Accounts Overview */}
        <Card>
          <CardHeader>
            <CardTitle>Bank Accounts</CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            {bankAccountsLoading ? (
              <LoadingTable rows={3} cols={5} />
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm border-collapse">
                  <thead className="bg-[#F3F4F6] border-b-2 border-[#E5E7EB]">
                    <tr>
                      <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Account</th>
                      <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Bank</th>
                      <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider hidden md:table-cell">Account Number</th>
                      <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider">Book/GL Balance</th>
                      <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider hidden md:table-cell">Statement Balance</th>
                      <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider">Difference</th>
                      <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider hidden lg:table-cell">Last Reconciled</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#E5E7EB] bg-white">
                    {bankAccounts.length === 0 ? (
                      <tr>
                        <td colSpan={7} className="px-4 py-8 text-center text-[#6B7280] text-sm">
                          No bank accounts configured
                        </td>
                      </tr>
                    ) : (
                      bankAccounts.map((account) => (
                        // RECON-003: rows are read-only — no onClick wired —
                        // so drop the hover-bg and the implied cursor
                        // affordance. If we later add row-click filtering of
                        // the Statements panel, restore `hover:bg-[#FFF7ED]
                        // cursor-pointer`.
                        <tr key={account.id} className="table-row-alt cursor-default">
                          <td className="px-4 py-3 text-[#111827] font-medium">
                            <div className="flex items-center gap-2">
                              <Landmark className="w-4 h-4 text-[#9CA3AF]" />
                              {account.account_name}
                            </div>
                          </td>
                          <td className="px-4 py-3 text-[#6B7280] text-xs">
                            {account.bank_name_display || account.bank_name || '—'}
                          </td>
                          <td className="px-4 py-3 text-[#9CA3AF] text-xs font-mono hidden md:table-cell">
                            {account.account_number_display || account.account_number || '—'}
                          </td>
                          <td className="px-4 py-3 text-right font-mono-nums">
                            <span className={parseAmount(account.book_balance || '0') >= 0 ? 'text-[#059669]' : 'text-[#DC2626]'}>
                              {fmt(account.book_balance || '0', account.currency)}
                            </span>
                          </td>
                          <td className="px-4 py-3 text-right font-mono-nums hidden md:table-cell">
                            {account.statement_balance == null
                              ? <span className="text-[#9CA3AF]">—</span>
                              : <span className="text-[#374151]">{fmt(account.statement_balance, account.currency)}</span>}
                            {account.statement_as_of && (
                              <div className="text-[10px] text-[#9CA3AF]">{formatDate(account.statement_as_of)}</div>
                            )}
                          </td>
                          {/* BANK-007 (Kelvin Kimani / Lefika, 15 Sep 2026):
                              "the GL balance is higher than the bank statement
                              balance, with no indication of why". Book/GL is
                              as-of-today by CFO directive BANK-001 while the
                              statement is as-of its own date, so the two
                              columns beside each other are NOT comparable and
                              subtracting them is meaningless. This shows the
                              like-for-like difference the backend computes
                              against the GL cut at the statement date, and
                              says out loud which date that is. */}
                          <td className="px-4 py-3 text-right font-mono-nums">
                            {account.statement_difference == null ? (
                              <span className="text-[#9CA3AF]">—</span>
                            ) : (
                              <button
                                type="button"
                                onClick={() => openReconciliation(account)}
                                className="group inline-flex flex-col items-end focus:outline-none focus-visible:ring-2 focus-visible:ring-[#F07F00] rounded"
                                aria-label={`Explain the difference on ${account.account_name}`}
                              >
                                <span
                                  className={cn(
                                    'underline decoration-dotted underline-offset-4 group-hover:decoration-solid',
                                    account.statement_reconciled
                                      ? 'text-[#059669]'
                                      : 'text-[#B45309] font-semibold'
                                  )}
                                >
                                  {fmt(account.statement_difference, account.currency)}
                                </span>
                                <span className="text-[10px] text-[#9CA3AF] font-sans">
                                  {account.statement_reconciled
                                    ? 'Agrees with the bank'
                                    : `${account.statement_unmatched ?? 0} unmatched`}
                                  {account.statement_as_of
                                    ? ` · GL to ${formatDate(account.statement_as_of)}`
                                    : ''}
                                </span>
                              </button>
                            )}
                          </td>
                          <td className="px-4 py-3 text-[#9CA3AF] text-xs hidden lg:table-cell">
                            {account.last_reconciled_date
                              ? formatDate(account.last_reconciled_date)
                              : <span className="text-[#D97706]">Never reconciled</span>}
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Statements List + Detail */}
        <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
          {/* Statements list */}
          <Card className="xl:col-span-1">
            <CardHeader className="space-y-3">
              <div className="flex flex-row items-center justify-between">
                <CardTitle>Statements</CardTitle>
                <button aria-label="Refresh statements" onClick={loadStatements} className="text-[#9CA3AF] hover:text-[#374151] transition-colors">
                  <RefreshCw className="w-4 h-4" />
                </button>
              </div>
              {/* BANK-008 (Lefika via Kelvin Kimani, 15 Sep 2026): view
                  statements one account and one month at a time. Before this
                  the list was the newest 20 across every account with no
                  filter, so older months could not be reached at all. */}
              <div className="space-y-2">
                <Select
                  aria-label="Filter statements by bank account"
                  value={stmtAccount}
                  onChange={(e) => setStmtAccount(e.target.value)}
                >
                  <option value="">All bank accounts</option>
                  {bankAccounts.map((a) => (
                    <option key={a.id} value={a.id}>{a.account_name}</option>
                  ))}
                </Select>
                <div className="flex items-center gap-2">
                  <input
                    type="date"
                    aria-label="Statements from date"
                    value={stmtFrom}
                    onChange={(e) => setStmtFrom(e.target.value)}
                    className="w-full rounded-md border border-[#D1D5DB] px-2 py-1.5 text-xs text-[#374151] focus:outline-none focus:ring-2 focus:ring-[#F07F00]"
                  />
                  <span className="text-xs text-[#9CA3AF]">to</span>
                  <input
                    type="date"
                    aria-label="Statements to date"
                    value={stmtTo}
                    onChange={(e) => setStmtTo(e.target.value)}
                    className="w-full rounded-md border border-[#D1D5DB] px-2 py-1.5 text-xs text-[#374151] focus:outline-none focus:ring-2 focus:ring-[#F07F00]"
                  />
                </div>
                {(stmtAccount || stmtFrom || stmtTo) && (
                  <div className="flex items-center justify-between">
                    <span className="text-[11px] text-[#6B7280]">
                      {statements.length} statement{statements.length === 1 ? '' : 's'} shown
                    </span>
                    <button
                      type="button"
                      onClick={() => { setStmtAccount(''); setStmtFrom(''); setStmtTo('') }}
                      className="text-[11px] text-[#F07F00] hover:underline"
                    >
                      Clear filter
                    </button>
                  </div>
                )}
              </div>
            </CardHeader>
            <CardContent className="p-0">
              {statementsLoading ? (
                <LoadingTable rows={4} cols={2} />
              ) : statements.length === 0 ? (
                /* CFO, bank.docx §5 (16 Sep 2026): a setup gap must not read
                   like an empty system. "No statements uploaded" beside an
                   account-and-month filter says the wrong thing — it reads as
                   "this account has never had a statement" when the real
                   answer is "nobody has loaded THIS month yet", which is the
                   one sentence that tells Finance what to do next. The three
                   states stay distinct: nothing loaded for this account/month
                   (here), no transactions in the range (the lines table), and
                   transactions still unmatched (the reconciliation panel). */
                <div className="py-8 text-center px-4">
                  {(stmtAccount || stmtFrom || stmtTo) ? (
                    <>
                      <p className="text-[#6B7280] text-sm">
                        No statement uploaded for{' '}
                        {stmtAccount
                          ? (bankAccounts.find((a) => a.id === stmtAccount)?.account_name
                             ?? 'this account')
                          : 'any account'}
                        {(stmtFrom || stmtTo) && (
                          <> between {stmtFrom || 'the beginning'} and {stmtTo || 'today'}</>
                        )}
                        .
                      </p>
                      <p className="text-[#9CA3AF] text-xs mt-1">
                        Upload the bank statement for this period first. Other
                        months may already be loaded — clear the filter to check.
                      </p>
                    </>
                  ) : (
                    <p className="text-[#6B7280] text-sm">No statements uploaded</p>
                  )}
                  <Button
                    variant="accent"
                    size="sm"
                    className="mt-3"
                    leftIcon={<Upload className="w-3.5 h-3.5" />}
                    onClick={() => setShowUpload(true)}
                  >
                    {(stmtAccount || stmtFrom || stmtTo)
                      ? 'Upload This Statement'
                      : 'Upload First Statement'}
                  </Button>
                </div>
              ) : (
                <div className="divide-y divide-[#E5E7EB]">
                  {statements.map((stmt) => (
                    <button
                      key={stmt.id}
                      onClick={() => loadStatementDetail(stmt.id)}
                      className={cn(
                        'w-full text-left px-4 py-3 hover:bg-[#F9FAFB] transition-colors',
                        selectedStatement?.id === stmt.id && 'bg-[#FFF7ED] border-l-2 border-[#F07F00]'
                      )}
                    >
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-sm font-medium text-[#111827]">{stmt.statement_number}</span>
                        <StatusBadge status={stmt.status} />
                      </div>
                      <p className="text-xs text-[#6B7280]">{stmt.bank_account_name}</p>
                      <div className="flex items-center justify-between mt-1">
                        <p className="text-xs text-[#9CA3AF]">{formatDate(stmt.statement_date)}</p>
                        <p className="text-xs text-[#9CA3AF]">{stmt.line_count} lines</p>
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          {/* Statement Detail */}
          <Card className="xl:col-span-2">
            <CardHeader className="flex flex-row items-center justify-between">
              <div>
                <CardTitle>
                  {selectedStatement ? selectedStatement.statement_number : 'Statement Lines'}
                </CardTitle>
                {selectedStatement && (
                  <p className="text-xs text-[#9CA3AF] mt-0.5">
                    {selectedStatement.bank_account_name} — {selectedStatement.bank_name}
                  </p>
                )}
              </div>
              {selectedStatement && (
                <div className="flex items-center gap-2">
                  {matchResult && (
                    <span className="text-xs text-[#6B7280]">
                      ✓ {matchResult.matched} matched, {matchResult.unmatched} unmatched
                    </span>
                  )}
                  <Button
                    variant="secondary"
                    size="sm"
                    leftIcon={matching ? <LoadingSpinner size="sm" /> : <Zap className="w-3.5 h-3.5" />}
                    onClick={handleRunMatching}
                    disabled={matching}
                  >
                    Auto-Match
                  </Button>
                </div>
              )}
            </CardHeader>
            <CardContent className="p-0">
              {!selectedStatement && !statementLoading ? (
                <div className="py-12 text-center">
                  <Landmark className="w-8 h-8 text-[#D1D5DB] mx-auto mb-2" />
                  <p className="text-[#6B7280] text-sm">Select a statement to view lines</p>
                </div>
              ) : statementLoading ? (
                <LoadingTable rows={6} cols={5} />
              ) : selectedStatement && (
                <>
                  {/* Statement summary */}
                  <div className="px-4 py-3 bg-[#F9FAFB] border-b border-[#E5E7EB] flex flex-wrap gap-4">
                    <div>
                      <p className="text-xs text-[#9CA3AF]">Opening</p>
                      <p className="text-sm font-semibold text-[#111827] font-mono-nums">
                        {fmt(selectedStatement.opening_balance)}
                      </p>
                    </div>
                    <div>
                      <p className="text-xs text-[#9CA3AF]">Closing</p>
                      <p className="text-sm font-semibold text-[#111827] font-mono-nums">
                        {fmt(selectedStatement.closing_balance)}
                      </p>
                    </div>
                    <div>
                      <p className="text-xs text-[#9CA3AF]">Lines</p>
                      <p className="text-sm font-semibold text-[#111827]">{selectedStatement.line_count}</p>
                    </div>
                    <div>
                      <p className="text-xs text-[#9CA3AF]">Matched</p>
                      <p className="text-sm font-semibold text-[#059669]">
                        {selectedStatement.lines.filter(l => l.match_status === 'matched' || l.match_status === 'auto_matched').length}
                      </p>
                    </div>
                    <div>
                      <p className="text-xs text-[#9CA3AF]">Unmatched</p>
                      <p className="text-sm font-semibold text-[#DC2626]">
                        {selectedStatement.lines.filter(l => l.match_status === 'unmatched').length}
                      </p>
                    </div>
                  </div>

                  {/* Date filter — view only the transactions in a chosen day
                      or range (Lefika Basotli, 14 Aug 2026). */}
                  <div className="px-4 py-2.5 border-b border-[#E5E7EB] flex flex-wrap items-center gap-2">
                    <span className="text-xs font-medium text-[#6B7280]">Filter by date</span>
                    <input
                      type="date"
                      value={lineFrom}
                      onChange={(e) => setLineFrom(e.target.value)}
                      aria-label="From date"
                      className="rounded border border-[#E5E7EB] px-2 py-1 text-xs text-[#374151]"
                    />
                    <span className="text-xs text-[#9CA3AF]">to</span>
                    <input
                      type="date"
                      value={lineTo}
                      onChange={(e) => setLineTo(e.target.value)}
                      aria-label="To date"
                      className="rounded border border-[#E5E7EB] px-2 py-1 text-xs text-[#374151]"
                    />
                    {dateFilterOn && (
                      <>
                        <button
                          onClick={() => { setLineFrom(''); setLineTo('') }}
                          className="text-xs text-[#F07F00] hover:text-[#CC6C00] transition-colors"
                        >
                          Clear
                        </button>
                        <span className="text-xs text-[#9CA3AF] ml-auto">
                          Showing {visibleLines.length} of {selectedStatement.lines.length}
                        </span>
                      </>
                    )}
                  </div>

                  <div className="overflow-x-auto max-h-[480px] overflow-y-auto">
                    <table className="w-full text-sm border-collapse">
                      <thead className="bg-[#F3F4F6] border-b-2 border-[#E5E7EB] sticky top-0">
                        <tr>
                          <th className="px-4 py-2.5 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Date</th>
                          <th className="px-4 py-2.5 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Description</th>
                          <th className="px-4 py-2.5 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider hidden md:table-cell">Reference</th>
                          <th className="px-4 py-2.5 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider">Amount</th>
                          <th className="px-4 py-2.5 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Status</th>
                          <th className="px-4 py-2.5 text-center text-xs font-semibold text-[#374151] uppercase tracking-wider">Action</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-[#E5E7EB] bg-white">
                        {visibleLines.length === 0 ? (
                          <tr>
                            <td colSpan={6} className="px-4 py-8 text-center text-[#6B7280] text-sm">
                              {dateFilterOn && selectedStatement.lines.length > 0
                                ? 'No transactions in this date range'
                                : 'No lines in this statement'}
                            </td>
                          </tr>
                        ) : (
                          visibleLines.map((line) => (
                            <tr key={line.id} className="table-row-alt hover:bg-[#FFF7ED] transition-colors">
                              <td className="px-4 py-2.5 text-[#6B7280] text-xs whitespace-nowrap">
                                {formatDate(line.transaction_date)}
                              </td>
                              <td className="px-4 py-2.5 text-[#374151] max-w-[200px] truncate">
                                {line.description}
                                {line.matched_payment_number && (
                                  <span className="ml-1 text-xs text-[#059669]">
                                    → {line.matched_payment_number}
                                  </span>
                                )}
                                {line.matched_je_number && (
                                  <span className="ml-1 text-xs text-[#2563EB]">
                                    → {line.matched_je_number}
                                  </span>
                                )}
                              </td>
                              <td className="px-4 py-2.5 text-[#9CA3AF] text-xs font-mono hidden md:table-cell">
                                {line.reference || '—'}
                              </td>
                              <td className={cn(
                                'px-4 py-2.5 text-right font-mono-nums text-sm font-medium',
                                parseAmount(line.amount) >= 0 ? 'text-[#059669]' : 'text-[#DC2626]'
                              )}>
                                {fmt(line.amount)}
                              </td>
                              <td className="px-4 py-2.5">
                                <span className={cn(
                                  'inline-flex items-center px-2 py-0.5 text-xs font-medium rounded-full',
                                  getMatchStatusColor(line.match_status)
                                )}>
                                  {line.match_status?.replace(/_/g, ' ')}
                                </span>
                                {line.match_confidence > 0 && line.match_status === 'auto_matched' && (
                                  <span className="ml-1 text-xs text-[#9CA3AF]">
                                    {(line.match_confidence * 100).toFixed(0)}%
                                  </span>
                                )}
                              </td>
                              {/* RECON-001 (critical): the floating ARIA
                                  bubble (z-50, fixed bottom-right) was
                                  swallowing clicks on the Match button on
                                  bottom-of-table rows at narrow viewports
                                  (e.g. 987px with sidebar expanded). Pin
                                  this TD's stacking context above the
                                  ARIA layer with `relative z-[60]` so the
                                  button always receives pointer events,
                                  regardless of viewport width. */}
                              <td className="px-4 py-2.5 text-center relative z-[60]">
                                {line.match_status === 'unmatched' && (
                                  <button
                                    onClick={() => openMatchModal(line)}
                                    className="text-xs text-[#F07F00] hover:text-[#CC6C00] transition-colors flex items-center gap-1 mx-auto relative z-[60]"
                                  >
                                    <Link2 className="w-3 h-3" />
                                    Match
                                  </button>
                                )}
                              </td>
                            </tr>
                          ))
                        )}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
            </CardContent>
          </Card>
        </div>
      </div>

      {/* Upload Modal */}
      <Modal
        open={showUpload}
        onOpenChange={setShowUpload}
        title="Upload Bank Statement"
        description="Upload the bank's CSV or Excel (.xlsx) statement file"
        size="sm"
      >
        <ModalBody className="space-y-4">
          {uploadError && (
            <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2">
              <AlertCircle className="w-4 h-4 text-[#DC2626] flex-shrink-0" />
              <p className="text-[#DC2626] text-xs">{uploadError}</p>
            </div>
          )}
          <FormField label="Bank Account" required>
            <Select
              value={uploadBankAccount}
              onChange={(e) => setUploadBankAccount(e.target.value)}
            >
              <option value="">Select bank account...</option>
              {bankAccounts.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.account_name} - {b.bank_name}
                </option>
              ))}
            </Select>
          </FormField>
          <FormField label="Statement File" required>
            <div className="border-2 border-dashed border-[#D1D5DB] rounded-lg p-6 text-center hover:border-[#F07F00] transition-colors">
              <input
                type="file"
                accept=".csv,.txt,.xlsx,.xlsm"
                onChange={(e) => setUploadFile(e.target.files?.[0] || null)}
                className="hidden"
                id="statement-file"
              />
              <label htmlFor="statement-file" className="cursor-pointer">
                {uploadFile ? (
                  <div>
                    <CheckCircle className="w-6 h-6 text-[#059669] mx-auto mb-1" />
                    <p className="text-sm text-[#059669]">{uploadFile.name}</p>
                    <p className="text-xs text-[#9CA3AF] mt-0.5">
                      {(uploadFile.size / 1024).toFixed(1)} KB
                    </p>
                  </div>
                ) : (
                  <div>
                    <Upload className="w-6 h-6 text-[#9CA3AF] mx-auto mb-1" />
                    <p className="text-sm text-[#6B7280]">Click to select file</p>
                    <p className="text-xs text-[#9CA3AF] mt-0.5">CSV or Excel (.xlsx)</p>
                  </div>
                )}
              </label>
            </div>
          </FormField>
        </ModalBody>
        <ModalFooter>
          <Button variant="secondary" size="sm" onClick={() => setShowUpload(false)} disabled={uploading}>
            Cancel
          </Button>
          <Button
            variant="accent"
            size="sm"
            loading={uploading}
            disabled={!uploadFile || !uploadBankAccount}
            onClick={handleUpload}
          >
            Upload
          </Button>
        </ModalFooter>
      </Modal>

      {/* Manual Match Modal — CFO M6: searchable picker with dry-run preview */}
      <Modal
        open={showMatchModal}
        onOpenChange={setShowMatchModal}
        title="Match Statement Line"
        description={matchingLine ? `Matching: ${matchingLine.description} (${fmt(matchingLine.amount)})` : ''}
        size="md"
      >
        <ModalBody className="space-y-4">
          <div className="bg-[#F9FAFB] rounded-lg p-3 border border-[#E5E7EB]">
            <p className="text-xs text-[#9CA3AF] uppercase tracking-wider mb-2">Statement Line</p>
            <div className="flex justify-between items-center">
              <div>
                <p className="text-sm text-[#111827]">{matchingLine?.description}</p>
                <p className="text-xs text-[#6B7280] mt-0.5">
                  {matchingLine && formatDate(matchingLine.transaction_date)} · Ref: {matchingLine?.reference || '—'}
                </p>
              </div>
              <p className={cn(
                'font-mono-nums font-bold',
                parseAmount(matchingLine?.amount || '0') >= 0 ? 'text-[#059669]' : 'text-[#DC2626]'
              )}>
                {fmt(matchingLine?.amount || '0')}
              </p>
            </div>
          </div>

          {suggestions.length > 0 && (
            <div>
              <p className="text-xs text-[#9CA3AF] uppercase tracking-wider mb-2">Suggested (you still check and confirm)</p>
              <ul className="space-y-2">
                {suggestions.map((s) => (
                  <li key={s.payment_id}>
                    <button
                      type="button"
                      onClick={() => handlePaymentSelected(s.payment_id)}
                      aria-pressed={selectedPaymentId === s.payment_id}
                      className={cn(
                        'w-full text-left rounded-lg border p-2.5 transition-colors',
                        selectedPaymentId === s.payment_id
                          ? 'border-[#0D1B2A] bg-[#F3F4F6]'
                          : 'border-[#E5E7EB] hover:border-[#9CA3AF]',
                      )}
                    >
                      <span className="flex justify-between gap-3 text-sm text-[#111827]">
                        <span>{s.payment_number} · {s.payee}</span>
                        <span className="font-mono-nums">{fmt(s.amount)}</span>
                      </span>
                      <span className="block text-xs text-[#6B7280] mt-1">{s.explanation}</span>
                      <span className="block text-xs text-[#6B7280] mt-0.5">
                        Suggested {s.score}% · Deterministic {s.deterministic_confidence}%
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <FormField label="Search and Select Payment">
            <SearchableSelect
              options={unmatchedPayments.map((p) => ({
                value: p.id,
                label: p.payment_number,
                hint: `${p.contact_name} · ${fmt(p.amount, p.currency)} · ${formatDate(p.payment_date)}`,
              }))}
              value={selectedPaymentId}
              onChange={handlePaymentSelected}
              placeholder="Type payment number, contact, or amount…"
            />
          </FormField>

          {/* Match explanation preview (CFO M6) */}
          {selectedPaymentId && (
            <>
              {loadingExplanation && (
                <div className="py-4 flex justify-center">
                  <LoadingSpinner />
                </div>
              )}
              {!loadingExplanation && matchExplanation && (
                <div className="bg-blue-50 rounded-lg p-3 border border-blue-200 space-y-2">
                  <p className="text-xs text-blue-900 font-medium">Match Preview</p>
                  <div className="text-xs text-blue-800 space-y-1">
                    <div className="flex justify-between">
                      <span>Amounts match:</span>
                      <span>{matchExplanation.amount_equal ? '✓ Yes' : '✗ No'}</span>
                    </div>
                    <div className="flex justify-between">
                      <span>Date difference:</span>
                      <span>{matchExplanation.date_gap_days} days</span>
                    </div>
                    <div className="flex justify-between">
                      <span>Reference overlap:</span>
                      <span>{matchExplanation.reference_match ? '✓ Yes' : '✗ No'}</span>
                    </div>
                    <div className="flex justify-between font-semibold border-t border-blue-200 pt-1 mt-1">
                      <span>Confidence:</span>
                      <span>{matchExplanation.confidence}%</span>
                    </div>
                  </div>
                  <p className="text-xs text-blue-700 mt-2">
                    Confirming links this bank line to the payment. Nothing is posted to the ledger.
                  </p>
                </div>
              )}

              {/* Confirmation checkbox */}
              {!loadingExplanation && matchExplanation && (
                <div className="flex items-start gap-2">
                  <input
                    type="checkbox"
                    id="confirmMatch"
                    checked={confirmChecked}
                    onChange={(e) => setConfirmChecked(e.target.checked)}
                    className="mt-1 cursor-pointer"
                  />
                  <label htmlFor="confirmMatch" className="text-xs text-[#6B7280] cursor-pointer">
                    I have checked this match
                  </label>
                </div>
              )}
            </>
          )}
        </ModalBody>
        <ModalFooter>
          <Button variant="secondary" size="sm" onClick={() => setShowMatchModal(false)} disabled={saving || loadingExplanation}>
            Cancel
          </Button>
          <Button
            variant="accent"
            size="sm"
            loading={saving}
            disabled={!selectedPaymentId || !matchExplanation || !confirmChecked || loadingExplanation}
            onClick={handleManualMatch}
          >
            Confirm Match
          </Button>
        </ModalFooter>
      </Modal>

      {/* BANK-007: the answer to "why is the GL higher than the bank?".
          Spells out the two figures on the SAME date, the gap between them,
          and the individual transactions the bank has that Omni has not
          matched — the reconciling items that were never shown anywhere. */}
      <Modal
        open={!!reconAccountName}
        onOpenChange={(open) => { if (!open) { setReconAccountName(''); setRecon(null) } }}
        title="Why the balances differ"
        description={reconAccountName}
        size="lg"
      >
        <ModalBody className="space-y-4">
          {reconLoading || !recon ? (
            <div className="py-8 flex justify-center"><LoadingSpinner /></div>
          ) : (
            <>
              <div className="bg-[#F9FAFB] rounded-lg p-4 border border-[#E5E7EB] space-y-2">
                <p className="text-xs text-[#9CA3AF] uppercase tracking-wider">
                  Compared as at {formatDate(recon.statement_date)}
                </p>
                <div className="flex justify-between text-sm">
                  <span className="text-[#6B7280]">Bank statement closing balance</span>
                  <span className="font-mono-nums text-[#111827]">{fmt(recon.bank_closing_balance)}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-[#6B7280]">Omni GL balance on that same date</span>
                  <span className="font-mono-nums text-[#111827]">{fmt(recon.gl_balance)}</span>
                </div>
                <div className="flex justify-between text-sm border-t border-[#E5E7EB] pt-2">
                  <span className="font-semibold text-[#374151]">Difference</span>
                  <span className={cn(
                    'font-mono-nums font-semibold',
                    recon.is_reconciled ? 'text-[#059669]' : 'text-[#B45309]'
                  )}>
                    {fmt(recon.difference)}
                  </span>
                </div>
              </div>

              {recon.is_reconciled ? (
                <div className="flex items-start gap-2 text-sm text-[#065F46] bg-[#ECFDF5] border border-[#A7F3D0] rounded-lg p-3">
                  <CheckCircle className="w-4 h-4 mt-0.5 shrink-0" />
                  <p>
                    These agree. If the Book/GL column on the table looks higher, that is
                    simply activity posted after {formatDate(recon.statement_date)} — it is
                    not yet on this statement, and nothing is wrong.
                  </p>
                </div>
              ) : (
                <div className="flex items-start gap-2 text-sm text-[#92400E] bg-[#FFFBEB] border border-[#FDE68A] rounded-lg p-3">
                  <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
                  <p>
                    {recon.lines.unmatched} transaction{recon.lines.unmatched === 1 ? '' : 's'} on
                    the bank statement {recon.lines.unmatched === 1 ? 'has' : 'have'} not been
                    matched in Omni. They are listed below.
                  </p>
                </div>
              )}

              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-center">
                {([
                  ['Lines', recon.lines.total],
                  ['Auto-matched', recon.lines.auto_matched],
                  ['Matched by hand', recon.lines.manually_matched],
                  ['Unmatched', recon.lines.unmatched],
                ] as const).map(([label, value]) => (
                  <div key={label} className="bg-white border border-[#E5E7EB] rounded-lg py-2">
                    <p className="text-lg font-mono-nums text-[#111827]">{value}</p>
                    <p className="text-[10px] text-[#9CA3AF] uppercase tracking-wider">{label}</p>
                  </div>
                ))}
              </div>

              {recon.unmatched_lines.length > 0 && (
                <div>
                  <p className="text-xs text-[#9CA3AF] uppercase tracking-wider mb-2">
                    Unmatched bank transactions
                  </p>
                  <div className="max-h-64 overflow-y-auto border border-[#E5E7EB] rounded-lg divide-y divide-[#E5E7EB]">
                    {recon.unmatched_lines.map((line) => (
                      <div key={line.id} className="flex items-center justify-between px-3 py-2">
                        <div className="min-w-0 pr-3">
                          <p className="text-sm text-[#111827] truncate">{line.description}</p>
                          <p className="text-[11px] text-[#9CA3AF]">{formatDate(line.transaction_date)}</p>
                        </div>
                        <span className="text-sm font-mono-nums text-[#374151] shrink-0">
                          {fmt(line.amount)}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
        </ModalBody>
        <ModalFooter>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => { setReconAccountName(''); setRecon(null) }}
          >
            Close
          </Button>
        </ModalFooter>
      </Modal>
    </div>
  )
}
