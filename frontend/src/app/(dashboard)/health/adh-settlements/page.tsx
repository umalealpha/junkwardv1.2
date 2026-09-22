'use client'

/**
 * /health/adh-settlements — the weekly ADH claims settlement load history.
 *
 * Every Saturday around 01:00 four near-identical files arrive from AFA and
 * Omni processes exactly one. This screen exists so Keetile can see, without
 * asking anyone, WHICH file was used and WHY the other three were not. The
 * rejected three — each with the single condition it failed — are the point of
 * the page, not a footnote to it.
 *
 * Read-only. Nothing here writes, and nothing here moves money: the loader
 * raises payment REQUESTS, which a person still signs off, and the money itself
 * only ever leaves at FNB under two-factor.
 *
 * Health claim data is medical. Claim numbers appear only inside a run's
 * problem list; no claimant name is in this payload, this page or its URL.
 */
import { useCallback, useEffect, useState } from 'react'
import {
  Loader2, RefreshCw, AlertTriangle, CheckCircle2, CircleSlash,
  FileWarning, FileX2, Inbox, FileCheck2,
} from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { getAdhSettlementRuns, type AdhSettlementRun } from '@/lib/api'
import { formatDay, formatTime } from './dates'

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'
const GREY = '#6B7280'

const STATUS_STYLE: Record<AdhSettlementRun['status'],
  { bg: string; fg: string; icon: typeof CheckCircle2 }> = {
  loaded:  { bg: '#E7F6EC', fg: '#1B6B36', icon: CheckCircle2 },
  partial: { bg: '#FEF3E2', fg: '#8A5A00', icon: FileWarning },
  nothing: { bg: '#F1F5F9', fg: '#475569', icon: CircleSlash },
  failed:  { bg: '#FDECEC', fg: '#A11B1B', icon: AlertTriangle },
}

function StatusChip({ run }: { run: AdhSettlementRun }) {
  const s = STATUS_STYLE[run.status] ?? STATUS_STYLE.nothing
  const Icon = s.icon
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium"
          style={{ background: s.bg, color: s.fg }}>
      <Icon size={13} /> {run.statusLabel}
    </span>
  )
}

function RunCard({ run }: { run: AdhSettlementRun }) {
  const failed = run.status === 'failed'
  // A failed run splits two ways, and the card must not mix them up: either a
  // file WAS opened and its contents were refused, or nothing on the drop
  // matched and no file was opened at all. The column-heading explanation is
  // only true of the first, and reads as nonsense under the second.
  const openedAFile = Boolean(run.fileName)
  return (
    <article className="overflow-hidden rounded-xl border border-slate-200 bg-white">

      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-200 px-4 py-3">
        <div>
          <div className="text-sm font-semibold" style={{ color: NAVY }}>
            {formatDay(run.loadedOn)}
            {formatTime(run.createdAt) && (
              <span className="ml-2 font-normal" style={{ color: GREY }}>
                {formatTime(run.createdAt)}
              </span>
            )}
          </div>
          <div className="mt-1 flex items-center gap-1.5 text-xs" style={{ color: GREY }}>
            {failed && !run.fileName ? (
              <>No file was opened</>
            ) : (
              <><FileCheck2 size={13} /> {run.fileName || '—'}</>
            )}
          </div>
        </div>
        <StatusChip run={run} />
      </header>

      {failed && (
        <div className="border-b border-red-200 bg-red-50 px-4 py-3">
          <div className="flex items-center gap-2 text-sm font-semibold text-red-800">
            <AlertTriangle size={16} />
            {openedAFile
              ? 'Nothing was loaded from this file'
              : 'No file matched the four conditions'}
          </div>
          <p className="mt-1.5 text-sm text-red-900">
            {run.error || 'The run stopped and gave no reason.'}
          </p>
          {openedAFile && (
            <p className="mt-1.5 text-xs text-red-800">
              The whole file is refused when it is not the listing we expect — for example a
              column heading that is not recognised. Nothing partial is taken from it.
            </p>
          )}
        </div>
      )}

      {!failed && (
        <div className="grid grid-cols-2 gap-px bg-slate-200 sm:grid-cols-4">
          {[
            { label: 'Claim lines in the file', value: run.lineCount },
            { label: 'Payment requests raised', value: run.createdCount },
            { label: 'Already loaded before', value: run.skippedCount },
            { label: 'Could not be raised', value: run.failedCount },
          ].map(k => (
            <div key={k.label} className="bg-white px-4 py-3">
              <div className="text-xs" style={{ color: GREY }}>{k.label}</div>
              <div className="mt-0.5 text-xl font-semibold tabular-nums" style={{ color: NAVY }}>
                {k.value}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* The whole reason the screen exists. */}
      <div className="border-t border-slate-200 px-4 py-3">
        <div className="flex items-center gap-2 text-sm font-semibold" style={{ color: NAVY }}>
          <FileX2 size={15} style={{ color: ORANGE }} />
          Other files that arrived, and why each was not used
        </div>
        {run.notProcessed.length === 0 ? (
          <p className="mt-1.5 text-sm" style={{ color: GREY }}>
            No other files were recorded against this run.
          </p>
        ) : (
          <table className="mt-2.5 w-full border-collapse text-sm">
            <tbody>
              {run.notProcessed.map((r, i) => (
                <tr key={`${r.name}-${i}`} className="border-t border-slate-100 align-top first:border-t-0">
                  <td className="w-[38%] py-2 pr-4 font-medium break-all" style={{ color: NAVY }}>
                    {r.name}
                  </td>
                  <td className="py-2 text-slate-600">{r.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {run.problems.length > 0 && (
        <div className="border-t border-amber-200 bg-amber-50 px-4 py-3">
          <div className="flex items-center gap-2 text-sm font-semibold text-amber-900">
            <FileWarning size={15} />
            {run.problems.length} claim {run.problems.length === 1 ? 'line' : 'lines'} produced
            no payment request
          </div>
          <table className="mt-2.5 w-full border-collapse text-sm">
            <tbody>
              {run.problems.map((p, i) => (
                <tr key={`${p.claim_number}-${i}`} className="border-t border-amber-100 align-top first:border-t-0">
                  <td className="w-[38%] py-2 pr-4 font-medium text-amber-900 break-all">
                    {p.claim_number}
                  </td>
                  <td className="py-2 text-amber-900">{p.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </article>
  )
}

export default function AdhSettlementsPage() {
  const [runs, setRuns] = useState<AdhSettlementRun[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const data = await getAdhSettlementRuns()
      setRuns(data.runs ?? [])
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Could not load the settlement history.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  return (
    <>
      <TopBar />
      <main className="mx-auto w-full max-w-5xl px-5 py-7">

        <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold" style={{ color: NAVY }}>ADH Settlement Runs</h1>
            <p className="mt-1 max-w-2xl text-sm" style={{ color: GREY }}>
              Four near-identical files arrive from AFA each Saturday and exactly one is used.
              This is which one, and the reason each of the others was left alone. Each claim
              line becomes a payment request awaiting finance sign-off — nothing here has been
              paid.
            </p>
          </div>
          <button onClick={() => void load()} disabled={loading}
                  className="inline-flex items-center gap-2 rounded-lg border border-slate-300 px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50">
            <RefreshCw size={15} className={loading ? 'animate-spin' : ''} /> Refresh
          </button>
        </header>

        {error && (
          <div className="mb-4 rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-800">
            {error}
          </div>
        )}

        {loading ? (
          <div className="flex items-center gap-2 py-16" style={{ color: GREY }}>
            <Loader2 className="animate-spin" size={18} /> Loading…
          </div>
        ) : runs.length === 0 ? (
          <div className="rounded-xl border border-dashed border-slate-300 bg-white p-12 text-center">
            <Inbox size={28} className="mx-auto" style={{ color: ORANGE }} />
            <p className="mt-3 text-base font-medium" style={{ color: NAVY }}>
              Nothing has arrived yet
            </p>
            <p className="mx-auto mt-1.5 max-w-md text-sm" style={{ color: GREY }}>
              The settlement files come in on a Saturday, early. The first run will appear here
              on its own — there is nothing to do and nothing is wrong.
            </p>
          </div>
        ) : (
          <div className="space-y-5">
            {runs.map(r => <RunCard key={r.id} run={r} />)}
          </div>
        )}
      </main>
    </>
  )
}
