'use client'

/**
 * /reports/tax-reconciliation — Tax Pack (TAX-006).
 *
 * Internal Audit / Finance view of the full IAS 12 tax disclosure.
 * The slim TaxRowSlim on the P&L face links here. This page hosts
 * the original TaxBlock component (current vs deferred, WHT,
 * permanent/temporary differences, statutory-rate reconciliation,
 * ETR variance analysis).
 *
 * Until TAX-002 (GL split) lands, the block degrades gracefully to
 * a single-GL view with an amber warning — that warning is
 * appropriate HERE (Finance + Audit audience) and was the reason
 * TAX-005 moved it off the P&L face.
 */

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { getMAProfitLoss, getMe, getToken } from '@/lib/api'
import type { MAProfitLossReport, UserProfile } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { ArrowLeft, AlertCircle, RefreshCw, Info, Lock } from 'lucide-react'
import { formatDate, getFyStart, localYmd, parseAmount, today } from '@/lib/utils'
import { useCompany } from '@/contexts/CompanyContext'
import TaxBlock from '@/components/reports/TaxBlock'

// TAX-006 audit sign-off requirement: Tax Pack restricted to Finance /
// CFO / Auditor. Operations & executive titles redirect away.
const TAX_PACK_TITLES = new Set<string>([
  'cfo', 'finance_manager', 'financial_controller',
  'accountant', 'finance_analyst', 'bookkeeper', 'auditor',
])

function canViewTaxPack(p: UserProfile | null): boolean {
  if (!p) return false
  if (p.is_administrator) return true
  return TAX_PACK_TITLES.has(p.title as string)
}

// CFO directive 2026-05-21: Tax JEs typically post at year-end (30-Jun).
// Defaulting to the current FY (Jul→today) showed P 0.00 because the most
// recent tax accrual sits in the prior FY. Open the window 24 months wide
// so the user sees real numbers without needing to widen the date range
// every time.
function defaultFromDate(): string {
  const d = new Date()
  d.setFullYear(d.getFullYear() - 2)
  d.setMonth(6); d.setDate(1)   // 1 July, two years back
  return localYmd(d)
}

export default function TaxReconciliationPage() {
  const router = useRouter()
  const { selectedId: companyId, selected: selectedCompany } = useCompany()
  const currency = (selectedCompany?.base_currency || 'BWP') as string

  const [fromDate, setFromDate] = useState(defaultFromDate())
  const [toDate,   setToDate]   = useState(today())
  const [report,   setReport]   = useState<MAProfitLossReport | null>(null)
  const [loading,  setLoading]  = useState(true)
  const [error,    setError]    = useState<string | null>(null)
  // undefined while probing, true allowed, false redirect.
  const [allowed,  setAllowed]  = useState<boolean | undefined>(undefined)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await getMAProfitLoss(fromDate, toDate, companyId)
      setReport(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load tax data')
    } finally {
      setLoading(false)
    }
  }, [fromDate, toDate, companyId])

  useEffect(() => {
    const token = getToken()
    if (!token) { router.replace('/login'); return }
    getMe()
      .then(p => setAllowed(canViewTaxPack(p)))
      .catch(() => setAllowed(false))
  }, [router])

  useEffect(() => {
    if (allowed === false) router.replace('/dashboard')
  }, [allowed, router])

  useEffect(() => {
    if (allowed === true) load()
  }, [allowed, load])

  // Builder convention (reporting/ma_pl.py:182): taxation = -_q(...) so the
  // value is already signed for the PBT→PAT subtraction (`pat = pbt + taxation`).
  // For an expense window this comes out negative. TaxBlock + ETR need the
  // expense magnitude (positive), so flip the sign before passing.
  const taxationSigned = report ? parseAmount(report.totals.taxation) : 0
  const totalTax = -taxationSigned
  const pbt      = report ? parseAmount(report.totals.pbt) : 0
  const period   = report ? `${formatDate(report.from_date)} – ${formatDate(report.to_date)}` : ''
  const noTaxInWindow = report !== null && totalTax === 0

  if (allowed === undefined) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="w-8 h-8 border-2 border-[#F07F00] border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }
  if (allowed === false) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <Card className="max-w-md">
          <CardContent className="py-6 flex items-start gap-3">
            <Lock className="w-5 h-5 text-amber-600 flex-shrink-0 mt-0.5" />
            <div>
              <p className="font-semibold text-amber-800">Tax Pack restricted</p>
              <p className="text-sm text-amber-700 mt-1">
                The Tax Reconciliation page is restricted to Finance, CFO,
                Financial Controller, and Auditor roles. Returning to dashboard…
              </p>
            </div>
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Tax Reconciliation"
        breadcrumbs={[{ label: 'Reports', href: '/reports' }, { label: 'Tax Pack' }]}
        actions={
          <Button variant="secondary" size="sm" onClick={load} loading={loading}>
            <RefreshCw className={`w-4 h-4 mr-1 ${loading ? 'animate-spin' : ''}`} /> Refresh
          </Button>
        }
      />

      <main className="flex-1 px-6 py-6 space-y-6">
        <div className="flex items-center justify-between">
          <Button variant="ghost" size="sm" onClick={() => router.back()}>
            <ArrowLeft className="h-4 w-4 mr-1" /> Back
          </Button>
        </div>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Period</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-4 items-end">
              <div>
                <label className="block text-xs text-[#374151] font-medium mb-1">From</label>
                <input type="date" value={fromDate} onChange={e => setFromDate(e.target.value)} aria-label="From"
                       className="bg-white border border-[#D1D5DB] rounded-lg px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="block text-xs text-[#374151] font-medium mb-1">To</label>
                <input type="date" value={toDate} onChange={e => setToDate(e.target.value)} aria-label="To"
                       className="bg-white border border-[#D1D5DB] rounded-lg px-3 py-2 text-sm" />
              </div>
              <Button size="sm" onClick={load} disabled={loading}>Apply</Button>
            </div>
            <p className="mt-3 text-xs text-muted-foreground">
              IAS 12 tax disclosure — current vs deferred, withholding tax,
              permanent / temporary differences, statutory-rate reconciliation.
              Source figures come from /reports/profit-loss (MA P&L).
            </p>
          </CardContent>
        </Card>

        {error && (
          <Card className="border-red-300 bg-red-50/40 dark:bg-red-950/20">
            <CardContent className="py-3 flex items-center gap-2 text-sm">
              <AlertCircle className="h-4 w-4 text-red-600" /> {error}
            </CardContent>
          </Card>
        )}

        {noTaxInWindow && (
          <Card className="border-amber-300 bg-amber-50">
            <CardContent className="py-3 flex items-start gap-2 text-sm text-amber-800">
              <Info className="h-4 w-4 mt-0.5 flex-shrink-0" />
              <div>
                <strong>No tax JEs in this window.</strong> Tax is typically
                accrued at year-end. The most recent recorded tax entries on
                the GL are on <strong>30-Jun-2025</strong> (FY25 close):
                <ul className="mt-1 ml-4 list-disc">
                  <li>Income Tax Expense (119002): BWP 1,003,589.00</li>
                  <li>Deferred Tax Expense (119001): BWP (306,431.00)</li>
                  <li>Net FY25 tax: <strong>BWP 697,158.00</strong></li>
                </ul>
                Widen the From-date to <strong>2024-07-01</strong> to see them,
                or accrue current-year tax via a journal entry.
              </div>
            </CardContent>
          </Card>
        )}

        {report && (
          <Card>
            <CardHeader>
              <CardTitle className="text-base flex items-baseline gap-3">
                <span>{selectedCompany?.name || 'ADIC'}</span>
                <span className="text-xs text-muted-foreground">
                  {period} · {currency}
                </span>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <TaxBlock
                glBalances={{ taxTotal: totalTax }}
                pbt={pbt}
                period={period}
                currency={currency}
                isExpanded={true}
              />
            </CardContent>
          </Card>
        )}
      </main>
    </div>
  )
}
