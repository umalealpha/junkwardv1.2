'use client'

/**
 * /bonu — BONU legal benefit: the revenue line and a forensic look at what the
 * panel firms bill us.
 *
 * CFO 2026-08-03: "we will make a nice dashboard with these info in omni, for now let us
 * wire to GL later, we need in depth analysis of invoices the work they performed because
 * lawyers are crooks and they can cheat us."
 *
 * READ ONLY on purpose. Every figure comes from the ledger or from loaded invoice detail;
 * nothing on this page posts a journal or changes an account.
 *
 * Backend: /api/v1/bonu/dashboard/ · /api/v1/bonu/forensics/ · /api/v1/bonu/ai-review/
 */

import Link from 'next/link'
import { useEffect, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { apiFetch } from '@/lib/api'
import {
  AlertTriangle, Scale, TrendingDown, Sparkles, Info, Building2, FileWarning, Loader2, Users,
} from 'lucide-react'

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'
const RED = '#DC2626'
const GREEN = '#059669'

const SEV_COLOR: Record<string, string> = { high: RED, medium: '#B45309', low: '#6B7280' }
// How many firm findings to print. Beyond this the page stops being readable.
const FINDINGS_SHOWN = 40

interface KpiPair { omni: string | null; author: string | null; differ?: boolean }
interface Kpis {
  available: boolean; reason?: string; period?: string
  revenue?: KpiPair; vat?: KpiPair; commission?: KpiPair; net_premium?: KpiPair
  claims?: KpiPair; admin?: KpiPair; profit_loss?: KpiPair
  loss_ratio?: KpiPair; expense_ratio?: KpiPair; combined_ratio?: KpiPair
  break_even?: { gross_needed: string | null; uplift_pct: string | null; note: string }
}

interface Dash {
  months: string[]
  series: Record<string, Record<string, number>>
  balances: Record<string, number | null>
  loss_ratio_pct: number | null
  result_on_ledger: number
  revenue_run_rate: number
  last_revenue_month: string | null
  missing_revenue_months: string[]
  missing_revenue_estimate: number
  caveat: string
}
interface Finding {
  code: string; severity: string; title: string; detail: string
  amount_at_risk: number; question_for_firm: string; firm: string; invoice: string
}
interface Forensics {
  invoices: number; lines_checked: number; firms: number
  // These aggregates are built over a bounded read. Past the cap they understate,
  // and would then sit above a link to a detail total that contradicts them.
  lines_total?: number
  lines_truncated?: boolean
  line_cap?: number
  matter_types: { value: string; label: string }[]
  by_matter_type: { type: string; label: string; lines: number; billed: number; matters: number; avg_per_matter: number }[]
  by_lawyer: { lawyer: string; firm: string; lines: number; billed: number; hours: number; effective_rate: number | null }[]
  filters_applied: Record<string, string>
  summary: { total: number; high: number; medium: number; low: number; amount_at_risk: number }
  by_firm: { firm: string; lines: number; billed: number; at_risk: number; findings: number }[]
  findings: Finding[]
  members?: {
    total_spend: number; spend_tied_to_a_member: number; coverage_pct: number | null
    members_identified: number; members_on_multiple_firms: number
    members_with_many_matters: number
    top_members: { member: string; matters: number; firms: number; spend: number }[]
    findings: { code: string; severity: string; title: string; detail: string
                amount_at_risk: number; question_for_firm: string; firm: string }[]
    note: string
  }
  no_data: boolean
  no_data_note: string
}

const money = (n: number | null | undefined) =>
  n === null || n === undefined ? '—' : `P ${Math.round(n).toLocaleString()}`

export default function BonuPage() {
  const [dash, setDash] = useState<Dash | null>(null)
  const [fx, setFx] = useState<Forensics | null>(null)
  const [kpis, setKpis] = useState<Kpis | null>(null)
  const [err, setErr] = useState('')
  const [aiBusy, setAiBusy] = useState(false)
  const [ai, setAi] = useState<{ ok: boolean; error?: string | null; result?: Record<string, unknown> } | null>(null)
  // The CFO asked to filter by lawyer and by case type ("for example divorce").
  const [firm, setFirm] = useState('')
  const [lawyer, setLawyer] = useState('')
  const [matterType, setMatterType] = useState('')

  const query = () => {
    const q = new URLSearchParams()
    if (firm) q.set('firm', firm)
    if (lawyer) q.set('lawyer', lawyer)
    if (matterType) q.set('matter_type', matterType)
    const s = q.toString()
    return s ? `?${s}` : ''
  }

  const loadForensics = () => {
    apiFetch<Forensics>(`/bonu/forensics/${query()}`).then(setFx).catch(() => {})
  }

  useEffect(() => {
    // apiFetch<T> returns PARSED data, not a Response — calling .json() on it
    // would have left this page blank.
    Promise.all([
      apiFetch<Dash>('/bonu/dashboard/'),
      apiFetch<Forensics>('/bonu/forensics/'),
      apiFetch<Kpis>('/bonu/schedule/kpis/').catch(() => null),
    ])
      .then(([d, f, k]) => { setDash(d); setFx(f); setKpis(k) })
      .catch(() => setErr('Could not load the BONU figures — refresh to retry.'))
  }, [])

  const runAi = async () => {
    setAiBusy(true); setAi(null)
    try {
      const r = await apiFetch<{ ok: boolean; error?: string | null; result?: Record<string, unknown> }>(
        '/bonu/ai-review/', { method: 'POST', body: JSON.stringify({}) })
      setAi(r)
    } catch {
      setAi({ ok: false, error: 'The AI review could not be reached.' })
    } finally { setAiBusy(false) }
  }

  const maxRev = dash ? Math.max(1, ...dash.months.map((m) => Math.abs(dash.series.revenue?.[m] || 0))) : 1

  return (
    <>
      <TopBar title="BONU — legal benefit" />
      <div className="mx-auto max-w-7xl space-y-5 p-4 md:p-6">

        {err && (
          <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">{err}</div>
        )}

        {/* ── the thing the CFO must see first ───────────────────────────── */}
        {dash && dash.missing_revenue_months.length > 0 && (
          <div className="rounded-xl border-2 p-4" style={{ borderColor: RED, background: '#FEF7F7' }}>
            <div className="flex items-start gap-3">
              <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" style={{ color: RED }} />
              <div>
                <div className="font-bold" style={{ color: RED }}>
                  {dash.missing_revenue_months.length} month(s) of BONU revenue are not in the books
                </div>
                <div className="mt-1 text-sm text-gray-700">
                  Written premium stops after <b>{dash.last_revenue_month}</b>. Missing:{' '}
                  <b>{dash.missing_revenue_months.join(', ')}</b>. At the recent run rate of{' '}
                  <b>{money(dash.revenue_run_rate)}</b> a month that is roughly{' '}
                  <b style={{ color: RED }}>{money(dash.missing_revenue_estimate)}</b> of revenue
                  invoiced but not recognised. Claims for those months <i>are</i> posted, which is why
                  the result below looks worse than it is.
                </div>
              </div>
            </div>
          </div>
        )}

        {/* ── tiles ──────────────────────────────────────────────────────── */}
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
          {[
            { label: 'Written premium', value: dash?.balances.revenue, tone: NAVY, note: 'to last posted month' },
            { label: 'Claims paid', value: dash?.balances.claims, tone: RED, note: 'posted to date' },
            { label: 'Commission', value: dash?.balances.commission, tone: '#B45309', note: '' },
            { label: 'Admin + acquisition', value: (dash ? (dash.balances.admin || 0) + (dash.balances.acquisition || 0) : null), tone: '#B45309', note: '' },
            { label: 'Receivable', value: dash?.balances.receivable, tone: ORANGE, note: 'outstanding' },
            { label: 'Loss ratio', value: null, tone: NAVY, note: '', raw: dash?.loss_ratio_pct != null ? `${dash.loss_ratio_pct}%` : '—' },
          ].map((t) => (
            <Card key={t.label} className="border-gray-200">
              <CardContent className="p-3">
                <div className="text-[10px] uppercase tracking-wide text-gray-500">{t.label}</div>
                <div className="mt-1 text-lg font-extrabold" style={{ color: t.tone }}>
                  {t.raw ?? money(t.value as number)}
                </div>
                {t.note && <div className="text-[10px] text-gray-400">{t.note}</div>}
              </CardContent>
            </Card>
          ))}
        </div>

        {/* ── Omni-computed KPIs beside the author's figures ─────────────── */}
        {kpis && kpis.available && (
          <Card>
            <CardContent className="p-4">
              <div className="mb-3 flex items-center justify-between font-semibold" style={{ color: NAVY }}>
                <span className="inline-flex items-center gap-2">
                  <Scale className="h-4 w-4" /> Scheme profitability &mdash; Omni computed
                </span>
                <span className="text-[11px] font-normal text-gray-400">{kpis.period}</span>
              </div>
              <div className="text-[11px] text-gray-500 mb-3">
                Every figure Omni derives from the schedule, alongside what the author&apos;s Totals cell reads.
                A red gap means the two disagree &mdash; one wrong cell in the workbook is enough to hide the loss.
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-[13px]">
                  <thead>
                    <tr className="text-left text-[11px] uppercase tracking-wide text-gray-500 border-b">
                      <th className="py-2 pr-3">Line</th>
                      <th className="py-2 pr-3 text-right">Omni</th>
                      <th className="py-2 pr-3 text-right">Author</th>
                      <th className="py-2 text-right">Agree</th>
                    </tr>
                  </thead>
                  <tbody className="tabular-nums">
                    {[
                      { k: 'Gross premium', p: kpis.revenue },
                      { k: 'VAT', p: kpis.vat },
                      { k: 'Commission', p: kpis.commission },
                      { k: 'Net premium (base for ratios)', p: kpis.net_premium, hero: true },
                      { k: 'Claims', p: kpis.claims },
                      { k: 'Admin', p: kpis.admin },
                      { k: 'Profit / (loss)', p: kpis.profit_loss, hero: true },
                      { k: 'Loss ratio %', p: kpis.loss_ratio },
                      { k: 'Expense ratio %', p: kpis.expense_ratio },
                      { k: 'Combined ratio %', p: kpis.combined_ratio, hero: true },
                    ].map(({ k, p, hero }) => p ? (
                      <tr key={k} className="border-b last:border-b-0">
                        <td className={`py-1.5 pr-3 ${hero ? 'font-semibold' : ''}`} style={{ color: NAVY }}>{k}</td>
                        <td className="py-1.5 pr-3 text-right" style={{ color: NAVY }}>{p.omni ?? '—'}</td>
                        <td className="py-1.5 pr-3 text-right text-gray-600">{p.author ?? '—'}</td>
                        <td className="py-1.5 text-right">
                          {p.differ ? <span style={{ color: RED }} className="font-semibold">no</span>
                                    : <span style={{ color: GREEN }}>yes</span>}
                        </td>
                      </tr>
                    ) : null)}
                  </tbody>
                </table>
              </div>
              {kpis.break_even?.gross_needed && (
                <div className="mt-3 rounded-lg p-3" style={{ background: '#FFF6E6', borderLeft: `4px solid ${ORANGE}` }}>
                  <div className="text-[12px] font-semibold" style={{ color: NAVY }}>Break-even premium</div>
                  <div className="text-[15px] mt-1" style={{ color: NAVY }}>
                    Gross premium would need to reach <b>P {kpis.break_even.gross_needed}</b>
                    {kpis.break_even.uplift_pct != null && (
                      <> &nbsp;— an uplift of <b>{kpis.break_even.uplift_pct}%</b></>
                    )}
                    &nbsp;to bring the combined ratio to 100% on today&apos;s claims and admin.
                  </div>
                  <div className="text-[11px] text-gray-500 mt-1">{kpis.break_even.note}</div>
                </div>
              )}
            </CardContent>
          </Card>
        )}

        {/* ── monthly shape ──────────────────────────────────────────────── */}
        {dash && (
          <Card>
            <CardContent className="p-4">
              <div className="mb-3 flex items-center gap-2 font-semibold" style={{ color: NAVY }}>
                <TrendingDown className="h-4 w-4" /> Written premium by month
                <span className="ml-auto inline-flex items-center gap-1 text-[10px] font-normal text-gray-400">
                  <Info className="h-3 w-3" /> source: GL 100004
                </span>
              </div>
              <div className="flex items-end gap-1 overflow-x-auto pb-1" style={{ height: 130 }}>
                {dash.months.map((m) => {
                  const v = dash.series.revenue?.[m] || 0
                  const missing = dash.missing_revenue_months.includes(m)
                  return (
                    <div key={m} className="flex min-w-[26px] flex-1 flex-col items-center justify-end gap-1">
                      <div
                        title={`${m}: ${money(v)}`}
                        style={{
                          height: `${Math.max(2, (Math.abs(v) / maxRev) * 100)}px`,
                          width: '100%',
                          background: v ? NAVY : '#FCA5A5',
                          borderRadius: 3,
                        }}
                      />
                      <div className="rotate-45 text-[8px] text-gray-400">{m.slice(2)}</div>
                      {missing && <div className="text-[8px] font-bold" style={{ color: RED }}>gap</div>}
                    </div>
                  )
                })}
              </div>
              <div className="mt-2 text-[11px] text-gray-500">{dash.caveat}</div>
            </CardContent>
          </Card>
        )}

        {/* ── forensics ──────────────────────────────────────────────────── */}
        <Card>
          <CardContent className="p-4">
            <div className="mb-3 flex flex-wrap items-center gap-2 font-semibold" style={{ color: NAVY }}>
              <Scale className="h-4 w-4" /> What the firms billed — forensic checks
              {fx && !fx.no_data && (
                <span className="ml-2 rounded-full px-2 py-0.5 text-[11px] font-bold text-white"
                      style={{ background: fx.summary.amount_at_risk > 0 ? RED : GREEN }}>
                  {money(fx.summary.amount_at_risk)} at risk · {fx.summary.total} findings
                </span>
              )}
              <button
                onClick={runAi}
                disabled={aiBusy || !!fx?.no_data}
                className="ml-auto inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-bold text-white disabled:opacity-40"
                style={{ background: NAVY }}
              >
                {aiBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
                {aiBusy ? 'Reviewing…' : 'Deeper AI review'}
              </button>
            </div>

            {/* ── filters: firm, individual lawyer, case type ─────────────── */}
            <div className="mb-4 flex flex-wrap items-end gap-2 rounded-lg bg-gray-50 p-3">
              <div>
                <div className="mb-1 text-[10px] uppercase tracking-wide text-gray-500">Law firm</div>
                <input value={firm} onChange={(e) => setFirm(e.target.value)} placeholder="any firm"
                       className="w-40 rounded border border-gray-300 px-2 py-1 text-sm" />
              </div>
              <div>
                <div className="mb-1 text-[10px] uppercase tracking-wide text-gray-500">Lawyer</div>
                <input value={lawyer} onChange={(e) => setLawyer(e.target.value)} placeholder="any lawyer"
                       className="w-40 rounded border border-gray-300 px-2 py-1 text-sm" />
              </div>
              <div>
                <div className="mb-1 text-[10px] uppercase tracking-wide text-gray-500">Case type</div>
                <select aria-label="Filter by case type"
                        value={matterType} onChange={(e) => setMatterType(e.target.value)}
                        className="w-48 rounded border border-gray-300 px-2 py-1 text-sm">
                  <option value="">every case type</option>
                  {(fx?.matter_types || []).map((t) => (
                    <option key={t.value} value={t.value}>{t.label}</option>
                  ))}
                </select>
              </div>
              <button onClick={loadForensics}
                      className="rounded-lg px-3 py-1.5 text-xs font-bold text-white"
                      style={{ background: NAVY }}>Apply</button>
              {(firm || lawyer || matterType) && (
                <button onClick={() => { setFirm(''); setLawyer(''); setMatterType(''); apiFetch<Forensics>('/bonu/forensics/').then(setFx).catch(() => {}) }}
                        className="text-xs text-gray-500 underline">clear</button>
              )}
              <div className="ml-auto text-[11px] text-gray-400">
                {fx ? `${fx.lines_checked} line(s) in view` : ''}
              </div>
            </div>

            {/* A capped read must say so. Otherwise a total here sits above a link
                to a detail total, aggregated over everything, that contradicts it —
                and nobody can tell which one to believe. */}
            {fx?.lines_truncated && (
              <div className="rounded-lg px-3 py-2 mb-3 text-[12px] flex items-start gap-2"
                   style={{ background: '#FFFBEB', border: '1px solid #B45309', color: '#92400E' }}>
                <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
                <span>
                  These figures cover the first {fx.line_cap} of {fx.lines_total} billed
                  transactions, so they understate the true total. Open{' '}
                  <Link href="/bonu/lines" className="underline font-semibold">
                    Billed detail
                  </Link>{' '}
                  for the complete figure.
                </span>
              </div>
            )}

            {/* ── spend by case type and by lawyer ────────────────────────── */}
            {fx && !fx.no_data && (fx.by_matter_type?.length > 0 || fx.by_lawyer?.length > 0) && (
              <div className="mb-4 grid gap-3 md:grid-cols-2">
                <div className="rounded-lg border border-gray-200 p-3">
                  <div className="mb-2 text-xs font-bold uppercase tracking-wide text-gray-500">
                    Spend by case type
                  </div>
                  <table className="w-full text-sm">
                    <thead><tr className="text-left text-[10px] uppercase text-gray-400">
                      <th>Type</th><th className="text-right">Matters</th>
                      <th className="text-right">Billed</th><th className="text-right">Avg / matter</th>
                    </tr></thead>
                    <tbody>
                      {(fx.by_matter_type || []).map((t) => (
                        <tr key={t.type} className="border-t border-gray-100">
                          <td className="py-1">
                            <button onClick={() => { setMatterType(t.type); apiFetch<Forensics>(`/bonu/forensics/?matter_type=${t.type}`).then(setFx).catch(() => {}) }}
                                    className="font-medium underline decoration-dotted" style={{ color: NAVY }}>
                              {t.label}
                            </button>
                          </td>
                          <td className="text-right">{t.matters}</td>
                          <td className="text-right">
                            <Link href={`/bonu/lines?matter_type=${t.type}`}
                                  className="underline decoration-dotted"
                                  title="Show the transactions behind this figure">
                              {money(t.billed)}
                            </Link>
                          </td>
                          <td className="text-right">{money(t.avg_per_matter)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="rounded-lg border border-gray-200 p-3">
                  <div className="mb-2 text-xs font-bold uppercase tracking-wide text-gray-500">
                    Spend by lawyer
                  </div>
                  <table className="w-full text-sm">
                    <thead><tr className="text-left text-[10px] uppercase text-gray-400">
                      <th>Lawyer</th><th>Firm</th><th className="text-right">Hours</th>
                      <th className="text-right">Billed</th><th className="text-right">Eff. rate</th>
                    </tr></thead>
                    <tbody>
                      {(fx.by_lawyer || []).slice(0, 12).map((w) => (
                        <tr key={w.lawyer} className="border-t border-gray-100">
                          <td className="py-1">
                            <button onClick={() => { setLawyer(w.lawyer); apiFetch<Forensics>(`/bonu/forensics/?lawyer=${encodeURIComponent(w.lawyer)}`).then(setFx).catch(() => {}) }}
                                    className="font-medium underline decoration-dotted" style={{ color: NAVY }}>
                              {w.lawyer}
                            </button>
                          </td>
                          <td className="text-[11px] text-gray-500">{w.firm}</td>
                          <td className="text-right">{w.hours || '—'}</td>
                          <td className="text-right">
                            <Link href={`/bonu/lines?lawyer=${encodeURIComponent(w.lawyer)}`}
                                  className="underline decoration-dotted"
                                  title="Show the transactions behind this figure">
                              {money(w.billed)}
                            </Link>
                          </td>
                          <td className="text-right">{w.effective_rate ? money(w.effective_rate) : '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {fx?.no_data ? (
              <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
                <div className="flex items-start gap-2">
                  <FileWarning className="mt-0.5 h-4 w-4 shrink-0" />
                  <div>
                    <b>These checks have no bills to test yet.</b>
                    <div className="mt-1">{fx.no_data_note}</div>
                    <div className="mt-2 text-[12px]">
                      All twelve are built and tested — duplicate matters, rates above the
                      agreed tariff, arithmetic that does not add up, invoices that do not foot,
                      impossible billed days, weekend and holiday work, outliers against a firm&apos;s
                      own normal, round-number bias, Benford&apos;s law, missing member references and
                      work dated outside the invoice period. They switch on as bills are confirmed.
                    </div>
                  </div>
                </div>
              </div>
            ) : (
              <>
                {fx && fx.by_firm.length > 0 && (
                  <div className="mb-4 overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="text-left text-[11px] uppercase tracking-wide text-gray-500">
                          <th className="py-1">Firm</th><th>Lines</th><th className="text-right">Billed</th>
                          <th className="text-right">At risk</th><th className="text-right">Findings</th>
                        </tr>
                      </thead>
                      <tbody>
                        {fx.by_firm.map((f) => (
                          <tr key={f.firm} className="border-t border-gray-100">
                            <td className="py-1.5 font-medium" style={{ color: NAVY }}>
                              <Building2 className="mr-1 inline h-3 w-3 text-gray-400" />{f.firm}
                            </td>
                            <td>{f.lines}</td>
                            <td className="text-right">
                              {/* The figure itself opens the transactions behind it
                                  (Kutlo Keitumele, 11 Aug 2026) — a total nobody can
                                  break down cannot be reconciled or defended. */}
                              <Link href={`/bonu/lines?firm=${encodeURIComponent(f.firm)}`}
                                    className="underline decoration-dotted"
                                    title={`Show the ${f.lines} transactions behind this figure`}>
                                {money(f.billed)}
                              </Link>
                            </td>
                            <td className="text-right font-bold" style={{ color: f.at_risk ? RED : GREEN }}>
                              {money(f.at_risk)}
                            </td>
                            <td className="text-right">{f.findings}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                {/* WHICH MEMBER — the repeat claimer. Members are shown as one-way tokens:
                    the same person always reads as the same token, and the token cannot be
                    turned back into a name (CFO 2026-08-03). */}
                {fx?.members && fx.members.members_identified ? (
                  <div className="mb-4 rounded-xl border p-4" style={{ borderColor: '#F3D6D6', background: '#FFFBFB' }}>
                    <div className="flex items-center gap-2 text-[14px] font-bold" style={{ color: RED }}>
                      <Users className="h-4 w-4" />
                      The same member, again
                    </div>
                    <div className="mt-1 max-w-3xl text-[12px]" style={{ color: '#6B7280' }}>
                      Each firm only sees its own file, so none of them can tell a claim is a
                      repeat. Members appear as a token — the same person always reads the same,
                      and a token cannot be turned back into a name.
                    </div>

                    <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
                      <div>
                        <div className="text-[11px] uppercase" style={{ color: '#6B7280' }}>Members identified</div>
                        <div className="text-[19px] font-bold" style={{ color: NAVY }}>{fx.members.members_identified}</div>
                      </div>
                      <div>
                        <div className="text-[11px] uppercase" style={{ color: '#6B7280' }}>Using 2+ firms</div>
                        <div className="text-[19px] font-bold" style={{ color: fx.members.members_on_multiple_firms ? RED : NAVY }}>
                          {fx.members.members_on_multiple_firms}
                        </div>
                      </div>
                      <div>
                        <div className="text-[11px] uppercase" style={{ color: '#6B7280' }}>3+ matters</div>
                        <div className="text-[19px] font-bold" style={{ color: NAVY }}>{fx.members.members_with_many_matters}</div>
                      </div>
                      <div>
                        <div className="text-[11px] uppercase" style={{ color: '#6B7280' }}>Spend with a member behind it</div>
                        <div className="text-[19px] font-bold" style={{ color: NAVY }}>
                          {fx.members.coverage_pct == null ? '—' : `${fx.members.coverage_pct}%`}
                        </div>
                        <div className="text-[11px]" style={{ color: '#6B7280' }}>{money(fx.members.spend_tied_to_a_member)}</div>
                      </div>
                    </div>

                    <div className="mt-3 space-y-2">
                      {fx.members.findings.slice(0, 8).map((f, i) => (
                        <div key={i} className="rounded-lg bg-white p-3" style={{ border: '1px solid #F3D6D6' }}>
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="rounded px-1.5 py-0.5 text-[10px] font-bold uppercase text-white"
                                  style={{ background: SEV_COLOR[f.severity] || '#6B7280' }}>
                              {f.severity}
                            </span>
                            <span className="font-semibold" style={{ color: NAVY }}>{f.title}</span>
                            {f.amount_at_risk ? (
                              <span className="ml-auto font-semibold" style={{ color: RED }}>{money(f.amount_at_risk)}</span>
                            ) : null}
                          </div>
                          <div className="mt-1 text-[13px]" style={{ color: '#374151' }}>{f.detail}</div>
                          {f.question_for_firm ? (
                            <div className="mt-1 text-[12px] italic" style={{ color: '#6B7280' }}>
                              Ask the firm: {f.question_for_firm}
                            </div>
                          ) : null}
                        </div>
                      ))}
                    </div>

                    <div className="mt-3 text-[12px]" style={{ color: '#6B7280' }}>{fx.members.note}</div>
                  </div>
                ) : null}

                {/* The firm findings are ranked by money. Printing all of them made the page
                    17,800 pixels tall on the live data — dozens of near-identical outliers —
                    so the largest are shown and the rest are counted honestly rather than
                    quietly dropped. */}
                {fx && fx.findings.length > FINDINGS_SHOWN ? (
                  <div className="mb-2 rounded-lg px-3 py-2 text-[12px]"
                       style={{ background: '#F8FAFC', border: '1px solid #EAEEF3', color: '#374151' }}>
                    Showing the <b>{FINDINGS_SHOWN}</b> biggest of <b>{fx.findings.length}</b> findings,
                    largest amount first. Use the filters above to narrow to a firm or a case type.
                  </div>
                ) : null}

                <div className="space-y-2">
                  {fx?.findings.slice(0, FINDINGS_SHOWN).map((f, i) => (
                    <div key={i} className="rounded-lg border border-gray-200 p-3">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="rounded px-1.5 py-0.5 text-[10px] font-bold uppercase text-white"
                              style={{ background: SEV_COLOR[f.severity] || '#6B7280' }}>
                          {f.severity}
                        </span>
                        <span className="font-semibold" style={{ color: NAVY }}>{f.title}</span>
                        {f.amount_at_risk > 0 && (
                          <span className="font-bold" style={{ color: RED }}>{money(f.amount_at_risk)}</span>
                        )}
                        <span className="ml-auto text-[11px] text-gray-400">
                          {f.firm}{f.invoice ? ` · ${f.invoice}` : ''} · {f.code}
                        </span>
                      </div>
                      {f.detail && <div className="mt-1 text-[13px] text-gray-600">{f.detail}</div>}
                      {f.question_for_firm && (
                        <div className="mt-2 rounded bg-gray-50 p-2 text-[12px] text-gray-700">
                          <b>Ask the firm:</b> {f.question_for_firm}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </>
            )}

            {ai && (
              <div className="mt-4 rounded-lg border p-3"
                   style={{ borderColor: ai.ok ? '#C7D6EA' : '#F3D6D6', background: ai.ok ? '#F5F9FF' : '#FEF7F7' }}>
                <div className="mb-1 flex items-center gap-1.5 text-sm font-bold" style={{ color: NAVY }}>
                  <Sparkles className="h-4 w-4" /> AI review
                  <span className="ml-2 text-[10px] font-normal text-gray-500">
                    firm names anonymised before sending · member data never leaves
                  </span>
                </div>
                {ai.error && <div className="text-[12px] text-red-700">{ai.error}</div>}
                <pre className="max-h-72 overflow-auto whitespace-pre-wrap text-[12px] text-gray-700">
                  {JSON.stringify(ai.result ?? {}, null, 2)}
                </pre>
              </div>
            )}
          </CardContent>
        </Card>

        <div className="pb-6 text-[11px] text-gray-400">
          Read only. This page posts no journals and changes no accounts — GL wiring is deliberately
          not connected yet (CFO, 3 Aug 2026).
        </div>
      </div>
    </>
  )
}
