'use client'

/**
 * /period-close — Period-close wizard
 *
 * Flow:
 *   1. Pick a fiscal period (status=open)
 *   2. Page calls /fiscal-periods/{id}/close-checks/ → list of blockers
 *   3. Each block shows red ✗; an empty list shows green ✓ ready
 *   4. Reviewer signs off in a free-text field
 *   5. "Close Period" button — POST /fiscal-periods/{id}/close/
 */

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import {
  getFiscalPeriods, getPeriodCloseChecks, closePeriod, getToken,
} from '@/lib/api'
import type { FiscalPeriod, PeriodCloseChecks } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/modal'
import {
  CheckCircle2, AlertCircle, Lock, Loader2, RefreshCw, ShieldCheck, Calendar,
} from 'lucide-react'
import { cn } from '@/lib/utils'

const CHECK_LABELS: Record<string, string> = {
  open_journal_entries:        'Draft / pending journal entries',
  open_purchase_orders:        'Open purchase orders raised in period',
  fx_revaluation_missing:      'FX revaluation not posted (foreign-currency balances exist)',
  account_reconciliations:     'Outstanding account reconciliations',
}

function prettyKey(k: string): string {
  return CHECK_LABELS[k] || k.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
}

export default function PeriodClosePage() {
  const router = useRouter()
  const [periods, setPeriods] = useState<FiscalPeriod[]>([])
  const [selectedId, setSelectedId] = useState<string>('')
  const [checks, setChecks] = useState<PeriodCloseChecks | null>(null)
  const [loadingChecks, setLoadingChecks] = useState(false)
  const [signoff, setSignoff] = useState('')
  const [closing, setClosing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [showConfirm, setShowConfirm] = useState(false)
  // CFO directive 2026-05-18: closing a period now requires the financial
  // lock password on top of the CFO-title check, matching the new fiscal
  // periods page. Prompt for it inline before sending the request.
  const [overridePw, setOverridePw] = useState('')

  const loadPeriods = useCallback(async () => {
    if (!getToken()) { router.replace('/login'); return }
    try {
      const res = await getFiscalPeriods()
      const open = res.results.filter(p => p.status === 'open')
      setPeriods(open)
      if (open.length > 0 && !selectedId) setSelectedId(open[0].id)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load periods')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router])

  useEffect(() => { loadPeriods() }, [loadPeriods])

  const loadChecks = useCallback(async () => {
    if (!selectedId) return
    setLoadingChecks(true); setError(null)
    try {
      const c = await getPeriodCloseChecks(selectedId)
      setChecks(c)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load close checks')
    } finally { setLoadingChecks(false) }
  }, [selectedId])

  useEffect(() => { loadChecks() }, [loadChecks])

  const handleClose = async () => {
    setShowConfirm(false)
    if (!overridePw) {
      setError('Financial-lock password is required to close a period.')
      return
    }
    setClosing(true); setError(null)
    try {
      const updated = await closePeriod(selectedId, overridePw, signoff)
      setSuccess(`Period ${updated.period_name} closed successfully.`)
      setTimeout(() => setSuccess(null), 5000)
      await loadPeriods()
      await loadChecks()
      setSignoff('')
      setOverridePw('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to close period')
    } finally { setClosing(false) }
  }

  const blockingIssues = checks ? Object.entries(checks.issues) : []
  const ready = !!checks?.ready_to_close && signoff.trim().length >= 10 && overridePw.length > 0

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar title="Period Close" breadcrumbs={[{ label: 'Finance' }, { label: 'Period Close' }]} />

      <div className="flex-1 p-6 max-w-4xl mx-auto w-full space-y-5">
        <div className="flex items-start gap-3">
          <Calendar className="w-5 h-5 mt-0.5" style={{ color: '#CC6C00' }} />
          <div>
            <h1 className="text-[20px] font-bold text-[#0B0B3B]">Period Close Wizard</h1>
            <p className="text-sm text-[#6B7280] mt-1">
              Close a fiscal period only after every check passes. Once closed, no further
              entries can be posted to dates within it. CFO authority required.
            </p>
          </div>
        </div>

        {success && (
          <div className="bg-[#ECFDF5] border border-[#A7F3D0] rounded-lg p-3 flex items-center gap-2">
            <CheckCircle2 className="w-4 h-4 text-[#059669]" /><p className="text-[#059669] text-sm">{success}</p>
          </div>
        )}
        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-[#DC2626]" /><p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}

        <Card>
          <CardHeader><CardTitle>1. Select an open period</CardTitle></CardHeader>
          <CardContent>
            {periods.length === 0 ? (
              <p className="text-sm text-[#9CA3AF]">No open periods.</p>
            ) : (
              <select
                value={selectedId}
                onChange={(e) => setSelectedId(e.target.value)}
                className="w-full text-sm rounded-md px-3 py-2 border border-[#E5E7EB] bg-white"
              >
                {periods.map(p => (
                  <option key={p.id} value={p.id}>
                    {p.period_name} — {p.start_date} to {p.end_date}
                  </option>
                ))}
              </select>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <CardTitle>2. Pre-close checklist</CardTitle>
              <Button variant="outline" size="sm" leftIcon={<RefreshCw className="w-3.5 h-3.5" />} onClick={loadChecks} disabled={loadingChecks || !selectedId}>
                Re-run
              </Button>
            </div>
          </CardHeader>
          <CardContent>
            {loadingChecks ? (
              <div className="flex items-center gap-2 text-sm text-[#6B7280]"><Loader2 className="w-4 h-4 animate-spin" />Running checks…</div>
            ) : !checks ? (
              <p className="text-sm text-[#9CA3AF]">Select a period to run checks.</p>
            ) : checks.ready_to_close ? (
              <div className="bg-[#ECFDF5] border border-[#A7F3D0] rounded-lg p-4 flex items-start gap-3">
                <CheckCircle2 className="w-5 h-5 text-[#059669] mt-0.5" />
                <div>
                  <p className="text-sm font-semibold text-[#059669]">All checks passed.</p>
                  <p className="text-xs text-[#047857] mt-1">No draft / pending entries, no open POs, FX revaluation up to date. Ready to close.</p>
                </div>
              </div>
            ) : (
              <div className="space-y-3">
                {blockingIssues.map(([key, items]) => (
                  <div key={key} className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3">
                    <div className="flex items-center gap-2 mb-2">
                      <AlertCircle className="w-4 h-4 text-[#DC2626]" />
                      <p className="text-sm font-semibold text-[#DC2626]">{prettyKey(key)}</p>
                      <span className="ml-auto text-[11px] font-medium text-[#DC2626]">{items.length} blocker{items.length === 1 ? '' : 's'}</span>
                    </div>
                    <ul className="text-xs text-[#7F1D1D] pl-6 list-disc space-y-0.5">
                      {items.slice(0, 8).map((item, i) => <li key={i}>{item}</li>)}
                      {items.length > 8 && <li className="italic">…and {items.length - 8} more</li>}
                    </ul>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>3. Reviewer sign-off</CardTitle></CardHeader>
          <CardContent>
            <textarea
              value={signoff}
              onChange={(e) => setSignoff(e.target.value)}
              placeholder="I have reviewed all account reconciliations, the FX revaluation, and the trial balance for this period. Closing on the authority of..."
              rows={4}
              className="w-full text-sm rounded-md px-3 py-2 border border-[#E5E7EB]"
              disabled={!checks?.ready_to_close}
            />
            <p className="text-[11px] text-[#9CA3AF] mt-2">
              Minimum 10 characters. Required before close. Stored in the audit log.
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>4. Financial-lock authentication</CardTitle></CardHeader>
          <CardContent>
            <input
              type="password"
              value={overridePw}
              onChange={(e) => setOverridePw(e.target.value)}
              placeholder="OMNI_FINANCIAL_LOCK_OVERRIDE"
              className="w-full text-sm rounded-md px-3 py-2 border border-[#E5E7EB]"
              disabled={!checks?.ready_to_close}
              autoComplete="off"
            />
            <p className="text-[11px] text-[#9CA3AF] mt-2">
              CFO directive 2026-05-18: closing a period requires the financial-lock password
              on top of the CFO title check. Ask the CFO for the current value.
            </p>
          </CardContent>
        </Card>

        <div className="flex items-center justify-end gap-3">
          <Button variant="outline" size="md" onClick={() => router.push('/dashboard')}>Cancel</Button>
          <Button
            variant="accent"
            size="md"
            leftIcon={<Lock className="w-4 h-4" />}
            disabled={!ready || closing}
            onClick={() => setShowConfirm(true)}
          >
            {closing ? 'Closing…' : 'Close Period'}
          </Button>
        </div>

        <div className="text-[11px] text-[#9CA3AF] flex items-center gap-1.5">
          <ShieldCheck className="w-3 h-3" />
          Only users with CFO title (or superuser) can complete this step.
        </div>
      </div>

      <ConfirmDialog
        open={showConfirm}
        onOpenChange={setShowConfirm}
        title="Close this period?"
        description={`Closing ${checks?.period_name || ''} cannot be undone. All entries dated in this period will be locked from posting.`}
        confirmLabel="Close Period"
        variant="warning"
        loading={closing}
        onConfirm={handleClose}
      />
    </div>
  )
}
