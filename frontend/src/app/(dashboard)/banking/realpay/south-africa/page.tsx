'use client'

/**
 * /banking/realpay/south-africa — RealPay SA collections segment.
 *
 * CFO directive 2026-06-03 (Charmaine + Nadine cred handover). Sandbox
 * creds for the SA beneficiary user 21175 / product ABSADC|ABSADO were
 * tested against uat.realpaycollect.com:4448 — OAuth token returned 200
 * but every report call comes back "WS-012 USER NOT LINKED TO BENEFICIARY
 * USER OR USER INACTIVE". RealPay needs to bind the SA client_id to user
 * 21175 (or issue a fresh SA-specific client_id pair) before any pull
 * can run.
 */

import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Globe, AlertTriangle, FileText } from 'lucide-react'

export default function RealPaySouthAfricaPage() {
  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="RealPay — South Africa"
        breadcrumbs={[{ label: 'Banking' }, { label: 'RealPay', href: '/banking/realpay' }, { label: 'South Africa' }]}
      />
      <div className="flex-1 p-6 space-y-4">

        <Card>
          <CardContent className="py-4">
            <div className="flex items-start gap-3">
              <Globe className="w-5 h-5 text-[#CC6C00] mt-0.5" />
              <div className="flex-1">
                <p className="text-sm text-[#0D1B2A] font-medium">
                  Beneficiary user <code className="text-xs font-mono">21175</code> ·
                  Products <code className="text-xs font-mono">ABSADC</code> / <code className="text-xs font-mono">ABSADO</code>
                </p>
                <p className="text-xs text-[#6B7280] mt-1">
                  UAT base · <code className="font-mono">https://uat.realpaycollect.com:4448/rpi/rpws</code>
                  · OAuth2 token issued (HTTP 200) 2026-06-03.
                </p>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="py-3 bg-[#ECFDF5] border border-[#A7F3D0] rounded-lg">
            <div className="flex items-start gap-2">
              <Globe className="w-4 h-4 text-[#059669] mt-0.5" />
              <div className="text-xs text-[#065F46] space-y-2">
                <p>
                  <b>UNBLOCKED 2026-06-03.</b> Earlier WS-012 was an OMNI-side OAuth
                  bug — sending <code>client_id</code> + <code>client_secret</code> as
                  body params instead of HTTP Basic on <code>/oauth/token</code> per
                  RFC 6749 §2.3.1. Token + Products + every Reports endpoint now return
                  HTTP 200 with <code>APIResponse.Status = SUCCESS</code> against
                  user 21175 (products ABSADC / ABSADO).
                </p>
                <p>
                  Collections source = Instalment Changes Report (Products report
                  confirmed bound; reports were behind same auth fix). 1 Jul → 30 Apr
                  backfill ready to run for both ABSADC and ABSADO.
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
          <CardContent>
            <p className="text-sm text-[#6B7280] py-8 text-center">
              Empty — awaiting WS-012 unlock from RealPay. Reports planned:
              <br/><span className="text-xs">transactions_report · collection_report · mandate_initiate_report · mandate_changes_report · contract_changes_report · instalment_changes_report</span>
            </p>
          </CardContent>
        </Card>

      </div>
    </div>
  )
}
