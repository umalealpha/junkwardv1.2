/** Shapes and field metadata for the phone "Quotes & certificates" screen.
 * Field ids are the SERVER's (underwriting/reader.py FIELDS) — the phone never
 * renames them. Nothing here computes money. */

export type DocType = 'quote' | 'cn' | 'cnfi' | 'wca'
export type DocPick = 'auto' | DocType
export type WcaLook = 'orig' | 'cool' | 'royal' | 'formal'

export const DOC_TYPES: readonly DocType[] = ['quote', 'cn', 'cnfi', 'wca']
export const isDocType = (v: unknown): v is DocType => typeof v === 'string' && (DOC_TYPES as readonly string[]).includes(v)

export const DOC_LABEL: Record<DocType, string> = {
  quote: 'Quotation', cn: 'Cover note', cnfi: 'Financed cover note', wca: 'WCA certificate',
}
export const CHIPS: { value: DocPick; label: string }[] = [
  { value: 'auto', label: 'Auto' }, { value: 'quote', label: 'Quote' }, { value: 'cn', label: 'Cover note' },
  { value: 'cnfi', label: 'Financed cover note' }, { value: 'wca', label: 'WCA' },
]
export const WCA_LOOKS: { value: WcaLook; label: string }[] = [
  { value: 'orig', label: 'Original' }, { value: 'cool', label: 'Cool' }, { value: 'royal', label: 'Royal' }, { value: 'formal', label: 'Formal' },
]

export interface FieldSpec { key: string; label: string; kind?: 'date' | 'money' | 'text' | 'long' }
/** Human labels for the server's field ids, in the order they read on the document. */
export const CERT_FIELDS: Record<Exclude<DocType, 'quote'>, FieldSpec[]> = {
  cn: [
    { key: 'cn_client', label: 'Client' }, { key: 'cn_policy', label: 'Policy no' },
    { key: 'cn_addr', label: 'Address', kind: 'long' }, { key: 'cn_risk', label: 'Risk address', kind: 'long' },
    { key: 'cn_class', label: 'Class of cover' }, { key: 'cn_sum', label: 'Sum insured (P)', kind: 'money' },
    { key: 'cn_from', label: 'From', kind: 'date' }, { key: 'cn_to', label: 'To', kind: 'date' },
    { key: 'cn_date', label: 'Issue date', kind: 'date' }, { key: 'cn_terr', label: 'Territorial limits', kind: 'long' },
  ],
  cnfi: [
    { key: 'cn_client', label: 'Client' }, { key: 'cn_policy', label: 'Policy no' },
    { key: 'fi_class', label: 'Class of cover' }, { key: 'fi_reg', label: 'Registration' },
    { key: 'fi_make', label: 'Make / model' }, { key: 'fi_value', label: 'Value (P)', kind: 'money' },
    { key: 'fi_bank', label: 'Bank / financier' },
    { key: 'cn_from', label: 'From', kind: 'date' }, { key: 'cn_to', label: 'To', kind: 'date' },
    { key: 'cn_date', label: 'Issue date', kind: 'date' }, { key: 'cn_terr', label: 'Territorial limits', kind: 'long' },
  ],
  wca: [
    { key: 'w_policy', label: 'Policy no' }, { key: 'w_agency', label: 'Agency / broker' },
    { key: 'w_name', label: 'Insured / employer' }, { key: 'w_addr', label: 'Address', kind: 'long' },
    { key: 'w_from', label: 'From', kind: 'date' }, { key: 'w_to', label: 'To', kind: 'date' },
    { key: 'w_date', label: 'Issue date', kind: 'date' },
    { key: 'w_emp', label: 'Employees' }, { key: 'w_earn', label: 'Annual earnings (P)', kind: 'money' },
  ],
}
/** Read-only on the phone: the server forces these to the tool's defaults. */
export const SIGNATORY_KEYS = ['cn_signame', 'cn_sigphone', 'cn_sigemail'] as const

export interface Section { group: string; name: string; note: string; sum_insured: string; basis: string; excess: string }
export const blankSection = (): Section => ({ group: '', name: '', note: '', sum_insured: '', basis: '', excess: '' })

export interface QuoteDraft {
  client_name: string; client_attn: string; class_of_business: string; period: string; broker: string; sections: Section[]
}
/** Exactly what POST underwriting/parse-text/ answers for a quote. Money strings are the server's. */
export interface QuoteParse {
  draft: QuoteDraft; premium: string; vat: string; total: string; premium_is_suggested: boolean; premium_basis: string; warnings: string[]
}
export interface CertParse { doctype: Exclude<DocType, 'quote'>; fields: Record<string, string>; via: string; confidence: number; warnings: string[]; ok: boolean }

export interface MeCompany { id: string; code: string; name: string; is_active: boolean }
export interface QuoteTemplate { id: number; name: string; class_of_business: string }

export interface IssuedDoc { id: string; doctype: string; doctype_label: string; policy_number: string; insured_name: string; emailed: boolean; email_error?: string }
export interface QuoteRow { id: string; quote_number: string; client_name: string; premium: string; vat: string; total: string; premium_is_suggested: boolean; valid_until: string | null; status: string }

type J = Record<string, unknown>
const str = (v: unknown): string => (typeof v === 'string' ? v : typeof v === 'number' ? String(v) : '')
const strList = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [])

export function readSection(v: unknown): Section {
  const s = (v && typeof v === 'object' ? v : {}) as J
  return { group: str(s.group), name: str(s.name), note: str(s.note), sum_insured: str(s.sum_insured), basis: str(s.basis), excess: str(s.excess) }
}
export function readQuoteParse(b: J): QuoteParse {
  const d = (b.draft && typeof b.draft === 'object' ? b.draft : {}) as J
  return {
    draft: {
      client_name: str(d.client_name), client_attn: str(d.client_attn), class_of_business: str(d.class_of_business),
      period: str(d.period) || '12 months', broker: str(d.broker),
      sections: Array.isArray(d.sections) ? d.sections.map(readSection) : [],
    },
    premium: str(b.premium), vat: str(b.vat), total: str(b.total),
    premium_is_suggested: b.premium_is_suggested === true, premium_basis: str(b.premium_basis), warnings: strList(b.warnings),
  }
}
export function readCertParse(b: J, doctype: Exclude<DocType, 'quote'>): CertParse {
  const raw = (b.fields && typeof b.fields === 'object' ? b.fields : {}) as J
  const fields: Record<string, string> = {}
  for (const [k, v] of Object.entries(raw)) fields[k] = str(v)
  return { doctype, fields, via: str(b.via), confidence: typeof b.confidence === 'number' ? b.confidence : 0, warnings: strList(b.warnings), ok: b.ok === true }
}
export function readIssuedDoc(b: J): IssuedDoc {
  return { id: str(b.id), doctype: str(b.doctype), doctype_label: str(b.doctype_label), policy_number: str(b.policy_number), insured_name: str(b.insured_name), emailed: b.emailed === true, email_error: str(b.email_error) || undefined }
}
export function readQuoteRow(b: J): QuoteRow {
  return { id: str(b.id), quote_number: str(b.quote_number), client_name: str(b.client_name), premium: str(b.premium), vat: str(b.vat), total: str(b.total), premium_is_suggested: b.premium_is_suggested === true, valid_until: str(b.valid_until) || null, status: str(b.status) }
}
export const readStrings = strList
export const readStr = str
