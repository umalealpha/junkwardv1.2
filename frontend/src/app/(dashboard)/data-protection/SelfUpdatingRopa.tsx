'use client'
/**
 * SelfUpdatingRopa — the live-derived Records of Processing Activities.
 * Built from the real modules (payroll, claims, KYC…) via /ropa-auto/ and
 * reconciled against the DPO's uploaded Validated ROPA, so it flags the
 * activities the system runs that aren't recorded in her register yet.
 */
import { useEffect, useState } from 'react'
import { apiFetch } from '@/lib/api'

const NAVY = '#0D1B2A', GREEN = '#16A34A', AMBER = '#B45309'

type Act = {
  key: string; activity: string; basis: string; data: string
  recipients: string; retention: string; transfers: string
  live_count: number | null; live_label: string | null; recorded: boolean | null
}
type Payload = {
  version: string; workbook_uploaded_at: string | null; has_validated_ropa: boolean
  summary: { total: number; recorded: number; needs_recording: number }
  activities: Act[]
}

export function SelfUpdatingRopa() {
  const [d, setD] = useState<Payload | null>(null)
  useEffect(() => { apiFetch<Payload>('/ropa-auto/').then(setD).catch(() => {}) }, [])
  if (!d) return null
  const need = d.summary.needs_recording

  return (
    <div className="dpa-card" style={{ padding: 18, marginBottom: 18 }}>
      <style>{`.ra-t{width:100%;border-collapse:collapse;font-size:12.5px;min-width:520px;table-layout:fixed}
        .ra-t th{background:${NAVY};color:#fff;text-align:left;padding:7px 10px;font-weight:600;white-space:nowrap}
        .ra-t td{padding:8px 10px;border-top:1px solid #eef2f7;color:#334155;vertical-align:top;overflow-wrap:anywhere}
        .ra-pill{border-radius:999px;padding:2px 9px;font-size:11px;font-weight:700;white-space:nowrap}`}</style>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', flexWrap: 'wrap', gap: 8 }}>
        <h3 style={{ margin: 0, color: NAVY }}>Self-updating ROPA</h3>
        <span style={{ color: '#94a3b8', fontSize: 12.5 }}>
          built live from your systems · {d.summary.total} activities · {d.summary.recorded} recorded
        </span>
      </div>
      <p style={{ color: '#64748b', fontSize: 13, margin: '6px 0 12px' }}>
        Omni lists the processing activities it actually runs and checks them against your uploaded ROPA —
        so anything new shows up on its own, without keeping the register by hand.
      </p>

      {d.has_validated_ropa && need > 0 && (
        <div style={{ background: '#FEF3F2', border: '1px solid #fecaca', borderRadius: 12, padding: '10px 14px', marginBottom: 12, color: '#7f1d1d', fontSize: 13 }}>
          <b style={{ color: '#C0392B' }}>{need} activit{need === 1 ? 'y is' : 'ies are'} running but not in your ROPA yet.</b> They&rsquo;re marked <b>To record</b> below — add them to your workbook and re-upload.
        </div>
      )}
      {!d.has_validated_ropa && (
        <div style={{ background: '#FFFBEB', border: '1px solid #fde68a', borderRadius: 12, padding: '10px 14px', marginBottom: 12, color: AMBER, fontSize: 13 }}>
          Upload your Validated ROPA in the Compliance workbook above and each activity below will show whether it&rsquo;s recorded.
        </div>
      )}

      <div style={{ overflowX: 'auto', border: '1px solid #eef2f7', borderRadius: 12 }}>
        <table className="ra-t">
          <thead><tr>
            <th>Processing activity</th><th>Live</th><th>Lawful basis</th>
            <th>Personal data</th><th>Retention</th><th>Status</th>
          </tr></thead>
          <tbody>
            {d.activities.map(a => (
              <tr key={a.key}>
                <td style={{ fontWeight: 600, color: NAVY }}>{a.activity}</td>
                <td style={{ whiteSpace: 'nowrap' }}>
                  {a.live_count != null
                    ? <><b style={{ color: NAVY }}>{a.live_count.toLocaleString()}</b><div style={{ color: '#94a3b8', fontSize: 10.5 }}>{a.live_label}</div></>
                    : <span style={{ color: '#94a3b8' }}>tracked</span>}
                </td>
                <td>{a.basis}</td>
                <td style={{ color: '#64748b' }}>{a.data}</td>
                <td style={{ color: '#64748b' }}>{a.retention}</td>
                <td>
                  {a.recorded === true && <span className="ra-pill" style={{ background: '#ECFDF5', color: GREEN }}>Recorded ✓</span>}
                  {a.recorded === false && <span className="ra-pill" style={{ background: '#FEF2F2', color: '#C0392B' }}>To record</span>}
                  {a.recorded === null && <span className="ra-pill" style={{ background: '#F1F5F9', color: '#64748b' }}>—</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export default SelfUpdatingRopa
