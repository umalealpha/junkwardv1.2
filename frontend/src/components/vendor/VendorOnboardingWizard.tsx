'use client'

/**
 * Shared 10-step vendor (healthcare service provider) onboarding wizard.
 *
 * Used by BOTH:
 *   - the staff page /health/vendor-onboarding (Ankete-assisted, with OCR capture)
 *   - the public self-service page /onboard/[token] (provider fills it in, no login)
 *
 * The only differences are injected via props: the submit transport, whether the
 * document-OCR capture is shown (staff only — the extract endpoint is authed),
 * an optional access banner, and the page header/chrome. All form state,
 * validation, the signature pad and the review/confirmation screens are shared
 * so the two entry points can never drift apart.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { extractVendorFields, type VendorExtractField, type VendorSubmitResult } from '@/lib/api'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  Upload, CheckCircle2, AlertCircle, Send, FileSignature, Sparkles, FileText,
} from 'lucide-react'
import { AGREEMENT_FULL_TEXT } from '@/app/(dashboard)/health/vendor-onboarding/agreementText'

const STEPS = ['Consent', 'Entity', 'Tax & VAT', 'Directors', 'Credentials',
  'Banking', 'Services', 'Agreement', 'Sign', 'Review'] as const

const SERVICE_CATEGORIES = [
  'General Practitioner', 'Specialist Physician', 'Dentist', 'Optometrist',
  'Pharmacy', 'Private Hospital / Clinic', 'Diagnostic / Laboratory',
  'Radiology / Imaging', 'Physiotherapy / Rehabilitation', 'Ambulance / Emergency', 'Other',
]

const CONSENT_TEXT = `Alpha Direct Insurance is collecting your company, tax, director, professional-credential and banking information to assess and onboard you as a healthcare vendor. By continuing you consent, under the Botswana Data Protection Act, to Alpha Direct processing this personal data for vendor onboarding, payment setup and regulatory record-keeping, transmitted over an encrypted connection, stored securely, and retained only as long as required.`

const AGREEMENT_SUMMARY = `AFA Service Provider Network Agreement — between Associated Fund Administrators Botswana (Co. No. BW00000794271, rep. Tebogo Motsie, MD) and the Practitioner. Funder (Schedule A): Alpha Direct Insurance Company.
Key terms: 3-year duration (renewable 2 yrs, 2 months' notice); services only per BHPC/Council registration + Director of Health Services licence; adhere to Funder Rules/Tariff; advise practice changes within 21 days; Funders pay directly under a separate contract; AFA may audit claims; confidentiality + Data Protection Act No. 18 of 2024 (Practitioner = Processor); disputes by negotiation → arbitration; governed by the laws of Botswana.
The complete, signed agreement (full clauses + Schedule A) is generated as a PDF and emailed to AFA on submit.`

function fillAgreement(form: Form): string {
  const blankTok = (v?: string) => (v && v.trim() ? v.trim() : '__________')
  const map: Record<string, string> = {
    PRACTITIONER_NAME: form.practitioner.practitionerName || form.agreement.tradingName,
    TRADING_NAME: form.agreement.tradingName || form.entity.companyName,
    DISCIPLINE: form.practitioner.discipline || form.services.serviceCategory,
    REPRESENTATIVE: form.agreement.representativeName,
    CAPACITY: form.agreement.representativeCapacity,
    PRINCIPAL_PLACE: form.agreement.principalPlaceOfBusiness || form.practitioner.practiceAddress,
    EFFECTIVE_DATE: form.agreement.effectiveDate,
    TEL: form.agreement.contactTel,
    EMAIL: form.agreement.contactEmail,
    SIGNATORY: form.declaration.signatoryFullName,
    SIGN_DAY: '____',
    SIGN_MONTH: '____',
    SIGN_YEAR: '____',
  }
  return AGREEMENT_FULL_TEXT.replace(/\{\{(\w+)\}\}/g, (m, k) =>
    k in map ? blankTok(map[k]) : m)
}

type Dir = { fullName: string; idNumber: string; role: string }
export type Form = {
  entity: { companyName: string; registrationNumber: string; registrationDate: string; registeredAddress: string }
  tax: { tin: string; vatNumber: string }
  directors: { directors: Dir[] }
  practitioner: { practitionerName: string; councilType: string; councilRegistrationNumber: string; discipline: string; practiceAddress: string }
  banking: { bankName: string; branchName: string; branchCode: string; accountHolder: string; accountNumber: string; accountNumberConfirm: string }
  services: { serviceCategory: string; servicesOffered: string }
  agreement: { tradingName: string; representativeName: string; representativeCapacity: string; principalPlaceOfBusiness: string; effectiveDate: string; contactTel: string; contactEmail: string; agreementAccepted: boolean }
  consent: { consentAccepted: boolean; consentTextVersion: string }
  declaration: { signatoryFullName: string; declarationAccepted: boolean; signatureDataUrl: string }
}

const blank: Form = {
  entity: { companyName: '', registrationNumber: '', registrationDate: '', registeredAddress: '' },
  tax: { tin: '', vatNumber: '' },
  directors: { directors: [{ fullName: '', idNumber: '', role: '' }] },
  practitioner: { practitionerName: '', councilType: 'BHPC', councilRegistrationNumber: '', discipline: '', practiceAddress: '' },
  banking: { bankName: '', branchName: '', branchCode: '', accountHolder: '', accountNumber: '', accountNumberConfirm: '' },
  services: { serviceCategory: 'General Practitioner', servicesOffered: '' },
  agreement: { tradingName: '', representativeName: '', representativeCapacity: '', principalPlaceOfBusiness: '', effectiveDate: '', contactTel: '', contactEmail: '', agreementAccepted: false },
  consent: { consentAccepted: false, consentTextVersion: '2026-06-04.v1' },
  declaration: { signatoryFullName: '', declarationAccepted: false, signatureDataUrl: '' },
}

export function VendorOnboardingWizard({
  submit,
  enableDocCapture = false,
  header = null,
  accessBanner = null,
  initialContactEmail = '',
}: {
  submit: (payload: Record<string, unknown>) => Promise<VendorSubmitResult>
  enableDocCapture?: boolean
  header?: React.ReactNode
  accessBanner?: React.ReactNode
  initialContactEmail?: string
}) {
  const [step, setStep] = useState(0)
  const [form, setForm] = useState<Form>(() => {
    const f = structuredClone(blank)
    if (initialContactEmail) f.agreement.contactEmail = initialContactEmail
    return f
  })
  const [needsConfirm, setNeedsConfirm] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<{ ref: string; emailed: boolean } | null>(null)
  const [showFull, setShowFull] = useState(false)
  const agreementFull = useMemo(() => fillAgreement(form), [form])

  const setPath = useCallback((path: string, value: unknown) => {
    setError(null)
    setForm((prev) => {
      const next = structuredClone(prev) as any
      const keys = path.split('.'); let o = next
      for (let i = 0; i < keys.length - 1; i++) o = o[keys[i]]
      o[keys[keys.length - 1]] = value
      return next
    })
  }, [])
  const getPath = (path: string): any =>
    path.split('.').reduce<any>((o, k) => (o == null ? o : o[k]), form)

  const confirm = (p: string) => setNeedsConfirm((s) => { const n = new Set(s); n.delete(p); return n })

  async function runExtract(file: File, docType: string) {
    setBusy(true); setError(null)
    try {
      const r = await extractVendorFields({ file, doc_type: docType })
      const flagged = new Set(needsConfirm)
      r.fields.forEach((f: VendorExtractField) => {
        if (f.path === 'directors') return
        setPath(f.path, f.value); flagged.add(f.path)
      })
      setNeedsConfirm(flagged)
      if (!r.fields.length) setError('Nothing could be read — type the fields in manually.')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Document read failed — type fields manually.')
    } finally { setBusy(false) }
  }

  const blocked = needsConfirm.size > 0

  function stepError(s: number): string | null {
    const f = form
    if (s === 0 && !f.consent.consentAccepted) return 'Please tick the consent box to continue (required under the Botswana DPA).'
    if (s === 1) {
      if (!f.entity.companyName.trim()) return 'Company name is required.'
      if (!f.entity.registrationNumber.trim()) return 'Registration number is required.'
    }
    if (s === 5) {
      const a = f.banking.accountNumber.trim(), b = f.banking.accountNumberConfirm.trim()
      if (!a) return 'Enter the bank account number.'
      if (a !== b) return 'The two account numbers do not match — please re-check.'
    }
    if (s === 6 && f.services.serviceCategory === 'Other' && !f.services.servicesOffered.trim())
      return 'Please describe the services offered when you choose "Other".'
    if (s === 7 && !f.agreement.agreementAccepted) return 'Please accept the AFA Service Provider Network Agreement to continue.'
    if (s === 8) {
      if (!f.declaration.signatoryFullName.trim()) return 'Enter the full name of the signatory.'
      if (!f.declaration.declarationAccepted) return 'Please tick the declaration to continue.'
      if (!f.declaration.signatureDataUrl) return 'Please sign in the signature box before continuing.'
    }
    return null
  }

  function goNext() {
    const err = stepError(step)
    if (err) { setError(err); return }
    setError(null); setStep((s) => s + 1)
  }

  async function onSubmit() {
    if (blocked) { setError(`${needsConfirm.size} pre-filled field(s) still need confirming.`); return }
    for (const s of [0, 1, 5, 6, 7, 8]) { const e = stepError(s); if (e) { setError(e); return } }
    setBusy(true); setError(null)
    try {
      const now = new Date().toISOString()
      const isOther = form.services.serviceCategory === 'Other'
      const payload = {
        ...form,
        services: { ...form.services, servicesOffered: isOther ? form.services.servicesOffered : '' },
        consent: { ...form.consent, consentAccepted: form.consent.consentAccepted, consentTimestamp: now },
        declaration: { ...form.declaration, signedTimestamp: now },
        deepseek_used: enableDocCapture,
      }
      const r = await submit(payload as Record<string, unknown>)
      if (!r.success) { setError('Submit failed.'); return }
      setDone({ ref: r.reference_number, emailed: r.agreement_email_sent })
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Submit failed.')
    } finally { setBusy(false) }
  }

  if (done) return <Confirmation header={header} reference={done.ref} emailed={done.emailed} />

  return (
    <div className="min-h-screen bg-slate-50">
      {header}
      <div className="mx-auto max-w-3xl px-4 py-6">
        <div className="mb-2 flex items-center justify-between text-sm text-slate-500">
          <span>Step {step + 1} of {STEPS.length}</span>
          <span className="font-semibold text-slate-800">{STEPS[step]}</span>
        </div>
        <div className="mb-6 h-2 w-full overflow-hidden rounded-full bg-slate-200">
          <div className="h-full rounded-full bg-orange-500 transition-all"
               style={{ width: `${((step + 1) / STEPS.length) * 100}%` }} />
        </div>

        {accessBanner}

        <Card><CardContent className="p-5">
          {step === 0 && (
            <Section title="Consent & data protection" hint="Required before any data is captured (Botswana DPA).">
              <p className="mb-4 whitespace-pre-line rounded-lg bg-slate-50 p-3 text-sm text-slate-700">{CONSENT_TEXT}</p>
              <Check label="I have read and consent to the processing of this data."
                     checked={form.consent.consentAccepted} onChange={(v) => setPath('consent.consentAccepted', v)} />
            </Section>
          )}

          {step === 1 && (
            <Section title="Entity details" hint={enableDocCapture ? 'Capture the Certificate of Incorporation to pre-fill — confirm each.' : 'Enter your company details from your Certificate of Incorporation.'}>
              {enableDocCapture && <DocCapture label="Certificate of Incorporation" docType="CoI" onFile={runExtract} busy={busy} />}
              <Field path="entity.companyName" label="Company name" {...{ getPath, setPath, needsConfirm, confirm }} />
              <Field path="entity.registrationNumber" label="Registration number (CIPA)" placeholder="CO2023/45678" {...{ getPath, setPath, needsConfirm, confirm }} />
              <Field path="entity.registrationDate" label="Registration date" {...{ getPath, setPath, needsConfirm, confirm }} />
              <Field path="entity.registeredAddress" label="Registered office address" textarea {...{ getPath, setPath, needsConfirm, confirm }} />
            </Section>
          )}

          {step === 2 && (
            <Section title="Tax & VAT" hint={enableDocCapture ? 'Capture the company extract to pre-fill — confirm each.' : 'Enter your tax details.'}>
              {enableDocCapture && <DocCapture label="Company extract / tax document" docType="extract" onFile={runExtract} busy={busy} />}
              <Field path="tax.tin" label="TIN (Taxpayer ID)" {...{ getPath, setPath, needsConfirm, confirm }} />
              <Field path="tax.vatNumber" label="VAT number (if registered)" {...{ getPath, setPath, needsConfirm, confirm }} />
            </Section>
          )}

          {step === 3 && (
            <Section title="Directors / owners" hint="Enter each director's name and ID carefully.">
              {form.directors.directors.map((d, i) => (
                <div key={i} className="mb-3 rounded-lg border border-slate-200 p-3">
                  <Field path={`directors.directors.${i}.fullName`} label={`Director ${i + 1} — full name`} {...{ getPath, setPath, needsConfirm, confirm }} />
                  <Field path={`directors.directors.${i}.idNumber`} label="ID / Omang / passport" {...{ getPath, setPath, needsConfirm, confirm }} />
                  <Field path={`directors.directors.${i}.role`} label="Role (optional)" {...{ getPath, setPath, needsConfirm, confirm }} />
                </div>
              ))}
              <Button variant="outline" onClick={() => setPath('directors.directors',
                [...form.directors.directors, { fullName: '', idNumber: '', role: '' }])}>+ Add director</Button>
            </Section>
          )}

          {step === 4 && (
            <Section title="Practitioner / credentials" hint="Type these — credential docs are not auto-read.">
              <Field path="practitioner.practitionerName" label="Practitioner full name" {...{ getPath, setPath, needsConfirm, confirm }} />
              <Select path="practitioner.councilType" label="Registering council" options={['BHPC', 'PharmacyCouncil', 'Other']} {...{ getPath, setPath }} />
              <Field path="practitioner.councilRegistrationNumber" label="Council / practice registration number" {...{ getPath, setPath, needsConfirm, confirm }} />
              <Field path="practitioner.discipline" label="Discipline / profession" {...{ getPath, setPath, needsConfirm, confirm }} />
              <Field path="practitioner.practiceAddress" label="Practice address" textarea {...{ getPath, setPath, needsConfirm, confirm }} />
            </Section>
          )}

          {step === 5 && (
            <Section title="Banking details" hint="Enter the account number twice to confirm it.">
              <Field path="banking.bankName" label="Bank name" {...{ getPath, setPath, needsConfirm, confirm }} />
              <Field path="banking.branchName" label="Branch" {...{ getPath, setPath, needsConfirm, confirm }} />
              <Field path="banking.branchCode" label="Branch / sort code (optional)" {...{ getPath, setPath, needsConfirm, confirm }} />
              <Field path="banking.accountHolder" label="Account holder name" {...{ getPath, setPath, needsConfirm, confirm }} />
              <Field path="banking.accountNumber" label="Account number" {...{ getPath, setPath, needsConfirm, confirm }} />
              <Field path="banking.accountNumberConfirm" label="Re-enter account number" {...{ getPath, setPath, needsConfirm, confirm }} />
            </Section>
          )}

          {step === 6 && (
            <Section title="Services offered">
              <Select path="services.serviceCategory" label="Service category" options={SERVICE_CATEGORIES} {...{ getPath, setPath }} />
              {form.services.serviceCategory === 'Other' && (
                <Field path="services.servicesOffered" label="Please specify the services offered"
                       placeholder="Type the service(s) this vendor provides" textarea
                       {...{ getPath, setPath, needsConfirm, confirm }} />
              )}
            </Section>
          )}

          {step === 7 && (
            <Section title="AFA Network Agreement" hint="Read the full agreement, complete the details, and accept to sign.">
              <p className="mb-3 whitespace-pre-line rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs text-slate-700">{AGREEMENT_SUMMARY}</p>
              <button type="button" onClick={() => setShowFull((v) => !v)}
                      className="mb-4 inline-flex items-center gap-2 rounded-lg border border-[#7A2E22] px-3 py-1.5 text-sm font-semibold text-[#7A2E22] transition hover:bg-[#7A2E22]/5">
                <FileText size={15} />{showFull ? 'Hide full agreement' : 'Read full agreement'}
              </button>
              {showFull && (
                <div className="mb-4 max-h-96 overflow-y-auto rounded-lg border border-slate-300 bg-white p-4">
                  <pre className="whitespace-pre-wrap break-words font-serif text-[13px] leading-relaxed text-slate-800">{agreementFull}</pre>
                </div>
              )}
              <Field path="agreement.tradingName" label="Carrying on business as (trading name)" {...{ getPath, setPath, needsConfirm, confirm }} />
              <Field path="agreement.representativeName" label="Represented by" {...{ getPath, setPath, needsConfirm, confirm }} />
              <Field path="agreement.representativeCapacity" label="In his/her capacity as" {...{ getPath, setPath, needsConfirm, confirm }} />
              <Field path="agreement.principalPlaceOfBusiness" label="Principal place of business" textarea {...{ getPath, setPath, needsConfirm, confirm }} />
              <Field path="agreement.effectiveDate" label="Effective date" placeholder="e.g. 1 July 2026" {...{ getPath, setPath, needsConfirm, confirm }} />
              <Field path="agreement.contactTel" label="Telephone (domicilia)" {...{ getPath, setPath, needsConfirm, confirm }} />
              <Field path="agreement.contactEmail" label="Email (domicilia)" {...{ getPath, setPath, needsConfirm, confirm }} />
              <Check label="I have read and accept the full AFA Service Provider Network Agreement."
                     checked={form.agreement.agreementAccepted} onChange={(v) => setPath('agreement.agreementAccepted', v)} />
            </Section>
          )}

          {step === 8 && (
            <Section title="Declaration & signature" hint="Confirm the information is true, then sign.">
              <Field path="declaration.signatoryFullName" label="Full name of signatory" {...{ getPath, setPath, needsConfirm, confirm }} />
              <Check label="I declare the information provided is true, accurate and complete, and I am authorised to submit it."
                     checked={form.declaration.declarationAccepted} onChange={(v) => setPath('declaration.declarationAccepted', v)} />
              <Signature onChange={(d) => setPath('declaration.signatureDataUrl', d)} value={form.declaration.signatureDataUrl} />
            </Section>
          )}

          {step === 9 && (
            <Section title="Review" hint="Account number is masked.">
              <Review form={form} />
            </Section>
          )}

          {error && <p className="mt-3 flex items-center gap-2 rounded-md bg-red-50 p-3 text-sm text-red-700"><AlertCircle size={16} />{error}</p>}

          <div className="mt-6 flex items-center justify-between">
            <Button variant="outline" disabled={step === 0 || busy} onClick={() => setStep((s) => Math.max(0, s - 1))}>Back</Button>
            {step < STEPS.length - 1 ? (
              <Button disabled={busy} onClick={goNext}>Next</Button>
            ) : (
              <Button disabled={busy || blocked} onClick={onSubmit}>
                <Send size={16} className="mr-2" />{busy ? 'Submitting…' : 'Submit'}
              </Button>
            )}
          </div>
          {blocked && step === STEPS.length - 1 && (
            <p className="mt-3 rounded-md bg-orange-50 p-3 text-sm text-orange-700">
              {needsConfirm.size} pre-filled field(s) still need confirming — go back and tap Confirm on each.
            </p>
          )}
        </CardContent></Card>
      </div>
    </div>
  )
}

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <h2 className="text-xl font-bold text-slate-900">{title}</h2>
      {hint && <p className="mb-4 mt-1 text-sm text-slate-500">{hint}</p>}
      {children}
    </div>
  )
}

function Field({ path, label, placeholder, textarea, getPath, setPath, needsConfirm, confirm }: {
  path: string; label: string; placeholder?: string; textarea?: boolean
  getPath: (p: string) => any; setPath: (p: string, v: unknown) => void
  needsConfirm: Set<string>; confirm: (p: string) => void
}) {
  const flagged = needsConfirm.has(path)
  const cls = `w-full rounded-lg border px-3 py-2 text-slate-900 outline-none focus:ring-2 ${
    flagged ? 'border-orange-400 bg-orange-50 ring-orange-200' : 'border-slate-300 focus:ring-blue-200'}`
  return (
    <div className="mb-4">
      <label className="mb-1 block text-sm font-semibold text-slate-700">{label}</label>
      {textarea
        ? <textarea rows={3} className={cls} placeholder={placeholder} value={getPath(path) ?? ''} onChange={(e) => setPath(path, e.target.value)} />
        : <input className={cls} placeholder={placeholder} value={getPath(path) ?? ''} onChange={(e) => setPath(path, e.target.value)} />}
      {flagged && (
        <div className="mt-1 flex items-center justify-between rounded bg-orange-100 px-2 py-1">
          <span className="text-xs font-semibold text-orange-700">From document — please confirm</span>
          <button type="button" onClick={() => confirm(path)} className="rounded bg-orange-500 px-3 py-0.5 text-xs font-bold text-white">Confirm</button>
        </div>
      )}
    </div>
  )
}

function Select({ path, label, options, getPath, setPath }: {
  path: string; label: string; options: string[]; getPath: (p: string) => any; setPath: (p: string, v: unknown) => void
}) {
  return (
    <div className="mb-4">
      <label className="mb-1 block text-sm font-semibold text-slate-700">{label}</label>
      <select className="w-full rounded-lg border border-slate-300 px-3 py-2 text-slate-900"
              value={getPath(path) ?? ''} onChange={(e) => setPath(path, e.target.value)}>
        {options.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    </div>
  )
}

function Check({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex items-start gap-3 rounded-lg bg-slate-50 p-3 text-sm text-slate-700">
      <input type="checkbox" className="mt-1 h-5 w-5 accent-orange-500" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span>{label}</span>
    </label>
  )
}

function DocCapture({ label, docType, onFile, busy }: {
  label: string; docType: string; onFile: (f: File, t: string) => void; busy: boolean
}) {
  const ref = useRef<HTMLInputElement>(null)
  const [name, setName] = useState('')
  return (
    <div className="mb-5 rounded-xl border border-slate-200 bg-slate-50 p-4">
      <div className="mb-2 flex items-center gap-2 font-semibold text-slate-800"><Sparkles size={16} className="text-orange-500" />{label}</div>
      <input ref={ref} type="file" accept="image/*,application/pdf" className="hidden"
             onChange={(e) => { const f = e.target.files?.[0]; if (f) { setName(f.name); onFile(f, docType) } }} />
      <Button variant="outline" disabled={busy} onClick={() => ref.current?.click()}>
        <Upload size={16} className="mr-2" />{busy ? 'Reading…' : 'Capture / upload document'}
      </Button>
      {name && <span className="ml-3 text-xs text-slate-500">{name}</span>}
    </div>
  )
}

type InkPoint = { x: number; y: number; w: number }
const PALM_GUARD_MS = 800

function Signature({ value, onChange }: { value: string; onChange: (d: string) => void }) {
  const ref = useRef<HTMLCanvasElement>(null)
  const strokes = useRef<InkPoint[][]>([])
  const activeId = useRef<number | null>(null)
  const activeType = useRef<string>('')
  const penDown = useRef(false)
  const lastPenAt = useRef(0)
  const baseImg = useRef<HTMLImageElement | null>(null)
  const rectRef = useRef<DOMRect | null>(null)
  const [hasInk, setHasInk] = useState(false)
  const [typed, setTyped] = useState('')

  const dpr = () => Math.min(typeof window !== 'undefined' ? window.devicePixelRatio || 1 : 1, 3)
  const nowMs = () => (typeof performance !== 'undefined' ? performance.now() : Date.now())

  const redraw = useCallback(() => {
    const c = ref.current; if (!c) return
    const ctx = c.getContext('2d'); if (!ctx) return
    const d = dpr()
    ctx.setTransform(1, 0, 0, 1, 0, 0)
    ctx.clearRect(0, 0, c.width, c.height)
    ctx.setTransform(d, 0, 0, d, 0, 0)
    if (baseImg.current) ctx.drawImage(baseImg.current, 0, 0, c.width / d, c.height / d)
    ctx.lineCap = 'round'; ctx.lineJoin = 'round'; ctx.strokeStyle = '#0D1B2A'; ctx.fillStyle = '#0D1B2A'
    for (const s of strokes.current) {
      if (s.length === 1) { ctx.beginPath(); ctx.arc(s[0].x, s[0].y, s[0].w / 2, 0, Math.PI * 2); ctx.fill(); continue }
      for (let i = 1; i < s.length; i++) {
        const a = s[i - 1], b = s[i]
        ctx.beginPath(); ctx.lineWidth = b.w; ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke()
      }
    }
  }, [])

  const fit = useCallback(() => {
    const c = ref.current; if (!c) return
    const r = c.getBoundingClientRect(); const d = dpr()
    const w = Math.round(r.width * d), h = Math.round(r.height * d)
    if (c.width !== w || c.height !== h) { c.width = w; c.height = h }
    redraw()
  }, [redraw])

  useEffect(() => {
    const initial = value
    if (initial) {
      const img = new Image()
      img.onload = () => { baseImg.current = img; setHasInk(true); fit() }
      img.src = initial
    } else { fit() }
    window.addEventListener('resize', fit)
    window.addEventListener('orientationchange', fit)
    const mq = typeof window.matchMedia === 'function'
      ? window.matchMedia(`(resolution: ${window.devicePixelRatio || 1}dppx)`) : null
    mq?.addEventListener?.('change', fit)
    return () => {
      window.removeEventListener('resize', fit)
      window.removeEventListener('orientationchange', fit)
      mq?.removeEventListener?.('change', fit)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fit])

  const point = (clientX: number, clientY: number, pressure: number): InkPoint => {
    const r = rectRef.current ?? ref.current!.getBoundingClientRect()
    const p = pressure > 0 ? pressure : 0.5
    return { x: clientX - r.left, y: clientY - r.top, w: 1 + p * 3.5 }
  }

  function commit() {
    const c = ref.current!
    const empty = strokes.current.length === 0 && !baseImg.current
    setHasInk(!empty)
    onChange(empty ? '' : c.toDataURL('image/png'))
  }

  function onDown(e: React.PointerEvent<HTMLCanvasElement>) {
    const t = e.pointerType
    if (t === 'pen') {
      penDown.current = true; lastPenAt.current = nowMs()
      if (activeId.current !== null && activeType.current === 'touch') {
        strokes.current.pop()
        try { ref.current!.releasePointerCapture(activeId.current) } catch { /* ignore */ }
        activeId.current = null; redraw()
      }
    } else if (t === 'touch') {
      if (penDown.current || nowMs() - lastPenAt.current < PALM_GUARD_MS) return
    } else if (t === 'mouse' && e.button !== 0) {
      return
    }
    if (activeId.current !== null) return
    e.preventDefault()
    rectRef.current = ref.current!.getBoundingClientRect()
    try { ref.current!.setPointerCapture(e.pointerId) } catch { /* capture optional */ }
    activeId.current = e.pointerId; activeType.current = t
    strokes.current.push([point(e.clientX, e.clientY, e.pressure)])
    redraw()
  }

  function onMove(e: React.PointerEvent<HTMLCanvasElement>) {
    if (activeId.current !== e.pointerId) return
    if (e.pointerType === 'mouse' && e.buttons === 0) return
    const cur = strokes.current[strokes.current.length - 1]
    if (!cur) return
    e.preventDefault()
    const native = e.nativeEvent as PointerEvent & { getCoalescedEvents?: () => PointerEvent[] }
    const coalesced = native.getCoalescedEvents?.()
    const batch = coalesced && coalesced.length ? coalesced : [native]
    for (const ne of batch) cur.push(point(ne.clientX, ne.clientY, ne.pressure))
    redraw()
  }

  function endStroke(e: React.PointerEvent<HTMLCanvasElement>) {
    if (activeId.current !== e.pointerId) return
    if (e.pointerType === 'pen') { penDown.current = false; lastPenAt.current = nowMs() }
    activeId.current = null; activeType.current = ''
    try { ref.current!.releasePointerCapture(e.pointerId) } catch { /* ignore */ }
    commit()
  }

  function onLost(e: React.PointerEvent<HTMLCanvasElement>) {
    if (activeId.current !== e.pointerId) return
    if (e.pointerType === 'pen') penDown.current = false
    activeId.current = null; activeType.current = ''
    commit()
  }

  function clear() {
    strokes.current = []; baseImg.current = null
    activeId.current = null; activeType.current = ''; penDown.current = false
    setHasInk(false); redraw(); onChange('')
  }

  function undo() {
    if (activeId.current !== null) return
    strokes.current.pop(); redraw(); commit()
  }

  function adoptTyped() {
    const name = typed.trim(); if (!name) return
    const c = ref.current!; const d = dpr()
    const off = document.createElement('canvas'); off.width = c.width; off.height = c.height
    const ictx = off.getContext('2d'); if (!ictx) return
    ictx.setTransform(d, 0, 0, d, 0, 0)
    ictx.fillStyle = '#0D1B2A'; ictx.textAlign = 'center'; ictx.textBaseline = 'middle'
    ictx.font = 'italic 34px "Book Antiqua", Palatino, Georgia, serif'
    ictx.fillText(name, (c.width / d) / 2, (c.height / d) / 2)
    const url = off.toDataURL('image/png')
    const img = new Image()
    img.onload = () => { baseImg.current = img; strokes.current = []; setHasInk(true); redraw(); onChange(url) }
    img.src = url
  }

  const signed = Boolean(value) || hasInk
  return (
    <div className="mb-2">
      <label className="mb-1 block text-sm font-semibold text-slate-700">Signature</label>
      <canvas
        ref={ref}
        role="img"
        aria-label="Signature pad — draw your signature with a finger or stylus"
        onPointerDown={onDown}
        onPointerMove={onMove}
        onPointerUp={endStroke}
        onPointerCancel={endStroke}
        onLostPointerCapture={onLost}
        className="h-40 w-full cursor-crosshair rounded-lg border border-slate-300 bg-white"
        style={{ touchAction: 'none' }}
      />
      <div className="mt-1 flex items-center justify-between">
        <span className="text-xs text-slate-500">{signed ? 'Signed ✓' : 'Sign with your finger or a stylus'}</span>
        <div className="flex gap-4">
          <button type="button" className="text-sm font-semibold text-slate-500 disabled:opacity-40" onClick={undo} disabled={!hasInk}>Undo</button>
          <button type="button" className="text-sm font-semibold text-orange-600" onClick={clear}>Clear</button>
        </div>
      </div>
      <div className="mt-2 flex items-center gap-2">
        <input
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); adoptTyped() } }}
          placeholder="…or type your full name to adopt as your signature"
          aria-label="Type your full name to adopt as your signature (keyboard alternative to drawing)"
          className="min-w-0 flex-1 rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-900 outline-none focus:ring-2 focus:ring-blue-200"
        />
        <button type="button" onClick={adoptTyped} disabled={!typed.trim()}
                className="shrink-0 rounded-lg bg-slate-800 px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-40">Adopt</button>
      </div>
    </div>
  )
}

function Review({ form }: { form: Form }) {
  const mask = (s: string) => (s && s.length > 4 ? '•'.repeat(s.length - 4) + s.slice(-4) : s)
  const rows: [string, string][] = [
    ['Company', form.entity.companyName], ['Registration', form.entity.registrationNumber],
    ['TIN', form.tax.tin], ['Directors', form.directors.directors.map((d) => d.fullName).filter(Boolean).join(', ')],
    ['Practitioner', form.practitioner.practitionerName],
    ['Council', `${form.practitioner.councilType} · ${form.practitioner.councilRegistrationNumber}`],
    ['Bank', `${form.banking.bankName} ${form.banking.branchName}`], ['Account', mask(form.banking.accountNumber)],
    ['Category', form.services.serviceCategory === 'Other'
      ? `Other — ${form.services.servicesOffered.trim() || '(not specified)'}`
      : form.services.serviceCategory],
    ['Effective date', form.agreement.effectiveDate],
  ]
  return (
    <dl className="overflow-hidden rounded-lg border border-slate-200">
      {rows.map(([k, v]) => (
        <div key={k} className="flex justify-between gap-4 border-b border-slate-100 px-4 py-2 last:border-0">
          <dt className="text-sm font-semibold text-slate-500">{k}</dt>
          <dd className="text-right text-sm text-slate-900">{v || '—'}</dd>
        </div>
      ))}
    </dl>
  )
}

function Confirmation({ header, reference, emailed }: { header: React.ReactNode; reference: string; emailed: boolean }) {
  return (
    <div className="min-h-screen bg-slate-50">
      {header}
      <div className="mx-auto max-w-2xl px-4 py-16 text-center">
        <CheckCircle2 size={56} className="mx-auto mb-4 text-green-500" />
        <h1 className="mb-2 text-2xl font-bold text-slate-900">Submission received</h1>
        <p className="mb-6 text-slate-600">The vendor record was submitted for review.</p>
        <div className="inline-block rounded-lg bg-white p-5 shadow">
          <div className="text-xs uppercase tracking-wide text-slate-400">Reference</div>
          <div className="text-xl font-bold text-orange-600">{reference}</div>
          <div className="mt-2 flex items-center justify-center gap-2 text-sm text-slate-600">
            <FileSignature size={15} />
            {emailed ? 'Signed agreement emailed to AFA.' : 'Saved — agreement email pending.'}
          </div>
        </div>
      </div>
    </div>
  )
}
