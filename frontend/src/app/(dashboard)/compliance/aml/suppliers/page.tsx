'use client'

/**
 * /compliance/aml/suppliers — screening the counterparties we actually PAID.
 *
 * The first cut listed the whole vendor master and showed 9,410 rows: the same
 * supplier register is replicated per company, so one supplier appeared eleven
 * times. Worse, it screened the wrong population — payments name their payee as
 * free text, so of the companies Alpha Direct actually paid in the last sixty
 * days, only two existed in that register at all.
 *
 * So this page asks the question the control is actually for: who did we send
 * money to, and have we checked them?
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { apiFetch } from '@/lib/api'
import { Building2, Search, X, AlertTriangle } from 'lucide-react'

interface PaidSupplier {
  name: string
  payments: number
  total_paid: string
  contact: number | null
  has_vendor_record: boolean
  kyc_id: string | null
  sanctions_status: 'clean' | 'flagged' | 'unchecked'
  pep_status: 'none' | 'pep' | 'associate' | 'unchecked'
  sanctions_checked_at: string | null
  // What the last screening actually said. Blank for a payee that has a
  // vendor record, whose answers live on the KYC file instead.
  list_source?: string
  list_version?: string
  screening_notes?: string
  already_screened: boolean
}

interface Payload {
  days: number
  count: number
  screened: number
  unscreened: number
  no_vendor_record: number
  results: PaidSupplier[]
}

const STATUS_TONE: Record<PaidSupplier['sanctions_status'], string> = {
  clean: 'bg-emerald-100 text-emerald-800',
  flagged: 'bg-red-100 text-red-800',
  unchecked: 'bg-slate-200 text-slate-700',
}

function pula(v: string) {
  const n = Number(v)
  if (!isFinite(n)) return '—'
  return 'P' + n.toLocaleString('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export default function SupplierScreeningPage() {
  const [data, setData] = useState<Payload | null>(null)
  const [days, setDays] = useState(60)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [q, setQ] = useState('')
  const [onlyUnscreened, setOnlyUnscreened] = useState(false)
  const [screening, setScreening] = useState<PaidSupplier | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setErr(null)
    try {
      setData(await apiFetch<Payload>(`/aml/supplier-screening/suppliers/?days=${days}`))
    } catch (e: any) { setErr(e?.message || 'Load failed') }
    finally { setLoading(false) }
  }, [days])
  useEffect(() => { void load() }, [load])

  const rows = useMemo(() => {
    let list = data?.results ?? []
    if (onlyUnscreened) {
      list = list.filter((r) => !r.already_screened && r.sanctions_status === 'unchecked')
    }
    const needle = q.trim().toLowerCase()
    if (needle) list = list.filter((r) => r.name.toLowerCase().includes(needle))
    return list
  }, [data, q, onlyUnscreened])

  const record = async (row: PaidSupplier, sanctions: string, pep: string,
                        listSource: string, listVersion: string, notes: string) => {
    try {
      await apiFetch('/aml/supplier-screening/screen/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          contact: row.contact, name: row.name,
          sanctions_status: sanctions, pep_status: pep,
          list_source: listSource, list_version: listVersion, notes,
        }),
      })
      setScreening(null); await load()
    } catch (e: any) { setErr(e?.message || 'Could not record the screening') }
  }

  return (
    <div className="min-h-screen bg-slate-50">
      <TopBar />
      <main className="mx-auto max-w-7xl px-6 py-8">
        <div className="mb-6">
          <div className="flex items-center gap-3">
            <Building2 size={28} style={{ color: '#0D1B2A' }} />
            <h1 className="text-2xl font-serif font-semibold" style={{ color: '#0D1B2A' }}>
              Supplier Screening
            </h1>
          </div>
          <p className="mt-1 text-sm text-slate-600">
            The counterparties Alpha Direct actually paid, newest window first. Click a row to record
            a screening.
          </p>
        </div>

        {data && (
          <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Tile label={`Paid in ${data.days} days`} value={data.count} />
            <Tile label="Screened" value={data.screened} />
            <Tile label="Not screened" value={data.unscreened}
              tone={data.unscreened ? 'red' : undefined} />
            <Tile label="No supplier record" value={data.no_vendor_record}
              tone={data.no_vendor_record ? 'amber' : undefined} />
          </div>
        )}

        {data && data.no_vendor_record > 0 && (
          <div className="mb-4 flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
            <AlertTriangle size={16} className="mt-0.5 shrink-0" />
            <span>
              {data.no_vendor_record} of these were paid without a supplier record on file —
              payments record the payee as free text. You can still screen them; the result is filed
              under their name in the sanctions register.
            </span>
          </div>
        )}

        {err && (
          <div className="mb-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
            {err}
          </div>
        )}

        <div className="mb-3 flex flex-wrap items-center gap-3">
          <div className="relative">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find a supplier"
              className="rounded border border-slate-300 py-2 pl-9 pr-3 text-sm" />
          </div>
          <select value={days} onChange={(e) => setDays(Number(e.target.value))}
            className="rounded border border-slate-300 px-3 py-2 text-sm">
            <option value={30}>Paid in the last 30 days</option>
            <option value={60}>Paid in the last 2 months</option>
            <option value={90}>Paid in the last 3 months</option>
            <option value={180}>Paid in the last 6 months</option>
          </select>
          <label className="flex items-center gap-2 text-sm text-slate-700">
            <input type="checkbox" checked={onlyUnscreened}
              onChange={(e) => setOnlyUnscreened(e.target.checked)} />
            Show only the ones not screened
          </label>
        </div>

        <div className="overflow-x-auto rounded-md border border-slate-200 bg-white">
          <table className="min-w-full text-sm">
            <thead className="bg-slate-100 text-left text-xs uppercase tracking-wide text-slate-600">
              <tr>
                <th className="px-3 py-2">Paid to</th>
                <th className="px-3 py-2 text-right">Payments</th>
                <th className="px-3 py-2 text-right">Total</th>
                <th className="px-3 py-2">Sanctions</th>
                <th className="px-3 py-2">PEP</th>
                <th className="px-3 py-2">Last screened</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {loading ? (
                <tr><td colSpan={6} className="px-3 py-8 text-center text-slate-500">Loading…</td></tr>
              ) : rows.length === 0 ? (
                <tr><td colSpan={6} className="px-3 py-8 text-center text-slate-500">
                  Nothing paid in this window.
                </td></tr>
              ) : rows.map((r) => (
                <tr key={r.name} onClick={() => setScreening(r)}
                    className="cursor-pointer hover:bg-slate-50">
                  <td className="px-3 py-2">
                    <div className="font-medium" style={{ color: '#0D1B2A' }}>{r.name}</div>
                    {!r.has_vendor_record && (
                      <div className="text-xs text-amber-700">No supplier record on file</div>
                    )}
                  </td>
                  <td className="px-3 py-2 text-right text-xs text-slate-600">{r.payments}</td>
                  <td className="px-3 py-2 text-right text-xs text-slate-600">{pula(r.total_paid)}</td>
                  <td className="px-3 py-2">
                    <span className={`inline-block rounded px-2 py-0.5 text-[11px] ${STATUS_TONE[r.sanctions_status]}`}>
                      {r.sanctions_status === 'unchecked'
                        ? (r.already_screened ? 'Screened by name' : 'Not screened')
                        : (r.sanctions_status === 'clean' ? 'Clean' : 'Flagged')}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-xs text-slate-600">
                    {r.pep_status === 'unchecked' ? 'Not assessed' : r.pep_status}
                  </td>
                  <td className="px-3 py-2 text-xs text-slate-600">
                    {r.sanctions_checked_at ? r.sanctions_checked_at.slice(0, 10) : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {screening && (
          <ScreenDialog row={screening} onSave={record} onClose={() => setScreening(null)} />
        )}
      </main>
    </div>
  )
}

function Tile({ label, value, tone }: { label: string; value: number; tone?: 'red' | 'amber' }) {
  const colour = tone === 'red' ? '#b91c1c' : tone === 'amber' ? '#b45309' : '#0D1B2A'
  return (
    <div className="rounded-md border border-slate-200 bg-white px-3 py-3">
      <div className="text-xs uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-1 text-2xl font-semibold" style={{ color: colour }}>{value}</div>
    </div>
  )
}

function ScreenDialog({
  row, onSave, onClose,
}: {
  row: PaidSupplier
  onSave: (row: PaidSupplier, sanctions: string, pep: string,
           listSource: string, listVersion: string, notes: string) => void | Promise<void>
  onClose: () => void
}) {
  const [sanctions, setSanctions] = useState<PaidSupplier['sanctions_status']>(
    row.sanctions_status === 'unchecked' ? 'clean' : row.sanctions_status)
  const [pep, setPep] = useState<PaidSupplier['pep_status']>(row.pep_status)
  // Re-open the dialog on a screened payee and it showed a blank form, so the
  // only way to see what was recorded was to record it again (kbotana, bug
  // 7dc9716b). Start from the last screening — EXCEPT the list version.
  //
  // The list version is the EVIDENCE, not context: it is what makes the search
  // repeatable for a regulator. `validate_list_version` only refuses a blank
  // one, so a pre-filled box lets a fresh row be filed with today's date and
  // last week's list without anyone noticing. She asked to SEE what was
  // recorded, not to have it re-typed for her — so it is shown below,
  // read-only, and this box starts empty every time.
  const [listSource, setListSource] = useState(row.list_source || 'UNSC Consolidated')
  const [listVersion, setListVersion] = useState('')
  const [notes, setNotes] = useState(row.screening_notes || '')

  return (
    <div className="fixed inset-0 z-40 flex items-end bg-slate-900/50 sm:items-center sm:justify-center">
      <div className="w-full max-w-lg rounded-t-lg bg-white p-6 shadow-xl sm:rounded-lg">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="font-serif text-xl font-semibold" style={{ color: '#0D1B2A' }}>
            Screen {row.name}
          </h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-700"><X size={20} /></button>
        </div>

        {!row.has_vendor_record && (
          <p className="mb-3 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
            No supplier record exists for this payee, so the screening will be filed under their name
            in the sanctions register.
          </p>
        )}

        {row.sanctions_checked_at && (
          <p className="mb-3 rounded border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600">
            Last screening on {row.sanctions_checked_at.slice(0, 10)}
            {row.list_version
              ? <> &mdash; {row.list_source || 'list not named'} / {row.list_version}</>
              : row.has_vendor_record
                ? <> &mdash; the list searched is not kept for a payee that has a supplier record.</>
                : <> &mdash; no list version was recorded.</>}
            {' '}Type the version you searched today.
          </p>
        )}

        <div className="space-y-3 text-sm">
          <div>
            <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500">Sanctions result</label>
            <select value={sanctions}
              onChange={(e) => setSanctions(e.target.value as PaidSupplier['sanctions_status'])}
              className="w-full rounded border border-slate-300 px-3 py-2 text-sm">
              <option value="clean">Clean — screened, no match</option>
              <option value="flagged">Flagged — name match requires review</option>
            </select>
          </div>
          <div>
            <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500">PEP status</label>
            <select value={pep} onChange={(e) => setPep(e.target.value as PaidSupplier['pep_status'])}
              className="w-full rounded border border-slate-300 px-3 py-2 text-sm">
              <option value="unchecked">Not yet assessed</option>
              <option value="none">Not a PEP</option>
              <option value="pep">Politically Exposed Person</option>
              <option value="associate">Close associate / family of a PEP</option>
            </select>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500">List searched</label>
              <input value={listSource} onChange={(e) => setListSource(e.target.value)}
                className="w-full rounded border border-slate-300 px-3 py-2 text-sm" />
            </div>
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500">List version / date</label>
              <input value={listVersion} onChange={(e) => setListVersion(e.target.value)}
                placeholder="e.g. 2026-09-16"
                className="w-full rounded border border-slate-300 px-3 py-2 text-sm" />
            </div>
          </div>
          <div>
            <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500">Notes</label>
            <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={2}
              className="w-full rounded border border-slate-300 px-3 py-2 text-sm" />
          </div>
        </div>
        <div className="mt-6 flex justify-end gap-2">
          <button onClick={onClose} className="rounded border border-slate-300 px-3 py-2 text-sm">Cancel</button>
          <button onClick={() => onSave(row, sanctions, pep, listSource, listVersion, notes)}
            className="rounded px-3 py-2 text-sm text-white" style={{ background: '#0D1B2A' }}>
            Record screening
          </button>
        </div>
      </div>
    </div>
  )
}
