'use client'

import { useEffect, useState } from 'react'
import { useRouter, useParams } from 'next/navigation'
import {
  getInvoice, postInvoice, getPayments, getToken,
  updateInvoice, getContacts, getAccounts, getTaxRates, createAccount,
  reverseInvoice, resetInvoiceToDraft, openInvoicePdf, invoicePdfUrl,
  submitBillForApproval, approveBill, rejectBill,
} from '@/lib/api'
import type { InvoiceDetail, Payment, Contact, Account, TaxRate, CreateAccountInput } from '@/lib/api'
import { useCompany } from '@/contexts/CompanyContext'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { StatusBadge, TypeBadge } from '@/components/ui/badge'
import { ConfirmDialog } from '@/components/ui/modal'
import { LoadingCard } from '@/components/ui/loading'
import { formatAmount, formatDate, parseAmount, cn } from '@/lib/utils'
import { useNumberFormat } from '@/contexts/NumberFormatContext'
import { Send, CreditCard, AlertCircle, CheckCircle, Pencil, Save, X, Plus, Trash2, Printer, Eye, RotateCcw, FileMinus2 } from 'lucide-react'

interface EditLine {
  id: string  // local id only
  account: string
  description: string
  quantity: string
  unit_price: string
  tax_code: string
}

function newEditLine(): EditLine {
  return {
    id: Math.random().toString(36).slice(2),
    account: '', description: '', quantity: '1', unit_price: '', tax_code: '',
  }
}

export default function InvoiceDetailPage() {
  const router = useRouter()
  const params = useParams()
  const id = params.id as string
  const { mode } = useNumberFormat()
  const fmt = (amount: string | number, currency = 'BWP') => formatAmount(amount, currency, mode)

  const [invoice, setInvoice] = useState<InvoiceDetail | null>(null)
  const [payments, setPayments] = useState<Payment[]>([])
  const [loading, setLoading] = useState(true)
  const [posting, setPosting] = useState(false)
  const [showPostConfirm, setShowPostConfirm] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [successMsg, setSuccessMsg] = useState<string | null>(null)

  // Edit mode state
  const [isEditing, setIsEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [showResetConfirm, setShowResetConfirm] = useState(false)
  const [showCreditConfirm, setShowCreditConfirm] = useState(false)
  const [busyAction, setBusyAction] = useState<string | null>(null)
  const [contacts, setContacts] = useState<Contact[]>([])
  const [accounts, setAccounts] = useState<Account[]>([])
  const [taxRates, setTaxRates] = useState<TaxRate[]>([])
  const [refDataLoaded, setRefDataLoaded] = useState(false)
  // Editable fields
  const [editContact, setEditContact] = useState('')
  const [editCompany, setEditCompany] = useState('')
  const [editIssue, setEditIssue] = useState('')
  const [editDue, setEditDue] = useState('')
  const [editDesc, setEditDesc] = useState('')
  const [editLines, setEditLines] = useState<EditLine[]>([])
  const { companies } = useCompany()

  // New-account modal state
  const [acctModalOpen, setAcctModalOpen] = useState(false)
  const [acctModalForLine, setAcctModalForLine] = useState<string | null>(null)
  const [newAcctCode, setNewAcctCode] = useState('')
  const [newAcctName, setNewAcctName] = useState('')
  const [newAcctType, setNewAcctType] = useState<CreateAccountInput['account_type']>('expense')
  const [newAcctSubType, setNewAcctSubType] = useState('')
  const [newAcctSaving, setNewAcctSaving] = useState(false)
  const [newAcctErr, setNewAcctErr] = useState<string | null>(null)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const inv = await getInvoice(id)
      setInvoice(inv)
      try {
        const pRes = await getPayments({ invoice: id, page_size: '20' })
        setPayments(pRes.results)
      } catch {}
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load invoice')
    } finally { setLoading(false) }
  }

  useEffect(() => {
    const token = getToken()
    if (!token) { router.replace('/login'); return }
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  const enterEditMode = async () => {
    if (!invoice) return
    setIsEditing(true)
    if (!refDataLoaded) {
      try {
        const [c, a, t] = await Promise.all([
          getContacts({ page_size: '500' }),
          getAccounts({ page_size: '500' }),
          getTaxRates(),
        ])
        setContacts(c.results)
        setAccounts(a.results)
        setTaxRates(t.results)
        setRefDataLoaded(true)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load reference data')
      }
    }
    // Seed editable state from current invoice
    setEditContact(invoice.contact)
    setEditCompany(invoice.company || '')
    setEditIssue(invoice.issue_date)
    setEditDue(invoice.due_date || '')
    setEditDesc(invoice.description || '')
    setEditLines(invoice.lines.map((l) => ({
      id: Math.random().toString(36).slice(2),
      account: l.account,
      description: l.description,
      quantity: String(parseAmount(l.quantity)),
      unit_price: String(parseAmount(l.unit_price)),
      tax_code: l.tax_code || '',
    })))
  }

  const cancelEdit = () => {
    setIsEditing(false)
    setError(null)
  }

  const updateEditLine = (lid: string, field: keyof EditLine, value: string) => {
    if (field === 'account' && value === '__new__') {
      // Open the modal; remember which line triggered it so we can fill it in
      setAcctModalForLine(lid)
      setNewAcctCode('')
      setNewAcctName('')
      setNewAcctSubType('')
      setNewAcctErr(null)
      // Default account_type based on invoice direction:
      // customer invoice / credit note → revenue;  vendor bill → expense
      const t = invoice?.invoice_type
      setNewAcctType(t === 'customer_invoice' || t === 'credit_note' ? 'revenue' : 'expense')
      setAcctModalOpen(true)
      return
    }
    setEditLines((prev) => prev.map((l) => l.id === lid ? { ...l, [field]: value } : l))
  }

  const submitNewAccount = async () => {
    setNewAcctErr(null)
    const code = newAcctCode.trim()
    const name = newAcctName.trim()
    const sub = newAcctSubType.trim() || newAcctType  // sub_type required by model
    if (!code || !name) { setNewAcctErr('Code and name are required'); return }
    setNewAcctSaving(true)
    try {
      const created = await createAccount({
        code, name, account_type: newAcctType, sub_type: sub,
      })
      setAccounts((prev) => [...prev, created].sort((a, b) => a.code.localeCompare(b.code)))
      if (acctModalForLine) {
        setEditLines((prev) => prev.map((l) =>
          l.id === acctModalForLine ? { ...l, account: created.code } : l,
        ))
      }
      setAcctModalOpen(false)
    } catch (e) {
      setNewAcctErr(e instanceof Error ? e.message : 'Failed to create account')
    } finally { setNewAcctSaving(false) }
  }

  const addEditLine = () => setEditLines((prev) => [...prev, newEditLine()])
  const removeEditLine = (lid: string) =>
    setEditLines((prev) => prev.filter((l) => l.id !== lid))

  const saveEdits = async () => {
    if (!invoice) return
    setError(null)
    if (!editContact) { setError('Contact is required'); return }
    if (editLines.length === 0) { setError('At least one line is required'); return }
    for (const ln of editLines) {
      if (!ln.account)   { setError('Each line needs an account'); return }
      if (!ln.tax_code)  { setError('Each line needs a tax code'); return }
      if (!ln.description) { setError('Each line needs a description'); return }
      if (parseAmount(ln.unit_price) <= 0) { setError('Unit price must be > 0'); return }
    }
    setSaving(true)
    try {
      const updated = await updateInvoice(id, {
        contact: editContact,
        company: editCompany || undefined,
        issue_date: editIssue,
        due_date: editDue || undefined,
        description: editDesc || undefined,
        lines: editLines.map((l) => ({
          account: l.account,
          description: l.description,
          quantity: l.quantity,
          unit_price: l.unit_price,
          tax_code: l.tax_code,
        })),
      })
      setInvoice(updated)
      setIsEditing(false)
      setSuccessMsg('Draft updated')
      setTimeout(() => setSuccessMsg(null), 3500)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update invoice')
    } finally { setSaving(false) }
  }

  const handlePost = async () => {
    setPosting(true)
    try {
      const updated = await postInvoice(id)
      setInvoice(updated)
      setShowPostConfirm(false)
      setSuccessMsg('Invoice posted successfully')
      setTimeout(() => setSuccessMsg(null), 4000)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to post invoice')
      setShowPostConfirm(false)
    } finally { setPosting(false) }
  }

  const handleSubmitForApproval = async () => {
    setBusyAction('submit'); setError(null)
    try {
      const updated = await submitBillForApproval(id)
      setInvoice(updated)
      setSuccessMsg(updated.status === 'pending_approval'
        ? `Submitted for approval (tier: ${(updated as any).approval_tier || 'manager/cfo'})`
        : 'Auto-approved (below auto-approve ceiling) — ready to post.')
      setTimeout(() => setSuccessMsg(null), 4500)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Submit failed')
    } finally { setBusyAction(null) }
  }
  const handleApproveBill = async () => {
    setBusyAction('approve'); setError(null)
    try {
      const updated = await approveBill(id)
      setInvoice(updated)
      setSuccessMsg('Approved. You can now post the bill.')
      setTimeout(() => setSuccessMsg(null), 4000)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Approve failed')
    } finally { setBusyAction(null) }
  }
  const handleRejectBill = async () => {
    const reason = prompt('Why are you rejecting this bill? (required)')
    if (!reason || !reason.trim()) return
    setBusyAction('reject'); setError(null)
    try {
      const updated = await rejectBill(id, reason.trim())
      setInvoice(updated)
      setSuccessMsg('Bill rejected.')
      setTimeout(() => setSuccessMsg(null), 4000)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Reject failed')
    } finally { setBusyAction(null) }
  }
  const handlePrint = async () => {
    setBusyAction('print'); setError(null)
    try { await openInvoicePdf(id) }
    catch (err) { setError(err instanceof Error ? err.message : 'Failed to open PDF') }
    finally { setBusyAction(null) }
  }
  const handlePreview = () => window.open(`${invoicePdfUrl(id)}?token=${getToken()}`, '_blank')
  const handleResetToDraft = async () => {
    setShowResetConfirm(false)
    setBusyAction('reset'); setError(null)
    try {
      const updated = await resetInvoiceToDraft(id)
      setInvoice(updated)
      setSuccessMsg('Invoice reset to draft. Edit and re-post when ready.')
      setTimeout(() => setSuccessMsg(null), 4000)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to reset to draft')
    } finally { setBusyAction(null) }
  }
  const handleCreditNote = async () => {
    setShowCreditConfirm(false)
    setBusyAction('credit'); setError(null)
    try {
      const cn = await reverseInvoice(id)
      setSuccessMsg(`Credit note ${cn.invoice_number} created. Opening it now…`)
      setTimeout(() => router.push(`/invoices/${cn.id}`), 800)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create credit note')
    } finally { setBusyAction(null) }
  }

  if (loading) {
    return (
      <div className="flex flex-col min-h-screen">
        <TopBar title="Invoice Detail" breadcrumbs={[{ label: 'Invoices', href: '/invoices' }]} />
        <div className="p-6"><LoadingCard message="Loading invoice..." className="h-48" /></div>
      </div>
    )
  }

  if (error && !invoice) {
    return (
      <div className="flex flex-col min-h-screen">
        <TopBar title="Invoice Detail" breadcrumbs={[{ label: 'Invoices', href: '/invoices' }]} />
        <div className="p-6">
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-xl p-4 flex items-center gap-3">
            <AlertCircle className="w-5 h-5 text-[#DC2626]" />
            <p className="text-[#DC2626]">{error}</p>
          </div>
        </div>
      </div>
    )
  }

  if (!invoice) return null

  const isDraft = invoice.status === 'draft'
  const isPosted = invoice.status === 'posted'
  const isPending = invoice.status === 'pending_approval'
  const isVendorBill = invoice.invoice_type === 'vendor_bill'
  const balanceDue = parseAmount(invoice.balance_due)

  // Live totals while editing
  const liveTotals = (() => {
    let subtotal = 0, taxTotal = 0
    for (const ln of editLines) {
      const qty = parseAmount(ln.quantity || '0')
      const price = parseAmount(ln.unit_price || '0')
      const sub = qty * price
      const tr = taxRates.find((t) => t.tax_code === ln.tax_code)
      const taxPct = tr ? parseAmount(tr.rate) / 100 : 0
      subtotal += sub
      taxTotal += sub * taxPct
    }
    return { subtotal, taxTotal, total: subtotal + taxTotal }
  })()

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title={invoice.invoice_number}
        breadcrumbs={[{ label: 'Invoices', href: '/invoices' }, { label: invoice.invoice_number }]}
        actions={
          <div className="flex items-center gap-2">
            {/* Draft mode: edit / save / cancel / post */}
            {isDraft && !isEditing && (
              <Button variant="outline" size="sm" leftIcon={<Pencil className="w-3.5 h-3.5" />} onClick={enterEditMode}>
                Edit Draft
              </Button>
            )}
            {isDraft && isEditing && (
              <>
                <Button variant="outline" size="sm" leftIcon={<X className="w-3.5 h-3.5" />} onClick={cancelEdit} disabled={saving}>
                  Cancel
                </Button>
                <Button variant="accent" size="sm" leftIcon={<Save className="w-3.5 h-3.5" />} onClick={saveEdits} disabled={saving}>
                  {saving ? 'Saving…' : 'Save'}
                </Button>
              </>
            )}
            {isDraft && !isEditing && isVendorBill && (
              <Button variant="outline" size="sm" leftIcon={<Send className="w-3.5 h-3.5" />} onClick={handleSubmitForApproval} disabled={busyAction === 'submit'}>
                Submit for Approval
              </Button>
            )}
            {isDraft && !isEditing && (
              <Button variant="accent" size="sm" leftIcon={<Send className="w-3.5 h-3.5" />} onClick={() => setShowPostConfirm(true)}>
                Post Invoice
              </Button>
            )}

            {/* Pending approval mode: approve / reject */}
            {isPending && (
              <>
                <Button variant="outline" size="sm" leftIcon={<X className="w-3.5 h-3.5" />} onClick={handleRejectBill} disabled={busyAction === 'reject'}>
                  Reject
                </Button>
                <Button variant="accent" size="sm" leftIcon={<CheckCircle className="w-3.5 h-3.5" />} onClick={handleApproveBill} disabled={busyAction === 'approve'}>
                  Approve
                </Button>
                {invoice.approved_at && (
                  <Button variant="outline" size="sm" leftIcon={<Send className="w-3.5 h-3.5" />} onClick={() => setShowPostConfirm(true)}>
                    Post (after approval)
                  </Button>
                )}
              </>
            )}

            {/* Posted mode: full set of actions */}
            {!isDraft && !isPending && (
              <>
                <Button variant="outline" size="sm" leftIcon={<Printer className="w-3.5 h-3.5" />} onClick={handlePrint} disabled={busyAction === 'print'}>
                  Send &amp; Print
                </Button>
                {balanceDue > 0 && (
                  <Button variant="accent" size="sm" leftIcon={<CreditCard className="w-3.5 h-3.5" />} onClick={() => router.push(`/payments/new?invoice=${id}`)}>
                    Register Payment
                  </Button>
                )}
                <Button variant="outline" size="sm" leftIcon={<Eye className="w-3.5 h-3.5" />} onClick={handlePreview}>
                  Preview
                </Button>
                {(invoice.invoice_type === 'customer_invoice' || invoice.invoice_type === 'vendor_bill') && isPosted && (
                  <Button variant="outline" size="sm" leftIcon={<FileMinus2 className="w-3.5 h-3.5" />} onClick={() => setShowCreditConfirm(true)} disabled={busyAction === 'credit'}>
                    Credit Note
                  </Button>
                )}
                {isPosted && (
                  <Button variant="outline" size="sm" leftIcon={<RotateCcw className="w-3.5 h-3.5" />} onClick={() => setShowResetConfirm(true)} disabled={busyAction === 'reset'}>
                    Reset to Draft
                  </Button>
                )}
              </>
            )}

            {/* Status pill (Draft → Posted/Cancelled) */}
            <div className="flex items-center gap-1 ml-2 pl-3 border-l border-[#E5E7EB]">
              <span className={cn(
                'px-2.5 py-1 rounded-md text-[11px] font-medium',
                isDraft ? 'bg-[#EFF6FF] text-[#2563EB]' : 'bg-[#F3F4F6] text-[#9CA3AF]'
              )}>Draft</span>
              <span className="text-[#D1D5DB] text-xs">→</span>
              <span className={cn(
                'px-2.5 py-1 rounded-md text-[11px] font-medium',
                isPosted ? 'bg-[#ECFDF5] text-[#059669]'
                  : invoice.status === 'cancelled' ? 'bg-[#F3F4F6] text-[#6B7280]'
                  : 'bg-[#F3F4F6] text-[#9CA3AF]'
              )}>
                {invoice.status === 'cancelled' ? 'Cancelled' : 'Posted'}
              </span>
            </div>
          </div>
        }
      />

      <div className="flex-1 p-6 space-y-6">
        {successMsg && (
          <div className="bg-[#ECFDF5] border border-[#A7F3D0] rounded-xl p-3 flex items-center gap-2">
            <CheckCircle className="w-4 h-4 text-[#059669]" />
            <p className="text-[#059669] text-sm">{successMsg}</p>
          </div>
        )}
        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-xl p-3 flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-[#DC2626]" />
            <p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}

        {/* Header Card */}
        <Card>
          <CardContent className="py-5">
            {!isEditing ? (
              <>
                <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-6">
                  <div>
                    <p className="text-xs text-[#6B7280] uppercase tracking-wider">Invoice #</p>
                    <p className="text-base font-bold text-[#CC6C00] mt-1">{invoice.invoice_number}</p>
                  </div>
                  <div>
                    <p className="text-xs text-[#6B7280] uppercase tracking-wider">Type</p>
                    <div className="mt-1.5"><TypeBadge type={invoice.invoice_type} /></div>
                  </div>
                  <div>
                    <p className="text-xs text-[#6B7280] uppercase tracking-wider">Company</p>
                    <p className="text-sm text-[#111827] font-medium mt-1">
                      {invoice.company_code ? (
                        <><span className="font-mono text-[#CC6C00]">{invoice.company_code}</span> · {invoice.company_name}</>
                      ) : <span className="text-[#9CA3AF]">—</span>}
                    </p>
                  </div>
                  <div>
                    <p className="text-xs text-[#6B7280] uppercase tracking-wider">Contact</p>
                    <p className="text-sm text-[#111827] font-medium mt-1">{invoice.contact_name}</p>
                  </div>
                  <div>
                    <p className="text-xs text-[#6B7280] uppercase tracking-wider">Issue Date</p>
                    <p className="text-sm text-[#374151] mt-1">{formatDate(invoice.issue_date)}</p>
                  </div>
                  <div>
                    <p className="text-xs text-[#6B7280] uppercase tracking-wider">Due Date</p>
                    <p className="text-sm text-[#374151] mt-1">{formatDate(invoice.due_date)}</p>
                  </div>
                  <div>
                    <p className="text-xs text-[#6B7280] uppercase tracking-wider">Status</p>
                    <div className="mt-1.5"><StatusBadge status={invoice.status} size="md" /></div>
                  </div>
                </div>
                {invoice.description && (
                  <div className="mt-4 pt-4 border-t border-[#E5E7EB]">
                    <p className="text-xs text-[#6B7280] uppercase tracking-wider mb-1">Description</p>
                    <p className="text-sm text-[#6B7280]">{invoice.description}</p>
                  </div>
                )}
                {invoice.je_number && (
                  <div className="mt-3">
                    <p className="text-xs text-[#9CA3AF]">
                      Journal Entry: <span className="text-[#CC6C00]">{invoice.je_number}</span>
                    </p>
                  </div>
                )}
              </>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                <div>
                  <label className="block text-xs text-[#6B7280] uppercase tracking-wider mb-1">Company / Subsidiary</label>
                  <select
                    value={editCompany}
                    onChange={(e) => setEditCompany(e.target.value)}
                    className="w-full text-sm rounded-md px-3 py-2 border border-[#E5E7EB] bg-white"
                  >
                    <option value="">— Default —</option>
                    {companies.map((c) => (
                      <option key={c.id} value={c.id}>{c.code} — {c.name}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-xs text-[#6B7280] uppercase tracking-wider mb-1">Contact</label>
                  <select
                    value={editContact}
                    onChange={(e) => setEditContact(e.target.value)}
                    className="w-full text-sm rounded-md px-3 py-2 border border-[#E5E7EB] bg-white"
                  >
                    <option value="">— Select contact —</option>
                    {contacts.map((c) => (
                      <option key={c.id} value={c.id}>{c.name} ({c.contact_type})</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-xs text-[#6B7280] uppercase tracking-wider mb-1">Issue Date</label>
                  <input
                    type="date"
                    value={editIssue}
                    onChange={(e) => setEditIssue(e.target.value)}
                    className="w-full text-sm rounded-md px-3 py-2 border border-[#E5E7EB] bg-white"
                  />
                </div>
                <div>
                  <label className="block text-xs text-[#6B7280] uppercase tracking-wider mb-1">Due Date</label>
                  <input
                    type="date"
                    value={editDue}
                    onChange={(e) => setEditDue(e.target.value)}
                    className="w-full text-sm rounded-md px-3 py-2 border border-[#E5E7EB] bg-white"
                  />
                </div>
                <div className="col-span-full">
                  <label className="block text-xs text-[#6B7280] uppercase tracking-wider mb-1">Description</label>
                  <input
                    type="text"
                    value={editDesc}
                    onChange={(e) => setEditDesc(e.target.value)}
                    placeholder="Optional"
                    className="w-full text-sm rounded-md px-3 py-2 border border-[#E5E7EB] bg-white"
                  />
                </div>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Line Items */}
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <CardTitle>Line Items</CardTitle>
              {isEditing && (
                <Button variant="outline" size="sm" leftIcon={<Plus className="w-3.5 h-3.5" />} onClick={addEditLine}>
                  Add Line
                </Button>
              )}
            </div>
          </CardHeader>
          <CardContent className="p-0">
            <div className="overflow-x-auto">
              <table className="w-full text-sm border-collapse">
                <thead className="bg-[#F3F4F6] border-b-2 border-[#E5E7EB]">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Account</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Description</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider w-20">Qty</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider w-28">Unit Price</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider w-28">Tax</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider w-28">Total</th>
                    {isEditing && <th className="px-2 py-3 w-10"></th>}
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#E5E7EB] bg-white">
                  {!isEditing ? (
                    invoice.lines.map((line) => (
                      <tr key={line.id} className="table-row-alt">
                        <td className="px-4 py-3 text-[#6B7280] text-xs">
                          <span className="font-mono">{line.account_name}</span>
                        </td>
                        <td className="px-4 py-3 text-[#374151]">{line.description}</td>
                        <td className="px-4 py-3 text-right text-[#6B7280] font-mono-nums">
                          {parseAmount(line.quantity).toFixed(2)}
                        </td>
                        <td className="px-4 py-3 text-right font-mono-nums text-[#374151]">
                          {fmt(line.unit_price, invoice.currency)}
                        </td>
                        <td className="px-4 py-3 text-[#9CA3AF] text-xs">
                          {line.tax_code || '—'}
                          {line.tax_rate && ` (${parseFloat(line.tax_rate).toFixed(1)}%)`}
                        </td>
                        <td className="px-4 py-3 text-right font-mono-nums text-[#111827] font-medium">
                          {fmt(line.line_total, invoice.currency)}
                        </td>
                      </tr>
                    ))
                  ) : (
                    editLines.map((ln) => {
                      const qty = parseAmount(ln.quantity || '0')
                      const price = parseAmount(ln.unit_price || '0')
                      const sub = qty * price
                      const tr = taxRates.find((t) => t.tax_code === ln.tax_code)
                      const taxPct = tr ? parseAmount(tr.rate) / 100 : 0
                      const total = sub + sub * taxPct
                      return (
                        <tr key={ln.id} className="bg-white">
                          <td className="px-2 py-2">
                            <select
                              value={ln.account}
                              onChange={(e) => updateEditLine(ln.id, 'account', e.target.value)}
                              className="w-full text-xs font-mono rounded-md px-2 py-1.5 border border-[#E5E7EB] bg-white"
                            >
                              <option value="">— Account —</option>
                              <option value="__new__" style={{ color: '#CC6C00', fontWeight: 600 }}>+ New account…</option>
                              {accounts.map((a) => (
                                <option key={a.code} value={a.code}>{a.code} — {a.name}</option>
                              ))}
                            </select>
                          </td>
                          <td className="px-2 py-2">
                            <input
                              type="text"
                              value={ln.description}
                              onChange={(e) => updateEditLine(ln.id, 'description', e.target.value)}
                              className="w-full text-sm rounded-md px-2 py-1.5 border border-[#E5E7EB] bg-white"
                            />
                          </td>
                          <td className="px-2 py-2">
                            <input
                              type="number"
                              step="0.01"
                              value={ln.quantity}
                              onChange={(e) => updateEditLine(ln.id, 'quantity', e.target.value)}
                              className="w-full text-sm font-mono rounded-md px-2 py-1.5 border border-[#E5E7EB] bg-white text-right"
                            />
                          </td>
                          <td className="px-2 py-2">
                            <input
                              type="number"
                              step="0.01"
                              value={ln.unit_price}
                              onChange={(e) => updateEditLine(ln.id, 'unit_price', e.target.value)}
                              className="w-full text-sm font-mono rounded-md px-2 py-1.5 border border-[#E5E7EB] bg-white text-right"
                            />
                          </td>
                          <td className="px-2 py-2">
                            <select
                              value={ln.tax_code}
                              onChange={(e) => updateEditLine(ln.id, 'tax_code', e.target.value)}
                              className="w-full text-xs rounded-md px-2 py-1.5 border border-[#E5E7EB] bg-white"
                            >
                              <option value="">— Tax —</option>
                              {taxRates.map((t) => (
                                <option key={t.tax_code} value={t.tax_code}>
                                  {t.tax_code} ({parseFloat(t.rate).toFixed(0)}%)
                                </option>
                              ))}
                            </select>
                          </td>
                          <td className="px-4 py-2 text-right font-mono-nums text-[#111827] font-medium text-sm">
                            {fmt(total, invoice.currency)}
                          </td>
                          <td className="px-1 py-2">
                            <button
                              type="button"
                              onClick={() => removeEditLine(ln.id)}
                              disabled={editLines.length <= 1}
                              className="p-1 rounded hover:bg-[#FEF2F2] text-[#9CA3AF] hover:text-[#DC2626] disabled:opacity-30 disabled:cursor-not-allowed"
                              aria-label="Remove line"
                            >
                              <Trash2 className="w-4 h-4" />
                            </button>
                          </td>
                        </tr>
                      )
                    })
                  )}
                </tbody>
                <tfoot className="bg-[#F9FAFB] border-t border-[#E5E7EB]">
                  <tr>
                    <td colSpan={isEditing ? 5 : 5} className="px-4 py-2.5 text-right text-xs text-[#6B7280]">Subtotal</td>
                    <td className="px-4 py-2.5 text-right font-mono-nums text-[#374151]">
                      {fmt(isEditing ? liveTotals.subtotal : invoice.subtotal, invoice.currency)}
                    </td>
                    {isEditing && <td></td>}
                  </tr>
                  <tr>
                    <td colSpan={isEditing ? 5 : 5} className="px-4 py-2.5 text-right text-xs text-[#6B7280]">Tax Total</td>
                    <td className="px-4 py-2.5 text-right font-mono-nums text-[#374151]">
                      {fmt(isEditing ? liveTotals.taxTotal : invoice.tax_total, invoice.currency)}
                    </td>
                    {isEditing && <td></td>}
                  </tr>
                  <tr>
                    <td colSpan={isEditing ? 5 : 5} className="px-4 py-2.5 text-right text-sm font-semibold text-[#111827]">Grand Total</td>
                    <td className="px-4 py-2.5 text-right font-semibold font-mono-nums text-[#CC6C00] text-base">
                      {fmt(isEditing ? liveTotals.total : invoice.total_amount, invoice.currency)}
                    </td>
                    {isEditing && <td></td>}
                  </tr>
                  {!isEditing && parseAmount(invoice.amount_paid) > 0 && (
                    <tr>
                      <td colSpan={5} className="px-4 py-2.5 text-right text-xs text-[#6B7280]">Amount Paid</td>
                      <td className="px-4 py-2.5 text-right font-mono-nums text-[#059669]">
                        ({fmt(invoice.amount_paid, invoice.currency)})
                      </td>
                    </tr>
                  )}
                  {!isEditing && (
                    <tr>
                      <td colSpan={5} className="px-4 py-3 text-right text-sm font-bold text-[#0B0B3B]">Balance Due</td>
                      <td className={cn('px-4 py-3 text-right font-bold font-mono-nums text-base', balanceDue > 0 ? 'text-[#D97706]' : 'text-[#059669]')}>
                        {fmt(invoice.balance_due, invoice.currency)}
                      </td>
                    </tr>
                  )}
                </tfoot>
              </table>
            </div>
          </CardContent>
        </Card>

        {/* Payment History */}
        {!isEditing && payments.length > 0 && (
          <Card>
            <CardHeader><CardTitle>Payment History</CardTitle></CardHeader>
            <CardContent className="p-0">
              <table className="w-full text-sm border-collapse">
                <thead className="bg-[#F3F4F6] border-b-2 border-[#E5E7EB]">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Payment #</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Date</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Method</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider">Amount</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#E5E7EB] bg-white">
                  {payments.map((p) => (
                    <tr key={p.id} className="table-row-alt hover:bg-[#FFF7ED] cursor-pointer transition-colors" onClick={() => router.push(`/payments/${p.id}`)}>
                      <td className="px-4 py-3 font-mono text-xs text-[#CC6C00]">{p.payment_number}</td>
                      <td className="px-4 py-3 text-[#6B7280] text-xs">{formatDate(p.payment_date)}</td>
                      <td className="px-4 py-3 text-[#6B7280] text-xs">{p.payment_method}</td>
                      <td className="px-4 py-3 text-right font-mono-nums text-[#059669]">{fmt(p.amount, p.currency)}</td>
                      <td className="px-4 py-3"><StatusBadge status={p.status} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </CardContent>
          </Card>
        )}
      </div>

      <ConfirmDialog
        open={showPostConfirm}
        onOpenChange={setShowPostConfirm}
        title="Post Invoice"
        description={`Are you sure you want to post invoice ${invoice.invoice_number}? This will create a journal entry and cannot be undone.`}
        confirmLabel="Post Invoice"
        variant="warning"
        loading={posting}
        onConfirm={handlePost}
      />

      <ConfirmDialog
        open={showResetConfirm}
        onOpenChange={setShowResetConfirm}
        title="Reset to Draft"
        description={`Reverse the journal entry for ${invoice.invoice_number} and put it back into draft status? The original posting will be reversed in the GL (audit trail preserved). You can then edit and re-post.`}
        confirmLabel="Reset to Draft"
        variant="warning"
        loading={busyAction === 'reset'}
        onConfirm={handleResetToDraft}
      />

      <ConfirmDialog
        open={showCreditConfirm}
        onOpenChange={setShowCreditConfirm}
        title="Create Credit Note"
        description={`Create a credit note that reverses ${invoice.invoice_number}? A draft credit note will be created with negative line amounts — review and post it to apply.`}
        confirmLabel="Create Credit Note"
        variant="warning"
        loading={busyAction === 'credit'}
        onConfirm={handleCreditNote}
      />

      {/* New-Account modal */}
      {acctModalOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
          onClick={() => !newAcctSaving && setAcctModalOpen(false)}
        >
          <div
            className="bg-white rounded-xl shadow-xl w-full max-w-md p-5"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-base font-semibold text-[#0B0B3B]">New Account</h2>
              <button onClick={() => !newAcctSaving && setAcctModalOpen(false)} className="text-[#9CA3AF] hover:text-[#374151]">
                <X className="w-4 h-4" />
              </button>
            </div>
            <p className="text-xs text-[#6B7280] mb-4">
              Add a new GL account to the chart of accounts. It becomes immediately usable on this invoice line.
            </p>
            {newAcctErr && (
              <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-md p-2.5 mb-3 text-xs text-[#DC2626]">
                {newAcctErr}
              </div>
            )}
            <div className="space-y-3">
              <div>
                <label className="block text-xs font-medium text-[#374151] mb-1">Account Code <span className="text-[#DC2626]">*</span></label>
                <input
                  type="text"
                  value={newAcctCode}
                  onChange={(e) => setNewAcctCode(e.target.value)}
                  placeholder="e.g. 4150"
                  className="w-full text-sm font-mono rounded-md px-3 py-2 border border-[#E5E7EB]"
                />
                <p className="text-[10px] text-[#9CA3AF] mt-0.5">
                  4xxx = revenue, 5xxx/6xxx = expense, 1xxx = asset, 2xxx = liability, 3xxx = equity
                </p>
              </div>
              <div>
                <label className="block text-xs font-medium text-[#374151] mb-1">Account Name <span className="text-[#DC2626]">*</span></label>
                <input
                  type="text"
                  value={newAcctName}
                  onChange={(e) => setNewAcctName(e.target.value)}
                  placeholder="e.g. Insurance fee revenue"
                  className="w-full text-sm rounded-md px-3 py-2 border border-[#E5E7EB]"
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium text-[#374151] mb-1">Type</label>
                  <select
                    value={newAcctType}
                    onChange={(e) => setNewAcctType(e.target.value as CreateAccountInput['account_type'])}
                    className="w-full text-sm rounded-md px-3 py-2 border border-[#E5E7EB] bg-white"
                  >
                    <option value="asset">Asset</option>
                    <option value="liability">Liability</option>
                    <option value="equity">Equity</option>
                    <option value="revenue">Revenue</option>
                    <option value="expense">Expense</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-medium text-[#374151] mb-1">Sub-type</label>
                  <input
                    type="text"
                    value={newAcctSubType}
                    onChange={(e) => setNewAcctSubType(e.target.value)}
                    placeholder="optional"
                    className="w-full text-sm rounded-md px-3 py-2 border border-[#E5E7EB]"
                  />
                </div>
              </div>
            </div>
            <div className="flex items-center justify-end gap-2 mt-5">
              <Button variant="outline" size="sm" onClick={() => setAcctModalOpen(false)} disabled={newAcctSaving}>
                Cancel
              </Button>
              <Button variant="accent" size="sm" leftIcon={<Plus className="w-3.5 h-3.5" />} onClick={submitNewAccount} disabled={newAcctSaving}>
                {newAcctSaving ? 'Creating…' : 'Create Account'}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
