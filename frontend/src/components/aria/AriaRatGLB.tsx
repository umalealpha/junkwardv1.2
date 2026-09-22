'use client'
/**
 * AriaRatGLB — the floating assistant's visible character.
 *
 * CFO 2026-06-29: REVERTED to the 3D cartoon rat for ALL themes. The glowing
 * particle face (public/aria-face.html) is retired per CFO call — "bring back
 * the rat, I don't like the glowing one." The rat model lives in AriaRatModel;
 * this stays a thin wrapper with the same name + Props so AriaFloatingA's two
 * call sites (chat bubble + compact header) need no change.
 *
 * (History: #224 swapped the rat for the glowing aria-face.html iframe in
 * Classic/Heavenly, keeping the rat only in Fun mode. This reversal makes the
 * rat universal again.)
 */
import { AriaRatModel } from './AriaRatModel'

export type RatMood = 'idle' | 'thinking' | 'error' | 'success'

interface Props {
  blink?:    boolean
  dx?:       number
  dy?:       number
  excited?:  boolean
  compact?:  boolean
  mood?:     RatMood
}

export function AriaRatGLB(props: Props) {
  return <AriaRatModel {...props} />
}
