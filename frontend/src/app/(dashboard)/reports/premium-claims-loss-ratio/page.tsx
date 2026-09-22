'use client'

/**
 * /reports/premium-claims-loss-ratio — the monthly Premium, Claims and Loss
 * Ratio report, built from the finance team's own walkthrough.
 *
 * Three tabs that build on each other: premium, claims, then claims divided by
 * premium. The work this replaces was a manual stitch of five spreadsheets, so
 * the two things that used to go wrong are shown rather than hidden — whether
 * the four tables tie, and how many source rows could not be read.
 *
 * Nothing is stored. The files are read, the report is computed, and that is
 * the end of it: no ledger entry, no posting, no saved copy to drift.
 */
import { useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { apiFetchRaw } from '@/lib/api'
import { Loader2, AlertTriangle, CheckCircle2, Upload } from 'lucide-react'

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'
const SERIF = 'Book Antiqua, Palatino, Georgia, serif'

interface TableRow { name: string; months: Record<string, string>; ytd: string }
interface RTable {
  title: string
  months: string[]
  rows: TableRow[]
  total: { months: Record<string, string>; ytd: string }
}
interface Reconciliation { balanced: boolean; totals: string[]; difference: string; note: string }
interface LRRow {
  name: string
  premium_ytd: string
  paid_ytd: string
  reserve_ytd: string
  incurred_ytd: string
  claims_ytd: string
  loss_ratio_pct: string | null
  loss_ratio_paid_pct: string | null
  pct_of_premium: string | null
  status: string
}
interface LRTable { title: string; rows: LRRow[]; total: Omit<LRRow, 'name' | 'status'> }
interface ClaimsSide { tables: RTable[]; reconciliation: Reconciliation }
interface SourceRead {
  rows_read: number
  rows_used: number
  skipped: Record<string, number>
  truncated?: boolean
}
interface OmniSourceMeta { available: boolean; source: string; months_matched?: number }
interface Report {
  premium: { tables: RTable[]; reconciliation: Reconciliation }
  claims: { reserve: ClaimsSide; paid: ClaimsSide }
  loss_ratio: LRTable[]
  omni_sources?: Record<string, OmniSourceMeta>
  months: string[]
  period: {
    from: string | null
    to: string | null
    claims_rows_outside_period: number
    premium_rows_outside_period: number
    note: string
  }
  sources: Record<string, SourceRead>
}

const pula = (s: string) => {
  const n = Number(s)
  if (!isFinite(n)) return s
  return n.toLocaleString('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

const monthLabel = (m: string) => {
  const [y, mm] = m.split('-')
  const d = new Date(Number(y), Number(mm) - 1, 1)
  return isNaN(d.getTime()) ? m : d.toLocaleDateString('en-GB', { month: 'short', year: '2-digit' })
}

function FileField({ label, hint, name, onPick }: {
  label: string; hint: string; name: string
  onPick: (name: string, f: File | null) => void
}) {
  const [picked, setPicked] = useState<string>('')
  return (
    <label className="block rounded-lg border border-dashed border-slate-300 px-4 py-3 cursor-pointer hover:border-slate-400 transition-colors">
      <div className="flex items-center gap-2">
        <Upload className="h-4 w-4 text-slate-400" />
        <span className="text-sm font-medium" style={{ color: NAVY }}>{label}</span>
      </div>
      <p className="text-xs text-slate-500 mt-1">{picked || hint}</p>
      <input
        type="file"
        className="hidden"
        accept=".xlsx,.xlsb,.xls,.xlsm,application/vnd.ms-excel,application/vnd.ms-excel.sheet.binary.macroEnabled.12,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        onChange={e => {
          const f = e.target.files?.[0] || null
          setPicked(f ? f.name : '')
          onPick(name, f)
        }}
      />
    </label>
  )
}

function ReconciliationNote({ r }: { r: Reconciliation }) {
  if (r.balanced) {
    return (
      <p className="flex items-center gap-2 text-xs text-emerald-700 mt-2">
        <CheckCircle2 className="h-3.5 w-3.5" />
        All four tables tie at P {pula(r.totals[0])}.
      </p>
    )
  }
  return (
    <p className="flex items-start gap-2 text-xs text-[#B42318] mt-2">
      <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
      <span>Tables differ by P {pula(r.difference)}. {r.note}</span>
    </p>
  )
}

function MoneyTable({ t }: { t: RTable }) {
  return (
    <div className="mb-6">
      <h3 className="text-sm font-semibold mb-2" style={{ color: NAVY }}>{t.title}</h3>
      <div className="overflow-x-auto rounded-lg border border-slate-200">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-xs text-slate-500">
            <tr>
              <th className="text-left font-medium px-3 py-2 whitespace-nowrap">Line</th>
              {t.months.map(m => (
                <th key={m} className="text-right font-medium px-3 py-2 whitespace-nowrap">{monthLabel(m)}</th>
              ))}
              <th className="text-right font-medium px-3 py-2 whitespace-nowrap">YTD</th>
            </tr>
          </thead>
          <tbody>
            {t.rows.map(r => (
              <tr key={r.name} className="border-t border-slate-100">
                <td className="px-3 py-2 whitespace-nowrap">{r.name}</td>
                {t.months.map(m => (
                  <td key={m} className="px-3 py-2 text-right tabular-nums text-slate-600 whitespace-nowrap">
                    {pula(r.months[m] || '0')}
                  </td>
                ))}
                <td className="px-3 py-2 text-right tabular-nums font-medium whitespace-nowrap">{pula(r.ytd)}</td>
              </tr>
            ))}
            <tr className="border-t-2 border-slate-300 bg-slate-50 font-semibold">
              <td className="px-3 py-2">TOTAL</td>
              {t.months.map(m => (
                <td key={m} className="px-3 py-2 text-right tabular-nums whitespace-nowrap">
                  {pula(t.total.months[m] || '0')}
                </td>
              ))}
              <td className="px-3 py-2 text-right tabular-nums whitespace-nowrap" style={{ color: NAVY }}>
                {pula(t.total.ytd)}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  )
}

function LossRatioTable({ t }: { t: LRTable }) {
  return (
    <div className="mb-6">
      <h3 className="text-sm font-semibold mb-2" style={{ color: NAVY }}>{t.title}</h3>
      <div className="overflow-x-auto rounded-lg border border-slate-200">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-xs text-slate-500">
            <tr>
              <th className="text-left font-medium px-3 py-2">Line</th>
              <th className="text-right font-medium px-3 py-2">Premiums (YTD)</th>
              <th className="text-right font-medium px-3 py-2">Paid</th>
              <th className="text-right font-medium px-3 py-2">Reserved</th>
              <th className="text-right font-medium px-3 py-2">Incurred</th>
              <th className="text-right font-medium px-3 py-2">LR paid</th>
              <th className="text-right font-medium px-3 py-2">LR incurred</th>
              <th className="text-right font-medium px-3 py-2">% of premium</th>
              <th className="text-left font-medium px-3 py-2">Status</th>
            </tr>
          </thead>
          <tbody>
            {t.rows.map(r => (
              <tr key={r.name} className="border-t border-slate-100">
                <td className="px-3 py-2 whitespace-nowrap">{r.name}</td>
                <td className="px-3 py-2 text-right tabular-nums text-slate-600">{pula(r.premium_ytd)}</td>
                <td className="px-3 py-2 text-right tabular-nums text-slate-600">{pula(r.paid_ytd)}</td>
                <td className="px-3 py-2 text-right tabular-nums text-slate-600">{pula(r.reserve_ytd)}</td>
                <td className="px-3 py-2 text-right tabular-nums text-slate-700 font-medium">{pula(r.incurred_ytd)}</td>
                <td className="px-3 py-2 text-right tabular-nums text-slate-500">
                  {r.loss_ratio_paid_pct === null ? '—' : `${r.loss_ratio_paid_pct}%`}
                </td>
                <td className="px-3 py-2 text-right tabular-nums font-medium">
                  {r.loss_ratio_pct === null ? '—' : `${r.loss_ratio_pct}%`}
                </td>
                <td className="px-3 py-2 text-right tabular-nums text-slate-500">
                  {r.pct_of_premium === null ? '—' : `${r.pct_of_premium}%`}
                </td>
                <td className="px-3 py-2">
                  <span className="text-xs font-semibold px-2 py-0.5 rounded-full"
                        style={r.status === 'High'
                          ? { background: '#FEF2F2', color: '#B42318' }
                          : r.status === 'Good'
                            ? { background: '#ECFDF5', color: '#15803D' }
                            : { background: '#F1F5F9', color: '#64748B' }}>
                    {r.status}
                  </span>
                </td>
              </tr>
            ))}
            <tr className="border-t-2 border-slate-300 bg-slate-50 font-semibold">
              <td className="px-3 py-2">TOTAL</td>
              <td className="px-3 py-2 text-right tabular-nums">{pula(t.total.premium_ytd)}</td>
              <td className="px-3 py-2 text-right tabular-nums">{pula(t.total.paid_ytd)}</td>
              <td className="px-3 py-2 text-right tabular-nums">{pula(t.total.reserve_ytd)}</td>
              <td className="px-3 py-2 text-right tabular-nums">{pula(t.total.incurred_ytd)}</td>
              <td className="px-3 py-2 text-right tabular-nums text-slate-500">
                {t.total.loss_ratio_paid_pct === null ? '—' : `${t.total.loss_ratio_paid_pct}%`}
              </td>
              <td className="px-3 py-2 text-right tabular-nums" style={{ color: NAVY }}>
                {t.total.loss_ratio_pct === null ? '—' : `${t.total.loss_ratio_pct}%`}
              </td>
              <td className="px-3 py-2 text-right tabular-nums">{t.total.pct_of_premium ?? '—'}%</td>
              <td />
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  )
}

const TABS = ['premium', 'claims', 'loss_ratio'] as const
type Tab = typeof TABS[number]
const TAB_LABEL: Record<Tab, string> = {
  premium: 'Premium Analysis',
  claims: 'Claims Analysis',
  loss_ratio: 'Loss Ratio',
}

export default function PremiumClaimsLossRatioPage() {
  const [files, setFiles] = useState<Record<string, File | null>>({})
  const [months, setMonths] = useState('')
  const [unionLegal, setUnionLegal] = useState('')
  const [health, setHealth] = useState('')
  const [report, setReport] = useState<Report | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>('premium')
  const [claimsMeasure, setClaimsMeasure] = useState<'reserve' | 'paid'>('reserve')

  const pick = (name: string, f: File | null) =>
    setFiles(prev => ({ ...prev, [name]: f }))

  async function run() {
    setBusy(true); setError(null); setReport(null)
    try {
      const fd = new FormData()
      Object.entries(files).forEach(([k, f]) => { if (f) fd.append(k, f) })
      if (months.trim()) fd.append('months', months.trim())
      if (unionLegal.trim()) fd.append('union_legal', unionLegal.trim())
      if (health.trim()) fd.append('health', health.trim())

      const res = await apiFetchRaw('/finance-report/build/', { method: 'POST', body: fd })
      // The server can answer with something that is not JSON — a gateway page
      // if the request ran long, or a plain-text limit message. Reading it as
      // text first and only then parsing means the user sees a real message
      // instead of "Unexpected token ... is not valid JSON" (Omni bug 2026-09-01).
      const raw = await res.text()
      let body: unknown = null
      try { body = raw ? JSON.parse(raw) : null } catch { body = null }
      if (!res.ok || body === null) {
        const detail = (body as { detail?: string } | null)?.detail
        throw new Error(
          detail
          || (res.status === 413 ? 'Those files are larger than the report will accept. Try one entity or a shorter period.'
          : res.status >= 500 ? 'The report took too long or the server hit an error. Try a shorter period, or a smaller file.'
          : 'Could not build the report. Please try again.'))
      }
      setReport(body as Report)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not build the report.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <TopBar title="Premium, Claims & Loss Ratio"
              breadcrumbs={[{ label: 'Reports' }, { label: 'Premium, Claims & Loss Ratio' }]} />
      <div className="p-6 max-w-7xl mx-auto">
        <header className="mb-6">
          <h1 style={{ fontFamily: SERIF, color: NAVY }} className="text-2xl font-semibold tracking-tight">
            How much came in, how much is going out.
          </h1>
          <p className="text-sm text-slate-500 mt-1 max-w-3xl">
            Premium, claims, and the loss ratio between them — built from the same source reports
            finance stitches by hand, with the same rules. Nothing is saved and nothing is posted.
          </p>
        </header>

        <section className="rounded-lg border border-slate-200 bg-white p-4 mb-8">
          <div className="grid md:grid-cols-3 gap-3">
            <FileField name="premium_board" label="Premium Board"
                       hint="Corporate + Personal premium — any Excel file" onPick={pick} />
            <FileField name="month_on_month" label="Month-on-Month Performance"
                       hint="Instant Insurance + Motor Comprehensive — any Excel file" onPick={pick} />
            <FileField name="claims_as_on_date" label="Claims As On Date"
                       hint="Every claim reserve — .xlsb or any Excel file" onPick={pick} />
          </div>

          <div className="grid md:grid-cols-3 gap-3 mt-4">
            <Field label="Period (optional)" placeholder="2026-07,2026-08"
                   hint="Leave blank to use every month in the Premium Board."
                   value={months} onChange={setMonths} />
            <Field label="Union Legal Insurance" placeholder="2026-07:125000"
                   hint="month:amount — this line has no source report."
                   value={unionLegal} onChange={setUnionLegal} />
            <Field label="Health Insurance" placeholder="2026-07:98000"
                   hint="month:amount — same."
                   value={health} onChange={setHealth} />
          </div>

          <div className="mt-4 flex items-center gap-3">
            <button
              onClick={run}
              disabled={busy}
              className="px-4 py-2 rounded-lg text-sm font-medium text-white disabled:opacity-50"
              style={{ background: NAVY }}
            >
              {busy ? <span className="flex items-center gap-2"><Loader2 className="h-4 w-4 animate-spin" /> Building…</span> : 'Build the report'}
            </button>
            {error && (
              <span className="text-sm text-[#B42318] flex items-start gap-1.5">
                <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />{error}
              </span>
            )}
          </div>
        </section>

        {report && (
          <>
            <SourceSummary report={report} />

            <div className="flex items-center gap-2 mb-5">
              {TABS.map(t => (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  className="px-3 py-1.5 rounded-full text-sm font-medium transition-colors"
                  style={t === tab
                    ? { background: NAVY, color: 'white' }
                    : { background: '#F1F5F9', color: '#475569' }}
                >
                  {TAB_LABEL[t]}
                </button>
              ))}
            </div>

            {tab === 'premium' && (
              <>
                {report.premium.tables.map(t => <MoneyTable key={t.title} t={t} />)}
                <ReconciliationNote r={report.premium.reconciliation} />
              </>
            )}
            {tab === 'claims' && (
              <>
                <div className="flex items-center gap-2 mb-4">
                  {(['reserve', 'paid'] as const).map(m => (
                    <button
                      key={m}
                      onClick={() => setClaimsMeasure(m)}
                      className="px-3 py-1.5 rounded-full text-xs font-medium transition-colors"
                      style={m === claimsMeasure
                        ? { background: ORANGE, color: '#0D1B2A' }
                        : { background: '#F1F5F9', color: '#475569' }}
                    >
                      {m === 'reserve' ? 'Reserved' : 'Paid'}
                    </button>
                  ))}
                  <span className="text-xs text-slate-400 ml-1">
                    Both sides are analysed separately; the loss-ratio tab shows them together.
                  </span>
                </div>
                {report.claims[claimsMeasure].tables.map(t => <MoneyTable key={t.title} t={t} />)}
                <ReconciliationNote r={report.claims[claimsMeasure].reconciliation} />
              </>
            )}
            {tab === 'loss_ratio' && report.loss_ratio.map(t => <LossRatioTable key={t.title} t={t} />)}
          </>
        )}
      </div>
    </>
  )
}

function Field({ label, placeholder, hint, value, onChange }: {
  label: string; placeholder: string; hint: string
  value: string; onChange: (v: string) => void
}) {
  return (
    <div>
      <label className="block text-xs font-medium text-slate-600 mb-1">{label}</label>
      <input
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2"
        style={{ ['--tw-ring-color' as string]: ORANGE }}
      />
      <p className="text-[11px] text-slate-400 mt-1">{hint}</p>
    </div>
  )
}

function SourceSummary({ report }: { report: Report }) {
  const entries = Object.entries(report.sources || {})
  const dropped = entries.reduce(
    (n, [, s]) => n + Object.values(s.skipped || {}).reduce((a, b) => a + b, 0), 0)

  return (
    <section className="rounded-lg border border-slate-200 bg-slate-50 p-4 mb-6 text-sm">
      <div className="flex flex-wrap gap-x-8 gap-y-2">
        <span><strong style={{ color: NAVY }}>Period:</strong>{' '}
          {report.period.from ? `${monthLabel(report.period.from)} – ${monthLabel(report.period.to || report.period.from)}` : '—'}
        </span>
        {entries.map(([k, s]) => (
          <span key={k} className="text-slate-600">
            <strong className="text-slate-700">{k.replace(/_/g, ' ')}:</strong>{' '}
            {s.rows_used.toLocaleString('en-GB')} of {s.rows_read.toLocaleString('en-GB')} rows used
          </span>
        ))}
      </div>

      {dropped > 0 && (
        <p className="text-xs text-amber-700 mt-2">
          {dropped.toLocaleString('en-GB')} source rows could not be placed — usually a missing date
          or a policy number with no DOMG/COMG/MIS prefix. They are excluded, not guessed at.
        </p>
      )}
      {report.period.claims_rows_outside_period > 0 && (
        <p className="text-xs text-slate-600 mt-2">
          {report.period.note}{' '}
          ({report.period.claims_rows_outside_period.toLocaleString('en-GB')} claim rows fell outside the period.)
        </p>
      )}
      {report.period.premium_rows_outside_period > 0 && (
        <p className="text-xs text-amber-700 mt-2">
          {report.period.premium_rows_outside_period.toLocaleString('en-GB')} premium rows fell outside the
          period you asked for. If that is more than you expected, check the period box.
        </p>
      )}
      {entries.some(([, s]) => s.truncated) && (
        <p className="text-xs text-[#B42318] mt-2">
          A source file was larger than this report will read and was cut short — the totals below
          understate. Split the file by period and run it again.
        </p>
      )}
    </section>
  )
}
