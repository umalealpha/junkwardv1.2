'use client'

import { useEffect, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import {
  getContacts,
  getAccounts,
  getTaxRates,
  getCurrencies,
  createInvoice,
  getToken,
  getPurchaseOrders,
} from '@/lib/api'
import type {
  Contact, Account, TaxRate, Currency, PurchaseOrderListItem,
} from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { FormField, Input, Select } from '@/components/ui/input'
import { formatAmount, localYmd, parseAmount, today } from '@/lib/utils'
import { useNumberFormat } from '@/contexts/NumberFormatContext'
import { Plus, Trash2, AlertCircle } from 'lucide-react'

// ─── Line item type ───────────────────────────────────────────────────────────

interface LineItem {
  id: string
  account: string
  description: string
  quantity: string
  unit_price: string
  tax_code: string
  // Computed
  tax_amount: number
  line_total: number
}

function newLine(): LineItem {
  return {
    id: Math.random().toString(36).slice(2),
    account: '',
    description: '',
    quantity: '1',
    unit_price: '',
    tax_code: '',
    tax_amount: 0,
    line_total: 0,
  }
}

// ─── New Invoice page ─────────────────────────────────────────────────────────

export default function NewInvoicePage() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const { mode } = useNumberFormat()
  const fmt = (amount: string | number, currency = 'BWP') => formatAmount(amount, currency, mode)

  const defaultType = searchParams.get('type') === 'vendor_bill' ? 'vendor_bill' : 'customer_invoice'

  const [invoiceType, setInvoiceType] = useState(defaultType)
  const [contact, setContact] = useState('')
  const [purchaseOrder, setPurchaseOrder] = useState('')
  const [pos, setPos] = useState<PurchaseOrderListItem[]>([])
  const [loadingPOs, setLoadingPOs] = useState(false)
  const isVendorBill = invoiceType === 'vendor_bill'
  const [issueDate, setIssueDate] = useState(today())
  const [dueDate, setDueDate] = useState(() => {
    const d = new Date()
    d.setDate(d.getDate() + 30)
    return localYmd(d)
  })
  const [currency, setCurrency] = useState('BWP')
  const [description, setDescription] = useState('')
  const [lines, setLines] = useState<LineItem[]>([newLine()])
  const [submitting, setSubmitting] = useState(false)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [apiError, setApiError] = useState<string | null>(null)

  // Data
  const [contacts, setContacts] = useState<Contact[]>([])
  const [accounts, setAccounts] = useState<Account[]>([])
  const [taxRates, setTaxRates] = useState<TaxRate[]>([])
  const [currencies, setCurrencies] = useState<Currency[]>([])

  useEffect(() => {
    const token = getToken()
    if (!token) {
      router.replace('/login')
      return
    }
    // Load reference data
    Promise.all([
      getContacts({ page_size: '500' }),
      getAccounts({ page_size: '500' }),
      getTaxRates(),
      getCurrencies(),
    ]).then(([c, a, t, curr]) => {
      setContacts(c.results)
      setAccounts(a.results)
      setTaxRates(t.results)
      setCurrencies(curr.results)
    }).catch(console.error)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // For vendor bills: load this vendor's APPROVED purchase orders so the
  // user can pick one. CFO-mandated: every vendor bill must reference a PO.
  useEffect(() => {
    if (!isVendorBill || !contact) {
      setPos([]); setPurchaseOrder(''); return
    }
    setLoadingPOs(true)
    Promise.all([
      getPurchaseOrders({ supplier: contact, status: 'approved' }),
      getPurchaseOrders({ supplier: contact, status: 'partially_received' }),
      getPurchaseOrders({ supplier: contact, status: 'fully_received' }),
    ])
      .then(([a, b, c]) => {
        const merged = [...a.results, ...b.results, ...c.results]
        setPos(merged)
        if (merged.length === 1) setPurchaseOrder(merged[0].id)
      })
      .catch(() => { setPos([]); setPurchaseOrder('') })
      .finally(() => setLoadingPOs(false))
  }, [contact, isVendorBill])

  // Compute line totals
  const computeLine = (line: LineItem, taxRatesList: TaxRate[]): LineItem => {
    const qty = parseAmount(line.quantity)
    const price = parseAmount(line.unit_price)
    const subtotal = qty * price
    const taxRate = taxRatesList.find((t) => t.tax_code === line.tax_code)
    const taxPct = taxRate ? parseAmount(taxRate.rate) / 100 : 0
    const tax = subtotal * taxPct
    return { ...line, tax_amount: tax, line_total: subtotal + tax }
  }

  const updateLine = (id: string, field: keyof LineItem, value: string) => {
    setLines((prev) =>
      prev.map((l) => {
        if (l.id !== id) return l
        const updated = { ...l, [field]: value }
        return computeLine(updated, taxRates)
      })
    )
  }

  const addLine = () => setLines((prev) => [...prev, newLine()])
  const removeLine = (id: string) =>
    setLines((prev) => (prev.length > 1 ? prev.filter((l) => l.id !== id) : prev))

  const subtotal = lines.reduce((sum, l) => sum + parseAmount(l.unit_price) * parseAmount(l.quantity), 0)
  const taxTotal = lines.reduce((sum, l) => sum + l.tax_amount, 0)
  const grandTotal = subtotal + taxTotal

  const validate = () => {
    const errs: Record<string, string> = {}
    if (!contact) errs.contact = 'Contact is required'
    if (!issueDate) errs.issueDate = 'Issue date is required'
    if (!dueDate) errs.dueDate = 'Due date is required'
    if (!currency) errs.currency = 'Currency is required'
    if (isVendorBill && !purchaseOrder) {
      errs.purchaseOrder =
        'Vendor bills require a Purchase Order reference. Pick the approved PO this bill belongs to.'
    }
    lines.forEach((l, idx) => {
      if (!l.account) errs[`line_${idx}_account`] = 'Account required'
      if (!l.description) errs[`line_${idx}_description`] = 'Description required'
      if (!l.unit_price || parseAmount(l.unit_price) <= 0)
        errs[`line_${idx}_unit_price`] = 'Price must be > 0'
    })
    setErrors(errs)
    return Object.keys(errs).length === 0
  }

  const handleSubmit = async () => {
    if (!validate()) return
    setSubmitting(true)
    setApiError(null)
    try {
      const result = await createInvoice({
        invoice_type: invoiceType,
        contact,
        issue_date: issueDate,
        due_date: dueDate,
        currency_code: currency,
        description,
        purchase_order: isVendorBill ? (purchaseOrder || null) : null,
        lines: lines.map((l) => ({
          account: l.account,
          description: l.description,
          quantity: l.quantity,
          unit_price: l.unit_price,
          tax_code: l.tax_code,
        })),
      })
      router.push(`/invoices/${result.id}`)
    } catch (err) {
      setApiError(err instanceof Error ? err.message : 'Failed to create invoice')
    } finally {
      setSubmitting(false)
    }
  }

  const revenueExpenseAccounts = accounts.filter((a) =>
    ['revenue', 'expense', 'income'].includes(a.account_type?.toLowerCase())
  )

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title={invoiceType === 'vendor_bill' ? 'New Vendor Bill' : 'New Customer Invoice'}
        breadcrumbs={[
          { label: 'Invoices', href: '/invoices' },
          { label: invoiceType === 'vendor_bill' ? 'New Bill' : 'New Invoice' },
        ]}
      />

      <div className="flex-1 p-6 max-w-5xl mx-auto w-full space-y-6">
        {apiError && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-xl p-4 flex items-center gap-3">
            <AlertCircle className="w-5 h-5 text-[#DC2626] flex-shrink-0" />
            <p className="text-[#DC2626] text-sm">{apiError}</p>
          </div>
        )}

        {/* Header details */}
        <Card>
          <CardHeader>
            <CardTitle>Invoice Details</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              <FormField label="Invoice Type" required>
                <Select value={invoiceType} onChange={(e) => setInvoiceType(e.target.value)}>
                  <option value="customer_invoice">Customer Invoice</option>
                  <option value="vendor_bill">Vendor Bill</option>
                </Select>
              </FormField>

              <FormField label="Contact" required error={errors.contact}>
                <Select
                  value={contact}
                  onChange={(e) => setContact(e.target.value)}
                  error={errors.contact}
                >
                  <option value="">Select contact...</option>
                  {contacts.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </Select>
              </FormField>

              {isVendorBill && (
                <FormField
                  label="Purchase Order"
                  required
                  error={errors.purchaseOrder}
                  className="md:col-span-2 lg:col-span-3"
                >
                  {!contact ? (
                    <p className="text-xs text-[#6B7280] py-2">
                      Pick the vendor first — PO list will load.
                    </p>
                  ) : loadingPOs ? (
                    <p className="text-xs text-[#6B7280] py-2">
                      Loading approved POs for this vendor…
                    </p>
                  ) : pos.length === 0 ? (
                    <div className="text-xs text-[#B45309] bg-[#FFFBEB] border border-[#FDE68A] rounded p-2">
                      No <strong>approved</strong> Purchase Orders on file for this vendor.
                      A vendor bill cannot post without a PO reference. Raise and approve a PO
                      on{' '}
                      <a href="/purchase-orders/new" className="underline">/purchase-orders/new</a>{' '}
                      first.
                    </div>
                  ) : (
                    <>
                      <Select
                        value={purchaseOrder}
                        onChange={(e) => setPurchaseOrder(e.target.value)}
                        error={errors.purchaseOrder}
                      >
                        <option value="">Select Purchase Order…</option>
                        {pos.map((p) => (
                          <option key={p.id} value={p.id}>
                            {p.po_number} — {p.department_display} — {p.currency_code}{' '}
                            {p.total_amount} ({p.status_display})
                          </option>
                        ))}
                      </Select>
                      <p className="text-xs text-[#6B7280] mt-1">
                        On post, the system auto-matches the bill against this PO and routes
                        any variance for approval. ARIA runs a second-pass anti-fraud check
                        in the background.
                      </p>
                    </>
                  )}
                </FormField>
              )}

              <FormField label="Currency" required error={errors.currency}>
                <Select
                  value={currency}
                  onChange={(e) => setCurrency(e.target.value)}
                  error={errors.currency}
                >
                  {currencies.map((c) => (
                    <option key={c.code} value={c.code}>
                      {c.code} - {c.name}
                    </option>
                  ))}
                  {currencies.length === 0 && (
                    <>
                      <option value="BWP">BWP - Botswana Pula</option>
                      <option value="USD">USD - US Dollar</option>
                      <option value="ZAR">ZAR - South African Rand</option>
                    </>
                  )}
                </Select>
              </FormField>

              <FormField label="Issue Date" required error={errors.issueDate}>
                <input
                  type="date"
                  value={issueDate}
                  onChange={(e) => setIssueDate(e.target.value)}
                  className="w-full bg-white border border-[#D1D5DB] rounded-lg px-3 py-2 text-sm text-[#111827] focus:outline-none focus:border-[#F07F00] focus:ring-[3px] focus:ring-[rgba(240,127,0,0.1)] transition-all"
                />
              </FormField>

              <FormField label="Due Date" required error={errors.dueDate}>
                <input
                  type="date"
                  value={dueDate}
                  onChange={(e) => setDueDate(e.target.value)}
                  className="w-full bg-white border border-[#D1D5DB] rounded-lg px-3 py-2 text-sm text-[#111827] focus:outline-none focus:border-[#F07F00] focus:ring-[3px] focus:ring-[rgba(240,127,0,0.1)] transition-all"
                />
              </FormField>

              <FormField label="Description">
                <Input
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="Optional description..."
                />
              </FormField>
            </div>
          </CardContent>
        </Card>

        {/* Line Items */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle>Line Items</CardTitle>
            <Button
              variant="secondary"
              size="sm"
              leftIcon={<Plus className="w-3.5 h-3.5" />}
              onClick={addLine}
            >
              Add Line
            </Button>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-[#F3F4F6] border-b-2 border-[#E5E7EB]">
                  <tr>
                    <th className="px-3 py-2.5 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider min-w-[180px]">
                      Account
                    </th>
                    <th className="px-3 py-2.5 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider min-w-[180px]">
                      Description
                    </th>
                    <th className="px-3 py-2.5 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider w-20">
                      Qty
                    </th>
                    <th className="px-3 py-2.5 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider w-28">
                      Unit Price
                    </th>
                    <th className="px-3 py-2.5 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider w-28">
                      Tax
                    </th>
                    <th className="px-3 py-2.5 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider w-24">
                      Tax Amt
                    </th>
                    <th className="px-3 py-2.5 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider w-28">
                      Total
                    </th>
                    <th className="px-3 py-2.5 w-10" />
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#E5E7EB] bg-white">
                  {lines.map((line, idx) => (
                    <tr key={line.id} className="table-row-alt">
                      <td className="px-2 py-2">
                        <Select
                          value={line.account}
                          onChange={(e) => updateLine(line.id, 'account', e.target.value)}
                          error={errors[`line_${idx}_account`]}
                        >
                          <option value="">Select account...</option>
                          {revenueExpenseAccounts.map((a) => (
                            <option key={a.id} value={a.id}>
                              {a.code} - {a.name}
                            </option>
                          ))}
                        </Select>
                      </td>
                      <td className="px-2 py-2">
                        <Input
                          value={line.description}
                          onChange={(e) => updateLine(line.id, 'description', e.target.value)}
                          placeholder="Description..."
                          error={errors[`line_${idx}_description`]}
                        />
                      </td>
                      <td className="px-2 py-2">
                        <Input
                          type="number"
                          value={line.quantity}
                          onChange={(e) => updateLine(line.id, 'quantity', e.target.value)}
                          className="text-right"
                          min="0"
                          step="0.01"
                        />
                      </td>
                      <td className="px-2 py-2">
                        <Input
                          type="number"
                          value={line.unit_price}
                          onChange={(e) => updateLine(line.id, 'unit_price', e.target.value)}
                          className="text-right"
                          placeholder="0.00"
                          min="0"
                          step="0.01"
                          error={errors[`line_${idx}_unit_price`]}
                        />
                      </td>
                      <td className="px-2 py-2">
                        <Select
                          value={line.tax_code}
                          onChange={(e) => updateLine(line.id, 'tax_code', e.target.value)}
                        >
                          <option value="">No Tax</option>
                          {taxRates.map((t) => (
                            <option key={t.id} value={t.tax_code}>
                              {t.tax_code} ({t.rate}%)
                            </option>
                          ))}
                        </Select>
                      </td>
                      <td className="px-3 py-2 text-right font-mono-nums text-[#6B7280] text-xs">
                        {fmt(line.tax_amount, currency)}
                      </td>
                      <td className="px-3 py-2 text-right font-mono-nums text-[#111827] font-medium">
                        {fmt(line.line_total, currency)}
                      </td>
                      <td className="px-2 py-2 text-center">
                        <button
                          onClick={() => removeLine(line.id)}
                          className="text-[#D1D5DB] hover:text-[#DC2626] transition-colors p-1 rounded"
                          disabled={lines.length === 1}
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Totals */}
            <div className="flex justify-end mt-6">
              <div className="w-64 space-y-2">
                <div className="flex justify-between text-sm">
                  <span className="text-[#6B7280]">Subtotal</span>
                  <span className="text-[#374151] font-mono-nums">{fmt(subtotal, currency)}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-[#6B7280]">Tax Total</span>
                  <span className="text-[#374151] font-mono-nums">{fmt(taxTotal, currency)}</span>
                </div>
                <div className="flex justify-between text-base font-bold border-t border-[#E5E7EB] pt-2">
                  <span className="text-[#111827]">Grand Total</span>
                  <span className="text-[#CC6C00] font-mono-nums">{fmt(grandTotal, currency)}</span>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Actions */}
        <div className="flex items-center justify-end gap-3 pb-6">
          <Button
            variant="outline"
            size="md"
            onClick={() => router.push('/invoices')}
            disabled={submitting}
          >
            Cancel
          </Button>
          <Button
            variant="accent"
            size="md"
            loading={submitting}
            onClick={handleSubmit}
          >
            Create Invoice
          </Button>
        </div>
      </div>
    </div>
  )
}
