'use client'

/**
 * VAT Reconciliation (build spec B2).
 *
 * Output VAT, input VAT, the net payable or refundable — and the tie-out back
 * to the general ledger VAT control accounts, with every line that does not
 * reconcile named in the exceptions list.
 *
 * Two things this page deliberately does NOT do:
 *
 * 1. It does not calculate. Every figure, including the rate, the due date and
 *    the prepare-by date, comes from the server (regulatory/vat_recon.py).
 *    There is no arithmetic on this side — the Tax Calendar page was rebuilt in
 *    September 2026 for exactly that reason, after every date it computed in
 *    the browser turned out to be wrong.
 *
 * 2. It posts nothing. There is no "post the difference" button and there must
 *    never be one: no journal posting and no GL mapping change without the CFO
 *    signing off first. A difference is something a person investigates at
 *    source, not something this screen fixes.
 *
 * VAT is due on the 25th (the CFO's correction, not the 28th advisers quote),
 * so the return has to be prepared by the 15th. Both dates are shown.
 */

import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2, Loader2, Lock } from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { Card } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import { getVatReconciliation, type VatReconResponse } from '@/lib/api'

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

/** Omni shows money with thousands separators. Figures arrive as strings so no
 *  precision is lost between the server's Decimal and the screen. */
function money(value: string): string {
  const n = Number(value)
  if (Number.isNaN(n)) return value
  return n.toLocaleString('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function fmtDate(iso: string): string {
  return new Date(iso + 'T00:00:00').toLocaleDateString('en-GB', {
    day: 'numeric', month: 'short', year: 'numeric',
  })
}

function Figure({ label, value, hint, tone = 'plain' }: {
  label: string; value: string; hint?: string
  tone?: 'plain' | 'good' | 'bad'
}) {
  return (
    <Card className="p-4">
      <div className="text-[11px] uppercase tracking-wide text-[#6B7280]">{label}</div>
      <div className={cn(
        'text-[22px] font-semibold mt-1 tabular-nums',
        tone === 'good' && 'text-[#059669]',
        tone === 'bad' && 'text-[#B3261E]',
        tone === 'plain' && 'text-[#0D1B2A]',
      )}>
        {value}
      </div>
      {hint ? <div className="text-[12px] text-[#6B7280] mt-1">{hint}</div> : null}
    </Card>
  )
}

export default function VatReconPage() {
  // The month just gone, in Gaborone terms — the period a preparer is actually
  // working on. The server defaults to the same thing if nothing is passed.
  const now = new Date()
  const defaultMonth = now.getMonth() === 0 ? 12 : now.getMonth()
  const defaultYear = now.getMonth() === 0 ? now.getFullYear() - 1 : now.getFullYear()

  const [year, setYear] = useState(defaultYear)
  const [month, setMonth] = useState(defaultMonth)
  const [data, setData] = useState<VatReconResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setData(await getVatReconciliation(year, month))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load the reconciliation.')
      setData(null)
    } finally {
      setLoading(false)
    }
  }, [year, month])

  useEffect(() => { void load() }, [load])

  const years: number[] = []
  for (let y = defaultYear + 1; y >= defaultYear - 4; y--) years.push(y)

  const errorCount = data?.exceptions.filter(e => e.severity === 'error').length ?? 0

  return (
    <div className="min-h-screen bg-[#FAFAFA]">
      <TopBar />

      <div className="max-w-6xl mx-auto px-6 py-8">
        <header className="mb-6">
          <h1 className="text-[22px] font-semibold text-[#0D1B2A]">VAT Reconciliation</h1>
          <p className="text-[13px] text-[#4B5563] mt-1">
            Output VAT, input VAT and the net position, tied back to the general ledger.
            This screen reads the ledger — it posts nothing and changes no account mapping.
          </p>
        </header>

        {/* Period picker */}
        <div className="flex flex-wrap items-center gap-3 mb-6">
          <select
            aria-label="Month"
            value={month}
            onChange={e => setMonth(Number(e.target.value))}
            className="h-9 px-3 rounded-md border border-[#E5E7EB] bg-white text-[13px] text-[#0D1B2A]"
          >
            {MONTHS.map((m, i) => (
              <option key={m} value={i + 1}>{m}</option>
            ))}
          </select>
          <select
            aria-label="Year"
            value={year}
            onChange={e => setYear(Number(e.target.value))}
            className="h-9 px-3 rounded-md border border-[#E5E7EB] bg-white text-[13px] text-[#0D1B2A]"
          >
            {years.map(y => <option key={y} value={y}>{y}</option>)}
          </select>
          {loading ? <Loader2 className="w-4 h-4 animate-spin text-[#6B7280]" /> : null}
        </div>

        {error ? (
          <Card className="p-4 border-[#FEE2E2] bg-[#FEF2F2] text-[13px] text-[#B3261E]">
            {error}
          </Card>
        ) : null}

        {data ? (
          <>
            {/* Where the return stands */}
            <Card className={cn(
              'p-4 mb-6 flex flex-wrap items-center gap-x-6 gap-y-2 text-[13px]',
              data.tie_out.reconciled
                ? 'border-[#D1FAE5] bg-[#ECFDF5]'
                : 'border-[#FDE68A] bg-[#FFFBEB]',
            )}>
              {data.tie_out.reconciled ? (
                <span className="flex items-center gap-2 text-[#059669] font-medium">
                  <CheckCircle2 className="w-4 h-4" />
                  Reconciled to the ledger
                </span>
              ) : (
                <span className="flex items-center gap-2 text-[#B45309] font-medium">
                  <AlertTriangle className="w-4 h-4" />
                  {errorCount} item{errorCount === 1 ? '' : 's'} to investigate
                </span>
              )}
              <span className="text-[#4B5563]">
                Rate applied: {(Number(data.vat_rate) * 100).toFixed(2)}%
              </span>
              <span className="text-[#4B5563]">
                Prepare by <strong className="text-[#0D1B2A]">{fmtDate(data.prepare_by_date)}</strong>
              </span>
              <span className="text-[#4B5563]">
                Due <strong className="text-[#0D1B2A]">{fmtDate(data.due_date)}</strong>
              </span>
              <span className="flex items-center gap-1.5 text-[#6B7280] ml-auto">
                <Lock className="w-3.5 h-3.5" /> Read-only
              </span>
            </Card>

            {/* The three headline figures */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
              <Figure
                label="Output VAT"
                value={money(data.subledger.output_vat)}
                hint={`on sales of ${money(data.subledger.output_net)}, net of credit notes`}
              />
              <Figure
                label="Input VAT"
                value={money(data.subledger.input_vat)}
                hint={`on purchases of ${money(data.subledger.input_net)}`}
              />
              <Figure
                label={
                  data.position === 'payable' ? 'Payable to BURS'
                    : data.position === 'refundable' ? 'Refundable from BURS'
                      : 'Nil return'
                }
                value={money(data.amount_due)}
                tone={data.position === 'refundable' ? 'good' : 'plain'}
                hint={`for ${MONTHS[month - 1]} ${year}`}
              />
            </div>

            {/* The tie-out */}
            <Card className="mb-6 overflow-hidden">
              <div className="px-4 py-3 border-b border-[#E5E7EB]">
                <h2 className="text-[14px] font-semibold text-[#0D1B2A]">Tie-out to the ledger</h2>
                <p className="text-[12px] text-[#6B7280] mt-0.5">
                  The documents on the left, the posted movement on the VAT control
                  accounts on the right. Anything other than zero is named below.
                </p>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-[13px]">
                  <thead className="bg-[#FAFAFA] text-[#6B7280]">
                    <tr>
                      <th className="text-left font-medium px-4 py-2"></th>
                      <th className="text-right font-medium px-4 py-2">Per the documents</th>
                      <th className="text-right font-medium px-4 py-2">Per the ledger</th>
                      <th className="text-right font-medium px-4 py-2">Difference</th>
                    </tr>
                  </thead>
                  <tbody>
                    {([
                      ['Output VAT', data.subledger.output_vat, data.ledger.output_vat, data.tie_out.output_difference],
                      ['Input VAT', data.subledger.input_vat, data.ledger.input_vat, data.tie_out.input_difference],
                      ['Net VAT', data.subledger.net_vat, data.ledger.net_vat, data.tie_out.net_difference],
                    ] as const).map(([label, sub, gl, diff]) => (
                      <tr key={label} className="border-t border-[#F3F4F6]">
                        <td className="px-4 py-2.5 text-[#0D1B2A]">{label}</td>
                        <td className="px-4 py-2.5 text-right tabular-nums">{money(sub)}</td>
                        <td className="px-4 py-2.5 text-right tabular-nums">{money(gl)}</td>
                        <td className={cn(
                          'px-4 py-2.5 text-right tabular-nums font-medium',
                          Number(diff) === 0 ? 'text-[#059669]' : 'text-[#B3261E]',
                        )}>
                          {money(diff)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>

            {/* Exceptions — nothing is silently absorbed */}
            <Card className="mb-6 overflow-hidden">
              <div className="px-4 py-3 border-b border-[#E5E7EB]">
                <h2 className="text-[14px] font-semibold text-[#0D1B2A]">
                  Exceptions ({data.exceptions.length})
                </h2>
                <p className="text-[12px] text-[#6B7280] mt-0.5">
                  Every line that does not reconcile, named. A flagged line still counts
                  towards the totals above — it is reported, not removed.
                </p>
              </div>
              {data.exceptions.length === 0 ? (
                <div className="px-4 py-6 text-[13px] text-[#6B7280]">
                  Nothing to investigate for this period.
                </div>
              ) : (
                <ul className="divide-y divide-[#F3F4F6]">
                  {data.exceptions.map((e, i) => (
                    <li key={`${e.code}-${e.reference}-${i}`} className="px-4 py-3">
                      <div className="flex items-start gap-3">
                        <span className={cn(
                          'shrink-0 text-[11px] font-mono px-1.5 py-0.5 rounded border',
                          e.severity === 'error'
                            ? 'bg-[#FEF2F2] text-[#B3261E] border-[#FEE2E2]'
                            : 'bg-[#FFFBEB] text-[#B45309] border-[#FDE68A]',
                        )}>
                          {e.code}
                        </span>
                        <div className="min-w-0">
                          <div className="text-[13px] text-[#0D1B2A]">{e.message}</div>
                          <div className="text-[12px] text-[#6B7280] mt-0.5">
                            {e.reference}
                            {e.difference !== null
                              ? <span className="ml-2">Difference {money(e.difference)}</span>
                              : null}
                          </div>
                        </div>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </Card>

            {/* The documents behind the figures */}
            <Card className="overflow-hidden">
              <div className="px-4 py-3 border-b border-[#E5E7EB]">
                <h2 className="text-[14px] font-semibold text-[#0D1B2A]">
                  Documents in the period ({data.lines.length})
                </h2>
              </div>
              <div className="overflow-x-auto max-h-[480px]">
                <table className="w-full text-[13px]">
                  <thead className="bg-[#FAFAFA] text-[#6B7280] sticky top-0">
                    <tr>
                      <th className="text-left font-medium px-4 py-2">Reference</th>
                      <th className="text-left font-medium px-4 py-2">Party</th>
                      <th className="text-left font-medium px-4 py-2">Date</th>
                      <th className="text-left font-medium px-4 py-2">Side</th>
                      <th className="text-right font-medium px-4 py-2">Net</th>
                      <th className="text-right font-medium px-4 py-2">VAT</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.lines.map((l, i) => (
                      <tr key={`${l.reference}-${l.kind}-${i}`} className="border-t border-[#F3F4F6]">
                        <td className="px-4 py-2 font-mono text-[12px] text-[#0D1B2A]">{l.reference}</td>
                        <td className="px-4 py-2 text-[#0D1B2A]">{l.party}</td>
                        <td className="px-4 py-2 text-[#4B5563]">{fmtDate(l.doc_date)}</td>
                        <td className="px-4 py-2 text-[#4B5563] capitalize">{l.kind.replace(/_/g, ' ')}</td>
                        <td className="px-4 py-2 text-right tabular-nums">{money(l.net_amount)}</td>
                        <td className="px-4 py-2 text-right tabular-nums">{money(l.vat_amount)}</td>
                      </tr>
                    ))}
                    {data.lines.length === 0 ? (
                      <tr>
                        <td colSpan={6} className="px-4 py-6 text-[13px] text-[#6B7280]">
                          No documents found for this period.
                        </td>
                      </tr>
                    ) : null}
                  </tbody>
                </table>
              </div>
            </Card>
          </>
        ) : null}
      </div>
    </div>
  )
}
