'use client'

/**
 * /payroll/monthly-pack — Monthly payroll pack + 3-way control check
 * (CFO directive 2026-08-24).
 *
 * One screen: this month vs last month for a company, plus two controls:
 *  - Incentives paid on payslips vs the Staff Incentive module (approved +
 *    processed) — flags paid-not-approved, rejected-but-paid, over-approved.
 *  - Salary changes / new joiners vs the signed Authority to Recruit/Regrade —
 *    flags no-authority, paid-above-signed, applied-before-effective.
 * Download the full pack as Excel.
 */

import { useCallback, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  getPayrollPeriods, getCompanies, getMonthlyPayrollPack, downloadMonthlyPayrollPack,
} from '@/lib/api'
import type { MonthlyPack, PayrollPeriod, Company } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { ArrowLeft, Download, AlertTriangle, CheckCircle2, Coins, ShieldCheck } from 'lucide-react'

const NAVY = '#0D1B2A'
const pula = (v: string | number | undefined) =>
  'P' + Number(v ?? 0).toLocaleString('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const num = (v: string | number | undefined) => Number(v ?? 0)

// flags that are genuine exceptions (colour red); everything else is fine
const BAD = new Set([
  'NOT_IN_MODULE', 'REJECTED_BUT_PAID', 'PAID_NOT_APPROVED', 'AMOUNT_DIFF',
  'APPROVED_NOT_PROCESSED', 'NO_AUTHORITY', 'ABOVE_SIGNED', 'EARLY',
])
function Flag({ flag }: { flag: string }) {
  const bad = BAD.has(flag)
  return (
    <span className="inline-flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded"
          style={bad ? { background: '#FEE2E2', color: '#991B1B' } : { background: '#D1FAE5', color: '#065F46' }}>
      {bad ? <AlertTriangle className="w-3 h-3" /> : <CheckCircle2 className="w-3 h-3" />}
      {flag.replace(/_/g, ' ').toLowerCase()}
    </span>
  )
}

function Delta({ a, b }: { a: string | number; b: string | number }) {
  const d = num(a) - num(b)
  const c = d < 0 ? '#991B1B' : d > 0 ? '#065F46' : '#555'
  return <span style={{ color: c, fontWeight: 600 }}>{d >= 0 ? '+' : ''}{pula(d)}</span>
}

export default function MonthlyPackPage() {
  const router = useRouter()
  const [periods, setPeriods] = useState<PayrollPeriod[]>([])
  const [companies, setCompanies] = useState<Company[]>([])
  const [period, setPeriod] = useState('')
  const [company, setCompany] = useState('')
  const [pack, setPack] = useState<MonthlyPack | null>(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)

  // initial dropdowns
  useEffect(() => {
    (async () => {
      try {
        const [prResp, co] = await Promise.all([getPayrollPeriods(), getCompanies({ is_active: 'true' })])
        const pr = [...(prResp.results || [])].sort(
          (a, b) => (b.period_name || '').localeCompare(a.period_name || ''))
        setPeriods(pr)
        const clist = co.results || []
        setCompanies(clist)
        if (pr.length) setPeriod(pr[0].period_name)
        const adic = clist.find(c => /alpha direct insurance$/i.test(c.name)) || clist[0]
        if (adic) setCompany(adic.id)
      } catch {
        setMsg({ kind: 'err', text: 'Could not load periods or companies.' })
      }
    })()
  }, [])

  const load = useCallback(async () => {
    if (!period || !company) return
    setLoading(true); setMsg(null)
    try {
      setPack(await getMonthlyPayrollPack(period, company))
    } catch (e) {
      setPack(null); setMsg({ kind: 'err', text: (e as Error).message || 'Could not build the pack.' })
    } finally { setLoading(false) }
  }, [period, company])

  useEffect(() => { load() }, [load])

  const download = async () => {
    setBusy(true); setMsg(null)
    try { await downloadMonthlyPayrollPack(period, company) }
    catch (e) { setMsg({ kind: 'err', text: (e as Error).message || 'Download failed.' }) }
    finally { setBusy(false) }
  }

  const cur = pack?.totals.current
  const pri = pack?.totals.prior
  const ir = pack?.incentive_recon
  const badIncent = (ir?.rows || []).filter(r => BAD.has(r.flag))
  const badAtr = (pack?.atr_check || []).filter(r => BAD.has(r.flag))

  return (
    <div className="min-h-screen" style={{ background: '#F7F7F5' }}>
      <TopBar />
      <div className="max-w-6xl mx-auto px-4 py-6">
        <button onClick={() => router.push('/payroll/dashboard')}
                className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-gray-800 mb-3">
          <ArrowLeft className="w-4 h-4" /> Payroll
        </button>

        <div className="flex items-start justify-between gap-4 flex-wrap mb-4">
          <div>
            <h1 className="text-2xl font-bold" style={{ color: NAVY, fontFamily: 'Book Antiqua, serif' }}>
              Monthly Payroll Pack
            </h1>
            <p className="text-sm text-gray-500">This month vs last month, with the incentive and authority checks.</p>
          </div>
          <div className="flex items-end gap-2 flex-wrap">
            <label className="text-xs text-gray-500">Month
              <select value={period} onChange={e => setPeriod(e.target.value)}
                      className="block mt-0.5 border rounded px-2 py-1.5 text-sm bg-white min-w-[7rem]">
                {periods.map(p => <option key={p.id} value={p.period_name}>{p.period_name}</option>)}
              </select>
            </label>
            <label className="text-xs text-gray-500">Company
              <select value={company} onChange={e => setCompany(e.target.value)}
                      className="block mt-0.5 border rounded px-2 py-1.5 text-sm bg-white min-w-[12rem]">
                {companies.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
            </label>
            <Button onClick={download} disabled={busy || !pack}
                    style={{ background: NAVY }} className="text-white">
              <Download className="w-4 h-4 mr-1" /> {busy ? 'Preparing…' : 'Download Excel'}
            </Button>
          </div>
        </div>

        {msg && (
          <div className="mb-4 px-3 py-2 rounded text-sm"
               style={msg.kind === 'ok' ? { background: '#D1FAE5', color: '#065F46' } : { background: '#FEE2E2', color: '#991B1B' }}>
            {msg.text}
          </div>
        )}
        {loading && <p className="text-sm text-gray-500">Loading…</p>}

        {pack && cur && (
          <div className="space-y-5">
            {/* headline totals */}
            <Card>
              <CardHeader><CardTitle style={{ color: NAVY }}>
                {pack.meta.period} vs {pack.meta.prior_period || '—'} · {pack.meta.company}
              </CardTitle></CardHeader>
              <CardContent>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead><tr className="text-left text-white" style={{ background: NAVY }}>
                      <th className="px-3 py-2">Measure</th>
                      <th className="px-3 py-2 text-right">{pack.meta.period}</th>
                      <th className="px-3 py-2 text-right">{pack.meta.prior_period || '—'}</th>
                      <th className="px-3 py-2 text-right">Change</th>
                    </tr></thead>
                    <tbody>
                      {[
                        ['Employees paid', String(cur.headcount), String(pri?.headcount ?? 0), null],
                        ['Basic salaries', pula(cur.basic), pula(pri?.basic), ['basic']],
                        ['Commission', pula(cur.commission), pula(pri?.commission), ['commission']],
                        ['Incentives', pula(cur.incentive), pula(pri?.incentive), ['incentive']],
                        ['Allowances & benefits', pula(cur.allowances), pula(pri?.allowances), ['allowances']],
                        ['Gross pay', pula(cur.gross), pula(pri?.gross), ['gross']],
                        ['PAYE', pula(cur.paye), pula(pri?.paye), ['paye']],
                        ['Net pay', pula(cur.net), pula(pri?.net), ['net']],
                        ['Cost to company', pula(cur.ctc), pula(pri?.ctc), ['ctc']],
                      ].map((r, i) => {
                        const key = r[3] as string[] | null
                        return (
                          <tr key={i} className={i % 2 ? 'bg-gray-50' : ''}>
                            <td className="px-3 py-1.5 font-medium">{r[0]}</td>
                            <td className="px-3 py-1.5 text-right">{r[1]}</td>
                            <td className="px-3 py-1.5 text-right text-gray-500">{r[2]}</td>
                            <td className="px-3 py-1.5 text-right">
                              {key ? <Delta a={(cur as never)[key[0]]} b={(pri as never)?.[key[0]] ?? 0} />
                                   : (cur.headcount - (pri?.headcount ?? 0))}
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              </CardContent>
            </Card>

            {/* incentive check */}
            <Card>
              <CardHeader><CardTitle className="flex items-center gap-2" style={{ color: NAVY }}>
                <Coins className="w-5 h-5" /> Incentive check — payroll vs module
              </CardTitle></CardHeader>
              <CardContent>
                <div className="grid grid-cols-2 sm:grid-cols-5 gap-2 mb-3 text-sm">
                  {[['Paid in payroll', ir!.pay_total], ['Module approved', ir!.mod_approved_total],
                    ['Module PROCESSED', ir!.mod_processed_total], ['Pending', ir!.mod_pending_total],
                    ['Rejected', ir!.mod_rejected_total]].map(([l, v], i) => (
                    <div key={i} className="rounded border p-2">
                      <div className="text-xs text-gray-500">{l as string}</div>
                      <div className="font-semibold" style={{ color: NAVY }}>{pula(v as string)}</div>
                    </div>
                  ))}
                </div>
                {num(ir!.mod_processed_total) === 0 && num(ir!.pay_total) > 0 && (
                  <div className="mb-3 px-3 py-2 rounded text-sm" style={{ background: '#FEF3C7', color: '#92400E' }}>
                    <AlertTriangle className="w-4 h-4 inline mr-1" />
                    Incentives were paid on payslips but nothing is marked processed in the module this month.
                  </div>
                )}
                <p className="text-sm font-semibold mb-1">Exceptions ({badIncent.length})</p>
                {badIncent.length === 0 ? <p className="text-sm text-gray-500">None — every paid incentive matches an approved, processed module line.</p> : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead><tr className="text-left text-gray-500 border-b">
                        <th className="px-2 py-1">Employee</th><th className="px-2 py-1">Dept</th>
                        <th className="px-2 py-1 text-right">Paid</th><th className="px-2 py-1">Flag</th>
                        <th className="px-2 py-1">Note</th>
                      </tr></thead>
                      <tbody>
                        {badIncent.map((r, i) => (
                          <tr key={i} className="border-b last:border-0">
                            <td className="px-2 py-1 font-medium">{r.name}</td>
                            <td className="px-2 py-1 text-gray-500">{r.dept}</td>
                            <td className="px-2 py-1 text-right">{pula(r.amount)}</td>
                            <td className="px-2 py-1"><Flag flag={r.flag} /></td>
                            <td className="px-2 py-1 text-gray-600">{r.note}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </CardContent>
            </Card>

            {/* authority check */}
            <Card>
              <CardHeader><CardTitle className="flex items-center gap-2" style={{ color: NAVY }}>
                <ShieldCheck className="w-5 h-5" /> Authority check — salary changes &amp; joiners
              </CardTitle></CardHeader>
              <CardContent>
                <p className="text-sm font-semibold mb-1">Needing attention ({badAtr.length})</p>
                {badAtr.length === 0 ? <p className="text-sm text-gray-500">None — every change matches a signed authority.</p> : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead><tr className="text-left text-gray-500 border-b">
                        <th className="px-2 py-1">Person</th><th className="px-2 py-1">Change</th>
                        <th className="px-2 py-1">Authority</th><th className="px-2 py-1">Flag</th>
                        <th className="px-2 py-1">Note</th>
                      </tr></thead>
                      <tbody>
                        {badAtr.map((r, i) => (
                          <tr key={i} className="border-b last:border-0">
                            <td className="px-2 py-1 font-medium">{r.person}</td>
                            <td className="px-2 py-1 text-gray-600">{r.change}</td>
                            <td className="px-2 py-1">{r.authority || '—'}</td>
                            <td className="px-2 py-1"><Flag flag={r.flag} /></td>
                            <td className="px-2 py-1 text-gray-600">{r.note}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </CardContent>
            </Card>

            {/* major changes */}
            <Card>
              <CardHeader><CardTitle style={{ color: NAVY }}>Major changes</CardTitle></CardHeader>
              <CardContent className="space-y-3 text-sm">
                <div>
                  <span className="font-semibold">Joiners:</span>{' '}
                  {pack.major_changes.joiners.length
                    ? pack.major_changes.joiners.map(j => `${j.name} (${j.dept})`).join(', ')
                    : 'none'}
                </div>
                <div>
                  <span className="font-semibold">Leavers:</span>{' '}
                  {pack.major_changes.leavers.length
                    ? pack.major_changes.leavers.map(l => `${l.name} (${l.dept})`).join(', ')
                    : 'none'}
                </div>
                {pack.major_changes.moves.length > 0 && (
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead><tr className="text-left text-gray-500 border-b">
                        <th className="px-2 py-1">Employee</th><th className="px-2 py-1">Dept</th>
                        <th className="px-2 py-1 text-right">Basic Δ</th><th className="px-2 py-1 text-right">Gross Δ</th>
                      </tr></thead>
                      <tbody>
                        {pack.major_changes.moves.map((m, i) => (
                          <tr key={i} className="border-b last:border-0">
                            <td className="px-2 py-1 font-medium">{m.name}</td>
                            <td className="px-2 py-1 text-gray-500">{m.dept}</td>
                            <td className="px-2 py-1 text-right"><Delta a={m.basic_cur} b={m.basic_prior} /></td>
                            <td className="px-2 py-1 text-right"><Delta a={m.gross_cur} b={m.gross_prior} /></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        )}
      </div>
    </div>
  )
}
