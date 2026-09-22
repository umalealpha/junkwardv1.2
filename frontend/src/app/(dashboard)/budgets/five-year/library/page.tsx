'use client'

/**
 * /budgets/five-year/library — Strategic Plan Library, AD Insurtech.
 *
 * Requested by Finance (Oprah Mogomotsi, 2026-08-04): "build the strategic plan
 * library, it should work the same way the budget library and budget cockpit
 * work". Her spec put it on /strategic-plan; the CFO asked for it beside the
 * 5-Year Plan Cockpit built the day before, so the plan and its archive sit in
 * one place under Budgets. Same decision, better address.
 *
 * It is the source-of-record archive: plan packs (Base / Conservative /
 * Aggressive), their headline KPIs, the key assumption register, and the source
 * files. "Open in Cockpit" hands the pack and scenario to /budgets/five-year.
 *
 * THE INTEGRITY RULE, and how it is kept:
 *   A draft pack stores NO figures. Its KPIs are computed here from
 *   lib/fiveYearModel.ts — the same model, the same arithmetic the Cockpit runs —
 *   so a draft can never quote a number the Cockpit disagrees with. Approving a
 *   pack freezes that snapshot into the row, and from then on the stored figures
 *   are displayed verbatim and never recomputed. Approved figures are quotable;
 *   draft figures are live.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { useCompany } from '@/contexts/CompanyContext'
import { isAdiplPlanVisible, isCompanyResolving } from '@/lib/entityScope'
import { EntityScopeNotice } from '@/components/EntityScopeNotice'
import {
  getPlanPacks, updatePlanPack, uploadPlanPackFile, downloadPlanPackFile,
  getToken, type PlanPack, type PlanPackList,
} from '@/lib/api'
import {
  BASE, CONSERVATIVE, AGGRESSIVE, SEGMENTS, INVESTOR,
  compute, valuation, cagr, type Levers, type SegmentId,
} from '@/lib/fiveYearModel'
import {
  BookOpen, Download, Upload, Loader2, AlertTriangle, CheckCircle2,
  BarChart3, Lock, Archive, ArrowRight,
} from 'lucide-react'

const ORANGE = '#F4A623', NAVY = '#0D1B2A'

const LEVERS_BY_SCENARIO: Record<PlanPack['scenario_slug'], Levers> = {
  base: BASE, conservative: CONSERVATIVE, aggressive: AGGRESSIVE,
}

const STATUS_STYLE: Record<PlanPack['status'], { bg: string; fg: string }> = {
  draft:    { bg: '#FFF4DB', fg: '#8A5A00' },
  approved: { bg: '#E6F6EC', fg: '#2E7D32' },
  archived: { bg: '#EFEFEF', fg: '#555555' },
}

const FILE_KINDS = [
  { value: 'workbook', label: 'Financial model (xlsx)' },
  { value: 'spec', label: 'Specification / narrative' },
  { value: 'deck', label: 'Board deck' },
  { value: 'app', label: 'Interactive page / app' },
  { value: 'other', label: 'Other' },
]

const ALL_SEGMENTS = new Set<SegmentId>(SEGMENTS.map(s => s.id))

const pm = (v: number) => `P${(v / 1_000_000).toFixed(1)}m`
const fmtSize = (n: number) =>
  !n ? '' : n < 1024 * 1024 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`

/** The 7 headline KPIs the spec asks for, off the shared model. */
function kpisFor(pack: PlanPack): { label: string; value: string; note: string }[] {
  const stored = pack.base_figures || {}
  const locked = pack.figures_locked && Object.keys(stored).length > 0

  if (locked) {
    const g = (k: string) => stored[k]
    return [
      { label: 'FY30 Group revenue', value: g('fy30_revenue') != null ? `P${Number(g('fy30_revenue')).toFixed(1)}m` : '—', note: 'approved' },
      { label: 'FY30 EBITDA',        value: g('fy30_ebitda') != null ? `P${Number(g('fy30_ebitda')).toFixed(1)}m` : '—', note: 'approved' },
      { label: 'FY30 NPAT',          value: g('fy30_npat') != null ? `P${Number(g('fy30_npat')).toFixed(1)}m` : '—', note: 'approved' },
      { label: '5-yr cumulative PAT', value: g('cumulative_pat_5yr') != null ? `P${Number(g('cumulative_pat_5yr')).toFixed(1)}m` : '—', note: 'FY26–FY30' },
      { label: 'FY30 solvency ratio', value: g('fy30_solvency') != null ? `${Number(g('fy30_solvency')).toFixed(2)}x` : '—', note: 'approved' },
      { label: 'Investor capital',    value: g('investor_capital_usd') != null ? `USD ${Number(g('investor_capital_usd')).toFixed(1)}m` : '—', note: '2 tranches' },
      { label: 'FY30 lives impacted', value: g('fy30_lives') != null ? Math.round(Number(g('fy30_lives'))).toLocaleString('en-GB') : '—', note: 'approved' },
    ]
  }

  const levers = LEVERS_BY_SCENARIO[pack.scenario_slug]
  const c = compute(levers, ALL_SEGMENTS)
  const last = c.revenue.length - 1
  const v = valuation(c.revenue[last], levers.revMultiple)
  const growth = cagr(c.revenue)
  return [
    // cagr() returns null when the first year is zero or negative.
    { label: 'FY30 Group revenue', value: pm(c.revenue[last]),
      note: growth === null ? 'FY25A → FY30E' : `CAGR ${(growth * 100).toFixed(1)}%` },
    { label: 'FY30 EBITDA',        value: pm(c.ebitda[last]),  note: `margin ${(c.ebitdaMargin[last] * 100).toFixed(1)}%` },
    { label: 'FY30 NPAT',          value: pm(c.pat[last]),     note: 'after tax' },
    { label: '5-yr cumulative PAT', value: pm(c.cumulativePat), note: 'FY26–FY30' },
    { label: 'FY30 solvency ratio', value: `${c.solvency[last].toFixed(2)}x`, note: 'capital cover' },
    { label: 'Investor capital',    value: `USD ${(INVESTOR.capitalUsd / 1_000_000).toFixed(1)}m`, note: `BWP ${(INVESTOR.capitalBwp / 1_000_000).toFixed(0)}m · 2 tranches` },
    { label: 'Enterprise value', value: pm(v.evBwp), note: `${levers.revMultiple.toFixed(2)}x FY30 revenue` },
  ]
}

/** Entity-gated for the same reason as the cockpit — see bug 2026-08-06. */
export default function StrategicPlanLibraryRoute() {
  const { selected, selectedId } = useCompany()
  // See the cockpit: unresolved must never read as allowed.
  if (isCompanyResolving(selectedId, selected)) return null
  if (!isAdiplPlanVisible(selected?.code)) {
    return (
      <>
        <TopBar />
        <EntityScopeNotice
          entityName="Alpha Direct Insurtech"
          activeName={selected?.name}
          what="The Strategic Plan Library"
        />
      </>
    )
  }
  return <StrategicPlanLibraryPage />
}

function StrategicPlanLibraryPage() {
  const router = useRouter()
  const { theme } = useTheme()
  const [data, setData] = useState<PlanPackList | null>(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const upRef = useRef<Record<string, HTMLInputElement | null>>({})
  const kindRef = useRef<Record<string, string>>({})

  const refresh = useCallback(() => {
    setLoading(true); setErr(null)
    getPlanPacks().then(setData)
      .catch(e => setErr(e instanceof Error ? e.message : 'Could not load the plan library'))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    refresh()
  }, [refresh, router])

  const packs = data?.packs || []
  const canManage = !!data?.can_manage
  const counts = useMemo(() => ({
    total: packs.length,
    draft: packs.filter(p => p.status === 'draft').length,
    approved: packs.filter(p => p.status === 'approved').length,
    archived: packs.filter(p => p.status === 'archived').length,
  }), [packs])

  // Stats bar headline: read the Base pack, which is the reference for the others.
  const basePack = packs.find(p => p.scenario_slug === 'base' && p.status !== 'archived')
  const baseKpis = basePack ? kpisFor(basePack) : []
  const lastUpdated = packs.reduce<string | null>(
    (a, p) => (!a || p.created_at > a ? p.created_at : a), null)

  const card = { background: theme.card, border: `1px solid ${theme.cardBdr}` }

  const setStatus = async (p: PlanPack, status: PlanPack['status']) => {
    setBusy(true); setErr(null); setMsg(null)
    try {
      const body: Record<string, unknown> = { status }
      if (status === 'approved') {
        // Freeze exactly what the page is showing — the live model snapshot.
        const lv = LEVERS_BY_SCENARIO[p.scenario_slug]
        const c = compute(lv, ALL_SEGMENTS)
        const last = c.revenue.length - 1
        body.base_figures = {
          fy30_revenue: +(c.revenue[last] / 1e6).toFixed(1),
          fy30_ebitda: +(c.ebitda[last] / 1e6).toFixed(1),
          fy30_npat: +(c.pat[last] / 1e6).toFixed(1),
          fy30_ebitda_margin: +c.ebitdaMargin[last].toFixed(4),
          fy30_loss_ratio: +c.lossRatio[last].toFixed(4),
          fy30_solvency: +c.solvency[last].toFixed(2),
          fy30_lives: Math.round(c.lives[last]),
          cumulative_pat_5yr: +(c.cumulativePat / 1e6).toFixed(1),
          investor_capital_usd: INVESTOR.capitalUsd / 1e6,
          investor_capital_bwp: INVESTOR.capitalBwp / 1e6,
          fy30_ev_multiple: lv.revMultiple,
        }
      }
      await updatePlanPack(p.id, body)
      setMsg(status === 'approved'
        ? 'Plan approved — its figures are now frozen.'
        : `Plan marked ${status}.`)
      refresh()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not update the plan')
    } finally { setBusy(false) }
  }

  // The picked file is passed IN from the change event, not read back out of a ref.
  // Reading it from `upRef` meant that if the ref had not settled on the element the
  // function hit `if (!file) return` and gave up SILENTLY — no filename, no request, no
  // error. That is bug bbb942d6: "clicking Choose File opens the picker fine, but after
  // selecting an .xlsx nothing happens." The event's target is always the input the person
  // just used, so there is nothing left to resolve.
  const doUpload = async (packId: string, picked: File | null) => {
    if (!picked) {
      // Never fail without saying so — a silent return is what made this bug invisible.
      setErr('No file came through from the picker. Please choose the file again.')
      return
    }
    const file = picked
    const input = upRef.current[packId]
    setBusy(true); setErr(null); setMsg(null)
    try {
      const fd = new FormData()
      fd.append('file', file)
      fd.append('kind', kindRef.current[packId] || 'other')
      fd.append('label', file.name)
      await uploadPlanPackFile(packId, fd)
      setMsg(`Attached ${file.name}.`)
      refresh()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Upload failed')
    } finally {
      setBusy(false)
      // ALWAYS clear it, not only when the upload worked. This is the actual cause of bug
      // bbb942d6. Clearing on the success path alone meant that after ANY failure — a 403, a
      // 500, or a 502 during one of the day's deploys — the input kept the old value, so
      // picking THE SAME FILE again fired no change event at all: no filename change, no
      // request, complete silence. Exactly what was reported. An empty input guarantees the
      // next pick is always seen as a change.
      if (input) input.value = ''
    }
  }

  return (
    <div>
      <TopBar
        title="Strategic Plan Library"
        breadcrumbs={[{ label: 'Budgets' }, { label: 'Strategic Plan' }, { label: 'Plan Library' }]}
      />
      <div className="p-6 space-y-5">

        {/* intro + entity badge */}
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div className="flex items-start gap-3">
            <BookOpen className="w-5 h-5 mt-0.5" style={{ color: ORANGE }} />
            <div>
              <p className="text-sm font-semibold" style={{ color: theme.text }}>
                AD Insurtech · 5-Year Strategic Plan FY2026–FY2030
              </p>
              <p className="text-xs mt-0.5" style={{ color: theme.t2 }}>
                The source of record. Every figure here is the plan as filed — the Cockpit
                models around it and never writes back.
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span className="px-3 py-1.5 rounded-full text-[11px] font-semibold tracking-wide"
                  style={{ background: NAVY, color: '#fff' }}>AD INSURTECH</span>
            <button onClick={() => router.push('/budgets/five-year')}
              className="px-4 py-2 rounded-lg text-sm font-semibold inline-flex items-center gap-2"
              style={{ background: ORANGE, color: NAVY }}>
              <BarChart3 className="w-4 h-4" /> Open the Cockpit
            </button>
          </div>
        </div>

        {msg && (
          <div className="rounded-lg px-4 py-3 flex items-center gap-2 text-sm"
               style={{ background: '#E6F6EC', color: '#2E7D32' }}>
            <CheckCircle2 className="w-4 h-4" /> {msg}
          </div>
        )}
        {err && (
          <div className="rounded-lg px-4 py-3 flex items-center gap-2 text-sm"
               style={{ background: '#FDECEC', color: '#C62828' }}>
            <AlertTriangle className="w-4 h-4" /> {err}
          </div>
        )}

        {/* stats bar */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          {[
            { label: 'Plan packs', value: String(counts.total),
              sub: `${counts.draft} draft · ${counts.approved} approved · ${counts.archived} archived` },
            { label: 'FY30 target revenue', value: baseKpis[0]?.value || '—',
              sub: `Base case · ${baseKpis[0]?.note || ''}` },
            { label: 'Investor capital required', value: baseKpis[5]?.value || '—',
              sub: baseKpis[5]?.note || '' },
            { label: 'Last updated',
              value: lastUpdated ? new Date(lastUpdated).toLocaleDateString('en-GB', { month: 'short', year: 'numeric' }) : '—',
              sub: 'FY2025 base — Forvis Mazars signed audit' },
          ].map(s => (
            <div key={s.label} className="rounded-lg p-4"
                 style={{ ...card, borderLeft: `3px solid ${ORANGE}` }}>
              <p className="text-[11px] uppercase tracking-wider" style={{ color: theme.t2 }}>{s.label}</p>
              <p className="text-2xl font-semibold mt-1" style={{ color: theme.text }}>{s.value}</p>
              <p className="text-[11px] mt-1" style={{ color: theme.t2 }}>{s.sub}</p>
            </div>
          ))}
        </div>

        {loading && (
          <p className="text-sm flex items-center gap-2" style={{ color: theme.t2 }}>
            <Loader2 className="w-4 h-4 animate-spin" /> Loading the library…
          </p>
        )}
        {!loading && packs.length === 0 && (
          <div className="rounded-lg p-6" style={card}>
            <p className="text-sm" style={{ color: theme.text }}>
              No plan packs saved yet.
            </p>
            <p className="text-xs mt-1" style={{ color: theme.t2 }}>
              Finance seeds the Base, Conservative and Aggressive packs with
              <code className="mx-1 px-1.5 py-0.5 rounded" style={{ background: theme.g100 }}>
                seed_plan_packs
              </code>.
            </p>
          </div>
        )}

        {/* plan packs */}
        {packs.map(p => {
          const kpis = kpisFor(p)
          const st = STATUS_STYLE[p.status]
          return (
            <div key={p.id} className="rounded-lg overflow-hidden" style={card}>
              {/* header band */}
              <div className="px-5 py-3 flex items-center justify-between gap-3 flex-wrap"
                   style={{ background: NAVY }}>
                <div>
                  <p className="text-sm font-bold text-white">{p.label}</p>
                  <p className="text-[11px] mt-0.5" style={{ color: '#ffffff99' }}>
                    {Array.from(new Set([p.prepared_by, p.department, p.prepared_date]
                      .filter(Boolean) as string[])).join(' · ')}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  {p.figures_locked && (
                    <span className="inline-flex items-center gap-1 text-[10px]" style={{ color: '#ffffff99' }}>
                      <Lock className="w-3 h-3" /> figures frozen
                    </span>
                  )}
                  <span className="px-2.5 py-1 rounded text-[10px] font-bold uppercase tracking-wider"
                        style={{ background: st.bg, color: st.fg }}>
                    {p.status}
                  </span>
                </div>
              </div>

              <div className="p-5 grid grid-cols-1 lg:grid-cols-3 gap-5">
                {/* KPIs + narrative */}
                <div className="lg:col-span-2">
                  <div className="flex flex-wrap gap-x-5 gap-y-4 pb-2">
                    {kpis.map((k, i) => (
                      <div key={k.label} className="flex-shrink-0 pr-5"
                           style={{ borderRight: i < kpis.length - 1 ? `1px solid ${theme.cardBdr}` : undefined }}>
                        <p className="text-lg font-semibold whitespace-nowrap" style={{ color: theme.text }}>{k.value}</p>
                        <p className="text-[10px] mt-0.5 whitespace-nowrap" style={{ color: theme.t2 }}>{k.label}</p>
                        <p className="text-[9px] whitespace-nowrap" style={{ color: theme.t2, opacity: 0.75 }}>{k.note}</p>
                      </div>
                    ))}
                  </div>
                  <p className="text-xs mt-3 leading-relaxed" style={{ color: theme.t2 }}>{p.narrative}</p>
                  {!p.figures_locked && (
                    <p className="text-[10px] mt-2" style={{ color: theme.t2, opacity: 0.8 }}>
                      Draft — figures shown live from the plan model, so they always match the Cockpit.
                      Approving freezes them.
                    </p>
                  )}
                </div>

                {/* files */}
                <div>
                  <p className="text-[11px] uppercase tracking-wider mb-2" style={{ color: theme.t2 }}>
                    Source files
                  </p>
                  {p.files.length === 0 && (
                    <p className="text-xs" style={{ color: theme.t2 }}>Nothing attached yet.</p>
                  )}
                  <div className="space-y-1.5">
                    {p.files.map(f => (
                      <button key={f.id}
                        onClick={() => downloadPlanPackFile(f.id, f.filename || f.label)
                          .catch(e => setErr(e instanceof Error ? e.message : 'Download failed'))}
                        className="w-full text-left rounded px-2.5 py-2 flex items-center gap-2 text-xs"
                        style={{ background: theme.g100, color: theme.text }}>
                        <Download className="w-3.5 h-3.5 flex-shrink-0" style={{ color: ORANGE }} />
                        <span className="flex-1 truncate font-medium">{f.label || f.filename}</span>
                        <span style={{ color: theme.t2 }}>{fmtSize(f.size)}</span>
                      </button>
                    ))}
                  </div>
                  {canManage && (
                    <div className="mt-3 space-y-2">
                      <select
                        onChange={e => { kindRef.current[p.id] = e.target.value }}
                        defaultValue="other"
                        className="w-full rounded px-2 py-1.5 text-xs"
                        style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }}>
                        {FILE_KINDS.map(k => <option key={k.value} value={k.value}>{k.label}</option>)}
                      </select>
                      <input ref={el => { upRef.current[p.id] = el }} type="file"
                             onChange={e => doUpload(p.id, e.currentTarget.files?.[0] ?? null)}
                             className="block w-full text-xs" style={{ color: theme.t2 }} />
                      <p className="text-[10px] flex items-center gap-1" style={{ color: theme.t2 }}>
                        <Upload className="w-3 h-3" /> Pick a file to attach it straight away.
                      </p>
                    </div>
                  )}
                </div>
              </div>

              {/* action footer */}
              <div className="px-5 py-3 flex items-center justify-end gap-2 flex-wrap"
                   style={{ borderTop: `1px solid ${theme.cardBdr}`, background: theme.g100 }}>
                {canManage && p.status === 'draft' && (
                  <button disabled={busy} onClick={() => setStatus(p, 'approved')}
                    className="px-3 py-1.5 rounded text-xs font-semibold disabled:opacity-50"
                    style={{ border: `1px solid ${theme.cardBdr}`, color: theme.text }}>
                    Approve &amp; freeze figures
                  </button>
                )}
                {canManage && p.status !== 'archived' && (
                  <button disabled={busy} onClick={() => setStatus(p, 'archived')}
                    className="px-3 py-1.5 rounded text-xs font-semibold inline-flex items-center gap-1.5 disabled:opacity-50"
                    style={{ border: `1px solid ${theme.cardBdr}`, color: theme.t2 }}>
                    <Archive className="w-3.5 h-3.5" /> Archive
                  </button>
                )}
                <button
                  onClick={() => router.push(
                    `/budgets/five-year?pack=${p.id}&scenario=${p.scenario_slug}`)}
                  className="px-4 py-1.5 rounded text-xs font-semibold inline-flex items-center gap-1.5"
                  style={{ background: NAVY, color: '#fff' }}>
                  Open in Cockpit <ArrowRight className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          )
        })}

        {/* key assumption register */}
        {packs.length > 0 && (
          <div className="rounded-lg overflow-hidden" style={card}>
            <div className="px-5 py-3" style={{ borderBottom: `1px solid ${theme.cardBdr}` }}>
              <p className="text-sm font-semibold" style={{ color: theme.text }}>Key assumption register</p>
              <p className="text-[11px] mt-0.5" style={{ color: theme.t2 }}>
                The inputs behind the Base pack. Change one of these in the workbook and the
                pack must be re-issued.
              </p>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr style={{ background: NAVY }}>
                    {['Assumption', 'Segment', 'FY26E', 'FY27E', 'FY28E', 'FY29E', 'FY30E', 'Source'].map(h => (
                      <th key={h} className="text-left px-3 py-2 font-semibold text-white whitespace-nowrap">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {(basePack?.assumptions || packs[0].assumptions || []).map((a, i) => (
                    <tr key={`${a.label}-${i}`} style={{ background: i % 2 ? theme.g100 : 'transparent' }}>
                      <td className="px-3 py-2 whitespace-nowrap" style={{ color: theme.text }}>{a.label}</td>
                      <td className="px-3 py-2">
                        <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold whitespace-nowrap"
                              style={{ background: theme.g100, color: theme.t2 }}>{a.segment}</span>
                      </td>
                      {[a.fy26, a.fy27, a.fy28, a.fy29, a.fy30].map((v, j) => (
                        <td key={j} className="px-3 py-2 whitespace-nowrap" style={{ color: theme.text }}>{v || '—'}</td>
                      ))}
                      <td className="px-3 py-2 whitespace-nowrap" style={{ color: theme.t2 }}>{a.source}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* governance note — spec §4.6, shown verbatim */}
        <div className="rounded-lg p-4" style={{ ...card, borderLeft: `4px solid ${ORANGE}` }}>
          <p className="text-xs font-bold mb-2" style={{ color: theme.text }}>
            Governance &amp; integrity note
          </p>
          <ul className="text-xs space-y-1.5" style={{ color: theme.t2 }}>
            <li>FY2025 actuals are locked — source: Forvis Mazars signed audit, 2026.</li>
            <li>No figure in the base layer deviates from the consolidated workbook; every
                transformation is a labelled line.</li>
            <li>The Cockpit&rsquo;s sliders move the scenario layer only — Reset always returns
                to the workbook base numbers.</li>
            <li>Aria is advisory only and never writes to stored figures.</li>
            <li>Any change to an assumption must be reflected in the workbook before the plan
                pack is updated.</li>
          </ul>
        </div>
      </div>
    </div>
  )
}
