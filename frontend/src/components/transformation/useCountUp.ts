'use client'

import { useEffect, useRef, useState } from 'react'

// Same RAF-based count-up as the /dashboard "glass KPI" cards (redesign pack
// 03) — animates 0 → target once on mount, ease-out cubic, and snaps straight
// to the final value when `enabled` is false (prefers-reduced-motion). Kept
// as its own small hook here rather than importing the dashboard's local copy
// so this page never depends on another page's internals.
export function useCountUp(target: number, durationMs = 900, enabled = true): number {
  const [val, setVal] = useState(enabled ? 0 : target)
  const rafRef = useRef<number | null>(null)

  useEffect(() => {
    if (!enabled || !isFinite(target) || target === 0) { setVal(target); return }
    let start: number | null = null
    const tick = (t: number) => {
      if (start === null) start = t
      const p = Math.min(1, (t - start) / durationMs)
      const eased = 1 - Math.pow(1 - p, 3)
      setVal(target * eased)
      if (p < 1) rafRef.current = requestAnimationFrame(tick)
      else setVal(target)
    }
    rafRef.current = requestAnimationFrame(tick)
    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current) }
  }, [target, durationMs, enabled])

  return val
}
