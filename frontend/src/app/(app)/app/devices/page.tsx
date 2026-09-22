'use client'
import { useEffect, useState } from 'react'
import { listDevices, revokeDevice, clearAppToken, AppApiError, type DeviceRow } from '../../api'
import { C, h, headerPad, card } from '../../ui'

export default function Devices() {
  const [rows, setRows] = useState<DeviceRow[] | null>(null)
  const load = () => listDevices().then(r => setRows(r.devices)).catch(() => setRows([]))
  useEffect(() => { load() }, [])
  const [err, setErr] = useState<string | null>(null)
  const off = async (d: DeviceRow) => {
    if (!confirm(`Switch off ${d.label}?`)) return
    setErr(null)
    try {
      await revokeDevice(d.id)
    } catch (e) {
      setErr(e instanceof AppApiError ? e.message : 'Could not switch that phone off. Try again.')
      return
    }
    if (d.current) { clearAppToken(); window.location.href = '/app/login'; return }
    load()
  }
  return (
    <main>
      <header style={{ padding: headerPad }}><h1 style={h(26)}>Signed-in devices</h1></header>
      {err && <p role="alert" style={{ color: C.red, margin: '0 16px 10px', fontSize: 14 }}>{err}</p>}
      <section style={{ padding: '0 16px', display: 'grid', gap: 10 }}>
        {rows === null && <p style={{ color: C.inkSoft }}>Loading…</p>}
        {rows?.length === 0 && <p style={{ color: C.inkSoft }}>No phone sessions found.</p>}
        {rows?.map(d => (
          <div key={d.id} className="oa-rise" style={{ ...card, padding: 14, display: 'flex', alignItems: 'center', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 600 }}>{d.label}{d.current ? ' · this phone' : ''}</div>
              <div style={{ fontSize: 12, color: C.inkSoft }}>Ends {new Date(d.expires_at).toLocaleDateString()}</div>
            </div>
            <button onClick={() => off(d)} className="oa-press" style={{ minHeight: 44, padding: '0 12px', borderRadius: 10, border: `1px solid ${C.line}`, background: C.card, color: C.red, fontWeight: 600 }}>Switch off</button>
          </div>
        ))}
      </section>
    </main>
  )
}
