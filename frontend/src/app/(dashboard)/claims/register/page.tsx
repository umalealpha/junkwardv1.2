'use client'

/**
 * /claims/register — Claims Register (Bokani 2026-06-24).
 *
 * Every Graphite V2 claim on ONE screen with filters + Excel download, the same
 * way the RealPay tab was simplified — so the team stops going claim-by-claim
 * in Graphite. Read-only mirror (integrations.GraphiteClaim), filled by the
 * pull_graphite_claims sweep.
 *
 * Look: editorial-minimal / data-dense (design-guides) on the Alpha Direct
 * Finance palette — navy #0D1B2A + orange #F4A623 used as a surgical accent.
 */
import { useCallback, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { getToken, getClaimsRegister, downloadClaimsRegister } from '@/lib/api'
import type { ClaimsRegister, ClaimsRegisterFilters, ClaimsBreakdown } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { MajorClaimsPanel } from '@/components/graphite/MajorClaimsPanel'
import { ClaimsReconciliationPanel } from '@/components/claims/ClaimsReconciliationPanel'
import { Button } from '@/components/ui/button'
import { FileSpreadsheet, Download, Search, Filter, RefreshCw, X } from 'lucide-react'

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'
const STATUS_COLOR: Record<string, string> = {
  Closed: '#059669', Pending: '#D97706', Approved: '#2563EB',
  Reopen: '#7C3AED', Rejected: '#DC2626', '(blank)': '#9CA3AF',
}
const barColor = (i: number) => ['#0D1B2A', '#2E6FB7', '#F4A623', '#059669', '#7C3AED', '#9CA3AF'][i % 6]
const fmtP = (s: string | null | undefined) => {
  const v = Number(s)
  if (!s || !isFinite(v) || v === 0) return '—'
  return 'P ' + v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function Bars({ title, rows, total, onPick }: {
  title: string; rows: ClaimsBreakdown[]; total: number; onPick?: (k: string) => void
}) {
  const top = rows.filter(r => r.key !== '(blank)').slice(0, 6)
  return (
    <div className="rounded-xl border border-[#E4E4E7] bg-white p-4">
      <div className="text-[11px] font-semibold uppercase tracking-wide text-[#6B6B76] mb-3">{title}</div>
      <div className="space-y-2">
        {top.length === 0 && <div className="text-xs text-[#A0A0AB]">—</div>}
        {top.map((r, i) => {
          const pct = total ? Math.round((r.count / total) * 100) : 0
          return (
            <button key={r.key} onClick={() => onPick?.(r.key)} disabled={!onPick}
              className="w-full group text-left" title={`${r.key}: ${r.count}`}>
              <div className="flex items-center justify-between text-xs mb-0.5">
                <span className="truncate text-[#374151] group-hover:text-[#0D1B2A]" style={{ maxWidth: '70%' }}>{r.key}</span>
                <span className="font-mono text-[#6B6B76]">{r.count.toLocaleString()}</span>
              </div>
              <div className="h-1.5 rounded-full bg-[#F4F4F5] overflow-hidden">
                <div className="h-full rounded-full transition-all duration-500"
                  style={{ width: `${Math.max(pct, 2)}%`, background: barColor(i) }} />
              </div>
            </button>
          )
        })}
      </div>
    </div>
  )
}

export default function ClaimsRegisterPage() {
  const router = useRouter()
  const [f, setF] = useState<ClaimsRegisterFilters>({ page: 1, per_page: 50 })
  const [data, setData] = useState<ClaimsRegister | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [shown, setShown] = useState(false)

  const load = useCallback(async (filters: ClaimsRegisterFilters) => {
    setLoading(true); setError(null)
    try { setData(await getClaimsRegister(filters)); setShown(true) }
    catch (e) { setError(e instanceof Error ? e.message : 'Failed to load claims') }
    finally { setLoading(false) }
  }, [])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load(f)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function apply(next: Partial<ClaimsRegisterFilters>) {
    const merged = { ...f, ...next, page: next.page ?? 1 }
    setF(merged); load(merged)
  }
  function clearAll() {
    const cleared = { page: 1, per_page: 50 }
    setF(cleared); load(cleared)
  }
  async function onDownload() {
    setBusy(true); setError(null)
    try { await downloadClaimsRegister(f) }
    catch (e) { setError(e instanceof Error ? e.message : 'Download failed') }
    finally { setBusy(false) }
  }

  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.per_page)) : 1
  const activeFilters = [f.status, f.claim_type, f.product, f.handler, f.search, f.from, f.to].filter(Boolean).length

  return (
    <div className="min-h-screen bg-[#FAFAFA]">
      <TopBar title="Claims Register" />
      <div className={`max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 transition-all duration-500 ${shown ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-2'}`}>

        {/* Branded header */}
        <div className="rounded-2xl mb-5 p-5 flex items-center gap-4 text-white"
          style={{ background: `linear-gradient(135deg, ${NAVY} 0%, #16263a 100%)` }}>
          <div className="w-11 h-11 rounded-xl flex items-center justify-center shrink-0" style={{ background: 'rgba(244,166,35,0.15)' }}>
            <FileSpreadsheet className="w-5 h-5" style={{ color: ORANGE }} />
          </div>
          <div className="flex-1 min-w-0">
            <h1 className="text-xl font-bold leading-tight">Claims Register</h1>
            <p className="text-sm text-white/70">
              Every claim on one screen — filter, then download to Excel.
              {data?.last_synced && <span> · synced {new Date(data.last_synced).toLocaleString()}</span>}
            </p>
          </div>
          <Button onClick={onDownload} disabled={busy || loading}
            className="bg-[#F4A623] hover:bg-[#e0991c] text-[#0D1B2A] font-semibold shrink-0">
            {busy ? <RefreshCw className="w-4 h-4 mr-2 animate-spin" /> : <Download className="w-4 h-4 mr-2" />}
            Download Excel
          </Button>
        </div>

        {/* KPI + breakdowns */}
        {data && (
          <div className="grid grid-cols-1 lg:grid-cols-4 gap-4 mb-5">
            <div className="rounded-xl border border-[#E4E4E7] bg-white p-5 flex flex-col justify-center">
              <div className="text-[11px] font-semibold uppercase tracking-wide text-[#6B6B76]">Claims shown</div>
              <div className="text-4xl font-bold mt-1" style={{ color: NAVY }}>{data.total.toLocaleString()}</div>
              <div className="grid grid-cols-2 gap-2 mt-3">
                <div>
                  <div className="text-[10px] uppercase tracking-wide text-[#6B6B76]">Total reserve</div>
                  <div className="text-sm font-semibold" style={{ color: '#2E6FB7' }}>{fmtP(data.total_reserve)}</div>
                </div>
                <div>
                  <div className="text-[10px] uppercase tracking-wide text-[#6B6B76]">Total payment</div>
                  <div className="text-sm font-semibold" style={{ color: '#059669' }}>{fmtP(data.total_payment)}</div>
                </div>
              </div>
              <div className="flex flex-wrap gap-1.5 mt-3">
                {data.by_status.slice(0, 4).map(s => (
                  <span key={s.key} className="text-[10px] px-2 py-0.5 rounded-full text-white"
                    style={{ background: STATUS_COLOR[s.key] || '#6B7280' }}>{s.key} {s.count.toLocaleString()}</span>
                ))}
              </div>
            </div>
            <Bars title="By status" rows={data.by_status} total={data.total} onPick={k => apply({ status: k })} />
            <Bars title="By claim type" rows={data.by_type} total={data.total} onPick={k => apply({ claim_type: k })} />
            <Bars title="By product" rows={data.by_product} total={data.total} onPick={k => apply({ product: k })} />
          </div>
        )}

        {/* Filters */}
        <div className="rounded-xl border border-[#E4E4E7] bg-white p-4 mb-4">
          <div className="flex items-center gap-2 mb-3 text-sm font-medium text-[#374151]">
            <Filter className="w-4 h-4" style={{ color: ORANGE }} /> Filters
            {activeFilters > 0 && (
              <button onClick={clearAll} className="ml-auto text-xs text-[#6B6B76] hover:text-[#0D1B2A] flex items-center gap-1">
                <X className="w-3 h-3" /> Clear {activeFilters}
              </button>
            )}
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            <div className="lg:col-span-2">
              <label className="block text-xs text-[#6B6B76] mb-1">Search — claim #, policy #, customer</label>
              <div className="relative">
                <Search className="w-4 h-4 absolute left-2.5 top-2.5 text-[#A0A0AB]" />
                <input defaultValue={f.search || ''}
                  onKeyDown={e => { if (e.key === 'Enter') apply({ search: (e.target as HTMLInputElement).value }) }}
                  className="w-full border border-[#E4E4E7] rounded-lg pl-8 pr-2 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[#F4A623]/40 focus:border-[#F4A623]"
                  placeholder="Type and press Enter" />
              </div>
            </div>
            <div>
              <label className="block text-xs text-[#6B6B76] mb-1">Claim type</label>
              <select aria-label="Claim type" value={f.claim_type || ''} onChange={e => apply({ claim_type: e.target.value })}
                className="w-full border border-[#E4E4E7] rounded-lg px-2 py-2 text-sm bg-white focus:ring-2 focus:ring-[#F4A623]/40">
                <option value="">All types</option>
                {(data?.by_type || []).filter(t => t.key !== '(blank)').map(t => (
                  <option key={t.key} value={t.key}>{t.key} ({t.count})</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs text-[#6B6B76] mb-1">Product</label>
              <select aria-label="Product" value={f.product || ''} onChange={e => apply({ product: e.target.value })}
                className="w-full border border-[#E4E4E7] rounded-lg px-2 py-2 text-sm bg-white focus:ring-2 focus:ring-[#F4A623]/40">
                <option value="">All products</option>
                {(data?.by_product || []).filter(p => p.key !== '(blank)').map(p => (
                  <option key={p.key} value={p.key}>{p.key} ({p.count})</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs text-[#6B6B76] mb-1">Registered from</label>
              <input aria-label="Registered from" type="date" value={f.from || ''} onChange={e => apply({ from: e.target.value })}
                className="w-full border border-[#E4E4E7] rounded-lg px-2 py-2 text-sm focus:ring-2 focus:ring-[#F4A623]/40" />
            </div>
            <div>
              <label className="block text-xs text-[#6B6B76] mb-1">Registered to</label>
              <input aria-label="Registered to" type="date" value={f.to || ''} onChange={e => apply({ to: e.target.value })}
                className="w-full border border-[#E4E4E7] rounded-lg px-2 py-2 text-sm focus:ring-2 focus:ring-[#F4A623]/40" />
            </div>
          </div>
        </div>

        {error && <div className="mb-3 text-sm text-[#991B1B] bg-[#FEF2F2] border border-[#FECACA] rounded-lg px-3 py-2">{error}</div>}

        {/* Omni vs Graphite — Bokani's reconciliation (31883c46) */}
        <ClaimsReconciliationPanel from={f.from} to={f.to} />

        {/* Table */}
        <div className="rounded-xl border border-[#E4E4E7] bg-white overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-[#FAFAFA] text-[11px] uppercase tracking-wide text-[#6B6B76] sticky top-0">
                <tr className="border-b border-[#E4E4E7]">
                  <th className="px-4 py-3 text-left font-semibold">Claim #</th>
                  <th className="px-4 py-3 text-left font-semibold">Status</th>
                  <th className="px-4 py-3 text-left font-semibold">Type</th>
                  <th className="px-4 py-3 text-left font-semibold">Product</th>
                  <th className="px-4 py-3 text-left font-semibold">Policy #</th>
                  <th className="px-4 py-3 text-left font-semibold">Customer</th>
                  <th className="px-4 py-3 text-left font-semibold">Handler</th>
                  <th className="px-4 py-3 text-left font-semibold">Reported Date</th>
                  <th className="px-4 py-3 text-left font-semibold">Date of Loss</th>
                  <th className="px-4 py-3 text-left font-semibold">Damage Cause</th>
                  <th className="px-4 py-3 text-right font-semibold">Reserve</th>
                  <th className="px-4 py-3 text-right font-semibold">Payment</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#F4F4F5]">
                {loading ? (
                  <tr><td colSpan={12} className="px-4 py-12 text-center text-[#A0A0AB]">Loading…</td></tr>
                ) : (data?.results || []).length === 0 ? (
                  <tr><td colSpan={12} className="px-4 py-12 text-center text-[#A0A0AB]">No claims match these filters.</td></tr>
                ) : data!.results.map(c => (
                  <tr key={c.id} className="hover:bg-[#FAFAFA] transition-colors">
                    <td className="px-4 py-2.5 font-mono text-xs text-[#0D1B2A]">{c.claim_number || '—'}</td>
                    <td className="px-4 py-2.5">
                      <span className="text-[10px] px-2 py-0.5 rounded-full text-white font-medium"
                        style={{ background: STATUS_COLOR[c.status] || '#6B7280' }}>{c.status || '—'}</span>
                    </td>
                    <td className="px-4 py-2.5 text-[#374151]">{c.claim_type || '—'}</td>
                    <td className="px-4 py-2.5 text-[#374151]">{c.product_name || '—'}</td>
                    <td className="px-4 py-2.5 font-mono text-xs text-[#6B6B76]">{c.policy_number || '—'}</td>
                    <td className="px-4 py-2.5 text-[#374151]">{c.customer_name || '—'}</td>
                    <td className="px-4 py-2.5 text-[#6B6B76]">{c.claim_handler || '—'}</td>
                    <td className="px-4 py-2.5 text-[#374151]">{c.registered_date || '—'}</td>
                    <td className="px-4 py-2.5 text-[#6B6B76]">{c.date_of_loss || '—'}</td>
                    <td className="px-4 py-2.5 text-[#374151]">{c.damage_cause || '—'}</td>
                    <td className="px-4 py-2.5 text-right font-mono text-xs" style={{ color: '#2E6FB7' }}>{fmtP(c.total_reserve)}</td>
                    <td className="px-4 py-2.5 text-right font-mono text-xs" style={{ color: '#059669' }}>{fmtP(c.total_payment)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {data && data.total > data.per_page && (
            <div className="flex items-center justify-between px-4 py-3 border-t border-[#E4E4E7] text-sm bg-[#FAFAFA]">
              <span className="text-[#6B6B76]">Page {data.page} of {totalPages} · {data.total.toLocaleString()} claims</span>
              <div className="flex gap-2">
                <Button variant="outline" disabled={data.page <= 1} onClick={() => apply({ page: data.page - 1 })}>Prev</Button>
                <Button variant="outline" disabled={data.page >= totalPages} onClick={() => apply({ page: data.page + 1 })}>Next</Button>
              </div>
            </div>
          )}
        </div>
        <p className="text-xs text-[#A0A0AB] mt-3">{data?.source} · preliminary — please test and advise.</p>

        {/* The register pages through everything in date order, so the biggest
            exposures never surface on their own. Graphite's major-claims feed
            puts them at the top. */}
        <div className="mt-8">
          <MajorClaimsPanel />
        </div>
      </div>
    </div>
  )
}
