'use client'

/**
 * /fleet — Alpha Direct's own company cars, tracked via Cartrack (CFO 2026-07-10).
 * Reads GET /api/v1/nexus/fleet/ (the synced FleetVehicle register). Shows each
 * vehicle's latest position, moving/parked state, driver and odometer, with an
 * embedded map for the selected car. Falls back to a clear "connect Cartrack"
 * state until the API credentials are set; demo rows are clearly labelled.
 */
import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { apiFetch, getToken } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'

interface Vehicle {
  registration: string; make: string; model: string; description: string
  lat: number | null; lng: number | null; speed: number | null
  moving: boolean | null; ignition_on: boolean | null; odometer_km: number | null
  where: string; driver: string; last_seen: string; is_demo: boolean
  updated_at: string | null
}
interface FleetResp { configured: boolean; count: number; demo: boolean; vehicles: Vehicle[] }

const NAVY = '#0D1B2A', ORANGE_DK = '#9A640A', MUT = '#6B7280', HAIR = '#ECEEF2'
const CANVAS = '#F7F8FB'
const CARD_SHADOW = '0 1px 2px rgba(13,27,42,.05), 0 10px 28px rgba(13,27,42,.07)'
const card: React.CSSProperties = { background: '#fff', borderRadius: 20, boxShadow: CARD_SHADOW, border: `1px solid ${HAIR}` }
const eyebrow: React.CSSProperties = { fontSize: 11, letterSpacing: '.12em', textTransform: 'uppercase', color: MUT, fontWeight: 700 }

function osmEmbed(lat: number, lng: number): string {
  const d = 0.02
  const bbox = `${lng - d}%2C${lat - d}%2C${lng + d}%2C${lat + d}`
  return `https://www.openstreetmap.org/export/embed.html?bbox=${bbox}&layer=mapnik&marker=${lat}%2C${lng}`
}

export default function FleetPage() {
  const router = useRouter()
  const [resp, setResp] = useState<FleetResp | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [sel, setSel] = useState<string | null>(null)

  const load = useCallback(async () => {
    try { setResp(await apiFetch<FleetResp>('/nexus/fleet/')) }
    catch (e) { setErr(e instanceof Error ? e.message : 'Failed to load fleet') }
  }, [])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load()
  }, [load, router])

  const vehicles = resp?.vehicles ?? []
  const withPos = vehicles.filter(v => v.lat != null && v.lng != null)
  const selected = vehicles.find(v => v.registration === sel) ?? withPos[0] ?? vehicles[0] ?? null
  const movingCount = vehicles.filter(v => v.moving).length

  return (
    <div className="flex flex-col min-h-screen" style={{ background: CANVAS }}>
      <TopBar title="Company Fleet" breadcrumbs={[{ label: 'Assets' }, { label: 'Fleet tracking' }]} />
      <div className="flex-1 p-6 max-w-5xl mx-auto w-full space-y-4" style={{ color: NAVY }}>
        {err && <div className="rounded-2xl p-4 text-sm" style={{ background: '#FCEAEA', border: '1px solid #F6C9C9', color: '#B42318' }}>{err}</div>}

        {/* status banner */}
        {resp && !resp.configured && (
          <div className="rounded-2xl p-4 text-sm" style={{ background: '#FDEFD7', border: '1px solid #F3D8A6', color: ORANGE_DK }}>
            <b>Cartrack not connected yet.</b> Add the Cartrack API login to start
            seeing live positions of the company cars. The page below is a preview.
          </div>
        )}
        {resp?.demo && (
          <div className="rounded-2xl p-3 text-xs" style={{ background: '#EEF0F4', border: `1px solid ${HAIR}`, color: MUT }}>
            Showing <b>sample vehicles</b> for preview — these are not real cars. Real
            vehicles appear automatically once Cartrack is connected.
          </div>
        )}

        {/* header stats */}
        <div className="p-5 flex items-center justify-between flex-wrap gap-3" style={card}>
          <div className="flex items-center gap-2" style={eyebrow}>
            <span className="inline-block w-2 h-2 rounded-full" style={{ background: '#34C759', boxShadow: '0 0 0 4px rgba(52,199,89,.18)' }} />
            Company fleet · Cartrack
          </div>
          <div style={{ fontSize: 13, color: MUT }}>
            <b style={{ color: NAVY }}>{vehicles.length}</b> vehicle{vehicles.length !== 1 ? 's' : ''}
            {vehicles.length > 0 && <> · <b style={{ color: '#1B7A3D' }}>{movingCount}</b> moving now</>}
          </div>
        </div>

        {vehicles.length === 0 && resp && (
          <div className="p-8 text-center" style={card}>
            <div style={{ fontSize: 15, fontWeight: 600 }}>No vehicles yet</div>
            <p style={{ fontSize: 13, color: MUT, marginTop: 6 }}>
              Connect Cartrack (or run a preview) to see the company cars here.
            </p>
          </div>
        )}

        {/* map for the selected vehicle */}
        {selected && selected.lat != null && selected.lng != null && (
          <div className="overflow-hidden" style={card}>
            <div className="px-5 pt-4 pb-2 flex items-center justify-between flex-wrap gap-2">
              <div style={{ fontSize: 18, fontWeight: 600 }}>{selected.registration}
                <span style={{ fontSize: 13, color: MUT, fontWeight: 400 }}> · {[selected.make, selected.model].filter(Boolean).join(' ')}</span>
              </div>
              {selected.where && <span style={{ fontSize: 13, color: '#374151' }}>📍 {selected.where}</span>}
            </div>
            <iframe
              key={`${selected.lat},${selected.lng}`}
              title={`Map — ${selected.registration}`}
              src={osmEmbed(selected.lat, selected.lng)}
              style={{ width: '100%', height: 340, border: 'none' }}
              loading="lazy"
            />
          </div>
        )}

        {/* vehicle list */}
        {vehicles.length > 0 && (
          <div className="p-2" style={card}>
            {vehicles.map((v, i) => {
              const isSel = selected?.registration === v.registration
              return (
                <button key={v.registration} onClick={() => setSel(v.registration)}
                  className="w-full text-left flex items-center gap-3 flex-wrap rounded-xl transition-colors"
                  style={{ padding: '12px 14px', background: isSel ? '#F7F8FB' : 'transparent',
                           borderTop: i ? `1px solid ${HAIR}` : 'none', cursor: 'pointer' }}>
                  <div style={{ fontSize: 16, fontWeight: 600, minWidth: 120 }}>{v.registration}</div>
                  <span style={{ fontSize: 12, color: MUT, minWidth: 110 }}>{[v.make, v.model].filter(Boolean).join(' ') || '—'}</span>
                  <span className="px-2.5 py-1 rounded-full" style={{
                    fontSize: 12, fontWeight: 700,
                    background: v.moving ? '#E6F6EC' : '#EEF0F4',
                    color: v.moving ? '#1B7A3D' : MUT,
                  }}>{v.moving ? `▶ moving${v.speed != null ? ` · ${Math.round(v.speed)} km/h` : ''}` : '⏸ parked'}</span>
                  {v.where && <span style={{ fontSize: 13, color: '#374151' }}>📍 {v.where}</span>}
                  {v.driver && <span style={{ fontSize: 12.5, color: MUT }}>👤 {v.driver}</span>}
                  {v.odometer_km != null && <span style={{ fontSize: 12, color: MUT }}>{Math.round(v.odometer_km).toLocaleString()} km</span>}
                  {v.last_seen && <span style={{ fontSize: 11.5, color: MUT, marginLeft: 'auto' }}>last seen {v.last_seen}</span>}
                  {v.lat != null && v.lng != null && (
                    <a href={`https://www.openstreetmap.org/?mlat=${v.lat}&mlon=${v.lng}#map=15/${v.lat}/${v.lng}`}
                      onClick={e => e.stopPropagation()} target="_blank" rel="noreferrer"
                      className="font-semibold" style={{ fontSize: 13, color: ORANGE_DK }}>open →</a>
                  )}
                </button>
              )
            })}
          </div>
        )}

        {!resp && !err && <p className="text-sm" style={{ color: MUT }}>Loading…</p>}
      </div>
    </div>
  )
}
