'use client'

/**
 * /hris/assess — Performance Management System (PMS) engine.
 *
 * CFO directive 2026-05-18 (Unami audit closeout): build the full 1-4
 * scale assessment with Competencies (80%), Values (20%), Potential
 * dimensions, and unlimited OKRs / KPIs.
 *
 * This page is the entry point — managers see their direct reports here
 * and pick one to assess. The actual form lives on a row-expansion
 * panel because building a dedicated dynamic-route page for a JSON
 * blob is overkill while we land Unami's spec.
 */
import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import {
  ChevronLeft, Plus, Trash2, Send, Info, AlertCircle, CheckCircle2,
  Award, TrendingUp, Loader2,
} from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { useHrisAccess, useHrisCan } from '@/hooks/useHrisAccess'
import { authedHrisFetch, fetchHrisEmployees, type HrisEmployee } from '../_shared'

const RATING_LABELS = ['—', 'Below', 'Developing', 'Meets', 'Exceeds'] as const

const TALENT_SEGMENTS = [
  ['star',              'Star (HP-HP)'],
  ['high_potential',    'High potential'],
  ['solid_performer',   'Solid performer'],
  ['specialist',        'Technical specialist'],
  ['developing',        'Developing'],
  ['underperforming',   'Underperforming'],
  ['new_hire',          'New hire (<6m)'],
] as const

const DEFAULT_COMPETENCIES = [
  'Communication', 'Execution', 'Customer focus', 'Collaboration',
  'Technical depth', 'Decision making',
]
const DEFAULT_VALUES = [
  'Integrity', 'Accountability', 'Pula-first',
  'Continuous improvement', 'One Alpha',
]
const POTENTIAL_DIMS = [
  ['learning_agility',    'Learning agility'],
  ['leadership_capacity', 'Leadership capacity'],
  ['aspiration_drive',    'Aspiration & drive'],
  ['adaptability',        'Adaptability'],
] as const

interface OkrRow { name: string; weight: number; h1: number; h2: number }

function defaultPeriod(): string {
  const d = new Date()
  const half = d.getMonth() < 6 ? 'H1' : 'H2'
  const fy = d.getMonth() >= 6 ? d.getFullYear() + 1 : d.getFullYear()
  return `FY${String(fy).slice(2)}-${half}`
}

export default function HrisAssessPage() {
  const router = useRouter()
  const { theme } = useTheme()
  const allowed = useHrisAccess()
  const canAssess = useHrisCan('assess_team')
  const [employees, setEmployees] = useState<HrisEmployee[]>([])
  const [loading, setLoading] = useState(true)
  const [activeId, setActiveId] = useState<number | null>(null)

  const [period, setPeriod] = useState(defaultPeriod())
  const [competencies, setCompetencies] = useState<Record<string, number>>({})
  const [values, setValues] = useState<Record<string, number>>({})
  const [potential, setPotential] = useState<Record<string, number>>({})
  const [okrs, setOkrs] = useState<OkrRow[]>([
    { name: '', weight: 100, h1: 0, h2: 0 },
  ])
  const [narrative, setNarrative] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<{ ok: boolean; msg: string; score?: number } | null>(null)

  // 9-box calibration state
  const [calibSeg, setCalibSeg] = useState<string>('')
  const [calibRationale, setCalibRationale] = useState('')
  const [calibBusy, setCalibBusy] = useState(false)
  const [calibResult, setCalibResult] = useState<{ ok: boolean; msg: string } | null>(null)

  useEffect(() => {
    if (allowed === false) router.replace('/dashboard')
  }, [allowed, router])

  useEffect(() => {
    if (allowed !== true) return
    setLoading(true)
    fetchHrisEmployees()
      .then(r => setEmployees(r.employees || []))
      .finally(() => setLoading(false))
  }, [allowed])

  const totalOkrWeight = useMemo(
    () => okrs.reduce((s, o) => s + (Number(o.weight) || 0), 0),
    [okrs],
  )

  const compAvg = useMemo(() => {
    const xs = Object.values(competencies).filter(v => v > 0)
    return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0
  }, [competencies])
  const valAvg = useMemo(() => {
    const xs = Object.values(values).filter(v => v > 0)
    return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0
  }, [values])
  const composite = compAvg * 0.8 + valAvg * 0.2

  function resetForm() {
    setCompetencies({})
    setValues({})
    setPotential({})
    setOkrs([{ name: '', weight: 100, h1: 0, h2: 0 }])
    setNarrative('')
    setResult(null)
  }

  async function submit() {
    if (!activeId) return
    setBusy(true)
    setResult(null)
    try {
      const r = await authedHrisFetch('/hris/api/assessments/', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          employee_id:  activeId,
          period,
          competencies,
          values:       Object.values(values),
          potential,
          okrs: okrs
            .filter(o => o.name.trim())
            .map(o => ({
              name: o.name.trim(),
              weight_pct: o.weight,
              score_h1: o.h1,
              score_h2: o.h2,
            })),
          narrative,
        }),
      })
      const data = await r.json().catch(() => ({}))
      if (r.ok) {
        setResult({
          ok: true,
          msg: data.message || 'Assessment recorded.',
          score: data.composite_score,
        })
        resetForm()
      } else {
        setResult({ ok: false, msg: data.detail || `HTTP ${r.status}` })
      }
    } catch (err) {
      setResult({ ok: false, msg: err instanceof Error ? err.message : 'Network error' })
    } finally {
      setBusy(false)
    }
  }

  async function submitCalibration() {
    const active = employees.find(e => e.id === activeId)
    if (!active?.pid || !calibSeg) {
      setCalibResult({ ok: false, msg: 'Pick a segment first.' }); return
    }
    setCalibBusy(true); setCalibResult(null)
    try {
      const r = await authedHrisFetch('/hris/api/calibrate-talent/', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          profile_id:     active.pid,
          talent_segment: calibSeg,
          rationale:      calibRationale,
        }),
      })
      const data = await r.json().catch(() => ({}))
      if (!r.ok) { setCalibResult({ ok: false, msg: data.detail || `HTTP ${r.status}` }); return }
      setCalibResult({ ok: true, msg: `Calibrated ${data.employee} → ${data.talent_segment_new}.` })
    } catch (err) {
      setCalibResult({ ok: false, msg: err instanceof Error ? err.message : 'Network error' })
    } finally {
      setCalibBusy(false)
    }
  }

  if (allowed !== true) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ backgroundColor: theme.bg }}>
        <div className="w-8 h-8 border-2 border-[#F07F00] border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar title="Performance Assessment" breadcrumbs={[{ label: 'HRIS', href: '/hris' }, { label: 'Assess' }]} />
      <main className="flex-1 overflow-y-auto p-6 space-y-5">
        <Link href="/hris" className="inline-flex items-center gap-1.5 text-sm font-medium" style={{ color: theme.t2 }}>
          <ChevronLeft className="w-4 h-4" /> Back to HRIS
        </Link>

        <div className="rounded-2xl p-4" style={{ background: theme.oL, border: `1px solid ${theme.orange}33` }}>
          <p className="text-xs leading-relaxed" style={{ color: theme.text }}>
            <strong>1-4 PMS scale</strong> per Alpha Direct spec: 1 Below · 2 Developing · 3 Meets · 4 Exceeds.
            Composite = Competencies (<strong>80%</strong>) + Values (<strong>20%</strong>). OKRs / KPIs scored
            twice (H1 + H2) and weighted to 100%.
            {canAssess === false && (
              <span className="block mt-2 font-semibold" style={{ color: theme.er }}>
                Your current role cannot submit assessments — read-only view.
              </span>
            )}
          </p>
        </div>

        {/* Employee picker */}
        <div className="rounded-2xl p-4"
             style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
          <h3 className="font-semibold mb-3" style={{ color: theme.text }}>Pick a team member</h3>
          {loading ? (
            <div className="py-6 text-center text-sm" style={{ color: theme.t2 }}>Loading…</div>
          ) : (
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2 max-h-64 overflow-y-auto">
              {employees.map(e => (
                <button key={e.id} type="button" onClick={() => { setActiveId(e.id); setResult(null) }}
                        className="px-3 py-2 rounded-lg text-left transition-all"
                        style={{
                          background: activeId === e.id ? theme.oL : theme.g100,
                          border: `1px solid ${activeId === e.id ? theme.orange + '55' : theme.cardBdr}`,
                        }}>
                  <div className="text-sm font-medium truncate" style={{ color: theme.text }}>{e.nm}</div>
                  <div className="text-[11px] truncate" style={{ color: theme.t2 }}>{e.ps || '—'} · {e.dp || '—'}</div>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Active assessment form */}
        {activeId !== null && (
          <div className="rounded-2xl p-5 space-y-5"
               style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
            <div className="flex items-center justify-between gap-4 flex-wrap">
              <div>
                <h3 className="font-semibold" style={{ color: theme.text }}>
                  Assess {employees.find(e => e.id === activeId)?.nm}
                </h3>
                <p className="text-xs" style={{ color: theme.t2 }}>
                  Period (e.g. FY26-H1): editable below
                </p>
              </div>
              <input value={period} onChange={e => setPeriod(e.target.value)}
                     className="px-3 py-2 rounded-lg text-sm outline-none"
                     style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }} />
            </div>

            <SectionScores
              title="Competencies (80%)"
              icon={TrendingUp}
              keys={DEFAULT_COMPETENCIES}
              scores={competencies}
              setScores={setCompetencies}
              theme={theme}
            />
            <SectionScores
              title="Values (20%)"
              icon={Award}
              keys={DEFAULT_VALUES}
              scores={values}
              setScores={setValues}
              theme={theme}
            />
            <SectionScores
              title="Potential dimensions"
              icon={Award}
              keys={POTENTIAL_DIMS.map(([k]) => k)}
              labels={POTENTIAL_DIMS.map(([, label]) => label)}
              scores={potential}
              setScores={setPotential}
              theme={theme}
            />

            {/* OKRs */}
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <h4 className="font-semibold text-sm" style={{ color: theme.text }}>OKRs / KPIs</h4>
                <div className="flex items-center gap-3">
                  <span className="text-xs tabular-nums"
                        style={{ color: totalOkrWeight === 100 ? theme.ok : theme.wr }}>
                    Σ weight: {totalOkrWeight}%
                  </span>
                  <button type="button"
                          onClick={() => setOkrs([...okrs, { name: '', weight: 0, h1: 0, h2: 0 }])}
                          className="inline-flex items-center gap-1 text-xs font-semibold"
                          style={{ color: theme.orange }}>
                    <Plus className="w-3.5 h-3.5" /> Add OKR
                  </button>
                </div>
              </div>
              <div className="space-y-2">
                {okrs.map((o, idx) => (
                  <div key={idx} className="grid grid-cols-12 gap-2 items-center">
                    <input
                      placeholder="OKR / KPI name"
                      value={o.name}
                      onChange={e => {
                        const next = [...okrs]; next[idx] = { ...o, name: e.target.value }; setOkrs(next)
                      }}
                      className="col-span-6 px-2 py-1.5 rounded-md text-sm outline-none"
                      style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }} />
                    <input
                      type="number" min={0} max={100} placeholder="weight"
                      value={o.weight}
                      onChange={e => {
                        const next = [...okrs]; next[idx] = { ...o, weight: Number(e.target.value) }; setOkrs(next)
                      }}
                      className="col-span-2 px-2 py-1.5 rounded-md text-sm outline-none tabular-nums"
                      style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }} />
                    <input
                      type="number" step={0.1} min={1} max={4} placeholder="H1"
                      value={o.h1}
                      onChange={e => {
                        const next = [...okrs]; next[idx] = { ...o, h1: Number(e.target.value) }; setOkrs(next)
                      }}
                      className="col-span-1 px-2 py-1.5 rounded-md text-sm outline-none tabular-nums"
                      style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }} />
                    <input
                      type="number" step={0.1} min={1} max={4} placeholder="H2"
                      value={o.h2}
                      onChange={e => {
                        const next = [...okrs]; next[idx] = { ...o, h2: Number(e.target.value) }; setOkrs(next)
                      }}
                      className="col-span-1 px-2 py-1.5 rounded-md text-sm outline-none tabular-nums"
                      style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }} />
                    <button type="button"
                            onClick={() => setOkrs(okrs.filter((_, i) => i !== idx))}
                            className="col-span-2 inline-flex items-center justify-center gap-1 text-xs"
                            style={{ color: theme.er }}>
                      <Trash2 className="w-3.5 h-3.5" /> Remove
                    </button>
                  </div>
                ))}
              </div>
            </div>

            <div>
              <label className="block text-xs font-medium mb-1" style={{ color: theme.t2 }}>
                Manager narrative
              </label>
              <textarea rows={3} value={narrative} onChange={e => setNarrative(e.target.value)}
                        placeholder="Optional — strengths, growth areas, calibration notes"
                        className="w-full px-3 py-2 rounded-lg text-sm outline-none resize-y"
                        style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }} />
            </div>

            <div className="flex flex-wrap items-center justify-between gap-3 pt-2">
              <div className="text-xs tabular-nums" style={{ color: theme.t2 }}>
                Comp avg <strong style={{ color: theme.text }}>{compAvg.toFixed(2)}</strong>
                {' · '}
                Val avg <strong style={{ color: theme.text }}>{valAvg.toFixed(2)}</strong>
                {' · '}
                Composite <strong style={{ color: theme.orange }}>{composite.toFixed(2)}</strong> / 4
              </div>
              <button type="button" onClick={submit} disabled={busy || !canAssess}
                      className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold transition-opacity disabled:opacity-50"
                      style={{ background: theme.orange, color: '#fff' }}>
                {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
                {busy ? 'Submitting…' : 'Submit assessment'}
              </button>
            </div>

            {result && (
              <div className="rounded-lg px-3 py-2 text-sm flex items-start gap-2"
                   style={{
                     background: result.ok ? theme.okB : theme.erB,
                     color:      result.ok ? theme.ok  : theme.er,
                     border:     `1px solid ${result.ok ? theme.ok : theme.er}40`,
                   }}>
                {result.ok ? <CheckCircle2 className="w-4 h-4 flex-shrink-0 mt-0.5" /> :
                              <AlertCircle  className="w-4 h-4 flex-shrink-0 mt-0.5" />}
                <div>
                  {result.msg}
                  {result.ok && result.score !== undefined && (
                    <span className="ml-2 font-bold">composite {result.score.toFixed(2)}/4</span>
                  )}
                </div>
              </div>
            )}

            {/* 9-box talent calibration — separate write to HRISProfile.talent_segment */}
            <div className="rounded-xl p-4 mt-3" style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}` }}>
              <div className="flex items-center justify-between mb-2">
                <h4 className="font-semibold text-sm inline-flex items-center gap-1.5" style={{ color: theme.text }}>
                  <Award className="w-4 h-4" style={{ color: theme.orange }} /> 9-Box calibration
                </h4>
                <span className="text-[11px]" style={{ color: theme.t2 }}>
                  Current: {employees.find(e => e.id === activeId)?.segment || '—'}
                </span>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label className="block text-[11px] font-medium mb-1" style={{ color: theme.t2 }}>Talent segment</label>
                  <select value={calibSeg} onChange={e => setCalibSeg(e.target.value)}
                          className="w-full px-3 py-2 rounded-md text-sm outline-none"
                          style={{ background: theme.card, border: `1px solid ${theme.cardBdr}`, color: theme.text }}>
                    <option value="">Pick a segment…</option>
                    {TALENT_SEGMENTS.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
                  </select>
                </div>
                <div>
                  <label className="block text-[11px] font-medium mb-1" style={{ color: theme.t2 }}>Rationale</label>
                  <input value={calibRationale} onChange={e => setCalibRationale(e.target.value)}
                         placeholder="Optional — calibration note (≤160 chars)"
                         className="w-full px-3 py-2 rounded-md text-sm outline-none"
                         style={{ background: theme.card, border: `1px solid ${theme.cardBdr}`, color: theme.text }} />
                </div>
              </div>
              <div className="flex justify-end mt-3">
                <button type="button" onClick={submitCalibration} disabled={calibBusy || !calibSeg}
                        className="inline-flex items-center gap-2 px-3 py-1.5 rounded-md text-xs font-semibold transition-opacity disabled:opacity-50"
                        style={{ background: theme.orange, color: '#fff' }}>
                  {calibBusy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Send className="w-3.5 h-3.5" />}
                  Calibrate
                </button>
              </div>
              {calibResult && (
                <div className="mt-2 rounded-md px-3 py-1.5 text-xs flex items-start gap-2"
                     style={{
                       background: calibResult.ok ? theme.okB : theme.erB,
                       color:      calibResult.ok ? theme.ok  : theme.er,
                       border:     `1px solid ${calibResult.ok ? theme.ok : theme.er}40`,
                     }}>
                  {calibResult.ok ? <CheckCircle2 className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" /> :
                                    <AlertCircle  className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />}
                  <div>{calibResult.msg}</div>
                </div>
              )}
            </div>
          </div>
        )}
      </main>
    </div>
  )
}

interface SectionScoresProps {
  title: string
  icon: any
  keys: readonly string[]
  labels?: readonly string[]
  scores: Record<string, number>
  setScores: (s: Record<string, number>) => void
  theme: any
}

function SectionScores({ title, icon: Icon, keys, labels, scores, setScores, theme }: SectionScoresProps) {
  return (
    <div className="space-y-2">
      <h4 className="font-semibold text-sm inline-flex items-center gap-1.5" style={{ color: theme.text }}>
        <Icon className="w-4 h-4" style={{ color: theme.orange }} />
        {title}
      </h4>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
        {keys.map((k, i) => {
          const label = labels?.[i] || k
          const v = scores[k] || 0
          return (
            <div key={k} className="flex items-center gap-2">
              <span className="flex-1 text-sm" style={{ color: theme.text }}>{label}</span>
              {[1, 2, 3, 4].map(n => (
                <button key={n} type="button"
                        onClick={() => setScores({ ...scores, [k]: n })}
                        className="w-7 h-7 rounded-full text-xs font-bold transition-all"
                        style={{
                          background: v === n ? theme.orange : theme.g100,
                          color:      v === n ? '#fff'       : theme.t2,
                          border:    `1px solid ${v === n ? theme.orange : theme.cardBdr}`,
                        }}
                        title={RATING_LABELS[n]}>
                  {n}
                </button>
              ))}
            </div>
          )
        })}
      </div>
    </div>
  )
}
