'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { getPurchaseOrders, getToken, SSO_SENTINEL_TOKEN } from '@/lib/api'
import type { PurchaseOrderListItem } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { LoadingTable } from '@/components/ui/loading'
import { Plus, Search, AlertCircle, ChevronLeft, ChevronRight, ShoppingCart, Download, FileArchive } from 'lucide-react'

const PAGE_SIZE = 25

function fmt(value: string | number): string {
  const n = typeof value === 'string' ? parseFloat(value) : value
  if (Number.isNaN(n)) return '—'
  return new Intl.NumberFormat('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n)
}

const STATUS_STYLES: Record<string, string> = {
  draft:                  'bg-gray-100 text-gray-700 border-gray-300',
  pending_fm_approval:    'bg-amber-50 text-amber-800 border-amber-300',
  pending_cfo_approval:   'bg-orange-50 text-orange-800 border-orange-300',
  approved:               'bg-emerald-50 text-emerald-800 border-emerald-300',
  partially_received:     'bg-blue-50 text-blue-800 border-blue-300',
  fully_received:         'bg-cyan-50 text-cyan-800 border-cyan-300',
  closed:                 'bg-slate-100 text-slate-700 border-slate-300',
  rejected:               'bg-red-50 text-red-800 border-red-300',
  cancelled:              'bg-zinc-100 text-zinc-700 border-zinc-300',
}

export default function PurchaseOrdersPage() {
  const router = useRouter()
  const [pos, setPos]                 = useState<PurchaseOrderListItem[]>([])
  const [loading, setLoading]         = useState(true)
  const [error, setError]             = useState<string | null>(null)
  const [search, setSearch]           = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [departmentFilter, setDepartmentFilter] = useState('')
  const [issueDateFrom, setIssueDateFrom] = useState('')
  const [issueDateTo, setIssueDateTo]     = useState('')
  const [page, setPage]               = useState(1)
  const [totalCount, setTotalCount]   = useState(0)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params: {
        page: number; status?: string; department?: string; search?: string;
        issue_date_from?: string; issue_date_to?: string;
      } = { page }
      if (statusFilter)     params.status = statusFilter
      if (departmentFilter) params.department = departmentFilter
      if (search)           params.search = search
      if (issueDateFrom)    params.issue_date_from = issueDateFrom
      if (issueDateTo)      params.issue_date_to = issueDateTo
      const res = await getPurchaseOrders(params)
      setPos(res.results)
      setTotalCount(res.count)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load purchase orders')
    } finally {
      setLoading(false)
    }
  }, [statusFilter, departmentFilter, page, search, issueDateFrom, issueDateTo])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load()
  }, [load, router])

  const [exporting, setExporting] = useState<null | 'csv' | 'zip'>(null)

  async function download(kind: 'csv' | 'zip') {
    setExporting(kind)
    setError(null)
    try {
      const params = new URLSearchParams()
      if (statusFilter)     params.set('status', statusFilter)
      if (departmentFilter) params.set('department', departmentFilter)
      if (search)           params.set('search', search)
      if (issueDateFrom)    params.set('issue_date_from', issueDateFrom)
      if (issueDateTo)      params.set('issue_date_to', issueDateTo)
      const qs = params.toString()
      const url = `/api/v1/purchase-orders/export-${kind}/${qs ? `?${qs}` : ''}`

      const headers: Record<string, string> = {}
      try {
        const { acquireApiToken, SSO_API_CALLS_READY } = await import('@/auth/msal')
        if (SSO_API_CALLS_READY) {
          const t = await acquireApiToken()
          if (t) headers['Authorization'] = `Bearer ${t}`
        }
      } catch { /* fall through */ }
      if (!headers['Authorization']) {
        const t = getToken()
        if (t && t !== SSO_SENTINEL_TOKEN) headers['Authorization'] = `Token ${t}`
      }
      const res = await fetch(url, { headers, credentials: 'include' })
      if (!res.ok) {
        const txt = await res.text().catch(() => '')
        throw new Error(`Download failed: HTTP ${res.status} ${txt.slice(0, 200)}`)
      }
      const blob = await res.blob()
      const dlUrl = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = dlUrl
      a.download = `purchase_orders_${statusFilter || 'all'}.${kind}`
      document.body.appendChild(a)
      a.click()
      a.remove()
      setTimeout(() => URL.revokeObjectURL(dlUrl), 60_000)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Download failed')
    } finally {
      setExporting(null)
    }
  }

  const totalPages = Math.max(1, Math.ceil(totalCount / PAGE_SIZE))

  return (
    <div className="min-h-screen bg-[#F8F9FA]">
      <TopBar title="Purchase Orders" subtitle="Procurement & 3-way match" />

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3 flex-wrap">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
              <input
                type="text" value={search}
                onChange={(e) => { setSearch(e.target.value); setPage(1) }}
                placeholder="Search PO number or supplier"
                className="pl-9 pr-3 py-2 border border-gray-300 rounded-md text-sm w-72"
              />
            </div>
            <select value={statusFilter} aria-label="Status filter"
              onChange={(e) => { setStatusFilter(e.target.value); setPage(1) }}
              className="px-3 py-2 border border-gray-300 rounded-md text-sm">
              <option value="">All statuses</option>
              <option value="draft">Draft</option>
              {/* Kago (FM) 2026-07-25: "Pending FM" used to return the FM's own
                  queue AND the Claims Manager's queue together — both legs share
                  one status. Split so each approver can see only their side. */}
              <option value="pending_fm_finance">Pending FM (Admin / HR)</option>
              <option value="pending_claims_approval">Pending Claims approval</option>
              <option value="pending_fm_approval">Pending FM or Claims (all)</option>
              <option value="pending_cfo_approval">Pending CFO</option>
              <option value="approved">Approved</option>
              <option value="partially_received">Partially received</option>
              <option value="fully_received">Fully received</option>
              <option value="closed">Closed</option>
              <option value="rejected">Rejected</option>
              <option value="cancelled">Cancelled</option>
            </select>
            <select value={departmentFilter} aria-label="Department filter"
              onChange={(e) => { setDepartmentFilter(e.target.value); setPage(1) }}
              className="px-3 py-2 border border-gray-300 rounded-md text-sm">
              <option value="">All departments</option>
              <option value="admin">Admin</option>
              <option value="claims">Claims</option>
              <option value="hr">Human Resources</option>
            </select>
            <div className="flex items-center gap-1 text-sm">
              <span className="text-gray-500 text-xs uppercase tracking-wide">From</span>
              <input
                aria-label="Issue date from"
                type="date" value={issueDateFrom}
                onChange={(e) => { setIssueDateFrom(e.target.value); setPage(1) }}
                className="px-2 py-1.5 border border-gray-300 rounded-md text-sm"
              />
              <span className="text-gray-500 text-xs uppercase tracking-wide">To</span>
              <input
                aria-label="Issue date to"
                type="date" value={issueDateTo}
                onChange={(e) => { setIssueDateTo(e.target.value); setPage(1) }}
                className="px-2 py-1.5 border border-gray-300 rounded-md text-sm"
              />
              {(issueDateFrom || issueDateTo) && (
                <button
                  type="button"
                  onClick={() => { setIssueDateFrom(''); setIssueDateTo(''); setPage(1) }}
                  className="text-xs text-orange-600 hover:underline ml-1"
                >clear</button>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              onClick={() => download('csv')}
              disabled={exporting !== null}
              title="Download filtered POs as CSV"
            >
              <Download className="w-4 h-4 mr-1" />
              {exporting === 'csv' ? 'Exporting…' : 'Export CSV'}
            </Button>
            <Button
              variant="outline"
              onClick={() => download('zip')}
              disabled={exporting !== null}
              title="Download every visible PO's PDF in one ZIP (max 200)"
            >
              <FileArchive className="w-4 h-4 mr-1" />
              {exporting === 'zip' ? 'Building ZIP…' : 'Download PDFs (ZIP)'}
            </Button>
            <Link href="/purchase-orders/new">
              <Button className="bg-[#0D1B2A] hover:bg-[#1a2940] text-white">
                <Plus className="w-4 h-4 mr-1" /> New PO
              </Button>
            </Link>
          </div>
        </div>

        {error && (
          <Card className="border-red-200 bg-red-50">
            <CardContent className="p-3 flex items-start gap-2">
              <AlertCircle className="w-4 h-4 text-red-700 mt-0.5" />
              <span className="text-sm text-red-700">{error}</span>
            </CardContent>
          </Card>
        )}

        <Card>
          <CardContent className="p-0">
            {loading ? (
              <div className="p-6"><LoadingTable rows={6} /></div>
            ) : pos.length === 0 ? (
              <div className="p-12 text-center text-gray-500">
                <ShoppingCart className="w-10 h-10 mx-auto mb-2 opacity-40" />
                <p className="text-sm">No purchase orders yet.</p>
              </div>
            ) : (
              <table className="w-full text-sm" style={{ fontVariantNumeric: 'tabular-nums' }}>
                <thead>
                  <tr className="bg-[#0D1B2A] text-white/90 text-left text-[10.5px] uppercase tracking-[0.10em]">
                    <th className="px-5 py-3 font-semibold">PO #</th>
                    <th className="px-4 py-3 font-semibold">Department</th>
                    <th className="px-4 py-3 font-semibold">Supplier</th>
                    <th className="px-4 py-3 font-semibold">Issue date</th>
                    <th className="px-4 py-3 font-semibold text-right">Total</th>
                    <th className="px-5 py-3 font-semibold text-right">Total (BWP)</th>
                    <th className="px-4 py-3 font-semibold">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {pos.map((po, i) => (
                    <tr key={po.id}
                      className={`border-b border-gray-100 dark:border-white/5 cursor-pointer transition-colors hover:bg-[#F4A623]/[0.06] ${i % 2 ? 'bg-gray-50/50 dark:bg-white/[0.015]' : ''}`}
                      onClick={() => router.push(`/purchase-orders/${po.id}`)}>
                      <td className="px-5 py-3 font-medium text-gray-900 dark:text-gray-100">{po.po_number}</td>
                      <td className="px-4 py-3 text-gray-600 dark:text-gray-300">{po.department_display}</td>
                      <td className="px-4 py-3 text-gray-800 dark:text-gray-200">{po.supplier_name}</td>
                      <td className="px-4 py-3 text-gray-500 dark:text-gray-400">{po.issue_date}</td>
                      <td className="px-4 py-3 text-right text-gray-700 dark:text-gray-300 tabular-nums">
                        {po.currency_code} {fmt(po.total_amount)}
                      </td>
                      <td className="px-5 py-3 text-right font-semibold text-gray-900 dark:text-gray-100 tabular-nums">BWP {fmt(po.total_bwp)}</td>
                      <td className="px-4 py-3">
                        <span className={`text-xs px-2.5 py-0.5 rounded-full border ${STATUS_STYLES[po.status] || 'bg-gray-100'}`}>
                          {po.status_display}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </CardContent>
        </Card>

        {totalCount > PAGE_SIZE && (
          <div className="flex items-center justify-between text-sm text-gray-600">
            <span>Page {page} of {totalPages} — {totalCount} total</span>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>
                <ChevronLeft className="w-4 h-4" /> Prev
              </Button>
              <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => setPage(page + 1)}>
                Next <ChevronRight className="w-4 h-4" />
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
