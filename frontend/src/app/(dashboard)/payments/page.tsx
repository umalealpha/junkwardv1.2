'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import {
  getPayments, getToken,
  confirmPayment, deletePayment, duplicatePayment, resetPaymentToDraft,
  exportEftBatch,
} from '@/lib/api'
import type { Payment } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { LoadingTable } from '@/components/ui/loading'
import { ConfirmDialog } from '@/components/ui/modal'
import { cn, formatAmount, localYmd, parseAmount } from '@/lib/utils'
import { useNumberFormat } from '@/contexts/NumberFormatContext'
import { useCompany } from '@/contexts/CompanyContext'
import * as DropdownMenu from '@radix-ui/react-dropdown-menu'
import {
  Plus, Upload, Search, CreditCard, AlertCircle, ChevronLeft, ChevronRight,
  X, Settings, Copy, Trash2, RotateCcw, CheckCircle, ArrowDownLeft, ArrowUpRight,
  FileDown,
} from 'lucide-react'

// ─── Helpers ──────────────────────────────────────────────────────────────────

function dmy(dateStr: string): string {
  if (!dateStr) return ''
  const d = new Date(dateStr + (dateStr.includes('T') ? '' : 'T00:00:00'))
  if (isNaN(d.getTime())) return dateStr
  return `${String(d.getDate()).padStart(2, '0')}/${String(d.getMonth() + 1).padStart(2, '0')}/${d.getFullYear()}`
}

function statusInfo(p: Payment): { label: string; bg: string; fg: string } {
  switch (p.status) {
    case 'confirmed':  return { label: 'Confirmed',  bg: '#ECFDF5', fg: '#059669' }
    case 'reconciled': return { label: 'Reconciled', bg: '#ECFDF5', fg: '#059669' }
    case 'cancelled':  return { label: 'Cancelled',  bg: '#F3F4F6', fg: '#6B7280' }
    case 'draft':      return { label: 'Draft',      bg: '#EFF6FF', fg: '#2563EB' }
    default:           return { label: p.status,     bg: '#F3F4F6', fg: '#6B7280' }
  }
}

const CURRENCY_SYMBOL: Record<string, string> = {
  BWP: 'P', USD: '$', ZAR: 'R', INR: '₹', ZMW: 'ZK',
}

// ─── Tabs ──────────────────────────────────────────────────────────────────────

const TABS = [
  { value: 'all',       label: 'All' },
  { value: 'sent',      label: 'Vendor Payments' },
  { value: 'received',  label: 'Customer Payments' },
  { value: 'draft',     label: 'Draft' },
  { value: 'confirmed', label: 'Confirmed' },
]

// ─── Page ──────────────────────────────────────────────────────────────────────

export default function PaymentsPage() {
  const router = useRouter()
  const { mode } = useNumberFormat()
  const fmt = (amount: string | number, currency = 'BWP') => formatAmount(amount, currency, mode)
  const { selectedId: companyId } = useCompany()

  const [payments, setPayments] = useState<Payment[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  // Initialise the tab from the URL BEFORE the first load so a Receipts view
  // (/payments?type=receipt) never briefly fetches/shows outbound PAY-OUTs
  // (CFO 2026-08-30). type=payment shows money OUT; anything else = "All".
  const [activeTab, setActiveTab] = useState(() => {
    if (typeof window === 'undefined') return 'all'
    const t = new URLSearchParams(window.location.search).get('type')
    return t === 'receipt' ? 'received' : t === 'payment' ? 'sent' : 'all'
  })
  // Receipts view (/payments?type=receipt) is a hard-locked PAY-IN-only view
  // (bug d1cd235d — an audit page for customer receipts must NEVER show vendor
  // pay-outs). When locked we force payment_type=received on every fetch and
  // hide the tab switcher so it cannot be flipped to All / Vendor Payments.
  const [receiptLocked] = useState(() => {
    if (typeof window === 'undefined') return false
    return new URLSearchParams(window.location.search).get('type') === 'receipt'
  })
  const [page, setPage] = useState(1)
  const [totalCount, setTotalCount] = useState(0)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [acting, setActing] = useState(false)
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)
  const PAGE_SIZE = 25

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const params: Record<string, string> = { page: String(page), page_size: String(PAGE_SIZE), ordering: '-payment_date' }
      if (receiptLocked)                  params.payment_type = 'received'
      else if (activeTab === 'received')  params.payment_type = 'received'
      else if (activeTab === 'sent')      params.payment_type = 'sent'
      else if (activeTab === 'confirmed') params.status = 'confirmed'
      else if (activeTab === 'draft')     params.status = 'draft'
      if (search) params.search = search
      if (companyId) params.company = companyId
      const res = await getPayments(params)
      setPayments(res.results)
      setTotalCount(res.count)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load payments')
    } finally { setLoading(false) }
  }, [activeTab, page, search, companyId, receiptLocked])

  useEffect(() => {
    const token = getToken()
    if (!token) { router.replace('/login'); return }
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load])

  const handleTabChange = (value: string) => { setActiveTab(value); setPage(1); setSelected(new Set()) }
  const totalPages = Math.max(1, Math.ceil(totalCount / PAGE_SIZE))
  const rangeStart = totalCount === 0 ? 0 : (page - 1) * PAGE_SIZE + 1
  const rangeEnd = Math.min(page * PAGE_SIZE, totalCount)
  const selectedPayments = payments.filter((p) => selected.has(p.id))
  const allOnPageSelected = payments.length > 0 && payments.every((p) => selected.has(p.id))
  const someOnPageSelected = payments.some((p) => selected.has(p.id))

  const toggleOne = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }
  const toggleAllOnPage = () => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (allOnPageSelected) payments.forEach((p) => next.delete(p.id))
      else payments.forEach((p) => next.add(p.id))
      return next
    })
  }
  const clearSelection = () => setSelected(new Set())

  // ─── Bulk action handlers ───────────────────────────────────────────────────

  const showResult = (verb: string, ok: number, errors: string[]) => {
    if (errors.length === 0) {
      setSuccess(`${verb} ${ok} payment${ok === 1 ? '' : 's'} successfully`)
      setTimeout(() => setSuccess(null), 4000)
    } else {
      setError(`${verb} succeeded for ${ok}, failed for ${errors.length}: ${errors.slice(0, 3).join('; ')}`)
    }
  }

  const runBulk = async (
    verb: string,
    fn: (p: Payment) => Promise<void>,
    filter?: (p: Payment) => string | null,
  ) => {
    setActing(true); setError(null); setSuccess(null)
    let ok = 0
    const errors: string[] = []
    for (const p of selectedPayments) {
      if (filter) {
        const msg = filter(p)
        if (msg) { errors.push(`${p.payment_number}: ${msg}`); continue }
      }
      try { await fn(p); ok += 1 }
      catch (e) { errors.push(`${p.payment_number}: ${(e as Error).message || 'failed'}`) }
    }
    showResult(verb, ok, errors)
    setActing(false)
    clearSelection()
    await load()
  }

  const handleDuplicate = () => runBulk('Duplicated', async (p) => { await duplicatePayment(p.id) })
  const handleConfirm   = () => runBulk('Confirmed', async (p) => { await confirmPayment(p.id) },
    (p) => p.status !== 'draft' ? 'only drafts can be confirmed'
      : p.payment_type === 'sent' ? 'outbound payments post via the approval quorum — submit for approval instead'
      : null)
  const handleReset     = () => runBulk('Reset',     async (p) => { await resetPaymentToDraft(p.id) },
    (p) => p.status !== 'confirmed' ? 'only confirmed payments can be reset' : null)
  const performDelete = async () => {
    setShowDeleteConfirm(false)
    await runBulk('Deleted', async (p) => { await deletePayment(p.id) },
      (p) => p.status !== 'draft' ? 'only drafts can be deleted' : null)
  }
  const handleDelete = () => setShowDeleteConfirm(true)

  const eligibleForDelete  = selectedPayments.filter((p) => p.status === 'draft').length
  const eligibleForConfirm = selectedPayments.filter((p) => p.status === 'draft').length
  const eligibleForReset   = selectedPayments.filter((p) => p.status === 'confirmed').length
  const eligibleForEft     = selectedPayments.filter(
    (p) => p.status === 'confirmed' && p.payment_type === 'sent',
  ).length

  const handleEftExport = async () => {
    if (eligibleForEft === 0) return
    const sourceAcct = window.prompt(
      'Source bank account number (the company account paying out, e.g. FNB BWP operating):',
      '62123456789',
    )
    if (!sourceAcct || !sourceAcct.trim()) return
    const ref = window.prompt('Batch reference (shows on the bank file):',
      `PAYRUN-${localYmd(new Date())}`)
    if (!ref) return
    setActing(true); setError(null); setSuccess(null)
    try {
      const ids = selectedPayments
        .filter((p) => p.status === 'confirmed' && p.payment_type === 'sent')
        .map((p) => p.id)
      const { filename, summary } = await exportEftBatch({
        payment_ids: ids,
        source_account_number: sourceAcct.trim(),
        batch_ref: ref.trim(),
      })
      const skipped = (summary?.skipped || []).length
      setSuccess(
        `Downloaded ${filename} — ${summary?.record_count || 0} payment${summary?.record_count === 1 ? '' : 's'} in batch, total BWP ${summary?.total_amount || '0.00'}` +
        (skipped > 0 ? ` (${skipped} row${skipped === 1 ? '' : 's'} skipped — see X-EFT-Summary)` : '')
      )
      setTimeout(() => setSuccess(null), 8000)
    } catch (e) {
      setError((e as Error).message || 'EFT export failed')
    } finally { setActing(false); clearSelection() }
  }

  const hasSelection = selected.size > 0
  const inputCls = 'h-10 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm text-[#111827] placeholder-[#9CA3AF] focus:outline-none focus:border-[#F07F00] focus:ring-[3px] focus:ring-[rgba(240,127,0,0.1)] transition-all'
  const menuItemCls = 'flex items-center gap-2 px-3 py-2 text-sm text-[#374151] hover:bg-[#FFF7ED] hover:text-[#0B0B3B] rounded cursor-pointer outline-none data-[disabled]:opacity-40 data-[disabled]:cursor-not-allowed'

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Payments"
        breadcrumbs={[{ label: 'Finance' }, { label: 'Payments' }]}
        actions={
          <div className="flex items-center gap-2">
            <Button variant="accent" size="sm" leftIcon={<Plus className="w-3.5 h-3.5" />} onClick={() => router.push('/payments/new')}>
              New
            </Button>
            <Button variant="secondary" size="sm" leftIcon={<Upload className="w-3.5 h-3.5" />} onClick={() => router.push('/quick-entry')}>
              Scan doc
            </Button>
            <Button variant="secondary" size="sm" leftIcon={<Upload className="w-3.5 h-3.5" />} onClick={() => router.push('/payments/upload')}>
              Bulk pay
            </Button>
            <Button variant="secondary" size="sm" leftIcon={<Plus className="w-3.5 h-3.5" />} onClick={() => router.push('/payments/once-off')}>
              One-off
            </Button>
          </div>
        }
      />

      <div className="flex-1 p-6 space-y-4">
        <Tabs value={activeTab} onValueChange={handleTabChange}>
          {!receiptLocked && (
            <TabsList variant="underline">
              {TABS.map((tab) => (
                <TabsTrigger key={tab.value} value={tab.value}>{tab.label}</TabsTrigger>
              ))}
            </TabsList>
          )}

          {/* Selection toolbar OR search/pagination */}
          {hasSelection ? (
            <div className="mt-4 flex items-center gap-2 flex-wrap">
              <button
                onClick={clearSelection}
                className="inline-flex items-center gap-2 px-3 py-1.5 rounded-md text-sm font-medium bg-[#FFF7ED] text-[#CC6C00] border border-[#FFD7B5] hover:bg-[#FFEDD5]"
              >
                {selected.size} selected
                <X className="w-3.5 h-3.5" />
              </button>
              <Button
                variant="secondary" size="sm"
                leftIcon={<CheckCircle className="w-3.5 h-3.5" />}
                onClick={handleConfirm}
                disabled={acting || eligibleForConfirm === 0}
              >
                Confirm {eligibleForConfirm > 0 && eligibleForConfirm < selected.size ? `(${eligibleForConfirm})` : ''}
              </Button>
              <DropdownMenu.Root>
                <DropdownMenu.Trigger asChild>
                  <button
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium bg-white border border-[#D1D5DB] text-[#374151] hover:bg-[#F9FAFB] disabled:opacity-50"
                    disabled={acting}
                  >
                    <Settings className="w-3.5 h-3.5" />
                    Actions
                  </button>
                </DropdownMenu.Trigger>
                <DropdownMenu.Portal>
                  <DropdownMenu.Content
                    align="start"
                    sideOffset={4}
                    className="z-50 bg-white border border-[#E5E7EB] rounded-md shadow-lg py-1 min-w-[220px]"
                  >
                    <DropdownMenu.Item className={menuItemCls} onSelect={handleDuplicate}>
                      <Copy className="w-3.5 h-3.5" />Duplicate
                    </DropdownMenu.Item>
                    <DropdownMenu.Item
                      className={cn(menuItemCls, eligibleForDelete === 0 && 'opacity-40 cursor-not-allowed')}
                      onSelect={(e) => { if (eligibleForDelete === 0) { e.preventDefault(); return } handleDelete() }}
                    >
                      <Trash2 className="w-3.5 h-3.5" />Delete
                      {selected.size > 0 && eligibleForDelete < selected.size && (
                        <span className="ml-auto text-[10px] text-[#6B7280]">drafts only ({eligibleForDelete}/{selected.size})</span>
                      )}
                    </DropdownMenu.Item>
                    <DropdownMenu.Item
                      className={cn(menuItemCls, eligibleForConfirm === 0 && 'opacity-40 cursor-not-allowed')}
                      onSelect={(e) => { if (eligibleForConfirm === 0) { e.preventDefault(); return } handleConfirm() }}
                    >
                      <CheckCircle className="w-3.5 h-3.5" />Confirm
                      {selected.size > 0 && eligibleForConfirm < selected.size && (
                        <span className="ml-auto text-[10px] text-[#6B7280]">drafts only ({eligibleForConfirm}/{selected.size})</span>
                      )}
                    </DropdownMenu.Item>
                    <DropdownMenu.Item
                      className={cn(menuItemCls, eligibleForReset === 0 && 'opacity-40 cursor-not-allowed')}
                      onSelect={(e) => { if (eligibleForReset === 0) { e.preventDefault(); return } handleReset() }}
                    >
                      <RotateCcw className="w-3.5 h-3.5" />Reset to Draft
                      {selected.size > 0 && eligibleForReset < selected.size && (
                        <span className="ml-auto text-[10px] text-[#6B7280]">confirmed only ({eligibleForReset}/{selected.size})</span>
                      )}
                    </DropdownMenu.Item>
                    <DropdownMenu.Separator className="h-px bg-[#E5E7EB] my-1" />
                    <DropdownMenu.Item
                      className={cn(menuItemCls, eligibleForEft === 0 && 'opacity-40 cursor-not-allowed')}
                      onSelect={(e) => { if (eligibleForEft === 0) { e.preventDefault(); return } handleEftExport() }}
                    >
                      <FileDown className="w-3.5 h-3.5" />Export FNB BOL (EFT batch)
                      {selected.size > 0 && eligibleForEft < selected.size && (
                        <span className="ml-auto text-[10px] text-[#6B7280]">confirmed sent only ({eligibleForEft}/{selected.size})</span>
                      )}
                    </DropdownMenu.Item>
                  </DropdownMenu.Content>
                </DropdownMenu.Portal>
              </DropdownMenu.Root>
            </div>
          ) : (
            <div className="flex items-center justify-between gap-3 mt-4">
              <div className="relative flex-1 max-w-md">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[#6B7280] pointer-events-none" strokeWidth={1.5} />
                <input
                  type="text"
                  placeholder="Search..."
                  value={search}
                  onChange={(e) => { setSearch(e.target.value); setPage(1) }}
                  className={cn(inputCls, 'pl-9 w-full')}
                />
              </div>
              <div className="flex items-center gap-3 text-sm text-[#6B7280]">
                <span className="font-medium font-mono-nums tabular-nums">
                  {rangeStart}-{rangeEnd} / {totalCount}
                </span>
                <div className="flex items-center gap-1">
                  <button type="button" className="p-1.5 rounded hover:bg-[#F3F4F6] disabled:opacity-30 disabled:cursor-not-allowed" disabled={page === 1} onClick={() => setPage(page - 1)} aria-label="Previous page">
                    <ChevronLeft className="w-4 h-4" />
                  </button>
                  <button type="button" className="p-1.5 rounded hover:bg-[#F3F4F6] disabled:opacity-30 disabled:cursor-not-allowed" disabled={page >= totalPages} onClick={() => setPage(page + 1)} aria-label="Next page">
                    <ChevronRight className="w-4 h-4" />
                  </button>
                </div>
              </div>
            </div>
          )}

          {success && (
            <div className="bg-[#ECFDF5] border border-[#A7F3D0] rounded-lg p-3 mt-2">
              <p className="text-[#059669] text-sm">{success}</p>
            </div>
          )}
          {error && (
            <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2 mt-2">
              <AlertCircle className="w-4 h-4 text-[#DC2626] flex-shrink-0" />
              <p className="text-[#DC2626] text-sm">{error}</p>
            </div>
          )}

          <TabsContent value={activeTab} className="mt-4">
            <Card>
              <CardContent className="p-0">
                {loading ? (
                  <LoadingTable rows={8} cols={9} />
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm border-collapse">
                      <thead className="bg-[#F9FAFB] border-b border-[#E5E7EB]">
                        <tr>
                          <th className="w-10 px-3 py-3">
                            <input
                              type="checkbox"
                              className="rounded border-[#D1D5DB]"
                              checked={allOnPageSelected}
                              ref={(el) => { if (el) el.indeterminate = !allOnPageSelected && someOnPageSelected }}
                              onChange={toggleAllOnPage}
                              aria-label="Select all on page"
                            />
                          </th>
                          <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Number</th>
                          <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Direction</th>
                          <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Contact</th>
                          <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider whitespace-nowrap">Date</th>
                          <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider whitespace-nowrap">Method</th>
                          <th className="px-3 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider">Amount</th>
                          <th title="Amount in transaction currency" className="px-3 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider whitespace-nowrap">Amount in Currency</th>
                          <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Status</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-[#E5E7EB] bg-white">
                        {payments.length === 0 ? (
                          <tr>
                            <td colSpan={9} className="px-4 py-16 text-center">
                              <CreditCard className="w-8 h-8 text-[#D1D5DB] mx-auto mb-2" />
                              <p className="text-[#6B7280] text-sm font-medium">No payments found</p>
                            </td>
                          </tr>
                        ) : (
                          payments.map((p) => {
                            const stat = statusInfo(p)
                            const sym = CURRENCY_SYMBOL[p.currency] || p.currency
                            const isReceived = p.payment_type === 'received'
                            const cancelled = p.status === 'cancelled'
                            const isSelected = selected.has(p.id)
                            return (
                              <tr
                                key={p.id}
                                onClick={() => router.push(`/payments/${p.id}`)}
                                className={cn(
                                  'cursor-pointer transition-colors',
                                  isSelected ? 'bg-[#FFF7ED] hover:bg-[#FFEDD5]' : 'hover:bg-[#FFF7ED]',
                                  cancelled && 'opacity-50',
                                )}
                              >
                                <td className="px-3 py-3" onClick={(e) => e.stopPropagation()}>
                                  <input
                                    type="checkbox"
                                    className="rounded border-[#D1D5DB]"
                                    checked={isSelected}
                                    onChange={() => toggleOne(p.id)}
                                    aria-label={`Select ${p.payment_number}`}
                                  />
                                </td>
                                <td className="px-3 py-3 font-mono text-xs text-[#CC6C00] font-semibold whitespace-nowrap">{p.payment_number}</td>
                                <td className="px-3 py-3">
                                  <span className={cn(
                                    'inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium',
                                    isReceived ? 'bg-[#ECFDF5] text-[#059669]' : 'bg-[#FEF2F2] text-[#DC2626]'
                                  )}>
                                    {isReceived ? <ArrowDownLeft className="w-3 h-3" /> : <ArrowUpRight className="w-3 h-3" />}
                                    {isReceived ? 'IN' : 'OUT'}
                                  </span>
                                </td>
                                <td className="px-3 py-3 text-[#111827] max-w-[260px] truncate">{p.contact_name}</td>
                                <td className="px-3 py-3 text-[#374151] whitespace-nowrap">{dmy(p.payment_date)}</td>
                                <td className="px-3 py-3 text-[#6B7280] text-xs capitalize whitespace-nowrap">{p.payment_method?.replace(/_/g, ' ')}</td>
                                <td className={cn('px-3 py-3 text-right font-mono-nums font-semibold whitespace-nowrap', isReceived ? 'text-[#059669]' : 'text-[#DC2626]')}>
                                  {isReceived ? '+' : '-'}{fmt(parseAmount(p.amount_bwp), 'BWP')}
                                </td>
                                <td className="px-3 py-3 text-right font-mono-nums text-[#374151] whitespace-nowrap">
                                  {p.currency === 'BWP'
                                    ? fmt(parseAmount(p.amount), 'BWP')
                                    : `${sym} ${fmt(parseAmount(p.amount), p.currency).replace(/[^\d.,\s-]/g, '').trim()}`}
                                </td>
                                <td className="px-3 py-3">
                                  <span className="inline-flex items-center px-2.5 py-1 rounded-md text-[11px] font-medium" style={{ background: stat.bg, color: stat.fg }}>
                                    {stat.label}
                                  </span>
                                </td>
                              </tr>
                            )
                          })
                        )}
                      </tbody>
                    </table>
                  </div>
                )}
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </div>

      <ConfirmDialog
        open={showDeleteConfirm}
        onOpenChange={setShowDeleteConfirm}
        title="Delete Payments"
        description={`Delete ${eligibleForDelete} draft payment${eligibleForDelete === 1 ? '' : 's'}? This cannot be undone.${selected.size > eligibleForDelete ? ` (${selected.size - eligibleForDelete} non-draft will be skipped.)` : ''}`}
        confirmLabel="Delete"
        variant="danger"
        loading={acting}
        onConfirm={performDelete}
      />
    </div>
  )
}
