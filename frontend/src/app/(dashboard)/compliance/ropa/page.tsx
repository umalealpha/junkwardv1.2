'use client'
/**
 * ROPA registry — DPO review queue + entry editor (Developer Build Brief).
 * One page serves the DPO (full, can confirm) and a department head (own dept
 * only, no confirm controls) — the API scopes and flags is_dpo. Suggested values
 * are shown distinct from confirmed; a suggestion is never pre-selected.
 */
import { useEffect, useState } from 'react'
import { apiFetch } from '@/lib/api'

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'

type Entry = {
  entry_id: string; source_table: string; source_field: string; detected_data_category: string
  flag_status: string; flag_status_label: string; flag_reasons: string[]
  suggested_lawful_basis: string; suggested_lawful_basis_label: string; suggested_lawful_basis_reason: string
  department: string; processing_activity: string; purpose_of_processing: string; data_subject_categories: string
  confirmed_lawful_basis: string; confirmed_lawful_basis_label: string
  processor_vendor: string | null; processor_vendor_name: string
  recipients_third_parties: string; cross_border_transfer: string; cross_border_details: string
  retention_period: string; security_measures: string; description_of_risks: string
  dpia_required: string; dpia_status: string
}
interface Payload {
  is_dpo: boolean
  summary: { total: number; by_status: Record<string, number>; labels: Record<string, string> }
  entries: Entry[]
}

const FLAG_COLOR: Record<string, string> = {
  draft_needs_review: ORANGE, flagged_incomplete: '#C0392B', confirmed: '#16A34A',
}
const BASES = [
  ['consent', 'Consent'], ['contract', 'Performance of a contract'], ['legal_obligation', 'Legal obligation'],
  ['vital_interests', 'Vital interests'], ['public_task', 'Public task'], ['legitimate_interests', 'Legitimate interests'],
]

export default function RopaRegistryPage() {
  const [d, setD] = useState<Payload | null>(null)
  const [err, setErr] = useState('')
  const [open, setOpen] = useState<string | null>(null)
  const [busy, setBusy] = useState('')

  const load = () => apiFetch<Payload>('/ropa/queue/').then(setD).catch(() => setErr('Data unavailable — check OMNI connection.'))
  useEffect(() => { load() }, [])

  async function scan() {
    setBusy('scan')
    try { await apiFetch('/ropa/scan/', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }); await load() }
    catch { /* */ } finally { setBusy('') }
  }
  async function save(e: Entry, patch: Record<string, unknown>) {
    setBusy(e.entry_id)
    try { await apiFetch(`/ropa/entries/${e.entry_id}/`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(patch) }); await load() }
    catch { /* */ } finally { setBusy('') }
  }

  if (err) return <div style={{ padding: 24, color: '#C0392B' }}>{err}</div>
  if (!d) return <div style={{ padding: 24, color: '#64748b' }}>Loading the ROPA queue…</div>
  const s = d.summary

  return (
    <div style={{ padding: '20px 24px', maxWidth: 1120, margin: '0 auto', fontFamily: 'Book Antiqua, Palatino, Georgia, serif' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h1 style={{ margin: 0, color: NAVY, fontSize: 26 }}>ROPA — records of processing</h1>
          <p style={{ margin: '4px 0 0', color: '#64748b', fontSize: 13.5 }}>
            {d.is_dpo ? 'DPO review queue — you confirm the lawful basis and risk.' : 'Your department’s processing activities.'}
          </p>
        </div>
        {d.is_dpo && (
          <button disabled={busy === 'scan'} onClick={scan} style={{ padding: '9px 16px', borderRadius: 10, border: 'none', background: NAVY, color: '#fff', fontSize: 13, fontWeight: 600, cursor: 'pointer' }}>
            {busy === 'scan' ? 'Scanning…' : 'Run detection scan'}
          </button>
        )}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: 10, marginTop: 18 }}>
        {[
          { k: 'total', label: 'Total activities', v: s.total, c: NAVY },
          { k: 'draft_needs_review', label: s.labels.draft_needs_review, v: s.by_status.draft_needs_review || 0, c: ORANGE },
          { k: 'flagged_incomplete', label: s.labels.flagged_incomplete, v: s.by_status.flagged_incomplete || 0, c: '#C0392B' },
          { k: 'confirmed', label: s.labels.confirmed, v: s.by_status.confirmed || 0, c: '#16A34A' },
        ].map(t => (
          <div key={t.k} style={{ background: '#fff', border: '1px solid #eef2f7', borderLeft: `3px solid ${t.c}`, borderRadius: 12, padding: '12px 14px' }}>
            <div style={{ fontSize: 22, fontWeight: 700, color: NAVY, fontVariantNumeric: 'tabular-nums' }}>{t.v}</div>
            <div style={{ fontSize: 11.5, color: '#64748b' }}>{t.label}</div>
          </div>
        ))}
      </div>

      <div style={{ marginTop: 18, display: 'grid', gap: 8 }}>
        {d.entries.map(e => {
          const isOpen = open === e.entry_id
          const b = busy === e.entry_id
          return (
            <div key={e.entry_id} style={{ background: '#fff', border: '1px solid #eef2f7', borderRadius: 12, overflow: 'hidden', opacity: b ? 0.6 : 1 }}>
              <div onClick={() => setOpen(isOpen ? null : e.entry_id)} style={{ padding: '11px 14px', cursor: 'pointer', display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                <span style={{ background: '#f1f5f9', color: FLAG_COLOR[e.flag_status] || '#64748b', borderRadius: 999, padding: '2px 9px', fontSize: 11, fontWeight: 700 }}>{e.flag_status_label}</span>
                <b style={{ color: NAVY }}>{e.processing_activity || `${e.source_table}.${e.source_field}`}</b>
                <span style={{ color: '#94a3b8', fontSize: 12 }}>{e.detected_data_category}{e.department ? ` · ${e.department}` : ''}</span>
                {e.flag_reasons.length > 0 && <span style={{ color: '#C0392B', fontSize: 11.5 }}>needs: {e.flag_reasons.join(', ')}</span>}
              </div>
              {isOpen && <EntryEditor e={e} isDpo={d.is_dpo} onSave={save} />}
            </div>
          )
        })}
        {d.entries.length === 0 && <div style={{ padding: 16, color: '#94a3b8', textAlign: 'center' }}>Nothing in the queue.</div>}
      </div>
    </div>
  )
}

function Field({ label, value }: { label: string; value: string }) {
  return <div><div style={{ fontSize: 10.5, color: '#94a3b8', textTransform: 'uppercase' }}>{label}</div><div style={{ color: NAVY, fontSize: 12.5 }}>{value || '—'}</div></div>
}

function EntryEditor({ e, isDpo, onSave }: { e: Entry; isDpo: boolean; onSave: (e: Entry, p: Record<string, unknown>) => void }) {
  const [f, setF] = useState({
    department: e.department, processing_activity: e.processing_activity, purpose_of_processing: e.purpose_of_processing,
    data_subject_categories: e.data_subject_categories, retention_period: e.retention_period,
    cross_border_transfer: e.cross_border_transfer, cross_border_details: e.cross_border_details,
    security_measures: e.security_measures, dpia_required: e.dpia_required,
  })
  const [basis, setBasis] = useState(e.confirmed_lawful_basis)
  const set = (k: string, v: string) => setF(p => ({ ...p, [k]: v }))
  const inp: React.CSSProperties = { width: '100%', padding: '6px 8px', border: '1px solid #e2e8f0', borderRadius: 8, fontSize: 12.5 }

  return (
    <div style={{ borderTop: '1px solid #eef2f7', padding: 14, background: '#fafbfc' }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px,1fr))', gap: 10, marginBottom: 12 }}>
        <Field label="Source (auto)" value={`${e.source_table}.${e.source_field}`} />
        <Field label="Detected category (auto)" value={e.detected_data_category} />
        <Field label="Processor / vendor" value={e.processor_vendor_name} />
      </div>

      {/* suggested vs confirmed lawful basis */}
      <div style={{ background: '#fff', border: '1px dashed #cbd5e1', borderRadius: 10, padding: '10px 12px', marginBottom: 12 }}>
        <div style={{ fontSize: 11, color: ORANGE, fontWeight: 700, textTransform: 'uppercase' }}>Suggested lawful basis</div>
        {e.suggested_lawful_basis
          ? <div style={{ fontStyle: 'italic', color: '#475569', fontSize: 12.5, margin: '3px 0' }}>{e.suggested_lawful_basis_label} — {e.suggested_lawful_basis_reason}</div>
          : <div style={{ color: '#94a3b8', fontSize: 12.5 }}>{e.suggested_lawful_basis_reason || 'No suggestion — needs manual determination.'}</div>}
        <div style={{ marginTop: 8, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={{ fontSize: 11.5, color: NAVY, fontWeight: 700 }}>Confirmed basis:</span>
          {isDpo ? (
            <>
              <select value={basis} onChange={ev => setBasis(ev.target.value)} style={{ ...inp, width: 'auto' }}>
                <option value="">— not confirmed —</option>
                {BASES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
              <button onClick={() => onSave(e, { ...f, confirmed_lawful_basis: basis })} style={{ padding: '5px 12px', borderRadius: 8, border: 'none', background: '#16A34A', color: '#fff', fontSize: 12, fontWeight: 600, cursor: 'pointer' }}>Confirm</button>
            </>
          ) : <span style={{ color: e.confirmed_lawful_basis ? '#16A34A' : '#94a3b8', fontSize: 12.5 }}>{e.confirmed_lawful_basis_label || 'Awaiting DPO confirmation'}</span>}
        </div>
      </div>

      {/* human fields */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px,1fr))', gap: 10 }}>
        {[['department', 'Department'], ['processing_activity', 'Processing activity'], ['purpose_of_processing', 'Purpose'],
          ['data_subject_categories', 'Data subjects'], ['retention_period', 'Retention period'], ['security_measures', 'Security measures']].map(([k, l]) => (
          <label key={k} style={{ fontSize: 11, color: '#64748b' }}>{l}
            <input value={(f as Record<string, string>)[k]} onChange={ev => set(k, ev.target.value)} style={inp} />
          </label>
        ))}
        <label style={{ fontSize: 11, color: '#64748b' }}>Cross-border transfer
          <select value={f.cross_border_transfer} onChange={ev => set('cross_border_transfer', ev.target.value)} style={inp}>
            <option value="">—</option><option value="No">No</option><option value="Yes">Yes</option>
          </select>
        </label>
        <label style={{ fontSize: 11, color: '#64748b' }}>DPIA required
          <select value={f.dpia_required} onChange={ev => set('dpia_required', ev.target.value)} style={inp}>
            <option value="">—</option><option value="No">No</option><option value="Pending">Pending</option><option value="Yes">Yes</option>
          </select>
        </label>
      </div>
      <div style={{ marginTop: 12 }}>
        <button onClick={() => onSave(e, f)} style={{ padding: '7px 16px', borderRadius: 9, border: `1px solid ${NAVY}`, background: '#fff', color: NAVY, fontSize: 12.5, fontWeight: 600, cursor: 'pointer' }}>Save details</button>
      </div>
    </div>
  )
}
