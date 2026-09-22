'use client'

/**
 * PartsRegister — the typing half of Veritas · Parts & Savings.
 *
 * CFO directive 2026-09-10 (third pass): the module is a REGISTER. Veritas
 * staff enter data into it going forward, Bharath manages it, and he can add a
 * supplier, add a line of savings, or correct anything he likes. Memorandum
 * only — no journal, no money.
 *
 * Three registers, one tab strip:
 *   Savings lines  — one assessed job: quote vs assessment, saving derived.
 *   Parts bought   — supplier / category / month / amount.
 *   Contract pricing — the month's matched-parts figure.
 *
 * The saving is never typed. Parts, labour and paint are typed on both sides
 * and the difference is shown live, then recomputed again on the server, so a
 * register line can never carry a total that disagrees with itself.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Check, Loader2, Pencil, Plus, Trash2, TriangleAlert, X,
} from 'lucide-react'
import { apiFetch } from '@/lib/api'
import { localYmd } from '@/lib/utils'

type Tab = 'savings' | 'spend' | 'contract'

interface SavingRow {
  id: string; period: string; assessment_id: string; reg_no: string; vehicle: string
  repairer: string; req_auth_date: string | null
  quote_parts: string; quote_labour: string; quote_paint: string; quote_total: string
  report_parts: string; report_labour: string; report_paint: string; report_total: string
  saving_total: string; source: string; source_display: string
  entered_by_name: string; notes: string
}
interface SpendRow {
  id: string; category: string; category_display: string; supplier: string
  month: string; amount: string; source: string; source_display: string
  entered_by_name: string; notes: string
}
interface ContractRow {
  id: string; month: string; amount: string; source: string; source_display: string
  entered_by_name: string; notes: string
}
interface Paged<T> { count?: number; next?: string | null; results?: T[] }

/** What one register tab is showing right now. */
interface TabState<T> { rows: T[]; count: number; page: number }

// The server caps a page at 100 (core.pagination.CappedPageNumberPagination).
// Asking for 200 silently returned 100 of 120 savings lines and the tab badge
// counted the rows on screen, so twenty lines were invisible with nothing
// saying so. Page explicitly and take the count from the server.
const PAGE_SIZE = 100

const ENDPOINT: Record<Tab, string> = {
  savings:  '/salvage/parts/savings/',
  spend:    '/salvage/parts/spend/',
  contract: '/salvage/parts/contract-pricing/',
}

const CATEGORIES = [
  { value: 'DEALERSHIP',  label: 'Dealership' },
  { value: 'AFTERMARKET', label: 'Aftermarket' },
  { value: 'WINDSCREEN',  label: 'Windscreen / glass' },
]

const n = (v: unknown) => Number(v ?? 0)
const pula = (v: unknown) => n(v).toLocaleString('en-BW', { maximumFractionDigits: 2 })
const monthName = (iso: string) =>
  iso ? new Date(`${iso.slice(0, 7)}-01T00:00:00`)
    .toLocaleDateString('en-BW', { month: 'short', year: 'numeric' }) : '—'

const pageOf = <T,>(r: Paged<T> | T[], page: number): TabState<T> =>
  Array.isArray(r)
    ? { rows: r, count: r.length, page }
    : { rows: r.results ?? [], count: r.count ?? (r.results ?? []).length, page }

export function PartsRegister({ theme, onChanged }: { theme: any; onChanged: () => void }) {
  const [tab, setTab] = useState<Tab>('savings')
  const [month, setMonth] = useState<string>('')      // '' = every month
  const [page, setPage]   = useState(1)
  const empty = { rows: [], count: 0, page: 1 }
  const [savings, setSavings]   = useState<TabState<SavingRow>>(empty)
  const [spend, setSpend]       = useState<TabState<SpendRow>>(empty)
  const [contract, setContract] = useState<TabState<ContractRow>>(empty)
  const [months, setMonths]     = useState<string[]>([])
  const [suppliers, setSuppliers] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState<string | null>(null)
  const [editing, setEditing] = useState<string | null>(null)
  const [adding, setAdding]   = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    const q = (p: number) =>
      `?page_size=${PAGE_SIZE}&page=${p}${month ? `&month=${month}` : ''}`
    // Only the open tab is paged; the other two just need their totals, so
    // page 1 is enough for their badge counts.
    Promise.all([
      apiFetch<Paged<SavingRow>>(`${ENDPOINT.savings}${q(tab === 'savings' ? page : 1)}`),
      apiFetch<Paged<SpendRow>>(`${ENDPOINT.spend}${q(tab === 'spend' ? page : 1)}`),
      apiFetch<Paged<ContractRow>>(`${ENDPOINT.contract}${q(tab === 'contract' ? page : 1)}`),
      apiFetch<{ suppliers: string[] }>('/salvage/parts/spend/suppliers/'),
    ])
      .then(([s, p, c, sup]) => {
        setSavings(pageOf(s, tab === 'savings' ? page : 1))
        setSpend(pageOf(p, tab === 'spend' ? page : 1))
        setContract(pageOf(c, tab === 'contract' ? page : 1))
        setSuppliers(sup.suppliers || [])
        setError(null)
      })
      .catch(e => setError(e instanceof Error ? e.message : 'Could not load the register'))
      .finally(() => setLoading(false))
  }, [tab, page, month])

  useEffect(() => { load() }, [load])

  // The month list comes from the rows themselves, so it grows on its own.
  useEffect(() => {
    Promise.all([
      apiFetch<Paged<SavingRow>>(`${ENDPOINT.savings}?page_size=${PAGE_SIZE}`),
      apiFetch<Paged<SpendRow>>(`${ENDPOINT.spend}?page_size=${PAGE_SIZE}`),
    ]).then(([s, p]) => {
      const found = new Set<string>()
      for (const r of (Array.isArray(s) ? s : s.results ?? [])) if (r.period) found.add(r.period)
      for (const r of (Array.isArray(p) ? p : p.results ?? [])) if (r.month) found.add(r.month)
      setMonths([...found].sort().reverse())
    }).catch(() => { /* the filter is a convenience; the register still works */ })
  }, [])

  async function save(tabKey: Tab, body: Record<string, unknown>, id?: string) {
    const path = id ? `${ENDPOINT[tabKey]}${id}/` : ENDPOINT[tabKey]
    await apiFetch(path, {
      method: id ? 'PATCH' : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    setAdding(false); setEditing(null)
    load(); onChanged()
  }

  async function remove(tabKey: Tab, id: string) {
    await apiFetch(`${ENDPOINT[tabKey]}${id}/`, { method: 'DELETE' })
    load(); onChanged()
  }

  const counts = { savings: savings.count, spend: spend.count, contract: contract.count }
  const current: TabState<{ id: string }> =
    tab === 'savings' ? savings : tab === 'spend' ? spend : contract
  const firstShown = current.count === 0 ? 0 : (current.page - 1) * PAGE_SIZE + 1
  const lastShown  = Math.min(current.page * PAGE_SIZE, current.count)
  const morePages  = current.count > PAGE_SIZE

  return (
    <section className="rounded-xl overflow-hidden"
             style={{ background: theme.card, border: `1px solid ${theme.cardBdr}`, boxShadow: theme.cardSh }}>
      <header className="px-5 pt-5 pb-4">
        <h2 className="font-display text-lg font-bold" style={{ color: theme.navy }}>
          The register
        </h2>
        <p className="text-xs mt-1 max-w-2xl" style={{ color: theme.t3 }}>
          Add a line, correct a line, or delete one. A saving is worked out from
          the quote and the assessment — never typed. Loading a workbook replaces
          only the lines that came from a workbook, so what you type here stays.
        </p>

        <div className="flex flex-wrap gap-1.5 mt-4">
          {([
            ['savings',  'Savings lines'],
            ['spend',    'Parts bought'],
            ['contract', 'Contract pricing'],
          ] as [Tab, string][]).map(([key, label]) => (
            <button key={key} type="button"
                    onClick={() => { setTab(key); setPage(1); setAdding(false); setEditing(null) }}
                    className="text-xs font-semibold px-3 py-1.5 rounded-md transition-colors"
                    style={{
                      background: tab === key ? theme.navy : theme.g100,
                      color:      tab === key ? '#FFFFFF' : theme.t2,
                    }}>
              {label}
              <span className="ml-1.5 opacity-70 tabular-nums">{counts[key]}</span>
            </button>
          ))}
          <select value={month}
                  onChange={e => { setMonth(e.target.value); setPage(1) }}
                  aria-label="Show one month only"
                  className="text-xs font-semibold px-2.5 py-1.5 rounded-md"
                  style={{ background: theme.g100, color: theme.t2,
                           border: `1px solid ${theme.cardBdr}` }}>
            <option value="">Every month</option>
            {months.map(m => <option key={m} value={m}>{monthName(m)}</option>)}
          </select>
          <button type="button" onClick={() => { setAdding(a => !a); setEditing(null) }}
                  className="ml-auto inline-flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-md"
                  style={{
                    background: adding ? theme.g100 : `linear-gradient(135deg, ${theme.orange}, #FF9A2E)`,
                    color:      adding ? theme.t2 : '#FFFFFF',
                  }}>
            {adding ? <><X className="w-3.5 h-3.5" /> Cancel</> : <><Plus className="w-3.5 h-3.5" /> Add a line</>}
          </button>
        </div>
      </header>

      {error && (
        <div className="mx-5 mb-4 rounded-md p-3 flex items-start gap-2"
             style={{ background: theme.erB, border: `1px solid ${theme.er}30` }}>
          <TriangleAlert className="w-4 h-4 mt-0.5 shrink-0" style={{ color: theme.er }} />
          <span className="text-xs" style={{ color: theme.er }}>{error}</span>
        </div>
      )}

      {adding && (
        <div className="mx-5 mb-4 rounded-lg p-4"
             style={{ background: theme.g50, border: `1px solid ${theme.cardBdr}` }}>
          {tab === 'savings'  && <SavingForm  theme={theme} onSave={b => save('savings', b)} />}
          {tab === 'spend'    && <SpendForm   theme={theme} suppliers={suppliers}
                                              onSave={b => save('spend', b)} />}
          {tab === 'contract' && <ContractForm theme={theme} onSave={b => save('contract', b)} />}
        </div>
      )}

      <div className="px-5 pb-5">
        {loading && (
          <div className="py-8 text-center text-sm" style={{ color: theme.t3 }}>
            <Loader2 className="w-4 h-4 animate-spin mx-auto mb-2" /> Loading the register…
          </div>
        )}

        {!loading && tab === 'savings' && (
          <RegisterTable
            theme={theme}
            head={['Job', 'Month', 'Repairer', 'Quoted', 'Assessed', 'Saved', 'Entered by', '']}
            rows={savings.rows}
            empty="No savings lines yet. Add one, or load the month's workbook."
            render={row => editing === row.id ? (
              <tr key={row.id}>
                <td colSpan={8} className="py-3">
                  <SavingForm theme={theme} initial={row}
                              onSave={b => save('savings', b, row.id)}
                              onCancel={() => setEditing(null)} />
                </td>
              </tr>
            ) : (
              <tr key={row.id} className="border-t" style={{ borderColor: theme.cardBdr }}>
                <td className="py-2 pr-3">
                  <span className="font-semibold" style={{ color: theme.text }}>{row.assessment_id}</span>
                  <span className="block text-[10px]" style={{ color: theme.t3 }}>
                    {[row.reg_no, row.vehicle].filter(Boolean).join(' · ') || '—'}
                  </span>
                </td>
                <td className="py-2 pr-3 whitespace-nowrap" style={{ color: theme.t2 }}>{monthName(row.period)}</td>
                <td className="py-2 pr-3" style={{ color: theme.t2 }}>{row.repairer || '—'}</td>
                <td className="py-2 pr-3 text-right tabular-nums" style={{ color: theme.t2 }}>P {pula(row.quote_total)}</td>
                <td className="py-2 pr-3 text-right tabular-nums" style={{ color: theme.t2 }}>P {pula(row.report_total)}</td>
                <td className="py-2 pr-3 text-right tabular-nums font-bold" style={{ color: theme.ok }}>P {pula(row.saving_total)}</td>
                <td className="py-2 pr-3 text-[11px]" style={{ color: theme.t3 }}>
                  {row.entered_by_name}
                  <span className="block opacity-70">{row.source_display}</span>
                </td>
                <RowActions theme={theme} onEdit={() => { setEditing(row.id); setAdding(false) }}
                            onDelete={() => remove('savings', row.id)} />
              </tr>
            )} />
        )}

        {!loading && tab === 'spend' && (
          <RegisterTable
            theme={theme}
            head={['Supplier', 'Where from', 'Month', 'Amount', 'Entered by', '']}
            rows={spend.rows}
            empty="No parts purchases yet."
            render={row => editing === row.id ? (
              <tr key={row.id}>
                <td colSpan={6} className="py-3">
                  <SpendForm theme={theme} suppliers={suppliers} initial={row}
                             onSave={b => save('spend', b, row.id)}
                             onCancel={() => setEditing(null)} />
                </td>
              </tr>
            ) : (
              <tr key={row.id} className="border-t" style={{ borderColor: theme.cardBdr }}>
                <td className="py-2 pr-3 font-semibold" style={{ color: theme.text }}>{row.supplier}</td>
                <td className="py-2 pr-3" style={{ color: theme.t2 }}>{row.category_display}</td>
                <td className="py-2 pr-3 whitespace-nowrap" style={{ color: theme.t2 }}>{monthName(row.month)}</td>
                <td className="py-2 pr-3 text-right tabular-nums font-bold" style={{ color: theme.navy }}>P {pula(row.amount)}</td>
                <td className="py-2 pr-3 text-[11px]" style={{ color: theme.t3 }}>
                  {row.entered_by_name}
                  <span className="block opacity-70">{row.source_display}</span>
                </td>
                <RowActions theme={theme} onEdit={() => { setEditing(row.id); setAdding(false) }}
                            onDelete={() => remove('spend', row.id)} />
              </tr>
            )} />
        )}

        {!loading && tab === 'contract' && (
          <RegisterTable
            theme={theme}
            head={['Month', 'Matched by the repairer', 'Entered by', '']}
            rows={contract.rows}
            empty="No contract-pricing figures yet."
            render={row => editing === row.id ? (
              <tr key={row.id}>
                <td colSpan={4} className="py-3">
                  <ContractForm theme={theme} initial={row}
                                onSave={b => save('contract', b, row.id)}
                                onCancel={() => setEditing(null)} />
                </td>
              </tr>
            ) : (
              <tr key={row.id} className="border-t" style={{ borderColor: theme.cardBdr }}>
                <td className="py-2 pr-3 whitespace-nowrap font-semibold" style={{ color: theme.text }}>{monthName(row.month)}</td>
                <td className="py-2 pr-3 text-right tabular-nums font-bold" style={{ color: theme.navy }}>P {pula(row.amount)}</td>
                <td className="py-2 pr-3 text-[11px]" style={{ color: theme.t3 }}>
                  {row.entered_by_name}
                  <span className="block opacity-70">{row.source_display}</span>
                </td>
                <RowActions theme={theme} onEdit={() => { setEditing(row.id); setAdding(false) }}
                            onDelete={() => remove('contract', row.id)} />
              </tr>
            )} />
        )}
        {!loading && current.count > 0 && (
          <div className="flex flex-wrap items-center justify-between gap-2 pt-3 mt-1 border-t"
               style={{ borderColor: theme.cardBdr }}>
            <span className="text-[11px] tabular-nums" style={{ color: theme.t3 }}>
              Showing {firstShown}&ndash;{lastShown} of {current.count}
              {month ? ` in ${monthName(month)}` : ''}
            </span>
            {morePages && (
              <span className="inline-flex items-center gap-1.5">
                <button type="button" disabled={current.page <= 1}
                        onClick={() => setPage(p => Math.max(1, p - 1))}
                        className="text-[11px] font-semibold px-2.5 py-1 rounded-md"
                        style={{ background: theme.g100, color: theme.t2,
                                 opacity: current.page <= 1 ? 0.45 : 1 }}>
                  Previous
                </button>
                <button type="button" disabled={lastShown >= current.count}
                        onClick={() => setPage(p => p + 1)}
                        className="text-[11px] font-semibold px-2.5 py-1 rounded-md"
                        style={{ background: theme.g100, color: theme.t2,
                                 opacity: lastShown >= current.count ? 0.45 : 1 }}>
                  Next
                </button>
              </span>
            )}
          </div>
        )}
      </div>
    </section>
  )
}

function RegisterTable<T extends { id: string }>({ theme, head, rows, render, empty }: {
  theme: any; head: string[]; rows: T[]; empty: string
  render: (row: T) => React.ReactNode
}) {
  if (rows.length === 0) {
    return <p className="text-sm py-8 text-center" style={{ color: theme.t3 }}>{empty}</p>
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-[10px] uppercase tracking-wider" style={{ color: theme.t3 }}>
            {head.map((h, i) => (
              <th key={i} className={`py-2 pr-3 font-semibold ${i >= 3 && i < head.length - 2 ? 'text-right' : ''}`}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{rows.map(render)}</tbody>
      </table>
    </div>
  )
}

function RowActions({ theme, onEdit, onDelete }: {
  theme: any; onEdit: () => void; onDelete: () => void
}) {
  const [confirming, setConfirming] = useState(false)
  return (
    <td className="py-2 text-right whitespace-nowrap">
      {confirming ? (
        <span className="inline-flex items-center gap-1">
          <button type="button" onClick={onDelete}
                  className="text-[11px] font-bold px-2 py-1 rounded"
                  style={{ background: theme.erB, color: theme.er }}>
            Delete it
          </button>
          <button type="button" onClick={() => setConfirming(false)}
                  className="text-[11px] px-2 py-1 rounded" style={{ color: theme.t3 }}>
            Keep
          </button>
        </span>
      ) : (
        <span className="inline-flex items-center gap-1">
          <button type="button" onClick={onEdit} aria-label="Edit this line"
                  className="w-7 h-7 rounded-md inline-flex items-center justify-center"
                  style={{ background: theme.g100, color: theme.t2 }}>
            <Pencil className="w-3.5 h-3.5" />
          </button>
          <button type="button" onClick={() => setConfirming(true)} aria-label="Delete this line"
                  className="w-7 h-7 rounded-md inline-flex items-center justify-center"
                  style={{ background: theme.erB, color: theme.er }}>
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        </span>
      )}
    </td>
  )
}

// ── forms ──────────────────────────────────────────────────────────────────

function Field({ theme, label, children, hint }: {
  theme: any; label: string; children: React.ReactNode; hint?: string
}) {
  return (
    <label className="block">
      <span className="block text-[11px] font-semibold mb-1" style={{ color: theme.t2 }}>{label}</span>
      {children}
      {hint && <span className="block text-[10px] mt-1" style={{ color: theme.t3 }}>{hint}</span>}
    </label>
  )
}

function inputStyle(theme: any) {
  return {
    background: theme.input, border: `1px solid ${theme.cardBdr}`, color: theme.text,
  } as const
}

const INPUT_CLASS = 'w-full text-sm rounded-md px-2.5 py-1.5 outline-none focus:ring-2'

function SubmitRow({ theme, busy, err, onCancel, label }: {
  theme: any; busy: boolean; err: string | null; onCancel?: () => void; label: string
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 pt-1">
      <button type="submit" disabled={busy}
              className="inline-flex items-center gap-1.5 text-xs font-semibold px-3 py-2 rounded-md"
              style={{
                background: `linear-gradient(135deg, ${theme.orange}, #FF9A2E)`,
                color: '#FFFFFF', opacity: busy ? 0.6 : 1,
              }}>
        {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
        {label}
      </button>
      {onCancel && (
        <button type="button" onClick={onCancel}
                className="text-xs font-semibold px-3 py-2 rounded-md"
                style={{ background: theme.g100, color: theme.t2 }}>
          Cancel
        </button>
      )}
      {err && <span className="text-xs" style={{ color: theme.er }}>{err}</span>}
    </div>
  )
}

function useSubmit(onSave: (body: Record<string, unknown>) => Promise<void>) {
  const [busy, setBusy] = useState(false)
  const [err, setErr]   = useState<string | null>(null)
  const submit = async (body: Record<string, unknown>) => {
    setBusy(true); setErr(null)
    try { await onSave(body) }
    catch (e) { setErr(e instanceof Error ? e.message : 'Could not save that line') }
    finally { setBusy(false) }
  }
  return { busy, err, submit }
}

function SavingForm({ theme, initial, onSave, onCancel }: {
  theme: any; initial?: SavingRow
  onSave: (body: Record<string, unknown>) => Promise<void>; onCancel?: () => void
}) {
  const [f, setF] = useState({
    assessment_id: initial?.assessment_id ?? '',
    reg_no:        initial?.reg_no ?? '',
    vehicle:       initial?.vehicle ?? '',
    repairer:      initial?.repairer ?? '',
    req_auth_date: initial?.req_auth_date ?? localYmd(),
    quote_parts:   initial?.quote_parts ?? '', quote_labour: initial?.quote_labour ?? '',
    quote_paint:   initial?.quote_paint ?? '',
    report_parts:  initial?.report_parts ?? '', report_labour: initial?.report_labour ?? '',
    report_paint:  initial?.report_paint ?? '',
    notes:         initial?.notes ?? '',
  })
  const { busy, err, submit } = useSubmit(onSave)
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setF(s => ({ ...s, [k]: e.target.value }))

  const saving = useMemo(() => ({
    parts:  n(f.quote_parts)  - n(f.report_parts),
    labour: n(f.quote_labour) - n(f.report_labour),
    paint:  n(f.quote_paint)  - n(f.report_paint),
  }), [f])
  const total = saving.parts + saving.labour + saving.paint

  return (
    <form className="space-y-3" onSubmit={e => {
      e.preventDefault()
      submit({
        ...f,
        period: `${f.req_auth_date.slice(0, 7)}-01`,
        quote_parts:  f.quote_parts  || 0, quote_labour:  f.quote_labour  || 0,
        quote_paint:  f.quote_paint  || 0, report_parts:  f.report_parts  || 0,
        report_labour: f.report_labour || 0, report_paint: f.report_paint || 0,
      })
    }}>
      <div className="grid sm:grid-cols-2 lg:grid-cols-5 gap-3">
        <Field theme={theme} label="Job / assessment number">
          <input required className={INPUT_CLASS} style={inputStyle(theme)}
                 value={f.assessment_id} onChange={set('assessment_id')} placeholder="ALPHA-0000002690" />
        </Field>
        <Field theme={theme} label="Registration">
          <input className={INPUT_CLASS} style={inputStyle(theme)}
                 value={f.reg_no} onChange={set('reg_no')} />
        </Field>
        <Field theme={theme} label="Vehicle">
          <input className={INPUT_CLASS} style={inputStyle(theme)}
                 value={f.vehicle} onChange={set('vehicle')} />
        </Field>
        <Field theme={theme} label="Repairer">
          <input className={INPUT_CLASS} style={inputStyle(theme)}
                 value={f.repairer} onChange={set('repairer')} />
        </Field>
        <Field theme={theme} label="Authorised on">
          <input required type="date" className={INPUT_CLASS} style={inputStyle(theme)}
                 value={f.req_auth_date} onChange={set('req_auth_date')} />
        </Field>
      </div>

      <div className="grid sm:grid-cols-2 gap-4">
        <fieldset>
          <legend className="text-[11px] font-bold uppercase tracking-wider mb-2"
                  style={{ color: theme.t2 }}>What the repairer quoted</legend>
          <div className="grid grid-cols-3 gap-2">
            {(['parts', 'labour', 'paint'] as const).map(part => (
              <Field key={part} theme={theme} label={part[0].toUpperCase() + part.slice(1)}>
                <input type="number" step="0.01" min="0" className={INPUT_CLASS}
                       style={inputStyle(theme)}
                       value={f[`quote_${part}` as keyof typeof f] as string}
                       onChange={set(`quote_${part}` as keyof typeof f)} />
              </Field>
            ))}
          </div>
        </fieldset>
        <fieldset>
          <legend className="text-[11px] font-bold uppercase tracking-wider mb-2"
                  style={{ color: theme.t2 }}>What we assessed it at</legend>
          <div className="grid grid-cols-3 gap-2">
            {(['parts', 'labour', 'paint'] as const).map(part => (
              <Field key={part} theme={theme} label={part[0].toUpperCase() + part.slice(1)}>
                <input type="number" step="0.01" min="0" className={INPUT_CLASS}
                       style={inputStyle(theme)}
                       value={f[`report_${part}` as keyof typeof f] as string}
                       onChange={set(`report_${part}` as keyof typeof f)} />
              </Field>
            ))}
          </div>
        </fieldset>
      </div>

      <div className="rounded-md px-3 py-2 flex flex-wrap items-baseline gap-x-5 gap-y-1 tabular-nums"
           style={{ background: total >= 0 ? theme.okB : theme.erB }}>
        <span className="text-[11px] font-bold uppercase tracking-wider"
              style={{ color: total >= 0 ? theme.ok : theme.er }}>
          Saving, worked out for you
        </span>
        <span className="text-lg font-bold" style={{ color: total >= 0 ? theme.ok : theme.er }}>
          P {pula(total)}
        </span>
        <span className="text-[11px]" style={{ color: theme.t2 }}>
          parts P {pula(saving.parts)} · labour P {pula(saving.labour)} · paint P {pula(saving.paint)}
        </span>
      </div>

      <SubmitRow theme={theme} busy={busy} err={err} onCancel={onCancel}
                 label={initial ? 'Save the change' : 'Add this saving'} />
    </form>
  )
}

function SpendForm({ theme, suppliers, initial, onSave, onCancel }: {
  theme: any; suppliers: string[]; initial?: SpendRow
  onSave: (body: Record<string, unknown>) => Promise<void>; onCancel?: () => void
}) {
  const [f, setF] = useState({
    supplier: initial?.supplier ?? '',
    category: initial?.category ?? 'DEALERSHIP',
    month:    (initial?.month ?? localYmd()).slice(0, 7),
    amount:   initial?.amount ?? '',
    notes:    initial?.notes ?? '',
  })
  const { busy, err, submit } = useSubmit(onSave)
  return (
    <form className="space-y-3"
          onSubmit={e => { e.preventDefault(); submit({ ...f, month: `${f.month}-01` }) }}>
      <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-3">
        <Field theme={theme} label="Supplier" hint="Pick an existing name where you can — a new spelling counts as a new supplier.">
          <input required list="parts-suppliers" className={INPUT_CLASS} style={inputStyle(theme)}
                 value={f.supplier} onChange={e => setF(s => ({ ...s, supplier: e.target.value }))} />
          <datalist id="parts-suppliers">
            {suppliers.map(s => <option key={s} value={s} />)}
          </datalist>
        </Field>
        <Field theme={theme} label="Where from">
          <select className={INPUT_CLASS} style={inputStyle(theme)}
                  value={f.category} onChange={e => setF(s => ({ ...s, category: e.target.value }))}>
            {CATEGORIES.map(c => <option key={c.value} value={c.value}>{c.label}</option>)}
          </select>
        </Field>
        <Field theme={theme} label="Month">
          <input required type="month" className={INPUT_CLASS} style={inputStyle(theme)}
                 value={f.month} onChange={e => setF(s => ({ ...s, month: e.target.value }))} />
        </Field>
        <Field theme={theme} label="Amount (BWP)">
          <input required type="number" step="0.01" min="0" className={INPUT_CLASS}
                 style={inputStyle(theme)}
                 value={f.amount} onChange={e => setF(s => ({ ...s, amount: e.target.value }))} />
        </Field>
      </div>
      <SubmitRow theme={theme} busy={busy} err={err} onCancel={onCancel}
                 label={initial ? 'Save the change' : 'Add this purchase'} />
    </form>
  )
}

function ContractForm({ theme, initial, onSave, onCancel }: {
  theme: any; initial?: ContractRow
  onSave: (body: Record<string, unknown>) => Promise<void>; onCancel?: () => void
}) {
  const [f, setF] = useState({
    month:  (initial?.month ?? localYmd()).slice(0, 7),
    amount: initial?.amount ?? '',
    notes:  initial?.notes ?? '',
  })
  const { busy, err, submit } = useSubmit(onSave)
  return (
    <form className="space-y-3"
          onSubmit={e => { e.preventDefault(); submit({ ...f, month: `${f.month}-01` }) }}>
      <div className="grid sm:grid-cols-3 gap-3">
        <Field theme={theme} label="Month">
          <input required type="month" className={INPUT_CLASS} style={inputStyle(theme)}
                 value={f.month} onChange={e => setF(s => ({ ...s, month: e.target.value }))} />
        </Field>
        <Field theme={theme} label="Matched by the repairer (BWP)"
               hint="Parts the panel beater supplied at our pricing, so we did not source them.">
          <input required type="number" step="0.01" min="0" className={INPUT_CLASS}
                 style={inputStyle(theme)}
                 value={f.amount} onChange={e => setF(s => ({ ...s, amount: e.target.value }))} />
        </Field>
      </div>
      <SubmitRow theme={theme} busy={busy} err={err} onCancel={onCancel}
                 label={initial ? 'Save the change' : 'Add this figure'} />
    </form>
  )
}
