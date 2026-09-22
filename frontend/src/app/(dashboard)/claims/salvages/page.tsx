'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { getSalvages, getMe, getToken } from '@/lib/api'
import type { Salvage, UserProfile } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Plus, Search, Upload, AlertCircle, ChevronLeft, ChevronRight, Package } from 'lucide-react'

const PAGE_SIZE = 25

function fmt(v: string | number): string {
  const n = typeof v === 'string' ? parseFloat(v) : v
  if (Number.isNaN(n)) return '—'
  return new Intl.NumberFormat('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n)
}

const STATUS_STYLES: Record<string, string> = {
  pending:  'bg-[#FFFBEB] text-[#92400E] border-[#FDE68A]',
  for_sale: 'bg-[#EFF6FF] text-[#1D4ED8] border-[#BFDBFE]',
  sold:     'bg-[#ECFDF5] text-[#047857] border-[#A7F3D0]',
  scrapped: 'bg-[#FEF2F2] text-[#B91C1C] border-[#FECACA]',
  retained: 'bg-[#F3F4F6] text-[#374151] border-[#D1D5DB]',
}

export default function SalvagesPage() {
  const router = useRouter()
  const [items, setItems] = useState<Salvage[]>([])
  const [me, setMe] = useState<UserProfile | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [page, setPage] = useState(1)
  const [count, setCount] = useState(0)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await getSalvages({
        page, status: statusFilter || undefined, search: search || undefined,
      })
      setItems(res.results)
      setCount(res.count)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load')
    } finally {
      setLoading(false)
    }
  }, [page, search, statusFilter])

  useEffect(() => {
    const token = getToken()
    if (!token) { router.replace('/login'); return }
    getMe().then(setMe).catch(() => setMe(null))
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load])

  const totalPages = Math.max(1, Math.ceil(count / PAGE_SIZE))
  const canImport = !!me?.can_approve_journal_entries

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Salvages"
        breadcrumbs={[{ label: 'Claims Recoveries' }, { label: 'Salvages' }]}
        actions={
          <div className="flex gap-2">
            <Button
              variant="outline" size="sm"
              leftIcon={<Upload className="w-3.5 h-3.5" />}
              onClick={() => router.push('/claims/salvages/import')}
              disabled={!canImport}
              title={canImport ? undefined : 'Imports restricted to CFO / Finance Manager / Financial Controller'}
            >
              Import
            </Button>
            <Button variant="accent" size="sm" leftIcon={<Plus className="w-3.5 h-3.5" />} onClick={() => router.push('/claims/salvages/new')}>
              New Salvage
            </Button>
          </div>
        }
      />

      <div className="flex-1 p-6 space-y-4">
        <div className="flex gap-3 items-end">
          <div className="relative flex-1 max-w-sm">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[#9CA3AF] pointer-events-none" strokeWidth={1.5} />
            <input
              type="text"
              placeholder="Search by claim ref, asset, buyer..."
              value={search}
              onChange={e => { setSearch(e.target.value); setPage(1) }}
              className="w-full h-10 bg-white border border-[#D1D5DB] rounded-md pl-9 pr-3 text-sm"
            />
          </div>
          <select value={statusFilter} onChange={e => { setStatusFilter(e.target.value); setPage(1) }}
                  className="h-10 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm">
            <option value="">All statuses</option>
            <option value="pending">Pending</option>
            <option value="for_sale">For sale</option>
            <option value="sold">Sold</option>
            <option value="scrapped">Scrapped</option>
            <option value="retained">Retained</option>
          </select>
        </div>

        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-[#DC2626]" />
            <p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}

        <Card>
          <CardContent className="p-0">
            {loading ? (
              <p className="px-6 py-8 text-center text-sm text-[#6B7280]">Loading…</p>
            ) : items.length === 0 ? (
              <div className="px-6 py-12 text-center text-[#6B7280]">
                <Package className="w-10 h-10 mx-auto mb-3 text-[#D1D5DB]" strokeWidth={1.5} />
                <p className="text-sm">No salvages yet.</p>
                <p className="text-xs text-[#9CA3AF] mt-1">Add manually or upload a CSV / Excel file.</p>
              </div>
            ) : (
              <>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm border-collapse">
                    <thead className="bg-[#F3F4F6] border-b-2 border-[#E5E7EB]">
                      <tr>
                        <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase">Claim ref</th>
                        <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase">Asset</th>
                        <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase">Estimated</th>
                        <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase">Proceeds</th>
                        <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase hidden md:table-cell">Gain/Loss</th>
                        <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase hidden md:table-cell">Buyer</th>
                        <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase">Status</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[#E5E7EB] bg-white">
                      {items.map(s => (
                        <tr key={s.id} onClick={() => router.push(`/salvage/inventory/${s.id}`)}
                            className="hover:bg-[#F9FAFB] cursor-pointer transition-colors">
                          <td className="px-4 py-3 font-mono">{s.claim_reference}</td>
                          <td className="px-4 py-3 text-[#111827] truncate max-w-xs" title={s.asset_description}>{s.asset_description}</td>
                          <td className="px-4 py-3 text-right tabular-nums">{fmt(s.estimated_value)}</td>
                          <td className="px-4 py-3 text-right tabular-nums font-medium">{fmt(s.sale_proceeds)}</td>
                          <td className={`px-4 py-3 text-right tabular-nums hidden md:table-cell ${parseFloat(s.gain_loss) >= 0 ? 'text-[#047857]' : 'text-[#B91C1C]'}`}>{fmt(s.gain_loss)}</td>
                          <td className="px-4 py-3 hidden md:table-cell text-[#374151]">{s.buyer_name || '—'}</td>
                          <td className="px-4 py-3">
                            <span className={`inline-block px-2 py-0.5 rounded-md text-xs font-medium border ${STATUS_STYLES[s.status] || STATUS_STYLES.pending}`}>
                              {s.status_display}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {totalPages > 1 && (
                  <div className="flex items-center justify-between px-4 py-3 border-t border-[#E5E7EB]">
                    <span className="text-xs text-[#6B7280]">Page {page} of {totalPages} — {count} total</span>
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
      </div>
    </div>
  )
}
