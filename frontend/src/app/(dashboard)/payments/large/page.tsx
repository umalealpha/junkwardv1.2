'use client'

/**
 * Large Payment Requests — the CEO authorisation requests for large claim
 * payments (CFO 2026-09-11).
 *
 * Piece 1: the button, the selection box, the live Graphite progress, and the
 * request landing on the CFO as a task. The CEO email with its approve / reject /
 * ask-a-question buttons is Piece 2 and is deliberately not here, so the part
 * where money is released on a link click gets its own build and its own test.
 */

import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, Check, FileText, Plus, Send, X } from 'lucide-react'

import { TopBar } from '@/components/layout/TopBar'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import LargePaymentRequestDialog from '@/components/payments/LargePaymentRequestDialog'
import { decideLargePayment, largePaymentList, resendLargePaymentToCeo,
         type LargePaymentRequest } from '@/lib/api'

const money = (v: string | number) =>
  Number(v).toLocaleString('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

const TONE: Record<string, string> = {
  enriching:     'bg-[#EFF6FF] text-[#1E3A8A] border-[#BFDBFE]',
  enrich_failed: 'bg-[#FEE2E2] text-[#991B1B] border-[#FCA5A5]',
  pending_cfo:   'bg-[#FEF3C7] text-[#92400E] border-[#FDE68A]',
  // Piece 2 — the CEO leg. 'approved' now means the CFO signed but the email did
  // NOT go out, so it is deliberately a WARNING colour, not a green one: it is an
  // unfinished state that needs Send again, not a success.
  approved:      'bg-[#FEE2E2] text-[#991B1B] border-[#FCA5A5]',
  sent_to_ceo:   'bg-[#EFF6FF] text-[#1E3A8A] border-[#BFDBFE]',
  question:      'bg-[#FEF3C7] text-[#92400E] border-[#FDE68A]',
  ceo_approved:  'bg-[#F0FDF4] text-[#166534] border-[#BBF7D0]',
  ceo_rejected:  'bg-[#FEE2E2] text-[#991B1B] border-[#FCA5A5]',
  rejected:      'bg-[#F3F4F6] text-[#374151] border-[#E5E7EB]',
  cancelled:     'bg-[#F3F4F6] text-[#374151] border-[#E5E7EB]',
}

export default function LargePaymentsPage() {
  const [rows, setRows] = useState<LargePaymentRequest[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [open, setOpen] = useState(false)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

  // A request stuck on 'approved' means the CFO signed and the email to Mr Iyer
  // did NOT go out. It is the one state that needs a human to press something,
  // so it gets its own button rather than being left to look finished.
  // FABLE 5.1, 2026-09-12: decideLargePayment existed in the API client and
  // NOTHING CALLED IT. There was no Approve button anywhere, so the CFO's own
  // task told him to "open it in Omni" and the page offered nothing to press —
  // the whole of Piece 2 (send, CEO task, his buttons) was unreachable from the
  // app. A finished back end with no way in is not a finished feature.
  const decide = async (id: string, decision: 'approve' | 'reject') => {
    let note = ''
    if (decision === 'reject') {
      note = (window.prompt('Why are you rejecting this request?') || '').trim()
      if (!note) return                      // cancelled, or gave no reason
    } else if (!window.confirm(
      'Approve this request and email it to Mr Iyer for authorisation?')) {
      return
    }
    setBusyId(id); setErr(null); setNote(null)
    try {
      const out = await decideLargePayment(id, decision, note)
      setNote(out.note_to_user || null)
      load()
    } catch (e) {
      // The 502 path carries the real reason: approved, but the email did not go.
      setErr(e instanceof Error ? e.message : 'Could not record the decision.')
      load()
    } finally {
      setBusyId(null)
    }
  }

  const resend = async (id: string) => {
    setBusyId(id)
    try { await resendLargePaymentToCeo(id); load(); setErr(null) }
    catch (e) { setErr(e instanceof Error ? e.message : 'Could not send it again.') }
    finally { setBusyId(null) }
  }

  const load = useCallback(() => {
    largePaymentList()
      .then(d => { setRows(d.results); setErr(null) })
      .catch(e => setErr(e instanceof Error ? e.message
                                            : 'Could not load the requests.'))
  }, [])

  useEffect(load, [load])

  return (
    <>
      <TopBar />
      <div className="mx-auto max-w-6xl space-y-4 px-4 py-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-semibold text-[#0D1B2A]">Large Payment Requests</h1>
            <p className="text-[13px] text-[#6B7280]">
              Claim payments already signed off by Finance and loaded to FNB,
              packaged into one authorisation request.
            </p>
          </div>
          <Button onClick={() => setOpen(true)}>
            <Plus className="mr-1 h-4 w-4" /> Request large claim payment
          </Button>
        </div>

        {err && (
          <div className="rounded-md border-2 border-[#DC2626] bg-[#FEE2E2] p-3 text-sm text-[#991B1B]">
            {err}
          </div>
        )}

        {note && (
          <div className="rounded-md border border-[#BBF7D0] bg-[#F0FDF4] p-3 text-sm text-[#166534]">
            {note}
          </div>
        )}

        <Card>
          <CardHeader><CardTitle>Requests</CardTitle></CardHeader>
          <CardContent>
            {rows === null ? (
              <p className="py-6 text-sm text-[#6B7280]">Loading…</p>
            ) : rows.length === 0 ? (
              <p className="py-6 text-sm text-[#6B7280]">
                No requests yet. Press <b>Request large claim payment</b> to make the first one.
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-[13px]">
                  <thead className="text-left text-[11px] uppercase tracking-wider text-[#6B7280]">
                    <tr>
                      <th className="px-3 py-2">Reference</th>
                      <th className="px-3 py-2">What it covers</th>
                      <th className="px-3 py-2 text-right">Payments</th>
                      <th className="px-3 py-2 text-right">Total (BWP)</th>
                      <th className="px-3 py-2">State</th>
                      <th className="px-3 py-2">Raised by</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map(r => (
                      <tr key={r.id} className="border-t border-[#F3F4F6]">
                        <td className="px-3 py-2 font-medium text-[#0D1B2A]">
                          <span className="flex items-center gap-1">
                            <FileText className="h-3.5 w-3.5 text-[#6B7280]" />
                            {r.ref}
                          </span>
                        </td>
                        <td className="px-3 py-2">
                          {r.title}
                          {r.flagged_count > 0 && (
                            <span className="ml-2 inline-flex items-center gap-1 text-[12px] text-[#B45309]">
                              <AlertTriangle className="h-3.5 w-3.5" />
                              {r.flagged_count} need a look
                            </span>
                          )}
                        </td>
                        <td className="px-3 py-2 text-right tabular-nums">{r.line_count}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{money(r.total)}</td>
                        <td className="px-3 py-2">
                          <span className={`rounded-full border px-2 py-[2px] text-[12px] ${TONE[r.status] || ''}`}>
                            {r.status === 'enriching'
                              ? `Collecting… ${r.progress_pct}%`
                              : r.status_label}
                          </span>
                        </td>
                        <td className="px-3 py-2 text-[#6B7280]">
                          {r.status === 'pending_cfo' ? (
                            <span className="flex gap-2">
                              <Button size="sm" disabled={busyId === r.id}
                                      onClick={() => decide(r.id, 'approve')}>
                                <Check className="mr-1 h-3.5 w-3.5" /> Approve &amp; send
                              </Button>
                              <Button size="sm" variant="outline" disabled={busyId === r.id}
                                      onClick={() => decide(r.id, 'reject')}>
                                <X className="mr-1 h-3.5 w-3.5" /> Reject
                              </Button>
                            </span>
                          ) : r.status === 'approved' ? (
                            <span className="flex flex-col gap-1">
                              <span className="text-[12px] text-[#991B1B]">
                                Signed by you, but the email to Mr Iyer did not go out.
                                {r.send_error ? ` ${r.send_error}` : ''}
                              </span>
                              <Button size="sm" variant="outline" disabled={busyId === r.id}
                                      onClick={() => resend(r.id)}>
                                <Send className="mr-1 h-3.5 w-3.5" /> Send again
                              </Button>
                            </span>
                          ) : r.status === 'question' ? (
                            <span className="text-[12px] text-[#92400E]">
                              Mr Iyer asked{' '}
                              {r.ceo_questions?.[r.ceo_questions.length - 1]?.name
                                || 'a question'}
                            </span>
                          ) : (
                            r.raised_by
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {open && (
        <LargePaymentRequestDialog
          onClose={() => { setOpen(false); load() }}
          onRaised={load}
        />
      )}
    </>
  )
}
