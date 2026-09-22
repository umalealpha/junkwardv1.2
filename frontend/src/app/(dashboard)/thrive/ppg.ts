/**
 * thrive/ppg.ts — finger-photoplethysmography signal processing.
 *
 * Pure, dependency-free DSP. Takes the per-frame mean RED-channel brightness
 * sampled while the user covers the rear camera lens with a fingertip, and
 * recovers the pulse: detrend → smooth → adaptive peak-detect with a
 * refractory period → inter-beat intervals (IBIs, ms).
 *
 * Only NUMBERS are produced here — the raw frames are sampled in the browser
 * and never stored or transmitted. The IBIs we return are what the page POSTs
 * to /rewards/thrive/scan/; the server recomputes HRV authoritatively
 * (rewards/hrv.py). This client copy exists for the live HR readout only.
 *
 * Wellness only, never diagnosis. No blood pressure is derived.
 */

export interface Sample { t: number; v: number } // t = ms epoch, v = red mean (0-255)

export interface PpgResult {
  hr: number | null            // bpm
  ibis: number[]               // inter-beat intervals, ms
  respiration: number | null   // breaths/min, coarse estimate
  quality: number              // 0-1 share of plausible IBIs
  beats: number
}

// Plausible IBI window (ms): ~30–200 bpm. Mirrors rewards/hrv.py server-side.
const IBI_MIN = 300
const IBI_MAX = 2000

/** Centered moving average; window in samples (odd preferred). */
function movingAverage(xs: number[], window: number): number[] {
  if (window <= 1) return xs.slice()
  const half = Math.floor(window / 2)
  const out = new Array<number>(xs.length)
  for (let i = 0; i < xs.length; i++) {
    let sum = 0, n = 0
    for (let j = i - half; j <= i + half; j++) {
      if (j >= 0 && j < xs.length) { sum += xs[j]; n++ }
    }
    out[i] = sum / n
  }
  return out
}

/** Remove slow baseline drift: signal minus a wide moving average. */
function detrend(xs: number[], fps: number): number[] {
  const baseline = movingAverage(xs, Math.max(3, Math.round(fps))) // ~1s window
  return xs.map((x, i) => x - baseline[i])
}

/**
 * Adaptive peak detection with a refractory period.
 * A peak is a local maximum above a fraction of the running max, separated
 * from the previous peak by at least IBI_MIN ms.
 */
function detectPeakTimes(values: number[], times: number[], fps: number): number[] {
  const smoothed = movingAverage(values, 3)
  // Adaptive threshold from positive-amplitude statistics.
  const positives = smoothed.filter(v => v > 0)
  if (positives.length < 5) return []
  const mean = positives.reduce((a, b) => a + b, 0) / positives.length
  const threshold = mean * 0.5
  const refractory = IBI_MIN
  const peaks: number[] = []
  let lastPeakT = -Infinity
  for (let i = 1; i < smoothed.length - 1; i++) {
    const v = smoothed[i]
    if (v > threshold && v >= smoothed[i - 1] && v > smoothed[i + 1]) {
      const t = times[i]
      if (t - lastPeakT >= refractory) { peaks.push(t); lastPeakT = t }
    }
  }
  return peaks
}

function peakTimesToIbis(peakTimes: number[]): number[] {
  const ibis: number[] = []
  for (let i = 1; i < peakTimes.length; i++) ibis.push(peakTimes[i] - peakTimes[i - 1])
  return ibis
}

/**
 * Coarse respiration estimate from baseline wander. Breathing modulates the
 * PPG baseline at ~0.15–0.4 Hz; we heavily low-pass the signal and count
 * zero-crossings over the capture window. Returns null if the window is too
 * short to be meaningful. Labelled approximate in the UI.
 */
function estimateRespiration(values: number[], times: number[], fps: number): number | null {
  if (times.length < fps * 8) return null // need ~8s
  const durationSec = (times[times.length - 1] - times[0]) / 1000
  if (durationSec < 8) return null
  const slow = movingAverage(values, Math.max(5, Math.round(fps * 1.5))) // ~1.5s lowpass
  const mean = slow.reduce((a, b) => a + b, 0) / slow.length
  let crossings = 0
  for (let i = 1; i < slow.length; i++) {
    if ((slow[i - 1] - mean) < 0 && (slow[i] - mean) >= 0) crossings++ // upward crossings
  }
  const breaths = crossings
  const rate = (breaths / durationSec) * 60
  if (rate < 4 || rate > 40) return null // implausible → don't report
  return Math.round(rate)
}

/** Full analysis of a captured red-channel series. */
export function analyzeSignal(samples: Sample[]): PpgResult {
  const empty: PpgResult = { hr: null, ibis: [], respiration: null, quality: 0, beats: 0 }
  if (samples.length < 30) return empty
  const times = samples.map(s => s.t)
  const durationSec = (times[times.length - 1] - times[0]) / 1000
  if (durationSec <= 0) return empty
  const fps = samples.length / durationSec

  const detrended = detrend(samples.map(s => s.v), fps)
  const peakTimes = detectPeakTimes(detrended, times, fps)
  const rawIbis = peakTimesToIbis(peakTimes)
  const ibis = rawIbis.filter(x => x >= IBI_MIN && x <= IBI_MAX)
  const quality = rawIbis.length ? ibis.length / rawIbis.length : 0

  let hr: number | null = null
  if (ibis.length >= 2) {
    const meanIbi = ibis.reduce((a, b) => a + b, 0) / ibis.length
    if (meanIbi > 0) hr = Math.round(60000 / meanIbi)
  }
  const respiration = estimateRespiration(detrended, times, fps)
  return { hr, ibis, respiration, quality: Math.round(quality * 100) / 100, beats: ibis.length }
}
