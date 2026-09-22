'use client'

/**
 * /bonu/schedule — the accountant's BONU workbook, captured in Omni and EDITABLE.
 *
 * CFO 2026-08-12: the schedule came in as an emailed Excel file from Accounts.
 * "we need full info in that system and I want people to use omni rather excel to
 * capture all these info going forward" — so every sheet is loaded verbatim (no
 * column or row dropped) and staff add / edit / delete rows here instead of Excel.
 *
 * Backend: /bonu/schedule/ (sheets + reconciliation) · /bonu/schedule/<key>/rows/
 *          (page + add) · /bonu/schedule/rows/<id>/ (edit / delete)
 */

import { Fragment, useCallback, useEffect, useRef, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { apiFetch, apiFetchRaw, saveBlob } from '@/lib/api'
import { Loader2, Plus, Pencil, Trash2, Check, X, Download, Upload, History } from 'lucide-react'
import { AMBER, BonuTabs, GREEN, LINE, NAVY, ORANGE, RED, Stat, cellText, money2 } from '../_shared'

interface SheetBrief {
  key: string; title: string; columns: string[]; amount_column: string
  rows: number; total: string; source_note: string
}
interface Row { id: string; position: number; cells: Record<string, string>; note: string; updated_by: string; updated_at: string | null }
interface Recon { claims_reconciliation: { schedule_total: string; omni_ledger_total: string; difference: string } }
interface Finding {
  code: string; severity: 'critical' | 'high' | 'low'; title: string; detail: string
  where: Record<string, string | number>; amount: string | null
}
interface Validation { critical_count: number; high_count: number; low_count: number; findings: Finding[] }
interface SheetsResp { sheets: SheetBrief[]; reconciliation: Recon; validation?: Validation }
interface RowsResp {
  sheet: SheetBrief; rows: Row[]; offset: number; limit: number; total: number
  firm_column: string; firms: string[]; filtered_total: string
}

const PAGE = 100
const num = (s: string) => { const n = Number(String(s).replace(/[^0-9.-]/g, '')); return Number.isFinite(n) ? n : NaN }

export default function BonuSchedulePage() {
  const [sheets, setSheets] = useState<SheetBrief[]>([])
  const [recon, setRecon] = useState<Recon | null>(null)
  const [validation, setValidation] = useState<Validation | null>(null)
  const [showAllFindings, setShowAllFindings] = useState(false)
  const [activeKey, setActiveKey] = useState<string>('')
  const [rows, setRows] = useState<Row[]>([])
  const [sheet, setSheet] = useState<SheetBrief | null>(null)
  const [offset, setOffset] = useState(0)
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [editId, setEditId] = useState<string | null>(null)   // 'new' for a fresh row
  const [draft, setDraft] = useState<Record<string, string>>({})
  // filters (server-side, across the whole sheet)
  const [q, setQ] = useState('')
  const [firm, setFirm] = useState('')
  const [amountMin, setAmountMin] = useState('')
  const [amountMax, setAmountMax] = useState('')
  const [firms, setFirms] = useState<string[]>([])
  const [firmColumn, setFirmColumn] = useState('')
  const [filteredTotal, setFilteredTotal] = useState('0')
  // upload-new-month (preview diff → apply) + per-row history
  const [diff, setDiff] = useState<any[] | null>(null)
  const [pending, setPending] = useState<File | null>(null)
  const [histFor, setHistFor] = useState<string | null>(null)
  const [hist, setHist] = useState<any[]>([])

  const loadSheets = useCallback(async () => {
    const r = await apiFetch<SheetsResp>('/bonu/schedule/').catch(() => null)
    if (!r) { setMsg('Could not load the schedule.'); setLoading(false); return }
    setSheets(r.sheets); setRecon(r.reconciliation); setValidation(r.validation ?? null)
    setActiveKey(k => k || (r.sheets[0]?.key ?? ''))
    setLoading(false)
  }, [])

  const filtersRef = useRef({ q: '', firm: '', amountMin: '', amountMax: '' })
  useEffect(() => { filtersRef.current = { q, firm, amountMin, amountMax } }, [q, firm, amountMin, amountMax])

  const loadRows = useCallback(async (key: string, off: number) => {
    if (!key) return
    setBusy(true)
    const f = filtersRef.current
    const p = new URLSearchParams({ offset: String(off), limit: String(PAGE) })
    if (f.q) p.set('q', f.q)
    if (f.firm) p.set('firm', f.firm)
    if (f.amountMin) p.set('amount_min', f.amountMin)
    if (f.amountMax) p.set('amount_max', f.amountMax)
    const r = await apiFetch<RowsResp>(`/bonu/schedule/${key}/rows/?${p.toString()}`).catch(() => null)
    setBusy(false)
    if (!r) { setMsg('Could not load rows.'); return }
    setRows(r.rows); setSheet(r.sheet); setTotal(r.total); setOffset(r.offset)
    setFirms(r.firms || []); setFirmColumn(r.firm_column || ''); setFilteredTotal(r.filtered_total ?? '0')
    setEditId(null); setDraft({})
  }, [])

  useEffect(() => { loadSheets() }, [loadSheets])
  // On sheet change, clear filters then load fresh.
  useEffect(() => {
    if (!activeKey) return
    setQ(''); setFirm(''); setAmountMin(''); setAmountMax('')
    filtersRef.current = { q: '', firm: '', amountMin: '', amountMax: '' }
    loadRows(activeKey, 0)
  }, [activeKey, loadRows])

  const runSearch = () => { filtersRef.current = { q, firm, amountMin, amountMax }; loadRows(activeKey, 0) }
  const clearFilters = () => {
    setQ(''); setFirm(''); setAmountMin(''); setAmountMax('')
    filtersRef.current = { q: '', firm: '', amountMin: '', amountMax: '' }
    loadRows(activeKey, 0)
  }
  const hasFilter = !!(q || firm || amountMin || amountMax)

  const columns = sheet?.columns ?? []
  const amountCol = sheet?.amount_column ?? ''

  const startEdit = (r: Row) => { setEditId(r.id); setDraft({ ...r.cells }) }
  const startAdd = () => {
    const blank: Record<string, string> = {}; columns.forEach(c => { blank[c] = '' })
    setDraft(blank); setEditId('new')
  }
  const cancel = () => { setEditId(null); setDraft({}) }

  const saveEdit = async () => {
    if (!editId) return
    setBusy(true)
    try {
      if (editId === 'new') {
        await apiFetch<Row>(`/bonu/schedule/${activeKey}/rows/`,
          { method: 'POST', body: JSON.stringify({ cells: draft }) })
        setMsg('Row added.')
      } else {
        await apiFetch<Row>(`/bonu/schedule/rows/${editId}/`,
          { method: 'PATCH', body: JSON.stringify({ cells: draft }) })
        setMsg('Row saved.')
      }
      await Promise.all([loadRows(activeKey, offset), loadSheets()])
    } catch { setMsg('Save failed.') } finally { setBusy(false) }
  }

  const del = async (r: Row) => {
    if (!confirm('Delete this row? This cannot be undone.')) return
    setBusy(true)
    try {
      await apiFetch(`/bonu/schedule/rows/${r.id}/`, { method: 'DELETE' })
      setMsg('Row deleted.')
      await Promise.all([loadRows(activeKey, offset), loadSheets()])
    } catch { setMsg('Delete failed.') } finally { setBusy(false) }
  }

  const exportCsv = async () => {
    const r = await apiFetchRaw(`/bonu/schedule/${activeKey}/export/`)
    saveBlob(await r.blob(), `bonu_${activeKey}.csv`)
  }
  const onPickFile = async (file: File | null) => {
    if (!file) return
    setPending(file); setBusy(true); setMsg(null)
    const fd = new FormData(); fd.append('file', file)
    try {
      const r = await apiFetch<{ sheets: any[] }>(`/bonu/schedule/upload/?preview=1`, { method: 'POST', body: fd })
      setDiff(r.sheets)
    } catch { setMsg('Could not read that file.'); setPending(null) } finally { setBusy(false) }
  }
  const applyUpload = async () => {
    if (!pending) return
    setBusy(true)
    const fd = new FormData(); fd.append('file', pending)
    try {
      await apiFetch(`/bonu/schedule/upload/`, { method: 'POST', body: fd })
      setMsg('New workbook imported.'); setDiff(null); setPending(null)
      await Promise.all([loadSheets(), loadRows(activeKey, 0)])
    } catch { setMsg('Import failed.') } finally { setBusy(false) }
  }
  const showHistory = async (id: string) => {
    if (histFor === id) { setHistFor(null); return }
    setHistFor(id); setHist([])
    const r = await apiFetch<{ history: any[] }>(`/bonu/schedule/rows/${id}/history/`).catch(() => null)
    setHist(r?.history ?? [])
  }

  const rc = recon?.claims_reconciliation
  const gap = rc ? num(rc.difference) : NaN
  const pageStart = total === 0 ? 0 : offset + 1
  const pageEnd = Math.min(offset + rows.length, total)

  if (loading) return (
    <div className="min-h-screen" style={{ background: '#F6F8FB' }}>
      <TopBar title="BONU — schedule" />
      <div className="mx-auto max-w-[1500px] px-6 py-5"><BonuTabs active="/bonu/schedule" />
        <div className="mt-10 flex items-center gap-2 text-sm text-gray-500"><Loader2 className="h-4 w-4 animate-spin" /> Loading…</div>
      </div>
    </div>
  )

  return (
    <div className="min-h-screen" style={{ background: '#F6F8FB' }}>
      <TopBar title="BONU — schedule" />
      <div className="mx-auto max-w-[1500px] px-6 py-5">
        <BonuTabs active="/bonu/schedule" />

        {/* Exceptions the machine finds inside the schedule itself — every check
            Kutlo's file surfaced on 13-Aug now runs against live data. */}
        {validation && (validation.critical_count + validation.high_count + validation.low_count) > 0 && (
          <Card className="mt-5" style={{ borderColor: validation.critical_count > 0 ? RED : AMBER }}>
            <CardContent className="p-4">
              <div className="flex items-center justify-between">
                <div className="text-sm font-semibold" style={{ color: NAVY }}>
                  Exceptions in the schedule
                </div>
                <div className="text-xs text-gray-500">
                  {validation.critical_count > 0 && <span style={{ color: RED }} className="mr-2">Critical {validation.critical_count}</span>}
                  {validation.high_count > 0 && <span style={{ color: AMBER }} className="mr-2">High {validation.high_count}</span>}
                  {validation.low_count > 0 && <span className="mr-2">Low {validation.low_count}</span>}
                </div>
              </div>
              <ul className="mt-2 space-y-1.5">
                {(showAllFindings ? validation.findings : validation.findings.slice(0, 5)).map((f, i) => (
                  <li key={i} className="text-[13px] flex items-start gap-2">
                    <span
                      className="mt-1 inline-block h-2 w-2 shrink-0 rounded-full"
                      style={{ background: f.severity === 'critical' ? RED : f.severity === 'high' ? AMBER : '#9CA3AF' }}
                    />
                    <div>
                      <span style={{ color: NAVY }} className="font-medium">{f.title}</span>
                      <div className="text-gray-600">{f.detail}</div>
                    </div>
                  </li>
                ))}
              </ul>
              {validation.findings.length > 5 && (
                <button
                  onClick={() => setShowAllFindings(v => !v)}
                  className="mt-2 text-xs underline"
                  style={{ color: NAVY }}
                >
                  {showAllFindings ? 'Show fewer' : `Show all ${validation.findings.length}`}
                </button>
              )}
            </CardContent>
          </Card>
        )}

        {/* Reconciliation: what the schedule says vs what the ledger holds */}
        {rc && (
          <Card className="mt-5"><CardContent className="p-4">
            <div className="mb-2 text-sm font-semibold" style={{ color: NAVY }}>Claims — schedule vs Omni ledger</div>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
              <Stat label="Schedule (this workbook)" value={money2(num(rc.schedule_total))} tone={NAVY} />
              <Stat label="Omni ledger (billed detail)" value={money2(num(rc.omni_ledger_total))} tone={ORANGE} />
              <Stat label="Not yet in the ledger" value={money2(gap)} tone={Number.isFinite(gap) && gap > 0 ? RED : GREEN} />
            </div>
            <div className="mt-2 text-xs text-gray-500">
              The schedule holds the full firm-by-firm detail; the ledger figure is what has posted. A gap is claim
              detail captured here that the general ledger does not yet carry — not a discrepancy to fear, a to-do.
            </div>
          </CardContent></Card>
        )}

        {/* Sheet selector */}
        <div className="mt-5 flex flex-wrap gap-2">
          {sheets.map(s => {
            const on = s.key === activeKey
            return (
              <button key={s.key} onClick={() => setActiveKey(s.key)}
                className="rounded-full px-3 py-1.5 text-sm font-medium transition"
                style={{ background: on ? NAVY : '#fff', color: on ? '#fff' : NAVY, border: `1px solid ${on ? NAVY : LINE}` }}>
                {s.title} <span style={{ opacity: 0.7 }}>· {s.rows}</span>
              </button>
            )
          })}
        </div>

        {msg && <div className="mt-3 text-sm" style={{ color: NAVY }}>{msg}</div>}

        {/* Filters — search across the whole sheet, by firm, and by amount */}
        <div className="mt-4 flex flex-wrap items-end gap-2 rounded-lg border bg-white px-3 py-3" style={{ borderColor: LINE }}>
          <div className="flex flex-col">
            <label htmlFor="bonu-sched-q" className="mb-1 text-xs text-gray-500">Search (firm, client, invoice, matter…)</label>
            <input id="bonu-sched-q" value={q} onChange={e => setQ(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') runSearch() }}
              placeholder="type and press Enter" className="w-64 rounded border px-2 py-1.5 text-sm" style={{ borderColor: LINE }} />
          </div>
          {firmColumn && firms.length > 0 && (
            <div className="flex flex-col">
              <label htmlFor="bonu-sched-firm" className="mb-1 text-xs text-gray-500">{firmColumn}</label>
              <select id="bonu-sched-firm" value={firm} onChange={e => { const v = e.target.value; setFirm(v); filtersRef.current = { q, firm: v, amountMin, amountMax }; loadRows(activeKey, 0) }}
                className="w-56 rounded border px-2 py-1.5 text-sm" style={{ borderColor: LINE }}>
                <option value="">All firms</option>
                {firms.map(f => <option key={f} value={f}>{f}</option>)}
              </select>
            </div>
          )}
          {sheet?.amount_column && (
            <div className="flex flex-col">
              <label className="mb-1 text-xs text-gray-500">Amount {sheet.amount_column} — min / max</label>
              <div className="flex items-center gap-1">
                <input aria-label="Minimum amount" value={amountMin} onChange={e => setAmountMin(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') runSearch() }}
                  inputMode="decimal" placeholder="min" className="w-24 rounded border px-2 py-1.5 text-sm" style={{ borderColor: LINE }} />
                <span className="text-gray-400">–</span>
                <input aria-label="Maximum amount" value={amountMax} onChange={e => setAmountMax(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') runSearch() }}
                  inputMode="decimal" placeholder="max" className="w-24 rounded border px-2 py-1.5 text-sm" style={{ borderColor: LINE }} />
              </div>
            </div>
          )}
          <button onClick={runSearch} className="rounded-md px-3 py-1.5 text-sm font-medium text-white" style={{ background: NAVY }}>Search</button>
          {hasFilter && <button onClick={clearFilters} className="rounded-md px-3 py-1.5 text-sm" style={{ border: `1px solid ${LINE}`, color: NAVY }}>Clear</button>}
          {hasFilter && sheet?.amount_column && (
            <span className="ml-auto text-sm text-gray-600">Matched Σ <b style={{ color: NAVY }}>{money2(num(filteredTotal))}</b></span>
          )}
        </div>

        {/* The grid */}
        <Card className="mt-3"><CardContent className="p-0">
          <div className="flex items-center justify-between gap-3 border-b px-4 py-3" style={{ borderColor: LINE }}>
            <div className="text-sm">
              <span className="font-semibold" style={{ color: NAVY }}>{sheet?.title}</span>
              <span className="text-gray-500"> · {total} rows{hasFilter ? ' (filtered)' : ''}{amountCol ? ` · Σ ${amountCol}: ${money2(num(filteredTotal))}` : ''}</span>
            </div>
            <div className="flex items-center gap-2">
              {busy && <Loader2 className="h-4 w-4 animate-spin text-gray-400" />}
              <button onClick={exportCsv}
                className="inline-flex items-center gap-1 rounded-md px-3 py-1.5 text-sm font-medium"
                style={{ border: `1px solid ${LINE}`, color: NAVY }}><Download className="h-4 w-4" /> Export CSV</button>
              <label className="inline-flex cursor-pointer items-center gap-1 rounded-md px-3 py-1.5 text-sm font-medium"
                style={{ border: `1px solid ${LINE}`, color: NAVY }}>
                <Upload className="h-4 w-4" /> Upload new month
                <input aria-label="Schedule workbook to upload"
                  type="file" accept=".xlsx" className="hidden"
                  onChange={e => onPickFile(e.target.files?.[0] ?? null)} />
              </label>
              <button onClick={startAdd} disabled={editId === 'new'}
                className="inline-flex items-center gap-1 rounded-md px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
                style={{ background: NAVY }}><Plus className="h-4 w-4" /> Add row</button>
            </div>
          </div>

          {diff && (
            <div className="border-b px-4 py-3" style={{ borderColor: LINE, background: '#FFFBEB' }}>
              <div className="mb-2 text-sm font-semibold" style={{ color: NAVY }}>What this file changes — review before you apply</div>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead><tr className="text-left text-gray-500"><th className="px-2 py-1">Sheet</th><th className="px-2 py-1 text-right">Now</th><th className="px-2 py-1 text-right">In file</th><th className="px-2 py-1 text-right">New</th><th className="px-2 py-1 text-right">Gone</th><th className="px-2 py-1 text-right">Unchanged</th></tr></thead>
                  <tbody>{diff.map(s => (
                    <tr key={s.key} style={{ borderTop: `1px solid ${LINE}` }}>
                      <td className="px-2 py-1 font-medium">{s.title}</td>
                      <td className="px-2 py-1 text-right tabular-nums">{s.current_rows}</td>
                      <td className="px-2 py-1 text-right tabular-nums">{s.file_rows}</td>
                      <td className="px-2 py-1 text-right tabular-nums" style={{ color: GREEN }}>+{s.added}</td>
                      <td className="px-2 py-1 text-right tabular-nums" style={{ color: RED }}>-{s.removed}</td>
                      <td className="px-2 py-1 text-right tabular-nums text-gray-400">{s.unchanged}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
              <div className="mt-2 flex gap-2">
                <button onClick={applyUpload} disabled={busy} className="rounded-md px-3 py-1.5 text-sm font-medium text-white" style={{ background: GREEN }}>Apply — replace with this file</button>
                <button onClick={() => { setDiff(null); setPending(null) }} className="rounded-md px-3 py-1.5 text-sm" style={{ border: `1px solid ${LINE}` }}>Cancel</button>
              </div>
            </div>
          )}

          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left" style={{ background: '#F1F5F9', color: '#475569' }}>
                  <th className="whitespace-nowrap px-2 py-2 sticky left-0" style={{ background: '#F1F5F9' }}>#</th>
                  {columns.map(c => <th key={c} className="whitespace-nowrap px-2 py-2">{c}</th>)}
                  <th className="whitespace-nowrap px-2 py-2 text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {editId === 'new' && (
                  <tr style={{ background: '#FFFBEB' }}>
                    <td className="px-2 py-1 sticky left-0" style={{ background: '#FFFBEB' }}>new</td>
                    {columns.map(c => (
                      <td key={c} className="px-1 py-1">
                        <input aria-label={`${c}, new row`}
                          value={draft[c] ?? ''} onChange={e => setDraft(d => ({ ...d, [c]: e.target.value }))}
                          className="w-full min-w-[110px] rounded border px-1.5 py-1" style={{ borderColor: LINE }} />
                      </td>
                    ))}
                    <td className="px-2 py-1 text-right whitespace-nowrap">
                      <button aria-label="Save the new row" title="Save"
                        onClick={saveEdit} disabled={busy} className="mr-1 inline-flex items-center rounded px-2 py-1 text-white" style={{ background: GREEN }}><Check className="h-3.5 w-3.5" /></button>
                      <button aria-label="Discard the new row" title="Cancel"
                        onClick={cancel} className="inline-flex items-center rounded px-2 py-1" style={{ border: `1px solid ${LINE}` }}><X className="h-3.5 w-3.5" /></button>
                    </td>
                  </tr>
                )}
                {rows.map(r => {
                  const editing = editId === r.id
                  return (
                    <Fragment key={r.id}>
                    <tr style={{ borderBottom: `1px solid ${LINE}`, background: editing ? '#FFFBEB' : undefined }}>
                      <td className="px-2 py-1 text-gray-400 sticky left-0" style={{ background: editing ? '#FFFBEB' : '#fff' }}>{r.position + 1}</td>
                      {columns.map(c => (
                        <td key={c} className="px-2 py-1 align-top">
                          {editing
                            ? <input aria-label={`${c}, row ${r.position + 1}`}
                                value={draft[c] ?? ''} onChange={e => setDraft(d => ({ ...d, [c]: e.target.value }))}
                                className="w-full min-w-[110px] rounded border px-1.5 py-1" style={{ borderColor: LINE }} />
                            : <span className={amountCol === c ? 'tabular-nums whitespace-nowrap' : ''}>{cellText(r.cells[c])}</span>}
                        </td>
                      ))}
                      <td className="px-2 py-1 text-right whitespace-nowrap">
                        {editing ? (
                          <>
                            <button aria-label={`Save row ${r.position + 1}`} title="Save"
                              onClick={saveEdit} disabled={busy} className="mr-1 inline-flex items-center rounded px-2 py-1 text-white" style={{ background: GREEN }}><Check className="h-3.5 w-3.5" /></button>
                            <button aria-label={`Cancel editing row ${r.position + 1}`} title="Cancel"
                              onClick={cancel} className="inline-flex items-center rounded px-2 py-1" style={{ border: `1px solid ${LINE}` }}><X className="h-3.5 w-3.5" /></button>
                          </>
                        ) : (
                          <>
                            <button aria-label={`History of row ${r.position + 1}`} title="History"
                              onClick={() => showHistory(r.id)} disabled={!!editId} className="mr-1 inline-flex items-center rounded px-2 py-1 disabled:opacity-40" style={{ border: `1px solid ${LINE}`, color: NAVY }}><History className="h-3.5 w-3.5" /></button>
                            <button aria-label={`Edit row ${r.position + 1}`} title="Edit"
                              onClick={() => startEdit(r)} disabled={!!editId} className="mr-1 inline-flex items-center rounded px-2 py-1 disabled:opacity-40" style={{ border: `1px solid ${LINE}`, color: NAVY }}><Pencil className="h-3.5 w-3.5" /></button>
                            <button aria-label={`Delete row ${r.position + 1}`} title="Delete"
                              onClick={() => del(r)} disabled={!!editId} className="inline-flex items-center rounded px-2 py-1 disabled:opacity-40" style={{ border: `1px solid ${LINE}`, color: RED }}><Trash2 className="h-3.5 w-3.5" /></button>
                          </>
                        )}
                      </td>
                    </tr>
                    {histFor === r.id && (
                      <tr style={{ background: '#F8FAFC' }}>
                        <td colSpan={columns.length + 2} className="px-4 py-2">
                          <div className="text-xs font-medium" style={{ color: NAVY }}>History — who changed what</div>
                          {hist.length === 0 ? <div className="text-xs text-gray-400">No changes recorded yet.</div> : (
                            <ul className="mt-1 space-y-0.5">
                              {hist.map((h, i) => (
                                <li key={i} className="text-xs text-gray-600">
                                  <b>{h.action}</b> · {h.user} · {h.when?.slice(0, 16).replace('T', ' ')}{h.description ? ` · ${h.description}` : ''}
                                </li>
                              ))}
                            </ul>
                          )}
                        </td>
                      </tr>
                    )}
                    </Fragment>
                  )
                })}
                {rows.length === 0 && editId !== 'new' && (
                  <tr><td colSpan={columns.length + 2} className="px-3 py-6 text-center text-gray-400">No rows yet — press “Add row”.</td></tr>
                )}
              </tbody>
            </table>
          </div>

          {/* Pager */}
          <div className="flex items-center justify-between gap-3 border-t px-4 py-3 text-sm" style={{ borderColor: LINE }}>
            <span className="text-gray-500">{pageStart}–{pageEnd} of {total}</span>
            <div className="flex gap-2">
              <button disabled={offset === 0 || busy} onClick={() => loadRows(activeKey, Math.max(0, offset - PAGE))}
                className="rounded-md px-3 py-1.5 disabled:opacity-40" style={{ border: `1px solid ${LINE}` }}>Previous</button>
              <button disabled={offset + PAGE >= total || busy} onClick={() => loadRows(activeKey, offset + PAGE)}
                className="rounded-md px-3 py-1.5 disabled:opacity-40" style={{ border: `1px solid ${LINE}` }}>Next</button>
            </div>
          </div>
        </CardContent></Card>
      </div>
    </div>
  )
}
