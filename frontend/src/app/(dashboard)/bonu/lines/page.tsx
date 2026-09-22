'use client'

/**
 * BONU — Billed detail: the transactions behind a consolidated figure.
 *
 * Kutlo Keitumele asked for this on 11 August 2026: *"it would be beneficial to have
 * a feature that provides a detailed breakdown of the consolidated billed amount,
 * rather than only displaying the consolidated figure. This functionality would
 * assist the Finance team with reconciliation, verification, and identifying any
 * discrepancies between the underlying transactions and the consolidated billed
 * amount."*
 *
 * Every other BONU screen shows totals. P4.8m billed, P1.1m for one firm, 620
 * matters — all correct, and impossible to verify or explain to the union, because
 * there was nothing underneath them.
 *
 * The reconciliation strip at the top is the point of the page, not decoration: it
 * states what the total should be over every matching row, what this page actually
 * shows, and whether the two agree. A page that quietly drops rows would be worse
 * than no page at all, so truncation is said out loud.
 *
 * Backend: /api/v1/bonu/lines/ — the same query the consolidated figures are built
 * from, so a difference here is a real difference in the data.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, Check, Download, Loader2, Search } from 'lucide-react'

import { apiFetch } from '@/lib/api'
import { BonuTabs, LINE, NAVY, ORANGE, RED, AMBER, GREEN, money2 } from '../_shared'

type Line = {
  id: string
  invoice_number: string
  invoice_date: string | null
  firm: string
  line_no: number
  service_date: string | null
  matter_ref: string
  member_ref: string
  fee_earner: string
  matter_type: string
  matter_type_label: string
  matter_type_source: string
  matter_type_source_label: string
  classification_confirmed: boolean
  description: string
  basis: string
  hours: string
  rate: string
  amount: string
}

type Reconciliation = {
  total_lines: number
  total_billed: string
  shown_lines: number
  shown_billed: string
  truncated: boolean
  balances: boolean
  unconfirmed_classification_billed: string
}

type Payload = {
  matter_types: { value: string; label: string }[]
  filters: Record<string, string>
  reconciliation: Reconciliation
  limit: number
  offset: number
  lines: Line[]
}

const PAGE = 500

export default function BonuBilledDetailPage() {
  const [data, setData] = useState<Payload | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [offset, setOffset] = useState(0)

  // Filters. Read from the URL on first load so a figure on the Overview can link
  // straight here with its own filter already applied.
  const [firm, setFirm] = useState('')
  const [lawyer, setLawyer] = useState('')
  const [matterType, setMatterType] = useState('')
  const [memberRef, setMemberRef] = useState('')

  type Filters = { firm: string; lawyer: string; matterType: string; memberRef: string }

  // A sequence number, so a slow earlier reply cannot overwrite a newer one.
  const seq = useRef(0)

  const loadWith = useCallback(async (f: Filters, nextOffset: number) => {
    const mine = ++seq.current
    setLoading(true); setError(null)
    const q = new URLSearchParams()
    if (f.firm) q.set('firm', f.firm)
    if (f.lawyer) q.set('lawyer', f.lawyer)
    if (f.matterType) q.set('matter_type', f.matterType)
    if (f.memberRef) q.set('member_ref', f.memberRef)
    q.set('limit', String(PAGE))
    q.set('offset', String(nextOffset))
    try {
      const payload = await apiFetch<Payload>(`/bonu/lines/?${q.toString()}`)
      if (mine !== seq.current) return
      setData(payload)
      setOffset(nextOffset)
    } catch (e) {
      if (mine !== seq.current) return
      setError(e instanceof Error ? e.message : 'Could not load the billed detail.')
    } finally {
      if (mine === seq.current) setLoading(false)
    }
  }, [])

  const load = useCallback((nextOffset = 0) =>
    loadWith({ firm, lawyer, matterType, memberRef }, nextOffset),
    [loadWith, firm, lawyer, matterType, memberRef])

  // Seed from the URL so a figure on the Overview links straight here with its
  // filter applied, then fetch ONCE. Typing must not refetch: each keystroke
  // would run a whole-table aggregate, and out-of-order replies would leave rows
  // on screen that no longer match the filters shown.
  useEffect(() => {
    const q = new URLSearchParams(window.location.search)
    const f = q.get('firm') || ''
    const l = q.get('lawyer') || ''
    const m = q.get('matter_type') || ''
    const r = q.get('member_ref') || ''
    setFirm(f); setLawyer(l); setMatterType(m); setMemberRef(r)
    loadWith({ firm: f, lawyer: l, matterType: m, memberRef: r }, 0)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])


  function exportCsv() {
    if (!data) return
    const head = ['Invoice', 'Invoice date', 'Firm', 'Service date', 'Matter ref',
                  'Member ref', 'Fee earner', 'Case type', 'Classified by',
                  'Basis', 'Hours', 'Rate', 'Amount']
    const rows = data.lines.map(l => [
      l.invoice_number, l.invoice_date || '', l.firm, l.service_date || '',
      l.matter_ref, l.member_ref, l.fee_earner, l.matter_type_label,
      l.matter_type_source_label, l.basis, l.hours, l.rate, l.amount,
    ])
    // Firm-supplied free text goes into this file. A cell beginning = + - @ is
    // executed as a formula by Excel, so it is prefixed with an apostrophe.
    const safe = (c: unknown) => {
      const v = String(c ?? '')
      return /^[=+\-@]/.test(v) ? `'${v}` : v
    }
    const csv = [head, ...rows]
      .map(r => r.map(c => `"${safe(c).replace(/"/g, '""')}"`).join(','))
      .join('\n')
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }))
    const a = document.createElement('a')
    a.href = url
    a.download = 'bonu-billed-detail.csv'
    a.click()
    URL.revokeObjectURL(url)
  }

  const rec = data?.reconciliation

  return (
    <main className="p-6 max-w-[1400px] mx-auto">
      <h1 className="text-[22px] font-bold mb-1" style={{ color: NAVY }}>
        BONU — billed detail
      </h1>
      <p className="text-[13px] mb-4" style={{ color: '#6B7280' }}>
        Every transaction behind the consolidated billed figure, so it can be
        reconciled and verified line by line rather than taken on trust.
      </p>

      <BonuTabs active="/bonu/lines" />

      {/* The reconciliation strip IS the feature. */}
      {rec && (
        <div
          className="rounded-xl p-4 mt-4 mb-4 grid gap-4"
          style={{
            border: `1px solid ${rec.balances ? LINE : AMBER}`,
            background: rec.balances ? '#fff' : '#FFFBEB',
            gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
          }}
        >
          <div>
            <div className="text-[11px] uppercase tracking-wide" style={{ color: '#6B7280' }}>
              Consolidated billed
            </div>
            <div className="text-[21px] font-bold tabular-nums" style={{ color: NAVY }}>
              {money2(Number(rec.total_billed))}
            </div>
            <div className="text-[11px]" style={{ color: '#6B7280' }}>
              {rec.total_lines} transaction{rec.total_lines === 1 ? '' : 's'}
            </div>
          </div>

          <div>
            <div className="text-[11px] uppercase tracking-wide" style={{ color: '#6B7280' }}>
              Shown on this page
            </div>
            <div className="text-[21px] font-bold tabular-nums" style={{ color: NAVY }}>
              {money2(Number(rec.shown_billed))}
            </div>
            <div className="text-[11px]" style={{ color: '#6B7280' }}>
              {rec.shown_lines} line{rec.shown_lines === 1 ? '' : 's'}
            </div>
          </div>

          <div>
            <div className="text-[11px] uppercase tracking-wide" style={{ color: '#6B7280' }}>
              Reconciles
            </div>
            {rec.total_lines === 0 ? (
              // 0 === 0 is not a reconciliation. A green tick over an empty page told
              // Finance the figures agreed when there were no figures at all — the
              // worst possible reading of "nothing loaded".
              <div className="text-[13px] font-semibold" style={{ color: '#6B7280' }}>
                Nothing to reconcile — no transactions loaded
              </div>
            ) : rec.balances ? (
              <div className="text-[15px] font-semibold flex items-center gap-1"
                   style={{ color: GREEN }}>
                <Check className="h-4 w-4" /> Adds up exactly
              </div>
            ) : (
              <div className="text-[13px] font-semibold flex items-start gap-1"
                   style={{ color: AMBER }}>
                <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
                <span>
                  {rec.truncated || offset > 0
                    ? `Showing ${offset + 1}–${offset + rec.shown_lines} of ${rec.total_lines} `
                      + 'transactions — page through for the rest. The consolidated '
                      + 'figure above covers all of them.'
                    : 'The lines shown do not add up to the total. Investigate before '
                      + 'relying on this figure.'}
                </span>
              </div>
            )}
          </div>

          <div>
            <div className="text-[11px] uppercase tracking-wide" style={{ color: '#6B7280' }}>
              On an unconfirmed case type
            </div>
            <div
              className="text-[21px] font-bold tabular-nums"
              style={{ color: Number(rec.unconfirmed_classification_billed) > 0 ? RED : NAVY }}
            >
              {money2(Number(rec.unconfirmed_classification_billed))}
            </div>
            <div className="text-[11px]" style={{ color: '#6B7280' }}>
              Nobody has confirmed what kind of case this is
            </div>
          </div>
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-wrap items-end gap-2 mb-3">
        <Field label="Law firm" value={firm} onChange={setFirm} placeholder="Any firm" />
        <Field label="Lawyer" value={lawyer} onChange={setLawyer} placeholder="Any lawyer" />
        <Field label="Member reference" value={memberRef} onChange={setMemberRef}
               placeholder="e.g. BONU-0042" />
        <div>
          <label className="block text-[11px] mb-1" style={{ color: '#6B7280' }}>Case type</label>
          <select
            aria-label="Filter by matter type"
            value={matterType}
            onChange={e => setMatterType(e.target.value)}
            className="text-[13px] rounded-md px-2 py-1.5 bg-white"
            style={{ border: `1px solid ${LINE}`, color: NAVY, minWidth: 170 }}
          >
            {/* From the server, not a copy. A new case type on the model would
                otherwise be missing from this page alone. */}
            <option value="">Any case type</option>
            {(data?.matter_types || []).map(t => (
              <option key={t.value} value={t.value}>{t.label}</option>
            ))}
          </select>
        </div>
        <button
          onClick={() => load(0)}
          className="inline-flex items-center gap-1 text-[13px] font-semibold px-3 py-1.5 rounded-md text-white"
          style={{ background: ORANGE }}
        >
          <Search className="h-3.5 w-3.5" /> Show
        </button>
        <button
          onClick={() => { setFirm(''); setLawyer(''); setMatterType(''); setMemberRef('') }}
          className="text-[13px] px-3 py-1.5 rounded-md bg-white"
          style={{ border: `1px solid ${LINE}`, color: '#6B7280' }}
        >
          Clear
        </button>
        <button
          onClick={exportCsv}
          disabled={!data?.lines.length}
          className="inline-flex items-center gap-1 text-[13px] px-3 py-1.5 rounded-md bg-white disabled:opacity-50"
          style={{ border: `1px solid ${LINE}`, color: NAVY }}
        >
          <Download className="h-3.5 w-3.5" /> Export this page
        </button>
      </div>

      {error && (
        <div className="rounded-lg px-3 py-2 mb-3 text-[13px]"
             style={{ background: '#FEF2F2', color: RED, border: `1px solid ${RED}33` }}>
          {error}
        </div>
      )}

      <div className="rounded-xl overflow-hidden bg-white" style={{ border: `1px solid ${LINE}` }}>
        <div className="overflow-x-auto">
          <table className="w-full text-[13px]">
            <thead>
              <tr className="text-left text-[11px] uppercase"
                  style={{ background: '#F9FAFB', color: '#6B7280' }}>
                <th className="py-2 px-3">Invoice</th>
                <th className="py-2 px-3">Firm</th>
                <th className="py-2 px-3">Service date</th>
                <th className="py-2 px-3">Matter</th>
                <th className="py-2 px-3">Member</th>
                <th className="py-2 px-3">Lawyer</th>
                <th className="py-2 px-3">Case type</th>
                <th className="py-2 px-3 text-right">Hours</th>
                <th className="py-2 px-3 text-right">Rate</th>
                <th className="py-2 px-3 text-right">Amount</th>
              </tr>
            </thead>
            <tbody>
              {loading && (
                <tr>
                  <td colSpan={10} className="py-8 text-center" style={{ color: '#9CA3AF' }}>
                    <Loader2 className="h-4 w-4 animate-spin inline" /> Loading…
                  </td>
                </tr>
              )}
              {!loading && !data?.lines.length && (
                <tr>
                  <td colSpan={10} className="py-8 text-center" style={{ color: '#9CA3AF' }}>
                    No billed transactions match those filters.
                  </td>
                </tr>
              )}
              {!loading && data?.lines.map(l => (
                <tr key={l.id} className="border-t" style={{ borderColor: LINE }}>
                  <td className="py-2 px-3 whitespace-nowrap">
                    <span className="font-mono text-[12px]">{l.invoice_number}</span>
                    <div className="text-[11px]" style={{ color: '#9CA3AF' }}>{l.invoice_date}</div>
                  </td>
                  <td className="py-2 px-3">{l.firm}</td>
                  <td className="py-2 px-3 whitespace-nowrap">{l.service_date || '—'}</td>
                  <td className="py-2 px-3">
                    <span className="font-mono text-[12px]">{l.matter_ref || '—'}</span>
                    {l.description && (
                      <div className="text-[11px] max-w-[280px] truncate" style={{ color: '#6B7280' }}
                           title={l.description}>
                        {l.description}
                      </div>
                    )}
                  </td>
                  <td className="py-2 px-3 font-mono text-[12px]">{l.member_ref || '—'}</td>
                  <td className="py-2 px-3">{l.fee_earner || '—'}</td>
                  <td className="py-2 px-3">
                    {l.matter_type_label}
                    {!l.classification_confirmed && (
                      <div className="text-[11px]" style={{ color: RED }}>
                        {l.matter_type_source_label}
                      </div>
                    )}
                  </td>
                  <td className="py-2 px-3 text-right tabular-nums">{l.hours || '—'}</td>
                  <td className="py-2 px-3 text-right tabular-nums">
                    {l.rate ? money2(Number(l.rate)) : '—'}
                  </td>
                  <td className="py-2 px-3 text-right tabular-nums font-semibold">
                    {money2(Number(l.amount))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {rec && rec.total_lines > PAGE && (
        <div className="flex items-center justify-between mt-3 text-[13px]">
          <span style={{ color: '#6B7280' }}>
            Lines {offset + 1}–{offset + rec.shown_lines} of {rec.total_lines}
          </span>
          <span className="flex gap-2">
            <button
              onClick={() => load(Math.max(offset - PAGE, 0))}
              disabled={offset === 0 || loading}
              className="px-3 py-1.5 rounded-md bg-white disabled:opacity-40"
              style={{ border: `1px solid ${LINE}`, color: NAVY }}
            >
              Previous
            </button>
            <button
              onClick={() => load(offset + PAGE)}
              disabled={!rec.truncated || loading}
              className="px-3 py-1.5 rounded-md bg-white disabled:opacity-40"
              style={{ border: `1px solid ${LINE}`, color: NAVY }}
            >
              Next
            </button>
          </span>
        </div>
      )}
    </main>
  )
}

function Field({
  label, value, onChange, placeholder,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  placeholder?: string
}) {
  return (
    <div>
      <label className="block text-[11px] mb-1" style={{ color: '#6B7280' }}>{label}</label>
      <input
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        className="text-[13px] rounded-md px-2 py-1.5 bg-white"
        style={{ border: `1px solid ${LINE}`, color: NAVY, minWidth: 160 }}
      />
    </div>
  )
}
