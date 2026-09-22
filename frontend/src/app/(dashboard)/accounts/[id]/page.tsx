'use client'

import { useEffect, useState, useRef } from 'react'
import { useRouter, useParams } from 'next/navigation'
import {
  getAccount, getGeneralLedger, getToken, patchAccount,
  getFsLineOptions, getClassifySuggestion, getNextUnmapped,
} from '@/lib/api'
import type { Account, GeneralLedgerReport, FsLineOptionGroup } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { LoadingTable, LoadingCard } from '@/components/ui/loading'
import { formatAmount, formatDate, parseAmount, exportToCsv, getFyStart, today, cn } from '@/lib/utils'
import { useNumberFormat } from '@/contexts/NumberFormatContext'
import {
  Download, AlertCircle, TrendingUp, TrendingDown, Edit3, Save, X,
  CheckCircle2, Sparkles, ArrowRight, HelpCircle,
} from 'lucide-react'

export default function AccountDetailPage() {
  const router = useRouter()
  const params = useParams()
  const id = params.id as string
  const { mode } = useNumberFormat()
  const fmt = (amount: string | number, currency = 'BWP') => formatAmount(amount, currency, mode)

  const [account, setAccount] = useState<Account | null>(null)
  const [glReport, setGlReport] = useState<GeneralLedgerReport | null>(null)
  const [loading, setLoading] = useState(true)
  const [glLoading, setGlLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [fromDate, setFromDate] = useState(getFyStart())
  const [toDate, setToDate] = useState(today())

  // ─── Edit Account state (CFO directive 2026-06-09 — Legakwa 211 RSA unmapped
  // accounts; the page had no UI to set fs_line_item, so users were stuck.) ──
  const [editOpen, setEditOpen]       = useState(false)
  const [editSaving, setEditSaving]   = useState(false)
  const [editError, setEditError]     = useState<string | null>(null)
  const [editSavedTs, setEditSavedTs] = useState<number | null>(null)
  const [editFsLine, setEditFsLine]   = useState('')
  const [editAcctType, setEditAcctType] = useState('')
  const [editSubType, setEditSubType] = useState('')
  const [editStmtClass, setEditStmtClass] = useState('')

  // ─── UX upgrade 2026-06-09: dropdown options + AI suggest + next-unmapped ──
  const [optionGroups, setOptionGroups] = useState<FsLineOptionGroup[]>([])
  const [suggestion, setSuggestion]     = useState<string | null>(null)
  const [suggestRule, setSuggestRule]   = useState<string | null>(null)
  const [suggestLoading, setSuggestLoading] = useState(false)
  const [nextUnmappedId, setNextUnmappedId] = useState<string | null>(null)
  const [unmappedRemaining, setUnmappedRemaining] = useState<number>(0)
  const fsInputRef = useRef<HTMLInputElement | null>(null)

  const loadAccount = async () => {
    try {
      const acc = await getAccount(id)
      setAccount(acc)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load account')
    }
  }

  const loadGL = async () => {
    setGlLoading(true)
    try {
      const report = await getGeneralLedger(id, fromDate, toDate)
      setGlReport(report)
    } catch (err) {
      console.error('GL load error:', err)
      setGlReport(null)
    } finally { setGlLoading(false) }
  }

  useEffect(() => {
    const token = getToken()
    if (!token) { router.replace('/login'); return }
    setLoading(true)
    Promise.all([loadAccount(), loadGL()]).finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  // After the account loads, pull the company-aware label options + the
  // next-unmapped pointer + (if THIS account is itself unmapped) the
  // auto-classify suggestion. All in parallel, all failure-tolerant —
  // the page must still render if any of these endpoints flake.
  useEffect(() => {
    if (!account) return
    const code = (account.owner_company_code || '').toUpperCase()
    let cancelled = false
    ;(async () => {
      try {
        const opts = await getFsLineOptions(code || undefined)
        if (!cancelled) setOptionGroups(opts.groups || [])
      } catch { /* ignore */ }
    })()
    ;(async () => {
      try {
        const nx = await getNextUnmapped(id)
        if (!cancelled) {
          setNextUnmappedId(nx.next_id)
          setUnmappedRemaining(nx.remaining || 0)
        }
      } catch { /* ignore */ }
    })()
    if (!account.fs_line_item) {
      setSuggestLoading(true)
      ;(async () => {
        try {
          const s = await getClassifySuggestion(id)
          if (!cancelled) { setSuggestion(s.suggestion); setSuggestRule(s.rule) }
        } catch { /* ignore */ }
        finally { if (!cancelled) setSuggestLoading(false) }
      })()
    } else {
      setSuggestion(null); setSuggestRule(null)
    }
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [account?.id, account?.fs_line_item])

  // Flat list of every valid fs_line_item label for the datalist (dedup,
  // preserve order so the most-relevant section appears first).
  const allLabels: string[] = (() => {
    const seen = new Set<string>(); const out: string[] = []
    for (const g of optionGroups) {
      for (const o of g.options) {
        if (!seen.has(o)) { seen.add(o); out.push(o) }
      }
    }
    return out
  })()
  const findGroupOf = (label: string): string | null => {
    const t = label.trim().toLowerCase()
    if (!t) return null
    for (const g of optionGroups) {
      if (g.options.some(o => o.toLowerCase() === t)) return g.group
    }
    return null
  }

  const handleExportCsv = () => {
    if (!glReport) return
    const rows = glReport.lines.map((line) => ({
      Date: line.date,
      'Entry Number': line.entry_number,
      Description: line.description,
      'Journal Type': line.journal_type,
      Debit: line.debit,
      Credit: line.credit,
      'Running Balance': line.running_balance,
    }))
    exportToCsv(rows, `GL_${account?.code}_${fromDate}_${toDate}`)
  }

  // Shared save handler — used by Save and Save & Next.
  // Diff-only PATCH so we never trip "may not be null" on optional string
  // fields the user didn't touch. Empty fs_line_item = explicit unmap.
  const doSaveClassification = async (jumpToNext: boolean) => {
    setEditSaving(true); setEditError(null)
    try {
      const payload: Record<string, any> = {}
      if (editFsLine.trim() !== (account?.fs_line_item || '').trim()) {
        payload.fs_line_item = editFsLine.trim()
      }
      if (editAcctType && editAcctType !== account?.account_type) {
        payload.account_type = editAcctType
      }
      if (editSubType.trim() && editSubType.trim() !== (account?.sub_type || '').trim()) {
        payload.sub_type = editSubType.trim()
      }
      const curStmt = ((account as any)?.statement_class || '').trim()
      if (editStmtClass.trim() && editStmtClass.trim() !== curStmt) {
        payload.statement_class = editStmtClass.trim()
      }
      if (Object.keys(payload).length > 0) {
        const updated = await patchAccount(id, payload as any)
        setAccount(updated)
      }
      setEditOpen(false)
      setEditSavedTs(Date.now())
      if (jumpToNext && nextUnmappedId) {
        router.push(`/accounts/${nextUnmappedId}`)
      }
    } catch (e: any) {
      setEditError(e?.message || 'Save failed.')
    } finally { setEditSaving(false) }
  }

  const getTypeColor = (type: string) => {
    const t = type?.toLowerCase()
    if (t === 'asset')     return 'text-[#2563EB]'
    if (t === 'liability') return 'text-[#CC6C00]'
    if (t === 'equity')    return 'text-[#7C3AED]'
    if (t === 'revenue')   return 'text-[#059669]'
    if (t === 'expense')   return 'text-[#DC2626]'
    return 'text-[#6B7280]'
  }

  if (loading) {
    return (
      <div className="flex flex-col min-h-screen">
        <TopBar title="Account Detail" breadcrumbs={[{ label: 'Chart of Accounts', href: '/accounts' }, { label: 'Loading...' }]} />
        <div className="p-6"><LoadingCard message="Loading account..." className="h-40" /></div>
      </div>
    )
  }

  if (error || !account) {
    return (
      <div className="flex flex-col min-h-screen">
        <TopBar title="Account Detail" breadcrumbs={[{ label: 'Chart of Accounts', href: '/accounts' }]} />
        <div className="p-6">
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-xl p-4 flex items-center gap-3">
            <AlertCircle className="w-5 h-5 text-[#DC2626]" />
            <p className="text-[#DC2626]">{error || 'Account not found'}</p>
          </div>
        </div>
      </div>
    )
  }

  const balance = parseAmount(account.balance)

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title={account.name}
        breadcrumbs={[{ label: 'Chart of Accounts', href: '/accounts' }, { label: account.code }]}
        actions={
          <Button variant="secondary" size="sm" leftIcon={<Download className="w-3.5 h-3.5" />} onClick={handleExportCsv} disabled={!glReport}>
            Export CSV
          </Button>
        }
      />

      <div className="flex-1 p-6 space-y-6">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <Card className="p-4">
            <p className="text-xs text-[#6B7280] uppercase tracking-wider">Account Code</p>
            <p className="text-xl font-bold text-[#0B0B3B] mt-1 font-mono">{account.code}</p>
          </Card>
          <Card className="p-4">
            <p className="text-xs text-[#6B7280] uppercase tracking-wider">Type</p>
            <p className={cn('text-base font-semibold mt-1 capitalize', getTypeColor(account.account_type))}>
              {account.account_type}
            </p>
            <p className="text-xs text-[#9CA3AF] mt-0.5">{account.sub_type?.replace(/_/g, ' ')}</p>
          </Card>
          <Card className="p-4">
            <p className="text-xs text-[#6B7280] uppercase tracking-wider">Currency</p>
            <p className="text-base font-semibold text-[#374151] mt-1">{account.currency}</p>
            {account.is_bank_account && <span className="text-xs text-[#2563EB]">Bank Account</span>}
          </Card>
          <Card className="p-4">
            <p className="text-xs text-[#6B7280] uppercase tracking-wider">Current Balance</p>
            <p className={cn('text-xl font-bold mt-1 font-mono-nums', balance >= 0 ? 'text-[#059669]' : 'text-[#DC2626]')}>
              {fmt(account.balance, account.currency)}
            </p>
            <div className="flex items-center gap-1 mt-0.5">
              {balance >= 0
                ? <TrendingUp className="w-3 h-3 text-[#059669]" />
                : <TrendingDown className="w-3 h-3 text-[#DC2626]" />}
              <span className={cn('text-xs', balance >= 0 ? 'text-[#059669]' : 'text-[#DC2626]')}>
                {balance >= 0 ? 'Debit Balance' : 'Credit Balance'}
              </span>
            </div>
          </Card>
        </div>

        {/* ── Account Classification ─────────────────────────────────────
            CFO directive 2026-06-09 (Legakwa UX pass): combobox-driven labels,
            human field names, AI-style suggest, Save-and-Next-Unmapped, keyboard
            shortcuts, unmapped banner. Backed by:
              - GET /accounts/fs-line-options/?company=<code>
              - GET /accounts/<id>/classify-suggest/
              - GET /accounts/<id>/next-unmapped/
              - PATCH /accounts/<id>/  (diff-only payload)
            P&L and BS figures unaffected — mapping only renames MA-tree buckets;
            see ma_pl_spec.py (code-driven) + entity_pl/bs engines (name-driven).
        */}
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between gap-3">
              <div>
                <CardTitle>Account Classification</CardTitle>
                <p className="text-xs text-[#6B7280] mt-1">
                  Where this account lands on the MA tree, Balance Sheet & P&L.
                </p>
              </div>
              {!editOpen ? (
                <div className="flex items-center gap-2">
                  {unmappedRemaining > 0 && nextUnmappedId && (
                    <Button
                      variant="secondary" size="sm"
                      rightIcon={<ArrowRight className="w-3.5 h-3.5" />}
                      onClick={() => router.push(`/accounts/${nextUnmappedId}`)}
                      title={`${unmappedRemaining} unmapped left in ${account.owner_company_code || 'this company'}`}
                    >
                      Next unmapped ({unmappedRemaining})
                    </Button>
                  )}
                  <Button
                    variant="primary" size="sm"
                    leftIcon={<Edit3 className="w-3.5 h-3.5" />}
                    onClick={() => {
                      setEditFsLine(account.fs_line_item || '')
                      setEditAcctType(account.account_type || '')
                      setEditSubType(account.sub_type || '')
                      setEditStmtClass((account as any).statement_class || '')
                      setEditError(null); setEditSavedTs(null)
                      setEditOpen(true)
                      setTimeout(() => fsInputRef.current?.focus(), 50)
                    }}
                  >{account.fs_line_item ? 'Edit classification' : 'Classify now'}</Button>
                </div>
              ) : (
                <div className="flex items-center gap-2">
                  <Button
                    variant="secondary" size="sm"
                    leftIcon={<X className="w-3.5 h-3.5" />}
                    onClick={() => { setEditOpen(false); setEditError(null) }}
                    disabled={editSaving}
                  >Cancel (Esc)</Button>
                  <Button
                    variant="primary" size="sm"
                    leftIcon={editSaving
                      ? <span className="inline-block w-3.5 h-3.5 animate-pulse">…</span>
                      : <Save className="w-3.5 h-3.5" />}
                    disabled={editSaving}
                    onClick={() => doSaveClassification(false)}
                  >{editSaving ? 'Saving…' : 'Save (⌘+S)'}</Button>
                  {nextUnmappedId && (
                    <Button
                      variant="primary" size="sm"
                      rightIcon={<ArrowRight className="w-3.5 h-3.5" />}
                      disabled={editSaving}
                      onClick={() => doSaveClassification(true)}
                      title="Save this, then jump to the next unmapped account"
                    >Save & Next</Button>
                  )}
                </div>
              )}
            </div>
          </CardHeader>
          <CardContent>
            {/* Unmapped banner — only when not in edit mode AND truly unmapped */}
            {!editOpen && !account.fs_line_item && (
              <div className="mb-4 bg-[#FFF7ED] border border-[#FED7AA] rounded-lg px-4 py-3 flex items-start gap-3">
                <AlertCircle className="w-4 h-4 text-[#CC6C00] flex-shrink-0 mt-0.5" />
                <div className="text-sm flex-1">
                  <p className="font-semibold text-[#9A3412]">
                    This account is not yet on the MA tree.
                  </p>
                  <p className="text-[#7C2D12] mt-0.5">
                    It will not appear under any specific Balance Sheet / P&L line
                    until you set its <em>MA tree line</em>. Click <strong>Classify now</strong> above
                    {suggestion && <> &mdash; we suggest <strong>{suggestion}</strong>.</>}
                  </p>
                </div>
              </div>
            )}

            {!editOpen ? (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                <div className="lg:col-span-2">
                  <p className="text-xs text-[#6B7280] uppercase tracking-wider font-medium">MA tree line</p>
                  <p className="text-sm font-semibold text-[#111827] mt-1">
                    {account.fs_line_item || <span className="text-[#DC2626]">— unmapped</span>}
                  </p>
                  {account.fs_line_item && findGroupOf(account.fs_line_item) && (
                    <p className="text-xs text-[#6B7280] mt-1">
                      Section: <span className="text-[#374151] font-medium">{findGroupOf(account.fs_line_item)}</span>
                    </p>
                  )}
                </div>
                <div>
                  <p className="text-xs text-[#6B7280] uppercase tracking-wider font-medium">Balance side</p>
                  <p className="text-sm font-semibold text-[#111827] mt-1 capitalize">{account.account_type}</p>
                </div>
                <div>
                  <p className="text-xs text-[#6B7280] uppercase tracking-wider font-medium">Statement</p>
                  <p className="text-sm font-semibold text-[#111827] mt-1">
                    {(account as any).statement_class === 'BS' ? 'Balance Sheet'
                     : (account as any).statement_class === 'PNL' ? 'Income Statement'
                     : (account as any).statement_class || '—'}
                  </p>
                </div>
                {editSavedTs && (Date.now() - editSavedTs) < 8000 && (
                  <div className="sm:col-span-2 lg:col-span-4 bg-[#ECFDF5] border border-[#A7F3D0] text-sm text-[#065F46] rounded-lg px-3 py-2 flex items-center gap-2">
                    <CheckCircle2 className="w-4 h-4" /> Saved. The MA tree will pick this up on next report build.
                  </div>
                )}
              </div>
            ) : (
              <div
                className="space-y-4"
                onKeyDown={(e) => {
                  if (e.key === 'Escape' && !editSaving) { setEditOpen(false); setEditError(null) }
                  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') {
                    e.preventDefault(); if (!editSaving) doSaveClassification(false)
                  }
                }}
              >
                {/* Suggestion strip */}
                {(suggestion || suggestLoading) && (
                  <div className="bg-[#EFF6FF] border border-[#BFDBFE] rounded-lg px-4 py-3 flex items-start gap-3">
                    <Sparkles className="w-4 h-4 text-[#1D4ED8] flex-shrink-0 mt-0.5" />
                    <div className="text-sm flex-1">
                      {suggestLoading ? (
                        <p className="text-[#1E40AF]">Looking up a suggestion…</p>
                      ) : suggestion ? (
                        <>
                          <p className="text-[#1E40AF]">
                            Suggested label: <strong className="text-[#1D4ED8]">{suggestion}</strong>
                            {suggestRule && <span className="text-[#3B82F6]"> &mdash; {suggestRule}</span>}
                          </p>
                          <button
                            type="button"
                            className="text-xs font-semibold text-[#1D4ED8] underline decoration-dotted mt-1 hover:text-[#1E3A8A]"
                            onClick={() => setEditFsLine(suggestion)}
                          >Use this suggestion ↩</button>
                        </>
                      ) : null}
                    </div>
                  </div>
                )}

                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                  <div className="lg:col-span-2">
                    <label className="flex items-center gap-1.5 text-xs text-[#374151] font-semibold mb-1">
                      MA tree line
                      <span
                        className="inline-flex items-center"
                        title="Pick the Management Accounts label this account rolls into on the BS / P&L. Use the dropdown — the listed labels are what the MA workbook expects (no typos)."
                      ><HelpCircle className="w-3 h-3 text-[#9CA3AF]" /></span>
                    </label>
                    <input
                      ref={fsInputRef}
                      list="fs-line-options"
                      type="text" value={editFsLine}
                      onChange={(e) => setEditFsLine(e.target.value)}
                      placeholder="Start typing — e.g. Bank…  PPE…  Trade Recv…"
                      className="w-full bg-white border border-[#D1D5DB] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[#F07F00] focus:ring-[3px] focus:ring-[rgba(240,127,0,0.1)]"
                      autoComplete="off"
                    />
                    <datalist id="fs-line-options">
                      {allLabels.map(l => <option key={l} value={l} />)}
                    </datalist>
                    {editFsLine.trim() && (
                      findGroupOf(editFsLine) ? (
                        <p className="text-xs text-[#059669] mt-1">
                          ✓ Routes to: <span className="font-medium">{findGroupOf(editFsLine)}</span>
                        </p>
                      ) : (
                        <p className="text-xs text-[#CC6C00] mt-1">
                          ⚠ This label is not in the {account.owner_company_code || 'company'} MA tree.
                          The report will silently drop this account. Pick from the dropdown.
                        </p>
                      )
                    )}
                    <p className="text-xs text-[#6B7280] mt-1">Leave blank to unmap.</p>
                  </div>
                  <div>
                    <label className="block text-xs text-[#374151] font-semibold mb-1">Balance side</label>
                    <select
                      value={editAcctType}
                      onChange={(e) => setEditAcctType(e.target.value)}
                      className="w-full bg-white border border-[#D1D5DB] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[#F07F00] focus:ring-[3px] focus:ring-[rgba(240,127,0,0.1)]"
                    >
                      {[
                        ['asset', 'Asset'],
                        ['liability', 'Liability'],
                        ['equity', 'Equity'],
                        ['revenue', 'Revenue (Income)'],
                        ['expense', 'Expense'],
                        ['equity_unaffected', 'Equity – Unaffected Earnings'],
                      ].map(([v, lbl]) => (
                        <option key={v} value={v}>{lbl}</option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="block text-xs text-[#374151] font-semibold mb-1">Statement</label>
                    <select
                      value={editStmtClass}
                      onChange={(e) => setEditStmtClass(e.target.value)}
                      className="w-full bg-white border border-[#D1D5DB] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[#F07F00] focus:ring-[3px] focus:ring-[rgba(240,127,0,0.1)]"
                    >
                      <option value="">— unchanged —</option>
                      <option value="BS">Balance Sheet</option>
                      <option value="PNL">Income Statement</option>
                    </select>
                  </div>
                  <div className="sm:col-span-2 lg:col-span-4">
                    <label className="block text-xs text-[#374151] font-semibold mb-1">
                      Sub-type <span className="text-[#9CA3AF] font-normal">(advanced — usually leave as-is)</span>
                    </label>
                    <input
                      type="text" value={editSubType}
                      onChange={(e) => setEditSubType(e.target.value)}
                      placeholder="current_asset / fixed_asset / current_liability …"
                      className="w-full bg-white border border-[#D1D5DB] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[#F07F00] focus:ring-[3px] focus:ring-[rgba(240,127,0,0.1)]"
                    />
                  </div>
                </div>

                {editError && (
                  <div className="text-sm text-[#DC2626] bg-[#FEF2F2] border border-[#FECACA] rounded-md px-3 py-2 flex items-start gap-2">
                    <AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5" />
                    <span>{editError}</span>
                  </div>
                )}
                <p className="text-xs text-[#9CA3AF]">
                  <kbd className="px-1.5 py-0.5 bg-[#F3F4F6] border border-[#E5E7EB] rounded text-[10px]">Esc</kbd> cancel
                  &nbsp;·&nbsp;
                  <kbd className="px-1.5 py-0.5 bg-[#F3F4F6] border border-[#E5E7EB] rounded text-[10px]">⌘+S</kbd> save
                </p>
              </div>
            )}
          </CardContent>
        </Card>

        {/* GL Report */}
        <Card>
          <CardHeader>
            <CardTitle>General Ledger</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap items-end gap-3 mb-4">
              <div className="flex-1 min-w-[140px]">
                <label className="block text-xs text-[#374151] font-medium mb-1">From Date</label>
                <input
                  type="date"
                  value={fromDate}
                  onChange={(e) => setFromDate(e.target.value)}
                  className="w-full bg-white border border-[#D1D5DB] rounded-lg px-3 py-2 text-sm text-[#111827] focus:outline-none focus:border-[#F07F00] focus:ring-[3px] focus:ring-[rgba(240,127,0,0.1)] transition-all"
                />
              </div>
              <div className="flex-1 min-w-[140px]">
                <label className="block text-xs text-[#374151] font-medium mb-1">To Date</label>
                <input
                  type="date"
                  value={toDate}
                  onChange={(e) => setToDate(e.target.value)}
                  className="w-full bg-white border border-[#D1D5DB] rounded-lg px-3 py-2 text-sm text-[#111827] focus:outline-none focus:border-[#F07F00] focus:ring-[3px] focus:ring-[rgba(240,127,0,0.1)] transition-all"
                />
              </div>
              <Button variant="primary" size="md" onClick={loadGL} loading={glLoading}>
                Generate
              </Button>
            </div>

            {glLoading ? (
              <LoadingTable rows={6} cols={7} />
            ) : !glReport ? (
              <div className="py-8 text-center text-[#9CA3AF] text-sm">
                Select a date range and click Generate to view the general ledger.
              </div>
            ) : (
              <div className="overflow-x-auto">
                <div className="flex items-center justify-between px-4 py-2 bg-[#F9FAFB] border border-[#E5E7EB] rounded-t-lg text-sm">
                  <span className="text-[#6B7280] font-medium">Opening Balance</span>
                  <span className="font-mono-nums text-[#374151] font-semibold">
                    {fmt(glReport.opening_balance, account.currency)}
                  </span>
                </div>

                <table className="w-full text-sm border-collapse">
                  <thead className="bg-[#F3F4F6] border-x border-[#E5E7EB]">
                    <tr>
                      <th className="px-4 py-2.5 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Date</th>
                      <th className="px-4 py-2.5 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Entry #</th>
                      <th className="px-4 py-2.5 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Description</th>
                      <th className="px-4 py-2.5 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider hidden md:table-cell">Type</th>
                      <th className="px-4 py-2.5 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider">Debit</th>
                      <th className="px-4 py-2.5 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider">Credit</th>
                      <th className="px-4 py-2.5 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider">Balance</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#E5E7EB] border-x border-[#E5E7EB] bg-white">
                    {glReport.lines.length === 0 ? (
                      <tr>
                        <td colSpan={7} className="px-4 py-8 text-center text-[#9CA3AF] text-sm">
                          No transactions in this period
                        </td>
                      </tr>
                    ) : (
                      glReport.lines.map((line, idx) => (
                        <tr key={idx} className="table-row-alt hover:bg-[#FFF7ED] transition-colors">
                          <td className="px-4 py-2.5 text-[#6B7280] text-xs whitespace-nowrap">{formatDate(line.date)}</td>
                          <td className="px-4 py-2.5 font-mono text-xs text-[#CC6C00]">{line.entry_number}</td>
                          <td className="px-4 py-2.5 text-[#374151] max-w-[200px] truncate">{line.description}</td>
                          <td className="px-4 py-2.5 text-[#9CA3AF] text-xs hidden md:table-cell">{line.journal_type?.replace(/_/g, ' ')}</td>
                          <td className="px-4 py-2.5 text-right font-mono-nums text-[#059669]">
                            {parseAmount(line.debit) > 0 ? fmt(line.debit, account.currency) : '—'}
                          </td>
                          <td className="px-4 py-2.5 text-right font-mono-nums text-[#DC2626]">
                            {parseAmount(line.credit) > 0 ? fmt(line.credit, account.currency) : '—'}
                          </td>
                          <td className="px-4 py-2.5 text-right font-mono-nums text-[#111827] font-medium">
                            {fmt(line.running_balance, account.currency)}
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                  {glReport.lines.length > 0 && (
                    <tfoot className="bg-[#F3F4F6] border border-[#E5E7EB]">
                      <tr>
                        <td colSpan={4} className="px-4 py-3 text-xs font-semibold text-[#6B7280] uppercase">Totals</td>
                        <td className="px-4 py-3 text-right font-semibold font-mono-nums text-[#059669]">
                          {fmt(glReport.totals.total_debits, account.currency)}
                        </td>
                        <td className="px-4 py-3 text-right font-semibold font-mono-nums text-[#DC2626]">
                          {fmt(glReport.totals.total_credits, account.currency)}
                        </td>
                        <td className="px-4 py-3 text-right font-semibold font-mono-nums text-[#0B0B3B]">
                          {fmt(glReport.totals.closing_balance, account.currency)}
                        </td>
                      </tr>
                    </tfoot>
                  )}
                </table>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
