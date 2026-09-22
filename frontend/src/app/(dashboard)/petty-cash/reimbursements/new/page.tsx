'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import {
  getToken,
  getPettyCashLocations,
  previewPettyCashReimbursement,
  createPettyCashReimbursement,
  submitPettyCashReimbursement,
  type PettyCashLocation,
  type PettyCashReimbursementPreview,
} from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  ArrowLeft, Coins, AlertTriangle, CheckCircle2, RefreshCw, Send,
} from 'lucide-react'

function fmtMoney(s: string | null | undefined): string {
  if (!s) return '—'
  const n = Number(s)
  return isFinite(n)
    ? n.toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : s
}

// Format a Date as a LOCAL calendar date. NOT toISOString() — that shifts to
// UTC first, so in Botswana (UTC+2) local midnight on the 1st becomes the
// previous day, making the default window "May 31 → Jun 29" and silently
// dropping every voucher dated the last day of the month from the sweep.
function localISO(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function defaultPeriod(): { start: string; end: string } {
  const today = new Date()
  // Default = previous calendar month (typical petty cash reimbursement cycle)
  const lastDayPrev = new Date(today.getFullYear(), today.getMonth(), 0)
  const firstDayPrev = new Date(today.getFullYear(), today.getMonth() - 1, 1)
  return {
    start: localISO(firstDayPrev),
    end: localISO(lastDayPrev),
  }
}

export default function NewPettyCashReimbursementPage() {
  const router = useRouter()
  const [locations, setLocations] = useState<PettyCashLocation[]>([])
  const [locationId, setLocationId] = useState('')
  const period = defaultPeriod()
  const [periodStart, setPeriodStart] = useState(period.start)
  const [periodEnd, setPeriodEnd] = useState(period.end)
  const [notes, setNotes] = useState('')

  const [preview, setPreview] = useState<PettyCashReimbursementPreview | null>(null)
  const [submitted, setSubmitted] = useState(false)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    void (async () => {
      try {
        const res = await getPettyCashLocations()
        const active = res.results.filter((l) => l.is_active)
        setLocations(active)
        if (active.length === 1) setLocationId(active[0].id)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load locations')
      } finally {
        setLoading(false)
      }
    })()
  }, [router])

  async function runPreview() {
    if (!locationId) { setError('Pick a location first.'); return }
    setBusy(true); setError(null); setSubmitted(false)
    try {
      const p = await previewPettyCashReimbursement({
        location: locationId,
        period_start: periodStart,
        period_end: periodEnd,
      })
      setPreview(p)
    } catch (err) {
      setPreview(null)
      setError(err instanceof Error ? err.message : 'Preview failed')
    } finally {
      setBusy(false)
    }
  }

  async function createAndSubmit() {
    if (!locationId) return
    setBusy(true); setError(null)
    try {
      const d = await createPettyCashReimbursement({
        location: locationId,
        period_start: periodStart,
        period_end: periodEnd,
        notes,
      })
      // Send straight to FM review — nothing posts to the bank here.
      await submitPettyCashReimbursement(d.id)
      setSubmitted(true)
      setTimeout(() => router.replace(`/petty-cash/reimbursements/${d.id}`), 1000)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to submit reimbursement')
    } finally {
      setBusy(false)
    }
  }

  const selectedLoc = locations.find((l) => l.id === locationId) || null

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Top Up the Petty Cash Tin"
        breadcrumbs={[
          { label: 'Petty Cash', href: '/petty-cash' },
          { label: 'Reimbursements', href: '/petty-cash/reimbursements' },
          { label: 'New' },
        ]}
        actions={
          <Link href="/petty-cash/reimbursements">
            <Button
              variant="outline" size="sm"
              leftIcon={<ArrowLeft className="w-3.5 h-3.5" />}
            >
              Back
            </Button>
          </Link>
        }
      />

      <div className="flex-1 p-6 max-w-4xl space-y-4">
        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 text-[#DC2626] flex-shrink-0 mt-0.5" />
            <p className="text-[#DC2626] text-sm whitespace-pre-wrap">{error}</p>
          </div>
        )}

        {/* Step 1: select scope */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Coins className="w-4 h-4 text-[#F07F00]" />
              Reimbursement scope
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="md:col-span-2">
                <label className="block text-xs font-medium text-[#374151] mb-1.5">Location</label>
                <select
                  value={locationId}
                  onChange={(e) => { setLocationId(e.target.value); setPreview(null) }}
                  disabled={loading}
                  className="w-full h-10 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm"
                >
                  <option value="">Select location…</option>
                  {locations.map((l) => (
                    <option key={l.id} value={l.id}>{l.name}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs font-medium text-[#374151] mb-1.5">Period start</label>
                <input
                  type="date" value={periodStart}
                  onChange={(e) => { setPeriodStart(e.target.value); setPreview(null) }}
                  className="w-full h-10 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-[#374151] mb-1.5">Period end</label>
                <input
                  type="date" value={periodEnd}
                  onChange={(e) => { setPeriodEnd(e.target.value); setPreview(null) }}
                  className="w-full h-10 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm"
                />
              </div>
              <div className="md:col-span-2">
                <label className="block text-xs font-medium text-[#374151] mb-1.5">Notes (optional)</label>
                <input
                  type="text" value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  placeholder="e.g. April 2026 imprest top-up"
                  className="w-full h-10 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm"
                />
              </div>
            </div>

            <div className="mt-4 flex gap-3">
              <Button
                onClick={runPreview} disabled={busy || !locationId}
                variant="outline"
                leftIcon={<RefreshCw className="w-4 h-4" />}
              >
                Preview reimbursement
              </Button>
            </div>
          </CardContent>
        </Card>

        {/* Step 2: preview */}
        {preview && (
          <Card>
            <CardHeader>
              <CardTitle>Preview — {preview.location_name}</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
                <div>
                  <p className="text-[10px] uppercase tracking-wider text-[#6B7280]">Vouchers</p>
                  <p className="text-2xl font-bold text-[#0B0B3B]">{preview.voucher_count}</p>
                </div>
                <div>
                  <p className="text-[10px] uppercase tracking-wider text-[#6B7280]">Reimbursement total</p>
                  <p className="text-2xl font-bold font-mono tabular-nums text-[#0B0B3B]">
                    BWP {fmtMoney(preview.total_amount)}
                  </p>
                </div>
                <div>
                  <p className="text-[10px] uppercase tracking-wider text-[#6B7280]">Cash on hand now</p>
                  <p className="text-2xl font-bold font-mono tabular-nums text-[#374151]">
                    BWP {fmtMoney(preview.cash_on_hand_before)}
                  </p>
                </div>
                <div>
                  <p className="text-[10px] uppercase tracking-wider text-[#6B7280]">After top-up</p>
                  <p className="text-2xl font-bold font-mono tabular-nums text-[#047857]">
                    BWP {fmtMoney(preview.cash_on_hand_after)}
                  </p>
                </div>
              </div>

              {preview.voucher_count === 0 ? (
                <p className="text-sm text-[#9CA3AF] italic">
                  No reimbursable vouchers in this window. Either none have
                  been posted, or they've already been swept.
                </p>
              ) : (
                <div className="overflow-x-auto border border-[#E5E7EB] rounded">
                  <table className="min-w-full text-sm">
                    <thead className="bg-[#F9FAFB]">
                      <tr>
                        <th className="text-left px-3 py-2 font-semibold text-[#374151]">Voucher</th>
                        <th className="text-left px-3 py-2 font-semibold text-[#374151]">Date</th>
                        <th className="text-left px-3 py-2 font-semibold text-[#374151]">Payee</th>
                        <th className="text-left px-3 py-2 font-semibold text-[#374151]">Account</th>
                        <th className="text-right px-3 py-2 font-semibold text-[#374151]">Amount</th>
                      </tr>
                    </thead>
                    <tbody>
                      {preview.vouchers.map((v) => (
                        <tr key={v.id} className="border-t border-[#F3F4F6]">
                          <td className="px-3 py-2 font-mono text-xs text-[#0B0B3B]">{v.voucher_number}</td>
                          <td className="px-3 py-2 text-[#374151]">{v.voucher_date}</td>
                          <td className="px-3 py-2 text-[#374151]">{v.payee}</td>
                          <td className="px-3 py-2 text-xs text-[#6B7280]">
                            {v.expense_account_code} · {v.expense_account_name}
                          </td>
                          <td className="px-3 py-2 text-right font-mono tabular-nums">
                            {fmtMoney(v.amount)}
                          </td>
                        </tr>
                      ))}
                      <tr className="bg-[#F9FAFB] border-t border-[#E5E7EB]">
                        <td className="px-3 py-2 font-semibold text-[#374151]" colSpan={4}>
                          Total
                        </td>
                        <td className="px-3 py-2 text-right font-mono tabular-nums font-bold text-[#0B0B3B]">
                          {fmtMoney(preview.total_amount)}
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              )}

              {preview.voucher_count > 0 && (
                <div className="mt-4 p-3 rounded bg-[#EFF6FF] border border-[#BFDBFE] text-sm text-[#1E40AF]">
                  <p>
                    This goes to the <strong>Finance Manager</strong> for review, then to the
                    {' '}<strong>CFO</strong> to approve the bank payment. Nothing posts to the
                    bank yet. When approved it will post: DR{' '}
                    <span className="font-mono">{selectedLoc?.petty_cash_account_code}</span> · {selectedLoc?.petty_cash_account_name}
                    {' '}P {fmtMoney(preview.total_amount)}, CR{' '}
                    <span className="font-mono">{selectedLoc?.reimbursing_bank_account_code}</span> · {selectedLoc?.reimbursing_bank_account_name}
                    {' '}P {fmtMoney(preview.total_amount)}.
                  </p>
                </div>
              )}

              {preview.voucher_count > 0 && (
                <div className="mt-4 flex justify-end">
                  <Button
                    onClick={createAndSubmit} disabled={busy || submitted}
                    leftIcon={<Send className="w-4 h-4" />}
                  >
                    {busy ? 'Sending…' : 'Reimburse — send to FM review'}
                  </Button>
                </div>
              )}
            </CardContent>
          </Card>
        )}

        {submitted && (
          <div className="bg-[#ECFDF5] border border-[#A7F3D0] rounded-lg p-3 flex items-center gap-2">
            <CheckCircle2 className="w-5 h-5 text-[#059669]" />
            <p className="text-[#047857] text-sm">
              Sent to the Finance Manager for review. Taking you to the reimbursement…
            </p>
          </div>
        )}
      </div>
    </div>
  )
}
