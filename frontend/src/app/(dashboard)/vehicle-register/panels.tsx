'use client'
/** Trips register table + Reports panel (with Alpha Direct-branded xlsx export). */
import React, { useCallback, useEffect, useState } from 'react'
import { API_BASE, apiFetchRaw } from '@/lib/api'
import {
  Btn, HAIR, MUT, NAVY, ORANGE_TXT, Opt, StatusPill, Trip, card, eyebrow,
  fmtWhen, jget,
} from './ui'
import { CorrectOdometerModal } from './forms'
import { localYmd } from '@/lib/utils'

const th: React.CSSProperties = { textAlign: 'left', padding: '10px 12px', fontSize: 11,
  letterSpacing: '.06em', textTransform: 'uppercase', color: MUT, fontWeight: 700, borderBottom: `1px solid ${HAIR}` }
const td: React.CSSProperties = { padding: '10px 12px', fontSize: 13.5, color: NAVY, borderBottom: `1px solid ${HAIR}`, verticalAlign: 'top' }
const num: React.CSSProperties = { ...td, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }

const STATUS_OPTS: Opt[] = [
  { value: '', label: 'All statuses' },
  { value: 'checked_out', label: 'Checked out' },
  { value: 'returned_pending', label: 'Returned — pending sign-off' },
  { value: 'closed', label: 'Closed' },
]

// ── Trips register ────────────────────────────────────────────────────────────
export function TripsPanel() {
  const [rows, setRows] = useState<Trip[]>([])
  const [status, setStatus] = useState('')
  const [driver, setDriver] = useState('')
  const [flagged, setFlagged] = useState(false)
  const [loading, setLoading] = useState(true)
  const [canCorrect, setCanCorrect] = useState(false)
  const [correcting, setCorrecting] = useState<Trip | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    const qs = new URLSearchParams()
    if (status) qs.set('status', status)
    if (driver) qs.set('driver', driver)
    if (flagged) qs.set('flagged', '1')
    const r = await jget<{ trips: Trip[]; can_correct_odometer: boolean }>(`/nexus/vehicle-register/trips/?${qs}`)
    setRows(r.trips); setCanCorrect(!!r.can_correct_odometer); setLoading(false)
  }, [status, driver, flagged])
  useEffect(() => { load() }, [load])

  const sel: React.CSSProperties = { padding: '8px 12px', borderRadius: 10, border: `1px solid ${HAIR}`, fontSize: 13.5, color: NAVY, background: '#fff' }
  return (
    <div style={card}>
      <div style={{ padding: 16, display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center', borderBottom: `1px solid ${HAIR}` }}>
        <select value={status} onChange={e => setStatus(e.target.value)} style={sel}>
          {STATUS_OPTS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
        <input value={driver} onChange={e => setDriver(e.target.value)} placeholder="Driver name…" style={sel} />
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13.5, color: NAVY, cursor: 'pointer' }}>
          <input type="checkbox" checked={flagged} onChange={e => setFlagged(e.target.checked)} style={{ accentColor: NAVY }} />
          Flagged only
        </label>
        <span style={{ marginLeft: 'auto', fontSize: 13, color: MUT }}>{rows.length} trip{rows.length !== 1 ? 's' : ''}</span>
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 760 }}>
          <thead><tr>
            <th style={th}>Vehicle</th><th style={th}>Driver</th><th style={th}>Purpose</th>
            <th style={th}>Destination</th><th style={th}>Out</th><th style={th}>In</th>
            <th style={{ ...th, textAlign: 'right' }}>Km</th><th style={th}>Status</th>
            {canCorrect && <th style={{ ...th, textAlign: 'right' }}></th>}
          </tr></thead>
          <tbody>
            {rows.map(t => (
              <tr key={t.id} style={{ background: t.flagged ? '#FFF9EE' : 'transparent' }}>
                <td style={td}><b>{t.registration}</b></td>
                <td style={td}>{t.driver_name}</td>
                <td style={td}>{t.purpose_label}{t.flagged && <span title={t.flag_reason} style={{ color: ORANGE_TXT }}> ⚑</span>}</td>
                <td style={td}>{t.destination}</td>
                <td style={td}>{fmtWhen(t.checkout_at)}{t.odometer_out != null && <span style={{ color: MUT }}> · {t.odometer_out.toLocaleString()}</span>}</td>
                <td style={td}>{t.checkin_at ? <>{fmtWhen(t.checkin_at)}{t.odometer_in != null && <span style={{ color: MUT }}> · {t.odometer_in.toLocaleString()}</span>}</> : (t.is_overdue ? <span style={{ color: '#B42318', fontWeight: 700 }}>overdue</span> : '—')}</td>
                <td style={num}>{t.distance_km != null ? t.distance_km.toLocaleString() : '—'}</td>
                <td style={td}><StatusPill status={t.status === 'closed' ? 'available' : t.status === 'checked_out' ? 'out' : 'maintenance'} /></td>
                {canCorrect && (
                  <td style={{ ...td, textAlign: 'right' }}>
                    <Btn small kind="ghost" onClick={() => setCorrecting(t)}>Correct</Btn>
                  </td>
                )}
              </tr>
            ))}
            {!loading && rows.length === 0 && (
              <tr><td style={{ ...td, textAlign: 'center', color: MUT, padding: 28 }} colSpan={canCorrect ? 9 : 8}>No trips match.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      {correcting && (
        <CorrectOdometerModal trip={correcting}
          onClose={() => setCorrecting(null)}
          onDone={() => { setCorrecting(null); load() }} />
      )}
    </div>
  )
}

// ── Reports ──────────────────────────────────────────────────────────────────
interface ReportResp {
  totals: { trips: number; km: number; flagged: number; vehicles: number; utilisation_pct: number }
  per_driver: { driver_name: string; trips: number; km: number; flagged: number }[]
  per_purpose: { purpose_label: string; trips: number }[]
  per_vehicle: { registration: string; make_model: string; status: string; trips: number; km: number; odometer_km: number | null }[]
  flagged: { registration: string; driver_name: string; purpose_label: string; reasons: string; checkout_at: string | null }[]
}

export function ReportsPanel() {
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [data, setData] = useState<ReportResp | null>(null)
  const [downloading, setDownloading] = useState(false)

  const qs = useCallback(() => {
    const q = new URLSearchParams()
    if (from) q.set('from', from); if (to) q.set('to', to)
    return q.toString()
  }, [from, to])
  const load = useCallback(async () => {
    setData(await jget<ReportResp>(`/nexus/vehicle-register/reports/?${qs()}`))
  }, [qs])
  useEffect(() => { load() }, [load])

  async function exportXlsx() {
    setDownloading(true)
    try {
      const res = await apiFetchRaw(`${API_BASE}/nexus/vehicle-register/reports/export/?${qs()}`)
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url; a.download = `vehicle-register-${localYmd(new Date())}.xlsx`
      document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url)
    } finally { setDownloading(false) }
  }

  const sel: React.CSSProperties = { padding: '8px 12px', borderRadius: 10, border: `1px solid ${HAIR}`, fontSize: 13.5, color: NAVY, background: '#fff' }
  const t = data?.totals
  return (
    <div className="space-y-4">
      <div style={{ ...card, padding: 16, display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'end' }}>
        <label style={{ fontSize: 12, color: MUT }}>From<br /><input type="date" value={from} onChange={e => setFrom(e.target.value)} style={sel} /></label>
        <label style={{ fontSize: 12, color: MUT }}>To<br /><input type="date" value={to} onChange={e => setTo(e.target.value)} style={sel} /></label>
        <div style={{ marginLeft: 'auto' }}>
          <Btn onClick={exportXlsx} disabled={downloading}>{downloading ? 'Preparing…' : '⬇ Export to Excel'}</Btn>
        </div>
      </div>

      {t && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(130px,1fr))', gap: 12 }}>
          {[
            ['Trips', t.trips], ['Km driven', t.km.toLocaleString()],
            ['Flagged', t.flagged], ['Vehicles', t.vehicles],
            ['Utilisation', `${t.utilisation_pct}%`],
          ].map(([k, v]) => (
            <div key={k} style={{ ...card, padding: 16 }}>
              <div style={eyebrow}>{k}</div>
              <div style={{ fontSize: 26, fontWeight: 700, color: NAVY, marginTop: 4 }}>{v}</div>
            </div>
          ))}
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(300px,1fr))', gap: 16 }}>
        <ReportTable title="Trips per driver" head={['Driver', 'Trips', 'Km', 'Flagged']}
          rows={(data?.per_driver ?? []).map(r => [r.driver_name, r.trips, r.km.toLocaleString(), r.flagged])} />
        <ReportTable title="Purpose breakdown" head={['Purpose', 'Trips']}
          rows={(data?.per_purpose ?? []).map(r => [r.purpose_label, r.trips])} />
      </div>
      <ReportTable title="Km per vehicle" head={['Registration', 'Vehicle', 'Status', 'Trips', 'Km', 'Odometer']}
        rows={(data?.per_vehicle ?? []).map(r => [r.registration, r.make_model, r.status, r.trips, r.km.toLocaleString(), r.odometer_km ?? '—'])} />
      <ReportTable title="Flagged / review" head={['Registration', 'Driver', 'Purpose', 'Reason', 'Checked out']}
        rows={(data?.flagged ?? []).map(r => [r.registration, r.driver_name, r.purpose_label, r.reasons, fmtWhen(r.checkout_at)])}
        empty="No flagged trips 🎉" />
    </div>
  )
}

function ReportTable({ title, head, rows, empty }: {
  title: string; head: string[]; rows: (string | number)[][]; empty?: string
}) {
  return (
    <div style={card}>
      <div style={{ padding: '14px 16px', fontWeight: 700, color: NAVY, borderBottom: `1px solid ${HAIR}` }}>{title}</div>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr>{head.map((h, i) => <th key={h} style={{ ...th, textAlign: i === 0 ? 'left' : 'right' }}>{h}</th>)}</tr></thead>
          <tbody>
            {rows.map((r, ri) => (
              <tr key={ri}>{r.map((c, ci) => <td key={ci} style={ci === 0 ? td : num}>{c}</td>)}</tr>
            ))}
            {rows.length === 0 && <tr><td style={{ ...td, textAlign: 'center', color: MUT, padding: 24 }} colSpan={head.length}>{empty ?? 'No data.'}</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}
