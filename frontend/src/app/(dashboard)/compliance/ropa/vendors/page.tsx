'use client'
/**
 * ROPA — vendor register (Developer Build Brief). Each vendor shows a SUGGESTED
 * risk score/level with itemised reasoning; the DPO confirms the level.
 */
import { useEffect, useState } from 'react'
import { apiFetch } from '@/lib/api'

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'

type Factor = { factor: string; points: number }
type Vendor = {
  vendor_id: string; vendor_name: string; dpa_contract_signed: boolean; sub_processors_used: boolean
  last_review_date: string | null; suggested_risk_score: number; suggested_risk_level: string
  suggested_risk_reason: Factor[]; confirmed_risk_level: string; linked_activities: number
}

const RISK_COLOR: Record<string, string> = { Low: '#16A34A', Medium: ORANGE, High: '#C0392B' }

export default function VendorRegisterPage() {
  const [rows, setRows] = useState<Vendor[] | null>(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState('')
  const [name, setName] = useState('')

  const load = () => apiFetch<{ vendors: Vendor[] }>('/ropa/vendors/').then(d => setRows(d.vendors)).catch(() => setErr('Data unavailable — check OMNI connection.'))
  useEffect(() => { load() }, [])

  async function add() {
    if (!name.trim()) return
    setBusy('add')
    try { await apiFetch('/ropa/vendors/', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ vendor_name: name.trim() }) }); setName(''); await load() }
    catch { /* */ } finally { setBusy('') }
  }
  async function patch(v: Vendor, body: Record<string, unknown>) {
    setBusy(v.vendor_id)
    try { await apiFetch(`/ropa/vendors/${v.vendor_id}/`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }); await load() }
    catch { /* */ } finally { setBusy('') }
  }

  if (err) return <div style={{ padding: 24, color: '#C0392B' }}>{err}</div>
  if (!rows) return <div style={{ padding: 24, color: '#64748b' }}>Loading vendors…</div>

  return (
    <div style={{ padding: '20px 24px', maxWidth: 1080, margin: '0 auto', fontFamily: 'Book Antiqua, Palatino, Georgia, serif' }}>
      <h1 style={{ margin: 0, color: NAVY, fontSize: 26 }}>ROPA — vendor register</h1>
      <p style={{ margin: '4px 0 16px', color: '#64748b', fontSize: 13.5 }}>Processor risk is suggested from the record; the DPO confirms the level.</p>

      <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        <input value={name} onChange={e => setName(e.target.value)} placeholder="Add a vendor…" style={{ padding: '8px 12px', border: '1px solid #e2e8f0', borderRadius: 10, fontSize: 13, minWidth: 240 }} />
        <button disabled={busy === 'add'} onClick={add} style={{ padding: '8px 16px', borderRadius: 10, border: 'none', background: NAVY, color: '#fff', fontSize: 13, fontWeight: 600, cursor: 'pointer' }}>Add</button>
      </div>

      <div style={{ display: 'grid', gap: 10 }}>
        {rows.map(v => (
          <div key={v.vendor_id} style={{ background: '#fff', border: '1px solid #eef2f7', borderRadius: 12, padding: '13px 15px', opacity: busy === v.vendor_id ? 0.6 : 1 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
              <b style={{ color: NAVY, fontSize: 14 }}>{v.vendor_name}</b>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <span style={{ fontSize: 12, color: '#64748b' }}>Suggested:</span>
                <span style={{ background: '#f1f5f9', color: RISK_COLOR[v.suggested_risk_level] || '#64748b', borderRadius: 999, padding: '2px 10px', fontSize: 12, fontWeight: 700 }}>{v.suggested_risk_level || '—'} · {v.suggested_risk_score}</span>
              </div>
            </div>
            <div style={{ fontSize: 12, color: '#64748b', margin: '6px 0' }}>
              DPA signed: <b style={{ color: v.dpa_contract_signed ? '#16A34A' : '#C0392B' }}>{v.dpa_contract_signed ? 'Yes' : 'No'}</b>
              {' · '}Sub-processors: {v.sub_processors_used ? 'Yes' : 'No'}{' · '}Linked activities: {v.linked_activities}
              {' · '}Last review: {v.last_review_date || 'never'}
            </div>
            {v.suggested_risk_reason?.length > 0 && (
              <div style={{ fontSize: 11.5, color: '#94a3b8' }}>Why: {v.suggested_risk_reason.map(f => `${f.factor} (+${f.points})`).join(' · ')}</div>
            )}
            <div style={{ marginTop: 10, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <span style={{ fontSize: 11.5, color: NAVY, fontWeight: 700 }}>Confirmed level:</span>
              {(['Low', 'Medium', 'High'] as const).map(lvl => (
                <button key={lvl} onClick={() => patch(v, { confirmed_risk_level: lvl })} style={{
                  padding: '4px 12px', borderRadius: 8, fontSize: 11.5, fontWeight: 600, cursor: 'pointer',
                  border: `1px solid ${RISK_COLOR[lvl]}`, background: v.confirmed_risk_level === lvl ? RISK_COLOR[lvl] : '#fff',
                  color: v.confirmed_risk_level === lvl ? '#fff' : RISK_COLOR[lvl],
                }}>{lvl}</button>
              ))}
              <button onClick={() => patch(v, { dpa_contract_signed: !v.dpa_contract_signed })} style={{ marginLeft: 8, padding: '4px 10px', borderRadius: 8, border: '1px solid #e2e8f0', background: '#fff', color: '#334155', fontSize: 11.5, cursor: 'pointer' }}>
                Toggle DPA signed
              </button>
            </div>
          </div>
        ))}
        {rows.length === 0 && <div style={{ padding: 16, color: '#94a3b8', textAlign: 'center' }}>No vendors yet.</div>}
      </div>
    </div>
  )
}
