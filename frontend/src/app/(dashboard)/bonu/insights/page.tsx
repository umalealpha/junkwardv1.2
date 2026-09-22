'use client'

/**
 * /bonu/insights — the money-protecting reads over the captured BONU schedule
 * (CFO 2026-08-12, nine upgrades). Everything here is derived from the schedule
 * rows already in Omni, so it stays true as staff edit them.
 *
 *  1 Gap to the ledger   2 Member limits   3 Duplicate claims
 *  4 Premium gaps        6 Cost by firm / case   8 Bill matches
 *
 * Backend: /bonu/insights/ (one call) · /bonu/insights/gap/ (+ ?download=csv)
 */

import { useCallback, useEffect, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { apiFetch, apiFetchRaw, saveBlob } from '@/lib/api'
import { AlertTriangle, Copy, Download, Loader2, Receipt, TrendingUp, Users } from 'lucide-react'
import { AMBER, BonuTabs, GREEN, LINE, NAVY, ORANGE, RED, Stat, money2 } from '../_shared'

const num = (s: string | number) => { const n = Number(String(s).replace(/[^0-9.-]/g, '')); return Number.isFinite(n) ? n : NaN }

interface Insights {
  summary: any
  members: { available: boolean; limit: string; over: any[]; near: any[]; over_count: number; near_count: number }
  duplicates: { by_reference: any[]; ref_dupe_groups: number; fam_dupe_groups: number; amount_at_risk_ref: string }
  premium: { available: boolean; months: { month: string; amount: string; booked: boolean }[]; present_count: number; missing: string[] }
  cost_by_firm: { available: boolean; rows: { name: string; total: string; count: number }[] }
  cost_by_case: { available: boolean; rows: { name: string; total: string; count: number }[] }
  bills: { available: boolean; matched: number; mismatch_count: number; only_in_schedule: number; only_in_bills: number; mismatches: any[] }
}
interface Gap { available: boolean; unmatched_count: number; unmatched_total: string; schedule_total: string; ledger_total: string; total_gap: string; unmatched: any[] }

function Section({ icon, title, tone, children }: { icon: React.ReactNode; title: string; tone: string; children: React.ReactNode }) {
  return (
    <Card className="mt-4"><CardContent className="p-4">
      <div className="mb-3 flex items-center gap-2 text-sm font-semibold" style={{ color: tone }}>{icon}{title}</div>
      {children}
    </CardContent></Card>
  )
}

const th = 'px-2 py-1.5 text-left text-xs font-medium text-gray-500'
const cell = 'px-2 py-1.5 text-sm'
const cellR = 'px-2 py-1.5 text-sm text-right tabular-nums whitespace-nowrap'

export default function BonuInsightsPage() {
  const [d, setD] = useState<Insights | null>(null)
  const [gap, setGap] = useState<Gap | null>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    const [ins, g] = await Promise.all([
      apiFetch<Insights>('/bonu/insights/').catch(() => null),
      apiFetch<Gap>('/bonu/insights/gap/').catch(() => null),
    ])
    setLoading(false)
    if (!ins) { setErr('Could not load insights.'); return }
    setD(ins); setGap(g)
  }, [])
  useEffect(() => { load() }, [load])

  const downloadGap = async () => {
    const r = await apiFetchRaw('/bonu/insights/gap/?download=csv')
    saveBlob(await r.blob(), 'bonu_claims_not_in_ledger.csv')
  }

  if (loading) return (
    <div className="min-h-screen" style={{ background: '#F6F8FB' }}>
      <TopBar title="BONU — insights" />
      <div className="mx-auto max-w-[1400px] px-6 py-5"><BonuTabs active="/bonu/insights" />
        <div className="mt-10 flex items-center gap-2 text-sm text-gray-500"><Loader2 className="h-4 w-4 animate-spin" /> Loading…</div></div>
    </div>
  )

  const s = d?.summary
  return (
    <div className="min-h-screen" style={{ background: '#F6F8FB' }}>
      <TopBar title="BONU — insights" />
      <div className="mx-auto max-w-[1400px] px-6 py-5">
        <BonuTabs active="/bonu/insights" />
        {err && <div className="mt-4 text-sm" style={{ color: RED }}>{err}</div>}

        {/* Headline cards */}
        <div className="mt-5 grid grid-cols-2 gap-3 md:grid-cols-4">
          <Stat label="Schedule vs ledger gap" value={gap ? money2(num(gap.total_gap)) : '—'} tone={RED} />
          <Stat label="Members over the P90k cap" value={String(d?.members?.over_count ?? 0)} tone={d && d.members.over_count ? RED : GREEN} />
          <Stat label="Duplicate-invoice groups" value={String(d?.duplicates?.ref_dupe_groups ?? 0)} tone={d && d.duplicates.ref_dupe_groups ? AMBER : GREEN} />
          <Stat label="Premium months missing" value={String(d?.premium?.missing?.length ?? 0)} tone={d && d.premium.missing.length ? AMBER : GREEN} />
        </div>

        {/* 1 — Gap to the ledger */}
        <Section icon={<AlertTriangle className="h-4 w-4" />} title="Schedule claims vs the ledger" tone={RED}>
          {gap && gap.available ? (
            <>
              <div className="mb-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
                <span className="text-gray-600">Schedule <b>{money2(num(gap.schedule_total))}</b> · Ledger <b>{money2(num(gap.ledger_total))}</b> · Gap <b style={{ color: RED }}>{money2(num(gap.total_gap))}</b></span>
              </div>
              <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
                <span className="text-gray-600"><b>{gap.unmatched_count}</b> invoices not matched to a ledger reference — a worklist to verify (references are in different formats)</span>
                <button onClick={downloadGap} className="inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-xs font-medium text-white" style={{ background: NAVY }}><Download className="h-3.5 w-3.5" /> Download CSV</button>
              </div>
              <div className="max-h-72 overflow-auto rounded border" style={{ borderColor: LINE }}>
                <table className="w-full"><thead className="sticky top-0 bg-gray-50"><tr>
                  <th className={th}>Law Firm</th><th className={th}>Invoice</th><th className={th}>Client</th><th className={th}>Month</th><th className={th + ' text-right'}>Amount</th>
                </tr></thead><tbody>
                  {gap.unmatched.slice(0, 200).map((u, i) => (
                    <tr key={u.id ?? i} style={{ borderTop: `1px solid ${LINE}` }}>
                      <td className={cell}>{u.firm}</td><td className={cell}>{u.ref}</td><td className={cell}>{u.client}</td><td className={cell}>{u.month}</td><td className={cellR}>{money2(num(u.amount))}</td>
                    </tr>
                  ))}
                </tbody></table>
              </div>
              {gap.unmatched.length > 200 && <div className="mt-1 text-xs text-gray-400">Showing top 200 by amount — download the CSV for all {gap.unmatched_count}.</div>}
            </>
          ) : <div className="text-sm text-gray-500">No claims sheet loaded.</div>}
        </Section>

        {/* 2 — Member limits */}
        <Section icon={<Users className="h-4 w-4" />} title={`Members near or over the P90k-a-year cap`} tone={NAVY}>
          {d?.members?.available ? (
            <div className="grid gap-4 md:grid-cols-2">
              <div>
                <div className="mb-1 text-xs font-medium" style={{ color: RED }}>Over the cap ({d.members.over_count})</div>
                <MemberTable rows={d.members.over} tone={RED} />
              </div>
              <div>
                <div className="mb-1 text-xs font-medium" style={{ color: AMBER }}>Getting close ({d.members.near_count})</div>
                <MemberTable rows={d.members.near} tone={AMBER} />
              </div>
            </div>
          ) : <div className="text-sm text-gray-500">No claims sheet loaded.</div>}
          <div className="mt-2 text-xs text-gray-400">Retainer and block-cover rows are excluded — they are not a member&rsquo;s personal legal cost.</div>
        </Section>

        {/* 3 — Duplicates */}
        <Section icon={<Copy className="h-4 w-4" />} title="Possible double-billing (same invoice more than once)" tone={AMBER}>
          <div className="mb-2 text-sm text-gray-600">{d?.duplicates.ref_dupe_groups ?? 0} by invoice reference · {d?.duplicates.fam_dupe_groups ?? 0} by firm+amount+month · up to <b>{money2(num(d?.duplicates.amount_at_risk_ref ?? '0'))}</b> at risk</div>
          <div className="max-h-64 overflow-auto rounded border" style={{ borderColor: LINE }}>
            <table className="w-full"><thead className="sticky top-0 bg-gray-50"><tr>
              <th className={th}>Invoice</th><th className={th}>Firm(s)</th><th className={th}>Times</th><th className={th + ' text-right'}>Amount each</th>
            </tr></thead><tbody>
              {(d?.duplicates.by_reference ?? []).slice(0, 100).map((g, i) => (
                <tr key={i} style={{ borderTop: `1px solid ${LINE}` }}>
                  <td className={cell}>{g.items[0].ref}</td>
                  <td className={cell}>{Array.from(new Set(g.items.map((x: any) => x.firm))).join(', ')}</td>
                  <td className={cell}>{g.count}</td><td className={cellR}>{money2(num(g.amount_each))}</td>
                </tr>
              ))}
              {(d?.duplicates.by_reference ?? []).length === 0 && <tr><td className={cell + ' text-gray-400'} colSpan={4}>None found.</td></tr>}
            </tbody></table>
          </div>
        </Section>

        {/* 4 — Premium gaps */}
        <Section icon={<AlertTriangle className="h-4 w-4" />} title="Premium booked by month" tone={d?.premium?.missing?.length ? AMBER : GREEN}>
          {d?.premium?.available ? (
            <div className="flex flex-wrap gap-2">
              {d.premium.months.map(m => (
                <div key={m.month} className="rounded-md border px-3 py-2 text-center" style={{ borderColor: LINE, background: m.booked ? '#fff' : '#FEF2F2', minWidth: 84 }}>
                  <div className="text-xs text-gray-500">{m.month}</div>
                  <div className="text-sm font-medium" style={{ color: m.booked ? NAVY : RED }}>{m.booked ? money2(num(m.amount)) : 'missing'}</div>
                </div>
              ))}
            </div>
          ) : <div className="text-sm text-gray-500">No premiums sheet loaded.</div>}
        </Section>

        {/* 6 — Cost by firm / case */}
        <div className="grid gap-4 md:grid-cols-2">
          <Section icon={<TrendingUp className="h-4 w-4" />} title="Cost by law firm" tone={NAVY}>
            <CostTable rows={d?.cost_by_firm?.rows ?? []} />
          </Section>
          <Section icon={<TrendingUp className="h-4 w-4" />} title="Cost by case type" tone={NAVY}>
            <CostTable rows={d?.cost_by_case?.rows ?? []} />
          </Section>
        </div>

        {/* 8 — Bill matches */}
        <Section icon={<Receipt className="h-4 w-4" />} title="Emailed bills vs the schedule" tone={d?.bills?.mismatch_count ? RED : GREEN}>
          {d?.bills?.available ? (
            <>
              <div className="mb-2 grid grid-cols-2 gap-2 text-sm md:grid-cols-4">
                <span>Matched: <b style={{ color: GREEN }}>{d.bills.matched}</b></span>
                <span>Amount differs: <b style={{ color: RED }}>{d.bills.mismatch_count}</b></span>
                <span>Only in schedule: <b>{d.bills.only_in_schedule}</b></span>
                <span>Only in bills: <b>{d.bills.only_in_bills}</b></span>
              </div>
              {d.bills.mismatches.length > 0 && (
                <div className="max-h-56 overflow-auto rounded border" style={{ borderColor: LINE }}>
                  <table className="w-full"><thead className="sticky top-0 bg-gray-50"><tr>
                    <th className={th}>Invoice</th><th className={th}>Firm</th><th className={th + ' text-right'}>Schedule</th><th className={th + ' text-right'}>Bill</th>
                  </tr></thead><tbody>
                    {d.bills.mismatches.map((m, i) => (
                      <tr key={i} style={{ borderTop: `1px solid ${LINE}` }}>
                        <td className={cell}>{m.ref}</td><td className={cell}>{m.firm}</td><td className={cellR}>{money2(num(m.schedule_amount))}</td><td className={cellR}>{money2(num(m.bill_amount))}</td>
                      </tr>
                    ))}
                  </tbody></table>
                </div>
              )}
            </>
          ) : <div className="text-sm text-gray-500">No claims sheet loaded.</div>}
        </Section>
      </div>
    </div>
  )
}

function MemberTable({ rows, tone }: { rows: any[]; tone: string }) {
  if (!rows.length) return <div className="text-sm text-gray-400">None.</div>
  return (
    <div className="max-h-60 overflow-auto rounded border" style={{ borderColor: LINE }}>
      <table className="w-full"><thead className="sticky top-0 bg-gray-50"><tr>
        <th className={th}>Member</th><th className={th}>Year</th><th className={th + ' text-right'}>Total</th><th className={th + ' text-right'}>Over by</th>
      </tr></thead><tbody>
        {rows.map((r, i) => (
          <tr key={i} style={{ borderTop: `1px solid ${LINE}` }}>
            <td className={cell}>{r.member}</td><td className={cell}>{r.year ?? '—'}</td>
            <td className={cellR}>{money2(num(r.total))}</td>
            <td className={cellR} style={{ color: tone }}>{num(r.over_by) > 0 ? money2(num(r.over_by)) : '—'}</td>
          </tr>
        ))}
      </tbody></table>
    </div>
  )
}

function CostTable({ rows }: { rows: { name: string; total: string; count: number }[] }) {
  if (!rows.length) return <div className="text-sm text-gray-400">No data.</div>
  return (
    <div className="max-h-72 overflow-auto rounded border" style={{ borderColor: LINE }}>
      <table className="w-full"><thead className="sticky top-0 bg-gray-50"><tr>
        <th className={th}>Name</th><th className={th + ' text-right'}>Invoices</th><th className={th + ' text-right'}>Total</th>
      </tr></thead><tbody>
        {rows.slice(0, 100).map((r, i) => (
          <tr key={i} style={{ borderTop: `1px solid ${LINE}` }}>
            <td className={cell}>{r.name}</td><td className={cellR}>{r.count}</td><td className={cellR}>{money2(num(r.total))}</td>
          </tr>
        ))}
      </tbody></table>
    </div>
  )
}
