/**
 * Read-only quality-check view — browser-side flag.
 *
 * CFO 2026-07-29. /qa swaps the browser into a session for the locked
 * `omni-qa-view` identity, which the server refuses on anything but a read
 * (core/token_auth.py). This flag is only so the UI can SAY so — a banner, and
 * a plain refusal on a write instead of a confusing authentication error. It is
 * not the control: clearing it does not grant anyone a single write.
 */
const FLAG = 'alpha_qa_readonly'
const KEY = 'alpha_qa_key'

export function isQaReadOnly(): boolean {
  if (typeof window === 'undefined') return false
  return localStorage.getItem(FLAG) === '1'
}

export function setQaReadOnly(): void {
  if (typeof window !== 'undefined') localStorage.setItem(FLAG, '1')
}

export function clearQaReadOnly(): void {
  if (typeof window !== 'undefined') localStorage.removeItem(FLAG)
}

/** The access key, remembered on this browser so /qa is one click next time. */
export function getSavedQaKey(): string {
  if (typeof window === 'undefined') return ''
  return localStorage.getItem(KEY) || ''
}

export function saveQaKey(key: string): void {
  if (typeof window !== 'undefined') localStorage.setItem(KEY, key)
}

export function forgetQaKey(): void {
  if (typeof window !== 'undefined') localStorage.removeItem(KEY)
}
