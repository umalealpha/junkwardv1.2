'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import {
  getToken,
  getReinsurers,
  type Reinsurer,
} from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Building2, AlertTriangle, RefreshCw, Plus } from 'lucide-react'
import { NewReinsurerDialog } from '@/components/reinsurance/NewReinsurerDialog'

type ActiveFilter = 'all' | 'active' | 'inactive'

function truncate(s: string | null | undefined, n = 60): string {
  if (!s) return '—'
  const t = s.trim()
  return t.length > n ? `${t.slice(0, n - 1)}…` : t
}

export default function ReinsurersPage() {
  const router = useRouter()
  const [reinsurers, setReinsurers] = useState<Reinsurer[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState<ActiveFilter>('all')
  const [creating, setCreating] = useState(false)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const res = await getReinsurers()
      setReinsurers(res.results)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load reinsurers')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    void load()
  }, [router, load])

  const filtered = reinsurers.filter((r) => {
    if (filter === 'active') return r.is_active
    if (filter === 'inactive') return !r.is_active
    return true
  })

  const counts = {
    all: reinsurers.length,
    active: reinsurers.filter((r) => r.is_active).length,
    inactive: reinsurers.filter((r) => !r.is_active).length,
  }

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Reinsurers"
        breadcrumbs={[{ label: 'Reinsurance' }, { label: 'Reinsurers' }]}
        actions={
          <div className="flex items-center gap-2">
            <Button
              variant="outline" size="sm"
              leftIcon={<RefreshCw className="w-3.5 h-3.5" />}
              onClick={load} disabled={loading}
            >
              Refresh
            </Button>
            {/* Underwriting could not raise a counterparty at all before this
                — the whole approval chain existed with no way in. */}
            <Button
              size="sm"
              leftIcon={<Plus className="w-3.5 h-3.5" />}
              onClick={() => setCreating(true)}
            >
              New reinsurer
            </Button>
          </div>
        }
      />

      <div className="flex-1 p-6 space-y-4">
        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-[#DC2626]" />
            <p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}

        {/* Filter pills */}
        <div className="flex items-center gap-2">
          {([
            { key: 'all',      label: 'All',           count: counts.all },
            { key: 'active',   label: 'Active only',   count: counts.active },
            { key: 'inactive', label: 'Inactive only', count: counts.inactive },
          ] as { key: ActiveFilter; label: string; count: number }[]).map((p) => {
            const selected = filter === p.key
            return (
              <button
                key={p.key}
                type="button"
                onClick={() => setFilter(p.key)}
                className="text-xs font-semibold px-3 py-1.5 rounded-full border transition-colors"
                style={
                  selected
                    ? { background: '#0B0B3B', borderColor: '#0B0B3B', color: '#FFFFFF' }
                    : { background: '#FFFFFF', borderColor: '#E5E7EB', color: '#374151' }
                }
              >
                {p.label}
                <span
                  className="ml-2 inline-flex items-center justify-center text-[10px] font-mono px-1.5 py-0.5 rounded"
                  style={
                    selected
                      ? { background: '#F07F00', color: '#FFFFFF' }
                      : { background: '#F3F4F6', color: '#6B7280' }
                  }
                >
                  {p.count}
                </span>
              </button>
            )
          })}
        </div>

        <Card>
          <CardContent className="p-0">
            {loading && (
              <p className="px-6 py-8 text-center text-sm text-[#6B7280]">Loading…</p>
            )}
            {!loading && filtered.length === 0 && (
              <div className="px-6 py-12 text-center text-[#6B7280]">
                <Building2 className="w-10 h-10 mx-auto text-[#D1D5DB] mb-3" strokeWidth={1.5} />
                <p className="text-sm">No reinsurers registered.</p>
                <p className="text-xs text-[#9CA3AF] mt-2">
                  Add counterparties in Django admin under{' '}
                  <code>/admin/reinsurance/reinsurer/</code>
                </p>
              </div>
            )}
            {!loading && filtered.length > 0 && (
              <div className="overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="bg-[#F9FAFB] border-b border-[#E5E7EB]">
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Short Code</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Name</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Country</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Credit Rating</th>
                      <th className="text-right px-4 py-2.5 font-semibold text-[#374151]">Treaties</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Status</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Notes</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filtered.map((r) => {
                      const pill = r.is_active
                        ? { bg: '#ECFDF5', fg: '#047857', label: 'Active' }
                        : { bg: '#F3F4F6', fg: '#6B7280', label: 'Inactive' }
                      return (
                        <tr key={r.id} className="border-b border-[#F3F4F6] hover:bg-[#F9FAFB]">
                          <td className="px-4 py-2.5 font-mono text-xs text-[#0B0B3B]">{r.short_code}</td>
                          <td className="px-4 py-2.5 text-[#374151]">{r.name}</td>
                          <td className="px-4 py-2.5 text-[#374151] font-mono text-xs uppercase">
                            {r.country || '—'}
                          </td>
                          <td className="px-4 py-2.5 text-[#374151] font-mono text-xs">
                            {r.credit_rating || '—'}
                          </td>
                          <td className="px-4 py-2.5 text-right text-[#6B7280] text-xs">
                            <span className="font-mono tabular-nums text-[#374151]">{r.treaty_count}</span>
                            <span className="ml-1 text-[#9CA3AF]">
                              {r.treaty_count === 1 ? 'treaty' : 'treaties'}
                            </span>
                          </td>
                          <td className="px-4 py-2.5">
                            <span
                              className="inline-flex text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded border"
                              style={{ background: pill.bg, color: pill.fg, borderColor: `${pill.fg}30` }}
                            >
                              {pill.label}
                            </span>
                          </td>
                          <td className="px-4 py-2.5 text-[#6B7280] text-xs" title={r.notes || ''}>
                            {truncate(r.notes, 60)}
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

      {creating && (
        <NewReinsurerDialog
          onClose={() => setCreating(false)}
          onCreated={() => {
            setCreating(false)
            // To the control centre, NOT the record: the new draft appears in
            // the queue there WITH its "submit for underwriting review" button.
            // The record page shows the KYC file, which is not needed until the
            // compliance stage. Sending someone to a screen with no next step
            // is how the whole chain ended up with no way in.
            router.push('/reinsurance')
          }}
        />
      )}
    </div>
  )
}
