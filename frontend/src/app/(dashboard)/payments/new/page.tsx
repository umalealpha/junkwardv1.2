'use client'

import { useEffect, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import {
  getContacts,
  getBankAccounts,
  getCurrencies,
  createPayment,
  getToken,
  getVendorBankAccounts,
  previewBankView,
  readInvoiceForPayment,
} from '@/lib/api'
import type { Contact, BankAccount, Currency, VendorBankAccountListItem, BankViewPreview } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { FormField, Input, Select } from '@/components/ui/input'
import { formatAmount, today } from '@/lib/utils'
import { useNumberFormat } from '@/contexts/NumberFormatContext'
import { AlertCircle, Upload, CheckCircle2 } from 'lucide-react'

// ─── New Payment Page ─────────────────────────────────────────────────────────

export default function NewPaymentPage() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const { mode } = useNumberFormat()
  const fmt = (amount: string | number, currency = 'BWP') => formatAmount(amount, currency, mode)

  const [paymentType, setPaymentType] = useState<'received' | 'sent'>('received')
  const [contact, setContact] = useState(searchParams.get('contact') || '')
  const [bankAccount, setBankAccount] = useState('')
  const [vendorBankAccount, setVendorBankAccount] = useState('')
  const [paymentDate, setPaymentDate] = useState(today())
  const [currency, setCurrency] = useState('BWP')
  const [amount, setAmount] = useState('')
  const [paymentMethod, setPaymentMethod] = useState('bank_transfer')
  const [reference, setReference] = useState('')
  const [description, setDescription] = useState('')
  // Where FNB emails the proof of payment (POP). Defaults to Accounts, editable
  // per payment (CFO 2026-08-22, raised by Tlamelo).
  const [remittanceEmail, setRemittanceEmail] = useState('accountsdept@alphadirect.co.bw')
  const [submitting, setSubmitting] = useState(false)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [apiError, setApiError] = useState<string | null>(null)
  // "What FNB will see" preview (CFO 2026-08-22).
  const [bankView, setBankView] = useState<BankViewPreview | null>(null)
  // Invoice OCR pre-fill (CFO 2026-08-22).
  const [invReading, setInvReading] = useState(false)
  const [invPct, setInvPct] = useState(0)
  const [invMsg, setInvMsg] = useState<string | null>(null)
  const [invErr, setInvErr] = useState<string | null>(null)

  // Data
  const [contacts, setContacts] = useState<Contact[]>([])   // type-to-search results only
  const [contactQuery, setContactQuery] = useState('')
  const [contactName, setContactName] = useState('')        // name of the picked contact (for the summary)
  const [bankAccounts, setBankAccounts] = useState<BankAccount[]>([])
  const [currencies, setCurrencies] = useState<Currency[]>([])
  const [vendorBanks, setVendorBanks] = useState<VendorBankAccountListItem[]>([])
  const [loadingVendorBanks, setLoadingVendorBanks] = useState(false)

  // Outbound electronic payment must hit an APPROVED vendor bank account.
  const ELECTRONIC = new Set(['bank_transfer', 'debit_order', 'mobile_money', 'gateway', 'rtgs'])
  const needsVendorBank = paymentType === 'sent' && ELECTRONIC.has(paymentMethod)

  // Type-to-search the Contact field on the server — scoped to vendors for a
  // Sent (vendor) payment, customers for a Received one. Empty until the user
  // types 2+ chars, so we never pull the ~200k-row contact list into the form.
  useEffect(() => {
    const q = contactQuery.trim()
    if (q.length < 2) { setContacts([]); return }
    const t = setTimeout(() => {
      getContacts({
        contact_type: paymentType === 'sent' ? 'vendor' : 'customer',
        page_size: '50',
        search: q,
      }).then((c) => setContacts(c.results)).catch(() => {})
    }, 300)
    return () => clearTimeout(t)
  }, [contactQuery, paymentType])

  useEffect(() => {
    const token = getToken()
    if (!token) {
      router.replace('/login')
      return
    }
    // Do NOT preload contacts — there are ~200k of them. The Contact field
    // below is a type-to-search box that queries the server on demand.
    Promise.all([
      getBankAccounts(),
      getCurrencies(),
    ]).then(([b, curr]) => {
      setBankAccounts(b.results)
      setCurrencies(curr.results)
      if (b.results.length > 0) setBankAccount(b.results[0].id)
    }).catch(console.error)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Reload vendor bank accounts whenever the chosen vendor changes (for SENT payments)
  useEffect(() => {
    if (!needsVendorBank || !contact) {
      setVendorBanks([])
      setVendorBankAccount('')
      return
    }
    setLoadingVendorBanks(true)
    getVendorBankAccounts({ contact, status: 'active' })
      .then((r) => {
        setVendorBanks(r.results)
        const def = r.results.find((b) => b.currency_code === currency && b.is_default)
                ?? r.results.find((b) => b.currency_code === currency)
                ?? r.results.find((b) => b.is_default)
                ?? r.results[0]
        setVendorBankAccount(def ? def.id : '')
        // Remember-the-POP (CFO 2026-08-22): pre-fill the POP email from this
        // vendor's remembered address, so it isn't re-typed every payment.
        if (def?.email) setRemittanceEmail(def.email)
      })
      .catch(() => { setVendorBanks([]); setVendorBankAccount('') })
      .finally(() => setLoadingVendorBanks(false))
  }, [contact, currency, needsVendorBank])

  // Invoice OCR pre-fill — drop the invoice, fill the amount (CFO 2026-08-22).
  const onInvoicePick = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    setInvErr(null); setInvMsg(null); setInvPct(0); setInvReading(true)
    try {
      const res = await readInvoiceForPayment(file, setInvPct)
      if (!res.ok) { setInvErr(res.message || 'Could not read the invoice.'); return }
      const f = res.fields
      if (f.total_amount) setAmount(f.total_amount)
      if (f.invoice_number && !reference) setReference(f.invoice_number)
      setInvMsg(res.message + (res.tier === 'vision' ? ' (read from the photo)' : ''))
    } catch (err) {
      setInvErr(err instanceof Error ? err.message : 'Could not read the invoice.')
    } finally { setInvReading(false) }
  }

  // "How this will appear at FNB" — live, only for real outbound EFTs. The
  // beneficiary is the VENDOR BANK ACCOUNT's holder name (what FNB actually
  // gets), and the narration is the description/reference; the server folds both
  // to FNB's character set. The reference number itself doesn't exist until save,
  // so it is described rather than faked (debounced).
  useEffect(() => {
    if (!needsVendorBank) { setBankView(null); return }
    const selectedVba = vendorBanks.find((b) => b.id === vendorBankAccount)
    const beneficiary = selectedVba?.account_holder_name || contactName
    if (!beneficiary) { setBankView(null); return }
    let alive = true
    const t = setTimeout(() => {
      previewBankView({
        bank_beneficiary_name: beneficiary,
        bank_narration: description.trim() || reference.trim() || '',
      })
        .then((v) => { if (alive) setBankView(v) })
        .catch(() => { if (alive) setBankView(null) })
    }, 350)
    return () => { alive = false; clearTimeout(t) }
  }, [needsVendorBank, vendorBankAccount, vendorBanks, contactName, reference, description])

  const validate = () => {
    const errs: Record<string, string> = {}
    if (!contact) errs.contact = 'Contact is required'
    if (!bankAccount) errs.bankAccount = 'Bank account is required'
    if (!paymentDate) errs.paymentDate = 'Date is required'
    else if (paymentDate > today()) errs.paymentDate = 'Payment date cannot be in the future.'  // BUG-002
    if (!amount || parseFloat(amount) <= 0) errs.amount = 'Amount must be greater than 0'
    if (!paymentMethod) errs.paymentMethod = 'Payment method is required'
    if (needsVendorBank && !vendorBankAccount) {
      errs.vendorBankAccount =
        'Approved vendor bank account is required for outbound electronic payments. ' +
        'Have the Finance Manager approve one on /vendor-banking first.'
    }
    setErrors(errs)
    return Object.keys(errs).length === 0
  }

  const handleSubmit = async () => {
    if (!validate()) return
    setSubmitting(true)
    setApiError(null)
    try {
      const result = await createPayment({
        payment_type: paymentType,
        contact,
        bank_account: bankAccount,
        vendor_bank_account: needsVendorBank ? vendorBankAccount : null,
        payment_date: paymentDate,
        currency_code: currency,
        amount,
        payment_method: paymentMethod,
        reference,
        description,
        // POP email only matters for money going OUT electronically.
        ...(needsVendorBank ? { remittance_email: remittanceEmail.trim() } : {}),
      })
      router.push(`/payments/${result.id}`)
    } catch (err) {
      setApiError(err instanceof Error ? err.message : 'Failed to create payment')
    } finally {
      setSubmitting(false)
    }
  }

  const selectedBankAccount = bankAccounts.find((b) => b.id === bankAccount)

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Record Payment"
        breadcrumbs={[{ label: 'Payments', href: '/payments' }, { label: 'New Payment' }]}
      />

      <div className="flex-1 p-6 max-w-3xl mx-auto w-full space-y-6">
        {apiError && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-xl p-4 flex items-center gap-3">
            <AlertCircle className="w-5 h-5 text-[#DC2626] flex-shrink-0" />
            <p className="text-[#DC2626] text-sm">{apiError}</p>
          </div>
        )}

        <Card>
          <CardHeader>
            <CardTitle>Payment Details</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="mb-4 rounded-lg border border-dashed border-slate-300 bg-slate-50 p-4">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-sm font-semibold text-slate-900">Upload the invoice to auto-fill</div>
                  <div className="text-xs text-slate-500">Omni reads the amount. Check it before you save.</div>
                </div>
                <label className="shrink-0">
                  <input type="file" className="hidden" onChange={onInvoicePick} disabled={invReading}
                         accept=".pdf,.png,.jpg,.jpeg,.webp,.tif,.tiff,.xlsx,.xls,.csv" />
                  <span className={`inline-flex cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium ${invReading ? 'bg-slate-200 text-slate-500' : 'bg-[#0D1B2A] text-white hover:opacity-90'}`}>
                    <Upload className="h-4 w-4" />{invReading ? 'Reading…' : 'Upload invoice'}
                  </span>
                </label>
              </div>
              {invReading && (
                <div className="mt-3">
                  <div className="h-2 w-full overflow-hidden rounded-full bg-slate-200">
                    <div className="h-full bg-[#F4A623] transition-all" style={{ width: `${invPct}%` }} />
                  </div>
                  <div className="mt-1 text-xs text-slate-500">{invPct < 100 ? `Uploading… ${invPct}%` : 'Reading the invoice…'}</div>
                </div>
              )}
              {invMsg && !invReading && (
                <div className="mt-3 flex items-center gap-2 text-sm text-[#059669]"><CheckCircle2 className="h-4 w-4 shrink-0" />{invMsg}</div>
              )}
              {invErr && !invReading && (<div className="mt-3 text-sm text-amber-700">{invErr}</div>)}
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <FormField label="Payment Type" required>
                <Select
                  value={paymentType}
                  onChange={(e) => {
                    setPaymentType(e.target.value as 'received' | 'sent')
                    setContact(''); setContactName(''); setContactQuery('')
                  }}
                >
                  <option value="received">Received (Customer Payment)</option>
                  <option value="sent">Sent (Vendor Payment)</option>
                </Select>
              </FormField>

              <FormField label="Contact" required error={errors.contact}>
                <input
                  type="text"
                  className="input"
                  placeholder={paymentType === 'sent' ? 'Type the vendor name to search…' : 'Type the customer name to search…'}
                  value={contactQuery}
                  onChange={(e) => { setContactQuery(e.target.value); setContact(''); setContactName('') }}
                />
                {contactName && !contactQuery && (
                  <p className="mt-1 text-xs text-gray-600">Selected: <b>{contactName}</b></p>
                )}
                {contactQuery.trim().length >= 2 && (
                  <Select
                    value={contact}
                    onChange={(e) => {
                      setContact(e.target.value)
                      const picked = contacts.find((c) => c.id === e.target.value)
                      setContactName(picked?.name || '')
                      if (picked) setContactQuery('')
                    }}
                    error={errors.contact}
                  >
                    <option value="">{contacts.length ? 'Choose from matches…' : 'No match — keep typing'}</option>
                    {contacts.map((c) => (
                      <option key={c.id} value={c.id}>{c.name}</option>
                    ))}
                  </Select>
                )}
              </FormField>

              <FormField label="Bank Account" required error={errors.bankAccount}>
                <Select
                  value={bankAccount}
                  onChange={(e) => setBankAccount(e.target.value)}
                  error={errors.bankAccount}
                >
                  <option value="">Select bank account...</option>
                  {bankAccounts.map((b) => (
                    <option key={b.id} value={b.id}>
                      {b.account_name} - {b.bank_name} ({b.currency})
                    </option>
                  ))}
                </Select>
                {selectedBankAccount && (
                  <p className="text-xs text-[#9CA3AF] mt-1">
                    Balance: {fmt(selectedBankAccount.current_balance, selectedBankAccount.currency)}
                  </p>
                )}
              </FormField>

              <FormField label="Payment Date" required error={errors.paymentDate}>
                <input
                  type="date"
                  max={today()}
                  value={paymentDate}
                  onChange={(e) => setPaymentDate(e.target.value)}
                  className="w-full bg-white border border-[#D1D5DB] rounded-lg px-3 py-2 text-sm text-[#111827] focus:outline-none focus:border-[#F07F00] focus:ring-[3px] focus:ring-[rgba(240,127,0,0.1)] transition-all"
                />
              </FormField>

              <FormField label="Currency" required>
                <Select value={currency} onChange={(e) => setCurrency(e.target.value)}>
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

              <FormField label="Amount" required error={errors.amount}>
                <Input
                  type="number"
                  value={amount}
                  onChange={(e) => setAmount(e.target.value)}
                  placeholder="0.00"
                  min="0"
                  step="0.01"
                  error={errors.amount}
                />
              </FormField>

              <FormField label="Payment Method" required error={errors.paymentMethod}>
                <Select
                  value={paymentMethod}
                  onChange={(e) => setPaymentMethod(e.target.value)}
                  error={errors.paymentMethod}
                >
                  <option value="bank_transfer">Bank Transfer (EFT / RTGS)</option>
                  <option value="cheque">Cheque</option>
                  <option value="cash">Cash</option>
                  <option value="debit_order">Debit Order</option>
                  <option value="mobile_money">Mobile Money</option>
                </Select>
              </FormField>

              {needsVendorBank && (
                <FormField
                  label="Vendor Bank Account"
                  required
                  error={errors.vendorBankAccount}
                  className="col-span-full"
                >
                  {loadingVendorBanks ? (
                    <p className="text-xs text-[#6B7280] py-2">Loading approved bank accounts…</p>
                  ) : vendorBanks.length === 0 ? (
                    <div className="text-xs text-[#B45309] bg-[#FFFBEB] border border-[#FDE68A] rounded p-2">
                      No <strong>ACTIVE</strong> bank accounts on file for this vendor.
                      Add one and have the Finance Manager approve it on{' '}
                      <a href="/vendor-banking" className="underline">/vendor-banking</a>{' '}
                      before recording this payment.
                    </div>
                  ) : (
                    <>
                      <Select
                        value={vendorBankAccount}
                        onChange={(e) => setVendorBankAccount(e.target.value)}
                        error={errors.vendorBankAccount}
                      >
                        <option value="">Select vendor bank account…</option>
                        {vendorBanks.map((b) => (
                          <option key={b.id} value={b.id}>
                            {b.bank_name} — {b.masked_account} ({b.currency_code})
                            {b.is_default ? ' · default' : ''}
                            {b.name_mismatch ? ' · ⚠ name mismatch' : ''}
                          </option>
                        ))}
                      </Select>
                      {(() => {
                        const sel = vendorBanks.find(b => b.id === vendorBankAccount)
                        if (sel && sel.currency_code !== currency) {
                          return (
                            <p className="text-xs text-[#B45309] mt-1">
                              ⚠ Selected bank account is in {sel.currency_code}, but the payment is in {currency}.
                            </p>
                          )
                        }
                        return null
                      })()}
                    </>
                  )}
                </FormField>
              )}

              <FormField label="Reference">
                <Input
                  value={reference}
                  onChange={(e) => setReference(e.target.value)}
                  placeholder="Bank reference or cheque number..."
                />
              </FormField>

              <FormField label="Description" className="col-span-full">
                <Input
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="Optional payment description..."
                />
              </FormField>

              {needsVendorBank && (
                <FormField label="Proof of payment email" className="col-span-full">
                  <Input
                    type="email"
                    value={remittanceEmail}
                    onChange={(e) => setRemittanceEmail(e.target.value)}
                    placeholder="accountsdept@alphadirect.co.bw"
                  />
                  <p className="text-xs text-[#6B7280] mt-1">
                    FNB emails the proof of payment here once the payment is made.
                    Defaults to Accounts — change it to send the POP to the payee.
                  </p>
                </FormField>
              )}
            </div>
          </CardContent>
        </Card>

        {/* How this will appear at FNB (CFO 2026-08-22) */}
        {bankView && (
          <Card>
            <CardContent className="py-4">
              <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                How this will appear at FNB
              </div>
              <dl className="mt-2 space-y-1 text-sm">
                <div className="flex gap-3">
                  <dt className="w-36 shrink-0 text-slate-500">Beneficiary</dt>
                  <dd className="font-mono text-slate-900">{bankView.beneficiary_name || '—'}</dd>
                </div>
                <div className="flex gap-3">
                  <dt className="w-36 shrink-0 text-slate-500">Our reference</dt>
                  <dd className="text-slate-900">
                    Leads with the payee name, then the payment number, marked <span className="font-mono">(O)</span>
                  </dd>
                </div>
                <div className="flex gap-3">
                  <dt className="w-36 shrink-0 text-slate-500">Their reference</dt>
                  <dd className="font-mono text-slate-900">
                    {bankView.narration || <span className="font-sans text-slate-500">the description you enter</span>}
                  </dd>
                </div>
              </dl>
              <p className="mt-2 text-xs text-slate-500">
                The <span className="font-mono">(O)</span> marks it as loaded through Omni. The
                payment number is added when you save. Long dashes and accents are cleaned for FNB.
              </p>
            </CardContent>
          </Card>
        )}

        {/* Summary */}
        {amount && parseFloat(amount) > 0 && (
          <Card className="border-[#FED7AA]">
            <CardContent className="py-4">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm text-[#6B7280]">Payment Summary</p>
                  <p className="text-xs text-[#9CA3AF] mt-0.5">
                    {paymentType === 'received' ? 'Receiving from' : 'Sending to'}{' '}
                    {contactName || contacts.find((c) => c.id === contact)?.name || 'selected contact'}
                  </p>
                </div>
                <div className="text-right">
                  <p
                    className={`text-2xl font-bold font-mono-nums ${
                      paymentType === 'received' ? 'text-[#059669]' : 'text-[#DC2626]'
                    }`}
                  >
                    {paymentType === 'received' ? '+' : '-'}
                    {fmt(amount, currency)}
                  </p>
                  <p className="text-xs text-[#9CA3AF]">{currency}</p>
                </div>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Actions */}
        <div className="flex items-center justify-end gap-3 pb-6">
          <Button variant="outline" size="md" onClick={() => router.push('/payments')} disabled={submitting}>
            Cancel
          </Button>
          <Button variant="accent" size="md" loading={submitting} disabled={paymentDate > today()} onClick={handleSubmit}>
            Record Payment
          </Button>
        </div>
      </div>
    </div>
  )
}
