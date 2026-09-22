'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { apiFetch, getToken } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import TripDetail from './TripDetail'

interface Trip {
  label: string; distance_km: number; duration_minutes: number; score: number
  points: number; grade: string; feedback: string
  harsh_brakes: number; speeding_events: number; idle_minutes: number
}
interface Summary {
  driver: { id: string; name: string }
  score: number; km_total: number; trips_count: number; idle_pct: number
  points: number; tier: string; next_tier: string | null; points_to_next: number
  recent_trips: Trip[]
  ledger: { description: string; points: number; created_at: string }[]
}

interface LiveVehicle {
  name: string; lat: number | null; lng: number | null; speed: string | number
  moving: boolean; ignition_on: boolean; where: string; last_seen: string; odometer_km: string | number
}
interface LiveResp { configured: boolean; vehicles: LiveVehicle[]; count: number }

// ── Alpha light skin tokens (from the Stitch DESIGN.md) ───────────────────────
const NAVY = '#0D1B2A', ORANGE = '#F4A623', ORANGE_DK = '#9A640A', TEAL = '#3FA7B8'
const MUT = '#6B7280', HAIR = '#ECEEF2', CANVAS = '#F7F8FB', TRACK = '#EEF0F4'
const CARD_SHADOW = '0 1px 2px rgba(13,27,42,.05), 0 10px 28px rgba(13,27,42,.07)'

const scoreColor = (s: number) => (s >= 85 ? '#34C759' : s >= 70 ? ORANGE : '#F2545B')
// soft tint background + readable text for a grade/status, keyed to the score colour
const gradeTint = (s: number) =>
  s >= 85 ? { bg: '#E6F6EC', fg: '#1B7A3D' }
  : s >= 70 ? { bg: '#FDEFD7', fg: ORANGE_DK }
  : { bg: '#FCEAEA', fg: '#B42318' }

const card: React.CSSProperties = {
  background: '#fff', borderRadius: 20, boxShadow: CARD_SHADOW, border: `1px solid ${HAIR}`,
}
const eyebrow: React.CSSProperties = {
  fontSize: 11, letterSpacing: '.12em', textTransform: 'uppercase', color: MUT, fontWeight: 700,
}

export default function NexusPage() {
  const router = useRouter()
  const [s, setS] = useState<Summary | null>(null)
  const [live, setLive] = useState<LiveResp | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [openTrip, setOpenTrip] = useState<Trip | null>(null)

  const load = useCallback(async () => {
    try { setS(await apiFetch<Summary>('/nexus/summary/')) }
    catch (e) { setErr(e instanceof Error ? e.message : 'Failed to load') }
    try { setLive(await apiFetch<LiveResp>('/nexus/live/')) } catch { /* live is best-effort */ }
  }, [])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load()
  }, [load, router])

  const ringPct = s ? s.score : 0

  return (
    <div className="flex flex-col min-h-screen" style={{ background: CANVAS }}>
      <TopBar title="Nexus" breadcrumbs={[{ label: 'Drive Rewards' }, { label: 'Nexus' }]} />
      <div className="flex-1 p-6 max-w-5xl mx-auto w-full space-y-4" style={{ color: NAVY }}>
        {err && <div className="rounded-2xl p-4 text-sm" style={{ background: '#FCEAEA', border: '1px solid #F6C9C9', color: '#B42318' }}>{err}</div>}

        {/* Live tracking (WebFleet) */}
        {live?.configured && live.vehicles.length > 0 && (
          <div className="p-5" style={card}>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2" style={eyebrow}>
                <span className="inline-block w-2 h-2 rounded-full" style={{ background: '#34C759', boxShadow: '0 0 0 4px rgba(52,199,89,.18)' }} />
                Live tracking · WebFleet
              </div>
              <span style={{ fontSize: 12, color: MUT }}>{live.count} vehicle{live.count !== 1 ? 's' : ''}</span>
            </div>
            {live.vehicles.map((v, i) => (
              <div key={v.name} className="flex items-center gap-3 flex-wrap"
                   style={{ marginTop: 12, paddingTop: i ? 12 : 0, borderTop: i ? `1px solid ${HAIR}` : 'none' }}>
                <div style={{ fontSize: 22, fontWeight: 600 }}>{v.name}</div>
                <span className="px-2.5 py-1 rounded-full" style={{
                  fontSize: 12, fontWeight: 700,
                  background: v.moving ? '#E6F6EC' : '#EEF0F4',
                  color: v.moving ? '#1B7A3D' : MUT,
                }}>{v.moving ? `▶ moving · ${v.speed} km/h` : '⏸ parked'}</span>
                {v.where && <span style={{ fontSize: 13, color: '#374151' }}>📍 {v.where}</span>}
                <span style={{ fontSize: 11.5, color: MUT }}>last seen {v.last_seen}</span>
                {v.lat != null && v.lng != null && (
                  <a href={`https://www.openstreetmap.org/?mlat=${v.lat}&mlon=${v.lng}#map=15/${v.lat}/${v.lng}`}
                    target="_blank" rel="noreferrer" className="font-semibold" style={{ fontSize: 13, color: ORANGE_DK }}>view on map →</a>
                )}
              </div>
            ))}
          </div>
        )}

        {!s && !err && <p className="text-sm" style={{ color: MUT }}>Loading…</p>}
        {s && (
          <>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {/* score + driver + stats */}
              <div className="p-6" style={card}>
                <div style={eyebrow}>Driving performance</div>
                <div className="flex items-center gap-6 mt-4 flex-wrap">
                  <div className="relative grid place-items-center" style={{
                    width: 150, height: 150, borderRadius: '50%', flex: 'none',
                    background: `conic-gradient(${scoreColor(s.score)} ${ringPct}%, ${TRACK} 0)`,
                    transition: 'background 1s cubic-bezier(.16,1,.3,1)',
                  }}>
                    <div className="absolute rounded-full" style={{ inset: 13, background: '#fff' }} />
                    <div className="relative text-center">
                      <b style={{ fontSize: 44, fontWeight: 600, color: NAVY, lineHeight: 1 }}>{s.score}</b>
                      <small className="block" style={{ fontSize: 10, letterSpacing: '.12em', color: MUT, marginTop: 4 }}>SCORE / 100</small>
                    </div>
                  </div>
                  <div>
                    <h1 style={{ fontSize: 24, fontWeight: 600, color: NAVY }}>{s.driver.name}</h1>
                    <span className="inline-flex items-center gap-1.5 mt-2 px-3 py-1.5 rounded-full"
                      style={{ fontSize: 12.5, fontWeight: 700, background: '#FEF3DD', color: ORANGE_DK }}>
                      ★ {s.tier} driver
                    </span>
                  </div>
                </div>
                <div className="grid grid-cols-3 gap-3 mt-5">
                  {[[s.km_total, 'km tracked'], [s.trips_count, 'trips scored'], [`${s.idle_pct}%`, 'idle time']].map(([v, k], i) => (
                    <div key={i} className="rounded-2xl p-3 text-center" style={{ background: CANVAS, border: `1px solid ${HAIR}` }}>
                      <b style={{ fontSize: 22, fontWeight: 600, color: NAVY, display: 'block' }}>{v}</b>
                      <span style={{ fontSize: 11, color: MUT }}>{k}</span>
                    </div>
                  ))}
                </div>
              </div>
              {/* rewards engine */}
              <div className="p-6" style={card}>
                <div style={eyebrow}>Rewards engine</div>
                <div className="flex items-baseline gap-2 my-3">
                  <b style={{ fontSize: 40, fontWeight: 600, color: ORANGE }}>{s.points}</b>
                  <span style={{ color: MUT, fontSize: 13.5 }}>points{s.next_tier ? ` · ${s.points_to_next} to ${s.next_tier}` : ' · top tier'}</span>
                </div>
                <div className="rounded-full overflow-hidden" style={{ height: 9, background: TRACK }}>
                  <div style={{ height: '100%', width: `${Math.min(100, s.next_tier ? (s.points / (s.points + s.points_to_next)) * 100 : 100)}%`, background: ORANGE, borderRadius: 999, transition: 'width 1.2s cubic-bezier(.16,1,.3,1)' }} />
                </div>
                <div className="mt-4" style={{ borderTop: `1px solid ${HAIR}`, paddingTop: 12 }}>
                  {s.ledger.map((e, i) => (
                    <div key={i} className="flex justify-between items-center py-2" style={{ fontSize: 13, borderBottom: i < s.ledger.length - 1 ? `1px dashed ${HAIR}` : 'none' }}>
                      <span style={{ color: '#374151' }}>{e.description}</span>
                      <span className="font-bold" style={{ color: e.points >= 0 ? '#1B7A3D' : '#B42318' }}>{e.points >= 0 ? '+' : ''}{e.points}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            {/* recent trips */}
            <div className="p-6" style={card}>
              <div style={eyebrow}>Recent trips</div>
              <div className="grid gap-3 mt-4" style={{ gridTemplateColumns: 'repeat(auto-fill,minmax(230px,1fr))' }}>
                {s.recent_trips.map((t, i) => {
                  const tint = gradeTint(t.score)
                  return (
                    <button
                      key={i}
                      type="button"
                      onClick={() => setOpenTrip(t)}
                      className="rounded-2xl p-4 text-left w-full transition-transform active:scale-[0.99]"
                      style={{ background: CANVAS, border: `1px solid ${HAIR}`, cursor: 'pointer' }}
                    >
                      <div className="flex justify-between items-center mb-1.5">
                        <b style={{ fontSize: 15, fontWeight: 600, color: NAVY }}>{t.label || 'Trip'}</b>
                        <span className="px-2 py-0.5 rounded-full" style={{ fontSize: 11, fontWeight: 700, background: tint.bg, color: tint.fg }}>{t.grade || `${t.score}`}</span>
                      </div>
                      <div className="flex gap-3" style={{ fontSize: 12, color: MUT }}>
                        <span>{t.distance_km} km</span><span>{t.duration_minutes} min</span>
                        {t.points > 0 && <span style={{ color: '#1B7A3D', fontWeight: 600 }}>+{t.points} pts</span>}
                      </div>
                      {t.feedback && <div style={{ fontSize: 12, marginTop: 8, color: '#374151' }}>{t.feedback}</div>}
                      <div className="flex gap-1.5 mt-2 flex-wrap">
                        {t.harsh_brakes === 0 && t.speeding_events === 0
                          ? <span className="px-2 py-0.5 rounded-full font-semibold" style={{ fontSize: 10.5, background: '#E6F6EC', color: '#1B7A3D' }}>Smooth</span>
                          : <>
                              {t.harsh_brakes > 0 && <span className="px-2 py-0.5 rounded-full font-semibold" style={{ fontSize: 10.5, background: '#FDEFD7', color: ORANGE_DK }}>{t.harsh_brakes} hard brake{t.harsh_brakes > 1 ? 's' : ''}</span>}
                              {t.speeding_events > 0 && <span className="px-2 py-0.5 rounded-full font-semibold" style={{ fontSize: 10.5, background: '#FCEAEA', color: '#B42318' }}>Speeding</span>}
                            </>}
                      </div>
                      <div style={{ fontSize: 11, color: ORANGE_DK, fontWeight: 600, marginTop: 8 }}>View detail →</div>
                    </button>
                  )
                })}
              </div>
              <p style={{ fontSize: 12, color: MUT, marginTop: 16 }}>Each trip is scored on how safely and smoothly you drove. Better driving earns more reward points. Trips come straight from the vehicle tracker.</p>
            </div>
          </>
        )}
      </div>
      {openTrip && <TripDetail trip={openTrip} onClose={() => setOpenTrip(null)} />}
    </div>
  )
}
