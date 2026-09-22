'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import {
  getToken,
  getPettyCashReimbursements,
  type PettyCashReimbursement,
} from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Plus, Coins, RefreshCw, AlertTriangle, ChevronRight } from 'lucide-react'

const STATUS_BADGE: Record<string, { bg: string; fg: string; label: string }> = {
  draft:       { bg: '#F3F4F6', fg: '#374151', label: 'Draft' },
  pending_fm:  { bg: '#FFFBEB', fg: '#92400E', label: 'Pending FM' },
  pending_cfo: { bg: '#EFF6FF', fg: '#1D4ED8', label: 'Pending CFO' },
  posted:      { bg: '#ECFDF5', fg: '#047857', label: 'Posted' },
  rejected:    { bg: '#FEF2F2', fg: '#B91C1C', label: 'Rejected' },
}

function fmtMoney(s: string): string {
  const n = Number(s)
  return isFinite(n)
    ? n.toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : s
}
function fmtDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('en-GB', {
    day: '2-digit', month: 'short', year: 'numeric',
  })
}

export default function PettyCashReimbursementsListPage() {
  const router = useRouter()
  const [items, setItems] = useState<PettyCashReimbursement[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const res = await getPettyCashReimbursements()
      setItems(res.results)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load reimbursements')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    void load()
  }, [router, load])

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Petty Cash Reimbursements"
        breadcrumbs={[
          { label: 'Petty Cash', href: '/petty-cash' },
          { label: 'Reimbursements' },
        ]}
        actions={
          <div className="flex gap-2">
            <Button
              variant="outline" size="sm"
              leftIcon={<RefreshCw className="w-3.5 h-3.5" />}
              onClick={load} disabled={loading}
            >
              Refresh
            </Button>
            <Link href="/petty-cash/reimbursements/new">
              <Button size="sm" leftIcon={<Plus className="w-3.5 h-3.5" />}>
                Top Up the Tin
              </Button>
            </Link>
          </div>
        }
      />

      <div className="flex-1 p-6">
        {error && (
          <div className="mb-4 bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-[#DC2626]" />
            <p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}

        <Card>
          <CardContent className="p-0">
            {loading && (
              <p className="px-6 py-8 text-center text-sm text-[#6B7280]">Loading…</p>
            )}
            {!loading && items.length === 0 && (
              <div className="px-6 py-12 text-center text-[#6B7280]">
                <Coins className="w-10 h-10 mx-auto text-[#D1D5DB] mb-3" strokeWidth={1.5} />
                <p className="text-sm">No reimbursements yet.</p>
                <p className="text-xs text-[#9CA3AF] mt-2">
                  Run one at month-end to top the tin back up to its float.
                </p>
              </div>
            )}
            {!loading && items.length > 0 && (
              <div className="overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="bg-[#F9FAFB] border-b border-[#E5E7EB]">
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Reimb #</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Date</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Location</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Period</th>
                      <th className="text-right px-4 py-2.5 font-semibold text-[#374151]">Vouchers</th>
                      <th className="text-right px-4 py-2.5 font-semibold text-[#374151]">Total</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Status</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">JE</th>
                      <th className="text-right px-4 py-2.5 font-semibold text-[#374151]">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((r) => {
                      const status = STATUS_BADGE[r.status] || STATUS_BADGE.draft
                      return (
                        <tr key={r.id} className="border-b border-[#F3F4F6] hover:bg-[#F9FAFB]">
                          <td className="px-4 py-2.5">
                            <Link href={`/petty-cash/reimbursements/${r.id}`} className="text-[#0B0B3B] font-mono text-xs hover:underline">
                              {r.reimbursement_number}
                            </Link>
                          </td>
                          <td className="px-4 py-2.5 text-[#374151]">{fmtDate(r.reimbursement_date)}</td>
                          <td className="px-4 py-2.5 text-[#374151]">{r.location_name}</td>
                          <td className="px-4 py-2.5 text-[#6B7280] text-xs font-mono">
                            {r.period_start} → {r.period_end}
                          </td>
                          <td className="px-4 py-2.5 text-right font-mono tabular-nums text-[#374151]">
                            {r.voucher_count}
                          </td>
                          <td className="px-4 py-2.5 text-right font-mono tabular-nums text-[#111827]">
                            BWP {fmtMoney(r.total_amount)}
                          </td>
                          <td className="px-4 py-2.5">
                            <span
                              className="inline-flex text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded border"
                              style={{ background: status.bg, color: status.fg, borderColor: `${status.fg}30` }}
                            >
                              {status.label}
                            </span>
                          </td>
                          <td className="px-4 py-2.5 text-[#047857] font-mono text-xs">
                            {r.je_number || '—'}
                          </td>
                          <td className="px-4 py-2.5 text-right whitespace-nowrap">
                            <Link href={`/petty-cash/reimbursements/${r.id}`}>
                              <Button size="sm" variant="outline" leftIcon={<ChevronRight className="w-3 h-3" />}>
                                {r.status === 'pending_fm' || r.status === 'pending_cfo' ? 'Review' : 'Open'}
                              </Button>
                            </Link>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
