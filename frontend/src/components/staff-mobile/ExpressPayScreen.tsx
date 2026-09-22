'use client'

/** Express Pay — the CFO or CEO loads ONE payment to FNB from the phone with a
 * single authorisation (CFO 2026-09-04). Backend: fnb/express_pay.py.
 *   GET  /fnb/express-pay/can/                 am I allowed? (shows / hides the tile)
 *   GET  /fnb/express-pay/history/?q=&from=&to=&amount=   old payments (requests + earlier loads) → "Pay again"
 *   GET  /fnb/claim-payee/?claim=              who we last paid on a claim
 *   POST /payment-requests/read-invoice/       snap → fields (field name `file`)
 *   POST /fnb/express-pay/                     stage it to FNB; 409 needs_confirm → re-post with confirm:true
 *   GET  /fnb/express-pay/<id>/status/         loaded / paid / failed / unknown
 *   GET/POST /fnb/express-pay/payees/  DELETE /fnb/express-pay/payees/<id>/   saved "click and pay" list
 * Omni moves no money. This puts the payment in FNB's queue; it leaves the bank
 * only when you release it in the FNB app with your phone. */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Camera, Clock3, History, FileSearch, Star, X, Zap } from 'lucide-react'
import { compressImage, reauthOn401, sfetch } from '@/app/(customer)/api'
import { C, serif } from '@/app/(customer)/ui'
import { useStaffBase } from './useStaffBase'
import {
  Card, ScreenFrame, ServerMessage, Toast, errText, formatServerErrors,
  ghostBtn, inputStyle, labelStyle, primaryBtn, rawStaffFetch, type JsonBody,
} from './StaffFormKit'

interface Past { source: string; id: string; ref: string; date: string; payee: string; amount: string; currency: string; reference: string; status: string; category: 'operations' | 'claim'; company: string; account_number: string; bank_name: string; branch_code: string }
interface Saved { id: string; name: string; bank_name: string; account_number: string; branch_code: string; default_amount: string; reference: string; company: string; category: 'operations' | 'claim'; last_paid_at: string | null }
interface ClaimPayee { found: boolean; payee?: string; last_paid?: string; account_number?: string; bank_name?: string; branch_code?: string; request_ref?: string; detail?: string }
interface MeCompany { id: string; code: string; name: string }
interface Warning { control: string; detail: string }
interface Loaded { batch_id: string; reference: string; status: string; amount: string; payee: string; company: string; next: string }
interface Unknown { reason: 'unknown'; batch_id: string | null; reference: string; detail: string }
interface StatusResp { status: 'loaded' | 'paid' | 'failed' | 'unknown'; failure_reason: string; reference: string }
interface Form { payee: string; bank: string; account: string; branch: string; amount: string; reference: string; company: string; category: 'operations' | 'claim' }

const EMPTY: Form = { payee: '', bank: 'FNB', account: '', branch: '', amount: '', reference: '', company: 'ADIC', category: 'operations' }
const fmt = (s: string) => { const n = Number(String(s).replace(/[,\s]/g, '')); return Number.isFinite(n) && n > 0 ? n.toLocaleString('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '' }

/** Chrome/Android only: read a QR off the photo. Anything that looks like an
 * account number, an amount or a name is offered as a prefill; the AI read
 * below covers everything else (and every iPhone). Never throws. */
async function readQr(file: File): Promise<Partial<Form> | null> {
  try {
    const W = window as unknown as { BarcodeDetector?: new (o: { formats: string[] }) => { detect: (i: ImageBitmap) => Promise<{ rawValue: string }[]> } }
    if (!W.BarcodeDetector) return null
    const det = new W.BarcodeDetector({ formats: ['qr_code'] })
    const codes = await det.detect(await createImageBitmap(file))
    const raw = codes[0]?.rawValue
    if (!raw) return null
    const out: Partial<Form> = {}
    try {
      const j = JSON.parse(raw) as Record<string, unknown>
      const pick = (...ks: string[]) => { for (const k of ks) { const v = j[k]; if (typeof v === 'string' || typeof v === 'number') return String(v) } return '' }
      const got: Record<string, string> = { payee: pick('payee', 'payee_name', 'beneficiary', 'name'), account: pick('account', 'account_number', 'acc'), amount: pick('amount', 'total'), bank: pick('bank', 'bank_name') }
      for (const [k, v] of Object.entries(got)) if (v) (out as Record<string, string>)[k] = v
    } catch {
      const acc = raw.match(/\b\d{9,13}\b/); const amt = raw.match(/(?:BWP|P)\s?([\d,]+\.\d{2})/i)
      if (acc) out.account = acc[0]; if (amt) out.amount = amt[1]
    }
    return Object.values(out).some(Boolean) ? out : null
  } catch { return null }
}

export default function ExpressPayScreen() {
  const base = useStaffBase()
  const [allowed, setAllowed] = useState<boolean | null>(null)
  const [f, setF] = useState<Form>(EMPTY)
  const [companies, setCompanies] = useState<MeCompany[]>([])
  const [mode, setMode] = useState<'none' | 'history' | 'claim'>('none')
  const [past, setPast] = useState<Past[] | null>(null)
  const [saved, setSaved] = useState<Saved[]>([])
  const [saveNext, setSaveNext] = useState(false)       // "save this payee for next time" on load
  const [manage, setManage] = useState(false)
  const [q, setQ] = useState('')
  const [hFrom, setHFrom] = useState('')
  const [hTo, setHTo] = useState('')
  const [hAmount, setHAmount] = useState('')
  const [claimNo, setClaimNo] = useState('')
  const [claimNote, setClaimNote] = useState<string | null>(null)
  const [reading, setReading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [warnings, setWarnings] = useState<Warning[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const [unknown, setUnknown] = useState<Unknown | null>(null)
  const [status, setStatus] = useState<StatusResp | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  const camRef = useRef<HTMLInputElement | null>(null)
  const set = (patch: Partial<Form>) => setF(x => ({ ...x, ...patch }))
  const show = (m: string) => { setToast(m); setTimeout(() => setToast(null), 4000) }

  useEffect(() => {
    sfetch<{ allowed: boolean }>('/fnb/express-pay/can/').then(r => setAllowed(!!r.allowed))
      .catch(e => { if (!reauthOn401(e)) setAllowed(false) })
    sfetch<{ companies: MeCompany[] }>('/me/companies/').then(r => setCompanies(r.companies || [])).catch(() => {})
  }, [])
  const loadSaved = useCallback(() => {
    sfetch<{ payees: Saved[] }>('/fnb/express-pay/payees/').then(r => setSaved(r.payees || [])).catch(() => {})
  }, [])
  useEffect(() => { if (allowed) loadSaved() }, [allowed, loadSaved])

  const pickSaved = (x: Saved) => {
    set({ payee: x.name, bank: x.bank_name || 'FNB', account: x.account_number, branch: x.branch_code || '', amount: x.default_amount || '', reference: x.reference || '', company: x.company || 'ADIC', category: x.category || 'operations' })
    setMode('none'); setSaveNext(false)
    show(x.default_amount ? `${x.name} filled — check the amount, then load` : `${x.name} filled — type the amount`)
  }
  const removeSaved = async (x: Saved) => {
    if (!window.confirm(`Remove ${x.name} from your saved list?`)) return
    try {
      const r = await rawStaffFetch(`/fnb/express-pay/payees/${x.id}/`, { method: 'DELETE' })
      if (r.ok || r.status === 204) setSaved(list => list.filter(y => y.id !== x.id)); else show(formatServerErrors(r.body, r.status))
    } catch (e) { if (!reauthOn401(e)) show(errText(e, 'Could not remove.')) }
  }
  const savePayee = async () => {
    const r = await rawStaffFetch('/fnb/express-pay/payees/', { method: 'POST', body: JSON.stringify({
      name: f.payee.trim(), bank_name: f.bank.trim(), account_number: f.account.trim(), branch_code: f.branch.trim(),
      default_amount: f.amount.replace(/[,\s]/g, ''), reference: f.reference.trim(), company: f.company, category: f.category }) })
    if (!r.ok) throw new Error(formatServerErrors(r.body, r.status))
    loadSaved()
  }

  const loadHistory = useCallback((query: string, from: string, to: string, amount: string) => {
    const p = new URLSearchParams()
    if (query) p.set('q', query); if (from) p.set('from', from); if (to) p.set('to', to)
    if (amount.replace(/[,\s]/g, '')) p.set('amount', amount.replace(/[,\s]/g, ''))
    const qs = p.toString()
    sfetch<{ payments: Past[] }>(`/fnb/express-pay/history/${qs ? `?${qs}` : ''}`)
      .then(r => setPast(r.payments || [])).catch(e => { if (!reauthOn401(e)) setPast([]) })
  }, [])
  useEffect(() => {
    if (mode !== 'history') return
    const t = window.setTimeout(() => loadHistory(q, hFrom, hTo, hAmount), q || hAmount ? 350 : 0)   // debounce typing
    return () => window.clearTimeout(t)
  }, [mode, q, hFrom, hTo, hAmount, loadHistory])

  // Poll the batch after a load: "Paid" once FNB settles it, otherwise waiting on you.
  useEffect(() => {
    if (!loaded) return
    let stop = false
    const tick = async () => {
      try {
        const s = await sfetch<StatusResp>(`/fnb/express-pay/${loaded.batch_id}/status/`)
        if (!stop) setStatus(s)
        if (s.status === 'paid' || s.status === 'failed') return
      } catch { /* keep polling */ }
      if (!stop) t = window.setTimeout(tick, 20_000)
    }
    let t = window.setTimeout(tick, 4_000)
    return () => { stop = true; window.clearTimeout(t) }
  }, [loaded])

  const pickPast = (x: Past) => {
    set({ payee: x.payee, account: x.account_number || '', bank: x.bank_name || 'FNB', branch: x.branch_code || '', amount: x.amount || '', reference: x.reference || '', category: x.category || 'operations', ...(x.company && companies.some(c => c.code === x.company) ? { company: x.company } : {}) })
    setMode('none'); setSaveNext(false)
    show(x.account_number ? `Filled from ${x.ref} — check the amount, then load` : `${x.payee} filled — no bank on that record, type the account`)
  }

  const lookupClaim = async () => {
    if (claimNo.trim().length < 3) { setClaimNote('Type at least 3 characters of the claim number.'); return }
    setClaimNote(null)
    try {
      const r = await sfetch<ClaimPayee>(`/fnb/claim-payee/?claim=${encodeURIComponent(claimNo.trim())}`)
      if (!r.found) { setClaimNote(r.detail || 'Omni has not paid anyone on that claim yet. Type the payee by hand.'); return }
      set({ payee: r.payee || '', account: r.account_number || '', bank: r.bank_name || 'FNB', branch: r.branch_code || '', reference: claimNo.trim(), category: 'claim' })
      setMode('none'); show(`Payee filled from ${r.request_ref}${r.last_paid ? ` (last paid BWP ${fmt(r.last_paid)}) — type this payment's amount` : ''}`)
    } catch (e) { if (!reauthOn401(e)) setClaimNote(errText(e, 'Could not look that up.')) }
  }

  const onSnap = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const raw = e.target.files?.[0]; e.target.value = ''
    if (!raw) return
    setReading(true); setNote(null)
    try {
      const qr = await readQr(raw)
      if (qr) { set(qr); setNote('Read from the QR code on the invoice — check every field.'); return }
      const fd = new FormData(); fd.append('file', await compressImage(raw))
      const r = await sfetch<{ ok?: boolean; message?: string; fields?: Record<string, string> }>('/payment-requests/read-invoice/', { method: 'POST', body: fd }, false)
      const x = r.fields || {}
      if (r.ok === false || !Object.keys(x).length) { setNote(r.message || 'Could not read that invoice — type it in.'); return }
      set({ payee: x.payee_name || f.payee, amount: x.total_amount || f.amount, account: x.account_number || f.account, bank: x.bank_name || f.bank, branch: x.branch_code || f.branch, reference: x.invoice_number || f.reference })
      setNote('Filled from the invoice — check every field before you load it.')
    } catch (e2) { if (!reauthOn401(e2)) setNote(errText(e2, 'Could not read that invoice.')) }
    finally { setReading(false) }
  }

  async function load(confirm: boolean) {
    setBusy(true); setErr(null)
    const body: JsonBody = { payee_name: f.payee.trim(), account_number: f.account.trim(), bank_name: f.bank.trim(), branch_code: f.branch.trim(), amount: f.amount.replace(/[,\s]/g, ''), reference: f.reference.trim(), company: f.company, category: f.category, confirm }
    try {
      const r = await rawStaffFetch('/fnb/express-pay/', { method: 'POST', body: JSON.stringify(body) })
      if (r.status === 409 && r.body.needs_confirm) { setWarnings((r.body.warnings as Warning[]) || []); return }
      if (r.body.reason === 'unknown') { setWarnings(null); setUnknown(r.body as unknown as Unknown); return }
      if (!r.ok) { setWarnings(null); setErr(formatServerErrors(r.body, r.status)); return }
      setWarnings(null); setLoaded(r.body as unknown as Loaded)
      if (saveNext && !alreadySaved) { try { await savePayee() } catch (e2) { show(`Payment loaded. Saving ${f.payee.trim()} for next time failed: ${errText(e2, 'try again from the form')}`) } }
    } catch (e) { if (!reauthOn401(e)) setErr(errText(e, 'Could not reach Omni.')) }
    finally { setBusy(false) }
  }

  const amountOk = Number(f.amount.replace(/[,\s]/g, '')) > 0
  const alreadySaved = saved.some(x => x.account_number === f.account.replace(/\D/g, ''))
  const ready = f.payee.trim() && f.account.replace(/\D/g, '').length >= 6 && f.bank.trim() && amountOk

  if (allowed === false) return (
    <ScreenFrame title="Express Pay" base={base}>
      <ServerMessage tone="error" text="Express Pay is for the CFO and the CEO only. Raise a payment request instead — it goes through finance sign-off." />
    </ScreenFrame>
  )

  if (unknown) return (
    <ScreenFrame title="Express Pay" base={base}>
      <div style={{ textAlign: 'center', padding: '24px 8px 8px' }}>
        <p style={{ fontSize: 52, margin: '0 0 8px' }}>⚠️</p>
        <h2 style={{ fontFamily: serif, fontWeight: 800, fontSize: 22, color: C.ink, margin: 0 }}>Check FNB before you try again</h2>
        <p style={{ color: C.ink, fontSize: 18, fontWeight: 800, margin: '10px 0 0', fontVariantNumeric: 'tabular-nums' }}>BWP {fmt(f.amount)} → {f.payee}</p>
      </div>
      <ServerMessage tone="warn" text={`${unknown.detail}${unknown.reference ? ` Reference ${unknown.reference}.` : ''}`} />
      <button onClick={() => setUnknown(null)} style={ghostBtn}>Back</button>
    </ScreenFrame>
  )

  if (loaded) {
    const paid = status?.status === 'paid'; const failed = status?.status === 'failed'
    return (
      <ScreenFrame title="Express Pay" base={base}>
        <div style={{ textAlign: 'center', padding: '24px 8px 8px' }}>
          <p style={{ fontSize: 52, margin: '0 0 8px' }}>{paid ? '✅' : failed ? '⛔' : '🏦'}</p>
          <h2 style={{ fontFamily: serif, fontWeight: 800, fontSize: 22, color: C.ink, margin: 0 }}>
            {paid ? 'Paid' : failed ? 'FNB rejected it' : 'Loaded to FNB'}
          </h2>
          <p style={{ color: C.ink, fontSize: 18, fontWeight: 800, margin: '10px 0 0', fontVariantNumeric: 'tabular-nums' }}>BWP {fmt(loaded.amount)} → {loaded.payee}</p>
          <p style={{ color: C.inkSoft, fontSize: 14, lineHeight: 1.6, margin: '10px 0 0' }}>
            {paid ? 'FNB has settled it.' : failed ? (status?.failure_reason || 'See the reason in Omni → Banking → FNB.') : `${loaded.next} Reference ${loaded.reference}.`}
          </p>
          {!paid && !failed && <p style={{ color: C.inkSoft, fontSize: 12.5, margin: '8px 0 0', display: 'inline-flex', alignItems: 'center', gap: 6 }}><Clock3 size={14} /> Waiting for your approval in the FNB app — this page updates itself.</p>}
        </div>
        <ServerMessage tone="ok" text="Omni has moved no money. It leaves the bank only when you approve the batch in the FNB app." />
        <button onClick={() => { setLoaded(null); setStatus(null); setUnknown(null); setF(EMPTY); setWarnings(null); setSaveNext(false) }} style={primaryBtn(false)}><Zap size={18} /> Pay someone else</button>
      </ScreenFrame>
    )
  }

  return (
    <ScreenFrame title="Express Pay" base={base}>
      <input ref={camRef} type="file" accept="image/*,.pdf" capture="environment" style={{ display: 'none' }} onChange={onSnap} />
      <p style={{ margin: 0, fontSize: 13, color: C.inkSoft, lineHeight: 1.5 }}>One payment, straight into FNB&apos;s queue, your approval only. You still release it in the FNB app.</p>

      {saved.length > 0 && (
        <Card style={{ padding: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <b style={{ flex: 1, color: C.ink, fontSize: 14, display: 'inline-flex', alignItems: 'center', gap: 6 }}><Star size={15} color={C.orange} /> Saved payees</b>
            <button onClick={() => setManage(m => !m)} style={{ ...ghostBtn, minHeight: 36, padding: '6px 12px', fontSize: 12 }}>{manage ? 'Done' : 'Edit list'}</button>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', marginTop: 4 }}>
            {saved.map(x => (
              <div key={x.id} style={{ display: 'flex', alignItems: 'center', gap: 8, borderTop: `1px solid ${C.line}` }}>
                <button onClick={() => pickSaved(x)} style={{ flex: 1, minHeight: 48, textAlign: 'left', background: 'none', border: 'none', padding: '8px 2px', cursor: 'pointer' }}>
                  <b style={{ color: C.ink, fontSize: 14.5, display: 'block' }}>{x.name}</b>
                  <span style={{ color: C.inkSoft, fontSize: 12.5 }}>{x.bank_name} ···{x.account_number.slice(-4)}{x.default_amount ? ` · BWP ${fmt(x.default_amount)}` : ''}{x.reference ? ` · ${x.reference}` : ''}</span>
                </button>
                {manage && <button onClick={() => removeSaved(x)} aria-label={`Remove ${x.name}`} style={{ ...ghostBtn, minWidth: 44, padding: 0, color: '#B91C1C' }}><X size={16} /></button>}
              </div>
            ))}
          </div>
        </Card>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8 }}>
        <Quick icon={Camera} label={reading ? 'Reading…' : 'Snap invoice'} onClick={() => camRef.current?.click()} disabled={reading || allowed === null} />
        <Quick icon={History} label="Pay again" onClick={() => setMode(m => m === 'history' ? 'none' : 'history')} active={mode === 'history'} disabled={allowed === null} />
        <Quick icon={FileSearch} label="From a claim" onClick={() => setMode(m => m === 'claim' ? 'none' : 'claim')} active={mode === 'claim'} disabled={allowed === null} />
      </div>
      {note && <ServerMessage text={note} tone="ok" />}

      {mode === 'history' && (
        <Card style={{ padding: 14 }}>
          <label style={{ ...labelStyle, marginTop: 0 }} htmlFor="xp-q">Search old payments</label>
          <input id="xp-q" value={q} onChange={e => setQ(e.target.value)} placeholder="Name or reference" style={inputStyle} />
          <div style={{ display: 'flex', gap: 8 }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <label style={labelStyle} htmlFor="xp-hfrom">From</label>
              <input id="xp-hfrom" type="date" value={hFrom} onChange={e => setHFrom(e.target.value)} style={inputStyle} />
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <label style={labelStyle} htmlFor="xp-hto">To</label>
              <input id="xp-hto" type="date" value={hTo} onChange={e => setHTo(e.target.value)} style={inputStyle} />
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <label style={labelStyle} htmlFor="xp-hamt">Amount</label>
              <input id="xp-hamt" value={hAmount} onChange={e => setHAmount(e.target.value)} inputMode="decimal" placeholder="exact" style={inputStyle} />
            </div>
          </div>
          <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column' }}>
            {past === null && <p style={{ color: C.inkSoft, fontSize: 13, margin: '6px 0' }}>Loading…</p>}
            {past?.length === 0 && <p style={{ color: C.inkSoft, fontSize: 13, margin: '6px 0' }}>No payment matches.</p>}
            {past?.map(x => (
              <button key={`${x.source}-${x.id}`} onClick={() => pickPast(x)} style={{ minHeight: 48, textAlign: 'left', background: 'none', border: 'none', borderTop: `1px solid ${C.line}`, padding: '10px 2px', cursor: 'pointer' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'baseline' }}>
                  <b style={{ color: C.ink, fontSize: 14, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{x.payee}</b>
                  <span style={{ color: C.ink, fontSize: 14, fontWeight: 800, whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' }}>BWP {fmt(x.amount)}</span>
                </div>
                <span style={{ color: C.inkSoft, fontSize: 12.5 }}>{new Date(`${x.date}T00:00:00`).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })} · {x.reference || x.ref}{x.account_number ? ` · ···${x.account_number.slice(-4)}` : ' · no bank on record'}</span>
              </button>
            ))}
            {past && past.length >= 50 && <p style={{ color: C.inkSoft, fontSize: 12, margin: '6px 0 0' }}>Showing the latest 50. Narrow the search to see older ones.</p>}
          </div>
        </Card>
      )}

      {mode === 'claim' && (
        <Card style={{ padding: 14 }}>
          <label style={{ ...labelStyle, marginTop: 0 }} htmlFor="xp-claim">Claim number</label>
          <div style={{ display: 'flex', gap: 8 }}>
            <input id="xp-claim" value={claimNo} onChange={e => setClaimNo(e.target.value)} placeholder="e.g. CLM-2026-0042" style={inputStyle} />
            <button onClick={lookupClaim} style={ghostBtn}>Find</button>
          </div>
          {claimNote && <p style={{ color: '#92400E', fontSize: 12.5, margin: '8px 0 0', lineHeight: 1.5 }}>{claimNote}</p>}
        </Card>
      )}

      <Card style={{ padding: 14 }}>
        <label style={{ ...labelStyle, marginTop: 0 }} htmlFor="xp-payee">Payee</label>
        <input id="xp-payee" value={f.payee} onChange={e => set({ payee: e.target.value })} autoComplete="off" style={inputStyle} />
        <label style={labelStyle} htmlFor="xp-bank">Bank</label>
        <input id="xp-bank" value={f.bank} onChange={e => set({ bank: e.target.value })} style={inputStyle} />
        <div style={{ display: 'flex', gap: 8 }}>
          <div style={{ flex: 3 }}>
            <label style={labelStyle} htmlFor="xp-acc">Account number</label>
            <input id="xp-acc" value={f.account} onChange={e => set({ account: e.target.value })} inputMode="numeric" autoComplete="off" style={inputStyle} />
          </div>
          <div style={{ flex: 2 }}>
            <label style={labelStyle} htmlFor="xp-branch">Branch code</label>
            <input id="xp-branch" value={f.branch} onChange={e => set({ branch: e.target.value })} inputMode="numeric" placeholder="blank if FNB" style={inputStyle} />
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <div style={{ flex: 1 }}>
            <label style={labelStyle} htmlFor="xp-amt">Amount (BWP)</label>
            <input id="xp-amt" value={f.amount} onChange={e => set({ amount: e.target.value })} inputMode="decimal" placeholder="0.00" style={{ ...inputStyle, textAlign: 'right', fontWeight: 800 }} />
          </div>
          <div style={{ flex: 1 }}>
            <label style={labelStyle} htmlFor="xp-ref">Reference</label>
            <input id="xp-ref" value={f.reference} onChange={e => set({ reference: e.target.value })} placeholder="invoice / claim no." style={inputStyle} />
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <div style={{ flex: 1 }}>
            <label style={labelStyle} htmlFor="xp-co">Paying company</label>
            <select id="xp-co" value={f.company} onChange={e => set({ company: e.target.value })} style={inputStyle}>
              {!companies.some(c => c.code === f.company) && <option value={f.company}>{f.company}</option>}
              {companies.map(c => <option key={c.id} value={c.code}>{c.code} — {c.name}</option>)}
            </select>
          </div>
          <div style={{ flex: 1 }}>
            <label style={labelStyle} htmlFor="xp-cat">Pay from</label>
            <select id="xp-cat" value={f.category} onChange={e => set({ category: e.target.value as Form['category'] })} style={inputStyle}>
              <option value="operations">Operating account</option>
              <option value="claim">Claims account</option>
            </select>
          </div>
        </div>
      </Card>

      {warnings && (
        <Card style={{ padding: 14, borderColor: '#FCD34D', background: '#FFFBEB' }}>
          <b style={{ color: '#92400E', fontSize: 14, display: 'block', marginBottom: 6 }}>Read this before you load it</b>
          {warnings.map(w => <ServerMessage key={w.control} control={w.control} text={w.detail} />)}
          <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
            <button onClick={() => load(true)} disabled={busy} style={{ ...primaryBtn(busy), flex: 2 }}>Yes, load it</button>
            <button onClick={() => setWarnings(null)} style={{ ...ghostBtn, flex: 1 }}>Change it</button>
          </div>
        </Card>
      )}
      {err && <ServerMessage tone="error" text={err} />}

      {!warnings && !alreadySaved && (
        <label style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 14, color: C.ink, minHeight: 44, cursor: 'pointer' }}>
          <input type="checkbox" checked={saveNext} onChange={e => setSaveNext(e.target.checked)} style={{ width: 22, height: 22, accentColor: C.orange }} />
          Save {f.payee.trim() || 'this payee'} for next time (one tap to pay again)
        </label>
      )}
      {!warnings && (
        <button onClick={() => load(false)} disabled={busy || !ready} style={{ ...primaryBtn(busy || !ready), minHeight: 56, fontSize: 16 }}>
          <Zap size={20} /> {amountOk ? `Load to FNB · BWP ${fmt(f.amount)}` : 'Load to FNB'}
        </button>
      )}
      <p style={{ margin: 0, fontSize: 12, color: C.inkSoft, textAlign: 'center', lineHeight: 1.5 }}>Omni moves no money. You approve the payment in the FNB app after this.</p>
      <Toast text={toast} />
    </ScreenFrame>
  )
}

function Quick({ icon: Icon, label, onClick, active, disabled }: { icon: typeof Camera; label: string; onClick: () => void; active?: boolean; disabled?: boolean }) {
  return (
    <button onClick={onClick} disabled={disabled} style={{ minHeight: 64, borderRadius: 14, border: `2px ${active ? 'solid' : 'dashed'} ${C.orange}`, background: active ? '#FFEDD5' : '#FFF7ED', color: '#B45309', fontWeight: 800, fontSize: 12.5, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 4, cursor: disabled ? 'default' : 'pointer', opacity: disabled ? 0.6 : 1 }}>
      <Icon size={20} /> {label}
    </button>
  )
}
