/**
 * tracker.ts — Click & Drive GPS trip tracker.
 *
 * Watches the phone's location, computes live trip AGGREGATES — distance,
 * duration, idle time, harsh-event count, max speed — and never keeps the raw
 * coordinate track (DPA: only the numbers leave the device, via /drive/trip/).
 *
 * Hard-won mobile-GPS rules baked in here:
 *  - Duration is measured from GPS fix timestamps (wall clock), NOT
 *    performance.now(), which FREEZES while an iPhone is locked/backgrounded
 *    (a 40-min drive was reading a few seconds).
 *  - Coarse fixes (cell/wi-fi triangulation) are discarded on accuracy, so a
 *    stationary phone doesn't accrue "distance" from 500 m jitter jumps.
 *  - Callbacks that arrive < MIN_DT_MS apart, or after a > MAX_GAP_MS gap
 *    (screen-lock / tunnel / signal loss), never fabricate speed or harsh
 *    events — the old code floored dt to 1 ms and manufactured huge speeds.
 *  - A real device-reported 0 km/h is honoured (phone at a red light) instead
 *    of being overwritten by a derived value.
 *  - A Screen Wake Lock keeps the fix stream alive while the page is visible.
 */

export interface TripLive {
  distanceKm: number
  durationMin: number
  speedKmh: number
  harshEvents: number
  idleMinutes: number
  maxSpeed: number
}

const HARSH_DELTA_KMH_PER_S = 11   // |Δspeed| over 1s above this = harsh brake/accel
const IDLE_SPEED_KMH = 3           // below this counts as idling
const ACC_MAX_M = 35               // ignore fixes coarser than this (cell/wi-fi jitter)
const MIN_DT_MS = 500              // ignore duplicate/burst callbacks (no fake dt=0 speeds)
const MAX_GAP_MS = 20000           // a gap this big = lock/tunnel/signal loss, not driving
const SPEED_CEIL_KMH = 220         // clamp derived speed — GPS glitches spike to 1000s

function haversineM(aLat: number, aLng: number, bLat: number, bLng: number): number {
  const R = 6371000, toRad = Math.PI / 180
  const dLat = (bLat - aLat) * toRad, dLng = (bLng - aLng) * toRad
  const s = Math.sin(dLat / 2) ** 2 +
    Math.cos(aLat * toRad) * Math.cos(bLat * toRad) * Math.sin(dLng / 2) ** 2
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(s)))
}

export class TripTracker {
  private watchId: number | null = null
  private firstT = 0                 // first fix timestamp (wall clock, ms)
  private lastT = 0                  // latest fix timestamp (wall clock, ms)
  private prev: { t: number; lat: number; lng: number; v: number } | null = null
  private distM = 0
  private idleMs = 0
  private harsh = 0
  private maxV = 0
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  private wakeLock: any = null
  startedAtIso = ''

  private durMin(): number {
    return this.firstT && this.lastT ? Math.max(0, (this.lastT - this.firstT) / 60000) : 0
  }

  private live(): TripLive {
    const dur = this.durMin()
    return {
      distanceKm: this.distM / 1000,
      durationMin: dur,
      speedKmh: this.prev ? this.prev.v : 0,
      harshEvents: this.harsh,
      idleMinutes: Math.min(this.idleMs / 60000, dur),  // idle can never exceed duration
      maxSpeed: this.maxV,
    }
  }

  private async acquireWake(): Promise<void> {
    try {
      const nav = navigator as unknown as { wakeLock?: { request(t: string): Promise<unknown> } }
      if (nav.wakeLock) this.wakeLock = await nav.wakeLock.request('screen')
    } catch { /* wake lock is best-effort; denial must not break tracking */ }
  }

  private onVisibility = (): void => {
    // iOS releases the wake lock when the app is backgrounded; re-acquire on return.
    if (typeof document !== 'undefined' && document.visibilityState === 'visible' && this.watchId != null) {
      void this.acquireWake()
    }
  }

  start(onUpdate: (l: TripLive) => void, onError: (msg: string) => void, onFirstFix?: () => void): boolean {
    if (typeof navigator === 'undefined' || !navigator.geolocation) {
      onError('Location is not available on this device.'); return false
    }
    this.startedAtIso = new Date().toISOString()
    void this.acquireWake()
    if (typeof document !== 'undefined') document.addEventListener('visibilitychange', this.onVisibility)
    let firstReported = false
    this.watchId = navigator.geolocation.watchPosition(
      pos => {
        const acc = pos.coords.accuracy
        const t = pos.timestamp || Date.now()
        const { latitude: lat, longitude: lng } = pos.coords
        // A coarse fix (cell/wi-fi, hundreds of metres) is good enough to say
        // "we have you" and start the clock, but must NEVER be scored — 500 m
        // of jitter would book phantom distance and harsh events. The old code
        // dropped coarse fixes outright, so a first fix that never sharpened
        // below ACC_MAX_M left the screen stuck on "Getting your location…"
        // forever, with no error and no way to tell what was wrong.
        const scorable = acc != null && acc <= ACC_MAX_M
        // Prefer the device's own speed (m/s → km/h); a real 0 is valid.
        // Clamp it like the derived speed — a GPS-chip glitch can report a
        // momentary 1000+ km/h, which faked a harsh-brake and dented the score.
        const hasDeviceSpeed = pos.coords.speed != null && pos.coords.speed >= 0
        let v = hasDeviceSpeed ? Math.min(SPEED_CEIL_KMH, (pos.coords.speed as number) * 3.6) : 0

        if (!this.firstT) this.firstT = t
        this.lastT = t

        if (scorable) {
          if (this.prev) {
            const gap = t - this.prev.t
            // Only score fixes that are neither a burst-duplicate nor across a
            // suspension/signal gap — otherwise dt is meaningless. A stretch of
            // coarse fixes lands here as a big gap, so nothing is fabricated.
            if (gap >= MIN_DT_MS && gap <= MAX_GAP_MS) {
              const dtS = gap / 1000
              const stepM = haversineM(this.prev.lat, this.prev.lng, lat, lng)
              if (!hasDeviceSpeed) v = Math.min(SPEED_CEIL_KMH, (stepM / dtS) * 3.6)
              // Count real movement only: bigger than accuracy noise.
              if (stepM > Math.max(3, (acc as number) * 0.5)) this.distM += stepM
              const delta = Math.abs(v - this.prev.v) / dtS  // km/h per second
              if (delta > HARSH_DELTA_KMH_PER_S) this.harsh += 1
              if (v < IDLE_SPEED_KMH) this.idleMs += gap
            }
            // gap > MAX_GAP_MS → phone was asleep/underground: rebase, book nothing.
          }
          if (v > this.maxV && v <= SPEED_CEIL_KMH) this.maxV = v
          this.prev = { t, lat, lng, v }
        }

        if (!firstReported) { firstReported = true; onFirstFix?.() }
        onUpdate(this.live())
      },
      err => {
        // Once a fix has landed the drive is live: a tunnel, a basement or a
        // slow re-fix must not throw the trip away — watchPosition keeps
        // trying on its own, and the aggregates already banked stay valid.
        if (firstReported) return
        onError(
          err.code === err.PERMISSION_DENIED
            ? 'Location permission was blocked. Allow location for Alpha Nexus, then try again.'
            : err.code === err.TIMEOUT
              ? 'No GPS fix yet. Move to open sky and try again.'
              // POSITION_UNAVAILABLE — the phone could not produce a position at
              // all. Usually Location switched off, or an app build with no
              // location permission (that was the Play Store bug of 15-Aug-2026).
              : 'Your phone could not provide a location. Check Location is switched on, then try again — if it keeps failing, update Alpha Nexus in the Play Store.',
        )
      },
      // The first high-accuracy fix from a cold GPS chip regularly takes longer
      // than 20 s; the old 20 s ceiling failed people who were doing everything
      // right. In watchPosition the timeout applies to EVERY fix, so this is
      // also the mid-drive re-fix window — and mid-drive timeouts are ignored
      // above rather than ending the trip.
      { enableHighAccuracy: true, maximumAge: 0, timeout: 60000 },
    )
    return true
  }

  private releaseWake(): void {
    try { this.wakeLock?.release?.() } catch { /* ignore */ }
    this.wakeLock = null
    if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', this.onVisibility)
  }

  stop(): TripLive {
    if (this.watchId != null && navigator.geolocation) navigator.geolocation.clearWatch(this.watchId)
    this.watchId = null
    this.releaseWake()
    return this.live()
  }
}
