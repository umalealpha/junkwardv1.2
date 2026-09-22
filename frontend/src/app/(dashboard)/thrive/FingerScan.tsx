'use client'

/**
 * thrive/FingerScan.tsx — finger-PPG capture.
 *
 * Opens the rear camera, asks the user to cover the lens with a fingertip,
 * samples the mean RED-channel brightness each frame for ~30s while drawing a
 * live waveform, then recovers inter-beat intervals (ppg.ts) and hands the
 * NUMBERS up via onComplete. Frames are sampled in-memory only — nothing is
 * recorded, saved, or uploaded. Wellness only, never diagnosis.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { analyzeSignal, type PpgResult, type Sample } from './ppg'

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'

type Phase = 'idle' | 'measuring' | 'error'

export function FingerScan({
  durationSec = 30,
  onComplete,
}: {
  durationSec?: number
  onComplete: (result: PpgResult) => void
}) {
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const waveRef = useRef<HTMLCanvasElement | null>(null)
  const sampleCanvas = useRef<HTMLCanvasElement | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const rafRef = useRef<number | null>(null)
  const samplesRef = useRef<Sample[]>([])
  const startTsRef = useRef<number>(0)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const wakeRef = useRef<any>(null)

  const [phase, setPhase] = useState<Phase>('idle')
  const [remaining, setRemaining] = useState(durationSec)
  const [hrLive, setHrLive] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  const stop = useCallback(() => {
    if (rafRef.current != null) cancelAnimationFrame(rafRef.current)
    rafRef.current = null
    const s = streamRef.current
    if (s) {
      s.getTracks().forEach(t => {
        try { (t as MediaStreamTrack).applyConstraints({ advanced: [{ torch: false }] as unknown as MediaTrackConstraintSet[] }) } catch { /* torch unsupported */ }
        t.stop()
      })
    }
    streamRef.current = null
    try { wakeRef.current?.release?.() } catch { /* ignore */ }
    wakeRef.current = null
  }, [])

  useEffect(() => () => stop(), [stop]) // cleanup on unmount

  // Keep the screen awake for the full 30s scan — if the phone dims/sleeps the
  // rAF sampling loop pauses and the scan never completes. Re-acquire when the
  // page returns to the foreground (iOS drops the lock on backgrounding).
  const acquireWake = useCallback(async () => {
    try {
      const nav = navigator as unknown as { wakeLock?: { request(t: string): Promise<unknown> } }
      if (nav.wakeLock) wakeRef.current = await nav.wakeLock.request('screen')
    } catch { /* best-effort */ }
  }, [])
  useEffect(() => {
    const onVis = () => { if (document.visibilityState === 'visible' && streamRef.current) void acquireWake() }
    document.addEventListener('visibilitychange', onVis)
    return () => document.removeEventListener('visibilitychange', onVis)
  }, [acquireWake])

  const drawWave = useCallback(() => {
    const canvas = waveRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const w = canvas.width, h = canvas.height
    ctx.fillStyle = NAVY
    ctx.fillRect(0, 0, w, h)
    const data = samplesRef.current.slice(-Math.floor(w))
    if (data.length < 2) return
    const vs = data.map(d => d.v)
    const min = Math.min(...vs), max = Math.max(...vs)
    const range = max - min || 1
    ctx.strokeStyle = ORANGE
    ctx.lineWidth = 2
    ctx.beginPath()
    data.forEach((d, i) => {
      const x = (i / (data.length - 1)) * w
      const y = h - ((d.v - min) / range) * (h * 0.8) - h * 0.1
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y)
    })
    ctx.stroke()
  }, [])

  const tick = useCallback(() => {
    const video = videoRef.current
    const sc = sampleCanvas.current
    if (!video || !sc || video.readyState < 2) {
      // Camera-stall watchdog: this branch returns BEFORE the countdown code,
      // so a camera that never delivers a first frame left "measuring… 30s"
      // frozen forever with no error and no way out. Bail out after 5s.
      if (performance.now() - startTsRef.current > 5000 && samplesRef.current.length === 0) {
        stop()
        setPhase('error')
        setError('The camera did not start. Close other camera apps, then try again.')
        return
      }
      rafRef.current = requestAnimationFrame(tick)
      return
    }
    const sctx = sc.getContext('2d', { willReadFrequently: true })
    if (sctx) {
      sctx.drawImage(video, 0, 0, sc.width, sc.height)
      // Mean red over the centre quarter (where the fingertip covers).
      const x0 = Math.floor(sc.width * 0.375), y0 = Math.floor(sc.height * 0.375)
      const w = Math.floor(sc.width * 0.25), h = Math.floor(sc.height * 0.25)
      const img = sctx.getImageData(x0, y0, w, h).data
      let sum = 0
      for (let i = 0; i < img.length; i += 4) sum += img[i] // red channel
      const mean = sum / (img.length / 4)
      samplesRef.current.push({ t: performance.now(), v: mean })
    }
    drawWave()

    const elapsed = (performance.now() - startTsRef.current) / 1000
    const rem = Math.max(0, durationSec - elapsed)
    setRemaining(Math.ceil(rem))
    // Live HR readout every ~2s.
    if (samplesRef.current.length % 30 === 0) {
      const live = analyzeSignal(samplesRef.current)
      if (live.hr) setHrLive(live.hr)
    }

    if (rem <= 0) {
      stop()
      const result = analyzeSignal(samplesRef.current)
      onComplete(result)
      setPhase('idle')
      return
    }
    rafRef.current = requestAnimationFrame(tick)
  }, [durationSec, drawWave, onComplete, stop])

  const startingRef = useRef(false)
  const start = useCallback(async () => {
    // Double-tap guard: two rapid taps on "Start" opened two camera streams —
    // only the second was tracked, so the first kept the torch lit and the
    // camera busy (battery drain) until the page was closed.
    if (startingRef.current || streamRef.current) return
    startingRef.current = true
    setError(null)
    samplesRef.current = []
    setHrLive(null)
    if (!sampleCanvas.current) {
      const c = document.createElement('canvas'); c.width = 64; c.height = 48
      sampleCanvas.current = c
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: 'environment' }, width: { ideal: 320 }, height: { ideal: 240 } },
        audio: false,
      })
      streamRef.current = stream
      // Best-effort: turn on the torch so the fingertip is well lit.
      const track = stream.getVideoTracks()[0]
      try { await track.applyConstraints({ advanced: [{ torch: true }] as unknown as MediaTrackConstraintSet[] }) } catch { /* torch unsupported on this device */ }
      if (videoRef.current) {
        videoRef.current.srcObject = stream
        await videoRef.current.play()
      }
      void acquireWake()
      startTsRef.current = performance.now()
      setRemaining(durationSec)
      setPhase('measuring')
      rafRef.current = requestAnimationFrame(tick)
    } catch (e) {
      setPhase('error')
      const msg = e instanceof Error ? e.message : String(e)
      setError(/denied|NotAllowed/i.test(msg)
        ? 'Camera permission was blocked. Allow camera access, then try again.'
        : `Could not open the camera: ${msg}`)
    } finally {
      startingRef.current = false
    }
  }, [durationSec, tick])

  return (
    <div className="rounded-2xl overflow-hidden" style={{ background: NAVY }}>
      <div className="relative aspect-[4/3] w-full">
        {/* Source video: kept in the render tree but effectively invisible.
            display:none / visibility:hidden stop iOS Safari + WKWebView from
            decoding frames (drawImage would read black), so the scan never
            gets a signal — position it off-flow at 2px with opacity 0 instead. */}
        <video ref={videoRef} playsInline muted
          style={{ position: 'absolute', top: 0, left: 0, width: 2, height: 2, opacity: 0, pointerEvents: 'none' }} />
        <canvas ref={waveRef} width={480} height={300} className="w-full h-full block" />
        {phase === 'idle' && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 text-center px-6">
            <p className="text-white/90 text-sm max-w-xs">
              Cover the <b>rear camera lens</b> gently with your fingertip and hold still.
            </p>
            <button onClick={start}
              className="px-6 py-3 rounded-full font-semibold text-navy-950"
              style={{ background: ORANGE, color: NAVY }}>
              Start 30-second scan
            </button>
          </div>
        )}
        {phase === 'measuring' && (
          <div className="absolute top-3 left-0 right-0 flex items-center justify-between px-4">
            <span className="text-white/90 text-sm font-medium animate-pulse">measuring…</span>
            <span className="text-white text-2xl font-bold tabular-nums">{remaining}s</span>
          </div>
        )}
        {phase === 'measuring' && hrLive && (
          <div className="absolute bottom-3 left-0 right-0 text-center">
            <span className="text-white text-3xl font-bold tabular-nums">{hrLive}</span>
            <span className="text-white/70 text-sm ml-1">bpm</span>
          </div>
        )}
      </div>
      {phase === 'error' && (
        <div className="p-4 text-center">
          <p className="text-red-300 text-sm mb-3">{error}</p>
          <button onClick={start} className="px-5 py-2 rounded-full font-semibold"
            style={{ background: ORANGE, color: NAVY }}>Try again</button>
        </div>
      )}
    </div>
  )
}
