'use client'

/**
 * /reports/entity-pl — Per-entity Profit & Loss (CFO directive 2026-05-21).
 *
 * Renders the unique P&L structure for each non-ADIC group entity using
 * the templates defined in reporting/entity_pl_templates.py. ADIC is
 * intentionally excluded — its insurance MA P&L lives at
 * /reports/profit-loss and is untouched.
 */

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { getEntityPL, getCompanies, getToken } from '@/lib/api'
import type { EntityPLReport, Company } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { AlertCircle, Download, RefreshCw, ArrowLeft } from 'lucide-react'
import { formatAmount, localYmd } from '@/lib/utils'

// Non-ADIC entities only — matches the backend template registry.
const SUPPORTED_ENTITIES: Array<{ code: string; alias: string; ccy: string; name: string }> = [
  { code: 'QIH',   alias: 'QTM',    ccy: 'BWP', name: 'Quantum Insurance Holdings' },
  { code: 'VCM',   alias: 'VER',    ccy: 'BWP', name: 'Veritas Capital (salvage)' },
  { code: 'RSA',   alias: 'RSA',    ccy: 'BWP', name: 'Risk Software Africa' },
  { code: 'ADSA',  alias: 'ADSA',   ccy: 'ZAR', name: 'Alpha Direct South Africa' },
  { code: 'GCX',   alias: 'GCE',    ccy: 'BWP', name: 'Gaborone Coin Exchange' },
  { code: 'UNI',   alias: 'UNI',    ccy: 'BWP', name: 'Unicoin' },
  { code: 'ADIPL', alias: 'SGP',    ccy: 'USD', name: 'Alpha Direct Insurtech Pte (Singapore)' },
  { code: 'AIZ',   alias: 'ZMB',    ccy: 'ZMW', name: 'Alpha Insurtech Zambia' },
  { code: 'ADIL',  alias: 'ADLIFE', ccy: 'BWP', name: 'Alpha Direct Life' },
  { code: 'ADRG',  alias: 'ADRISK', ccy: 'INR', name: 'ADRisk Global (India)' },
]

function defaultRange() {
  const today = new Date()
  const m = today.getMonth()
  const fyStartYear = m >= 6 ? today.getFullYear() : today.getFullYear() - 1
  return {
    from: `${fyStartYear}-07-01`,
    to:   localYmd(today),
  }
}

export default function EntityProfitLossPage() {
  const router = useRouter()
  const init = defaultRange()
  const [companyCode, setCompanyCode] = useState('QIH')
  const [fromDate, setFromDate] = useState(init.from)
  const [toDate,   setToDate]   = useState(init.to)
  const [companies, setCompanies] = useState<Company[]>([])
  const [report, setReport]   = useState<EntityPLReport | null>(null)
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState<string | null>(null)

  const fmt = (v: string | number, ccy: string) => {
    const n = typeof v === 'string' ? parseFloat(v) : v
    if (Number.isNaN(n)) return '—'
    return new Intl.NumberFormat('en-BW', {
      style: 'currency', currency: ccy,
      currencyDisplay: 'code',
      minimumFractionDigits: 2, maximumFractionDigits: 2,
    }).format(n).replace(ccy, ccy + ' ')
  }

  const load = useCallback(async () => {
    if (companyCode === 'ADIC') {
      setError("ADIC uses the dedicated insurance MA P&L at /reports/profit-loss.")
      setReport(null)
      setLoading(false)
      return
    }
    setLoading(true)
    setError(null)
    try {
      const co = companies.find(c => c.code === companyCode)
      if (!co) throw new Error(`Company ${companyCode} not loaded yet`)
      const data = await getEntityPL(fromDate, toDate, String(co.id))
      setReport(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load report')
    } finally {
      setLoading(false)
    }
  }, [companyCode, fromDate, toDate, companies])

  useEffect(() => {
    const token = getToken()
    if (!token) { router.replace('/login'); return }
    getCompanies()
      .then(r => setCompanies(r.results))
      .catch(() => {})
  }, [router])

  useEffect(() => {
    if (companies.length > 0) load()
  }, [load, companies.length])

  function exportCsv() {
    if (!report || !report.sections) return
    const ccy = report.currency || 'BWP'
    const rows: string[][] = [['Section', 'Line', `Amount (${ccy})`]]
    for (const s of report.sections) {
      for (const ln of s.lines) {
        rows.push([s.label, ln.label, ln.amount])
      }
      rows.push([s.label, s.subtotal_label.toUpperCase(), s.subtotal])
    }
    if (Array.isArray(report.totals)) {
      for (const t of report.totals) {
        rows.push(['', t.label.toUpperCase(), t.amount])
      }
    }
    const csv = rows.map(r => r.map(c => `"${String(c).replace(/"/g, '""')}"`).join(',')).join('\n')
    const blob = new Blob([csv], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${companyCode}-pl-${fromDate}_to_${toDate}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  const ccy = report?.currency || 'BWP'

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar />
      <main className="flex-1 px-6 py-6 space-y-6">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Button variant="ghost" size="sm" onClick={() => router.back()}>
              <ArrowLeft className="h-4 w-4 mr-1" /> Back
            </Button>
            <h1 className="text-2xl font-semibold">Per-Entity Profit & Loss</h1>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={load} disabled={loading}>
              <RefreshCw className={`h-4 w-4 mr-1 ${loading ? 'animate-spin' : ''}`} /> Refresh
            </Button>
            <Button variant="outline" size="sm" onClick={exportCsv} disabled={!report}>
              <Download className="h-4 w-4 mr-1" /> CSV
            </Button>
          </div>
        </div>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Entity &amp; period</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-4 items-end">
              <div>
                <label className="block text-xs text-muted-foreground mb-1">Entity</label>
                <select
                  value={companyCode}
                  onChange={e => setCompanyCode(e.target.value)}
                  className="border rounded px-3 py-1.5 text-sm bg-background min-w-[280px]"
                >
                  {SUPPORTED_ENTITIES.map(e => (
                    <option key={e.code} value={e.code}>
                      {e.code} — {e.name} ({e.ccy})
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs text-muted-foreground mb-1">From</label>
                <input type="date" value={fromDate} onChange={e => setFromDate(e.target.value)}
                       className="border rounded px-3 py-1.5 text-sm bg-background" />
              </div>
              <div>
                <label className="block text-xs text-muted-foreground mb-1">To</label>
                <input type="date" value={toDate} onChange={e => setToDate(e.target.value)}
                       className="border rounded px-3 py-1.5 text-sm bg-background" />
              </div>
              <Button size="sm" onClick={load} disabled={loading}>Apply</Button>
            </div>
            <div className="mt-3 text-xs text-muted-foreground">
              ADIC uses the dedicated insurance MA P&L — open <a className="underline" href="/reports/profit-loss">/reports/profit-loss</a>.
            </div>
          </CardContent>
        </Card>

        {error && (
          <Card className="border-red-300 bg-red-50/40 dark:bg-red-950/20">
            <CardContent className="py-3 flex items-center gap-2 text-sm">
              <AlertCircle className="h-4 w-4 text-red-600" /> {error}
            </CardContent>
          </Card>
        )}

        {report && report.sections && (
          <Card>
            <CardHeader>
              <CardTitle className="text-base flex items-baseline gap-3">
                <span>{report.entity_name || report.entity_code}</span>
                <span className="text-xs text-muted-foreground">
                  {report.report_title} · {report.from_date} → {report.to_date} · Currency: {ccy}
                </span>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-6">
                {report.sections.map(s => (
                  <div key={s.id} className={!s.has_activity ? 'opacity-50' : ''}>
                    <div className="text-xs uppercase tracking-wide font-semibold text-muted-foreground mb-2">
                      {s.label}{!s.has_activity && ' (no activity)'}
                    </div>
                    <table className="w-full text-sm">
                      <tbody>
                        {s.lines.map(ln => (
                          <tr key={ln.id} className="border-b last:border-0">
                            <td className="py-1 pr-4">{ln.label}</td>
                            <td className="py-1 text-right tabular-nums">{fmt(ln.amount, ccy)}</td>
                          </tr>
                        ))}
                        <tr className="border-t-2 border-foreground/40 font-semibold">
                          <td className="py-1 pr-4">{s.subtotal_label}</td>
                          <td className="py-1 text-right tabular-nums">{fmt(s.subtotal, ccy)}</td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                ))}
                {Array.isArray(report.totals) && (
                  <div className="border-t-2 border-foreground pt-3">
                    <table className="w-full text-sm">
                      <tbody>
                        {report.totals.map(t => (
                          <tr key={t.id} className="font-bold">
                            <td className="py-1 pr-4 uppercase">{t.label}</td>
                            <td className="py-1 text-right tabular-nums">{fmt(t.amount, ccy)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </CardContent>
          </Card>
        )}
      </main>
    </div>
  )
}
