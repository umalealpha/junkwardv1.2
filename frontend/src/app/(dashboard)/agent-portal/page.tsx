'use client'

/*
 * Agent Portal — UNICOIN sales commissions.
 * Look + tab structure deliberately mirror Motlatsi Molefe's commission_reports.html
 * tool (UNICOIN SOP), per CFO requirement 2026-07-06: 7 tabs, legacy AD palette
 * (#0D1B2A ink / #F4A623 accent, Space Grotesk / Inter / IBM Plex Mono). Data is
 * DB-backed via the agent_portal API (not the tool's localStorage).
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  getToken, apiFetchBinary,
  agentPortalStreams, agentPortalCycles, agentPortalCreateCycle, agentPortalIngest,
  agentPortalPayrun, agentPortalPayout, agentPortalAgents, agentPortalLines,
  agentPortalGetBank, agentPortalSaveBank, agentPortalBankAccounts, agentPortalBankUnlock, agentPortalBankLock, agentPortalSubmissions,
  agentPortalSourceReports, agentPortalAddSourceReport, agentPortalStatusCheck,
  agentPortalAccess, agentPortalMyProfile, agentPortalVerifyGraphite,
  agentPortalPayslips, agentPortalBuildPayslips, agentPortalPayslipPdf, agentPortalEmailPayslip,
  agentPortalApprove, agentPortalReopen, agentPortalAgentReviewXlsx,
  type AgentPortalPayslip,
  type AgentPortalStream, type AgentPortalCycle, type AgentPortalPayrun,
  type AgentPortalPayout, type AgentPortalAgent, type AgentPortalBank, type AgentPortalLine,
  type AgentPortalBankAccounts, type AgentPortalSubmission, type AgentPortalIngestExtras,
  type AgentPortalSourceReport, type AgentPortalStatusCheck,
  type AgentPortalAccess, type AgentPortalMyProfile,
} from '@/lib/api'
import { AGENT_PROFILE, STREAM_COLS } from './unicoinRef'
import { localYmd } from '@/lib/utils'

const TABS = ['Dashboard', 'Daily reports', 'Agents', 'Pay run', 'Reports', 'Payout', 'Payslips', 'Banking', 'Master payable'] as const
type Tab = typeof TABS[number]

/* UniCoin mark — navy circle + four 'expand' arrows (NW/SE orange, NE/SW white),
   recreated from the CFO's logo (2026-07-27). Self-contained inline SVG. */
function UnicoinMark({ size = 30 }: { size?: number }) {
  // Four outward 'expand' arrows to the corners. TL + BR orange, TR + BL white.
  return (
    <svg width={size} height={size} viewBox="0 0 100 100" aria-label="UniCoin" role="img">
      <circle cx="50" cy="50" r="48" fill="#1B2A6B" />
      {/* shafts */}
      <g stroke="#ffffff" strokeWidth="8" strokeLinecap="round">
        <line x1="50" y1="50" x2="78" y2="22" /><line x1="50" y1="50" x2="22" y2="78" />
      </g>
      <g stroke="#F26A21" strokeWidth="8" strokeLinecap="round">
        <line x1="50" y1="50" x2="22" y2="22" /><line x1="50" y1="50" x2="78" y2="78" />
      </g>
      {/* arrowheads */}
      <g fill="#ffffff">
        <path d="M78 22 L62 22 L78 38 Z" /><path d="M22 78 L38 78 L22 62 Z" />
      </g>
      <g fill="#F26A21">
        <path d="M22 22 L38 22 L22 38 Z" /><path d="M78 78 L62 78 L78 62 Z" />
      </g>
    </svg>
  )
}
function UnicoinBrand({ tag }: { tag: string }) {
  return (
    <div className="brand">
      <UnicoinMark size={30} />
      <div className="brandtext">
        <span className="k"><b style={{ color: '#eaf0f6' }}>Uni</b><b style={{ color: 'var(--accent)' }}>Coin</b></span>
        <span className="s">{tag}</span>
      </div>
    </div>
  )
}

const money = (v: string | number) =>
  'BWP ' + Number(v || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

export default function AgentPortalPage() {
  const router = useRouter()
  const [tab, setTab] = useState<Tab>('Dashboard')
  const [streams, setStreams] = useState<AgentPortalStream[]>([])
  const [cycles, setCycles] = useState<AgentPortalCycle[]>([])
  const [cycleId, setCycleId] = useState('')
  const [payrun, setPayrun] = useState<AgentPortalPayrun | null>(null)
  const [payout, setPayout] = useState<AgentPortalPayout | null>(null)
  const [agents, setAgents] = useState<AgentPortalAgent[]>([])
  const [rejLines, setRejLines] = useState<AgentPortalLine[]>([])
  const [msg, setMsg] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [showNew, setShowNew] = useState(false)
  const [access, setAccess] = useState<AgentPortalAccess | null>(null)
  const [accessReady, setAccessReady] = useState(false)

  const cycle = cycles.find(c => c.id === cycleId) || null

  const loadCore = useCallback(async () => {
    try {
      const [st, cy, ag] = await Promise.all([agentPortalStreams(), agentPortalCycles(), agentPortalAgents()])
      // Newest-created first: when two cycles share the same dates (e.g. a real
      // load next to a leftover empty test cycle), the freshly-loaded one must
      // win the default pick, not the stray. (Bharath landed on an empty
      // same-date cycle 2026-07-27.)
      const ordered = [...cy.results].sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''))
      setStreams(st); setCycles(ordered); setAgents(ag.results)
      setCycleId(prev => prev || (ordered[0]?.id ?? ''))
    } catch (e) { setErr(e instanceof Error ? e.message : 'Failed to load') }
  }, [])

  const loadCycle = useCallback(async (id: string) => {
    if (!id) return
    try {
      const [pr, po, rej] = await Promise.all([
        agentPortalPayrun(id), agentPortalPayout(id), agentPortalLines(id, undefined, false),
      ])
      setPayrun(pr); setPayout(po); setRejLines(rej)
    } catch (e) { setErr(e instanceof Error ? e.message : 'Failed to load cycle') }
  }, [])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    // Access first: managers get the full portal; everyone else is confined to
    // their own profile (CFO 2026-07-07). Only load the management data when
    // the caller is a manager, so a non-manager never even fetches it.
    agentPortalAccess()
      .then(a => { setAccess(a); if (a.is_manager) loadCore() })
      .catch(() => setAccess({ email: '', name: '', is_manager: false }))
      .finally(() => setAccessReady(true))
  }, [loadCore, router])
  useEffect(() => { if (access?.is_manager && cycleId) loadCycle(cycleId) }, [access, cycleId, loadCycle])

  const flash = (m: string) => { setMsg(m); setTimeout(() => setMsg(null), 2400) }
  const streamName = (k: string) => streams.find(s => s.key === k)?.name || k

  const cycleApproved = cycle?.status === 'approved' || cycle?.status === 'paid'
  const hasPayable = Number(payrun?.total_payable_bwp || 0) > 0
  const doApprove = async () => {
    if (!cycleId) return
    setBusy(true); setErr(null)
    try { await agentPortalApprove(cycleId); flash('Pay cycle approved — payslips can now be emailed'); await loadCore() }
    catch (e) { setErr(e instanceof Error ? e.message : 'Approve failed') } finally { setBusy(false) }
  }
  const doReopen = async () => {
    if (!cycleId) return
    setBusy(true); setErr(null)
    try { await agentPortalReopen(cycleId); flash('Pay cycle reopened for corrections'); await loadCore() }
    catch (e) { setErr(e instanceof Error ? e.message : 'Reopen failed') } finally { setBusy(false) }
  }

  if (!accessReady) {
    return <div className="ap-root"><style>{CSS}</style><div className="ap-main"><p className="lead">Loading…</p></div></div>
  }
  if (access && !access.is_manager) {
    return <MyProfileOnly name={access.name} />
  }

  return (
    <div className="ap-root">
      <style>{CSS}</style>

      <header className="ap-top">
        <UnicoinBrand tag="Instant Insurance · Agent commissions" />
        <div className="tabs">
          {TABS.map(t => (
            <button key={t} className={`tab${tab === t ? ' active' : ''}`} onClick={() => setTab(t)}>{t}</button>
          ))}
        </div>
        <div className="cycle">
          <div>Cycle window</div>
          <select value={cycleId} onChange={e => setCycleId(e.target.value)}>
            {!cycles.length && <option value="">No cycle yet</option>}
            {cycles.map(c => <option key={c.id} value={c.id}>{c.label}</option>)}
          </select>
          <button className="ncbtn" onClick={() => setShowNew(v => !v)}>+ New cycle</button>
        </div>
        <div className="tape">
          <div className="lbl">Grand total payable</div>
          <div className="val num">{money(payrun?.total_payable_bwp || 0)}</div>
        </div>
        {cycle && (
          <div className="tape">
            <div className="lbl">Pay-run sign-off</div>
            {cycleApproved ? (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontWeight: 700, fontSize: 14, color: '#59e39b' }}>Approved ✓</span>
                <button className="ncbtn" disabled={busy} onClick={doReopen}>Reopen</button>
              </div>
            ) : (
              <button className="ncbtn" disabled={busy || !hasPayable}
                      title={hasPayable ? 'Sign off this pay run' : 'Nothing payable to approve'}
                      onClick={doApprove}>Approve pay cycle</button>
            )}
          </div>
        )}
      </header>

      <main className="ap-main">
        {err && <div className="errbox" onClick={() => setErr(null)}>{err} — dismiss</div>}
        {msg && <div className="okbox">{msg}</div>}

        {(showNew || (!cycle && tab === 'Pay run')) && (
          <NewCyclePrompt onCreated={async () => { setShowNew(false); await loadCore(); flash('Cycle created') }} setErr={setErr} />
        )}

        {tab === 'Dashboard' && <Dashboard payrun={payrun} rejLines={rejLines} streamName={streamName} cycle={cycle} cycles={cycles} onPickCycle={setCycleId} setErr={setErr} />}
        {tab === 'Daily reports' && (
          <Daily cycle={cycle} streams={streams} busy={busy}
                 onIngest={async (stream, text, extras) => {
                   if (!cycle) { setErr('Create/select a cycle first.'); return }
                   setBusy(true); setErr(null)
                   try { const r = await agentPortalIngest(cycle.id, stream, text, extras); flash('Saved ✓ ' + JSON.stringify(r).slice(0, 180)); await loadCycle(cycle.id) }
                   catch (e) { setErr(e instanceof Error ? e.message : 'Ingest failed') }
                   finally { setBusy(false) }
                 }} payrun={payrun} streamName={streamName} />
        )}
        {tab === 'Agents' && <Agents cycleId={cycleId} agents={agents} streamName={streamName} />}
        {tab === 'Pay run' && <Payrun cycleId={cycleId} payrun={payrun} streamName={streamName} />}
        {tab === 'Reports' && <Reports cycle={cycle} flash={flash} setErr={setErr} streamName={streamName} />}
        {tab === 'Payout' && <Payout cycle={cycle} payout={payout} flash={flash} setErr={setErr} />}
        {tab === 'Payslips' && <Payslips cycle={cycle} cycleId={cycleId} flash={flash} setErr={setErr} reload={loadCore} />}
        {tab === 'Banking' && <Banking agents={agents} reload={loadCore} flash={flash} setErr={setErr} />}
        {tab === 'Master payable' && <Master payrun={payrun} streamName={streamName} />}

        <div className="disclaim">
          UniCoin Instant Insurance agent commissions. Applies the UniCoin SOP §4 rules to what is ingested.
          The SOP cross-checks (All-Policy list, paygate exports, never-pay-twice) and the CFO sign-off remain
          finance controls. Pay by the 28th, before 9:00.
        </div>
      </main>
    </div>
  )
}

/* ─────────────── My Profile (non-managers only) ─────────────── */
function MyProfileOnly({ name }: { name: string }) {
  const [data, setData] = useState<AgentPortalMyProfile | null>(null)
  const [loaded, setLoaded] = useState(false)
  useEffect(() => { agentPortalMyProfile().then(setData).catch(() => setData(null)).finally(() => setLoaded(true)) }, [])
  return (
    <div className="ap-root">
      <style>{CSS}</style>
      <header className="ap-top">
        <UnicoinBrand tag="Instant Insurance · My agent profile" />
        <div className="tape"><div className="lbl">Signed in as</div><div className="val">{name || 'you'}</div></div>
      </header>
      <main className="ap-main">
        <div className="crumbs">Agent Portal</div><h1>My profile</h1>
        {!loaded ? <p className="lead">Loading…</p> : !data?.matched ? (
          <div className="card"><div className="bd">
            <p className="lead">This is your personal Agent Portal view. The management screens are limited to authorised managers.</p>
            <p className="lead">No commission profile is linked to your sign-in yet. If you are a sales agent and expect to see your figures here, ask the Agent Portal managers to link your name.</p>
          </div></div>
        ) : (
          <>
            <div className="metrics">
              <div className="metric hero"><div className="l">Cycle</div><div className="v" style={{ fontSize: 18 }}>{data.cycle}</div></div>
              <div className="metric"><div className="l">Approved</div><div className="v">{data.summary.approved ?? 0}</div></div>
              <div className="metric"><div className="l">Rejected</div><div className="v">{data.summary.rejected ?? 0}</div></div>
              <div className="metric"><div className="l">Payable</div><div className="v" style={{ fontSize: 18 }}>{money(data.summary.payable_bwp || 0)}</div></div>
            </div>
            <div className="card"><div className="hd"><h3>My commission lines</h3><span className="meta">{data.agent}</span></div>
              <table><thead><tr><th>Policy</th><th>Stream</th><th className="r">Basis</th><th>Status</th><th>Reason</th><th className="r">Commission</th></tr></thead>
                <tbody>{data.lines.length ? data.lines.map(l => (
                  <tr key={l.id} className={l.payable ? '' : 'is-fail'}>
                    <td className="num">{l.policy_ref || '—'}</td><td className="reason">{l.stream}</td>
                    <td className="money r">{Number(l.basis) ? money(l.basis) : '—'}</td>
                    <td><span className={`pill ${l.payable ? 'pay' : 'no'}`}>{l.payable ? 'PAID' : 'NOT PAID'}</span></td>
                    <td className="reason">{l.reason}</td><td className="money r">{money(l.commission)}</td>
                  </tr>
                )) : <tr><td colSpan={6} className="empty">No lines in this cycle.</td></tr>}</tbody></table></div>
          </>
        )}
      </main>
    </div>
  )
}

/* ─────────────── Dashboard ─────────────── */
function Dashboard({ payrun, rejLines, streamName, cycle, cycles, onPickCycle, setErr }: {
  payrun: AgentPortalPayrun | null; rejLines: AgentPortalLine[]; streamName: (k: string) => string
  cycle: AgentPortalCycle | null; cycles: AgentPortalCycle[]; onPickCycle: (id: string) => void
  setErr: (m: string) => void
}) {
  const streams = payrun ? Object.entries(payrun.streams).map(([k, v]) => ({ key: k, ...v })) : []
  const activeStreams = streams.filter(st => st.approved || st.rejected).sort((a, b) => Number(b.payable_bwp) - Number(a.payable_bwp))
  const totalPay = Number(payrun?.total_payable_bwp || 0)
  const itemsReported = streams.reduce((n, st) => n + st.approved + st.rejected, 0)
  const notPayable = rejLines.length
  const agents = payrun?.agents || []
  const pickDate = (which: 'from' | 'to', v: string) => {
    if (!v) return
    const hit = cycles.find(c => (which === 'from' ? c.start_date === v : c.end_date === v)) ||
      cycles.find(c => c.start_date <= v && v <= c.end_date)
    if (hit) onPickCycle(hit.id)
    else setErr('No cycle covers that date — create one with + New cycle (top right).')
  }

  return (
    <>
      <div className="crumbs">Overview</div><h1>Dashboard</h1>
      <p className="lead">A summary of everything agents have reported in this tool between the two dates below. Pick the dates for the cycle you want to see — the totals update as reports are added.</p>
      <div className="filters">
        <div className="fld"><label>From</label><input type="date" value={cycle?.start_date || ''} onChange={e => pickDate('from', e.target.value)} /></div>
        <div className="fld"><label>To</label><input type="date" value={cycle?.end_date || ''} onChange={e => pickDate('to', e.target.value)} /></div>
      </div>
      <div className="metrics">
        <div className="metric hero"><div className="l">Total to pay</div><div className="v">{money(totalPay)}</div></div>
        <div className="metric info"><div className="l">Agents to pay</div><div className="v">{agents.length}</div></div>
        <div className="metric ok"><div className="l">Items reported</div><div className="v">{itemsReported}</div></div>
      </div>

      <div className="card"><div className="hd"><h3>By stream</h3><span className="meta">where the money is</span></div>
        <table><thead><tr><th>Stream</th><th className="r">Items paid</th><th>Share</th><th className="r">Amount</th></tr></thead>
          <tbody>{activeStreams.length ? activeStreams.map(st => (
            <tr key={st.key}>
              <td><span className="dot" />{streamName(st.key)}</td>
              <td className="r num">{st.approved}</td>
              <td style={{ width: '30%' }}><span className="sharetrack"><span className="sharebar" style={{ width: `${totalPay ? (Number(st.payable_bwp) / totalPay * 100).toFixed(1) : 0}%` }} /></span></td>
              <td className="money r">{money(st.payable_bwp)}</td>
            </tr>
          )) : <tr><td colSpan={4} className="empty">Nothing ingested yet — add reports on the Daily reports tab.</td></tr>}</tbody>
          <tfoot><tr><td>Total</td><td /><td /><td className="money r">{money(totalPay)}</td></tr></tfoot></table></div>

      <div className="card"><div className="hd"><h3>By agent</h3><span className="meta">{agents.length} agent(s) to pay</span></div>
        <table><thead><tr><th>Agent</th><th className="r">Items paid</th><th className="r">Commission</th></tr></thead>
          <tbody>{agents.length ? agents.map(a => <tr key={a.agent}><td className="agentcell">{a.agent}</td><td className="r num">{a.items ?? '—'}</td><td className="money r">{money(a.payable_bwp)}</td></tr>)
            : <tr><td colSpan={3} className="empty">No agents to pay yet.</td></tr>}</tbody>
          <tfoot><tr><td>Total</td><td /><td className="money r">{money(totalPay)}</td></tr></tfoot></table></div>

      {notPayable > 0 && (
        <p className="hint" style={{ textAlign: 'center' }}>{notPayable} reported item(s) are not payable yet — see the reason for each on the <b>Pay run</b> tab.</p>
      )}
    </>
  )
}

/* ─────────────── Daily reports ─────────────── */
const STREAM_RULES: Record<string, string> = {
  new_sales_mis: '<b>MIS book.</b> 1st month = 100% of premium. 2nd month = 100% if RealPay, 50% if DPO. Pays only when <b>Activated</b>, <b>KYC Approved</b>, and the client has paid — a <b>SUCCESS paygate with Paid Amount ≥ premium</b> in the cycle.',
  new_sales_liberty: '<b>Liberty (UNI) book.</b> 1st month = 100% of premium. 2nd month = 100% if RealPay, 50% if DPO. Pays only when <b>Activated</b>, <b>KYC Approved</b>, and the client has paid — a <b>SUCCESS paygate with Paid Amount ≥ premium</b> in the cycle.',
  conversion_mis: '<b>MIS book — 70% of premium.</b> Pays only when <b>Activated</b>, <b>KYC Approved</b>, the client has paid (<b>SUCCESS paygate, Paid ≥ premium</b>), and the policy <b>started 3+ months before the cycle end</b>.',
  conversion_liberty: '<b>Liberty book — 70% of premium.</b> Pays only when <b>Activated</b>, <b>KYC Approved</b>, the client has paid (<b>SUCCESS paygate, Paid ≥ premium</b>), and the policy <b>started 3+ months before the cycle end</b>.',
  collection: '<b>20% of the amount collected.</b> Pays only when the <b>paygate collected amount EQUALS the claim</b> (±0.01), <b>KYC Approved</b>, and <b>Activated</b>.',
  motor: '<b>Flat BWP 20</b> per policy. Pays only when <b>Activated</b> and the client has paid (<b>SUCCESS paygate</b>).',
  kyc_claims: '<b>BWP 20 per confirmed item.</b> Pays only when <b>Confirmed = Yes</b> and <b>Withheld = No</b>. One row per item.',
  bank_confirmation: '<b>Bank confirmation collections — 20% of the amount collected.</b> Pays only when the <b>paygate collected amount EQUALS the claim</b> (±0.01), <b>KYC Approved</b>, and <b>Activated</b>.',
  hospital: 'Commission = <b>Expected Total − Base Premium</b> (the dependant premium added). Pays only when <b>Activated</b> and <b>Reconciliation = Pay</b>.',
  incentives: 'The <b>approved amount</b> for the operation on an approved memo. Office-handling agents — <b>no policy/paygate check.</b> Lines with no amount are not paid.',
}

const SHEET_FOR: Record<string, string> = {
  new_sales_mis: 'New Sales', new_sales_liberty: 'New Sales',
  conversion_mis: 'RealPay Conversion', conversion_liberty: 'RealPay Conversion',
  collection: 'Collection', motor: 'Motor Comprehensive', kyc_claims: 'KYC Claims',
  bank_confirmation: 'Bank Confirmation', hospital: 'Hospital Cashback', incentives: 'Proposed Incentives',
}

function fieldSpec(name: string): { t: 'select' | 'date' | 'number' | 'text'; o?: string[] } {
  const n = name.toLowerCase()
  if (/kyc status/.test(n)) return { t: 'select', o: ['Approved', 'Pending', 'Non-Compliant'] }
  if (/policy status/.test(n)) return { t: 'select', o: ['Activated', 'Cancelled', 'Deactivated'] }
  if (/paygate status/.test(n)) return { t: 'select', o: ['SUCCESS', 'FAILED', 'None'] }
  if (/payment source/.test(n)) return { t: 'select', o: ['RealPay', 'DPO'] }
  if (/commission month/.test(n)) return { t: 'select', o: ['1st', '2nd'] }
  if (/^book$/.test(n)) return { t: 'select', o: ['MIS', 'Liberty'] }
  if (/confirmed|withheld/.test(n)) return { t: 'select', o: ['Yes', 'No'] }
  if (/reconcil/.test(n)) return { t: 'select', o: ['Pay', 'Do Not Pay'] }
  if (/date/.test(n)) return { t: 'date' }
  if (/premium|amount|total|collected|payment \(/.test(n)) return { t: 'number' }
  return { t: 'text' }
}

/* ≈ preview only — the SERVER recomputes and is the authority at ingest. */
const _low = (v: string) => (v || '').trim().toLowerCase()
const _cancelled = (v: string) => /deact|cancel|inactive|lapse/.test(_low(v))
const _kycOk = (v: string) => /appro|compl/.test(_low(v)) && !/not|pend|non|reject/.test(_low(v))
const _pgOk = (v: string) => /succ|paid/.test(_low(v)) && !/unpaid|fail/.test(_low(v))
function previewRow(key: string, cols: string[], vals: string[]): string {
  const at = (frag: string) => vals[cols.findIndex(c => c.toLowerCase().includes(frag))] || ''
  if (key === 'incentives') return Number(at('amount')) > 0 ? '≈ payable — approved amount' : '≈ not payable — no approved amount'
  if (key === 'kyc_claims') {
    if (!/^(y|yes|true|1)/.test(_low(at('confirmed')))) return '≈ not payable — item not confirmed'
    if (/^(y|yes|true|1)/.test(_low(at('withheld')))) return '≈ not payable — item withheld'
    return '≈ payable — P20'
  }
  if (_cancelled(at('policy status'))) return '≈ not payable — not Activated'
  if (cols.some(c => /kyc status/i.test(c)) && !_kycOk(at('kyc status'))) return '≈ not payable — KYC not approved'
  if (key === 'hospital') return /^pay/.test(_low(at('reconcil'))) ? '≈ payable — dependant premium' : "≈ not payable — Reconciliation ≠ 'Pay'"
  if (!_pgOk(at('paygate status'))) return '≈ not payable — no SUCCESS paygate'
  return '≈ payable (server confirms on save)'
}

function Daily({ cycle, streams, onIngest, busy, payrun, streamName }: {
  cycle: AgentPortalCycle | null; streams: AgentPortalStream[]; busy: boolean
  onIngest: (stream: string, text: string, extras?: AgentPortalIngestExtras) => void
  payrun: AgentPortalPayrun | null; streamName: (k: string) => string
}) {
  const [stream, setStream] = useState('new_sales_mis')
  const [text, setText] = useState('')
  const [agentName, setAgentName] = useState('')
  const [repDate, setRepDate] = useState(() => localYmd(new Date()))
  const [showHand, setShowHand] = useState(false)
  const [hand, setHand] = useState<Record<number, string>>({})
  const [upMsg, setUpMsg] = useState('')
  const [srcUsed, setSrcUsed] = useState<'paste' | 'sheet' | 'hand'>('paste')
  const [subs, setSubs] = useState<AgentPortalSubmission[]>([])
  useEffect(() => {
    if (!cycle) { setSubs([]); return }
    agentPortalSubmissions(cycle.id).then(setSubs).catch(() => setSubs([]))
  }, [cycle, payrun])
  const ingested = payrun ? Object.entries(payrun.streams).map(([k, v]) => ({ key: k, ...v })) : []
  const sel = streams.find(st => st.key === stream)
  const cols = STREAM_COLS[stream] || []

  const junk = (r: string[]) => {
    const f = (r[0] || '').trim()
    return f === '' || /^\[/.test(f) || /^agent name$/i.test(f) || /^agent$/i.test(f) || /unicoin/i.test(f) || /^how we confirm/i.test(f) || /^pay:/i.test(f)
  }
  const bookFilter = (rows: string[][]) => {
    if (stream === 'new_sales_mis') return rows.filter(r => !(/liberty/i.test(r[4] || '') || /^uni/i.test(r[1] || '')))
    if (stream === 'new_sales_liberty') return rows.filter(r => /liberty/i.test(r[4] || '') || /^uni/i.test(r[1] || ''))
    return rows
  }
  const loadSheet = async (f: File | null) => {
    if (!f) { setUpMsg('Choose a file first.'); return }
    setUpMsg('Reading ' + f.name + '…')
    try {
      const XLSX = await import('xlsx')
      const wb = XLSX.read(new Uint8Array(await f.arrayBuffer()), { type: 'array' })
      const want = (SHEET_FOR[stream] || '').toLowerCase()
      const sheet = wb.SheetNames.find(n => n.toLowerCase() === want) ||
        wb.SheetNames.find(n => n.toLowerCase().includes(want.split(' ')[0])) || wb.SheetNames[0]
      const arr = XLSX.utils.sheet_to_json(wb.Sheets[sheet], { header: 1, raw: false, defval: '' }) as string[][]
      const rows = bookFilter(arr.map(r => r.map(c => String(c ?? '').trim())).filter(r => !junk(r)))
      setText(rows.map(r => r.join('\t')).join('\n'))
      setSrcUsed('sheet')
      setUpMsg(rows.length ? `Loaded ${rows.length} row(s) below — check them, then Save report.` : 'No data rows found for this stream in that file.')
    } catch { setUpMsg('Could not read that spreadsheet.') }
  }
  const handVals = cols.map((c, i) => (/agent name/i.test(c) ? agentName : (hand[i] || '')))
  const addHandRow = () => {
    if (!agentName.trim()) { setUpMsg('Enter your agent name first (top of the page).'); return }
    setText(t => (t ? t + '\n' : '') + handVals.join('\t'))
    setSrcUsed('hand')
    setHand({})
  }

  return (
    <>
      <div className="crumbs">Agent reporting</div><h1>Add your daily report</h1>
      <p className="lead">Pick what you worked on, then fill in the row — the columns are exactly the ones in the submission template. It saves here and flows into the pay run, dashboard and payout.</p>

      <div className="card"><div className="bd">
        <div className="frm" style={{ gridTemplateColumns: '2fr 1fr' }}>
          <div className="fld"><label>Your name</label>
            <input list="apAgentNames" value={agentName} onChange={e => setAgentName(e.target.value)} placeholder="Start typing your name…" autoComplete="off" />
            <datalist id="apAgentNames">{Object.keys(AGENT_PROFILE).sort().map(n => <option key={n} value={n} />)}</datalist>
          </div>
          <div className="fld"><label>Date</label><input type="date" value={repDate} onChange={e => setRepDate(e.target.value)} /></div>
        </div>
        <div className="fld" style={{ marginTop: 10 }}><label>What did you work on?</label></div>
        <div className="scards">
          {streams.map(st => (
            <button key={st.key} className={`scard${stream === st.key ? ' sel' : ''}`} onClick={() => { setStream(st.key); setHand({}) }}>
              <span className="n">{st.name}</span>
              <span className="t">{st.tag}</span>
            </button>
          ))}
        </div>
        {sel && STREAM_RULES[sel.key] && <div className="rule" dangerouslySetInnerHTML={{ __html: STREAM_RULES[sel.key] }} />}

        <div className="upbox">
          <div className="upttl">Upload your {sel ? sel.name : ''} sheet</div>
          <div className="hint" style={{ marginTop: 0 }}>Fill the <b>{SHEET_FOR[stream] || sel?.name}</b> tab of the submission template and attach it here — every row loads in at once, following that format. Or paste the rows below.</div>
          <div className="actions" style={{ marginTop: 8 }}>
            <input type="file" accept=".xlsx,.xls,.csv" id="apSheet" onChange={e => loadSheet(e.target.files?.[0] || null)} />
          </div>
          {upMsg && <div className="hint">{upMsg}</div>}
        </div>

        <details className="hand" open={showHand} onToggle={e => setShowHand((e.target as HTMLDetailsElement).open)}>
          <summary>Or add a single row by hand</summary>
          <div className="egrid" style={{ marginTop: 10 }}>
            {cols.map((c, i) => {
              if (/agent name/i.test(c)) return null
              const spec = fieldSpec(c)
              return (
                <div className="fld" key={c + i}>
                  <label>{c}</label>
                  {spec.t === 'select'
                    ? <select value={hand[i] || ''} onChange={e => setHand({ ...hand, [i]: e.target.value })}><option value="" />{spec.o!.map(o => <option key={o}>{o}</option>)}</select>
                    : <input type={spec.t === 'date' ? 'date' : spec.t === 'number' ? 'number' : 'text'} value={hand[i] || ''} onChange={e => setHand({ ...hand, [i]: e.target.value })} />}
                </div>
              )
            })}
          </div>
          <div className="actions">
            <button className="btn primary" onClick={addHandRow}>+ Add to today&apos;s report</button>
            <span className="reason">{previewRow(stream, cols, handVals)}</span>
          </div>
          <div className="hint">Fill the row like the template. Your name is taken from the top — you can leave columns you don&apos;t have blank.</div>
        </details>

        <textarea value={text} onChange={e => setText(e.target.value)}
          placeholder={`Loaded rows appear here — or paste your full ${sel ? sel.name : ''} report directly (one row per policy / item, columns in the template order). Header / title / guidance rows are detected and skipped.`} />
        <div className="actions">
          <button className="btn primary" disabled={busy || !cycle || !text.trim()}
            onClick={() => { onIngest(stream, text, { agent_name: agentName, report_date: repDate, source: srcUsed }); setText(''); setSrcUsed('paste') }}>
            {busy ? 'Saving…' : 'Save report'}</button>
          <span className="hint" style={{ margin: 0 }}>{cycle ? `Cycle: ${cycle.label}` : 'Create a cycle first (+ New cycle, top right)'}</span>
        </div>
      </div></div>

      <div className="card"><div className="hd"><h3>Import from policy report</h3><span className="meta">New Sales (MIS &amp; Liberty) + Motor, from the all-policies export</span></div><div className="bd">
        <p className="hint" style={{ marginTop: 0 }}>Paste the <b>all-policies CSV</b> below and it builds New Sales &amp; Motor entries automatically. Re-importing replaces the previous import for those streams.</p>
        <AllPoliciesImport cycle={cycle} busy={busy} onIngest={onIngest} />
      </div></div>

      <div className="card"><div className="hd"><h3>Ingested this cycle</h3><span className="meta">{ingested.length} stream(s)</span></div>
        <table><thead><tr><th>Stream</th><th className="r">Approved</th><th className="r">Rejected</th><th className="r">Payable</th></tr></thead>
          <tbody>{ingested.length ? ingested.map(st => (
            <tr key={st.key}><td>{streamName(st.key)}</td><td className="r num">{st.approved}</td><td className="r num">{st.rejected}</td><td className="money r">{money(st.payable_bwp)}</td></tr>
          )) : <tr><td colSpan={4} className="empty">Nothing ingested for this cycle yet.</td></tr>}</tbody></table></div>

      <div className="card"><div className="hd"><h3>Submitted reports</h3><span className="meta">{subs.length ? `${subs.length} report(s) on record` : 'every upload is kept on record'}</span></div>
        <table><thead><tr><th>When</th><th>Agent</th><th>Stream</th><th>How</th><th className="r">Lines</th><th className="r">Earns</th><th>By</th></tr></thead>
          <tbody>{subs.length ? subs.slice(0, 100).map(sb => (
            <tr key={sb.id}>
              <td className="num" style={{ whiteSpace: 'nowrap' }}>{(sb.report_date || sb.created_at || '').slice(0, 10)}</td>
              <td className="agentcell">{sb.agent_name || '—'}</td>
              <td><span className="pill mod">{streamName(sb.stream)}</span></td>
              <td className="reason">{sb.source === 'sheet' ? 'Sheet upload' : sb.source === 'hand' ? 'By hand' : sb.source === 'all_policies' ? 'Policy import' : 'Pasted'}</td>
              <td className="r num">{sb.rows_count}</td>
              <td className="money r">{money(sb.payable_bwp)}</td>
              <td className="reason">{sb.submitted_by_name}</td>
            </tr>
          )) : <tr><td colSpan={7} className="empty">No reports submitted yet. Save the first one above.</td></tr>}</tbody></table></div>
    </>
  )
}

function AllPoliciesImport({ cycle, busy, onIngest }: {
  cycle: AgentPortalCycle | null; busy: boolean; onIngest: (stream: string, text: string) => void
}) {
  const [text, setText] = useState('')
  return (
    <>
      <textarea value={text} onChange={e => setText(e.target.value)} style={{ minHeight: 100 }}
        placeholder="Paste the all-policies export here…" />
      <div className="actions">
        <button className="btn primary" disabled={busy || !cycle || !text.trim()} onClick={() => { onIngest('all_policies', text); setText('') }}>
          {busy ? 'Importing…' : 'Import New Sales + Motor'}</button>
      </div>
    </>
  )
}

/* ─────────────── Agents ─────────────── */
function Agents({ cycleId, agents, streamName }: { cycleId: string; agents: AgentPortalAgent[]; streamName: (k: string) => string }) {
  const [pick, setPick] = useState('')
  const [lines, setLines] = useState<AgentPortalLine[]>([])
  useEffect(() => {
    if (!cycleId || !pick) { setLines([]); return }
    agentPortalLines(cycleId, undefined, undefined, pick).then(setLines).catch(() => setLines([]))
  }, [cycleId, pick])
  const agent = agents.find(a => a.id === pick)
  const approved = lines.filter(l => l.payable)
  const rejected = lines.filter(l => !l.payable)
  const total = approved.reduce((t, l) => t + Number(l.commission), 0)
  const june = agent ? AGENT_PROFILE[agent.name] : undefined
  const juneRows = june ? Object.entries(june.amt) : []
  const juneTotal = juneRows.reduce((t, [, v]) => t + v, 0)

  return (
    <>
      <div className="crumbs">Agent lookup</div><h1>Agent profile</h1>
      <p className="lead">Pick an agent to see every policy they reported in this tool — whether it was approved or rejected, and the reason for each.</p>
      <div className="filters"><div className="fld" style={{ minWidth: 320 }}><label>Agent name</label>
        <select value={pick} onChange={e => setPick(e.target.value)}>
          <option value="">— pick an agent —</option>
          {agents.map(a => <option key={a.id} value={a.id}>{a.name}{a.has_bank ? '  ✓ bank' : ''}</option>)}
        </select></div></div>
      {agent ? (
        <>
          <div className="metrics">
            <div className="metric hero"><div className="l">Commission this cycle</div><div className="v">{money(total)}</div></div>
            <div className="metric ok"><div className="l">Approved</div><div className="v">{approved.length}</div></div>
            <div className="metric info"><div className="l">Rejected</div><div className="v">{rejected.length}</div></div>
          </div>
          <div className="card"><div className="hd"><h3>{agent.name} — policies reported</h3><span className="meta">{lines.length} item(s) in this tool</span></div>
            <table><thead><tr><th>Policy</th><th>Stream</th><th>Status</th><th className="r">Commission</th></tr></thead>
              <tbody>{lines.length ? lines.slice(0, 500).map(l => (
                <tr key={l.id} className={l.payable ? '' : 'is-fail'}>
                  <td className="num">{l.policy_ref || '—'}</td>
                  <td className="reason">{streamName(l.stream)}</td>
                  <td><span className={`pill ${l.payable ? 'pay' : 'no'}`}>{l.payable ? 'APPROVED' : 'REJECTED'}</span> <span className="reason">{l.reason}</span></td>
                  <td className="money r">{l.payable ? money(l.commission) : '—'}</td>
                </tr>
              )) : <tr><td colSpan={4} className="empty">No policies reported in this tool yet for this agent. Add them on the Daily reports tab.</td></tr>}</tbody></table></div>
          <div className="card"><div className="hd"><h3>Streams — June reference</h3><span className="meta">from the uploaded module reports</span></div>
            <table><thead><tr><th>Stream</th><th className="r">June amount</th></tr></thead>
              <tbody>{juneRows.length ? juneRows.map(([k, v]) => <tr key={k}><td>{streamName(k)}</td><td className="money r">{money(v)}</td></tr>)
                : <tr><td colSpan={2} className="empty">No June reference for this agent.</td></tr>}</tbody>
              {juneRows.length > 0 && <tfoot><tr><td>Total</td><td className="money r">{money(juneTotal)}</td></tr></tfoot>}</table></div>
        </>
      ) : <div className="empty">Pick an agent above.</div>}
    </>
  )
}

/* ─────────────── Pay run ─────────────── */
function gchip(status?: string, note?: string) {
  const map: Record<string, { t: string; c: string; b: string }> = {
    ok:          { t: 'Graphite ✓',   c: '#1F5132', b: '#B6E0C2' },
    mismatch:    { t: 'Differs',      c: '#8E1F12', b: '#F5C2BE' },
    not_found:   { t: 'Not in Graphite', c: '#7a4a00', b: '#F4A623' },
    unavailable: { t: 'Unchecked',    c: '#555', b: '#ddd' },
    skipped:     { t: '—',            c: '#999', b: '#eee' },
  }
  const s = map[status || ''] || null
  if (!s) return <span style={{ color: '#bbb' }}>—</span>
  return <span title={note || ''} style={{ fontSize: 11, fontWeight: 700, color: s.c, border: `1px solid ${s.b}`, borderRadius: 6, padding: '1px 6px', whiteSpace: 'nowrap' }}>{s.t}</span>
}

function Payrun({ cycleId, payrun, streamName }: { cycleId: string; payrun: AgentPortalPayrun | null; streamName: (k: string) => string }) {
  const [mf, setMf] = useState('')
  const [lines, setLines] = useState<AgentPortalLine[]>([])
  const [verifying, setVerifying] = useState(false)
  const [vres, setVres] = useState<import('@/lib/api').AgentPortalVerifyResult | null>(null)
  const reload = useCallback(() => { if (cycleId) agentPortalLines(cycleId, mf || undefined).then(setLines).catch(() => setLines([])) }, [cycleId, mf])
  useEffect(() => { reload() }, [reload])
  const verify = async () => {
    if (!cycleId) return
    setVerifying(true)
    try { const r = await agentPortalVerifyGraphite(cycleId); setVres(r); reload() }
    catch { setVres(null) }
    finally { setVerifying(false) }
  }
  const agents = payrun?.agents || []
  const approved = lines.filter(l => l.payable), rejected = lines.filter(l => !l.payable)
  const streamKeys = payrun ? Object.keys(payrun.streams) : []
  return (
    <>
      <div className="crumbs">Finance</div><h1>Pay run</h1>
      <p className="lead">Approved and rejected lines with the reason for each. The cycle window is set when the cycle is created.</p>
      <div className="card"><div className="hd"><h3>Live Graphite check</h3><span className="meta">confirms each payable line against the live system — paid · active · KYC</span></div><div className="bd">
        <button className="btn primary" onClick={verify} disabled={verifying || !cycleId}>{verifying ? 'Checking Graphite…' : 'Verify against Graphite'}</button>
        {vres && (
          <p className="lead" style={{ marginTop: 8 }}>
            {vres.unavailable
              ? 'Graphite was unreachable — lines left unchecked; try again shortly.'
              : <>Checked {vres.lines_checked}: <b style={{ color: '#1F5132' }}>{vres.ok} confirmed</b>, <b style={{ color: '#8E1F12' }}>{vres.mismatch} differ</b>, {vres.not_found} not in Graphite, {vres.skipped} no policy number. The chips below now show each line&apos;s live status.</>}
          </p>
        )}
      </div></div>
      <div className="card"><div className="hd"><h3>Per-agent payable</h3><span className="meta">{agents.length} agent(s)</span></div>
        <table><thead><tr><th>Agent</th><th className="r">Commission</th></tr></thead>
          <tbody>{agents.length ? agents.map(a => <tr key={a.agent}><td className="agentcell">{a.agent}</td><td className="money r">{money(a.payable_bwp)}</td></tr>)
            : <tr><td colSpan={2} className="empty">No approved lines.</td></tr>}</tbody>
          <tfoot><tr><td>Total</td><td className="money r">{money(payrun?.total_payable_bwp || 0)}</td></tr></tfoot></table></div>

      <div className="filters"><div className="fld"><label>Stream</label>
        <select value={mf} onChange={e => setMf(e.target.value)}>
          <option value="">All streams</option>
          {streamKeys.map(k => <option key={k} value={k}>{streamName(k)}</option>)}
        </select></div></div>

      <div className="card"><div className="hd"><h3><span className="pill pay">APPROVED</span> &nbsp;Paid lines</h3><span className="meta">{approved.length} line(s)</span></div>
        <table><thead><tr><th>Agent</th><th>Policy</th><th>Stream</th><th className="r">Basis</th><th>Why paid</th><th>Graphite</th><th className="r">Commission</th></tr></thead>
          <tbody>{approved.length ? approved.slice(0, 500).map(l => <tr key={l.id}><td>{l.agent_name}</td><td className="num">{l.policy_ref || '—'}</td><td className="reason">{streamName(l.stream)}</td><td className="money r">{Number(l.basis) ? money(l.basis) : '—'}</td><td className="reason">{l.reason}</td><td>{gchip(l.graphite_status, l.graphite_note)}</td><td className="money r">{money(l.commission)}</td></tr>)
            : <tr><td colSpan={7} className="empty">Nothing approved.</td></tr>}</tbody></table></div>

      <div className="card"><div className="hd"><h3><span className="pill no">REJECTED</span> &nbsp;Not paid</h3><span className="meta">{rejected.length} line(s)</span></div>
        <table><thead><tr><th>Agent</th><th>Policy</th><th>Stream</th><th className="r">Basis</th><th>Why not paid</th></tr></thead>
          <tbody>{rejected.length ? rejected.slice(0, 500).map(l => <tr key={l.id} className="is-fail"><td>{l.agent_name}</td><td className="num">{l.policy_ref || '—'}</td><td className="reason">{streamName(l.stream)}</td><td className="money r">{Number(l.basis) ? money(l.basis) : '—'}</td><td className="reason">{l.reason}</td></tr>)
            : <tr><td colSpan={5} className="empty">Nothing rejected.</td></tr>}</tbody></table></div>
    </>
  )
}

/* ─────────────── Reports (control reports + status check) ─────────────── */
const REPORT_KINDS = [
  { key: 'all_policies', name: 'All Policies Report', hint: 'drives the policy status check below' },
  { key: 'transactions', name: 'Transaction Report', hint: 'paygate / RealPay transactions' },
  { key: 'bank_statement', name: 'Bank Statement Report', hint: 'bank statement export' },
] as const

function Reports({ cycle, flash, setErr, streamName }: {
  cycle: AgentPortalCycle | null; flash: (m: string) => void; setErr: (m: string) => void
  streamName: (k: string) => string
}) {
  const [kind, setKind] = useState<string>('all_policies')
  const [text, setText] = useState('')
  const [reps, setReps] = useState<AgentPortalSourceReport[]>([])
  const [check, setCheck] = useState<AgentPortalStatusCheck | null>(null)
  const [busy, setBusy] = useState(false)
  const [upMsg, setUpMsg] = useState('')

  const load = useCallback(() => {
    if (!cycle) { setReps([]); setCheck(null); return }
    agentPortalSourceReports(cycle.id).then(setReps).catch(() => setReps([]))
    agentPortalStatusCheck(cycle.id).then(setCheck).catch(() => setCheck(null))
  }, [cycle])
  useEffect(() => { load() }, [load])

  const loadSheet = async (f: File | null) => {
    if (!f) { setUpMsg('Choose a file first.'); return }
    setUpMsg('Reading ' + f.name + '…')
    try {
      const XLSX = await import('xlsx')
      const wb = XLSX.read(new Uint8Array(await f.arrayBuffer()), { type: 'array' })
      const arr = XLSX.utils.sheet_to_json(wb.Sheets[wb.SheetNames[0]], { header: 1, raw: false, defval: '' }) as string[][]
      const rows = arr.map(r => r.map(c => String(c ?? '').trim())).filter(r => r.some(c => c !== ''))
      setText(rows.map(r => r.join('\t')).join('\n'))
      setUpMsg(rows.length ? `Loaded ${rows.length} row(s) — check, then Save report.` : 'That file looks empty.')
    } catch { setUpMsg('Could not read that file.') }
  }
  const save = async () => {
    if (!cycle) { setErr('Create/select a cycle first.'); return }
    setBusy(true)
    try {
      await agentPortalAddSourceReport(cycle.id, kind, text)
      setText(''); setUpMsg(''); flash('Report saved ✓'); load()
    } catch (e) { setErr(e instanceof Error ? e.message : 'Save failed') }
    finally { setBusy(false) }
  }
  const sel = REPORT_KINDS.find(k => k.key === kind)
  const vlabel: Record<string, string> = {
    not_in_report: 'Not in report', cancelled: 'Cancelled in report', premium_differs: 'Premium differs',
  }

  return (
    <>
      <div className="crumbs">Control reports</div><h1>Reports</h1>
      <p className="lead">Add the All Policies, Transaction and Bank Statement reports for the cycle — they are kept on record, and the All Policies Report drives the policy status check below so you can monitor the status of policies and track transactions.</p>

      <div className="card"><div className="bd">
        <div className="scards" style={{ gridTemplateColumns: 'repeat(auto-fill,minmax(230px,1fr))' }}>
          {REPORT_KINDS.map(k => (
            <button key={k.key} className={`scard${kind === k.key ? ' sel' : ''}`} onClick={() => setKind(k.key)}>
              <span className="n">{k.name}</span>
              <span className="t">{k.hint}</span>
            </button>
          ))}
        </div>
        <div className="upbox">
          <div className="upttl">Upload the {sel?.name}</div>
          <div className="hint" style={{ marginTop: 0 }}>Attach the export (.xlsx or .csv) — or paste the rows below.</div>
          <div className="actions" style={{ marginTop: 8 }}>
            <input type="file" accept=".xlsx,.xls,.csv" onChange={e => loadSheet(e.target.files?.[0] || null)} />
          </div>
          {upMsg && <div className="hint">{upMsg}</div>}
        </div>
        <textarea value={text} onChange={e => setText(e.target.value)} style={{ minHeight: 110 }}
          placeholder={`Loaded rows appear here — or paste the ${sel?.name || ''} rows directly.`} />
        <div className="actions">
          <button className="btn primary" disabled={busy || !cycle || !text.trim()} onClick={save}>
            {busy ? 'Saving…' : 'Save report'}</button>
          <span className="hint" style={{ margin: 0 }}>{cycle ? `Cycle: ${cycle.label}` : 'Create a cycle first (+ New cycle, top right)'}</span>
        </div>
      </div></div>

      <div className="card"><div className="hd"><h3>Policy status check</h3>
        <span className="meta">payable lines vs the latest All Policies Report</span>
        <button className="btn ghost" style={{ marginLeft: 'auto' }} onClick={load}>Re-check</button></div>
        <div className="bd">
        {!check || !check.available ? (
          <div className="empty">{check?.detail || 'Upload the All Policies Report above to run the check.'}</div>
        ) : (
          <>
            <div className="metrics" style={{ marginTop: 0 }}>
              <div className="metric ok"><div className="l">Verified active</div><div className="v">{check.ok}</div></div>
              <div className="metric"><div className="l">Cancelled in report</div><div className="v">{check.cancelled}</div></div>
              <div className="metric"><div className="l">Not in report</div><div className="v">{check.not_in_report}</div></div>
              <div className="metric info"><div className="l">Premium differs</div><div className="v">{check.premium_differs}</div></div>
            </div>
            {(check.issues || []).length > 0 ? (
              <table><thead><tr><th>Policy</th><th>Agent</th><th>Stream</th><th>Finding</th></tr></thead>
                <tbody>{check.issues!.map((i, ix) => (
                  <tr key={ix} className="is-fail">
                    <td className="num">{i.policy}</td>
                    <td>{i.agent}</td>
                    <td className="reason">{streamName(i.stream)}</td>
                    <td><span className="pill no">{vlabel[i.verdict] || i.verdict}</span> <span className="reason">{i.note}</span></td>
                  </tr>
                ))}</tbody></table>
            ) : <div className="empty">All payable lines check out against the report. ✓</div>}
          </>
        )}
      </div></div>

      <div className="card"><div className="hd"><h3>Reports on record</h3><span className="meta">{reps.length} report(s) for this cycle</span></div>
        <table><thead><tr><th>When</th><th>Report</th><th className="r">Rows</th><th>By</th></tr></thead>
          <tbody>{reps.length ? reps.map(r => (
            <tr key={r.id}>
              <td className="num" style={{ whiteSpace: 'nowrap' }}>{(r.created_at || '').slice(0, 16).replace('T', ' ')}</td>
              <td><span className="pill mod">{r.kind_display}</span></td>
              <td className="r num">{r.rows_count}</td>
              <td className="reason">{r.uploaded_by_name}</td>
            </tr>
          )) : <tr><td colSpan={4} className="empty">No reports uploaded yet.</td></tr>}</tbody></table></div>
    </>
  )
}

/* ─────────────── Payout ─────────────── */
function Payout({ cycle, payout, flash, setErr }: {
  cycle: AgentPortalCycle | null; payout: AgentPortalPayout | null
  flash: (m: string) => void; setErr: (m: string) => void
}) {
  const ready = payout?.ready || [], held = payout?.held || []
  const readyTot = ready.reduce((s, r) => s + Number(r.amount_bwp), 0)
  const heldTot = held.reduce((s, r) => s + Number(r.amount_bwp), 0)
  const download = async () => {
    if (!cycle) return
    try {
      const res = await apiFetchBinary(`/agent-portal/cycles/${cycle.id}/payout-export/`)
      const blob = await res.blob()
      const url = URL.createObjectURL(blob); const a = document.createElement('a')
      a.href = url; a.download = `payout_${cycle.label.replace(/[^A-Za-z0-9._-]+/g, '_')}.csv`
      document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 30000)
      flash('Payment file downloaded ✓')
    } catch (e) { setErr(e instanceof Error ? e.message : 'Export failed') }
  }
  return (
    <>
      <div className="crumbs">Payment</div><h1>Payout</h1>
      <p className="lead">Only agents with bank details captured appear as ready to pay; anyone earning without an account is held until you add it on the Banking tab.</p>
      <div className="metrics">
        <div className="metric hero"><div className="l">Ready to pay</div><div className="v">{money(readyTot)}</div></div>
        <div className="metric"><div className="l">Agents ready</div><div className="v">{ready.length}</div></div>
        <div className="metric"><div className="l">On hold (no bank)</div><div className="v">{money(heldTot)}</div></div>
        <div className="metric"><div className="l">Agents on hold</div><div className="v">{held.length}</div></div>
      </div>
      <div className="card"><div className="hd"><h3><span className="pill pay">READY</span> &nbsp;Pay these agents</h3>
        <span className="meta">{ready.length} agent(s) · {money(readyTot)}</span>
        {ready.length > 0 && <button className="btn ghost" style={{ marginLeft: 'auto' }} onClick={download}>Download payment file</button>}</div>
        <table><thead><tr><th>Agent</th><th>Bank</th><th>Branch</th><th>Account number</th><th className="r">Amount</th></tr></thead>
          <tbody>{ready.length ? ready.map((r, i) => <tr key={i}><td className="agentcell">{r.agent}</td><td>{r.bank}</td><td className="num">{r.branch}</td><td className="num">{r.account}</td><td className="money r">{money(r.amount_bwp)}</td></tr>)
            : <tr><td colSpan={5} className="empty">No payable agents with bank details yet.</td></tr>}</tbody>
          <tfoot><tr><td>Total to pay</td><td /><td /><td /><td className="money r">{money(readyTot)}</td></tr></tfoot></table></div>
      {held.length > 0 && (
        <div className="card"><div className="hd"><h3><span className="pill no">ON HOLD</span> &nbsp;Missing bank details</h3><span className="meta">{held.length} agent(s) · {money(heldTot)}</span></div>
          <table><thead><tr><th>Agent</th><th className="r">Amount owed</th><th>Reason held</th></tr></thead>
            <tbody>{held.map((r, i) => <tr key={i} className="is-fail"><td className="agentcell">{r.agent}</td><td className="money r">{money(r.amount_bwp)}</td><td className="reason">No bank details — add on the Banking tab</td></tr>)}</tbody></table></div>
      )}
    </>
  )
}

/* ─────────────── Banking ─────────────── */
function Banking({ agents, reload, flash, setErr }: {
  agents: AgentPortalAgent[]; reload: () => void; flash: (m: string) => void; setErr: (m: string) => void
}) {
  const [pick, setPick] = useState('')
  const [form, setForm] = useState<AgentPortalBank>({})
  const [q, setQ] = useState('')
  const [data, setData] = useState<AgentPortalBankAccounts | null>(null)
  const [showPw, setShowPw] = useState(false)
  const [pw, setPw] = useState('')
  const revealed = !!data?.revealed
  const revealEnabled = data?.reveal_configured !== false
  const loadAccounts = useCallback(() => {
    agentPortalBankAccounts().then(setData).catch(() => setData(null))
  }, [])
  const doUnlock = async () => {
    try {
      await agentPortalBankUnlock(pw)
      setPw(''); setShowPw(false); flash('Account numbers unlocked ✓'); loadAccounts()
    } catch (e) { setErr(e instanceof Error ? e.message : 'Wrong reveal password') }
  }
  const doLock = async () => { try { await agentPortalBankLock() } catch { /* ignore */ } finally { loadAccounts() } }
  useEffect(() => { loadAccounts() }, [loadAccounts])
  useEffect(() => {
    if (!pick) { setForm({}); return }
    agentPortalGetBank(pick).then(b => setForm(b || {})).catch(() => setForm({}))
  }, [pick])
  const save = async () => {
    if (!pick) { setErr('Pick an agent.'); return }
    if (!(form.account_number || '').trim()) { setErr('An account number is required.'); return }
    try { await agentPortalSaveBank(pick, form); flash('Bank details saved ✓'); reload(); loadAccounts() }
    catch (e) { setErr(e instanceof Error ? e.message : 'Save failed') }
  }
  const total = data?.total_agents ?? agents.length
  const submitted = data?.submitted ?? agents.filter(a => a.has_bank).length
  const pct = total ? Math.round(submitted / total * 100) : 0
  const rows = (data?.accounts || []).filter(r => r.agent_name.toLowerCase().includes(q.toLowerCase()))
  // Account numbers are masked SERVER-SIDE now; the reveal password (below) unmasks them.
  const pickFor = agents.filter(a => a.name.toLowerCase().includes(q.toLowerCase())).slice(0, 400)
  return (
    <>
      <div className="crumbs">Agent banking</div><h1>Agent bank accounts</h1>
      <p className="lead">Capture and view each agent&apos;s bank details for payment. An agent must have an account number to appear on the Payout list. Account numbers are <b>masked</b> and only shown after you enter the <b>reveal password</b> (auto-relocks after a few hours). Handled under the DPA.</p>
      <div className="metrics">
        <div className="metric hero"><div className="l">Bank details submitted</div><div className="v">{submitted} / {total}</div></div>
        <div className="metric"><div className="l">Coverage</div><div className="v">{pct}%</div></div>
        <div className="metric"><div className="l">Still missing</div><div className="v">{Math.max(0, total - submitted)}</div></div>
      </div>
      <div className="card"><div className="hd"><h3>Add / edit bank details</h3><span className="meta">pick an agent, or click Edit in the table below</span></div><div className="bd">
        <div className="egrid">
          <div className="fld"><label>Agent</label>
            <input placeholder="type to filter…" value={q} onChange={e => setQ(e.target.value)} />
            <select value={pick} onChange={e => setPick(e.target.value)} style={{ marginTop: 6 }}>
              <option value="">— pick —</option>
              {pickFor.map(a => <option key={a.id} value={a.id}>{a.name}{a.has_bank ? ' ✓' : ''}</option>)}
            </select></div>
          <div className="fld"><label>Bank</label><input value={form.bank_name || ''} onChange={e => setForm({ ...form, bank_name: e.target.value })} placeholder="e.g. FNB Botswana" /></div>
          <div className="fld"><label>Branch code</label><input value={form.branch_code || ''} onChange={e => setForm({ ...form, branch_code: e.target.value })} /></div>
          <div className="fld"><label>Account number</label><input value={form.account_number || ''} onChange={e => setForm({ ...form, account_number: e.target.value })} /></div>
          <div className="fld"><label>Account holder</label><input value={form.account_name || ''} onChange={e => setForm({ ...form, account_name: e.target.value })} /></div>
        </div>
        <div className="actions"><button className="btn primary" onClick={save} disabled={!pick}>Save bank details</button></div>
      </div></div>
      <div className="card"><div className="hd"><h3>Submitted bank accounts</h3>
        <span className="meta">{rows.length} of {submitted} · {revealed ? 'numbers shown' : 'numbers masked'}</span>
        {revealed
          ? <button className="btn ghost" style={{ marginLeft: 'auto' }} onClick={doLock}>Hide numbers</button>
          : showPw
            ? <span style={{ marginLeft: 'auto', display: 'flex', gap: 6, alignItems: 'center' }}>
                <input type="password" placeholder="reveal password" value={pw} onChange={e => setPw(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') doUnlock() }} style={{ padding: '4px 8px', fontSize: 13 }} autoFocus />
                <button className="btn primary" style={{ padding: '4px 10px', fontSize: 12 }} onClick={doUnlock}>Unlock</button>
                <button className="btn ghost" style={{ padding: '4px 10px', fontSize: 12 }} onClick={() => { setShowPw(false); setPw('') }}>Cancel</button>
              </span>
            : <button className="btn ghost" style={{ marginLeft: 'auto' }} onClick={() => setShowPw(true)} disabled={!revealEnabled} title={revealEnabled ? 'Enter the reveal password to view full numbers' : 'Reveal password not set yet — ask the CFO'}>Reveal numbers</button>}</div>
        <table><thead><tr><th>Agent</th><th>Bank</th><th>Branch</th><th>Account number</th><th>Holder</th><th></th></tr></thead>
          <tbody>{rows.length ? rows.map(r => (
            <tr key={r.agent_id}>
              <td className="agentcell">{r.agent_name}</td>
              <td>{r.bank_name || '—'}</td>
              <td className="num">{r.branch_code || '—'}</td>
              <td className="num">{r.account_number}</td>
              <td>{r.account_name || '—'}</td>
              <td className="r"><button className="btn ghost" style={{ padding: '4px 10px', fontSize: 12 }} onClick={() => setPick(r.agent_id)}>Edit</button></td>
            </tr>
          )) : <tr><td colSpan={6} className="empty">No bank details captured yet.</td></tr>}</tbody></table></div>
    </>
  )
}

/* ─────────────── Master payable ─────────────── */
function Master({ payrun, streamName }: { payrun: AgentPortalPayrun | null; streamName: (k: string) => string }) {
  const agents = payrun?.agents || []
  const grand = Number(payrun?.total_payable_bwp || 0)
  const streams = payrun ? Object.entries(payrun.streams).filter(([, v]) => Number(v.payable_bwp) > 0).sort((a, b) => Number(b[1].payable_bwp) - Number(a[1].payable_bwp)) : []
  const csv = () => {
    const rows = [['Agent', 'Total Payable (BWP)'], ...agents.map(a => [a.agent, a.payable_bwp]), ['GRAND TOTAL', String(grand)]]
    const text = rows.map(r => r.map(c => /[",\n]/.test(String(c)) ? '"' + String(c).replace(/"/g, '""') + '"' : c).join(',')).join('\n')
    const b = new Blob([text], { type: 'text/csv' }); const u = URL.createObjectURL(b); const a = document.createElement('a')
    a.href = u; a.download = 'UNICOIN_Master_Payable.csv'; a.click(); setTimeout(() => URL.revokeObjectURL(u), 30000)
  }
  return (
    <>
      <div className="crumbs">Roll-up</div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12 }}>
        <h1>Master payable</h1>
        {agents.length > 0 && <button className="btn ghost" onClick={csv}>Download master CSV</button>}
      </div>
      <div className="grand"><div><div className="lbl">Grand total payable</div><div className="big num">{money(grand)}</div></div>
        <div className="sub">{agents.length} agent(s)</div></div>
      <div className="card"><div className="hd"><h3>Payable by agent</h3><span className="meta">The pay sheet</span></div>
        <table><thead><tr><th>Agent</th><th className="r">Total payable</th></tr></thead>
          <tbody>{agents.length ? agents.map(a => <tr key={a.agent}><td className="agentcell">{a.agent}</td><td className="money r">{money(a.payable_bwp)}</td></tr>)
            : <tr><td colSpan={2} className="empty">No reports logged yet.</td></tr>}</tbody>
          <tfoot><tr><td>Grand total</td><td className="money r">{money(grand)}</td></tr></tfoot></table></div>
      <div className="card"><div className="hd"><h3>Stream totals</h3></div>
        <table><tbody>{streams.map(([k, v]) => <tr key={k}><td>{streamName(k)}</td><td className="money r">{money(v.payable_bwp)}</td></tr>)}</tbody>
          <tfoot><tr><td>Grand total</td><td className="money r">{money(grand)}</td></tr></tfoot></table></div>
    </>
  )
}

/* ─────────────── New cycle prompt ─────────────── */
function NewCyclePrompt({ onCreated, setErr }: { onCreated: () => void; setErr: (m: string) => void }) {
  const [label, setLabel] = useState(''); const [from, setFrom] = useState(''); const [to, setTo] = useState('')
  const create = async () => {
    if (!label || !from || !to) { setErr('Fill label + both dates.'); return }
    try { await agentPortalCreateCycle({ label, start_date: from, end_date: to }); onCreated() }
    catch (e) { setErr(e instanceof Error ? e.message : 'Create failed') }
  }
  return (
    <div className="card"><div className="hd"><h3>Start a commission cycle</h3><span className="meta">No cycle selected</span></div><div className="bd">
      <div className="frm">
        <div className="fld"><label>Cycle label</label><input value={label} onChange={e => setLabel(e.target.value)} placeholder="e.g. 24 May – 23 Jun 2026" /></div>
        <div className="fld"><label>Start date</label><input type="date" value={from} onChange={e => setFrom(e.target.value)} /></div>
        <div className="fld"><label>End date</label><input type="date" value={to} onChange={e => setTo(e.target.value)} /></div>
      </div>
      <div className="actions"><button className="btn primary" onClick={create}>Create cycle</button></div>
    </div></div>
  )
}

/* ─────────────── Motlatsi tool look (legacy AD palette) ─────────────── */
/* ─────────────── Payslips (net of tax, per agent) ─────────────── */
function Payslips({ cycle, cycleId, flash, setErr, reload }: {
  cycle: AgentPortalCycle | null; cycleId: string
  flash: (m: string) => void; setErr: (m: string | null) => void; reload: () => Promise<void>
}) {
  const [rows, setRows] = useState<AgentPortalPayslip[]>([])
  const [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState(false)
  const [q, setQ] = useState('')
  const approved = cycle?.status === 'approved' || cycle?.status === 'paid'

  const load = useCallback(async () => {
    if (!cycleId) { setRows([]); setLoaded(true); return }
    try { setRows(await agentPortalPayslips(cycleId)) }
    catch (e) { setErr(e instanceof Error ? e.message : 'Failed to load payslips') }
    finally { setLoaded(true) }
  }, [cycleId, setErr])
  useEffect(() => { setLoaded(false); load() }, [load])

  const build = async () => {
    if (!cycleId) { setErr('Select a cycle first.'); return }
    setBusy(true); setErr(null)
    try { const r = await agentPortalBuildPayslips(cycleId); flash(`Payslips ready: ${r.payslips} (net ${money(r.net_total_bwp)})`); await load() }
    catch (e) { setErr(e instanceof Error ? e.message : 'Build failed') }
    finally { setBusy(false) }
  }
  const download = async (p: AgentPortalPayslip) => {
    try {
      const blob = await agentPortalPayslipPdf(p.id)
      const u = URL.createObjectURL(blob); const a = document.createElement('a')
      a.href = u; a.download = `${p.number}.pdf`; a.click(); setTimeout(() => URL.revokeObjectURL(u), 30000)
    } catch (e) { setErr(e instanceof Error ? e.message : 'Download failed') }
  }
  const downloadExcel = async () => {
    if (!cycleId) return
    setBusy(true); setErr(null)
    try {
      const blob = await agentPortalAgentReviewXlsx(cycleId)
      const u = URL.createObjectURL(blob); const a = document.createElement('a')
      a.href = u; a.download = `UniCoin_${(cycle?.label || 'cycle').replace(/[^\w-]+/g, '_')}_Agent_Review.xlsx`
      a.click(); setTimeout(() => URL.revokeObjectURL(u), 30000)
    } catch (e) { setErr(e instanceof Error ? e.message : 'Excel download failed') }
    finally { setBusy(false) }
  }
  const email = async (p: AgentPortalPayslip) => {
    if (!p.has_email) { setErr(`${p.agent_name} has no email on file.`); return }
    setBusy(true)
    try { const r = await agentPortalEmailPayslip(p.id); flash(`Emailed ${p.agent_name} → ${r.to}`); await load() }
    catch (e) { setErr(e instanceof Error ? e.message : 'Email failed') }
    finally { setBusy(false) }
  }
  const approveCycle = async () => {
    if (!cycleId) return
    setBusy(true); setErr(null)
    try { await agentPortalApprove(cycleId); flash('Pay cycle approved — payslips can now be emailed'); await reload() }
    catch (e) { setErr(e instanceof Error ? e.message : 'Approve failed') }
    finally { setBusy(false) }
  }
  const reopenCycle = async () => {
    if (!cycleId) return
    setBusy(true); setErr(null)
    try { await agentPortalReopen(cycleId); flash('Pay cycle reopened for corrections'); await reload() }
    catch (e) { setErr(e instanceof Error ? e.message : 'Reopen failed') }
    finally { setBusy(false) }
  }

  const shown = rows.filter(r => !q || r.agent_name.toLowerCase().includes(q.toLowerCase()) || (r.agent_ref || '').includes(q))
  const totNet = rows.reduce((s, r) => s + Number(r.net || 0), 0)
  const totTax = rows.reduce((s, r) => s + Number(r.tax || 0), 0)

  return (
    <>
      <div className="crumbs">UniCoin · Instant Insurance</div>
      <h1>Commission payslips</h1>
      <p className="lead">A one-page commission payslip per agent — net of tax (UniCoin agent scale).
        Agents have no login: download the PDF or email it straight to them. Regenerating is safe; it re-reads this cycle&apos;s pay-run.</p>
      <div className="card"><div className="bd">
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 14 }}>
          <button className="btn primary" disabled={busy || !cycle || (approved && rows.length > 0)}
                  title={approved && rows.length > 0 ? 'Cycle is approved — reopen it to rebuild' : ''}
                  onClick={build}>{rows.length ? 'Rebuild payslips' : 'Generate payslips'}</button>
          {rows.length > 0 && (approved
            ? <><span style={{ fontSize: 12, fontWeight: 700, color: 'var(--pass)', border: '1px solid var(--pass)', borderRadius: 6, padding: '3px 9px' }}>Approved ✓</span>
                <button className="btn ghost" disabled={busy} onClick={reopenCycle} style={{ padding: '6px 12px' }}>Reopen</button></>
            : <button className="btn" disabled={busy} onClick={approveCycle}>Approve pay cycle</button>)}
          {rows.length > 0 && (
            <button className="btn ghost" disabled={busy} onClick={downloadExcel}
                    title="Download every agent's commission as an Excel workbook for review">Download Excel</button>
          )}
          <input placeholder="Find agent or ID…" value={q} onChange={e => setQ(e.target.value)}
                 style={{ flex: '0 1 220px', fontSize: 13, padding: '8px 11px', border: '1px solid var(--line-strong)', borderRadius: 8 }} />
          <div style={{ marginLeft: 'auto', fontSize: 12.5, color: 'var(--slate)' }}>
            {rows.length} payslips · tax <b className="num">{money(totTax)}</b> · net <b className="num">{money(totNet)}</b>
          </div>
        </div>
        {rows.length > 0 && !approved && (
          <p className="hint" style={{ marginTop: -6, marginBottom: 12 }}>Download any slip to review. <b>Emailing to agents unlocks after you Approve the pay cycle.</b></p>
        )}
        {!loaded ? <p className="lead">Loading…</p> : !rows.length ? (
          <p className="lead">No payslips yet for this cycle. Click <b>Generate payslips</b> once the pay-run is loaded.</p>
        ) : (
          <table><thead><tr>
            <th>Payslip</th><th>Agent</th><th>Agent ID</th><th className="r">Gross</th>
            <th className="r">Tax</th><th className="r">Net payable</th><th>Sent</th><th></th>
          </tr></thead><tbody>
            {shown.map(p => (
              <tr key={p.id}>
                <td className="num">{p.number}</td>
                <td>{p.agent_name}</td>
                <td className="num">{p.agent_ref || '—'}</td>
                <td className="money r">{money(p.gross)}</td>
                <td className="money r">{Number(p.tax) ? money(p.tax) : '—'}</td>
                <td className="money r"><b>{money(p.net)}</b></td>
                <td>{p.emailed_at ? <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--pass)' }} title={p.emailed_to}>emailed</span> : ''}</td>
                <td className="r" style={{ whiteSpace: 'nowrap' }}>
                  <button className="btn" style={{ padding: '4px 10px', fontSize: 12 }} onClick={() => download(p)}>PDF</button>{' '}
                  <button className="btn ghost" style={{ padding: '4px 10px', fontSize: 12 }} disabled={busy || !p.has_email || !approved} title={!approved ? 'Approve the pay cycle first' : (p.has_email ? `Email ${p.agent_name}` : 'No email on file')} onClick={() => email(p)}>Email</button>
                </td>
              </tr>
            ))}
          </tbody></table>
        )}
      </div></div>
    </>
  )
}

const CSS = `
.ap-root{--ink:#1B2A6B;--ink-soft:#26357a;--paper:#fafbfc;--surface:#fff;--line:#dfe3e8;--line-strong:#c3cad3;--slate:#5a6b7b;--slate-dim:#8a97a4;--accent:#F26A21;--pass:#137a4a;--pass-bg:#e5f4ec;--fail:#b0402f;--fail-bg:#faeae6;--display:'Space Grotesk',system-ui,sans-serif;--body:'Inter',system-ui,sans-serif;--mono:'IBM Plex Mono',ui-monospace,monospace;--radius:10px;--shadow:0 1px 2px rgba(27,42,107,.06),0 8px 24px -12px rgba(27,42,107,.18);color:var(--ink);font-family:var(--body);font-size:14px}
.ap-root .num{font-family:var(--mono);font-variant-numeric:tabular-nums;letter-spacing:-.02em}
.ap-top{background:var(--ink);color:#eaf0f6;padding:14px 22px;display:flex;align-items:center;gap:18px;flex-wrap:wrap;border-bottom:3px solid var(--accent);border-radius:12px 12px 0 0}
.ap-top .brand{display:flex;flex-direction:row;align-items:center;gap:10px;line-height:1.15}
.ap-top .brand .brandtext{display:flex;flex-direction:column}
.ap-top .brand .k{font-family:var(--display);font-weight:700;font-size:18px}
.ap-top .brand .s{font-size:11px;color:#9fb0c0;letter-spacing:.04em;text-transform:uppercase}
.ap-top .tabs{display:flex;gap:4px;flex-wrap:wrap}
.ap-top .tab{font-family:var(--display);font-weight:600;font-size:12.5px;color:#aebccb;background:transparent;border:none;padding:8px 12px;border-radius:8px;cursor:pointer}
.ap-top .tab:hover{background:#16283a;color:#eaf0f6}
.ap-top .tab.active{background:var(--accent);color:#fff}
.ap-top .cycle{margin-left:auto;text-align:right;font-size:11px;color:#9fb0c0}
.ap-top .cycle select{font-family:var(--body);font-size:12px;color:#eaf0f6;background:#16283a;border:1px solid #2c3f52;border-radius:6px;padding:4px 7px;max-width:220px}
.ap-top .tape{background:var(--ink-soft);border:1px solid #2c3f52;border-radius:8px;padding:7px 14px;text-align:right;min-width:170px}
.ap-top .tape .lbl{font-size:10px;letter-spacing:.09em;text-transform:uppercase;color:#8ba0b4}
.ap-top .tape .val{font-family:var(--mono);font-weight:600;font-size:18px;color:var(--accent)}
.ap-main{background:var(--paper);padding:22px 24px 60px;border-radius:0 0 12px 12px;min-height:70vh}
.ap-main .crumbs{font-size:12px;color:var(--slate-dim);margin-bottom:4px}
.ap-main h1{font-family:var(--display);font-weight:700;font-size:24px;letter-spacing:-.02em;margin:0 0 4px}
.ap-main .lead{color:var(--slate);font-size:13px;margin:0 0 16px;max-width:760px}
.ap-main .card{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow);margin-bottom:20px;overflow:hidden}
.ap-main .card .hd{display:flex;align-items:baseline;gap:12px;padding:13px 16px;border-bottom:1px solid var(--line);background:#fbfcfd}
.ap-main .card .hd h3{font-family:var(--display);font-size:14px;margin:0}
.ap-main .card .hd .meta{font-size:12px;color:var(--slate)}
.ap-main .card .bd{padding:16px}
.ap-main .frm{display:grid;grid-template-columns:1.4fr 1fr 1.4fr;gap:14px;margin-bottom:6px}
.ap-main .egrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px 12px}
.ap-main .fld label{display:block;font-family:var(--display);font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--slate);margin-bottom:5px}
.ap-main .fld input,.ap-main .fld select{width:100%;font-family:var(--body);font-size:13.5px;padding:9px 11px;border:1px solid var(--line-strong);border-radius:8px;background:var(--surface);color:var(--ink)}
.ap-main .fld input:focus,.ap-main .fld select:focus,.ap-main textarea:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px rgba(242,106,33,.16)}
.ap-main textarea{width:100%;min-height:150px;resize:vertical;font-family:var(--mono);font-size:12px;line-height:1.5;padding:12px 13px;border:1px solid var(--line-strong);border-radius:var(--radius);background:var(--surface);color:var(--ink);margin-top:8px}
.ap-main .hint{font-size:12px;color:var(--slate);margin:6px 2px 0}
.ap-main .actions{display:flex;gap:10px;align-items:center;margin-top:14px;flex-wrap:wrap}
.ap-main button.btn{font-family:var(--display);font-weight:600;font-size:13.5px;border-radius:8px;padding:10px 18px;border:1px solid transparent;cursor:pointer}
.ap-main .btn.primary{background:var(--accent);color:#fff}
.ap-main .btn.primary:disabled{opacity:.5;cursor:default}
.ap-main .btn.ghost{background:transparent;border-color:var(--line-strong);color:var(--ink)}
.ap-main .btn.ghost:hover{background:#eef2f6}
.ap-main table{width:100%;border-collapse:collapse;font-size:12.7px}
.ap-main thead th{text-align:left;font-family:var(--display);font-weight:600;font-size:11px;letter-spacing:.05em;text-transform:uppercase;color:var(--slate);padding:9px 14px;border-bottom:1px solid var(--line);white-space:nowrap}
.ap-main tbody td{padding:8px 14px;border-bottom:1px solid #eef1f4}
.ap-main td.r,.ap-main th.r{text-align:right}
.ap-main td.money{font-family:var(--mono);font-variant-numeric:tabular-nums;white-space:nowrap}
.ap-main tr.is-fail{background:#fdf7f5}
.ap-main .pill{display:inline-block;font-family:var(--display);font-weight:600;font-size:10.5px;letter-spacing:.04em;padding:2px 8px;border-radius:999px}
.ap-main .pill.pay{background:var(--pass-bg);color:var(--pass)}
.ap-main .pill.no{background:var(--fail-bg);color:var(--fail)}
.ap-main .pill.mod{background:#eaeef2;color:var(--ink-soft)}
.ap-main .reason{color:var(--slate);font-size:12px}
.ap-main .agentcell{font-weight:600}
.ap-main tfoot td{padding:10px 14px;border-top:2px solid var(--ink);font-family:var(--display);font-weight:600}
.ap-main .empty{color:var(--slate-dim);font-size:13px;padding:26px 4px;text-align:center}
.ap-main .filters{display:flex;gap:10px;flex-wrap:wrap;align-items:end;margin-bottom:14px}
.ap-main .metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:6px 0 20px}
.ap-main .metric{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow);padding:14px 16px}
.ap-main .metric .l{font-family:var(--display);font-size:10.5px;letter-spacing:.09em;text-transform:uppercase;color:var(--slate-dim)}
.ap-main .metric .v{font-family:var(--mono);font-weight:600;font-size:22px;color:var(--ink);margin-top:4px}
.ap-main .metric.hero{background:var(--ink);border-bottom:3px solid var(--accent)}
.ap-main .metric.ok{border-top:3px solid var(--pass)}
.ap-main .metric.info{border-top:3px solid #378ADD}
.ap-main .scards{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:10px;margin:6px 0 12px}
.ap-main .scard{text-align:left;background:var(--surface);border:1px solid var(--line-strong);border-radius:10px;padding:12px 14px;cursor:pointer;display:flex;flex-direction:column;gap:3px}
.ap-main .scard:hover{border-color:var(--accent)}
.ap-main .scard.sel{border:2px solid var(--accent);background:#fffdf6}
.ap-main .scard .n{font-family:var(--display);font-weight:700;font-size:14px;color:var(--ink)}
.ap-main .scard .t{font-size:12px;color:var(--slate)}
.ap-main .rule{background:#fffdf6;border:1px solid #ecdcae;border-left:3px solid var(--accent);border-radius:8px;padding:10px 13px;color:#5c4d24;font-size:12.5px;margin:10px 0}
.ap-main .rule b{color:#3f3316}
.ap-main .dot{display:inline-block;width:9px;height:9px;border-radius:2px;background:#378ADD;margin-right:8px}
.ap-main .sharetrack{display:block;background:#eef2f6;border-radius:6px;height:10px;overflow:hidden;min-width:80px}
.ap-main .sharebar{display:block;background:#378ADD;height:100%;border-radius:6px;min-width:2px}
.ap-top .ncbtn{margin-top:5px;font-family:var(--display);font-weight:600;font-size:11.5px;color:#241a03;background:var(--accent);border:none;border-radius:6px;padding:4px 9px;cursor:pointer}
.ap-main .upbox{background:#fffdf6;border:1px solid #ecdcae;border-radius:8px;padding:12px 14px;margin:10px 0}
.ap-main .upttl{font-family:var(--display);font-weight:700;font-size:14px;margin-bottom:4px}
.ap-main details.hand{margin:12px 0;border:1px dashed var(--line-strong);border-radius:8px;padding:10px 13px}
.ap-main details.hand summary{cursor:pointer;font-family:var(--display);font-weight:600;font-size:13px}
.ap-main input[type=file]{font-size:12.5px}
.ap-main .metric.hero .l{color:#9fb0c0}.ap-main .metric.hero .v{color:var(--accent)}
.ap-main .bars{display:flex;flex-direction:column;gap:9px}
.ap-main .bar{display:grid;grid-template-columns:180px 1fr auto;align-items:center;gap:10px;font-size:12.7px}
.ap-main .bar .lab{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ap-main .bar .track{background:#eef2f6;border-radius:6px;height:16px;overflow:hidden}
.ap-main .bar .track{display:block}
.ap-main .bar .fill{display:block;background:var(--accent);height:100%;border-radius:6px;min-width:2px}
.ap-main .bar .fill.rej{background:var(--fail)}
.ap-main .bar .val{font-family:var(--mono);color:var(--slate);font-size:12px;white-space:nowrap}
.ap-main .grand{background:var(--ink);color:#eaf0f6;border-radius:12px;padding:16px 20px;display:flex;align-items:center;justify-content:space-between;gap:18px;margin:6px 0 20px;flex-wrap:wrap;border-bottom:3px solid var(--accent)}
.ap-main .grand .lbl{font-family:var(--display);font-size:12px;letter-spacing:.1em;text-transform:uppercase;color:#9fb0c0}
.ap-main .grand .big{font-family:var(--mono);font-weight:600;font-size:29px;color:var(--accent)}
.ap-main .grand .sub{font-size:12px;color:#9fb0c0;text-align:right}
.ap-main .disclaim{font-size:11.5px;color:var(--slate-dim);margin-top:24px;border-top:1px dashed var(--line-strong);padding-top:12px;max-width:820px}
.ap-main .errbox{background:var(--fail-bg);border:1px solid #e6b8ad;color:#7a281a;border-radius:8px;padding:10px 13px;font-size:12.5px;margin-bottom:14px;cursor:pointer}
.ap-main .okbox{background:var(--pass-bg);border:1px solid #b9e0cb;color:#0f5c38;border-radius:8px;padding:10px 13px;font-size:12.5px;margin-bottom:14px}
@media (max-width:820px){.ap-main .frm{grid-template-columns:1fr}.ap-top .cycle{margin-left:0}}
`
