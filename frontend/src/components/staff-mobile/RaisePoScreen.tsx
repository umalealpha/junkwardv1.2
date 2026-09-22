'use client'

/** Raise a purchase order from the phone. Drives the SAME endpoints as the
 * desktop /purchase-orders/new page (frontend/src/app/(dashboard)/purchase-orders/new/page.tsx):
 *   GET  /me/companies/                          entity list (company is required by the serializer)
 *   GET  /contacts/?contact_type=vendor&search=  server-side vendor search (~1,000 vendors, page cap 100)
 *   GET  /currencies/                            funding currencies; BWP is always first (CFO 2026-05-21)
 *   GET  /exchange-rates/latest/?from_currency=X&to_currency=BWP  approved BoB rate for a foreign PO
 *   POST /purchase-orders/                       create (same payload shape as desktop)
 *   POST /purchase-orders/<id>/submit/           send for FM + CFO approval
 * Totals shown after saving are the SERVER's figures echoed back — nothing is
 * computed here. Excess / claims logic is the server's; this form has none. */
import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { Plus, Send, X } from 'lucide-react'
import { reauthOn401, sfetch } from '@/app/(customer)/api'
import { C, serif } from '@/app/(customer)/ui'
import { useStaffBase } from './useStaffBase'
import {
  Card, RetryBanner, ScreenFrame, ServerMessage, Toast, errText, formatServerErrors,
  ghostBtn, inputStyle, labelStyle, primaryBtn, rawStaffFetch,
} from './StaffFormKit'
import { localYmd } from '@/lib/utils'

interface MeCompany { id: string; code: string; name: string; base_currency: string; is_active: boolean; can_write: boolean }
interface Vendor { id: string; name: string; currency_code?: string }
interface Currency { code: string; name: string }
interface Line { description: string; quantity: string; unit_price: string }
interface PoCreated {
  id: string; po_number: string; status: string; status_display: string
  currency_code: string; subtotal: string; tax_total: string; total_amount: string; total_bwp: string
}
type Department = 'admin' | 'claims' | 'hr'

const today = () => localYmd(new Date())
const blankLine = (): Line => ({ description: '', quantity: '1', unit_price: '' })
const PRIORITY = ['BWP', 'ZAR', 'USD']

export default function RaisePoScreen() {
  const base = useStaffBase()
  const [companies, setCompanies] = useState<MeCompany[] | null>(null)
  const [currencies, setCurrencies] = useState<Currency[]>([])
  const [loadErr, setLoadErr] = useState<string | null>(null)

  const [companyId, setCompanyId] = useState('')
  const [department, setDepartment] = useState<Department>('admin')
  const [vendorQuery, setVendorQuery] = useState('')
  const [vendors, setVendors] = useState<Vendor[]>([])
  const [vendorErr, setVendorErr] = useState<string | null>(null)
  const [supplier, setSupplier] = useState('')
  const [currencyCode, setCurrencyCode] = useState('BWP')
  const [exchangeRate, setExchangeRate] = useState('1.00000000')
  const [rateNote, setRateNote] = useState<string | null>(null)
  const [lines, setLines] = useState<Line[]>([blankLine()])
  const [justification, setJustification] = useState('')
  const [relatedClaim, setRelatedClaim] = useState('')

  const [busy, setBusy] = useState(false)
  const [serverErr, setServerErr] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  const [created, setCreated] = useState<PoCreated | null>(null)
  const [submitWarn, setSubmitWarn] = useState<string | null>(null)
  const show = (m: string) => { setToast(m); setTimeout(() => setToast(null), 4000) }

  const load = useCallback(() => {
    setLoadErr(null)
    Promise.all([
      sfetch<{ companies: MeCompany[] }>('/me/companies/'),
      sfetch<{ results: Currency[] }>('/currencies/').catch(() => ({ results: [] as Currency[] })),
    ]).then(([me, cur]) => {
      const list = (me.companies || []).filter(c => c.is_active !== false)
      setCompanies(list)
      // Prefer the main insurer (ADIC) like the desktop payment form does.
      const preferred = list.find(c => c.code === 'ADIC') || list[0]
      if (preferred) {
        setCompanyId(prev => prev || preferred.id)
        // Default currency from the company; BWP stays first in the list.
        setCurrencyCode(prev => (prev === 'BWP' && preferred.base_currency) ? preferred.base_currency : prev)
      }
      const sorted = (cur.results || []).slice().sort((a, b) => {
        const ia = PRIORITY.indexOf(a.code); const ib = PRIORITY.indexOf(b.code)
        if (ia !== -1 || ib !== -1) { if (ia === -1) return 1; if (ib === -1) return -1; return ia - ib }
        return a.code.localeCompare(b.code)
      })
      setCurrencies(sorted.length ? sorted : [{ code: 'BWP', name: 'Botswana Pula' }])
    }).catch(e => {
      if (reauthOn401(e)) return
      setCompanies([])
      setLoadErr(errText(e, 'Could not load your companies.'))
    })
  }, [])
  useEffect(() => { load() }, [load])

  // Server-side vendor search, debounced — the same query the desktop runs.
  useEffect(() => {
    const q = vendorQuery.trim()
    const t = setTimeout(() => {
      const params = new URLSearchParams({ contact_type: 'vendor', page_size: '100', ...(q ? { search: q } : {}) })
      sfetch<{ results: Vendor[] }>(`/contacts/?${params.toString()}`)
        .then(r => { setVendors(r.results || []); setVendorErr(null) })
        .catch(e => { if (!reauthOn401(e)) setVendorErr(errText(e, 'Could not search vendors.')) })
    }, 300)
    return () => clearTimeout(t)
  }, [vendorQuery])

  // Same rule as desktop (bug c9355408): a foreign currency auto-loads the
  // approved BoB rate so 1.0 never silently understates the BWP value.
  const onCurrencyChange = async (code: string) => {
    setCurrencyCode(code)
    if (code === 'BWP') { setExchangeRate('1.00000000'); setRateNote(null); return }
    setRateNote('Loading latest approved rate…')
    try {
      const qs = new URLSearchParams({ from_currency: code, to_currency: 'BWP' })
      const r = await sfetch<{ rate: string | null; effective_date: string | null }>(`/exchange-rates/latest/?${qs.toString()}`)
      if (r.rate) {
        setExchangeRate(r.rate)
        setRateNote(`Auto-filled from the approved BoB rate${r.effective_date ? ` (${r.effective_date})` : ''}. Reviewed at FM/CFO approval.`)
      } else {
        setRateNote(`No approved ${code}/BWP rate on file — enter the rate; it is reviewed at approval.`)
      }
    } catch (e) {
      if (!reauthOn401(e)) setRateNote('Could not load the latest rate — enter it by hand.')
    }
  }

  const setLine = (i: number, patch: Partial<Line>) => setLines(ls => ls.map((l, ix) => ix === i ? { ...l, ...patch } : l))
  const addLine = () => setLines(ls => [...ls, blankLine()])
  const removeLine = (i: number) => setLines(ls => ls.length > 1 ? ls.filter((_, ix) => ix !== i) : ls)

  async function submit() {
    setServerErr(null); setSubmitWarn(null)
    if (!companyId) { show('Pick the company first.'); return }
    if (!supplier) { show('Pick a supplier.'); return }
    const filled = lines.filter(l => l.description.trim())
    if (!filled.length) { show('Add at least one line.'); return }
    if (!justification.trim()) { show('Say why we are buying this — every PO needs a reason.'); return }
    setBusy(true)
    try {
      // Identical payload to the desktop New PO page (no GL account on lines —
      // CFO 2026-05-21; tax_code null = no VAT, the server owns the arithmetic).
      const payload = {
        department, supplier, company: companyId,
        issue_date: today(), expected_delivery_date: null,
        currency_code: currencyCode, exchange_rate: exchangeRate,
        discount_percent: 0, related_claim_reference: relatedClaim.trim(),
        justification: justification.trim(),
        lines: filled.map(l => ({ description: l.description.trim(), quantity: l.quantity, unit_price: l.unit_price, tax_code: null })),
      }
      const r = await rawStaffFetch('/purchase-orders/', { method: 'POST', body: JSON.stringify(payload) })
      if (!r.ok) { setServerErr(formatServerErrors(r.body, r.status)); return }
      const po = r.body as unknown as PoCreated
      // Created as Draft; now send it for approval — the desktop does both in one tap.
      const s = await rawStaffFetch(`/purchase-orders/${po.id}/submit/`, { method: 'POST' })
      if (s.ok) setCreated(s.body as unknown as PoCreated)
      else { setCreated(po); setSubmitWarn(`PO ${po.po_number} was saved as a draft but could not be sent for approval: ${formatServerErrors(s.body, s.status)}`) }
    } catch (e) {
      if (!reauthOn401(e)) setServerErr(errText(e, 'Could not create the PO.'))
    } finally { setBusy(false) }
  }

  const company = companies?.find(c => c.id === companyId)

  if (created) return (
    <ScreenFrame title="Raise a PO" base={base}>
      <div style={{ textAlign: 'center', padding: '24px 8px 8px' }}>
        <p style={{ fontSize: 52, margin: '0 0 8px' }}>✅</p>
        <h2 style={{ fontFamily: serif, fontWeight: 800, fontSize: 24, color: C.ink, margin: 0 }}>{created.po_number}</h2>
        <p style={{ color: C.inkSoft, fontSize: 14, lineHeight: 1.6, margin: '10px 0 0' }}>
          {created.status_display || created.status}{company ? ` · ${company.name}` : ''}
        </p>
      </div>
      <Card>
        <Row k="Subtotal" v={`${created.currency_code} ${created.subtotal}`} />
        <Row k="VAT" v={`${created.currency_code} ${created.tax_total}`} />
        <Row k="Total" v={`${created.currency_code} ${created.total_amount}`} strong />
        {created.currency_code !== 'BWP' && <Row k="≈ BWP" v={`BWP ${created.total_bwp}`} />}
        <p style={{ margin: '10px 0 0', fontSize: 11.5, color: C.inkSoft }}>Figures as saved by Omni.</p>
      </Card>
      {submitWarn && <ServerMessage text={submitWarn} tone="warn" />}
      <Link href={`${base}/approvals`} style={{ ...primaryBtn(false), textDecoration: 'none' }}>Go to Approvals</Link>
      <button onClick={() => { setCreated(null); setLines([blankLine()]); setJustification(''); setRelatedClaim(''); setSupplier('') }} style={ghostBtn}>Raise another</button>
    </ScreenFrame>
  )

  return (
    <ScreenFrame title="Raise a PO" base={base}>
      {loadErr && <RetryBanner message={loadErr} onRetry={load} />}
      <Card>
        <p style={{ color: C.inkSoft, fontSize: 13, margin: 0, lineHeight: 1.5 }}>Creates the PO and sends it for FM + CFO approval — the same as Omni on your computer.</p>

        <label htmlFor="po-company" style={labelStyle}>Company</label>
        <select id="po-company" value={companyId} onChange={e => setCompanyId(e.target.value)} style={inputStyle} disabled={companies === null}>
          {companies === null && <option value="">Loading…</option>}
          {companies && companies.length === 0 && <option value="">No company available</option>}
          {companies?.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>

        <label style={labelStyle}>Department</label>
        <div style={{ display: 'flex', gap: 8 }}>
          {([['admin', 'Admin'], ['claims', 'Claims'], ['hr', 'HR']] as const).map(([v, l]) => (
            <button key={v} onClick={() => setDepartment(v)}
              style={{ flex: 1, minHeight: 44, borderRadius: 999, border: 'none', fontWeight: 700, fontSize: 13, cursor: 'pointer',
                background: department === v ? C.navy : '#fff', color: department === v ? '#fff' : C.inkSoft, boxShadow: department === v ? 'none' : `inset 0 0 0 1px ${C.line}` }}>
              {l}
            </button>
          ))}
        </div>

        <label htmlFor="po-supplier" style={labelStyle}>Supplier</label>
        <input value={vendorQuery} onChange={e => setVendorQuery(e.target.value)} placeholder="Type to search vendors…" aria-label="Search vendors" style={{ ...inputStyle, marginBottom: 8 }} />
        <select id="po-supplier" value={supplier} onChange={e => setSupplier(e.target.value)} style={inputStyle}>
          <option value="">{vendors.length ? '— Choose supplier —' : vendorErr ? '— Search unavailable —' : '— No match —'}</option>
          {vendors.map(v => <option key={v.id} value={v.id}>{v.name}</option>)}
        </select>
        {vendorErr && <p style={{ margin: '6px 0 0', fontSize: 12, color: '#B91C1C' }}>{vendorErr}</p>}

        <div style={{ display: 'flex', gap: 10 }}>
          <div style={{ flex: 1 }}>
            <label htmlFor="po-currency" style={labelStyle}>Currency</label>
            <select id="po-currency" value={currencyCode} onChange={e => onCurrencyChange(e.target.value)} style={inputStyle}>
              {currencies.map(c => <option key={c.code} value={c.code}>{c.code}</option>)}
              {!currencies.some(c => c.code === currencyCode) && <option value={currencyCode}>{currencyCode}</option>}
            </select>
          </div>
          {currencyCode !== 'BWP' && (
            <div style={{ flex: 1.4 }}>
              <label htmlFor="po-rate" style={labelStyle}>Rate to BWP</label>
              <input id="po-rate" value={exchangeRate} onChange={e => setExchangeRate(e.target.value)} inputMode="decimal" style={inputStyle} />
            </div>
          )}
        </div>
        {rateNote && <p style={{ margin: '6px 0 0', fontSize: 12, color: C.inkSoft }}>{rateNote}</p>}
      </Card>

      <Card>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <b style={{ color: C.ink, fontSize: 15 }}>Lines</b>
          <button onClick={addLine} style={{ ...ghostBtn, display: 'flex', alignItems: 'center', gap: 6, color: '#B45309' }}><Plus size={14} /> Add line</button>
        </div>
        {lines.map((l, i) => (
          <div key={i} style={{ marginTop: 12, paddingTop: 12, borderTop: `1px solid ${C.line}` }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <input value={l.description} onChange={e => setLine(i, { description: e.target.value })} placeholder={`Line ${i + 1} — what you're buying`} aria-label={`Line ${i + 1} description`} style={{ ...inputStyle, flex: 1 }} />
              {lines.length > 1 && (
                <button onClick={() => removeLine(i)} aria-label="Remove line" style={{ width: 44, height: 44, borderRadius: 12, border: 'none', background: '#F0F2F5', color: C.inkSoft, display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer' }}><X size={16} /></button>
              )}
            </div>
            <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
              <input value={l.quantity} onChange={e => setLine(i, { quantity: e.target.value.replace(/[^0-9.]/g, '') })} inputMode="decimal" placeholder="Qty" aria-label={`Line ${i + 1} quantity`} style={{ ...inputStyle, flex: 1 }} />
              <input value={l.unit_price} onChange={e => setLine(i, { unit_price: e.target.value.replace(/[^0-9.\-]/g, '') })} inputMode="decimal" placeholder={`Unit price (${currencyCode})`} aria-label={`Line ${i + 1} unit price (${currencyCode})`} style={{ ...inputStyle, flex: 2 }} />
            </div>
          </div>
        ))}
        <p style={{ margin: '12px 0 0', fontSize: 11.5, color: C.inkSoft, lineHeight: 1.5 }}>Omni works out the totals and VAT when it saves the PO — you will see its figures on the next screen.</p>
      </Card>

      <Card>
        <label htmlFor="po-why" style={{ ...labelStyle, marginTop: 0 }}>Why are we buying this? · required</label>
        <textarea id="po-why" value={justification} onChange={e => setJustification(e.target.value)} rows={3}
          placeholder="Reason for the PO — and for claims, the client name, vehicle/policy reg and contact."
          style={{ ...inputStyle, resize: 'vertical' }} />
        <label htmlFor="po-claim" style={labelStyle}>Related claim ref (optional)</label>
        <input id="po-claim" value={relatedClaim} onChange={e => setRelatedClaim(e.target.value)} placeholder="e.g. CLM-2026-001234" style={inputStyle} />
      </Card>

      {serverErr && <ServerMessage text={serverErr} tone="error" />}
      <button onClick={submit} disabled={busy} style={primaryBtn(busy)}>
        <Send size={16} /> {busy ? 'Creating…' : 'Create & send for approval'}
      </button>
      <Toast text={toast} />
    </ScreenFrame>
  )
}

function Row({ k, v, strong }: { k: string; v: string; strong?: boolean }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 0', fontSize: strong ? 16 : 14, fontWeight: strong ? 800 : 500, color: C.ink }}>
      <span style={{ color: strong ? C.ink : C.inkSoft }}>{k}</span><span style={{ fontVariantNumeric: 'tabular-nums' }}>{v}</span>
    </div>
  )
}
