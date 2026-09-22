'use client'

/**
 * FnbPreSelectButton — "Pre-select in FNB" on a payment authorisation task.
 *
 * Hands the request text to the Alpha Direct FNB Pre-Selector Chrome extension,
 * which ticks the matching batches in FNB online banking. It NEVER authorises,
 * deletes or rejects — the CFO does that by hand (CFO 2026-08-03).
 *
 * Two routes, because a web page cannot reach into another site by itself:
 *   - In Chrome with the extension → postMessage; the extension picks it up.
 *   - In the OmniDesktop app (WebView2, no extensions) → an `alphafnb:` link,
 *     which the one-time Windows bridge turns into "open Chrome and hand over".
 *
 * This component only ever SENDS the request text. It performs no banking action.
 */
import { useEffect, useState } from 'react'
import { Landmark } from 'lucide-react'

/** Does this look like a payment authorisation request we can pre-select? */
export function isPaymentAuthBody(title?: string, body?: string): boolean {
  const t = `${title || ''}`
  const b = `${body || ''}`
  if (!/payment authorisation/i.test(t) && !/PAYMENT AUTHORISATION REQUEST/i.test(b)) return false
  // needs at least one G-number line with an amount, else there is nothing to tick
  return /G\d{6,}[\s\S]{0,120}?BWP\s*[\d,]+\.\d{2}/i.test(b)
}

function toBase64(s: string): string {
  // handles the — and · characters Omni uses in the request body
  return btoa(unescape(encodeURIComponent(s)))
}

export function FnbPreSelectButton({ body }: { body: string }) {
  const [hasExt, setHasExt] = useState(false)
  const [sent, setSent] = useState<string | null>(null)

  useEffect(() => {
    const check = () =>
      setHasExt(document.documentElement.getAttribute('data-fnb-preselect') === '1')
    check()
    const id = window.setTimeout(check, 400)   // the content script may land late
    const ack = (ev: MessageEvent) => {
      if (ev.source === window && (ev.data as { type?: string })?.type === 'ALPHA_FNB_PRESELECT_ACK') {
        setSent('Sent to Chrome — the FNB tab is opening. Press Preview there.')
      }
    }
    window.addEventListener('message', ack)
    return () => { window.clearTimeout(id); window.removeEventListener('message', ack) }
  }, [])

  const go = () => {
    if (hasExt) {
      // same-origin target, not '*' — the payload is a payment request
      window.postMessage({ type: 'ALPHA_FNB_PRESELECT', text: body, source: 'omni' },
                         window.location.origin)
      setSent('Sent to Chrome — the FNB tab is opening. Press Preview there.')
      return
    }
    // OmniDesktop (or Chrome without the add-on yet): hand over to Windows.
    try {
      window.location.href = `alphafnb://${encodeURIComponent(toBase64(body))}`
      setSent('Opening Chrome… if nothing happens, the one-time bridge is not installed yet.')
    } catch {
      setSent('Could not open Chrome automatically. Open the add-on and paste the request.')
    }
  }

  return (
    <div className="mt-3">
      <button
        type="button" onClick={go}
        className="inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold text-white"
        style={{ background: '#0D1B2A' }}
        title="Ticks the matching batches in FNB. It cannot authorise, delete or reject."
      >
        <Landmark className="h-4 w-4" /> Pre-select in FNB
      </button>
      <p className="mt-1.5 text-xs text-[#6B7280]">
        Ticks the matching batches for you in FNB online banking. It shows you the selection first
        and <b>never</b> presses Authorise — you still do that yourself.
      </p>
      {sent && <p className="mt-1 text-xs font-medium text-[#065F46]">{sent}</p>}
    </div>
  )
}

export default FnbPreSelectButton
