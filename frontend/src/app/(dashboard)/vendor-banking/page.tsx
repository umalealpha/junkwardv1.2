'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import {
  getVendorBankAccounts, getVendorBankAccount, createVendorBankAccount,
  submitVendorBank, approveVendorBank, rejectVendorBank, retireVendorBank,
  getContacts, getCurrencies, getMe, getToken,
  previewSupplierBankUpload, loadSupplierBankUpload,
} from '@/lib/api'
import type {
  VendorBankAccountListItem, VendorBankAccountDetail,
  CreateVendorBankAccountInput, Contact, Currency, UserProfile,
  SupplierBankUploadPreview,
} from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Modal } from '@/components/ui/modal'
import { Button } from '@/components/ui/button'
import { SearchableSelect } from '@/components/ui/SearchableSelect'
import {
  Plus, Search, AlertCircle, CheckCircle2, X, ShieldCheck, Send,
  AlertTriangle, Archive, Eye, Upload, FileSpreadsheet, Ban,
} from 'lucide-react'

const PAGE_SIZE = 25

const STATUS_STYLES: Record<string, string> = {
  draft:            'bg-gray-100 text-gray-700 border-gray-300',
  pending_approval: 'bg-amber-50 text-amber-800 border-amber-300',
  active:           'bg-emerald-50 text-emerald-800 border-emerald-300',
  rejected:         'bg-red-50 text-red-800 border-red-300',
  retired:          'bg-zinc-100 text-zinc-700 border-zinc-300',
}

export default function VendorBankingPage() {
  const router = useRouter()
  const [accounts, setAccounts] = useState<VendorBankAccountListItem[]>([])
  const [me, setMe]             = useState<UserProfile | null>(null)
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState<string | null>(null)
  const [info, setInfo]         = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState('')
  const [search, setSearch]     = useState('')
  const [page, setPage]         = useState(1)
  const [totalCount, setTotalCount] = useState(0)
  const [showNew, setShowNew]   = useState(false)
  const [showUpload, setShowUpload] = useState(false)
  const [showDetail, setShowDetail] = useState<VendorBankAccountDetail | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const params: { page: number; status?: string; search?: string } = { page }
      if (statusFilter) params.status = statusFilter
      if (search)       params.search = search
      const res = await getVendorBankAccounts(params)
      setAccounts(res.results)
      setTotalCount(res.count)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load')
    } finally {
      setLoading(false)
    }
  }, [statusFilter, page, search])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    getMe().then(setMe).catch(() => null)
    load()
  }, [load, router])

  const action = async (fn: () => Promise<unknown>, msg: string) => {
    setError(null); setInfo(null)
    try { await fn(); setInfo(msg); setShowDetail(null); await load() }
    catch (err) { setError(err instanceof Error ? err.message : 'Action failed') }
  }

  const canApprove = !!me && (
    me.title === 'cfo' || me.title === 'finance_manager' || me.title === 'financial_controller'
  )

  const totalPages = Math.max(1, Math.ceil(totalCount / PAGE_SIZE))

  return (
    <div className="min-h-screen bg-[#F8F9FA]">
      <TopBar title="Vendor Bank Accounts" subtitle="Maker-checker control — Finance Manager approves" />

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 space-y-4">
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div className="flex items-center gap-3 flex-wrap">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
              <input
                type="text" value={search}
                onChange={(e) => { setSearch(e.target.value); setPage(1) }}
                placeholder="Search vendor or bank"
                className="pl-9 pr-3 py-2 border border-gray-300 rounded-md text-sm w-72"
              />
            </div>
            <select value={statusFilter}
              onChange={(e) => { setStatusFilter(e.target.value); setPage(1) }}
              className="px-3 py-2 border border-gray-300 rounded-md text-sm">
              <option value="">All statuses</option>
              <option value="draft">Draft</option>
              <option value="pending_approval">Pending Approval</option>
              <option value="active">Active</option>
              <option value="rejected">Rejected</option>
              <option value="retired">Retired</option>
            </select>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={() => setShowUpload(true)}>
              <Upload className="w-4 h-4 mr-1" /> Load a list
            </Button>
            <Button onClick={() => setShowNew(true)} className="bg-[#0D1B2A] hover:bg-[#1a2940] text-white">
              <Plus className="w-4 h-4 mr-1" /> New bank account
            </Button>
          </div>
        </div>

        {!canApprove && (
          <Card className="border-amber-200 bg-amber-50">
            <CardContent className="p-3 text-sm text-amber-800 flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 mt-0.5 flex-shrink-0" />
              <span>You can enter bank accounts but only the Finance Manager,
              Financial Controller, or CFO can approve them.</span>
            </CardContent>
          </Card>
        )}

        {error && (
          <Card className="border-red-200 bg-red-50">
            <CardContent className="p-3 flex items-start gap-2">
              <AlertCircle className="w-4 h-4 text-red-700 mt-0.5" />
              <span className="text-sm text-red-700">{error}</span>
            </CardContent>
          </Card>
        )}
        {info && (
          <Card className="border-emerald-200 bg-emerald-50">
            <CardContent className="p-3 flex items-start gap-2">
              <CheckCircle2 className="w-4 h-4 text-emerald-700 mt-0.5" />
              <span className="text-sm text-emerald-700">{info}</span>
            </CardContent>
          </Card>
        )}

        <Card>
          <CardContent className="p-0">
            {loading ? (
              <div className="p-6 text-gray-500 text-sm">Loading...</div>
            ) : accounts.length === 0 ? (
              <div className="p-12 text-center text-gray-500">
                <ShieldCheck className="w-10 h-10 mx-auto mb-2 opacity-40" />
                <p className="text-sm">No bank accounts yet.</p>
              </div>
            ) : (
              <table className="w-full text-sm">
                <thead className="bg-gray-50 border-b text-xs uppercase text-gray-600">
                  <tr className="text-left">
                    <th className="px-4 py-3">Vendor</th>
                    <th className="px-4 py-3">Bank</th>
                    <th className="px-4 py-3">Holder name</th>
                    <th className="px-4 py-3">Account #</th>
                    <th className="px-4 py-3">CCY</th>
                    <th className="px-4 py-3">Status</th>
                    <th className="px-4 py-3"></th>
                  </tr>
                </thead>
                <tbody>
                  {accounts.map((b) => (
                    <tr key={b.id} className="border-b border-gray-100 hover:bg-gray-50">
                      <td className="px-4 py-2.5">
                        {b.contact_name}
                        {b.is_default && (
                          <span className="ml-2 text-[10px] uppercase bg-blue-50 text-blue-700 border border-blue-200 px-1.5 py-0.5 rounded">default</span>
                        )}
                      </td>
                      <td className="px-4 py-2.5">{b.bank_name}</td>
                      <td className="px-4 py-2.5">
                        {b.account_holder_name}
                        {b.name_mismatch && (
                          <span className="ml-2 text-[10px] uppercase bg-amber-50 text-amber-800 border border-amber-200 px-1.5 py-0.5 rounded" title="Holder name does not match vendor name — Finance Manager should question this">
                            ⚠ name mismatch
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-2.5 font-mono text-xs">{b.masked_account}</td>
                      <td className="px-4 py-2.5">{b.currency_code}</td>
                      <td className="px-4 py-2.5">
                        <span className={`text-xs px-2 py-0.5 rounded-full border ${STATUS_STYLES[b.status]}`}>
                          {b.status_display}
                        </span>
                      </td>
                      <td className="px-4 py-2.5 text-right">
                        <Button size="sm" variant="outline" onClick={async () => {
                          setShowDetail(await getVendorBankAccount(b.id))
                        }}>
                          <Eye className="w-3.5 h-3.5 mr-1" /> View
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </CardContent>
        </Card>

        {totalCount > PAGE_SIZE && (
          <div className="flex justify-between text-sm text-gray-600">
            <span>Page {page} of {totalPages} — {totalCount} total</span>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>Prev</Button>
              <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => setPage(page + 1)}>Next</Button>
            </div>
          </div>
        )}
      </div>

      {showUpload && (
        <LoadASupplierListModal
          onClose={() => setShowUpload(false)}
          onLoaded={async (msg) => {
            setShowUpload(false)
            setInfo(msg)
            await load()
          }}
        />
      )}
      {showNew && (
        <NewBankAccountModal
          onClose={() => setShowNew(false)}
          onCreated={async () => {
            setShowNew(false)
            setInfo('Bank account saved as Draft. Submit it for FM approval when ready.')
            await load()
          }}
        />
      )}
      {showDetail && (
        <DetailModal
          bank={showDetail}
          canApprove={canApprove}
          currentUserId={me?.id || null}
          onClose={() => setShowDetail(null)}
          onSubmit={async () => action(() => submitVendorBank(showDetail.id), 'Submitted for approval')}
          onApprove={async () => action(() => approveVendorBank(showDetail.id), 'Bank account approved — now ACTIVE')}
          onReject={async (reason) => action(() => rejectVendorBank(showDetail.id, reason), 'Rejected')}
          onRetire={async (reason) => action(() => retireVendorBank(showDetail.id, reason), 'Retired')}
        />
      )}
    </div>
  )
}

// ─── Load a whole supplier list ─────────────────────────────────────────────
// CFO 2026-08-20: "Create a place where people can upload the supplier names.
// We already have the list of suppliers and the bank accounts … so they don't
// need to really do hard work."
//
// Deliberately two steps. Nothing is written until the person has seen what
// every row of their sheet will do — including the rows that will NOT load,
// which is the half people never get told about.
const VERDICT_STYLE: Record<string, { row: string; chip: string }> = {
  load:               { row: 'bg-white',       chip: 'bg-emerald-100 text-emerald-800' },
  load_new_supplier:  { row: 'bg-white',       chip: 'bg-sky-100 text-sky-800' },
  already:            { row: 'bg-gray-50',     chip: 'bg-gray-200 text-gray-700' },
  bank_changed:       { row: 'bg-amber-50',    chip: 'bg-amber-200 text-amber-900' },
  missing:            { row: 'bg-red-50',      chip: 'bg-red-100 text-red-800' },
  bad_account:        { row: 'bg-red-50',      chip: 'bg-red-100 text-red-800' },
  bad_currency:       { row: 'bg-red-50',      chip: 'bg-red-100 text-red-800' },
}

function LoadASupplierListModal({ onClose, onLoaded }: {
  onClose: () => void
  onLoaded: (message: string) => void
}) {
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<SupplierBankUploadPreview | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const look = async (f: File) => {
    setBusy(true); setError(''); setPreview(null)
    try {
      setPreview(await previewSupplierBankUpload(f))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'That file could not be read.')
    } finally {
      setBusy(false)
    }
  }

  const pick = async (f: File | null) => {
    setFile(f)
    if (f) await look(f)
  }

  const doLoad = async () => {
    if (!file) return
    setBusy(true); setError('')
    try {
      const out = await loadSupplierBankUpload(file)
      onLoaded(out.message)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'The load did not finish.')
    } finally {
      setBusy(false)
    }
  }

  // The house Modal, not a hand-rolled overlay: it portals to the body, so the
  // navigation flyout cannot sit on top of it. Rolling my own put the sidebar
  // over the left half of this dialog — visible only by looking at the screen.
  return (
    <Modal open onOpenChange={(o) => { if (!o) onClose() }} size="xl"
           title="Load a supplier list"
           description="Excel or CSV. The columns are matched by meaning, so your own headings are fine.">
      <div className="px-6 py-4 space-y-4">
          <label className="flex flex-col items-center justify-center gap-2 border-2 border-dashed
                            border-gray-300 rounded-lg py-8 cursor-pointer hover:border-[#F4A623]
                            transition-colors">
            <FileSpreadsheet className="w-8 h-8 text-gray-400" />
            <span className="text-sm text-gray-600">
              {file ? file.name : 'Choose your supplier list'}
            </span>
            <span className="text-xs text-gray-400">
              Supplier name, bank, account number. A branch code helps.
            </span>
            <input type="file" className="hidden"
              accept=".csv,.xlsx,.xlsm,.txt,text/csv"
              onChange={(e) => pick(e.target.files?.[0] ?? null)} />
          </label>

          {busy && <p className="text-sm text-gray-500">Reading it…</p>}

          {error && (
            <div className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 p-3">
              <AlertCircle className="w-4 h-4 text-red-700 mt-0.5 flex-shrink-0" />
              <span className="text-sm text-red-700">{error}</span>
            </div>
          )}

          {preview && (
            <>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-center">
                <div className="rounded-md border border-emerald-200 bg-emerald-50 py-2">
                  <div className="text-lg font-semibold text-emerald-800">{preview.will_load}</div>
                  <div className="text-[11px] text-emerald-700">will be added</div>
                </div>
                <div className="rounded-md border border-amber-200 bg-amber-50 py-2">
                  <div className="text-lg font-semibold text-amber-900">{preview.held}</div>
                  <div className="text-[11px] text-amber-800">held — bank changed</div>
                </div>
                <div className="rounded-md border border-gray-200 bg-gray-50 py-2">
                  <div className="text-lg font-semibold text-gray-700">{preview.already_on_file}</div>
                  <div className="text-[11px] text-gray-600">already on file</div>
                </div>
                <div className="rounded-md border border-red-200 bg-red-50 py-2">
                  <div className="text-lg font-semibold text-red-800">{preview.rejected}</div>
                  <div className="text-[11px] text-red-700">cannot be read</div>
                </div>
              </div>

              {preview.held > 0 && (
                <div className="flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 p-3">
                  <Ban className="w-4 h-4 text-amber-800 mt-0.5 flex-shrink-0" />
                  <span className="text-sm text-amber-900">
                    Some rows would move a supplier we already pay onto a
                    different account. This screen will not do that — add those
                    one at a time, so somebody records why the account changed.
                  </span>
                </div>
              )}

              <div className="max-h-80 overflow-y-auto border border-gray-200 rounded-md">
                <table className="w-full text-sm">
                  <thead className="bg-gray-50 sticky top-0">
                    <tr className="text-left text-xs text-gray-600">
                      <th className="px-3 py-2 font-medium">Row</th>
                      <th className="px-3 py-2 font-medium">Supplier</th>
                      <th className="px-3 py-2 font-medium">Bank</th>
                      <th className="px-3 py-2 font-medium">Account ends</th>
                      <th className="px-3 py-2 font-medium">What happens</th>
                    </tr>
                  </thead>
                  <tbody>
                    {preview.rows.map((r) => {
                      const st = VERDICT_STYLE[r.verdict] ?? { row: 'bg-white', chip: 'bg-gray-100 text-gray-700' }
                      return (
                        <tr key={r.line} className={`border-t border-gray-100 ${st.row}`}>
                          <td className="px-3 py-2 text-gray-500">{r.line}</td>
                          <td className="px-3 py-2 text-gray-900">{r.supplier || '—'}</td>
                          <td className="px-3 py-2 text-gray-700">{r.bank || '—'}</td>
                          <td className="px-3 py-2 text-gray-700 tabular-nums">
                            {r.account_ends ? `…${r.account_ends}` : '—'}
                          </td>
                          <td className="px-3 py-2">
                            <span className={`inline-block rounded px-2 py-0.5 text-[11px] ${st.chip}`}>
                              {r.label}
                            </span>
                            {r.reason && (
                              <div className="text-[11px] text-gray-500 mt-0.5">{r.reason}</div>
                            )}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>

            {preview.columns_ignored.length > 0 && (
              <p className="text-xs text-gray-500">
                Columns we did not use: {preview.columns_ignored.join(', ')}
              </p>
            )}
          </>
        )}
      </div>

      <div className="flex items-center justify-between gap-2 px-6 py-4 border-t bg-gray-50">
        <p className="text-xs text-gray-500">
          Everything loaded starts as a Draft and still needs approval here.
        </p>
        <div className="flex items-center gap-2">
          <Button variant="outline" onClick={onClose} disabled={busy}>Close</Button>
          <Button onClick={doLoad}
            disabled={busy || !preview || preview.will_load === 0}
            className="bg-[#0D1B2A] hover:bg-[#1a2940] text-white">
            {preview ? `Add ${preview.will_load} account${preview.will_load === 1 ? '' : 's'}` : 'Add them'}
          </Button>
        </div>
      </div>
    </Modal>
  )
}

// ─── New bank account modal ─────────────────────────────────────────────────

function NewBankAccountModal({ onClose, onCreated }: {
  onClose: () => void; onCreated: () => Promise<void>;
}) {
  const [vendors, setVendors] = useState<Contact[]>([])
  const [currencies, setCurrencies] = useState<Currency[]>([])
  const [form, setForm] = useState<CreateVendorBankAccountInput>({
    contact: '', bank_name: '', account_holder_name: '', account_number: '',
    branch_code: '', branch_name: '', swift_bic: '', iban: '', email: '',
    currency_code: 'BWP', is_default: true, notes: '',
  })
  const [busy, setBusy] = useState(false)
  const [err, setErr]   = useState<string | null>(null)

  useEffect(() => {
    getContacts({ contact_type: 'vendor', page_size: '5000' }).then(r => setVendors(r.results || [])).catch(() => null)
    getCurrencies().then(r => setCurrencies(r.results || [])).catch(() => null)
  }, [])

  const onSubmit = async () => {
    setErr(null); setBusy(true)
    try {
      await createVendorBankAccount(form)
      await onCreated()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Save failed')
    } finally { setBusy(false) }
  }

  const upd = (k: keyof CreateVendorBankAccountInput, v: string | boolean) =>
    setForm({ ...form, [k]: v })

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-lg max-w-2xl w-full p-6 space-y-3 max-h-[90vh] overflow-y-auto">
        <h3 className="text-lg font-medium">New vendor bank account</h3>
        <p className="text-xs text-gray-600">
          You enter the details — saves as Draft. Submit for Finance Manager
          approval when ready. Once approved, the record becomes ACTIVE and
          cannot be edited (to change details, retire it and create a new one).
        </p>
        {err && <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded p-2">{err}</div>}

        <div className="grid grid-cols-2 gap-3 text-sm">
          <Field label="Vendor" required>
            <SearchableSelect
              options={vendors.map(v => ({ value: v.id, label: v.name }))}
              value={form.contact}
              onChange={v => upd('contact', v)}
              placeholder="Search vendor…"
            />
          </Field>
          <Field label="Currency" required>
            <select value={form.currency_code} onChange={e => upd('currency_code', e.target.value)} className="input">
              {currencies.map(c => <option key={c.code} value={c.code}>{c.code}</option>)}
            </select>
          </Field>

          <Field label="Bank name" required>
            <input value={form.bank_name} onChange={e => upd('bank_name', e.target.value)} className="input" placeholder="e.g. First National Bank" />
          </Field>
          <Field label="Branch name">
            <input value={form.branch_name} onChange={e => upd('branch_name', e.target.value)} className="input" placeholder="e.g. Gaborone Main" />
          </Field>

          <Field label="Account holder name (must match bank)" required>
            <input value={form.account_holder_name} onChange={e => upd('account_holder_name', e.target.value)} className="input" />
          </Field>
          <Field label="Branch code / sort code">
            <input value={form.branch_code} onChange={e => upd('branch_code', e.target.value)} className="input" />
          </Field>

          <Field label="Account number" required>
            <input value={form.account_number} onChange={e => upd('account_number', e.target.value)} className="input" />
          </Field>
          <Field label="SWIFT/BIC (international)">
            <input value={form.swift_bic} onChange={e => upd('swift_bic', e.target.value)} className="input" placeholder="e.g. FIRNBWGX" />
          </Field>

          <Field label="IBAN (if applicable)">
            <input value={form.iban} onChange={e => upd('iban', e.target.value)} className="input" />
          </Field>
          <Field label="Proof of payment email">
            <input type="email" value={form.email || ''} onChange={e => upd('email', e.target.value)} className="input" placeholder="Where FNB sends this vendor's POP" />
          </Field>
          <Field label="">
            <label className="flex items-center gap-2 mt-6">
              <input type="checkbox" checked={!!form.is_default} onChange={e => upd('is_default', e.target.checked)} />
              <span>Default for this currency</span>
            </label>
          </Field>

          <div className="col-span-2">
            <Field label="Notes">
              <textarea value={form.notes} onChange={e => upd('notes', e.target.value)} rows={2} className="input" />
            </Field>
          </div>
        </div>

        <div className="flex justify-end gap-2 pt-2">
          <Button variant="outline" onClick={onClose} disabled={busy}>Close</Button>
          <Button onClick={onSubmit}
            disabled={busy || !form.contact || !form.bank_name || !form.account_holder_name || !form.account_number}
            className="bg-[#0D1B2A] hover:bg-[#1a2940] text-white">
            {busy ? 'Saving...' : 'Save as Draft'}
          </Button>
        </div>
      </div>
      <style jsx>{`
        .input {
          width: 100%; padding: 0.5rem 0.75rem;
          border: 1px solid #d1d5db; border-radius: 0.375rem;
          font-size: 0.875rem; background: white;
        }
      `}</style>
    </div>
  )
}

// ─── Detail modal — view + workflow actions ──────────────────────────────────

function DetailModal({ bank, canApprove, currentUserId, onClose,
                       onSubmit, onApprove, onReject, onRetire }: {
  bank: VendorBankAccountDetail;
  canApprove: boolean;
  currentUserId: string | null;
  onClose: () => void;
  onSubmit: () => Promise<void>;
  onApprove: () => Promise<void>;
  onReject: (reason: string) => Promise<void>;
  onRetire: (reason: string) => Promise<void>;
}) {
  const [reason, setReason] = useState('')
  const [showReject, setShowReject] = useState(false)
  const [showRetire, setShowRetire] = useState(false)

  const isCreator = bank.created_by === currentUserId
  const isSubmitter = bank.submitted_by === currentUserId

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-lg max-w-2xl w-full p-6 space-y-3 max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between">
          <h3 className="text-lg font-medium">{bank.contact_name} — bank account</h3>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="text-xs">
          <span className={`px-2 py-0.5 rounded-full border ${STATUS_STYLES[bank.status]}`}>
            {bank.status_display}
          </span>
          {bank.name_mismatch && (
            <span className="ml-2 text-amber-800 bg-amber-50 border border-amber-200 px-2 py-0.5 rounded text-[11px]">
              ⚠ Holder name doesn't match vendor name — verify before approving
            </span>
          )}
        </div>

        <div className="grid grid-cols-2 gap-3 text-sm pt-2">
          <Stat label="Bank" value={bank.bank_name} />
          <Stat label="Branch" value={bank.branch_name || '—'} />
          <Stat label="Holder name" value={bank.account_holder_name} />
          <Stat label="Branch code" value={bank.branch_code || '—'} />
          <Stat label="Account #" value={<span className="font-mono">{bank.account_number}</span>} />
          <Stat label="SWIFT/BIC" value={bank.swift_bic || '—'} />
          <Stat label="IBAN" value={bank.iban || '—'} />
          <Stat label="POP email" value={bank.email || '—'} />
          <Stat label="Currency" value={bank.currency_code} />
          <Stat label="Default" value={bank.is_default ? 'Yes' : 'No'} />
          <Stat label="Created by" value={bank.created_by_username} />
          <Stat label="Submitted by" value={bank.submitted_by_username || '—'} />
          <Stat label="Approved by" value={bank.approved_by_username || '—'} />
        </div>

        {bank.notes && (
          <div className="text-sm border-t pt-2">
            <div className="text-xs text-gray-600 mb-0.5">Notes</div>
            <p>{bank.notes}</p>
          </div>
        )}
        {bank.rejection_reason && (
          <div className="text-sm text-red-800 bg-red-50 border border-red-200 rounded p-2">
            <strong>Rejection reason:</strong> {bank.rejection_reason}
          </div>
        )}
        {bank.retirement_reason && (
          <div className="text-sm text-zinc-700 bg-zinc-50 border border-zinc-200 rounded p-2">
            <strong>Retirement reason:</strong> {bank.retirement_reason}
          </div>
        )}

        <div className="flex flex-wrap gap-2 pt-3 border-t">
          {bank.status === 'draft' && (
            <Button onClick={onSubmit} className="bg-amber-700 hover:bg-amber-800 text-white">
              <Send className="w-4 h-4 mr-1" /> Submit for FM approval
            </Button>
          )}
          {bank.status === 'pending_approval' && canApprove && !isCreator && !isSubmitter && (
            <>
              <Button onClick={onApprove} className="bg-emerald-700 hover:bg-emerald-800 text-white">
                <CheckCircle2 className="w-4 h-4 mr-1" /> Approve
              </Button>
              <Button variant="outline" onClick={() => setShowReject(true)}>
                <X className="w-4 h-4 mr-1" /> Reject
              </Button>
            </>
          )}
          {bank.status === 'pending_approval' && (isCreator || isSubmitter) && (
            <span className="text-xs text-gray-600 italic">
              Segregation of duties: a different user must approve this record.
            </span>
          )}
          {bank.status === 'active' && canApprove && (
            <Button variant="outline" onClick={() => setShowRetire(true)}>
              <Archive className="w-4 h-4 mr-1" /> Retire
            </Button>
          )}
        </div>

        {showReject && (
          <div className="border border-red-200 bg-red-50 rounded p-3 space-y-2">
            <div className="text-sm font-medium text-red-800">Rejection reason</div>
            <textarea value={reason} onChange={e => setReason(e.target.value)} rows={2}
              className="w-full p-2 border border-red-300 rounded text-sm" />
            <div className="flex justify-end gap-2">
              <Button variant="outline" size="sm" onClick={() => { setShowReject(false); setReason('') }}>Cancel</Button>
              <Button size="sm" disabled={!reason.trim()}
                onClick={() => onReject(reason)}
                className="bg-red-600 hover:bg-red-700 text-white">
                Confirm reject
              </Button>
            </div>
          </div>
        )}
        {showRetire && (
          <div className="border border-zinc-200 bg-zinc-50 rounded p-3 space-y-2">
            <div className="text-sm font-medium text-zinc-800">Retirement reason</div>
            <p className="text-xs text-zinc-600">
              Use when the vendor has changed bank or this account is no longer
              valid. To enter new banking details, create a new draft after
              retiring.
            </p>
            <textarea value={reason} onChange={e => setReason(e.target.value)} rows={2}
              className="w-full p-2 border border-zinc-300 rounded text-sm" />
            <div className="flex justify-end gap-2">
              <Button variant="outline" size="sm" onClick={() => { setShowRetire(false); setReason('') }}>Cancel</Button>
              <Button size="sm" disabled={!reason.trim()}
                onClick={() => onRetire(reason)}
                className="bg-zinc-700 hover:bg-zinc-800 text-white">
                Confirm retire
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function Field({ label, required, children }: {
  label: string; required?: boolean; children: React.ReactNode;
}) {
  return (
    <div>
      {label && (
        <label className="block text-xs font-medium text-gray-600 mb-1">
          {label}{required && <span className="text-red-500">*</span>}
        </label>
      )}
      {children}
    </div>
  )
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <div className="text-xs uppercase text-gray-500">{label}</div>
      <div className="text-sm font-medium text-gray-800">{value}</div>
    </div>
  )
}
