'use client'

import { useEffect, useState, useCallback, useRef, DragEvent } from 'react'
import { useRouter } from 'next/navigation'
import { API_BASE, SSO_SENTINEL_TOKEN } from '@/lib/api'
import {
  Search,
  CheckSquare,
  Square,
  Loader2,
  CheckCircle2,
  AlertCircle,
  ExternalLink,
  Receipt,
  ArrowUpRight,
  ArrowDownLeft,
  FileText,
  X,
  Upload,
  FileUp,
  Sparkles,
  PenLine,
  Plus,
  Trash2,
  BookOpen,
} from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { SearchableSelect } from '@/components/ui/SearchableSelect'
// Card components handled inline
import { cn, formatAmount, today } from '@/lib/utils'
import { useNumberFormat } from '@/contexts/NumberFormatContext'
import { useTheme } from '@/contexts/ThemeContext'
import { useCompany } from '@/contexts/CompanyContext'
import {
  getToken,
  uploadDocument,
  getDocuments,
  getDocument,
  confirmDocument,
  rejectDocument,
  deleteDocument,
  createJournalEntry,
  getAccounts,
  getCurrencies,
} from '@/lib/api'
import type { DocumentUploadRecord, Account, Currency } from '@/lib/api'
import axios from 'axios'

// ─── Shared Types ─────────────────────────────────────────────────────────────

interface Contact {
  id: string
  name: string
  contact_type: string
  email: string | null
}

interface Invoice {
  id: string
  invoice_number: string
  issue_date: string
  due_date: string
  total_amount: string
  amount_paid: string
  balance_due: string
  status: string
  currency: string
}

interface BankAccount {
  id: string
  account_name: string
  bank_name: string
  currency: string
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function getAxios() {
  // QA-007 fix 2026-06-04: (1) was hardcoded http://localhost:8000 — in prod
  // that pointed Smart Entry at the user's own machine -> same-origin API_BASE.
  // (2) used legacy `Token <alpha_token>` which is empty under Microsoft SSO,
  // so calls 401'd. Attach the SAME auth the canonical client uses via an async
  // request interceptor: MSAL Bearer when SSO is on, else the legacy DRF token.
  // Interceptor keeps getAxios synchronous so call sites are unchanged.
  const inst = axios.create({ baseURL: API_BASE })
  inst.interceptors.request.use(async (config) => {
    let auth = ''
    try {
      const { acquireApiToken, SSO_API_CALLS_READY } = await import('@/auth/msal')
      if (SSO_API_CALLS_READY) {
        const b = await acquireApiToken()
        if (b) auth = `Bearer ${b}`
      }
    } catch { /* fall back to legacy token */ }
    if (!auth) {
      const t = localStorage.getItem('alpha_token')
      if (t) auth = `Token ${t}`
    }
    if (auth) config.headers.Authorization = auth
    return config
  })
  return inst
}

function fmtDate(iso: string) {
  if (!iso) return '\u2014'
  const d = new Date(iso + 'T00:00:00')
  return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })
}

function timeAgo(iso: string): string {
  const d = new Date(iso)
  const now = new Date()
  const sec = Math.floor((now.getTime() - d.getTime()) / 1000)
  if (sec < 60) return 'just now'
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min}m ago`
  const hr = Math.floor(min / 60)
  if (hr < 24) return `${hr}h ago`
  const days = Math.floor(hr / 24)
  return `${days}d ago`
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

const ACCEPTED_TYPES = '.pdf,.png,.jpg,.jpeg,.csv,.xlsx'
const MAX_FILE_SIZE = 10 * 1024 * 1024

// ─── ContactSearch ────────────────────────────────────────────────────────────

interface ContactSearchProps {
  contactType: 'customer' | 'vendor'
  value: Contact | null
  onChange: (c: Contact | null) => void
  disabled?: boolean
}

function getRecentContacts(type: string): Contact[] {
  try {
    const raw = localStorage.getItem(`alpha_recent_contacts_${type}`)
    return raw ? JSON.parse(raw) : []
  } catch { return [] }
}

function saveRecentContact(type: string, contact: Contact) {
  const existing = getRecentContacts(type)
  const filtered = existing.filter((c) => c.id !== contact.id)
  const updated = [contact, ...filtered].slice(0, 3)
  localStorage.setItem(`alpha_recent_contacts_${type}`, JSON.stringify(updated))
}

function ContactSearch({ contactType, value, onChange, disabled }: ContactSearchProps) {
  const { theme } = useTheme()
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<Contact[]>([])
  const [loading, setLoading] = useState(false)
  const [open, setOpen] = useState(false)
  const [recentContacts, setRecentContacts] = useState<Contact[]>([])

  useEffect(() => {
    setRecentContacts(getRecentContacts(contactType))
  }, [contactType])

  const selectContact = useCallback((c: Contact) => {
    saveRecentContact(contactType, c)
    setRecentContacts(getRecentContacts(contactType))
    onChange(c)
    setOpen(false)
    setQuery('')
  }, [contactType, onChange])

  const search = useCallback(
    async (q: string) => {
      if (q.length < 1) { setResults([]); return }
      setLoading(true)
      try {
        const api = getAxios()
        const typeParam = contactType === 'vendor' ? 'vendor,broker' : 'customer'
        const res = await api.get(`/contacts/?contact_type=${typeParam}&search=${encodeURIComponent(q)}&page_size=20`)
        setResults(res.data.results || [])
      } catch {
        setResults([])
      } finally {
        setLoading(false)
      }
    },
    [contactType]
  )

  useEffect(() => {
    const timer = setTimeout(() => search(query), 300)
    return () => clearTimeout(timer)
  }, [query, search])

  if (value) {
    return (
      <div className="flex items-center gap-2 px-3 py-2.5 rounded-lg" style={{ background: theme.oL, border: `1px solid ${theme.orange}40` }}>
        <div className="w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0" style={{ background: theme.oL, border: `1px solid ${theme.orange}40` }}>
          <span className="text-xs font-bold uppercase" style={{ color: theme.orange }}>{value.name[0]}</span>
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium truncate" style={{ color: theme.text }}>{value.name}</p>
          {value.email && <p className="text-xs truncate" style={{ color: theme.t3 }}>{value.email}</p>}
        </div>
        {!disabled && (
          <button onClick={() => onChange(null)} className="p-1 transition-colors flex-shrink-0 hover:opacity-70" style={{ color: theme.t3 }}>
            <X className="w-3.5 h-3.5" />
          </button>
        )}
      </div>
    )
  }

  return (
    <div className="relative">
      {/* Recent contacts chips */}
      {recentContacts.length > 0 && !disabled && (
        <div className="flex items-center gap-2 mb-2 flex-wrap">
          <span className="text-[11px]" style={{ color: theme.t3 }}>Recent:</span>
          {recentContacts.map((c) => (
            <button
              key={c.id}
              onClick={() => selectContact(c)}
              className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium transition-colors hover:opacity-80"
              style={{ background: theme.g100, color: theme.t2, border: `1px solid ${theme.g200}` }}
            >
              <span className="w-4 h-4 rounded-full flex items-center justify-center text-[9px] font-bold uppercase" style={{ background: theme.oL, color: theme.orange }}>{c.name[0]}</span>
              {c.name}
            </button>
          ))}
        </div>
      )}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4" style={{ color: theme.t3 }} />
        <input type="text" value={query}
          onChange={(e) => { setQuery(e.target.value); setOpen(true) }}
          onFocus={() => setOpen(true)}
          placeholder={`Search ${contactType}s...`} disabled={disabled}
          className="w-full pl-9 pr-4 py-2.5 rounded-lg text-sm focus:outline-none transition-all"
          style={{ background: theme.card, border: `1px solid ${theme.g200}`, color: theme.text }}
        />
        {loading && <Loader2 className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 animate-spin" style={{ color: theme.t3 }} />}
      </div>
      {open && results.length > 0 && (
        <div className="absolute z-50 w-full mt-1 rounded-lg shadow-lg overflow-hidden max-h-60 overflow-y-auto" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
          {results.map((c) => (
            <button key={c.id} className="w-full flex items-center gap-3 px-3 py-2.5 text-left transition-colors" style={{ background: theme.card }}
              onClick={() => selectContact(c)}
              onMouseEnter={(e) => { e.currentTarget.style.background = theme.oL }}
              onMouseLeave={(e) => { e.currentTarget.style.background = theme.card }}>
              <div className="w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0" style={{ background: theme.g100 }}>
                <span className="text-xs font-bold uppercase" style={{ color: theme.t2 }}>{c.name[0]}</span>
              </div>
              <div className="min-w-0">
                <p className="text-sm font-medium truncate" style={{ color: theme.text }}>{c.name}</p>
                <p className="text-xs truncate capitalize" style={{ color: theme.t3 }}>{c.contact_type}</p>
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ─── Payment Tab (shared for customer + vendor) ──────────────────────────────

interface PaymentTabProps { mode: 'customer' | 'vendor' }

function PaymentTab({ mode }: PaymentTabProps) {
  const { theme } = useTheme()
  const { mode: nfMode } = useNumberFormat()
  const fmt = (amount: string | number, currency = 'BWP') => formatAmount(amount, currency, nfMode)
  const [contact, setContact] = useState<Contact | null>(null)
  const [invoices, setInvoices] = useState<Invoice[]>([])
  const [loadingInvoices, setLoadingInvoices] = useState(false)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [bankAccounts, setBankAccounts] = useState<BankAccount[]>([])
  const [paymentDate, setPaymentDate] = useState(today())
  const [bankAccount, setBankAccount] = useState('')
  const [amount, setAmount] = useState('')
  const [paymentMethod, setPaymentMethod] = useState('EFT')
  const [reference, setReference] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [success, setSuccess] = useState<{ payment_number: string; id: string; je_number?: string; journal_entry?: string } | null>(null)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [jeExpanded, setJeExpanded] = useState(false)
  const [jeLines, setJeLines] = useState<{ account_code: string; account_name: string; debit_amount: string; credit_amount: string; description: string }[]>([])
  const [jeLoading, setJeLoading] = useState(false)

  useEffect(() => {
    getAxios().get('/bank-accounts/').then((r) => {
      setBankAccounts(r.data.results || [])
      if (r.data.results?.length) setBankAccount(r.data.results[0].id)
    }).catch(() => {})
  }, [])

  useEffect(() => {
    if (!contact) { setInvoices([]); setSelectedIds(new Set()); setAmount(''); return }
    setLoadingInvoices(true)
    const invType = mode === 'customer' ? 'customer_invoice' : 'vendor_bill'
    getAxios().get(`/invoices/?contact=${contact.id}&status=posted&invoice_type=${invType}&page_size=50`)
      .then((r) => { setInvoices((r.data.results || []).filter((inv: Invoice) => parseFloat(inv.balance_due) > 0)) })
      .catch(() => setInvoices([])).finally(() => setLoadingInvoices(false))
  }, [contact, mode])

  useEffect(() => {
    const total = invoices.filter((inv) => selectedIds.has(inv.id)).reduce((sum, inv) => sum + parseFloat(inv.balance_due), 0)
    if (total > 0) setAmount(total.toFixed(2))
  }, [selectedIds, invoices])

  const toggleInvoice = (id: string) => { setSelectedIds((prev) => { const next = new Set(prev); if (next.has(id)) next.delete(id); else next.add(id); return next }) }
  const runningTotal = invoices.filter((inv) => selectedIds.has(inv.id)).reduce((s, inv) => s + parseFloat(inv.balance_due), 0)

  const validate = () => {
    const e: Record<string, string> = {}
    if (!contact) e.contact = 'Please select a contact'
    if (!bankAccount) e.bankAccount = 'Please select a bank account'
    if (!amount || parseFloat(amount) <= 0) e.amount = 'Please enter a valid amount'
    if (!paymentDate) e.paymentDate = 'Please enter a payment date'
    setErrors(e); return Object.keys(e).length === 0
  }

  const handleSubmit = async () => {
    if (!validate()) return
    setSubmitting(true)
    try {
      const api = getAxios()
      const res = await api.post('/payments/', { payment_type: mode === 'customer' ? 'received' : 'sent', contact: contact!.id, bank_account: bankAccount, payment_date: paymentDate, amount, payment_method: paymentMethod, reference })
      for (const invId of Array.from(selectedIds)) {
        const inv = invoices.find((i) => i.id === invId)
        if (inv) await api.post(`/payments/${res.data.id}/allocate/`, { invoice: invId, amount: parseFloat(inv.balance_due) }).catch(() => {})
      }
      setSuccess({ payment_number: res.data.payment_number, id: res.data.id, je_number: res.data.je_number, journal_entry: res.data.journal_entry })
    } catch (err: any) { setErrors({ submit: err?.response?.data?.detail || err?.message || 'Failed to record payment' }) }
    finally { setSubmitting(false) }
  }

  const reset = () => { setContact(null); setInvoices([]); setSelectedIds(new Set()); setAmount(''); setReference(''); setSuccess(null); setErrors({}); setJeExpanded(false); setJeLines([]) }

  const loadJeLines = async (jeId: string) => {
    setJeLoading(true)
    try {
      const res = await getAxios().get(`/journal-entries/${jeId}/`)
      setJeLines(res.data.lines || [])
    } catch { setJeLines([]) }
    finally { setJeLoading(false) }
  }

  if (success) {
    return (
      <div className="py-8 flex flex-col items-center gap-6">
        <div className="w-full max-w-md rounded-xl p-8 flex flex-col items-center gap-4 text-center" style={{ background: theme.okB, border: `1px solid ${theme.ok}40` }}>
          <div className="w-16 h-16 rounded-full flex items-center justify-center" style={{ background: theme.okB }}><CheckCircle2 className="w-8 h-8" style={{ color: theme.ok }} /></div>
          <div>
            <h3 className="text-lg font-semibold" style={{ color: theme.ok }}>Payment Recorded!</h3>
            <p className="text-sm mt-1" style={{ color: theme.t2 }}>Payment reference</p>
            <p className="text-2xl font-bold mt-1 font-mono" style={{ color: theme.text }}>{success.payment_number}</p>
          </div>
          <div className="flex gap-3">
            <a href={`/payments/${success.id}`} className="inline-flex items-center gap-1.5 px-4 py-2 text-white rounded-lg text-sm font-medium hover:opacity-90" style={{ background: theme.ok }}><ExternalLink className="w-3.5 h-3.5" />View Payment</a>
            <button onClick={reset} className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium hover:opacity-90" style={{ background: theme.card, color: theme.g700, border: `1px solid ${theme.g200}` }}>Record Another</button>
          </div>
          {/* Expandable Journal Entry */}
          {success.journal_entry && (
            <div className="w-full mt-2">
              <button
                onClick={() => { if (!jeExpanded) { setJeExpanded(true); loadJeLines(success.journal_entry!) } else { setJeExpanded(false) } }}
                className="text-xs font-medium flex items-center gap-1 mx-auto hover:opacity-80"
                style={{ color: theme.t2 }}
              >
                <BookOpen className="w-3 h-3" />
                {jeExpanded ? 'Hide' : 'View'} Journal Entry {success.je_number && `(${success.je_number})`}
              </button>
              {jeExpanded && (
                <div className="mt-3 rounded-lg overflow-hidden text-left" style={{ border: `1px solid ${theme.ok}30` }}>
                  {jeLoading ? (
                    <div className="flex items-center justify-center py-4"><Loader2 className="w-4 h-4 animate-spin" style={{ color: theme.t3 }} /></div>
                  ) : jeLines.length > 0 ? (
                    <table className="w-full text-xs">
                      <thead>
                        <tr style={{ background: theme.g100 }}>
                          <th className="text-left px-3 py-1.5 font-semibold" style={{ color: theme.g700 }}>Account</th>
                          <th className="text-right px-3 py-1.5 font-semibold" style={{ color: theme.g700 }}>Debit</th>
                          <th className="text-right px-3 py-1.5 font-semibold" style={{ color: theme.g700 }}>Credit</th>
                        </tr>
                      </thead>
                      <tbody>
                        {jeLines.map((line, i) => (
                          <tr key={i} style={{ borderBottom: i < jeLines.length - 1 ? `1px solid ${theme.g100}` : 'none' }}>
                            <td className="px-3 py-1.5" style={{ color: theme.text }}>{line.account_code} {line.account_name}</td>
                            <td className="px-3 py-1.5 text-right font-mono" style={{ color: parseFloat(line.debit_amount) > 0 ? theme.text : theme.t3 }}>{parseFloat(line.debit_amount) > 0 ? fmt(line.debit_amount) : '-'}</td>
                            <td className="px-3 py-1.5 text-right font-mono" style={{ color: parseFloat(line.credit_amount) > 0 ? theme.text : theme.t3 }}>{parseFloat(line.credit_amount) > 0 ? fmt(line.credit_amount) : '-'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  ) : (
                    <p className="text-xs text-center py-3" style={{ color: theme.t3 }}>No journal lines found.</p>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Step 1: Select Contact */}
      <div className={cn('rounded-xl p-5', !true && 'opacity-60')} style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
        <div className="flex items-center gap-3 mb-4">
          <div className="w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0" style={{ background: theme.orange }}><span className="text-white text-xs font-bold">1</span></div>
          <h3 className="text-sm font-semibold" style={{ color: theme.text }}>Select {mode === 'customer' ? 'Customer' : 'Vendor'}</h3>
        </div>
        <ContactSearch contactType={mode} value={contact} onChange={(c) => { setContact(c); setSelectedIds(new Set()); setErrors({}) }} />
        {errors.contact && <p className="mt-1.5 text-xs flex items-center gap-1" style={{ color: theme.er }}><AlertCircle className="w-3 h-3" />{errors.contact}</p>}
      </div>

      {/* Step 2: Unpaid Invoices */}
      <div className={cn('rounded-xl p-5 transition-opacity', !contact && 'opacity-40')} style={{ background: contact ? theme.card : theme.g50, border: `1px solid ${theme.cardBdr}` }}>
        <div className="flex items-center gap-3 mb-4">
          <div className="w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0" style={{ background: contact ? theme.orange : theme.g200 }}><span className="text-white text-xs font-bold">2</span></div>
          <h3 className="text-sm font-semibold" style={{ color: theme.text }}>
            Select Invoices {selectedIds.size > 0 && <span className="ml-2 text-xs font-normal" style={{ color: theme.orange }}>({selectedIds.size} selected — {fmt(runningTotal)})</span>}
          </h3>
        </div>
        {loadingInvoices ? (
          <div className="flex items-center gap-2 py-6 text-sm" style={{ color: theme.t3 }}><Loader2 className="w-4 h-4 animate-spin" />Loading invoices...</div>
        ) : contact && invoices.length === 0 ? (
          <div className="py-6 text-center text-sm" style={{ color: theme.t3 }}>No unpaid invoices for this {mode}.</div>
        ) : invoices.length > 0 ? (
          <div className="overflow-x-auto rounded-lg" style={{ border: `1px solid ${theme.cardBdr}` }}>
            <table className="w-full text-sm">
              <thead><tr style={{ background: theme.g100, borderBottom: `2px solid ${theme.cardBdr}` }}>
                <th className="w-10 px-3 py-2"></th>
                <th className="text-left px-3 py-2 text-xs font-semibold uppercase tracking-wider" style={{ color: theme.g700 }}>Invoice #</th>
                <th className="text-left px-3 py-2 text-xs font-semibold uppercase tracking-wider" style={{ color: theme.g700 }}>Due</th>
                <th className="text-right px-3 py-2 text-xs font-semibold uppercase tracking-wider" style={{ color: theme.g700 }}>Balance</th>
              </tr></thead>
              <tbody>{invoices.map((inv, idx) => {
                const selected = selectedIds.has(inv.id)
                return (
                  <tr key={inv.id} className="cursor-pointer transition-colors" onClick={() => toggleInvoice(inv.id)}
                    style={{ background: selected ? theme.oL : theme.card, borderBottom: idx < invoices.length - 1 ? `1px solid ${theme.cardBdr}` : 'none' }}
                    onMouseEnter={(e) => { if (!selected) e.currentTarget.style.background = theme.oL }}
                    onMouseLeave={(e) => { if (!selected) e.currentTarget.style.background = theme.card }}>
                    <td className="px-3 py-2.5 text-center">{selected ? <CheckSquare className="w-4 h-4 mx-auto" style={{ color: theme.orange }} /> : <Square className="w-4 h-4 mx-auto" style={{ color: theme.g200 }} />}</td>
                    <td className="px-3 py-2.5 font-mono text-xs font-medium" style={{ color: theme.orange }}>{inv.invoice_number}</td>
                    <td className="px-3 py-2.5 text-xs" style={{ color: theme.t2 }}>{fmtDate(inv.due_date)}</td>
                    <td className="px-3 py-2.5 text-right font-semibold font-mono-nums" style={{ color: theme.text }}>{fmt(inv.balance_due, inv.currency)}</td>
                  </tr>
                )
              })}</tbody>
            </table>
          </div>
        ) : <p className="text-sm py-4" style={{ color: theme.t3 }}>Select a {mode} above to see their unpaid invoices.</p>}
      </div>

      {/* Step 3: Payment Details */}
      <div className={cn('rounded-xl p-5 transition-opacity', !contact && 'opacity-40')} style={{ background: contact ? theme.card : theme.g50, border: `1px solid ${theme.cardBdr}` }}>
        <div className="flex items-center gap-3 mb-4">
          <div className="w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0" style={{ background: contact ? theme.orange : theme.g200 }}><span className="text-white text-xs font-bold">3</span></div>
          <h3 className="text-sm font-semibold" style={{ color: theme.text }}>Payment Details</h3>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="block text-xs font-medium mb-1.5" style={{ color: theme.g700 }}>Payment Date</label>
            <div className="flex items-center gap-2">
              <input type="date" max={today()} value={paymentDate} onChange={(e) => setPaymentDate(e.target.value)} className="flex-1 px-3 py-2.5 rounded-lg text-sm focus:outline-none" style={{ background: theme.card, border: `1px solid ${theme.g200}`, color: theme.text }} />
              <button type="button" onClick={() => setPaymentDate(today())} className="text-[11px] font-medium px-2 py-1 rounded hover:opacity-80 flex-shrink-0" style={{ color: theme.orange }}>Today</button>
            </div>
            {errors.paymentDate && <p className="mt-1 text-xs flex items-center gap-1" style={{ color: theme.er }}><AlertCircle className="w-3 h-3" />{errors.paymentDate}</p>}
          </div>
          <div>
            <label className="block text-xs font-medium mb-1.5" style={{ color: theme.g700 }}>Bank Account</label>
            <select value={bankAccount} onChange={(e) => setBankAccount(e.target.value)} className="w-full px-3 py-2.5 rounded-lg text-sm focus:outline-none appearance-none" style={{ background: theme.card, border: `1px solid ${theme.g200}`, color: theme.text }}>
              <option value="">Select bank account...</option>
              {bankAccounts.map((ba) => <option key={ba.id} value={ba.id}>{ba.account_name} — {ba.bank_name}</option>)}
            </select>
            {errors.bankAccount && <p className="mt-1 text-xs flex items-center gap-1" style={{ color: theme.er }}><AlertCircle className="w-3 h-3" />{errors.bankAccount}</p>}
          </div>
          <div>
            <label className="block text-xs font-medium mb-1.5" style={{ color: theme.g700 }}>Amount (BWP)</label>
            <input type="number" min="0" step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="0.00" className="w-full px-3 py-2.5 rounded-lg text-sm font-mono focus:outline-none" style={{ background: theme.card, border: `1px solid ${theme.g200}`, color: theme.text }} />
            {errors.amount && <p className="mt-1 text-xs flex items-center gap-1" style={{ color: theme.er }}><AlertCircle className="w-3 h-3" />{errors.amount}</p>}
          </div>
          <div>
            <label className="block text-xs font-medium mb-1.5" style={{ color: theme.g700 }}>Method</label>
            <div className="flex gap-2 flex-wrap">{['EFT', 'Cheque', 'Cash', 'Card'].map((m) => (
              <button key={m} type="button" onClick={() => setPaymentMethod(m)} className="px-3 py-2 rounded-lg text-sm font-medium transition-colors"
                style={{ background: paymentMethod === m ? theme.oL : theme.card, color: paymentMethod === m ? theme.orange : theme.t2, border: `1px solid ${paymentMethod === m ? theme.orange + '40' : theme.g200}` }}>{m}</button>
            ))}</div>
          </div>
          <div className="sm:col-span-2">
            <label className="block text-xs font-medium mb-1.5" style={{ color: theme.g700 }}>Reference (optional)</label>
            <input type="text" value={reference} onChange={(e) => setReference(e.target.value)} placeholder="e.g. EFT ref #, cheque no..." className="w-full px-3 py-2.5 rounded-lg text-sm focus:outline-none" style={{ background: theme.card, border: `1px solid ${theme.g200}`, color: theme.text }} />
          </div>
        </div>
        {errors.submit && <div className="mt-4 p-3 rounded-lg flex items-start gap-2" style={{ background: theme.erB, border: `1px solid ${theme.er}30` }}><AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5" style={{ color: theme.er }} /><p className="text-sm" style={{ color: theme.er }}>{errors.submit}</p></div>}
        <div className="mt-5 flex items-center justify-between">
          {selectedIds.size > 0 && <p className="text-sm" style={{ color: theme.t2 }}>Allocating to <span className="font-medium" style={{ color: theme.orange }}>{selectedIds.size}</span> invoice(s)</p>}
          <button onClick={handleSubmit} disabled={submitting || !contact || paymentDate > today()} className="ml-auto flex items-center gap-2 px-6 py-2.5 rounded-lg font-medium text-sm hover:opacity-90"
            style={{ background: submitting || !contact ? theme.g200 : theme.orange, color: submitting || !contact ? theme.t3 : '#FFFFFF', cursor: submitting || !contact ? 'not-allowed' : 'pointer' }}>
            {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <CheckCircle2 className="w-4 h-4" />}{submitting ? 'Recording...' : 'Record Payment'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ─── Quick Expense Tab ───────────────────────────────────────────────────────

function QuickExpenseTab() {
  const { theme } = useTheme()
  const { mode: nfMode } = useNumberFormat()
  const fmt = (amount: string | number, currency = 'BWP') => formatAmount(amount, currency, nfMode)
  const [vendor, setVendor] = useState<Contact | null>(null)
  const [accounts, setAccounts] = useState<{ id: string; code: string; name: string }[]>([])
  const [account, setAccount] = useState('')
  const [description, setDescription] = useState('')
  const [amount, setAmount] = useState('')
  const [includeVAT, setIncludeVAT] = useState(false)
  const [expenseDate, setExpenseDate] = useState(today())
  const [submitting, setSubmitting] = useState(false)
  const [success, setSuccess] = useState<{ invoice_number: string; id: string } | null>(null)
  const [errors, setErrors] = useState<Record<string, string>>({})

  useEffect(() => {
    getAxios().get('/accounts/?account_type=expense&page_size=100').then((r) => { setAccounts(r.data.results || []); if (r.data.results?.length) setAccount(r.data.results[0].id) }).catch(() => {})
  }, [])

  const validate = () => {
    const e: Record<string, string> = {}
    if (!vendor) e.vendor = 'Please select a vendor'
    if (!account) e.account = 'Please select an expense account'
    if (!description.trim()) e.description = 'Please enter a description'
    if (!amount || parseFloat(amount) <= 0) e.amount = 'Please enter a valid amount'
    if (!expenseDate) e.expenseDate = 'Please enter a date'
    setErrors(e); return Object.keys(e).length === 0
  }

  const handleSubmit = async () => {
    if (!validate()) return
    setSubmitting(true)
    try {
      const api = getAxios()
      const invRes = await api.post('/invoices/', { invoice_type: 'vendor_bill', contact: vendor!.id, issue_date: expenseDate, due_date: expenseDate, currency_code: 'BWP', description, lines: [{ account, description, quantity: '1', unit_price: amount, tax_code: includeVAT ? 'VAT14' : 'EXEMPT' }] })
      await api.post(`/invoices/${invRes.data.id}/post/`)
      setSuccess({ invoice_number: invRes.data.invoice_number, id: invRes.data.id })
    } catch (err: any) { setErrors({ submit: err?.response?.data?.detail || err?.message || 'Failed to create expense' }) }
    finally { setSubmitting(false) }
  }

  const reset = () => { setVendor(null); setDescription(''); setAmount(''); setIncludeVAT(false); setSuccess(null); setErrors({}) }

  if (success) {
    return (
      <div className="py-8 flex flex-col items-center gap-6">
        <div className="w-full max-w-md rounded-xl p-8 flex flex-col items-center gap-4 text-center" style={{ background: theme.okB, border: `1px solid ${theme.ok}40` }}>
          <CheckCircle2 className="w-12 h-12" style={{ color: theme.ok }} />
          <h3 className="text-lg font-semibold" style={{ color: theme.ok }}>Expense Recorded!</h3>
          <p className="text-2xl font-bold font-mono" style={{ color: theme.text }}>{success.invoice_number}</p>
          <div className="flex gap-3">
            <a href={`/invoices/${success.id}`} className="inline-flex items-center gap-1.5 px-4 py-2 text-white rounded-lg text-sm font-medium hover:opacity-90" style={{ background: theme.ok }}><ExternalLink className="w-3.5 h-3.5" />View Bill</a>
            <button onClick={reset} className="px-4 py-2 rounded-lg text-sm font-medium hover:opacity-90" style={{ background: theme.card, color: theme.g700, border: `1px solid ${theme.g200}` }}>Record Another</button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="rounded-xl p-5 space-y-4" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="sm:col-span-2">
          <label className="block text-xs font-medium mb-1.5" style={{ color: theme.g700 }}>Vendor</label>
          <ContactSearch contactType="vendor" value={vendor} onChange={setVendor} />
          {errors.vendor && <p className="mt-1 text-xs flex items-center gap-1" style={{ color: theme.er }}><AlertCircle className="w-3 h-3" />{errors.vendor}</p>}
        </div>
        <div>
          <label className="block text-xs font-medium mb-1.5" style={{ color: theme.g700 }}>Expense Account</label>
          <SearchableSelect
            value={account}
            onChange={(v) => setAccount(v)}
            placeholder="Select account…"
            options={accounts.map((a) => ({ value: a.id, label: `${a.code} — ${a.name}` }))}
          />
          {errors.account && <p className="mt-1 text-xs flex items-center gap-1" style={{ color: theme.er }}><AlertCircle className="w-3 h-3" />{errors.account}</p>}
        </div>
        <div>
          <label className="block text-xs font-medium mb-1.5" style={{ color: theme.g700 }}>Date</label>
          <div className="flex items-center gap-2">
            <input type="date" max={today()} value={expenseDate} onChange={(e) => setExpenseDate(e.target.value)} className="flex-1 px-3 py-2.5 rounded-lg text-sm focus:outline-none" style={{ background: theme.card, border: `1px solid ${theme.g200}`, color: theme.text }} />
            <button type="button" onClick={() => setExpenseDate(today())} className="text-[11px] font-medium px-2 py-1 rounded hover:opacity-80 flex-shrink-0" style={{ color: theme.orange }}>Today</button>
          </div>
        </div>
        <div className="sm:col-span-2">
          <label className="block text-xs font-medium mb-1.5" style={{ color: theme.g700 }}>Description</label>
          <input type="text" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="e.g. Office supplies, Fuel..." className="w-full px-3 py-2.5 rounded-lg text-sm focus:outline-none" style={{ background: theme.card, border: `1px solid ${theme.g200}`, color: theme.text }} />
          {errors.description && <p className="mt-1 text-xs flex items-center gap-1" style={{ color: theme.er }}><AlertCircle className="w-3 h-3" />{errors.description}</p>}
        </div>
        <div>
          <label className="block text-xs font-medium mb-1.5" style={{ color: theme.g700 }}>Amount (BWP){includeVAT && <span style={{ color: theme.t3 }}> — excl. VAT</span>}</label>
          <input type="number" min="0" step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="0.00" className="w-full px-3 py-2.5 rounded-lg text-sm font-mono focus:outline-none" style={{ background: theme.card, border: `1px solid ${theme.g200}`, color: theme.text }} />
          {errors.amount && <p className="mt-1 text-xs flex items-center gap-1" style={{ color: theme.er }}><AlertCircle className="w-3 h-3" />{errors.amount}</p>}
        </div>
        <div className="flex flex-col justify-end">
          <label className="flex items-center gap-2.5 cursor-pointer">
            <div onClick={() => setIncludeVAT((v) => !v)} className="w-10 h-5 rounded-full transition-colors relative cursor-pointer" style={{ background: includeVAT ? theme.orange : theme.g200 }}>
              <div className={cn('absolute top-0.5 w-4 h-4 bg-white rounded-full transition-transform shadow-sm', includeVAT ? 'translate-x-5' : 'translate-x-0.5')} />
            </div>
            <span className="text-sm" style={{ color: theme.g700 }}>Include VAT (14%)</span>
          </label>
          {includeVAT && amount && parseFloat(amount) > 0 && (
            <div className="mt-2 text-xs space-y-0.5" style={{ color: theme.t3 }}>
              <p>VAT: <span style={{ color: theme.orange }}>{fmt(parseFloat(amount) * 0.14)}</span></p>
              <p>Total: <span className="font-medium" style={{ color: theme.g700 }}>{fmt(parseFloat(amount) * 1.14)}</span></p>
            </div>
          )}
        </div>
      </div>
      {errors.submit && <div className="p-3 rounded-lg flex items-start gap-2" style={{ background: theme.erB, border: `1px solid ${theme.er}30` }}><AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5" style={{ color: theme.er }} /><p className="text-sm" style={{ color: theme.er }}>{errors.submit}</p></div>}
      <div className="flex justify-end pt-2">
        {(() => {
          // OMNI-QA-009 (Lakshmi QA 2026-06-09): Record Expense was enabled
          // with vendor / description / amount all blank. Block until the
          // three mandatory fields are present and amount > 0.
          const amtNum = parseFloat(amount)
          const incomplete = !vendor || !description.trim() || !amount || Number.isNaN(amtNum) || amtNum <= 0
          const blocked = submitting || expenseDate > today() || incomplete
          return (
        <button onClick={handleSubmit} disabled={blocked} className="flex items-center gap-2 px-6 py-2.5 rounded-lg font-medium text-sm hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed"
          style={{ background: blocked ? theme.g200 : theme.orange, color: blocked ? theme.t3 : '#FFFFFF', cursor: blocked ? 'not-allowed' : 'pointer' }}>
          {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Receipt className="w-4 h-4" />}{submitting ? 'Creating...' : 'Record Expense'}
        </button>
          )
        })()}
      </div>
    </div>
  )
}

// ─── Journal Entry Tab ───────────────────────────────────────────────────────

interface JELine { account: string; debit: string; credit: string; description: string }

function JournalEntryTab() {
  const { theme } = useTheme()
  const { mode: nfMode } = useNumberFormat()
  const fmt = (amount: string | number, currency = 'BWP') => formatAmount(amount, currency, nfMode)
  // BLOCKER-001 fix 2026-05-28: stamp the topbar entity on every Smart-Entry JE
  // so the per-company sequence emits JE-{CO}-YYYY-NNNNNN and the detail
  // page (company-scoped) can find the row.
  const { selectedId: companyId } = useCompany()
  const [accounts, setAccounts] = useState<Account[]>([])
  const [entryDate, setEntryDate] = useState(today())
  const [description, setDescription] = useState('')
  const [journalType, setJournalType] = useState('general')
  const [lines, setLines] = useState<JELine[]>([
    { account: '', debit: '', credit: '', description: '' },
    { account: '', debit: '', credit: '', description: '' },
  ])
  const [submitting, setSubmitting] = useState(false)
  const [success, setSuccess] = useState<{ entry_number: string; id: string } | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    // Bug be4c1c72 (Pako): the backend caps page_size at 100 but ADIC has
    // 600+ active accounts, so the dropdown silently held only the first
    // 100 by code and search showed "No match" for everything later (e.g.
    // Staff Welfare). Walk ALL pages so the client-side search sees the
    // full chart of accounts.
    let cancelled = false
    async function loadAll() {
      const all: Account[] = []
      for (let page = 1; page < 30; page++) {
        try {
          const r = await getAccounts({ page_size: '100', ordering: 'code', page: String(page) })
          all.push(...r.results)
          if (!r.next) break
        } catch { break }
      }
      if (!cancelled) setAccounts(all)
    }
    loadAll()
    return () => { cancelled = true }
  }, [])

  const updateLine = (idx: number, field: keyof JELine, val: string) => {
    setLines((prev) => prev.map((l, i) => i === idx ? { ...l, [field]: val } : l))
  }
  const addLine = () => setLines((prev) => [...prev, { account: '', debit: '', credit: '', description: '' }])
  const removeLine = (idx: number) => { if (lines.length > 2) setLines((prev) => prev.filter((_, i) => i !== idx)) }

  const totalDebit = lines.reduce((s, l) => s + (parseFloat(l.debit) || 0), 0)
  const totalCredit = lines.reduce((s, l) => s + (parseFloat(l.credit) || 0), 0)
  const balanced = Math.abs(totalDebit - totalCredit) < 0.005 && totalDebit > 0

  // BUG-003 (Oprah retest 2026-05-29): stale validation banner cleared once
  // the form transitions back to a valid state. Without this the banner sits
  // until the next submit attempt, even after the capturer fixed the field.
  useEffect(() => {
    if (!error) return
    const validLineCount = lines.filter((l) => l.account && (parseFloat(l.debit) > 0 || parseFloat(l.credit) > 0)).length
    const ok = description.trim() && balanced && validLineCount >= 2
    if (ok) setError('')
  }, [description, balanced, lines, error])

  const handleSubmit = async () => {
    if (!description.trim()) { setError('Please enter a description'); return }
    if (!balanced) { setError('Debits and credits must be equal and greater than zero'); return }
    const validLines = lines.filter((l) => l.account && (parseFloat(l.debit) > 0 || parseFloat(l.credit) > 0))
    if (validLines.length < 2) { setError('At least 2 valid lines required'); return }
    setSubmitting(true); setError('')
    try {
      const result = await createJournalEntry({
        entry_date: entryDate, description, journal_type: journalType, currency_code: 'BWP',
        company: companyId || undefined,
        lines: validLines.map((l) => ({
          account: l.account,
          debit_amount: l.debit || '0', credit_amount: l.credit || '0', description: l.description,
        })),
      })
      setSuccess({ entry_number: result.entry_number, id: result.id })
    } catch (err: any) { setError(err?.message || 'Failed to create journal entry') }
    finally { setSubmitting(false) }
  }

  if (success) {
    return (
      <div className="py-8 flex flex-col items-center gap-6">
        <div className="w-full max-w-md rounded-xl p-8 flex flex-col items-center gap-4 text-center" style={{ background: theme.okB, border: `1px solid ${theme.ok}40` }}>
          <CheckCircle2 className="w-12 h-12" style={{ color: theme.ok }} />
          <h3 className="text-lg font-semibold" style={{ color: theme.ok }}>Journal Entry Created!</h3>
          <p className="text-2xl font-bold font-mono" style={{ color: theme.text }}>{success.entry_number}</p>
          <div className="flex gap-3">
            <a href={`/journal-entries/${success.id}`} className="inline-flex items-center gap-1.5 px-4 py-2 text-white rounded-lg text-sm font-medium hover:opacity-90" style={{ background: theme.ok }}><ExternalLink className="w-3.5 h-3.5" />View Entry</a>
            <button onClick={() => { setSuccess(null); setLines([{ account: '', debit: '', credit: '', description: '' }, { account: '', debit: '', credit: '', description: '' }]); setDescription('') }}
              className="px-4 py-2 rounded-lg text-sm font-medium hover:opacity-90" style={{ background: theme.card, color: theme.g700, border: `1px solid ${theme.g200}` }}>Create Another</button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="rounded-xl p-5 space-y-4" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div>
          <label className="block text-xs font-medium mb-1.5" style={{ color: theme.g700 }}>Date</label>
          <div className="flex items-center gap-2">
            <input type="date" max={today()} value={entryDate} onChange={(e) => setEntryDate(e.target.value)} className="flex-1 px-3 py-2.5 rounded-lg text-sm focus:outline-none" style={{ background: theme.card, border: `1px solid ${theme.g200}`, color: theme.text }} />
            <button type="button" onClick={() => setEntryDate(today())} className="text-[11px] font-medium px-2 py-1 rounded hover:opacity-80 flex-shrink-0" style={{ color: theme.orange }}>Today</button>
          </div>
        </div>
        <div>
          <label className="block text-xs font-medium mb-1.5" style={{ color: theme.g700 }}>Type</label>
          <select value={journalType} onChange={(e) => setJournalType(e.target.value)} className="w-full px-3 py-2.5 rounded-lg text-sm focus:outline-none appearance-none" style={{ background: theme.card, border: `1px solid ${theme.g200}`, color: theme.text }}>
            {['general', 'sales', 'purchases', 'cash_receipts', 'cash_payments', 'bank'].map((t) => (
              <option key={t} value={t}>{t.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-xs font-medium mb-1.5" style={{ color: theme.g700 }}>Description</label>
          <input type="text" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Journal entry description..." className="w-full px-3 py-2.5 rounded-lg text-sm focus:outline-none" style={{ background: theme.card, border: `1px solid ${theme.g200}`, color: theme.text }} />
        </div>
      </div>

      {/* Lines table */}
      <div className="overflow-x-auto rounded-lg" style={{ border: `1px solid ${theme.cardBdr}` }}>
        <table className="w-full text-sm">
          <thead><tr style={{ background: theme.g100 }}>
            <th className="text-left px-3 py-2 text-xs font-semibold uppercase" style={{ color: theme.g700 }}>Account</th>
            <th className="text-left px-3 py-2 text-xs font-semibold uppercase" style={{ color: theme.g700 }}>Description</th>
            <th className="text-right px-3 py-2 text-xs font-semibold uppercase" style={{ color: theme.g700 }}>Debit</th>
            <th className="text-right px-3 py-2 text-xs font-semibold uppercase" style={{ color: theme.g700 }}>Credit</th>
            <th className="w-10"></th>
          </tr></thead>
          <tbody>
            {lines.map((line, idx) => (
              <tr key={idx} style={{ borderBottom: `1px solid ${theme.cardBdr}` }}>
                <td className="px-2 py-1.5">
                  <SearchableSelect
                    dense
                    minWidth={180}
                    value={line.account}
                    onChange={(v) => updateLine(idx, 'account', v)}
                    placeholder="Select…"
                    options={accounts.map((a) => ({ value: a.code, label: `${a.code} — ${a.name}` }))}
                  />
                </td>
                <td className="px-2 py-1.5"><input type="text" value={line.description} onChange={(e) => updateLine(idx, 'description', e.target.value)} placeholder="Line description" className="w-full px-2 py-1.5 rounded text-xs focus:outline-none" style={{ background: theme.card, border: `1px solid ${theme.g200}`, color: theme.text }} /></td>
                <td className="px-2 py-1.5"><input type="number" min="0" step="0.01" value={line.debit} onChange={(e) => updateLine(idx, 'debit', e.target.value)} placeholder="0.00" className="w-24 px-2 py-1.5 rounded text-xs text-right font-mono focus:outline-none" style={{ background: theme.card, border: `1px solid ${theme.g200}`, color: theme.text }} /></td>
                <td className="px-2 py-1.5"><input type="number" min="0" step="0.01" value={line.credit} onChange={(e) => updateLine(idx, 'credit', e.target.value)} placeholder="0.00" className="w-24 px-2 py-1.5 rounded text-xs text-right font-mono focus:outline-none" style={{ background: theme.card, border: `1px solid ${theme.g200}`, color: theme.text }} /></td>
                <td className="px-2 py-1.5 text-center">
                  {lines.length > 2 && <button onClick={() => removeLine(idx)} className="p-1 rounded hover:opacity-70" style={{ color: theme.t3 }}><Trash2 className="w-3.5 h-3.5" /></button>}
                </td>
              </tr>
            ))}
            {/* Totals row */}
            <tr style={{ background: theme.g50 }}>
              <td colSpan={2} className="px-3 py-2 text-right text-xs font-semibold" style={{ color: theme.g700 }}>
                Totals
                <span className="ml-3 px-2 py-0.5 rounded text-[10px] font-bold" style={{ background: balanced ? theme.okB : theme.erB, color: balanced ? theme.ok : theme.er }}>{balanced ? 'BALANCED' : 'UNBALANCED'}</span>
              </td>
              <td className="px-3 py-2 text-right font-mono text-xs font-bold" style={{ color: theme.text }}>{fmt(totalDebit)}</td>
              <td className="px-3 py-2 text-right font-mono text-xs font-bold" style={{ color: theme.text }}>{fmt(totalCredit)}</td>
              <td></td>
            </tr>
          </tbody>
        </table>
      </div>

      <button onClick={addLine} className="flex items-center gap-1.5 text-xs font-medium hover:opacity-80" style={{ color: theme.orange }}><Plus className="w-3.5 h-3.5" />Add Line</button>

      {error && <div className="p-3 rounded-lg flex items-start gap-2" style={{ background: theme.erB, border: `1px solid ${theme.er}30` }}><AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5" style={{ color: theme.er }} /><p className="text-sm" style={{ color: theme.er }}>{error}</p></div>}

      <div className="flex justify-end pt-2">
        <button onClick={handleSubmit} disabled={submitting || !balanced || entryDate > today()} className="flex items-center gap-2 px-6 py-2.5 rounded-lg font-medium text-sm hover:opacity-90"
          style={{ background: submitting || !balanced ? theme.g200 : theme.orange, color: submitting || !balanced ? theme.t3 : '#FFFFFF', cursor: submitting || !balanced ? 'not-allowed' : 'pointer' }}>
          {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <BookOpen className="w-4 h-4" />}{submitting ? 'Creating...' : 'Create Journal Entry'}
        </button>
      </div>
    </div>
  )
}

// ─── Upload Zone ─────────────────────────────────────────────────────────────

function UploadZone({ onUpload, uploading }: { onUpload: (file: File) => void; uploading: boolean }) {
  const { theme } = useTheme()
  const [dragOver, setDragOver] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const handleFile = (file: File) => {
    if (file.size > MAX_FILE_SIZE) { alert('File too large. Maximum 10MB.'); return }
    onUpload(file)
  }

  const onDrop = (e: DragEvent<HTMLDivElement>) => { e.preventDefault(); setDragOver(false); const f = e.dataTransfer.files[0]; if (f) handleFile(f) }
  const onDragOver = (e: DragEvent<HTMLDivElement>) => { e.preventDefault(); setDragOver(true) }
  const onDragLeave = () => setDragOver(false)
  const onFileChange = (e: React.ChangeEvent<HTMLInputElement>) => { const f = e.target.files?.[0]; if (f) handleFile(f); if (e.target) e.target.value = '' }

  useEffect(() => {
    const handlePaste = (e: globalThis.ClipboardEvent) => {
      const items = e.clipboardData?.items
      if (!items) return
      for (const item of Array.from(items)) {
        if (item.kind === 'file') { const f = item.getAsFile(); if (f) { handleFile(f); break } }
      }
    }
    document.addEventListener('paste', handlePaste)
    return () => document.removeEventListener('paste', handlePaste)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div
      onDrop={onDrop} onDragOver={onDragOver} onDragLeave={onDragLeave}
      onClick={() => !uploading && fileInputRef.current?.click()}
      className="rounded-xl p-8 flex flex-col items-center gap-3 cursor-pointer transition-all"
      style={{
        border: `2px dashed ${dragOver ? theme.orange : theme.cardBdr}`,
        background: dragOver ? theme.oL : theme.card,
        transform: dragOver ? 'scale(1.01)' : 'scale(1)',
      }}
    >
      <input ref={fileInputRef} type="file" accept={ACCEPTED_TYPES} onChange={onFileChange} className="hidden" />
      {uploading ? (
        <>
          <Loader2 className="w-10 h-10 animate-spin" style={{ color: theme.orange }} />
          <p className="text-sm font-medium" style={{ color: theme.text }}>Uploading & analyzing...</p>
        </>
      ) : (
        <>
          <div className="w-14 h-14 rounded-full flex items-center justify-center" style={{ background: theme.oL }}>
            <FileUp className="w-7 h-7" style={{ color: theme.orange }} />
          </div>
          <div className="text-center">
            <p className="text-sm font-medium" style={{ color: theme.text }}>
              Drop a file here, <span style={{ color: theme.orange }}>browse</span>, or paste (Ctrl+V)
            </p>
            <p className="text-xs mt-1" style={{ color: theme.t3 }}>PDF, PNG, JPG, CSV, XLSX — max 10MB</p>
          </div>
        </>
      )}
    </div>
  )
}

// ─── Document Status Badge ───────────────────────────────────────────────────

function DocStatusBadge({ status, theme: t }: { status: string; theme: typeof import('@/lib/themes').themes.light }) {
  const map: Record<string, { bg: string; fg: string; label: string }> = {
    processing: { bg: t.wrB, fg: t.wr, label: 'Processing' },
    classified: { bg: t.inB, fg: t.inf, label: 'Classified' },
    extracted: { bg: t.inB, fg: t.inf, label: 'Ready' },
    suggested: { bg: t.oL, fg: t.orange, label: 'Ready' },
    confirmed: { bg: t.okB, fg: t.ok, label: 'Confirmed' },
    rejected: { bg: t.g100, fg: t.t3, label: 'Rejected' },
    error: { bg: t.erB, fg: t.er, label: 'Error' },
  }
  const m = map[status] || { bg: t.g100, fg: t.t3, label: status }
  return <span className="px-2 py-0.5 rounded text-[10px] font-semibold uppercase" style={{ background: m.bg, color: m.fg }}>{m.label}</span>
}

function DocTypeBadge({ type, theme: t }: { type: string | null; theme: typeof import('@/lib/themes').themes.light }) {
  if (!type || type === 'unknown') return null
  const label = type.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
  return <span className="px-2 py-0.5 rounded text-[10px] font-medium" style={{ background: t.inB, color: t.inf }}>{label}</span>
}

// ─── Document Review Panel ───────────────────────────────────────────────────

// Action-type options the user can pick if AI got the direction wrong.
// `direction` is just a UX hint — 'in' = money received, 'out' = money paid.
const ACTION_OPTIONS: { value: string; label: string; direction: 'in' | 'out' | 'neutral' }[] = [
  { value: 'create_vendor_bill',       label: 'Vendor Bill (money OUT)',       direction: 'out' },
  { value: 'create_customer_invoice',  label: 'Customer Invoice (money IN)',   direction: 'in' },
  { value: 'record_payment_received',  label: 'Payment Received (money IN)',   direction: 'in' },
  { value: 'record_payment_made',      label: 'Payment Made (money OUT)',      direction: 'out' },
  { value: 'import_bank_statement',    label: 'Bank Statement',                direction: 'neutral' },
  { value: 'create_credit_note',       label: 'Credit Note',                   direction: 'neutral' },
  { value: 'manual_review',            label: 'Manual Review (no action)',     direction: 'neutral' },
]

function DocumentReview({ doc, onRefresh }: { doc: DocumentUploadRecord; onRefresh: () => void }) {
  const { theme } = useTheme()
  const { mode: nfMode } = useNumberFormat()
  const fmt = (amount: string | number, currency = 'BWP') => formatAmount(amount, currency, nfMode)
  const [confirming, setConfirming] = useState(false)
  const [rejecting, setRejecting] = useState(false)

  const data = (doc.extracted_data || {}) as Record<string, unknown>
  const suggestion = doc.suggested_action
  const confidence = doc.classification_confidence ? parseFloat(doc.classification_confidence) : 0

  // Editable local state — seeded from the AI's extraction. User can change
  // anything before confirming; only changed fields are sent.
  const initialAction = suggestion?.action_type || 'manual_review'
  const initialVendor = (data.vendor_name || data.possible_vendor || '') as string
  const initialAmount = data.total_amount != null ? String(data.total_amount) : ''
  const initialDate = data.document_date ? String(data.document_date) : ''
  const initialRef = Array.isArray(data.references) && data.references.length > 0
    ? String(data.references[0]) : ''
  const initialCurrency = (data.currency as string) || 'BWP'

  const [actionType, setActionType] = useState(initialAction)
  const [vendor, setVendor]         = useState(initialVendor)
  const [currency, setCurrency]     = useState(initialCurrency)
  const [amount, setAmount]         = useState(initialAmount)
  const [docDate, setDocDate]       = useState(initialDate)
  const [reference, setReference]   = useState(initialRef)

  // Currency list — fetched once per session
  const [currencies, setCurrencies] = useState<Currency[]>([])
  useEffect(() => {
    if (currencies.length > 0) return
    getCurrencies().then((res) => setCurrencies(res.results)).catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Reset local state when a different document is loaded
  useEffect(() => {
    setActionType(suggestion?.action_type || 'manual_review')
    setVendor((data.vendor_name || data.possible_vendor || '') as string)
    setCurrency((data.currency as string) || 'BWP')
    setAmount(data.total_amount != null ? String(data.total_amount) : '')
    setDocDate(data.document_date ? String(data.document_date) : '')
    setReference(Array.isArray(data.references) && data.references.length > 0
      ? String(data.references[0]) : '')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doc.id])

  const selectedOption = ACTION_OPTIONS.find((o) => o.value === actionType) || ACTION_OPTIONS[6]

  const handleConfirm = async () => {
    setConfirming(true)
    try {
      const editedData: Record<string, unknown> = {}
      if (vendor !== initialVendor) editedData.vendor_name = vendor
      const amountNum = amount === '' ? null : Number(amount)
      if (amount !== initialAmount && (amountNum === null || !Number.isNaN(amountNum))) {
        editedData.total_amount = amountNum
      }
      if (docDate !== initialDate) editedData.document_date = docDate || null
      if (reference !== initialRef) editedData.reference_number = reference || null
      if (currency !== initialCurrency) editedData.currency = currency

      await confirmDocument(doc.id, {
        edited_data: Object.keys(editedData).length ? editedData : undefined,
        edited_action_type: actionType !== initialAction ? actionType : undefined,
      })
      onRefresh()
    }
    catch (e) { alert((e as Error).message || 'Failed to confirm') }
    finally { setConfirming(false) }
  }

  const handleReject = async () => {
    setRejecting(true)
    try { await rejectDocument(doc.id); onRefresh() }
    catch { /* error handled by onRefresh */ }
    finally { setRejecting(false) }
  }

  const inputStyle: React.CSSProperties = {
    background: theme.card,
    border: `1px solid ${theme.cardBdr}`,
    color: theme.text,
  }

  // Processing state
  if (doc.status === 'processing' || doc.status === 'uploading') {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-4 py-16">
        <Loader2 className="w-10 h-10 animate-spin" style={{ color: theme.orange }} />
        <p className="text-sm font-medium" style={{ color: theme.text }}>Analyzing document...</p>
        <p className="text-xs" style={{ color: theme.t3 }}>Extracting text and classifying</p>
      </div>
    )
  }

  // Confirmed state
  if (doc.status === 'confirmed') {
    const kind = doc.resulting_object_kind
    const linkPath = kind === 'invoice'
      ? `/invoices/${doc.resulting_object_id}`
      : kind === 'payment'
        ? `/payments/${doc.resulting_object_id}`
        : null
    const recordLabel = kind === 'invoice' ? 'invoice'
      : kind === 'payment' ? 'payment'
      : 'record'
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-4 py-16">
        <div className="w-16 h-16 rounded-full flex items-center justify-center" style={{ background: theme.okB }}>
          <CheckCircle2 className="w-8 h-8" style={{ color: theme.ok }} />
        </div>
        <h3 className="text-lg font-semibold" style={{ color: theme.ok }}>Confirmed</h3>
        <p className="text-sm text-center max-w-md" style={{ color: theme.t2 }}>{doc.ai_explanation}</p>
        {linkPath ? (
          <div className="flex flex-col items-center gap-1.5">
            <p className="text-xs" style={{ color: theme.t3 }}>
              A draft {recordLabel} has been created — review and post it:
            </p>
            <a href={linkPath} className="text-sm font-medium inline-flex items-center gap-1 hover:opacity-80" style={{ color: theme.orange }}>
              <ExternalLink className="w-3.5 h-3.5" />Open draft {recordLabel}
            </a>
          </div>
        ) : doc.resulting_object_id ? (
          <a href={`/invoices/${doc.resulting_object_id}`} className="text-xs font-medium hover:opacity-80" style={{ color: theme.orange }}>View created record</a>
        ) : (
          <p className="text-xs italic" style={{ color: theme.t3 }}>
            No automatic record created (manual_review or bank statement). Use the relevant page to create one manually.
          </p>
        )}
      </div>
    )
  }

  // Rejected state
  if (doc.status === 'rejected') {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-4 py-16">
        <X className="w-12 h-12" style={{ color: theme.t3 }} />
        <h3 className="text-lg font-semibold" style={{ color: theme.t2 }}>Rejected</h3>
        <p className="text-sm" style={{ color: theme.t3 }}>This document suggestion was rejected.</p>
      </div>
    )
  }

  // Error state
  if (doc.status === 'error') {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-4 py-16">
        <AlertCircle className="w-12 h-12" style={{ color: theme.er }} />
        <h3 className="text-lg font-semibold" style={{ color: theme.er }}>Processing Error</h3>
        <p className="text-sm text-center max-w-md" style={{ color: theme.t2 }}>{doc.error_message || 'An error occurred while processing this document.'}</p>
      </div>
    )
  }

  // Review state (extracted / suggested) — editable
  const directionBadge = selectedOption.direction === 'in'
    ? { bg: theme.okB, fg: theme.ok, label: 'Money IN', icon: ArrowDownLeft }
    : selectedOption.direction === 'out'
      ? { bg: theme.erB, fg: theme.er, label: 'Money OUT', icon: ArrowUpRight }
      : { bg: theme.g100, fg: theme.t2, label: 'Neutral', icon: FileText }
  const DirIcon = directionBadge.icon
  const actionChanged = actionType !== initialAction

  return (
    <div className="space-y-5">
      {/* Summary */}
      <div className="rounded-lg p-4" style={{ background: theme.g50, border: `1px solid ${theme.cardBdr}` }}>
        <div className="flex items-center gap-2 flex-wrap mb-2">
          <DocTypeBadge type={doc.document_type} theme={theme} />
          {confidence > 0 && (
            <span className="text-[10px] font-medium px-2 py-0.5 rounded" style={{
              background: confidence >= 0.85 ? theme.okB : confidence >= 0.6 ? theme.wrB : theme.erB,
              color: confidence >= 0.85 ? theme.ok : confidence >= 0.6 ? theme.wr : theme.er,
            }}>
              {(confidence * 100).toFixed(0)}% confidence
            </span>
          )}
          <span className="text-[10px] px-2 py-0.5 rounded" style={{ background: theme.g100, color: theme.t3 }}>{doc.extraction_method.replace(/_/g, ' ')}</span>
        </div>
        {doc.ai_explanation && (
          <p className="text-sm leading-relaxed" style={{ color: theme.text }}>{doc.ai_explanation}</p>
        )}
        <p className="text-xs mt-2" style={{ color: theme.t3 }}>{doc.original_filename} — {formatFileSize(doc.file_size)} — {timeAgo(doc.created_at)}</p>
      </div>

      {/* Direction / action override */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <h4 className="text-xs font-semibold uppercase tracking-wider" style={{ color: theme.t2 }}>What is this?</h4>
          <span className="text-[10px] font-medium px-2 py-0.5 rounded inline-flex items-center gap-1" style={{ background: directionBadge.bg, color: directionBadge.fg }}>
            <DirIcon className="w-3 h-3" />{directionBadge.label}
          </span>
        </div>
        <select
          value={actionType}
          onChange={(e) => setActionType(e.target.value)}
          className="w-full text-sm rounded-md px-3 py-2 outline-none focus:ring-2"
          style={{
            ...inputStyle,
            borderColor: actionChanged ? theme.orange : theme.cardBdr,
          }}
        >
          {ACTION_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
        {actionChanged && (
          <p className="text-[11px] mt-1.5 flex items-center gap-1" style={{ color: theme.orange }}>
            <PenLine className="w-3 h-3" />AI suggested
            &nbsp;<span className="font-medium">{(initialAction || 'manual_review').replace(/_/g, ' ')}</span>
            &nbsp;— change saves on Confirm
          </p>
        )}
      </div>

      {/* Editable extracted fields */}
      <div>
        <h4 className="text-xs font-semibold uppercase tracking-wider mb-3" style={{ color: theme.t2 }}>Details</h4>
        <div className="grid grid-cols-2 gap-3">
          <div className="col-span-2">
            <label className="block text-[10px] font-medium uppercase tracking-wider mb-1" style={{ color: theme.t3 }}>
              {selectedOption.direction === 'in' ? 'Customer' : 'Vendor / Counterparty'}
            </label>
            <input
              type="text"
              value={vendor}
              onChange={(e) => setVendor(e.target.value)}
              placeholder="Acme Ltd"
              className="w-full text-sm rounded-md px-3 py-2 outline-none focus:ring-2"
              style={{ ...inputStyle, borderColor: vendor !== initialVendor ? theme.orange : theme.cardBdr }}
            />
          </div>
          <div>
            <label className="block text-[10px] font-medium uppercase tracking-wider mb-1" style={{ color: theme.t3 }}>Currency</label>
            <select
              value={currency}
              onChange={(e) => setCurrency(e.target.value)}
              className="w-full text-sm rounded-md px-3 py-2 outline-none focus:ring-2"
              style={{ ...inputStyle, borderColor: currency !== initialCurrency ? theme.orange : theme.cardBdr }}
            >
              {(currencies.length > 0 ? currencies : [{ code: 'BWP', name: 'Botswana Pula', symbol: 'P', decimal_places: 2, is_active: true }]).map((c) => (
                <option key={c.code} value={c.code}>
                  {c.code}{c.symbol ? ` (${c.symbol})` : ''}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-[10px] font-medium uppercase tracking-wider mb-1" style={{ color: theme.t3 }}>Amount ({currency})</label>
            <input
              type="number"
              step="0.01"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              placeholder="0.00"
              className="w-full text-sm font-mono rounded-md px-3 py-2 outline-none focus:ring-2"
              style={{ ...inputStyle, borderColor: amount !== initialAmount ? theme.orange : theme.cardBdr }}
            />
          </div>
          <div>
            <label className="block text-[10px] font-medium uppercase tracking-wider mb-1" style={{ color: theme.t3 }}>Date</label>
            <input
              type="date"
              value={docDate}
              onChange={(e) => setDocDate(e.target.value)}
              className="w-full text-sm rounded-md px-3 py-2 outline-none focus:ring-2"
              style={{ ...inputStyle, borderColor: docDate !== initialDate ? theme.orange : theme.cardBdr }}
            />
          </div>
          <div className="col-span-2">
            <label className="block text-[10px] font-medium uppercase tracking-wider mb-1" style={{ color: theme.t3 }}>Reference</label>
            <input
              type="text"
              value={reference}
              onChange={(e) => setReference(e.target.value)}
              placeholder="INV-12345"
              className="w-full text-sm font-mono rounded-md px-3 py-2 outline-none focus:ring-2"
              style={{ ...inputStyle, borderColor: reference !== initialRef ? theme.orange : theme.cardBdr }}
            />
          </div>
        </div>
        {amount !== '' && Number.isNaN(Number(amount)) && (
          <p className="text-[11px] mt-1.5" style={{ color: theme.er }}>Amount must be a number.</p>
        )}
      </div>

      {/* Live preview of what Confirm will save */}
      <div className="rounded-lg p-3" style={{ background: theme.oL, border: `1px solid ${theme.orange}30` }}>
        <div className="flex items-center gap-2">
          <Sparkles className="w-4 h-4" style={{ color: theme.orange }} />
          <span className="text-sm font-semibold" style={{ color: theme.orange }}>
            {selectedOption.label}
          </span>
        </div>
        <div className="mt-2 text-xs space-y-0.5" style={{ color: theme.t2 }}>
          {amount !== '' && !Number.isNaN(Number(amount)) ? <p>Amount: <span className="font-mono font-medium" style={{ color: theme.text }}>{fmt(Number(amount), currency)}</span></p> : null}
          {vendor ? <p>{selectedOption.direction === 'in' ? 'Customer' : 'Vendor'}: <span className="font-medium" style={{ color: theme.text }}>{vendor}</span></p> : null}
          {docDate ? <p>Date: <span className="font-medium" style={{ color: theme.text }}>{fmtDate(docDate)}</span></p> : null}
          {reference ? <p>Reference: <span className="font-mono" style={{ color: theme.text }}>{reference}</span></p> : null}
        </div>
      </div>

      {/* Action Buttons */}
      <div className="flex items-center gap-3 pt-2">
        <button
          onClick={handleConfirm}
          disabled={confirming || (amount !== '' && Number.isNaN(Number(amount)))}
          className="flex-1 flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium text-white hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed"
          style={{ background: theme.ok }}
        >
          {confirming ? <Loader2 className="w-4 h-4 animate-spin" /> : <CheckCircle2 className="w-4 h-4" />}Confirm &amp; Save
        </button>
        <button onClick={handleReject} disabled={rejecting} className="flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed"
          style={{ background: theme.card, color: theme.t2, border: `1px solid ${theme.g200}` }}>
          {rejecting ? <Loader2 className="w-4 h-4 animate-spin" /> : <X className="w-4 h-4" />}Reject
        </button>
      </div>
    </div>
  )
}

// ─── Upload Document Mode ────────────────────────────────────────────────────

function UploadDocumentMode() {
  const { theme } = useTheme()
  const [docs, setDocs] = useState<DocumentUploadRecord[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [selectedDoc, setSelectedDoc] = useState<DocumentUploadRecord | null>(null)
  const [uploading, setUploading] = useState(false)
  const [loadingDocs, setLoadingDocs] = useState(true)

  const loadDocs = useCallback(async () => {
    try {
      const res = await getDocuments({ page_size: '20' })
      setDocs(res.results)
    } catch { /* ignore */ }
    finally { setLoadingDocs(false) }
  }, [])

  useEffect(() => { loadDocs() }, [loadDocs])

  // Load detail when selected
  useEffect(() => {
    if (!selectedId) { setSelectedDoc(null); return }
    let cancelled = false
    const load = async () => {
      try {
        const d = await getDocument(selectedId)
        if (!cancelled) setSelectedDoc(d)
      } catch { /* ignore */ }
    }
    load()
    return () => { cancelled = true }
  }, [selectedId])

  const handleUpload = async (file: File) => {
    setUploading(true)
    try {
      const result = await uploadDocument(file)
      // If it was a duplicate, the API returns differently
      const doc = (result as any).document || result
      setSelectedId(doc.id)
      // Refresh list
      await loadDocs()
    } catch { /* ignore */ }
    finally { setUploading(false) }
  }

  const refreshSelected = async () => {
    if (selectedId) {
      try {
        const d = await getDocument(selectedId)
        setSelectedDoc(d)
        await loadDocs()
      } catch { /* ignore */ }
    }
  }

  const [deletingId, setDeletingId] = useState<string | null>(null)
  const handleDelete = async (doc: DocumentUploadRecord) => {
    if (!confirm(`Delete "${doc.original_filename}"? This cannot be undone.`)) return
    setDeletingId(doc.id)
    try {
      await deleteDocument(doc.id)
      if (selectedId === doc.id) {
        setSelectedId(null)
        setSelectedDoc(null)
      }
      await loadDocs()
    } catch (e) {
      alert((e as Error).message || 'Failed to delete document')
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <div className="flex flex-col lg:flex-row gap-6 min-h-0">
      {/* Left Panel — upload + recent uploads */}
      <div className="w-full lg:w-[40%] flex flex-col gap-4 flex-shrink-0">
        <UploadZone onUpload={handleUpload} uploading={uploading} />

        {/* Recent Uploads */}
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wider mb-2" style={{ color: theme.t2 }}>Recent Uploads</h3>
          <div className="space-y-1.5 max-h-[400px] overflow-y-auto">
            {loadingDocs ? (
              <div className="flex items-center gap-2 py-4 text-sm" style={{ color: theme.t3 }}><Loader2 className="w-4 h-4 animate-spin" />Loading...</div>
            ) : docs.length === 0 ? (
              <p className="text-sm py-4" style={{ color: theme.t3 }}>No documents uploaded yet.</p>
            ) : docs.map((d) => (
              <div
                key={d.id}
                onClick={() => setSelectedId(d.id)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setSelectedId(d.id) } }}
                className="group w-full flex items-center gap-3 p-3 rounded-lg text-left transition-all cursor-pointer"
                style={{
                  background: selectedId === d.id ? theme.oL : theme.card,
                  border: `1px solid ${selectedId === d.id ? theme.orange + '40' : theme.cardBdr}`,
                }}
                onMouseEnter={(e) => { if (selectedId !== d.id) e.currentTarget.style.background = theme.g50 }}
                onMouseLeave={(e) => { if (selectedId !== d.id) e.currentTarget.style.background = theme.card }}
              >
                <div className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0" style={{ background: theme.g100 }}>
                  <FileText className="w-4 h-4" style={{ color: theme.t2 }} />
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-medium truncate" style={{ color: theme.text }}>{d.original_filename}</p>
                  <div className="flex items-center gap-1.5 mt-0.5">
                    <DocStatusBadge status={d.status} theme={theme} />
                    <DocTypeBadge type={d.document_type} theme={theme} />
                  </div>
                </div>
                <span className="text-[10px] flex-shrink-0" style={{ color: theme.t3 }}>{timeAgo(d.created_at)}</span>
                <button
                  type="button"
                  aria-label={`Delete ${d.original_filename}`}
                  onClick={(e) => { e.stopPropagation(); handleDelete(d) }}
                  disabled={deletingId === d.id}
                  className="p-1.5 rounded opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed"
                  style={{ color: theme.t3 }}
                  onMouseEnter={(e) => { e.currentTarget.style.color = theme.er; e.currentTarget.style.background = theme.erB }}
                  onMouseLeave={(e) => { e.currentTarget.style.color = theme.t3; e.currentTarget.style.background = 'transparent' }}
                >
                  {deletingId === d.id ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
                </button>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Right Panel — AI review of the selected document */}
      <div className="flex-1 min-w-0 rounded-xl p-5" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}`, boxShadow: theme.cardSh }}>
        {!selectedDoc ? (
          <div className="h-full flex flex-col items-center justify-center gap-4 py-16" style={{ minHeight: 320 }}>
            <div className="w-14 h-14 rounded-full flex items-center justify-center" style={{ background: theme.oL }}>
              <Sparkles className="w-7 h-7" style={{ color: theme.orange }} />
            </div>
            <div className="text-center max-w-sm space-y-1">
              <h3 className="text-sm font-semibold" style={{ color: theme.text }}>AI Review</h3>
              <p className="text-xs" style={{ color: theme.t2 }}>
                Upload a file or pick one from <span className="font-medium">Recent Uploads</span> to see what was extracted.
              </p>
            </div>
            <div className="grid grid-cols-2 gap-2 w-full max-w-sm mt-2">
              {[
                { label: 'Document type', hint: 'Invoice, receipt, statement…' },
                { label: 'Vendor / customer', hint: 'Detected from text' },
                { label: 'Amount & date', hint: 'Extracted automatically' },
                { label: 'Suggested action', hint: 'Confirm or reject' },
              ].map((f) => (
                <div key={f.label} className="rounded-md p-2.5" style={{ background: theme.g50, border: `1px dashed ${theme.cardBdr}` }}>
                  <p className="text-[10px] font-medium uppercase tracking-wider" style={{ color: theme.t2 }}>{f.label}</p>
                  <p className="text-[10px] mt-0.5" style={{ color: theme.t3 }}>{f.hint}</p>
                </div>
              ))}
            </div>
          </div>
        ) : (
          <DocumentReview doc={selectedDoc} onRefresh={refreshSelected} />
        )}
      </div>
    </div>
  )
}

// ─── Manual Entry Mode ───────────────────────────────────────────────────────

type ManualTab = 'customer' | 'vendor' | 'expense' | 'journal'

const MANUAL_TABS: { key: ManualTab; label: string; icon: React.ReactNode }[] = [
  { key: 'customer', label: 'Customer Payment', icon: <ArrowDownLeft className="w-4 h-4" /> },
  { key: 'vendor', label: 'Vendor Payment', icon: <ArrowUpRight className="w-4 h-4" /> },
  { key: 'expense', label: 'Quick Expense', icon: <Receipt className="w-4 h-4" /> },
  { key: 'journal', label: 'Journal Entry', icon: <BookOpen className="w-4 h-4" /> },
]

function ManualEntryMode() {
  const { theme } = useTheme()
  const [activeTab, setActiveTab] = useState<ManualTab>('customer')

  return (
    <div className="space-y-5">
      {/* Tab selector */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        {MANUAL_TABS.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className="flex items-center gap-2 p-3 rounded-lg text-left transition-all"
            style={{
              background: activeTab === tab.key ? theme.oL : theme.card,
              border: `1px solid ${activeTab === tab.key ? theme.orange + '40' : theme.cardBdr}`,
              color: activeTab === tab.key ? theme.orange : theme.t2,
            }}
          >
            {tab.icon}
            <span className="text-xs font-semibold truncate">{tab.label}</span>
          </button>
        ))}
      </div>

      {/* Tab content */}
      {activeTab === 'customer' && <PaymentTab mode="customer" key="customer" />}
      {activeTab === 'vendor' && <PaymentTab mode="vendor" key="vendor" />}
      {activeTab === 'expense' && <QuickExpenseTab key="expense" />}
      {activeTab === 'journal' && <JournalEntryTab key="journal" />}
    </div>
  )
}

// ─── Batch Upload Mode (TB + JE CSV) ─────────────────────────────────────────

interface BatchResult {
  ok: boolean
  title: string
  detail: string
  rows?: string[]
}

// Period presets for the JE batch dropdown. Year-End 2025 + 9-Months Ended
// 2026 are the CFO's two historical TB cuts; the rest cover the FY26 cycle.
const BATCH_PERIODS: { label: string; date: string; description: string }[] = [
  { label: 'FY2025 (Year-End)', date: '2025-06-30', description: 'YE25 TB import' },
  { label: 'Mar26 YTD',          date: '2026-03-31', description: '9M FY26 TB import' },
  { label: 'FY2026 (Year-End)',  date: '2026-06-30', description: 'YE26 TB import' },
  { label: 'Current period',     date: '',           description: '' },
]

function BatchUploadMode() {
  const { theme } = useTheme()
  return (
    <div className="space-y-5">
      <div className="rounded-2xl p-4" style={{ background: theme.oL, border: `1px solid ${theme.orange}33` }}>
        <h3 className="font-semibold text-sm mb-1" style={{ color: theme.navy }}>Batch Upload (TB &amp; Journals)</h3>
        <p className="text-xs leading-relaxed" style={{ color: theme.t2 }}>
          Upload a Trial Balance or a flat journal-entry sheet. Both <strong>CSV</strong> and <strong>XLSX</strong>
          formats are accepted. The page previews every row before anything is posted — confirm to ingest, or
          tweak the file and re-upload. Use the Period selector to tag where the entries belong (e.g. FY25 YE).
        </p>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <TbUploadCard />
        <JeBatchUploadCard />
      </div>
    </div>
  )
}

function TbUploadCard() {
  const { theme } = useTheme()
  const [file, setFile] = useState<File | null>(null)
  const [override, setOverride] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<BatchResult | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!file) return
    setBusy(true)
    setResult(null)
    try {
      const fd = new FormData()
      fd.append('file', file)
      if (override) fd.append('override', override)
      const headers: Record<string, string> = {}
      try {
        const { acquireApiToken, SSO_API_CALLS_READY } = await import('@/auth/msal')
        if (SSO_API_CALLS_READY) {
          const t = await acquireApiToken()
          if (t) headers['Authorization'] = `Bearer ${t}`
        }
      } catch { /* fall through to DRF token */ }
      if (!headers['Authorization']) {
        // FE-SWEEP swarm 2026-06-08 #13/#14: SSO users have getToken() ===
        // SSO_SENTINEL_TOKEN ('__sso__'). Sending "Token __sso__" → 401.
        const t = getToken()
        if (t && t !== SSO_SENTINEL_TOKEN) headers['Authorization'] = `Token ${t}`
      }
      const r = await fetch('/api/v1/admin/cfo-upload-tb/', { method: 'POST', body: fd, headers, credentials: 'include' })
      const data = await r.json().catch(() => ({}))
      if (r.ok) {
        setResult({
          ok: true,
          title: 'Trial Balance imported',
          detail: data.summary ? JSON.stringify(data.summary, null, 2) : 'Import command ran successfully.',
          rows: data.log ? String(data.log).split('\n').slice(-12) : undefined,
        })
        setFile(null)
        if (inputRef.current) inputRef.current.value = ''
      } else {
        setResult({
          ok: false,
          title: `Import failed (HTTP ${r.status})`,
          detail: data.error || data.detail || 'Unknown error.',
        })
      }
    } catch (err) {
      setResult({ ok: false, title: 'Network error', detail: err instanceof Error ? err.message : String(err) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} className="rounded-2xl p-5 space-y-4"
          style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="font-semibold" style={{ color: theme.text }}>Trial Balance (CSV)</h3>
          <p className="text-xs mt-0.5" style={{ color: theme.t2 }}>
            Odoo TB export. FY25 close + 9M FY26 supported. Imported via <code>import_tb_csv --commit</code>.
          </p>
        </div>
        <FileUp className="w-5 h-5 flex-shrink-0" style={{ color: theme.orange }} />
      </div>

      <div className="space-y-3">
        <div>
          <label className="block text-xs font-medium mb-1" style={{ color: theme.t2 }}>CSV file</label>
          <input ref={inputRef} type="file" accept=".csv,.txt"
                 onChange={e => setFile(e.target.files?.[0] || null)}
                 className="w-full text-sm file:mr-3 file:px-3 file:py-1.5 file:rounded-md file:border-0 file:text-xs file:font-semibold file:cursor-pointer"
                 style={{ color: theme.t2 }} />
          {file && (
            <p className="text-xs mt-1.5" style={{ color: theme.t2 }}>
              {file.name} · {(file.size / 1024).toFixed(0)} KB
            </p>
          )}
        </div>

        <div>
          <label className="block text-xs font-medium mb-1" style={{ color: theme.t2 }}>
            Override password
            <span className="ml-1 opacity-60">(financial lock — ask the CFO)</span>
          </label>
          <input type="password" value={override} onChange={e => setOverride(e.target.value)}
                 placeholder="Required for locked periods"
                 className="w-full px-3 py-2 rounded-lg text-sm outline-none"
                 style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }} />
        </div>

        <p className="text-[11px] leading-relaxed" style={{ color: theme.t2 }}>
          <strong>Required columns:</strong> Period, Row Type, Account Code,
          End Balance Debit (BWP), End Balance Credit (BWP).
        </p>
      </div>

      <button type="submit" disabled={busy || !file}
              className="w-full inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg text-sm font-semibold transition-opacity disabled:opacity-40"
              style={{ background: theme.orange, color: '#fff' }}>
        {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />}
        {busy ? 'Importing…' : 'Import Trial Balance'}
      </button>

      {result && <BatchResultBox theme={theme} result={result} />}
    </form>
  )
}

interface PreviewRow {
  rowNumber: number
  entry_date: string
  description: string
  account_code: string
  account_name: string
  debit: number
  credit: number
  warning?: string
}

// Forgiving column lookup so the parser accepts the CFO's column names
// (Account Code, Debit, Date, …), the backend's canonical names
// (account_code, debit, entry_date, …), and a few obvious synonyms.
function pickKey(headers: string[], wanted: string[]): string | null {
  const lower = headers.map(h => h.trim().toLowerCase().replace(/[\s_-]+/g, ''))
  for (const w of wanted) {
    const wn = w.toLowerCase().replace(/[\s_-]+/g, '')
    const idx = lower.indexOf(wn)
    if (idx >= 0) return headers[idx]
  }
  return null
}

function parseNumber(v: unknown): number {
  if (typeof v === 'number') return Number.isFinite(v) ? v : 0
  if (typeof v !== 'string') return 0
  const cleaned = v.replace(/[,\s]/g, '').replace(/^\((.*)\)$/, '-$1')
  const n = parseFloat(cleaned)
  return Number.isFinite(n) ? n : 0
}

function normaliseDate(v: unknown): string {
  if (!v) return ''
  if (v instanceof Date) return v.toISOString().slice(0, 10)
  if (typeof v === 'number') {
    // Excel serial date — days since 1899-12-30
    const ms = Math.round((v - 25569) * 86400 * 1000)
    return new Date(ms).toISOString().slice(0, 10)
  }
  const s = String(v).trim()
  if (/^\d{4}-\d{2}-\d{2}/.test(s)) return s.slice(0, 10)
  const d = new Date(s)
  if (!isNaN(d.getTime())) return d.toISOString().slice(0, 10)
  return s
}

function JeBatchUploadCard() {
  const { theme } = useTheme()
  const [file, setFile] = useState<File | null>(null)
  const [autoPost, setAutoPost] = useState(true)
  const [periodIdx, setPeriodIdx] = useState(1)   // default Mar26 YTD
  const [busy, setBusy] = useState(false)
  const [parsing, setParsing] = useState(false)
  const [result, setResult] = useState<BatchResult | null>(null)
  const [preview, setPreview] = useState<PreviewRow[] | null>(null)
  const [parseError, setParseError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  async function handleFile(f: File | null) {
    setFile(f)
    setPreview(null)
    setParseError(null)
    setResult(null)
    if (!f) return
    setParsing(true)
    try {
      const xlsx = await import('xlsx')
      const buf = await f.arrayBuffer()
      const wb = xlsx.read(buf, { type: 'array', cellDates: true })
      const sheet = wb.Sheets[wb.SheetNames[0]]
      if (!sheet) throw new Error('Workbook has no sheets.')
      const raw = xlsx.utils.sheet_to_json<Record<string, unknown>>(sheet, { defval: '' })
      if (!raw.length) throw new Error('Sheet has no data rows.')

      const headers = Object.keys(raw[0] || {})
      const colDate    = pickKey(headers, ['entry_date', 'date', 'transaction date', 'doc date'])
      const colDesc    = pickKey(headers, ['description', 'narration', 'memo', 'reference'])
      const colCode    = pickKey(headers, ['account_code', 'account code', 'account', 'code', 'gl code'])
      const colName    = pickKey(headers, ['account_name', 'account name', 'name', 'gl name'])
      const colDebit   = pickKey(headers, ['debit', 'dr', 'debit (bwp)', 'end balance debit (bwp)'])
      const colCredit  = pickKey(headers, ['credit', 'cr', 'credit (bwp)', 'end balance credit (bwp)'])

      const missing: string[] = []
      if (!colCode)   missing.push('Account Code')
      if (!colDebit)  missing.push('Debit')
      if (!colCredit) missing.push('Credit')
      if (missing.length) {
        throw new Error(`Missing required column(s): ${missing.join(', ')}. Found: ${headers.join(', ')}.`)
      }

      const fallbackDate = BATCH_PERIODS[periodIdx]?.date || new Date().toISOString().slice(0, 10)
      const fallbackDesc = BATCH_PERIODS[periodIdx]?.description || 'Batch upload'

      const rows: PreviewRow[] = raw.map((r, i) => {
        const debit  = parseNumber(r[colDebit!])
        const credit = parseNumber(r[colCredit!])
        const date   = colDate ? normaliseDate(r[colDate!]) : ''
        const code   = String(r[colCode!] || '').trim()
        let warning: string | undefined
        if (!code) warning = 'Account code is blank'
        else if (debit === 0 && credit === 0) warning = 'Both debit and credit are zero'
        else if (debit > 0 && credit > 0) warning = 'Debit and credit on the same row'
        return {
          rowNumber: i + 2,                        // +2 = header on row 1
          entry_date:   date || fallbackDate,
          description:  String(r[colDesc!] || fallbackDesc).trim() || fallbackDesc,
          account_code: code,
          account_name: colName ? String(r[colName] || '').trim() : '',
          debit, credit, warning,
        }
      })
      setPreview(rows)
    } catch (err) {
      setParseError(err instanceof Error ? err.message : String(err))
    } finally {
      setParsing(false)
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!preview || preview.length === 0) return
    setBusy(true)
    setResult(null)

    // Rebuild a canonical CSV from the preview so the backend sees exactly
    // what the user just confirmed. Strips the account_name + warning
    // columns the backend doesn't read.
    const csv =
      'entry_date,description,account_code,debit,credit\n' +
      preview.filter(r => r.account_code && (r.debit > 0 || r.credit > 0))
        .map(r =>
          `${r.entry_date},${JSON.stringify(r.description)},${r.account_code},${r.debit || 0},${r.credit || 0}`
        ).join('\n') + '\n'

    try {
      const fd = new FormData()
      const blob = new Blob([csv], { type: 'text/csv' })
      fd.append('file', blob, 'batch-upload.csv')
      fd.append('auto_post', autoPost ? 'true' : 'false')
      const headers: Record<string, string> = {}
      try {
        const { acquireApiToken, SSO_API_CALLS_READY } = await import('@/auth/msal')
        if (SSO_API_CALLS_READY) {
          const t = await acquireApiToken()
          if (t) headers['Authorization'] = `Bearer ${t}`
        }
      } catch { /* fall through */ }
      if (!headers['Authorization']) {
        // FE-SWEEP swarm 2026-06-08 #13/#14: SSO users have getToken() ===
        // SSO_SENTINEL_TOKEN ('__sso__'). Sending "Token __sso__" → 401.
        const t = getToken()
        if (t && t !== SSO_SENTINEL_TOKEN) headers['Authorization'] = `Token ${t}`
      }
      const r = await fetch('/api/v1/journal-entries/batch-upload/', {
        method: 'POST', body: fd, headers, credentials: 'include',
      })
      const data = await r.json().catch(() => ({}))
      if (r.ok) {
        setResult({
          ok: true,
          title: `Posted ${data.created || (Array.isArray(data.entries) ? data.entries.length : '?')} journal entries`,
          detail: data.message || `Batch import succeeded for ${BATCH_PERIODS[periodIdx]?.label || 'the selected period'}.`,
          rows: Array.isArray(data.errors) ? data.errors.slice(0, 10) : undefined,
        })
        setFile(null)
        setPreview(null)
        if (inputRef.current) inputRef.current.value = ''
      } else {
        setResult({
          ok: false,
          title: `Import failed (HTTP ${r.status})`,
          detail: data.error || data.detail || 'Unknown error.',
          rows: Array.isArray(data.details) ? data.details.slice(0, 12) : undefined,
        })
      }
    } catch (err) {
      setResult({ ok: false, title: 'Network error', detail: err instanceof Error ? err.message : String(err) })
    } finally {
      setBusy(false)
    }
  }

  function downloadTemplate() {
    const csv = 'Date,Description,Account Code,Account Name,Debit,Credit\n'
              + '2026-05-18,Sample JE,1110,Bank — FNB Operational,1000.00,0\n'
              + '2026-05-18,Sample JE,4010,Premium income,0,1000.00\n'
    const blob = new Blob([csv], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'batch-upload-template.csv'
    a.click()
    URL.revokeObjectURL(url)
  }

  const totalDr = preview ? preview.reduce((s, r) => s + r.debit, 0) : 0
  const totalCr = preview ? preview.reduce((s, r) => s + r.credit, 0) : 0
  const balanced = preview ? Math.abs(totalDr - totalCr) < 0.01 : false

  return (
    <form onSubmit={submit} className="rounded-2xl p-5 space-y-4 lg:col-span-2"
          style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="font-semibold" style={{ color: theme.text }}>Journal Entries (CSV / XLSX)</h3>
          <p className="text-xs mt-0.5" style={{ color: theme.t2 }}>
            Generic flat-row import. Columns auto-detected (Account Code, Account Name, Debit, Credit,
            Description, Date). Rows sharing date + description merge into one balanced JE.
          </p>
        </div>
        <BookOpen className="w-5 h-5 flex-shrink-0" style={{ color: theme.orange }} />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <div className="md:col-span-2">
          <label className="block text-xs font-medium mb-1" style={{ color: theme.t2 }}>CSV or XLSX file</label>
          <input ref={inputRef} type="file" accept=".csv,.txt,.xlsx,.xls"
                 onChange={e => handleFile(e.target.files?.[0] || null)}
                 className="w-full text-sm file:mr-3 file:px-3 file:py-1.5 file:rounded-md file:border-0 file:text-xs file:font-semibold file:cursor-pointer"
                 style={{ color: theme.t2 }} />
          {file && (
            <p className="text-xs mt-1.5" style={{ color: theme.t2 }}>
              {file.name} · {(file.size / 1024).toFixed(0)} KB
              {parsing && <span className="ml-2"><Loader2 className="w-3 h-3 inline animate-spin" /> parsing…</span>}
            </p>
          )}
        </div>
        <div>
          <label className="block text-xs font-medium mb-1" style={{ color: theme.t2 }}>Period</label>
          <select value={periodIdx} onChange={e => setPeriodIdx(parseInt(e.target.value))}
                  className="w-full px-3 py-2 rounded-lg text-sm outline-none"
                  style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }}>
            {BATCH_PERIODS.map((p, i) => <option key={i} value={i}>{p.label}</option>)}
          </select>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-4">
        <label className="flex items-center gap-2 text-sm cursor-pointer" style={{ color: theme.text }}>
          <input type="checkbox" checked={autoPost} onChange={e => setAutoPost(e.target.checked)} />
          Auto-post valid entries
        </label>
        <button type="button" onClick={downloadTemplate}
                className="text-xs font-semibold underline" style={{ color: theme.orange }}>
          Download CSV template
        </button>
      </div>

      {parseError && (
        <div className="rounded-lg p-3 text-sm"
             style={{ background: '#ef444415', border: '1px solid #ef444455', color: '#dc2626' }}>
          <div className="flex items-center gap-2 font-semibold">
            <AlertCircle className="w-4 h-4" /> Parse error
          </div>
          <div className="text-xs mt-1 whitespace-pre-wrap" style={{ color: theme.text }}>{parseError}</div>
        </div>
      )}

      {preview && preview.length > 0 && (
        <div className="space-y-2">
          <div className="flex items-center justify-between text-sm">
            <span className="font-semibold" style={{ color: theme.text }}>
              Preview · {preview.length} row{preview.length === 1 ? '' : 's'}
            </span>
            <span className="text-xs tabular-nums" style={{ color: balanced ? theme.ok : theme.er }}>
              ΣDr {totalDr.toLocaleString('en-BW', { minimumFractionDigits: 2 })}
              {' · '}
              ΣCr {totalCr.toLocaleString('en-BW', { minimumFractionDigits: 2 })}
              {' · '}
              {balanced ? 'balanced' : `unbalanced (Δ ${(totalDr - totalCr).toFixed(2)})`}
            </span>
          </div>
          <div className="overflow-x-auto rounded-lg border max-h-72"
               style={{ borderColor: theme.cardBdr }}>
            <table className="w-full text-xs">
              <thead className="sticky top-0" style={{ background: theme.g100 }}>
                <tr className="text-left">
                  <th className="px-2 py-1.5 font-semibold" style={{ color: theme.t2 }}>#</th>
                  <th className="px-2 py-1.5 font-semibold" style={{ color: theme.t2 }}>Date</th>
                  <th className="px-2 py-1.5 font-semibold" style={{ color: theme.t2 }}>Code</th>
                  <th className="px-2 py-1.5 font-semibold" style={{ color: theme.t2 }}>Account Name</th>
                  <th className="px-2 py-1.5 font-semibold" style={{ color: theme.t2 }}>Description</th>
                  <th className="px-2 py-1.5 font-semibold text-right" style={{ color: theme.t2 }}>Debit</th>
                  <th className="px-2 py-1.5 font-semibold text-right" style={{ color: theme.t2 }}>Credit</th>
                </tr>
              </thead>
              <tbody>
                {preview.slice(0, 200).map(r => (
                  <tr key={r.rowNumber} className="border-t" style={{ borderColor: theme.cardBdr }}>
                    <td className="px-2 py-1 tabular-nums" style={{ color: theme.t2 }}>{r.rowNumber}</td>
                    <td className="px-2 py-1 font-mono" style={{ color: theme.text }}>{r.entry_date}</td>
                    <td className="px-2 py-1 font-mono" style={{ color: theme.text }}>{r.account_code || '—'}</td>
                    <td className="px-2 py-1 truncate max-w-[200px]" style={{ color: theme.t2 }}>{r.account_name}</td>
                    <td className="px-2 py-1 truncate max-w-[260px]" style={{ color: theme.text }}>{r.description}</td>
                    <td className="px-2 py-1 text-right tabular-nums" style={{ color: theme.text }}>{r.debit ? r.debit.toFixed(2) : ''}</td>
                    <td className="px-2 py-1 text-right tabular-nums" style={{ color: theme.text }}>{r.credit ? r.credit.toFixed(2) : ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {preview.length > 200 && (
            <p className="text-[11px]" style={{ color: theme.t2 }}>Showing first 200 of {preview.length} rows in the preview.</p>
          )}
          {preview.some(r => r.warning) && (
            <p className="text-[11px]" style={{ color: theme.wr }}>
              {preview.filter(r => r.warning).length} row(s) flagged: {preview.filter(r => r.warning).slice(0, 3).map(r => `#${r.rowNumber} (${r.warning})`).join(', ')}
              {preview.filter(r => r.warning).length > 3 ? '…' : ''}
            </p>
          )}
        </div>
      )}

      <button type="submit" disabled={busy || !preview || preview.length === 0}
              className="w-full inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg text-sm font-semibold transition-opacity disabled:opacity-40"
              style={{ background: theme.orange, color: '#fff' }}>
        {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />}
        {busy ? 'Importing…' : `Confirm & import ${preview ? preview.length : 0} row${preview && preview.length === 1 ? '' : 's'}`}
      </button>

      {result && <BatchResultBox theme={theme} result={result} />}
    </form>
  )
}

function BatchResultBox({ theme, result }: { theme: any; result: BatchResult }) {
  return (
    <div className="rounded-lg p-3 text-sm"
         style={{
           background: result.ok ? '#10b98115' : '#ef444415',
           border: `1px solid ${result.ok ? '#10b98155' : '#ef444455'}`,
         }}>
      <div className="flex items-center gap-2 font-semibold mb-1"
           style={{ color: result.ok ? '#059669' : '#dc2626' }}>
        {result.ok ? <CheckCircle2 className="w-4 h-4" /> : <AlertCircle className="w-4 h-4" />}
        {result.title}
      </div>
      <div className="text-xs whitespace-pre-wrap" style={{ color: theme.text }}>{result.detail}</div>
      {result.rows && result.rows.length > 0 && (
        <ul className="mt-2 text-[11px] space-y-0.5 font-mono" style={{ color: theme.t2 }}>
          {result.rows.map((r, i) => <li key={i}>· {r}</li>)}
        </ul>
      )}
    </div>
  )
}

// ─── Main Page ───────────────────────────────────────────────────────────────

type PageMode = 'upload' | 'manual' | 'batch'

export default function SmartEntryPage() {
  const router = useRouter()
  const { theme } = useTheme()
  const [mode, setMode] = useState<PageMode>('upload')

  useEffect(() => {
    if (!getToken()) router.push('/login')
  }, [router])

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar title="Smart Entry" breadcrumbs={[{ label: 'Smart Entry' }]} />
      <main className="flex-1 overflow-y-auto p-6 space-y-5">
        {/* Header */}
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          <div>
            <h2 className="text-xl font-bold" style={{ color: theme.navy }}>Smart Entry</h2>
            <p className="text-sm" style={{ color: theme.t2 }}>Upload documents for AI-assisted processing, enter transactions manually, or batch-import a TB / JE CSV.</p>
          </div>

          {/* Mode Segmented Control */}
          <div className="flex rounded-lg p-1 flex-shrink-0" style={{ background: theme.g100 }}>
            {([
              { key: 'upload' as PageMode, label: 'Upload Document', icon: <Sparkles className="w-3.5 h-3.5" /> },
              { key: 'manual' as PageMode, label: 'Manual Entry', icon: <PenLine className="w-3.5 h-3.5" /> },
              { key: 'batch'  as PageMode, label: 'Batch Upload (TB & Journals)', icon: <FileUp className="w-3.5 h-3.5" /> },
            ]).map((m) => (
              <button
                key={m.key}
                onClick={() => setMode(m.key)}
                className="flex items-center gap-1.5 px-4 py-2 rounded-md text-xs font-medium transition-all"
                style={{
                  background: mode === m.key ? theme.card : 'transparent',
                  color: mode === m.key ? theme.orange : theme.t2,
                  boxShadow: mode === m.key ? '0 1px 3px rgba(0,0,0,0.08)' : 'none',
                }}
              >
                {m.icon}{m.label}
              </button>
            ))}
          </div>
        </div>

        {/* Mode Content */}
        {mode === 'upload' && <UploadDocumentMode />}
        {mode === 'manual' && <ManualEntryMode />}
        {mode === 'batch'  && <BatchUploadMode />}
      </main>
    </div>
  )
}
