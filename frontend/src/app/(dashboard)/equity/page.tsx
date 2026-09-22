'use client'

/**
 * /equity — Equity / Cap Table ("The Capital Story").
 * CFO/Legakwa Ntabeni 2026-06-19: build an Equity section under Accounting from
 * the Capital Story, structured per Open Cap Format (OCF). Tabs: Overview,
 * Timeline, Cap Table, ESOP, Dilution Modeller, Sources. EXCO/Finance-gated
 * (server enforces /reports/equity/ 403 for non-viewers). Read-only, off-GL.
 */
import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { Loader2, PieChart, TrendingUp, Users, Layers, Calculator, BookOpen, Landmark, Settings2 } from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { getEquityCapitalStory, getToken, type EquityCapitalStory } from '@/lib/api'
import { RegisterManager } from './RegisterManager'

const usd = (n: number) => '$' + Math.round(n).toLocaleString('en-US')
const usd2 = (n: number) => '$' + Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 4 })
const num = (n: number) => Math.round(n).toLocaleString('en-US')
const pct = (n: number) => n.toFixed(2) + '%'

const TABS = [
  ['overview', 'Overview', PieChart],
  ['timeline', 'Timeline', TrendingUp],
  ['captable', 'Cap Table', Users],
  ['esop', 'ESOP', Layers],
  ['modeller', 'Dilution Modeller', Calculator],
  ['sources', 'Sources', BookOpen],
  ['manage', 'Manage Register', Settings2],
] as const

export default function EquityPage() {
  const router = useRouter()
  const { theme } = useTheme()
  const [d, setD] = useState<EquityCapitalStory | null>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [tab, setTab] = useState<string>('overview')

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    getEquityCapitalStory().then(setD).catch(e => setErr(e instanceof Error ? e.message : 'Failed')).finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const card = { background: theme.card, border: `1px solid ${theme.cardBdr}` }
  const NAVY = '#0D1B2A', ORANGE = '#F4A623'

  if (loading) return <Shell theme={theme}><div className="flex items-center gap-2 p-8" style={{ color: theme.t2 }}><Loader2 className="w-5 h-5 animate-spin" /> Loading the Capital Story…</div></Shell>
  if (err) return <Shell theme={theme}><div className="m-6 rounded-xl p-4" style={{ background: '#fef2f2', color: '#991b1b' }}>{err}</div></Shell>
  if (!d) return <Shell theme={theme}><div className="p-6" style={{ color: theme.t2 }}>No data.</div></Shell>

  return (
    <Shell theme={theme}>
      <div className="px-5 py-5 max-w-[1180px] mx-auto w-full">
        {/* header */}
        <div className="rounded-2xl p-5 mb-4" style={{ background: NAVY }}>
          <div className="flex items-center gap-2">
            <Landmark className="w-5 h-5" style={{ color: ORANGE }} />
            <h1 className="text-lg font-bold text-white">{d.issuer.legal_name} — The Capital Story</h1>
          </div>
          <p className="text-xs mt-1" style={{ color: '#9fb0c3' }}>
            From founding to future · {d.issuer.country} · UEN {d.issuer.uen} · incorporated {d.issuer.formation_date}
            {d.ocf_aligned && <span className="ml-2 px-2 py-0.5 rounded-full text-[10px] font-semibold" style={{ background: ORANGE, color: NAVY }}>OCF-aligned</span>}
          </p>
        </div>

        {/* tabs */}
        <div className="flex items-center gap-1 flex-wrap mb-4">
          {TABS.map(([k, lbl, Icon]) => (
            <button key={k} onClick={() => setTab(k)}
              className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-semibold transition-colors"
              style={{ background: tab === k ? ORANGE : theme.g100, color: tab === k ? NAVY : theme.t2 }}>
              <Icon className="w-3.5 h-3.5" /> {lbl}
            </button>
          ))}
        </div>

        {tab === 'overview' && <Overview d={d} theme={theme} card={card} />}
        {tab === 'timeline' && <Timeline d={d} theme={theme} card={card} />}
        {tab === 'captable' && <CapTable d={d} theme={theme} card={card} />}
        {tab === 'esop' && <Esop d={d} theme={theme} card={card} />}
        {tab === 'modeller' && <Modeller d={d} theme={theme} card={card} />}
        {tab === 'sources' && <Sources d={d} theme={theme} card={card} />}
        {tab === 'manage' && <RegisterManager theme={theme} card={card} onChanged={() => getEquityCapitalStory().then(setD).catch(() => {})} />}
      </div>
    </Shell>
  )
}

function Shell({ children, theme }: any) {
  return <div className="min-h-screen" style={{ background: theme.bg }}><TopBar /><div>{children}</div></div>
}

function SectionTitle({ children }: any) {
  return <h2 className="text-sm font-bold mb-3" style={{ color: '#F4A623' }}>{children}</h2>
}

function Overview({ d, theme, card }: any) {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        {d.kpis.map((k: any) => (
          <div key={k.key} className="rounded-xl p-4" style={card}>
            <div className="text-[11px] uppercase tracking-wide" style={{ color: theme.t2 }}>{k.label}</div>
            <div className="text-2xl font-bold mt-1" style={{ color: '#F4A623' }}>{k.value}</div>
            <div className="text-[11px] mt-1" style={{ color: theme.t2 }}>{k.sub}</div>
          </div>
        ))}
      </div>
      {typeof d.security_holder_count === 'number' && (
        <div className="rounded-xl p-4 flex items-center justify-between flex-wrap gap-2" style={card}>
          <div>
            <div className="text-[11px] uppercase tracking-wide" style={{ color: theme.t2 }}>Security holders (shareholders + option holders)</div>
            <div className="text-2xl font-bold mt-0.5" style={{ color: '#F4A623' }}>{d.security_holder_count}</div>
          </div>
          <div className="text-xs max-w-md" style={{ color: theme.t2 }}>
            This is the count our cap-table provider (Carta) bills on. Tiers: 1–25 = $2,000/yr · 26–50 = $4,900/yr · 51–75 = $7,000/yr.
            Retire former holders (Manage Register) before the 31 Aug renewal to hold the tier down.
          </div>
        </div>
      )}
      <div className="rounded-xl p-4" style={card}>
        <SectionTitle>Capital Raised — By Instrument</SectionTitle>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr style={{ color: theme.t2 }} className="text-left text-xs">
              <th className="py-1.5 pr-3">Instrument</th><th className="pr-3">Cash Raised</th><th className="pr-3">Shares</th><th className="pr-3">Price/Share</th><th>Status</th>
            </tr></thead>
            <tbody>
              {d.instruments.map((i: any) => (
                <tr key={i.name} style={{ borderTop: `1px solid ${theme.cardBdr}`, color: theme.text }}>
                  <td className="py-2 pr-3 font-semibold">{i.name}<div className="text-[11px] font-normal" style={{ color: theme.t2 }}>{i.label}</div></td>
                  <td className="pr-3">{usd(i.cash_usd)}</td><td className="pr-3">{num(i.shares)}</td><td className="pr-3">{usd2(i.price)}</td><td className="text-xs">{i.status}</td>
                </tr>
              ))}
              <tr style={{ borderTop: `2px solid #F4A623`, color: theme.text }} className="font-bold">
                <td className="py-2 pr-3">Grand Total</td><td className="pr-3">{usd(d.instruments_total.cash_usd)}</td><td className="pr-3">{num(d.instruments_total.shares)}</td><td colSpan={2}></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

function Timeline({ d, theme, card }: any) {
  return (
    <div className="rounded-xl p-4" style={card}>
      <SectionTitle>The Fundraising Journey</SectionTitle>
      <div className="space-y-3">
        {d.timeline.map((t: any, i: number) => (
          <div key={i} className="flex gap-3">
            <div className="flex flex-col items-center">
              <div className="w-2.5 h-2.5 rounded-full mt-1.5" style={{ background: '#F4A623' }} />
              {i < d.timeline.length - 1 && <div className="w-px flex-1 mt-1" style={{ background: theme.cardBdr }} />}
            </div>
            <div className="pb-2">
              <div className="text-[11px] font-mono" style={{ color: theme.t2 }}>{t.date}</div>
              <div className="text-sm font-semibold" style={{ color: theme.text }}>{t.title}</div>
              <div className="text-xs mt-0.5" style={{ color: theme.t2 }}>{t.detail}</div>
              <span className="inline-block mt-1 px-2 py-0.5 rounded text-[10px] font-semibold" style={{ background: theme.g100, color: theme.text }}>{t.tag}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function CapTable({ d, theme, card }: any) {
  const max = Math.max(...d.cap_table.map((r: any) => r.pct))
  return (
    <div className="space-y-4">
      <div className="rounded-xl p-4" style={card}>
        <SectionTitle>Ownership — Fully Diluted</SectionTitle>
        <div className="space-y-1.5">
          {d.cap_table.slice(0, 10).map((r: any) => (
            <div key={r.holder} className="flex items-center gap-2 text-xs">
              <div className="w-40 truncate" style={{ color: theme.text }}>{r.holder}</div>
              <div className="flex-1 h-4 rounded" style={{ background: theme.g100 }}>
                <div className="h-4 rounded" style={{ width: `${(r.pct / max) * 100}%`, background: '#F4A623' }} />
              </div>
              <div className="w-12 text-right font-semibold" style={{ color: theme.text }}>{r.pct}%</div>
            </div>
          ))}
        </div>
      </div>
      <div className="rounded-xl p-4" style={card}>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr style={{ color: theme.t2 }} className="text-left text-xs">
              <th className="py-1.5 pr-3">Shareholder</th><th className="pr-3">Shares</th><th className="pr-3">%</th><th className="pr-3">Class</th><th className="pr-3">USD Invested</th><th>Notes</th>
            </tr></thead>
            <tbody>
              {d.cap_table.map((r: any) => (
                <tr key={r.holder} style={{ borderTop: `1px solid ${theme.cardBdr}`, color: theme.text }}>
                  <td className="py-2 pr-3 font-semibold">{r.holder}</td><td className="pr-3">{num(r.shares)}</td><td className="pr-3">{r.pct}%</td>
                  <td className="pr-3 text-xs">{r.klass}</td><td className="pr-3">{usd(r.usd)}</td><td className="text-xs" style={{ color: theme.t2 }}>{r.note}</td>
                </tr>
              ))}
              <tr style={{ borderTop: `2px solid #F4A623`, color: theme.text }} className="font-bold">
                <td className="py-2 pr-3">Total</td><td className="pr-3">{num(d.cap_table_total.shares)}</td><td className="pr-3">{d.cap_table_total.pct}%</td><td></td><td className="pr-3">{usd(d.cap_table_total.usd)}</td><td></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

function Esop({ d, theme, card }: any) {
  const e = d.esop
  const utilized = e.pct_utilized
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[['Pool Size', num(e.pool_size), `${e.pct_of_capital}% of issued capital`],
          ['Granted & Outstanding', num(e.granted), `${e.pct_utilized}% of pool utilized`],
          ['Available to Grant', num(e.available), `${(100 - e.pct_utilized).toFixed(1)}% remaining`],
          ['Option Holders', String(e.option_holders), `${e.fd_ownership_pct}% FD ownership`]].map(([l, v, s]) => (
          <div key={l} className="rounded-xl p-4" style={card}>
            <div className="text-[11px] uppercase tracking-wide" style={{ color: theme.t2 }}>{l}</div>
            <div className="text-xl font-bold mt-1" style={{ color: '#F4A623' }}>{v}</div>
            <div className="text-[11px] mt-1" style={{ color: theme.t2 }}>{s}</div>
          </div>
        ))}
      </div>
      <div className="rounded-xl p-4" style={card}>
        <SectionTitle>Pool Allocation — {utilized}% utilized</SectionTitle>
        <div className="h-5 rounded-full overflow-hidden flex" style={{ background: theme.g100 }}>
          <div style={{ width: `${utilized}%`, background: '#F4A623' }} title="Granted" />
        </div>
        <div className="flex justify-between text-[11px] mt-1" style={{ color: theme.t2 }}>
          <span>Granted {num(e.granted)}</span><span>Available {num(e.available)}</span>
        </div>
        <div className="grid md:grid-cols-2 gap-2 mt-3 text-xs">
          {e.tiers.map((t: any) => (
            <div key={t.name} className="rounded-lg p-2.5" style={{ background: theme.g100, color: theme.text }}>
              <span className="font-semibold">{t.name} — {t.pct}%</span><div style={{ color: theme.t2 }}>{t.detail}</div>
            </div>
          ))}
        </div>
      </div>
      <div className="rounded-xl p-4" style={card}>
        <SectionTitle>Plan Terms</SectionTitle>
        <div className="grid md:grid-cols-2 gap-2 text-xs">
          {e.plan_terms.map((p: any) => (
            <div key={p.k} style={{ borderTop: `1px solid ${theme.cardBdr}` }} className="py-1.5">
              <span style={{ color: theme.t2 }}>{p.k}: </span><span className="font-semibold" style={{ color: theme.text }}>{p.v}</span>
              <div style={{ color: theme.t2 }}>{p.note}</div>
            </div>
          ))}
        </div>
      </div>
      <div className="rounded-xl p-4" style={card}>
        <SectionTitle>Option Holders — Carta (as at 31 Mar 2026)</SectionTitle>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr style={{ color: theme.t2 }} className="text-left text-xs"><th className="py-1.5 pr-3">#</th><th className="pr-3">Grantee</th><th className="pr-3">Options/RSUs</th><th className="pr-3">% of Pool</th><th className="pr-3">% FD</th><th className="pr-3">Value (indic.)</th><th>Vested</th></tr></thead>
            <tbody>
              {e.grants.map((g: any, i: number) => (
                <tr key={g.grantee} style={{ borderTop: `1px solid ${theme.cardBdr}`, color: theme.text }}>
                  <td className="py-1.5 pr-3" style={{ color: theme.t2 }}>{i + 1}</td><td className="pr-3 font-semibold">{g.grantee}</td>
                  <td className="pr-3">{num(g.units)}</td><td className="pr-3">{g.pct_pool}%</td><td className="pr-3">{g.pct_fd}%</td>
                  <td className="pr-3">{g.worth_usd != null ? usd(g.worth_usd) : '—'}</td>
                  <td style={{ color: theme.t2 }}>{g.vesting ? (g.vesting.has_schedule ? `${g.vesting.pct_vested}%` : 'per letter') : '—'}</td>
                </tr>
              ))}
              <tr style={{ borderTop: `2px solid #F4A623`, color: theme.text }} className="font-bold">
                <td colSpan={2} className="py-1.5 pr-3">Total Granted</td><td className="pr-3">{num(e.granted_total.units)}</td><td className="pr-3">{e.granted_total.pct_pool}%</td><td className="pr-3">{e.granted_total.pct_fd}%</td><td colSpan={2}></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

function Modeller({ d, theme, card }: any) {
  const [pre, setPre] = useState(d.scenario.pre_money_usd)
  const [round, setRound] = useState(d.scenario.round_size_usd)
  const [esopTopup, setEsopTopup] = useState(d.scenario.esop_topup_pct)

  const m = useMemo(() => {
    const fd = d.scenario.current_fd_shares
    const price = pre / fd
    const newShares = price > 0 ? round / price : 0
    const topupShares = Math.round((esopTopup / 100) * fd)
    const postFd = fd + newShares + topupShares
    const post = pre + round
    return {
      price, newShares, topupShares, postFd, post,
      newInvestorPct: post > 0 ? (round / post) * 100 : 0,
      existingPct: postFd > 0 ? (fd / postFd) * 100 : 0,
      dilutionPct: postFd > 0 ? (1 - fd / postFd) * 100 : 0,
    }
  }, [pre, round, esopTopup, d])

  const top = d.cap_table.slice(0, 6)
  const inputCls = "w-full rounded-lg px-3 py-2 text-sm font-semibold"
  const inputSty = { background: theme.g100, color: theme.text, border: `1px solid ${theme.cardBdr}` }

  return (
    <div className="space-y-4">
      <div className="rounded-xl p-4" style={card}>
        <SectionTitle>Scenario Modeller — Next Round</SectionTitle>
        <div className="grid md:grid-cols-3 gap-3">
          <label className="text-xs" style={{ color: theme.t2 }}>Pre-money valuation (USD)
            <input type="number" className={inputCls} style={inputSty} value={pre} onChange={e => setPre(Number(e.target.value) || 0)} />
          </label>
          <label className="text-xs" style={{ color: theme.t2 }}>Round size (USD)
            <input type="number" className={inputCls} style={inputSty} value={round} onChange={e => setRound(Number(e.target.value) || 0)} />
          </label>
          <label className="text-xs" style={{ color: theme.t2 }}>ESOP top-up (% of current FD)
            <input type="number" className={inputCls} style={inputSty} value={esopTopup} onChange={e => setEsopTopup(Number(e.target.value) || 0)} />
          </label>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-4">
          {[['Price / Share', usd2(m.price)], ['New Shares Issued', num(m.newShares)], ['Post-money', usd(m.post)], ['New Investor %', pct(m.newInvestorPct)]].map(([l, v]) => (
            <div key={l} className="rounded-lg p-3" style={{ background: theme.g100 }}>
              <div className="text-[11px]" style={{ color: theme.t2 }}>{l}</div>
              <div className="text-lg font-bold" style={{ color: '#F4A623' }}>{v}</div>
            </div>
          ))}
        </div>
        <p className="text-xs mt-2" style={{ color: theme.t2 }}>
          Existing holders are diluted by <b style={{ color: theme.text }}>{pct(m.dilutionPct)}</b> (retain {pct(m.existingPct)} collectively).
          New fully-diluted shares: {num(m.postFd)}.
        </p>
      </div>
      <div className="rounded-xl p-4" style={card}>
        <SectionTitle>Top Holders — Before vs After</SectionTitle>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr style={{ color: theme.t2 }} className="text-left text-xs"><th className="py-1.5 pr-3">Holder</th><th className="pr-3">Now %</th><th className="pr-3">After %</th><th>Δ</th></tr></thead>
            <tbody>
              {top.map((r: any) => {
                const after = (r.shares / m.postFd) * 100
                return (
                  <tr key={r.holder} style={{ borderTop: `1px solid ${theme.cardBdr}`, color: theme.text }}>
                    <td className="py-1.5 pr-3 font-semibold">{r.holder}</td><td className="pr-3">{r.pct}%</td>
                    <td className="pr-3">{after.toFixed(2)}%</td>
                    <td style={{ color: '#dc2626' }}>−{(r.pct - after).toFixed(2)}%</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

function Sources({ d, theme, card }: any) {
  return (
    <div className="space-y-4">
      <div className="rounded-xl p-4" style={card}>
        <SectionTitle>Odoo Equity Reconciliation</SectionTitle>
        <p className="text-sm font-semibold" style={{ color: theme.text }}>{d.odoo_recon.equity_account}</p>
        <p className="text-xs mt-1" style={{ color: theme.t2 }}>{d.odoo_recon.note}</p>
      </div>
      <div className="rounded-xl p-4" style={card}>
        <SectionTitle>Sources</SectionTitle>
        <ul className="text-xs space-y-1" style={{ color: theme.t2 }}>
          {d.sources.map((s: string, i: number) => <li key={i}>• {s}</li>)}
        </ul>
      </div>
    </div>
  )
}
