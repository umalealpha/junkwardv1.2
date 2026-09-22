'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import {
  getToken,
  getCessions,
  postCession,
  type Cession,
} from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Shield, AlertTriangle, RefreshCw, Coins } from 'lucide-react'

// ─── Helpers ────────────────────────────────────────────────────────────────

function fmtMoney(s: string | null): string {
  if (s === null || s === undefined || s === '') return '—'
  const n = Number(s)
  if (!isFinite(n)) return String(s)
  return n.toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function fmtDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('en-GB', {
    day: '2-digit', month: 'short', year: 'numeric',
  })
}

const STATUS_BADGE: Record<string, { bg: string; fg: string }> = {
  draft:  { bg: '#F3F4F6', fg: '#374151' },
  posted: { bg: '#ECFDF5', fg: '#047857' },
  voided: { bg: '#FEF2F2', fg: '#B91C1C' },
}

function StatusPill({ status, label }: { status: string; label: string }) {
  const c = STATUS_BADGE[status] || STATUS_BADGE.draft
  return (
    <span
      className="inline-flex text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded border"
      style={{ background: c.bg, color: c.fg, borderColor: `${c.fg}30` }}
    >
      {label}
    </span>
  )
}

// ─── Page ───────────────────────────────────────────────────────────────────

export default function ReinsuranceCessionsPage() {
  const router = useRouter()
  const [cessions, setCessions] = useState<Cession[]>([])
  const [statusFilter, setStatusFilter] = useState<string>('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [postingId, setPostingId] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const res = await getCessions({ status: statusFilter || undefined })
      setCessions(res.results)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load cessions')
    } finally {
      setLoading(false)
    }
  }, [statusFilter])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    void load()
  }, [router, load])

  const handlePost = useCallback(async (id: string) => {
    setPostingId(id); setError(null)
    try {
      await postCession(id)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to post cession')
    } finally {
      setPostingId(null)
    }
  }, [load])

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Cessions"
        breadcrumbs={[{ label: 'Reinsurance' }, { label: 'Cessions' }]}
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
        {/* Filter strip */}
        <Card>
          <CardContent className="p-4 flex flex-wrap items-center gap-3">
            <span className="text-xs font-medium text-[#374151]">Status:</span>
            {[
              { value: '',       label: 'All' },
              { value: 'draft',  label: 'Draft' },
              { value: 'posted', label: 'Posted' },
              { value: 'voided', label: 'Voided' },
            ].map((opt) => (
              <button
                key={opt.value}
                onClick={() => setStatusFilter(opt.value)}
                className={`text-xs px-2.5 py-1 rounded border transition-colors ${
                  statusFilter === opt.value
                    ? 'bg-[#0B0B3B] text-white border-[#0B0B3B]'
                    : 'bg-white text-[#374151] border-[#D1D5DB] hover:bg-[#F9FAFB]'
                }`}
              >
                {opt.label}
              </button>
            ))}
          </CardContent>
        </Card>

        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-[#DC2626]" />
            <p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}

        <Card>
          <CardContent className="p-0">
            {loading && (
              <p className="px-6 py-8 text-center text-sm text-[#6B7280]">Loading…</p>
            )}
            {!loading && cessions.length === 0 && (
              <div className="px-6 py-12 text-center text-[#6B7280]">
                <Coins className="w-10 h-10 mx-auto text-[#D1D5DB] mb-3" strokeWidth={1.5} />
                <p className="text-sm">No cessions yet.</p>
                <p className="text-xs text-[#9CA3AF] mt-2">
                  Create cessions in Django admin under{' '}
                  <code>/admin/reinsurance/cession/</code>
                </p>
              </div>
            )}
            {!loading && cessions.length > 0 && (
              <div className="overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="bg-[#F9FAFB] border-b border-[#E5E7EB]">
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Cession #</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Date</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Treaty</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Reinsurer</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Policy Ref</th>
                      <th className="text-right px-4 py-2.5 font-semibold text-[#374151]">Gross Premium</th>
                      <th className="text-right px-4 py-2.5 font-semibold text-[#374151]">Ceded Premium</th>
                      <th className="text-right px-4 py-2.5 font-semibold text-[#374151]">Commission</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Status</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {cessions.map((c) => (
                      <tr key={c.id} className="border-b border-[#F3F4F6] hover:bg-[#F9FAFB]">
                        <td className="px-4 py-2.5 font-mono text-xs text-[#0B0B3B]">{c.cession_number}</td>
                        <td className="px-4 py-2.5 text-[#374151]">{fmtDate(c.cession_date)}</td>
                        <td className="px-4 py-2.5 text-[#374151] font-mono text-xs">{c.treaty_number}</td>
                        <td className="px-4 py-2.5 text-[#374151] font-mono text-xs">{c.reinsurer_short_code}</td>
                        <td className="px-4 py-2.5 text-[#6B7280] text-xs">{c.policy_reference || '—'}</td>
                        <td className="px-4 py-2.5 text-right font-mono tabular-nums text-[#111827]">
                          {fmtMoney(c.gross_premium)}
                        </td>
                        <td className="px-4 py-2.5 text-right font-mono tabular-nums text-[#111827]">
                          {fmtMoney(c.ceded_premium)}
                        </td>
                        <td className="px-4 py-2.5 text-right font-mono tabular-nums text-[#111827]">
                          {fmtMoney(c.commission_amount)}
                        </td>
                        <td className="px-4 py-2.5">
                          <StatusPill status={c.status} label={c.status_display} />
                        </td>
                        <td className="px-4 py-2.5">
                          {c.status === 'draft' && (
                            <Button
                              size="sm"
                              onClick={() => handlePost(c.id)}
                              disabled={postingId === c.id}
                            >
                              {postingId === c.id ? 'Posting…' : 'Post to GL'}
                            </Button>
                          )}
                          {c.status === 'posted' && (
                            <span className="text-xs font-mono text-[#047857]">
                              {c.je_number || '—'}
                            </span>
                          )}
                          {c.status === 'voided' && (
                            <span className="text-xs text-[#9CA3AF]">—</span>
                          )}
                        </td>
                      </tr>
                    ))}
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
