'use client'

/**
 * /banking/realpay/botswana — RealPay BW collections segment.
 *
 * CFO directive 2026-06-03 (Charmaine + Nadine cred handover). Sandbox
 * creds for the BW beneficiary user 19413 / product RTFNBBW were tested
 * against uat.realpaycollect.com:4448 — auth OK; transactions_report
 * endpoint 404 on UAT (awaiting RealPay to enable). 5 other reports
 * return 200 already; placeholder grid below for the pre-downloaded
 * reports cache once the transactions endpoint is online.
 */

import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Globe, CheckCircle2, AlertTriangle, Clock, FileText } from 'lucide-react'

const REPORTS = [
  { id: 'instalment_changes_report', label: 'Instalment Changes Report  ★ collections source', status: 'ok', detail: 'PRIMARY collections feed. Per Nadine 2026-06-03 — replaces transactions_report (now deprecated). Endpoint live (RTFNBBW).' },
  { id: 'payo_payment_changes_report', label: 'Payment changes report', status: 'ok', detail: 'Endpoint live; pre-download window 1 Jul → 30 Apr ready.' },
  { id: 'client_billing_report', label: 'Client billing report', status: 'ok', detail: 'Endpoint live (product RTFNBBW).' },
  { id: 'contract_changes_report', label: 'Contract changes report', status: 'ok', detail: 'Endpoint live (product RTFNBBW).' },
  { id: 'mandate_initiate_report', label: 'Mandate initiate report', status: 'ok', detail: 'Endpoint live (no records in sandbox yet).' },
  { id: 'transactions_report', label: 'Transactions report  ✗ deprecated', status: 'deprecated', detail: 'No longer available via API per Nadine 2026-06-03. Swagger doc is being updated. Use Instalment Changes Report instead.' },
] as const

const STATUS_PILL: Record<string, { bg: string; fg: string; icon: any; label: string }> = {
  ok:         { bg: '#ECFDF5', fg: '#059669', icon: CheckCircle2,  label: 'Live' },
  deprecated: { bg: '#F3F4F6', fg: '#6B7280', icon: AlertTriangle, label: 'Deprecated by vendor' },
  blocked:    { bg: '#FEF2F2', fg: '#DC2626', icon: AlertTriangle, label: 'Blocked (404 on UAT)' },
  pending:    { bg: '#FEF3C7', fg: '#92400E', icon: Clock,         label: 'Awaiting' },
}

export default function RealPayBotswanaPage() {
  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="RealPay — Botswana"
        breadcrumbs={[{ label: 'Banking' }, { label: 'RealPay', href: '/banking/realpay' }, { label: 'Botswana' }]}
      />
      <div className="flex-1 p-6 space-y-4">

        <Card>
          <CardContent className="py-4">
            <div className="flex items-start gap-3">
              <Globe className="w-5 h-5 text-[#CC6C00] mt-0.5" />
              <div className="flex-1">
                <p className="text-sm text-[#0D1B2A] font-medium">
                  Beneficiary user <code className="text-xs font-mono">19413</code> · Product <code className="text-xs font-mono">RTFNBBW</code> / <code className="text-xs font-mono">FNBNDOBW</code>
                </p>
                <p className="text-xs text-[#6B7280] mt-1">
                  UAT base · <code className="font-mono">https://uat.realpaycollect.com:4448/rpi/rpws</code>
                  · OAuth2 client_credentials handshake verified 2026-06-03.
                </p>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm flex items-center gap-2">
              <FileText className="w-4 h-4 text-[#CC6C00]" />
              Pre-downloaded reports cache  <span className="text-xs font-normal text-[#6B7280]">(window 1 Jul → 30 Apr)</span>
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            <table className="w-full text-sm border-collapse">
              <thead className="bg-[#F9FAFB] border-b border-[#E5E7EB]">
                <tr>
                  <th className="px-3 py-2 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Report</th>
                  <th className="px-3 py-2 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Status</th>
                  <th className="px-3 py-2 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Notes</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#E5E7EB] bg-white">
                {REPORTS.map(r => {
                  const s = STATUS_PILL[r.status]
                  const Icon = s.icon
                  return (
                    <tr key={r.id}>
                      <td className="px-3 py-3 text-[#111827]">{r.label}<br/><code className="text-[10px] text-[#9CA3AF]">{r.id}</code></td>
                      <td className="px-3 py-3">
                        <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[11px] font-medium" style={{ background: s.bg, color: s.fg }}>
                          <Icon className="w-3 h-3" /> {s.label}
                        </span>
                      </td>
                      <td className="px-3 py-3 text-[#6B7280] text-xs">{r.detail}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="py-3 bg-[#ECFDF5] border border-[#A7F3D0] rounded-lg">
            <p className="text-xs text-[#065F46]">
              <b>UNBLOCKED 2026-06-03.</b> Nadine confirmed <b>transactions_report</b> is
              deprecated and replaced by <b>Instalment Changes Report</b> — backend client
              (<code>realpay/client.py :: list_transactions</code>) rewired to call
              <code>/reports/instalment_changes_report/RTFNBBW?BeneficiaryUser=19413&amp;Version=v1</code>
              with HTTP Basic OAuth handshake. End-to-end probe HTTP 200 / Status SUCCESS.
              1 Jul → 30 Apr backfill ready to run.
            </p>
          </CardContent>
        </Card>

      </div>
    </div>
  )
}
