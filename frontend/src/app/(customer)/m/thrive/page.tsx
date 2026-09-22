'use client'

/**
 * /m/thrive — Alpha Nexus Wellness (Stitch design). Heart hero + single
 * "Consent & Begin Scan", then the reused FingerScan + ScoreGauge against the
 * member-scoped customer API. User-facing name is "Wellness"; the /thrive route
 * segment is internal. Wellness only, never diagnosis; frames never leave device.
 */
import { useCallback, useEffect, useState } from 'react'
import { Sparkles, TrendingUp, Activity, HeartPulse, Stethoscope, Gift } from 'lucide-react'
import { FingerScan } from '../../../(dashboard)/thrive/FingerScan'
import { ScoreGauge } from '../../../(dashboard)/thrive/ScoreGauge'
import type { PpgResult } from '../../../(dashboard)/thrive/ppg'
import { postScan, getAlphaScore, getTrend, postCoach, getScreening, claimScreening, ApiError, type ThriveVitals, type TrendResp, type ScreeningResp } from '../../api'
import type { ThriveScore } from '@/lib/api'
import { C, serif, sans, h, card, headerPad } from '../../ui'

const CONSENT_KEY = 'alpha_thrive_consent'

export default function CustomerWellness() {
  const [consented, setConsented] = useState(false)
  const [vitals, setVitals] = useState<ThriveVitals | null>(null)
  const [score, setScore] = useState<ThriveScore | null>(null)
  const [trend, setTrend] = useState<TrendResp | null>(null)
  const [coach, setCoach] = useState<string | null>(null)
  // Free annual screening, earned with pulse checks (CFO 2026-09-08, "10 is good").
  const [screening, setScreening] = useState<ScreeningResp | null>(null)
  const [screeningErr, setScreeningErr] = useState<string | null>(null)
  const [claiming, setClaiming] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (typeof window !== 'undefined' && localStorage.getItem(CONSENT_KEY) === '1') setConsented(true)
    getAlphaScore().then(s => { if (s.hasVitals) setScore(s.score) }).catch(() => {})
    getTrend().then(setTrend).catch(() => {})
    // The screening card is additive: if this call fails the rest of Wellness
    // still works, so the failure stays local to the card.
    getScreening().then(setScreening)
      // A broken call must not masquerade as "you have no screening benefit".
      .catch(e => setScreeningErr(e instanceof ApiError ? e.message
        : 'Could not load your screening progress — pull down to retry.'))
  }, [])

  const accept = () => {
    try { localStorage.setItem(CONSENT_KEY, '1') } catch { /* storage blocked */ }
    setConsented(true)
  }

  const onScan = useCallback(async (r: PpgResult) => {
    if (r.beats < 2 || !r.hr) { setError('That scan was too noisy — keep your fingertip still over the lens and try again.'); return }
    setBusy('scan'); setError(null)
    try {
      const res = await postScan(r.ibis, r.respiration ?? undefined)
      setVitals(res.vitals); setScore(res.alphaScore)
      getTrend().then(setTrend).catch(() => {})
      setBusy('coach'); setCoach((await postCoach()).nudge)
    } catch (e) { setError(e instanceof Error ? e.message : 'Scan could not be saved') }
    finally { setBusy(null) }
  }, [])

  return (
    <div style={{ background: C.surface, minHeight: '100vh', fontFamily: sans }}>
      <header style={{ textAlign: 'center', padding: headerPad, background: 'linear-gradient(180deg, #E7F6F3, #ffffff)', borderBottom: `1px solid ${C.line}` }}>
        <span style={{ fontFamily: serif, fontWeight: 800, fontSize: 21, color: C.ink }}>Alpha Nexus</span>
      </header>

      <main style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 14 }}>
        {error && <div style={{ background: '#FEF2F2', border: '1px solid #FECACA', color: '#B91C1C', fontSize: 13, padding: '10px 12px', borderRadius: 12 }}>{error}</div>}

        {!consented ? (
          <>
            <div style={{ textAlign: 'center', paddingTop: 8 }}>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src="/brand/nexus/wellness.png" alt="" width={120} height={120} />
            </div>
            <div style={{ ...card, padding: 24, textAlign: 'center' }}>
              <h1 style={{ ...h(26) }}>Ready for your<br />wellness check?</h1>
              <p style={{ color: C.inkSoft, fontSize: 14, lineHeight: 1.55, margin: '12px 6px 20px' }}>
                We use your phone camera as a light-based pulse sensor. Sit still in a well-lit spot and
                cover the rear lens with your fingertip. <b>Only the resulting numbers are saved</b> — the
                camera pictures never leave your phone, and we use them for wellness rewards only.
              </p>
              <button onClick={accept} style={{ width: '100%', padding: '15px', borderRadius: 999, border: 'none', background: `linear-gradient(135deg, ${C.tealLight}, ${C.teal})`, color: '#fff', fontWeight: 700, fontSize: 15, cursor: 'pointer', fontFamily: sans }}>
                Consent &amp; begin scan
              </button>
            </div>
          </>
        ) : (
          <>
            <FingerScan onComplete={onScan} />
            {busy === 'scan' && <p style={{ fontSize: 13, color: C.inkSoft }}>Saving your scan…</p>}

            {vitals && (
              <div style={{ ...card, padding: 16, display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', textAlign: 'center', gap: 8 }}>
                <Vital icon={<HeartPulse size={18} />} label="Heart rate" value={vitals.restingHr} unit="bpm" />
                <Vital icon={<Activity size={18} />} label="Stress" value={vitals.stressBand || '—'} text />
                <Vital icon={<Sparkles size={18} />} label="Breathing" value={vitals.respirationRate} unit="/min" />
              </div>
            )}

            {score ? <ScoreGauge score={score} /> : (
              <div style={{ ...card, padding: 18, textAlign: 'center', color: C.inkSoft, fontSize: 13 }}>Do a 30-second scan to see your wellness score.</div>
            )}

            {trend && trend.points.length > 1 && (
              <div style={{ ...card, padding: 14 }}>
                <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 6 }}>
                  <TrendingUp size={18} style={{ color: C.navy }} />
                  <span style={{ fontSize: 13, fontWeight: 600, color: C.ink }}>Your trend ({trend.direction})</span>
                </div>
                <Sparkline points={trend.points} direction={trend.direction} />
              </div>
            )}

            {coach && (
              <div style={{ borderRadius: 18, padding: 16, background: '#E7F6F3', border: `1px solid ${C.tealLight}`, display: 'flex', gap: 10 }}>
                <Sparkles size={20} style={{ color: C.teal, flexShrink: 0 }} />
                <div><p style={{ margin: 0, fontSize: 13, fontWeight: 700, color: C.ink }}>Your coach</p>
                  <p style={{ margin: '4px 0 0', fontSize: 13, color: '#374151' }}>{coach}</p></div>
              </div>
            )}
            {busy === 'coach' && <p style={{ fontSize: 13, color: C.inkSoft }}>Your coach is thinking…</p>}
          </>
        )}

        {/* Free annual screening — the pulse check earns something real. The
            COST is a commercial term and never appears here; this is an
            entitlement, not a payment. */}
        {screeningErr && !screening && (
          <div role="alert" style={{ ...card, padding: 16, marginTop: 16, borderLeft: '5px solid #FECACA', color: '#B91C1C', fontSize: 13 }}>{screeningErr}</div>
        )}
        {screening && (
          <div style={{ ...card, padding: 18, marginTop: 16, borderLeft: `5px solid ${screening.unlocked ? C.teal : C.line}` }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <Stethoscope size={20} style={{ color: screening.unlocked ? C.teal : C.inkSoft }} />
              <h2 style={{ ...h(20), margin: 0 }}>Free health screening</h2>
            </div>
            <p style={{ fontSize: 13, color: C.inkSoft, margin: '10px 0 12px', lineHeight: 1.5 }}>{screening.label}</p>

            {/* Progress toward the required checks — a real count, never a guess. */}
            <div style={{ height: 8, borderRadius: 999, background: '#EEF0F3', overflow: 'hidden' }}>
              <div style={{ height: '100%', width: `${Math.min(100, Math.round((screening.scansDone / Math.max(1, screening.required)) * 100))}%`, background: screening.unlocked ? C.teal : C.orange, transition: 'width 300ms ease' }} />
            </div>
            <p style={{ fontSize: 12, color: C.inkSoft, margin: '6px 0 0' }}>
              {screening.scansDone} of {screening.required} pulse checks
              {/* Say WHY the count restarted, or it reads as lost progress. */}
              {screening.countingSince ? ` since ${screening.countingSince}` : ''}
            </p>

            {screening.voucher ? (
              <div style={{ marginTop: 14, borderRadius: 16, background: '#E7F6F3', border: `1px solid ${C.tealLight}`, padding: 14 }}>
                <p style={{ margin: 0, fontSize: 11, letterSpacing: '0.1em', color: C.inkSoft }}>YOUR VOUCHER — SHOW THIS AT THE CLINIC</p>
                <p style={{ fontFamily: serif, fontWeight: 800, fontSize: 26, letterSpacing: '0.14em', color: C.ink, margin: '6px 0 2px' }}>{screening.voucher.code}</p>
                <p style={{ margin: 0, fontSize: 12, color: C.inkSoft }}>Valid until {screening.voucher.expiresOn}</p>
              </div>
            ) : screening.unlocked ? (
              <button onClick={async () => {
                if (claiming) return
                setClaiming(true); setScreeningErr(null)
                try { setScreening(await claimScreening()) }
                catch (e) { setScreeningErr(e instanceof ApiError ? e.message : 'Could not get your voucher — please try again.') }
                finally { setClaiming(false) }
              }} disabled={claiming}
                style={{ marginTop: 14, width: '100%', padding: '13px 20px', borderRadius: 999, border: 'none', background: `linear-gradient(135deg, ${C.tealLight}, ${C.teal})`, color: C.navy, fontWeight: 700, fontSize: 15, cursor: 'pointer', fontFamily: sans, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 8, opacity: claiming ? 0.6 : 1 }}>
                <Gift size={18} /> {claiming ? 'Getting your voucher…' : 'Claim my free screening'}
              </button>
            ) : null}

            {screeningErr && (
              <p role="alert" style={{ color: '#B91C1C', fontSize: 13, margin: '10px 0 0' }}>{screeningErr}</p>
            )}
          </div>
        )}
      </main>
    </div>
  )
}

function Vital({ icon, label, value, unit, text }: { icon: React.ReactNode; label: string; value: number | string | null; unit?: string; text?: boolean }) {
  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'center', color: '#9CA3AF', marginBottom: 4 }}>{icon}</div>
      <div style={{ fontFamily: serif, fontSize: 19, fontWeight: 700, color: C.ink }}>
        {value == null ? '—' : value}{!text && value != null && unit ? <span style={{ fontSize: 11, color: '#9CA3AF', marginLeft: 2 }}>{unit}</span> : null}
      </div>
      <div style={{ fontSize: 11, color: C.inkSoft }}>{label}</div>
    </div>
  )
}

function Sparkline({ points, direction }: { points: { date: string; score: number }[]; direction: string }) {
  const w = 320, ht = 60
  const xs = points.map((_, i) => (i / (points.length - 1)) * w)
  const ys = points.map(p => ht - (p.score / 100) * (ht - 8) - 4)
  const d = xs.map((x, i) => `${i ? 'L' : 'M'} ${x.toFixed(1)} ${ys[i].toFixed(1)}`).join(' ')
  const color = direction === 'declining' ? '#dc2626' : direction === 'improving' ? C.teal : C.orange
  return (
    <svg viewBox={`0 0 ${w} ${ht}`} style={{ width: '100%' }} role="img" aria-label={`Trend ${direction}`}>
      <path d={d} fill="none" stroke={color} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
      {xs.map((x, i) => <circle key={i} cx={x} cy={ys[i]} r="2.5" fill={color} />)}
    </svg>
  )
}
