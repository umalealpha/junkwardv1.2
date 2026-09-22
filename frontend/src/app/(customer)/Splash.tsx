'use client'

/**
 * Splash — Alpha Nexus startup screen.
 *
 * Light, premium: the three smooth 3D feature objects (Drive shield, Rewards
 * coin, Wellness heart) float gently around the Alpha Direct logo + "Alpha
 * Nexus" wordmark for ~7s, then the app loads (tap to skip). Plays once per
 * session. Pure CSS motion (no WebGL) — the branded layer paints the first
 * frame so startup is always "Alpha Nexus", never "Omni".
 */
import { useEffect, useState } from 'react'
import { C, serif, sans } from './ui'

const DURATION_MS = 7000
const CAPTIONS = ['Nexus Drive', 'Loyalty Rewards', 'Wellness', 'Welcome']

const FLOATERS = [
  { img: '/brand/nexus/drive.png',    style: { top: '16%', left: '14%' },  size: 84,  anim: 'nxFloatA 6s' },
  { img: '/brand/nexus/rewards.png',  style: { top: '20%', right: '12%' }, size: 72,  anim: 'nxFloatB 7s' },
  { img: '/brand/nexus/wellness.png', style: { bottom: '24%', left: '50%', marginLeft: -44 }, size: 88, anim: 'nxFloatC 6.5s' },
]

export function Splash({ onDone }: { onDone: () => void }) {
  const [pct, setPct] = useState(0)
  const [cap, setCap] = useState(0)
  const [leaving, setLeaving] = useState(false)

  useEffect(() => {
    const start = performance.now()
    const id = setInterval(() => {
      const p = Math.min(100, ((performance.now() - start) / DURATION_MS) * 100)
      setPct(p); setCap(Math.min(CAPTIONS.length - 1, Math.floor((p / 100) * CAPTIONS.length)))
    }, 80)
    const done = setTimeout(() => { setLeaving(true); setTimeout(onDone, 450) }, DURATION_MS)
    return () => { clearInterval(id); clearTimeout(done) }
  }, [onDone])

  const skip = () => { setLeaving(true); setTimeout(onDone, 250) }

  return (
    <div onClick={skip} style={{
      position: 'fixed', inset: 0, zIndex: 100, cursor: 'pointer', fontFamily: sans,
      background: `radial-gradient(120% 80% at 50% 28%, #ffffff 0%, #EAF6F4 50%, #E2E8EF 100%)`,
      opacity: leaving ? 0 : 1, transition: 'opacity .45s ease', overflow: 'hidden',
      display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
    }}>
      <style>{`
        @keyframes nxFloatA { 0%,100%{transform:translateY(0) rotate(-4deg)} 50%{transform:translateY(-22px) rotate(4deg)} }
        @keyframes nxFloatB { 0%,100%{transform:translateY(0) rotate(5deg)}  50%{transform:translateY(-26px) rotate(-5deg)} }
        @keyframes nxFloatC { 0%,100%{transform:translateY(0) rotate(0)}     50%{transform:translateY(-18px) rotate(6deg)} }
        @keyframes nxRise   { 0%{opacity:0;transform:translateY(14px)} 100%{opacity:1;transform:translateY(0)} }
      `}</style>

      {FLOATERS.map((f, i) => (
        // eslint-disable-next-line @next/next/no-img-element
        <img key={i} src={f.img} alt="" width={f.size} height={f.size}
          style={{ position: 'absolute', ...f.style, animation: `${f.anim} ease-in-out infinite`, filter: 'drop-shadow(0 12px 24px rgba(16,27,42,0.14))', opacity: 0.96 }} />
      ))}

      <div style={{ position: 'relative', textAlign: 'center', animation: 'nxRise .6s ease both' }}>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/brand/logo-full-color.png" alt="Alpha Direct" style={{ height: 50, marginBottom: 14, filter: 'drop-shadow(0 6px 16px rgba(16,27,42,0.16))' }} />
        <h1 style={{ fontFamily: serif, fontWeight: 800, fontSize: 40, color: C.navy, margin: 0, letterSpacing: '-0.01em' }}>Alpha Nexus</h1>
        <p style={{ fontSize: 14, color: C.inkSoft, margin: '6px 0 0', minHeight: 20 }} key={cap}>{CAPTIONS[cap]}</p>
      </div>

      <div style={{ position: 'absolute', bottom: 48, width: 200, textAlign: 'center' }}>
        <div style={{ height: 4, background: 'rgba(16,27,42,0.10)', borderRadius: 999, overflow: 'hidden' }}>
          <div style={{ height: '100%', width: `${pct}%`, background: `linear-gradient(90deg, ${C.tealLight}, ${C.teal})`, borderRadius: 999, transition: 'width .12s linear' }} />
        </div>
        <p style={{ margin: '12px 0 0', fontSize: 11, color: '#94A3B8' }}>tap to skip</p>
      </div>
    </div>
  )
}
