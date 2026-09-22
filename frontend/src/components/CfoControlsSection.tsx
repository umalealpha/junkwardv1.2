'use client'

/**
 * CfoControlsSection — the CFO-only control band shown at the bottom of the
 * main dashboard (CFO 2026-08-22 dashboard merge). It absorbs the unique tiles
 * of the old standalone /cfo page (approvals, overdue, related-party, asset
 * sign-offs, PO authorisations, audit) so there is ONE executive dashboard.
 *
 * It self-hides: getCFODashboard() is server-gated (CanViewFinancials → 403 for
 * everyone else), so on any error OR before data loads this renders null — a
 * non-finance user never sees it and it can never break the shared dashboard.
 * The money KPIs (cash / AR / AP / net profit) are deliberately omitted here —
 * the dashboard already shows them; this band is controls only.
 */

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { getCFODashboard } from '@/lib/api'
import type { CFODashboard } from '@/lib/api'
import { AIInsightRibbon } from '@/components/AIInsightRibbon'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { formatAmount, cn, formatDateTime } from '@/lib/utils'
import { useNumberFormat } from '@/contexts/NumberFormatContext'
import {
  ClipboardCheck, FileWarning, ShieldAlert, Calendar, ArrowUpRight,
} from 'lucide-react'

export function CfoControlsSection() {
  const router = useRouter()
  const { mode } = useNumberFormat()
  const fmt = (a: string | number, c = 'BWP') => formatAmount(a, c, mode)

  const [data, setData] = useState<CFODashboard | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let cancelled = false
    getCFODashboard()
      .then((d) => { if (!cancelled) setData(d) })
      .catch(() => { if (!cancelled) setFailed(true) })
    return () => { cancelled = true }
  }, [])

  // Invisible to non-finance users, and never blocks the shared dashboard.
  if (failed || !data) return null

  return (
    <section className="space-y-5 mt-6">
      {/* Compact branded band header — matches the CFO page hero, slimmed. */}
      <div
        className="relative overflow-hidden rounded-2xl"
        style={{ background: '#FFFFFF', border: '1px solid rgba(13,27,42,0.06)', boxShadow: '0 10px 40px -12px rgba(13,27,42,0.12)' }}
      >
        <div
          className="absolute -top-32 -right-24 w-[36rem] h-[36rem] rounded-full pointer-events-none"
          style={{
            background: `radial-gradient(circle at 30% 30%, rgba(240,127,0,0.16) 0%, transparent 55%),
                         radial-gradient(circle at 70% 60%, rgba(168,85,247,0.14) 0%, transparent 55%)`,
            filter: 'blur(40px)',
          }}
        />
        <div className="relative px-5 py-5 lg:px-7 lg:py-6" style={{ color: '#0D1B2A' }}>
          <p className="text-[11px] uppercase tracking-[0.25em] font-semibold mb-2" style={{ color: '#0D1B2A', opacity: 0.55 }}>
            CFO Command Centre &middot; as of {formatDateTime(data.as_of)}
          </p>
          <h2 className="font-display-tight text-2xl lg:text-3xl font-bold leading-tight" style={{ color: '#0D1B2A' }}>
            <span style={{ opacity: 0.92 }}>Controls that need</span>{' '}
            <span
              className="italic bg-clip-text text-transparent"
              style={{ backgroundImage: 'linear-gradient(120deg, #0D1B2A 0%, #F07F00 60%, #FF9A2E 100%)' }}
            >
              your eyes today.
            </span>
          </h2>
          <div className="mt-3">
            <AIInsightRibbon ctx="cfo" />
          </div>
        </div>
      </div>

      {/* KPI tiles — controls */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <KpiTile icon={<ClipboardCheck className="w-5 h-5" />} label="Pending Approvals" value={String(data.pending_approvals.count_total)} accent="#2563EB" onClick={() => router.push('/approvals')} />
        <KpiTile icon={<FileWarning className="w-5 h-5" />} label="Overdue Invoices" value={data.overdue_invoices.count === 0 ? '0' : `${data.overdue_invoices.count} · ${fmt(data.overdue_invoices.total_amount, 'BWP')}`} accent="#DC2626" onClick={() => router.push('/invoices?tab=overdue')} />
        <KpiTile icon={<ShieldAlert className="w-5 h-5" />} label="Related-Party (this week)" value={String(data.related_party_this_week.length)} accent="#7C3AED" onClick={() => router.push('/journal-entries?related_party=1')} />
        <KpiTile icon={<Calendar className="w-5 h-5" />} label="Asset Sign-offs Overdue" value={String(data.asset_signoffs.overdue_count)} accent="#D97706" onClick={() => router.push('/approvals')} />
      </div>

      {/* Tax Pack drill-through tile */}
      <div
        role="button"
        tabIndex={0}
        onClick={() => router.push('/reports/tax-reconciliation')}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); router.push('/reports/tax-reconciliation') } }}
        className="rounded-xl p-4 bg-white border border-[#E5E7EB] cursor-pointer hover:shadow-md hover:border-[#FFD7B5] focus:outline-none focus:ring-2 focus:ring-[#F4A623] transition-all flex items-center justify-between gap-4"
        aria-label="Open Tax Reconciliation"
      >
        <div className="min-w-0">
          <p className="text-[11px] font-semibold text-[#6B7280] uppercase tracking-wider">Tax Pack</p>
          <p className="font-display-tight text-base font-bold text-[#111827] mt-0.5">Tax Reconciliation</p>
          <p className="text-xs text-[#6B7280] mt-0.5">ETR analysis, IAS 12 reconciliation, current vs deferred tax.</p>
        </div>
        <ArrowUpRight className="w-5 h-5 text-[#F07F00] flex-shrink-0" />
      </div>

      {/* Awaiting approval + audit log columns */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <Card>
          <CardHeader><CardTitle>Awaiting your approval</CardTitle></CardHeader>
          <CardContent className="p-0">
            {data.pending_approvals.count_total === 0 ? (
              <p className="text-sm text-[#9CA3AF] p-6">Inbox clear.</p>
            ) : (
              <div className="divide-y divide-[#E5E7EB]">
                {data.pending_approvals.awaiting_me_je.map(je => (
                  <div
                    key={je.id}
                    role="button"
                    tabIndex={0}
                    aria-label={`Open journal entry ${je.entry_number}`}
                    className="px-4 py-3 hover:bg-[#FFF7ED] cursor-pointer focus:outline-none focus:bg-[#FFF7ED]"
                    onClick={() => router.push(`/journal-entries/${je.id}`)}
                    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); router.push(`/journal-entries/${je.id}`) } }}
                  >
                    <p className="text-sm font-medium text-[#111827]"><span className="font-mono text-[#CC6C00]">{je.entry_number}</span> — {je.description}</p>
                    {je.total_amount && <p className="text-xs text-[#6B7280] mt-0.5">{fmt(je.total_amount, 'BWP')}</p>}
                  </div>
                ))}
                {data.pending_approvals.awaiting_my_second_import.map(b => (
                  <div
                    key={b.id}
                    role="button"
                    tabIndex={0}
                    aria-label={`Open import batch awaiting approval`}
                    className="px-4 py-3 hover:bg-[#FFF7ED] cursor-pointer focus:outline-none focus:bg-[#FFF7ED]"
                    onClick={() => router.push('/approvals')}
                    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); router.push('/approvals') } }}
                  >
                    <p className="text-sm font-medium text-[#111827]">Import batch · {b.source} · {b.rows_total} rows</p>
                    <p className="text-xs text-[#6B7280] mt-0.5">Awaiting second approver</p>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>Recent audit activity</CardTitle></CardHeader>
          <CardContent className="p-0">
            {data.audit_recent.length === 0 ? (
              <p className="text-sm text-[#9CA3AF] p-6">No recent activity.</p>
            ) : (
              <div className="divide-y divide-[#E5E7EB]">
                {data.audit_recent.slice(0, 8).map(a => (
                  <div key={a.id} className="px-4 py-2.5">
                    <p className="text-sm text-[#374151]">{a.description || `${a.action} ${a.table_name}`}</p>
                    <p className="text-[11px] text-[#9CA3AF] mt-0.5">{a.user__username || 'system'} · {new Date(a.created_at).toLocaleString()}</p>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Operational PO authorisations today */}
      {data.po_operational_authorizations && (
        <Card>
          <CardHeader>
            <CardTitle>
              Operational PO authorisations — today
              <span className="ml-2 text-sm font-normal text-[#6B7280]">
                {data.po_operational_authorizations.count} PO · {data.po_operational_authorizations.fm_only} at FM level only · {fmt(data.po_operational_authorizations.total_bwp, 'BWP')}
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            {data.po_operational_authorizations.count === 0 ? (
              <p className="px-4 py-6 text-sm text-[#6B7280]">No operational (Admin / HR) purchase orders authorised today.</p>
            ) : (
              <div className="divide-y divide-[#E5E7EB]">
                {data.po_operational_authorizations.items.map(p => (
                  <div
                    key={p.po_number}
                    role="button"
                    tabIndex={0}
                    aria-label={`Open purchase order ${p.po_number}`}
                    className="px-4 py-2.5 hover:bg-[#FFF7ED] cursor-pointer focus:outline-none focus:bg-[#FFF7ED] flex items-center justify-between gap-3"
                    onClick={() => router.push('/purchase-orders')}
                    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); router.push('/purchase-orders') } }}
                  >
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-[#111827] truncate"><span className="font-mono text-[#CC6C00]">{p.po_number}</span> — {p.supplier}</p>
                      <p className="text-xs text-[#6B7280] mt-0.5">{p.department} · {p.fm_only ? 'FM level only' : 'FM + CFO'}</p>
                    </div>
                    <p className="text-sm font-semibold text-[#111827] whitespace-nowrap">{fmt(p.total_bwp, 'BWP')}</p>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* Related-party this week */}
      {data.related_party_this_week.length > 0 && (
        <Card>
          <CardHeader><CardTitle>Related-party transactions — this week</CardTitle></CardHeader>
          <CardContent className="p-0">
            <div className="divide-y divide-[#E5E7EB]">
              {data.related_party_this_week.map(rp => (
                <div
                  key={rp.id}
                  role="button"
                  tabIndex={0}
                  aria-label={`Open related-party journal entry ${rp.entry_number}`}
                  className="px-4 py-3 hover:bg-[#FFF7ED] cursor-pointer focus:outline-none focus:bg-[#FFF7ED]"
                  onClick={() => router.push(`/journal-entries/${rp.id}`)}
                  onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); router.push(`/journal-entries/${rp.id}`) } }}
                >
                  <p className="text-sm font-medium text-[#111827]"><span className="font-mono text-[#CC6C00]">{rp.entry_number}</span> — {rp.description}</p>
                  <p className="text-xs text-[#6B7280] mt-0.5">{rp.entry_date}</p>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </section>
  )
}

function KpiTile({ icon, label, value, accent, onClick }: {
  icon: React.ReactNode; label: string; value: string; accent: string; onClick?: () => void
}) {
  return (
    <div
      onClick={onClick}
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
      onKeyDown={(e) => {
        if (!onClick) return
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onClick() }
      }}
      aria-label={onClick ? `${label}: ${value}` : undefined}
      className={cn('rounded-xl p-4 bg-white border border-[#E5E7EB] transition-all', onClick && 'cursor-pointer hover:shadow-md hover:border-[#FFD7B5] focus:outline-none focus:ring-2 focus:ring-[#F4A623]')}
    >
      <div className="flex items-center justify-between">
        <p className="text-[11px] font-semibold text-[#6B7280] uppercase tracking-wider">{label}</p>
        <span style={{ color: accent }}>{icon}</span>
      </div>
      <p className="font-display-tight text-2xl font-bold mt-2 tabular-nums" style={{ color: accent }}>{value}</p>
    </div>
  )
}
