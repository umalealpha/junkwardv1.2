'use client'

/** /m/drive — Alpha Nexus Drive (Stitch design) + Click & Drive trip tracking.
 * Tap to track a drive: GPS → distance / harsh braking / idle → server score.
 * Raw coordinates never leave the phone; only aggregate numbers are sent. */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Navigation, Square, RefreshCw } from 'lucide-react'
import { getDrive, postDriveTrip, type DriveResp, type DriveTripResult } from '../../api'
import { TripTracker, type TripLive } from './tracker'
import { C, serif, sans, h, card, pill, headerPad } from '../../ui'

function scoreColor(s: number) { return s >= 80 ? C.teal : s >= 60 ? C.orange : C.orangeDeep }
function fmtDur(min: number) { const s = Math.floor(min * 60); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}` }

function Ring({ value }: { value: number | null }) {
  const v = value == null ? 0 : Math.max(0, Math.min(100, value))
  const r = 76, circ = 2 * Math.PI * r, dash = (v / 100) * circ
  return (
    <div style={{ position: 'relative', width: 200, height: 200, margin: '0 auto' }}>
      <svg viewBox="0 0 200 200" style={{ transform: 'rotate(-90deg)' }}>
        <circle cx="100" cy="100" r={r} fill="none" stroke="#EEF0F3" strokeWidth="14" />
        <circle cx="100" cy="100" r={r} fill="none" stroke={value == null ? '#EEF0F3' : scoreColor(v)} strokeWidth="14"
          strokeLinecap="round" strokeDasharray={`${dash} ${circ}`} />
      </svg>
      <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center' }}>
        <span style={{ fontFamily: serif, fontWeight: 800, fontSize: 46, color: C.ink }}>{value ?? '—'}</span>
        <span style={{ fontSize: 11, letterSpacing: '0.14em', color: C.inkSoft }}>SAFE SCORE</span>
      </div>
    </div>
  )
}

export default function CustomerDrive() {
  const [data, setData] = useState<DriveResp | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [phase, setPhase] = useState<'idle' | 'acquiring' | 'tracking' | 'result'>('idle')
  const [live, setLive] = useState<TripLive | null>(null)
  const [result, setResult] = useState<DriveTripResult | null>(null)
  const [busy, setBusy] = useState(false)
  const trackerRef = useRef<TripTracker | null>(null)

  const load = useCallback(() => { getDrive().then(setData).catch(e => setError(e instanceof Error ? e.message : 'Failed to load')) }, [])
  useEffect(() => { load() }, [load])

  // Stop the GPS watch (and release the wake lock) if the user leaves this
  // screen mid-trip — otherwise the watch leaks and keeps the screen awake.
  useEffect(() => () => { trackerRef.current?.stop() }, [])

  // Trip-detection assist (web slice of "automatic trip detection"): if
  // location permission is ALREADY granted, quietly watch while idle on this
  // screen; two consecutive fixes above driving speed → offer one-tap start.
  // Never triggers the permission prompt itself, stops the moment a real trip
  // starts or the page hides. Full background auto-detection needs the native
  // app and is a separate workstream.
  const [moving, setMoving] = useState(false)
  const preWatch = useRef<number | null>(null)
  const fastFixes = useRef(0)
  useEffect(() => {
    if (phase !== 'idle' || typeof navigator === 'undefined' || !navigator.geolocation) { setMoving(false); return }
    let cancelled = false
    const clear = () => { if (preWatch.current != null) { navigator.geolocation.clearWatch(preWatch.current); preWatch.current = null } }
    const perms = (navigator as unknown as { permissions?: { query(o: { name: string }): Promise<{ state: string }> } }).permissions
    if (!perms) return
    perms.query({ name: 'geolocation' }).then(p => {
      if (cancelled || p.state !== 'granted') return
      preWatch.current = navigator.geolocation.watchPosition(pos => {
        const kmh = pos.coords.speed != null && pos.coords.speed >= 0 ? pos.coords.speed * 3.6 : 0
        fastFixes.current = kmh >= 20 ? fastFixes.current + 1 : 0
        if (fastFixes.current >= 2) setMoving(true)
      }, () => { /* silent — assist only */ }, { enableHighAccuracy: false, maximumAge: 5000, timeout: 30000 })
    }).catch(() => { /* permissions API unavailable — no assist */ })
    const onHide = () => { if (document.visibilityState !== 'visible') clear() }
    document.addEventListener('visibilitychange', onHide)
    return () => { cancelled = true; clear(); document.removeEventListener('visibilitychange', onHide); fastFixes.current = 0 }
  }, [phase])

  const startDrive = () => {
    // Double-tap guard: a second tap while a tracker exists would spawn a
    // second GPS watch + wake lock that nothing ever stops (battery drain).
    if (trackerRef.current) return
    setError(null); setResult(null); setLive(null)
    const tk = new TripTracker(); trackerRef.current = tk
    // Show an "acquiring GPS" state immediately; only flip to the live tracking
    // UI once a real fix lands (onFirstFix). If permission is denied or GPS is
    // unavailable, onError fires and we drop straight back to idle — no more
    // blank "Recording" card stuck forever with no way out.
    const ok = tk.start(
      setLive,
      msg => { setError(msg); setPhase('idle'); trackerRef.current = null },
      () => setPhase(p => (p === 'acquiring' ? 'tracking' : p)),
    )
    if (ok) setPhase('acquiring')
    else trackerRef.current = null
  }
  const stopDrive = async () => {
    // Take the tracker atomically — a double-tap on "Stop & score" found the
    // ref still set and posted the trip twice (double points on a race).
    const tk = trackerRef.current; if (!tk) return
    trackerRef.current = null
    const f = tk.stop()
    setBusy(true); setError(null)
    try {
      const r = await postDriveTrip({
        startedAt: tk.startedAtIso,
        distanceKm: +f.distanceKm.toFixed(3), durationMin: +f.durationMin.toFixed(2),
        idleMinutes: +f.idleMinutes.toFixed(2), harshEvents: f.harshEvents, maxSpeed: +f.maxSpeed.toFixed(1),
      })
      setResult(r); setPhase('result'); load()
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not save your trip'); setPhase('idle') }
    finally { setBusy(false) }
  }

  return (
    <div style={{ background: C.surface, minHeight: '100vh', fontFamily: sans }}>
      <header style={{ textAlign: 'center', padding: headerPad, background: C.card, borderBottom: `1px solid ${C.line}` }}>
        <span style={{ fontFamily: serif, fontWeight: 800, fontSize: 21, color: C.ink }}>Alpha Nexus</span>
      </header>

      <main style={{ padding: 16 }}>
        {error && <div style={{ background: '#FEF2F2', border: '1px solid #FECACA', color: '#B91C1C', fontSize: 13, padding: '10px 12px', borderRadius: 12, marginBottom: 12 }}>{error}</div>}

        {/* Click & Drive */}
        <div style={{ background: `linear-gradient(160deg, ${C.navy2}, ${C.navy})`, borderRadius: 24, padding: 22, color: '#fff', boxShadow: '0 18px 40px rgba(15,28,44,0.28)' }}>
          {phase === 'idle' && moving && (
            <div style={{ background: 'rgba(45,212,191,0.14)', border: '1px solid rgba(45,212,191,0.5)', borderRadius: 14, padding: '10px 14px', marginBottom: 14, display: 'flex', alignItems: 'center', gap: 10 }}>
              <span style={{ fontSize: 18 }}>🚗</span>
              <span style={{ flex: 1, fontSize: 13, color: '#fff' }}>Looks like you&apos;re on the move — score this drive?</span>
              <button onClick={startDrive} style={{ border: 'none', borderRadius: 999, padding: '8px 16px', background: C.orange, color: '#fff', fontWeight: 700, fontSize: 13, cursor: 'pointer' }}>Start</button>
            </div>
          )}
          {phase === 'idle' && (
            <div style={{ textAlign: 'center' }}>
              <p style={{ ...h(22), color: '#fff' }}>Click &amp; Drive</p>
              <p style={{ color: 'rgba(255,255,255,0.62)', fontSize: 13, margin: '8px 16px 14px' }}>Track a drive — we score your braking, idling and speed, and award safe-driving points. Your location stays on your phone.</p>
              {/* Permission primer: telling the user WHY before the OS prompt
                  appears markedly lifts allow-rates. Most testers never reached
                  the driving feature — often the location prompt was declined. */}
              <p style={{ color: 'rgba(45,212,191,0.95)', fontSize: 12, margin: '0 20px 16px', display: 'flex', gap: 6, alignItems: 'flex-start', justifyContent: 'center' }}>
                <Navigation size={13} style={{ flexShrink: 0, marginTop: 2 }} />
                <span>When you tap Start, your phone will ask for location — tap <b style={{ color: '#fff' }}>Allow</b> so we can track the drive.</span>
              </p>
              <button onClick={startDrive} style={{ ...primaryBtn, margin: '0 auto' }}><Navigation size={18} /> Start drive</button>
            </div>
          )}
          {phase === 'acquiring' && (
            <div style={{ textAlign: 'center', padding: '8px 0' }}>
              <p style={{ ...h(20), color: '#fff' }}>Getting your location…</p>
              <p style={{ color: 'rgba(255,255,255,0.62)', fontSize: 13, margin: '8px 20px 18px' }}>Allow location when your phone asks. Keep this screen open — tracking starts the moment we have a GPS fix.</p>
              <button onClick={() => { trackerRef.current?.stop(); trackerRef.current = null; setPhase('idle') }}
                style={{ ...primaryBtn, margin: '0 auto', background: 'rgba(255,255,255,0.14)' }}>Cancel</button>
            </div>
          )}
          {phase === 'tracking' && live && (
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
                <span style={pill('rgba(45,212,191,0.18)', C.tealLight)}>● Recording</span>
                <span style={{ fontFamily: serif, fontWeight: 800, fontSize: 26 }}>{fmtDur(live.durationMin)}</span>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10, textAlign: 'center', marginBottom: 16 }}>
                <Stat label="km" value={live.distanceKm.toFixed(2)} />
                <Stat label="km/h" value={Math.round(live.speedKmh).toString()} />
                <Stat label="harsh" value={live.harshEvents.toString()} />
              </div>
              <button onClick={stopDrive} disabled={busy} style={{ ...primaryBtn, width: '100%', background: '#fff', color: C.navy }}>
                <Square size={16} /> {busy ? 'Scoring…' : 'Stop & score'}
              </button>
            </div>
          )}
          {phase === 'result' && result && (
            <div style={{ textAlign: 'center' }}>
              <p style={{ color: 'rgba(255,255,255,0.6)', fontSize: 12, letterSpacing: '0.1em', margin: 0 }}>TRIP SCORE</p>
              <p style={{ fontFamily: serif, fontWeight: 800, fontSize: 56, margin: '4px 0 0', color: result.trip.score >= 70 ? C.tealLight : C.orange }}>{result.trip.score}</p>
              <p style={{ margin: '0 0 6px', fontWeight: 700 }}>{result.trip.band}</p>
              <p style={{ color: 'rgba(255,255,255,0.75)', fontSize: 13, margin: '0 0 4px' }}>
                {result.trip.distanceKm} km · {fmtDur(result.trip.durationMin)} · {result.trip.harshEvents} harsh
                {result.trip.maxSpeed == null ? ' · peak speed not reliable' : ` · max ${result.trip.maxSpeed} km/h`}
              </p>
              {result.trip.pointsAwarded > 0 && <p style={{ color: C.tealLight, fontWeight: 700, fontSize: 15, margin: '6px 0 0' }}>+{result.trip.pointsAwarded} points</p>}
              <button onClick={startDrive} style={{ ...primaryBtn, margin: '16px auto 0' }}><RefreshCw size={16} /> Drive again</button>
            </div>
          )}
        </div>

        {/* Overall safe score */}
        <div style={{ position: 'relative', paddingTop: 18 }}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/brand/nexus/drive.png" alt="" width={58} height={58} style={{ position: 'absolute', right: 14, top: 12 }} />
          <Ring value={data?.avgScore ?? null} />
        </div>
        <p style={{ textAlign: 'center', color: C.inkSoft, fontSize: 13, margin: '8px 24px 8px' }}>Your safe-driving score across recent trips.</p>
        <div style={{ display: 'flex', gap: 10, justifyContent: 'center', marginBottom: 8 }}>
          <span style={{ ...pill('#fff', C.ink), border: `1px solid ${C.line}` }}>◈ Safe Driver</span>
          <span style={{ ...pill('#fff', C.ink), border: `1px solid ${C.line}` }}>Telematics</span>
        </div>

        {/* Driving profile — honest, no sugar-coating */}
        {data?.profile && data.profile.trips > 0 && (() => {
          const p = data.profile
          const bad = p.band === 'Dangerous' || p.band === 'Risky'
          const bandColor = p.band === 'Dangerous' ? '#B91C1C' : p.band === 'Risky' ? C.orangeDeep
            : p.band === 'Inconsistent' ? C.orange : p.band === 'Solid' ? C.teal : '#16a34a'
          return (
            <div style={{ ...card, padding: 18, marginTop: 20, borderLeft: `5px solid ${bandColor}` }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <h2 style={{ ...h(20) }}>Your driving profile</h2>
                <span style={{ ...pill(bad ? '#FEECEC' : '#EAF6F4', bandColor) }}>{p.band}</span>
              </div>
              <p style={{ fontSize: 14, lineHeight: 1.5, color: bad ? '#B91C1C' : C.ink, margin: '12px 0 14px', fontWeight: bad ? 600 : 400 }}>{p.verdict}</p>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10, textAlign: 'center', marginBottom: p.flags.length ? 14 : 0 }}>
                <PStat label="trips" value={String(p.trips)} />
                <PStat label="total km" value={String(p.totalKm)} />
                <PStat label="avg score" value={p.avgScore == null ? '—' : String(p.avgScore)} />
                {p.harshPer100km == null
                  ? <PStat label={p.harshEvents === 1 ? 'hard event' : 'hard events'} value={String(p.harshEvents)} />
                  : <PStat label="harsh /100km" value={String(p.harshPer100km)} />}
                <PStat label="idle %" value={String(p.idlePct)} />
                <PStat label="top km/h" value={p.topSpeed > 0 ? String(p.topSpeed) : '—'} />
              </div>
              {p.excludedTrips > 0 && (
                <p style={{ fontSize: 12, color: C.inkSoft, margin: '0 0 12px' }}>
                  {p.excludedTrips} recording{p.excludedTrips > 1 ? 's' : ''} left out of this profile —
                  too little ground or time to grade, so {p.excludedTrips > 1 ? 'they were' : 'it was'} not
                  counted. {p.excludedTrips > 1 ? 'They are' : 'It is'} still listed below.
                </p>
              )}
              {p.flags.map((f, i) => (
                <p key={i} style={{ fontSize: 13, color: '#B91C1C', margin: '4px 0 0', display: 'flex', gap: 6 }}>
                  <span>⚠</span>{f}
                </p>
              ))}
            </div>
          )
        })()}

        {/* Trip history */}
        <h2 style={{ ...h(22), margin: '22px 0 12px' }}>Recent trips</h2>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {(!data || (data.trips.length === 0 && data.scores.length === 0)) &&
            <p style={{ color: C.inkSoft, fontSize: 13 }}>No trips yet. Tap <b>Start drive</b> above to record one.</p>}
          {data?.trips.map((t, i) => (
            <div key={`t${i}`} style={{ ...card, padding: 16, opacity: t.counted ? 1 : 0.62 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                <span style={{ fontFamily: serif, fontWeight: 700, fontSize: 17, color: C.ink }}>{new Date(t.startedAt).toLocaleDateString()} · {new Date(t.startedAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
                {t.counted
                  ? t.pointsAwarded > 0 && <span style={{ fontWeight: 700, color: C.teal, fontSize: 14 }}>+{t.pointsAwarded} pts</span>
                  : <span style={{ ...pill('#F1F3F5', C.inkSoft), fontSize: 11 }}>not counted</span>}
              </div>
              <div style={{ display: 'flex', gap: 16, marginTop: 8, fontSize: 13, color: C.inkSoft }}>
                {t.counted && <span><b style={{ color: scoreColor(t.score) }}>{t.score}</b>/100</span>}
                <span>{t.distanceKm} km</span>
                <span>{fmtDur(t.durationMin)}</span>
                {t.counted && t.harshEvents > 0 && <span style={{ color: C.orangeDeep }}>{t.harshEvents} harsh</span>}
              </div>
              {!t.counted && t.notCountedWhy &&
                <p style={{ fontSize: 12, color: C.inkSoft, margin: '8px 0 0' }}>{t.notCountedWhy}</p>}
            </div>
          ))}
          {data?.scores.map((s, i) => (
            <div key={`s${i}`} style={{ ...card, padding: 16 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                <span style={{ fontFamily: serif, fontWeight: 700, fontSize: 17, color: C.ink }}>{s.period}{s.vehicleReg ? ` · ${s.vehicleReg}` : ''}</span>
                <span style={{ fontWeight: 700, color: C.teal, fontSize: 14 }}>+{s.pointsAwarded} pts</span>
              </div>
              <div style={{ display: 'flex', gap: 16, marginTop: 8, fontSize: 13, color: C.inkSoft }}>
                <span><b style={{ color: scoreColor(s.score) }}>{s.score}</b>/100</span>
                <span>{s.distanceKm} km</span>
                {s.harshEvents > 0 && <span style={{ color: C.orangeDeep }}>{s.harshEvents} harsh</span>}
              </div>
            </div>
          ))}
        </div>
      </main>
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div style={{ fontFamily: serif, fontWeight: 800, fontSize: 24 }}>{value}</div>
      <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.55)' }}>{label}</div>
    </div>
  )
}

function PStat({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ background: '#F6F7F9', borderRadius: 12, padding: '10px 6px' }}>
      <div style={{ fontFamily: serif, fontWeight: 800, fontSize: 20, color: C.ink }}>{value}</div>
      <div style={{ fontSize: 10, color: C.inkSoft }}>{label}</div>
    </div>
  )
}

const primaryBtn: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 8,
  padding: '13px 26px', borderRadius: 999, border: 'none',
  background: `linear-gradient(135deg, ${C.orange}, ${C.orangeDeep})`, color: '#fff',
  fontWeight: 700, fontSize: 15, cursor: 'pointer', fontFamily: sans,
}
