/**
 * Which message to show when the expense-account list fails to load.
 *
 * Reported 2026-08-14: the GL dropdown on the amend panel showed only "Keep the
 * current account", identical to "there are no accounts", because the loader
 * swallowed its own error.
 *
 * The first cut of the fix hardcoded a permission explanation — and that was
 * wrong for the only people who can read it. A 403 on the account list
 * ("Financial data is restricted to finance and management staff.") only
 * happens to accounts that are NOT cleared for GL data, and those accounts have
 * `viewer_can_code === false`, so they never see the amend panel at all. Every
 * custodian who does see it holds the capability and gets HTTP 200. So the
 * failures actually reachable here are transient — a network drop or a 5xx
 * during a deploy — and telling that person to "ask Finance to code your
 * account" sends them after a problem they do not have.
 *
 * Branch on the error instead of describing the original report. `apiFetch`
 * attaches the real HTTP status to the thrown Error (lib/api.ts), so the
 * permission case is identified by status, not by matching prose that could be
 * reworded later.
 */

/** Denied the capability — the account cannot read GL data at all. */
export const ACCOUNTS_DENIED_MESSAGE =
  'The expense-account list could not be loaded because this account is not '
  + 'cleared for GL data. The amount can still be corrected here; ask Finance '
  + 'to code the account.'

/** Anything else: a network drop, a 5xx, a deploy window. Retrying is the fix. */
export const ACCOUNTS_UNAVAILABLE_MESSAGE =
  'The expense-account list could not be loaded, so only the amount can be '
  + 'corrected here. Close the panel and open it again to retry — if it keeps '
  + 'failing, report it.'

export function accountsLoadErrorMessage(err: unknown): string {
  const status = (err as { status?: number } | null | undefined)?.status
  return status === 403 ? ACCOUNTS_DENIED_MESSAGE : ACCOUNTS_UNAVAILABLE_MESSAGE
}
