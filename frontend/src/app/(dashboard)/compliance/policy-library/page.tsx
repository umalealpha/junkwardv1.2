'use client'
/**
 * Policy & Legal Framework Library (Developer Build Brief, DPO Oratile 2026-09-08).
 * The DPO's 73-row Botswana DPA 2024 mapping, each requirement linked to the
 * internal policy that governs it. Reads /api/v1/policy-library/. Read-only.
 */
import { useEffect, useMemo, useState } from 'react'
import { apiFetch } from '@/lib/api'

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'

type Link = {
  policy_id: string; policy_name: string; policy_status: string; policy_status_label: string
  specific_section: string; confirmed: boolean; is_placeholder: boolean; is_gap: boolean
}
type Req = {
  requirement_code: string; requirement_name: string; plain_description: string
  category: string; category_label: string; source_act: string; policies: Link[]
}
type Policy = {
  policy_id: string; policy_name: string; status: string; status_label: string
  version: string; is_placeholder: boolean; is_gap: boolean; gap_note: string
  next_review_due: string | null; requirement_count: number
}
interface Payload {
  as_of: string
  summary: {
    requirements_total: number; policies_total: number; policies_placeholder: number
    policies_gap: number; links_total: number; links_unconfirmed: number
    policy_status_counts: Record<string, number>; policy_status_labels: Record<string, string>
    categories: { key: string; label: string; count: number }[]
  }
  requirements: Req[]
  policies: Policy[]
}

const STATUS_COLOR: Record<string, string> = {
  draft: '#64748b', under_review: ORANGE, approved: '#2563EB',
  published: '#16A34A', archived: '#94a3b8', deprecated: '#C0392B',
}

export default function PolicyLibraryPage() {
  const [d, setD] = useState<Payload | null>(null)
  const [err, setErr] = useState('')
  const [q, setQ] = useState('')
  const [tab, setTab] = useState<'framework' | 'policies'>('framework')

  useEffect(() => {
    apiFetch<Payload>('/policy-library/').then(setD).catch(() => setErr('Data unavailable — check OMNI connection.'))
  }, [])

  const reqs = useMemo(() => {
    if (!d) return []
    const t = q.trim().toLowerCase()
    if (!t) return d.requirements
    return d.requirements.filter(r =>
      `${r.requirement_code} ${r.requirement_name} ${r.plain_description} ${r.policies.map(p => p.policy_name).join(' ')}`.toLowerCase().includes(t))
  }, [d, q])

  if (err) return <div style={{ padding: 24, color: '#C0392B' }}>{err}</div>
  if (!d) return <div style={{ padding: 24, color: '#64748b' }}>Loading the library…</div>
  const s = d.summary
  const cats = s.categories

  const StatusBadge = ({ st, label }: { st: string; label: string }) => (
    <span style={{ background: '#f1f5f9', color: STATUS_COLOR[st] || '#64748b', borderRadius: 999, padding: '1px 8px', fontSize: 10.5, fontWeight: 700 }}>{label}</span>
  )

  return (
    <div style={{ padding: '20px 24px', maxWidth: 1180, margin: '0 auto', fontFamily: 'Book Antiqua, Palatino, Georgia, serif' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h1 style={{ margin: 0, color: NAVY, fontSize: 26 }}>Policy &amp; Legal Framework Library</h1>
          <p style={{ margin: '4px 0 0', color: '#64748b', fontSize: 13.5 }}>
            {s.requirements_total} Botswana DPA 2024 requirements, linked to {s.policies_total} internal policies. As at {d.as_of}.
          </p>
        </div>
        <input value={q} onChange={e => setQ(e.target.value)} placeholder="Search requirements & policies…"
          style={{ padding: '9px 12px', border: '1px solid #e2e8f0', borderRadius: 10, fontSize: 13.5, minWidth: 260 }} />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: 10, marginTop: 18 }}>
        {[
          { label: 'Requirements', value: s.requirements_total, color: NAVY },
          { label: 'Policies', value: s.policies_total, color: NAVY },
          { label: 'Placeholder (to complete)', value: s.policies_placeholder, color: ORANGE },
          { label: 'Flagged gaps', value: s.policies_gap, color: '#C0392B' },
          { label: 'Links to confirm', value: s.links_unconfirmed, color: ORANGE },
        ].map(t => (
          <div key={t.label} style={{ background: '#fff', border: '1px solid #eef2f7', borderLeft: `3px solid ${t.color}`, borderRadius: 12, padding: '12px 14px' }}>
            <div style={{ fontSize: 22, fontWeight: 700, color: NAVY, fontVariantNumeric: 'tabular-nums' }}>{t.value}</div>
            <div style={{ fontSize: 11.5, color: '#64748b' }}>{t.label}</div>
          </div>
        ))}
      </div>

      <div style={{ display: 'flex', gap: 6, margin: '20px 0 12px' }}>
        {(['framework', 'policies'] as const).map(t => (
          <button key={t} onClick={() => setTab(t)} style={{
            padding: '7px 14px', borderRadius: 9, fontSize: 13, fontWeight: 600, cursor: 'pointer',
            border: `1px solid ${tab === t ? NAVY : '#e2e8f0'}`, background: tab === t ? NAVY : '#fff', color: tab === t ? '#fff' : '#334155',
          }}>{t === 'framework' ? 'Legal framework' : 'Internal policies'}</button>
        ))}
      </div>

      {tab === 'framework' && cats.map(c => {
        const rows = reqs.filter(r => r.category === c.key)
        if (!rows.length) return null
        return (
          <div key={c.key} style={{ marginBottom: 18 }}>
            <div style={{ color: NAVY, fontWeight: 700, fontSize: 14, margin: '10px 0 8px' }}>
              {c.label} <span style={{ color: '#94a3b8', fontWeight: 400 }}>· {rows.length}</span>
            </div>
            <div style={{ display: 'grid', gap: 8 }}>
              {rows.map(r => (
                <div key={r.requirement_code} style={{ background: '#fff', border: '1px solid #eef2f7', borderRadius: 12, padding: '12px 14px' }}>
                  <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
                    <b style={{ color: ORANGE, fontVariantNumeric: 'tabular-nums' }}>{r.requirement_code}</b>
                    <span style={{ color: NAVY, fontWeight: 600, fontSize: 13 }}>{r.requirement_name}</span>
                  </div>
                  {r.plain_description && <p style={{ margin: '6px 0', color: '#475569', fontSize: 12.5 }}>{r.plain_description}</p>}
                  <div style={{ fontSize: 11.5, color: '#64748b', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                    Governed by:
                    {r.policies.length ? r.policies.map((p, i) => (
                      <span key={i} style={{ display: 'inline-flex', gap: 5, alignItems: 'center' }}>
                        <b style={{ color: NAVY }}>{p.policy_name}{p.specific_section ? ` (${p.specific_section})` : ''}</b>
                        <StatusBadge st={p.policy_status} label={p.policy_status_label} />
                        {p.is_gap && <span style={{ color: '#C0392B', fontSize: 10.5 }}>gap</span>}
                        {!p.confirmed && <span style={{ color: ORANGE, fontSize: 10.5 }}>unconfirmed</span>}
                      </span>
                    )) : <span style={{ color: '#C0392B' }}>no policy linked</span>}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )
      })}

      {tab === 'policies' && (
        <div style={{ overflowX: 'auto', border: '1px solid #eef2f7', borderRadius: 12 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5, minWidth: 640 }}>
            <thead><tr>
              {['Policy', 'Status', 'Requirements', 'Next review', 'Note'].map(h => (
                <th key={h} style={{ background: NAVY, color: '#fff', textAlign: 'left', padding: '8px 10px', whiteSpace: 'nowrap' }}>{h}</th>
              ))}
            </tr></thead>
            <tbody>
              {d.policies.map(p => (
                <tr key={p.policy_id}>
                  <td style={{ padding: '8px 10px', borderTop: '1px solid #eef2f7' }}>
                    <b style={{ color: NAVY }}>{p.policy_name}</b>
                    {p.is_placeholder && <span style={{ color: ORANGE, fontSize: 10.5 }}> · placeholder</span>}
                  </td>
                  <td style={{ padding: '8px 10px', borderTop: '1px solid #eef2f7' }}><StatusBadge st={p.status} label={p.status_label} /></td>
                  <td style={{ padding: '8px 10px', borderTop: '1px solid #eef2f7', color: '#334155' }}>{p.requirement_count}</td>
                  <td style={{ padding: '8px 10px', borderTop: '1px solid #eef2f7', color: '#64748b', whiteSpace: 'nowrap' }}>{p.next_review_due || '—'}</td>
                  <td style={{ padding: '8px 10px', borderTop: '1px solid #eef2f7', color: p.is_gap ? '#C0392B' : '#94a3b8' }}>{p.gap_note || (p.is_gap ? 'gap' : '—')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p style={{ color: '#94a3b8', fontSize: 11.5, marginTop: 20 }}>
        Read-only. The ROPA module cites these via /api/v1/policy-library/citation/?code=… — real DPA 2024 clauses, not keyword guesses.
      </p>
    </div>
  )
}
