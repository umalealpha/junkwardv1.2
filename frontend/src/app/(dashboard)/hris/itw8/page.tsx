'use client'

/**
 * /hris/itw8 — BURS ITW8 Tax Certificate generator.
 *
 * CFO directive 2026-05-18 (Unami audit closeout): every employee must
 * be able to download their ITW8 tax certificate in the BURS layout.
 *
 * Data source: GET /hris/api/my-itw8/?year=YYYY
 * Until the payroll module is fully wired the endpoint returns zero
 * figures with `is_live: false` — the page surfaces a clear "draft"
 * banner so HR knows it's not yet certifiable.
 */
import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { ChevronLeft, Download, Info, Printer } from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { useHrisAccess } from '@/hooks/useHrisAccess'
import { authedHrisFetch } from '../_shared'

interface Itw8Response {
  is_live: boolean
  tax_year: number
  employee_name: string
  employee_id_number: string
  employer_name: string
  employer_burs_tin: string
  basic_salary: number
  allowances: {
    housing: number
    transport: number
    cellphone: number
    other: number
  }
  gross_earnings: number
  paye_deducted: number
  net_pay: number
}

function fmtP(n: number): string {
  return new Intl.NumberFormat('en-BW', {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  }).format(n || 0)
}

export default function HrisItw8Page() {
  const router = useRouter()
  const { theme } = useTheme()
  const allowed = useHrisAccess()
  const thisYear = new Date().getFullYear()
  const [year, setYear] = useState(thisYear - 1)
  const [data, setData] = useState<Itw8Response | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (allowed === false) router.replace('/dashboard')
  }, [allowed, router])

  useEffect(() => {
    if (allowed !== true) return
    setLoading(true)
    authedHrisFetch(`/hris/api/my-itw8/?year=${year}`)
      .then(async r => { if (r.ok) setData(await r.json()) })
      .finally(() => setLoading(false))
  }, [allowed, year])

  if (allowed !== true) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ backgroundColor: theme.bg }}>
        <div className="w-8 h-8 border-2 border-[#F07F00] border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  function downloadCsv() {
    if (!data) return
    const rows: (string | number)[][] = [
      ['ITW8 Tax Certificate', `${data.tax_year}`],
      ['Employer', data.employer_name],
      ['Employer BURS TIN', data.employer_burs_tin],
      ['Employee', data.employee_name],
      ['Employee ID', data.employee_id_number],
      [],
      ['Basic salary',           data.basic_salary.toFixed(2)],
      ['Housing allowance',      data.allowances.housing.toFixed(2)],
      ['Transport allowance',    data.allowances.transport.toFixed(2)],
      ['Cellphone allowance',    data.allowances.cellphone.toFixed(2)],
      ['Other allowances',       data.allowances.other.toFixed(2)],
      ['Gross earnings',         data.gross_earnings.toFixed(2)],
      ['PAYE deducted',          data.paye_deducted.toFixed(2)],
      ['Net pay',                data.net_pay.toFixed(2)],
    ]
    const csv = rows.map(r => r.map(c => {
      const s = String(c ?? '')
      return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
    }).join(',')).join('\n') + '\n'
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `ITW8-${data.employee_name.replace(/\s+/g, '_')}-${data.tax_year}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar title="ITW8 Tax Certificate" breadcrumbs={[{ label: 'HRIS', href: '/hris' }, { label: 'ITW8' }]} />
      <main className="flex-1 overflow-y-auto p-6 space-y-5">
        <div className="flex items-center justify-between flex-wrap gap-3 print:hidden">
          <Link href="/hris" className="inline-flex items-center gap-1.5 text-sm font-medium" style={{ color: theme.t2 }}>
            <ChevronLeft className="w-4 h-4" /> Back to HRIS
          </Link>
          <div className="flex items-center gap-2">
            <label className="text-xs" style={{ color: theme.t2 }}>Tax year</label>
            <select value={year} onChange={e => setYear(parseInt(e.target.value))}
                    className="h-9 px-2 rounded-md text-sm"
                    style={{ background: theme.card, border: `1px solid ${theme.cardBdr}`, color: theme.text }}>
              {[thisYear - 1, thisYear - 2, thisYear - 3, thisYear].map(y => (
                <option key={y} value={y}>{y}</option>
              ))}
            </select>
            <button type="button" onClick={() => window.print()}
                    className="inline-flex items-center gap-1.5 h-9 px-3 rounded-md text-sm font-semibold"
                    style={{ background: theme.g100, color: theme.text, border: `1px solid ${theme.cardBdr}` }}>
              <Printer className="w-3.5 h-3.5" /> Print
            </button>
            <button type="button" onClick={downloadCsv} disabled={!data}
                    className="inline-flex items-center gap-1.5 h-9 px-3 rounded-md text-sm font-semibold disabled:opacity-50"
                    style={{ background: theme.orange, color: '#fff' }}>
              <Download className="w-3.5 h-3.5" /> CSV
            </button>
          </div>
        </div>

        {data && !data.is_live && (
          <div className="rounded-xl px-4 py-3 text-sm flex items-start gap-2 print:hidden"
               style={{ background: '#fef3c7', color: '#92400e', border: '1px solid #fde68a' }}>
            <Info className="w-4 h-4 flex-shrink-0 mt-0.5" />
            <div>
              <strong>Draft certificate.</strong> Payroll data has not been posted to the ITW8 endpoint yet —
              every figure below is BWP 0.00 until HR finishes wiring the payroll exporter. The layout matches
              the BURS standard so once data lands, the same page is print-ready.
            </div>
          </div>
        )}

        {/* The certificate itself — designed to print one page A4. */}
        <div className="rounded-2xl p-8 space-y-6 mx-auto"
             style={{
               background: '#fff',
               border: `1px solid ${theme.cardBdr}`,
               boxShadow: '0 4px 20px rgba(0,0,0,0.04)',
               maxWidth: 800,
             }}>
          <div className="flex items-start justify-between">
            <div>
              <div className="text-[10px] uppercase tracking-widest font-semibold" style={{ color: theme.t2 }}>
                Botswana Unified Revenue Service
              </div>
              <h1 className="font-display text-2xl font-bold mt-1" style={{ color: theme.navy }}>
                ITW8 — Tax Certificate
              </h1>
              <div className="text-xs mt-1" style={{ color: theme.t2 }}>
                Tax year {data?.tax_year ?? year}
              </div>
            </div>
            <div className="text-right text-xs" style={{ color: theme.t2 }}>
              <div className="font-semibold" style={{ color: theme.text }}>{data?.employer_name || 'Alpha Direct Insurance Co.'}</div>
              <div>BURS TIN: {data?.employer_burs_tin || '—'}</div>
              <div>Plot 50369, Fairgrounds, Gaborone</div>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4 text-sm">
            <div>
              <div className="text-[10px] uppercase tracking-widest font-semibold" style={{ color: theme.t2 }}>Employee</div>
              <div className="font-semibold" style={{ color: theme.text }}>{data?.employee_name || '—'}</div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-widest font-semibold" style={{ color: theme.t2 }}>Employee ID</div>
              <div className="font-mono" style={{ color: theme.text }}>{data?.employee_id_number || '—'}</div>
            </div>
          </div>

          <table className="w-full text-sm">
            <thead>
              <tr style={{ borderBottom: `2px solid ${theme.text}` }}>
                <th className="py-2 text-left text-[11px] uppercase tracking-wider font-semibold" style={{ color: theme.t2 }}>Earning</th>
                <th className="py-2 text-right text-[11px] uppercase tracking-wider font-semibold" style={{ color: theme.t2 }}>Amount (BWP)</th>
              </tr>
            </thead>
            <tbody>
              <Row label="Basic salary"        value={data?.basic_salary ?? 0} theme={theme} />
              <Row label="Housing allowance"   value={data?.allowances.housing ?? 0} theme={theme} />
              <Row label="Transport allowance" value={data?.allowances.transport ?? 0} theme={theme} />
              <Row label="Cellphone allowance" value={data?.allowances.cellphone ?? 0} theme={theme} />
              <Row label="Other allowances"    value={data?.allowances.other ?? 0} theme={theme} />
              <tr style={{ borderTop: `1px solid ${theme.text}` }}>
                <td className="py-2 font-semibold" style={{ color: theme.text }}>Gross earnings</td>
                <td className="py-2 text-right font-semibold tabular-nums" style={{ color: theme.text }}>
                  {fmtP(data?.gross_earnings ?? 0)}
                </td>
              </tr>
              <Row label="PAYE deducted"       value={-(data?.paye_deducted ?? 0)} theme={theme} />
              <tr style={{ borderTop: `2px solid ${theme.text}` }}>
                <td className="py-2 font-bold" style={{ color: theme.navy }}>Net pay</td>
                <td className="py-2 text-right font-bold tabular-nums" style={{ color: theme.navy }}>
                  {fmtP(data?.net_pay ?? 0)}
                </td>
              </tr>
            </tbody>
          </table>

          <div className="text-[10px] mt-4" style={{ color: theme.t2 }}>
            Certified on behalf of {data?.employer_name || 'the employer'}. This document is generated
            electronically and is valid without signature for tax-year {data?.tax_year ?? year}.
          </div>
        </div>

        {loading && (
          <p className="text-xs text-center" style={{ color: theme.t2 }}>Loading…</p>
        )}
      </main>

      <style jsx global>{`
        @media print {
          body { background: #fff !important; }
          aside, header { display: none !important; }
          main { padding: 0 !important; }
        }
      `}</style>
    </div>
  )
}

function Row({ label, value, theme }: { label: string; value: number; theme: any }) {
  return (
    <tr style={{ borderBottom: `1px solid ${theme.cardBdr}` }}>
      <td className="py-2" style={{ color: theme.text }}>{label}</td>
      <td className="py-2 text-right tabular-nums" style={{ color: theme.text }}>{fmtP(value)}</td>
    </tr>
  )
}
