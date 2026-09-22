'use client'

/**
 * /payroll/dashboard — per-entity payroll dashboard.
 *
 * When the payroll team opens Payroll they land here: a summary of their
 * entity's payroll by period (headcount + Gross / PAYE / Net, broken out into
 * Basic / Commission / Incentive / Allowances), with a
 * per-period download and an "export all periods" button. Backend:
 * /api/v1/payroll/summary/ + /api/v1/payroll/export/. Payroll-view gated +
 * company-scoped server-side.
 */
import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { apiFetch, apiFetchBinary, getToken,
  getPayrollComponentCoverage, uploadPayrollRegister, askPayrollAssistant,
  type PayrollCoverageRow, type RegisterBackfillSummary } from '@/lib/api'
import { useCompany } from '@/contexts/CompanyContext'
import { TopBar } from '@/components/layout/TopBar'
import { Button } from '@/components/ui/button'
import { Download, Building2, Users, AlertCircle,
  Upload, CheckCircle2, Loader2, X, Sparkles, Send } from 'lucide-react'
import { AddEmployeeButton, PendingAdditionsCard } from './payroll-additions'

interface PeriodRow {
  period: string; headcount: number
  basic: string; commission: string; incentive: string; allowances: string
  gross: string; paye: string; net: string; ctc: string
}
interface SummaryResp {
  company: string | null
  periods: PeriodRow[]
  grand_total: {
    headcount: number; basic: string; commission: string; incentive: string
    allowances: string
    gross: string; paye: string; net: string; ctc: string
  }
}

const pula = (v: string | number) => {
  const n = typeof v === 'string' ? parseFloat(v) : v
  if (Number.isNaN(n)) return '—'
  return new Intl.NumberFormat('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n)
}

export default function PayrollDashboardPage() {
  const router = useRouter()
  const { selectedId, setSelectedId, companies, loaded: companiesLoaded } = useCompany()
  const [data, setData] = useState<SummaryResp | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [additionsKey, setAdditionsKey] = useState(0)
  // Frame guard: the payroll DATA is already role-gated server-side, but the empty
  // page frame used to open for anyone with the link. Probe the payroll access gate
  // (mirrors user_can_view_payroll) and bounce unauthorised users (CFO brief 2026-08-26).
  const [allowed, setAllowed] = useState<boolean | undefined>(undefined)

  const load = useCallback(async () => {
    setError(null)
    try { setData(await apiFetch<SummaryResp>('/payroll/summary/')) }
    catch (e) { setError(e instanceof Error ? e.message : 'Failed to load') }
  }, [])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    apiFetch<{ allowed: boolean }>('/payroll/access/')
      .then((r) => setAllowed(!!r.allowed))
      .catch(() => setAllowed(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (allowed === false) router.replace('/dashboard')
  }, [allowed, router])

  useEffect(() => {
    if (allowed) load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load, selectedId, allowed])

  async function download(period?: string) {
    const key = period || 'ALL'
    setBusy(key); setError(null)
    try {
      const qs = new URLSearchParams()
      if (period) qs.set('period', period); else qs.set('all', '1')
      if (selectedId) qs.set('company', selectedId)
      const r = await apiFetchBinary(`/payroll/export/?${qs.toString()}`)
      if (!r.ok) { const j = await r.json().catch(() => ({})); setError(j.detail || `Download failed (HTTP ${r.status})`); return }
      const blob = await r.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = period ? `payroll_${period}.xlsx` : 'payroll_all_periods.xlsx'
      document.body.appendChild(a); a.click(); a.remove()
      URL.revokeObjectURL(url)
    } catch (e) { setError(e instanceof Error ? e.message : 'Download failed') }
    finally { setBusy(null) }
  }

  const latest = data?.periods[0]
  const card = { background: 'linear-gradient(180deg,#13263d,#11233a)', borderColor: '#21384f', color: '#eaf1f8' }

  if (allowed === undefined) {
    return (
      <div className="flex flex-col min-h-screen">
        <TopBar title="Payroll Dashboard" breadcrumbs={[{ label: 'Payroll' }, { label: 'Dashboard' }]} />
        <div className="flex-1 grid place-items-center text-slate-400">
          <Loader2 className="w-6 h-6 animate-spin" />
        </div>
      </div>
    )
  }
  if (allowed === false) {
    return (
      <div className="flex flex-col min-h-screen">
        <TopBar title="Payroll Dashboard" breadcrumbs={[{ label: 'Payroll' }, { label: 'Dashboard' }]} />
        <div className="flex-1 grid place-items-center p-6">
          <div style={{ background: '#13263d', border: '1px solid #21384f', color: '#eaf1f8' }}
               className="max-w-md w-full rounded-xl p-6 text-center">
            <AlertCircle className="w-8 h-8 mx-auto mb-3" style={{ color: '#F4A623' }} />
            <div className="text-lg font-semibold mb-1">Payroll restricted</div>
            <div className="text-sm opacity-80">
              You don’t have access to the payroll dashboard. If you believe this is a mistake, contact Finance.
            </div>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar title="Payroll Dashboard" breadcrumbs={[{ label: 'Payroll' }, { label: 'Dashboard' }]} />
      <div className="flex-1 p-6 max-w-5xl mx-auto w-full space-y-4">

        {/* entity selector */}
        <div className="flex items-center gap-3 flex-wrap">
          <div className="relative">
            <Building2 className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[#9bb3c9] pointer-events-none" />
            <select aria-label="Entity" value={selectedId || ''} onChange={e => setSelectedId(e.target.value || null)} disabled={!companiesLoaded}
              className="h-10 pl-9 pr-3 rounded-lg text-sm" style={{ background: '#0f2236', border: '1px solid #21384f', color: '#eaf1f8' }}>
              <option value="">All my entities</option>
              {companies.map(c => <option key={c.id} value={c.id}>{c.code || c.name}</option>)}
            </select>
          </div>
          <Button variant="outline" size="sm" onClick={() => download()} disabled={busy !== null || !data?.periods.length}
            leftIcon={<Download className="w-3.5 h-3.5" />}>
            {busy === 'ALL' ? 'Preparing…' : 'Download all periods (Excel)'}
          </Button>
          <AddEmployeeButton onDone={() => { load(); setAdditionsKey(k => k + 1) }} />
        </div>

        {error && (
          <div className="rounded-lg p-3 text-sm flex items-center gap-2 bg-[#FEF2F2] border border-[#FECACA] text-[#991B1B]">
            <AlertCircle className="w-4 h-4" /> {error}
          </div>
        )}

        {/* new employees pending Finance sign-off (Pako Kago 2026-08-12) */}
        <PendingAdditionsCard card={card} reloadKey={additionsKey}
          onDone={() => { load(); setAdditionsKey(k => k + 1) }} />

        {/* latest-period tiles */}
        {latest && (
          <div>
            <div className="text-xs uppercase tracking-widest text-[#6B7280] font-semibold mb-2">
              Latest period · {latest.period}{data?.company ? ` · ${data.company}` : ''}
            </div>
            {/* 7 tiles since Allowances was added. Do NOT go to 7 columns: the
                page is max-w-5xl, so 7 tiles leave ~97px of content and a live
                figure like 966,731.20 paints over the next tile at EVERY
                width. 4 across (then 3) keeps every amount inside its card. */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              {[
                ['Employees', String(latest.headcount), Users],
                ['Gross', pula(latest.gross), null],
                // Commission + Incentive are material earnings inside Gross, but
                // the dashboard broke out only Basic — so a run's commission was
                // invisible here (Pako Kago bug report 2026-07-29).
                ['Commission', pula(latest.commission), null],
                ['Incentive', pula(latest.incentive), null],
                // Allowances likewise sit inside Gross and were invisible here
                // (Pako Kago 2026-07-29 — Bokani Makosha's BWP 2,000).
                ['Allowances', pula(latest.allowances), null],
                ['PAYE', pula(latest.paye), null],
                ['Net pay', pula(latest.net), null],
              ].map(([label, val], i) => (
                <div key={i} className="rounded-2xl p-4 border" style={card}>
                  <div className="text-[11px] uppercase tracking-wider text-[#9bb3c9] font-semibold">{label as string}</div>
                  <div className="text-2xl font-extrabold tabular-nums mt-1">{val as string}</div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* periods table */}
        <div className="rounded-2xl p-5 border" style={card}>
          <div className="text-xs uppercase tracking-widest text-[#9bb3c9] font-semibold mb-3">Payroll by period</div>
          {!data ? <p className="text-sm text-[#9bb3c9]">Loading…</p>
          : data.periods.length === 0 ? <p className="text-sm text-[#9bb3c9]">No payroll in your scope yet.</p>
          : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-[11px] uppercase tracking-wider text-[#9bb3c9]">
                    <th className="text-left py-2 pr-3">Period</th>
                    <th className="text-right py-2 px-2">Staff</th>
                    <th className="text-right py-2 px-2">Basic</th>
                    <th className="text-right py-2 px-2">Commission</th>
                    <th className="text-right py-2 px-2">Incentive</th>
                    <th className="text-right py-2 px-2">Allowances</th>
                    <th className="text-right py-2 px-2">Gross</th>
                    <th className="text-right py-2 px-2">PAYE</th>
                    <th className="text-right py-2 px-2">Net</th>
                    <th className="py-2 pl-2"></th>
                  </tr>
                </thead>
                <tbody>
                  {data.periods.map(p => (
                    <tr key={p.period} style={{ borderTop: '1px solid #1b3047' }}>
                      <td className="py-2.5 pr-3 font-mono">{p.period}</td>
                      <td className="text-right px-2 tabular-nums">{p.headcount}</td>
                      <td className="text-right px-2 tabular-nums text-[#9bb3c9]">{pula(p.basic)}</td>
                      <td className="text-right px-2 tabular-nums text-[#9bb3c9]">{pula(p.commission)}</td>
                      <td className="text-right px-2 tabular-nums text-[#9bb3c9]">{pula(p.incentive)}</td>
                      <td className="text-right px-2 tabular-nums text-[#9bb3c9]">{pula(p.allowances)}</td>
                      <td className="text-right px-2 tabular-nums">{pula(p.gross)}</td>
                      <td className="text-right px-2 tabular-nums text-[#fca5a5]">{pula(p.paye)}</td>
                      <td className="text-right px-2 tabular-nums font-semibold">{pula(p.net)}</td>
                      <td className="pl-2 text-right">
                        <button onClick={() => download(p.period)} disabled={busy !== null}
                          className="inline-flex items-center gap-1 text-[12px] px-2 py-1 rounded-md disabled:opacity-50"
                          style={{ background: 'rgba(244,166,35,.14)', color: '#F4A623' }}>
                          <Download className="w-3 h-3" /> {busy === p.period ? '…' : 'Excel'}
                        </button>
                      </td>
                    </tr>
                  ))}
                  {data.grand_total && (
                    <tr style={{ borderTop: '2px solid #21384f' }} className="font-bold">
                      <td className="py-2.5 pr-3">GRAND TOTAL</td>
                      <td className="text-right px-2 tabular-nums">{data.grand_total.headcount}</td>
                      <td className="text-right px-2 tabular-nums text-[#9bb3c9]">{pula(data.grand_total.basic)}</td>
                      <td className="text-right px-2 tabular-nums text-[#9bb3c9]">{pula(data.grand_total.commission)}</td>
                      <td className="text-right px-2 tabular-nums text-[#9bb3c9]">{pula(data.grand_total.incentive)}</td>
                      <td className="text-right px-2 tabular-nums text-[#9bb3c9]">{pula(data.grand_total.allowances)}</td>
                      <td className="text-right px-2 tabular-nums">{pula(data.grand_total.gross)}</td>
                      <td className="text-right px-2 tabular-nums text-[#fca5a5]">{pula(data.grand_total.paye)}</td>
                      <td className="text-right px-2 tabular-nums">{pula(data.grand_total.net)}</td>
                      <td></td>
                    </tr>
                  )}
                </tbody>
              </table>
              <p className="text-[11px] text-[#9bb3c9] mt-3">
                Gross / PAYE / Net are the figures on each payslip. Basic, Commission, Incentive and
                Allowances are their own payslip lines and may be partial for months imported as totals
                only — they are already inside Gross, not additional to it. Allowances is every other
                earning (housing, vehicle, fuel, medical, health, internet, leave pay, principal officer,
                bonus, severance) and can be negative where an employee sacrificed salary for housing
                under BURS §32. Download any period (or all) to reconcile in Excel.
              </p>
            </div>
          )}
        </div>

        {/* natural-language payroll assistant */}
        <AssistantCard />

        {/* component completeness + self-serve register backfill */}
        <CoverageCard card={card} onDone={load} />
      </div>
    </div>
  )
}

// ─── Payroll Assistant (CFO 2026-07-14: make payroll seamless + fun) ─────────
// Natural-language helper. Sends only a PII-free completeness snapshot to the
// AI (routed through reasoning_complete: Gemini → OpenAI → DeepSeek → …).
const SUGGESTIONS = [
  'What still needs finishing?',
  'Which June runs are not complete?',
  'How do I fix a “totals only” run?',
]
function AssistantCard() {
  const [q, setQ] = useState('')
  const [answer, setAnswer] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const ask = useCallback(async (question: string) => {
    const text = question.trim()
    if (!text || busy) return
    setBusy(true); setErr(null); setAnswer(null)
    try { setAnswer((await askPayrollAssistant(text)).answer) }
    catch (e) { setErr(e instanceof Error ? e.message : 'Assistant unavailable') }
    finally { setBusy(false) }
  }, [busy])

  return (
    <div className="rounded-2xl p-5 border" style={{
      background: 'linear-gradient(135deg,#13263d,#0f2236)', borderColor: '#2a4a68',
    }}>
      <div className="flex items-center gap-2 mb-1">
        <Sparkles className="w-4 h-4 text-[#F4A623]" />
        <span className="text-xs uppercase tracking-widest text-[#9bb3c9] font-semibold">Payroll Assistant</span>
      </div>
      <p className="text-[11px] text-[#9bb3c9] mb-3">
        Ask about your payroll runs in plain English. It only sees run completeness — never names, salaries or bank details.
      </p>

      <form onSubmit={e => { e.preventDefault(); ask(q) }} className="flex items-center gap-2">
        <input value={q} onChange={e => setQ(e.target.value)} disabled={busy}
          placeholder="e.g. What still needs finishing for June?"
          className="flex-1 h-10 px-3 rounded-lg text-sm outline-none"
          style={{ background: '#0b1a2c', border: '1px solid #21384f', color: '#eaf1f8' }} />
        <Button type="submit" size="sm" disabled={busy || !q.trim()}
          leftIcon={busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Send className="w-3.5 h-3.5" />}>
          {busy ? 'Thinking…' : 'Ask'}
        </Button>
      </form>

      <div className="flex flex-wrap gap-2 mt-2">
        {SUGGESTIONS.map(s => (
          <button key={s} onClick={() => { setQ(s); ask(s) }} disabled={busy}
            className="text-[11px] px-2.5 py-1 rounded-full disabled:opacity-50"
            style={{ background: 'rgba(244,166,35,.12)', color: '#F4A623', border: '1px solid rgba(244,166,35,.25)' }}>
            {s}
          </button>
        ))}
      </div>

      {err && (
        <div className="rounded-lg p-3 text-sm flex items-center gap-2 mt-3 bg-[#FEF2F2] border border-[#FECACA] text-[#991B1B]">
          <AlertCircle className="w-4 h-4" /> {err}
        </div>
      )}
      {answer && (
        <div className="rounded-xl p-4 mt-3 text-sm leading-relaxed whitespace-pre-wrap"
          style={{ background: '#0b1a2c', border: '1px solid #21384f', color: '#eaf1f8' }}>
          {answer}
        </div>
      )}
    </div>
  )
}

// ─── Component completeness + register backfill (CFO 2026-07-14) ─────────────
// Shows which payroll runs are "totals only" (no per-component breakdown, so the
// report columns are blank) and lets the payroll team fix any run themselves by
// uploading its register. Dry-run first: it previews how many employees tie to
// the approved Gross before anything is written.
function CoverageCard({ card, onDone }: { card: React.CSSProperties; onDone: () => void }) {
  const [rows, setRows] = useState<PayrollCoverageRow[] | null>(null)
  const [target, setTarget] = useState<{ period: string; company: string } | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [dry, setDry] = useState<RegisterBackfillSummary | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const loadCoverage = useCallback(async () => {
    try { setRows((await getPayrollComponentCoverage()).coverage) }
    catch (e) { setErr(e instanceof Error ? e.message : 'Failed to load coverage') }
  }, [])
  useEffect(() => { loadCoverage() }, [loadCoverage])

  async function pick(period: string, company: string, f: File) {
    setBusy(true); setErr(null); setDry(null)
    setTarget({ period, company }); setFile(f)
    try { setDry(await uploadPayrollRegister(f, period, company, false)) }
    catch (e) { setErr(e instanceof Error ? e.message : 'Upload failed') }
    finally { setBusy(false) }
  }
  async function commit() {
    if (!target || !file) return
    setBusy(true); setErr(null)
    try {
      await uploadPayrollRegister(file, target.period, target.company, true)
      setDry(null); setTarget(null); setFile(null)
      await loadCoverage(); onDone()
    } catch (e) { setErr(e instanceof Error ? e.message : 'Load failed') }
    finally { setBusy(false) }
  }
  function reset() { setDry(null); setTarget(null); setFile(null); setErr(null) }

  const incomplete = rows?.filter(r => !r.complete && r.payslips > 0) ?? []

  return (
    <div className="rounded-2xl p-5 border" style={card}>
      <div className="flex items-center justify-between mb-1">
        <div className="text-xs uppercase tracking-widest text-[#9bb3c9] font-semibold">
          Component completeness
        </div>
        {rows && incomplete.length === 0 && (
          <span className="inline-flex items-center gap-1 text-[11px] text-[#34d399]">
            <CheckCircle2 className="w-3.5 h-3.5" /> All runs have a full breakdown
          </span>
        )}
      </div>
      <p className="text-[11px] text-[#9bb3c9] mb-3">
        A run marked “totals only” has no per-component breakdown, so those columns are blank in the report.
        Upload that run’s register to load the detail — only employees whose register Gross matches the
        approved Gross are loaded, so an approved total is never changed.
      </p>

      {!rows ? <p className="text-sm text-[#9bb3c9]">Loading…</p>
      : rows.length === 0 ? <p className="text-sm text-[#9bb3c9]">No payroll runs yet.</p>
      : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-[11px] uppercase tracking-wider text-[#9bb3c9]">
                <th className="text-left py-2 pr-3">Run</th>
                <th className="text-right py-2 px-2">Payslips</th>
                <th className="text-right py-2 px-2">With breakdown</th>
                <th className="text-right py-2 px-2">Totals only</th>
                <th className="py-2 pl-2"></th>
              </tr>
            </thead>
            <tbody>
              {rows.map(r => (
                <tr key={`${r.period}/${r.company}`} style={{ borderTop: '1px solid #1b3047' }}>
                  <td className="py-2.5 pr-3 font-mono">{r.period} · {r.company}</td>
                  <td className="text-right px-2 tabular-nums">{r.payslips}</td>
                  <td className="text-right px-2 tabular-nums text-[#34d399]">{r.with_components}</td>
                  <td className="text-right px-2 tabular-nums" style={{ color: r.totals_only ? '#fbbf24' : '#9bb3c9' }}>
                    {r.totals_only || '—'}
                  </td>
                  <td className="pl-2 text-right">
                    {r.complete ? (
                      <span className="inline-flex items-center gap-1 text-[11px] text-[#34d399]">
                        <CheckCircle2 className="w-3.5 h-3.5" /> Complete
                      </span>
                    ) : (
                      <label className="inline-flex items-center gap-1 text-[12px] px-2 py-1 rounded-md cursor-pointer"
                        style={{ background: 'rgba(244,166,35,.14)', color: '#F4A623' }}>
                        <Upload className="w-3 h-3" /> Upload register
                        <input type="file" accept=".xlsx,.xls,.csv" className="hidden" disabled={busy}
                          onChange={e => { const f = e.target.files?.[0]; if (f) pick(r.period, r.company, f); e.currentTarget.value = '' }} />
                      </label>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {err && (
        <div className="rounded-lg p-3 text-sm flex items-center gap-2 mt-3 bg-[#FEF2F2] border border-[#FECACA] text-[#991B1B]">
          <AlertCircle className="w-4 h-4" /> {err}
        </div>
      )}

      {busy && !dry && (
        <div className="flex items-center gap-2 text-sm text-[#9bb3c9] mt-3">
          <Loader2 className="w-4 h-4 animate-spin" /> Reading the register…
        </div>
      )}

      {/* dry-run preview → confirm load */}
      {dry && target && (
        <div className="rounded-xl p-4 mt-3 border" style={{ background: '#0f2236', borderColor: '#21384f' }}>
          <div className="flex items-center justify-between mb-2">
            <div className="text-sm font-semibold text-[#eaf1f8]">
              {target.period} · {target.company} — preview
            </div>
            <button onClick={reset} className="text-[#9bb3c9] hover:text-white"><X className="w-4 h-4" /></button>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-center mb-3">
            {[
              ['Ready to load', dry.reconciled, '#34d399'],
              ['Don’t tie', dry.recon_fail, '#fbbf24'],
              ['Name not matched', dry.unmatched, '#fca5a5'],
              ['Payslips', dry.payslips, '#9bb3c9'],
            ].map(([l, v, c], i) => (
              <div key={i} className="rounded-lg py-2" style={{ background: '#13263d' }}>
                <div className="text-lg font-extrabold tabular-nums" style={{ color: c as string }}>{v as number}</div>
                <div className="text-[10px] uppercase tracking-wider text-[#9bb3c9]">{l as string}</div>
              </div>
            ))}
          </div>
          {dry.recon_fail > 0 && (
            <p className="text-[11px] text-[#fbbf24] mb-2">
              {dry.recon_fail} employee(s) have a register Gross that doesn’t match the approved payslip Gross —
              they are skipped (not loaded). Reconcile those and re-upload.
              {dry.fails.length > 0 && (
                <span className="block text-[#9bb3c9] mt-1">
                  e.g. {dry.fails.slice(0, 3).map(f => `${f.employee} (reg ${f.register_gross} vs ${f.system_gross})`).join('; ')}
                </span>
              )}
            </p>
          )}
          <div className="flex items-center gap-2">
            <Button size="sm" onClick={commit} disabled={busy || dry.reconciled === 0}
              leftIcon={busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <CheckCircle2 className="w-3.5 h-3.5" />}>
              {busy ? 'Loading…' : `Load ${dry.reconciled} breakdown${dry.reconciled === 1 ? '' : 's'}`}
            </Button>
            <Button size="sm" variant="outline" onClick={reset} disabled={busy}>Cancel</Button>
          </div>
        </div>
      )}
    </div>
  )
}
