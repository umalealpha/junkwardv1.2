'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import {
  getToken,
  getReinsuranceRecoveries,
  postReinsuranceRecovery,
  type ReinsuranceRecovery,
} from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Coins, AlertTriangle, RefreshCw } from 'lucide-react'

function fmtMoney(s: string | null): string {
  if (!s) return '—'
  const n = Number(s)
  if (!isFinite(n)) return s
  return n.toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function fmtDate(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })
}

const STATUS_BADGE: Record<string, { bg: string; fg: string }> = {
  draft:   { bg: '#F3F4F6', fg: '#374151' },
  posted:  { bg: '#ECFDF5', fg: '#047857' },
  settled: { bg: '#EFF6FF', fg: '#1D4ED8' },
  voided:  { bg: '#FEF2F2', fg: '#B91C1C' },
}

type StatusFilter = '' | 'draft' | 'posted' | 'settled' | 'voided'

const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: '',        label: 'All' },
  { value: 'draft',   label: 'Draft' },
  { value: 'posted',  label: 'Posted' },
  { value: 'settled', label: 'Settled' },
  { value: 'voided',  label: 'Voided' },
]

export default function ReinsuranceRecoveriesPage() {
  const router = useRouter()
  const [recoveries, setRecoveries] = useState<ReinsuranceRecovery[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('')
  const [postingId, setPostingId] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const params: { status?: string } = {}
      if (statusFilter) params.status = statusFilter
      const res = await getReinsuranceRecoveries(params)
      setRecoveries(res.results)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load recoveries')
    } finally {
      setLoading(false)
    }
  }, [statusFilter])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    void load()
  }, [router, load])

  const handlePost = async (recovery: ReinsuranceRecovery) => {
    setPostingId(recovery.id)
    setError(null)
    try {
      await postReinsuranceRecovery(recovery.id)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to post recovery')
    } finally {
      setPostingId(null)
    }
  }

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Reinsurance Recoveries"
        breadcrumbs={[{ label: 'Reinsurance' }, { label: 'Recoveries' }]}
        actions={
          <Button
            variant="outline" size="sm"
            leftIcon={<RefreshCw className="w-3.5 h-3.5" />}
            onClick={load} disabled={loading}
          >
            Refresh
          </Button>
        }
      />

      <div className="flex-1 p-6 space-y-4">
        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-[#DC2626]" />
            <p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}

        {/* Status filter pills */}
        <div className="flex items-center gap-2 flex-wrap">
          {STATUS_FILTERS.map((f) => {
            const active = statusFilter === f.value
            return (
              <button
                key={f.value || 'all'}
                onClick={() => setStatusFilter(f.value)}
                className="px-3 py-1.5 text-xs font-semibold rounded-full border transition-colors"
                style={{
                  background: active ? '#0B0B3B' : '#FFFFFF',
                  color: active ? '#FFFFFF' : '#374151',
                  borderColor: active ? '#0B0B3B' : '#E5E7EB',
                }}
              >
                {f.label}
              </button>
            )
          })}
        </div>

        <Card>
          <CardContent className="p-0">
            {loading && (
              <p className="px-6 py-8 text-center text-sm text-[#6B7280]">Loading…</p>
            )}
            {!loading && recoveries.length === 0 && (
              <div className="px-6 py-12 text-center text-[#6B7280]">
                <Coins className="w-10 h-10 mx-auto text-[#D1D5DB] mb-3" strokeWidth={1.5} />
                <p className="text-sm">No recoveries yet.</p>
                <p className="text-xs text-[#9CA3AF] mt-2">
                  Recoveries are claim-side reinsurance — log them in Django admin under{' '}
                  <code>/admin/reinsurance/reinsurancerecovery/</code>
                </p>
              </div>
            )}
            {!loading && recoveries.length > 0 && (
              <div className="overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="bg-[#F9FAFB] border-b border-[#E5E7EB]">
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Recovery #</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Date</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Treaty</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Reinsurer</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Claim Ref</th>
                      <th className="text-right px-4 py-2.5 font-semibold text-[#374151]">Gross Loss</th>
                      <th className="text-right px-4 py-2.5 font-semibold text-[#374151]">Ceded Recovery</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Status</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">JE #</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {recoveries.map((r) => {
                      const c = STATUS_BADGE[r.status] || STATUS_BADGE.draft
                      const isPosting = postingId === r.id
                      return (
                        <tr key={r.id} className="border-b border-[#F3F4F6] hover:bg-[#F9FAFB]">
                          <td className="px-4 py-2.5 font-mono text-xs text-[#0B0B3B]">{r.recovery_number}</td>
                          <td className="px-4 py-2.5 text-[#6B7280] text-xs font-mono">{fmtDate(r.recovery_date)}</td>
                          <td className="px-4 py-2.5 text-[#374151] font-mono text-xs">{r.treaty_number}</td>
                          <td className="px-4 py-2.5 text-[#374151] font-mono text-xs">{r.reinsurer_short_code}</td>
                          <td className="px-4 py-2.5 text-[#374151] font-mono text-xs">{r.claim_reference}</td>
                          <td className="px-4 py-2.5 text-right font-mono tabular-nums text-[#374151]">
                            {fmtMoney(r.gross_loss)}
                          </td>
                          <td className="px-4 py-2.5 text-right font-mono tabular-nums text-[#374151]">
                            {fmtMoney(r.ceded_recovery)}
                          </td>
                          <td className="px-4 py-2.5">
                            <span
                              className="inline-flex text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded border"
                              style={{ background: c.bg, color: c.fg, borderColor: `${c.fg}30` }}
                            >
                              {r.status_display}
                            </span>
                          </td>
                          <td className="px-4 py-2.5 font-mono text-xs text-[#6B7280]">
                            {r.je_number || '—'}
                          </td>
                          <td className="px-4 py-2.5">
                            {r.status === 'draft' ? (
                              <Button
                                size="sm"
                                onClick={() => handlePost(r)}
                                disabled={isPosting || postingId !== null}
                              >
                                {isPosting ? 'Posting…' : 'Post to GL'}
                              </Button>
                            ) : (
                              <span className="text-[#9CA3AF] text-xs">—</span>
                            )}
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
