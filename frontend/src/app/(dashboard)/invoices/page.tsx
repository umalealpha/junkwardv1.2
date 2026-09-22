'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import {
  getInvoices, getToken,
  postInvoice, deleteInvoice, duplicateInvoice, reverseInvoice, openInvoicePdf,
} from '@/lib/api'
import type { Invoice } from '@/lib/api'
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
  Plus, Upload, Search, FileText, AlertCircle, ChevronLeft, ChevronRight, Clock,
  X, Printer, CreditCard, Settings, Copy, Trash2, RotateCcw, Send, ScanLine,
} from 'lucide-react'

// ─── Helpers ──────────────────────────────────────────────────────────────────

function dmy(dateStr: string): string {
  if (!dateStr) return ''
  const d = new Date(dateStr + (dateStr.includes('T') ? '' : 'T00:00:00'))
  if (isNaN(d.getTime())) return dateStr
  return `${String(d.getDate()).padStart(2, '0')}/${String(d.getMonth() + 1).padStart(2, '0')}/${d.getFullYear()}`
}

function daysUntil(dateStr: string): number | null {
  if (!dateStr) return null
  const d = new Date(dateStr + (dateStr.includes('T') ? '' : 'T00:00:00'))
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  if (isNaN(d.getTime())) return null
  return Math.floor((d.getTime() - today.getTime()) / (1000 * 60 * 60 * 24))
}

function dueDateCell(inv: Invoice): { text: string; tone: 'red' | 'amber' | 'muted' | 'plain' } {
  if (inv.status === 'cancelled' || inv.status === 'paid') {
    return { text: dmy(inv.due_date), tone: 'muted' }
  }
  const diff = daysUntil(inv.due_date)
  if (diff === null) return { text: '', tone: 'muted' }
  if (diff < 0) return { text: dmy(inv.due_date), tone: 'red' }
  if (diff === 0) return { text: 'Today', tone: 'amber' }
  if (diff <= 30) return { text: `In ${diff} day${diff === 1 ? '' : 's'}`, tone: diff <= 7 ? 'amber' : 'plain' }
  return { text: dmy(inv.due_date), tone: 'plain' }
}

function paymentInfo(inv: Invoice): { label: string; bg: string; fg: string } | null {
  if (inv.status === 'cancelled' || inv.status === 'draft') return null
  const total = parseAmount(inv.total_amount)
  const paid = parseAmount(inv.amount_paid)
  if (paid >= total && total > 0) return { label: 'Paid',           bg: '#ECFDF5', fg: '#059669' }
  if (paid > 0)                   return { label: 'Partially Paid', bg: '#FFFBEB', fg: '#D97706' }
  return { label: 'Not Paid', bg: '#FEF2F2', fg: '#DC2626' }
}

function statusInfo(inv: Invoice): { label: string; bg: string; fg: string } {
  switch (inv.status) {
    case 'posted':         return { label: 'Posted',    bg: '#ECFDF5', fg: '#059669' }
    case 'partially_paid': return { label: 'Posted',    bg: '#ECFDF5', fg: '#059669' }
    case 'paid':           return { label: 'Posted',    bg: '#ECFDF5', fg: '#059669' }
    case 'overdue':        return { label: 'Posted',    bg: '#ECFDF5', fg: '#059669' }
    case 'cancelled':      return { label: 'Cancelled', bg: '#F3F4F6', fg: '#6B7280' }
    case 'draft':          return { label: 'Draft',     bg: '#EFF6FF', fg: '#2563EB' }
    default:               return { label: inv.status,  bg: '#F3F4F6', fg: '#6B7280' }
  }
}

const CURRENCY_SYMBOL: Record<string, string> = {
  BWP: 'P', USD: '$', ZAR: 'R', INR: '₹', ZMW: 'ZK',
}

// ─── Tabs ──────────────────────────────────────────────────────────────────────

const TABS = [
  { value: 'customer_invoice', label: 'Customer Invoices' },
  { value: 'vendor_bill',      label: 'Vendor Bills' },
  { value: 'all',              label: 'All' },
  { value: 'draft',            label: 'Draft' },
  { value: 'posted',           label: 'Posted' },
  { value: 'overdue',          label: 'Overdue' },
]

// ─── Page ──────────────────────────────────────────────────────────────────────

export default function InvoicesPage() {
  const router = useRouter()
  const { mode } = useNumberFormat()
  const fmt = (amount: string | number, currency = 'BWP') => formatAmount(amount, currency, mode)
  const { selectedId: companyId, selected: selectedCompany } = useCompany()

  const [invoices, setInvoices] = useState<Invoice[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [activeTab, setActiveTab] = useState('customer_invoice')
  const [page, setPage] = useState(1)
  const [totalCount, setTotalCount] = useState(0)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [acting, setActing] = useState(false)
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)
  const PAGE_SIZE = 25

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params: Record<string, string> = { page: String(page), page_size: String(PAGE_SIZE) }
      if (activeTab === 'customer_invoice' || activeTab === 'vendor_bill') params.invoice_type = activeTab
      else if (activeTab === 'draft')   params.status = 'draft'
      else if (activeTab === 'posted')  params.status = 'posted'
      else if (activeTab === 'overdue') params.status = 'overdue'
      if (search) params.search = search
      if (companyId) params.company = companyId
      const res = await getInvoices(params)
      setInvoices(res.results)
      setTotalCount(res.count)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load invoices')
    } finally {
      setLoading(false)
    }
  }, [activeTab, page, search, companyId])

  useEffect(() => {
    const token = getToken()
    if (!token) { router.replace('/login'); return }
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load])

  const handleTabChange = (value: string) => {
    setActiveTab(value); setPage(1); setSelected(new Set())
  }
  const totalPages = Math.max(1, Math.ceil(totalCount / PAGE_SIZE))
  const rangeStart = totalCount === 0 ? 0 : (page - 1) * PAGE_SIZE + 1
  const rangeEnd = Math.min(page * PAGE_SIZE, totalCount)
  const selectedInvoices = invoices.filter((i) => selected.has(i.id))
  const allOnPageSelected = invoices.length > 0 && invoices.every((i) => selected.has(i.id))
  const someOnPageSelected = invoices.some((i) => selected.has(i.id))

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
      if (allOnPageSelected) invoices.forEach((i) => next.delete(i.id))
      else invoices.forEach((i) => next.add(i.id))
      return next
    })
  }
  const clearSelection = () => setSelected(new Set())

  // ─── Bulk-action handlers ───────────────────────────────────────────────────

  const showResult = (verb: string, ok: number, errors: string[]) => {
    if (errors.length === 0) {
      setSuccess(`${verb} ${ok} invoice${ok === 1 ? '' : 's'} successfully`)
      setTimeout(() => setSuccess(null), 4000)
    } else {
      setError(`${verb} succeeded for ${ok}, failed for ${errors.length}: ${errors.slice(0, 3).join('; ')}`)
    }
  }

  const runBulk = async (
    verb: string,
    fn: (inv: Invoice) => Promise<void>,
    filter?: (inv: Invoice) => string | null,  // returns error message if not allowed
  ) => {
    setActing(true); setError(null); setSuccess(null)
    let ok = 0
    const errors: string[] = []
    for (const inv of selectedInvoices) {
      if (filter) {
        const msg = filter(inv)
        if (msg) { errors.push(`${inv.invoice_number}: ${msg}`); continue }
      }
      try {
        await fn(inv); ok += 1
      } catch (e) {
        errors.push(`${inv.invoice_number}: ${(e as Error).message || 'failed'}`)
      }
    }
    showResult(verb, ok, errors)
    setActing(false)
    clearSelection()
    await load()
  }

  const handleDuplicate    = () => runBulk('Duplicated', async (i) => { await duplicateInvoice(i.id) })
  const handleDelete       = () => setShowDeleteConfirm(true)
  const performDelete = async () => {
    setShowDeleteConfirm(false)
    await runBulk('Deleted', async (i) => { await deleteInvoice(i.id) },
      (i) => i.status !== 'draft' ? 'only drafts can be deleted' : null)
  }
  const eligibleForDelete = selectedInvoices.filter((i) => i.status === 'draft').length
  const eligibleForReverse = selectedInvoices.filter((i) => i.status === 'posted').length
  const eligibleForPost   = selectedInvoices.filter((i) => i.status === 'draft').length
  const handleReverse      = () => runBulk('Reversed', async (i) => { await reverseInvoice(i.id) },
    (i) => i.status !== 'posted' ? 'only posted invoices can be reversed' : null)
  const handlePostEntries  = () => runBulk('Posted',   async (i) => { await postInvoice(i.id) },
    (i) => i.status !== 'draft' ? 'already posted' : null)
  const handlePrint = async () => {
    setActing(true); setError(null)
    try {
      for (const inv of selectedInvoices) await openInvoicePdf(inv.id)
    } catch (e) {
      setError((e as Error).message || 'Print failed')
    }
    setActing(false)
  }
  const handleRegisterPayment = () => {
    if (selected.size !== 1) {
      setError('Register Payment requires selecting exactly one invoice'); return
    }
    const id = Array.from(selected)[0]
    router.push(`/payments/new?invoice=${id}`)
  }

  const inputCls = 'h-10 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm text-[#111827] placeholder-[#9CA3AF] focus:outline-none focus:border-[#F07F00] focus:ring-[3px] focus:ring-[rgba(240,127,0,0.1)] transition-all'
  const menuItemCls = 'flex items-center gap-2 px-3 py-2 text-sm text-[#374151] hover:bg-[#FFF7ED] hover:text-[#0B0B3B] rounded cursor-pointer outline-none data-[disabled]:opacity-40 data-[disabled]:cursor-not-allowed'

  const hasSelection = selected.size > 0

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Invoices"
        breadcrumbs={[{ label: 'Finance' }, { label: 'Invoices' }]}
        actions={
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={async () => {
              try {
                const collected: Invoice[] = []
                let p = 1
                while (true) {
                  const params: Record<string, string> = { page: String(p), page_size: String(200) }
                  if (activeTab !== 'all') params.invoice_type = activeTab
                  const res = await getInvoices(params)
                  collected.push(...res.results)
                  if (res.results.length < 200 || collected.length >= res.count) break
                  p += 1
                  if (p > 50) break
                }
                const header = ['Invoice #', 'Type', 'Status', 'Date', 'Due', 'Currency', 'Contact', 'Subtotal', 'Tax', 'Total', 'Paid', 'Balance', 'JE #']
                const rows = collected.map(i => [
                  i.invoice_number, i.invoice_type, i.status, i.issue_date, i.due_date,
                  i.currency, i.contact_name || '',
                  i.subtotal, i.tax_total, i.total_amount, i.amount_paid, i.balance_due,
                  i.je_number || '',
                ])
                const csv = [header, ...rows]
                  .map(r => r.map(c => `"${String(c).replace(/"/g, '""')}"`).join(','))
                  .join('\n')
                const blob = new Blob([csv], { type: 'text/csv' })
                const url = URL.createObjectURL(blob)
                const a = document.createElement('a')
                a.href = url
                a.download = `invoices-${localYmd(new Date())}.csv`
                a.click()
                URL.revokeObjectURL(url)
              } catch (e) {
                console.error(e)
              }
            }}>
              Export CSV
            </Button>
            <Button variant="accent" size="sm" leftIcon={<Plus className="w-3.5 h-3.5" />} onClick={() => router.push(`/invoices/new${activeTab === 'vendor_bill' ? '?type=vendor_bill' : ''}`)}>
              New
            </Button>
            <Button variant="secondary" size="sm" leftIcon={<Upload className="w-3.5 h-3.5" />} onClick={() => router.push('/quick-entry')}>
              Upload
            </Button>
          </div>
        }
      />

      <div className="flex-1 p-6 space-y-4">
        <Tabs value={activeTab} onValueChange={handleTabChange}>
          <TabsList variant="underline">
            {TABS.map((tab) => (
              <TabsTrigger key={tab.value} value={tab.value}>{tab.label}</TabsTrigger>
            ))}
          </TabsList>

          {/* Selection toolbar OR search/pagination row */}
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
                leftIcon={<CreditCard className="w-3.5 h-3.5" />}
                onClick={handleRegisterPayment}
                disabled={acting}
              >
                Register Payment
              </Button>
              <Button
                variant="secondary" size="sm"
                leftIcon={<Printer className="w-3.5 h-3.5" />}
                onClick={handlePrint}
                disabled={acting}
              >
                Print
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
                        <span className="ml-auto text-[10px] text-[#9CA3AF]">drafts only ({eligibleForDelete}/{selected.size})</span>
                      )}
                    </DropdownMenu.Item>
                    <DropdownMenu.Item
                      className={cn(menuItemCls, eligibleForReverse === 0 && 'opacity-40 cursor-not-allowed')}
                      onSelect={(e) => { if (eligibleForReverse === 0) { e.preventDefault(); return } handleReverse() }}
                    >
                      <RotateCcw className="w-3.5 h-3.5" />Reverse
                      {selected.size > 0 && eligibleForReverse < selected.size && (
                        <span className="ml-auto text-[10px] text-[#9CA3AF]">posted only ({eligibleForReverse}/{selected.size})</span>
                      )}
                    </DropdownMenu.Item>
                    <DropdownMenu.Item
                      className={cn(menuItemCls, eligibleForPost === 0 && 'opacity-40 cursor-not-allowed')}
                      onSelect={(e) => { if (eligibleForPost === 0) { e.preventDefault(); return } handlePostEntries() }}
                    >
                      <Send className="w-3.5 h-3.5" />Post entries
                      {selected.size > 0 && eligibleForPost < selected.size && (
                        <span className="ml-auto text-[10px] text-[#9CA3AF]">drafts only ({eligibleForPost}/{selected.size})</span>
                      )}
                    </DropdownMenu.Item>
                    <DropdownMenu.Item className={menuItemCls} onSelect={handleRegisterPayment}>
                      <CreditCard className="w-3.5 h-3.5" />Register Payment
                    </DropdownMenu.Item>
                    <DropdownMenu.Separator className="h-px bg-[#E5E7EB] my-1" />
                    <DropdownMenu.Item className={cn(menuItemCls, 'opacity-40 cursor-not-allowed')} disabled>
                      <ScanLine className="w-3.5 h-3.5" />Send Bills for digitization
                      <span className="ml-auto text-[10px] text-[#9CA3AF]">soon</span>
                    </DropdownMenu.Item>
                    <DropdownMenu.Item className={menuItemCls} onSelect={handlePrint}>
                      <Printer className="w-3.5 h-3.5" />Send &amp; Print
                    </DropdownMenu.Item>
                  </DropdownMenu.Content>
                </DropdownMenu.Portal>
              </DropdownMenu.Root>
            </div>
          ) : (
            <div className="flex items-center justify-between gap-3 mt-4">
              <div className="relative flex-1 max-w-md">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[#9CA3AF] pointer-events-none" strokeWidth={1.5} />
                <input
                  type="text"
                  placeholder="Search..."
                  aria-label="Search invoices"
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
            <div className="bg-[#ECFDF5] border border-[#A7F3D0] rounded-lg p-3 flex items-center gap-2 mt-2">
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
                  <LoadingTable rows={8} cols={10} />
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
                          <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider whitespace-nowrap">Company</th>
                          <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Customer</th>
                          <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider whitespace-nowrap">Invoice Date</th>
                          <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider whitespace-nowrap">Due Date</th>
                          <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Activities</th>
                          <th className="px-3 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider whitespace-nowrap">Tax Excluded</th>
                          <th className="px-3 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider">Total</th>
                          <th className="px-3 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider whitespace-nowrap">Total in Currency</th>
                          <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Payment</th>
                          <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Status</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-[#E5E7EB] bg-white">
                        {invoices.length === 0 ? (
                          <tr>
                            <td colSpan={12} className="px-4 py-16 text-center">
                              <FileText className="w-8 h-8 text-[#D1D5DB] mx-auto mb-2" />
                              <p className="text-[#6B7280] text-sm font-medium">No invoices found</p>
                            </td>
                          </tr>
                        ) : (
                          invoices.map((inv) => {
                            const due = dueDateCell(inv)
                            const dueClass = due.tone === 'red' ? 'text-[#DC2626] font-medium'
                              : due.tone === 'amber' ? 'text-[#D97706] font-medium'
                              : due.tone === 'muted' ? 'text-[#9CA3AF]'
                              : 'text-[#374151]'
                            const pay = paymentInfo(inv)
                            const stat = statusInfo(inv)
                            const sym = CURRENCY_SYMBOL[inv.currency] || inv.currency
                            const rate = parseAmount(inv.exchange_rate || '1')
                            const taxExcludedBwp = parseAmount(inv.subtotal) * rate
                            const totalBwp = parseAmount(inv.total_amount) * rate
                            const cancelledRow = inv.status === 'cancelled'
                            const isSelected = selected.has(inv.id)
                            return (
                              <tr
                                key={inv.id}
                                onClick={() => router.push(`/invoices/${inv.id}`)}
                                className={cn(
                                  'cursor-pointer transition-colors',
                                  isSelected ? 'bg-[#FFF7ED] hover:bg-[#FFEDD5]' : 'hover:bg-[#FFF7ED]',
                                  cancelledRow && 'opacity-50',
                                )}
                              >
                                <td className="px-3 py-3" onClick={(e) => e.stopPropagation()}>
                                  <input
                                    type="checkbox"
                                    className="rounded border-[#D1D5DB]"
                                    checked={isSelected}
                                    onChange={() => toggleOne(inv.id)}
                                    aria-label={`Select ${inv.invoice_number}`}
                                  />
                                </td>
                                <td className="px-3 py-3 font-mono text-xs text-[#CC6C00] font-semibold whitespace-nowrap">{inv.invoice_number}</td>
                                <td className="px-3 py-3 whitespace-nowrap">
                                  {inv.company_code ? (
                                    <span className="inline-flex items-center px-2 py-0.5 rounded text-[11px] font-mono font-medium bg-[#F3F4F6] text-[#374151]" title={inv.company_name || ''}>
                                      {inv.company_code}
                                    </span>
                                  ) : <span className="text-[#9CA3AF] text-xs">—</span>}
                                </td>
                                <td className="px-3 py-3 text-[#111827] max-w-[260px] truncate">{inv.contact_name}</td>
                                <td className="px-3 py-3 text-[#374151] whitespace-nowrap">{dmy(inv.issue_date)}</td>
                                <td className={cn('px-3 py-3 whitespace-nowrap', dueClass)}>{due.text}</td>
                                <td className="px-3 py-3 text-[#9CA3AF]"><Clock className="w-4 h-4" /></td>
                                <td className="px-3 py-3 text-right font-mono-nums text-[#374151] whitespace-nowrap">
                                  {fmt(taxExcludedBwp, 'BWP')}
                                </td>
                                <td className="px-3 py-3 text-right font-mono-nums text-[#111827] font-semibold whitespace-nowrap">
                                  {fmt(totalBwp, 'BWP')}
                                </td>
                                <td className="px-3 py-3 text-right font-mono-nums text-[#374151] whitespace-nowrap">
                                  {inv.currency === 'BWP'
                                    ? fmt(totalBwp, 'BWP')
                                    : `${sym} ${fmt(parseAmount(inv.total_amount), inv.currency).replace(/[^\d.,\s-]/g, '').trim()}`}
                                </td>
                                <td className="px-3 py-3">
                                  {pay ? (
                                    <span className="inline-flex items-center px-2.5 py-1 rounded-md text-[11px] font-medium" style={{ background: pay.bg, color: pay.fg }}>
                                      {pay.label}
                                    </span>
                                  ) : null}
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
        title="Delete Invoices"
        description={`Delete ${eligibleForDelete} draft invoice${eligibleForDelete === 1 ? '' : 's'}? This cannot be undone.${selected.size > eligibleForDelete ? ` (${selected.size - eligibleForDelete} non-draft invoice${selected.size - eligibleForDelete === 1 ? '' : 's'} will be skipped.)` : ''}`}
        confirmLabel="Delete"
        variant="danger"
        loading={acting}
        onConfirm={performDelete}
      />
    </div>
  )
}
