'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  getFXRevaluations, runFXRevaluation, getFiscalPeriods, getExchangeRates, getToken,
} from '@/lib/api'
import type { FXRevaluationListItem, FiscalPeriod, ExchangeRate } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Globe, Play, AlertCircle, CheckCircle2, ShieldCheck, ExternalLink } from 'lucide-react'
import Link from 'next/link'

const fmt = (v: string | number): string => {
  const n = typeof v === 'string' ? parseFloat(v) : v
  if (Number.isNaN(n)) return '—'
  return new Intl.NumberFormat('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n)
}

export default function FXRevaluationPage() {
  const router = useRouter()
  const [periods, setPeriods] = useState<FiscalPeriod[]>([])
  const [periodId, setPeriodId] = useState('')
  const [revs, setRevs] = useState<FXRevaluationListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [info, setInfo]   = useState<string | null>(null)
  const [rates, setRates] = useState<ExchangeRate[]>([])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    Promise.all([getFiscalPeriods(), getFXRevaluations(), getExchangeRates({ approved_only: true, page_size: '500' })])
      .then(([p, r, ex]) => {
        setPeriods((p?.results || []).filter((x) => x.status === 'open'))
        setRevs(r?.results || [])
        setRates(ex?.results || [])
      })
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false))
  }, [router])

  // FX-001: latest APPROVED BoB rate per currency at or before the picked
  // period's end_date. Drives the rate display + the user-facing block-list.
  const selectedPeriod = periods.find(p => p.id === periodId) || null
  const ratesAsOf: Record<string, ExchangeRate> = {}
  if (selectedPeriod) {
    const asOf = selectedPeriod.end_date
    for (const r of rates) {
      if (r.to_currency !== 'BWP') continue
      if (r.effective_date > asOf) continue
      const cur = ratesAsOf[r.from_currency]
      if (!cur || r.effective_date > cur.effective_date) ratesAsOf[r.from_currency] = r
    }
  }

  const onRun = async (dryRun: boolean) => {
    if (!periodId) { setError('Pick a period.'); return }
    setBusy(true); setError(null); setInfo(null)
    try {
      const r = await runFXRevaluation(periodId, { dry_run: dryRun })
      setInfo(`${dryRun ? 'Dry run' : 'Posted'} — net BWP ${fmt(r.net_bwp)} (${r.lines.length} accounts revalued)`)
      const list = await getFXRevaluations()
      setRevs(list?.results || [])
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Run failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-screen bg-[#F8F9FA]">
      <TopBar title="FX Revaluation" subtitle="Month-end foreign-currency revaluation" />

      <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-6 space-y-4">
        {error && (
          <Card className="border-red-200 bg-red-50">
            <CardContent className="p-3 flex items-start gap-2">
              <AlertCircle className="w-4 h-4 text-red-700 mt-0.5" />
              <span className="text-sm text-red-700">{error}</span>
            </CardContent>
          </Card>
        )}
        {info && (
          <Card className="border-emerald-200 bg-emerald-50">
            <CardContent className="p-3 flex items-start gap-2">
              <CheckCircle2 className="w-4 h-4 text-emerald-700 mt-0.5" />
              <span className="text-sm text-emerald-700">{info}</span>
            </CardContent>
          </Card>
        )}

        <Card>
          <CardContent className="p-6 space-y-3">
            <h3 className="font-medium flex items-center gap-2"><Globe className="w-4 h-4" /> Run revaluation</h3>
            <p className="text-sm text-gray-600">
              For each foreign-currency balance-sheet account, the engine restates the BWP
              book value at the period-end exchange rate and posts the difference as an
              unrealised gain or loss to account 6950. Dry-run first to preview the lines.
            </p>
            <div className="flex items-end gap-3">
              <div className="flex-1">
                <label className="text-xs text-gray-600 block mb-1">Open period</label>
                <select value={periodId} onChange={(e) => setPeriodId(e.target.value)}
                  className="w-full p-2 border border-gray-300 rounded text-sm">
                  <option value="">— Pick period —</option>
                  {periods.map((p) => (
                    <option key={p.id} value={p.id}>{p.period_name} ({p.start_date} → {p.end_date})</option>
                  ))}
                </select>
              </div>
              <Button variant="outline" onClick={() => onRun(true)} disabled={!periodId || busy}>
                <Play className="w-4 h-4 mr-1" /> Dry run
              </Button>
              <Button onClick={() => onRun(false)} disabled={!periodId || busy} className="bg-[#0D1B2A] hover:bg-[#1a2940] text-white">
                <Play className="w-4 h-4 mr-1" /> Run & post
              </Button>
            </div>

            {/* FX-001 — show the approved BoB mid-rate per currency that
                this revaluation will use, before the user posts anything. */}
            {selectedPeriod && (
              <div className="mt-4 border-t pt-3">
                <div className="flex items-center gap-2 mb-2">
                  <ShieldCheck className="w-4 h-4 text-[#CC6C00]" />
                  <span className="text-sm font-medium text-[#0D1B2A]">
                    Approved BoB mid-rates as of {selectedPeriod.end_date}
                  </span>
                  <Link href="/settings/fx-rates" className="ml-auto text-xs text-[#CC6C00] hover:underline inline-flex items-center gap-1">
                    Manage rates <ExternalLink className="w-3 h-3" />
                  </Link>
                </div>
                {Object.keys(ratesAsOf).length === 0 ? (
                  <p className="text-xs text-[#92400E] bg-[#FFFBEB] border border-[#FDE68A] rounded-md p-2">
                    No approved BoB rates loaded for this period yet. Revaluation will be
                    blocked for any non-BWP currency on a foreign-balance account until a
                    rate is loaded + approved at /settings/fx-rates.
                  </p>
                ) : (
                  <table className="w-full text-xs">
                    <thead className="text-left text-[#6B7280] uppercase tracking-wider">
                      <tr>
                        <th className="py-1 pr-3">Currency</th>
                        <th className="py-1 pr-3 text-right">Rate → BWP</th>
                        <th className="py-1 pr-3">Effective</th>
                        <th className="py-1 pr-3">Source</th>
                        <th className="py-1 pr-3">Approved by</th>
                      </tr>
                    </thead>
                    <tbody className="text-[#374151]">
                      {Object.entries(ratesAsOf).sort(([a],[b]) => a.localeCompare(b)).map(([cur, r]) => (
                        <tr key={cur} className="border-t border-[#F3F4F6]">
                          <td className="py-1 pr-3 font-mono font-semibold">{cur}/BWP</td>
                          <td className="py-1 pr-3 text-right font-mono-nums">{r.rate}</td>
                          <td className="py-1 pr-3">{r.effective_date}</td>
                          <td className="py-1 pr-3">{r.source === 'bank_of_botswana' ? 'BoB' : 'Manual'}</td>
                          <td className="py-1 pr-3">{r.approved_by_name || '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-0">
            {loading ? (
              <div className="p-6 text-gray-500 text-sm">Loading...</div>
            ) : revs.length === 0 ? (
              <div className="p-12 text-center text-gray-500 text-sm">
                <Globe className="w-10 h-10 mx-auto mb-2 opacity-40" />
                No FX revaluations yet.
              </div>
            ) : (
              <table className="w-full text-sm">
                <thead className="bg-gray-50 border-b text-xs uppercase text-gray-600">
                  <tr className="text-left">
                    <th className="px-4 py-3">Period</th>
                    <th className="px-4 py-3">Run date</th>
                    <th className="px-4 py-3">Status</th>
                    <th className="px-4 py-3 text-right">Gain (BWP)</th>
                    <th className="px-4 py-3 text-right">Loss (BWP)</th>
                    <th className="px-4 py-3 text-right">Net (BWP)</th>
                  </tr>
                </thead>
                <tbody>
                  {revs.map((r) => (
                    <tr key={r.id} className="border-b border-gray-100">
                      <td className="px-4 py-2.5 font-mono">{r.period_name}</td>
                      <td className="px-4 py-2.5 text-gray-600">{r.run_date}</td>
                      <td className="px-4 py-2.5">{r.status_display}</td>
                      <td className="px-4 py-2.5 text-right font-mono text-emerald-700">{fmt(r.total_gain_bwp)}</td>
                      <td className="px-4 py-2.5 text-right font-mono text-red-700">{fmt(r.total_loss_bwp)}</td>
                      <td className="px-4 py-2.5 text-right font-mono font-medium">{fmt(r.net_bwp)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
