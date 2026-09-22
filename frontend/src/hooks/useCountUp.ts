'use client'

import { useEffect, useRef, useState } from 'react'

/**
 * Animate a number from 0 → target once, via requestAnimationFrame (no library).
 *
 * Lifted out of `(dashboard)/dashboard/page.tsx` (redesign pack 03) so the bank
 * balances panel reuses the dashboard's motion instead of growing a fifth copy
 * of it. Behaviour is unchanged from that original.
 *
 * Purely presentational: it is always fed a REAL value already computed by the
 * caller and never invents data. `enabled = false` (reduce-motion, or a figure
 * that is not yet known) snaps straight to the target.
 */
export function useCountUp(target: number, durationMs = 900, enabled = true): number {
  const [val, setVal] = useState(enabled ? 0 : target)
  const rafRef = useRef<number | null>(null)
  // Re-run when the target settles on a new real value (e.g. after fetch /
  // company / date-range change), so the number always lands on the truth.
  useEffect(() => {
    if (!enabled || !isFinite(target) || target === 0) { setVal(target); return }
    let start: number | null = null
    const from = 0
    const tick = (t: number) => {
      if (start === null) start = t
      const p = Math.min(1, (t - start) / durationMs)
      // ease-out cubic
      const eased = 1 - Math.pow(1 - p, 3)
      setVal(from + (target - from) * eased)
      if (p < 1) rafRef.current = requestAnimationFrame(tick)
      else setVal(target)
    }
    rafRef.current = requestAnimationFrame(tick)
    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current) }
  }, [target, durationMs, enabled])
  return val
}
