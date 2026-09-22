'use client'

/**
 * /reverse-charge — Reverse-Charge VAT capture on imported remote services.
 *
 * Botswana VAT Amendment Act No.16 of 2025, effective 1 June 2026. Foreign
 * digital-service suppliers (AWS, Anthropic, Google, OpenAI, GitHub,
 * Mailgun, Time Doctor, etc.) are non-resident and carry no Botswana VAT —
 * finance self-assesses here instead of relying on the supplier's invoice.
 *
 * The backend (settings.RC_VAT_RATE) is the source of truth for output_vat
 * / net_vat_cost — this page only previews them live at the standard 14%
 * rate for feedback while typing. Nothing here is auto-populated from
 * other modules; every row is a deliberate, manually-captured entry.
 */

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import {
  getReverseChargeEntries, createReverseChargeEntry, updateReverseChargeEntry, getToken,
  type ReverseChargeEntryRow, type ReverseChargeCategory,
} from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  Plus, Info, ChevronDown, ChevronUp, Globe, AlertCircle, FileText,
  Pencil, Check, X,
} from 'lucide-react'
import { localYmd } from '@/lib/utils'

// Client-side preview only — settings.RC_VAT_RATE on the server is the
// authoritative rate and recomputes output_vat / net_vat_cost on save.
const RC_VAT_RATE_PREVIEW = 0.14

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'

const CATEGORIES: { value: ReverseChargeCategory; label: string }[] = [
  { value: 'cloud',        label: 'Cloud hosting & compute (e.g. AWS)' },
  { value: 'ai_saas',      label: 'AI / SaaS subscriptions (e.g. Anthropic, Google, OpenAI)' },
  { value: 'productivity', label: 'Productivity & automation (e.g. Microsoft 365, n8n, Power Automate)' },
  { value: 'design',       label: 'Design & content tools (e.g. Canva, Gamma)' },
  { value: 'devtools',     label: 'Dev tooling (e.g. GitHub, Cursor)' },
  { value: 'hris',         label: 'HRIS / monitoring (e.g. Time Doctor)' },
  { value: 'email_api',    label: 'Email / messaging API (e.g. Mailgun)' },
  { value: 'other',        label: 'Other imported remote service' },
]

function money(n: number) {
  return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function todayISO() {
  return localYmd(new Date())
}

export default function ReverseChargeVatPage() {
  const router = useRouter()
  const [rows, setRows] = useState<ReverseChargeEntryRow[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [showSop, setShowSop] = useState(false)

  // M-1 (Fable-5 review): the SOP above tells finance to lower input VAT
  // recoverable on entries behind VAT-exempt income, but there was no edit
  // path in the UI to do it — updateReverseChargeEntry existed in the API
  // client but nothing on this page ever called it. Inline row-edit for
  // just the two fields finance actually needs to touch: input VAT
  // recoverable (the override C-1 depends on) and the note.
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editRecoverable, setEditRecoverable] = useState('')
  const [editNote, setEditNote] = useState('')
  const [editBusy, setEditBusy] = useState(false)
  const [editErr, setEditErr] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setLoadError(null)
    try {
      const d = await getReverseChargeEntries({ page_size: '100' })
      setRows(d.results)
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : 'Failed to load reverse-charge entries')
    } finally { setLoading(false) }
  }, [])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load()
  }, [load, router])

  function startEdit(r: ReverseChargeEntryRow) {
    setEditingId(r.id)
    setEditRecoverable(r.input_vat_recoverable)
    setEditNote(r.note || '')
    setEditErr(null)
  }

  function cancelEdit() {
    setEditingId(null)
    setEditErr(null)
  }

  async function saveEdit(id: string) {
    setEditBusy(true); setEditErr(null)
    try {
      await updateReverseChargeEntry(id, {
        input_vat_recoverable: editRecoverable,
        note: editNote.trim(),
      })
      setEditingId(null)
      await load()
    } catch (e) {
      setEditErr(e instanceof Error ? e.message : 'Could not save the change')
    } finally {
      setEditBusy(false)
    }
  }

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Reverse-Charge VAT"
        breadcrumbs={[{ label: 'Accounting' }, { label: 'Reverse-Charge VAT' }]}
      />
      <main className="flex-1 p-6 space-y-5">
        {/* Heading + helper tooltip */}
        <div className="flex items-start gap-2">
          <Globe className="w-5 h-5 mt-0.5 flex-shrink-0" style={{ color: NAVY }} />
          <div>
            <div className="flex items-center gap-1.5">
              <h1 className="text-lg font-bold" style={{ color: NAVY }}>
                Reverse-Charge VAT — Imported Remote Services
              </h1>
              <span
                tabIndex={0}
                title='Local suppliers (e.g. RealPay) are NOT reverse-charge. Only non-resident/foreign remote services.'
                className="inline-flex cursor-help"
              >
                <Info className="w-4 h-4" style={{ color: ORANGE }} />
              </span>
            </div>
            <p className="text-sm text-[#6B7280] max-w-2xl mt-0.5">
              Self-assessed VAT on foreign digital services (AWS, Anthropic, etc.) &mdash; VAT
              Amendment Act No.16 of 2025, effective 1 June 2026.
            </p>
          </div>
        </div>

        {/* Collapsible SOP panel */}
        <Card style={{ borderColor: `${NAVY}26` }}>
          <button
            type="button"
            onClick={() => setShowSop(v => !v)}
            aria-expanded={showSop}
            className="w-full flex items-center justify-between px-4 py-3 text-left"
          >
            <span className="text-sm font-semibold" style={{ color: NAVY }}>
              How to capture &mdash; read me
            </span>
            {showSop ? (
              <ChevronUp className="w-4 h-4" style={{ color: NAVY }} />
            ) : (
              <ChevronDown className="w-4 h-4" style={{ color: NAVY }} />
            )}
          </button>
          {showSop && (
            <CardContent className="pt-0 pb-4">
              <ol className="list-decimal list-inside space-y-1.5 text-sm text-[#374151]">
                <li>
                  Only <strong>foreign / non-resident</strong> remote services go here &mdash;
                  things like AWS, Anthropic, Google, GitHub. <strong>Local suppliers
                  (e.g. RealPay) are excluded</strong> &mdash; they already charge Botswana
                  VAT normally.
                </li>
                <li>
                  Enter the <strong>BWP amount you actually paid</strong> &mdash; not a
                  converted estimate. Use the amount that hit the bank/card statement.
                </li>
                <li>
                  VAT <strong>auto-calculates at 14%</strong> of the BWP amount &mdash; you
                  don&apos;t enter it yourself.
                </li>
                <li>
                  Input VAT defaults to <strong>full recovery</strong> (100% of the output
                  VAT). <span style={{ color: '#B45309' }} className="font-semibold">
                  But on entries that sit behind VAT-exempt insurance income, full recovery
                  overstates the recoverable VAT and is a BURS audit risk &mdash; lower it
                  for those lines.</span>
                </li>
                <li>
                  Picking <strong>&quot;Other&quot;</strong> requires a short note on what
                  the service is before you can save.
                </li>
              </ol>
            </CardContent>
          )}
        </Card>

        {loadError && (
          <Card className="border-red-300 bg-red-50/40">
            <CardContent className="py-3 text-sm text-red-700 flex items-center gap-2">
              <AlertCircle className="w-4 h-4" /> {loadError}
            </CardContent>
          </Card>
        )}

        {/* Capture form */}
        <NewEntryForm onSaved={load} />

        {/* Existing entries */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">Captured entries</CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            {editErr && (
              <div className="px-3 py-2 text-xs text-red-700 bg-red-50/40 border-b flex items-center gap-1.5">
                <AlertCircle className="w-3.5 h-3.5 flex-shrink-0" /> {editErr}
              </div>
            )}
            {loading ? (
              <div className="py-10 text-center text-sm text-[#9CA3AF]">Loading…</div>
            ) : rows.length === 0 ? (
              <div className="py-10 text-center text-sm text-[#9CA3AF]">
                <FileText className="w-8 h-8 mx-auto mb-2 opacity-40" />
                No reverse-charge entries captured yet.
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead style={{ background: `${NAVY}0D` }} className="text-left text-xs uppercase text-[#6B7280] border-b">
                    <tr>
                      <th className="py-2 px-3">Vendor</th>
                      <th className="py-2 px-3">Category</th>
                      <th className="py-2 px-3">Invoice Date</th>
                      <th className="py-2 px-3 text-right">Foreign Amount</th>
                      <th className="py-2 px-3 text-right">BWP Amount</th>
                      <th className="py-2 px-3 text-right">Output VAT</th>
                      <th className="py-2 px-3 text-right">Input VAT Recoverable</th>
                      <th className="py-2 px-3 text-right">Net VAT Cost</th>
                      <th className="py-2 px-3 text-right">Edit</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map(r => {
                      const isEditing = editingId === r.id
                      return (
                      <tr key={r.id} className="border-b last:border-0 hover:bg-[#FFF7ED]">
                        <td className="py-2 px-3 font-medium">
                          {r.vendor}
                          {isEditing ? (
                            <input
                              value={editNote} onChange={e => setEditNote(e.target.value)}
                              placeholder='Note — required for "Other"'
                              className="block w-full mt-1 border rounded px-2 py-1 text-xs bg-background"
                            />
                          ) : r.note && (
                            <span
                              className="block text-xs text-[#9CA3AF] truncate max-w-[220px]"
                              title={r.note}
                            >
                              {r.note}
                            </span>
                          )}
                        </td>
                        <td className="py-2 px-3 text-xs text-[#6B7280]">{r.category_label}</td>
                        <td className="py-2 px-3 text-xs">{r.invoice_date}</td>
                        <td className="py-2 px-3 text-right tabular-nums text-xs text-[#6B7280]">
                          {r.foreign_currency} {money(Number(r.foreign_amount))}
                        </td>
                        <td className="py-2 px-3 text-right tabular-nums">
                          BWP {money(Number(r.bwp_amount))}
                        </td>
                        <td className="py-2 px-3 text-right tabular-nums font-medium">
                          {money(Number(r.output_vat))}
                        </td>
                        <td className="py-2 px-3 text-right tabular-nums">
                          {isEditing ? (
                            <input
                              type="number" step="0.01" value={editRecoverable}
                              onChange={e => setEditRecoverable(e.target.value)}
                              className="w-28 border rounded px-2 py-1 text-sm bg-background text-right"
                              style={{ borderColor: ORANGE }}
                            />
                          ) : (
                            money(Number(r.input_vat_recoverable))
                          )}
                        </td>
                        <td
                          className="py-2 px-3 text-right tabular-nums font-semibold"
                          style={{ color: Number(r.net_vat_cost) > 0 ? '#B45309' : '#059669' }}
                        >
                          {money(Number(r.net_vat_cost))}
                        </td>
                        <td className="py-2 px-3 text-right">
                          {isEditing ? (
                            <div className="flex items-center justify-end gap-1">
                              <button
                                type="button" onClick={() => saveEdit(r.id)} disabled={editBusy}
                                title="Save" aria-label="Save"
                                className="p-1 rounded hover:bg-[#0596691a] disabled:opacity-50"
                              >
                                <Check className="w-4 h-4" style={{ color: '#059669' }} />
                              </button>
                              <button
                                type="button" onClick={cancelEdit} disabled={editBusy}
                                title="Cancel" aria-label="Cancel"
                                className="p-1 rounded hover:bg-[#DC26261a] disabled:opacity-50"
                              >
                                <X className="w-4 h-4" style={{ color: '#DC2626' }} />
                              </button>
                            </div>
                          ) : (
                            <button
                              type="button" onClick={() => startEdit(r)}
                              title="Edit input VAT recoverable / note"
                              aria-label="Edit input VAT recoverable / note"
                              className="p-1 rounded hover:bg-[#0D1B2A0D]"
                            >
                              <Pencil className="w-4 h-4" style={{ color: NAVY }} />
                            </button>
                          )}
                        </td>
                      </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>
      </main>
    </div>
  )
}

// ─── New entry form ─────────────────────────────────────────────────────────

function NewEntryForm({ onSaved }: { onSaved: () => void }) {
  const [vendor, setVendor] = useState('')
  const [category, setCategory] = useState<ReverseChargeCategory | ''>('')
  const [invoiceDate, setInvoiceDate] = useState(todayISO())
  const [foreignCurrency, setForeignCurrency] = useState('USD')
  const [foreignAmount, setForeignAmount] = useState('')
  const [bwpAmount, setBwpAmount] = useState('')
  const [inputRecoverable, setInputRecoverable] = useState('')
  const [recoverableTouched, setRecoverableTouched] = useState(false)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const bwpNum = Number(bwpAmount) || 0
  const outputVatPreview = Math.round(bwpNum * RC_VAT_RATE_PREVIEW * 100) / 100
  const recoverableNum = recoverableTouched ? (Number(inputRecoverable) || 0) : outputVatPreview
  const netVatCostPreview = Math.round((outputVatPreview - recoverableNum) * 100) / 100

  const isOther = category === 'other'
  const noteOk = !isOther || note.trim().length > 0
  const canSubmit = vendor.trim().length >= 2 && !!category && !!invoiceDate
    && foreignCurrency.trim().length === 3 && Number(foreignAmount) > 0
    && bwpNum > 0 && noteOk && !busy

  async function submit() {
    if (!canSubmit || !category) return
    setBusy(true); setErr(null)
    try {
      await createReverseChargeEntry({
        vendor: vendor.trim(),
        category,
        invoice_date: invoiceDate,
        foreign_currency: foreignCurrency.trim().toUpperCase(),
        foreign_amount: foreignAmount,
        bwp_amount: bwpAmount,
        // Omit entirely unless the user actually touched it — the server
        // then defaults it to full recovery (= output_vat) itself, the
        // same rule the live preview below follows.
        input_vat_recoverable: recoverableTouched ? inputRecoverable : undefined,
        note: note.trim(),
      })
      // Reset for the next capture — this is a repeat-entry workflow.
      setVendor(''); setCategory(''); setForeignAmount(''); setBwpAmount('')
      setInputRecoverable(''); setRecoverableTouched(false); setNote('')
      setInvoiceDate(todayISO())
      onSaved()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not save the entry')
    } finally { setBusy(false) }
  }

  return (
    <Card style={{ borderColor: `${NAVY}26` }}>
      <CardHeader className="pb-3">
        <CardTitle className="text-base flex items-center gap-2">
          <Plus className="w-4 h-4" /> New reverse-charge entry
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-xs text-[#6B7280] mb-1">Vendor</label>
            <input
              value={vendor} onChange={e => setVendor(e.target.value)}
              placeholder="e.g. Amazon Web Services"
              className="w-full border rounded px-3 py-1.5 text-sm bg-background"
            />
          </div>
          <div>
            <label className="block text-xs text-[#6B7280] mb-1">Category</label>
            <select
              value={category}
              onChange={e => setCategory(e.target.value as ReverseChargeCategory)}
              className="w-full border rounded px-3 py-1.5 text-sm bg-background"
            >
              <option value="">Select a category…</option>
              {CATEGORIES.map(c => <option key={c.value} value={c.value}>{c.label}</option>)}
            </select>
          </div>
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <div>
            <label className="block text-xs text-[#6B7280] mb-1">Invoice date</label>
            <input
              type="date" value={invoiceDate} onChange={e => setInvoiceDate(e.target.value)}
              className="w-full border rounded px-3 py-1.5 text-sm bg-background"
            />
          </div>
          <div>
            <label className="block text-xs text-[#6B7280] mb-1">Foreign currency</label>
            <input
              value={foreignCurrency}
              onChange={e => setForeignCurrency(e.target.value.toUpperCase().slice(0, 3))}
              placeholder="USD" maxLength={3}
              className="w-full border rounded px-3 py-1.5 text-sm bg-background uppercase"
            />
          </div>
          <div>
            <label className="block text-xs text-[#6B7280] mb-1">Foreign amount</label>
            <input
              type="number" step="0.01" value={foreignAmount}
              onChange={e => setForeignAmount(e.target.value)}
              placeholder="0.00"
              className="w-full border rounded px-3 py-1.5 text-sm bg-background text-right"
            />
          </div>
          <div>
            <label className="block text-xs text-[#6B7280] mb-1">BWP amount paid</label>
            <input
              type="number" step="0.01" value={bwpAmount}
              onChange={e => setBwpAmount(e.target.value)}
              placeholder="0.00"
              className="w-full border rounded px-3 py-1.5 text-sm bg-background text-right font-medium"
            />
          </div>
        </div>

        {/* Auto-calculated preview — greyed / read-only. Server recomputes
            authoritatively from settings.RC_VAT_RATE on save. */}
        <div className="grid grid-cols-2 gap-3 rounded-lg p-3" style={{ background: `${NAVY}0D` }}>
          <div>
            <label className="block text-xs text-[#6B7280] mb-1">Output VAT (14%, auto)</label>
            <div
              className="w-full rounded px-3 py-1.5 text-sm text-right font-semibold tabular-nums bg-[#F3F4F6] text-[#6B7280] cursor-not-allowed"
              aria-readonly="true"
            >
              BWP {money(outputVatPreview)}
            </div>
          </div>
          <div>
            <label className="block text-xs text-[#6B7280] mb-1">Input VAT recoverable</label>
            <input
              type="number" step="0.01"
              value={recoverableTouched ? inputRecoverable : money(outputVatPreview)}
              onChange={e => { setRecoverableTouched(true); setInputRecoverable(e.target.value) }}
              className="w-full border rounded px-3 py-1.5 text-sm bg-background text-right font-medium"
              style={recoverableTouched ? { borderColor: ORANGE } : undefined}
            />
          </div>
          <div className="col-span-2 pt-2" style={{ borderTop: `1px dashed ${NAVY}33` }}>
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold uppercase tracking-wider text-[#6B7280]">
                Net VAT cost
              </span>
              <span
                className="text-sm font-bold tabular-nums"
                style={{ color: netVatCostPreview === 0 ? '#059669' : '#B45309' }}
              >
                BWP {money(netVatCostPreview)}
              </span>
            </div>
          </div>
        </div>

        {isOther && (
          <div>
            <label className="block text-xs text-[#6B7280] mb-1">
              Note <span style={{ color: '#B45309' }}>&mdash; required for &quot;Other&quot;</span>
            </label>
            <textarea
              value={note} onChange={e => setNote(e.target.value)}
              rows={2} placeholder="What is this imported remote service?"
              className="w-full border rounded px-3 py-1.5 text-sm bg-background"
            />
          </div>
        )}

        {err && <p className="text-xs text-red-700">{err}</p>}

        <div className="flex justify-end gap-2 pt-1">
          <Button onClick={submit} loading={busy} disabled={!canSubmit}>
            <Plus className="w-4 h-4 mr-1" /> Save entry
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
