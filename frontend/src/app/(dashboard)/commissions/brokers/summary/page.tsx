'use client'

/**
 * /commissions/brokers/summary — per-broker commission for one month.
 *
 * READ ONLY AND MARKED PREVIEW. Nothing on this screen posts, pays or writes.
 * The maths has not been reconciled to a Finance workbook and no figure here is
 * approved for payment, so the banner says so and never goes away on its own.
 *
 * The screen is built around one idea: an unknown must never look like a zero.
 * Three things can make a figure unknowable — no rates configured, no
 * motor/non-motor split set for a broker, or the RealPay outcome feed not
 * reporting for the month — and in each case the page shows the reason in words
 * where the number would have been. A dash with a reason is honest; P0.00 is a
 * confident lie.
 */

import { useCallback, useEffect, useState } from 'react'
import {
  getBrokerCommissionSummary, closeBrokerMonth, setBrokerCompliance,
  type BrokerCommissionSummary, type BrokerCommissionRow,
} from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  AlertTriangle, RefreshCw, Info, TrendingUp, TrendingDown, Minus, Lock, CheckCircle2,
} from 'lucide-react'

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'
const SERIF = '"Book Antiqua", "Palatino Linotype", Palatino, Georgia, serif'

function money(v: number | null | undefined): string {
  if (v == null || !isFinite(v)) return '—'
  return v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

/** YYYY-MM for the last 18 months, newest first. Built in Gaborone time. */
function recentPeriods(): string[] {
  const out: string[] = []
  const now = new Date()
  let y = now.getFullYear()
  let m = now.getMonth() + 1
  for (let i = 0; i < 18; i++) {
    out.push(`${y}-${String(m).padStart(2, '0')}`)
    m -= 1
    if (m === 0) { m = 12; y -= 1 }
  }
  return out
}

function Growth({ pct }: { pct: number | null | undefined }) {
  // Undefined growth is a dash with a tooltip, not 0% — a broker with nothing
  // last month has no growth to report, and 0% would read as "flat".
  if (pct == null) {
    return (
      <span className="inline-flex items-center gap-1 text-slate-400" title="No figure last month to compare against">
        <Minus className="h-3.5 w-3.5" />—
      </span>
    )
  }
  const up = pct >= 0
  const Icon = up ? TrendingUp : TrendingDown
  return (
    <span className={`inline-flex items-center gap-1 tabular-nums ${up ? 'text-emerald-700' : 'text-red-700'}`}>
      <Icon className="h-3.5 w-3.5" />{Math.abs(pct).toFixed(2)}%
    </span>
  )
}

export default function BrokerCommissionSummaryPage() {
  const periods = recentPeriods()
  const [period, setPeriod] = useState(periods[0])
  const [data, setData] = useState<BrokerCommissionSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async (p: string) => {
    setLoading(true); setError(null)
    try {
      setData(await getBrokerCommissionSummary(p))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load the summary')
      setData(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load(period) }, [period, load])

  // C5 — Finance closes a finished month. The server refuses anyone outside
  // the Full Access role, an unfinished month, and a month whose RealPay
  // results have not come back; its reason is shown as-is.
  const [closing, setClosing] = useState(false)
  const [closeNote, setCloseNote] = useState<string | null>(null)
  async function closeMonth() {
    if (!window.confirm(`Close ${period}? This records every policy's status and `
                        + `captures each broker's payable as next month's Previous Payable.`)) return
    setClosing(true); setCloseNote(null); setError(null)
    try {
      const r = await closeBrokerMonth(period)
      setCloseNote(r.already_closed
        ? `${period} was already closed — nothing changed.`
        : `${period} closed: ${r.brokers_closed} broker(s), ${r.statuses_recorded} status(es) recorded`
          + (r.late_resolved ? `, ${r.late_resolved} late result(s) resolved` : '')
          + (r.payable_changes ? `, ${r.payable_changes} earlier payable(s) updated` : '') + '.')
      await load(period)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'The month could not be closed.')
    } finally { setClosing(false) }
  }

  const rows: BrokerCommissionRow[] = data?.rows ?? []
  const withMoney = rows.filter(r => r.collected_gross > 0 || r.commission_available)

  return (
    <div className="min-h-screen bg-slate-50">
      <TopBar />
      <div className="mx-auto max-w-[1400px] px-6 py-8">

        {/* Heading */}
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-3xl tracking-tight" style={{ fontFamily: SERIF, color: NAVY }}>
              Broker Commission
            </h1>
            <p className="mt-1 text-sm text-slate-600">
              Commission worked out on premium actually collected, per broker, for one month.
            </p>
          </div>
          <div className="flex items-center gap-3">
            <label htmlFor="period-select" className="sr-only">Month</label>
            <select
              id="period-select"
              aria-label="Month to show commission for"
              value={period}
              onChange={e => setPeriod(e.target.value)}
              className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
              style={{ color: NAVY }}
            >
              {periods.map(p => <option key={p} value={p}>{p}</option>)}
            </select>
            <Button variant="outline" onClick={() => void load(period)} disabled={loading}>
              <RefreshCw className={`mr-2 h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
              Refresh
            </Button>
            {data?.month_closed ? (
              <span className="inline-flex items-center gap-1.5 rounded-lg border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
                <CheckCircle2 className="h-4 w-4" /> Month closed
              </span>
            ) : (
              <Button variant="primary" onClick={() => void closeMonth()} loading={closing}
                      disabled={closing || loading || !data}>
                Close month
              </Button>
            )}
          </div>
        </div>

        {/* The preview banner. Deliberately permanent: it comes off when
            Finance has reconciled the maths, not when the page feels ready. */}
        <div className="mt-5 flex items-start gap-3 rounded-xl border-2 px-4 py-3"
             style={{ borderColor: ORANGE, background: '#FFF8ED' }}>
          <Lock className="mt-0.5 h-5 w-5 shrink-0" style={{ color: ORANGE }} />
          <div className="text-sm" style={{ color: NAVY }}>
            <strong>Preview — not signed off.</strong>{' '}
            {data?.preview_note ??
              'These figures have not been checked against a Finance workbook and none of them is approved for payment.'}
          </div>
        </div>

        {error && (
          <div className="mt-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800" role="alert">
            {error}
          </div>
        )}
        {closeNote && (
          <div className="mt-4 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
            {closeNote}
          </div>
        )}

        {/* The incomplete-figures warning. This is the most important control on
            the page: when RealPay has not returned outcomes, every collected
            figure below is a floor, not a total. */}
        {data && !data.figures_complete && (
          <div className="mt-4 flex items-start gap-3 rounded-xl border border-amber-300 bg-amber-50 px-4 py-3">
            <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-700" />
            <div className="text-sm text-amber-900">
              <strong>These figures are incomplete.</strong>{' '}
              {data.feed.reason || 'The RealPay collection results have not come back for this month.'}
              {' '}What is shown is the minimum collected, not the month&apos;s total, so every
              commission below can only go up.
            </div>
          </div>
        )}

        {data && !data.rates_configured && (
          <div className="mt-4 flex items-start gap-3 rounded-xl border border-slate-300 bg-white px-4 py-3">
            <Info className="mt-0.5 h-5 w-5 shrink-0 text-slate-500" />
            <div className="text-sm text-slate-700">
              <strong>No commission rates are set for this month.</strong> Premium collected is shown,
              but no commission can be worked out until Finance sets the rates.
            </div>
          </div>
        )}

        {/* Totals */}
        {data && (
          <div className="mt-6 grid grid-cols-2 gap-4 lg:grid-cols-5">
            {[
              ['Premium collected', data.totals.collected_gross],
              ['Commission excl VAT', data.totals.commission_excl_vat],
              ['Withholding tax', data.totals.wht],
              ['VAT', data.totals.vat],
              ['Payable this month', data.totals.current_payable],
            ].map(([label, value], i) => (
              <Card key={label as string} className={i === 4 ? 'border-2' : ''}
                    style={i === 4 ? { borderColor: ORANGE } : undefined}>
                <CardContent className="p-4">
                  <div className="text-xs uppercase tracking-wide text-slate-500">{label as string}</div>
                  <div className="mt-1 text-xl tabular-nums" style={{ fontFamily: SERIF, color: NAVY }}>
                    {money(value as number)}
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        )}

        {data?.rates && (
          <p className="mt-3 text-xs text-slate-500">
            Rates in force from {data.rates.effective_from}: motor {data.rates.motor_pct}%,
            non-motor {data.rates.non_motor_pct}%, VAT {data.rates.vat_pct}%,
            admin {data.rates.admin_pct}%, withholding {data.rates.wht_pct}%.
            Commission is worked out after VAT and the admin fee are taken out of what was collected.
          </p>
        )}

        {/* The table */}
        <Card className="mt-6">
          <CardContent className="p-0">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                    <th className="px-4 py-3 font-medium">Broker</th>
                    <th className="px-4 py-3 text-right font-medium">Collected</th>
                    <th className="px-4 py-3 text-right font-medium">Commission excl VAT</th>
                    <th className="px-4 py-3 text-right font-medium">WHT</th>
                    <th className="px-4 py-3 text-right font-medium">VAT</th>
                    <th className="px-4 py-3 text-right font-medium">Payable now</th>
                    <th className="px-4 py-3 text-right font-medium">Previous</th>
                    <th className="px-4 py-3 text-right font-medium">Growth</th>
                    <th className="px-4 py-3 font-medium">Compliance</th>
                  </tr>
                </thead>
                <tbody>
                  {loading && (
                    <tr><td colSpan={9} className="px-4 py-10 text-center text-slate-500">Loading…</td></tr>
                  )}
                  {!loading && withMoney.length === 0 && (
                    <tr><td colSpan={9} className="px-4 py-10 text-center text-slate-500">
                      No broker collected any Domestic or Commercial premium in {period}.
                    </td></tr>
                  )}
                  {!loading && withMoney.map(r => (
                    <tr key={r.broker_id} className="border-b border-slate-100 last:border-0 hover:bg-slate-50">
                      <td className="px-4 py-3">
                        <div style={{ color: NAVY }}>{r.broker}</div>
                        <div className="text-xs text-slate-500">
                          {r.collected_count} collection{r.collected_count === 1 ? '' : 's'}
                          {r.withholding_tax ? '' : ' · withholding exempt'}
                        </div>
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums" style={{ color: NAVY }}>
                        {money(r.collected_gross)}
                      </td>
                      {r.commission_available ? (
                        <>
                          <td className="px-4 py-3 text-right tabular-nums">{money(r.commission_excl_vat)}</td>
                          <td className="px-4 py-3 text-right tabular-nums text-slate-600">{money(r.wht)}</td>
                          <td className="px-4 py-3 text-right tabular-nums text-slate-600">{money(r.vat)}</td>
                          <td className="px-4 py-3 text-right tabular-nums font-medium" style={{ color: NAVY }}>
                            {money(r.current_payable)}
                          </td>
                          <td className="px-4 py-3 text-right tabular-nums text-slate-500"
                              title={r.previous_payable_source === 'closed'
                                ? 'As captured when last month was closed'
                                : 'Worked out now — last month has not been closed'}>
                            {money(r.previous_payable)}
                            {r.previous_payable_source === 'closed' && <Lock className="ml-1 inline h-3 w-3" />}
                          </td>
                          <td className="px-4 py-3 text-right"><Growth pct={r.growth_pct} /></td>
                        </>
                      ) : (
                        // The refusal, said in words across the money columns —
                        // never a row of zeros.
                        <td colSpan={6} className="px-4 py-3 text-xs text-amber-800">
                          {r.blocked_reason}
                        </td>
                      )}
                      <td className="px-4 py-3">
                        <ComplianceCell row={r} period={period} onError={setError} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>

        {/* Money collected under an agency no broker claims: somebody may be
            owed commission and the register does not know who. */}
        {data && data.unmapped_agencies.length > 0 && (
          <Card className="mt-6">
            <CardContent className="p-4">
              <div className="flex items-start gap-3">
                <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-600" />
                <div className="text-sm">
                  <div style={{ color: NAVY }}>
                    <strong>Premium collected under names no broker claims</strong>
                  </div>
                  <p className="mt-1 text-slate-600">
                    Somebody may be owed commission on this and the register cannot tell who.
                    Add the name to the right broker on the register screen.
                  </p>
                  <ul className="mt-2 list-disc pl-5 text-slate-700">
                    {data.unmapped_agencies.map(a => <li key={a}>{a}</li>)}
                  </ul>
                </div>
              </div>
            </CardContent>
          </Card>
        )}

        {data && data.blocked.length > 0 && (
          <Card className="mt-6">
            <CardContent className="p-4">
              <div className="text-sm" style={{ color: NAVY }}>
                <strong>{data.blocked.length} broker{data.blocked.length === 1 ? '' : 's'} cannot be worked out yet</strong>
              </div>
              <ul className="mt-2 space-y-1 text-sm text-slate-600">
                {data.blocked.map(b => (
                  <li key={b.broker}><span style={{ color: NAVY }}>{b.broker}</span> — {b.reason}</li>
                ))}
              </ul>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  )
}

/** C7 — the manual Compliance field. Only the Full Access role can save it;
 *  the server enforces that and its refusal is shown as the page error. */
function ComplianceCell({ row, period, onError }:
  { row: BrokerCommissionRow; period: string; onError: (m: string | null) => void }) {
  const [value, setValue] = useState(row.compliance || '')
  const [saved, setSaved] = useState(row.compliance || '')
  const [busy, setBusy] = useState(false)
  useEffect(() => { setValue(row.compliance || ''); setSaved(row.compliance || '') }, [row.compliance, period])

  async function save() {
    setBusy(true); onError(null)
    try {
      const r = await setBrokerCompliance(row.broker_id, period, value)
      setSaved(r.compliance); setValue(r.compliance)
    } catch (e) {
      onError(e instanceof Error ? e.message : 'Compliance could not be saved.')
    } finally { setBusy(false) }
  }

  return (
    <div className="flex items-center gap-1.5">
      <input value={value} maxLength={200} onChange={e => setValue(e.target.value)}
             aria-label={`Compliance for ${row.broker}`}
             className="w-40 rounded border border-slate-300 px-2 py-1 text-xs" />
      {value !== saved && (
        <Button size="sm" variant="outline" onClick={() => void save()} loading={busy} disabled={busy}>
          Save
        </Button>
      )}
    </div>
  )
}
