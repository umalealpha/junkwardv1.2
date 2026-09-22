'use client'

/**
 * Graphite Aware — executive NL Q&A + document checks over Graphite V2
 * (read-only). Whitelist: CEO, COO, CFO, pganesharajah, Finance Manager,
 * Claims Manager, Paul Beka.
 * Modes: Ask (chat, +attachments) · Claim payable? · KYC / ID check.
 * iPhone-grade: springs, depth, staged progress bar with ETA.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { apiFetch, getToken } from '@/lib/api'

const BASE_URL = process.env.NEXT_PUBLIC_API_BASE ?? ''
const API = `${BASE_URL}/api/v1`

async function authHeader(): Promise<Record<string, string>> {
  const h: Record<string, string> = {}
  try {
    const { acquireApiToken, SSO_API_CALLS_READY } = await import('@/auth/msal')
    if (SSO_API_CALLS_READY) {
      const b = await acquireApiToken()
      if (b) h['Authorization'] = `Bearer ${b}`
    }
  } catch { /* fall through */ }
  if (!h['Authorization']) { const t = getToken(); if (t) h['Authorization'] = `Token ${t}` }
  return h
}

type Mode = 'chat' | 'claim_payability' | 'kyc' | 'broker_analysis' | 'broker_scorecard' | 'top_dom' | 'claims_registry'
interface Round { sql?: string; rows?: number; ms?: number; error?: string; file?: string; chars?: number }
interface FileMeta { filename: string; chars: number; confidence: number; tier: string; escalate: boolean }
interface ReportFile { key: string; label: string; date: string; filename: string; size: number }
interface Msg {
  role: 'user' | 'aware'
  text: string
  kind?: Mode
  rounds?: Round[]
  files?: FileMeta[]
  result?: Record<string, unknown>
  ms?: number
  pending?: boolean
}

const MODES: { key: Mode; label: string; hint: string }[] = [
  { key: 'chat', label: 'Ask', hint: 'Ask anything about policies, claims, premiums…' },
  { key: 'claim_payability', label: 'Claim payable?', hint: 'Attach the assessment report / paste the email — I check if it’s payable.' },
  { key: 'kyc', label: 'KYC / ID check', hint: 'Attach the customer’s ID — I verify it against the record and flag fakes.' },
  { key: 'broker_analysis', label: 'Broker analysis', hint: 'Production book by broker — premium, policies, new business & claim counts. Press send to run it.' },
  { key: 'broker_scorecard', label: 'Broker scorecard', hint: 'Which brokers make us money — premium in vs claims paid, with a net money column, plus the top 20 open claims coming for payment. Press send to run it.' },
  { key: 'top_dom', label: 'Top 50 Dom', hint: 'The 50 biggest active domestic policies by annual premium. Press send to run it.' },
  { key: 'claims_registry', label: 'Claims registry', hint: 'Open & pending claims — paid, reserve, status & broker. Press send to run it.' },
]

// Report modes take no typed input — a Generate button / send runs them.
const REPORT_MODES: Mode[] = ['broker_analysis', 'broker_scorecard', 'top_dom', 'claims_registry']
// mode -> weekly-Excel report key (matches the backend file prefixes)
const MODE_KEY: Partial<Record<Mode, string>> = {
  broker_analysis: 'broker-analysis', top_dom: 'top-50-dom', claims_registry: 'claims-registry',
}

async function downloadReport(filename: string) {
  const res = await fetch(`${API}/aware/reports/download/?f=${encodeURIComponent(filename)}`,
    { headers: await authHeader() })
  if (!res.ok) return
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url; a.download = filename; a.click()
  URL.revokeObjectURL(url)
}

function ReportDownloads({ reportKey, reports }: { reportKey: string; reports: ReportFile[] }) {
  const mine = reports.filter(r => r.key === reportKey).slice(0, 6)
  return (
    <div className="aw-dl">
      <div className="aw-dl-head">Excel — refreshed automatically every Sunday</div>
      {mine.length ? (
        <div className="aw-dl-list">
          {mine.map(f => (
            <button key={f.filename} className="aw-dl-btn" onClick={() => downloadReport(f.filename)}>
              ⬇ {f.date}
            </button>
          ))}
        </div>
      ) : <div className="aw-dl-none">The first weekly file saves this Sunday.</div>}
    </div>
  )
}

const STAGES: Record<Mode, string[]> = {
  chat: ['Reading your question…', 'Pulling from Graphite…', 'Writing the answer…'],
  claim_payability: ['Reading the report…', 'Finding the policy in Graphite…', 'Checking cover, premiums & excess…', 'Deciding payability…'],
  kyc: ['Reading the ID…', 'Checking KYC status…', 'Matching the ID number on record…', 'Running the fraud check…'],
  broker_analysis: ['Reading the book…', 'Splitting direct vs brokers…', 'Ranking by premium…'],
  broker_scorecard: ['Reading the book…', 'Merging split brokers…', 'Weighing premium vs claims…', 'Pulling the biggest open claims…'],
  top_dom: ['Reading domestic policies…', 'Ranking by premium…', 'Masking personal names…'],
  claims_registry: ['Reading open claims…', 'Totting up reserves…', 'Ranking by exposure…'],
}

// Expected run time per mode (seconds). KYC + payability run the heavy LOCAL
// DeepSeek model and can take up to ~10 minutes (CFO 2026-07-19: keep the deep
// model, show a clear 10-minute progress that never looks frozen). Chat and the
// prebuilt reports are quick. The bar only reaches 100% when the real answer
// returns, so an over-estimate simply finishes early — it never stalls.
const ETA_SECONDS: Record<Mode, number> = {
  chat: 20,
  claim_payability: 600,
  kyc: 600,
  broker_analysis: 30,
  broker_scorecard: 35,
  top_dom: 30,
  claims_registry: 30,
}

function fmtEta(s: number): string {
  if (s >= 90) return `~${Math.ceil(s / 60)} min`
  if (s >= 45) return '~1 min'
  return `~${s}s`
}

function mdLite(src: string): string {
  const esc = src.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  const lines = esc.split('\n'); const out: string[] = []; let inList = false
  for (const ln of lines) {
    const li = ln.match(/^\s*[-*•]\s+(.*)/)
    if (li) { if (!inList) { out.push('<ul>'); inList = true } out.push(`<li>${li[1]}</li>`); continue }
    if (inList) { out.push('</ul>'); inList = false }
    if (/^\s*#{1,3}\s+/.test(ln)) out.push(`<div class="aw-h">${ln.replace(/^\s*#{1,3}\s+/, '')}</div>`)
    else if (ln.trim() === '') out.push('<div class="aw-gap"></div>')
    else out.push(`<p>${ln}</p>`)
  }
  if (inList) out.push('</ul>')
  return out.join('').replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/`([^`]+)`/g, '<code>$1</code>')
}

export default function AlphaAwarePage() {
  const [allowed, setAllowed] = useState<boolean | null>(null)
  const [reports, setReports] = useState<ReportFile[]>([])
  const [mode, setMode] = useState<Mode>('chat')
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [q, setQ] = useState('')
  const [policyNo, setPolicyNo] = useState('')
  const [email, setEmail] = useState('')
  const [showEmail, setShowEmail] = useState(false)
  const [files, setFiles] = useState<File[]>([])
  const [busy, setBusy] = useState(false)
  const [stage, setStage] = useState('')
  const [pct, setPct] = useState(0)
  const [eta, setEta] = useState(0)
  const scroller = useRef<HTMLDivElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const timer = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    apiFetch<{ allowed: boolean }>('/aware/access/').then(r => setAllowed(!!r.allowed)).catch(() => setAllowed(false))
    apiFetch<{ reports: ReportFile[] }>('/aware/reports/').then(r => setReports(r.reports || [])).catch(() => {})
  }, [])
  useEffect(() => { scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: 'smooth' }) }, [msgs, pct])

  const runProgress = useCallback((m: Mode) => {
    const stages = STAGES[m]
    const totalEta = ETA_SECONDS[m] ?? 30
    const start = Date.now()
    setPct(3); setEta(totalEta); setStage(stages[0])
    timer.current = setInterval(() => {
      const elapsed = (Date.now() - start) / 1000
      const frac = elapsed / totalEta
      // Pace toward ~95% across the estimate, then creep on — never freeze and
      // never reach 100% before the real answer lands. A fast (cloud) run just
      // returns early and stopProgress() snaps straight to 100%.
      const target = frac < 1 ? 95 * frac : 95 + Math.min(4, (elapsed - totalEta) / 90)
      setPct(p => Math.max(p, Math.min(99, target)))
      setEta(Math.max(0, Math.round(totalEta - elapsed)))
      const idx = Math.min(stages.length - 1, Math.floor(frac * stages.length))
      setStage(frac >= 1 ? 'Still working — the local AI is being thorough…' : stages[idx])
    }, 1000)
  }, [])
  const stopProgress = useCallback((ok: boolean) => {
    if (timer.current) { clearInterval(timer.current); timer.current = null }
    setPct(ok ? 100 : 0); setEta(0)
    setTimeout(() => { setPct(0); setStage('') }, 700)
  }, [])

  const send = useCallback(async (preset?: string) => {
    if (busy) return
    const question = (preset ?? q).trim()
    const noInputMode = REPORT_MODES.includes(mode)
    const hasInput = noInputMode || question || files.length || email.trim() || (mode !== 'chat' && policyNo.trim())
    if (!hasInput) return
    const userLine = question ||
      (mode === 'claim_payability' ? `Check if ${policyNo || 'this claim'} is payable` :
       mode === 'kyc' ? `KYC / ID check ${policyNo || ''}`.trim() :
       mode === 'broker_analysis' ? 'Broker analysis — production book' :
       mode === 'broker_scorecard' ? 'Broker scorecard — premium vs claims' :
       mode === 'top_dom' ? 'Top 50 domestic policies' :
       mode === 'claims_registry' ? 'Claims registry — open & pending' :
       'Review the attached document(s)') +
      (files.length ? `  ·  ${files.length} file${files.length > 1 ? 's' : ''}` : '')
    setBusy(true); setQ(''); runProgress(mode)
    setMsgs(m => [...m, { role: 'user', text: userLine }, { role: 'aware', text: '', kind: mode, pending: true }])
    try {
      const fd = new FormData()
      fd.append('mode', mode)
      if (question) fd.append('question', question)
      if (policyNo.trim()) fd.append('policy_no', policyNo.trim())
      if (email.trim()) fd.append('email', email.trim())
      files.forEach(f => fd.append('files', f))
      const res = await fetch(`${API}/aware/ask/`, { method: 'POST', headers: await authHeader(), body: fd })
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `Server error (${res.status})`)
      const data = await res.json()
      stopProgress(true)
      setMsgs(m => {
        const c = [...m]
        c[c.length - 1] = {
          role: 'aware', kind: mode, text: data.answer || '',
          rounds: data.rounds, files: data.files, result: data.result, ms: data.duration_ms,
        }
        return c
      })
      setFiles([]); setEmail(''); setShowEmail(false)
    } catch (e: unknown) {
      stopProgress(false)
      const msg = e instanceof Error ? e.message : 'Something went wrong.'
      setMsgs(m => { const c = [...m]; c[c.length - 1] = { role: 'aware', text: `⚠️ ${msg}` }; return c })
    } finally { setBusy(false) }
  }, [q, files, email, policyNo, mode, busy, runProgress, stopProgress])

  if (allowed === null) return <div className="aw-shell aw-center"><div className="aw-orb" /><style jsx global>{awCss}</style></div>
  if (!allowed) return (
    <div className="aw-shell aw-center">
      <div className="aw-locked"><div className="aw-lock-badge">🔒</div><h1>Graphite Aware</h1>
        <p>This assistant is limited to the executive team.<br />Speak to the CFO&rsquo;s office for access.</p></div>
      <style jsx global>{awCss}</style>
    </div>
  )

  const empty = msgs.length === 0
  const modeHint = MODES.find(m => m.key === mode)!.hint

  return (
    <div className="aw-shell">
      <div className="aw-bg" />
      <header className="aw-head">
        <div className="aw-mark"><span className="aw-mark-dot" /><span className="aw-mark-name">Graphite <b>Aware</b></span></div>
        <span className="aw-ro">READ-ONLY · GRAPHITE V2</span>
      </header>

      {/* mode pills */}
      <div className="aw-modes">
        {MODES.map(m => (
          <button key={m.key} className={`aw-pill ${mode === m.key ? 'on' : ''}`}
            onClick={() => { setMode(m.key); if (m.key === 'chat') setShowEmail(false) }}>{m.label}</button>
        ))}
      </div>

      <div className="aw-scroll" ref={scroller}>
        {empty && (
          <div className="aw-hero">
            <div className="aw-orb" />
            <h1>{mode === 'chat' ? 'Ask the book of business.' : mode === 'claim_payability' ? 'Is this claim payable?' : mode === 'kyc' ? 'Verify an ID.' : mode === 'broker_analysis' ? 'Broker book by intermediary.' : mode === 'broker_scorecard' ? 'Which brokers make us money.' : mode === 'top_dom' ? 'Top 50 domestic policies.' : 'Open & pending claims.'}</h1>
            <p>{modeHint} Personal details stay masked; everything is read-only.</p>
            {REPORT_MODES.includes(mode) && <button className="aw-run" onClick={() => send()} disabled={busy}>Generate report</button>}
          </div>
        )}
        <div className="aw-msgs">
          {msgs.map((m, i) => m.role === 'user' ? (
            <div key={i} className="aw-row aw-row-user"><div className="aw-bubble aw-user">{m.text}</div></div>
          ) : (
            <div key={i} className="aw-row"><div className="aw-bubble aw-answer">
              {m.pending ? (
                <div className="aw-prog">
                  <div className="aw-prog-top"><span className="aw-prog-stage">{stage || 'Working…'}</span>
                    <span className="aw-prog-eta">{pct > 0 ? `${Math.round(pct)}%` : ''}{eta > 0 ? ` · ${fmtEta(eta)} left` : pct > 0 ? ' · almost there…' : ''}</span></div>
                  <div className="aw-prog-track"><div className="aw-prog-fill" style={{ width: `${pct}%` }} /></div>
                </div>
              ) : m.result ? <ResultCard kind={m.kind!} r={m.result} files={m.files} reports={reports} /> : (
                <>
                  <div className="aw-md" dangerouslySetInnerHTML={{ __html: mdLite(m.text) }} />
                  {m.files?.length ? <div className="aw-files-note">📎 {m.files.map(f => f.filename).join(', ')}</div> : null}
                  {(m.rounds?.length || 0) > 0 && (
                    <details className="aw-trace"><summary>{m.rounds!.length} step{m.rounds!.length === 1 ? '' : 's'}{m.ms ? ` · ${(m.ms / 1000).toFixed(1)}s` : ''}</summary>
                      {m.rounds!.map((r, j) => <div key={j} className="aw-trace-row"><code>{r.sql || r.file}</code><span>{r.error ? `error: ${r.error}` : r.rows != null ? `${r.rows} rows · ${r.ms}ms` : `${r.chars} chars`}</span></div>)}
                    </details>)}
                </>
              )}
            </div></div>
          ))}
        </div>
      </div>

      {/* composer */}
      <div className="aw-composer-wrap">
        {files.length > 0 && (
          <div className="aw-chips-row">
            {files.map((f, i) => <span key={i} className="aw-filechip">📄 {f.name}<button onClick={() => setFiles(fs => fs.filter((_, k) => k !== i))}>×</button></span>)}
          </div>
        )}
        {showEmail && (
          <textarea className="aw-email" placeholder="Paste the email here…" value={email} onChange={e => setEmail(e.target.value)} rows={3} />
        )}
        {mode !== 'chat' && !REPORT_MODES.includes(mode) && (
          <input className="aw-polinput" placeholder="Policy number (optional — I'll try to find it)" value={policyNo} onChange={e => setPolicyNo(e.target.value)} />
        )}
        <div className="aw-composer">
          <button className="aw-attach" title="Attach files" onClick={() => fileRef.current?.click()}>📎</button>
          <input ref={fileRef} type="file" multiple hidden accept=".pdf,.xlsx,.xls,.docx,.doc,.csv,.png,.jpg,.jpeg"
            onChange={e => { setFiles(f => [...f, ...Array.from(e.target.files || [])].slice(0, 5)); if (fileRef.current) fileRef.current.value = '' }} />
          <button className={`aw-attach ${showEmail ? 'on' : ''}`} title="Paste an email" onClick={() => setShowEmail(s => !s)}>✉️</button>
          <textarea value={q} rows={1} placeholder={modeHint} disabled={busy}
            onChange={e => setQ(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }} />
          <button className="aw-send" onClick={() => send()} disabled={busy} aria-label="Send">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M12 19V5M5 12l7-7 7 7" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" /></svg>
          </button>
        </div>
        <div className="aw-foot">Read-only from the live Graphite database. Personal details masked automatically.</div>
      </div>
      <style jsx global>{awCss}</style>
    </div>
  )
}

function Badge({ tone, children }: { tone: string; children: React.ReactNode }) {
  return <span className={`aw-badge aw-badge-${tone}`}>{children}</span>
}

function ResultCard({ kind, r, files, reports = [] }: { kind: Mode; r: Record<string, unknown>; files?: FileMeta[]; reports?: ReportFile[] }) {
  const filesNote = files?.length ? <div className="aw-files-note">📎 {files.map(f => f.filename).join(', ')}</div> : null
  const dl = MODE_KEY[kind] ? <ReportDownloads reportKey={MODE_KEY[kind]!} reports={reports} /> : null
  if (kind === 'claims_registry') {
    const rows = (r.rows as Record<string, number | string>[]) || []
    const notes = (r.notes as string[]) || []
    const by = (r.by_status as Record<string, number>) || {}
    const P = (n: number) => 'P' + Math.round(Number(n) || 0).toLocaleString('en-US')
    return (
      <div className="aw-brk">
        <div className="aw-brk-kpis">
          <div><span>Open &amp; pending claims</span><b>{Number(r.open_count) || 0}</b></div>
          <div><span>Outstanding reserve</span><b>{P(r.total_outstanding as number)}</b></div>
          <div><span>Paid to date (on open)</span><b>{P(r.total_paid as number)}</b></div>
          <div><span>By status</span><b style={{ fontSize: '13px', fontWeight: 600 }}>{Object.entries(by).map(([k, v]) => `${k} ${v}`).join(' · ') || '—'}</b></div>
        </div>
        <div className="aw-brk-tblwrap">
          <table className="aw-brk-tbl">
            <thead><tr><th className="l">Claim</th><th className="l">Policy</th><th className="l">Status</th><th>Reported</th><th className="l">Broker</th><th>Paid</th><th>Reserve</th></tr></thead>
            <tbody>
              {rows.map((b, i) => (
                <tr key={i}>
                  <td className="l">{b.claim}</td>
                  <td className="l">{b.policy}</td>
                  <td className="l">{b.status}</td>
                  <td>{b.reported}</td>
                  <td className="l">{b.broker}</td>
                  <td>{Number(b.paid) ? P(b.paid as number) : '—'}</td>
                  <td>{Number(b.outstanding) ? P(b.outstanding as number) : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {notes.length ? <ul className="aw-brk-notes">{notes.map((n, i) => <li key={i}>{n}</li>)}</ul> : null}
        <div className="aw-files-note">Snapshot {String(r.generated_at || '')} · read-only from Graphite · safe fields only, no personal data.</div>
        {dl}
        {filesNote}
      </div>
    )
  }
  if (kind === 'top_dom') {
    const rows = (r.rows as Record<string, number | string>[]) || []
    const notes = (r.notes as string[]) || []
    const P = (n: number) => 'P' + Math.round(Number(n) || 0).toLocaleString('en-US')
    return (
      <div className="aw-brk">
        <div className="aw-brk-kpis">
          <div><span>Domestic policies shown</span><b>{Number(r.count) || rows.length}</b></div>
          <div><span>Their annual premium</span><b>{P(r.top50_gwp as number)}</b></div>
        </div>
        <div className="aw-brk-tblwrap">
          <table className="aw-brk-tbl">
            <thead><tr><th>#</th><th className="l">Policy</th><th className="l">Policyholder</th><th className="l">Broker</th><th>Annual premium</th><th>Pays</th><th>Since</th></tr></thead>
            <tbody>
              {rows.map((b, i) => (
                <tr key={i}>
                  <td>{b.rank}</td>
                  <td className="l">{b.policy}</td>
                  <td className="l">{b.customer}</td>
                  <td className="l">{b.broker}</td>
                  <td>{P(b.annual_premium as number)}</td>
                  <td>{b.freq}</td>
                  <td>{b.since}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {notes.length ? <ul className="aw-brk-notes">{notes.map((n, i) => <li key={i}>{n}</li>)}</ul> : null}
        <div className="aw-files-note">Snapshot {String(r.generated_at || '')} · read-only from Graphite · individual names shown as initials.</div>
        {dl}
        {filesNote}
      </div>
    )
  }
  if (kind === 'broker_scorecard') {
    const t = (r.totals as Record<string, number>) || {}
    const ch = (r.channel as Record<string, Record<string, number>>) || {}
    const brokers = (r.brokers as Record<string, number | string | boolean | null>[]) || []
    const claims = (r.top_claims as Record<string, number | string>[]) || []
    const notes = (r.notes as string[]) || []
    const P = (n: number) => 'P' + Math.round(Number(n) || 0).toLocaleString('en-US')
    const brk = ch.broker || {}
    return (
      <div className="aw-brk">
        <div className="aw-brk-kpis">
          <div><span>External broker book</span><b>{P(brk.gwp)} · {brk.pct}%</b></div>
          <div><span>Claims paid 12m (all channels)</span><b>{P(t.claims_paid_12m)}</b></div>
          <div><span>Outstanding reserve</span><b>{P(t.outstanding)}</b></div>
          <div><span>Brokers costing money</span><b style={{ color: Number(r.loss_makers) ? '#DC2626' : '#059669' }}>{Number(r.loss_makers) || 0} of {brokers.length}</b></div>
        </div>
        <div className="aw-brk-tblwrap">
          <table className="aw-brk-tbl">
            <thead><tr><th className="l">Broker</th><th>Policies</th><th>Premium (annual)</th><th>% book</th><th>Claims paid 12m</th><th>Open reserve</th><th>Net</th></tr></thead>
            <tbody>
              {brokers.map((b, i) => (
                <tr key={i}>
                  <td className="l">{b.broker as string}</td>
                  <td>{Number(b.active_pol).toLocaleString()}</td>
                  <td>{P(b.inforce_gwp as number)}</td>
                  <td>{b.pct as number}%</td>
                  <td>{Number(b.net_paid_12m) ? P(b.net_paid_12m as number) : '—'}</td>
                  <td>{Number(b.outstanding) ? P(b.outstanding as number) : '—'}</td>
                  <td style={{ fontWeight: 700, color: Number(b.net) < 0 ? '#DC2626' : '#059669' }}>
                    {Number(b.net) < 0 ? '−' : '+'}{P(Math.abs(Number(b.net) || 0))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Top-20 open claims coming for payment — Finance readiness box */}
        <div className="aw-brk-tblwrap" style={{ marginTop: 18 }}>
          <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8, marginBottom: 8 }}>
            <b style={{ color: '#0D1B2A', fontSize: '14px' }}>Top {claims.length} open claims coming for payment</b>
            <span style={{ color: '#6B7280', fontSize: '12px' }}>{Number(r.open_claims_count) || 0} open in total · P{Math.round(Number(r.open_claims_total_outstanding) || 0).toLocaleString('en-US')} outstanding</span>
          </div>
          <table className="aw-brk-tbl">
            <thead><tr><th className="l">Claim</th><th className="l">Status</th><th className="l">Broker</th><th>Outstanding</th></tr></thead>
            <tbody>
              {claims.map((c, i) => (
                <tr key={i}>
                  <td className="l">{c.claim}</td>
                  <td className="l">{c.status}</td>
                  <td className="l">{c.broker}</td>
                  <td style={{ fontWeight: 700 }}>{Number(c.outstanding) ? P(c.outstanding as number) : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {notes.length ? <ul className="aw-brk-notes">{notes.map((n, i) => <li key={i}>{n}</li>)}</ul> : null}
        <div className="aw-files-note">Snapshot {String(r.generated_at || '')} · read-only from Graphite · indicative management view, not the audited loss ratio.</div>
        {dl}
        {filesNote}
      </div>
    )
  }
  if (kind === 'broker_analysis') {
    const t = (r.totals as Record<string, number>) || {}
    const ch = (r.channel as Record<string, Record<string, number>>) || {}
    const brokers = (r.brokers as Record<string, number | string>[]) || []
    const notes = (r.notes as string[]) || []
    const P = (n: number) => 'P' + Math.round(Number(n) || 0).toLocaleString('en-US')
    const dir = ch.direct || {}, brk = ch.broker || {}
    return (
      <div className="aw-brk">
        <div className="aw-brk-kpis">
          <div><span>In-force book</span><b>{P(t.inforce_gwp)}</b></div>
          <div><span>Active policies</span><b>{(t.active_pol || 0).toLocaleString()}</b></div>
          <div><span>External brokers</span><b>{P(brk.gwp)} · {brk.pct}%</b></div>
          <div><span>Direct &amp; retail</span><b>{P(dir.gwp)} · {dir.pct}%</b></div>
          <div><span>Claims paid (12m)</span><b>{P(t.claims_paid_12m)}</b></div>
          <div><span>Outstanding reserve</span><b>{P(t.outstanding)}</b></div>
        </div>
        <div className="aw-brk-bar"><i className="d" style={{ width: `${dir.pct || 0}%` }} /><i className="b" style={{ width: `${brk.pct || 0}%` }} /></div>
        <div className="aw-brk-legend"><span><i className="d" />Direct &amp; retail (in-house)</span><span><i className="b" />External brokers</span></div>
        <div className="aw-brk-tblwrap">
          <table className="aw-brk-tbl">
            <thead><tr><th className="l">Broker</th><th>Policies</th><th>In-force GWP</th><th>% book</th><th>New biz 12m</th><th>Claims paid 12m</th><th>O/S reserve</th></tr></thead>
            <tbody>
              {brokers.map((b, i) => (
                <tr key={i}>
                  <td className="l">{b.broker}</td>
                  <td>{Number(b.active_pol).toLocaleString()}</td>
                  <td>{P(b.inforce_gwp as number)}</td>
                  <td>{b.pct}%</td>
                  <td>{Number(b.nb_cnt) ? `${b.nb_cnt} · ${P(b.nb_gwp as number)}` : '—'}</td>
                  <td>{Number(b.net_paid_12m) ? P(b.net_paid_12m as number) : '—'}</td>
                  <td>{Number(b.outstanding) ? P(b.outstanding as number) : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {notes.length ? <ul className="aw-brk-notes">{notes.map((n, i) => <li key={i}>{n}</li>)}</ul> : null}
        <div className="aw-files-note">Snapshot {String(r.generated_at || '')} · read-only from Graphite · not the audited / Management-Accounts GWP.</div>
        {dl}
        {filesNote}
      </div>
    )
  }
  if (kind === 'claim_payability') {
    const v = String(r.verdict || 'refer')
    const tone = v === 'payable' ? 'green' : v === 'not_payable' ? 'red' : v === 'refer' ? 'amber' : 'grey'
    const label = v === 'payable' ? 'PAYABLE' : v === 'not_payable' ? 'NOT PAYABLE' : v === 'refer' ? 'REFER' : 'INDETERMINATE'
    const reasons = (r.reasons as string[]) || []
    return (
      <div>
        <div className="aw-verdict"><Badge tone={tone}>{label}</Badge>{r.confidence ? <span className="aw-conf">{String(r.confidence)} confidence</span> : null}</div>
        <div className="aw-md" dangerouslySetInnerHTML={{ __html: mdLite(String(r.easy_summary || '')) }} />
        {reasons.length ? <ul className="aw-reasons">{reasons.map((x, i) => <li key={i}>{x}</li>)}</ul> : null}
        {filesNote}
      </div>
    )
  }
  // kyc
  const overall = String(r.overall || 'manual_review')
  const tone = overall === 'pass' ? 'green' : overall === 'fail' ? 'red' : overall === 'pass_face_unchecked' ? 'amber' : 'amber'
  const label = overall === 'pass' ? 'VERIFIED' : overall === 'fail' ? 'MISMATCH — FRAUD RISK' : overall === 'pass_face_unchecked' ? 'ID OK · FACE NOT CHECKED' : 'MANUAL REVIEW'
  const chip = (ok: unknown, t: string, f: string) => ok === true ? <Badge tone="green">{t}</Badge> : ok === false ? <Badge tone="red">{f}</Badge> : <Badge tone="grey">—</Badge>
  const face = (r.face_check as Record<string, unknown>) || {}
  return (
    <div>
      <div className="aw-verdict"><Badge tone={tone}>{label}</Badge></div>
      <div className="aw-md" dangerouslySetInnerHTML={{ __html: mdLite(String(r.easy_summary || '')) }} />
      <div className="aw-kyc-grid">
        <div><span>KYC compliant</span>{chip(r.policy_kyc_compliant, 'Yes', 'No')}</div>
        <div><span>ID number matches record</span>{chip(r.id_number_match, 'Match', 'No match')}</div>
        <div><span>Face vs record</span>{face.available ? chip(face.match, 'Match', 'No match') : <Badge tone="grey">manual</Badge>}</div>
      </div>
      {filesNote}
    </div>
  )
}

const awCss = `
/* Professional-theme re-skin (CFO 2026-08-22): Aware now uses omni's design
   tokens so it matches the active theme — light + slate-blue + Inter under the
   default professional theme — instead of its old bespoke dark navy/orange. All
   colours flow from --ad-* vars (with professional-value fallbacks); layout and
   class names are unchanged. */
.aw-shell{position:relative;display:flex;flex-direction:column;height:calc(100vh - 0px);background:var(--ad-bg-page,#F7F8FA);color:var(--ad-text-heading,#1A1D21);overflow:hidden;font-family:var(--font-sans,'Inter',system-ui,-apple-system,'Segoe UI',Roboto,sans-serif)}
.aw-center{align-items:center;justify-content:center}
.aw-bg{position:absolute;inset:0;pointer-events:none;background:radial-gradient(900px 500px at 85% -10%, var(--ad-orange-50,#EEF2FC), transparent 60%)}
.aw-head{position:relative;z-index:2;display:flex;align-items:center;justify-content:space-between;padding:16px 24px;border-bottom:1px solid var(--ad-border,#ECEEF1);background:var(--ad-bg-sidebar,#FFFFFF)}
.aw-mark{display:flex;align-items:center;gap:10px}
.aw-mark-dot{width:10px;height:10px;border-radius:999px;background:var(--ad-orange,#4F6BED);box-shadow:0 0 10px rgba(79,107,237,.45);animation:aw-pulse 2.6s ease-in-out infinite}
.aw-mark-name{font-size:17px;color:var(--ad-text-heading,#1A1D21)}.aw-mark-name b{color:var(--ad-orange,#4F6BED);font-weight:700}
.aw-ro{font-size:10.5px;letter-spacing:.14em;color:#6B7280;border:1px solid var(--ad-border,#ECEEF1);border-radius:999px;padding:5px 10px}
.aw-modes{position:relative;z-index:2;display:flex;gap:8px;padding:12px 24px 4px;flex-wrap:wrap}
.aw-pill{border:1px solid var(--ad-border,#ECEEF1);background:var(--ad-bg-sidebar,#FFFFFF);color:#4B5563;border-radius:999px;padding:7px 16px;font-size:13px;cursor:pointer;transition:all .2s ease}
.aw-pill:hover{border-color:var(--ad-orange,#4F6BED);color:var(--ad-orange-dark,#3F58CC)}
.aw-pill.on{background:var(--ad-orange,#4F6BED);color:#fff;font-weight:700;border-color:transparent;box-shadow:0 4px 12px rgba(79,107,237,.22)}
.aw-scroll{position:relative;z-index:1;flex:1;overflow-y:auto;padding:0 16px}
.aw-hero{max-width:640px;margin:7vh auto 0;text-align:center;animation:aw-rise .7s cubic-bezier(.16,1,.3,1) both}
.aw-orb{width:84px;height:84px;margin:0 auto 20px;border-radius:999px;background:radial-gradient(circle at 32% 28%, #A9B9F7, #4F6BED 60%, #3F58CC);box-shadow:0 14px 40px rgba(79,107,237,.28);animation:aw-float 5.5s ease-in-out infinite}
.aw-hero h1{font-size:clamp(24px,4vw,34px);font-weight:700;color:var(--ad-text-heading,#1A1D21);margin:0 0 10px}
.aw-hero p{color:#6B7280;font-size:14.5px;line-height:1.6;margin:0 auto;max-width:460px}
.aw-msgs{max-width:760px;margin:0 auto;padding:18px 0 8px;display:flex;flex-direction:column;gap:14px}
.aw-row{display:flex}.aw-row-user{justify-content:flex-end}
.aw-bubble{max-width:88%;border-radius:20px;padding:12px 16px;font-size:14.5px;line-height:1.6;animation:aw-pop .45s cubic-bezier(.16,1,.3,1) both}
.aw-user{background:var(--ad-orange,#4F6BED);color:#fff;font-weight:600;border-bottom-right-radius:6px;box-shadow:0 4px 14px rgba(79,107,237,.2)}
.aw-answer{background:var(--ad-bg-sidebar,#FFFFFF);border:1px solid var(--ad-border,#ECEEF1);border-bottom-left-radius:6px;color:var(--ad-text-heading,#1A1D21);min-width:280px;box-shadow:0 1px 3px rgba(16,24,40,.05)}
.aw-md p{margin:0 0 8px}.aw-md p:last-child{margin-bottom:0}.aw-md ul{margin:4px 0 10px 18px;padding:0}.aw-md li{margin:3px 0}
.aw-md strong{color:var(--ad-orange-dark,#3F58CC);font-weight:700}.aw-md code{background:#F1F3F5;border-radius:6px;padding:1px 6px;font-size:12.5px;color:#3F58CC}
.aw-md .aw-h{font-weight:700;color:var(--ad-text-heading,#1A1D21);margin:10px 0 6px;font-size:15px}.aw-md .aw-gap{height:6px}
.aw-prog{padding:2px 0}
.aw-prog-top{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
.aw-prog-stage{font-size:13.5px;color:var(--ad-text-heading,#1A1D21)}.aw-prog-eta{font-size:12px;color:#6B7280}
.aw-prog-track{height:7px;border-radius:999px;background:#EEF0F3;overflow:hidden}
.aw-prog-fill{height:100%;border-radius:999px;background:var(--ad-orange,#4F6BED);transition:width .9s cubic-bezier(.16,1,.3,1)}
.aw-verdict{display:flex;align-items:center;gap:10px;margin-bottom:8px}
.aw-badge{display:inline-block;padding:5px 12px;border-radius:999px;font-size:12px;font-weight:800;letter-spacing:.04em}
.aw-badge-green{background:#DCFCE7;color:#166534}.aw-badge-red{background:#FEE2E2;color:#991B1B}
.aw-badge-amber{background:#FEF3C7;color:#92400E}.aw-badge-grey{background:#EEF0F3;color:#6B7280}
.aw-conf{font-size:12px;color:#6B7280}
.aw-reasons{margin:8px 0 4px 18px;padding:0}.aw-reasons li{margin:3px 0;font-size:13.5px;color:#4B5563}
.aw-kyc-grid{display:flex;flex-direction:column;gap:8px;margin-top:12px}
.aw-kyc-grid>div{display:flex;justify-content:space-between;align-items:center;background:#F7F8FA;border:1px solid var(--ad-border,#ECEEF1);border-radius:10px;padding:8px 12px}
.aw-kyc-grid span{font-size:13px;color:#4B5563}
.aw-files-note{margin-top:8px;font-size:12px;color:#6B7280}
.aw-trace{margin-top:10px;border-top:1px dashed var(--ad-border,#ECEEF1);padding-top:8px}
.aw-trace summary{cursor:pointer;font-size:12px;color:#6B7280}
.aw-trace-row{display:flex;flex-direction:column;gap:2px;margin:8px 0}
.aw-trace-row code{font-size:11.5px;color:#3F58CC;word-break:break-all;background:#F1F3F5;border-radius:8px;padding:6px 8px}
.aw-trace-row span{font-size:11px;color:#8B95A6}
.aw-composer-wrap{position:relative;z-index:2;padding:8px 16px 16px}
.aw-chips-row{max-width:760px;margin:0 auto 8px;display:flex;flex-wrap:wrap;gap:6px}
.aw-filechip{display:inline-flex;align-items:center;gap:6px;background:#F7F8FA;border:1px solid var(--ad-border,#ECEEF1);border-radius:999px;padding:5px 10px;font-size:12.5px;color:#4B5563}
.aw-filechip button{background:none;border:0;color:#8B95A6;cursor:pointer;font-size:15px;line-height:1}
.aw-email,.aw-polinput{max-width:760px;margin:0 auto 8px;display:block;width:100%;background:#FFFFFF;border:1px solid var(--ad-border-input,#D7DBE0);border-radius:14px;color:#1A1D21;font-size:14px;padding:10px 14px;font-family:inherit;resize:vertical}
.aw-polinput{border-radius:999px}
.aw-composer{max-width:760px;margin:0 auto;display:flex;align-items:flex-end;gap:8px;background:#FFFFFF;border:1px solid var(--ad-border-input,#D7DBE0);border-radius:24px;padding:8px 8px 8px 12px;transition:border-color .25s,box-shadow .25s}
.aw-composer:focus-within{border-color:var(--ad-orange,#4F6BED);box-shadow:0 0 0 4px rgba(79,107,237,.12),0 8px 24px rgba(16,24,40,.08)}
.aw-attach{width:36px;height:36px;border-radius:999px;border:0;background:#F1F3F5;cursor:pointer;font-size:16px;flex:0 0 auto;transition:background .2s}
.aw-attach:hover{background:var(--ad-orange-50,#EEF2FC)}.aw-attach.on{background:var(--ad-orange-100,#E0E7FB)}
.aw-composer textarea{flex:1;background:transparent;border:0;outline:0;resize:none;color:#1A1D21;font-size:15px;line-height:1.5;max-height:120px;font-family:inherit;padding-top:7px}
.aw-composer textarea::placeholder{color:#9CA3AF}
.aw-send{width:40px;height:40px;border-radius:999px;border:0;cursor:pointer;flex:0 0 auto;background:var(--ad-orange,#4F6BED);color:#fff;display:flex;align-items:center;justify-content:center;transition:transform .22s cubic-bezier(.16,1,.3,1);box-shadow:0 4px 12px rgba(79,107,237,.28)}
.aw-send:hover:not(:disabled){transform:translateY(-2px) scale(1.04)}.aw-send:disabled{opacity:.35;cursor:default}
.aw-foot{max-width:760px;margin:8px auto 0;text-align:center;font-size:11px;color:#8B95A6}
.aw-locked{text-align:center}.aw-lock-badge{font-size:42px;margin-bottom:14px}.aw-locked h1{color:var(--ad-text-heading,#1A1D21);margin:0 0 8px}.aw-locked p{color:#6B7280;font-size:14px;line-height:1.7}
.aw-run{margin-top:20px;border:0;border-radius:999px;padding:12px 26px;font-size:14px;font-weight:700;cursor:pointer;color:#fff;background:var(--ad-orange,#4F6BED);box-shadow:0 6px 16px rgba(79,107,237,.24);transition:transform .2s cubic-bezier(.16,1,.3,1)}
.aw-run:hover:not(:disabled){transform:translateY(-2px)}.aw-run:disabled{opacity:.4;cursor:default}
.aw-brk{min-width:300px}
.aw-brk-kpis{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin-bottom:14px}
.aw-brk-kpis>div{background:var(--ad-bg-sidebar,#FFFFFF);border:1px solid var(--ad-border,#ECEEF1);border-radius:12px;padding:10px 12px;display:flex;flex-direction:column;gap:3px}
.aw-brk-kpis span{font-size:11px;letter-spacing:.04em;text-transform:uppercase;color:#6B7280}
.aw-brk-kpis b{font-size:16.5px;color:var(--ad-text-heading,#1A1D21);font-weight:800;font-variant-numeric:tabular-nums}
.aw-brk-bar{display:flex;height:10px;border-radius:6px;overflow:hidden;background:#EEF0F3}
.aw-brk-bar i{display:block;height:100%}.aw-brk-bar i.d{background:#8B95A6}.aw-brk-bar i.b{background:var(--ad-orange,#4F6BED)}
.aw-brk-legend{display:flex;gap:18px;margin:8px 0 16px;font-size:12px;color:#6B7280}
.aw-brk-legend span{display:inline-flex;align-items:center;gap:6px}
.aw-brk-legend i{width:10px;height:10px;border-radius:3px}.aw-brk-legend i.d{background:#8B95A6}.aw-brk-legend i.b{background:var(--ad-orange,#4F6BED)}
.aw-brk-tblwrap{overflow-x:auto;border:1px solid var(--ad-border,#ECEEF1);border-radius:12px}
.aw-brk-tbl{border-collapse:collapse;width:100%;min-width:520px;font-size:13px}
.aw-brk-tbl th{text-align:right;font-size:10.5px;letter-spacing:.04em;text-transform:uppercase;color:#6B7280;padding:10px 12px;background:var(--ad-bg-table-head,#F7F8FA);white-space:nowrap;position:sticky;top:0}
.aw-brk-tbl th.l,.aw-brk-tbl td.l{text-align:left}
.aw-brk-tbl td{text-align:right;padding:9px 12px;border-top:1px solid var(--ad-border,#ECEEF1);color:#1A1D21;white-space:nowrap;font-variant-numeric:tabular-nums}
.aw-brk-tbl td.l{color:var(--ad-text-heading,#1A1D21);font-weight:600;white-space:normal;min-width:180px}
.aw-brk-tbl tbody tr:hover{background:var(--ad-bg-hover,#F7F8FA)}
.aw-brk-notes{margin:14px 0 4px;padding-left:18px;display:flex;flex-direction:column;gap:7px}
.aw-brk-notes li{font-size:12.5px;color:#6B7280;line-height:1.5}
.aw-dl{margin-top:14px;padding:12px 14px;background:var(--ad-orange-50,#EEF2FC);border:1px solid var(--ad-orange-100,#E0E7FB);border-radius:12px}
.aw-dl-head{font-size:12.5px;font-weight:700;color:var(--ad-orange-dark,#3F58CC);margin-bottom:9px}
.aw-dl-list{display:flex;flex-wrap:wrap;gap:8px}
.aw-dl-btn{display:inline-flex;align-items:center;gap:5px;font-size:12.5px;font-weight:600;color:#fff;background:var(--ad-orange,#4F6BED);border:0;border-radius:999px;padding:6px 13px;cursor:pointer;transition:transform .18s cubic-bezier(.16,1,.3,1)}
.aw-dl-btn:hover{transform:translateY(-1px)}
.aw-dl-none{font-size:12px;color:#6B7280}
@keyframes aw-rise{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}
@keyframes aw-pop{from{opacity:0;transform:translateY(8px) scale(.97)}to{opacity:1;transform:none}}
@keyframes aw-float{0%,100%{transform:translateY(0)}50%{transform:translateY(-9px)}}
@keyframes aw-pulse{0%,100%{opacity:1}50%{opacity:.45}}
`
