'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { getToken, getBankAccounts, getCurrencies, createOnceOffPayment, previewBankView, readInvoiceForPayment } from '@/lib/api'
import type { BankAccount, Currency, BankViewPreview } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { FormField, Input, Select } from '@/components/ui/input'
import { AlertCircle, CheckCircle2, Upload } from 'lucide-react'

const METHODS = [
  ['bank_transfer', 'Bank Transfer (EFT / RTGS)'], ['mobile_money', 'Mobile Money'],
  ['cheque', 'Cheque'], ['cash', 'Cash'],
]

export default function OnceOffPaymentPage() {
  const router = useRouter()
  const [banks, setBanks] = useState<BankAccount[]>([])
  const [currencies, setCurrencies] = useState<Currency[]>([])
  const [f, setF] = useState({
    payee_name: '', bank_name: '', account_number: '', branch_code: '',
    amount: '', currency: 'BWP', payment_method: 'bank_transfer', reference: '', bank_account: '',
    bank_beneficiary_name: '', bank_our_reference: '', bank_narration: '',
    // Where FNB emails the POP. Defaults to Accounts, editable (CFO 2026-08-22).
    remittance_email: 'accountsdept@alphadirect.co.bw',
  })
  const [bankView, setBankView] = useState<BankViewPreview | null>(null)
  const [bankViewFailed, setBankViewFailed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [done, setDone] = useState<{ payment_number?: string } | null>(null)
  // Invoice OCR pre-fill (CFO 2026-08-22, raised by Tlamelo).
  const [invReading, setInvReading] = useState(false)
  const [invPct, setInvPct] = useState(0)
  const [invMsg, setInvMsg] = useState<string | null>(null)
  const [invErr, setInvErr] = useState<string | null>(null)
  const [invFilled, setInvFilled] = useState<string[]>([])

  const onInvoicePick = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    e.target.value = ''            // let the same file be re-picked
    if (!file) return
    setInvErr(null); setInvMsg(null); setInvPct(0); setInvFilled([]); setInvReading(true)
    try {
      const res = await readInvoiceForPayment(file, setInvPct)
      if (!res.ok) { setInvErr(res.message || 'Could not read the invoice.'); return }
      const fld = res.fields
      const filled: string[] = []
      setF((s) => {
        const next = { ...s }
        if (fld.payee_name)     { next.payee_name = fld.payee_name;         filled.push('payee_name') }
        if (fld.account_number) { next.account_number = fld.account_number; filled.push('account_number') }
        if (fld.bank_name)      { next.bank_name = fld.bank_name;           filled.push('bank_name') }
        if (fld.branch_code)    { next.branch_code = fld.branch_code;       filled.push('branch_code') }
        if (fld.total_amount)   { next.amount = fld.total_amount;           filled.push('amount') }
        if (fld.invoice_number) { next.reference = fld.invoice_number;      filled.push('reference') }
        return next
      })
      setInvFilled(filled)
      setInvMsg(res.message + (res.tier === 'vision' ? ' (read from the photo)' : ''))
    } catch (e) {
      setInvErr(e instanceof Error ? e.message : 'Could not read the invoice.')
    } finally {
      setInvReading(false)
    }
  }
  // Ring a field the reader just filled, so the user knows what to check.
  const hl = (name: string) => (invFilled.includes(name) ? 'ring-2 ring-[#F4A623]' : '')

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    getBankAccounts().then((b) => {
      setBanks(b.results)
      if (b.results.length) setF((s) => ({ ...s, bank_account: b.results[0].id }))
    }).catch(() => {})
    getCurrencies().then((c) => setCurrencies(c.results)).catch(() => {})
  }, [router])

  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setF((s) => ({ ...s, [k]: e.target.value }))

  // Ask the server what the bank will be shown. Debounced so typing does not
  // fire a request per keystroke, and `alive` drops a slow reply that would
  // otherwise land after a newer one and show a stale answer.
  //
  // The values go RAW — no `bank_narration || reference` here. The server
  // mirrors what the create actually does with a blank reference; deciding it
  // on this side would be a second copy of that rule.
  useEffect(() => {
    let alive = true
    const t = setTimeout(() => {
      previewBankView({
        payee_name: f.payee_name,
        reference: f.reference,
        bank_beneficiary_name: f.bank_beneficiary_name,
        bank_our_reference: f.bank_our_reference,
        bank_narration: f.bank_narration,
      })
        .then((v) => { if (alive) { setBankView(v); setBankViewFailed(false) } })
        .catch(() => { if (alive) { setBankView(null); setBankViewFailed(true) } })
    }, 350)
    return () => { alive = false; clearTimeout(t) }
  }, [f.payee_name, f.reference, f.bank_beneficiary_name, f.bank_our_reference,
      f.bank_narration])

  const submit = async () => {
    setErr(null)
    if (!f.payee_name.trim()) { setErr('Enter the payee name.'); return }
    if (!f.account_number.trim()) { setErr("Enter the payee's account number."); return }
    if (!f.amount || Number(f.amount) <= 0) { setErr('Enter a valid amount.'); return }
    if (!f.bank_account) { setErr('Pick the paying bank account.'); return }
    setBusy(true)
    try {
      const r = await createOnceOffPayment({ ...f, submit: true })
      if (r.detail && !r.payment_number) { setErr(r.detail); return }
      setDone({ payment_number: r.payment_number })
    } catch (e) { setErr(e instanceof Error ? e.message : 'Failed to create payment') }
    finally { setBusy(false) }
  }

  if (done) {
    return (
      <div className="flex flex-col min-h-screen">
        <TopBar title="One-off Payment" breadcrumbs={[{ label: 'Payments', href: '/payments' }, { label: 'One-off' }]} />
        <div className="flex-1 p-6 max-w-2xl mx-auto w-full">
          <Card><CardContent className="p-6 space-y-3">
            <div className="flex items-center gap-2 text-[#059669]"><CheckCircle2 className="w-5 h-5" />
              <span className="font-medium">One-off payment created{done.payment_number ? ` — ${done.payment_number}` : ''}</span></div>
            <p className="text-sm text-[#6B7280]">It's in <b>Payment Approvals</b> awaiting the dual-control (FM + CFO) sign-off. It won't be paid until fully approved.</p>
            <div className="flex gap-3">
              <Button onClick={() => router.push('/payments/approvals')}>Go to Payment Approvals</Button>
              <Button variant="outline" onClick={() => { setDone(null); setF((s) => ({ ...s, payee_name: '', account_number: '', bank_name: '', branch_code: '', amount: '', reference: '', bank_beneficiary_name: '', bank_our_reference: '', bank_narration: '', remittance_email: 'accountsdept@alphadirect.co.bw' })) }}>New one-off</Button>
            </div>
          </CardContent></Card>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar title="One-off Payment" breadcrumbs={[{ label: 'Payments', href: '/payments' }, { label: 'One-off' }]} />
      <div className="flex-1 p-6 max-w-2xl mx-auto w-full space-y-5">
        {err && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-xl p-4 flex items-center gap-3">
            <AlertCircle className="w-5 h-5 text-[#DC2626] flex-shrink-0" /><p className="text-[#DC2626] text-sm">{err}</p>
          </div>
        )}
        <Card>
          <CardHeader><CardTitle>Pay a one-off payee</CardTitle></CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm text-[#6B7280]">
              For a payee that is <b>not</b> in the vendor list — enter their bank details here directly.
              No vendor record or pre-approved bank account needed. It still goes through the full
              dual-control (FM + CFO) approval before any money moves.
            </p>

            <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-4">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-sm font-semibold text-slate-900">Upload the invoice to auto-fill</div>
                  <div className="text-xs text-slate-500">
                    Omni reads the amount and the payee&apos;s bank details. Check them before you save.
                  </div>
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
                  <div className="mt-1 text-xs text-slate-500">
                    {invPct < 100 ? `Uploading… ${invPct}%` : 'Reading the invoice…'}
                  </div>
                </div>
              )}
              {invMsg && !invReading && (
                <div className="mt-3 flex items-center gap-2 text-sm text-[#059669]">
                  <CheckCircle2 className="h-4 w-4 shrink-0" />{invMsg}
                </div>
              )}
              {invErr && !invReading && (
                <div className="mt-3 text-sm text-amber-700">{invErr}</div>
              )}
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <FormField label="Payee name" required><Input className={hl('payee_name')} value={f.payee_name} onChange={set('payee_name')} placeholder="e.g. John Smith / ABC Traders" /></FormField>
              <FormField label="Paying bank account" required>
                <Select value={f.bank_account} onChange={set('bank_account')}>
                  <option value="">Select…</option>
                  {banks.map((b) => <option key={b.id} value={b.id}>{b.account_name} — {b.bank_name} ({b.currency})</option>)}
                </Select>
              </FormField>
              <FormField label="Payee bank name"><Input className={hl('bank_name')} value={f.bank_name} onChange={set('bank_name')} placeholder="e.g. FNB Botswana" /></FormField>
              <FormField label="Payee account number" required><Input className={hl('account_number')} value={f.account_number} onChange={set('account_number')} placeholder="Account number" /></FormField>
              <FormField label="Branch code"><Input className={hl('branch_code')} value={f.branch_code} onChange={set('branch_code')} placeholder="Branch code" /></FormField>
              <FormField label="Payment method"><Select value={f.payment_method} onChange={set('payment_method')}>{METHODS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Select></FormField>
              <FormField label="Amount" required><Input className={hl('amount')} type="number" value={f.amount} onChange={set('amount')} placeholder="0.00" /></FormField>
              <FormField label="Currency"><Select value={f.currency} onChange={set('currency')}>{(currencies.length ? currencies : [{ code: 'BWP', name: 'Botswana Pula' } as Currency]).map((c) => <option key={c.code} value={c.code}>{c.code}</option>)}</Select></FormField>
              <div className="md:col-span-2"><FormField label="Reference"><Input className={hl('reference')} value={f.reference} onChange={set('reference')} placeholder="What is this payment for?" /></FormField></div>
            </div>

            <div className="mt-8 border-t border-slate-200 pt-6">
              <h3 className="text-sm font-semibold text-slate-900">What the bank is told</h3>
              <p className="mt-1 text-xs text-slate-500">
                Leave any of these blank and we fill it in the usual way. Fill one
                in and that is exactly what goes to FNB.
              </p>

              <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
                <FormField label="Beneficiary name (what the bank calls the payee)">
                  <Input value={f.bank_beneficiary_name} onChange={set('bank_beneficiary_name')}
                         maxLength={140} placeholder={f.payee_name || 'The payee name we hold'} />
                </FormField>
                <FormField label="Our reference (for matching it back)">
                  <Input value={f.bank_our_reference} onChange={set('bank_our_reference')}
                         maxLength={35} placeholder="Blank = the payment number" />
                </FormField>
                <div className="md:col-span-2">
                  <FormField label="Their reference (what the payee sees on their statement)">
                    <Input value={f.bank_narration} onChange={set('bank_narration')}
                           maxLength={140} placeholder="Blank = the reference above" />
                  </FormField>
                </div>
                <div className="md:col-span-2">
                  <FormField label="Proof of payment email (FNB sends the POP here)">
                    <Input type="email" value={f.remittance_email} onChange={set('remittance_email')}
                           maxLength={254} placeholder="accountsdept@alphadirect.co.bw" />
                    <p className="mt-1 text-xs text-slate-500">
                      Defaults to Accounts — change it to send the proof of payment
                      straight to the payee.
                    </p>
                  </FormField>
                </div>
              </div>

              {bankViewFailed && (
                <div className="mt-4 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
                  Could not check what the bank will be shown. The payment can
                  still be created — but you are sending it unseen.
                </div>
              )}

              {bankView && (
                <div className="mt-4 rounded-lg bg-slate-50 p-4">
                  <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                    Exactly what FNB will receive
                  </div>
                  <dl className="mt-2 space-y-1 text-sm">
                    <div className="flex gap-3">
                      <dt className="w-40 shrink-0 text-slate-500">Beneficiary</dt>
                      <dd className="font-mono text-slate-900">{bankView.beneficiary_name || '—'}</dd>
                    </div>
                    <div className="flex gap-3">
                      <dt className="w-40 shrink-0 text-slate-500">Our reference</dt>
                      <dd className="font-mono text-slate-900">
                        {bankView.our_reference
                          || <span className="font-sans text-slate-500">the payment number, once it is created</span>}
                      </dd>
                    </div>
                    <div className="flex gap-3">
                      <dt className="w-40 shrink-0 text-slate-500">Their reference</dt>
                      <dd className="font-mono text-slate-900">{bankView.narration || '—'}</dd>
                    </div>
                  </dl>
                  <p className="mt-2 text-xs text-slate-500">
                    FNB only accepts plain typing. Long dashes, curly quotes and
                    accents are cleaned automatically — that is why what you see
                    here can differ slightly from what you typed.
                  </p>
                </div>
              )}
            </div>

            <Button disabled={busy} onClick={submit} className="mt-6">{busy ? 'Creating…' : 'Create + submit for approval'}</Button>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
