'use client'

/** /app/docs — "Quotes & certificates" (CFO 2026-09-04). Describe it in one
 * sentence, check the fields Omni read, issue the document and email the PDF
 * to the client — all from the phone. Drives the SAME endpoints as the desktop
 * underwriting tool; nothing is rebuilt and nothing here computes money:
 *   GET  /me/companies/                         entity picker (shown only when > 1)
 *   GET  /underwriting/quotes/templates/        class-of-business picker
 *   POST /underwriting/parse-text/              {text, doctype} → fields (cn/cnfi/wca) or a quote draft
 *   POST /underwriting/render/                  {doctype, fmt, fields} → PDF preview
 *   POST /underwriting/issue/                   {doctype, fmt, fields, email_to, company} → stored + emailed
 *   GET  /underwriting/documents/{id}/pdf/      the stored certificate
 *   POST /underwriting/quotes/?company=<id>     create the draft (with source_text)
 *   POST /underwriting/quotes/{id}/confirm-premium/  accept a suggested premium
 *   POST /underwriting/quotes/{id}/issue/       number + freeze the PDF (422 problems[] shown verbatim)
 *   POST /underwriting/quotes/{id}/email/       send the PDF to the client
 *   GET  /underwriting/quotes/{id}/pdf/         the stored quotation
 * VAT and totals on screen are the SERVER's strings, echoed back. */
import { useCallback, useEffect, useState } from 'react'
import { FileText, Mail, Plus, Send, X } from 'lucide-react'
import { ApiError, reauthOn401, sfetch, sfetchBlob, staffReauth, staffToken } from '@/app/(customer)/api'
import { C, serif } from '@/app/(customer)/ui'
import { useStaffBase } from './useStaffBase'
import {
  Card, RetryBanner, ScreenFrame, ServerMessage, Toast, errText, formatServerErrors,
  ghostBtn, inputStyle, labelStyle, primaryBtn, rawStaffFetch, type JsonBody,
} from './StaffFormKit'
import {
  CERT_FIELDS, CHIPS, DOC_LABEL, SIGNATORY_KEYS, WCA_LOOKS, blankSection, isDocType,
  readCertParse, readIssuedDoc, readQuoteParse, readQuoteRow, readStr, readStrings,
  type CertParse, type DocPick, type DocType, type IssuedDoc, type MeCompany, type QuoteParse,
  type QuoteRow, type QuoteTemplate, type Section, type WcaLook,
} from './docsTypes'

type Stage = 'describe' | 'check' | 'done'
const EXAMPLES = "e.g. 'Cover note for Thabo Motors, Toyota Hilux B123ABC, comprehensive, sum insured 320k, 4 Sep to 3 Oct' · 'WCA certificate for Kgalagadi Builders, 24 employees, annual earnings 1.9m, 1 Oct 2026 to 30 Sep 2027' · 'Quote: fleet of 6 bakkies…'"
const API = `${process.env.NEXT_PUBLIC_API_BASE ?? ''}/api/v1`

/** POST → PDF blob with the staff token (sfetchBlob is GET-only). */
async function postPdf(path: string, body: JsonBody): Promise<Blob> {
  const { token: t, app } = staffToken()
  const res = await fetch(`${API}${path}`, {
    method: 'POST', body: JSON.stringify(body),
    headers: { 'Content-Type': 'application/json', ...(t ? { Authorization: `Bearer ${t}` } : {}) },
  })
  if (res.status === 401) { staffReauth(app); throw new ApiError('Please sign in again.', 401) }
  if (!res.ok) {
    const j: unknown = await res.json().catch(() => ({}))
    throw new ApiError(formatServerErrors((j && typeof j === 'object' ? j : {}) as JsonBody, res.status), res.status)
  }
  return res.blob()
}
function openBlob(b: Blob) {
  const u = URL.createObjectURL(b)
  window.open(u, '_blank', 'noopener')
  setTimeout(() => URL.revokeObjectURL(u), 60_000)
}
const isEmail = (s: string) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(s)

export default function DocsScreen() {
  const base = useStaffBase()
  const [stage, setStage] = useState<Stage>('describe')
  const [companies, setCompanies] = useState<MeCompany[] | null>(null)
  const [companyId, setCompanyId] = useState('')
  const [templates, setTemplates] = useState<QuoteTemplate[]>([])
  const [loadErr, setLoadErr] = useState<string | null>(null)

  const [text, setText] = useState('')
  const [pick, setPick] = useState<DocPick>('auto')
  const [needsType, setNeedsType] = useState(false)

  const [cert, setCert] = useState<CertParse | null>(null)
  const [look, setLook] = useState<WcaLook>('orig')
  const [quote, setQuote] = useState<QuoteParse | null>(null)
  const [premium, setPremium] = useState('')
  const [premiumEdited, setPremiumEdited] = useState(false)
  const [premiumConfirmed, setPremiumConfirmed] = useState(false)
  const [agent, setAgent] = useState('')
  const [email, setEmail] = useState('')

  const [busy, setBusy] = useState(false)
  const [serverErr, setServerErr] = useState<string | null>(null)
  const [problems, setProblems] = useState<string[]>([])
  const [toast, setToast] = useState<string | null>(null)
  const show = (m: string) => { setToast(m); setTimeout(() => setToast(null), 4000) }

  const [issued, setIssued] = useState<IssuedDoc | null>(null)
  const [issuedQuote, setIssuedQuote] = useState<QuoteRow | null>(null)
  const [emailNote, setEmailNote] = useState<{ ok: boolean; text: string } | null>(null)

  const load = useCallback(() => {
    setLoadErr(null)
    Promise.all([
      sfetch<{ companies: MeCompany[] }>('/me/companies/'),
      sfetch<QuoteTemplate[]>('/underwriting/quotes/templates/').catch(() => [] as QuoteTemplate[]),
    ]).then(([me, tpl]) => {
      const list = (me.companies || []).filter(c => c.is_active !== false)
      setCompanies(list)
      const preferred = list.find(c => c.code === 'ADIC') || list[0]
      if (preferred) setCompanyId(prev => prev || preferred.id)
      setTemplates(Array.isArray(tpl) ? tpl : [])
    }).catch(e => {
      if (reauthOn401(e)) return
      setCompanies([])
      setLoadErr(errText(e, 'Could not load your companies.'))
    })
  }, [])
  useEffect(() => { load() }, [load])

  const doctype: DocType | null = quote ? 'quote' : cert ? cert.doctype : null

  // ── Describe → Check ───────────────────────────────────────────────────────
  async function parse() {
    setServerErr(null); setNeedsType(false)
    const t = text.trim()
    if (!t) { show('Describe what you need first.'); return }
    setBusy(true)
    try {
      const r = await rawStaffFetch('/underwriting/parse-text/', { method: 'POST', body: JSON.stringify({ text: t, doctype: pick }) })
      if (!r.ok) { setServerErr(formatServerErrors(r.body, r.status)); return }
      if (r.body.needs_doctype === true) { setNeedsType(true); return }
      const dt = r.body.doctype
      if (!isDocType(dt)) { setServerErr('Omni sent back something this screen did not understand.'); return }
      setCert(null); setQuote(null); setProblems([]); setEmailNote(null)
      setPremiumEdited(false); setPremiumConfirmed(false)
      if (dt === 'quote') {
        const q = readQuoteParse(r.body)
        setQuote(q); setPremium(q.premium === '0.00' ? '' : q.premium)
      } else {
        setCert(readCertParse(r.body, dt))
      }
      setStage('check')
    } catch (e) {
      if (!reauthOn401(e)) setServerErr(errText(e, 'Could not read that description.'))
    } finally { setBusy(false) }
  }

  // ── Check (certificate) ────────────────────────────────────────────────────
  const setField = (k: string, v: string) => setCert(c => c ? { ...c, fields: { ...c.fields, [k]: v } } : c)
  const certBody = (): JsonBody => ({ doctype: cert?.doctype, fmt: cert?.doctype === 'wca' ? look : 'orig', fields: cert?.fields ?? {} })

  async function preview() {
    if (!cert) return
    setServerErr(null); setBusy(true)
    try { openBlob(await postPdf('/underwriting/render/', certBody())) }
    catch (e) { if (!reauthOn401(e)) setServerErr(errText(e, 'Could not render the preview.')) }
    finally { setBusy(false) }
  }

  async function issueCert() {
    if (!cert) return
    setServerErr(null)
    const to = email.trim()
    if (to && !isEmail(to)) { show('That email address does not look right.'); return }
    setBusy(true)
    try {
      const r = await rawStaffFetch('/underwriting/issue/', { method: 'POST', body: JSON.stringify({ ...certBody(), email_to: to, company: companyId }) })
      if (!r.ok) { setServerErr(formatServerErrors(r.body, r.status)); return }
      const doc = readIssuedDoc(r.body)
      setIssued(doc)
      setEmailNote(to ? (doc.emailed ? { ok: true, text: `Emailed to ${to} ✓` } : { ok: false, text: doc.email_error || 'The email could not be sent.' }) : null)
      setStage('done')
    } catch (e) {
      if (!reauthOn401(e)) setServerErr(errText(e, 'Could not issue the document.'))
    } finally { setBusy(false) }
  }

  // ── Check (quote) ──────────────────────────────────────────────────────────
  const setDraft = (patch: Partial<QuoteParse['draft']>) => setQuote(q => q ? { ...q, draft: { ...q.draft, ...patch } } : q)
  const setSection = (i: number, patch: Partial<Section>) => setQuote(q => q ? { ...q, draft: { ...q.draft, sections: q.draft.sections.map((s, ix) => ix === i ? { ...s, ...patch } : s) } } : q)
  const addSection = () => setDraft({ sections: [...(quote?.draft.sections ?? []), blankSection()] })
  const removeSection = (i: number) => setDraft({ sections: (quote?.draft.sections ?? []).filter((_, ix) => ix !== i) })

  async function issueQuote() {
    if (!quote) return
    setServerErr(null); setProblems([])
    const to = email.trim()
    if (to && !isEmail(to)) { show('That email address does not look right.'); return }
    if (!quote.draft.client_name.trim()) { show('Add the client name first.'); return }
    if (!premium.trim()) { show('Type the premium first.'); return }
    if (quote.premium_is_suggested && !premiumEdited && !premiumConfirmed) { show('Tick the box to confirm the suggested premium.'); return }
    if (!companyId) { show('Pick the company first.'); return }
    setBusy(true)
    try {
      const payload = {
        client_name: quote.draft.client_name.trim(), client_attn: quote.draft.client_attn.trim(),
        class_of_business: quote.draft.class_of_business.trim(), period: quote.draft.period.trim() || '12 months',
        broker: quote.draft.broker.trim(), agent: agent.trim(),
        sections: quote.draft.sections.filter(s => s.name.trim()),
        premium: premium.trim(), source_text: text.trim(),
      }
      const c = await rawStaffFetch(`/underwriting/quotes/?company=${encodeURIComponent(companyId)}`, { method: 'POST', body: JSON.stringify(payload) })
      if (!c.ok) { setServerErr(formatServerErrors(c.body, c.status)); return }
      let row = readQuoteRow(c.body)
      // The server may flag the premium as a guess (not in the note). The person
      // typing or ticking it on the phone IS the confirmation — the desktop rule.
      if (row.premium_is_suggested && (premiumEdited || premiumConfirmed)) {
        const k = await rawStaffFetch(`/underwriting/quotes/${row.id}/confirm-premium/`, { method: 'POST', body: JSON.stringify({ premium: premium.trim() }) })
        if (!k.ok) { setServerErr(formatServerErrors(k.body, k.status)); return }
        row = readQuoteRow(k.body)
      }
      const i = await rawStaffFetch(`/underwriting/quotes/${row.id}/issue/`, { method: 'POST' })
      if (!i.ok) {
        const probs = readStrings(i.body.problems)
        if (probs.length) setProblems(probs)
        setServerErr(readStr(i.body.detail) || formatServerErrors(i.body, i.status))
        return
      }
      row = readQuoteRow(i.body)
      setIssuedQuote(row)
      if (to) {
        const m = await rawStaffFetch(`/underwriting/quotes/${row.id}/email/`, { method: 'POST', body: JSON.stringify({ to }) })
        setEmailNote(m.ok ? { ok: true, text: `Emailed to ${to} ✓` } : { ok: false, text: formatServerErrors(m.body, m.status) })
      } else setEmailNote(null)
      setStage('done')
    } catch (e) {
      if (!reauthOn401(e)) setServerErr(errText(e, 'Could not create the quotation.'))
    } finally { setBusy(false) }
  }

  async function openPdf(path: string) {
    setServerErr(null); setBusy(true)
    try { openBlob(await sfetchBlob(path)) }
    catch (e) { if (!reauthOn401(e)) setServerErr(errText(e, 'Could not open the PDF.')) }
    finally { setBusy(false) }
  }

  const reset = () => {
    setStage('describe'); setCert(null); setQuote(null); setIssued(null); setIssuedQuote(null)
    setEmailNote(null); setServerErr(null); setProblems([]); setEmail(''); setText(''); setPremium(''); setAgent('')
  }
  const company = companies?.find(c => c.id === companyId)

  // ── Done ───────────────────────────────────────────────────────────────────
  if (stage === 'done' && (issued || issuedQuote)) {
    const title = issuedQuote ? issuedQuote.quote_number : (issued?.policy_number || issued?.doctype_label || 'Issued')
    const sub = issuedQuote ? issuedQuote.client_name : `${issued?.doctype_label ?? ''}${issued?.insured_name ? ` · ${issued.insured_name}` : ''}`
    const pdfPath = issuedQuote ? `/underwriting/quotes/${issuedQuote.id}/pdf/` : `/underwriting/documents/${issued?.id}/pdf/`
    return (
      <ScreenFrame title="Quotes & certificates" base={base}>
        <div style={{ textAlign: 'center', padding: '24px 8px 8px' }}>
          <p style={{ fontSize: 52, margin: '0 0 8px' }}>✅</p>
          <h2 style={{ fontFamily: serif, fontWeight: 800, fontSize: 24, color: C.ink, margin: 0 }}>{title}</h2>
          <p style={{ color: C.inkSoft, fontSize: 14, lineHeight: 1.6, margin: '10px 0 0' }}>{sub}{company ? ` · ${company.name}` : ''}</p>
        </div>
        {issuedQuote && (
          <Card>
            <Row k="Premium" v={`BWP ${issuedQuote.premium}`} />
            <Row k="VAT" v={`BWP ${issuedQuote.vat}`} />
            <Row k="Total" v={`BWP ${issuedQuote.total}`} strong />
            {issuedQuote.valid_until && <Row k="Valid until" v={issuedQuote.valid_until} />}
            <p style={{ margin: '10px 0 0', fontSize: 11.5, color: C.inkSoft }}>Figures as saved by Omni.</p>
          </Card>
        )}
        {emailNote && <ServerMessage text={emailNote.text} tone={emailNote.ok ? 'ok' : 'error'} />}
        {serverErr && <ServerMessage text={serverErr} tone="error" />}
        <button onClick={() => openPdf(pdfPath)} disabled={busy} style={primaryBtn(busy)}><FileText size={16} /> Open PDF</button>
        <Card>
          <p style={{ margin: 0, fontSize: 13, color: C.inkSoft, lineHeight: 1.5 }}>
            <Mail size={13} style={{ verticalAlign: '-2px', marginRight: 6 }} />
            Need it at another address? Open the PDF and forward it from your mailbox — an issued document is sent once from Omni.
          </p>
        </Card>
        <button onClick={reset} style={ghostBtn}>Create another</button>
        <Toast text={toast} />
      </ScreenFrame>
    )
  }

  // ── Check ──────────────────────────────────────────────────────────────────
  if (stage === 'check' && doctype) {
    const warnings = quote ? quote.warnings : cert ? cert.warnings : []
    const to = email.trim()
    return (
      <ScreenFrame title="Quotes & certificates" base={base}>
        <Card>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
            <b style={{ color: C.ink, fontSize: 15 }}>{DOC_LABEL[doctype]}</b>
            <button onClick={() => { setStage('describe'); setServerErr(null); setProblems([]) }} style={ghostBtn}>Edit text</button>
          </div>
          <p style={{ color: C.inkSoft, fontSize: 13, margin: '8px 0 0', lineHeight: 1.5 }}>
            Check every field before you create it{cert?.via ? ` — read by ${cert.via === 'rules' || cert.via.startsWith('rules') ? 'Omni' : 'Aria'}` : ''}.
          </p>
          {warnings.map((w, i) => <div key={i} style={{ marginTop: 8 }}><ServerMessage text={w} tone="warn" /></div>)}
        </Card>

        {cert && (
          <Card>
            {cert.doctype === 'wca' && (
              <>
                <label htmlFor="docs-look" style={{ ...labelStyle, marginTop: 0 }}>Look</label>
                <select id="docs-look" value={look} onChange={e => setLook(e.target.value as WcaLook)} style={inputStyle}>
                  {WCA_LOOKS.map(l => <option key={l.value} value={l.value}>{l.label}</option>)}
                </select>
              </>
            )}
            {CERT_FIELDS[cert.doctype].map((f, ix) => (
              <div key={f.key}>
                <label htmlFor={`docs-${f.key}`} style={{ ...labelStyle, marginTop: ix === 0 && cert.doctype !== 'wca' ? 0 : 10 }}>{f.label}</label>
                {f.kind === 'long' ? (
                  <textarea id={`docs-${f.key}`} value={cert.fields[f.key] ?? ''} onChange={e => setField(f.key, e.target.value)} rows={2} style={{ ...inputStyle, resize: 'vertical' }} />
                ) : (
                  <input id={`docs-${f.key}`} type={f.kind === 'date' ? 'date' : 'text'} inputMode={f.kind === 'money' ? 'decimal' : undefined}
                    value={cert.fields[f.key] ?? ''} onChange={e => setField(f.key, e.target.value)} style={inputStyle} />
                )}
              </div>
            ))}
            {cert.doctype !== 'wca' && (
              <p style={{ margin: '12px 0 0', fontSize: 12, color: C.inkSoft, lineHeight: 1.5 }}>
                Signed by <b style={{ color: C.ink }}>{cert.fields[SIGNATORY_KEYS[0]]}</b> · {cert.fields[SIGNATORY_KEYS[1]]} · {cert.fields[SIGNATORY_KEYS[2]]} (Alpha Direct&apos;s signatory — set by Omni).
              </p>
            )}
            <button onClick={preview} disabled={busy} style={{ ...ghostBtn, width: '100%', marginTop: 14, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6 }}>
              <FileText size={14} /> Preview PDF
            </button>
          </Card>
        )}

        {quote && (
          <>
            <Card>
              <label htmlFor="docs-client" style={{ ...labelStyle, marginTop: 0 }}>Client</label>
              <input id="docs-client" value={quote.draft.client_name} onChange={e => setDraft({ client_name: e.target.value })} style={inputStyle} />
              <label htmlFor="docs-attn" style={labelStyle}>Attention / address</label>
              <input id="docs-attn" value={quote.draft.client_attn} onChange={e => setDraft({ client_attn: e.target.value })} style={inputStyle} />
              <label htmlFor="docs-class" style={labelStyle}>Class of business</label>
              <input id="docs-class" list="docs-classes" value={quote.draft.class_of_business} onChange={e => setDraft({ class_of_business: e.target.value })} placeholder="Pick one or type" style={inputStyle} />
              <datalist id="docs-classes">
                {Array.from(new Set(templates.map(t => t.class_of_business).filter(Boolean))).map(cob => <option key={cob} value={cob} />)}
              </datalist>
              <div style={{ display: 'flex', gap: 10 }}>
                <div style={{ flex: 1 }}>
                  <label htmlFor="docs-period" style={labelStyle}>Period</label>
                  <input id="docs-period" value={quote.draft.period} onChange={e => setDraft({ period: e.target.value })} style={inputStyle} />
                </div>
                <div style={{ flex: 1 }}>
                  <label htmlFor="docs-broker" style={labelStyle}>Broker</label>
                  <input id="docs-broker" value={quote.draft.broker} onChange={e => setDraft({ broker: e.target.value })} style={inputStyle} />
                </div>
              </div>
              <label htmlFor="docs-agent" style={labelStyle}>Agent (who brought the business)</label>
              <input id="docs-agent" value={agent} onChange={e => setAgent(e.target.value)} style={inputStyle} />
            </Card>

            <Card>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <b style={{ color: C.ink, fontSize: 15 }}>Cover</b>
                <button onClick={addSection} style={{ ...ghostBtn, display: 'flex', alignItems: 'center', gap: 6, color: '#B45309' }}><Plus size={14} /> Add row</button>
              </div>
              {quote.draft.sections.length === 0 && <p style={{ margin: '10px 0 0', fontSize: 13, color: C.inkSoft }}>No cover rows yet — add what is being insured.</p>}
              {quote.draft.sections.map((s, i) => (
                <div key={i} style={{ marginTop: 12, paddingTop: 12, borderTop: `1px solid ${C.line}` }}>
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    <input value={s.name} onChange={e => setSection(i, { name: e.target.value })} placeholder={`Row ${i + 1} — what is covered`} aria-label={`Row ${i + 1} cover`} style={{ ...inputStyle, flex: 1 }} />
                    <button onClick={() => removeSection(i)} aria-label={`Remove row ${i + 1}`} style={{ width: 44, height: 44, borderRadius: 12, border: 'none', background: '#F0F2F5', color: C.inkSoft, display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer' }}><X size={16} /></button>
                  </div>
                  <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                    <input value={s.sum_insured} onChange={e => setSection(i, { sum_insured: e.target.value })} inputMode="decimal" placeholder="Sum insured" aria-label={`Row ${i + 1} sum insured`} style={{ ...inputStyle, flex: 1.3 }} />
                    <input value={s.basis} onChange={e => setSection(i, { basis: e.target.value })} placeholder="Basis" aria-label={`Row ${i + 1} basis`} style={{ ...inputStyle, flex: 1 }} />
                    <input value={s.excess} onChange={e => setSection(i, { excess: e.target.value })} inputMode="decimal" placeholder="Excess" aria-label={`Row ${i + 1} excess`} style={{ ...inputStyle, flex: 1 }} />
                  </div>
                </div>
              ))}
            </Card>

            <Card>
              <label htmlFor="docs-premium" style={{ ...labelStyle, marginTop: 0 }}>
                Premium (BWP, excl. VAT){quote.premium_is_suggested && !premiumEdited ? ' · suggested — confirm' : ''}
              </label>
              <input id="docs-premium" value={premium} inputMode="decimal" onChange={e => { setPremium(e.target.value); setPremiumEdited(true) }} style={inputStyle} />
              {quote.premium_is_suggested && !premiumEdited && (
                <label htmlFor="docs-confirm" style={{ display: 'flex', alignItems: 'center', gap: 10, minHeight: 44, marginTop: 8, fontSize: 13, color: C.ink, cursor: 'pointer' }}>
                  <input id="docs-confirm" type="checkbox" checked={premiumConfirmed} onChange={e => setPremiumConfirmed(e.target.checked)} style={{ width: 22, height: 22 }} />
                  I have checked this premium and confirm it{quote.premium_basis ? ` (${quote.premium_basis})` : ''}
                </label>
              )}
              {!premiumEdited && premium ? (
                <>
                  <Row k="VAT" v={`BWP ${quote.vat}`} />
                  <Row k="Total" v={`BWP ${quote.total}`} strong />
                  <p style={{ margin: '6px 0 0', fontSize: 11.5, color: C.inkSoft }}>Figures from Omni. It recomputes them when it saves.</p>
                </>
              ) : (
                <p style={{ margin: '10px 0 0', fontSize: 12, color: C.inkSoft, lineHeight: 1.5 }}>Omni works out the VAT and total when it saves — you will see its figures on the next screen.</p>
              )}
            </Card>
          </>
        )}

        <Card>
          <label htmlFor="docs-email" style={{ ...labelStyle, marginTop: 0 }}>Customer&apos;s email — we send the PDF straight from Omni (optional)</label>
          <input id="docs-email" type="email" inputMode="email" autoComplete="off" value={email} onChange={e => setEmail(e.target.value)} placeholder="client@example.com" style={inputStyle} />
        </Card>

        {problems.length > 0 && <ServerMessage text={problems.join('\n')} control="Print check" tone="error" />}
        {serverErr && <ServerMessage text={serverErr} tone="error" />}
        <button onClick={quote ? issueQuote : issueCert} disabled={busy} style={primaryBtn(busy)}>
          <Send size={16} /> {busy ? 'Creating…' : to ? 'Create & send' : 'Create'}
        </button>
        <Toast text={toast} />
      </ScreenFrame>
    )
  }

  // ── Describe ───────────────────────────────────────────────────────────────
  return (
    <ScreenFrame title="Quotes & certificates" base={base}>
      {loadErr && <RetryBanner message={loadErr} onRetry={load} />}
      <Card>
        <label htmlFor="docs-text" style={{ ...labelStyle, marginTop: 0, fontSize: 14 }}>Describe what you need</label>
        <textarea id="docs-text" value={text} onChange={e => setText(e.target.value)} rows={6} maxLength={8000}
          placeholder={EXAMPLES} style={{ ...inputStyle, resize: 'vertical', fontSize: 16, lineHeight: 1.45 }} />
        <p style={{ margin: '8px 0 0', fontSize: 12, color: C.inkSoft, lineHeight: 1.5 }}>Omni reads it and fills the form. You check every field before anything is issued.</p>
      </Card>

      <Card>
        <p id="docs-type-label" style={{ ...labelStyle, marginTop: 0 }}>Type{needsType ? ' — pick one, the description did not say' : ' (optional)'}</p>
        <div role="group" aria-labelledby="docs-type-label" style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          {CHIPS.map(ch => {
            const on = pick === ch.value
            return (
              <button key={ch.value} onClick={() => { setPick(ch.value); setNeedsType(false) }} aria-pressed={on}
                style={{ minHeight: 44, padding: '0 14px', borderRadius: 999, border: 'none', fontWeight: 700, fontSize: 13, cursor: 'pointer',
                  background: on ? C.navy : '#fff', color: on ? '#fff' : C.inkSoft,
                  boxShadow: on ? 'none' : `inset 0 0 0 ${needsType && ch.value !== 'auto' ? 2 : 1}px ${needsType && ch.value !== 'auto' ? C.orangeDeep : C.line}` }}>
                {ch.label}
              </button>
            )
          })}
        </div>
        {needsType && <div style={{ marginTop: 10 }}><ServerMessage text="Which document is this — a quote, a cover note, a financed cover note or a WCA certificate?" tone="warn" /></div>}

        {companies && companies.length > 1 && (
          <>
            <label htmlFor="docs-company" style={labelStyle}>Company</label>
            <select id="docs-company" value={companyId} onChange={e => setCompanyId(e.target.value)} style={inputStyle}>
              {companies.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </>
        )}
      </Card>

      {serverErr && <ServerMessage text={serverErr} tone="error" />}
      <button onClick={parse} disabled={busy} style={primaryBtn(busy)}>
        {busy ? 'Reading…' : 'Create'}
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
