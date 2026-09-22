'use client'
/**
 * Security Posture dashboard (CFO directive 2026-08-06).
 *
 * Mirrors the Data Protection and Compliance/AML dashboards: a maintained
 * register with a completion percentage, plus the sprint plan that closes it.
 * Deliberately leads with what is already strong before what is weak — this is
 * a work plan for the board, not a list of complaints.
 *
 * Access is enforced server-side (core.security_dashboard, group C-suite only);
 * this page also gates the UI so a non-executive never sees a half-rendered
 * findings list.
 */
import { useEffect, useState } from 'react'
import { apiFetch } from '@/lib/api'

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'

type Sev = 'critical' | 'high' | 'medium' | 'low'
type Status = 'open' | 'in_progress' | 'fixed'

interface Finding {
  id: string; severity: Sev; area: string; title: string; status: Status
  plain_english: string; fix: string; owner: string
  effort: 'quick' | 'sprint' | 'architectural'
  sprint: number; evidence: string; confidence: string
}
interface Strength { title: string; detail: string; evidence: string }
interface Sprint {
  sprint: number; title: string; total: number; done: number; pct: number
  items: { id: string; title: string; owner: string; effort: string; status: Status; severity: Sev }[]
}
interface Payload {
  summary: {
    total_findings: number; fixed: number; in_progress: number; open: number
    pct_complete: number; open_by_severity: Partial<Record<Sev, number>>
    strengths_verified: number
  }
  findings: Finding[]
  strengths: Strength[]
  sprints: Sprint[]
  needs_live_test: string[]
  assessed_on: string
  method: string
}

const SEV: Record<Sev, { c: string; label: string }> = {
  critical: { c: '#B91C1C', label: 'Critical' },
  high:     { c: '#DC2626', label: 'High' },
  medium:   { c: ORANGE,    label: 'Medium' },
  low:      { c: '#2563EB', label: 'Low' },
}
const STATUS: Record<Status, { c: string; label: string }> = {
  open:        { c: '#94a3b8', label: 'Not started' },
  in_progress: { c: ORANGE,    label: 'Under way' },
  fixed:       { c: '#16A34A', label: 'Done' },
}
const EFFORT: Record<string, string> = {
  quick: 'Quick win — under a day',
  sprint: 'One sprint',
  architectural: 'Structural — plan it properly',
}

const pill = (bg: string, text: string, label: string) => (
  <span style={{
    background: bg, color: text, border: `1px solid ${text}44`, borderRadius: 999,
    padding: '2px 10px', fontSize: 12, fontWeight: 700, whiteSpace: 'nowrap',
  }}>{label}</span>
)

function Tile({ label, value, note, tone }: {
  label: string; value: string | number; note?: string; tone?: string
}) {
  return (
    <div className="sec-card" style={{ padding: '14px 16px', flex: '1 1 150px', minWidth: 150 }}>
      <div style={{ fontSize: 12, color: '#64748b', fontWeight: 600 }}>{label}</div>
      <div style={{ fontSize: 26, fontWeight: 800, color: tone || NAVY, marginTop: 2 }}>{value}</div>
      {note && <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 2 }}>{note}</div>}
    </div>
  )
}

/** A completion bar. Reads as progress, not as an alarm. */
function Bar({ pct, tone = ORANGE, height = 10 }: { pct: number; tone?: string; height?: number }) {
  return (
    <div style={{ background: '#EEF1F5', borderRadius: 999, height, overflow: 'hidden' }}>
      <div style={{
        width: `${Math.max(0, Math.min(100, pct))}%`, height: '100%', borderRadius: 999,
        background: `linear-gradient(90deg, ${tone}, ${tone}CC)`, transition: 'width .6s ease',
      }} />
    </div>
  )
}

export default function SecurityPosturePage() {
  const [allowed, setAllowed] = useState<boolean | null>(null)
  const [data, setData] = useState<Payload | null>(null)
  const [err, setErr] = useState('')
  const [open, setOpen] = useState<string | null>(null)

  useEffect(() => {
    apiFetch<{ allowed: boolean }>('/security-dashboard/access/')
      .then(r => setAllowed(!!r.allowed)).catch(() => setAllowed(false))
  }, [])
  useEffect(() => {
    if (allowed) {
      apiFetch<Payload>('/security-dashboard/')
        .then(setData).catch(e => setErr(String(e?.message || e)))
    }
  }, [allowed])

  if (allowed === null) return <div style={{ padding: 40, color: '#64748b' }}>Loading…</div>
  if (!allowed) return (
    <div style={{ maxWidth: 520, margin: '80px auto', textAlign: 'center', padding: 32,
      background: '#fff', border: '1px solid #e5e7eb', borderRadius: 16 }}>
      <div style={{ fontSize: 40 }}>🔒</div>
      <h2 style={{ color: NAVY, margin: '12px 0 6px' }}>Restricted</h2>
      <p style={{ color: '#64748b' }}>
        The Security Posture dashboard is for the CEO, COO and CFO.
      </p>
    </div>
  )

  const s = data?.summary
  const findings = data?.findings || []
  const openSev = s?.open_by_severity || {}

  return (
    <div style={{ maxWidth: 1080, margin: '0 auto', padding: '24px 20px 60px', animation: 'secFade .4s ease-out' }}>
      <style>{`@keyframes secFade{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
        .sec-card{background:#fff;border:1px solid #e8ebef;border-radius:16px;box-shadow:0 1px 3px rgba(13,27,42,.06)}
        .sec-row{padding:14px 18px;border-top:1px solid #f0f2f5;cursor:pointer;transition:background .15s}
        .sec-row:first-child{border-top:none}
        .sec-row:hover{background:#FAFBFC}
        .sec-h{font-weight:700;color:${NAVY};font-size:15px;margin:0 0 10px}
        .sec-lbl{font-size:11px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:.04em}`}</style>

      {/* Header */}
      <div style={{ background: NAVY, borderRadius: 16, padding: '20px 24px', color: '#fff', marginBottom: 18 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8, alignItems: 'baseline' }}>
          <h1 style={{ margin: 0, fontSize: 24 }}>Security <span style={{ color: ORANGE }}>Posture</span></h1>
          <span style={{ color: '#9fb0c3', fontSize: 13 }}>assessed {data?.assessed_on || '…'}</span>
        </div>
        <p style={{ margin: '8px 0 0', color: '#c7d2de', fontSize: 13, lineHeight: 1.5, maxWidth: 760 }}>
          {data?.method || ''}
        </p>
      </div>

      {err && <div className="sec-card" style={{ padding: 16, marginBottom: 16, color: '#C0392B' }}>{err}</div>}

      {/* Completion */}
      <div className="sec-card" style={{ padding: '18px 20px', marginBottom: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 10 }}>
          <div className="sec-h" style={{ margin: 0 }}>Remediation progress</div>
          <div style={{ fontSize: 28, fontWeight: 800, color: NAVY }}>{s?.pct_complete ?? 0}%</div>
        </div>
        <Bar pct={s?.pct_complete ?? 0} height={12} />
        <div style={{ fontSize: 12, color: '#94a3b8', marginTop: 8 }}>
          {s?.fixed ?? 0} done · {s?.in_progress ?? 0} under way · {s?.open ?? 0} not started
          {' '}· work under way counts half
        </div>
      </div>

      {/* Tiles */}
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 18 }}>
        <Tile label="Findings" value={s?.total_findings ?? 0} note="after re-verification" />
        <Tile label="High" value={openSev.high ?? 0} note="still open" tone={openSev.high ? SEV.high.c : '#16A34A'} />
        <Tile label="Medium" value={openSev.medium ?? 0} note="still open" tone={openSev.medium ? ORANGE : '#16A34A'} />
        <Tile label="Low" value={openSev.low ?? 0} note="still open" tone="#2563EB" />
        <Tile label="Controls verified strong" value={s?.strengths_verified ?? 0} note="already protecting us" tone="#16A34A" />
      </div>

      {/* Strengths first — deliberately */}
      <div className="sec-card" style={{ padding: '18px 20px', marginBottom: 18, borderLeft: '4px solid #16A34A' }}>
        <div className="sec-h">What is already protecting us</div>
        <p style={{ margin: '0 0 12px', color: '#64748b', fontSize: 13 }}>
          Each of these was checked, not assumed. Together they close off most of the
          ways a system like ours normally gets breached.
        </p>
        <div style={{ display: 'grid', gap: 10 }}>
          {(data?.strengths || []).map(st => (
            <div key={st.title} style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
              <span style={{ color: '#16A34A', fontWeight: 800, lineHeight: 1.4 }}>✓</span>
              <div>
                <div style={{ fontWeight: 700, color: NAVY, fontSize: 14 }}>{st.title}</div>
                <div style={{ color: '#475569', fontSize: 13, lineHeight: 1.5 }}>{st.detail}</div>
                <div style={{ color: '#94a3b8', fontSize: 11, marginTop: 2, fontFamily: 'ui-monospace,monospace' }}>{st.evidence}</div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Findings — each one carries its fix */}
      <div className="sec-card" style={{ marginBottom: 18 }}>
        <div style={{ padding: '18px 20px 6px' }}>
          <div className="sec-h" style={{ margin: 0 }}>What to fix, and how</div>
          <p style={{ margin: '6px 0 0', color: '#64748b', fontSize: 13 }}>
            Tap any row for the fix, the owner and the evidence it was found in.
          </p>
        </div>
        {findings.map(f => {
          const isOpen = open === f.id
          return (
            <div key={f.id} className="sec-row" onClick={() => setOpen(isOpen ? null : f.id)}>
              <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                {pill(SEV[f.severity].c + '1A', SEV[f.severity].c, SEV[f.severity].label)}
                <span style={{ fontWeight: 700, color: NAVY, fontSize: 14, flex: 1, minWidth: 220 }}>
                  {f.title}
                </span>
                {pill(STATUS[f.status].c + '1A', STATUS[f.status].c, STATUS[f.status].label)}
                <span style={{ color: '#94a3b8', fontSize: 12, fontFamily: 'ui-monospace,monospace' }}>{f.id}</span>
              </div>
              <div style={{ color: '#475569', fontSize: 13, lineHeight: 1.55, marginTop: 6 }}>
                {f.plain_english}
              </div>
              {isOpen && (
                <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px dashed #e5e7eb', display: 'grid', gap: 10 }}>
                  <div>
                    <div className="sec-lbl">The fix</div>
                    <div style={{ color: '#0F172A', fontSize: 13, lineHeight: 1.55 }}>{f.fix}</div>
                  </div>
                  <div style={{ display: 'flex', gap: 22, flexWrap: 'wrap' }}>
                    <div><div className="sec-lbl">Owner</div><div style={{ fontSize: 13, color: '#0F172A' }}>{f.owner}</div></div>
                    <div><div className="sec-lbl">Effort</div><div style={{ fontSize: 13, color: '#0F172A' }}>{EFFORT[f.effort] || f.effort}</div></div>
                    <div><div className="sec-lbl">Sprint</div><div style={{ fontSize: 13, color: '#0F172A' }}>{f.sprint}</div></div>
                    <div><div className="sec-lbl">Area</div><div style={{ fontSize: 13, color: '#0F172A' }}>{f.area}</div></div>
                  </div>
                  <div>
                    <div className="sec-lbl">Evidence</div>
                    <div style={{ fontSize: 12, color: '#475569', fontFamily: 'ui-monospace,monospace', lineHeight: 1.5 }}>{f.evidence}</div>
                  </div>
                  <div>
                    <div className="sec-lbl">Confidence</div>
                    <div style={{ fontSize: 12, color: '#475569', lineHeight: 1.5 }}>{f.confidence}</div>
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* Sprint plan */}
      <div className="sec-card" style={{ padding: '18px 20px', marginBottom: 18 }}>
        <div className="sec-h">The plan</div>
        <div style={{ display: 'grid', gap: 16 }}>
          {(data?.sprints || []).map(sp => (
            <div key={sp.sprint}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 10 }}>
                <div style={{ fontWeight: 700, color: NAVY, fontSize: 14 }}>{sp.title}</div>
                <div style={{ fontSize: 12, color: '#94a3b8' }}>{sp.done}/{sp.total} done</div>
              </div>
              <div style={{ margin: '6px 0 8px' }}><Bar pct={sp.pct} /></div>
              <div style={{ display: 'grid', gap: 4 }}>
                {sp.items.map(it => (
                  <div key={it.id} style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13, flexWrap: 'wrap' }}>
                    <span style={{ color: STATUS[it.status].c, fontWeight: 800 }}>
                      {it.status === 'fixed' ? '✓' : '○'}
                    </span>
                    <span style={{ color: '#334155', flex: 1, minWidth: 200 }}>{it.title}</span>
                    <span style={{ color: '#94a3b8', fontSize: 12 }}>{it.owner}</span>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Honest limits */}
      <div className="sec-card" style={{ padding: '18px 20px', borderLeft: `4px solid ${ORANGE}` }}>
        <div className="sec-h">What this review could not tell us</div>
        <p style={{ margin: '0 0 10px', color: '#64748b', fontSize: 13 }}>
          This was a review of the code. These questions need a live test or the
          infrastructure team, and are not covered by the percentage above.
        </p>
        <ul style={{ margin: 0, paddingLeft: 20, color: '#475569', fontSize: 13, lineHeight: 1.7 }}>
          {(data?.needs_live_test || []).map((n, i) => <li key={i}>{n}</li>)}
        </ul>
      </div>
    </div>
  )
}
