'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { getAssets, getAssetCategories, getAssetExportCsvUrl, getToken } from '@/lib/api'
import type { AssetListItem, AssetCategory } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { LoadingTable } from '@/components/ui/loading'
import { Plus, Search, AlertCircle, Upload, Download, ChevronLeft, ChevronRight, Boxes, LineChart } from 'lucide-react'
import SmartUpload from '@/components/SmartUpload'
import { localYmd } from '@/lib/utils'

const PAGE_SIZE = 25

function fmtBwp(value: string | number): string {
  const n = typeof value === 'string' ? parseFloat(value) : value
  if (Number.isNaN(n)) return '—'
  return new Intl.NumberFormat('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n)
}

const STATUS_STYLES: Record<string, string> = {
  active:       'bg-[#ECFDF5] text-[#047857] border-[#A7F3D0]',
  disposed:     'bg-[#F3F4F6] text-[#374151] border-[#D1D5DB]',
  written_off:  'bg-[#FEF2F2] text-[#B91C1C] border-[#FECACA]',
  transferred:  'bg-[#EFF6FF] text-[#1D4ED8] border-[#BFDBFE]',
}

export default function AssetsListPage() {
  const router = useRouter()
  const [assets, setAssets] = useState<AssetListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [activeTab, setActiveTab] = useState<'all' | 'active' | 'disposed' | 'written_off'>('all')
  const [categoryId, setCategoryId] = useState('')
  const [purchaseDateFrom, setPurchaseDateFrom] = useState('')
  const [purchaseDateTo, setPurchaseDateTo] = useState('')
  const [categories, setCategories] = useState<AssetCategory[]>([])
  const [page, setPage] = useState(1)
  const [totalCount, setTotalCount] = useState(0)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params: {
        page: number; page_size: number; status?: string; search?: string;
        category?: string; purchase_date_from?: string; purchase_date_to?: string;
      } = { page, page_size: PAGE_SIZE }
      if (activeTab !== 'all')   params.status = activeTab
      if (search)                params.search = search
      if (categoryId)            params.category = categoryId
      if (purchaseDateFrom)      params.purchase_date_from = purchaseDateFrom
      if (purchaseDateTo)        params.purchase_date_to = purchaseDateTo
      const res = await getAssets(params)
      setAssets(res.results)
      setTotalCount(res.count)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load assets')
    } finally {
      setLoading(false)
    }
  }, [activeTab, page, search, categoryId, purchaseDateFrom, purchaseDateTo])

  useEffect(() => {
    getAssetCategories().then(r => setCategories(r.results)).catch(() => setCategories([]))
  }, [])

  const onExport = async () => {
    const path = getAssetExportCsvUrl({
      status: activeTab === 'all' ? undefined : activeTab,
      category: categoryId || undefined,
      search: search || undefined,
      purchase_date_from: purchaseDateFrom || undefined,
      purchase_date_to: purchaseDateTo || undefined,
    })
    // Build the same auth header pattern used elsewhere (Bearer for SSO, Token fallback).
    const headers: Record<string, string> = {}
    try {
      const { acquireApiToken, SSO_API_CALLS_READY } = await import('@/auth/msal')
      if (SSO_API_CALLS_READY) {
        const bearer = await acquireApiToken()
        if (bearer) headers['Authorization'] = `Bearer ${bearer}`
      }
    } catch {}
    if (!headers['Authorization']) {
      const t = getToken()
      if (t) headers['Authorization'] = `Token ${t}`
    }
    const res = await fetch(path, { headers })
    if (!res.ok) { setError(`Export failed (HTTP ${res.status}).`); return }
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `fixed_assets_${localYmd(new Date())}.csv`
    document.body.appendChild(a); a.click(); a.remove()
    URL.revokeObjectURL(url)
  }

  useEffect(() => {
    const token = getToken()
    if (!token) { router.replace('/login'); return }
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load])

  const totalPages = Math.max(1, Math.ceil(totalCount / PAGE_SIZE))

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Fixed Assets"
        breadcrumbs={[{ label: 'Finance' }, { label: 'Fixed Assets' }]}
        actions={
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              leftIcon={<LineChart className="w-3.5 h-3.5" />}
              onClick={() => router.push('/assets/dashboard')}
            >
              Dashboard
            </Button>
            <Button
              variant="outline"
              size="sm"
              leftIcon={<Download className="w-3.5 h-3.5" />}
              onClick={onExport}
            >
              Export CSV
            </Button>
            <Button
              variant="outline"
              size="sm"
              leftIcon={<Upload className="w-3.5 h-3.5" />}
              onClick={() => router.push('/assets/import')}
            >
              Import from Odoo
            </Button>
            <Button
              variant="accent"
              size="sm"
              leftIcon={<Plus className="w-3.5 h-3.5" />}
              onClick={() => router.push('/assets/new')}
            >
              New Asset
            </Button>
          </div>
        }
      />

      <div className="flex-1 p-6 space-y-4">
        <SmartUpload section="ppe" onCommitted={load} />
        <Tabs value={activeTab} onValueChange={(v) => { setActiveTab(v as 'all'); setPage(1) }}>
          <TabsList variant="underline">
            <TabsTrigger value="all">All</TabsTrigger>
            <TabsTrigger value="active">Active</TabsTrigger>
            <TabsTrigger value="disposed">Disposed</TabsTrigger>
            <TabsTrigger value="written_off">Written off</TabsTrigger>
          </TabsList>

          <div className="flex gap-3 mt-4 flex-wrap items-center">
            <div className="relative flex-1 min-w-[240px] max-w-sm">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[#9CA3AF] pointer-events-none" strokeWidth={1.5} />
              <input
                type="text"
                placeholder="Search by tag, name, serial, location..."
                value={search}
                onChange={(e) => { setSearch(e.target.value); setPage(1) }}
                className="w-full h-10 bg-white border border-[#D1D5DB] rounded-md pl-9 pr-3 text-sm text-[#111827] placeholder-[#9CA3AF] focus:outline-none focus:border-[#F07F00] focus:ring-[3px] focus:ring-[rgba(240,127,0,0.1)] transition-all"
              />
            </div>
            <select
              aria-label="Category filter"
              value={categoryId}
              onChange={(e) => { setCategoryId(e.target.value); setPage(1) }}
              className="h-10 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm text-[#111827]"
            >
              <option value="">All categories</option>
              {categories.map(c => (
                <option key={c.id} value={c.id}>{c.code} — {c.name}</option>
              ))}
            </select>
            <div className="flex items-center gap-1 text-sm">
              <span className="text-[#6B7280] text-xs uppercase tracking-wide">Purchased</span>
              <input
                aria-label="Purchased from"
                type="date" value={purchaseDateFrom}
                onChange={(e) => { setPurchaseDateFrom(e.target.value); setPage(1) }}
                className="h-10 px-2 border border-[#D1D5DB] rounded-md text-sm"
              />
              <span className="text-[#6B7280] text-xs">to</span>
              <input
                aria-label="Purchased to"
                type="date" value={purchaseDateTo}
                onChange={(e) => { setPurchaseDateTo(e.target.value); setPage(1) }}
                className="h-10 px-2 border border-[#D1D5DB] rounded-md text-sm"
              />
              {(purchaseDateFrom || purchaseDateTo || categoryId) && (
                <button
                  type="button"
                  onClick={() => { setPurchaseDateFrom(''); setPurchaseDateTo(''); setCategoryId(''); setPage(1) }}
                  className="text-xs text-[#F07F00] hover:underline ml-1"
                >clear</button>
              )}
            </div>
          </div>

          {error && (
            <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2 mt-2">
              <AlertCircle className="w-4 h-4 text-[#DC2626] flex-shrink-0" />
              <p className="text-[#DC2626] text-sm">{error}</p>
            </div>
          )}

          <TabsContent value={activeTab} className="mt-4">
            <Card>
              <CardContent className="p-0">
                {loading ? (
                  <LoadingTable rows={8} cols={7} />
                ) : assets.length === 0 ? (
                  <div className="px-4 py-16 text-center text-[#6B7280]">
                    <Boxes className="w-10 h-10 mx-auto mb-3 text-[#D1D5DB]" strokeWidth={1.5} />
                    <p className="text-sm">No assets found.</p>
                    <p className="text-xs text-[#9CA3AF] mt-1">Add one manually or import your Odoo register.</p>
                  </div>
                ) : (
                  <>
                    <div className="overflow-x-auto">
                      <table className="w-full text-sm border-collapse">
                        <thead className="bg-[#F3F4F6] border-b-2 border-[#E5E7EB]">
                          <tr>
                            <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Tag</th>
                            <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Name</th>
                            <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider hidden md:table-cell">Category</th>
                            <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider">Cost</th>
                            <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider hidden lg:table-cell">Accum. Depr.</th>
                            <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider">NBV</th>
                            <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider hidden md:table-cell">Location</th>
                            <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Status</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-[#E5E7EB] bg-white">
                          {assets.map(a => (
                            <tr
                              key={a.id}
                              onClick={() => router.push(`/assets/${a.id}`)}
                              className="hover:bg-[#F9FAFB] cursor-pointer transition-colors"
                            >
                              <td className="px-4 py-3 text-[#111827] font-mono">{a.tag_number}</td>
                              <td className="px-4 py-3 text-[#111827]">{a.name}</td>
                              <td className="px-4 py-3 text-[#374151] hidden md:table-cell">{a.category_name}</td>
                              <td className="px-4 py-3 text-right text-[#111827] tabular-nums">{fmtBwp(a.cost)}</td>
                              <td className="px-4 py-3 text-right text-[#6B7280] hidden lg:table-cell tabular-nums">{fmtBwp(a.accumulated_depreciation)}</td>
                              <td className="px-4 py-3 text-right text-[#111827] font-medium tabular-nums">{fmtBwp(a.net_book_value)}</td>
                              <td className="px-4 py-3 text-[#374151] hidden md:table-cell">{a.location || '—'}</td>
                              <td className="px-4 py-3">
                                <span className={`inline-block px-2 py-0.5 rounded-md text-xs font-medium border ${STATUS_STYLES[a.status] || STATUS_STYLES.active}`}>
                                  {a.status_display}
                                </span>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>

                    {totalPages > 1 && (
                      <div className="flex items-center justify-between px-4 py-3 border-t border-[#E5E7EB]">
                        <span className="text-xs text-[#6B7280]">
                          Page {page} of {totalPages} — {totalCount} total
                        </span>
                        <div className="flex gap-2">
                          <Button variant="outline" size="sm" onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1} leftIcon={<ChevronLeft className="w-3.5 h-3.5" />}>Prev</Button>
                          <Button variant="outline" size="sm" onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page >= totalPages} rightIcon={<ChevronRight className="w-3.5 h-3.5" />}>Next</Button>
                        </div>
                      </div>
                    )}
                  </>
                )}
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </div>
    </div>
  )
}
