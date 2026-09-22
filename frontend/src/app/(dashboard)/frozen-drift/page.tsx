'use client'

/**
 * /frozen-drift — Self-Explaining Dashboard (feature #4, 2026-07-22).
 * When a locked headline figure (GWP / PAT / Total Assets / Cash & Bank) drifts
 * from its CFO-frozen value, this page doesn't just flag it — it explains WHY,
 * citing the posted journal entries booked since the figure was locked. Ends the
 * "why doesn't this tile match the workbook" back-and-forth. Read-only.
 * Server-gated to finance / management (CanViewFinancials).
 */
import { useCallback, useEffect, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import {
  getFrozenDriftNarrative,
  type FrozenDriftNarrativeResponse,
  type DriftNarrative,
} from '@/lib/api'
import { Loader2, AlertTriangle, CheckCircle2, ArrowRight, ShieldCheck } from 'lucide-react'

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'
const SERIF = 'Book Antiqua, Palatino, Georgia, serif'

const PERIODS: { code: string; label: string }[] = [
  { code: 'FY25_Jun2025', label: 'FY25 — Jun 2025' },
  { code: 'FY26_Mar2026', label: 'FY26 — Mar 2026 (9M)' },
]

function money(s: string): string {
  const n = Number(s)
  if (!isFinite(n)) return s
  return n.toLocaleString('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export default function FrozenDriftPage() {
  const [period, setPeriod] = useState(PERIODS[0].code)
  const [data, setData] = useState<FrozenDriftNarrativeResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [denied, setDenied] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null); setDenied(false)
    try {
      const resp = await getFrozenDriftNarrative(period)
      setData(resp)
    } catch (e: any) {
      if (e?.status === 403) setDenied(true)
      else setError(e?.message || 'Failed to load')
    } finally {
      setLoading(false)
    }
  }, [period])

  useEffect(() => { load() }, [load])

  const drifts: DriftNarrative[] = data?.narratives || []

  return (
    <>
      <TopBar title="Self-Explaining Dashboard" />
      <div className="p-6 max-w-5xl mx-auto">
        <header className="mb-6">
          <h1 style={{ fontFamily: SERIF, color: NAVY }} className="text-2xl font-semibold tracking-tight">
            Why did the numbers move?
          </h1>
          <p className="text-sm text-slate-500 mt-1 max-w-2xl">
            Every locked headline figure, checked against the live ledger. When one drifts past its
            tolerance, Omni names the journal entries that caused it — no more hunting.
          </p>
        </header>

        <div className="flex items-center gap-2 mb-6">
          {PERIODS.map((p) => {
            const active = p.code === period
            return (
              <button
                key={p.code}
                onClick={() => setPeriod(p.code)}
                className="px-3 py-1.5 rounded-full text-sm font-medium transition-colors"
                style={active
                  ? { background: NAVY, color: 'white' }
                  : { background: '#F1F5F9', color: '#475569' }}
              >
                {p.label}
              </button>
            )
          })}
        </div>

        {loading && (
          <div className="flex items-center gap-2 text-slate-500 py-16 justify-center">
            <Loader2 className="animate-spin" size={18} /> Reading the ledger…
          </div>
        )}

        {denied && !loading && (
          <div className="rounded-xl border border-amber-200 bg-amber-50 p-6 text-amber-800 text-sm">
            This view is restricted to finance and management.
          </div>
        )}

        {error && !loading && (
          <div className="rounded-xl border border-red-200 bg-red-50 p-6 text-red-700 text-sm">{error}</div>
        )}

        {!loading && !denied && !error && data && (
          <>
            {data.all_ok ? (
              <div
                className="rounded-2xl p-8 flex items-center gap-4"
                style={{ background: 'linear-gradient(135deg,#ECFDF5,#F0FDFA)', border: '1px solid #A7F3D0' }}
              >
                <ShieldCheck size={40} className="text-emerald-600 shrink-0" />
                <div>
                  <div style={{ fontFamily: SERIF, color: NAVY }} className="text-lg font-semibold">
                    Every figure ties to the frozen value.
                  </div>
                  <div className="text-sm text-emerald-700 mt-0.5">
                    {data.company || 'ADIC'} · {data.from_date} → {data.to_date}. Nothing has drifted past tolerance.
                  </div>
                  {data.reason && <div className="text-xs text-slate-500 mt-1">{data.reason}</div>}
                </div>
              </div>
            ) : (
              <div className="space-y-5">
                <div className="flex items-center gap-2 text-sm font-medium" style={{ color: '#B42318' }}>
                  <AlertTriangle size={16} />
                  {drifts.length} figure{drifts.length === 1 ? '' : 's'} drifted for {data.company || 'ADIC'}
                </div>

                {drifts.map((d) => {
                  const up = Number(d.diff) >= 0
                  return (
                    <article
                      key={d.label}
                      className="rounded-2xl bg-white overflow-hidden"
                      style={{ border: '1px solid #E2E8F0', boxShadow: '0 1px 3px rgba(13,27,42,0.06)' }}
                    >
                      <div className="px-5 py-4 flex items-center justify-between" style={{ background: NAVY }}>
                        <div style={{ fontFamily: SERIF }} className="text-white text-lg font-semibold">{d.label}</div>
                        <div
                          className="text-xs font-bold px-2.5 py-1 rounded-full"
                          style={{ background: ORANGE, color: NAVY }}
                        >
                          {up ? '▲' : '▼'} {d.diff_pct}% off frozen
                        </div>
                      </div>

                      <div className="px-5 py-4 flex items-center gap-3 text-sm border-b border-slate-100">
                        <span className="text-slate-400">Frozen</span>
                        <span className="font-semibold text-slate-700">P {money(d.frozen)}</span>
                        <ArrowRight size={15} className="text-slate-300" />
                        <span className="text-slate-400">Live</span>
                        <span className="font-semibold" style={{ color: up ? '#B45309' : '#B42318' }}>
                          P {money(d.actual)}
                        </span>
                        <span className="ml-auto text-xs text-slate-400">tolerance {d.tolerance_pct}%</span>
                      </div>

                      <div className="px-5 py-4">
                        <div className="text-[11px] uppercase tracking-wide text-slate-400 mb-1">Why</div>
                        <p className="text-[15px] leading-relaxed text-slate-800">{d.narrative}</p>
                      </div>

                      {d.top_entries.length > 0 && (
                        <div className="px-5 pb-5">
                          <div className="text-[11px] uppercase tracking-wide text-slate-400 mb-2">
                            Largest entries since it was locked
                          </div>
                          <div className="space-y-1.5">
                            {d.top_entries.map((e, i) => (
                              <div
                                key={`${e.entry_number}-${i}`}
                                className="flex items-center gap-3 text-[13px] rounded-lg px-3 py-2"
                                style={{ background: '#F8FAFC' }}
                              >
                                <span className="font-mono font-semibold" style={{ color: NAVY }}>{e.entry_number}</span>
                                <span className="text-slate-400">{e.entry_date}</span>
                                <span className="text-slate-500">· acct {e.account_code}</span>
                                <span className="ml-auto font-semibold tabular-nums text-slate-700">P {money(e.amount_bwp)}</span>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </article>
                  )
                })}
              </div>
            )}
          </>
        )}
      </div>
    </>
  )
}
