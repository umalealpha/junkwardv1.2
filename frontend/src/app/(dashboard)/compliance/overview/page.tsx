'use client'
/**
 * AML / Compliance overview dashboard (CFO directive 2026-07-24, for the AML
 * officer Kakale Botana). Mirrors the Data Protection dashboard: a maintained
 * monitoring register + LIVE tiles read from modules that already exist, and
 * deep links to those modules (KYC + policy checks in Graphite; NBFIRA in the
 * Compliance module; risk/CAPA in ISO; breaches on Data Protection).
 * Access is enforced server-side (core.compliance_dashboard); this page also
 * gates the UI.
 */
import { useEffect, useState } from 'react'
import { apiFetch } from '@/lib/api'
import { KycCompletenessPanel } from '@/components/graphite/KycCompletenessPanel'

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'

type RegisterRow = { group: string; item: string; source: string; source_label: string; note: string }
type LinkRow = { group: string; label: string; href: string; external: boolean; note: string }
interface BrainBlock {
  activated: boolean
  fetched_ok?: boolean
  as_of: string | null
  updated_at?: string | null
  counts: Record<string, number>
  kyc: Record<string, unknown>
  ai_narrative: string
  ai_engine?: string
  note?: string
}
interface Payload {
  summary: { total_areas: number; live: number; graphite: number; excel: number; manual: number }
  register: RegisterRow[]
  brain?: BrainBlock
  regulatory: {
    capital: { as_of: string; car_percent: string | null; status: string } | null
    nbfira_returns: { total: number; submitted: number; not_submitted: number; latest_period: string | null; latest_status: string | null } | null
  } | null
  risk: { total: number; open: number; med_high_open: number; mitigated: number; closed: number } | null
  capa: { total: number; open: number; overdue: number; closed: number } | null
  breach: { total: number; open: number; overdue: number } | null
  links: LinkRow[]
  officer: { name: string; email: string }
  as_of: string
}

const SOURCE_COLOR: Record<string, string> = {
  live: '#16A34A', graphite: '#2563EB', excel: ORANGE, manual: '#7C3AED',
}
const chip = (s: string, label: string) => (
  <span style={{ background: (SOURCE_COLOR[s] || '#666') + '1A', color: SOURCE_COLOR[s] || '#666',
    border: `1px solid ${(SOURCE_COLOR[s] || '#666')}55`, borderRadius: 999, padding: '2px 10px',
    fontSize: 12, fontWeight: 600, whiteSpace: 'nowrap' }}>{label}</span>
)

const GROUP_ORDER = ['KYC', 'Regulatory', 'Risk', 'Unusual transactions', 'Operations', 'Monthly policy checks']

function Tile({ label, value, note, tone }: { label: string; value: string | number; note?: string; tone?: string }) {
  return (
    <div className="cmp-card" style={{ padding: '14px 16px', flex: '1 1 150px', minWidth: 150 }}>
      <div style={{ fontSize: 12, color: '#64748b', fontWeight: 600 }}>{label}</div>
      <div style={{ fontSize: 26, fontWeight: 800, color: tone || NAVY, marginTop: 2 }}>{value}</div>
      {note && <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 2 }}>{note}</div>}
    </div>
  )
}

export default function ComplianceOverviewPage() {
  const [allowed, setAllowed] = useState<boolean | null>(null)
  const [data, setData] = useState<Payload | null>(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    apiFetch<{ allowed: boolean }>('/compliance-dashboard/access/')
      .then(r => setAllowed(!!r.allowed)).catch(() => setAllowed(false))
  }, [])
  useEffect(() => {
    if (allowed) apiFetch<Payload>('/compliance-dashboard/').then(setData).catch(e => setErr(String(e?.message || e)))
  }, [allowed])

  if (allowed === null) return <div style={{ padding: 40, color: '#64748b' }}>Loading…</div>
  if (!allowed) return (
    <div style={{ maxWidth: 520, margin: '80px auto', textAlign: 'center', padding: 32,
      background: '#fff', border: '1px solid #e5e7eb', borderRadius: 16 }}>
      <div style={{ fontSize: 40 }}>🔒</div>
      <h2 style={{ color: NAVY, margin: '12px 0 6px' }}>Restricted</h2>
      <p style={{ color: '#64748b' }}>The Compliance / AML dashboard is for the AML officer, Compliance &amp; Risk, C-suite, HR and Finance.</p>
    </div>
  )

  const s = data?.summary
  const reg = data?.register || []
  const groups = GROUP_ORDER.filter(g => reg.some(r => r.group === g))
  const linkGroups = Array.from(new Set((data?.links || []).map(l => l.group)))

  return (
    <div style={{ maxWidth: 1080, margin: '0 auto', padding: '24px 20px 60px', animation: 'cmpFade .4s ease-out' }}>
      <style>{`@keyframes cmpFade{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
        .cmp-card{background:#fff;border:1px solid #e8ebef;border-radius:16px;box-shadow:0 1px 3px rgba(13,27,42,.06)}
        .cmp-row{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;padding:12px 16px;border-top:1px solid #f0f2f5}
        .cmp-row:first-child{border-top:none}
        .cmp-a{color:#2563EB;text-decoration:none;font-weight:600}
        .cmp-a:hover{text-decoration:underline}`}</style>

      {/* Header */}
      <div style={{ background: NAVY, borderRadius: 16, padding: '20px 24px', color: '#fff', marginBottom: 18 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8, alignItems: 'baseline' }}>
          <h1 style={{ margin: 0, fontSize: 24 }}>Compliance <span style={{ color: ORANGE }}>&amp; AML</span></h1>
          <span style={{ color: '#9fb0c3', fontSize: 13 }}>as of {data?.as_of || '…'}</span>
        </div>
        <p style={{ margin: '6px 0 0', color: '#c7d2de', fontSize: 13 }}>
          AML officer: {data?.officer?.name || '—'} ({data?.officer?.email})
        </p>
      </div>

      {err && <div className="cmp-card" style={{ padding: 16, marginBottom: 16, color: '#C0392B' }}>{err}</div>}

      {/* Compliance intelligence — nightly Alpha Brain feed + our AI's read */}
      {data?.brain && (
        <div className="cmp-card" style={{ padding: '16px 18px', marginBottom: 18,
          borderLeft: `4px solid ${data.brain.activated ? '#16A34A' : ORANGE}` }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
            <div style={{ fontWeight: 700, color: NAVY, fontSize: 14 }}>Compliance intelligence — Alpha Brain</div>
            <div style={{ fontSize: 12, color: '#94a3b8' }}>
              {data.brain.activated
                ? `updated ${data.brain.updated_at ? new Date(data.brain.updated_at).toLocaleString() : data.brain.as_of}${data.brain.ai_engine ? ` · ${data.brain.ai_engine}` : ''}`
                : 'awaiting activation'}
            </div>
          </div>
          {data.brain.ai_narrative
            ? <p style={{ margin: '8px 0 0', color: '#334155', fontSize: 14, lineHeight: 1.5 }}>{data.brain.ai_narrative}</p>
            : <p style={{ margin: '8px 0 0', color: '#94a3b8', fontSize: 13 }}>
                {data.brain.note
                  || (data.brain.activated
                        ? 'Figures updated; the plain-English summary was unavailable this run.'
                        : 'Nightly summary will appear here once Alpha Brain is switched on.')}
              </p>}
          {data.brain.counts && Object.keys(data.brain.counts).length > 0 && (
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 10 }}>
              {Object.entries(data.brain.counts).map(([k, v]) => (
                <span key={k} style={{ background: '#f1f5f9', color: NAVY, borderRadius: 8,
                  padding: '3px 10px', fontSize: 12, fontWeight: 600 }}>{k}: {v}</span>
              ))}
            </div>
          )}
          {!data.brain.activated && (
            <p style={{ margin: '8px 0 0', color: '#94a3b8', fontSize: 12 }}>
              KYC and monthly policy-check figures pull from Alpha Brain nightly (no customer data leaves; analysed by our own AI). Live once Pramod activates it.
            </p>
          )}
        </div>
      )}

      {/* Register source mix */}
      {s && (
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 18 }}>
          <Tile label="Areas monitored" value={s.total_areas} />
          <Tile label="Live in Omni" value={s.live} tone="#16A34A" />
          <Tile label="In Graphite" value={s.graphite} tone="#2563EB" note="link across" />
          <Tile label="On Excel" value={s.excel} tone={ORANGE} note="systemise later" />
          <Tile label="Periodic checks" value={s.manual} tone="#7C3AED" />
        </div>
      )}

      {/* Live tiles */}
      <h2 style={{ color: NAVY, fontSize: 15, margin: '4px 0 10px' }}>Live status</h2>
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 22 }}>
        {data?.regulatory?.capital
          ? <Tile label="Capital adequacy — CAR (ADIC)"
              value={data.regulatory.capital.car_percent ? `${data.regulatory.capital.car_percent}%` : '—'}
              note={`ADIC · last computed ${data.regulatory.capital.as_of} · ${data.regulatory.capital.status}`}
              tone={data.regulatory.capital.status === 'breach' ? '#C0392B' : '#16A34A'} />
          : <Tile label="Capital adequacy — CAR (ADIC)" value="—" note="open the NBFIRA page to compute" />}
        {data?.regulatory?.nbfira_returns
          ? <Tile label="NBFIRA returns submitted (ADIC)"
              value={`${data.regulatory.nbfira_returns.submitted}/${data.regulatory.nbfira_returns.total}`}
              note={data.regulatory.nbfira_returns.latest_period
                ? `latest ${data.regulatory.nbfira_returns.latest_period} · ${data.regulatory.nbfira_returns.latest_status}`
                : 'no returns yet'}
              tone={data.regulatory.nbfira_returns.not_submitted ? ORANGE : '#16A34A'} />
          : <Tile label="NBFIRA returns" value="—" />}
        {data?.risk
          ? <Tile label="Med/High risks open" value={data.risk.med_high_open}
              note={`${data.risk.open} open of ${data.risk.total} · ${data.risk.mitigated} mitigated`}
              tone={data.risk.med_high_open ? ORANGE : '#16A34A'} />
          : <Tile label="Risk register" value="—" />}
        {data?.capa
          ? <Tile label="Audit findings open (CAPA)" value={data.capa.open}
              note={`${data.capa.overdue} overdue · ${data.capa.closed} closed`}
              tone={data.capa.overdue ? '#C0392B' : (data.capa.open ? ORANGE : '#16A34A')} />
          : <Tile label="Audit findings (CAPA)" value="—" />}
        {data?.breach
          ? <Tile label="Data-privacy breaches open" value={data.breach.open}
              note={`${data.breach.overdue} overdue · ${data.breach.total} total`}
              tone={data.breach.overdue ? '#C0392B' : (data.breach.open ? ORANGE : '#16A34A')} />
          : <Tile label="Data-privacy breaches" value="—" />}
      </div>

      {/* Monitoring register */}
      <h2 style={{ color: NAVY, fontSize: 15, margin: '4px 0 10px' }}>Monitoring register</h2>
      {groups.map(g => (
        <div key={g} style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: '#475569', margin: '0 0 6px 2px' }}>{g}</div>
          <div className="cmp-card">
            {reg.filter(r => r.group === g).map((r, i) => (
              <div className="cmp-row" key={i}>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontWeight: 600, color: NAVY, fontSize: 14 }}>{r.item}</div>
                  <div style={{ color: '#64748b', fontSize: 12, marginTop: 2 }}>{r.note}</div>
                </div>
                {chip(r.source, r.source_label)}
              </div>
            ))}
          </div>
        </div>
      ))}

      {/* KYC completeness. Graphite now pushes this nightly, so the picture
          can sit on the compliance screen instead of only behind a deep link. */}
      <h2 style={{ color: NAVY, fontSize: 15, margin: '18px 0 10px' }}>KYC across the book</h2>
      <KycCompletenessPanel />

      {/* Deep links */}
      <h2 style={{ color: NAVY, fontSize: 15, margin: '18px 0 10px' }}>Open the source system</h2>
      {linkGroups.map(g => (
        <div key={g} style={{ marginBottom: 14 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: '#475569', margin: '0 0 6px 2px' }}>{g}</div>
          <div className="cmp-card">
            {(data?.links || []).filter(l => l.group === g).map((l, i) => (
              <div className="cmp-row" key={i}>
                <div style={{ minWidth: 0 }}>
                  <a className="cmp-a" href={l.href} target={l.external ? '_blank' : undefined}
                     rel={l.external ? 'noopener noreferrer' : undefined}>
                    {l.label}{l.external ? ' ↗' : ''}
                  </a>
                  {l.note && <div style={{ color: '#64748b', fontSize: 12, marginTop: 2 }}>{l.note}</div>}
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}

      <p style={{ color: '#94a3b8', fontSize: 12, marginTop: 18 }}>
        Read-only. The KYC figures above are Graphite's own nightly numbers, shown here rather than
        recalculated; the monthly policy control checks still live in Graphite and are linked, not duplicated.
      </p>
    </div>
  )
}
