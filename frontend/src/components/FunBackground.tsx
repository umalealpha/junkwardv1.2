'use client'

import Image from 'next/image'
import { ALL_MASCOTS } from '@/lib/brand'

/**
 * Fun-Mode background — a 3D dark theme with neon haze, drifting
 * particles, and faded mascots. Purely decorative; no business logic.
 *
 * Accounting accuracy is unaffected by which mode is active — Fun Mode
 * is a UI layer only. Every approval gate, JE balance check, audit
 * trail and reconciliation stays strict regardless.
 */

const MASCOT_COUNT = 8
const PARTICLE_COUNT = 36

interface FunBackgroundProps {
  active: boolean
}

export function FunBackground({ active }: FunBackgroundProps) {
  if (!active) return null

  const mascots = Array.from({ length: MASCOT_COUNT }, (_, i) => {
    const x = ((i * 19 + 7) % 84) + 6
    const y = ((i * 23 + 11) % 80) + 8
    const rotation = (i * 13) % 30 - 15
    const scale = 0.32 + (i % 3) * 0.12
    const imgWidth = 110 + (i % 3) * 18
    const mascotSrc = ALL_MASCOTS[i % ALL_MASCOTS.length]
    return (
      <Image
        key={`m${i}`}
        src={mascotSrc}
        alt=""
        width={imgWidth}
        height={imgWidth}
        className="absolute select-none"
        style={{
          left: `${x}%`,
          top: `${y}%`,
          transform: `rotate(${rotation}deg) scale(${scale})`,
          opacity: 0.04,
          objectFit: 'contain',
          filter: 'hue-rotate(20deg) drop-shadow(0 0 24px rgba(240,127,0,0.35))',
        }}
        draggable={false}
      />
    )
  })

  const particles = Array.from({ length: PARTICLE_COUNT }, (_, i) => {
    const x = ((i * 31 + 13) % 95) + 2
    const y = ((i * 37 + 19) % 95) + 2
    const size = 2 + (i % 4)
    const dur = 14 + (i % 7) * 2
    const delay = -(i % 13)
    const colors = [
      'rgba(240,127,0,0.55)',
      'rgba(124,58,237,0.55)',
      'rgba(0,201,183,0.55)',
      'rgba(99,102,241,0.55)',
    ]
    const color = colors[i % colors.length]
    return (
      <span
        key={`p${i}`}
        className="absolute rounded-full"
        style={{
          left: `${x}%`,
          top: `${y}%`,
          width: `${size}px`,
          height: `${size}px`,
          background: color,
          boxShadow: `0 0 ${size * 4}px ${color}`,
          animation: `fmFloat ${dur}s ease-in-out infinite ${delay}s`,
        }}
      />
    )
  })

  return (
    <div
      className="fixed inset-0 overflow-hidden"
      style={{
        zIndex: 0,
        pointerEvents: 'none',
        background:
          `radial-gradient(ellipse at 20% 15%, rgba(124,58,237,0.35) 0%, transparent 45%),
           radial-gradient(ellipse at 85% 30%, rgba(240,127,0,0.32) 0%, transparent 50%),
           radial-gradient(ellipse at 50% 110%, rgba(0,201,183,0.32) 0%, transparent 55%),
           radial-gradient(ellipse at 30% 20%, #1A1060 0%, #121235 50%, #050520 100%)`,
      }}
    >
      <div
        className="absolute inset-0 opacity-[0.05]"
        style={{
          backgroundImage:
            'linear-gradient(rgba(255,255,255,0.4) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.4) 1px, transparent 1px)',
          backgroundSize: '48px 48px',
          maskImage: 'radial-gradient(ellipse at center, black 30%, transparent 80%)',
          WebkitMaskImage: 'radial-gradient(ellipse at center, black 30%, transparent 80%)',
        }}
      />

      <div className="absolute -top-24 -left-16 w-[40rem] h-[40rem] rounded-full pointer-events-none"
           style={{
             background: 'radial-gradient(circle, rgba(240,127,0,0.35), transparent 70%)',
             filter: 'blur(60px)',
             animation: 'fmDriftA 18s ease-in-out infinite',
           }} />
      <div className="absolute -bottom-32 right-0 w-[44rem] h-[44rem] rounded-full pointer-events-none"
           style={{
             background: 'radial-gradient(circle, rgba(124,58,237,0.32), transparent 70%)',
             filter: 'blur(70px)',
             animation: 'fmDriftB 22s ease-in-out infinite',
           }} />
      <div className="absolute top-1/3 left-1/2 w-[30rem] h-[30rem] rounded-full pointer-events-none"
           style={{
             background: 'radial-gradient(circle, rgba(0,201,183,0.28), transparent 70%)',
             filter: 'blur(80px)',
             animation: 'fmDriftC 26s ease-in-out infinite',
           }} />

      {mascots}
      {particles}

      <div
        className="absolute bottom-0 left-0 right-0"
        style={{
          height: '35%',
          background: 'linear-gradient(to bottom, transparent, rgba(5,5,30,0.7))',
        }}
      />

      <style dangerouslySetInnerHTML={{ __html: `
        @keyframes fmFloat {
          /* No vertical jump — gentle opacity breathe only. The old
             translate(8px,-20px) made mascots bounce up/down and read as a
             glitch (CFO 2026-06-04 "Superman jumps up and down, headache"). */
          0%, 100% { opacity: 0.45; }
          50%      { opacity: 0.7; }
        }
        @keyframes fmDriftA {
          0%, 100% { transform: translate(0, 0) scale(1); }
          50%      { transform: translate(60px, 40px) scale(1.1); }
        }
        @keyframes fmDriftB {
          0%, 100% { transform: translate(0, 0) scale(1); }
          50%      { transform: translate(-40px, -30px) scale(1.12); }
        }
        @keyframes fmDriftC {
          0%, 100% { transform: translate(0, 0) scale(1); }
          50%      { transform: translate(-30px, 25px) scale(0.92); }
        }
      ` }} />
    </div>
  )
}
