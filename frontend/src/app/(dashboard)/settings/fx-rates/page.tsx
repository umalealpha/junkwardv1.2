'use client'

/**
 * /settings/fx-rates — Bank of Botswana mid-rate maintenance (FX-001).
 *
 * CFO/Oprah directive 2026-05-28. Sole exchange-rate source is the BoB
 * published mid-rate (www.bankofbotswana.bw). Rates are entered manually
 * here (Phase 2: scraper). A Finance Manager must approve a rate before
 * any FX revaluation can use it; the revaluation run blocks if any
 * currency on a foreign-balance account lacks an approved rate at the
 * period-end date.
 */

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import {
  getExchangeRates, createExchangeRate, approveExchangeRate, clearExchangeRateApproval,
  getMe, getToken,
} from '@/lib/api'
import type { ExchangeRate, UserProfile } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { LoadingTable } from '@/components/ui/loading'
import { cn, formatDate, localYmd } from '@/lib/utils'
import { Plus, CheckCircle2, AlertCircle, ShieldCheck, RotateCcw } from 'lucide-react'

const CURRENCIES = ['USD', 'ZAR', 'EUR', 'GBP', 'INR', 'ZMW', 'NAD']

function canApprove(me: UserProfile | null): boolean {
  if (!me) return false
  const t = (me.title || '').toLowerCase()
  return !!(me.is_administrator || t === 'cfo' || t === 'finance_manager')
}

export default function FxRatesPage() {
  const router = useRouter()
  const [me, setMe] = useState<UserProfile | null>(null)
  const [rows, setRows] = useState<ExchangeRate[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [acting, setActing] = useState(false)

  // new-rate form
  const [showForm, setShowForm] = useState(false)
  const [fCurrency, setFCurrency] = useState('USD')
  const [fRate, setFRate] = useState('')
  const [fDate, setFDate] = useState(localYmd(new Date()))
  const [fNotes, setFNotes] = useState('')

  const isApprover = canApprove(me)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const [ratesRes, meRes] = await Promise.all([
        getExchangeRates({ page_size: '500' }),
        me ? Promise.resolve(me) : getMe().catch(() => null),
      ])
      setRows(ratesRes.results || [])
      if (!me && meRes) setMe(meRes)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load FX rates')
    } finally { setLoading(false) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load])

  const handleCreate = async () => {
    if (!fCurrency || !fRate || !fDate) { setError('All fields required.'); return }
    setActing(true); setError(null)
    try {
      await createExchangeRate({
        from_currency: fCurrency, to_currency: 'BWP', rate: fRate,
        effective_date: fDate, source: 'bank_of_botswana', notes: fNotes || undefined,
      })
      setSuccess(`Loaded ${fCurrency}/BWP @ ${fRate} for ${fDate} (pending FM approval).`)
      setTimeout(() => setSuccess(null), 5000)
      setShowForm(false); setFRate(''); setFNotes('')
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create rate')
    } finally { setActing(false) }
  }

  const handleApprove = async (id: string) => {
    setActing(true); setError(null)
    try {
      await approveExchangeRate(id)
      setSuccess('Rate approved. Now available to FX Revaluation.')
      setTimeout(() => setSuccess(null), 4500)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to approve rate')
    } finally { setActing(false) }
  }

  const handleClear = async (id: string) => {
    setActing(true); setError(null)
    try {
      await clearExchangeRateApproval(id)
      setSuccess('Approval cleared. Re-approval required.')
      setTimeout(() => setSuccess(null), 4500)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to clear approval')
    } finally { setActing(false) }
  }

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="FX Rates"
        breadcrumbs={[{ label: 'Settings' }, { label: 'FX Rates' }]}
        actions={
          <Button variant="accent" size="sm" leftIcon={<Plus className="w-3.5 h-3.5" />}
            onClick={() => setShowForm(s => !s)} disabled={acting}>
            Load BoB rate
          </Button>
        }
      />
      <div className="flex-1 p-6 space-y-4">

        <div className="flex items-start gap-2 text-sm text-[#6B7280] bg-[#FFFBEB] border border-[#FDE68A] rounded-lg p-3">
          <ShieldCheck className="w-4 h-4 text-[#D97706] mt-0.5" />
          <div>
            <p className="text-[#92400E] font-medium">Sole FX source: Bank of Botswana mid-rates</p>
            <p className="text-xs mt-1">
              Rates come from <a href="https://www.bankofbotswana.bw" target="_blank" rel="noopener noreferrer" className="underline">www.bankofbotswana.bw</a>.
              A Finance Manager must approve every loaded rate before any FX revaluation can use it.
              Editing an approved rate clears the approval — re-approve before re-use.
              (Phase 2: scraper to pull rates directly; no third-party paid feed.)
            </p>
          </div>
        </div>

        {showForm && (
          <Card>
            <CardHeader><CardTitle>Load a BoB mid-rate</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <div>
                  <label className="block text-xs text-[#6B7280] uppercase tracking-wider mb-1">Currency</label>
                  <select value={fCurrency} onChange={e => setFCurrency(e.target.value)}
                    className="w-full h-9 bg-white border border-[#D1D5DB] rounded-md px-2 text-sm">
                    {CURRENCIES.map(c => <option key={c} value={c}>{c}</option>)}
                  </select>
                </div>
                <div>
                  <label className="block text-xs text-[#6B7280] uppercase tracking-wider mb-1">Rate ({fCurrency} → BWP)</label>
                  <input type="number" step="0.00000001" value={fRate} onChange={e => setFRate(e.target.value)}
                    placeholder="13.45000000"
                    className="w-full h-9 bg-white border border-[#D1D5DB] rounded-md px-2 text-sm font-mono" />
                </div>
                <div>
                  <label className="block text-xs text-[#6B7280] uppercase tracking-wider mb-1">Effective date</label>
                  <input type="date" value={fDate} onChange={e => setFDate(e.target.value)}
                    className="w-full h-9 bg-white border border-[#D1D5DB] rounded-md px-2 text-sm" />
                </div>
                <div>
                  <label className="block text-xs text-[#6B7280] uppercase tracking-wider mb-1">Notes (optional)</label>
                  <input type="text" value={fNotes} onChange={e => setFNotes(e.target.value)}
                    placeholder="e.g. BoB page snapshot 27/05/2026"
                    className="w-full h-9 bg-white border border-[#D1D5DB] rounded-md px-2 text-sm" />
                </div>
              </div>
              <div className="flex items-center gap-2">
                <Button variant="accent" size="sm" onClick={handleCreate} disabled={acting}>
                  Load (pending approval)
                </Button>
                <Button variant="outline" size="sm" onClick={() => setShowForm(false)} disabled={acting}>
                  Cancel
                </Button>
              </div>
            </CardContent>
          </Card>
        )}

        {success && (
          <div className="bg-[#ECFDF5] border border-[#A7F3D0] rounded-lg p-3 flex items-center gap-2">
            <CheckCircle2 className="w-4 h-4 text-[#059669]" />
            <p className="text-[#059669] text-sm">{success}</p>
          </div>
        )}
        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-[#DC2626]" />
            <p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}

        <Card>
          <CardContent className="p-0">
            {loading ? <LoadingTable rows={8} cols={8} /> : rows.length === 0 ? (
              <div className="px-4 py-16 text-center text-[#6B7280] text-sm">No rates loaded yet.</div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm border-collapse">
                  <thead className="bg-[#F9FAFB] border-b border-[#E5E7EB]">
                    <tr>
                      <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Pair</th>
                      <th className="px-3 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider">Rate</th>
                      <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Effective</th>
                      <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Source</th>
                      <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Loaded</th>
                      <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Approved</th>
                      <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Notes</th>
                      <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Action</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#E5E7EB] bg-white">
                    {rows.map(r => {
                      const approved = !!(r.approved_by && r.approved_at)
                      return (
                        <tr key={r.id} className={cn('hover:bg-[#FAFAFA]', !approved && 'bg-[#FFFBEB]')}>
                          <td className="px-3 py-3 font-mono text-xs text-[#111827] font-semibold">{r.from_currency} / {r.to_currency}</td>
                          <td className="px-3 py-3 text-right font-mono-nums text-[#111827]">{r.rate}</td>
                          <td className="px-3 py-3 text-[#374151] whitespace-nowrap">{r.effective_date}</td>
                          <td className="px-3 py-3 text-xs">
                            <span className="inline-flex items-center px-2 py-0.5 rounded-md font-medium" style={{ background: r.source === 'bank_of_botswana' ? '#ECFDF5' : '#F3F4F6', color: r.source === 'bank_of_botswana' ? '#059669' : '#6B7280' }}>
                              {r.source === 'bank_of_botswana' ? 'BoB' : 'Manual'}
                            </span>
                          </td>
                          <td className="px-3 py-3 text-xs text-[#6B7280]">
                            {r.loaded_by_name || '—'}<br/>
                            <span className="text-[#9CA3AF]">{r.created_at ? formatDate(r.created_at) : ''}</span>
                          </td>
                          <td className="px-3 py-3 text-xs">
                            {approved ? (
                              <span className="text-[#059669]">
                                ✓ {r.approved_by_name || ''}<br/>
                                <span className="text-[#9CA3AF]">{r.approved_at ? formatDate(r.approved_at) : ''}</span>
                              </span>
                            ) : (
                              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-medium bg-[#FEF3C7] text-[#92400E]">
                                Pending approval
                              </span>
                            )}
                          </td>
                          <td className="px-3 py-3 text-xs text-[#6B7280] max-w-[180px] truncate">{r.notes || ''}</td>
                          <td className="px-3 py-3">
                            {approved ? (
                              isApprover && (
                                <Button variant="outline" size="sm" leftIcon={<RotateCcw className="w-3.5 h-3.5" />}
                                  onClick={() => handleClear(r.id)} disabled={acting}>
                                  Clear
                                </Button>
                              )
                            ) : (
                              isApprover ? (
                                <Button variant="accent" size="sm" leftIcon={<ShieldCheck className="w-3.5 h-3.5" />}
                                  onClick={() => handleApprove(r.id)} disabled={acting || r.loaded_by === me?.id}>
                                  {r.loaded_by === me?.id ? 'SoD: loader' : 'Approve'}
                                </Button>
                              ) : <span className="text-[#9CA3AF] text-xs">awaiting FM</span>
                            )}
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
