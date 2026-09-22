'use client'

/**
 * /bills — dedicated Vendor Bills (AP) page.
 *
 * Functionally a wrapper around the existing /invoices list with the
 * `vendor_bill` filter pinned. Splits AP from AR per CFO-mandated UX:
 * customer invoices live at /invoices, vendor bills here.
 */

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
import { LoadingTable } from '@/components/ui/loading'
import { ConfirmDialog } from '@/components/ui/modal'
import { formatAmount, parseAmount, cn } from '@/lib/utils'
import { useNumberFormat } from '@/contexts/NumberFormatContext'
import { useCompany } from '@/contexts/CompanyContext'
import * as DropdownMenu from '@radix-ui/react-dropdown-menu'
import {
  Plus, Upload, Search, FileText, AlertCircle, ChevronLeft, ChevronRight, Clock,
  X, Printer, CreditCard, Settings, Copy, Trash2, RotateCcw, Send,
} from 'lucide-react'

function dmy(s: string): string {
  if (!s) return ''
  const d = new Date(s + (s.includes('T') ? '' : 'T00:00:00'))
  if (isNaN(d.getTime())) return s
  return `${String(d.getDate()).padStart(2, '0')}/${String(d.getMonth() + 1).padStart(2, '0')}/${d.getFullYear()}`
}
function daysUntil(s: string): number | null {
  if (!s) return null
  const d = new Date(s + (s.includes('T') ? '' : 'T00:00:00'))
  const t = new Date(); t.setHours(0,0,0,0)
  return isNaN(d.getTime()) ? null : Math.floor((d.getTime() - t.getTime()) / 86400000)
}
function dueCell(inv: Invoice): { text: string; tone: 'red'|'amber'|'muted'|'plain' } {
  if (inv.status === 'cancelled' || inv.status === 'paid') return { text: dmy(inv.due_date), tone: 'muted' }
  const diff = daysUntil(inv.due_date)
  if (diff === null) return { text: '', tone: 'muted' }
  if (diff < 0) return { text: dmy(inv.due_date), tone: 'red' }
  if (diff === 0) return { text: 'Today', tone: 'amber' }
  if (diff <= 30) return { text: `In ${diff} day${diff === 1 ? '' : 's'}`, tone: diff <= 7 ? 'amber' : 'plain' }
  return { text: dmy(inv.due_date), tone: 'plain' }
}
function statusInfo(inv: Invoice) {
  switch (inv.status) {
    case 'pending_approval': return { label: 'Pending Approval', bg: '#FFFBEB', fg: '#D97706' }
    case 'rejected':         return { label: 'Rejected',         bg: '#FEF2F2', fg: '#DC2626' }
    case 'posted':           return { label: 'Posted',           bg: '#ECFDF5', fg: '#059669' }
    case 'partially_paid':   return { label: 'Posted',           bg: '#ECFDF5', fg: '#059669' }
    case 'paid':             return { label: 'Posted',           bg: '#ECFDF5', fg: '#059669' }
    case 'overdue':          return { label: 'Posted',           bg: '#ECFDF5', fg: '#059669' }
    case 'cancelled':        return { label: 'Cancelled',        bg: '#F3F4F6', fg: '#6B7280' }
    case 'draft':            return { label: 'Draft',            bg: '#EFF6FF', fg: '#2563EB' }
    default:                 return { label: inv.status,         bg: '#F3F4F6', fg: '#6B7280' }
  }
}

const CURRENCY_SYMBOL: Record<string, string> = { BWP: 'P', USD: '$', ZAR: 'R', INR: '₹', ZMW: 'ZK' }

export default function BillsPage() {
  const router = useRouter()
  const { mode } = useNumberFormat()
  const fmt = (a: string|number, c='BWP') => formatAmount(a, c, mode)
  const { selectedId: companyId } = useCompany()

  const [invoices, setInvoices] = useState<Invoice[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [statusTab, setStatusTab] = useState<'all'|'draft'|'pending_approval'|'posted'|'overdue'>('all')
  const [page, setPage] = useState(1)
  const [totalCount, setTotalCount] = useState(0)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [showDelete, setShowDelete] = useState(false)
  const [acting, setActing] = useState(false)
  const PAGE_SIZE = 25

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const params: Record<string, string> = {
        page: String(page), page_size: String(PAGE_SIZE), invoice_type: 'vendor_bill',
      }
      if (statusTab !== 'all') params.status = statusTab
      if (search) params.search = search
      if (companyId) params.company = companyId
      const res = await getInvoices(params)
      setInvoices(res.results); setTotalCount(res.count)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load bills')
    } finally { setLoading(false) }
  }, [page, search, statusTab, companyId])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load])

  const totalPages = Math.max(1, Math.ceil(totalCount / PAGE_SIZE))
  const rangeStart = totalCount === 0 ? 0 : (page - 1) * PAGE_SIZE + 1
  const rangeEnd = Math.min(page * PAGE_SIZE, totalCount)

  const selectedInvoices = invoices.filter(i => selected.has(i.id))
  const allOnPageSelected = invoices.length > 0 && invoices.every(i => selected.has(i.id))
  const eligibleForDelete = selectedInvoices.filter(i => i.status === 'draft').length
  const eligibleForPost = selectedInvoices.filter(i => i.status === 'draft').length

  const toggleAllOnPage = () => {
    setSelected(prev => {
      const next = new Set(prev)
      if (allOnPageSelected) invoices.forEach(i => next.delete(i.id))
      else invoices.forEach(i => next.add(i.id))
      return next
    })
  }
  const toggleOne = (id: string) => {
    setSelected(prev => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n })
  }
  const clearSelection = () => setSelected(new Set())

  const showResult = (verb: string, ok: number, errors: string[]) => {
    if (errors.length === 0) { setSuccess(`${verb} ${ok}`); setTimeout(() => setSuccess(null), 4000) }
    else setError(`${verb} ok=${ok} fails=${errors.length}: ${errors.slice(0,3).join('; ')}`)
  }
  const runBulk = async (verb: string, fn: (i: Invoice) => Promise<void>, filter?: (i: Invoice) => string|null) => {
    setActing(true); setError(null); setSuccess(null)
    let ok = 0; const errs: string[] = []
    for (const inv of selectedInvoices) {
      if (filter) { const m = filter(inv); if (m) { errs.push(`${inv.invoice_number}: ${m}`); continue } }
      try { await fn(inv); ok++ } catch (e) { errs.push(`${inv.invoice_number}: ${(e as Error).message || 'failed'}`) }
    }
    showResult(verb, ok, errs); setActing(false); clearSelection(); await load()
  }
  const handleDuplicate = () => runBulk('Duplicated', async i => { await duplicateInvoice(i.id) })
  const handleReverse   = () => runBulk('Reversed',   async i => { await reverseInvoice(i.id) },
    i => i.status !== 'posted' ? 'only posted bills can be reversed' : null)
  const handlePost      = () => runBulk('Posted',     async i => { await postInvoice(i.id) },
    i => i.status !== 'draft' ? 'only drafts can be posted' : null)
  const performDelete = async () => {
    setShowDelete(false)
    await runBulk('Deleted', async i => { await deleteInvoice(i.id) },
      i => i.status !== 'draft' ? 'only drafts can be deleted' : null)
  }
  const handlePrint = async () => { setActing(true); for (const inv of selectedInvoices) { try { await openInvoicePdf(inv.id) } catch {} } setActing(false) }
  const handleRegisterPayment = () => {
    if (selected.size !== 1) { setError('Pick exactly one bill'); return }
    router.push(`/payments/new?invoice=${Array.from(selected)[0]}`)
  }

  const inputCls = 'h-10 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm text-[#111827] placeholder-[#9CA3AF] focus:outline-none focus:border-[#F07F00] focus:ring-[3px] focus:ring-[rgba(240,127,0,0.1)] transition-all'
  const menuItemCls = 'flex items-center gap-2 px-3 py-2 text-sm text-[#374151] hover:bg-[#FFF7ED] hover:text-[#0B0B3B] rounded cursor-pointer outline-none data-[disabled]:opacity-40 data-[disabled]:cursor-not-allowed'
  const hasSelection = selected.size > 0

  const TABS: { v: typeof statusTab; l: string }[] = [
    { v: 'all',              l: 'All' },
    { v: 'draft',            l: 'Draft' },
    { v: 'pending_approval', l: 'Pending Approval' },
    { v: 'posted',           l: 'Posted' },
    { v: 'overdue',          l: 'Overdue' },
  ]

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Vendor Bills"
        breadcrumbs={[{ label: 'Finance' }, { label: 'Vendor Bills' }]}
        actions={
          <div className="flex items-center gap-2">
            <Button variant="accent" size="sm" leftIcon={<Plus className="w-3.5 h-3.5" />} onClick={() => router.push('/invoices/new?type=vendor_bill')}>New Bill</Button>
            <Button variant="secondary" size="sm" leftIcon={<Upload className="w-3.5 h-3.5" />} onClick={() => router.push('/quick-entry')}>Upload</Button>
          </div>
        }
      />

      <div className="flex-1 p-6 space-y-4">
        <div className="flex items-center gap-1 border-b border-[#E5E7EB]">
          {TABS.map(t => (
            <button
              key={t.v}
              onClick={() => { setStatusTab(t.v); setPage(1); clearSelection() }}
              className={cn(
                'px-3 py-2 text-sm font-medium transition-colors border-b-2',
                statusTab === t.v
                  ? 'border-[#F07F00] text-[#0B0B3B]'
                  : 'border-transparent text-[#6B7280] hover:text-[#0B0B3B]'
              )}
            >{t.l}</button>
          ))}
        </div>

        {hasSelection ? (
          <div className="mt-1 flex items-center gap-2 flex-wrap">
            <button onClick={clearSelection} className="inline-flex items-center gap-2 px-3 py-1.5 rounded-md text-sm font-medium bg-[#FFF7ED] text-[#CC6C00] border border-[#FFD7B5] hover:bg-[#FFEDD5]">
              {selected.size} selected <X className="w-3.5 h-3.5" />
            </button>
            <Button variant="secondary" size="sm" leftIcon={<CreditCard className="w-3.5 h-3.5" />} onClick={handleRegisterPayment} disabled={acting}>Register Payment</Button>
            <Button variant="secondary" size="sm" leftIcon={<Printer className="w-3.5 h-3.5" />} onClick={handlePrint} disabled={acting}>Print</Button>
            <DropdownMenu.Root>
              <DropdownMenu.Trigger asChild>
                <button className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium bg-white border border-[#D1D5DB] text-[#374151] hover:bg-[#F9FAFB]"><Settings className="w-3.5 h-3.5" />Actions</button>
              </DropdownMenu.Trigger>
              <DropdownMenu.Portal>
                <DropdownMenu.Content align="start" sideOffset={4} className="z-50 bg-white border border-[#E5E7EB] rounded-md shadow-lg py-1 min-w-[220px]">
                  <DropdownMenu.Item className={menuItemCls} onSelect={handleDuplicate}><Copy className="w-3.5 h-3.5" />Duplicate</DropdownMenu.Item>
                  <DropdownMenu.Item className={cn(menuItemCls, eligibleForDelete === 0 && 'opacity-40 cursor-not-allowed')} onSelect={(e) => { if (eligibleForDelete === 0) { e.preventDefault(); return } setShowDelete(true) }}>
                    <Trash2 className="w-3.5 h-3.5" />Delete
                  </DropdownMenu.Item>
                  <DropdownMenu.Item className={menuItemCls} onSelect={handleReverse}><RotateCcw className="w-3.5 h-3.5" />Reverse</DropdownMenu.Item>
                  <DropdownMenu.Item className={cn(menuItemCls, eligibleForPost === 0 && 'opacity-40 cursor-not-allowed')} onSelect={(e) => { if (eligibleForPost === 0) { e.preventDefault(); return } handlePost() }}>
                    <Send className="w-3.5 h-3.5" />Post entries
                  </DropdownMenu.Item>
                </DropdownMenu.Content>
              </DropdownMenu.Portal>
            </DropdownMenu.Root>
          </div>
        ) : (
          <div className="flex items-center justify-between gap-3">
            <div className="relative flex-1 max-w-md">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[#9CA3AF] pointer-events-none" strokeWidth={1.5} />
              <input type="text" placeholder="Search vendor bills..." value={search} onChange={(e) => { setSearch(e.target.value); setPage(1) }} className={cn(inputCls, 'pl-9 w-full')} />
            </div>
            <div className="flex items-center gap-3 text-sm text-[#6B7280]">
              <span className="font-medium font-mono-nums tabular-nums">{rangeStart}-{rangeEnd} / {totalCount}</span>
              <button aria-label="Previous page" className="p-1.5 rounded hover:bg-[#F3F4F6] disabled:opacity-30 disabled:cursor-not-allowed" disabled={page === 1} onClick={() => setPage(page - 1)}><ChevronLeft className="w-4 h-4" /></button>
              <button aria-label="Next page" className="p-1.5 rounded hover:bg-[#F3F4F6] disabled:opacity-30 disabled:cursor-not-allowed" disabled={page >= totalPages} onClick={() => setPage(page + 1)}><ChevronRight className="w-4 h-4" /></button>
            </div>
          </div>
        )}

        {success && <div className="bg-[#ECFDF5] border border-[#A7F3D0] rounded-lg p-3"><p className="text-[#059669] text-sm">{success}</p></div>}
        {error && <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2"><AlertCircle className="w-4 h-4 text-[#DC2626]" /><p className="text-[#DC2626] text-sm">{error}</p></div>}

        <Card>
          <CardContent className="p-0">
            {loading ? <LoadingTable rows={8} cols={9} /> : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm border-collapse">
                  <thead className="bg-[#F9FAFB] border-b border-[#E5E7EB]">
                    <tr>
                      <th className="w-10 px-3 py-3"><input type="checkbox" checked={allOnPageSelected} onChange={toggleAllOnPage} /></th>
                      <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Bill #</th>
                      <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Vendor</th>
                      <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Date</th>
                      <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Due</th>
                      <th className="px-3 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider">Total</th>
                      <th className="px-3 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider">In Currency</th>
                      <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Approval Tier</th>
                      <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Status</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#E5E7EB] bg-white">
                    {invoices.length === 0 ? (
                      <tr><td colSpan={9} className="px-4 py-16 text-center"><FileText className="w-8 h-8 text-[#D1D5DB] mx-auto mb-2" /><p className="text-[#6B7280] text-sm font-medium">No vendor bills found</p></td></tr>
                    ) : invoices.map(inv => {
                      const due = dueCell(inv)
                      const dueClass = due.tone === 'red' ? 'text-[#DC2626] font-medium' : due.tone === 'amber' ? 'text-[#D97706] font-medium' : due.tone === 'muted' ? 'text-[#9CA3AF]' : 'text-[#374151]'
                      const sym = CURRENCY_SYMBOL[inv.currency] || inv.currency
                      const rate = parseAmount(inv.exchange_rate || '1')
                      const totalBwp = parseAmount(inv.total_amount) * rate
                      const stat = statusInfo(inv)
                      const isSel = selected.has(inv.id)
                      return (
                        <tr key={inv.id} onClick={() => router.push(`/invoices/${inv.id}`)} className={cn('cursor-pointer transition-colors', isSel ? 'bg-[#FFF7ED] hover:bg-[#FFEDD5]' : 'hover:bg-[#FFF7ED]')}>
                          <td className="px-3 py-3" onClick={(e) => e.stopPropagation()}><input type="checkbox" checked={isSel} onChange={() => toggleOne(inv.id)} /></td>
                          <td className="px-3 py-3 font-mono text-xs text-[#CC6C00] font-semibold whitespace-nowrap">{inv.invoice_number}</td>
                          <td className="px-3 py-3 text-[#111827] max-w-[260px] truncate">{inv.contact_name}</td>
                          <td className="px-3 py-3 text-[#374151] whitespace-nowrap">{dmy(inv.issue_date)}</td>
                          <td className={cn('px-3 py-3 whitespace-nowrap', dueClass)}>{due.text}</td>
                          <td className="px-3 py-3 text-right font-mono-nums text-[#111827] font-semibold whitespace-nowrap">{fmt(totalBwp, 'BWP')}</td>
                          <td className="px-3 py-3 text-right font-mono-nums text-[#374151] whitespace-nowrap">{inv.currency === 'BWP' ? fmt(totalBwp, 'BWP') : `${sym} ${parseAmount(inv.total_amount).toFixed(2)}`}</td>
                          <td className="px-3 py-3 text-[#6B7280] text-xs">{(inv as any).approval_tier || '—'}</td>
                          <td className="px-3 py-3"><span className="inline-flex items-center px-2.5 py-1 rounded-md text-[11px] font-medium" style={{ background: stat.bg, color: stat.fg }}>{stat.label}</span></td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <ConfirmDialog open={showDelete} onOpenChange={setShowDelete}
        title="Delete Bills"
        description={`Delete ${eligibleForDelete} draft bill${eligibleForDelete === 1 ? '' : 's'}? This cannot be undone.`}
        confirmLabel="Delete" variant="danger" loading={acting} onConfirm={performDelete} />
    </div>
  )
}
