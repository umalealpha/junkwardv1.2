'use client'

/**
 * LargePaymentRequestDialog — "Request large claim payment".
 *
 * CFO 2026-09-11, in his words: "i want a button, request large claim payment,
 * when we click the button list of payment appears in a cool box, name of the
 * client and the amount of the payment, when we click the check box and press
 * enter a large claim request is made and sits for my approval, but during that
 * time it can take some time to get the claims details through APIs from
 * Graphite, so it should say 10% done 20% done 60% done etc."
 *
 * THE PROGRESS NUMBER IS REAL. It is the count of claims actually fetched from
 * Graphite, written to the database by the server and read back here. Nothing on
 * this screen is animated to look busy: a fake progress bar in a payment screen
 * is a lie, and the point of this module is that nothing on it is guessed.
 *
 * Everything shown for a payment comes from a payment that has ALREADY been
 * raised, signed off by Finance and loaded to FNB through Omni's own gated path.
 * This dialog creates no payment, amends none, and moves no money.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, CheckCircle2, Loader2, RefreshCw, Send, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  createLargePaymentRequest, largePaymentCandidates, largePaymentDetail,
  retryLargePaymentEnrich,
  type LargePaymentCandidate, type LargePaymentRequest,
} from '@/lib/api'

const money = (v: string | number) =>
  Number(v).toLocaleString('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

export default function LargePaymentRequestDialog(
  { onClose, onRaised }: { onClose: () => void; onRaised?: (r: LargePaymentRequest) => void },
) {
  const [rows, setRows] = useState<LargePaymentCandidate[] | null>(null)
  const [threshold, setThreshold] = useState('')
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [title, setTitle] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [req, setReq] = useState<LargePaymentRequest | null>(null)

  // ── load the box ──────────────────────────────────────────────────────────
  useEffect(() => {
    let alive = true
    largePaymentCandidates()
      .then(d => {
        if (!alive) return
        setRows(d.rows)
        setThreshold(d.threshold)
        // Everything over the cut-off is ticked for him. Smaller ones are shown
        // but unticked, because he asked to be able to add one by hand.
        setPicked(new Set(d.rows.filter(r => r.over_threshold).map(r => r.payment_request_id)))
        const today = new Date().toLocaleDateString('en-GB',
          { day: 'numeric', month: 'long', year: 'numeric' })
        setTitle(`Claims Payments ${today}`)
      })
      .catch(e => alive && setErr(e instanceof Error ? e.message : 'Could not load the payments.'))
    return () => { alive = false }
  }, [])

  // ── poll the real progress while Graphite is being read ───────────────────
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const stopPolling = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
  }, [])

  // Depends on the id and status ONLY, never on the whole object. Depending on
  // `req` rebuilt the interval on every tick, and a reply slower than 1.2s could
  // then land out of order and walk the percentage backwards (Fable 5.1,
  // 2026-09-11). The in-flight ref makes sure one poll is in the air at a time.
  const inFlight = useRef(false)
  const reqId = req?.id
  const reqStatus = req?.status

  useEffect(() => {
    if (!reqId || reqStatus !== 'enriching') { stopPolling(); return }
    pollRef.current = setInterval(async () => {
      if (inFlight.current) return
      inFlight.current = true
      try {
        const fresh = await largePaymentDetail(reqId)
        setReq(fresh)
        if (fresh.status !== 'enriching') stopPolling()
      } catch {
        // A dropped poll is not a failure of the request — the server keeps
        // going and writes its progress to the database. Stay quiet and retry.
      } finally {
        inFlight.current = false
      }
    }, 1200)
    return stopPolling
  }, [reqId, reqStatus, stopPolling])

  const toggle = (id: string) =>
    setPicked(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })

  const raise = async () => {
    if (busy) return
    if (!picked.size) {
      setErr('Tick at least one payment first.')
      return
    }
    setBusy(true); setErr(null)
    try {
      const created = await createLargePaymentRequest([...picked], title.trim())
      setReq(created)
      onRaised?.(created)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not raise the request.')
    } finally {
      setBusy(false)
    }
  }

  const retry = async () => {
    if (!req || busy) return
    setBusy(true); setErr(null)
    try { setReq(await retryLargePaymentEnrich(req.id)) }
    catch (e) { setErr(e instanceof Error ? e.message : 'Could not try again.') }
    finally { setBusy(false) }
  }

  const pickedTotal = (rows || [])
    .filter(r => picked.has(r.payment_request_id))
    .reduce((s, r) => s + Number(r.amount), 0)

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4">
      <div className="w-full max-w-5xl rounded-xl bg-white shadow-xl">
        <div className="flex items-center justify-between border-b border-[#E5E7EB] px-5 py-3">
          <h2 className="text-base font-semibold text-[#0D1B2A]">
            Request large claim payment
          </h2>
          <button onClick={onClose} aria-label="Close"
                  className="text-[#6B7280] hover:text-[#0D1B2A]">
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="space-y-4 px-5 py-4">
          {err && (
            <div className="rounded-md border-2 border-[#DC2626] bg-[#FEE2E2] p-3 text-sm text-[#991B1B]">
              {err}
            </div>
          )}

          {/* ── STEP 2: the request has been raised, Graphite is being read ── */}
          {req ? (
            <>
              <div className="rounded-lg border border-[#E5E7EB] bg-[#F9FAFB] p-4">
                <p className="text-sm font-semibold text-[#0D1B2A]">{req.ref}</p>
                <p className="text-[13px] text-[#6B7280]">{req.title}</p>

                {req.status === 'enriching' && (
                  <div className="mt-3">
                    <div className="mb-1 flex items-center justify-between text-[13px]">
                      <span className="flex items-center gap-2 text-[#0D1B2A]">
                        <Loader2 className="h-4 w-4 animate-spin" />
                        Collecting the claim details from Graphite
                      </span>
                      <span className="font-semibold tabular-nums text-[#0D1B2A]">
                        {req.progress_pct}% done
                      </span>
                    </div>
                    <div className="h-2 w-full overflow-hidden rounded-full bg-[#E5E7EB]">
                      <div className="h-full rounded-full bg-[#F4A623] transition-[width] duration-500"
                           style={{ width: `${req.progress_pct}%` }} />
                    </div>
                    <p className="mt-1 text-[12px] text-[#6B7280]">
                      {req.progress_note || 'Starting…'}
                    </p>
                    {/* A run whose server was recycled mid-collection stays at its
                        last percentage for ever. Without this the spinner spins
                        and the only thing that could rescue it is never offered
                        (Fable 5.1, 2026-09-11). */}
                    {req.stalled && (
                      <div className="mt-2 rounded-md border border-[#FDE68A] bg-[#FEF3C7] p-2 text-[13px] text-[#92400E]">
                        This has not moved for a while, so it has probably stopped.
                        Nothing was sent and no payment was changed.
                        <Button onClick={retry} disabled={busy} className="ml-2">
                          <RefreshCw className="mr-1 h-4 w-4" /> Try again
                        </Button>
                      </div>
                    )}
                  </div>
                )}

                {req.status === 'enrich_failed' && (
                  <div className="mt-3 rounded-md border-2 border-[#DC2626] bg-[#FEE2E2] p-3 text-sm text-[#991B1B]">
                    <p className="font-semibold">The claim details could not be collected.</p>
                    <p className="mt-1">{req.enrich_error}</p>
                    <Button onClick={retry} disabled={busy} className="mt-2">
                      <RefreshCw className="mr-1 h-4 w-4" /> Try again
                    </Button>
                  </div>
                )}

                {req.status === 'pending_cfo' && (
                  <div className="mt-3 rounded-md border border-[#BBF7D0] bg-[#F0FDF4] p-3 text-sm text-[#166534]">
                    <p className="flex items-center gap-2 font-semibold">
                      <CheckCircle2 className="h-4 w-4" />
                      Done — {req.line_count} payment(s), BWP {money(req.total)}.
                    </p>
                    <p className="mt-1">
                      It is now waiting for the CFO&rsquo;s approval and a task has been
                      raised for him. Nothing has been sent to the CEO — no payment
                      was changed and no money moved.
                    </p>
                    {req.flagged_count > 0 && (
                      <p className="mt-1 flex items-start gap-2 text-[#B45309]">
                        <AlertTriangle className="mt-[2px] h-4 w-4 shrink-0" />
                        {req.flagged_count} of these need a look before approval.
                      </p>
                    )}
                  </div>
                )}
              </div>

              {/* What came back, as it stands. Flags are never hidden. */}
              {(req.lines || []).length > 0 && (
                <div className="overflow-x-auto rounded-lg border border-[#E5E7EB]">
                  <table className="w-full text-[13px]">
                    <thead className="bg-[#F9FAFB] text-left text-[11px] uppercase tracking-wider text-[#6B7280]">
                      <tr>
                        <th className="px-3 py-2">Claim</th>
                        <th className="px-3 py-2">Insured</th>
                        <th className="px-3 py-2">Payee</th>
                        <th className="px-3 py-2 text-right">Amount (BWP)</th>
                        <th className="px-3 py-2">Needs a look</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(req.lines || []).map(ln => (
                        <tr key={ln.id} className="border-t border-[#F3F4F6] align-top">
                          <td className="px-3 py-2 font-medium text-[#0D1B2A]">
                            {ln.claim_number || <span className="text-[#B45309]">none</span>}
                          </td>
                          <td className="px-3 py-2">
                            {ln.insured_name || <span className="text-[#B45309]">not in Graphite</span>}
                          </td>
                          <td className="px-3 py-2">{ln.payee}</td>
                          <td className="px-3 py-2 text-right tabular-nums">{money(ln.amount)}</td>
                          <td className="px-3 py-2">
                            {ln.flags.length === 0
                              ? <span className="text-[#6B7280]">—</span>
                              : ln.flags.map(f => (
                                  <p key={f.code} className="text-[12px] text-[#B45309]">{f.message}</p>
                                ))}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              <div className="flex justify-end">
                <Button onClick={onClose}>Close</Button>
              </div>
            </>
          ) : (
            /* ── STEP 1: pick the payments ──────────────────────────────── */
            <>
              {rows === null ? (
                <p className="flex items-center gap-2 py-8 text-sm text-[#6B7280]">
                  <Loader2 className="h-4 w-4 animate-spin" /> Finding the claim payments…
                </p>
              ) : rows.length === 0 ? (
                <p className="rounded-md border border-[#E5E7EB] bg-[#F9FAFB] p-4 text-sm text-[#6B7280]">
                  There are no claim payments waiting. A payment appears here once it
                  has been signed off by Finance and loaded to FNB, and it disappears
                  once it is on a request.
                </p>
              ) : (
                <>
                  <p className="text-[13px] text-[#6B7280]">
                    Everything over BWP {money(threshold)} is ticked for you. Smaller
                    payments are listed but not ticked — tick one to add it by hand.
                  </p>

                  <div className="max-h-[40vh] overflow-y-auto rounded-lg border border-[#E5E7EB]">
                    <table className="w-full text-[13px]">
                      <thead className="sticky top-0 bg-[#F9FAFB] text-left text-[11px] uppercase tracking-wider text-[#6B7280]">
                        <tr>
                          <th className="w-10 px-3 py-2"></th>
                          <th className="px-3 py-2">Claim</th>
                          <th className="px-3 py-2">Client / payee</th>
                          <th className="px-3 py-2 text-right">Amount (BWP)</th>
                        </tr>
                      </thead>
                      <tbody>
                        {rows.map(r => (
                          <tr key={r.payment_request_id}
                              className="cursor-pointer border-t border-[#F3F4F6] hover:bg-[#F9FAFB]"
                              onClick={() => toggle(r.payment_request_id)}>
                            <td className="px-3 py-2">
                              <input type="checkbox" checked={picked.has(r.payment_request_id)}
                                     onChange={() => toggle(r.payment_request_id)}
                                     onClick={e => e.stopPropagation()} />
                            </td>
                            <td className="px-3 py-2 font-medium text-[#0D1B2A]">
                              {r.claim_number || <span className="text-[#B45309]">no claim no.</span>}
                            </td>
                            <td className="px-3 py-2">{r.payee || r.subject}</td>
                            <td className="px-3 py-2 text-right tabular-nums">{money(r.amount)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>

                  <div>
                    <label className="mb-1 block text-xs font-semibold uppercase tracking-wider text-[#6B7280]">
                      What to call it
                    </label>
                    <input value={title} onChange={e => setTitle(e.target.value)}
                           className="w-full rounded border border-[#D1D5DB] px-3 py-2 text-sm" />
                  </div>

                  <div className="flex items-center justify-between border-t border-[#E5E7EB] pt-3">
                    <p className="text-sm text-[#0D1B2A]">
                      <b>{picked.size}</b> payment(s) selected —{' '}
                      <b className="tabular-nums">BWP {money(pickedTotal)}</b>
                    </p>
                    <Button onClick={raise} disabled={busy}>
                      {busy ? <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                            : <Send className="mr-1 h-4 w-4" />}
                      Make the request
                    </Button>
                  </div>
                </>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
