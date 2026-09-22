'use client'

/**
 * /hris/payslips — native Payslips surface.
 *
 * Replaces the Graphiter "Payslip" iframe. Renders the latest payslip
 * + a 12-month history list with download stubs. Backend exposure
 * (payroll.api_views.my_payslips) is in flight — the page renders
 * what it has and shows a "coming soon" stripe when the endpoint
 * returns 404, rather than a broken iframe.
 */
import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { ChevronLeft, Wallet, Download, Lock, Info } from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { useHrisAccess, useHrisSelfService } from '@/hooks/useHrisAccess'
import { authedHrisFetch, fmtPula } from '../_shared'
import { saveBlob } from '@/lib/api'

interface Payslip {
  period: string         // e.g. "2026-04"
  pay_date: string       // ISO date
  gross: number
  paye: number
  net: number
  status: string         // 'paid' | 'pending' | 'draft'
  has_pdf: boolean
  pdf_url: string        // download link when the PDF exists
}

// The backend (hris my-payslips) emits gross_amount/paye_amount/net_amount +
// pdf_url + period_end. Normalise into the Payslip shape above so the surface
// renders real figures instead of P0 / "PDF not yet available". Tolerant of the
// legacy field names too so this can't silently regress if the API shifts back.
function normalisePayslip(p: Record<string, unknown>): Payslip {
  const num = (a: unknown, b: unknown) => Number((a ?? b ?? 0) as number | string) || 0
  const pdfUrl = (p.pdf_url as string) || ''
  return {
    period:   (p.period as string) || '',
    pay_date: (p.pay_date as string) || (p.period_end as string) || '',
    gross:    num(p.gross, p.gross_amount),
    paye:     num(p.paye,  p.paye_amount),
    net:      num(p.net,   p.net_amount),
    status:   (p.status as string) || 'draft',
    has_pdf:  typeof p.has_pdf === 'boolean' ? p.has_pdf : !!pdfUrl,
    pdf_url:  pdfUrl,
  }
}

// Friendly fallback so the surface looks alive while the payroll API ships.
// These are NOT real values — they render only when the backend returns 404.
const FALLBACK_HISTORY: Payslip[] = [
  { period: '2026-04', pay_date: '2026-04-25', gross: 0, paye: 0, net: 0, status: 'pending', has_pdf: false, pdf_url: '' },
  { period: '2026-03', pay_date: '2026-03-25', gross: 0, paye: 0, net: 0, status: 'pending', has_pdf: false, pdf_url: '' },
  { period: '2026-02', pay_date: '2026-02-25', gross: 0, paye: 0, net: 0, status: 'pending', has_pdf: false, pdf_url: '' },
]

function fmtPeriod(p: string): string {
  const [y, m] = p.split('-')
  if (!y || !m) return p
  return new Date(parseInt(y), parseInt(m) - 1, 1).toLocaleString('en-BW', { month: 'long', year: 'numeric' })
}

export default function HrisPayslipsPage() {
  const router = useRouter()
  const { theme } = useTheme()
  const allowed = useHrisAccess()
  // Self-service (CFO directive 2026-06-16, bug aec2f3ce): every employee may
  // view their OWN payslips — /hris/api/my-payslips/ is self-scoped to caller.
  const selfService = useHrisSelfService()
  const canView = allowed === true || selfService === true
  const accessDenied = allowed === false && selfService === false
  const [items, setItems] = useState<Payslip[]>([])
  const [loading, setLoading] = useState(true)
  const [notLive, setNotLive] = useState(false)
  const [dlErr, setDlErr] = useState<string | null>(null)

  useEffect(() => {
    if (accessDenied) router.replace('/dashboard')
  }, [accessDenied, router])

  useEffect(() => {
    if (!canView) return
    setLoading(true)
    authedHrisFetch('/hris/api/my-payslips/')
      .then(async r => {
        if (r.ok) {
          const data = await r.json()
          const raw = Array.isArray(data?.payslips) ? data.payslips : []
          setItems(raw.map(normalisePayslip))
        } else if (r.status === 404 || r.status === 405) {
          setNotLive(true)
          setItems(FALLBACK_HISTORY)
        }
      })
      .catch(() => {
        setNotLive(true)
        setItems(FALLBACK_HISTORY)
      })
      .finally(() => setLoading(false))
  }, [canView])

  if (!canView) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ backgroundColor: theme.bg }}>
        <div className="w-8 h-8 border-2 border-[#F07F00] border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  // Download the payslip PDF THROUGH the authed fetch — a bare window.open on
  // /api/v1/payslips/<id>/pdf/ carries no Authorization header, so SSO users
  // (no Django session cookie) got a 401 in a new tab (audit H11). And save via
  // a download anchor, NOT window.open(blob): the latter silently does nothing
  // inside the OmniDesktop wrapper (Kelebogile 2026-07-28) — see api.saveBlob.
  async function downloadPdf(url: string, filename = 'payslip.pdf') {
    if (!url) return
    setDlErr(null)
    try {
      const r = await authedHrisFetch(url)
      if (!r.ok) { setDlErr(`Could not download the payslip (error ${r.status}). Please try again.`); return }
      saveBlob(await r.blob(), filename)
    } catch {
      setDlErr('Could not download the payslip. Please try again, or email hr@alphadirect.co.bw.')
    }
  }

  const latest = items[0]

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar title="Payslips" breadcrumbs={[{ label: 'HRIS', href: '/hris' }, { label: 'Payslips' }]} />
      <main className="flex-1 overflow-y-auto p-6 space-y-5">
        <Link href="/hris" className="inline-flex items-center gap-1.5 text-sm font-medium" style={{ color: theme.t2 }}>
          <ChevronLeft className="w-4 h-4" /> Back to HRIS
        </Link>

        {notLive && (
          <div className="rounded-xl px-4 py-3 text-sm flex items-start gap-2"
               style={{ background: '#fef3c7', color: '#92400e', border: '1px solid #fde68a' }}>
            <Info className="w-4 h-4 flex-shrink-0 mt-0.5" />
            <div>
              Payroll API endpoint <code>/hris/api/my-payslips/</code> is being wired up. Latest payslip
              figures will populate here automatically once that ships. In the meantime,
              email <strong>hr@alphadirect.co.bw</strong> for an interim payslip copy.
            </div>
          </div>
        )}

        {/* Latest payslip hero */}
        <div className="rounded-2xl p-6"
             style={{ background: `linear-gradient(135deg, ${theme.navy}, ${theme.navy}e6)`, color: '#fff' }}>
          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="text-[11px] uppercase tracking-widest opacity-60 font-semibold">Latest payslip</div>
              <h2 className="font-display text-2xl font-bold mt-1 italic">
                {latest ? fmtPeriod(latest.period) : 'No payslip yet'}
              </h2>
              {latest && (
                <p className="text-xs opacity-70 mt-1">Pay date {latest.pay_date}</p>
              )}
            </div>
            <Wallet className="w-9 h-9 opacity-30" />
          </div>
          <div className="grid grid-cols-3 gap-4 mt-5">
            <PayslipStat label="Gross" value={latest ? fmtPula(latest.gross) : '—'} />
            <PayslipStat label="PAYE" value={latest ? fmtPula(latest.paye) : '—'} />
            <PayslipStat label="Net" value={latest ? fmtPula(latest.net) : '—'} accent />
          </div>
          <button disabled={!latest?.has_pdf}
                  onClick={() => downloadPdf(latest?.pdf_url || '', latest ? `payslip-${latest.period}.pdf` : 'payslip.pdf')}
                  className="mt-5 inline-flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-semibold disabled:opacity-40 transition-opacity"
                  style={{ background: '#ffffff22', color: '#fff', border: '1px solid #ffffff33' }}>
            {latest?.has_pdf ? <Download className="w-3.5 h-3.5" /> : <Lock className="w-3.5 h-3.5" />}
            {latest?.has_pdf ? 'Download PDF' : 'PDF not yet available'}
          </button>
          {dlErr && <p className="mt-2 text-xs" style={{ color: '#fecaca' }}>{dlErr}</p>}
        </div>

        {/* History */}
        <div className="rounded-2xl p-5"
             style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
          <h3 className="font-semibold mb-3" style={{ color: theme.text }}>12-month history</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-[11px] uppercase tracking-wider" style={{ color: theme.t2 }}>
                  <th className="py-2.5 pr-4 font-semibold">Period</th>
                  <th className="py-2.5 pr-4 font-semibold">Pay date</th>
                  <th className="py-2.5 pr-4 font-semibold text-right">Gross</th>
                  <th className="py-2.5 pr-4 font-semibold text-right">PAYE</th>
                  <th className="py-2.5 pr-4 font-semibold text-right">Net</th>
                  <th className="py-2.5 pr-4 font-semibold">Status</th>
                  <th className="py-2.5 pr-4 font-semibold text-right">PDF</th>
                </tr>
              </thead>
              <tbody>
                {loading && [0, 1, 2].map(i => (
                  <tr key={i} className="border-t" style={{ borderColor: theme.cardBdr }}>
                    {[0, 1, 2, 3, 4, 5, 6].map(j => (
                      <td key={j} className="py-3 pr-4">
                        <div className="h-3 w-20 rounded animate-pulse" style={{ background: theme.g100 }} />
                      </td>
                    ))}
                  </tr>
                ))}
                {!loading && items.length === 0 && (
                  <tr><td colSpan={7} className="py-8 text-center" style={{ color: theme.t2 }}>No payslips on file.</td></tr>
                )}
                {!loading && items.map(p => (
                  <tr key={p.period} className="border-t" style={{ borderColor: theme.cardBdr }}>
                    <td className="py-3 pr-4 font-medium" style={{ color: theme.text }}>{fmtPeriod(p.period)}</td>
                    <td className="py-3 pr-4" style={{ color: theme.t2 }}>{p.pay_date}</td>
                    <td className="py-3 pr-4 text-right tabular-nums" style={{ color: theme.text }}>{fmtPula(p.gross)}</td>
                    <td className="py-3 pr-4 text-right tabular-nums" style={{ color: theme.t2 }}>{fmtPula(p.paye)}</td>
                    <td className="py-3 pr-4 text-right tabular-nums font-semibold" style={{ color: theme.text }}>{fmtPula(p.net)}</td>
                    <td className="py-3 pr-4">
                      <span className="inline-block px-2 py-0.5 rounded text-[11px] font-medium uppercase tracking-wider"
                            style={{
                              background: p.status === 'paid' ? '#10b98119' : '#f59e0b19',
                              color: p.status === 'paid' ? '#059669' : '#b45309',
                            }}>
                        {p.status}
                      </span>
                    </td>
                    <td className="py-3 pr-4 text-right">
                      <button disabled={!p.has_pdf}
                              onClick={() => downloadPdf(p.pdf_url, `payslip-${p.period}.pdf`)}
                              className="inline-flex items-center gap-1 text-xs font-semibold disabled:opacity-40"
                              style={{ color: theme.orange }}>
                        <Download className="w-3.5 h-3.5" /> PDF
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </main>
    </div>
  )
}

function PayslipStat({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-widest opacity-60 font-semibold">{label}</div>
      <div className="font-display text-xl font-bold tabular-nums mt-1" style={{ color: accent ? '#F07F00' : '#fff' }}>
        {value}
      </div>
    </div>
  )
}
