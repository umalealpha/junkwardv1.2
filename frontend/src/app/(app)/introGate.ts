/**
 * Alpha Omni intro — the 5-second opening the staff app plays when it is
 * launched (CFO 2026-09-07: "it should load for 5 seconds, professional,
 * enterprise level, a company with AI capability").
 *
 * This file is the pure gate so it can be unit-tested; the picture itself
 * lives in Intro.tsx. Plays ONCE per app open (sessionStorage), never for the
 * read-only quality-check identity (its screenshots would otherwise catch the
 * intro and cry SPINNING), and never on the three store-reviewer pages, which
 * a reviewer with no account opens straight from the listing.
 */
export const INTRO_KEY = 'omni_app_intro_shown'
export const INTRO_MS = 5000

const NO_INTRO = new Set(['/app/privacy', '/app/support', '/app/delete-account'])

export function shouldPlayIntro(
  pathname: string | null | undefined,
  opts: { alreadyShown: boolean; qaReadOnly: boolean },
): boolean {
  if (opts.alreadyShown || opts.qaReadOnly) return false
  const p = (pathname || '/app').replace(/\/+$/, '') || '/app'
  return !NO_INTRO.has(p)
}

/** Read the once-per-open flag; a blocked storage (private mode) counts as
 * "already shown" so the intro can never loop on every navigation. */
export function introAlreadyShown(): boolean {
  if (typeof window === 'undefined') return true
  try { return sessionStorage.getItem(INTRO_KEY) === '1' } catch { return true }
}

export function markIntroShown(): void {
  try { sessionStorage.setItem(INTRO_KEY, '1') } catch { /* private mode */ }
}
