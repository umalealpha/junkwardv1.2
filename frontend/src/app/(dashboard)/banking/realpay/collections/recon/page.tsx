'use client'

/**
 * /banking/realpay/collections/recon — upload a RealPay export, get it
 * reconciled against Graphite, and read what the numbers mean.
 *
 * Keetile Mokhendo asked for this (handover note, 17 Aug 2026, report 1 of 6):
 * a RealPay-vs-Graphite reconciliation he does not rebuild by hand every month.
 * The importer behind it has existed since June but needed a shell on the
 * server — this is the same work behind a file picker.
 *
 * Nothing here posts a journal, moves money or changes a policy. It reads the
 * file, reads Graphite read-only, and shows the differences.
 */

import { useCallback, useRef, useState } from 'react'
import { uploadRealPayRecon } from '@/lib/api'
import type { RealPayReconItem, RealPayReconResult } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  Upload, Sparkles, AlertTriangle, CheckCircle2, FileSpreadsheet,
  Loader2, Info, ArrowRightLeft,
} from 'lucide-react'

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'
const RED = '#B91C1C'
const GREEN = '#047857'

function money(s: string | number | null | undefined): string {
  if (s == null) return '—'
  const v = Number(s)
  if (!isFinite(v)) return '—'
  return v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

/** The four buckets, in the order a person should read them: the ones that
 *  cost money first, agreement last. */
const BUCKETS = [
  {
    key: 'only_in_realpay' as const,
    title: 'Collected, but no policy found',
    why: 'We took money against a contract the policy system does not know. Check these first — the policy may have lapsed or been cancelled.',
    tone: RED,
  },
  {
    key: 'amount_differs' as const,
    title: 'Amount differs',
    why: 'Same contract on both sides, different money. Largest gap first.',
    tone: ORANGE,
  },
  {
    key: 'unverified' as const,
    title: 'Not verified',
    why: 'The policy system could not be read, so these were never compared. They are not agreements — they are unanswered.',
    tone: ORANGE,
  },
  {
    key: 'matched' as const,
    title: 'Agrees',
    why: 'RealPay and the policy system say the same thing.',
    tone: GREEN,
  },
]

export default function RealPayReconPage() {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<RealPayReconResult | null>(null)
  const [open, setOpen] = useState<string>('only_in_realpay')
  const fileRef = useRef<HTMLInputElement>(null)

  const run = useCallback(async (file: File) => {
    setBusy(true); setError(null); setResult(null)
    try {
      setResult(await uploadRealPayRecon(file))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'That upload could not be read.')
    } finally {
      setBusy(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }, [])

  const c = result?.counts
  const t = result?.totals

  return (
    <>
      <TopBar title="RealPay — reconcile against the policy system" />
      <div className="mx-auto max-w-7xl space-y-5 p-4 md:p-6">

        {/* ── upload ─────────────────────────────────────────────────────── */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <ArrowRightLeft className="h-4 w-4" style={{ color: NAVY }} />
              Upload the RealPay export
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-[#6B7280]">
              Export the Transaction Report from RealPay for the period you want, then
              drop the file here. Excel (.xlsb / .xlsx) or .csv. Nothing is saved and
              nothing is posted — the file is read, compared to the policy system, and
              the answer is shown below.
            </p>
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <input
                ref={fileRef}
                type="file"
                accept=".xlsb,.xlsx,.xlsm,.csv"
                className="hidden"
                onChange={(e) => { const f = e.target.files?.[0]; if (f) run(f) }}
              />
              <Button onClick={() => fileRef.current?.click()} disabled={busy}
                      leftIcon={busy
                        ? <Loader2 className="h-4 w-4 animate-spin" />
                        : <Upload className="h-4 w-4" />}>
                {busy ? 'Reading and comparing…' : 'Choose a file'}
              </Button>
              {result && (
                <span className="inline-flex items-center gap-1.5 text-xs text-[#6B7280]">
                  <FileSpreadsheet className="h-3.5 w-3.5" />
                  {result.file.name} · sheet “{result.file.sheet}” ·{' '}
                  {result.file.rows_read.toLocaleString()} debit lines read
                  {result.file.rows_skipped_no_contract > 0 &&
                    ` · ${result.file.rows_skipped_no_contract} skipped (no contract number)`}
                </span>
              )}
            </div>
            {error && (
              <div className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
                {error}
              </div>
            )}
          </CardContent>
        </Card>

        {result && (
          <>
            {/* ── the AI read ────────────────────────────────────────────── */}
            <Card>
              <CardContent className="p-4">
                <div className="mb-2 flex items-center gap-2 text-sm font-semibold"
                     style={{ color: NAVY }}>
                  <Sparkles className="h-4 w-4" style={{ color: ORANGE }} />
                  What this says
                </div>
                {result.commentary.ok ? (
                  <p className="text-[15px] leading-relaxed" style={{ color: NAVY }}>
                    {result.commentary.text}
                  </p>
                ) : (
                  <p className="text-sm text-[#6B7280]">{result.commentary.reason}</p>
                )}
                <p className="mt-2 text-[11px] text-[#9CA3AF]">
                  Written from the counts and totals only — no customer name, contract
                  number or bank detail is sent anywhere.
                </p>
              </CardContent>
            </Card>

            {!result.graphite_available && (
              <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
                <b>The policy system was not reachable.</b> The file was read in full, but
                nothing could be compared against it — so nothing is being called agreed
                and nothing is being called missing. Try again once the connection is back.
              </div>
            )}

            {result.only_in_graphite_checked === false && (
              <div className="rounded-lg border border-[#E5E7EB] bg-[#F9FAFB] px-4 py-3 text-sm text-[#374151]">
                <b>Missed debits are not part of this check.</b> This compares what the file
                collected against the policies it names. Finding policies that should have
                collected and did not needs the collection schedule for the period, which the
                export does not carry — so a zero here would mean “not looked at”, not
                “nothing missing”.
              </div>
            )}

            {/* ── headline ───────────────────────────────────────────────── */}
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {[
                { label: 'Collected in this file', value: `P ${money(t?.collected)}`,
                  note: `${c?.realpay_contracts.toLocaleString()} contracts`, tone: NAVY },
                { label: 'Agrees', value: (c?.matched ?? 0).toLocaleString(),
                  note: `P ${money(t?.matched)}`, tone: GREEN },
                { label: 'Amount differs', value: (c?.amount_differs ?? 0).toLocaleString(),
                  note: `net P ${money(t?.differs_gap)}`, tone: ORANGE },
                { label: 'Collected, no policy', value: (c?.only_in_realpay ?? 0).toLocaleString(),
                  note: `P ${money(t?.only_realpay)}`, tone: RED },
              ].map((s) => (
                <Card key={s.label}>
                  <CardContent className="p-4">
                    <div className="text-[11px] uppercase tracking-wide text-[#6B7280]">
                      {s.label}
                    </div>
                    <div className="mt-1 text-[21px] font-bold tabular-nums"
                         style={{ color: s.tone }}>{s.value}</div>
                    <div className="text-[11px] text-[#6B7280]">{s.note}</div>
                  </CardContent>
                </Card>
              ))}
            </div>

            {/* ── the four buckets ───────────────────────────────────────── */}
            {BUCKETS.map((b) => {
              const rows = (result[b.key] ?? []) as RealPayReconItem[]
              const isOpen = open === b.key
              return (
                <Card key={b.key}>
                  <CardHeader className="cursor-pointer" onClick={() => setOpen(isOpen ? '' : b.key)}>
                    <div className="flex items-center justify-between">
                      <CardTitle className="flex items-center gap-2 text-[15px]">
                        {b.tone === GREEN
                          ? <CheckCircle2 className="h-4 w-4" style={{ color: b.tone }} />
                          : <AlertTriangle className="h-4 w-4" style={{ color: b.tone }} />}
                        {b.title}
                        <span className="rounded-full px-2 py-0.5 text-xs font-semibold"
                              style={{ background: `${b.tone}14`, color: b.tone }}>
                          {rows.length.toLocaleString()}
                        </span>
                      </CardTitle>
                      <span className="text-xs text-[#6B7280]">{isOpen ? 'Hide' : 'Show'}</span>
                    </div>
                  </CardHeader>
                  {isOpen && (
                    <CardContent>
                      <p className="mb-3 flex items-start gap-1.5 text-xs text-[#6B7280]">
                        <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />{b.why}
                      </p>
                      {rows.length === 0 ? (
                        <p className="text-sm italic text-[#9CA3AF]">Nothing in this group.</p>
                      ) : (
                        <div className="overflow-x-auto">
                          <table className="w-full text-sm">
                            <thead>
                              <tr className="border-b border-[#E5E7EB] text-[10px] uppercase tracking-wide text-[#6B7280]">
                                <th className="py-2 pr-3 text-left">Contract</th>
                                <th className="py-2 pr-3 text-left">Client</th>
                                <th className="py-2 pr-3 text-right">Collected</th>
                                {b.key !== 'matched' && (
                                  <th className="py-2 pr-3 text-right">Expected</th>
                                )}
                                {b.key === 'amount_differs' && (
                                  <th className="py-2 pr-3 text-right">Difference</th>
                                )}
                                <th className="py-2 text-left">Debits</th>
                              </tr>
                            </thead>
                            <tbody className="tabular-nums">
                              {rows.map((r: any, i: number) => (
                                <tr key={`${r.contract_number}-${i}`} className="border-b border-[#F3F4F6]">
                                  <td className="py-2 pr-3 font-mono text-xs" style={{ color: NAVY }}>
                                    {r.contract_raw || r.policy_number || r.contract_number}
                                  </td>
                                  <td className="py-2 pr-3">{r.client_name || '—'}</td>
                                  <td className="py-2 pr-3 text-right font-mono">
                                    {r.collected != null ? money(r.collected) : '—'}
                                  </td>
                                  {b.key !== 'matched' && (
                                    <td className="py-2 pr-3 text-right font-mono">
                                      {r.expected != null ? money(r.expected) : '—'}
                                    </td>
                                  )}
                                  {b.key === 'amount_differs' && (
                                    <td className="py-2 pr-3 text-right font-mono font-semibold"
                                        style={{ color: Number(r.difference) < 0 ? RED : ORANGE }}>
                                      {money(r.difference)}
                                    </td>
                                  )}
                                  <td className="py-2 text-xs text-[#6B7280]">
                                    {r.lines != null
                                      ? `${r.lines} line${r.lines === 1 ? '' : 's'}`
                                      : (r.status || '—')}
                                    {r.failed > 0 && ` · ${r.failed} failed`}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                          {result.truncated && (
                            <p className="mt-2 text-[11px] text-[#6B7280]">
                              Showing the first 200 in this group.
                            </p>
                          )}
                        </div>
                      )}
                    </CardContent>
                  )}
                </Card>
              )
            })}
          </>
        )}
      </div>
    </>
  )
}
