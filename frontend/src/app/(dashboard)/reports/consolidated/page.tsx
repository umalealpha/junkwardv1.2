'use client'

/**
 * /reports/consolidated — Consolidated Financial Statements surface.
 *
 * Multi-entity Group BS + P&L. Per-entity columns + Eliminations column +
 * Consolidated total. Fetches BS / P&L per selected company from the
 * existing single-entity endpoints client-side, then sums.
 *
 * CFO directive 2026-05-20: prod doesn't reconcile to the audited Group
 * FY25 consol (lives in the CFO workbook by design — see
 * project_fy25_consolidated_official.md). This surface is the live
 * working-papers consolidator; the audited consol still ships from the
 * workbook until the elim model lands.
 */
import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  getCompanies, getBalanceSheet, getProfitLoss,
} from '@/lib/api'
import type { Company, BalanceSheetReport, ProfitLossReport } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { LoadingTable } from '@/components/ui/loading'
import { cn, exportToCsv, formatAmount, localYmd, parseAmount, today } from '@/lib/utils'
import { useNumberFormat } from '@/contexts/NumberFormatContext'
import {
  Download, AlertCircle, Layers, Calculator, CheckCircle, Info,
  Trash2, Plus,
} from 'lucide-react'

type EntityCol = {
  company: Company
  bs?: BalanceSheetReport
  pl?: ProfitLossReport
  loading: boolean
  error?: string
}

interface Elim {
  id: number
  label: string
  amount: string   // signed BWP — applied to Consol column
  side: 'bs_asset' | 'bs_liab' | 'bs_equity' | 'pl_rev' | 'pl_coi' | 'pl_opex'
}

const ELIM_SIDES: { value: Elim['side']; label: string }[] = [
  { value: 'bs_asset',  label: 'BS — Asset (e.g. intercompany receivable)' },
  { value: 'bs_liab',   label: 'BS — Liability (e.g. intercompany payable)' },
  { value: 'bs_equity', label: 'BS — Equity (e.g. investment-in-sub)' },
  { value: 'pl_rev',    label: 'P&L — Revenue (intra-group sales)' },
  { value: 'pl_coi',    label: 'P&L — Cost of insurance' },
  { value: 'pl_opex',   label: 'P&L — OPEX (intra-group expense)' },
]

export default function ConsolidatedReportPage() {
  const router = useRouter()
  const { mode } = useNumberFormat()
  const fmt = (n: number) => formatAmount(n, 'BWP', mode)

  const [companies, setCompanies] = useState<Company[]>([])
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [from, setFrom] = useState(() => {
    const d = new Date(); d.setMonth(d.getMonth() - 12); return localYmd(d)
  })
  const [to, setTo] = useState(today())
  const [asOf, setAsOf] = useState(today())
  const [cols, setCols] = useState<EntityCol[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [elims, setElims] = useState<Elim[]>([])
  const [nextElimId, setNextElimId] = useState(1)

  useEffect(() => {
    getCompanies({ is_active: 'true' })
      .then(r => {
        const list = (r.results || []).filter(c => c.is_active)
        setCompanies(list)
        // Default selection — all non-consolidated active entities
        const defaults = list
          .filter(c => c.entity_type !== 'consolidated')
          .map(c => c.id)
        setSelectedIds(new Set(defaults))
      })
      .catch(() => setError('Failed to load companies.'))
  }, [])

  function toggle(id: string) {
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }

  async function generate() {
    setError(null)
    const picked = companies.filter(c => selectedIds.has(c.id))
    if (picked.length === 0) {
      setError('Pick at least one entity.')
      return
    }
    setBusy(true)
    const initial: EntityCol[] = picked.map(c => ({ company: c, loading: true }))
    setCols(initial)
    const results = await Promise.all(picked.map(async c => {
      try {
        const [bs, pl] = await Promise.all([
          getBalanceSheet(asOf, c.id),
          getProfitLoss(from, to, c.id),
        ])
        return { company: c, bs, pl, loading: false } as EntityCol
      } catch (err) {
        return {
          company: c, loading: false,
          error: err instanceof Error ? err.message : 'fetch failed',
        } as EntityCol
      }
    }))
    setCols(results)
    setBusy(false)
  }

  // ─── Aggregation ──────────────────────────────────────────────────────────
  function sumGroup(extract: (c: EntityCol) => string | number | undefined): number {
    return cols.reduce((acc, c) => acc + parseAmount(extract(c) ?? 0), 0)
  }

  // Eliminations by side
  const elimByMSide = useMemo(() => {
    const m: Record<Elim['side'], number> = {
      bs_asset: 0, bs_liab: 0, bs_equity: 0, pl_rev: 0, pl_coi: 0, pl_opex: 0,
    }
    for (const e of elims) m[e.side] += parseAmount(e.amount)
    return m
  }, [elims])

  // BS totals (asset / liab / equity) per entity
  function bsAssets(c: EntityCol)  { return c.bs?.totals.total_assets ?? 0 }
  function bsLE(c: EntityCol)      { return c.bs?.totals.liabilities_and_equity ?? 0 }

  // P&L lines
  function plRevenue(c: EntityCol) { return c.pl?.revenue.total ?? 0 }
  function plCOI(c: EntityCol)     { return c.pl?.cost_of_insurance.total ?? 0 }
  function plGross(c: EntityCol)   { return c.pl?.gross_result ?? 0 }
  function plOpex(c: EntityCol)    { return c.pl?.operating_expenses.total ?? 0 }
  function plNP(c: EntityCol)      { return c.pl?.net_profit ?? 0 }

  const sumAssets   = sumGroup(bsAssets) - elimByMSide.bs_asset
  const sumLE       = sumGroup(bsLE) - elimByMSide.bs_liab - elimByMSide.bs_equity
  const sumRev      = sumGroup(plRevenue) - elimByMSide.pl_rev
  const sumCOI      = sumGroup(plCOI) - elimByMSide.pl_coi
  const sumGross    = sumRev - sumCOI
  const sumOpex     = sumGroup(plOpex) - elimByMSide.pl_opex
  const sumNP       = sumGross - sumOpex

  const balanced    = Math.abs(sumAssets - sumLE) < 1

  function addElim() {
    setElims(prev => [...prev,
      { id: nextElimId, label: '', amount: '0', side: 'bs_asset' }])
    setNextElimId(n => n + 1)
  }

  function updElim(id: number, patch: Partial<Elim>) {
    setElims(prev => prev.map(e => e.id === id ? { ...e, ...patch } : e))
  }

  function delElim(id: number) {
    setElims(prev => prev.filter(e => e.id !== id))
  }

  function exportCsv() {
    if (cols.length === 0) return
    const headers = ['Line', ...cols.map(c => c.company.code), 'Eliminations', 'Consolidated']
    const numCol = (extract: (c: EntityCol) => string | number | undefined, elim: number, consol: number, label: string) => ({
      Line: label,
      ...Object.fromEntries(cols.map(c => [c.company.code, parseAmount(extract(c) ?? 0)])),
      Eliminations: elim,
      Consolidated: consol,
    })
    const rows = [
      numCol(plRevenue, elimByMSide.pl_rev,  sumRev,   'Revenue'),
      numCol(plCOI,     elimByMSide.pl_coi,  sumCOI,   'Cost of insurance'),
      numCol(plGross,   0,                   sumGross, 'Gross result'),
      numCol(plOpex,    elimByMSide.pl_opex, sumOpex,  'Operating expenses'),
      numCol(plNP,      0,                   sumNP,    'Net profit'),
      numCol(bsAssets,  elimByMSide.bs_asset, sumAssets, 'Total assets'),
      numCol(bsLE,      elimByMSide.bs_liab + elimByMSide.bs_equity, sumLE, 'Liabilities + Equity'),
    ]
    exportToCsv(rows as any, `consolidated_${asOf}`)
  }

  // ─── Render ───────────────────────────────────────────────────────────────
  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Consolidated Financial Statements"
        breadcrumbs={[{ label: 'Reports', href: '/reports' }, { label: 'Consolidated' }]}
        actions={
          cols.length > 0 && (
            <Button variant="secondary" size="sm"
                    leftIcon={<Download className="w-3.5 h-3.5" />}
                    onClick={exportCsv}>
              Export CSV
            </Button>
          )
        }
      />

      <div className="flex-1 p-6 space-y-6">
        {/* CFO directive notice */}
        <Card className="border-[#FED7AA] bg-[#FFF7ED]">
          <CardContent className="py-3 flex items-start gap-2">
            <Info className="w-4 h-4 text-[#CC6C00] flex-shrink-0 mt-0.5" />
            <div className="text-sm text-[#7C2D12]">
              <strong>Working-papers consolidator.</strong> The audited Group FY25 figures
              (PAT 2.46M, Total Assets 71.8M) ship from the CFO workbook by design — prod
              ledgers don't yet hold the elimination journals. Use this surface for live
              consolidation drafts; quote the workbook for audit / board.
            </div>
          </CardContent>
        </Card>

        {/* Controls */}
        <Card>
          <CardContent className="py-4 space-y-4">
            <div className="flex flex-wrap items-end gap-4">
              <div>
                <label className="block text-xs text-[#374151] font-medium mb-1">P&L From</label>
                <input type="date" value={from} onChange={e => setFrom(e.target.value)}
                       className="bg-white border border-[#D1D5DB] rounded-lg px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="block text-xs text-[#374151] font-medium mb-1">P&L To</label>
                <input type="date" value={to} onChange={e => setTo(e.target.value)}
                       className="bg-white border border-[#D1D5DB] rounded-lg px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="block text-xs text-[#374151] font-medium mb-1">BS As-of</label>
                <input type="date" value={asOf} onChange={e => setAsOf(e.target.value)}
                       className="bg-white border border-[#D1D5DB] rounded-lg px-3 py-2 text-sm" />
              </div>
              <Button variant="primary" size="md" onClick={generate} loading={busy}>
                Consolidate
              </Button>
            </div>

            <div>
              <label className="block text-xs text-[#374151] font-medium mb-2">
                Entities ({selectedIds.size} selected)
              </label>
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2">
                {companies.map(c => (
                  <button key={c.id} type="button" onClick={() => toggle(c.id)}
                          className={cn(
                            'rounded-lg border px-3 py-2 text-left text-sm transition-all',
                            selectedIds.has(c.id)
                              ? 'border-[#F07F00] bg-[#FFF7ED] text-[#7C2D12]'
                              : 'border-[#E5E7EB] bg-white text-[#374151] hover:border-[#D1D5DB]',
                          )}>
                    <div className="font-semibold">{c.code}</div>
                    <div className="text-[11px] opacity-70 truncate">{c.name}</div>
                  </button>
                ))}
              </div>
            </div>
          </CardContent>
        </Card>

        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-xl p-3 flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-[#DC2626]" />
            <p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}

        {busy ? (
          <Card><CardContent className="p-0"><LoadingTable rows={10} cols={4} /></CardContent></Card>
        ) : cols.length === 0 ? (
          <Card>
            <CardContent className="py-12 text-center">
              <Layers className="w-8 h-8 text-[#D1D5DB] mx-auto mb-2" />
              <p className="text-[#9CA3AF] text-sm">Pick entities, dates, then Consolidate.</p>
            </CardContent>
          </Card>
        ) : (
          <>
            {/* Eliminations editor */}
            <Card>
              <CardHeader>
                <CardTitle>
                  <Calculator className="w-4 h-4 inline mr-1" />
                  Eliminations
                </CardTitle>
              </CardHeader>
              <CardContent>
                {elims.length === 0 && (
                  <p className="text-sm text-[#9CA3AF] mb-3">
                    No eliminations yet. Add intercompany balances / intra-group sales /
                    investment-in-sub etc. Amounts subtract from the consol column.
                  </p>
                )}
                <div className="space-y-2 mb-3">
                  {elims.map(e => (
                    <div key={e.id} className="grid grid-cols-12 gap-2 items-center">
                      <input
                        placeholder="Label (e.g. RSA → ADIC IC receivable)"
                        value={e.label}
                        onChange={ev => updElim(e.id, { label: ev.target.value })}
                        className="col-span-5 bg-white border border-[#D1D5DB] rounded-md px-2 py-1.5 text-sm" />
                      <select
                        value={e.side}
                        onChange={ev => updElim(e.id, { side: ev.target.value as Elim['side'] })}
                        className="col-span-4 bg-white border border-[#D1D5DB] rounded-md px-2 py-1.5 text-sm">
                        {ELIM_SIDES.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
                      </select>
                      <input
                        type="number" step="0.01"
                        placeholder="0.00"
                        value={e.amount}
                        onChange={ev => updElim(e.id, { amount: ev.target.value })}
                        className="col-span-2 bg-white border border-[#D1D5DB] rounded-md px-2 py-1.5 text-sm text-right font-mono-nums" />
                      <button type="button" onClick={() => delElim(e.id)}
                              className="col-span-1 inline-flex items-center justify-center text-[#DC2626]">
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  ))}
                </div>
                <Button variant="secondary" size="sm" onClick={addElim}
                        leftIcon={<Plus className="w-3.5 h-3.5" />}>
                  Add elimination
                </Button>
              </CardContent>
            </Card>

            {/* P&L matrix */}
            <Card>
              <CardHeader>
                <CardTitle>Consolidated P&L ({from} → {to})</CardTitle>
              </CardHeader>
              <CardContent className="overflow-x-auto">
                <PLMatrix
                  cols={cols} fmt={fmt}
                  rows={[
                    { label: 'Revenue',             values: cols.map(plRevenue), elim: elimByMSide.pl_rev,  consol: sumRev },
                    { label: 'Cost of insurance',   values: cols.map(plCOI),     elim: elimByMSide.pl_coi,  consol: sumCOI },
                    { label: 'Gross result',        values: cols.map(plGross),   elim: 0,                   consol: sumGross, emphasize: true },
                    { label: 'Operating expenses', values: cols.map(plOpex),    elim: elimByMSide.pl_opex, consol: sumOpex },
                    { label: 'Net profit',          values: cols.map(plNP),      elim: 0,                   consol: sumNP, emphasize: true, color: sumNP >= 0 ? 'text-[#059669]' : 'text-[#DC2626]' },
                  ]}
                />
              </CardContent>
            </Card>

            {/* BS matrix */}
            <Card>
              <CardHeader>
                <CardTitle>Consolidated Balance Sheet (as of {asOf})</CardTitle>
              </CardHeader>
              <CardContent className="overflow-x-auto">
                <PLMatrix
                  cols={cols} fmt={fmt}
                  rows={[
                    { label: 'Total assets',        values: cols.map(bsAssets), elim: elimByMSide.bs_asset,                       consol: sumAssets, emphasize: true, color: 'text-[#2563EB]' },
                    { label: 'Liabilities + Equity',values: cols.map(bsLE),     elim: elimByMSide.bs_liab + elimByMSide.bs_equity, consol: sumLE,     emphasize: true, color: 'text-[#7C3AED]' },
                  ]}
                />
                <div className={cn(
                  'mt-4 flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium',
                  balanced
                    ? 'bg-[#ECFDF5] border border-[#A7F3D0] text-[#059669]'
                    : 'bg-[#FEF2F2] border border-[#FEE2E2] text-[#DC2626]',
                )}>
                  {balanced ? <CheckCircle className="w-4 h-4" /> : <AlertCircle className="w-4 h-4" />}
                  {balanced
                    ? 'Consolidated balance sheet balances.'
                    : `Off by ${fmt(Math.abs(sumAssets - sumLE))} — adjust eliminations.`}
                </div>
              </CardContent>
            </Card>

            {/* Per-entity status */}
            <Card>
              <CardHeader>
                <CardTitle>Source coverage</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
                  {cols.map(c => (
                    <div key={c.company.id} className="rounded-md border border-[#E5E7EB] px-3 py-2 text-sm">
                      <div className="font-semibold text-[#111827]">{c.company.code}</div>
                      <div className="text-xs text-[#6B7280]">{c.company.name}</div>
                      {c.error ? (
                        <div className="text-xs text-[#DC2626] mt-1 inline-flex items-center gap-1">
                          <AlertCircle className="w-3 h-3" /> {c.error}
                        </div>
                      ) : (
                        <div className="text-xs text-[#059669] mt-1 inline-flex items-center gap-1">
                          <CheckCircle className="w-3 h-3" /> BS + P&L loaded
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          </>
        )}
      </div>
    </div>
  )
}

// ─── Matrix renderer ─────────────────────────────────────────────────────────

interface MatrixRow {
  label: string
  values: (string | number)[]   // per-entity column
  elim: number
  consol: number
  emphasize?: boolean
  color?: string
}

function PLMatrix({ cols, rows, fmt }:
                  { cols: EntityCol[]; rows: MatrixRow[]; fmt: (n: number) => string }) {
  return (
    <table className="w-full text-sm border-collapse">
      <thead>
        <tr className="border-b-2 border-[#E5E7EB]">
          <th className="text-left py-2 px-3 text-xs font-semibold uppercase tracking-wider text-[#6B7280]">
            Line item
          </th>
          {cols.map(c => (
            <th key={c.company.id}
                className="text-right py-2 px-3 text-xs font-semibold uppercase tracking-wider text-[#6B7280]">
              {c.company.code}
            </th>
          ))}
          <th className="text-right py-2 px-3 text-xs font-semibold uppercase tracking-wider text-[#CC6C00]">
            Elims
          </th>
          <th className="text-right py-2 px-3 text-xs font-semibold uppercase tracking-wider text-[#0D1B2A]">
            Consolidated
          </th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row, idx) => (
          <tr key={idx} className={cn(
            'border-b border-[#E5E7EB]',
            row.emphasize ? 'bg-[#F9FAFB] font-semibold' : '',
          )}>
            <td className={cn('py-2 px-3 text-[#374151]', row.emphasize ? 'font-bold' : '')}>
              {row.label}
            </td>
            {row.values.map((v, i) => (
              <td key={i} className="text-right py-2 px-3 font-mono-nums text-[#6B7280]">
                {fmt(parseAmount(v))}
              </td>
            ))}
            <td className="text-right py-2 px-3 font-mono-nums text-[#CC6C00]">
              {row.elim !== 0 ? `(${fmt(row.elim)})` : '—'}
            </td>
            <td className={cn('text-right py-2 px-3 font-mono-nums font-bold',
                              row.color || 'text-[#111827]')}>
              {fmt(row.consol)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
