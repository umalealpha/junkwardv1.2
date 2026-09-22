'use client'

/**
 * /banking/realpay — RealPay debit-order collections (LIVE).
 *
 * Pulls month-by-month transaction batches from realpaycollect.com, stores
 * the raw payload + an XLSX, then runs Aria over the totals to produce
 * a CFO-readable monthly narrative. Buttons:
 *   • Backfill Jul-2025 → today (one click, one beneficiary)
 *   • Re-run commentary for a single month
 *   • Download the generated XLSX
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  getToken, listRealPayReports, backfillRealPay, pullRealPayMonth,
  regenerateRealPayCommentary, getRealPayAnalytics, getRealPayGraphiteLive, getMe,
} from '@/lib/api'
import type { RealPayReportRow, RealPayAnalyticsResponse, RealPayGraphiteLive } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  ArrowDownLeft, ExternalLink, RefreshCw, Download, FileSpreadsheet,
  Sparkles, AlertTriangle, Play, Users, UserX, TrendingUp, BarChart3,
  Trophy, ShieldAlert, Database,
} from 'lucide-react'

// Production beneficiaries first so the default is never the UAT sandbox
// (GENRIC-UAT) — that default is what made Bokani's pull hit UAT + fail.
const BENEFICIARY_USERS = [
  { code: 'ADII',       name: 'Alpha Direct Instant Insurance', user_id: '24936' },
  { code: 'ADIC',       name: 'Alpha Direct Insurance',         user_id: '16244' },
  { code: 'UNI',        name: 'Unicoin Pty Ltd',                user_id: '28555' },
  { code: 'GENRIC-UAT', name: 'Genric Integration (BW UAT)',    user_id: '19413' },
]

const STATUS_COLOR: Record<string, string> = {
  pending:  'bg-[#F3F4F6] text-[#374151]',
  pulled:   'bg-[#DBEAFE] text-[#1E40AF]',
  analysed: 'bg-[#D1FAE5] text-[#065F46]',
  failed:   'bg-[#FEE2E2] text-[#991B1B]',
}

function fmtMoney(s: string | null | undefined): string {
  if (!s) return '—'
  const v = Number(s)
  if (!isFinite(v)) return '—'
  return 'BWP ' + v.toLocaleString(undefined, { maximumFractionDigits: 0 })
}

export default function RealPayPage() {
  const router = useRouter()
  const [rows, setRows] = useState<RealPayReportRow[]>([])
  const [analytics, setAnalytics] = useState<RealPayAnalyticsResponse | null>(null)
  const [glive, setGlive] = useState<RealPayGraphiteLive | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [info, setInfo] = useState<string | null>(null)
  const [pickedUser, setPickedUser] = useState(BENEFICIARY_USERS[0].user_id)
  const [activeId, setActiveId] = useState<string | null>(null)
  // The vendor Pull controls (backfill / pull-month) are an admin-only tool on the
  // UAT sandbox and are being retired in favour of the live Collections Dashboard.
  // Non-admins were shown buttons that always returned 403 — hide them.
  const [isAdmin, setIsAdmin] = useState(false)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const [r, a, g] = await Promise.all([
        listRealPayReports(),
        getRealPayAnalytics(12, pickedUser).catch(() => null),
        getRealPayGraphiteLive(6).catch(() => null),
      ])
      setRows(r.reports)
      setAnalytics(a)
      setGlive(g)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load reports')
    } finally {
      setLoading(false)
    }
  }, [pickedUser])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load()
    getMe().then(me => setIsAdmin(!!me?.is_administrator))
      .catch(() => setIsAdmin(false))
  }, [load, router])

  const userLabel = useMemo(() =>
    BENEFICIARY_USERS.find(u => u.user_id === pickedUser)?.name || pickedUser,
    [pickedUser])

  async function runBackfill() {
    if (!confirm(`Backfill all months from July 2025 → today for ${userLabel}? This may take a few minutes.`)) return
    setBusy(true); setError(null); setInfo(null)
    try {
      const r = await backfillRealPay({
        beneficiary_user_id: pickedUser, label: userLabel,
      })
      setInfo(`Backfilled ${r.count} months.`)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Backfill failed')
    } finally { setBusy(false) }
  }

  async function pullSingleMonth() {
    const ym = prompt('Pull which month? YYYY-MM:')
    if (!ym) return
    const [yy, mm] = ym.split('-').map(Number)
    if (!yy || !mm || mm < 1 || mm > 12) { setError('Invalid YYYY-MM.'); return }
    setBusy(true); setError(null); setInfo(null)
    try {
      const r = await pullRealPayMonth({
        year: yy, month: mm,
        beneficiary_user_id: pickedUser, label: userLabel,
      })
      setInfo(`Pulled ${r.period_label}: ${r.txn_count_total} txns.`)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Pull failed')
    } finally { setBusy(false) }
  }

  async function rerunCommentary(id: string) {
    setBusy(true); setError(null); setInfo(null)
    try {
      await regenerateRealPayCommentary(id)
      setInfo('Commentary regenerated.')
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Commentary failed')
    } finally { setBusy(false) }
  }

  const active = rows.find(r => r.id === activeId)

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="RealPay Collections"
        subtitle="Monthly debit-order pulls + Aria analysis · realpaycollect.com"
        breadcrumbs={[
          { label: 'Banking', href: '/banking' },
          { label: 'Collections' },
          { label: 'RealPay' },
        ]}
        actions={
          <div className="flex gap-2">
            <Button variant="secondary" size="sm" onClick={load} loading={loading}>
              <RefreshCw className={`w-4 h-4 mr-1 ${loading ? 'animate-spin' : ''}`} /> Refresh
            </Button>
            <a
              href="https://uat.realpaycollect.com:4442/rpi/f?p=200:LOGIN"
              target="_blank" rel="noopener"
              className="inline-flex items-center gap-1 text-sm px-3 py-1.5 rounded border border-[#D1D5DB] hover:bg-[#F9FAFB]"
            >
              <ExternalLink className="w-4 h-4" /> Portal
            </a>
          </div>
        }
      />

      <main className="flex-1 p-6 space-y-4">

        {/* LIVE from Graphite — reads the debit orders Graphite actually collects,
            via the read-only bridge. No vendor API key; cannot go stale; separate
            from the uploaded rows so it never double-counts. (CFO 2026-09-06.) */}
        {glive && glive.configured && (
          <Card className="border-[#0D1B2A]/15">
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-[#0D1B2A]">
                <Database className="w-4 h-4 text-[#F4A623]" />
                Collections — Live from Graphite
                <span className="ml-2 inline-flex items-center rounded-full bg-[#D1FAE5] px-2 py-0.5 text-[10px] font-semibold text-[#065F46]">LIVE · source of truth</span>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
                <div>
                  <div className="text-[10px] uppercase tracking-wider text-[#065F46]">Collected · last 30 days</div>
                  <div className="text-xl font-bold text-[#065F46]">{fmtMoney(String(glive.last30.collected_amount ?? 0))}</div>
                  <div className="text-[11px] text-[#6B7280]">{glive.last30.collected_count ?? 0} debits</div>
                </div>
                <div>
                  <div className="text-[10px] uppercase tracking-wider text-[#991B1B]">Failed · last 30 days</div>
                  <div className="text-xl font-bold text-[#991B1B]">{glive.last30.failed_count ?? 0}</div>
                </div>
                <div>
                  <div className="text-[10px] uppercase tracking-wider text-[#6B7280]">Collected · {glive.months ?? 6} months</div>
                  <div className="text-xl font-bold text-[#0D1B2A]">{fmtMoney(String(glive.totals.collected_amount ?? 0))}</div>
                  <div className="text-[11px] text-[#6B7280]">{glive.totals.collected_count ?? 0} debits</div>
                </div>
                <div>
                  <div className="text-[10px] uppercase tracking-wider text-[#6B7280]">Failed · {glive.months ?? 6} months</div>
                  <div className="text-xl font-bold text-[#0D1B2A]">{fmtMoney(String(glive.totals.failed_amount ?? 0))}</div>
                  <div className="text-[11px] text-[#6B7280]">{glive.totals.failed_count ?? 0} debits</div>
                </div>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-[10px] uppercase tracking-wider text-[#6B7280] border-b border-[#E5E7EB]">
                      <th className="py-2 pr-4">Month</th>
                      <th className="py-2 pr-4 text-right">Collected</th>
                      <th className="py-2 pr-4 text-right">Debits</th>
                      <th className="py-2 pr-4 text-right">Failed (amt)</th>
                      <th className="py-2 pr-4 text-right">Failed</th>
                      <th className="py-2 pr-4 text-right">Processing</th>
                    </tr>
                  </thead>
                  <tbody>
                    {glive.rows.map(m => (
                      <tr key={m.ym} className="border-b border-[#F3F4F6]">
                        <td className="py-2 pr-4 font-medium text-[#0D1B2A]">{m.ym}</td>
                        <td className="py-2 pr-4 text-right font-semibold text-[#065F46]">{fmtMoney(String(m.collected_amount))}</td>
                        <td className="py-2 pr-4 text-right">{m.collected_count}</td>
                        <td className="py-2 pr-4 text-right text-[#991B1B]">{fmtMoney(String(m.failed_amount))}</td>
                        <td className="py-2 pr-4 text-right">{m.failed_count}</td>
                        <td className="py-2 pr-4 text-right text-[#6B7280]">{m.processing_count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="mt-2 text-[11px] text-[#9CA3AF]">
                Live read from Graphite (the system that collects the debit orders). Totals across all books; a per-book split needs the RealPay beneficiary field, which Graphite does not store.
              </div>
            </CardContent>
          </Card>
        )}

        {/* Connections & system info — the two per-country pages folded into one
            collapsed panel (CFO 2026-08-26: dropped the extra sidebar links; the
            full pages stay reachable by the links below). */}
        <details className="rounded-lg border border-[#E5E7EB] bg-[#F9FAFB]">
          <summary className="cursor-pointer select-none px-4 py-3 text-sm font-semibold text-[#0D1B2A] flex items-center gap-2">
            <ArrowDownLeft className="w-4 h-4 text-[#6B7280]" /> Connections &amp; system info (Botswana · South Africa)
          </summary>
          <div className="px-4 pb-4 pt-1 text-sm text-[#374151] space-y-3">
            <div>
              <div className="font-semibold text-[#0D1B2A]">Botswana</div>
              <p className="text-[#6B7280] mt-0.5">Beneficiary user 19413 · product RTFNBBW. Primary collections feed = Instalment Changes Report (transactions_report deprecated by RealPay, 2026-06-03); 5 report endpoints live.</p>
              <a href="/banking/realpay/botswana" className="inline-flex items-center gap-1 text-[#1D4ED8] underline mt-1"><ExternalLink className="w-3 h-3" /> Full Botswana detail</a>
            </div>
            <div>
              <div className="font-semibold text-[#0D1B2A]">South Africa</div>
              <p className="text-[#6B7280] mt-0.5">Beneficiary user 21175 · products ABSADC / ABSADO.</p>
              <a href="/banking/realpay/south-africa" className="inline-flex items-center gap-1 text-[#1D4ED8] underline mt-1"><ExternalLink className="w-3 h-3" /> Full South Africa detail</a>
            </div>
          </div>
        </details>

        {/* Finance-facing report links — the live, downloadable collections figures.
            Everyone sees these; they replace the retired vendor Pull controls. */}
        <Card className="border-[#F4A623]/40 bg-[#FFFBEB]">
          <CardContent className="py-4 flex flex-wrap items-center gap-x-6 gap-y-2">
            <span className="text-sm font-medium text-[#0D1B2A]">Collections reports (live from Graphite):</span>
            <a href="/banking/realpay/collections/dashboard"
               className="inline-flex items-center gap-1 text-sm text-[#1D4ED8] underline">
              <ArrowDownLeft className="w-4 h-4" /> Collections Dashboard — amount collected, downloadable CSV
            </a>
            <a href="/banking/realpay/collections/debit-tracker"
               className="inline-flex items-center gap-1 text-sm text-[#1D4ED8] underline">
              <AlertTriangle className="w-4 h-4" /> Debit Tracker — failed / error debits
            </a>
          </CardContent>
        </Card>

        {/* Controls — vendor pull on the UAT sandbox. Admin/dev only. */}
        {isAdmin && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base flex items-center gap-2">
              <ArrowDownLeft className="w-4 h-4 text-emerald-600" /> Pull controls
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap items-end gap-3">
            <div>
              <label className="block text-xs text-[#6B7280] mb-1">Beneficiary user</label>
              <select value={pickedUser} onChange={e => setPickedUser(e.target.value)}
                      aria-label="Beneficiary user"
                      className="border rounded px-3 py-1.5 text-sm bg-background min-w-[280px]">
                {BENEFICIARY_USERS.map(u => (
                  <option key={u.user_id} value={u.user_id}>
                    {u.code} — {u.name} ({u.user_id})
                  </option>
                ))}
              </select>
            </div>
            <Button variant="primary" size="sm" onClick={runBackfill}
                    loading={busy} disabled={busy}>
              <Play className="w-4 h-4 mr-1" /> Backfill Jul-25 → today
            </Button>
            <Button variant="secondary" size="sm" onClick={pullSingleMonth}
                    loading={busy} disabled={busy}>
              Pull one month…
            </Button>
            <p className="text-xs text-[#6B7280] ml-auto">
              UAT sandbox · creds in /etc/alpha-finance/.env
            </p>
          </CardContent>
        </Card>
        )}

        {error && (
          <Card className="border-red-300 bg-red-50/40">
            <CardContent className="py-3 text-sm text-red-700">{error}</CardContent>
          </Card>
        )}
        {info && (
          <Card className="border-emerald-300 bg-emerald-50/40">
            <CardContent className="py-3 text-sm text-emerald-700">{info}</CardContent>
          </Card>
        )}

        {/* Monthly grid — vendor-pull (UAT) output; the XLSX links point at
            generated files that may no longer exist (404). Admin/dev only. */}
        {isAdmin && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">Monthly reports</CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            {rows.length === 0 ? (
              <div className="py-10 text-center text-sm text-[#9CA3AF]">
                <FileSpreadsheet className="w-8 h-8 mx-auto mb-2 opacity-40" />
                No reports yet. Hit <em>Backfill</em> to pull from July 2025.
              </div>
            ) : (
              <table className="w-full text-sm">
                <thead className="bg-[#F9FAFB] text-left text-xs uppercase text-[#6B7280] border-b">
                  <tr>
                    <th className="py-2 px-3">Month</th>
                    <th className="py-2 px-3">User</th>
                    <th className="py-2 px-3 text-right">Txns</th>
                    <th className="py-2 px-3 text-right">Collected</th>
                    <th className="py-2 px-3 text-right">Failed</th>
                    <th className="py-2 px-3">Status</th>
                    <th className="py-2 px-3"></th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map(r => (
                    <tr key={r.id}
                        onClick={() => setActiveId(r.id)}
                        className="border-b last:border-0 hover:bg-[#FFF7ED] cursor-pointer">
                      <td className="py-2 px-3 font-medium">{r.period_label}</td>
                      <td className="py-2 px-3 text-xs text-[#6B7280]">
                        {r.beneficiary_label || r.beneficiary_user_id}
                      </td>
                      <td className="py-2 px-3 text-right font-mono-nums">
                        {r.txn_count_total} <span className="text-[10px] text-[#9CA3AF]">
                          ({r.txn_count_successful}/{r.txn_count_failed})
                        </span>
                      </td>
                      <td className="py-2 px-3 text-right font-mono-nums text-[#065F46]">
                        {fmtMoney(r.amount_collected)}
                      </td>
                      <td className="py-2 px-3 text-right font-mono-nums text-[#991B1B]">
                        {fmtMoney(r.amount_failed)}
                      </td>
                      <td className="py-2 px-3">
                        <span className={`px-2 py-0.5 rounded text-xs ${STATUS_COLOR[r.status]}`}>
                          {r.status}
                        </span>
                      </td>
                      <td className="py-2 px-3 text-right">
                        {r.xlsx_url && (
                          <a href={r.xlsx_url} onClick={e => e.stopPropagation()}
                             className="text-xs text-[#F4A623] hover:underline inline-flex items-center gap-1">
                            <Download className="w-3 h-3" /> XLSX
                          </a>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </CardContent>
        </Card>
        )}

        {/* ─── Creative analytic sections (CFO 2026-05-25) ─── */}
        {analytics && analytics.summary.months_loaded > 0 && (
          <>
            {/* Summary KPIs */}
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-base flex items-center gap-2">
                  <BarChart3 className="w-4 h-4 text-[#F4A623]" />
                  Portfolio snapshot · last {analytics.summary.months_loaded} month(s)
                </CardTitle>
              </CardHeader>
              <CardContent className="grid grid-cols-2 sm:grid-cols-5 gap-3 text-sm">
                <div className="space-y-1">
                  <div className="text-[10px] uppercase tracking-wider text-[#6B7280]">Total txns</div>
                  <div className="text-lg font-bold">{analytics.summary.total_txns}</div>
                </div>
                <div className="space-y-1">
                  <div className="text-[10px] uppercase tracking-wider text-[#065F46]">Successful</div>
                  <div className="text-lg font-bold text-[#065F46]">{analytics.summary.total_successful}</div>
                </div>
                <div className="space-y-1">
                  <div className="text-[10px] uppercase tracking-wider text-[#991B1B]">Failed</div>
                  <div className="text-lg font-bold text-[#991B1B]">{analytics.summary.total_failed}</div>
                </div>
                <div className="space-y-1">
                  <div className="text-[10px] uppercase tracking-wider text-[#065F46]">Collected</div>
                  <div className="text-lg font-bold text-[#065F46] font-mono-nums">
                    {fmtMoney(analytics.summary.total_collected)}
                  </div>
                </div>
                <div className="space-y-1">
                  <div className="text-[10px] uppercase tracking-wider text-[#991B1B]">Lost to NSF</div>
                  <div className="text-lg font-bold text-[#991B1B] font-mono-nums">
                    {fmtMoney(analytics.summary.total_failed_amount)}
                  </div>
                </div>
              </CardContent>
            </Card>

            {/* Month-over-month sparkline */}
            {analytics.month_trend.length > 0 && (
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-base flex items-center gap-2">
                    <TrendingUp className="w-4 h-4 text-emerald-600" /> Month-over-month
                  </CardTitle>
                </CardHeader>
                <CardContent className="p-0">
                  <table className="w-full text-sm">
                    <thead className="bg-[#F9FAFB] text-xs uppercase text-[#6B7280] border-b">
                      <tr>
                        <th className="py-2 px-3 text-left">Period</th>
                        <th className="py-2 px-3 text-right">Collected</th>
                        <th className="py-2 px-3 text-right">Failed</th>
                        <th className="py-2 px-3 text-right">Success rate</th>
                      </tr>
                    </thead>
                    <tbody>
                      {analytics.month_trend.map(p => {
                        const tot = p.ok_count + p.fail_count
                        const rate = tot ? (p.ok_count / tot) : 0
                        return (
                          <tr key={p.period} className="border-b last:border-0">
                            <td className="py-2 px-3 font-medium">{p.period}</td>
                            <td className="py-2 px-3 text-right font-mono-nums text-[#065F46]">
                              {fmtMoney(p.collected)}
                            </td>
                            <td className="py-2 px-3 text-right font-mono-nums text-[#991B1B]">
                              {fmtMoney(p.failed)}
                            </td>
                            <td className="py-2 px-3 text-right font-mono-nums">
                              {(rate * 100).toFixed(1)}%
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </CardContent>
              </Card>
            )}

            {/* Two-column flex of feature cards */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">

              {/* Not-paying customers */}
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-base flex items-center gap-2 text-[#991B1B]">
                    <UserX className="w-4 h-4" /> Not paying · {analytics.not_paying.length}
                  </CardTitle>
                </CardHeader>
                <CardContent className="text-xs">
                  {analytics.not_paying.length === 0 ? (
                    <p className="text-[#9CA3AF]">Nobody chronically failing. Healthy book.</p>
                  ) : (
                    <table className="w-full">
                      <thead className="text-[10px] uppercase text-[#6B7280] border-b">
                        <tr>
                          <th className="text-left py-1">Customer</th>
                          <th className="text-right py-1">Months failed</th>
                          <th className="text-right py-1">Lost</th>
                        </tr>
                      </thead>
                      <tbody>
                        {analytics.not_paying.slice(0, 12).map((d, i) => (
                          <tr key={i} className="border-b last:border-0">
                            <td className="py-1.5">
                              <div className="font-medium">{d.name}</div>
                              <div className="text-[10px] text-[#9CA3AF]">{d.account}</div>
                            </td>
                            <td className="py-1.5 text-right font-mono-nums">
                              {d.months_failed}/{d.months_paid + d.months_failed}
                            </td>
                            <td className="py-1.5 text-right font-mono-nums text-[#991B1B]">
                              {fmtMoney(d.amount_failed)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </CardContent>
              </Card>

              {/* One payer, many policies */}
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-base flex items-center gap-2 text-[#1E40AF]">
                    <Users className="w-4 h-4" /> One payer, many policies · {analytics.multi_debit_payers.length}
                  </CardTitle>
                </CardHeader>
                <CardContent className="text-xs">
                  {analytics.multi_debit_payers.length === 0 ? (
                    <p className="text-[#9CA3AF]">No bank account funding multiple contracts.</p>
                  ) : (
                    <table className="w-full">
                      <thead className="text-[10px] uppercase text-[#6B7280] border-b">
                        <tr>
                          <th className="text-left py-1">Holder</th>
                          <th className="text-right py-1">Contracts</th>
                        </tr>
                      </thead>
                      <tbody>
                        {analytics.multi_debit_payers.slice(0, 12).map((m, i) => (
                          <tr key={i} className="border-b last:border-0">
                            <td className="py-1.5">
                              <div className="font-medium">{m.holder}</div>
                              <div className="text-[10px] text-[#9CA3AF]">{m.account}</div>
                            </td>
                            <td className="py-1.5 text-right font-mono-nums font-bold">
                              {m.contract_count}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </CardContent>
              </Card>

              {/* Top10 collected */}
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-base flex items-center gap-2 text-[#065F46]">
                    <Trophy className="w-4 h-4" /> Top 10 paying customers
                  </CardTitle>
                </CardHeader>
                <CardContent className="text-xs">
                  {analytics.top10_collected.length === 0 ? (
                    <p className="text-[#9CA3AF]">No successful collections yet.</p>
                  ) : (
                    <table className="w-full">
                      <thead className="text-[10px] uppercase text-[#6B7280] border-b">
                        <tr>
                          <th className="text-left py-1">#</th>
                          <th className="text-left py-1">Customer</th>
                          <th className="text-right py-1">Collected</th>
                        </tr>
                      </thead>
                      <tbody>
                        {analytics.top10_collected.map((t, i) => (
                          <tr key={i} className="border-b last:border-0">
                            <td className="py-1.5 text-[#9CA3AF]">{i + 1}</td>
                            <td className="py-1.5 font-medium">{t.name}</td>
                            <td className="py-1.5 text-right font-mono-nums text-[#065F46]">
                              {fmtMoney(t.amount_paid)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </CardContent>
              </Card>

              {/* Decline reason breakdown */}
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-base flex items-center gap-2 text-[#9A3412]">
                    <AlertTriangle className="w-4 h-4" /> Decline reasons
                  </CardTitle>
                </CardHeader>
                <CardContent className="text-xs">
                  {analytics.decline_reasons.length === 0 ? (
                    <p className="text-[#9CA3AF]">No decline reasons recorded.</p>
                  ) : (
                    <table className="w-full">
                      <thead className="text-[10px] uppercase text-[#6B7280] border-b">
                        <tr>
                          <th className="text-left py-1">Reason</th>
                          <th className="text-right py-1">Count</th>
                          <th className="text-right py-1">Amount</th>
                        </tr>
                      </thead>
                      <tbody>
                        {analytics.decline_reasons.map((d, i) => (
                          <tr key={i} className="border-b last:border-0">
                            <td className="py-1.5 font-mono">{d.code}</td>
                            <td className="py-1.5 text-right">{d.count}</td>
                            <td className="py-1.5 text-right font-mono-nums">
                              {fmtMoney(d.amount)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </CardContent>
              </Card>

              {/* Mandate flags */}
              {analytics.mandate_flags.length > 0 && (
                <Card className="lg:col-span-2 border-amber-200 bg-amber-50/30">
                  <CardHeader className="pb-2">
                    <CardTitle className="text-base flex items-center gap-2 text-[#92400E]">
                      <ShieldAlert className="w-4 h-4" /> Mandate maintenance flags · {analytics.mandate_flags.length}
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="text-xs">
                    <p className="text-[#9A3412] mb-2">
                      Accounts with ≥ 3 failed months. Recommend re-engaging the
                      customer or freezing the mandate.
                    </p>
                    <table className="w-full">
                      <thead className="text-[10px] uppercase text-[#6B7280] border-b">
                        <tr>
                          <th className="text-left py-1">Customer</th>
                          <th className="text-right py-1">Failure rate</th>
                          <th className="text-right py-1">Months failed</th>
                        </tr>
                      </thead>
                      <tbody>
                        {analytics.mandate_flags.slice(0, 15).map((m, i) => (
                          <tr key={i} className="border-b last:border-0">
                            <td className="py-1.5">
                              <div className="font-medium">{m.name}</div>
                              <div className="text-[10px] text-[#9CA3AF]">{m.account}</div>
                            </td>
                            <td className="py-1.5 text-right font-mono-nums text-[#991B1B]">
                              {(m.failure_rate * 100).toFixed(0)}%
                            </td>
                            <td className="py-1.5 text-right font-mono-nums">{m.months_failed}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </CardContent>
                </Card>
              )}
            </div>
          </>
        )}

        {/* Detail drawer */}
        {active && (
          <Card>
            <CardHeader className="pb-2 flex flex-row items-center justify-between">
              <CardTitle className="text-base flex items-center gap-2">
                <Sparkles className="w-4 h-4 text-[#F4A623]" />
                {active.period_label} · {active.beneficiary_label}
              </CardTitle>
              <div className="flex gap-2">
                <Button variant="secondary" size="sm" onClick={() => rerunCommentary(active.id)}
                        loading={busy}>
                  Re-run commentary
                </Button>
                <button onClick={() => setActiveId(null)}
                        className="text-xs text-[#9CA3AF] hover:text-[#374151]">close</button>
              </div>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs text-[#6B7280]">
                <div>Txns total: <strong className="text-[#0D1B2A]">{active.txn_count_total}</strong></div>
                <div>Successful: <strong className="text-[#065F46]">{active.txn_count_successful}</strong></div>
                <div>Failed: <strong className="text-[#991B1B]">{active.txn_count_failed}</strong></div>
                <div>Net: <strong className="font-mono-nums text-[#0D1B2A]">{fmtMoney(active.amount_net)}</strong></div>
              </div>
              {active.ai_commentary ? (
                <div className="text-sm whitespace-pre-wrap bg-[#FFF7ED] border border-[#FED7AA] rounded p-3">
                  {active.ai_commentary}
                </div>
              ) : (
                <p className="text-xs text-[#9CA3AF]">No commentary yet — click "Re-run commentary".</p>
              )}
              {active.error_log && (
                <div className="text-xs text-red-700 flex items-start gap-1">
                  <AlertTriangle className="w-3.5 h-3.5 mt-0.5" />
                  {active.error_log}
                </div>
              )}
            </CardContent>
          </Card>
        )}
      </main>
    </div>
  )
}
