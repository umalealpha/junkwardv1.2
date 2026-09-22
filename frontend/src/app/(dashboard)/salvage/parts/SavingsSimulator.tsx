'use client'

/**
 * Savings Simulator — the what-if half of Veritas · Parts & Savings.
 *
 * CFO directive 2026-09-10, second pass: "gamify this feature, we should be
 * able to move things to reduce or increase the savings."
 *
 * Four levers, dragged by hand. Each one starts at what the loaded months
 * actually did, so the opening projection equals the real figure and every
 * pula of movement is the operator's own change:
 *
 *   1. Price-match share — how much of the parts bill the repairer matches on
 *      our pricing instead of us sourcing it. Moving spend across earns the
 *      handling-and-courier rate (lever 2).
 *   2. Handling saved on a matched part — what we keep per matched pula.
 *   3. Parts assessment discipline — parts saving as a share of quoted parts.
 *   4. Labour & paint discipline — the same for labour and paint.
 *
 * Nothing here writes anywhere. It is a calculator on top of figures already
 * loaded, and it says so on the card. Levers are remembered per browser only.
 */
import { useEffect, useMemo, useState } from 'react'
import { Gauge, Medal, RotateCcw, Sparkles, Target, TrendingDown, TrendingUp } from 'lucide-react'

export interface SimTimelineRow {
  month: string
  parts_total: string
  contract_pricing: string
  assessment_saving: string
}
export interface SimSavingsRow {
  month: string
  jobs: number
  quote_total: string
  saving_parts: string
  saving_labour: string
  saving_paint: string
  saving_total: string
}

interface Levers {
  matchShare: number      // % of the parts bill the repairer price-matches
  handlingRate: number    // % kept on every matched pula
  partsRate: number       // parts saving as % of quoted parts
  labourRate: number      // labour + paint saving as % of quoted labour + paint
}

const STORE_KEY = 'veritas_parts_sim_levers_v1'
const n = (v: string | number | null | undefined) => Number(v ?? 0)
const pula = (v: number) => Math.round(v).toLocaleString('en-BW')
const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))

export function SavingsSimulator({ theme, timeline, savings }: {
  theme: any
  timeline: SimTimelineRow[]
  savings: SimSavingsRow[]
}) {
  // ── the real numbers the levers start from ──────────────────────────────
  const base = useMemo(() => {
    const sourced  = timeline.reduce((a, t) => a + n(t.parts_total), 0)
    const matched  = timeline.reduce((a, t) => a + n(t.contract_pricing), 0)
    const partsBill = sourced + matched

    const quoted   = savings.reduce((a, s) => a + n(s.quote_total), 0)
    const savParts = savings.reduce((a, s) => a + n(s.saving_parts), 0)
    const savOther = savings.reduce((a, s) => a + n(s.saving_labour) + n(s.saving_paint), 0)
    const actual   = savings.reduce((a, s) => a + n(s.saving_total), 0)
    const jobs     = savings.reduce((a, s) => a + s.jobs, 0)

    // Quoted parts are not split out by the workbook, so the parts share of
    // the quote is taken from the savings split — the only honest proxy.
    const partsShareOfQuote = actual > 0 ? savParts / actual : 0.6
    const quotedParts = quoted * partsShareOfQuote
    const quotedOther = Math.max(quoted - quotedParts, 0)

    return {
      partsBill,
      matchShare: partsBill > 0 ? (matched / partsBill) * 100 : 0,
      quotedParts,
      quotedOther,
      partsRate: quotedParts > 0 ? (savParts / quotedParts) * 100 : 0,
      labourRate: quotedOther > 0 ? (savOther / quotedOther) * 100 : 0,
      actual,
      jobs,
      months: timeline.length,
    }
  }, [timeline, savings])

  // NOT rounded — a rounded start makes the opening projection miss the
  // recorded figure by a few thousand pula, which reads as a bug.
  const START: Levers = useMemo(() => ({
    matchShare:   base.matchShare,
    handlingRate: 4,
    partsRate:    base.partsRate,
    labourRate:   base.labourRate,
  }), [base])

  const [levers, setLevers] = useState<Levers>(START)
  const [loaded, setLoaded] = useState(false)

  // Restore this browser's levers once, then keep them in step.
  useEffect(() => {
    try {
      const saved = localStorage.getItem(STORE_KEY)
      if (saved) setLevers({ ...START, ...JSON.parse(saved) })
      else setLevers(START)
    } catch { setLevers(START) }
    setLoaded(true)
  }, [START])

  useEffect(() => {
    if (!loaded) return
    try { localStorage.setItem(STORE_KEY, JSON.stringify(levers)) } catch { /* private window */ }
  }, [levers, loaded])

  // ── the projection ──────────────────────────────────────────────────────
  const projected = useMemo(() => {
    const matched  = base.partsBill * (levers.matchShare / 100)
    const handling = matched * (levers.handlingRate / 100)
    const parts    = base.quotedParts * (levers.partsRate / 100)
    const other    = base.quotedOther * (levers.labourRate / 100)
    return {
      handling, parts, other,
      assessment: parts + other,          // the workbook's own measure
      total: handling + parts + other,    // plus the modelled handling
    }
  }, [base, levers])

  const baseline = useMemo(() => {
    const matched = base.partsBill * (START.matchShare / 100)
    return matched * (START.handlingRate / 100)
         + base.quotedParts * (START.partsRate / 100)
         + base.quotedOther * (START.labourRate / 100)
  }, [base, START])

  const delta   = projected.total - baseline
  const perMonth = base.months > 0 ? delta / base.months : 0
  const bestMonth = useMemo(
    () => savings.reduce((best, s) => (n(s.saving_total) > n(best?.saving_total ?? 0) ? s : best),
                         savings[0]),
    [savings])
  const target = n(bestMonth?.saving_total) * base.months
  const towardsTarget = target > 0 ? clamp((projected.assessment / target) * 100, 0, 140) : 0

  const badges = useMemo(() => {
    const earned: { label: string; hint: string; hit: boolean }[] = [
      { label: 'Price-matcher',
        hint:  'Half the parts bill matched by the repairer',
        hit:   levers.matchShare >= 50 },
      { label: 'Hard assessor',
        hint:  'A fifth off quoted parts',
        hit:   levers.partsRate >= 20 },
      { label: 'Hours negotiator',
        hint:  'A tenth off labour and paint',
        hit:   levers.labourRate >= 10 },
      { label: 'Beat the best month',
        hint:  `Projection above ${base.months} × the best month on record`,
        hit:   target > 0 && projected.assessment >= target },
    ]
    return earned
  }, [levers, projected, target, base.months])

  const won = badges.filter(b => b.hit).length
  const rising = delta >= 0

  return (
    <section className="rounded-xl overflow-hidden"
             style={{ background: theme.card, border: `1px solid ${theme.cardBdr}`, boxShadow: theme.cardSh }}>
      <header className="px-5 pt-5 pb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-display text-lg font-bold flex items-center gap-2"
              style={{ color: theme.navy }}>
            <Gauge className="w-4.5 h-4.5" style={{ color: theme.orangeText }} />
            Savings simulator
          </h2>
          <p className="text-xs mt-1 max-w-xl" style={{ color: theme.t3 }}>
            Drag a lever and watch the saving move. Everything starts exactly where
            the loaded months actually landed, so any change you see is yours.
            This is a calculator — it changes no record and no money.
          </p>
        </div>
        <button type="button" onClick={() => setLevers(START)}
                className="inline-flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-md"
                style={{ background: theme.g100, color: theme.t2, border: `1px solid ${theme.cardBdr}` }}>
          <RotateCcw className="w-3.5 h-3.5" /> Back to actual
        </button>
      </header>

      <div className="grid lg:grid-cols-[1.15fr_1fr] gap-0">
        {/* levers */}
        <div className="px-5 pb-5 space-y-4">
          <Lever theme={theme} label="Parts the repairer price-matches"
                 help="Contract pricing instead of us sourcing the part."
                 value={levers.matchShare} start={START.matchShare} min={0} max={100} step={1}
                 unit="%" onChange={v => setLevers(l => ({ ...l, matchShare: v }))} />
          <Lever theme={theme} label="Handling kept on a matched pula"
                 help="Courier, handling and margin we do not spend when the repairer supplies it."
                 value={levers.handlingRate} start={START.handlingRate} min={0} max={15} step={0.5}
                 unit="%" onChange={v => setLevers(l => ({ ...l, handlingRate: v }))} />
          <Lever theme={theme} label="Off quoted parts, at assessment"
                 help="How hard the assessment cuts the repairer's parts line."
                 value={levers.partsRate} start={START.partsRate} min={0} max={45} step={0.5}
                 unit="%" onChange={v => setLevers(l => ({ ...l, partsRate: v }))} />
          <Lever theme={theme} label="Off quoted labour and paint"
                 help="Hours and paint hours agreed down against the assessment."
                 value={levers.labourRate} start={START.labourRate} min={0} max={45} step={0.5}
                 unit="%" onChange={v => setLevers(l => ({ ...l, labourRate: v }))} />
        </div>

        {/* scoreboard */}
        <div className="px-5 pb-5 lg:pt-0 lg:border-l lg:pl-6"
             style={{ borderColor: theme.cardBdr }}>
          <div className="rounded-lg p-4 mb-4" style={{ background: theme.g50, border: `1px solid ${theme.cardBdr}` }}>
            <div className="text-[10px] uppercase tracking-wider font-semibold mb-1"
                 style={{ color: theme.t3 }}>
              Assessment savings · {base.months} month{base.months === 1 ? '' : 's'}
            </div>
            <div className="font-display-tight text-3xl font-bold tabular-nums leading-none"
                 style={{ color: theme.navy }}>
              P {pula(projected.assessment)}
            </div>
            <div className="text-[11px] mt-1.5 tabular-nums" style={{ color: theme.t3 }}>
              {Math.abs(projected.assessment - base.actual) < 1
                ? <>Exactly the P {pula(base.actual)} the workbooks recorded.</>
                : <>Recorded: P {pula(base.actual)}.</>}
            </div>
            <div className="mt-2 inline-flex items-center gap-1.5 text-xs font-semibold px-2 py-1 rounded-md tabular-nums"
                 style={{
                   background: Math.abs(delta) < 1 ? theme.g100 : rising ? theme.okB : theme.erB,
                   color:      Math.abs(delta) < 1 ? theme.t2   : rising ? theme.ok  : theme.er,
                 }}>
              {Math.abs(delta) < 1
                ? 'Levers sitting on what actually happened'
                : <>
                    {rising ? <TrendingUp className="w-3.5 h-3.5" /> : <TrendingDown className="w-3.5 h-3.5" />}
                    {rising ? '+' : '−'}P {pula(Math.abs(delta))} · P {pula(Math.abs(perMonth))}/month
                  </>}
            </div>
            <ul className="mt-3 space-y-1 text-[11px]" style={{ color: theme.t2 }}>
              <li className="flex justify-between tabular-nums">
                <span>Off quoted parts</span><span>P {pula(projected.parts)}</span>
              </li>
              <li className="flex justify-between tabular-nums">
                <span>Off labour and paint</span><span>P {pula(projected.other)}</span>
              </li>
              <li className="flex justify-between tabular-nums pt-1 mt-1 border-t"
                  style={{ borderColor: theme.cardBdr }}>
                <span>
                  Handling kept on matched parts
                  <em className="not-italic block text-[10px]" style={{ color: theme.t3 }}>
                    modelled — the workbook does not measure this
                  </em>
                </span>
                <span>P {pula(projected.handling)}</span>
              </li>
              <li className="flex justify-between tabular-nums font-bold pt-1"
                  style={{ color: theme.navy }}>
                <span>Total with handling</span><span>P {pula(projected.total)}</span>
              </li>
            </ul>
          </div>

          {target > 0 && (
            <div className="mb-4">
              <div className="flex items-baseline justify-between mb-1.5">
                <span className="text-[11px] font-semibold inline-flex items-center gap-1.5"
                      style={{ color: theme.t2 }}>
                  <Target className="w-3.5 h-3.5" style={{ color: theme.orangeText }} />
                  Best month on record, every month
                </span>
                <span className="text-[11px] font-bold tabular-nums" style={{ color: theme.navy }}>
                  {towardsTarget.toFixed(0)}%
                </span>
              </div>
              <div className="h-2.5 rounded-full overflow-hidden relative" style={{ background: theme.g200 }}>
                <div className="h-full rounded-full transition-[width] duration-300"
                     style={{
                       width: `${clamp(towardsTarget, 0, 100)}%`,
                       background: towardsTarget >= 100
                         ? `linear-gradient(90deg, ${theme.ok}, ${theme.teal})`
                         : `linear-gradient(90deg, ${theme.navy}, ${theme.orange})`,
                     }} />
              </div>
              <p className="text-[10px] mt-1.5" style={{ color: theme.t3 }}>
                Target P {pula(target)} — {bestMonth ? new Date(`${bestMonth.month}T00:00:00`)
                  .toLocaleDateString('en-BW', { month: 'long' }) : '—'} repeated {base.months} times.
              </p>
            </div>
          )}

          <div className="flex items-center gap-2 mb-2">
            <Sparkles className="w-3.5 h-3.5" style={{ color: theme.orangeText }} />
            <span className="text-[11px] font-bold uppercase tracking-wider" style={{ color: theme.t2 }}>
              {won} of {badges.length} unlocked
            </span>
          </div>
          <div className="flex flex-wrap gap-2">
            {badges.map(b => (
              <span key={b.label} title={b.hint}
                    className="inline-flex items-center gap-1.5 text-[11px] font-semibold px-2.5 py-1.5 rounded-lg"
                    style={{
                      background: b.hit ? theme.okB : theme.g100,
                      color:      b.hit ? theme.ok  : theme.t3,
                      border:     `1px solid ${b.hit ? `${theme.ok}44` : theme.cardBdr}`,
                    }}>
                <Medal className="w-3.5 h-3.5" /> {b.label}
              </span>
            ))}
          </div>
        </div>
      </div>
    </section>
  )
}

function Lever({ theme, label, help, value, start, min, max, step, unit, onChange }: {
  theme: any; label: string; help: string; value: number; start: number
  min: number; max: number; step: number; unit: string; onChange: (v: number) => void
}) {
  const pct = ((value - min) / (max - min)) * 100
  const startPct = ((start - min) / (max - min)) * 100
  const moved = Math.abs(value - start) > 0.01
  return (
    <div>
      <div className="flex items-baseline justify-between gap-3 mb-1">
        <label className="text-sm font-semibold" style={{ color: theme.text }}>
          {label}
        </label>
        <span className="text-sm font-bold tabular-nums shrink-0"
              style={{ color: moved ? theme.orangeText : theme.navy }}>
          {value.toFixed(step < 1 ? 1 : 0)}{unit}
          {moved && (
            <span className="ml-1.5 text-[10px] font-medium" style={{ color: theme.t3 }}>
              was {start.toFixed(step < 1 ? 1 : 0)}{unit}
            </span>
          )}
        </span>
      </div>
      <div className="relative">
        <input type="range" min={min} max={max} step={step} value={value}
               aria-label={`${label} — ${help}`}
               onChange={e => onChange(Number(e.target.value))}
               className="w-full appearance-none bg-transparent cursor-pointer relative z-10
                          [&::-webkit-slider-thumb]:appearance-none
                          [&::-webkit-slider-thumb]:w-4 [&::-webkit-slider-thumb]:h-4
                          [&::-webkit-slider-thumb]:rounded-full
                          [&::-webkit-slider-thumb]:border-2
                          [&::-webkit-slider-thumb]:shadow
                          [&::-moz-range-thumb]:w-4 [&::-moz-range-thumb]:h-4
                          [&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:border-2"
               style={{
                 // The thumb picks these up through currentColor / accent-color.
                 accentColor: theme.orange,
                 color: theme.orange,
               }} />
        <div className="absolute left-0 right-0 top-1/2 -translate-y-1/2 h-1.5 rounded-full pointer-events-none"
             style={{ background: theme.g200 }}>
          <div className="h-full rounded-full"
               style={{ width: `${pct}%`, background: `linear-gradient(90deg, ${theme.navy}, ${theme.orange})` }} />
          <span className="absolute top-1/2 -translate-y-1/2 w-0.5 h-3 rounded"
                style={{ left: `${startPct}%`, background: theme.t3, opacity: 0.6 }} />
        </div>
      </div>
      <p className="text-[10px] mt-1.5" style={{ color: theme.t3 }}>{help}</p>
    </div>
  )
}
