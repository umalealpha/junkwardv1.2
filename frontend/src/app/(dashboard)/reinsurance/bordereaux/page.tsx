'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import {
  getToken,
  getBordereauImports,
  type BordereauImport,
} from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { FileText, AlertTriangle, RefreshCw } from 'lucide-react'

function fmtMoney(s: string | null): string {
  if (s === null || s === undefined || s === '') return '—'
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
  uploaded:  { bg: '#F3F4F6', fg: '#6B7280' },
  parsed:    { bg: '#EFF6FF', fg: '#1D4ED8' },
  committed: { bg: '#ECFDF5', fg: '#047857' },
  rejected:  { bg: '#FEF2F2', fg: '#B91C1C' },
}

export default function BordereauxPage() {
  const router = useRouter()
  const [imports, setImports] = useState<BordereauImport[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const res = await getBordereauImports()
      setImports(res.results)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load bordereaux')
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
        title="Bordereaux"
        breadcrumbs={[{ label: 'Reinsurance' }, { label: 'Bordereaux' }]}
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

        <Card>
          <CardContent className="p-0">
            {loading && (
              <p className="px-6 py-8 text-center text-sm text-[#6B7280]">Loading…</p>
            )}
            {!loading && imports.length === 0 && (
              <div className="px-6 py-12 text-center text-[#6B7280]">
                <FileText className="w-10 h-10 mx-auto text-[#D1D5DB] mb-3" strokeWidth={1.5} />
                <p className="text-sm">No bordereaux imported yet.</p>
                <p className="text-xs text-[#9CA3AF] mt-2">
                  Upload broker bordereaux via Django admin under{' '}
                  <code>/admin/reinsurance/bordereauimport/</code>
                </p>
              </div>
            )}
            {!loading && imports.length > 0 && (
              <div className="overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="bg-[#F9FAFB] border-b border-[#E5E7EB]">
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Bordereau #</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Treaty</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Period</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Received</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">File Name</th>
                      <th className="text-right px-4 py-2.5 font-semibold text-[#374151]">Line Count</th>
                      <th className="text-right px-4 py-2.5 font-semibold text-[#374151]">Gross Premium</th>
                      <th className="text-right px-4 py-2.5 font-semibold text-[#374151]">Ceded Premium</th>
                      <th className="text-right px-4 py-2.5 font-semibold text-[#374151]">Commission</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {imports.map((b) => {
                      const c = STATUS_BADGE[b.status] || STATUS_BADGE.uploaded
                      return (
                        <tr key={b.id} className="border-b border-[#F3F4F6] hover:bg-[#F9FAFB]">
                          <td className="px-4 py-2.5 font-mono text-xs text-[#0B0B3B]">{b.bordereau_number}</td>
                          <td className="px-4 py-2.5 font-mono text-xs text-[#374151]">{b.treaty_number}</td>
                          <td className="px-4 py-2.5 text-[#6B7280] text-xs font-mono">
                            {fmtDate(b.period_start)} → {fmtDate(b.period_end)}
                          </td>
                          <td className="px-4 py-2.5 text-[#6B7280] text-xs font-mono">
                            {fmtDate(b.received_date)}
                          </td>
                          <td className="px-4 py-2.5 text-[#374151] text-xs">{b.file_name}</td>
                          <td className="px-4 py-2.5 text-right tabular-nums text-[#374151]">
                            {b.line_count}
                          </td>
                          <td className="px-4 py-2.5 text-right font-mono tabular-nums text-[#374151]">
                            {fmtMoney(b.total_gross_premium)}
                          </td>
                          <td className="px-4 py-2.5 text-right font-mono tabular-nums text-[#374151]">
                            {fmtMoney(b.total_ceded_premium)}
                          </td>
                          <td className="px-4 py-2.5 text-right font-mono tabular-nums text-[#374151]">
                            {fmtMoney(b.total_commission)}
                          </td>
                          <td className="px-4 py-2.5">
                            <span
                              className="inline-flex text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded border"
                              style={{ background: c.bg, color: c.fg, borderColor: `${c.fg}30` }}
                            >
                              {b.status_display}
                            </span>
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
