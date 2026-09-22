'use client'

import { useEffect, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import Link from 'next/link'
import {
  createPurchaseOrder, updatePurchaseOrder, getPurchaseOrder,
  getContacts, getCurrencies, getLatestExchangeRate, getToken,
  getTaxRates, submitPO, getSupplierPOHistory,
} from '@/lib/api'
import type { CreatePOInput, CreatePOLineInput, Contact, Currency, TaxRate, SupplierHistoryItem } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { ArrowLeft, Plus, X, AlertCircle, Send, FilePlus, GripVertical, ChevronUp, ChevronDown } from 'lucide-react'
import { useCompany } from '@/contexts/CompanyContext'
import { localYmd } from '@/lib/utils'

const today = () => localYmd(new Date())

export default function NewPOPage() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const editId = searchParams.get('edit')          // amend an existing PO in place
  const copyId = searchParams.get('copy')          // clone an approved PO to a NEW draft (add extras)
  const prefillId = editId || copyId
  const { selectedId: companyId, selected: selectedCompany } = useCompany()

  const [suppliers, setSuppliers]   = useState<Contact[]>([])
  const [currencies, setCurrencies] = useState<Currency[]>([])
  const [taxRates, setTaxRates]     = useState<TaxRate[]>([])

  const [department, setDepartment]   = useState<CreatePOInput['department']>('admin')
  const [supplier, setSupplier]       = useState('')
  const [supplierQuery, setSupplierQuery] = useState('')
  const [issueDate, setIssueDate]     = useState(today())
  const [expectedDate, setExpectedDate] = useState('')
  // CFO directive 2026-05-21 — BWP is ALWAYS the first currency of choice on a
  // PO. ZAR / USD / others are picked manually from the dropdown when the
  // PO is raised on a non-BWP supplier. Do not auto-switch off the entity.
  const [currencyCode, setCurrencyCode] = useState('BWP')
  const [exchangeRate, setExchangeRate] = useState('1.00000000')
  // bug c9355408 — auto-load the real approved BoB rate when a foreign currency
  // is picked, so the rate never silently stays 1.0 (which let a requester
  // understate a PO's BWP value and skip the P5,000 approval threshold).
  const [rateNote, setRateNote] = useState<string | null>(null)

  const onCurrencyChange = async (code: string) => {
    setCurrencyCode(code)
    if (code === 'BWP') {
      setExchangeRate('1.00000000')
      setRateNote(null)
      return
    }
    setRateNote('Loading latest approved rate…')
    try {
      const r = await getLatestExchangeRate(code, 'BWP')
      if (r.rate) {
        setExchangeRate(r.rate)
        setRateNote(`Auto-filled from approved BoB rate${r.effective_date ? ` (${r.effective_date})` : ''}. Override is reviewed at FM/CFO approval.`)
      } else {
        setRateNote(`No approved ${code}/BWP rate on file — enter the rate manually; it is reviewed at approval.`)
      }
    } catch {
      setRateNote('Could not load the latest rate — enter it manually.')
    }
  }
  const [relatedClaim, setRelatedClaim] = useState('')
  const [justification, setJustification] = useState('')
  const [dragIdx, setDragIdx] = useState<number | null>(null)
  const [dragArmed, setDragArmed] = useState<number | null>(null)   // drag only from the grip handle
  // PO discount (CFO directive 2026-07-08). One % off every line before VAT.
  const [discountPercent, setDiscountPercent] = useState(0)
  const [lines, setLines] = useState<CreatePOLineInput[]>([
    { description: '', account: '', quantity: 1, unit_price: 0, tax_code: '' },
  ])
  // Recent items from the chosen supplier — tap to reuse (CFO 2026-07-14).
  const [supplierHistory, setSupplierHistory] = useState<SupplierHistoryItem[]>([])
  const [historyOpen, setHistoryOpen] = useState(true)

  const [submitting, setSubmitting] = useState(false)
  const [error, setError]           = useState<string | null>(null)
  const [loadingPO, setLoadingPO]   = useState(!!prefillId)

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    // CFO directive 2026-05-21 — POs are no longer linked to a GL line, so
    // the Account dropdown is gone. We still pull the currency list so the
    // currency selector can offer every funding currency the group raises
    // POs in (BWP, ZAR, USD, plus everything else from the Currency table).
    Promise.all([
      // Initial list is only the first page (API caps page_size at 100). With
      // ~1,000 vendors the dropdown alone can't show them all, so the search
      // box below queries the server by name — that's how any vendor is found.
      getContacts({ contact_type: 'vendor', page_size: '100' }).catch(() => null),
      getCurrencies().catch(() => null),
      // VAT: PO lines carry tax_code (backend ready) — Kao 2026-07-06 "unable to
      // add VAT". Load active tax rates so each line can pick VAT (e.g. 14%).
      getTaxRates().catch(() => null),
    ]).then(([s, c, t]) => {
      setSuppliers(s?.results || [])
      setTaxRates((t?.results || []).filter((r) => r.is_active))
      // Sort currencies: BWP first, then ZAR, USD (the three operating
      // currencies the CFO flagged), then everything else alphabetical.
      const PRIORITY = ['BWP', 'ZAR', 'USD']
      const list = (c?.results || []).slice().sort((a: Currency, b: Currency) => {
        const ia = PRIORITY.indexOf(a.code); const ib = PRIORITY.indexOf(b.code)
        if (ia !== -1 || ib !== -1) {
          if (ia === -1) return 1
          if (ib === -1) return -1
          return ia - ib
        }
        return a.code.localeCompare(b.code)
      })
      setCurrencies(list)
    })
  }, [router])

  // Server-side supplier search — the API caps a page at 100, so filtering the
  // dropdown client-side could never reach a vendor past the first 100. Query
  // the server by name (debounced) so all ~1,000 vendors are reachable.
  useEffect(() => {
    const q = supplierQuery.trim()
    const t = setTimeout(() => {
      getContacts({ contact_type: 'vendor', page_size: '100', ...(q ? { search: q } : {}) })
        .then((s) => setSuppliers(s?.results || []))
        .catch(() => {})
    }, 300)
    return () => clearTimeout(t)
  }, [supplierQuery])

  // Pull this supplier's recent line items whenever the picked supplier changes,
  // scoped to the selected entity. Advisory quick-fill; never blocks the form.
  useEffect(() => {
    if (!supplier) { setSupplierHistory([]); return }
    let cancelled = false
    getSupplierPOHistory(supplier, companyId || undefined)
      .then((items) => { if (!cancelled) setSupplierHistory(items) })
      .catch(() => { if (!cancelled) setSupplierHistory([]) })
    return () => { cancelled = true }
  }, [supplier, companyId])

  const addLine = () => setLines([...lines, { description: '', account: '', quantity: 1, unit_price: 0, tax_code: '' }])
  const removeLine = (i: number) => setLines(lines.filter((_, ix) => ix !== i))
  // Reorder lines (Kao 2026-07-08: place a new line above the Excess line).
  const moveLine = (from: number, to: number) => {
    if (to < 0 || to >= lines.length || from === to) return
    const copy = [...lines]; const [m] = copy.splice(from, 1); copy.splice(to, 0, m); setLines(copy)
  }
  const updateLine = (i: number, key: keyof CreatePOLineInput, value: string | number) => {
    const copy = [...lines]; (copy[i] as unknown as Record<string, unknown>)[key] = value; setLines(copy)
  }
  // Reuse a past item: fill the first blank line, else append a new one. The
  // user still reviews qty/price before submitting (advisory, not automatic).
  const addFromHistory = (h: SupplierHistoryItem) => {
    const line: CreatePOLineInput = {
      description: h.description, account: '',
      quantity: 1, unit_price: Number(h.unit_price) || 0,
      tax_code: h.tax_code || '',
    }
    setLines((cur) => {
      const blankIdx = cur.findIndex((l) => !l.description && !(Number(l.unit_price) || 0))
      if (blankIdx >= 0) { const copy = [...cur]; copy[blankIdx] = line; return copy }
      return [...cur, line]
    })
  }

  // VAT per line via the chosen tax_code (rate % from the TaxRate list). Excess
  // is entered as a normal line with a NEGATIVE unit price (it nets down the total).
  const rateOf = (tax_code?: string | null) => {
    const r = taxRates.find((t) => t.id === tax_code)
    return r ? (Number(r.rate) || 0) : 0
  }
  // Mirror the backend exactly: quantize each line, net the PO discount off the
  // gross line BEFORE VAT, then sum. r2 = round to 2 dp like the server.
  const r2 = (n: number) => Math.round((n + Number.EPSILON) * 100) / 100
  const dpct = Number(discountPercent) || 0
  const lineNet = (ln: CreatePOLineInput) => r2((Number(ln.quantity) || 0) * (Number(ln.unit_price) || 0)) // gross per line
  const lineDisc = (ln: CreatePOLineInput) => r2(lineNet(ln) * dpct / 100)
  const lineTax = (ln: CreatePOLineInput) => r2((lineNet(ln) - lineDisc(ln)) * rateOf(ln.tax_code) / 100)
  const grossSubtotal = lines.reduce((s, ln) => s + lineNet(ln), 0)
  const discountTotal = lines.reduce((s, ln) => s + lineDisc(ln), 0)
  const subtotal = grossSubtotal - discountTotal // net of discount (matches backend subtotal)
  const taxTotal = lines.reduce((s, ln) => s + lineTax(ln), 0)
  const grandTotal = subtotal + taxTotal
  const totalBwp = grandTotal * (Number(exchangeRate) || 1)

  // Amend mode (CFO directive 2026-07-08): prefill the form from the existing
  // PO so a mistake can be corrected. Backend permits this for DRAFT + pending;
  // approved POs are blocked and the detail page won't offer the Edit button.
  useEffect(() => {
    if (!prefillId) return
    let cancelled = false
    getPurchaseOrder(prefillId)
      .then((po) => {
        if (cancelled) return
        setDepartment((po.department as CreatePOInput['department']) || 'admin')
        setSupplier(po.supplier || '')
        setSupplierQuery(po.supplier_name || '')
        setIssueDate(po.issue_date || today())
        setExpectedDate(po.expected_delivery_date || '')
        setCurrencyCode(po.currency_code || 'BWP')
        setExchangeRate(String(po.exchange_rate ?? '1.00000000'))
        setRelatedClaim(po.related_claim_reference || '')
        setJustification(po.justification || '')
        setDiscountPercent(Number(po.discount_percent) || 0)
        const ls = (po.lines || []).map((l) => ({
          description: l.description || '',
          account: '',
          quantity: Number(l.quantity) || 1,
          unit_price: Number(l.unit_price) || 0,
          tax_code: l.tax_code || '',
        }))
        setLines(ls.length ? ls : [{ description: '', account: '', quantity: 1, unit_price: 0, tax_code: '' }])
      })
      .catch((e) => setError(e instanceof Error ? e.message : 'Could not load the PO to edit.'))
      .finally(() => { if (!cancelled) setLoadingPO(false) })
    return () => { cancelled = true }
  }, [prefillId])

  const createPO = async (alsoSubmit: boolean) => {
    setError(null)
    if (!companyId) {
      setError('Pick a company from the top-right entity switcher first.')
      return
    }
    if (!supplier) { setError('Pick a supplier.'); return }
    if (lines.length === 0 || lines.every((l) => !l.description)) {
      setError('Add at least one line.')
      return
    }
    // CFO directive 2026-05-21 — no GL-account requirement on PO lines.
    setSubmitting(true)
    try {
      // Amend an existing PO in place (PATCH) when in edit mode.
      if (editId) {
        const payload = {
          department, supplier,
          company: companyId,
          issue_date: issueDate,
          expected_delivery_date: expectedDate || null,
          currency_code: currencyCode,
          exchange_rate: exchangeRate,
          discount_percent: discountPercent,
          related_claim_reference: relatedClaim,
          justification,
          lines: lines.filter((l) => l.description).map((l) => ({
            description: l.description,
            quantity: l.quantity,
            unit_price: l.unit_price,
            tax_code: l.tax_code || null,
          })),
        }
        const updated = await updatePurchaseOrder(editId, payload)
        if (alsoSubmit) {
          try { await submitPO(updated.id) }
          catch (e) {
            setError(e instanceof Error
              ? `PO saved but re-submit failed: ${e.message}`
              : 'PO saved but re-submit failed.')
          }
        }
        router.replace(`/purchase-orders/${updated.id}`)
        return
      }
      // CFO directive 2026-05-21 (PO docx): the backend serializer
      // requires `company`. Previously the New PO page never sent it
      // and submission threw "company is required". Always carry the
      // selected entity through.
      const po = await createPurchaseOrder({
        department, supplier,
        company: companyId,
        issue_date: issueDate,
        expected_delivery_date: expectedDate || null,
        currency_code: currencyCode,
        exchange_rate: exchangeRate,
        discount_percent: discountPercent,
        related_claim_reference: relatedClaim,
        justification,
        // CFO directive 2026-05-21 — strip the legacy `account` field so
        // the serializer treats every line as un-mapped to GL.
        lines: lines
          .filter((l) => l.description)
          .map((l) => ({
            description: l.description,
            quantity: l.quantity,
            unit_price: l.unit_price,
            tax_code: l.tax_code || null,
          })),
      })
      if (alsoSubmit) {
        try { await submitPO(po.id) }
        catch (e) {
          // PO created but submit failed — still route to detail so the
          // user can fix and retry from there.
          setError(e instanceof Error
            ? `PO created but submit failed: ${e.message}`
            : 'PO created but submit failed.')
        }
      }
      router.replace(`/purchase-orders/${po.id}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to create PO')
    } finally {
      setSubmitting(false)
    }
  }
  const onCreateDraft = () => createPO(false)
  const onCreateAndSubmit = () => createPO(true)

  return (
    <div className="min-h-screen bg-[#F8F9FA]">
      <TopBar
        title={editId ? 'Amend Purchase Order' : copyId ? 'Revise — new PO from an approved one' : 'New Purchase Order'}
        subtitle={selectedCompany
          ? `${selectedCompany.name} · ${currencyCode} · ${editId ? 'Correct a PO before approval' : copyId ? 'Add extras — this creates a NEW PO to approve' : 'Create + Submit for FM + CFO approval'}`
          : 'Pick an entity from the top-right switcher to begin'}
      />

      <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-6 space-y-4">
        <Link href="/purchase-orders" className="text-sm text-gray-600 hover:text-gray-800 inline-flex items-center gap-1">
          <ArrowLeft className="w-4 h-4" /> Back to list
        </Link>

        {error && (
          <Card className="border-red-200 bg-red-50">
            <CardContent className="p-3 flex items-start gap-2">
              <AlertCircle className="w-4 h-4 text-red-700 mt-0.5" />
              <span className="text-sm text-red-700">{error}</span>
            </CardContent>
          </Card>
        )}

        <Card>
          <CardContent className="p-6 space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <Field label="Department">
                <select value={department} onChange={(e) => setDepartment(e.target.value as CreatePOInput['department'])} className="input">
                  <option value="admin">Admin</option>
                  <option value="claims">Claims</option>
                  <option value="hr">Human Resources</option>
                </select>
              </Field>
              <Field label="Supplier (type to filter)">
                <input
                  type="text"
                  value={supplierQuery}
                  onChange={(e) => setSupplierQuery(e.target.value)}
                  placeholder="Start typing the supplier name…"
                  className="input mb-1"
                />
                <select value={supplier} onChange={(e) => setSupplier(e.target.value)} className="input">
                  <option value="">{suppliers.length ? '— Choose supplier —' : '— No match —'}</option>
                  {suppliers.map((s) => (
                    <option key={s.id} value={s.id}>{s.name}</option>
                  ))}
                </select>
              </Field>
              <Field label="Related claim ref (optional)">
                <input value={relatedClaim} onChange={(e) => setRelatedClaim(e.target.value)} placeholder="e.g. CLM-2026-001234" className="input" />
              </Field>

              <Field label="Issue date">
                <input type="date" value={issueDate} onChange={(e) => setIssueDate(e.target.value)} className="input" />
              </Field>
              <Field label="Expected delivery">
                <input type="date" value={expectedDate} onChange={(e) => setExpectedDate(e.target.value)} className="input" />
              </Field>
              <Field label="Currency (BWP default, switch for ZAR / USD POs)">
                <select value={currencyCode} onChange={(e) => onCurrencyChange(e.target.value)} className="input">
                  {currencies.map((c) => (
                    <option key={c.code} value={c.code}>
                      {c.code}{c.name ? ` — ${c.name}` : ''}
                    </option>
                  ))}
                </select>
              </Field>

              <Field label="Exchange rate (to BWP)">
                <input type="text" value={exchangeRate} onChange={(e) => setExchangeRate(e.target.value)} className="input" />
                {rateNote && (
                  <p className="mt-1 text-xs text-gray-500">{rateNote}</p>
                )}
              </Field>
              <div className="md:col-span-2">
                <Field label="Why are we buying this? · required">
                  <textarea value={justification} onChange={(e) => setJustification(e.target.value)} rows={3} className="input"
                    placeholder="Reason for the PO — and for claims, the client details: client name, vehicle/policy reg #, contact." />
                  {!justification.trim() && (
                    <p className="mt-1 text-xs text-[#B45309]">
                      Every purchase order needs a reason, at any value. It cannot be
                      added later — an approved purchase order can no longer be edited.
                    </p>
                  )}
                </Field>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-6">
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-medium">Lines</h3>
              <Button size="sm" variant="outline" onClick={addLine}>
                <Plus className="w-4 h-4 mr-1" /> Add line
              </Button>
            </div>
            {/* PO discount (CFO directive 2026-07-08) — one % off every line
                before VAT. Quick 5% / 10%, or type any custom %. */}
            <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
              <span className="text-gray-600 font-medium">Discount:</span>
              {[0, 5, 10].map((v) => (
                <button type="button" key={v} onClick={() => setDiscountPercent(v)}
                  className={`px-3 py-1 rounded border ${Number(discountPercent) === v
                    ? 'bg-[#1D3270] text-white border-[#1D3270]'
                    : 'border-gray-300 text-gray-700 hover:border-[#1D3270]'}`}>
                  {v === 0 ? 'None' : `${v}%`}
                </button>
              ))}
              <span className="text-gray-400">or</span>
              <input type="number" min="0" max="100" step="0.5" value={discountPercent}
                onChange={(e) => setDiscountPercent(Math.min(100, Math.max(0, Number(e.target.value) || 0)))}
                className="input w-24 text-right" aria-label="Custom discount percent" />
              <span className="text-gray-500">% off (before VAT)</span>
            </div>
            {/* Reuse past items from this supplier (CFO 2026-07-14) — tap to
                add, so common orders don't get retyped every time. */}
            {supplierHistory.length > 0 && (
              <div className="mb-3 rounded-lg border border-[#E5E7EB] bg-[#F8F9FB] p-3">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-semibold text-[#0D1B2A]">
                    Recent items from this supplier — tap to add
                  </span>
                  <button type="button" onClick={() => setHistoryOpen((o) => !o)}
                    className="text-xs text-gray-500 hover:text-gray-700">
                    {historyOpen ? 'Hide' : 'Show'}
                  </button>
                </div>
                {historyOpen && (
                  <div className="flex flex-wrap gap-2">
                    {supplierHistory.map((h, i) => (
                      <button type="button" key={i} onClick={() => addFromHistory(h)}
                        title={`Last on ${h.last_po_number}${h.last_issue_date ? ` (${h.last_issue_date})` : ''} — review price before submitting`}
                        className="text-left px-2.5 py-1.5 rounded-md border border-gray-300 bg-white hover:border-[#1D3270] hover:bg-[#1D3270]/5 max-w-[220px]">
                        <span className="block text-xs font-medium text-[#0D1B2A] truncate">{h.description}</span>
                        <span className="block text-[10px] font-mono text-gray-500">last unit price {Number(h.unit_price).toFixed(2)}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
            {/* CFO directive 2026-05-21 — Account column removed. The GL
                line is captured on the bill that matches this PO, not on
                the PO itself. */}
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase text-gray-600 border-b border-gray-200">
                <tr>
                  <th className="py-2 w-16"></th>
                  <th className="py-2">Description</th>
                  <th className="py-2 text-right">Qty</th>
                  <th className="py-2 text-right">Unit price</th>
                  <th className="py-2 text-right">VAT</th>
                  <th className="py-2 text-right">Line total</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {lines.map((ln, i) => (
                  <tr key={i}
                    draggable={dragArmed === i}
                    onDragStart={(e) => { e.dataTransfer.setData('text/plain', String(i)); e.dataTransfer.effectAllowed = 'move'; setDragIdx(i) }}
                    onDragOver={(e) => { if (dragIdx !== null) e.preventDefault() }}
                    onDrop={(e) => { e.preventDefault(); if (dragIdx !== null) moveLine(dragIdx, i); setDragIdx(null); setDragArmed(null) }}
                    onDragEnd={() => { setDragIdx(null); setDragArmed(null) }}
                    className={`border-b border-gray-100 ${dragIdx === i ? 'opacity-40' : ''}`}>
                    <td className="py-2 pr-1 align-middle">
                      <div className="flex items-center gap-1 text-gray-400">
                        {/* Drag is armed only from this handle, so text-selection
                            inside the line inputs still works (Fable audit). */}
                        <GripVertical
                          className="w-4 h-4 cursor-grab active:cursor-grabbing"
                          aria-label="Drag to reorder"
                          onMouseDown={() => setDragArmed(i)}
                          onMouseUp={() => setDragArmed(null)} />
                        <div className="flex flex-col">
                          <button type="button" onClick={() => moveLine(i, i - 1)} disabled={i === 0}
                            className="hover:text-[#0D1B2A] disabled:opacity-25" aria-label="Move up"><ChevronUp className="w-3 h-3" /></button>
                          <button type="button" onClick={() => moveLine(i, i + 1)} disabled={i === lines.length - 1}
                            className="hover:text-[#0D1B2A] disabled:opacity-25" aria-label="Move down"><ChevronDown className="w-3 h-3" /></button>
                        </div>
                      </div>
                    </td>
                    <td className="py-2 pr-2">
                      <input value={ln.description}
                        onChange={(e) => updateLine(i, 'description', e.target.value)}
                        className="input" placeholder="What you're buying" />
                    </td>
                    <td className="py-2 pr-2 w-24">
                      <input type="number" step="0.0001" value={ln.quantity}
                        onChange={(e) => updateLine(i, 'quantity', e.target.value)}
                        className="input text-right" />
                    </td>
                    <td className="py-2 pr-2 w-32">
                      <input type="number" step="0.01" value={ln.unit_price}
                        onChange={(e) => updateLine(i, 'unit_price', e.target.value)}
                        className="input text-right" placeholder="− for excess" />
                    </td>
                    <td className="py-2 pr-2 w-36">
                      <select value={ln.tax_code || ''} onChange={(e) => updateLine(i, 'tax_code', e.target.value)} className="input">
                        <option value="">No VAT</option>
                        {taxRates.map((t) => (
                          <option key={t.id} value={t.id}>{(t.name || t.tax_code)} ({Number(t.rate).toFixed(0)}%)</option>
                        ))}
                      </select>
                    </td>
                    <td className="py-2 pr-2 text-right font-mono">
                      {lineNet(ln).toFixed(2)}
                      {rateOf(ln.tax_code) > 0 && (
                        <span className="block text-[10px] text-gray-400">+{lineTax(ln).toFixed(2)} VAT</span>
                      )}
                    </td>
                    <td className="py-2">
                      <button type="button" onClick={() => removeLine(i)} className="text-gray-400 hover:text-red-600">
                        <X className="w-4 h-4" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot className="text-sm">
                <tr className="border-t border-gray-200">
                  <td colSpan={5} className="py-2 text-right text-gray-600">Subtotal ({currencyCode}):</td>
                  <td className="py-2 text-right font-mono">{grossSubtotal.toFixed(2)}</td>
                  <td></td>
                </tr>
                {dpct > 0 && (
                  <tr>
                    <td colSpan={5} className="py-1 text-right text-gray-600">Discount ({dpct}%) ({currencyCode}):</td>
                    <td className="py-1 text-right font-mono text-red-600">−{discountTotal.toFixed(2)}</td>
                    <td></td>
                  </tr>
                )}
                <tr>
                  <td colSpan={5} className="py-1 text-right text-gray-600">VAT ({currencyCode}):</td>
                  <td className="py-1 text-right font-mono">{taxTotal.toFixed(2)}</td>
                  <td></td>
                </tr>
                <tr>
                  <td colSpan={5} className="py-2 text-right font-medium">Total ({currencyCode}):</td>
                  <td className="py-2 text-right font-mono font-semibold">{grandTotal.toFixed(2)}</td>
                  <td></td>
                </tr>
                {currencyCode !== 'BWP' && (
                  <tr>
                    <td colSpan={5} className="py-1 text-right text-gray-600">≈ BWP equivalent:</td>
                    <td className="py-1 text-right font-mono text-gray-700">BWP {totalBwp.toFixed(2)}</td>
                    <td></td>
                  </tr>
                )}
              </tfoot>
            </table>
            <p className="mt-2 text-xs text-gray-500">
              Pick <b>VAT</b> per line (e.g. 14%) — the tax shows in the total above. To record an{' '}
              <b>excess</b> (client contribution deducted), add a line such as &ldquo;Less: excess&rdquo; with a <b>negative</b> unit price.
            </p>
          </CardContent>
        </Card>

        {editId && (
          <p className="text-xs text-gray-500">
            Amending a PO that was already submitted returns it to <b>Draft</b> and
            voids any in-flight approval — it must be re-submitted so the corrected
            figures are re-approved.
          </p>
        )}
        {copyId && (
          <p className="text-xs text-gray-500">
            This is a <b>revision</b> — a brand-new PO copied from the approved one so
            you can add extras. The original approved PO is unchanged; cancel it if
            this revision replaces it. This new PO goes through approval on its own.
          </p>
        )}
        <div className="flex flex-wrap justify-end gap-3">
          <Link href={editId ? `/purchase-orders/${editId}` : '/purchase-orders'}><Button variant="outline">Cancel</Button></Link>
          <Button
            variant="outline"
            onClick={onCreateDraft}
            disabled={submitting || loadingPO}
            className="border-[#0D1B2A] text-[#0D1B2A] hover:bg-[#F3F4F6]"
          >
            <FilePlus className="w-4 h-4 mr-1" />
            {submitting ? 'Saving...' : editId ? 'Save changes (keep Draft)' : 'Create as Draft'}
          </Button>
          <Button
            onClick={onCreateAndSubmit}
            disabled={submitting || loadingPO || !justification.trim()}
            title={!justification.trim()
              ? 'Say why this is being bought before sending it for approval'
              : undefined}
            className="bg-[#0D1B2A] hover:bg-[#1a2940] text-white"
          >
            <Send className="w-4 h-4 mr-1" />
            {submitting
              ? (editId ? 'Saving...' : 'Creating...')
              : editId ? 'Save & Re-submit for Approval' : 'Create & Submit for Approval'}
          </Button>
        </div>
      </div>

      <style jsx>{`
        .input {
          width: 100%;
          padding: 0.5rem 0.75rem;
          border: 1px solid #d1d5db;
          border-radius: 0.375rem;
          font-size: 0.875rem;
          background: white;
        }
      `}</style>
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-xs font-medium text-gray-600 mb-1">{label}</label>
      {children}
    </div>
  )
}
