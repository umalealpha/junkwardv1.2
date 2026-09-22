'use client'

/**
 * /payments/approvals — PAY-003 tier maker-checker worklist.
 *
 * Lists every outbound payment sitting at approval_status=pending, scoped to
 * the topbar entity. The approver opens a row to approve/reject (with a
 * mandatory comment) on the payment detail page. Approving posts the GL
 * (DR Accounts Payable / CR Bank) and ties the AP aging back to the
 * Balance Sheet.
 */

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { getPaymentApprovalQueue, getToken } from '@/lib/api'
import type { PaymentDetail } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { LoadingTable } from '@/components/ui/loading'
import { formatAmount, formatDate, cn } from '@/lib/utils'
import { useNumberFormat } from '@/contexts/NumberFormatContext'
import { useCompany } from '@/contexts/CompanyContext'
import { ShieldCheck, AlertCircle, Inbox } from 'lucide-react'

const TIER_LABEL: Record<number, string> = { 1: 'Tier 1 · Finance Manager', 2: 'Tier 2 · Head of Finance', 3: 'Tier 3 · CFO' }

// How long a payment has been waiting for approval — shown as a colour-coded chip
// so a stale one is obvious at a glance (CFO 2026-08-22). Amber from 3 days, red
// from 6.
function waitingChip(iso: string | null | undefined): { label: string; cls: string } | null {
  if (!iso) return null
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000)
  if (isNaN(days) || days < 0) return null
  const label = days === 0 ? 'today' : days === 1 ? '1 day' : `${days} days`
  const cls = days >= 6 ? 'bg-[#FEF2F2] text-[#DC2626]'
            : days >= 3 ? 'bg-[#FFF7ED] text-[#CC6C00]'
            : 'bg-[#F3F4F6] text-[#6B7280]'
  return { label, cls }
}

export default function PaymentApprovalsPage() {
  const router = useRouter()
  const { mode } = useNumberFormat()
  const fmt = (a: string | number, c = 'BWP') => formatAmount(a, c, mode)
  const { selectedId: companyId } = useCompany()

  const [rows, setRows] = useState<PaymentDetail[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const params: Record<string, string> = {}
      if (companyId) params.company = companyId
      const res = await getPaymentApprovalQueue(params)
      setRows(Array.isArray(res) ? res : [])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load approval queue')
    } finally { setLoading(false) }
  }, [companyId])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load])

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Payment Approvals"
        breadcrumbs={[{ label: 'Payables' }, { label: 'Payment Approvals' }]}
      />
      <div className="flex-1 p-6 space-y-4">
        <div className="flex items-center gap-2 text-sm text-[#6B7280]">
          <ShieldCheck className="w-4 h-4 text-[#CC6C00]" />
          <span>
            Outbound payments awaiting tier approval. Open a row to approve/reject — the
            creator/submitter cannot approve their own (segregation of duties), a comment is
            mandatory, and approval posts DR Accounts Payable / CR Bank.
          </span>
        </div>

        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-[#DC2626]" />
            <p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}

        <Card>
          <CardContent className="p-0">
            {loading ? <LoadingTable rows={6} cols={6} /> : rows.length === 0 ? (
              <div className="px-4 py-16 text-center">
                <Inbox className="w-8 h-8 text-[#D1D5DB] mx-auto mb-2" />
                <p className="text-[#6B7280] text-sm font-medium">No payments awaiting approval</p>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm border-collapse">
                  <thead className="bg-[#F9FAFB] border-b border-[#E5E7EB]">
                    <tr>
                      <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Payment #</th>
                      <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Vendor</th>
                      <th className="px-3 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider">Amount (BWP)</th>
                      <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Tier</th>
                      <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Submitted by</th>
                      <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Submitted</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#E5E7EB] bg-white">
                    {rows.map(p => (
                      <tr key={p.id} onClick={() => router.push(`/payments/${p.id}`)} className="cursor-pointer transition-colors hover:bg-[#FFF7ED]">
                        <td className="px-3 py-3 font-mono text-xs text-[#CC6C00] font-semibold whitespace-nowrap">{p.payment_number}</td>
                        <td className="px-3 py-3 text-[#111827] max-w-[260px] truncate">
                          {(p as any).is_once_off && (p as any).payee_name ? (
                            <>
                              {(p as any).payee_name}
                              <span className="ml-1.5 inline-flex px-1.5 py-0.5 rounded text-[10px] font-semibold bg-[#FFF7ED] text-[#CC6C00]">ONE-OFF</span>
                            </>
                          ) : p.contact_name}
                        </td>
                        <td className="px-3 py-3 text-right font-mono-nums text-[#111827] font-semibold whitespace-nowrap">{fmt(p.amount_bwp)}</td>
                        <td className="px-3 py-3">
                          <span className={cn(
                            'inline-flex items-center px-2 py-0.5 rounded-md text-[11px] font-medium',
                            p.approval_tier === 3 ? 'bg-[#FEF2F2] text-[#DC2626]' : 'bg-[#FFF7ED] text-[#CC6C00]'
                          )}>
                            {p.approval_tier ? (TIER_LABEL[p.approval_tier] || `Tier ${p.approval_tier}`) : '—'}
                          </span>
                        </td>
                        <td className="px-3 py-3 text-[#374151]">{p.submitted_by_name || '—'}</td>
                        <td className="px-3 py-3 text-[#6B7280] text-xs whitespace-nowrap">
                          {p.submitted_for_approval_at ? formatDate(p.submitted_for_approval_at) : '—'}
                          {(() => {
                            const w = waitingChip(p.submitted_for_approval_at || p.created_at)
                            return w ? <span className={cn('ml-1.5 inline-flex px-1.5 py-0.5 rounded text-[10px] font-semibold', w.cls)}>{w.label}</span> : null
                          })()}
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
