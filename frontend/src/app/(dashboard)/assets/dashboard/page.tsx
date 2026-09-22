'use client'

/**
 * /assets/dashboard — Fixed Assets dashboard.
 * Cost, accumulated depreciation and net book value at a glance, a by-category
 * breakdown, 12-month movements (additions vs depreciation) and a sortable
 * register. Reads /assets/summary/ + /assets/. Populates the moment the PP&E
 * register is imported from Odoo (CFO 2026-08-30).
 */
import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { getAssetSummary, getAssets, getToken, type AssetSummary, type AssetListItem } from '@/lib/api'
import { formatAmount, formatDate } from '@/lib/utils'
import { useNumberFormat } from '@/contexts/NumberFormatContext'
import { useCompany } from '@/contexts/CompanyContext'
import { Boxes, TrendingDown, Wallet, Layers, Download, Upload } from 'lucide-react'

const NAVY = '#1D3270'
const ORANGE = '#F47C20'
const TEAL = '#0A9396'

export default function AssetDashboardPage() {
  const router = useRouter()
  const { mode } = useNumberFormat()
  const { selectedId: companyId } = useCompany()
  const money = (n: number | string) => formatAmount(n, 'BWP', mode)

  const [sum, setSum] = useState<AssetSummary | null>(null)
  const [rows, setRows] = useState<AssetListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [sortBy, setSortBy] = useState('nbv_desc')

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    setLoading(true); setError(null)
    Promise.all([
      getAssetSummary(companyId || undefined),
      getAssets({ page: 1, page_size: 100, ...(companyId ? { company: companyId } : {}) }),
    ])
      .then(([s, r]) => { setSum(s); setRows(r.results || []) })
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [companyId])

  const t = sum?.totals
  const maxCat = Math.max(1, ...(sum?.by_category || []).map(c => c.net_book_value))
  const maxMove = Math.max(1, ...(sum?.movements || []).flatMap(m => [m.additions, m.depreciation]))

  const num = (v: string | number | null | undefined) => {
    const n = typeof v === 'number' ? v : parseFloat(String(v ?? '0')); return isNaN(n) ? 0 : n
  }
  const sortedRows = useMemo(() => {
    const rs = [...rows]
    switch (sortBy) {
      case 'cost_desc': return rs.sort((a, b) => num(b.cost) - num(a.cost))
      case 'name_asc': return rs.sort((a, b) => (a.name || '').localeCompare(b.name || ''))
      case 'purchased_desc': return rs.sort((a, b) => (b.purchase_date || '').localeCompare(a.purchase_date || ''))
      case 'nbv_desc':
      default: return rs.sort((a, b) => num(b.net_book_value) - num(a.net_book_value))
    }
  }, [rows, sortBy])

  const empty = !loading && (t?.count ?? 0) === 0

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Fixed Assets — Dashboard"
        breadcrumbs={[{ label: 'Fixed Assets', href: '/assets' }, { label: 'Dashboard' }]}
        actions={
          <div className="flex gap-2">
            <Button variant="secondary" size="sm" leftIcon={<Upload className="w-3.5 h-3.5" />} onClick={() => router.push('/assets/import')}>Import from Odoo</Button>
            <Button variant="secondary" size="sm" leftIcon={<Boxes className="w-3.5 h-3.5" />} onClick={() => router.push('/assets')}>Register</Button>
          </div>
        }
      />

      <div className="flex-1 p-6 space-y-6">
        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-xl p-3 text-sm text-[#DC2626]">{error}</div>
        )}

        {/* Stat tiles */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          {[
            { label: 'Total cost', value: t ? money(t.cost) : '—', icon: <Wallet className="w-4 h-4" />, color: NAVY },
            { label: 'Accumulated depreciation', value: t ? money(t.accumulated_depreciation) : '—', icon: <TrendingDown className="w-4 h-4" />, color: ORANGE },
            { label: 'Net book value', value: t ? money(t.net_book_value) : '—', icon: <Layers className="w-4 h-4" />, color: TEAL },
            { label: 'Assets', value: t ? String(t.count) : '—', icon: <Boxes className="w-4 h-4" />, color: NAVY },
          ].map((s, i) => (
            <Card key={i}>
              <CardContent className="py-4">
                <div className="flex items-center gap-2 text-[11px] uppercase tracking-wider text-[#6B7280]">
                  <span style={{ color: s.color }}>{s.icon}</span>{s.label}
                </div>
                <div className="text-xl font-bold font-mono-nums mt-1" style={{ color: s.color }}>{s.value}</div>
              </CardContent>
            </Card>
          ))}
        </div>

        {empty && (
          <Card>
            <CardContent className="py-12 text-center">
              <Boxes className="w-8 h-8 text-[#D1D5DB] mx-auto mb-2" />
              <p className="text-[#374151] font-medium">No assets yet</p>
              <p className="text-[#9CA3AF] text-sm mt-1">Export your PP&amp;E register from Odoo and import it — this dashboard fills in automatically.</p>
              <Button variant="primary" size="sm" className="mt-3" leftIcon={<Upload className="w-3.5 h-3.5" />} onClick={() => router.push('/assets/import')}>Import from Odoo</Button>
            </CardContent>
          </Card>
        )}

        {!empty && (
          <>
            {/* Movements */}
            <Card>
              <CardContent className="py-4">
                <div className="flex items-center justify-between mb-3">
                  <h3 className="text-sm font-semibold text-[#111827]">Movements — last 12 months</h3>
                  <div className="flex items-center gap-3 text-[11px] text-[#6B7280]">
                    <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm inline-block" style={{ background: NAVY }} /> Additions</span>
                    <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm inline-block" style={{ background: ORANGE }} /> Depreciation</span>
                  </div>
                </div>
                <div className="flex items-end gap-1.5 h-40">
                  {(sum?.movements || []).map((m, i) => (
                    <div key={i} className="flex-1 flex flex-col items-center gap-1 min-w-0">
                      <div className="w-full flex items-end justify-center gap-0.5 h-32">
                        <div className="w-1/2 rounded-t" title={`Additions ${money(m.additions)}`} style={{ height: `${Math.max(2, (m.additions / maxMove) * 100)}%`, background: NAVY }} />
                        <div className="w-1/2 rounded-t" title={`Depreciation ${money(m.depreciation)}`} style={{ height: `${Math.max(2, (m.depreciation / maxMove) * 100)}%`, background: ORANGE }} />
                      </div>
                      <div className="text-[9px] text-[#9CA3AF] truncate w-full text-center">{m.month.slice(2)}</div>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>

            {/* By category */}
            <Card>
              <CardContent className="py-4">
                <h3 className="text-sm font-semibold text-[#111827] mb-3">Net book value by category</h3>
                <div className="space-y-2.5">
                  {(sum?.by_category || []).map((c, i) => (
                    <div key={i}>
                      <div className="flex items-center justify-between text-xs mb-1">
                        <span className="text-[#374151] font-medium">{c.category} <span className="text-[#9CA3AF]">· {c.count}</span></span>
                        <span className="font-mono-nums text-[#111827]">{money(c.net_book_value)}</span>
                      </div>
                      <div className="h-2 rounded-full overflow-hidden" style={{ background: 'rgba(0,0,0,0.06)' }}>
                        <div className="h-full rounded-full" style={{ width: `${(c.net_book_value / maxCat) * 100}%`, background: TEAL }} />
                      </div>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>

            {/* Register */}
            <Card>
              <CardContent className="p-0">
                <div className="flex items-center justify-between p-4 pb-2">
                  <h3 className="text-sm font-semibold text-[#111827]">Register</h3>
                  <div className="flex items-center gap-2">
                    <select value={sortBy} onChange={e => setSortBy(e.target.value)}
                      className="bg-white border border-[#D1D5DB] rounded-lg px-2 py-1.5 text-xs">
                      <option value="nbv_desc">NBV — high to low</option>
                      <option value="cost_desc">Cost — high to low</option>
                      <option value="purchased_desc">Newest first</option>
                      <option value="name_asc">Name (A–Z)</option>
                    </select>
                  </div>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead className="bg-[#F3F4F6] border-y border-[#E5E7EB] text-[11px] uppercase text-[#6B7280]">
                      <tr className="text-left">
                        <th className="px-4 py-2">Tag</th>
                        <th className="px-4 py-2">Asset</th>
                        <th className="px-4 py-2">Category</th>
                        <th className="px-4 py-2 text-right">Cost</th>
                        <th className="px-4 py-2 text-right">Accum. depr.</th>
                        <th className="px-4 py-2 text-right">NBV</th>
                        <th className="px-4 py-2">Purchased</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[#F0F0F0]">
                      {sortedRows.map((a) => (
                        <tr key={a.id} className="hover:bg-[#FFF7ED]">
                          <td className="px-4 py-2 font-mono text-xs text-[#CC6C00]">{a.tag_number}</td>
                          <td className="px-4 py-2 text-[#111827] max-w-[200px] truncate" title={a.name}>{a.name}</td>
                          <td className="px-4 py-2 text-[#6B7280] text-xs">{a.category_name || '—'}</td>
                          <td className="px-4 py-2 text-right font-mono-nums">{money(a.cost)}</td>
                          <td className="px-4 py-2 text-right font-mono-nums text-[#6B7280]">{money(a.accumulated_depreciation ?? 0)}</td>
                          <td className="px-4 py-2 text-right font-mono-nums font-semibold text-[#CC6C00]">{money(a.net_book_value ?? 0)}</td>
                          <td className="px-4 py-2 text-xs text-[#6B7280]">{a.purchase_date ? formatDate(a.purchase_date) : '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </CardContent>
            </Card>
          </>
        )}
      </div>
    </div>
  )
}
