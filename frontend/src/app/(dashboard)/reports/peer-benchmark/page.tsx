'use client'

/**
 * /reports/peer-benchmark — Alpha Direct against the Botswana short-term
 * market, from the eight signed FY2025 statements bought in September 2026.
 *
 * The screen is built around one finding: on a like-for-like basis Alpha
 * Direct's combined ratio is 100%, so the insurance book itself earns nothing
 * and the reported profit comes from elsewhere. Everything else on the page
 * exists to show where that 100% comes from and who in the market does it
 * better. Data is static reference material — see reporting/peer_benchmark.py.
 */
import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  AlertTriangle, ArrowDownRight, ArrowUpRight, ChevronRight, Minus,
  ShieldAlert, Info,
} from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { getPeerBenchmark, getToken } from '@/lib/api'
import type {
  BenchmarkInsurer, BenchmarkProfile, PeerBenchmarkDetail, PeerBenchmarkReport,
} from '@/lib/api'

/** A missing disclosure is not a zero. Render it as unknown. */
function pct(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return `${v.toFixed(1)}%`
}

function mn(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return new Intl.NumberFormat('en-BW', {
    minimumFractionDigits: 1, maximumFractionDigits: 1,
  }).format(v)
}

/** Which direction is good for a given ratio. Cession, loss, expense and
 *  combined ratios are costs — lower is better. The rest are returns. */
const LOWER_IS_BETTER = new Set([
  'cession_ratio', 'gross_loss_ratio', 'staff_to_revenue', 'staff_to_net_revenue',
  'expense_to_revenue', 'expense_to_net_revenue', 'acquisition_ratio',
  'combined_ratio', 'effective_tax_rate', 'operating_leverage',
])

type Verdict = 'better' | 'worse' | 'level' | 'unknown'

function verdict(key: string, ours: number | null, median: number | null): Verdict {
  if (ours === null || median === null) return 'unknown'
  const gap = ours - median
  // Inside a point of the median is not a real difference at this sample size.
  if (Math.abs(gap) < 1) return 'level'
  const oursIsHigher = gap > 0
  const better = LOWER_IS_BETTER.has(key) ? !oursIsHigher : oursIsHigher
  return better ? 'better' : 'worse'
}

const VERDICT_STYLE: Record<Verdict, { cls: string; Icon: React.ComponentType<{ className?: string }> }> = {
  better:  { cls: 'text-[#047857]', Icon: ArrowUpRight },
  worse:   { cls: 'text-[#B91C1C]', Icon: ArrowDownRight },
  level:   { cls: 'text-slate-500', Icon: Minus },
  unknown: { cls: 'text-slate-300', Icon: Minus },
}

/** The fourteen benchmarks, in the order the CFO reads them. */
const BENCHMARKS: { key: keyof BenchmarkInsurer; label: string; plain: string }[] = [
  { key: 'cession_ratio', label: 'Reinsurance cession', plain: 'Share of premium handed to reinsurers' },
  { key: 'gross_loss_ratio', label: 'Gross loss ratio', plain: 'Claims before reinsurance, over revenue' },
  { key: 'staff_to_revenue', label: 'Salaries to revenue', plain: 'Payroll as a share of revenue' },
  { key: 'staff_to_net_revenue', label: 'Salaries to retained revenue', plain: 'Payroll against what we actually keep' },
  { key: 'expense_to_revenue', label: 'Total expenses to revenue', plain: 'Running costs over revenue' },
  { key: 'expense_to_net_revenue', label: 'Total expenses to retained revenue', plain: 'Running costs over what we keep' },
  { key: 'acquisition_ratio', label: 'Acquisition cost ratio', plain: 'Commission and selling costs over revenue' },
  { key: 'combined_ratio', label: 'Combined ratio', plain: 'Total cost of underwriting. Over 100 loses money' },
  { key: 'insurance_service_margin', label: 'Insurance service margin', plain: 'Profit from insurance before running costs' },
  { key: 'underwriting_share_of_profit', label: 'Profit from underwriting', plain: 'How much of the bottom line insurance produced' },
  { key: 'pat_margin', label: 'Net profit margin', plain: 'Bottom line over revenue' },
  { key: 'return_on_equity', label: 'Return on equity', plain: 'Profit against shareholder money' },
  { key: 'return_on_assets', label: 'Return on assets', plain: 'Profit against everything we own' },
  { key: 'solvency_ratio', label: 'Solvency (equity to assets)', plain: 'Capital cushion' },
]

export default function PeerBenchmarkPage() {
  const router = useRouter()
  const [data, setData] = useState<PeerBenchmarkReport | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    getPeerBenchmark()
      .then(setData)
      // A swallowed failure renders an empty shell and nobody notices the page
      // was never connected — say what went wrong instead.
      .catch((e) => setError(e instanceof Error ? e.message : 'Could not load the benchmark'))
      .finally(() => setLoading(false))
  }, [router])

  return (
    <div>
      <TopBar
        title="Market Benchmark"
        breadcrumbs={[{ label: 'Reports' }, { label: 'Market Benchmark' }]}
      />

      {loading && <div className="p-8 text-sm text-slate-400">Loading the benchmark…</div>}

      {error && (
        <div className="p-8 max-w-xl mx-auto">
          <div className="rounded-xl border border-red-200 bg-red-50 p-6 flex gap-3">
            <ShieldAlert className="h-5 w-5 text-red-600 flex-shrink-0 mt-0.5" />
            <div>
              <p className="font-medium text-red-800">Could not load the benchmark</p>
              <p className="text-sm text-red-700 mt-1">{error}</p>
            </div>
          </div>
        </div>
      )}

      {data && <Benchmark data={data} />}
    </div>
  )
}

function Benchmark({ data }: { data: PeerBenchmarkReport }) {
  const us = data.us
  const median = data.peer_median

  return (
    <div className="p-6 space-y-6 max-w-[1400px]">
      <HeadlineTiles us={us} median={median} />
      <VerdictBox us={us} median={median} />
      <BenchmarkTable us={us} median={median} />
      <ClassSection d={data.detail} />
      <ReinsuranceSection d={data.detail} />
      <CostBaseSection d={data.detail} />
      <ExpenseLinesSection d={data.detail} />
      <MarketTable insurers={data.insurers} />
      <CompetitorsSection d={data.detail} insurers={data.insurers} />
      <ActionSection />
      <SummaryStatement />
      <TrendStrip history={data.history} />
      <LimitationsSection />

      <p className="text-xs text-slate-400 leading-relaxed pt-2">
        All figures in {data.units}. {data.sources} Year-ends differ across the
        market, so these are period-adjacent comparisons, not period-identical
        ones — each insurer&apos;s own year-end is shown in the table.
      </p>
    </div>
  )
}

function HeadlineTiles({ us, median }: { us: BenchmarkInsurer; median: Record<string, number | null> }) {
  const tiles = [
    {
      label: 'Underwriting profit',
      value: `P${mn(us.underwriting_profit)}m`,
      sub: 'Insurance only. Excludes investment and other income.',
      alarm: (us.underwriting_profit ?? 0) < 1,
    },
    {
      label: 'Combined ratio',
      value: pct(us.combined_ratio),
      sub: `Market median ${pct(median.combined_ratio)}. Over 100 means the book loses money.`,
      alarm: (us.combined_ratio ?? 0) >= 99,
    },
    {
      label: 'Premium given to reinsurers',
      value: pct(us.cession_ratio),
      sub: `Market median ${pct(median.cession_ratio)}.`,
      alarm: (us.cession_ratio ?? 0) > (median.cession_ratio ?? 100),
    },
    {
      label: 'Profit that came from underwriting',
      value: pct(us.underwriting_share_of_profit),
      sub: `Market median ${pct(median.underwriting_share_of_profit)}.`,
      alarm: (us.underwriting_share_of_profit ?? 0) < 50,
    },
  ]
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      {tiles.map((t) => (
        <Card key={t.label} className={t.alarm ? 'border-[#FECACA] bg-[#FEF2F2]' : ''}>
          <CardContent className="pt-5">
            <p className="text-xs uppercase tracking-wide text-slate-500">{t.label}</p>
            <p className={`text-3xl font-semibold mt-1 ${t.alarm ? 'text-[#B91C1C]' : 'text-[#0D1B2A]'}`}>
              {t.value}
            </p>
            <p className="text-xs text-slate-500 mt-2 leading-snug">{t.sub}</p>
          </CardContent>
        </Card>
      ))}
    </div>
  )
}

/** The plain-English box the CFO asked for. */
function VerdictBox({ us, median }: { us: BenchmarkInsurer; median: Record<string, number | null> }) {
  return (
    <Card className="border-[#F4A623] border-2">
      <CardHeader className="pb-3">
        <CardTitle className="text-base flex items-center gap-2">
          <Info className="h-4 w-4 text-[#F4A623]" />
          In plain English: what the competition does better, and what we fix
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4 text-sm leading-relaxed text-slate-700">
        <div>
          <p className="font-semibold text-[#0D1B2A]">Where we actually stand</p>
          <p className="mt-1">
            We wrote P133.4m of premium and made P2.6m. Phoenix wrote P133.1m —
            the same size company, same market, same year — and made P8.4m.
            Almost all our profit came from things other than insurance. Strip
            out investment and other income and the insurance book earned
            P{mn(us.underwriting_profit)}m. That is the whole finding.
          </p>
        </div>
        <div>
          <p className="font-semibold text-[#0D1B2A]">What they do better than us</p>
          <ul className="mt-1 space-y-1.5 list-disc pl-5">
            <li>
              <strong>They keep their premium.</strong> We hand{' '}
              {pct(us.cession_ratio)} of every pula to reinsurers. The market
              median is {pct(median.cession_ratio)}. Hollard keeps three quarters
              of its book and earns P26.4m from underwriting. We keep four pula
              in ten and earn nothing.
            </li>
            <li>
              <strong>They run leaner against what they keep.</strong> Our
              salary bill is 9.8% of revenue against a market median of 9.7% —
              the payroll is in line. The total cost base is 24.8% against a
              median of 19.2%. The bigger issue is the denominator: because we
              cede 60% of premium, the same costs are carried by a much smaller
              retained income.
            </li>
            <li>
              <strong>Their profit is real insurance profit.</strong> Across the
              market, {pct(median.underwriting_share_of_profit)} of the bottom
              line comes from underwriting. For us it is{' '}
              {pct(us.underwriting_share_of_profit)}.
            </li>
          </ul>
        </div>
        <div>
          <p className="font-semibold text-[#0D1B2A]">Where we fix ourselves — reinsurance</p>
          <ul className="mt-1 space-y-1.5 list-disc pl-5">
            <li>
              <strong>Motor is the problem, not the reinsurance of it.</strong>{' '}
              The blended loss ratio of 58.7% looks fine. Underneath it the
              motor book runs at 85.8% and personal-lines motor at 104.5% —
              that book pays out more than it takes in. Ceding 80% of motor is
              not a mistake; it is the only reason the result holds together.
            </li>
            <li>
              <strong>The renewal is the real risk.</strong> The motor slip
              caps the loss ratio it will carry at 80%, and we are running
              above it. At 85.8% the sliding scale pays its 24% floor. There
              is no commission top-up to claim — the reinsurers are losing
              money on our motor book, and that is what gets repriced or
              withdrawn at renewal.
            </li>
            <li>
              <strong>Keep more of what works.</strong> Non-motor runs at
              38.0% and Instant Insurance at 2.4% on a fifth of the book. The
              general quota share cedes 30% of that. Retaining more non-motor
              is where retained income grows without adding risk.
            </li>
            <li>
              <strong>Claim the General QS profit commission.</strong> That
              slip carries 35% ceding commission plus 28.5% profit commission
              on the underwriting year, first calculated 24 months after
              inception. Confirm the calculations have been submitted and
              settled — unlike the motor scale, this one is worth money.
            </li>
            <li>
              <strong>Provisions are sitting on the result.</strong> P5.5m
              against P53.0m of net earned premium — bad debt P2.7m and
              related party P3.1m. Neither is underwriting, but both land on
              the underwriting result.
            </li>
          </ul>
        </div>
        <div className="rounded-lg bg-amber-50 border border-amber-200 p-3">
          <p className="text-xs text-amber-900">
            <strong>One caveat on the numbers.</strong> The Management Accounts
            pack reports a combined ratio of 80% because it divides operating
            expenses by gross written premium while dividing claims by net earned
            premium. Put every element over the same retained-revenue base, as the
            market does, and the answer is 100%. The MA format is frozen and has
            not been touched — this screen simply shows the peer-comparable view
            alongside it.
          </p>
        </div>
      </CardContent>
    </Card>
  )
}

function BenchmarkTable({ us, median }: { us: BenchmarkInsurer; median: Record<string, number | null> }) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base">Fourteen benchmarks against the market median</CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                <th className="px-4 py-2.5 font-medium">Benchmark</th>
                <th className="px-4 py-2.5 font-medium text-right">Alpha Direct</th>
                <th className="px-4 py-2.5 font-medium text-right">Market median</th>
                <th className="px-4 py-2.5 font-medium text-right">Gap</th>
                <th className="px-4 py-2.5 font-medium">What it means</th>
              </tr>
            </thead>
            <tbody>
              {BENCHMARKS.map((b) => {
                const ours = us[b.key] as number | null
                const med = median[b.key as string] ?? null
                const v = verdict(b.key as string, ours, med)
                const { cls, Icon } = VERDICT_STYLE[v]
                const gap = ours !== null && med !== null ? ours - med : null
                return (
                  <tr key={b.key as string} className="border-b last:border-0 hover:bg-slate-50/60">
                    <td className="px-4 py-2.5 font-medium text-[#0D1B2A]">{b.label}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums font-semibold">{pct(ours)}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums text-slate-500">{pct(med)}</td>
                    <td className={`px-4 py-2.5 text-right tabular-nums font-medium ${cls}`}>
                      <span className="inline-flex items-center gap-1 justify-end">
                        <Icon className="h-3.5 w-3.5" />
                        {gap === null ? '—' : `${gap > 0 ? '+' : ''}${gap.toFixed(1)}`}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-xs text-slate-500">{b.plain}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        <p className="px-4 py-3 text-xs text-slate-400 border-t">
          Median excludes WestSure, which failed its capital test in FY25 and is
          not a benchmark. It still appears in the market table below.
        </p>
      </CardContent>
    </Card>
  )
}

function MarketTable({ insurers }: { insurers: BenchmarkInsurer[] }) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base">The whole market, side by side</CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                <th className="px-3 py-2.5 font-medium">Insurer</th>
                <th className="px-3 py-2.5 font-medium">Year end</th>
                <th className="px-3 py-2.5 font-medium text-right">Revenue</th>
                <th className="px-3 py-2.5 font-medium text-right">Ceded</th>
                <th className="px-3 py-2.5 font-medium text-right">Cession</th>
                <th className="px-3 py-2.5 font-medium text-right">Loss ratio</th>
                <th className="px-3 py-2.5 font-medium text-right">Salaries</th>
                <th className="px-3 py-2.5 font-medium text-right">Combined</th>
                <th className="px-3 py-2.5 font-medium text-right">UW profit</th>
                <th className="px-3 py-2.5 font-medium text-right">Tax</th>
                <th className="px-3 py-2.5 font-medium text-right">Profit</th>
                <th className="px-3 py-2.5 font-medium text-right">ROE</th>
              </tr>
            </thead>
            <tbody>
              {insurers.map((i) => (
                <tr
                  key={i.short}
                  className={`border-b last:border-0 ${
                    i.is_us ? 'bg-[#0D1B2A]/[0.04] font-medium' : 'hover:bg-slate-50/60'
                  }`}
                >
                  <td className="px-3 py-2.5">
                    <span className={i.is_us ? 'text-[#0D1B2A] font-semibold' : ''}>{i.short}</span>
                    {i.note && (
                      <span className="block text-[11px] text-slate-400 font-normal leading-snug max-w-xs mt-0.5">
                        {i.note}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2.5 text-xs text-slate-500 whitespace-nowrap">{i.year_end}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums">{mn(i.insurance_revenue)}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums">{mn(i.reinsurance_ceded)}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums">{pct(i.cession_ratio)}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums">{pct(i.gross_loss_ratio)}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums">{mn(i.staff_costs)}</td>
                  <td className={`px-3 py-2.5 text-right tabular-nums ${
                    (i.combined_ratio ?? 0) >= 100 ? 'text-[#B91C1C] font-semibold' : ''
                  }`}>
                    {pct(i.combined_ratio)}
                  </td>
                  <td className={`px-3 py-2.5 text-right tabular-nums ${
                    (i.underwriting_profit ?? 0) <= 0 ? 'text-[#B91C1C]' : ''
                  }`}>
                    {mn(i.underwriting_profit)}
                  </td>
                  <td className="px-3 py-2.5 text-right tabular-nums">{mn(i.taxation)}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums">{mn(i.profit_after_tax)}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums">{pct(i.return_on_equity)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  )
}

/** Summary income statement in the Management Accounts shape, with
 *  reinsurance, commission and tax broken out on their own lines. */
const MA = {
  gwp: 133.378, ceded: 80.774, upr: 0.441, nep: 53.045,
  grossClaims: 76.413, recovered: 51.726, salvage: 2.924, netClaims: 21.763,
  riCommission: 21.683, acquisition: 14.209, netAcquisition: 7.474,
  grossProfit: 38.755, opex: 31.381, staff: 13.175, provisions: 5.484,
  depreciation: 1.845, underwriting: 0.045, otherIncome: 2.545,
  pbt: 2.589, tax: 0.0, pat: 2.589,
}

function SummaryStatement() {
  // MA order, and the column foots. An earlier draft listed recoveries and
  // ceding commission as their own lines AND a net-cost-of-reinsurance
  // subtotal that already contained them — adding the column gave the wrong
  // answer, which is exactly the mistake this page criticises elsewhere.
  const m = MA
  const rows: { label: string; value: number; bold?: boolean; indent?: boolean; rule?: boolean }[] = [
    { label: 'Gross Written Premium', value: m.gwp },
    { label: 'Premiums Ceded To Reinsurance', value: -m.ceded },
    { label: 'Change In Unearned Premium Reserve', value: m.upr },
    { label: 'Net Earned Premium', value: m.nep, bold: true, rule: true },
    { label: 'Gross Claims Incurred', value: -m.grossClaims },
    { label: 'Claims Recovered From Reinsurers', value: m.recovered, indent: true },
    { label: 'Subrogations And Salvages', value: m.salvage, indent: true },
    { label: 'Net Claims Incurred', value: -m.netClaims, bold: true, rule: true },
    { label: 'Commission From Reinsurers', value: m.riCommission },
    { label: 'Commissions Paid And Acquisition Costs', value: -m.acquisition, indent: true },
    { label: 'Net Acquisition Income', value: m.netAcquisition, bold: true, rule: true },
    { label: 'Insurance Service Result (Gross Profit)', value: m.grossProfit, bold: true, rule: true },
    { label: 'Operating Expenses', value: -m.opex },
    { label: 'Of which salaries and bonus', value: -m.staff, indent: true },
    { label: 'Provisions', value: -m.provisions },
    { label: 'Depreciation', value: -m.depreciation },
    { label: 'Underwriting Profit', value: m.underwriting, bold: true, rule: true },
    { label: 'Investment And Other Income', value: m.otherIncome },
    { label: 'Profit Before Tax', value: m.pbt, bold: true, rule: true },
    { label: 'Taxation', value: -m.tax },
    { label: 'Profit After Tax', value: m.pat, bold: true, rule: true },
  ]
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base">
          Alpha Direct FY26 summary — reinsurance, commission and tax shown separately
        </CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        <table className="w-full text-sm max-w-2xl">
          <tbody>
            {rows.map((r) => (
              <tr key={r.label} className={r.rule ? 'border-t border-slate-300' : ''}>
                <td className={`px-4 py-2 ${r.indent ? 'pl-8 text-slate-500' : ''} ${
                  r.bold ? 'font-semibold text-[#0D1B2A]' : ''
                }`}>
                  {r.label}
                </td>
                <td className={`px-4 py-2 text-right tabular-nums ${
                  r.bold ? 'font-semibold text-[#0D1B2A]' : ''
                } ${r.value < 0 ? 'text-slate-600' : ''}`}>
                  {`${r.value < 0 ? '(' : ''}${mn(Math.abs(r.value))}${r.value < 0 ? ')' : ''}`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="px-4 py-3 text-xs text-slate-400 border-t">
          Source: Management Accounts workbook, June 2026, restated onto the
          IFRS 17 shape the peers publish. The frozen MA format is unchanged.
        </p>
      </CardContent>
    </Card>
  )
}

function TrendStrip({ history }: { history: PeerBenchmarkReport['history'] }) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base">Our own trend</CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                <th className="px-4 py-2.5 font-medium">Period</th>
                <th className="px-4 py-2.5 font-medium text-right">GWP</th>
                <th className="px-4 py-2.5 font-medium text-right">Ceded</th>
                <th className="px-4 py-2.5 font-medium text-right">Net earned</th>
                <th className="px-4 py-2.5 font-medium text-right">Net claims</th>
                <th className="px-4 py-2.5 font-medium text-right">Operating costs</th>
                <th className="px-4 py-2.5 font-medium text-right">Profit after tax</th>
              </tr>
            </thead>
            <tbody>
              {history.map((h) => (
                <tr key={h.period} className="border-b last:border-0">
                  <td className="px-4 py-2.5">{h.period}</td>
                  <td className="px-4 py-2.5 text-right tabular-nums">{mn(h.gwp)}</td>
                  <td className="px-4 py-2.5 text-right tabular-nums">{mn(h.ceded)}</td>
                  <td className="px-4 py-2.5 text-right tabular-nums">{mn(h.nep)}</td>
                  <td className="px-4 py-2.5 text-right tabular-nums">{mn(h.net_claims)}</td>
                  <td className="px-4 py-2.5 text-right tabular-nums">{mn(h.opex)}</td>
                  <td className="px-4 py-2.5 text-right tabular-nums font-medium">{mn(h.pat)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="px-4 py-3 text-xs text-slate-400 border-t flex items-start gap-1.5">
          <AlertTriangle className="h-3.5 w-3.5 flex-shrink-0 mt-0.5" />
          FY25 gross written premium is P125.1m. Any screen showing P99m for FY25
          is reading the nine-month comparative column, not the full year.
        </p>
      </CardContent>
    </Card>
  )
}

/* ── Level 2: class of business ─────────────────────────────────────────── */

function ClassSection({ d }: { d: PeerBenchmarkDetail }) {
  const c = d.classes
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base">
          Where the money is actually made and lost — by class
        </CardTitle>
        <p className="text-xs text-slate-500 mt-1">{c.basis}. The blended loss
          ratio of {pct(c.blended_loss_ratio)} is an average of two very
          different books.</p>
      </CardHeader>
      <CardContent className="p-0">
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-px bg-slate-200 border-y border-slate-200">
          {[
            { label: 'Motor', s: c.motor, alarm: true },
            { label: 'Non-motor', s: c.non_motor, alarm: false },
            { label: 'Blended — what the summary shows', s: null, alarm: false },
          ].map((t) => (
            <div key={t.label} className={`p-4 ${t.alarm ? 'bg-[#FEF2F2]' : 'bg-white'}`}>
              <p className="text-xs uppercase tracking-wide text-slate-500">{t.label}</p>
              <p className={`text-2xl font-semibold mt-1 ${t.alarm ? 'text-[#B91C1C]' : 'text-[#0D1B2A]'}`}>
                {t.s ? pct(t.s.loss_ratio) : pct(c.blended_loss_ratio)}
              </p>
              <p className="text-xs text-slate-500 mt-1">
                {t.s ? `${pct(t.s.share)} of the book · P${mn(t.s.premium)}m premium` : 'hides both'}
              </p>
            </div>
          ))}
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                <th className="px-4 py-2.5 font-medium">Class</th>
                <th className="px-4 py-2.5 font-medium text-right">Premium</th>
                <th className="px-4 py-2.5 font-medium text-right">Claims</th>
                <th className="px-4 py-2.5 font-medium text-right">Loss ratio</th>
                <th className="px-4 py-2.5 font-medium text-right">Margin</th>
                <th className="px-4 py-2.5 font-medium text-right">Share of book</th>
              </tr>
            </thead>
            <tbody>
              {c.classes.map((r) => {
                const bad = (r.loss_ratio ?? 0) >= 100
                const warn = (r.loss_ratio ?? 0) >= 70 && !bad
                return (
                  <tr key={r.name} className={`border-b last:border-0 ${bad ? 'bg-[#FEF2F2]' : ''}`}>
                    <td className="px-4 py-2.5">
                      {r.name}
                      {r.motor && <span className="ml-2 text-[10px] uppercase tracking-wide text-slate-400">motor</span>}
                    </td>
                    <td className="px-4 py-2.5 text-right tabular-nums">{mn(r.premium)}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums">{mn(r.claims)}</td>
                    <td className={`px-4 py-2.5 text-right tabular-nums font-semibold ${
                      bad ? 'text-[#B91C1C]' : warn ? 'text-[#92400E]' : 'text-[#047857]'
                    }`}>{pct(r.loss_ratio)}</td>
                    <td className={`px-4 py-2.5 text-right tabular-nums ${r.margin < 0 ? 'text-[#B91C1C]' : ''}`}>
                      {r.margin < 0 ? `(${mn(-r.margin)})` : mn(r.margin)}
                    </td>
                    <td className="px-4 py-2.5 text-right tabular-nums text-slate-500">{pct(r.share_of_book)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  )
}

/* ── Level 2: reinsurance structure ─────────────────────────────────────── */

function ReinsuranceSection({ d }: { d: PeerBenchmarkDetail }) {
  const m = d.motor_entitlement
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base">Reinsurance — treaty by treaty</CardTitle>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="rounded-lg border border-[#FDE68A] bg-[#FFFBEB] p-4">
          <p className="text-sm font-semibold text-[#92400E]">
            The motor treaty is carrying a book that runs at {pct(m.loss_ratio)}
          </p>
          <p className="text-sm text-amber-900 mt-1.5 leading-relaxed">
            The sliding scale pays {pct(m.commission)} at that loss ratio — its
            floor — and the treaty caps the loss ratio it will carry at 80%. We
            booked {pct(d.adic_ceding_commission_rate)} blended across all
            treaties, which is slightly above the motor floor because the
            general quota share pays 35%. {m.note}
          </p>
        </div>

        <div>
          <p className="text-xs uppercase tracking-wide text-slate-500 mb-2">Our treaties, 2025/26</p>
          <div className="space-y-2">
            {d.adic_treaties.map((t) => (
              <div key={t.name} className="rounded-lg border border-slate-200 p-3">
                <p className="text-sm font-medium text-[#0D1B2A]">{t.name}</p>
                <p className="text-xs text-slate-600 mt-1"><strong>Cession:</strong> {t.cession}</p>
                <p className="text-xs text-slate-600 mt-0.5"><strong>Commission:</strong> {t.commission}</p>
                <p className="text-xs text-slate-400 mt-0.5">{t.reinsurers}</p>
              </div>
            ))}
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
          <div>
            <p className="text-xs uppercase tracking-wide text-slate-500 mb-2">
              BIC cedes its property and keeps its motor
            </p>
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-xs text-slate-500">
                  <th className="py-1.5 font-medium">Class</th>
                  <th className="py-1.5 font-medium text-right">Revenue</th>
                  <th className="py-1.5 font-medium text-right">Ceded</th>
                  <th className="py-1.5 font-medium text-right">%</th>
                </tr>
              </thead>
              <tbody>
                {d.bic_cession.map((r) => (
                  <tr key={r.cls} className="border-b last:border-0">
                    <td className="py-1.5">{r.cls}</td>
                    <td className="py-1.5 text-right tabular-nums">{mn(r.revenue)}</td>
                    <td className="py-1.5 text-right tabular-nums">{mn(r.ceded)}</td>
                    <td className={`py-1.5 text-right tabular-nums font-medium ${
                      r.pct < 10 ? 'text-[#047857]' : ''
                    }`}>{pct(r.pct)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="text-xs text-slate-500 mt-2 leading-snug">
              Motor 2.0% ceded, property 79.2%. We cede 80% of motor. That is
              the structural difference between our book and theirs.
            </p>
          </div>
          <div>
            <p className="text-xs uppercase tracking-wide text-slate-500 mb-2">
              Hollard, by treaty type
            </p>
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-xs text-slate-500">
                  <th className="py-1.5 font-medium">Treaty</th>
                  <th className="py-1.5 font-medium text-right">Ceded</th>
                  <th className="py-1.5 font-medium text-right">Recovered</th>
                  <th className="py-1.5 font-medium text-right">Net cost</th>
                </tr>
              </thead>
              <tbody>
                {d.hollard_cession.map((r) => (
                  <tr key={r.treaty} className="border-b last:border-0">
                    <td className="py-1.5">{r.treaty}</td>
                    <td className="py-1.5 text-right tabular-nums">{mn(r.ceded)}</td>
                    <td className="py-1.5 text-right tabular-nums">{mn(r.recovered)}</td>
                    <td className={`py-1.5 text-right tabular-nums ${r.net < 0 ? 'text-[#047857]' : ''}`}>
                      {r.net < 0 ? `(${mn(-r.net)})` : mn(r.net)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="text-xs text-slate-500 mt-2 leading-snug">
              Hollard buys mostly facultative cover on individual large risks —
              23.9% of revenue in total — rather than handing over a whole class.
            </p>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

/* ── Level 2: cost base ─────────────────────────────────────────────────── */

function CostBaseSection({ d }: { d: PeerBenchmarkDetail }) {
  const cb = d.cost_base
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base">Cost base, counted properly</CardTitle>
        <p className="text-xs text-slate-500 mt-1">
          Peers report running costs in two places — inside insurance service
          expenses, and below the insurance result. Counting only the second
          understates them: Insure Guard reads 19.2% below the line and 44.9%
          in truth. Claims and commission are excluded throughout.
        </p>
      </CardHeader>
      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                <th className="px-3 py-2.5 font-medium">Insurer</th>
                <th className="px-3 py-2.5 font-medium text-right">Revenue</th>
                <th className="px-3 py-2.5 font-medium text-right">Staff in ISE</th>
                <th className="px-3 py-2.5 font-medium text-right">Staff below</th>
                <th className="px-3 py-2.5 font-medium text-right">Staff total</th>
                <th className="px-3 py-2.5 font-medium text-right">Staff %</th>
                <th className="px-3 py-2.5 font-medium text-right">Below line only</th>
                <th className="px-3 py-2.5 font-medium text-right">TRUE total</th>
                <th className="px-3 py-2.5 font-medium text-right">True %</th>
              </tr>
            </thead>
            <tbody>
              {cb.rows.map((r) => (
                <tr key={r.name} className={`border-b last:border-0 ${r.is_us ? 'bg-[#0D1B2A]/[0.04] font-medium' : ''}`}>
                  <td className="px-3 py-2.5">
                    <span className={r.is_us ? 'font-semibold text-[#0D1B2A]' : ''}>{r.name}</span>
                    {r.note && (
                      <span className="block text-[11px] text-slate-400 font-normal leading-snug max-w-md mt-0.5">
                        {r.note}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2.5 text-right tabular-nums">{mn(r.revenue)}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums text-slate-500">{mn(r.staff_ise)}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums text-slate-500">{mn(r.staff_below)}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums">{mn(r.staff_total)}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums">{pct(r.staff_pct)}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums text-slate-400">{mn(r.below_line_only)}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums font-semibold">{mn(r.total_cost)}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums font-semibold">{pct(r.total_pct)}</td>
                </tr>
              ))}
              <tr className="bg-slate-50 text-xs">
                <td className="px-3 py-2 text-slate-500" colSpan={5}>Peer median (excludes WestSure)</td>
                <td className="px-3 py-2 text-right tabular-nums text-slate-600">{pct(cb.median_staff_pct)}</td>
                <td className="px-3 py-2"></td>
                <td className="px-3 py-2"></td>
                <td className="px-3 py-2 text-right tabular-nums text-slate-600">{pct(cb.median_total_pct)}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p className="px-4 py-3 text-xs text-slate-500 border-t leading-relaxed">
          Our salary bill is {pct(cb.rows[0].staff_pct)} of revenue against a
          market median of {pct(cb.median_staff_pct)} — in line. The payroll is
          not the problem. The problem is that we keep only four pula in ten,
          so the same costs sit on a much smaller retained income.
        </p>
      </CardContent>
    </Card>
  )
}

/* ── Level 3: our own expense lines ─────────────────────────────────────── */

function ExpenseLinesSection({ d }: { d: PeerBenchmarkDetail }) {
  const e = d.expenses
  const [open, setOpen] = useState<string | null>('People')
  const p0 = (v: number) => new Intl.NumberFormat('en-BW', { maximumFractionDigits: 0 }).format(v)
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base">Every cost line we run</CardTitle>
        <p className="text-xs text-slate-500 mt-1">{e.basis} Click a group to open it.</p>
      </CardHeader>
      <CardContent className="p-0">
        {e.groups.map((g) => {
          const isOpen = open === g.group
          return (
            <div key={g.group} className="border-b last:border-0">
              <button
                onClick={() => setOpen(isOpen ? null : g.group)}
                className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-slate-50 transition-colors"
              >
                <ChevronRight className={`h-4 w-4 text-slate-400 transition-transform ${isOpen ? 'rotate-90' : ''}`} />
                <span className="font-medium text-[#0D1B2A] flex-1">{g.group}</span>
                <span className="text-xs text-slate-500 w-14 text-right">{pct(g.share)}</span>
                <span className="tabular-nums text-sm w-28 text-right">P{p0(g.fy26)}</span>
                <span className={`tabular-nums text-xs w-24 text-right ${
                  g.change > 0 ? 'text-[#B91C1C]' : 'text-[#047857]'
                }`}>
                  {g.change > 0 ? '+' : ''}{p0(g.change)}
                </span>
              </button>
              {isOpen && (
                <table className="w-full text-sm bg-slate-50/60">
                  <thead>
                    <tr className="text-left text-xs text-slate-500 border-y">
                      <th className="pl-11 pr-4 py-1.5 font-medium">Line</th>
                      <th className="px-4 py-1.5 font-medium text-right">FY25</th>
                      <th className="px-4 py-1.5 font-medium text-right">FY26</th>
                      <th className="px-4 py-1.5 font-medium text-right">Change</th>
                      <th className="px-4 py-1.5 font-medium text-right">%</th>
                    </tr>
                  </thead>
                  <tbody>
                    {g.lines.map((l) => (
                      <tr key={l.line} className="border-b last:border-0 border-slate-200">
                        <td className="pl-11 pr-4 py-1.5 text-slate-700">{l.line}</td>
                        <td className="px-4 py-1.5 text-right tabular-nums text-slate-500">{p0(l.fy25)}</td>
                        <td className="px-4 py-1.5 text-right tabular-nums">{p0(l.fy26)}</td>
                        <td className={`px-4 py-1.5 text-right tabular-nums ${
                          l.change > 0 ? 'text-[#B91C1C]' : l.change < 0 ? 'text-[#047857]' : 'text-slate-400'
                        }`}>{l.change > 0 ? '+' : ''}{p0(l.change)}</td>
                        <td className="px-4 py-1.5 text-right tabular-nums text-xs text-slate-500">
                          {l.change_pct === null ? '—' : `${l.change_pct > 0 ? '+' : ''}${l.change_pct.toFixed(0)}%`}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )
        })}
        <div className="flex items-center gap-3 px-4 py-3 bg-slate-100 font-semibold text-sm">
          <span className="w-4" />
          <span className="flex-1">Total</span>
          <span className="w-14" />
          <span className="tabular-nums w-28 text-right">P{p0(e.total_fy26)}</span>
          <span className="tabular-nums w-24 text-right text-[#B91C1C]">
            +{p0(e.total_fy26 - e.total_fy25)}
          </span>
        </div>
      </CardContent>
    </Card>
  )
}

/* ── Level 3: the competitors, one by one ──────────────────────────────── */

/** Units differ by insurer, so every profile prints its own and nothing is
 *  scaled. Peers that publish in thousands say so on the row. */
function num(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return new Intl.NumberFormat('en-BW', { maximumFractionDigits: 0 }).format(v)
}

function MiniTable({ head, rows, note }: {
  head: string[]
  rows: { label: string; values: (string | number | null)[]; kind?: 'total' | 'sub' }[]
  note?: string
}) {
  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b text-left text-xs uppercase tracking-wide text-slate-500">
              {head.map((h, i) => (
                <th key={h} className={`py-1.5 font-medium ${i ? 'text-right' : ''}`}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.label} className={`border-b last:border-0 ${
                r.kind === 'total' ? 'font-semibold bg-slate-50' : ''
              }`}>
                <td className={`py-1.5 ${r.kind === 'sub' ? 'pl-5 text-slate-500' : ''}`}>{r.label}</td>
                {r.values.map((v, i) => (
                  <td key={i} className="py-1.5 text-right tabular-nums">
                    {v === null || v === undefined ? '—' : v}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {note && <p className="text-xs text-slate-500 mt-1.5 leading-snug">{note}</p>}
    </div>
  )
}

function ProfileCard({ p, bm, staffPct, medianStaff }: {
  p: BenchmarkProfile
  bm: BenchmarkInsurer | undefined
  staffPct: number | null
  medianStaff: number | null
}) {
  const [open, setOpen] = useState(false)
  const badge = p.flag === 'twin'
    ? { text: 'Our size twin', cls: 'bg-[#0D1B2A] text-white' }
    : p.flag === 'distressed'
    ? { text: 'Distressed', cls: 'bg-[#B91C1C] text-white' }
    : { text: 'Peer', cls: 'bg-slate-100 text-slate-600' }

  return (
    <div className="border-t-2 border-[#0D1B2A] pt-3">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-start gap-3 text-left group"
      >
        <ChevronRight className={`h-4 w-4 mt-1 flex-shrink-0 text-slate-400 transition-transform ${open ? 'rotate-90' : ''}`} />
        <span className="flex-1">
          <span className="flex flex-wrap items-baseline gap-2">
            <span className="font-semibold text-[#0D1B2A] group-hover:underline">{p.full}</span>
            <span className={`text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded ${badge.cls}`}>
              {badge.text}
            </span>
            <span className="text-xs text-slate-500">{p.ye} · {p.units}</span>
          </span>
          <span className="block text-sm text-slate-600 mt-1 leading-snug">{p.headline}</span>
        </span>
      </button>

      {open && (
        <div className="pl-7 pr-1 py-4 space-y-5">
          <MiniTable
            head={['Result', p.units, 'Ratio']}
            rows={[
              { label: 'Insurance revenue', values: [num(p.revenue), ''] },
              ...(p.gwp ? [{ label: 'Gross written premium', values: [num(p.gwp), ''] }] : []),
              ...(p.nwp ? [{ label: 'Net written premium', values: [num(p.nwp), ''] }] : []),
              { label: 'Insurance service expenses', values: [`(${num(p.ise)})`, ''] },
              { label: 'Net reinsurance cost',
                values: [p.net_ri < 0 ? `${num(-p.net_ri)} income` : `(${num(p.net_ri)})`, ''] },
              { label: 'Insurance service result',
                values: [num(p.isr), pct(bm?.insurance_service_margin)], kind: 'total' },
              { label: 'Profit before tax', values: [num(p.pbt), ''] },
              { label: 'Taxation',
                values: [p.tax < 0 ? `${num(-p.tax)} credit` : `(${num(p.tax)})`,
                         pct(bm?.effective_tax_rate)] },
              { label: 'Profit after tax', values: [num(p.pat), pct(bm?.pat_margin)], kind: 'total' },
              { label: 'Total equity', values: [num(p.te), ''] },
              { label: 'Return on equity', values: ['', pct(bm?.return_on_equity)] },
              { label: 'Combined ratio', values: ['', pct(bm?.combined_ratio)] },
            ]}
          />

          <MiniTable
            head={['What the insurance service expense is made of', p.units]}
            rows={[
              ...p.ise_components.map(([n, v]) => ({
                label: n, values: [v < 0 ? `(${num(-v)})` : num(v)],
              })),
              { label: 'Total insurance service expenses', values: [num(p.ise)], kind: 'total' as const },
            ]}
            note={p.ise_note}
          />

          {p.staff_total !== null ? (
            <MiniTable
              head={['Staff cost', p.units]}
              rows={[
                ...p.staff.map(([n, v]) => ({ label: n, values: [num(v)] })),
                { label: 'Total staff cost', values: [num(p.staff_total)], kind: 'total' as const },
                { label: 'of which inside insurance service expenses',
                  values: [num(p.staff_ise)], kind: 'sub' as const },
                { label: 'of which below the insurance service result',
                  values: [num(p.staff_below)], kind: 'sub' as const },
              ]}
              note={`Staff cost is ${pct(staffPct)} of insurance revenue. Alpha Direct is 9.8%; the market median is ${pct(medianStaff)}.`}
            />
          ) : (
            <p className="text-sm text-slate-500">
              <strong>Staff cost:</strong> not disclosed. This insurer publishes no staff-cost note.
            </p>
          )}

          {p.opex_below.length > 0 && (
            <MiniTable
              head={['Operating expenses below the insurance service result', p.units]}
              rows={[
                ...p.opex_below.map(([n, v]) => ({ label: n, values: [num(v)] })),
                { label: 'Total', values: [num(p.opex_below_total)], kind: 'total' as const },
              ]}
            />
          )}
          {p.opex_ise.length > 0 && (
            <MiniTable
              head={['Operating expenses inside insurance service expenses', p.units]}
              rows={[
                ...p.opex_ise.map(([n, v]) => ({ label: n, values: [num(v)] })),
                { label: 'Total', values: [num(p.opex_ise_total)], kind: 'total' as const },
              ]}
            />
          )}
          {p.mgmt_fee_split && (
            <MiniTable
              head={['Group management fee, by recipient', p.units]}
              rows={p.mgmt_fee_split.map(([n, v]) => ({ label: n, values: [num(v)] }))}
            />
          )}
          {p.classes && (
            <MiniTable
              head={p.classes.some((c) => c[2] !== null)
                ? ['By class of business', 'Revenue', 'Service expense']
                : ['By class of business', 'Revenue']}
              rows={p.classes.map((c) => ({
                label: c[0],
                values: c[2] !== null && c[2] !== undefined
                  ? [num(c[1]), `(${num(c[2])})`]
                  : [num(c[1])],
              }))}
              note={p.class_note ?? (p.classes.every((c) => c[2] === null)
                ? 'Claims are not split by class in this statement.' : undefined)}
            />
          )}
          {p.ri_components ? (
            <MiniTable
              head={['Reinsurance', p.units]}
              rows={[
                ...p.ri_components.map(([n, v]) => ({
                  label: n, values: [v < 0 ? `(${num(-v)})` : num(v)],
                })),
                { label: 'Net expense from reinsurance contracts held',
                  values: [num(p.net_ri)], kind: 'total' as const },
              ]}
              note="Ceded less recovered does not equal the net cost; the components above are what the statement discloses and they sum exactly to it."
            />
          ) : (
            <MiniTable
              head={['Reinsurance', p.units]}
              rows={[
                { label: 'Premium ceded', values: [num(p.ceded)] },
                { label: 'Claims recovered', values: [num(p.recovered)] },
                ...(p.ri_commission !== null
                  ? [{ label: 'Ceding commission received', values: [num(p.ri_commission)] }] : []),
                ...((p.ri_other ?? []).map(([n, v]) => ({ label: n, values: [num(v)] }))),
                { label: 'Net cost of reinsurance', values: [num(p.net_ri)], kind: 'total' as const },
              ]}
              note={p.ri_note}
            />
          )}
          {p.cession && (
            <MiniTable
              head={['Cession by class', 'Revenue', 'Ceded', '% ceded']}
              rows={p.cession.map((c) => ({
                label: c[0], values: [num(c[1]), num(c[2]), pct(c[2] / c[1] * 100)],
              }))}
            />
          )}
          {p.treaty_cession && (
            <MiniTable
              head={['By treaty type', 'Ceded', 'Recovered']}
              rows={p.treaty_cession.map((t) => ({ label: t[0], values: [num(t[1]), num(t[2])] }))}
            />
          )}
          <p className="text-sm text-slate-600 leading-relaxed">
            <strong className="text-[#0D1B2A]">Capital and other disclosures.</strong> {p.pct_note}
          </p>
        </div>
      )}
    </div>
  )
}

function CompetitorsSection({ d, insurers }: {
  d: PeerBenchmarkDetail
  insurers: BenchmarkInsurer[]
}) {
  const by = Object.fromEntries(insurers.map((i) => [i.short, i]))
  const cb = Object.fromEntries(d.cost_base.rows.map((r) => [r.name, r]))
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base">The competitors, one by one</CardTitle>
        <p className="text-xs text-slate-500 mt-1">
          Each statement transcribed at the level it publishes — every expense line, class
          split and reinsurance component. Click a name to open it.
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        {d.profiles.map((p) => (
          <ProfileCard
            key={p.key}
            p={p}
            bm={by[p.key]}
            staffPct={cb[p.key]?.staff_pct ?? null}
            medianStaff={d.cost_base.median_staff_pct}
          />
        ))}
      </CardContent>
    </Card>
  )
}

/* ── Level 3: what to do ───────────────────────────────────────────────── */

function ActionSection() {
  const actions = [
    { n: 1, t: 'Motor underwriting', worth: 'P8 – 11m a year',
      body: 'Motor premium was P41.684m over the nine months to March 2026, annualising to about P55.6m. Bringing the loss ratio from 85.8% to 70% saves 15.8 points: P6.6m over nine months, roughly P8.8m on a full year. It would also move the motor treaty commission off its 24% floor to around 28%.',
      needs: 'Personal-lines motor (104.5%) is re-rated or exited, and corporate motor (73.6%) tightened. An underwriting and claims exercise, not a finance one — and it will cost premium volume in the short term.' },
    { n: 2, t: 'Cost base', worth: 'P4 – 13m',
      body: 'Our running cost base is 24.8% of revenue against a peer median of 19.2%. Closing half that gap is P3.7m. Matching Phoenix, on an almost identical book, is P12.6m.',
      needs: 'Look first at consultancy (P2.19m, up 192%) and the technology stack (P4.98m across software maintenance, Risk AI, AWS and licensing). Salaries at 9.8% of revenue are in line with the market and are not the place to start.' },
    { n: 3, t: 'Retain more non-motor', worth: 'P3 – 5m of retained income',
      body: 'Non-motor runs at 38.0% and the general quota share cedes 30% of it. Reducing that cession increases retained income at a loss ratio less than half the motor book’s.',
      needs: 'Capital. Operating leverage is already 7.7 times equity against a peer median of 2.2. Retention cannot rise without retained earnings or an injection, and the motor renewal may consume whatever headroom exists.' },
    { n: 4, t: 'Provisions', worth: 'P2.7 – 5.5m',
      body: 'P5.484m sits on the underwriting result — bad debt P2.691m, related party P3.095m, less a P0.301m IBNR credit. Neither large component is an underwriting loss.',
      needs: 'The low case recovers bad debt only. The high case assumes the related-party balance is settled or written off against the intercompany position — a board decision, not a collections exercise.' },
    { n: 5, t: 'Claim the General QS profit commission', worth: 'Unknown but real',
      body: '28.5% of the underwriting-year profit on the general quota share, first calculated 24 months after inception. On a non-motor book running at 38% this should be paying.',
      needs: 'Administrative: confirm the calculations have been prepared, submitted with the fourth-quarter account, and settled.' },
  ]
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base">Where we fix ourselves</CardTitle>
        <p className="text-xs text-slate-500 mt-1">
          Ordered by how much each moves the result.
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        {actions.map((a) => (
          <div key={a.n} className="flex gap-3">
            <span className="flex-shrink-0 w-6 h-6 rounded-full bg-[#0D1B2A] text-white text-xs
                             font-semibold flex items-center justify-center mt-0.5">{a.n}</span>
            <div className="flex-1">
              <p className="font-semibold text-[#0D1B2A]">
                {a.t} <span className="font-normal text-[#F4A623]">· {a.worth}</span>
              </p>
              <p className="text-sm text-slate-600 mt-1 leading-relaxed">{a.body}</p>
              <p className="text-sm text-slate-500 mt-1 leading-relaxed">
                <strong>What has to be true:</strong> {a.needs}
              </p>
            </div>
          </div>
        ))}
        <div className="rounded-lg border border-slate-200 overflow-hidden mt-2">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                <th className="px-3 py-2 font-medium">Action</th>
                <th className="px-3 py-2 font-medium text-right">Low</th>
                <th className="px-3 py-2 font-medium text-right">High</th>
              </tr>
            </thead>
            <tbody>
              {[['Motor loss ratio to 70% (annualised)', '8.8', '10.3'],
                ['Cost base toward the peer median', '3.7', '12.6'],
                ['Retain more non-motor', '3.0', '5.0'],
                ['Provisions', '2.7', '5.5'],
                ['General QS profit commission', '—', '—'],
                ['TOTAL indicative', '18.2', '33.4']].map((r) => (
                <tr key={r[0]} className={`border-t ${r[0].startsWith('TOTAL') ? 'font-semibold bg-slate-50' : ''}`}>
                  <td className="px-3 py-1.5">{r[0]}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{r[1]}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{r[2]}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="text-xs text-slate-500 leading-relaxed">
          Indicative ranges built from the gaps to the peer median, not a budget. Every one
          depends on operational action finance does not control. They are shown together
          only to make the point that the gap between us and a normally profitable insurer
          of this size is roughly P18 to P33 million of annual result — against a current
          profit after tax of P2.6 million.
        </p>
      </CardContent>
    </Card>
  )
}

function LimitationsSection() {
  const limits = [
    ['Different year-ends', 'Hollard and Sunshine close 30 June, WestSure 28 February, five peers 31 December. A market that moved sharply mid-year would distort every comparison here.'],
    ['Different accounting bases', 'Every peer reports under IFRS 17; our management accounts do not. The reconciliation is a judgement, not a fact.'],
    ['Class analysis is nine months', 'The whole motor argument rests on figures to March 2026, because the June 2026 pack carries no class split.'],
    ['Thin peer disclosure', 'No peer publishes headcount. Old Mutual publishes four expense lines in total. Sunshine publishes no staff-cost note. Only BIC publishes cession by class.'],
    ['Three scanned sources', 'Phoenix, Sunshine and Bryte were read by optical character recognition. Bryte is the worst — two of its expense sub-notes disagree in the source, and neither is relied on.'],
    ['No claims-development view', 'A 2.4% loss ratio on Instant Insurance may be a genuinely excellent book or an immature one. Nothing in the management accounts distinguishes the two.'],
  ]
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base">What this analysis cannot tell you</CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        <table className="w-full text-sm">
          <tbody>
            {limits.map(([k, v]) => (
              <tr key={k} className="border-b last:border-0">
                <td className="px-4 py-2.5 font-medium text-[#0D1B2A] align-top w-56">{k}</td>
                <td className="px-4 py-2.5 text-slate-600 leading-relaxed">{v}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </CardContent>
    </Card>
  )
}
