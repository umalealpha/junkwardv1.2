'use client'

/**
 * Fun Mode — 3D star-field galaxy easter egg for HRIS.
 *
 * Trigger: Konami code (↑↑↓↓←→←→BA) anywhere on the HRIS surface, OR
 * click the HRIS Home avatar 7 times in 3 seconds.
 *
 * The heavy Three.js scene lives in FunModeGalaxyScene.tsx and is loaded
 * lazily (client-only) — so three.js (~600 KB) is NOT in the HRIS page's
 * initial bundle; it downloads only if the easter egg actually fires
 * (CFO 2026-07-16, "make omni faster"). The Konami hook below stays here and
 * pulls in nothing heavy, so importing it costs the page nothing.
 *
 * CFO directive 2026-05-20 (Manus HRIS Part 4 — wow-factor extensions).
 */
import { useEffect } from 'react'
import dynamic from 'next/dynamic'

const KONAMI = [
  'ArrowUp', 'ArrowUp', 'ArrowDown', 'ArrowDown',
  'ArrowLeft', 'ArrowRight', 'ArrowLeft', 'ArrowRight',
  'b', 'a',
]

export function useFunModeKonami(onTrigger: () => void) {
  useEffect(() => {
    let buf: string[] = []
    function onKey(e: KeyboardEvent) {
      buf.push(e.key.length === 1 ? e.key.toLowerCase() : e.key)
      if (buf.length > KONAMI.length) buf.shift()
      if (buf.length === KONAMI.length && buf.every((k, i) => k === KONAMI[i])) {
        onTrigger()
        buf = []
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onTrigger])
}

// Lazy wrapper — three.js only loads when this component actually mounts.
// The HRIS page mounts it only once Fun Mode is triggered, so normal use never
// pays for it. Same props as before, so callers are unchanged.
export const FunModeGalaxy = dynamic(
  () => import('./FunModeGalaxyScene').then(m => m.FunModeGalaxy),
  { ssr: false, loading: () => null },
)
