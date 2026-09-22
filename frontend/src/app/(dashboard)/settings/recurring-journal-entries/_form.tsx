'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter, useParams } from 'next/navigation'
import {
  getRecurringJE, createRecurringJE, updateRecurringJE,
  getAccounts, getCompanies, getCurrencies, getToken,
} from '@/lib/api'
import type {
  RecurringFrequency, CreateRecurringJEInput, RecurringJELineInput,
  Account, Company, Currency,
} from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { SearchableSelect } from '@/components/ui/SearchableSelect'
import { ArrowLeft, AlertCircle, Plus, Trash2, Save } from 'lucide-react'
import { localYmd } from '@/lib/utils'

const inputCls =
  'w-full h-10 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm text-[#111827] placeholder-[#9CA3AF] focus:outline-none focus:border-[#F07F00] focus:ring-[3px] focus:ring-[rgba(240,127,0,0.1)] transition-all'

export default function RecurringJEForm({ mode }: { mode: 'create' | 'edit' }) {
  const router = useRouter()
  const params = useParams<{ id?: string }>()
  const id = mode === 'edit' ? (params?.id as string) : undefined

  const today = localYmd(new Date())
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [journalType, setJournalType] = useState('general')
  const [frequency, setFrequency] = useState<RecurringFrequency>('monthly')
  const [dayOfPeriod, setDayOfPeriod] = useState(28)
  const [companyId, setCompanyId] = useState('')
  const [currencyCode, setCurrencyCode] = useState('BWP')
  const [startDate, setStartDate] = useState(today)
  const [endDate, setEndDate] = useState('')
  const [isActive, setIsActive] = useState(true)
  const [lines, setLines] = useState<RecurringJELineInput[]>([
    { account: '', description: '', debit_amount: '0', credit_amount: '0' },
    { account: '', description: '', debit_amount: '0', credit_amount: '0' },
  ])

  const [accounts, setAccounts] = useState<Account[]>([])
  const [companies, setCompanies] = useState<Company[]>([])
  const [currencies, setCurrencies] = useState<Currency[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [bootstrapped, setBootstrapped] = useState(false)

  const load = useCallback(async () => {
    try {
      const [accs, cos, ccys] = await Promise.all([
        getAccounts({ page_size: '500' }),
        getCompanies(),
        getCurrencies(),
      ])
      setAccounts(accs.results)
      setCompanies(cos.results)
      setCurrencies(ccys.results)

      const def = cos.results.find(c => c.is_default) || cos.results[0]
      if (def) setCompanyId(prev => prev || def.id)

      if (mode === 'edit' && id) {
        const t = await getRecurringJE(id)
        setName(t.name)
        setDescription(t.description)
        setJournalType(t.journal_type)
        setFrequency(t.frequency)
        setDayOfPeriod(t.day_of_period)
        setCompanyId(t.company || '')
        setCurrencyCode(t.currency_code)
        setStartDate(t.start_date)
        setEndDate(t.end_date || '')
        setIsActive(t.is_active)
        setLines(t.lines.map(l => ({
          account: l.account,
          description: l.description,
          debit_amount: l.debit_amount,
          credit_amount: l.credit_amount,
          contact: l.contact,
        })))
      }
      setBootstrapped(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, mode])

  useEffect(() => {
    const token = getToken()
    if (!token) { router.replace('/login'); return }
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load])

  function updateLine<K extends keyof RecurringJELineInput>(idx: number, k: K, v: RecurringJELineInput[K]) {
    setLines(prev => prev.map((l, i) => i === idx ? { ...l, [k]: v } : l))
  }

  function addLine() {
    setLines(prev => [...prev, { account: '', description: '', debit_amount: '0', credit_amount: '0' }])
  }

  function removeLine(idx: number) {
    setLines(prev => prev.filter((_, i) => i !== idx))
  }

  const totalDr = lines.reduce((s, l) => s + (parseFloat(l.debit_amount  || '0') || 0), 0)
  const totalCr = lines.reduce((s, l) => s + (parseFloat(l.credit_amount || '0') || 0), 0)
  const balanced = Math.abs(totalDr - totalCr) < 0.01

  async function save() {
    setBusy(true)
    setError(null)
    try {
      const payload: CreateRecurringJEInput = {
        name, description, journal_type: journalType,
        frequency, day_of_period: dayOfPeriod,
        currency_code: currencyCode,
        start_date: startDate,
        end_date: endDate || null,
        company: companyId || null,
        is_active: isActive,
        lines: lines.filter(l => l.account),
      }
      if (mode === 'create') {
        const t = await createRecurringJE(payload)
        router.push(`/settings/recurring-journal-entries`)
      } else if (id) {
        await updateRecurringJE(id, payload)
        router.push(`/settings/recurring-journal-entries`)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed')
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title={mode === 'create' ? 'New recurring template' : 'Edit recurring template'}
        breadcrumbs={[
          { label: 'Settings' },
          { label: 'Recurring JEs', href: '/settings/recurring-journal-entries' },
          { label: mode === 'create' ? 'New' : 'Edit' },
        ]}
        actions={
          <Button variant="ghost" size="sm" leftIcon={<ArrowLeft className="w-3.5 h-3.5" />} onClick={() => router.back()}>Back</Button>
        }
      />

      <div className="flex-1 p-6 max-w-4xl space-y-4">
        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-[#DC2626]" />
            <p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}

        <Card>
          <CardHeader><CardTitle>Template</CardTitle></CardHeader>
          <CardContent className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <Field label="Name *" className="md:col-span-2">
              <input value={name} onChange={e => setName(e.target.value)} className={inputCls} placeholder="Monthly office rent accrual" />
            </Field>
            <Field label="Description" className="md:col-span-2">
              <input value={description} onChange={e => setDescription(e.target.value)} className={inputCls} />
            </Field>
            <Field label="Frequency *">
              <select value={frequency} onChange={e => setFrequency(e.target.value as RecurringFrequency)} className={inputCls}>
                <option value="monthly">Monthly</option>
                <option value="quarterly">Quarterly</option>
                <option value="semiannual">Semi-annual</option>
                <option value="annual">Annual</option>
              </select>
            </Field>
            <Field label="Day of period (1–28) *">
              <input type="number" min="1" max="28" value={dayOfPeriod} onChange={e => setDayOfPeriod(parseInt(e.target.value || '1', 10))} className={inputCls} />
            </Field>
            <Field label="Journal type">
              <select value={journalType} onChange={e => setJournalType(e.target.value)} className={inputCls}>
                <option value="general">General</option>
                <option value="sales">Sales</option>
                <option value="purchases">Purchases</option>
                <option value="cash_receipts">Cash Receipts</option>
                <option value="cash_payments">Cash Payments</option>
                <option value="bank">Bank</option>
              </select>
            </Field>
            <Field label="Currency *">
              <select value={currencyCode} onChange={e => setCurrencyCode(e.target.value)} className={inputCls}>
                {currencies.map(c => <option key={c.code} value={c.code}>{c.code} — {c.name}</option>)}
              </select>
            </Field>
            <Field label="Company">
              <select value={companyId} onChange={e => setCompanyId(e.target.value)} className={inputCls}>
                <option value="">— All / Master —</option>
                {companies.map(c => <option key={c.id} value={c.id}>{c.code} — {c.name}</option>)}
              </select>
            </Field>
            <Field label="Start date *">
              <input type="date" value={startDate} onChange={e => setStartDate(e.target.value)} className={inputCls} />
            </Field>
            <Field label="End date (optional)">
              <input type="date" value={endDate} onChange={e => setEndDate(e.target.value)} className={inputCls} />
            </Field>
            <label className="md:col-span-2 flex items-center gap-2 text-sm">
              <input type="checkbox" checked={isActive} onChange={e => setIsActive(e.target.checked)} className="rounded" />
              <span><span className="font-medium">Active</span> — uncheck to pause generation without deleting the template</span>
            </label>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center justify-between">
              <span>Lines</span>
              <Button variant="outline" size="sm" leftIcon={<Plus className="w-3.5 h-3.5" />} onClick={addLine}>Add line</Button>
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            <table className="w-full text-sm border-collapse">
              <thead className="bg-[#F3F4F6] border-b-2 border-[#E5E7EB]">
                <tr>
                  <th className="px-3 py-2 text-left text-xs font-semibold text-[#374151] uppercase">Account</th>
                  <th className="px-3 py-2 text-left text-xs font-semibold text-[#374151] uppercase">Description</th>
                  <th className="px-3 py-2 text-right text-xs font-semibold text-[#374151] uppercase w-32">Debit</th>
                  <th className="px-3 py-2 text-right text-xs font-semibold text-[#374151] uppercase w-32">Credit</th>
                  <th className="px-3 py-2 w-10"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#E5E7EB] bg-white">
                {lines.map((l, i) => (
                  <tr key={i}>
                    <td className="px-3 py-2">
                      <SearchableSelect
                        dense
                        minWidth={200}
                        value={l.account}
                        onChange={v => updateLine(i, 'account', v)}
                        placeholder="— Select —"
                        options={accounts.map(a => ({ value: a.id, label: `${a.code} — ${a.name}` }))}
                      />
                    </td>
                    <td className="px-3 py-2">
                      <input value={l.description || ''} onChange={e => updateLine(i, 'description', e.target.value)} className={inputCls} />
                    </td>
                    <td className="px-3 py-2">
                      <input type="number" step="0.01" value={l.debit_amount} onChange={e => updateLine(i, 'debit_amount', e.target.value)} className={inputCls + ' text-right tabular-nums'} />
                    </td>
                    <td className="px-3 py-2">
                      <input type="number" step="0.01" value={l.credit_amount} onChange={e => updateLine(i, 'credit_amount', e.target.value)} className={inputCls + ' text-right tabular-nums'} />
                    </td>
                    <td className="px-3 py-2 text-center">
                      <button onClick={() => removeLine(i)} className="text-[#DC2626] hover:text-[#991B1B]" disabled={lines.length <= 2} title="Remove line">
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot className="bg-[#F9FAFB] border-t-2 border-[#E5E7EB] font-semibold">
                <tr>
                  <td colSpan={2} className="px-3 py-2 text-right">Totals</td>
                  <td className="px-3 py-2 text-right tabular-nums">{totalDr.toFixed(2)}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{totalCr.toFixed(2)}</td>
                  <td></td>
                </tr>
                {!balanced && (
                  <tr>
                    <td colSpan={5} className="px-3 py-2 text-right text-xs text-[#B91C1C]">
                      Out of balance by {Math.abs(totalDr - totalCr).toFixed(2)} — generated entries won&apos;t pass approval until balanced.
                    </td>
                  </tr>
                )}
              </tfoot>
            </table>
          </CardContent>
        </Card>

        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={() => router.back()} disabled={busy}>Cancel</Button>
          <Button variant="accent" leftIcon={<Save className="w-3.5 h-3.5" />} onClick={save} disabled={busy || !bootstrapped || !name}>
            {busy ? 'Saving…' : (mode === 'create' ? 'Create template' : 'Save changes')}
          </Button>
        </div>
      </div>
    </div>
  )
}

function Field({ label, children, className }: { label: string; children: React.ReactNode; className?: string }) {
  return (
    <label className={`block ${className || ''}`}>
      <span className="block text-xs font-medium text-[#374151] mb-1">{label}</span>
      {children}
    </label>
  )
}
