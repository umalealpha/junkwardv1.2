'use client'

/**
 * Reinsurance Renewal — 2026/27 board dashboard.
 *
 * CFO directive 2026-06-05 (Kago renewal pack). One interactive page that
 * surfaces the full 2026/27 treaty renewal: General Quota Share structure,
 * reinsurer panel + ratings, 11-year loss-ratio history, EPI by class, and a
 * live "what-if" model (move the sliders → ceded premium / commission /
 * expected recoveries / reinsurer result recompute instantly). All source
 * documents (the renewal pack) are downloadable below.
 *
 * Figures are the renewal-pack snapshot (Munich-Re-led GQS, EPI P29m, 70%
 * cession proposed for 2026/27). Display + planning model only — the audited
 * ledger is unaffected. Built to a Munich Re / PwC-presentable standard.
 */
import { useState, useMemo, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { getToken, API_BASE, apiFetch } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Shield, Download, TrendingDown, TrendingUp, Layers, Building2, FileText, FileSpreadsheet, RefreshCw, Sparkles, Send } from 'lucide-react'

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'
const GREEN = '#059669'
const RED = '#DC2626'

// ── Renewal-pack snapshot (2026/27) ─────────────────────────────────────────
const STRUCTURE = {
  treaty: 'General Quota Share',
  leader: 'Munich Re',
  ccy: 'BWP',
  epi: 29_000_000,
  prior: { retention: 70, cession: 30, commission: 35 },
  proposed: { retention: 30, cession: 70, commission: 35 },
  profitCommission: 28.5,
  mgmtExpense: 7.5,
  maxLimit: 10_000_000,
  eventLimit: 70_000_000,
}

const PANEL = [
  { reinsurer: 'Munich Re of Africa', share: 45, rating: 'AA- (Fitch)' },
  { reinsurer: 'GIC Reinsurance', share: 20, rating: 'A- (AM Best)' },
  { reinsurer: 'FM Reinsurance', share: 10, rating: 'rating to confirm' },
  { reinsurer: 'Grand Reinsurance', share: 10, rating: 'rating to confirm' },
  { reinsurer: 'Continental Reinsurance', share: 5, rating: 'A- (AM Best)' },
  { reinsurer: 'Kuwait Re', share: 5, rating: 'A- (AM Best)' },
]

// Commercial-vehicle proportional treaty — 11-year underwriting-year history.
const LOSS_HISTORY = [
  { uwy: '14/15', premium: 1_975_195, lr: 155.8 },
  { uwy: '15/16', premium: 2_936_508, lr: 154.0 },
  { uwy: '16/17', premium: 2_470_783, lr: 103.6 },
  { uwy: '17/18', premium: 4_615_353, lr: 114.3 },
  { uwy: '18/19', premium: 6_558_762, lr: 55.7 },
  { uwy: '19/20', premium: 5_859_117, lr: 45.3 },
  { uwy: '20/21', premium: 6_492_223, lr: 88.7 },
  { uwy: '21/22', premium: 7_916_866, lr: 64.8 },
  { uwy: '24/25', premium: 52_822_272, lr: 55.9 },
  { uwy: '25/26', premium: 41_260_973, lr: 84.2 },
]

const EPI_BY_CLASS = [
  { cls: 'Property', gross: 17_774_273 },
  { cls: 'Mobile & Electronic', gross: 6_948_167 },
  { cls: 'Misc', gross: 4_328_961 },
  { cls: 'Transportation', gross: 4_050_789 },
  { cls: 'Engineering', gross: 2_429_682 },
  { cls: 'Guarantee', gross: 691_210 },
]
const EPI_TOTAL_QS = 36_223_082

const DOCUMENTS = [
  { name: '1.11-Year Historical Treaty Stats - Proportional (000).xlsx', label: '11-Yr Treaty Stats — Proportional', kind: 'xlsx' },
  { name: '2. 11-Year Historical Treaty Stats - Non Proportional (000).xlsx', label: '11-Yr Treaty Stats — Non-Proportional', kind: 'xlsx' },
  { name: '3.Auto_FAC_Treaty_Performance 2026.xlsx', label: 'Auto FAC Treaty Performance 2026', kind: 'xlsx' },
  { name: '4. XOL Claims 2026.xlsx', label: 'XOL Claims 2026', kind: 'xlsx' },
  { name: '5.Reinsurance claims Risk Profile 2026.xlsb', label: 'Reinsurance Claims Risk Profile 2026', kind: 'xlsb' },
  { name: '6. Alpha Direct Proposed Structure 2026-27.xlsx', label: 'Proposed Structure 2026-27', kind: 'xlsx' },
  { name: '2026-27 EPIs and EGNPIs revised on 3rd June 2026.xlsx', label: 'EPIs & EGNPIs (rev. 3 Jun 2026)', kind: 'xlsx' },
  { name: 'Global Re.pdf', label: 'Global Re — Profile', kind: 'pdf' },
  { name: 'MDP Analysis 2425.xlsb', label: 'MDP Analysis 24/25', kind: 'xlsb' },
  { name: 'Oman Re Fitch rating Circular.pdf', label: 'Oman Re — Fitch Rating Circular', kind: 'pdf' },
  { name: 'Oman Re_Corporate Profile (Digital)_compressed.pdf', label: 'Oman Re — Corporate Profile', kind: 'pdf' },
  { name: 'OmanRe Facultative_Geo Scope, Risk Appetite, Uw Capacity (April 2026).pdf', label: 'Oman Re — FAC Scope / Appetite / Capacity', kind: 'pdf' },
  { name: 'Reinsurance claims Risk Profile 2026 latest.xlsb', label: 'Claims Risk Profile 2026 (latest)', kind: 'xlsb' },
]

const P = (n: number) => 'P ' + n.toLocaleString('en-BW', { maximumFractionDigits: 0 })
const PM = (n: number) => 'P ' + (n / 1_000_000).toFixed(1) + 'm'

function Tile({ icon, label, value, sub, accent }: { icon: React.ReactNode; label: string; value: string; sub?: string; accent?: string }) {
  return (
    <div style={{ background: '#fff', border: '1px solid #E5E7EB', borderRadius: 12, padding: '16px 18px', boxShadow: '0 1px 3px rgba(13,27,42,0.05)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#6B7480', fontSize: 12, fontWeight: 600 }}>
        <span style={{ color: accent || ORANGE }}>{icon}</span>{label}
      </div>
      <div style={{ fontSize: 26, fontWeight: 800, color: NAVY, marginTop: 6, fontFamily: 'var(--font-display, Georgia)' }}>{value}</div>
      {sub && <div style={{ fontSize: 12, color: '#9AA3AF', marginTop: 2 }}>{sub}</div>}
    </div>
  )
}

export default function ReinsuranceRenewalPage() {
  const router = useRouter()
  useEffect(() => { if (!getToken()) router.replace('/login') }, [router])

  // ── Interactive what-if model (the "move figures" moment) ─────────────────
  const [epi, setEpi] = useState(STRUCTURE.epi)
  const [cession, setCession] = useState(STRUCTURE.proposed.cession)     // %
  const [commission, setCommission] = useState(STRUCTURE.proposed.commission) // %
  const [lossRatio, setLossRatio] = useState(75)                          // % projected

  const m = useMemo(() => {
    // Expert-audit fix 2026-06-05 (treaty accountant + reinsurance specialist):
    // a proportional QS reinsurer result is ceded premium LESS ceding
    // commission, LESS management expense (7.5%), LESS incurred claims. Profit
    // commission (28.5%) returns to the cedant ONLY when the treaty is
    // profitable — that is Alpha Direct's genuine upside, not the old
    // "net benefit" mirror (which was just the reinsurer's loss flipped).
    const ME = STRUCTURE.mgmtExpense / 100
    const PC = STRUCTURE.profitCommission / 100
    const cededPrem = epi * (cession / 100)
    const retainedPrem = epi * (1 - cession / 100)
    const commissionIncome = cededPrem * (commission / 100)
    const meCost = cededPrem * ME
    const expectedRecoveries = cededPrem * (lossRatio / 100)
    const reinsurerBeforePC = cededPrem - commissionIncome - meCost - expectedRecoveries
    const profitCommission = reinsurerBeforePC > 0 ? reinsurerBeforePC * PC : 0
    const reinsurerResult = reinsurerBeforePC - profitCommission
    return { cededPrem, retainedPrem, commissionIncome, meCost, expectedRecoveries, reinsurerResult, profitCommission }
  }, [epi, cession, commission, lossRatio])
  // Reinsurer break-even loss ratio = 100% − ceding commission − ME (the LR at
  // which the treaty stops being profitable for the reinsurer). NOT 100%.
  const reinsBreakeven = 100 - STRUCTURE.proposed.commission - STRUCTURE.mgmtExpense

  const reset = () => { setEpi(STRUCTURE.epi); setCession(STRUCTURE.proposed.cession); setCommission(STRUCTURE.proposed.commission); setLossRatio(75) }

  // ── Ask the Renewal AI (Aria-backed) ──────────────────────────────────
  const [askQ, setAskQ] = useState('')
  const [askA, setAskA] = useState<string | null>(null)
  const [askLoading, setAskLoading] = useState(false)
  const SUGGESTED = [
    'Is the 70% cession sensible given the 11-year loss history?',
    'Summarise the security of the reinsurer panel.',
    'At a 75% loss ratio, what is the net benefit to Alpha Direct?',
    'What are the key risks in this renewal structure?',
  ]
  async function askAI(question: string) {
    const q = question.trim()
    if (!q || askLoading) return
    setAskQ(q); setAskLoading(true); setAskA(null)
    try {
      const d = await apiFetch<{ answer?: string; detail?: string; configured?: boolean }>(
        '/reinsurance/renewal/ask/', { method: 'POST', body: JSON.stringify({ question: q }) },
      )
      setAskA(d.answer || d.detail || 'No answer returned.')
    } catch {
      setAskA('AI request failed — check connectivity or the Aria key.')
    } finally {
      setAskLoading(false)
    }
  }

  const maxLR = Math.max(...LOSS_HISTORY.map(h => h.lr))
  const maxEpi = Math.max(...EPI_BY_CLASS.map(e => e.gross))
  const docUrl = (name: string) => `${API_BASE}/reinsurance/renewal/document/?name=${encodeURIComponent(name)}`

  return (
    <div className="flex flex-col h-full">
      <TopBar title="Reinsurance Renewal" breadcrumbs={[{ label: 'Reinsurance' }, { label: 'Renewal 2026/27' }]} />

      <div className="flex-1 overflow-auto" style={{ background: '#F7F8FB' }}>
        {/* Hero */}
        <div style={{ background: `linear-gradient(120deg, ${NAVY} 0%, #16314d 100%)`, color: '#fff', padding: '28px 32px' }}>
          <div style={{ fontSize: 11, letterSpacing: 3, textTransform: 'uppercase', opacity: 0.7 }}>Treaty Year 2026/27 · CCY {STRUCTURE.ccy}</div>
          <div style={{ fontSize: 40, fontWeight: 800, lineHeight: 1.05, fontFamily: 'var(--font-display, Georgia)' }}>
            Reinsurance <span style={{ color: ORANGE, fontStyle: 'italic' }}>Renewal.</span>
          </div>
          <div style={{ fontSize: 15, opacity: 0.85, marginTop: 6 }}>{STRUCTURE.treaty} · Leader {STRUCTURE.leader} · 70% cession proposed</div>
          <div style={{ display: 'inline-block', marginTop: 10, fontSize: 11, fontWeight: 700, letterSpacing: 1, textTransform: 'uppercase', color: NAVY, background: ORANGE, borderRadius: 6, padding: '4px 10px' }}>
            Planning model · Not audited · Prospective figures
          </div>
        </div>

        <div style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 20 }}>
          {/* KPI tiles */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))', gap: 14 }}>
            <Tile icon={<Shield size={16} />} label="Estimated Premium Income" value={PM(STRUCTURE.epi)} sub="General Quota Share" />
            <Tile icon={<TrendingDown size={16} />} label="Cession (proposed)" value={`${STRUCTURE.proposed.cession}%`} sub={`was ${STRUCTURE.prior.cession}% in 2025/26`} accent={RED} />
            <Tile icon={<TrendingUp size={16} />} label="Retention (proposed)" value={`${STRUCTURE.proposed.retention}%`} sub={`was ${STRUCTURE.prior.retention}%`} accent={GREEN} />
            <Tile icon={<Layers size={16} />} label="Max Limit / Event" value={`${PM(STRUCTURE.maxLimit)} / ${PM(STRUCTURE.eventLimit)}`} sub="per risk / per event" />
            <Tile icon={<Building2 size={16} />} label="Ceding Commission" value={`${STRUCTURE.proposed.commission}%`} sub={`PC ${STRUCTURE.profitCommission}% · ME ${STRUCTURE.mgmtExpense}%`} />
          </div>

          {/* Interactive model + treaty structure */}
          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1.2fr) minmax(0,1fr)', gap: 16 }}>
            <Card>
              <CardContent>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <h3 style={{ fontWeight: 700, color: NAVY, fontSize: 16 }}>What-if model — move the figures</h3>
                  <button onClick={reset} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: '#6B7480', background: 'none', border: '1px solid #D6DAE2', borderRadius: 8, padding: '5px 10px', cursor: 'pointer' }}><RefreshCw size={13} /> Reset</button>
                </div>
                <p style={{ fontSize: 12, color: '#9AA3AF', margin: '4px 0 14px' }}>Drag the sliders — ceded premium, commission income, expected recoveries and the reinsurer result recompute live.</p>

                {[
                  { label: 'Estimated Premium Income (EPI)', val: epi, set: setEpi, min: 5_000_000, max: 60_000_000, step: 500_000, fmt: PM },
                  { label: 'Cession %', val: cession, set: setCession, min: 0, max: 100, step: 1, fmt: (v: number) => v + '%' },
                  { label: 'Ceding commission %', val: commission, set: setCommission, min: 0, max: 50, step: 0.5, fmt: (v: number) => v + '%' },
                  { label: 'Projected loss ratio %', val: lossRatio, set: setLossRatio, min: 20, max: 160, step: 1, fmt: (v: number) => v + '%' },
                ].map((s) => (
                  <div key={s.label} style={{ marginBottom: 12 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, color: NAVY, fontWeight: 600 }}>
                      <span>{s.label}</span><span style={{ color: ORANGE }}>{s.fmt(s.val)}</span>
                    </div>
                    <input type="range" min={s.min} max={s.max} step={s.step} value={s.val}
                      onChange={(e) => s.set(parseFloat(e.target.value))}
                      style={{ width: '100%', accentColor: ORANGE, marginTop: 4 }} />
                  </div>
                ))}

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginTop: 8 }}>
                  {[
                    { k: 'Ceded premium', v: m.cededPrem, c: NAVY },
                    { k: 'Retained premium (net)', v: m.retainedPrem, c: NAVY },
                    { k: 'Ceding commission income', v: m.commissionIncome, c: GREEN },
                    { k: 'Expected ceded claims (recoveries)', v: m.expectedRecoveries, c: NAVY },
                    { k: 'Reinsurer result (after comm, ME, PC)', v: m.reinsurerResult, c: m.reinsurerResult >= 0 ? GREEN : RED },
                    { k: 'Profit commission to Alpha Direct', v: m.profitCommission, c: GREEN },
                  ].map((o) => (
                    <div key={o.k} style={{ background: '#F5F7FB', borderRadius: 8, padding: '10px 12px' }}>
                      <div style={{ fontSize: 11, color: '#6B7480' }}>{o.k}</div>
                      <div style={{ fontSize: 18, fontWeight: 700, color: o.c, fontFamily: 'monospace' }}>{P(Math.round(o.v))}</div>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>

            {/* Reinsurer panel */}
            <Card>
              <CardContent>
                <h3 style={{ fontWeight: 700, color: NAVY, fontSize: 16, marginBottom: 4 }}>Reinsurer panel</h3>
                <p style={{ fontSize: 12, color: '#9AA3AF', marginBottom: 12 }}>2026/27 participation + security rating</p>
                {PANEL.map((r) => (
                  <div key={r.reinsurer} style={{ marginBottom: 10 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13 }}>
                      <span style={{ color: NAVY, fontWeight: 600 }}>{r.reinsurer}</span>
                      <span style={{ color: '#6B7480' }}>{r.share}% · {r.rating}</span>
                    </div>
                    <div style={{ height: 8, background: '#EEF1F6', borderRadius: 4, marginTop: 3 }}>
                      <div style={{ width: `${r.share}%`, height: '100%', background: r.reinsurer.includes('Munich') ? ORANGE : NAVY, borderRadius: 4 }} />
                    </div>
                  </div>
                ))}
              </CardContent>
            </Card>
          </div>

          {/* Ask the Renewal AI (Aria) */}
          <Card>
            <CardContent>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <Sparkles size={18} style={{ color: ORANGE }} />
                <h3 style={{ fontWeight: 700, color: NAVY, fontSize: 16 }}>Ask the Renewal AI</h3>
              </div>
              <p style={{ fontSize: 12, color: '#9AA3AF', margin: '4px 0 12px' }}>
                Treaty advisor on the 2026/27 structure. Only the figures above are shared with the model — never the raw documents.
              </p>
              <div style={{ display: 'flex', gap: 8 }}>
                <input
                  value={askQ}
                  onChange={(e) => setAskQ(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') askAI(askQ) }}
                  placeholder="Ask about the renewal — cession, panel, loss history, net cost…"
                  style={{ flex: 1, border: '1px solid #D6DAE2', borderRadius: 8, padding: '10px 12px', fontSize: 14, color: NAVY }}
                />
                <button onClick={() => askAI(askQ)} disabled={askLoading}
                  style={{ display: 'flex', alignItems: 'center', gap: 6, background: ORANGE, color: NAVY, border: 'none', borderRadius: 8, padding: '0 16px', fontWeight: 700, cursor: askLoading ? 'wait' : 'pointer', opacity: askLoading ? 0.6 : 1 }}>
                  {askLoading ? <RefreshCw size={15} className="animate-spin" /> : <Send size={15} />} Ask
                </button>
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 10 }}>
                {SUGGESTED.map((s) => (
                  <button key={s} onClick={() => askAI(s)} disabled={askLoading}
                    style={{ fontSize: 12, color: NAVY, background: '#F5F7FB', border: '1px solid #E5E7EB', borderRadius: 16, padding: '5px 11px', cursor: 'pointer' }}>
                    {s}
                  </button>
                ))}
              </div>
              {askA && (
                <div style={{ marginTop: 14, background: '#F5F7FB', borderLeft: `3px solid ${ORANGE}`, borderRadius: 8, padding: '12px 14px', fontSize: 14, color: NAVY, whiteSpace: 'pre-wrap', lineHeight: 1.5 }}>
                  {askA}
                </div>
              )}
            </CardContent>
          </Card>

          {/* 11-yr loss ratio + EPI by class */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
            <Card>
              <CardContent>
                <h3 style={{ fontWeight: 700, color: NAVY, fontSize: 16 }}>11-year loss ratio</h3>
                <p style={{ fontSize: 12, color: '#9AA3AF', marginBottom: 14 }}>Motor / commercial-vehicle proportional experience · dashed line = reinsurer break-even (after {STRUCTURE.proposed.commission}% comm + {STRUCTURE.mgmtExpense}% ME)</p>
                <div style={{ display: 'flex', alignItems: 'flex-end', gap: 6, height: 160, position: 'relative' }}>
                  {/* Reinsurer break-even line ≈57.5% (100 − comm − ME), NOT 100% */}
                  <div style={{ position: 'absolute', left: 0, right: 0, bottom: `${(reinsBreakeven / maxLR) * 140}px`, borderTop: `1px dashed ${RED}`, fontSize: 9, color: RED }}>Reinsurer break-even ≈{reinsBreakeven}%</div>
                  {LOSS_HISTORY.map((h) => (
                    <div key={h.uwy} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'flex-end' }}>
                      <div style={{ fontSize: 9, color: NAVY, fontWeight: 600 }}>{h.lr.toFixed(0)}</div>
                      <div title={`${h.uwy}: LR ${h.lr}% · prem ${PM(h.premium)}`}
                        style={{ width: '70%', height: `${(h.lr / maxLR) * 140}px`, background: h.lr > 100 ? RED : h.lr > 70 ? ORANGE : GREEN, borderRadius: '3px 3px 0 0' }} />
                      <div style={{ fontSize: 9, color: '#9AA3AF', marginTop: 3 }}>{h.uwy}</div>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardContent>
                <h3 style={{ fontWeight: 700, color: NAVY, fontSize: 16 }}>Non-motor EPI by class</h3>
                <p style={{ fontSize: 12, color: '#9AA3AF', marginBottom: 14 }}>100% gross · Non-motor QS total {PM(EPI_TOTAL_QS)} (separate book from the P29m General QS above)</p>
                {EPI_BY_CLASS.map((e) => (
                  <div key={e.cls} style={{ marginBottom: 9 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }}>
                      <span style={{ color: NAVY }}>{e.cls}</span><span style={{ color: '#6B7480' }}>{PM(e.gross)}</span>
                    </div>
                    <div style={{ height: 8, background: '#EEF1F6', borderRadius: 4, marginTop: 2 }}>
                      <div style={{ width: `${(e.gross / maxEpi) * 100}%`, height: '100%', background: ORANGE, borderRadius: 4 }} />
                    </div>
                  </div>
                ))}
              </CardContent>
            </Card>
          </div>

          {/* Documents — the renewal board pack */}
          <Card>
            <CardContent>
              <h3 style={{ fontWeight: 700, color: NAVY, fontSize: 16, marginBottom: 2 }}>Renewal board pack</h3>
              <p style={{ fontSize: 12, color: '#9AA3AF', marginBottom: 14 }}>All {DOCUMENTS.length} source documents — click to download (audit-logged)</p>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 10 }}>
                {DOCUMENTS.map((d) => (
                  <a key={d.name} href={docUrl(d.name)} target="_blank" rel="noopener noreferrer"
                    style={{ display: 'flex', alignItems: 'center', gap: 10, background: '#F5F7FB', border: '1px solid #E5E7EB', borderRadius: 10, padding: '11px 13px', textDecoration: 'none' }}>
                    <span style={{ color: d.kind === 'pdf' ? RED : GREEN }}>{d.kind === 'pdf' ? <FileText size={18} /> : <FileSpreadsheet size={18} />}</span>
                    <span style={{ flex: 1, fontSize: 13, color: NAVY, fontWeight: 500 }}>{d.label}</span>
                    <Download size={15} style={{ color: ORANGE }} />
                  </a>
                ))}
              </div>
            </CardContent>
          </Card>

          <p style={{ fontSize: 11, color: '#9AA3AF', textAlign: 'center', paddingBottom: 16 }}>
            Planning model — figures are the 2026/27 renewal-pack snapshot. The audited ledger is unaffected. Source: renewal pack compiled 3 Jun 2026.
          </p>
        </div>
      </div>
    </div>
  )
}
