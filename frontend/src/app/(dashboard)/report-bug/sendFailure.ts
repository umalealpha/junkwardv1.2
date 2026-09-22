/** What to tell someone whose bug-report send just failed.
 *
 * Oratile Tlhomelang lost a feature request with ten attachments on
 * 2026-09-08 and all the screen said was "Failed to fetch". Her screenshots
 * are stamped 15:01 and 15:02 — the two minutes a deploy was swapping the
 * frontend underneath her — and the "A new version of Omni is ready" bar was
 * already showing in the same shot. The backend was fine: the identical ten
 * files, 1.92 MB, post in 2.5 seconds. Her tab was simply running the old
 * bundle when the new one landed.
 *
 * "Failed to fetch" is the browser's words for "the request never arrived",
 * and it tells the person nothing they can act on — so she refreshed a few
 * times and reported the feature as broken, which is the only reasonable
 * conclusion from what the screen said. Name the likely cause and the one
 * button that fixes it instead. Nothing is cleared on a failure either way:
 * the text and the files stay on screen, which is what makes "send it again"
 * honest advice rather than a request to retype everything.
 *
 * This lives beside the page rather than inside it: a Next.js App Router page
 * file may only export a component and its known fields, so a named export
 * there fails the production build (caught 2026-09-08 — `tsc --noEmit` does
 * NOT check that rule, only `next build` does).
 */
export function sendFailureMessage(e: unknown): string {
  const raw = (e as { message?: string } | null)?.message || ''
  // TypeError: Failed to fetch / Load failed (Safari) / NetworkError — the
  // request never reached the server, so there is no status code to read.
  const neverArrived = /failed to fetch|load failed|networkerror|network request failed/i
    .test(raw)
  if (neverArrived) {
    return 'Your report did not reach us — this usually means Omni updated '
      + 'while you were typing. Nothing is lost: your words and your files are '
      + 'still on this page. If a "A new version of Omni is ready" bar is '
      + 'showing, press Refresh now, then send again. If there is no bar, '
      + 'check your internet and press send again.'
  }
  return raw || 'Could not submit your report. Please try again — your words '
    + 'and your files are still on this page.'
}
