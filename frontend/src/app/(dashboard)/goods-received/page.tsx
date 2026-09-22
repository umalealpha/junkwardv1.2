'use client'

// Feature 9ac69d47 + 39f3b73d (Oprah, 2026-09-04): a dedicated Goods Received
// Notes screen under Operations → Procurement, directly after Purchase Orders,
// listing all GRNs with a one-click link to the related Purchase Order (whose
// detail page already shows its GRNs and Bills) — so finance/procurement can
// walk GRN → PO → Bill during reconciliation instead of opening a PO first.

import { useEffect, useState, useCallback } from 'react'
import Link from 'next/link'
import { getGRNs } from '@/lib/api'
import type { GRNListItem } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { LoadingTable } from '@/components/ui/loading'
import { AlertCircle, ChevronLeft, ChevronRight, ClipboardCheck, ExternalLink } from 'lucide-react'

const PAGE_SIZE = 25

const STATUS_STYLES: Record<string, string> = {
  draft:     'bg-gray-100 text-gray-700 border-gray-300',
  posted:    'bg-emerald-50 text-emerald-800 border-emerald-300',
  cancelled: 'bg-zinc-100 text-zinc-700 border-zinc-300',
}

function fmtDate(d: string): string {
  if (!d) return '—'
  const dt = new Date(d)
  return Number.isNaN(dt.getTime()) ? d : dt.toLocaleDateString('en-BW', { day: '2-digit', month: 'short', year: 'numeric' })
}

export default function GoodsReceivedNotesPage() {
  const [grns, setGrns]           = useState<GRNListItem[]>([])
  const [loading, setLoading]     = useState(true)
  const [error, setError]         = useState<string | null>(null)
  const [statusFilter, setStatus] = useState('')
  const [page, setPage]           = useState(1)
  const [totalCount, setTotal]    = useState(0)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params: { page: number; status?: string } = { page }
      if (statusFilter) params.status = statusFilter
      const res = await getGRNs(params)
      setGrns(res.results)
      setTotal(res.count)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load goods received notes')
    } finally {
      setLoading(false)
    }
  }, [page, statusFilter])

  useEffect(() => { load() }, [load])

  const totalPages = Math.max(1, Math.ceil(totalCount / PAGE_SIZE))

  return (
    <div className="min-h-screen bg-[#F8F9FB]">
      <TopBar />
      <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        <div className="flex items-center gap-3 mb-6">
          <div className="w-10 h-10 rounded-xl bg-[#0D1B2A] flex items-center justify-center">
            <ClipboardCheck className="w-5 h-5 text-[#F4A623]" />
          </div>
          <div>
            <h1 className="text-xl font-semibold text-[#0D1B2A]">Goods Received Notes</h1>
            <p className="text-sm text-[#6B7280]">Every goods received note, linked to its Purchase Order (and its Bill via the PO).</p>
          </div>
        </div>

        <div className="flex flex-wrap gap-2 mb-4">
          {['', 'posted', 'draft', 'cancelled'].map((s) => (
            <button
              key={s || 'all'}
              onClick={() => { setStatus(s); setPage(1) }}
              className={`px-3 py-1.5 rounded-lg text-sm border transition-colors ${
                statusFilter === s ? 'bg-[#0D1B2A] text-white border-[#0D1B2A]' : 'bg-white text-[#374151] border-gray-200 hover:border-[#0D1B2A]'
              }`}
            >
              {s ? s[0].toUpperCase() + s.slice(1) : 'All'}
            </button>
          ))}
        </div>

        {error && (
          <div className="rounded-xl border border-red-200 bg-red-50 p-4 mb-4 flex items-center gap-2 text-sm text-red-800">
            <AlertCircle className="w-4 h-4" /> {error}
          </div>
        )}

        <Card>
          <CardContent className="p-0">
            {loading ? (
              <LoadingTable rows={6} />
            ) : grns.length === 0 ? (
              <div className="p-10 text-center text-sm text-[#6B7280]">
                No goods received notes {statusFilter ? `with status “${statusFilter}”` : 'yet'}. A GRN is created from a Purchase Order when the goods arrive.
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-gray-100 text-left text-xs uppercase tracking-wide text-[#9CA3AF]">
                      <th className="px-4 py-3 font-medium">GRN #</th>
                      <th className="px-4 py-3 font-medium">Received</th>
                      <th className="px-4 py-3 font-medium">Purchase Order</th>
                      <th className="px-4 py-3 font-medium">Supplier</th>
                      <th className="px-4 py-3 font-medium">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {grns.map((g) => (
                      <tr key={g.id} className="border-b border-gray-50 hover:bg-[#FFF7ED] transition-colors">
                        <td className="px-4 py-3 font-mono text-xs text-[#CC6C00]">{g.grn_number}</td>
                        <td className="px-4 py-3 text-[#6B7280] text-xs">{fmtDate(g.receipt_date)}</td>
                        <td className="px-4 py-3">
                          <Link
                            href={`/purchase-orders/${g.purchase_order}`}
                            className="inline-flex items-center gap-1 font-mono text-xs text-[#0D1B2A] hover:text-[#CC6C00] hover:underline"
                          >
                            {g.po_number} <ExternalLink className="w-3 h-3" />
                          </Link>
                        </td>
                        <td className="px-4 py-3 text-[#374151]">{g.supplier_name || '—'}</td>
                        <td className="px-4 py-3">
                          <span className={`inline-block px-2 py-0.5 rounded-md border text-xs ${STATUS_STYLES[g.status] || STATUS_STYLES.draft}`}>
                            {g.status_display || g.status}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>

        {totalPages > 1 && (
          <div className="flex items-center justify-between mt-4 text-sm text-[#6B7280]">
            <span>{totalCount} note{totalCount === 1 ? '' : 's'}</span>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                <ChevronLeft className="w-4 h-4" /> Prev
              </Button>
              <span>Page {page} of {totalPages}</span>
              <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}>
                Next <ChevronRight className="w-4 h-4" />
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
