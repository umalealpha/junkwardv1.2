/** Step-sync handshake logic, kept out of the page so it can be TESTED.
 *
 * The bug this exists to prevent (CFO, 2026-09-08: "the sync steps is not
 * working"): the Android hand-off used to `await` a pair code from the server
 * and only then set `window.location.href` to an `intent://` URL. By that
 * point the tap's user activation is spent, and Chrome refuses an
 * external-protocol navigation without activation — SILENTLY. Nothing threw,
 * so the surrounding catch never ran and no message was ever shown. The tile
 * looked completely dead.
 *
 * The fix is a two-tap handshake: one tap fetches the code, the next issues
 * the intent inside its own gesture. `nextStepSyncAction` is that decision,
 * and the invariant it guarantees is that 'openApp' NEVER needs the network.
 */
export const NEXUS_PACKAGE = 'com.alphadirect.rewardshealth'

/** The Chrome-family intent URL that hands a pair code to the native app. */
export function stepSyncIntentUrl(code: string, playFallbackUrl: string): string {
  return `intent://pair?code=${encodeURIComponent(code)}`
    + `#Intent;scheme=alphanexus;package=${NEXUS_PACKAGE};`
    + `S.browser_fallback_url=${encodeURIComponent(playFallbackUrl)};end`
}

export interface PairCode { code: string; expiresAt: number }

/** A cached code is only reusable while the SERVER still accepts it. */
export function isCodeLive(pair: PairCode | null, now = Date.now()): boolean {
  return !!pair && now < pair.expiresAt
}

export type StepSyncAction =
  | 'apple'       // packaged Apple shell: the native plugin does everything
  | 'unsupported' // no native path on this device/browser
  | 'fetchCode'   // tap 1 — get a pair code (network; activation is spent)
  | 'openApp'     // tap 2 — navigate out inside THIS gesture (no network)

export function nextStepSyncAction(o: {
  isAndroid: boolean
  isAppleShell: boolean
  pairCode: PairCode | null
  now?: number
}): StepSyncAction {
  if (o.isAppleShell) return 'apple'
  if (!o.isAndroid) return 'unsupported'
  return isCodeLive(o.pairCode, o.now ?? Date.now()) ? 'openApp' : 'fetchCode'
}

/** Should we tell the member the app never opened?
 *
 * The follow-up timer is armed BEFORE the `intent://` navigation (a timer armed
 * after it never exists if the assignment throws). But when the hand-off
 * SUCCEEDS, Android suspends the browser and the pending timer is deferred — it
 * fires when the member comes back, with `document.hidden === false`. Reporting
 * failure then is exactly the wrong-information fault this work exists to stop:
 * a member who just synced would be sent to Google Play.
 *
 * So visibility alone is not enough: a callback that ran materially late was
 * suspended, which means the app DID take over.
 */
export const HANDOFF_GRACE_MS = 4000

export function shouldReportNoHandoff(o: { hidden: boolean; elapsedMs: number }): boolean {
  if (o.hidden) return false                       // the app is in front right now
  if (o.elapsedMs > HANDOFF_GRACE_MS) return false // we were suspended — it opened
  return true
}
