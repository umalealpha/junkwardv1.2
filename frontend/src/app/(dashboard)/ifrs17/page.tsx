'use client'

/**
 * /ifrs17 — IFRS 17 valuation cockpit, Alpha Direct Insurance Company.
 *
 * Built in the shape of the 5-Year Plan Cockpit (CFO 2026-08-25: "copy the budget
 * module where we can move figures easily so it changes things"). Same rules:
 *   · THE PAGE OWNS NO ARITHMETIC — lib/ifrs17Model.ts does. Drag a lever, every
 *     table, ratio and reserve here recomputes.
 *   · Built from per-segment figures so a segment switch genuinely removes it.
 *   · At base lever positions the screen shows Empirica's SIGNED valuation
 *     verbatim; a badge marks the moment a lever departs from the signed basis.
 *
 * The disclosures and the MA bridge come from the API (they read the ledger and
 * the frozen register); everything else is computed here for instant response.
 */

import { useEffect, useMemo, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import {
  BASE, SEGMENTS, SEGMENT_IDS, SLIDERS, SIGNED_FY25, TREATIES, IBNR_BY_UWY,
  compute, jbbSensitivity, mn, num, pct, signed,
  type Levers, type SegmentId,
} from '@/lib/ifrs17Model'
import { RotateCcw, AlertTriangle, TrendingUp, ShieldAlert, ClipboardList,
         Download, Check, Undo2, ChevronDown, ChevronRight } from 'lucide-react'

const NAVY = '#0D1B2A', ORANGE = '#F4A623', GREEN = '#2E9E5B', RED = '#D14343'

const TABS = ['Results', 'Statistics', 'Statements', 'Reserves', 'Reinsurance',
              'Disclosures', 'MA bridge', 'Exceptions', 'Data quality'] as const
type Tab = typeof TABS[number]

const fmtLever = (v: number, f: string) =>
  f === 'factor' ? v.toFixed(4) : f === 'pct4' ? `${(v * 100).toFixed(4)}%` : `${(v * 100).toFixed(1)}%`

export default function Ifrs17Cockpit() {
  const [levers, setLevers] = useState<Levers>(BASE)
  const [on, setOn] = useState<Set<SegmentId>>(new Set(SEGMENT_IDS))
  const [tab, setTab] = useState<Tab>('Results')
  const [bridge, setBridge] = useState<any>(null)
  const [disc, setDisc] = useState<any>(null)
  const [stats, setStats] = useState<any>(null)

  const c = useMemo(() => compute(levers, on), [levers, on])
  const setLever = (k: keyof Levers, v: number | boolean) =>
    setLevers((p) => ({ ...p, [k]: v }))
  const toggleSeg = (id: SegmentId) =>
    setOn((p) => { const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n })
  const reset = () => { setLevers(BASE); setOn(new Set(SEGMENT_IDS)) }

  // The bridge reads the ledger + the frozen register, so it comes from the API.
  useEffect(() => {
    import('@/lib/api').then(({ apiFetch }) => {
      apiFetch<any>('/ifrs17/bridge/?year=FY2025').then(setBridge).catch(() => setBridge(null))
    })
  }, [])
  useEffect(() => {
    if (tab !== 'Statistics') return
    const qs = new URLSearchParams()
    ;(Object.keys(levers) as (keyof Levers)[]).forEach((k) => qs.set(k, String(levers[k])))
    qs.set('segments', [...on].join(','))
    import('@/lib/api').then(({ apiFetch }) => {
      apiFetch<any>(`/ifrs17/statistics/?${qs}`).then(setStats).catch(() => setStats(null))
    })
  }, [tab, levers, on])
  useEffect(() => {
    if (tab !== 'Disclosures') return
    const qs = new URLSearchParams()
    ;(Object.keys(levers) as (keyof Levers)[]).forEach((k) => qs.set(k, String(levers[k])))
    qs.set('segments', [...on].join(','))
    import('@/lib/api').then(({ apiFetch }) => {
      apiFetch<any>(`/ifrs17/disclosures/?${qs}`).then(setDisc).catch(() => setDisc(null))
    })
  }, [tab, levers, on])

  const kpis = [
    { label: 'Insurance revenue', v: c.insuranceRevenue },
    { label: 'Profit before tax', v: c.profitBeforeTax, hero: true },
    { label: 'Combined ratio', v: c.combinedRatio, isPct: true },
    { label: 'Insurance contract liabilities', v: c.insuranceContractLiabilities },
    { label: 'Reinsurance contract assets', v: c.reinsuranceContractAssets },
    { label: 'Net IFRS 17 liability', v: c.netIfrs17Liability },
    { label: 'Gross IBNR', v: c.grossIbnr },
    { label: 'Loss ratio', v: c.lossRatio, isPct: true },
  ]

  return (
    <div className="min-h-screen bg-slate-50">
      <TopBar />
      <div className="mx-auto max-w-[1400px] px-4 py-6">
        {/* header */}
        <div className="flex items-start justify-between gap-4 mb-4">
          <div>
            <h1 className="text-2xl font-semibold" style={{ color: NAVY }}>
              IFRS 17 Valuation Cockpit
            </h1>
            <p className="text-sm text-slate-500 mt-1">
              Alpha Direct Insurance Company · 30 June 2026 · Empirica Actuaries ·
              Premium Allocation Approach.
            </p>
          </div>
          <div className="flex items-center gap-3">
            {c.isSignedBasis ? (
              <span className="text-xs font-medium px-3 py-1.5 rounded-full"
                    style={{ background: '#E7F3EC', color: GREEN }}>
                ● Signed valuation — Empirica, verbatim
              </span>
            ) : (
              <span className="text-xs font-medium px-3 py-1.5 rounded-full flex items-center gap-1"
                    style={{ background: '#FDF3E3', color: ORANGE }}>
                <AlertTriangle size={13} /> Sensitivity — a lever has moved off the signed basis
              </span>
            )}
            <button onClick={reset}
                    className="inline-flex items-center gap-1.5 text-sm px-3 py-1.5 rounded-md border border-slate-300 hover:bg-slate-100">
              <RotateCcw size={14} /> Reset
            </button>
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-[300px_1fr] gap-6">
          {/* ---- lever rail ---- */}
          <aside className="space-y-4">
            <div className="rounded-xl border border-slate-200 bg-white p-4">
              <h2 className="text-sm font-semibold mb-3" style={{ color: NAVY }}>Assumptions</h2>
              <div className="space-y-4">
                {SLIDERS.map((s) => {
                  const v = levers[s.key] as number
                  const base = BASE[s.key] as number
                  const moved = v !== base
                  return (
                    <div key={s.key}>
                      <div className="flex justify-between items-baseline text-xs mb-1">
                        <span className="font-medium text-slate-700">{s.label}</span>
                        <span style={{ color: moved ? ORANGE : NAVY }} className="font-semibold tabular-nums">
                          {fmtLever(v, s.format)}
                          {moved && <span className="text-slate-400"> · base {fmtLever(base, s.format)}</span>}
                        </span>
                      </div>
                      <input type="range" min={s.min} max={s.max} step={s.step} value={v}
                             onChange={(e) => setLever(s.key, Number(e.target.value))}
                             className="w-full accent-[#F4A623]" />
                      <p className="text-[11px] leading-snug text-slate-400 mt-1">{s.why}</p>
                    </div>
                  )
                })}
              </div>
            </div>

            {/* toggles */}
            <div className="rounded-xl border border-slate-200 bg-white p-4">
              <h2 className="text-sm font-semibold mb-3" style={{ color: NAVY }}>Segments</h2>
              <div className="flex flex-wrap gap-2">
                {SEGMENTS.map((s) => (
                  <button key={s.id} onClick={() => toggleSeg(s.id)}
                          className="text-xs px-2.5 py-1 rounded-full border transition"
                          style={on.has(s.id)
                            ? { background: NAVY, color: 'white', borderColor: NAVY }
                            : { background: 'white', color: '#94A3B8', borderColor: '#E2E8F0' }}>
                    {s.name}
                  </button>
                ))}
              </div>
              <div className="mt-3 pt-3 border-t border-slate-100 flex items-center justify-between">
                <span className="text-xs text-slate-600">Health</span>
                <button onClick={() => setLever('healthModelled', !levers.healthModelled)}
                        className="text-xs px-2.5 py-1 rounded-full border"
                        style={levers.healthModelled
                          ? { background: ORANGE, color: 'white', borderColor: ORANGE }
                          : { background: 'white', color: '#94A3B8', borderColor: '#E2E8F0' }}>
                  {levers.healthModelled ? 'Modelled' : 'Not modelled'}
                </button>
              </div>
              <p className="text-[11px] leading-snug text-slate-400 mt-2">
                Health is not one of the eight segments and Empirica does not model it.
                Switch it on to see the sensitivity — Empirica recommends modelling it,
                and a Health treaty slip, before FY2027.
              </p>
            </div>

            {/* discounting + onerousness */}
            <div className="rounded-xl border border-slate-200 bg-white p-4 space-y-2">
              {(['discounting', 'onerousTest'] as (keyof Levers)[]).map((k) => (
                <label key={k} className="flex items-center justify-between text-xs text-slate-600">
                  {k === 'discounting' ? 'Apply discounting' : 'Run onerousness test'}
                  <input type="checkbox" checked={levers[k] as boolean}
                         onChange={(e) => setLever(k, e.target.checked)}
                         className="accent-[#F4A623]" />
                </label>
              ))}
            </div>
          </aside>

          {/* ---- main ---- */}
          <main>
            {/* KPI row */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
              {kpis.map((k) => (
                <div key={k.label}
                     className="rounded-xl border bg-white p-3"
                     style={{ borderColor: k.hero ? ORANGE : '#E2E8F0' }}>
                  <p className="text-[11px] text-slate-500">{k.label}</p>
                  <p className="text-lg font-semibold tabular-nums"
                     style={{ color: k.v < 0 ? RED : NAVY }}>
                    {k.isPct ? pct(k.v) : k.v < 0 ? `(${mn(Math.abs(k.v))})` : mn(k.v)}
                    {!k.isPct && <span className="text-xs text-slate-400"> m</span>}
                  </p>
                </div>
              ))}
            </div>

            {/* JBB callout — the number that matters */}
            {c.jbbSensitivity !== 0 && (
              <div className="rounded-xl p-4 mb-4 flex items-start gap-3"
                   style={{ background: '#FDF3E3', border: `1px solid ${ORANGE}` }}>
                <ShieldAlert size={20} style={{ color: ORANGE }} className="mt-0.5 shrink-0" />
                <div className="text-sm">
                  <p className="font-semibold" style={{ color: NAVY }}>
                    JBB Motor commission at {pct(levers.jbbCommissionPct)} shows{' '}
                    {num(c.jbbSensitivity)} of additional commission.
                  </p>
                  <p className="text-slate-600 mt-0.5">
                    Sensitivity only. The report states this is <em>"not a bookable
                    adjustment, and it has not been recognised."</em> Booked at the 25%
                    provisional rate against a 24–41% sliding scale.
                  </p>
                </div>
              </div>
            )}

            {/* tabs */}
            <div className="flex gap-1 border-b border-slate-200 mb-4 overflow-x-auto">
              {TABS.map((t) => (
                <button key={t} onClick={() => setTab(t)}
                        className="text-sm px-3 py-2 border-b-2 whitespace-nowrap -mb-px"
                        style={tab === t
                          ? { borderColor: ORANGE, color: NAVY, fontWeight: 600 }
                          : { borderColor: 'transparent', color: '#94A3B8' }}>
                  {t}
                </button>
              ))}
            </div>

            {tab === 'Results' && <ResultsTab c={c} />}
            {tab === 'Statistics' && <TablesTab data={stats} loading="Loading statistics…" />}
            {tab === 'Statements' && <StatementsTab c={c} />}
            {tab === 'Reserves' && <ReservesTab c={c} />}
            {tab === 'Reinsurance' && <ReinsuranceTab c={c} />}
            {tab === 'MA bridge' && <BridgeTab bridge={bridge} />}
            {tab === 'Disclosures' && <DisclosuresTab disc={disc} />}
            {tab === 'Exceptions' && <ExceptionsTab />}
            {tab === 'Data quality' && <DataQualityTab disc={disc} />}

            {c.notes.length > 0 && (
              <div className="mt-4 rounded-lg bg-slate-100 p-3 text-xs text-slate-600 space-y-1">
                {c.notes.map((n, i) => <p key={i}>· {n}</p>)}
              </div>
            )}
          </main>
        </div>
      </div>
    </div>
  )
}

// ---- tabs ----------------------------------------------------------------

function Row({ label, v, bold, indent }: { label: string; v: number; bold?: boolean; indent?: boolean }) {
  return (
    <tr className={bold ? 'font-semibold' : ''}>
      <td className={`py-1.5 pr-3 ${indent ? 'pl-4 text-slate-500' : ''}`}>{label}</td>
      <td className="py-1.5 text-right tabular-nums" style={{ color: v < 0 ? RED : NAVY }}>
        {signed(v)}
      </td>
    </tr>
  )
}

function ResultsTab({ c }: { c: ReturnType<typeof compute> }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4">
      <table className="w-full text-sm">
        <tbody>
          <Row label="Insurance revenue" v={c.insuranceRevenue} />
          <Row label="Insurance service expenses" v={c.insuranceServiceExpenses} />
          <Row label="Insurance service result before reinsurance" v={c.serviceResultBeforeRI} bold />
          <Row label="Net income / (expense) from reinsurance held" v={c.netReinsuranceResult} />
          <Row label="Profit before tax" v={c.profitBeforeTax} bold />
        </tbody>
      </table>
    </div>
  )
}

function StatementsTab({ c }: { c: ReturnType<typeof compute> }) {
  return (
    <div className="grid md:grid-cols-2 gap-4">
      <div className="rounded-xl border border-slate-200 bg-white p-4">
        <h3 className="text-sm font-semibold mb-2" style={{ color: NAVY }}>Financial position</h3>
        <table className="w-full text-sm"><tbody>
          <Row label="Reinsurance contract assets" v={c.reinsuranceContractAssets} />
          <Row label="Insurance contract liabilities" v={c.insuranceContractLiabilities} />
          <Row label="  · liability for remaining coverage" v={c.lrc} indent />
          <Row label="  · liability for incurred claims" v={c.licBestEstimate} indent />
          <Row label="  · risk adjustment" v={c.licRiskAdjustment} indent />
          <Row label="Net IFRS 17 position" v={c.netIfrs17Liability} bold />
        </tbody></table>
      </div>
      <div className="rounded-xl border border-slate-200 bg-white p-4">
        <h3 className="text-sm font-semibold mb-2" style={{ color: NAVY }}>By segment</h3>
        <table className="w-full text-xs"><thead>
          <tr className="text-slate-400 text-left">
            <th className="py-1">Segment</th><th className="text-right">Premium</th>
            <th className="text-right">Claims</th><th className="text-right">Combined</th>
          </tr></thead><tbody>
          {c.bySegment.map((s) => (
            <tr key={s.id}>
              <td className="py-1">{s.name}</td>
              <td className="text-right tabular-nums">{num(s.premium)}</td>
              <td className="text-right tabular-nums">{num(s.claims)}</td>
              <td className="text-right tabular-nums"
                  style={{ color: s.combinedRatio > 1 ? RED : NAVY }}>{pct(s.combinedRatio)}</td>
            </tr>
          ))}
        </tbody></table>
      </div>
    </div>
  )
}

function ReservesTab({ c }: { c: ReturnType<typeof compute> }) {
  const rowSum = c.byUwy.reduce((a, r) => a + r.ibnr, 0)
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4">
      <h3 className="text-sm font-semibold mb-2" style={{ color: NAVY }}>Gross IBNR by underwriting year</h3>
      <table className="w-full text-xs"><thead>
        <tr className="text-slate-400 text-left">
          <th className="py-1">UWY</th><th className="text-right">Selected ultimate</th>
          <th className="text-right">Outstanding</th><th className="text-right">IBNR</th>
        </tr></thead><tbody>
        {c.byUwy.map((r) => (
          <tr key={r.uwy}>
            <td className="py-1">{r.uwy}</td>
            <td className="text-right tabular-nums">{num(r.ultimate)}</td>
            <td className="text-right tabular-nums">{num(r.outstanding)}</td>
            <td className="text-right tabular-nums">{num(r.ibnr)}</td>
          </tr>
        ))}
        <tr className="text-slate-400"><td className="py-1">Rows sum</td><td/><td/>
          <td className="text-right tabular-nums">{num(rowSum)}</td></tr>
        <tr className="font-semibold"><td className="py-1">Signed selection</td><td/><td/>
          <td className="text-right tabular-nums">{num(c.grossIbnr)}</td></tr>
      </tbody></table>
      <p className="text-[11px] text-slate-400 mt-2">
        The rows sum to {num(rowSum)} against a signed selection of {num(c.grossIbnr)} —
        a {num(c.grossIbnr - rowSum)} rounding difference inside the report (DQ-11). The
        signed selection is the valuation figure.
      </p>
    </div>
  )
}

function ReinsuranceTab({ c }: { c: ReturnType<typeof compute> }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4">
      <table className="w-full text-xs"><thead>
        <tr className="text-slate-400 text-left">
          <th className="py-1">Treaty</th><th className="text-right">Ceded premium</th>
          <th className="text-right">Commission</th><th className="text-right">Recovery</th>
          <th className="text-right">Net cost</th>
        </tr></thead><tbody>
        {c.byTreaty.map((t) => (
          <tr key={t.id}>
            <td className="py-1">{t.name}</td>
            <td className="text-right tabular-nums">{num(t.cededPremium)}</td>
            <td className="text-right tabular-nums">{num(t.commission)}</td>
            <td className="text-right tabular-nums">{num(t.directRecovery)}</td>
            <td className="text-right tabular-nums">{num(t.netPremiumCost)}</td>
          </tr>
        ))}
      </tbody></table>
    </div>
  )
}

function BridgeTab({ bridge }: { bridge: any }) {
  if (!bridge) return <p className="text-sm text-slate-400">Loading the bridge…</p>
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4">
      <h3 className="text-sm font-semibold mb-1" style={{ color: NAVY }}>
        IFRS 17 → Management Accounts (FY2025)
      </h3>
      <p className="text-xs text-slate-500 mb-3">
        Two figures that are both correct, walked step by step. The gap is real —
        the valuation does not carry investment income, other income or tax.
      </p>
      <table className="w-full text-sm"><tbody>
        {bridge.waterfall?.map((w: any, i: number) => (
          <tr key={i} className={i === 0 ? 'font-semibold' : ''}>
            <td className="py-1.5 pr-3">{w.label}</td>
            <td className="py-1.5 text-right tabular-nums text-slate-400">
              {i === 0 ? '' : signed(Number(w.amount))}
            </td>
            <td className="py-1.5 text-right tabular-nums" style={{ color: NAVY }}>
              {num(Number(w.running))}
            </td>
          </tr>
        ))}
      </tbody></table>
      {bridge.conflict && (
        <div className="mt-3 rounded-lg p-3 text-xs flex items-start gap-2"
             style={{ background: '#FDF3E3', border: `1px solid ${ORANGE}` }}>
          <AlertTriangle size={14} style={{ color: ORANGE }} className="mt-0.5 shrink-0" />
          <div>
            <p className="font-medium" style={{ color: NAVY }}>
              {num(Number(bridge.unexplained))} still unexplained.
            </p>
            <p className="text-slate-600 mt-0.5">{bridge.conflict.message}</p>
          </div>
        </div>
      )}
      <p className="text-[11px] text-slate-400 mt-2">
        Gross written premium ties to the frozen 125,148,692: {' '}
        <span style={{ color: bridge.gwp_check?.ties ? GREEN : RED }}>
          {bridge.gwp_check?.ties ? '✓' : '✗'}
        </span>
      </p>
    </div>
  )
}

function TablesTab({ data, loading }: { data: any; loading: string }) {
  if (!data) return <p className="text-sm text-slate-400">{loading}</p>
  const tables = data.tables || {}
  return (
    <div className="space-y-4">
      {Object.entries(tables).map(([key, t]: any) => (
        <div key={key} className="rounded-xl border border-slate-200 bg-white p-4">
          <div className="flex items-baseline justify-between mb-2">
            <h3 className="text-sm font-semibold" style={{ color: NAVY }}>{t.title}</h3>
            <span className="text-[11px] text-slate-400">{t.ref}</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs"><thead>
              <tr className="text-slate-400 text-left">
                {t.columns.map((col: string, i: number) => (
                  <th key={i} className={i === 0 ? 'py-1' : 'py-1 text-right'}>{col}</th>
                ))}
              </tr></thead><tbody>
              {t.rows.map((r: any[], ri: number) => (
                <tr key={ri} className="border-t border-slate-50">
                  {r.map((cell, ci) => (
                    <td key={ci} className={ci === 0 ? 'py-1 font-medium' : 'py-1 text-right tabular-nums'}>
                      {typeof cell === 'number'
                        ? (Math.abs(cell) < 5 && !Number.isInteger(cell) ? pct(cell) : num(cell))
                        : cell ?? ''}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody></table>
          </div>
          {t.note && <p className="text-[11px] text-slate-400 mt-2">{t.note}</p>}
        </div>
      ))}
    </div>
  )
}

function DisclosuresTab({ disc }: { disc: any }) {
  if (!disc) return <p className="text-sm text-slate-400">Loading disclosures…</p>
  const tables = disc.tables || {}
  return (
    <div className="space-y-4">
      {Object.entries(tables).map(([key, t]: any) => (
        <div key={key} className="rounded-xl border border-slate-200 bg-white p-4">
          <div className="flex items-baseline justify-between mb-2">
            <h3 className="text-sm font-semibold" style={{ color: NAVY }}>{t.title}</h3>
            <span className="text-[11px] text-slate-400">{t.ref}</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs"><thead>
              <tr className="text-slate-400 text-left">
                {t.columns.map((col: string, i: number) => (
                  <th key={i} className={i === 0 ? 'py-1' : 'py-1 text-right'}>{col}</th>
                ))}
              </tr></thead><tbody>
              {t.rows.map((r: any[], ri: number) => (
                <tr key={ri}>
                  {r.map((cell, ci) => (
                    <td key={ci} className={ci === 0 ? 'py-1' : 'py-1 text-right tabular-nums'}>
                      {typeof cell === 'number' ? num(cell) : cell ?? ''}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody></table>
          </div>
          {t.note && <p className="text-[11px] text-slate-400 mt-2">{t.note}</p>}
        </div>
      ))}
    </div>
  )
}

function DataQualityTab({ disc }: { disc: any }) {
  const t = disc?.tables?.data_quality
  if (!t) return <p className="text-sm text-slate-400">Open the Disclosures tab first to load these.</p>
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4">
      <div className="flex items-center gap-2 mb-3">
        <TrendingUp size={16} style={{ color: ORANGE }} />
        <h3 className="text-sm font-semibold" style={{ color: NAVY }}>{t.title}</h3>
      </div>
      <table className="w-full text-xs"><thead>
        <tr className="text-slate-400 text-left">
          {t.columns.map((col: string, i: number) => (
            <th key={i} className={i === 2 ? 'py-1 text-right' : 'py-1'}>{col}</th>
          ))}
        </tr></thead><tbody>
        {t.rows.map((r: any[], ri: number) => (
          <tr key={ri} className="border-t border-slate-50">
            <td className="py-1.5 font-medium">{r[0]}</td>
            <td className="py-1.5">{r[1]}</td>
            <td className="py-1.5 text-right tabular-nums">{num(Number(r[2]))}</td>
            <td className="py-1.5">
              <span className="text-[10px] px-1.5 py-0.5 rounded-full"
                    style={r[3] === 'high'
                      ? { background: '#FBE9E9', color: RED }
                      : r[3] === 'medium'
                      ? { background: '#FDF3E3', color: ORANGE }
                      : { background: '#EEF2F6', color: '#64748B' }}>{r[3]}</span>
            </td>
            <td className="py-1.5 text-slate-400">{r[4]}</td>
          </tr>
        ))}
      </tbody></table>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Exception register (CFO directive 2026-08-26)
//
// "Create a place where the finance team has to record exceptions, answers to
//  the exceptions. Just don't put everything — major ones only, small variance
//  ignore, work on the materiality."
//
// So the screen is built around that split, not around a flat list: the
// material band is the work, and it is the only thing that can be typed into.
// The watch and below-threshold bands are collapsed by default — present so
// nobody can say they were hidden, quiet so they are not mistaken for work.
// ---------------------------------------------------------------------------

type Exc = {
  id: string; ref: string; title: string; detail: string; source: string
  amount: number; band: string; band_display: string
  materiality_basis: string; materiality_reason: string
  status: string; status_display: string
  explanation: string; action: string
  explanation_required: boolean; is_outstanding: boolean
  answered_by: string; answered_at: string | null
  reviewed_by: string; reviewed_at: string | null; review_note: string
}

const STATUS_STYLE: Record<string, { bg: string; fg: string }> = {
  open:                 { bg: '#FBE9E9', fg: RED },
  returned:             { bg: '#FBE9E9', fg: RED },
  answered:             { bg: '#FDF3E3', fg: ORANGE },
  accepted:             { bg: '#E8F5EE', fg: GREEN },
  no_response_required: { bg: '#EEF2F6', fg: '#64748B' },
}

function ExceptionsTab() {
  const [rows, setRows] = useState<Exc[] | null>(null)
  const [summary, setSummary] = useState<any>(null)
  const [err, setErr] = useState<string>('')
  const [busy, setBusy] = useState(false)

  const load = async () => {
    const { apiFetch } = await import('@/lib/api')
    try {
      const d = await apiFetch<any>('/ifrs17/exceptions/?year=FY2026')
      setRows(d.exceptions); setSummary(d.summary); setErr('')
    } catch (e: any) { setErr(e?.message || 'Could not load the register.') }
  }
  useEffect(() => { load() }, [])

  const seed = async () => {
    setBusy(true)
    const { apiFetch } = await import('@/lib/api')
    try {
      await apiFetch('/ifrs17/exceptions/seed/', {
        method: 'POST', body: JSON.stringify({ year: 'FY2026' }),
        headers: { 'Content-Type': 'application/json' },
      })
      await load()
    } catch (e: any) { setErr(e?.message || 'Could not build the register.') }
    setBusy(false)
  }

  const download = async () => {
    const { downloadIfrs17ExceptionReport } = await import('@/lib/api')
    try { await downloadIfrs17ExceptionReport('FY2026') }
    catch (e: any) { setErr(e?.message || 'Download failed.') }
  }

  if (err && !rows) return <p className="text-sm" style={{ color: RED }}>{err}</p>
  if (!rows) return <p className="text-sm text-slate-400">Loading the exception register…</p>

  if (rows.length === 0) {
    return (
      <div className="rounded-xl border border-slate-200 bg-white p-8 text-center">
        <ClipboardList size={28} className="mx-auto mb-3" style={{ color: ORANGE }} />
        <h3 className="text-sm font-semibold mb-1" style={{ color: NAVY }}>
          The register has not been built yet
        </h3>
        <p className="text-xs text-slate-500 max-w-md mx-auto mb-4">
          Build it once and Omni creates a row for every exception the actuary
          disclosed, works out which ones are material, and asks Finance to
          explain only those.
        </p>
        <button onClick={seed} disabled={busy}
                className="text-xs font-medium px-4 py-2 rounded-lg disabled:opacity-50"
                style={{ background: NAVY, color: 'white' }}>
          {busy ? 'Building…' : 'Build the register'}
        </button>
      </div>
    )
  }

  const material = rows.filter((r) => r.band === 'material')
  const watch = rows.filter((r) => r.band === 'watch')
  const below = rows.filter((r) => r.band === 'immaterial')
  const m = summary?.materiality

  return (
    <div className="space-y-4">
      {err && <p className="text-xs" style={{ color: RED }}>{err}</p>}

      {/* the materiality rule, stated before any exception is read */}
      <div className="rounded-xl p-4" style={{ background: NAVY }}>
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <p className="text-[10px] uppercase tracking-widest mb-1"
               style={{ color: ORANGE }}>Materiality</p>
            <p className="text-sm text-white/90 leading-relaxed max-w-2xl">
              An exception is <span className="font-semibold text-white">material</span>{' '}
              at <span className="font-semibold text-white tabular-nums">
              {m ? num(Number(m.register_threshold)) : '—'}</span> or above —
              {m ? ` ${(Number(m.performance_materiality_pct) * 100).toFixed(0)}% of ${(Number(m.overall_materiality_pct) * 100).toFixed(1)}% of IFRS 17 insurance revenue` : ''},
              rounded down. Only material exceptions need an explanation. Small
              variances are disclosed and left alone.
            </p>
          </div>
          <button onClick={download}
                  className="text-xs font-medium px-3 py-2 rounded-lg flex items-center gap-1.5 shrink-0"
                  style={{ background: ORANGE, color: NAVY }}>
            <Download size={13} /> Exception report
          </button>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-4">
          {[
            ['Need an answer', summary?.outstanding, RED],
            ['Awaiting review', summary?.awaiting_review, ORANGE],
            ['Accepted', summary?.accepted, GREEN],
            ['Below threshold', (summary?.watch ?? 0) + (summary?.immaterial ?? 0), '#94A3B8'],
          ].map(([label, value, colour]: any) => (
            <div key={label}>
              <p className="text-2xl font-semibold tabular-nums" style={{ color: colour }}>
                {value ?? '—'}
              </p>
              <p className="text-[11px] text-white/60">{label}</p>
            </div>
          ))}
        </div>
      </div>

      <Section title={`Material — Finance must explain these (${material.length})`} open>
        <div className="space-y-2">
          {material.map((e) => <ExceptionCard key={e.id} e={e} onSaved={load} />)}
        </div>
      </Section>

      {watch.length > 0 && (
        <Section title={`Watch — monitored, no answer needed (${watch.length})`}>
          <QuietList items={watch} />
        </Section>
      )}
      {below.length > 0 && (
        <Section title={`Below threshold — disclosed and accepted (${below.length})`}>
          <QuietList items={below} />
        </Section>
      )}
    </div>
  )
}

function Section({ title, open = false, children }:
                 { title: string; open?: boolean; children: React.ReactNode }) {
  const [show, setShow] = useState(open)
  return (
    <div className="rounded-xl border border-slate-200 bg-white overflow-hidden">
      <button onClick={() => setShow((v) => !v)}
              className="w-full flex items-center gap-2 px-4 py-3 hover:bg-slate-50 transition-colors">
        {show ? <ChevronDown size={15} className="text-slate-400" />
              : <ChevronRight size={15} className="text-slate-400" />}
        <h3 className="text-sm font-semibold" style={{ color: NAVY }}>{title}</h3>
      </button>
      {show && <div className="px-4 pb-4">{children}</div>}
    </div>
  )
}

function QuietList({ items }: { items: Exc[] }) {
  return (
    <table className="w-full text-xs"><thead>
      <tr className="text-slate-400 text-left">
        <th className="py-1">Ref</th><th className="py-1">Exception</th>
        <th className="py-1 text-right">Amount</th><th className="py-1">Why it is not material</th>
      </tr></thead><tbody>
      {items.map((e) => (
        <tr key={e.id} className="border-t border-slate-50 align-top">
          <td className="py-1.5 font-medium whitespace-nowrap">{e.ref}</td>
          <td className="py-1.5">{e.title}</td>
          <td className="py-1.5 text-right tabular-nums">{num(Number(e.amount))}</td>
          <td className="py-1.5 text-slate-400">{e.materiality_reason}</td>
        </tr>
      ))}
    </tbody></table>
  )
}

function ExceptionCard({ e, onSaved }: { e: Exc; onSaved: () => void }) {
  const [open, setOpen] = useState(e.is_outstanding)
  const [explanation, setExplanation] = useState(e.explanation)
  const [action, setAction] = useState(e.action)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const st = STATUS_STYLE[e.status] || STATUS_STYLE.open
  const short = explanation.trim().length > 0 && explanation.trim().length < 80

  const post = async (path: string, body: any) => {
    setBusy(true); setErr('')
    const { apiFetch } = await import('@/lib/api')
    try {
      await apiFetch(path, { method: 'POST', body: JSON.stringify(body),
                             headers: { 'Content-Type': 'application/json' } })
      onSaved()
    } catch (ex: any) { setErr(ex?.message || 'Could not save.') }
    setBusy(false)
  }

  return (
    <div className="rounded-lg border border-slate-200">
      <button onClick={() => setOpen((v) => !v)}
              className="w-full text-left px-3 py-2.5 flex items-start gap-3 hover:bg-slate-50 transition-colors">
        <span className="text-xs font-semibold tabular-nums pt-0.5 shrink-0"
              style={{ color: NAVY }}>{e.ref}</span>
        <span className="flex-1 min-w-0">
          <span className="block text-sm" style={{ color: NAVY }}>{e.title}</span>
          <span className="block text-[11px] text-slate-400 mt-0.5">
            {e.source} · {e.materiality_basis === 'qualitative'
              ? 'material by nature' : 'above the threshold'}
          </span>
        </span>
        <span className="text-sm font-semibold tabular-nums shrink-0"
              style={{ color: NAVY }}>{num(Number(e.amount))}</span>
        <span className="text-[10px] px-2 py-0.5 rounded-full shrink-0 whitespace-nowrap"
              style={{ background: st.bg, color: st.fg }}>{e.status_display}</span>
      </button>

      {open && (
        <div className="px-3 pb-3 pt-1 border-t border-slate-100 space-y-3">
          <p className="text-xs text-slate-600 leading-relaxed">{e.detail}</p>
          <p className="text-[11px] text-slate-400 leading-relaxed">
            <span className="font-medium">Why this is material: </span>{e.materiality_reason}
          </p>

          <div>
            <label className="block text-[11px] font-medium mb-1" style={{ color: NAVY }}>
              What is this exception, and why is our position right?
            </label>
            <textarea value={explanation} onChange={(ev) => setExplanation(ev.target.value)}
                      rows={4} placeholder="Explain it for the auditors — at least 80 characters."
                      className="w-full text-xs rounded-lg border border-slate-200 p-2
                                 focus:outline-none focus:ring-2 focus:ring-offset-0"
                      style={{ ['--tw-ring-color' as any]: ORANGE }} />
            <p className="text-[10px] mt-1" style={{ color: short ? RED : '#94A3B8' }}>
              {explanation.trim().length} characters{short ? ' — at least 80 needed' : ''}
            </p>
          </div>

          <div>
            <label className="block text-[11px] font-medium mb-1" style={{ color: NAVY }}>
              What is being done about it, and by when?
            </label>
            <textarea value={action} onChange={(ev) => setAction(ev.target.value)}
                      rows={2}
                      className="w-full text-xs rounded-lg border border-slate-200 p-2
                                 focus:outline-none focus:ring-2"
                      style={{ ['--tw-ring-color' as any]: ORANGE }} />
          </div>

          {e.answered_by && (
            <p className="text-[10px] text-slate-400">
              Answered by {e.answered_by}
              {e.answered_at ? ` on ${new Date(e.answered_at).toLocaleDateString('en-GB')}` : ''}
              {e.reviewed_by ? ` · reviewed by ${e.reviewed_by}` : ''}
              {e.review_note ? ` — "${e.review_note}"` : ''}
            </p>
          )}

          {err && <p className="text-[11px]" style={{ color: RED }}>{err}</p>}

          <div className="flex flex-wrap items-center gap-2">
            <button disabled={busy || explanation.trim().length < 80}
                    onClick={() => post(`/ifrs17/exceptions/${e.id}/answer/`,
                                        { explanation, action })}
                    className="text-xs font-medium px-3 py-1.5 rounded-lg disabled:opacity-40"
                    style={{ background: NAVY, color: 'white' }}>
              {busy ? 'Saving…' : e.status === 'answered' ? 'Update the answer' : 'Save the answer'}
            </button>

            {e.status === 'answered' && (
              <>
                <input value={note} onChange={(ev) => setNote(ev.target.value)}
                       placeholder="Reviewer note"
                       className="text-xs rounded-lg border border-slate-200 px-2 py-1.5 flex-1 min-w-[140px]" />
                <button disabled={busy}
                        onClick={() => post(`/ifrs17/exceptions/${e.id}/review/`,
                                            { accept: true, note })}
                        className="text-xs font-medium px-3 py-1.5 rounded-lg flex items-center gap-1 disabled:opacity-40"
                        style={{ background: GREEN, color: 'white' }}>
                  <Check size={12} /> Accept
                </button>
                <button disabled={busy || !note.trim()}
                        onClick={() => post(`/ifrs17/exceptions/${e.id}/review/`,
                                            { accept: false, note })}
                        title={!note.trim() ? 'Say what is missing' : ''}
                        className="text-xs font-medium px-3 py-1.5 rounded-lg flex items-center gap-1 disabled:opacity-40"
                        style={{ background: '#FBE9E9', color: RED }}>
                  <Undo2 size={12} /> Return
                </button>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
