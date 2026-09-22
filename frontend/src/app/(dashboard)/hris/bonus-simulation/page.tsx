'use client'

/**
 * /hris/bonus-simulation — 3-layer bonus pool calculator.
 *
 * CFO directive 2026-05-18 (Unami audit closeout):
 *   bonus = org_multiplier × dept_multiplier × (individual_score / 4) × target
 *
 * The client computes it locally so the page is responsive while typing,
 * and (optionally) cross-checks against POST /hris/api/bonus-simulate/.
 */
import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { ChevronLeft, Calculator, Info, ChevronRight, Wallet } from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { useHrisAccess } from '@/hooks/useHrisAccess'

function fmtP(n: number): string {
  return `BWP ${new Intl.NumberFormat('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n || 0)}`
}

export default function HrisBonusSimulationPage() {
  const router = useRouter()
  const { theme } = useTheme()
  const allowed = useHrisAccess()

  const [orgMult, setOrgMult] = useState(1.10)
  const [deptMult, setDeptMult] = useState(0.95)
  const [individual, setIndividual] = useState(3.5)
  const [target, setTarget] = useState(15000)

  useEffect(() => {
    if (allowed === false) router.replace('/dashboard')
  }, [allowed, router])

  const layers = useMemo(() => {
    const orgLayer  = orgMult * target
    const deptLayer = orgMult * deptMult * target
    const indLayer  = orgMult * deptMult * (individual / 4) * target
    return { orgLayer, deptLayer, indLayer }
  }, [orgMult, deptMult, individual, target])

  if (allowed !== true) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ backgroundColor: theme.bg }}>
        <div className="w-8 h-8 border-2 border-[#F07F00] border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar title="Bonus Simulation" breadcrumbs={[{ label: 'HRIS', href: '/hris' }, { label: 'Bonus' }]} />
      <main className="flex-1 overflow-y-auto p-6 space-y-5">
        <Link href="/hris" className="inline-flex items-center gap-1.5 text-sm font-medium" style={{ color: theme.t2 }}>
          <ChevronLeft className="w-4 h-4" /> Back to HRIS
        </Link>

        <div className="rounded-2xl p-4" style={{ background: theme.oL, border: `1px solid ${theme.orange}33` }}>
          <p className="text-xs leading-relaxed" style={{ color: theme.text }}>
            <strong>Formula:</strong> <code>org × dept × (individual / 4) × target</code>.
            Org and Dept multipliers come from EXCO's annual rating; individual score is the PMS
            composite on the 1-4 scale; target is the bonus the role pays at a 4/4 score in a flat year.
          </p>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
          {/* Inputs */}
          <div className="rounded-2xl p-5 space-y-4"
               style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
            <div className="flex items-center gap-2">
              <Calculator className="w-4 h-4" style={{ color: theme.orange }} />
              <h3 className="font-semibold" style={{ color: theme.text }}>Inputs</h3>
            </div>
            <NumberRow label="Org performance multiplier" hint="EXCO sets this annually. e.g. 1.10 = +10%"
                       value={orgMult} onChange={setOrgMult} step={0.05} min={0} max={3} suffix="×" theme={theme} />
            <NumberRow label="Department multiplier" hint="Dept head sets this. e.g. 0.95 = −5%"
                       value={deptMult} onChange={setDeptMult} step={0.05} min={0} max={3} suffix="×" theme={theme} />
            <NumberRow label="Individual PMS score" hint="1-4 scale (1 Below … 4 Exceeds)"
                       value={individual} onChange={setIndividual} step={0.1} min={1} max={4} suffix="/ 4" theme={theme} />
            <NumberRow label="Target bonus" hint="BWP — what the role pays at 4/4 in a flat year"
                       value={target} onChange={setTarget} step={500} min={0} max={1_000_000} suffix="BWP" theme={theme} />
          </div>

          {/* Output */}
          <div className="rounded-2xl p-5 space-y-4"
               style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
            <div className="flex items-center gap-2">
              <Wallet className="w-4 h-4" style={{ color: theme.orange }} />
              <h3 className="font-semibold" style={{ color: theme.text }}>Pay-out breakdown</h3>
            </div>

            <LayerRow label="Org-only layer (target × org)" value={layers.orgLayer} theme={theme} />
            <LayerRow label="+ Department adjustment" value={layers.deptLayer} theme={theme} />
            <div className="rounded-xl p-4"
                 style={{ background: theme.oL, border: `1px solid ${theme.orange}55` }}>
              <div className="text-[10px] uppercase tracking-widest font-semibold" style={{ color: theme.orange }}>
                Final bonus
              </div>
              <div className="font-display text-3xl font-bold mt-1 tabular-nums" style={{ color: theme.orange }}>
                {fmtP(layers.indLayer)}
              </div>
              <div className="text-[11px] mt-1" style={{ color: theme.t2 }}>
                = {orgMult.toFixed(2)} × {deptMult.toFixed(2)} × ({individual.toFixed(1)} / 4) × {target.toLocaleString('en-BW')}
              </div>
            </div>

            <p className="text-[11px]" style={{ color: theme.t2 }}>
              <Info className="w-3 h-3 inline mr-1" />
              Indicative only. Actual pay-outs are approved by EXCO + HR after year-end calibration.
            </p>
          </div>
        </div>
      </main>
    </div>
  )
}

function NumberRow({ label, hint, value, onChange, step, min, max, suffix, theme }:
  { label: string; hint?: string; value: number; onChange: (n: number) => void;
    step: number; min: number; max: number; suffix?: string; theme: any }) {
  return (
    <div>
      <div className="flex items-baseline justify-between">
        <label className="text-xs font-medium" style={{ color: theme.t2 }}>{label}</label>
        {suffix && <span className="text-[11px]" style={{ color: theme.t2 }}>{suffix}</span>}
      </div>
      <input type="number" value={value} onChange={e => onChange(parseFloat(e.target.value) || 0)}
             step={step} min={min} max={max}
             className="w-full mt-1 px-3 py-2 rounded-lg text-sm tabular-nums outline-none"
             style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }} />
      {hint && <p className="text-[11px] mt-1" style={{ color: theme.t2 }}>{hint}</p>}
    </div>
  )
}

function LayerRow({ label, value, theme }: { label: string; value: number; theme: any }) {
  return (
    <div className="flex items-center justify-between py-1.5 border-b"
         style={{ borderColor: theme.cardBdr }}>
      <div className="flex items-center gap-1.5 text-sm" style={{ color: theme.t2 }}>
        <ChevronRight className="w-3 h-3" /> {label}
      </div>
      <div className="text-sm font-semibold tabular-nums" style={{ color: theme.text }}>
        {fmtP(value)}
      </div>
    </div>
  )
}
