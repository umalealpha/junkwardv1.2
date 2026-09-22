'use client'

/**
 * /bonu/panel — the league table, the cases running hot, and what is still to come.
 *
 * CFO 2026-08-03, recommendations 5, 6 and 10. Three questions this screen answers and the
 * overview cannot:
 *   · Which firm is expensive, slow, or folds the moment it is queried?
 *   · Which single case is running away right now, while it is still worth a phone call?
 *   · What will the open cases cost us that has not been billed yet?
 *
 * Read only. Every figure is computed from our own bills — no imported benchmark, because a
 * Botswana panel rate is not a published number. Nothing here posts to the ledger.
 *
 * Backend: /api/v1/bonu/panel/
 */

import { useEffect, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { apiFetch } from '@/lib/api'
import { Flame, Gavel, Hourglass, Loader2, TrendingUp } from 'lucide-react'
import {
  AMBER, BonuTabs, GREEN, LINE, NAVY, Note, ORANGE, RED, Stat, Table, money, money2, td, tdNum, trBorder,
} from '../_shared'

interface LeagueRow {
  firm_id: string; firm: string; agreed_hourly_rate: number | null
  spend: number; matters: number; cost_per_matter: number | null
  effective_hourly_rate: number | null; over_tariff: boolean
  open_cases: number; cases_gone_quiet: number; median_days_to_close: number | null
  queries_raised: number; amount_queried: number; amount_conceded: number
  concede_rate: number | null; median_days_to_answer: number | null
}
interface WarnRow {
  firm: string; matter_ref: string; matter_type: string; spend: number
  typical_cost: number | null; multiple: number | null
  running_hot: boolean; over_approval: boolean; message: string
}
interface PanelData {
  league: LeagueRow[]
  early_warnings: WarnRow[]
  hot_cases: number
  typical_cost: { matter_type: string; matters: number; typical: number | null; basis: string; total_spend: number }[]
  unbilled: {
    as_of: string; open_cases: number; estimated: number; cases_priced: number
    cases_not_priced: number; caveat: string
    rows: { case_ref: string; matter_type: string; status: string; typical_cost: number; billed_so_far: number; still_to_come: number; days_open: number }[]
  }
  classification: { total: number; unconfirmed: number; unconfirmed_pct: number | null; note: string }
  thresholds: { matter_type: string; approval_above: number; typical_cost: number | null; warn_multiple: number }[]
  no_data: boolean
  no_data_note: string
}

export default function BonuPanelPage() {
  const [d, setD] = useState<PanelData | null>(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    apiFetch<PanelData>('/bonu/panel/')
      .then(setD)
      .catch(() => setErr('Could not load the panel figures.'))
  }, [])

  if (err) {
    return (
      <div className="min-h-screen" style={{ background: '#F6F8FB' }}>
        <TopBar title="BONU — panel" />
        <div className="mx-auto max-w-[1400px] px-6 py-5">
          <BonuTabs active="/bonu/panel" />
          <div className="mt-5"><Note tone="warn" title="Not loaded">{err}</Note></div>
        </div>
      </div>
    )
  }

  if (!d) {
    return (
      <div className="flex min-h-screen items-center justify-center" style={{ background: '#F6F8FB' }}>
        <Loader2 className="h-5 w-5 animate-spin" style={{ color: ORANGE }} />
      </div>
    )
  }

  const totalSpend = d.league.reduce((s, r) => s + (r.spend || 0), 0)
  const recovered = d.league.reduce((s, r) => s + (r.amount_conceded || 0), 0)

  return (
    <div className="min-h-screen" style={{ background: '#F6F8FB' }}>
      <TopBar title="BONU — panel" />
      <div className="mx-auto max-w-[1400px] px-6 py-5">
        <BonuTabs active="/bonu/panel" />

        {d.no_data ? (
          <div className="mt-5"><Note title="Nothing to compare yet">{d.no_data_note}</Note></div>
        ) : null}

        <div className="mt-5 grid grid-cols-2 gap-4 lg:grid-cols-5">
          <Card><CardContent className="p-4">
            <Stat label="Firms billing us" value={String(d.league.length)} />
          </CardContent></Card>
          <Card><CardContent className="p-4">
            <Stat label="Total billed" value={money(totalSpend)} />
          </CardContent></Card>
          <Card><CardContent className="p-4">
            <Stat label="Cases running hot" value={String(d.hot_cases)} tone={d.hot_cases ? RED : GREEN} />
          </CardContent></Card>
          <Card><CardContent className="p-4">
            <Stat
              label="Still to come"
              value={money(d.unbilled?.estimated)}
              tone={AMBER}
              sub={`${d.unbilled?.open_cases ?? 0} case(s) open`}
            />
          </CardContent></Card>
          <Card><CardContent className="p-4">
            <Stat label="Recovered by asking" value={money(recovered)} tone={GREEN} />
          </CardContent></Card>
        </div>

        {d.classification?.unconfirmed_pct ? (
          <div className="mt-4">
            <Note tone={d.classification.unconfirmed_pct > 25 ? 'warn' : 'info'} title="Before you trust a spend-by-case-type figure">
              {money2(d.classification.unconfirmed)} of {money2(d.classification.total)}
              {' '}({d.classification.unconfirmed_pct}%) sits on a case type that nobody confirmed —
              either an AI suggestion or never set. Confirm those on the bills-to-check screen first.
            </Note>
          </div>
        ) : null}

        {/* THE LEAGUE TABLE — the negotiation table. */}
        <Card className="mt-4">
          <CardContent className="p-4">
            <SectionHead
              icon={<TrendingUp className="h-4 w-4" style={{ color: ORANGE }} />}
              title="Panel league table"
              sub="Sorted by cost per matter — the column a rate conversation starts from. A high
                   concede rate is not proof of anything on its own, but it does say that querying
                   that firm pays."
            />
            <Table
              head={['Firm', 'Billed', 'Matters', 'Cost / matter', 'Real hourly rate', 'Open', 'Gone quiet',
                     'Days to close', 'Queried', 'Given back', 'Concede rate', 'Days to answer']}
            >
              {d.league.map((r) => (
                <tr key={r.firm_id} style={trBorder}>
                  <td className={td}>
                    <div className="font-semibold" style={{ color: NAVY }}>{r.firm}</div>
                    {r.agreed_hourly_rate == null ? (
                      <div className="text-[11px]" style={{ color: AMBER }}>no agreed rate on file</div>
                    ) : null}
                  </td>
                  <td className={tdNum}>{money(r.spend)}</td>
                  <td className={tdNum}>{r.matters}</td>
                  <td className={`${td} text-right font-semibold`} style={{ color: NAVY }}>
                    {money(r.cost_per_matter)}
                  </td>
                  <td className={tdNum} style={{ color: r.over_tariff ? RED : '#374151' }}>
                    {r.effective_hourly_rate == null ? '—' : money2(r.effective_hourly_rate)}
                    {r.over_tariff ? ' ▲' : ''}
                  </td>
                  <td className={tdNum}>{r.open_cases}</td>
                  <td className={tdNum} style={{ color: r.cases_gone_quiet ? RED : '#374151' }}>
                    {r.cases_gone_quiet}
                  </td>
                  <td className={tdNum}>{r.median_days_to_close ?? '—'}</td>
                  <td className={tdNum}>{money(r.amount_queried)}</td>
                  <td className={tdNum} style={{ color: r.amount_conceded ? GREEN : '#374151' }}>
                    {money(r.amount_conceded)}
                  </td>
                  <td className={tdNum}>{r.concede_rate == null ? '—' : `${r.concede_rate}%`}</td>
                  <td className={tdNum}>{r.median_days_to_answer ?? '—'}</td>
                </tr>
              ))}
              {!d.league.length ? (
                <tr><td colSpan={12} className="px-3 py-6 text-center text-[13px]" style={{ color: '#6B7280' }}>
                  No firms billing yet.
                </td></tr>
              ) : null}
            </Table>
          </CardContent>
        </Card>

        <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
          {/* EARLY WARNING — one case, right now. */}
          <Card><CardContent className="p-4">
            <SectionHead
              icon={<Flame className="h-4 w-4" style={{ color: RED }} />}
              title="Cases worth a call today"
              sub="Measured against what that KIND of case normally costs us — our own median, not
                   an average, so one runaway matter cannot move the bar."
            />
            <div className="space-y-2">
              {d.early_warnings.map((w, i) => (
                <div
                  key={w.matter_ref + i}
                  className="rounded-lg px-3 py-2.5 text-[13px]"
                  style={{
                    background: w.over_approval ? '#FEF7F7' : w.running_hot ? '#FFF7E8' : '#F8FAFC',
                    border: `1px solid ${w.over_approval ? '#F3D6D6' : w.running_hot ? '#F3E4C4' : LINE}`,
                  }}
                >
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="font-semibold" style={{ color: NAVY }}>{w.firm}</span>
                    {w.multiple ? (
                      <span
                        className="shrink-0 rounded px-1.5 py-0.5 text-[11px] font-bold"
                        style={{
                          background: w.multiple >= 2 ? RED : '#E5E7EB',
                          color: w.multiple >= 2 ? '#fff' : '#374151',
                        }}
                      >
                        {w.multiple}× normal
                      </span>
                    ) : null}
                  </div>
                  <div className="mt-1" style={{ color: '#374151' }}>{w.message}</div>
                </div>
              ))}
              {!d.early_warnings.length ? (
                <Note tone="good">Nothing is running hot. Every open matter is inside the normal
                  cost for its case type.</Note>
              ) : null}
            </div>
          </CardContent></Card>

          {/* WHAT NORMAL LOOKS LIKE + WHAT IS STILL TO COME */}
          <div className="space-y-4">
            <Card><CardContent className="p-4">
              <SectionHead
                icon={<Gavel className="h-4 w-4" style={{ color: NAVY }} />}
                title="What a case normally costs us"
                sub="Our own history, by case type. This is the yardstick every warning above is
                     measured against — and where too few cases exist, it says so."
              />
              <Table head={['Case type', 'Matters', 'Typical', 'Total spend', 'Basis']}>
                {d.typical_cost
                  .slice()
                  .sort((a, b) => (b.total_spend || 0) - (a.total_spend || 0))
                  .map((t) => (
                    <tr key={t.matter_type} style={trBorder}>
                      <td className={td} style={{ color: NAVY }}>{t.matter_type}</td>
                      <td className={tdNum}>{t.matters}</td>
                      <td className={`${td} text-right font-semibold`}>{money(t.typical)}</td>
                      <td className={tdNum}>{money(t.total_spend)}</td>
                      <td className={td} style={{ color: '#6B7280' }}>{t.basis}</td>
                    </tr>
                  ))}
                {!d.typical_cost.length ? (
                  <tr><td colSpan={5} className="px-3 py-5 text-center text-[13px]" style={{ color: '#6B7280' }}>
                    No history yet.
                  </td></tr>
                ) : null}
              </Table>
            </CardContent></Card>

            <Card><CardContent className="p-4">
              <SectionHead
                icon={<Hourglass className="h-4 w-4" style={{ color: AMBER }} />}
                title="What the open cases will still cost"
                sub={d.unbilled?.caveat || ''}
              />
              <div className="mb-3 grid grid-cols-3 gap-3">
                <Stat label="Estimate" value={money(d.unbilled?.estimated)} tone={AMBER} />
                <Stat label="Cases priced" value={String(d.unbilled?.cases_priced ?? 0)} />
                <Stat
                  label="Cannot price"
                  value={String(d.unbilled?.cases_not_priced ?? 0)}
                  sub="too few of that case type"
                />
              </div>
              <Table head={['Case', 'Type', 'Billed so far', 'Still to come', 'Days open']}>
                {(d.unbilled?.rows || []).slice(0, 10).map((r) => (
                  <tr key={r.case_ref} style={trBorder}>
                    <td className={td} style={{ color: NAVY }}>{r.case_ref}</td>
                    <td className={td}>{r.matter_type}</td>
                    <td className={tdNum}>{money(r.billed_so_far)}</td>
                    <td className={`${td} text-right font-semibold`}>{money(r.still_to_come)}</td>
                    <td className={tdNum}>{r.days_open}</td>
                  </tr>
                ))}
                {!(d.unbilled?.rows || []).length ? (
                  <tr><td colSpan={5} className="px-3 py-5 text-center text-[13px]" style={{ color: '#6B7280' }}>
                    No open cases on the register yet.
                  </td></tr>
                ) : null}
              </Table>
            </CardContent></Card>
          </div>
        </div>
      </div>
    </div>
  )
}

function SectionHead({ icon, title, sub }: { icon: React.ReactNode; title: string; sub?: string }) {
  return (
    <div className="mb-3">
      <div className="flex items-center gap-2 text-[14px] font-bold" style={{ color: NAVY }}>
        {icon}
        {title}
      </div>
      {sub ? (
        <div className="mt-1 max-w-3xl text-[12px] leading-relaxed" style={{ color: '#6B7280' }}>
          {sub}
        </div>
      ) : null}
    </div>
  )
}
