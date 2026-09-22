'use client'
/**
 * ComplianceWorkbook — renders the DPO's uploaded DPA-2024 compliance workbook
 * (ROPA, systems inventory, controls, policies, gap tracker, incidents, training)
 * on the Data Protection dashboard, with a gap banner. The DPO uploads the .xlsx;
 * the backend (core/dpo_workbook) parses + strips staff PII from training sheets.
 */
import { useEffect, useRef, useState } from 'react'
import { apiFetch } from '@/lib/api'

const NAVY = '#0D1B2A', ORANGE = '#F4A623', RED = '#C0392B', GREEN = '#16A34A'

type Sheet = { type: 'table' | 'notes'; headers?: string[]; rows?: Record<string, string>[]; lines?: string[]; pii_withheld?: number }
type Summary = {
  gaps?: string[]
  ropa?: { total: number; validated: number; amber: number; red: number }
  systems?: { total: number; cross_border: number; no_dpa: number }
  compliance?: { total: number; gaps: number }
  incidents?: { total: number; open: number }
  policies?: { total: number; needs_attention: number }
  controls?: { total: number; not_ok: number }
  training?: { total: number; completed: number; outstanding: number; avg_score: number | null }
}
type WB = { has_workbook: boolean; file_name?: string; uploaded_at?: string; uploaded_by?: string | null; can_upload?: boolean; summary?: Summary; sheets?: Record<string, Sheet> }

// Order the panels the DPO cares about most; only those present render.
const PANEL_ORDER = ['Validated ROPA', 'Compliance Tracker', 'Alpha Systems Inventory',
  'Incidents', 'Policies & Supporting Docs', 'Security Controls', 'Systems by Department']

export function ComplianceWorkbook() {
  const [wb, setWb] = useState<WB | null>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [tab, setTab] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)

  const load = () => apiFetch<WB>('/dpo-workbook/').then(w => {
    setWb(w)
    if (w.sheets) { const first = PANEL_ORDER.find(n => w.sheets![n]?.type === 'table'); if (first) setTab(first) }
  }).catch(() => setWb({ has_workbook: false }))
  useEffect(() => { load() }, [])

  async function upload(f: File) {
    setBusy(true); setMsg('')
    try {
      const fd = new FormData(); fd.append('file', f)
      await apiFetch('/dpo-workbook/upload/', { method: 'POST', body: fd })
      setMsg('Workbook updated ✓'); load()
    } catch (e) { setMsg(e instanceof Error ? e.message : 'Upload failed') }
    finally { setBusy(false) }
  }

  if (!wb) return null
  const s = wb.summary || {}
  const canUp = !!wb.can_upload
  const tables = PANEL_ORDER.filter(n => wb.sheets?.[n]?.type === 'table')
  const cur = wb.sheets?.[tab]

  const uploadBtn = canUp && (
    <>
      <input ref={fileRef} type="file" accept=".xlsx,.xlsm" style={{ display: 'none' }}
        onChange={e => { const f = e.target.files?.[0]; if (f) upload(f); e.target.value = '' }} />
      <button onClick={() => fileRef.current?.click()} disabled={busy}
        style={{ background: NAVY, color: '#fff', border: 'none', borderRadius: 9, padding: '7px 14px', fontWeight: 700, fontSize: 12.5, cursor: busy ? 'wait' : 'pointer' }}>
        {busy ? 'Reading…' : wb.has_workbook ? 'Update workbook' : 'Upload workbook'}
      </button>
    </>
  )

  return (
    <div className="dpa-card" style={{ padding: 18, marginBottom: 18 }}>
      <style>{`.wb-tab{border:1px solid #d7dee6;background:#fff;color:#334155;border-radius:999px;padding:5px 12px;font-size:12.5px;font-weight:600;cursor:pointer;white-space:nowrap}
        .wb-tab.on{background:${NAVY};color:#fff;border-color:${NAVY}}
        .wb-t{width:100%;border-collapse:collapse;font-size:12.5px;min-width:520px;table-layout:fixed}
        .wb-t th{background:${NAVY};color:#fff;text-align:left;padding:7px 10px;font-weight:600;position:sticky;top:0;white-space:nowrap}
        .wb-t td{padding:7px 10px;border-top:1px solid #eef2f7;color:#334155;vertical-align:top;overflow-wrap:anywhere}
        .wb-tile{background:#f8fafc;border:1px solid #eef2f7;border-radius:12px;padding:10px 12px}`}</style>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 10 }}>
        <div>
          <h3 style={{ margin: 0, color: NAVY }}>Compliance workbook</h3>
          <p style={{ margin: '3px 0 0', color: '#94a3b8', fontSize: 12.5 }}>
            {wb.has_workbook ? `${wb.file_name} · uploaded ${String(wb.uploaded_at || '').slice(0, 10)}${wb.uploaded_by ? ' by ' + wb.uploaded_by : ''}` : 'Your DPA-2024 register — ROPA, systems, policies, incidents, training.'}
          </p>
        </div>
        {uploadBtn}
      </div>
      {msg && <p style={{ fontSize: 12.5, color: msg.includes('✓') ? GREEN : RED, margin: '8px 0 0' }}>{msg}</p>}

      {!wb.has_workbook && (
        <p style={{ color: '#64748b', fontSize: 13.5, margin: '12px 0 0' }}>
          No workbook uploaded yet.{canUp ? ' Upload your Data Protection Dashboard .xlsx and it appears here with a gap analysis.' : ' The DPO will upload it.'}
        </p>
      )}

      {wb.has_workbook && (
        <>
          {/* Gap analysis banner */}
          {(s.gaps?.length || 0) > 0 && (
            <div style={{ background: '#FEF3F2', border: '1px solid #fecaca', borderRadius: 12, padding: '12px 14px', margin: '14px 0' }}>
              <div style={{ fontWeight: 700, color: RED, fontSize: 13.5, marginBottom: 6 }}>
                {s.gaps!.length} gap{s.gaps!.length !== 1 ? 's' : ''} to close
              </div>
              <ul style={{ margin: 0, paddingLeft: 18, color: '#7f1d1d', fontSize: 13 }}>
                {s.gaps!.map((g, i) => <li key={i} style={{ marginBottom: 2 }}>{g}</li>)}
              </ul>
            </div>
          )}
          {(s.gaps?.length || 0) === 0 && (
            <div style={{ background: '#ECFDF5', border: '1px solid #a7f3d0', borderRadius: 12, padding: '10px 14px', margin: '14px 0', color: GREEN, fontWeight: 700, fontSize: 13.5 }}>
              No open gaps — the register is clean.
            </div>
          )}

          {/* Summary tiles */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(120px,1fr))', gap: 10, marginBottom: 14 }}>
            {s.ropa && <Tile n={`${s.ropa.validated}/${s.ropa.total}`} l="ROPA validated" bad={s.ropa.red > 0} />}
            {s.systems && <Tile n={s.systems.cross_border} l="Cross-border systems" />}
            {s.systems && <Tile n={s.systems.no_dpa} l="Systems w/o DPA" bad={s.systems.no_dpa > 0} />}
            {s.compliance && <Tile n={s.compliance.gaps} l="Compliance gaps" bad={s.compliance.gaps > 0} />}
            {s.incidents && <Tile n={s.incidents.open} l="Incidents open" bad={s.incidents.open > 0} />}
            {s.policies && <Tile n={`${s.policies.total - s.policies.needs_attention}/${s.policies.total}`} l="Policies current" bad={s.policies.needs_attention > 0} />}
            {s.training && <Tile n={`${s.training.completed}/${s.training.total}`} l={`Training done${s.training.avg_score != null ? ` · avg ${s.training.avg_score}%` : ''}`} bad={s.training.outstanding > 0} />}
          </div>

          {/* Sheet tabs + table */}
          <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap', marginBottom: 10 }}>
            {tables.map(n => (
              <button key={n} className={`wb-tab${tab === n ? ' on' : ''}`} onClick={() => setTab(n)}>
                {n.replace(' & Supporting Docs', '').replace('Alpha ', '')}{wb.sheets?.[n]?.rows ? ` (${wb.sheets![n].rows!.length})` : ''}
              </button>
            ))}
          </div>
          {cur?.type === 'table' && (
            <div style={{ overflowX: 'auto', border: '1px solid #eef2f7', borderRadius: 12, maxHeight: 460, overflowY: 'auto' }}>
              <table className="wb-t">
                <thead><tr>{cur.headers!.map(h => <th key={h}>{h}</th>)}</tr></thead>
                <tbody>
                  {cur.rows!.map((r, i) => (
                    <tr key={i}>{cur.headers!.map(h => {
                      const v = String(r[h] ?? '')
                      const red = v.includes('🔴'), amber = v.includes('🟠'), ok = v.includes('✅')
                      return <td key={h} style={{ color: red ? RED : amber ? '#B45309' : ok ? GREEN : undefined, fontWeight: (red || amber || ok) ? 700 : undefined }}>{v.length > 160 ? v.slice(0, 158) + '…' : v}</td>
                    })}</tr>
                  ))}
                  {cur.rows!.length === 0 && <tr><td colSpan={cur.headers!.length} style={{ color: '#94a3b8' }}>No rows.</td></tr>}
                </tbody>
              </table>
            </div>
          )}
          {wb.sheets?.['Quiz Register'] && (
            <p style={{ color: '#94a3b8', fontSize: 12, marginTop: 10 }}>
              Staff training details (names, scores) are held privately — only the totals above are shown here.
            </p>
          )}
        </>
      )}
    </div>
  )
}

function Tile({ n, l, bad }: { n: string | number; l: string; bad?: boolean }) {
  return (
    <div className="wb-tile">
      <div style={{ fontSize: 20, fontWeight: 800, color: bad ? RED : NAVY, lineHeight: 1 }}>{n}</div>
      <div style={{ fontSize: 11.5, color: '#64748b', marginTop: 3 }}>{l}</div>
    </div>
  )
}

export default ComplianceWorkbook
