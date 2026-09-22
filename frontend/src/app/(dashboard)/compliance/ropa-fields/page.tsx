'use client'
/**
 * ROPA — personal-data field detector (DPO Oratile, 2026-09-08).
 * Flags NEW personal-data fields (columns) across the Omni/Graphite schema into
 * a review queue, so the DPO confirms purpose/legal basis or dismisses them.
 * Reads /api/v1/ropa-field-scan/ (schema metadata only — never data values).
 */
import { useEffect, useState } from 'react'
import { apiFetch } from '@/lib/api'

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'

type Row = {
  app_label: string; model_name: string; field_name: string
  category: string; status: string; note: string; reviewed_by: string; reviewed: boolean
}
interface Payload {
  as_of: string
  summary: { total_detected: number; needs_review: number; flagged: number; confirmed: number; dismissed: number }
  fields: Row[]
}

const CAT_LABEL: Record<string, string> = {
  banking: 'Banking details', identity: 'Identity / ID', health: 'Health data',
  contact: 'Contact info', identity_name: 'Name',
}
const STATUS_STYLE: Record<string, { bg: string; fg: string; label: string }> = {
  flagged:      { bg: '#FEF2F2', fg: '#C0392B', label: 'Flagged' },
  needs_review: { bg: '#FFFBEB', fg: '#B45309', label: 'Needs review' },
  confirmed:    { bg: '#ECFDF5', fg: '#16A34A', label: 'Confirmed' },
  dismissed:    { bg: '#F1F5F9', fg: '#64748b', label: 'Not personal' },
}

export default function RopaFieldsPage() {
  const [d, setD] = useState<Payload | null>(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState('')

  const load = () => apiFetch<Payload>('/ropa-field-scan/').then(setD).catch(() => setErr('Data unavailable — check OMNI connection.'))
  useEffect(() => { load() }, [])

  async function review(r: Row, status: string) {
    const key = `${r.app_label}.${r.model_name}.${r.field_name}`
    setBusy(key)
    try {
      await apiFetch('/ropa-field-scan/', {
        method: 'POST',
        body: JSON.stringify({ app_label: r.app_label, model_name: r.model_name, field_name: r.field_name, status }),
      })
      await load()
    } catch { /* keep UI */ } finally { setBusy('') }
  }

  if (err) return <div style={{ padding: 24, color: '#C0392B' }}>{err}</div>
  if (!d) return <div style={{ padding: 24, color: '#64748b' }}>Scanning the schema…</div>
  const s = d.summary

  return (
    <div style={{ padding: '20px 24px', maxWidth: 1080, margin: '0 auto', fontFamily: 'Book Antiqua, Palatino, Georgia, serif' }}>
      <h1 style={{ margin: 0, color: NAVY, fontSize: 25 }}>ROPA — personal-data field detector</h1>
      <p style={{ margin: '4px 0 0', color: '#64748b', fontSize: 13.5 }}>
        New personal-data fields across Omni &amp; Graphite, flagged for your review. Column names only — no data is read. As at {d.as_of}.
      </p>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: 10, marginTop: 18 }}>
        {[
          { label: 'Detected', value: s.total_detected, color: NAVY },
          { label: 'Flagged', value: s.flagged, color: '#C0392B' },
          { label: 'Needs review', value: s.needs_review, color: ORANGE },
          { label: 'Confirmed', value: s.confirmed, color: '#16A34A' },
          { label: 'Not personal', value: s.dismissed, color: '#64748b' },
        ].map(t => (
          <div key={t.label} style={{ background: '#fff', border: '1px solid #eef2f7', borderLeft: `3px solid ${t.color}`, borderRadius: 12, padding: '12px 14px' }}>
            <div style={{ fontSize: 22, fontWeight: 700, color: NAVY, fontVariantNumeric: 'tabular-nums' }}>{t.value}</div>
            <div style={{ fontSize: 11.5, color: '#64748b' }}>{t.label}</div>
          </div>
        ))}
      </div>

      <div style={{ overflowX: 'auto', border: '1px solid #eef2f7', borderRadius: 12, marginTop: 18 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5, minWidth: 720 }}>
          <thead><tr>
            {['Field', 'Where it lives', 'Detected', 'Status', 'Action'].map(h => (
              <th key={h} style={{ background: NAVY, color: '#fff', textAlign: 'left', padding: '8px 10px', whiteSpace: 'nowrap' }}>{h}</th>
            ))}
          </tr></thead>
          <tbody>
            {d.fields.map(r => {
              const key = `${r.app_label}.${r.model_name}.${r.field_name}`
              const st = STATUS_STYLE[r.status] || STATUS_STYLE.needs_review
              const b = busy === key
              return (
                <tr key={key} style={{ opacity: b ? 0.5 : 1 }}>
                  <td style={{ padding: '8px 10px', borderTop: '1px solid #eef2f7' }}><b style={{ color: NAVY }}>{r.field_name}</b></td>
                  <td style={{ padding: '8px 10px', borderTop: '1px solid #eef2f7', color: '#64748b' }}>{r.app_label} · {r.model_name}</td>
                  <td style={{ padding: '8px 10px', borderTop: '1px solid #eef2f7', color: '#334155' }}>{CAT_LABEL[r.category] || r.category}</td>
                  <td style={{ padding: '8px 10px', borderTop: '1px solid #eef2f7' }}>
                    <span style={{ background: st.bg, color: st.fg, borderRadius: 999, padding: '2px 9px', fontSize: 11, fontWeight: 700 }}>{st.label}</span>
                  </td>
                  <td style={{ padding: '8px 10px', borderTop: '1px solid #eef2f7', whiteSpace: 'nowrap' }}>
                    <button disabled={b} onClick={() => review(r, 'confirmed')} style={btn('#16A34A')}>Confirm</button>
                    <button disabled={b} onClick={() => review(r, 'dismissed')} style={btn('#64748b')}>Not PII</button>
                  </td>
                </tr>
              )
            })}
            {d.fields.length === 0 && <tr><td colSpan={5} style={{ padding: 16, color: '#94a3b8', textAlign: 'center' }}>No personal-data fields detected.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function btn(color: string): React.CSSProperties {
  return { marginRight: 6, padding: '4px 10px', borderRadius: 8, border: `1px solid ${color}`, background: '#fff', color, fontSize: 11.5, fontWeight: 600, cursor: 'pointer' }
}
