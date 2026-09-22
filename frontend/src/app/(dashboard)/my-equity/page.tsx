'use client'

/**
 * /my-equity — a holder's own equity, self-service.
 * Any logged-in user; the server returns only THEIR shares/options (or a plain
 * "nothing recorded" state). Mirrors the My-Payslips pattern. Navy/Orange house
 * style. Values are indicative (valuation-model price), clearly labelled.
 */
import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { Loader2, PieChart, Download, TrendingUp, Info } from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { getMyEquity, openMyEquityStatement, getToken, type MyEquity } from '@/lib/api'

const NAVY = '#0D1B2A', ORANGE = '#F4A623'
const usd = (n: number | null | undefined) => '$' + Math.round(Number(n || 0)).toLocaleString('en-US')
const usd2 = (n: number | null | undefined) => '$' + Number(n || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const num = (n: number | null | undefined) => Math.round(Number(n || 0)).toLocaleString('en-US')

export default function MyEquityPage() {
  const router = useRouter()
  const { theme } = useTheme()
  const [d, setD] = useState<MyEquity | null>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [dl, setDl] = useState(false)

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    getMyEquity().then(setD).catch(e => setErr(e instanceof Error ? e.message : 'Failed')).finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const card = { background: theme.card, border: `1px solid ${theme.cardBdr}` }

  async function download() {
    setDl(true)
    try { await openMyEquityStatement() } catch (e) { setErr(e instanceof Error ? e.message : 'Download failed') } finally { setDl(false) }
  }

  if (loading) return <Shell theme={theme}><div className="flex items-center gap-2 p-8" style={{ color: theme.t2 }}><Loader2 className="w-5 h-5 animate-spin" /> Loading your equity…</div></Shell>
  if (err) return <Shell theme={theme}><div className="m-6 rounded-xl p-4" style={{ background: '#fef2f2', color: '#991b1b' }}>{err}</div></Shell>
  if (!d) return <Shell theme={theme}><div className="p-6" style={{ color: theme.t2 }}>No data.</div></Shell>

  if (!d.has_equity) {
    return (
      <Shell theme={theme}>
        <div className="px-5 py-5 max-w-[900px] mx-auto w-full">
          <div className="rounded-2xl p-5 mb-4" style={{ background: NAVY }}>
            <div className="flex items-center gap-2"><PieChart className="w-5 h-5" style={{ color: ORANGE }} /><h1 className="text-lg font-bold text-white">My Equity</h1></div>
          </div>
          <div className="rounded-xl p-6 text-center" style={card}>
            <Info className="w-8 h-8 mx-auto mb-3" style={{ color: theme.t2 }} />
            <p className="text-sm" style={{ color: theme.text }}>{d.detail || 'No equity is recorded against your login.'}</p>
            <p className="text-xs mt-2" style={{ color: theme.t2 }}>If you hold shares or share options, ask Finance to link your record.</p>
          </div>
        </div>
      </Shell>
    )
  }

  const t = d.totals!
  return (
    <Shell theme={theme}>
      <div className="px-5 py-5 max-w-[900px] mx-auto w-full">
        {/* header */}
        <div className="rounded-2xl p-5 mb-4 flex items-center justify-between" style={{ background: NAVY }}>
          <div>
            <div className="flex items-center gap-2"><PieChart className="w-5 h-5" style={{ color: ORANGE }} /><h1 className="text-lg font-bold text-white">My Equity</h1></div>
            <p className="text-xs mt-1" style={{ color: '#9fb0c3' }}>{d.holder} · {d.issuer?.legal_name}</p>
          </div>
          <button onClick={download} disabled={dl}
            className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-semibold"
            style={{ background: ORANGE, color: NAVY, opacity: dl ? 0.6 : 1 }}>
            {dl ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />} Statement (PDF)
          </button>
        </div>

        {/* plain explainer */}
        <div className="rounded-xl p-3 mb-4 text-xs flex gap-2" style={{ background: theme.g100, color: theme.t2 }}>
          <Info className="w-4 h-4 shrink-0 mt-0.5" style={{ color: ORANGE }} />
          <span>These are the shares and share options recorded against you under the 2021 Stock Ownership &amp; Option Plan.
          Options vest (become yours) over time while you remain employed. Values below are <b style={{ color: theme.text }}>indicative</b> —
          based on the latest valuation, not a market price or a promise of cash.</span>
        </div>

        {/* summary tiles */}
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3 mb-4">
          <Tile theme={theme} card={card} label="Share options" value={num(t.option_units)} sub={`${usd(t.option_worth_usd)} indicative`} />
          <Tile theme={theme} card={card} label="Shares held" value={num(t.shares)} sub={`${usd(t.share_worth_usd)} indicative`} />
          <Tile theme={theme} card={card} label="Indicative price / share" value={usd2(d.current_share_price_usd)} sub="valuation basis" />
        </div>

        {/* grants */}
        {d.grants!.length > 0 && (
          <div className="space-y-3">
            {d.grants!.map((g, i) => {
              const v = g.vesting
              const pct = v.has_schedule ? (v.pct_vested ?? 0) : null
              return (
                <div key={i} className="rounded-xl p-4" style={card}>
                  <div className="flex items-center justify-between flex-wrap gap-2">
                    <div className="text-sm font-bold" style={{ color: theme.text }}>{num(g.units)} options
                      <span className="ml-2 px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase"
                        style={{ background: theme.g100, color: theme.t2 }}>{g.status}</span>
                    </div>
                    <div className="text-sm font-bold" style={{ color: ORANGE }}>{usd(g.worth_usd)} <span className="text-[11px] font-normal" style={{ color: theme.t2 }}>indicative</span></div>
                  </div>
                  <div className="text-[11px] mt-0.5" style={{ color: theme.t2 }}>
                    {g.grant_date ? `Granted ${g.grant_date}` : 'Grant date on file with Finance'}
                    {g.letter_ref ? ` · ${g.letter_ref}` : ''}
                    {g.exercise_price_usd != null ? ` · exercise ${usd2(g.exercise_price_usd)}` : ''}
                  </div>

                  {/* vesting */}
                  <div className="mt-3">
                    {pct === null ? (
                      <div className="text-xs" style={{ color: theme.t2 }}>Vesting is per your Letter of Grant. Finance has not loaded a schedule yet.</div>
                    ) : (
                      <>
                        <div className="flex items-center justify-between text-[11px] mb-1" style={{ color: theme.t2 }}>
                          <span className="inline-flex items-center gap-1"><TrendingUp className="w-3 h-3" style={{ color: ORANGE }} /> {pct}% vested</span>
                          <span>{v.next_vest_date ? `Next: ${num(v.next_vest_units)} on ${v.next_vest_date}` : 'Fully vested'}</span>
                        </div>
                        <div className="h-3 rounded-full overflow-hidden" style={{ background: theme.g100 }}>
                          <div className="h-3 rounded-full" style={{ width: `${pct}%`, background: ORANGE }} />
                        </div>
                        <div className="flex justify-between text-[11px] mt-1" style={{ color: theme.t2 }}>
                          <span>Vested {num(v.vested_units)} ({usd(g.vested_worth_usd)})</span>
                          <span>Unvested {num(v.unvested_units)}</span>
                        </div>
                      </>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        )}

        {/* holdings (shareholders) */}
        {d.holdings!.length > 0 && (
          <div className="rounded-xl p-4 mt-3" style={card}>
            <h2 className="text-sm font-bold mb-2" style={{ color: ORANGE }}>Shares held</h2>
            <table className="w-full text-sm">
              <thead><tr className="text-left text-xs" style={{ color: theme.t2 }}><th className="py-1.5 pr-3">Class</th><th className="pr-3">Shares</th><th>Invested</th></tr></thead>
              <tbody>
                {d.holdings!.map((h, i) => (
                  <tr key={i} style={{ borderTop: `1px solid ${theme.cardBdr}`, color: theme.text }}>
                    <td className="py-2 pr-3 font-semibold">{h.klass}</td><td className="pr-3">{num(h.shares)}</td><td>{usd(h.usd_invested)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <p className="text-[11px] mt-4" style={{ color: theme.t2 }}>{d.price_basis}</p>
      </div>
    </Shell>
  )
}

function Shell({ children, theme }: any) {
  return <div className="min-h-screen" style={{ background: theme.bg }}><TopBar /><div>{children}</div></div>
}

function Tile({ theme, card, label, value, sub }: any) {
  return (
    <div className="rounded-xl p-4" style={card}>
      <div className="text-[11px] uppercase tracking-wide" style={{ color: theme.t2 }}>{label}</div>
      <div className="text-2xl font-bold mt-1" style={{ color: ORANGE }}>{value}</div>
      <div className="text-[11px] mt-1" style={{ color: theme.t2 }}>{sub}</div>
    </div>
  )
}
