'use client'

/**
 * /banking/realpay/monitoring — the six Finance monitoring reports.
 *
 * Keetile Mokhendo asked for six reports he currently builds by hand (handover
 * note, 17 Aug 2026). All six read the Graphite replica read-only: no journal,
 * no money movement, no policy change.
 *
 * Read order is deliberate. The first thing on the screen is money we took from
 * somebody we should not have — that is the one that turns into a refund and a
 * complaint. Agreement comes last, because nobody acts on it.
 */

import { useCallback, useEffect, useState } from 'react'
import {
  downloadFinanceMonitoringCsv, getFinanceMonitoringReport,
  listFinanceMonitoringReports,
} from '@/lib/api'
import type { FinanceMonitoringReport, FinanceMonitoringResult } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  AlertTriangle, CheckCircle2, Download, Loader2, RefreshCw, ShieldAlert,
} from 'lucide-react'

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'
const RED = '#B91C1C'
const AMBER = '#B45309'
const GREEN = '#047857'

/** Plain-English cadence — "friday-1630" means nothing to a reader. */
const CADENCE_LABEL: Record<string, string> = {
  daily: 'Every day',
  weekly: 'Every week',
  fortnightly: 'Every two weeks',
  monthly: 'Every month',
  'friday-1630': 'Fridays, 16:30',
  'friday-cob': 'Fridays, close of business',
}

/** One line per report saying what it is FOR, in the words a person would use. */
const WHAT_IT_ANSWERS: Record<string, string> = {
  'collections-vs-graphite':
    'Did we debit anybody whose policy is cancelled or lapsed? That money has to go back.',
  'refunds-posted':
    'Has every refund Graphite recorded actually been raised for payment on our side?',
  'policy-status-integrity':
    'Which policies say one thing and do another — cancelled but still being debited, active but expired.',
  'contract-expiry':
    'Whose debit schedule is about to run out while the policy is still on risk?',
  'failed-debits':
    'Which debits did not collect, and which have now failed more than once?',
  'payments-vs-bank':
    'Does what Graphite says it received match what actually landed in the bank?',
}

function money(v: unknown): string {
  const n = Number(v)
  if (!isFinite(n)) return '—'
  return `P${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function isMoneyKey(k: string): boolean {
  return /amount|collected|premium|difference|_at_risk/.test(k)
}

/** snake_case → readable heading. */
function label(k: string): string {
  return k.replace(/_/g, ' ').replace(/^\w/, c => c.toUpperCase())
}

const SEVERITY_STYLE: Record<string, { bg: string; fg: string; text: string }> = {
  critical: { bg: '#FEE2E2', fg: RED, text: 'Act now' },
  warning: { bg: '#FEF3C7', fg: AMBER, text: 'Check' },
  ok: { bg: '#D1FAE5', fg: GREEN, text: 'Fine' },
  unknown: { bg: '#E5E7EB', fg: '#4B5563', text: 'No data' },
}

export default function FinanceMonitoringPage() {
  const [reports, setReports] = useState<FinanceMonitoringReport[]>([])
  const [active, setActive] = useState<string>('')
  const [result, setResult] = useState<FinanceMonitoringResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string>('')
  const [downloading, setDownloading] = useState(false)

  useEffect(() => {
    listFinanceMonitoringReports()
      .then(d => {
        setReports(d.reports)
        if (d.reports.length) setActive(d.reports[0].slug)
      })
      .catch(e => setError(e?.message || 'Could not load the report list.'))
  }, [])

  const run = useCallback(async (slug: string) => {
    if (!slug) return
    setLoading(true); setError(''); setResult(null)
    try {
      setResult(await getFinanceMonitoringReport(slug))
    } catch (e: unknown) {
      // A dead replica returns 503 on purpose: an empty report would read as
      // all-clear, which is the one wrong answer this screen must never give.
      setError((e as Error)?.message
        || 'This report could not be run. Nothing is being claimed either way.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { if (active) void run(active) }, [active, run])

  const current = reports.find(r => r.slug === active)

  return (
    <div className="min-h-screen bg-slate-50">
      <TopBar title="Finance monitoring" />
      <div className="mx-auto max-w-7xl px-4 py-6 space-y-6">

        <div className="rounded-lg border bg-white p-4" style={{ borderColor: '#E5E7EB' }}>
          <h1 className="text-lg font-semibold" style={{ color: NAVY }}>
            The six monitoring reports
          </h1>
          <p className="mt-1 text-sm text-slate-600">
            All six read the policy system directly, so none of them needs a file
            emailed first. Nothing on this page moves money or changes a policy.
          </p>
        </div>

        {/* Report picker */}
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {reports.map(r => {
            const on = r.slug === active
            return (
              <button
                key={r.slug}
                onClick={() => setActive(r.slug)}
                className="rounded-lg border p-4 text-left transition-shadow hover:shadow-md focus:outline-none focus:ring-2"
                style={{
                  borderColor: on ? ORANGE : '#E5E7EB',
                  background: on ? '#FFFBEB' : '#FFFFFF',
                  boxShadow: on ? `inset 0 0 0 1px ${ORANGE}` : undefined,
                }}
                aria-pressed={on}
              >
                <div className="text-sm font-semibold" style={{ color: NAVY }}>
                  {r.title}
                </div>
                <div className="mt-1 text-xs text-slate-500">
                  {CADENCE_LABEL[r.cadence] || r.cadence}
                </div>
                <p className="mt-2 text-xs leading-relaxed text-slate-600">
                  {WHAT_IT_ANSWERS[r.slug] || ''}
                </p>
              </button>
            )
          })}
        </div>

        {/* Result */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between gap-3">
            <CardTitle style={{ color: NAVY }}>
              {current?.title || 'Report'}
            </CardTitle>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" onClick={() => void run(active)}
                      disabled={loading || !active}>
                <RefreshCw className="mr-1 h-4 w-4" /> Run again
              </Button>
              <Button variant="outline" size="sm" disabled={!active || downloading}
                      onClick={() => {
                        setDownloading(true)
                        void downloadFinanceMonitoringCsv(active)
                          .catch(e => setError(e?.message || 'The download failed.'))
                          .finally(() => setDownloading(false))
                      }}>
                {downloading
                  ? <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                  : <Download className="mr-1 h-4 w-4" />}
                Download
              </Button>
            </div>
          </CardHeader>

          <CardContent>
            {loading && (
              <div className="flex items-center gap-2 py-10 text-slate-500">
                <Loader2 className="h-5 w-5 animate-spin" /> Reading the policy system…
              </div>
            )}

            {!loading && error && (
              <div className="flex items-start gap-3 rounded-md p-4"
                   style={{ background: '#FEE2E2', color: RED }}>
                <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0" />
                <div>
                  <div className="font-semibold">This report did not run.</div>
                  <p className="mt-1 text-sm">{error}</p>
                  <p className="mt-1 text-sm">
                    That is not the same as “nothing is wrong” — it means nothing
                    was checked.
                  </p>
                </div>
              </div>
            )}

            {!loading && !error && result && (
              <div className="space-y-5">
                {/* Headline numbers */}
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                  {Object.entries(result.summary).map(([k, v]) => (
                    <div key={k} className="rounded-lg border p-3"
                         style={{ borderColor: '#E5E7EB' }}>
                      <div className="text-xs uppercase tracking-wide text-slate-500">
                        {label(k)}
                      </div>
                      <div className="mt-1 text-xl font-semibold" style={{ color: NAVY }}>
                        {isMoneyKey(k) ? money(v) : Number(v).toLocaleString()}
                      </div>
                    </div>
                  ))}
                </div>

                {result.meta?.note && (
                  <p className="text-xs text-slate-500">{result.meta.note}</p>
                )}

                {result.rows.length === 0 ? (
                  <div className="flex items-center gap-2 rounded-md p-4"
                       style={{ background: '#D1FAE5', color: GREEN }}>
                    <CheckCircle2 className="h-5 w-5" />
                    Nothing to action. This report ran and found no exceptions.
                  </div>
                ) : (
                  <div className="overflow-x-auto rounded-lg border"
                       style={{ borderColor: '#E5E7EB' }}>
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="bg-slate-50 text-left">
                          {Object.keys(result.rows[0])
                            .filter(k => k !== 'severity')
                            .map(k => (
                              <th key={k} className="whitespace-nowrap px-3 py-2 font-medium text-slate-600">
                                {label(k)}
                              </th>
                            ))}
                          <th className="px-3 py-2 font-medium text-slate-600">Action</th>
                        </tr>
                      </thead>
                      <tbody>
                        {result.rows.slice(0, 200).map((row, i) => {
                          const sev = String(row.severity || 'unknown')
                          const st = SEVERITY_STYLE[sev] || SEVERITY_STYLE.unknown
                          return (
                            <tr key={i} className="border-t" style={{ borderColor: '#F1F5F9' }}>
                              {Object.keys(result.rows[0])
                                .filter(k => k !== 'severity')
                                .map(k => (
                                  <td key={k} className="whitespace-nowrap px-3 py-2 text-slate-700">
                                    {row[k] == null || row[k] === ''
                                      ? '—'
                                      : isMoneyKey(k) ? money(row[k]) : String(row[k])}
                                  </td>
                                ))}
                              <td className="px-3 py-2">
                                <span className="rounded-full px-2 py-0.5 text-xs font-medium"
                                      style={{ background: st.bg, color: st.fg }}>
                                  {st.text}
                                </span>
                              </td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                    {result.rows.length > 200 && (
                      <div className="flex items-center gap-2 border-t px-3 py-2 text-xs text-slate-500"
                           style={{ borderColor: '#F1F5F9' }}>
                        <AlertTriangle className="h-4 w-4" />
                        Showing the first 200 of {result.rows.length.toLocaleString()}.
                        Download the file for all of them.
                      </div>
                    )}
                  </div>
                )}

                <p className="text-xs text-slate-400">
                  {result.meta?.source} · run {new Date(result.meta.generated_at).toLocaleString()}
                </p>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
