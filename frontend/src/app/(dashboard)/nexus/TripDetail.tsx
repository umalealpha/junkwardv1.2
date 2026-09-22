'use client'

// Nexus trip-detail panel — LIGHT Alpha skin, ported from the Google Stitch
// nexus_trip_detail screen (2026-06-25). There is NO per-trip backend route;
// this renders the Trip object the /nexus summary already returns, shown as a
// click-to-expand modal over the trip cards. No invented API calls.

import type { CSSProperties } from 'react'

export interface Trip {
  label: string; distance_km: number; duration_minutes: number; score: number
  points: number; grade: string; feedback: string
  harsh_brakes: number; speeding_events: number; idle_minutes: number
}

const NAVY = '#0D1B2A', ORANGE = '#F4A623', ORANGE_DK = '#9A640A'
const MUT = '#6B7280', HAIR = '#ECEEF2', CANVAS = '#F7F8FB', TRACK = '#EEF0F4'
const CARD_SHADOW = '0 1px 2px rgba(13,27,42,.05), 0 10px 28px rgba(13,27,42,.07)'
const SERIF = 'Spectral, "Book Antiqua", Georgia, serif'

const scoreColor = (s: number) => (s >= 85 ? '#34C759' : s >= 70 ? ORANGE : '#F2545B')
const gradeTint = (s: number) =>
  s >= 85 ? { bg: '#E6F6EC', fg: '#1B7A3D' }
  : s >= 70 ? { bg: '#FDEFD7', fg: ORANGE_DK }
  : { bg: '#FCEAEA', fg: '#B42318' }

const statBox: CSSProperties = {
  background: CANVAS, border: `1px solid ${HAIR}`, borderRadius: 16, padding: 16,
}

function Stat({ value, unit, label, accent }: { value: number | string; unit?: string; label: string; accent?: string }) {
  return (
    <div style={statBox}>
      <div style={{ fontFamily: SERIF, fontSize: 28, fontWeight: 600, color: accent ?? NAVY, lineHeight: 1 }}>
        {value}{unit && <span style={{ fontSize: 12, fontWeight: 700, color: MUT, marginLeft: 4 }}>{unit}</span>}
      </div>
      <div style={{ fontSize: 11, letterSpacing: '.06em', textTransform: 'uppercase', color: MUT, fontWeight: 600, marginTop: 8 }}>{label}</div>
    </div>
  )
}

function EventRow({ icon, text, badge, tone }: { icon: string; text: string; badge: string; tone: 'error' | 'warning' | 'success' }) {
  const tint =
    tone === 'error' ? { bg: '#FCEAEA', fg: '#B42318' }
    : tone === 'warning' ? { bg: '#FDEFD7', fg: ORANGE_DK }
    : { bg: '#E6F6EC', fg: '#1B7A3D' }
  return (
    <div className="flex items-center justify-between p-3 rounded-2xl" style={{ background: '#fff', border: `1px solid ${HAIR}` }}>
      <div className="flex items-center gap-3">
        <span className="grid place-items-center" style={{ width: 36, height: 36, borderRadius: '50%', background: tint.bg, fontSize: 17 }}>{icon}</span>
        <span style={{ fontSize: 13.5, fontWeight: 500, color: NAVY }}>{text}</span>
      </div>
      <span className="px-2 py-0.5 rounded" style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: '.04em', background: tint.bg, color: tint.fg }}>{badge}</span>
    </div>
  )
}

export default function TripDetail({ trip, onClose }: { trip: Trip; onClose: () => void }) {
  const ring = Math.max(0, Math.min(100, trip.score))
  const tint = gradeTint(trip.score)
  const smooth = trip.harsh_brakes === 0 && trip.speeding_events === 0

  return (
    <div
      className="fixed inset-0 z-50 flex items-end md:items-center justify-center p-0 md:p-6"
      style={{ background: 'rgba(13,27,42,.45)' }}
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="Trip detail"
    >
      <div
        className="w-full md:max-w-lg max-h-[90vh] overflow-y-auto"
        style={{ background: '#fff', borderRadius: 20, boxShadow: CARD_SHADOW, border: `1px solid ${HAIR}` }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* header */}
        <div className="flex items-center justify-between px-5 py-4" style={{ borderBottom: `1px solid ${HAIR}` }}>
          <h2 style={{ fontFamily: SERIF, fontSize: 20, fontWeight: 600, color: NAVY }}>{trip.label || 'Trip'}</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="grid place-items-center"
            style={{ width: 34, height: 34, borderRadius: '50%', background: CANVAS, border: `1px solid ${HAIR}`, color: MUT, fontSize: 18, lineHeight: 1 }}
          >×</button>
        </div>

        <div className="p-5 space-y-5" style={{ color: NAVY }}>
          {/* big score ring */}
          <div className="flex flex-col items-center text-center py-2">
            <div className="relative grid place-items-center" style={{
              width: 150, height: 150, borderRadius: '50%',
              background: `conic-gradient(${scoreColor(trip.score)} ${ring}%, ${TRACK} 0)`,
            }}>
              <div className="absolute rounded-full" style={{ inset: 13, background: '#fff' }} />
              <div className="relative">
                <span style={{ fontFamily: SERIF, fontSize: 46, fontWeight: 600, color: NAVY, lineHeight: 1 }}>{trip.score}</span>
              </div>
            </div>
            <div style={{ fontSize: 10, letterSpacing: '.12em', color: MUT, fontWeight: 700, marginTop: 10 }}>SCORE / 100</div>
            <span className="mt-3 px-4 py-1 rounded-full" style={{ fontSize: 11, fontWeight: 700, letterSpacing: '.06em', textTransform: 'uppercase', background: tint.bg, color: tint.fg }}>{trip.grade || `${trip.score}`}</span>
          </div>

          {/* feedback banner */}
          {trip.feedback && (
            <div className="flex items-start gap-3 p-4 rounded-2xl" style={{ background: '#EAF6F8', border: '1px solid #CDE9EE' }}>
              <span style={{ fontSize: 18, lineHeight: 1.3 }}>✓</span>
              <p style={{ fontSize: 13.5, color: '#155E6B' }}>{trip.feedback}</p>
            </div>
          )}

          {/* stat grid */}
          <div className="grid grid-cols-2 gap-3">
            <Stat value={trip.distance_km} unit="km" label="Distance" />
            <Stat value={trip.duration_minutes} unit="min" label="Duration" />
            <Stat value={trip.idle_minutes} unit="min" label="Idle" />
            <Stat value={trip.points > 0 ? `+${trip.points}` : `${trip.points}`} unit="pts" label="Points earned" accent={trip.points > 0 ? '#1B7A3D' : MUT} />
          </div>

          {/* events */}
          <div>
            <div style={{ fontSize: 11, letterSpacing: '.12em', textTransform: 'uppercase', color: MUT, fontWeight: 700, marginBottom: 10 }}>What happened</div>
            <div className="space-y-2.5">
              {trip.speeding_events > 0 && (
                <EventRow icon="⚡" tone="error" badge="CRITICAL"
                  text={`${trip.speeding_events} speeding event${trip.speeding_events > 1 ? 's' : ''}`} />
              )}
              {trip.harsh_brakes > 0 && (
                <EventRow icon="🛑" tone="warning" badge="CAUTION"
                  text={`${trip.harsh_brakes} hard brake${trip.harsh_brakes > 1 ? 's' : ''}`} />
              )}
              {smooth && (
                <EventRow icon="📈" tone="success" badge="SMOOTH" text="Smooth, safe driving — no incidents" />
              )}
              {!smooth && trip.harsh_brakes === 0 && trip.speeding_events === 0 && (
                <EventRow icon="📈" tone="success" badge="SMOOTH" text="No flagged events" />
              )}
            </div>
          </div>

          <p style={{ fontSize: 11.5, color: MUT }}>Scored from the vehicle tracker feed. There is no separate per-trip record — this is the detail behind the trip card.</p>
        </div>
      </div>
    </div>
  )
}
