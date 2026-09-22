/**
 * Shapes and small helpers for the legal-office screens.
 *
 * Every money value crosses the wire as a STRING. Decimal money turned into a
 * JavaScript number and back is how a figure comes to disagree with the one
 * beside it, so a value is only turned into a number to draw it, never to store
 * it or to send it back.
 */

import { apiFetch, apiFetchBinary, saveBlob } from '@/lib/api'
import { localYmd } from '@/lib/utils'

export interface FeeNote {
  id: string
  date: string | null
  client: string
  portfolio: string
  description: string
  unit: string
  rate: string
  qty: string
  amount: string
  external_item: string
  external_rate: string | null
  calc_basis: string
  external_equivalent: string | null
  saving: string | null
  pct_saved: number | null
  mapped: boolean
  updated_by: string
}

export interface InvoiceSaving {
  id: string
  date_received: string | null
  date_reviewed: string | null
  invoice_ref: string
  external_attorney: string
  original_amount: string
  agreed_amount: string
  saving: string
  note: string
  turnaround_days: number | null
  sla_met: boolean | null
  updated_by: string
}

export interface Advisory {
  id: string
  date: string | null
  department: string
  client: string
  matter_ref: string
  description: string
  type_of_work: string
  hours: string
  value: string
  updated_by: string
}

export interface TariffItem {
  id: string
  scope: 'internal' | 'external'
  section: string
  item: string
  unit: string
  rate: string
  position: number
}

export interface Mapping {
  id: string
  fee_description: string
  internal_unit: string
  internal_rate: string
  external_item: string
  external_unit: string
  external_rate: string
  calc_basis: 'flat' | 'per_hour'
  is_disbursement: boolean
  position: number
}

export interface Settings {
  quarterly_threshold: string
  quarterly_bonus_pct: string
  monthly_bonus_cap: string
  external_hourly_rate: string
  sla_days: number
}

export interface Summary {
  matter_billing: string
  monthly_bonus_entered: string | null
  monthly_bonus: string
  monthly_bonus_cap: string
  invoice_saving_month: string
  quarter_saving: string
  quarter_threshold: string
  quarter_met: boolean
  quarter_bonus: string
  quarter_months: { month: string; saving: string }[]
  quarter_in_house_saving: string
  mapped_internal_month: string
  external_equivalent: string
  in_house_saving: string
  pct_saved: number | null
  unmapped_lines_month: number
  unmapped_lines_total: number
  lifetime_internal: string
  lifetime_mapped_internal: string
  lifetime_external: string
  lifetime_saving: string
  lifetime_pct_saved: number | null
  advisory_hours: string
  advisory_value: string
  total_value: string
  sla: { measured: number; within: number; unknown: number; pct: string | null }
}

export interface ClientSaving {
  client: string
  internal: string
  external: string
  saving: string
  pct_saved: number | null
}

export interface Overview {
  settings: Settings
  departments: string[]
  months: string[]
  quarters: string[]
  month: string
  quarter: string
  fee_notes: FeeNote[]
  invoice_savings: InvoiceSaving[]
  advisory: Advisory[]
  tariffs: TariffItem[]
  mappings: Mapping[]
  client_savings: ClientSaving[]
  summary: Summary
}

/** A wire string as a number, for display only. */
export const n = (s: string | null | undefined): number => {
  const v = Number(s ?? '')
  return Number.isFinite(v) ? v : 0
}

export const pct = (v: number | string | null | undefined): string => {
  if (v === null || v === undefined || v === '') return '—'
  const x = typeof v === 'string' ? Number(v) : v
  return Number.isFinite(x) ? `${(x * 100).toFixed(1)}%` : '—'
}

export const monthLabel = (key: string): string => {
  if (!key) return '—'
  const [y, m] = key.split('-')
  const d = new Date(Number(y), Number(m) - 1, 1)
  return d.toLocaleString('en-GB', { month: 'long', year: 'numeric' })
}

export const quarterLabel = (key: string): string => {
  if (!key) return '—'
  const [y, q] = key.split('-')
  return `${q} ${y}`
}

export const today = () => localYmd(new Date())

/** Write to one register. The caller reloads; nothing is patched in place, so
 *  what is on screen is always what the server actually stored. */
export const writeRow = async (
  slug: string,
  body: Record<string, unknown>,
  id?: string,
): Promise<void> => {
  await apiFetch(`/bonu/legal/${slug}/${id ? `${id}/` : ''}`, {
    method: id ? 'PATCH' : 'POST',
    body: JSON.stringify(body),
  })
}

export const deleteRow = async (slug: string, id: string): Promise<void> => {
  await apiFetch(`/bonu/legal/${slug}/${id}/`, { method: 'DELETE' })
}

/** Download one office report (Monthly Fee Note or Quarterly Savings & Bonus)
 *  as Word or PDF. Built server-side from the captured registers; fetched with
 *  auth (a plain <a href> would 401 on the token) and handed to the browser. */
export const downloadReport = async (
  kind: 'feenote' | 'quarterly',
  fmt: 'docx' | 'pdf',
  params: { month?: string; quarter?: string },
): Promise<void> => {
  const qs = new URLSearchParams()
  if (params.month) qs.set('month', params.month)
  if (params.quarter) qs.set('quarter', params.quarter)
  const res = await apiFetchBinary(
    `/bonu/legal/report/${kind}/${fmt}/${qs.toString() ? `?${qs}` : ''}`,
  )
  if (!res.ok) {
    let detail = 'Could not build that report.'
    try { detail = (await res.json())?.detail || detail } catch { /* keep fallback */ }
    throw new Error(detail)
  }
  const blob = await res.blob()
  const base = kind === 'feenote' ? 'Monthly_Fee_Note' : 'Quarterly_Savings_Bonus_Report'
  const period = (params.month || params.quarter || '').replace(/[^0-9A-Za-z-]/g, '')
  saveBlob(blob, `${base}${period ? `_${period}` : ''}.${fmt}`)
}

/** The message the server sent, or a plain fallback — never a silent failure. */
export const errText = (e: unknown, fallback: string): string => {
  const d = (e as { data?: { detail?: string }; message?: string })?.data?.detail
  return d || (e as { message?: string })?.message || fallback
}
