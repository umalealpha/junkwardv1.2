'use client'
/**
 * Data Protection dashboard (CFO directive 2026-07-19).
 * For the DPO (Oratile) + C-suite + HR + Finance Manager: live DPA audit
 * scorecard, proof the AI PII shield is working, and the cross-border data map.
 * Access is enforced server-side (core.dpa_dashboard); this page also gates the UI.
 */
import { useEffect, useState } from 'react'
import { apiFetch } from '@/lib/api'
import { ComplianceWorkbook } from './ComplianceWorkbook'
import { SelfUpdatingRopa } from './SelfUpdatingRopa'

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'

type Finding = { id: string; severity: string; title: string; status: string; note: string }
type DataRow = { purpose: string; processor: string; location: string; status: string; note: string }
interface Payload {
  summary: { total_findings: number; fixed: number; in_progress: number; open: number; pct_fixed: number; open_by_severity: Record<string, number> }
  findings: Finding[]
  ai_shield: { firewall_mode: string; protected: boolean; ai_calls_30d: number; avg_ms: number; by_engine: { engine: string; n: number }[] }
  data_map: DataRow[]
  processor_dpas?: { reference: string; processor: string; country: string; adequate: boolean; purpose: string; data_categories: string; status: string; status_label: string; signed_at: string | null; signatory: string | null; view_url: string }[]
  breach?: { total: number; open: number; overdue: number }
  retention?: { total_past_retention: number; classes_tracked: number }
  dsr?: { total: number; open: number; overdue: number }
  dpia?: { summary: { total: number; approved: number; draft: number; high_risk: number }, items: DpiaItem[] }
  ropa?: { summary: { total: number; version: string }, items: RopaItem[] }
  dpo: { name: string; email: string }
  as_of: string
}
type DpiaItem = { key: string; module: string; risk: string; status: string; purpose: string; data: string; necessity: string; risks: string[]; mitigations: string[]; residual_risk: string }
type RopaItem = { key: string; activity: string; basis: string; data: string; recipients: string; retention: string; transfers: string }

const STATUS_COLOR: Record<string, string> = {
  fixed: '#16A34A', in_progress: ORANGE, open: '#C0392B', pass: '#2563EB',
  shielded: '#16A34A', adequate: '#2563EB', review: ORANGE,
}
const STATUS_LABEL: Record<string, string> = {
  fixed: 'Fixed', in_progress: 'In progress', open: 'Open', pass: 'Pass',
  shielded: 'Shielded', adequate: 'Adequate', review: 'To document',
}
const chip = (s: string) => (
  <span style={{ background: (STATUS_COLOR[s] || '#666') + '1A', color: STATUS_COLOR[s] || '#666',
    border: `1px solid ${(STATUS_COLOR[s] || '#666')}55`, borderRadius: 999, padding: '2px 10px',
    fontSize: 12, fontWeight: 600, whiteSpace: 'nowrap' }}>{STATUS_LABEL[s] || s}</span>
)

const SEV_ORDER = ['severe', 'high', 'low', 'pass']
const SEV_LABEL: Record<string, string> = { severe: 'Severe', high: 'High', low: 'Low', pass: 'Strengths' }

export default function DataProtectionPage() {
  const [allowed, setAllowed] = useState<boolean | null>(null)
  const [data, setData] = useState<Payload | null>(null)
  const [err, setErr] = useState('')

  // Cold-load resilience (CFO hit a blank dashboard 2026-07-29): the gate + the
  // data both fetch once on mount. A single transient blip (auth-not-ready race,
  // a 502/timeout) used to leave the page stuck blank until a manual reload —
  // there was no retry and nothing to refetch. Both now retry with backoff, so
  // a hiccup self-heals instead of showing an empty shell. Mirrors the bounded
  // retry idiom already in lib/api.ts.
  useEffect(() => {
    let off = false
    ;(async () => {
      for (let i = 0; i < 4 && !off; i++) {
        try {
          const r = await apiFetch<{ allowed: boolean }>('/dpa-dashboard/access/')
          if (!off) setAllowed(!!r.allowed)
          return
        } catch {
          if (i === 3) { if (!off) setAllowed(false); return }
          await new Promise(res => setTimeout(res, 700 * (i + 1)))
        }
      }
    })()
    return () => { off = true }
  }, [])
  useEffect(() => {
    if (!allowed) return
    let off = false
    ;(async () => {
      for (let i = 0; i < 5 && !off; i++) {
        try {
          const d = await apiFetch<Payload>('/dpa-dashboard/')
          if (!d) throw new Error('empty payload')
          if (!off) { setData(d); setErr('') }
          return
        } catch (e) {
          if (i === 4) { if (!off) setErr(String((e as Error)?.message || e)); return }
          await new Promise(res => setTimeout(res, 700 * (i + 1)))
        }
      }
    })()
    return () => { off = true }
  }, [allowed])

  if (allowed === null) return <div style={{ padding: 40, color: '#64748b' }}>Loading…</div>
  if (!allowed) return (
    <div style={{ maxWidth: 520, margin: '80px auto', textAlign: 'center', padding: 32,
      background: '#fff', border: '1px solid #e5e7eb', borderRadius: 16 }}>
      <div style={{ fontSize: 40 }}>🔒</div>
      <h2 style={{ color: NAVY, margin: '12px 0 6px' }}>Restricted</h2>
      <p style={{ color: '#64748b' }}>The Data Protection dashboard is for the DPO, C-suite, HR and the Finance Manager.</p>
    </div>
  )

  const bySev = (sev: string) => (data?.findings || []).filter(f => f.severity === sev)

  return (
    <div style={{ maxWidth: 1080, margin: '0 auto', padding: '24px 20px 60px', animation: 'dpaFade .4s ease-out' }}>
      <style>{`@keyframes dpaFade{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
        .dpa-card{background:#fff;border:1px solid #e8ebef;border-radius:16px;box-shadow:0 1px 3px rgba(13,27,42,.06)}
        .dpa-row{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;padding:12px 16px;border-top:1px solid #f0f2f5}
        .dpa-row:first-child{border-top:none}
        .dpa-cockpit{display:flex;gap:24px;align-items:center;flex-wrap:wrap}`}</style>

      {/* Header */}
      <div style={{ background: NAVY, borderRadius: 16, padding: '20px 24px', color: '#fff', marginBottom: 18 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8, alignItems: 'baseline' }}>
          <h1 style={{ margin: 0, fontSize: 24 }}>Data <span style={{ color: ORANGE }}>Protection</span></h1>
          <span style={{ color: '#9fb0c3', fontSize: 13 }}>as of {data?.as_of || '…'} · DPA No. 18 of 2024</span>
        </div>
        <p style={{ margin: '6px 0 0', color: '#c7d2de', fontSize: 13 }}>
          DPO: {data?.dpo?.name || '—'} ({data?.dpo?.email})
        </p>
      </div>

      {err && <div className="dpa-card" style={{ padding: 16, marginBottom: 16, color: '#C0392B' }}>{err}</div>}

      {/* DPO compliance cockpit — at-a-glance health (design 2026-07-24) */}
      {data && <DpoCockpit data={data} />}

      {/* DPO's uploaded compliance workbook — ROPA, systems, policies, gaps (2026-07-24) */}
      <ComplianceWorkbook />

      {/* Self-updating ROPA — live-derived processing register + drift (2026-07-24) */}
      <SelfUpdatingRopa />

      {/* Monthly compulsory DPO checklist */}
      <div id="checklist"><MonthlyChecklist /></div>

      {/* AI shield */}
      {data?.ai_shield && (
        <div className="dpa-card" style={{ padding: 18, marginBottom: 18 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 10 }}>
            <h3 style={{ margin: 0, color: NAVY }}>AI privacy shield</h3>
            <span style={{ background: data.ai_shield.protected ? '#16A34A1A' : '#C0392B1A',
              color: data.ai_shield.protected ? '#16A34A' : '#C0392B', fontWeight: 700, borderRadius: 999,
              padding: '4px 12px', fontSize: 13 }}>
              {data.ai_shield.protected ? '● Shield ON — ' : '● Shield OFF — '}{data.ai_shield.firewall_mode}
            </span>
          </div>
          <p style={{ color: '#64748b', fontSize: 13, margin: '6px 0 12px' }}>
            Personal data is replaced with reversible tokens before any AI request leaves the building.
          </p>
          <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
            <Stat n={data.ai_shield.ai_calls_30d} label="AI calls (30 days)" />
            <Stat n={`${data.ai_shield.avg_ms} ms`} label="Avg response" />
            <div>
              <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 4 }}>Engines used</div>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {(data.ai_shield.by_engine || []).map(e => (
                  <span key={e.engine} style={{ background: '#f1f5f9', borderRadius: 8, padding: '2px 8px', fontSize: 12, color: NAVY }}>
                    {e.engine} · {e.n}
                  </span>
                ))}
                {(!data.ai_shield.by_engine?.length) && <span style={{ color: '#94a3b8', fontSize: 12 }}>no AI calls yet</span>}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Breach / incident register */}
      <div id="breaches"><BreachRegister /></div>

      {/* Data-subject requests */}
      <div id="dsr" style={{ scrollMarginTop: 16 }}><DataSubjectRequests /></div>

      {/* DPIA register */}
      {data?.dpia && <div id="dpia" style={{ scrollMarginTop: 16 }}><DpiaPanel items={data.dpia.items} /></div>}

      {/* ROPA — records of processing */}
      {data?.ropa && <div id="ropa" style={{ scrollMarginTop: 16 }}><RopaPanel items={data.ropa.items} /></div>}

      {/* Findings scorecard */}
      <h3 id="scorecard" style={{ color: NAVY, margin: '4px 0 10px', scrollMarginTop: 16 }}>Audit scorecard</h3>
      {SEV_ORDER.map(sev => bySev(sev).length > 0 && (
        <div key={sev} style={{ marginBottom: 14 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: sev === 'pass' ? '#2563EB' : NAVY, margin: '0 0 6px 2px', textTransform: 'uppercase', letterSpacing: .5 }}>
            {SEV_LABEL[sev]}
          </div>
          <div className="dpa-card">
            {bySev(sev).map(f => (
              <div className="dpa-row" key={f.id}>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontWeight: 600, color: NAVY, fontSize: 14 }}>
                    <span style={{ color: '#94a3b8', fontWeight: 500 }}>{f.id}</span>&nbsp; {f.title}
                  </div>
                  <div style={{ color: '#64748b', fontSize: 12.5, marginTop: 3 }}>{f.note}</div>
                </div>
                {chip(f.status)}
              </div>
            ))}
          </div>
        </div>
      ))}

      {/* Data map */}
      <h3 style={{ color: NAVY, margin: '18px 0 10px' }}>Where data goes</h3>
      <div className="dpa-card" style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13.5, minWidth: 520, tableLayout: 'fixed' }}>
          <thead>
            <tr style={{ background: NAVY, color: '#fff', textAlign: 'left' }}>
              <th style={{ padding: '9px 14px' }}>Purpose</th><th style={{ padding: '9px 14px' }}>Processor</th>
              <th style={{ padding: '9px 14px' }}>Location</th><th style={{ padding: '9px 14px' }}>Status</th>
              <th style={{ padding: '9px 14px' }}>Note</th>
            </tr>
          </thead>
          <tbody>
            {(data?.data_map || []).map((r, i) => (
              <tr key={i} style={{ borderTop: '1px solid #f0f2f5' }}>
                <td style={{ padding: '9px 14px', color: NAVY, fontWeight: 600 }}>{r.purpose}</td>
                <td style={{ padding: '9px 14px' }}>{r.processor}</td>
                <td style={{ padding: '9px 14px' }}>{r.location}</td>
                <td style={{ padding: '9px 14px' }}>{chip(r.status)}</td>
                <td style={{ padding: '9px 14px', color: '#64748b' }}>{r.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Processor agreements / DPAs (DPA-audit H-6) */}
      <h3 id="processor-dpas" style={{ color: NAVY, margin: '18px 0 10px', scrollMarginTop: 16 }}>
        Processor agreements (DPAs)
      </h3>
      <div className="dpa-card" style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13.5, minWidth: 520, tableLayout: 'fixed' }}>
          <thead>
            <tr style={{ background: NAVY, color: '#fff', textAlign: 'left' }}>
              <th style={{ padding: '9px 14px' }}>Ref</th><th style={{ padding: '9px 14px' }}>Processor</th>
              <th style={{ padding: '9px 14px' }}>Country</th><th style={{ padding: '9px 14px' }}>Data shared</th>
              <th style={{ padding: '9px 14px' }}>Status</th><th style={{ padding: '9px 14px' }}></th>
            </tr>
          </thead>
          <tbody>
            {(data?.processor_dpas || []).length === 0 && (
              <tr><td colSpan={6} style={{ padding: '12px 14px', color: '#64748b' }}>No processor agreements on file yet.</td></tr>
            )}
            {(data?.processor_dpas || []).map((d, i) => {
              const c = d.status === 'signed' ? { bg: '#ECFDF5', fg: '#059669' }
                : d.status === 'sent' ? { bg: '#FFFBEB', fg: '#92400E' }
                : { bg: '#F1F5F9', fg: '#475569' }
              return (
                <tr key={d.reference || i} style={{ borderTop: '1px solid #f0f2f5' }}>
                  <td style={{ padding: '9px 14px', color: NAVY, fontWeight: 600, whiteSpace: 'nowrap' }}>{d.reference || '—'}</td>
                  <td style={{ padding: '9px 14px' }}>{d.processor}</td>
                  <td style={{ padding: '9px 14px' }}>{d.country || '—'}{d.adequate ? '' : ' ⚠'}</td>
                  <td style={{ padding: '9px 14px', color: '#64748b', maxWidth: 260 }}>{d.data_categories}</td>
                  <td style={{ padding: '9px 14px', whiteSpace: 'nowrap' }}>
                    <span style={{ background: c.bg, color: c.fg, padding: '3px 10px', borderRadius: 999, fontSize: 12, fontWeight: 700 }}>
                      {d.status_label}{d.signed_at ? ` · ${d.signed_at}` : ''}
                    </span>
                  </td>
                  <td style={{ padding: '9px 14px', whiteSpace: 'nowrap' }}>
                    <a href={d.view_url} target="_blank" rel="noopener noreferrer" style={{ color: '#2563EB', fontWeight: 600, textDecoration: 'none' }}>
                      {d.status === 'signed' ? 'View' : 'View / sign'} →
                    </a>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* Privacy notices (DPA Part VIII) */}
      <div id="notices" style={{ scrollMarginTop: 16 }}><PrivacyNotices /></div>
    </div>
  )
}

function Stat({ n, label }: { n: number | string; label: string }) {
  return (
    <div>
      <div style={{ fontSize: 22, fontWeight: 700, color: NAVY }}>{n}</div>
      <div style={{ fontSize: 12, color: '#94a3b8' }}>{label}</div>
    </div>
  )
}

// DPO cockpit — the compliance-health headline the DPO lands on. Pure
// presentation over the existing /dpa-dashboard payload (no new data).
function DpoCockpit({ data }: { data: Payload }) {
  const s = data.summary
  const pct = Math.max(0, Math.min(100, s?.pct_fixed ?? 0))
  const severe = s?.open_by_severity?.severe || 0
  const breachOverdue = data.breach?.overdue || 0
  const dsrOverdue = data.dsr?.overdue || 0
  const pastRet = data.retention?.total_past_retention || 0
  const highDpia = data.dpia?.summary?.high_risk || 0
  const ropaN = data.ropa?.summary?.total || 0
  const alerts = severe + breachOverdue + dsrOverdue
  const verdict = alerts > 0
    ? { t: 'Action needed', c: '#C0392B' }
    : (s?.open || 0) > 0 ? { t: 'On track', c: ORANGE } : { t: 'Compliant', c: '#16A34A' }
  // Ring/dot use verdict.c (shapes — contrast-exempt), but brand orange #F4A623
  // fails WCAG AA as text on white, so the verdict LABEL uses the darker
  // #B04E00 orange-text token (keeps the axe-clean state). CFO 2026-07-24.
  const verdictText = verdict.c === ORANGE ? '#B04E00' : verdict.c
  const R = 52, CIRC = 2 * Math.PI * R
  const off = CIRC * (1 - pct / 100)
  return (
    <div className="dpa-card dpa-cockpit" style={{ padding: 20, marginBottom: 18 }}>
      <div style={{ position: 'relative', width: 132, height: 132, flexShrink: 0 }}>
        <svg width="132" height="132" viewBox="0 0 132 132" style={{ transform: 'rotate(-90deg)' }}>
          <circle cx="66" cy="66" r={R} fill="none" stroke="#eef2f7" strokeWidth="12" />
          <circle cx="66" cy="66" r={R} fill="none" stroke={verdict.c} strokeWidth="12" strokeLinecap="round"
            strokeDasharray={CIRC} strokeDashoffset={off}
            style={{ transition: 'stroke-dashoffset 1.1s cubic-bezier(.16,1,.3,1)' }} />
        </svg>
        <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center' }}>
          <div style={{ fontSize: 30, fontWeight: 800, color: NAVY, lineHeight: 1 }}>{pct}%</div>
          <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 2 }}>compliant</div>
        </div>
      </div>
      <div style={{ flex: 1, minWidth: 240 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <span style={{ width: 10, height: 10, borderRadius: 999, background: verdict.c, display: 'inline-block' }} />
          <span style={{ fontSize: 20, fontWeight: 800, color: verdictText }}>{verdict.t}</span>
          <span style={{ color: '#94a3b8', fontSize: 13 }}>
            {alerts > 0 ? `${alerts} item${alerts > 1 ? 's' : ''} need attention` : 'nothing overdue'}
          </span>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(104px,1fr))', gap: 10, marginTop: 14 }}>
          <RiskStat n={severe} label="Severe open" bad={severe > 0} />
          <RiskStat n={breachOverdue} label="Breach 72h overdue" bad={breachOverdue > 0} />
          <RiskStat n={dsrOverdue} label="DSR overdue" bad={dsrOverdue > 0} />
          <RiskStat n={pastRet} label="Past retention" bad={pastRet > 0} />
          <RiskStat n={highDpia} label="High-risk DPIAs" bad={false} />
          <RiskStat n={ropaN} label="ROPA activities" bad={false} />
        </div>
      </div>
    </div>
  )
}
function RiskStat({ n, label, bad }: { n: number; label: string; bad: boolean }) {
  return (
    <div style={{ background: bad ? '#FEF2F2' : '#f8fafc', border: `1px solid ${bad ? '#fecaca' : '#eef2f7'}`, borderRadius: 12, padding: '10px 12px' }}>
      <div style={{ fontSize: 22, fontWeight: 800, color: bad ? '#C0392B' : NAVY, lineHeight: 1 }}>{n}</div>
      <div style={{ fontSize: 11, color: '#64748b', marginTop: 3 }}>{label}</div>
    </div>
  )
}

type ClItem = { key: string; system: string; question: string; response: string; note: string }
type ClRun = { id: number; period: string; status: string; due_date: string; submitted_at: string | null; editable: boolean; disciplinary_raised: boolean; items: ClItem[] }
interface ClPayload { is_dpo: boolean; current: ClRun; overdue: ClRun[]; history: { id: number; period: string; status: string; due_date: string; submitted_at: string | null; disciplinary_raised: boolean }[] }

function MonthlyChecklist() {
  const [d, setD] = useState<ClPayload | null>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const load = () => apiFetch<ClPayload>('/dpa-checklist/').then(setD).catch(() => {})
  useEffect(() => { load() }, [])
  if (!d) return null
  const cur = d.current

  const setItem = (key: string, patch: Partial<ClItem>) => setD(p => !p ? p : ({
    ...p, current: { ...p.current, items: p.current.items.map(it => it.key === key ? { ...it, ...patch } : it) } }))

  const post = async (submit: boolean) => {
    setBusy(true); setMsg('')
    const responses: Record<string, { response: string; note: string }> = {}
    cur.items.forEach(it => { responses[it.key] = { response: it.response, note: it.note } })
    try {
      const r = await apiFetch<{ status: string }>('/dpa-checklist/save/', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ responses, submit }) })
      setMsg(submit ? (r.status === 'submitted' ? 'Submitted ✓' : '') : 'Saved ✓')
      await load()
    } catch (e) { setMsg(String((e as Error)?.message || e)) } finally { setBusy(false) }
  }
  const discipline = async (id: number) => {
    if (!window.confirm('Raise a disciplinary action for this missed monthly checklist?')) return
    try { await apiFetch(`/dpa-checklist/${id}/discipline/`, { method: 'POST' }); await load() }
    catch (e) { window.alert(String((e as Error)?.message || e)) }
  }

  const RADIO = (it: ClItem) => (
    <div style={{ display: 'flex', gap: 10 }}>
      {['yes', 'no', 'na'].map(v => (
        <label key={v} style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 13,
          color: it.response === v ? NAVY : '#94a3b8', fontWeight: it.response === v ? 700 : 400, cursor: 'pointer' }}>
          <input type="radio" name={it.key} checked={it.response === v} onChange={() => setItem(it.key, { response: v })} />
          {v === 'na' ? 'N/A' : v[0].toUpperCase() + v.slice(1)}
        </label>
      ))}
    </div>
  )

  return (
    <div className="dpa-card" style={{ padding: 18, marginBottom: 18 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 10 }}>
        <h3 style={{ margin: 0, color: NAVY }}>Monthly DPO checklist <span style={{ color: '#94a3b8', fontWeight: 500 }}>· {cur.period}</span></h3>
        {chip(cur.status)}
      </div>
      <p style={{ color: '#64748b', fontSize: 13, margin: '6px 0 12px' }}>
        Compulsory — covers Graphite + omni. Due {cur.due_date}. {d.is_dpo ? 'Complete every item and submit.' : 'Completed by the DPO each month.'}
      </p>

      {/* Overdue action cards (C-suite can escalate) */}
      {d.overdue.map(o => (
        <div key={o.id} style={{ background: '#C0392B10', border: '1px solid #C0392B55', borderRadius: 12,
          padding: '12px 14px', marginBottom: 10, display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
          <div style={{ color: '#C0392B', fontWeight: 600, fontSize: 14 }}>
            ⚠ {o.period} not submitted (was due {o.due_date})
          </div>
          {o.disciplinary_raised
            ? <span style={{ color: '#C0392B', fontSize: 13, fontWeight: 600 }}>Disciplinary action raised ✓</span>
            : <button onClick={() => discipline(o.id)} style={{ background: '#C0392B', color: '#fff', border: 'none',
                borderRadius: 8, padding: '7px 14px', fontWeight: 600, cursor: 'pointer', fontSize: 13 }}>
                Convert to disciplinary action
              </button>}
        </div>
      ))}

      {/* The checklist */}
      <div style={{ display: 'grid', gap: 8 }}>
        {cur.items.map(it => (
          <div key={it.key} style={{ borderTop: '1px solid #f0f2f5', paddingTop: 10 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
              <div style={{ fontSize: 13.5, color: NAVY, maxWidth: 620 }}>
                <span style={{ background: '#eef2f7', color: '#475569', borderRadius: 6, padding: '1px 7px', fontSize: 11, marginRight: 6 }}>{it.system}</span>
                {it.question}
              </div>
              {cur.editable ? RADIO(it)
                : <span style={{ fontWeight: 700, color: it.response ? NAVY : '#cbd5e1' }}>{it.response ? it.response.toUpperCase() : '—'}</span>}
            </div>
            {cur.editable && (
              <input value={it.note} onChange={e => setItem(it.key, { note: e.target.value })} placeholder="Note (optional)"
                style={{ marginTop: 6, width: '100%', border: '1px solid #e2e8f0', borderRadius: 8, padding: '6px 10px', fontSize: 13 }} />
            )}
          </div>
        ))}
      </div>

      {cur.editable && (
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 14 }}>
          <button onClick={() => post(false)} disabled={busy}
            style={{ background: '#eef2f7', color: NAVY, border: 'none', borderRadius: 8, padding: '8px 16px', fontWeight: 600, cursor: 'pointer' }}>Save</button>
          <button onClick={() => post(true)} disabled={busy}
            style={{ background: ORANGE, color: NAVY, border: 'none', borderRadius: 8, padding: '8px 18px', fontWeight: 700, cursor: 'pointer' }}>Submit for the month</button>
          {msg && <span style={{ fontSize: 13, color: '#16A34A' }}>{msg}</span>}
        </div>
      )}
      {!cur.editable && cur.status === 'submitted' && (
        <div style={{ marginTop: 10, color: '#16A34A', fontSize: 13, fontWeight: 600 }}>Submitted {cur.submitted_at ? `on ${cur.submitted_at.slice(0, 10)}` : ''}.</div>
      )}
    </div>
  )
}

type Breach = { id: number; title: string; description: string; discovered_at: string | null; severity: string; reportable: boolean; status: string; idpc_notified: boolean; subjects_notified: boolean; hours_left: number | null; overdue: boolean }

function BreachRegister() {
  const [d, setD] = useState<{ incidents: Breach[]; summary: { total: number; open: number; overdue: number } } | null>(null)
  const [show, setShow] = useState(false)
  const [title, setTitle] = useState(''); const [desc, setDesc] = useState('')
  const [sev, setSev] = useState('medium'); const [rep, setRep] = useState(true); const [busy, setBusy] = useState(false)
  const load = () => apiFetch<{ incidents: Breach[]; summary: { total: number; open: number; overdue: number } }>('/breaches/').then(setD).catch(() => {})
  useEffect(() => { load() }, [])
  const create = async () => {
    if (!title.trim()) return
    setBusy(true)
    try {
      await apiFetch('/breaches/', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title, description: desc, severity: sev, reportable: rep }) })
      setTitle(''); setDesc(''); setShow(false); await load()
    } catch (e) { window.alert(String((e as Error)?.message || e)) } finally { setBusy(false) }
  }
  const act = async (id: number, patch: Record<string, unknown>) => {
    try { await apiFetch(`/breaches/${id}/`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(patch) }); await load() }
    catch (e) { window.alert(String((e as Error)?.message || e)) }
  }
  if (!d) return null
  const clock = (b: Breach) => {
    if (b.status === 'closed') return <span style={{ color: '#94a3b8' }}>closed</span>
    if (b.idpc_notified) return <span style={{ color: '#16A34A' }}>IDPC notified ✓</span>
    if (!b.reportable) return <span style={{ color: '#94a3b8' }}>not reportable</span>
    if (b.hours_left === null) return null
    return <span style={{ color: b.overdue ? '#C0392B' : ORANGE, fontWeight: 700 }}>{b.overdue ? `72h OVERDUE by ${Math.abs(b.hours_left)}h` : `${b.hours_left}h to IDPC deadline`}</span>
  }
  const inp = { border: '1px solid #e2e8f0', borderRadius: 8, padding: '8px 10px', fontSize: 13 }
  return (
    <div className="dpa-card" style={{ padding: 18, marginBottom: 18 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 10 }}>
        <h3 style={{ margin: 0, color: NAVY }}>Breach &amp; incident register <span style={{ color: '#94a3b8', fontWeight: 500 }}>· {d.summary.open} open{d.summary.overdue ? `, ${d.summary.overdue} overdue` : ''}</span></h3>
        <button onClick={() => setShow(s => !s)} style={{ background: ORANGE, color: NAVY, border: 'none', borderRadius: 8, padding: '7px 14px', fontWeight: 700, cursor: 'pointer', fontSize: 13 }}>{show ? 'Cancel' : 'Log an incident'}</button>
      </div>
      <p style={{ color: '#64748b', fontSize: 13, margin: '6px 0 12px' }}>Reportable breaches must be notified to the IDPC within 72 hours.</p>
      {show && (
        <div style={{ background: '#f8fafc', border: '1px solid #e2e8f0', borderRadius: 12, padding: 12, marginBottom: 12, display: 'grid', gap: 8 }}>
          <input value={title} onChange={e => setTitle(e.target.value)} placeholder="What happened (short title)" style={inp} />
          <textarea value={desc} onChange={e => setDesc(e.target.value)} placeholder="Details" rows={2} style={inp} />
          <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
            <select value={sev} onChange={e => setSev(e.target.value)} style={inp}><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option><option value="critical">Critical</option></select>
            <label style={{ fontSize: 13, color: NAVY, display: 'flex', gap: 6, alignItems: 'center' }}><input type="checkbox" checked={rep} onChange={e => setRep(e.target.checked)} /> Reportable to the IDPC</label>
            <button onClick={create} disabled={busy} style={{ background: NAVY, color: '#fff', border: 'none', borderRadius: 8, padding: '8px 16px', fontWeight: 600, cursor: 'pointer' }}>Log incident</button>
          </div>
        </div>
      )}
      {d.incidents.length === 0 && <div style={{ color: '#94a3b8', fontSize: 13 }}>No incidents logged.</div>}
      {d.incidents.map(b => (
        <div key={b.id} style={{ borderTop: '1px solid #f0f2f5', padding: '10px 0', display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
          <div style={{ minWidth: 0 }}>
            <div style={{ color: NAVY, fontWeight: 600, fontSize: 14 }}>{b.title} <span style={{ color: '#94a3b8', fontWeight: 400, fontSize: 12 }}>· {b.severity} · {b.status}</span></div>
            <div style={{ fontSize: 12.5, marginTop: 3 }}>{clock(b)}</div>
          </div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {!b.idpc_notified && b.reportable && b.status !== 'closed' && <button onClick={() => act(b.id, { idpc_notified: true })} style={{ background: '#eef2f7', color: NAVY, border: 'none', borderRadius: 8, padding: '6px 10px', fontWeight: 600, cursor: 'pointer', fontSize: 12 }}>Mark IDPC notified</button>}
            {b.status !== 'closed' && <button onClick={() => act(b.id, { status: 'closed' })} style={{ background: '#eef2f7', color: NAVY, border: 'none', borderRadius: 8, padding: '6px 10px', fontWeight: 600, cursor: 'pointer', fontSize: 12 }}>Close</button>}
          </div>
        </div>
      ))}
    </div>
  )
}

type Notice = { title: string; version: string; html: string }
function PrivacyNotices() {
  const [d, setD] = useState<{ notices: Record<string, Notice> } | null>(null)
  const [open, setOpen] = useState('')
  useEffect(() => { apiFetch<{ notices: Record<string, Notice> }>('/privacy-notices/').then(setD).catch(() => {}) }, [])
  if (!d?.notices) return null
  return (
    <div className="dpa-card" style={{ padding: 18, marginBottom: 18 }}>
      <h3 style={{ margin: '0 0 6px', color: NAVY }}>Privacy notices</h3>
      <p style={{ color: '#64748b', fontSize: 13, margin: '0 0 8px' }}>Plain-language notices (DPA Part VIII), separate from the staff access undertaking.</p>
      {Object.entries(d.notices).map(([k, n]) => (
        <div key={k} style={{ borderTop: '1px solid #f0f2f5', padding: '10px 0' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', cursor: 'pointer' }} onClick={() => setOpen(o => o === k ? '' : k)}>
            <div style={{ color: NAVY, fontWeight: 600, fontSize: 14 }}>{n.title} <span style={{ color: '#94a3b8', fontWeight: 400, fontSize: 12 }}>· v{n.version}</span></div>
            <span style={{ color: ORANGE, fontSize: 13, fontWeight: 600 }}>{open === k ? 'Hide' : 'View'}</span>
          </div>
          {open === k && <div style={{ marginTop: 8, fontSize: 13.5, color: '#334155', lineHeight: 1.5 }} dangerouslySetInnerHTML={{ __html: n.html }} />}
        </div>
      ))}
    </div>
  )
}

type Dsr = { id: number; subject_name: string; subject_email: string; subject_type: string; kind: string; details: string; status: string; due_date: string | null; resolution: string; days_left: number | null; overdue: boolean }
const DSR_KIND: Record<string, string> = { access: 'Access', rectify: 'Rectify', erase: 'Erase', restrict: 'Restrict', object: 'Object', portability: 'Portability' }
function DataSubjectRequests() {
  const [d, setD] = useState<{ requests: Dsr[]; summary: { total: number; open: number; overdue: number } } | null>(null)
  const [show, setShow] = useState(false)
  const [name, setName] = useState(''); const [email, setEmail] = useState(''); const [kind, setKind] = useState('access')
  const [stype, setStype] = useState('customer'); const [details, setDetails] = useState(''); const [busy, setBusy] = useState(false)
  const load = () => apiFetch<{ requests: Dsr[]; summary: { total: number; open: number; overdue: number } }>('/dsrs/').then(setD).catch(() => {})
  useEffect(() => { load() }, [])
  const create = async () => {
    if (!name.trim()) return
    setBusy(true)
    try {
      await apiFetch('/dsrs/', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ subject_name: name, subject_email: email, kind, subject_type: stype, details }) })
      setName(''); setEmail(''); setDetails(''); setShow(false); await load()
    } catch (e) { window.alert(String((e as Error)?.message || e)) } finally { setBusy(false) }
  }
  const setStatus = async (id: number, status: string) => {
    try { await apiFetch(`/dsrs/${id}/`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status }) }); await load() }
    catch (e) { window.alert(String((e as Error)?.message || e)) }
  }
  if (!d) return null
  const inp = { border: '1px solid #e2e8f0', borderRadius: 8, padding: '8px 10px', fontSize: 13 }
  return (
    <div className="dpa-card" style={{ padding: 18, marginBottom: 18 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 10 }}>
        <h3 style={{ margin: 0, color: NAVY }}>Data-subject requests <span style={{ color: '#94a3b8', fontWeight: 500 }}>· {d.summary.open} open{d.summary.overdue ? `, ${d.summary.overdue} overdue` : ''}</span></h3>
        <button onClick={() => setShow(s => !s)} style={{ background: ORANGE, color: NAVY, border: 'none', borderRadius: 8, padding: '7px 14px', fontWeight: 700, cursor: 'pointer', fontSize: 13 }}>{show ? 'Cancel' : 'Log a request'}</button>
      </div>
      <p style={{ color: '#64748b', fontSize: 13, margin: '6px 0 12px' }}>See / correct / delete requests. Statutory clock: 30 days from receipt.</p>
      {show && (
        <div style={{ background: '#f8fafc', border: '1px solid #e2e8f0', borderRadius: 12, padding: 12, marginBottom: 12, display: 'grid', gap: 8 }}>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <input value={name} onChange={e => setName(e.target.value)} placeholder="Person's name" style={{ ...inp, flex: 1, minWidth: 160 }} />
            <input value={email} onChange={e => setEmail(e.target.value)} placeholder="Email (optional)" style={{ ...inp, flex: 1, minWidth: 160 }} />
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <select value={kind} onChange={e => setKind(e.target.value)} style={inp}>{Object.entries(DSR_KIND).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
            <select value={stype} onChange={e => setStype(e.target.value)} style={inp}><option value="customer">Customer</option><option value="staff">Staff</option></select>
            <button onClick={create} disabled={busy} style={{ background: NAVY, color: '#fff', border: 'none', borderRadius: 8, padding: '8px 16px', fontWeight: 600, cursor: 'pointer' }}>Log request</button>
          </div>
          <textarea value={details} onChange={e => setDetails(e.target.value)} placeholder="Details" rows={2} style={inp} />
        </div>
      )}
      {d.requests.length === 0 && <div style={{ color: '#94a3b8', fontSize: 13 }}>No requests logged.</div>}
      {d.requests.map(r => (
        <div key={r.id} style={{ borderTop: '1px solid #f0f2f5', padding: '10px 0', display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
          <div>
            <div style={{ color: NAVY, fontWeight: 600, fontSize: 14 }}>{DSR_KIND[r.kind] || r.kind} — {r.subject_name} <span style={{ color: '#94a3b8', fontWeight: 400, fontSize: 12 }}>· {r.subject_type}</span></div>
            <div style={{ fontSize: 12.5, marginTop: 3, color: r.overdue ? '#C0392B' : '#64748b', fontWeight: r.overdue ? 700 : 400 }}>
              {r.status === 'completed' || r.status === 'rejected' ? r.status : (r.overdue ? `OVERDUE by ${Math.abs(r.days_left || 0)}d` : `${r.days_left}d left (due ${r.due_date})`)}
            </div>
          </div>
          {r.status !== 'completed' && r.status !== 'rejected' && (
            <select value={r.status} onChange={e => setStatus(r.id, e.target.value)} style={inp}>
              <option value="open">Open</option><option value="in_progress">In progress</option><option value="completed">Completed</option><option value="rejected">Rejected</option>
            </select>
          )}
        </div>
      ))}
    </div>
  )
}

function DpiaPanel({ items }: { items: DpiaItem[] }) {
  const [open, setOpen] = useState('')
  if (!items?.length) return null
  const RC: Record<string, string> = { high: '#C0392B', medium: ORANGE, low: '#16A34A' }
  return (
    <div className="dpa-card" style={{ padding: 18, marginBottom: 18 }}>
      <h3 style={{ margin: '0 0 6px', color: NAVY }}>DPIA — impact assessments</h3>
      <p style={{ color: '#64748b', fontSize: 13, margin: '0 0 8px' }}>Drafted per high-risk activity — the DPO reviews &amp; signs off.</p>
      {items.map(it => (
        <div key={it.key} style={{ borderTop: '1px solid #f0f2f5', padding: '10px 0' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', cursor: 'pointer', gap: 8 }} onClick={() => setOpen(o => o === it.key ? '' : it.key)}>
            <div style={{ color: NAVY, fontWeight: 600, fontSize: 14 }}>{it.module} <span style={{ color: RC[it.risk] || '#666', fontSize: 12, fontWeight: 700 }}>· {it.risk} risk</span></div>
            <span style={{ color: it.status === 'approved' ? '#16A34A' : ORANGE, fontSize: 12, fontWeight: 600 }}>{it.status}</span>
          </div>
          {open === it.key && (
            <div style={{ marginTop: 8, fontSize: 13, color: '#334155', lineHeight: 1.5 }}>
              <p style={{ margin: '4px 0' }}><b>Purpose:</b> {it.purpose}</p>
              <p style={{ margin: '4px 0' }}><b>Data:</b> {it.data}</p>
              <p style={{ margin: '4px 0' }}><b>Necessity:</b> {it.necessity}</p>
              <p style={{ margin: '4px 0 2px' }}><b>Risks:</b></p><ul style={{ margin: '0 0 6px' }}>{it.risks.map((r, i) => <li key={i}>{r}</li>)}</ul>
              <p style={{ margin: '4px 0 2px' }}><b>Mitigations:</b></p><ul style={{ margin: '0 0 6px' }}>{it.mitigations.map((m, i) => <li key={i}>{m}</li>)}</ul>
              <p style={{ margin: '4px 0' }}><b>Residual risk:</b> {it.residual_risk}</p>
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

function RopaPanel({ items }: { items: RopaItem[] }) {
  if (!items?.length) return null
  const th = { padding: '8px 12px' }
  const td = { padding: '8px 12px', borderTop: '1px solid #f0f2f5', verticalAlign: 'top' as const, overflowWrap: 'anywhere' as const }
  return (
    <div className="dpa-card" style={{ padding: 18, marginBottom: 18 }}>
      <h3 style={{ margin: '0 0 6px', color: NAVY }}>Records of Processing (ROPA)</h3>
      <p style={{ color: '#64748b', fontSize: 13, margin: '0 0 10px' }}>Every processing activity → lawful basis, data, recipients, retention, transfers (DPA S-2).</p>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5, minWidth: 560, tableLayout: 'fixed' }}>
          <thead><tr style={{ background: NAVY, color: '#fff', textAlign: 'left' }}>
            <th style={th}>Activity</th><th style={th}>Lawful basis</th><th style={th}>Data</th><th style={th}>Recipients</th><th style={th}>Retention</th><th style={th}>Transfers</th>
          </tr></thead>
          <tbody>{items.map(r => (
            <tr key={r.key}>
              <td style={{ ...td, color: NAVY, fontWeight: 600 }}>{r.activity}</td>
              <td style={td}>{r.basis}</td><td style={td}>{r.data}</td><td style={td}>{r.recipients}</td><td style={td}>{r.retention}</td><td style={td}>{r.transfers}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
    </div>
  )
}
