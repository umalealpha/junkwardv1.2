'use client'

/**
 * /payments/exceptions — the exception cockpit.
 *
 * CFO's FNB & Payments improvement plan, 13-Sep-2026: 46 submitted batches worth
 * BWP 2.04m sit open and 16 failed ones sit unowned — "unclear ownership
 * encourages delayed action or unsafe resubmission."
 *
 * This screen reads the SAME check the daily email sends, so the screen and the
 * email can never tell two different stories — one parser, one truth.
 *
 * It is deliberately read-only. Two of the disagreements on production are
 * payments a person deliberately CANCELLED against a batch the bank had already
 * settled; a "Fix this" button that flipped them back to paid would erase a
 * human decision and the audit trail with it. This screen shows what is wrong
 * and who owns it. A person decides what to do about it.
 *
 * Omni moves no money. A payment request is a workflow record; money leaves at
 * FNB, authorised by a person with two-factor.
 */

import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2, Loader2, RefreshCw } from 'lucide-react'

import { TopBar } from '@/components/layout/TopBar'
import { Button } from '@/components/ui/button'
import { getPaymentExceptionCockpit, type PaymentExceptionCockpit } from '@/lib/api'

const NAVY = '#0D1B2A'
const GREY = '#6B7280'

// Worst first. A payment the bank has already made, that a person then
// cancelled, needs looking at before a queue that is merely out of date.
const ORDER = [
  'cancelled_but_settled',
  'settled_but_not_paid',
  'paid_but_batch_failed',
  'paid_but_batch_unconfirmed',
]

const money = (v: string | number) =>
  `BWP ${Number(v || 0).toLocaleString('en-BW', {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  })}`

export default function PaymentExceptionsPage() {
  const [data, setData] = useState<PaymentExceptionCockpit | null>(null)
  const [busy, setBusy] = useState(true)
  const [err, setErr] = useState<string | null>(null)

  const load = useCallback(async () => {
    setBusy(true)
    setErr(null)
    try {
      setData(await getPaymentExceptionCockpit())
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not load the check.')
    } finally {
      setBusy(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  return (
    <div className="flex min-h-screen flex-col">
      <TopBar
        title="Payments — exceptions"
        breadcrumbs={[{ label: 'Payments', href: '/payment-requests' },
                      { label: 'Exceptions' }]}
      />
      <div className="w-full flex-1 space-y-5 p-6">

        <div className="flex items-start justify-between gap-4">
          <p className="max-w-2xl text-sm" style={{ color: GREY }}>
            Every payment where Omni&apos;s record and the bank&apos;s answer do not
            agree, and every batch the bank has not settled. Nothing on this page
            changes a payment — it shows what needs a person.
          </p>
          <Button onClick={load} disabled={busy} variant="outline">
            {busy
              ? <Loader2 className="mr-1 h-4 w-4 animate-spin" />
              : <RefreshCw className="mr-1 h-4 w-4" />}
            Check again
          </Button>
        </div>

        {err && (
          <div className="rounded-lg border border-[#FCA5A5] bg-[#FEF2F2] p-3 text-sm text-[#991B1B]">
            {err}
          </div>
        )}

        {busy && !data && (
          <p className="text-sm" style={{ color: GREY }}>Checking…</p>
        )}

        {data && (
          <>
            <p className="text-xs" style={{ color: GREY }}>Checked {data.checked_at}</p>

            {data.clean ? (
              <div className="flex items-start gap-2 rounded-lg border border-[#BBF7D0] bg-[#F0FDF4] p-4">
                <CheckCircle2 className="mt-0.5 h-5 w-5 text-[#166534]" />
                <div>
                  <p className="font-semibold text-[#166534]">
                    Omni and the bank agree on every payment that has a batch.
                  </p>
                  <p className="mt-1 text-sm" style={{ color: GREY }}>
                    Payments made another way, with no bank batch at all, are not
                    counted here — there is nothing to compare them against.
                  </p>
                </div>
              </div>
            ) : (
              <div className="flex items-start gap-2 rounded-lg border border-[#FCA5A5] bg-[#FEF2F2] p-4">
                <AlertTriangle className="mt-0.5 h-5 w-5 text-[#991B1B]" />
                <p className="font-semibold text-[#991B1B]">
                  {data.contradiction_count} payment
                  {data.contradiction_count === 1 ? '' : 's'} where Omni and the
                  bank do not agree.
                </p>
              </div>
            )}

            {ORDER.filter(k => (data.contradictions[k] || []).length > 0).map(key => {
              const rows = data.contradictions[key]
              const meaning = data.findings_meaning[key]
              return (
                <section key={key}>
                  <h2 className="text-sm font-semibold" style={{ color: NAVY }}>
                    {meaning?.headline} — {rows.length}
                  </h2>
                  <p className="mb-2 text-xs" style={{ color: GREY }}>
                    {meaning?.what_it_means}
                  </p>
                  <div className="overflow-x-auto rounded-lg border border-[#E5E7EB]">
                    <table className="w-full min-w-[760px] text-sm">
                      <thead className="bg-[#F9FAFB]">
                        <tr>
                          {['Reference', 'Payee', 'Amount', 'Omni says',
                            'Bank says', 'Age'].map(h => (
                            <th key={h} className="px-3 py-2 text-left text-xs font-semibold"
                                style={{ color: GREY }}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {rows.map(r => (
                          <tr key={r.ref} className="border-t border-[#F3F4F6]">
                            <td className="px-3 py-2 font-medium" style={{ color: NAVY }}>
                              {r.ref}
                            </td>
                            <td className="px-3 py-2">{r.payee}</td>
                            <td className="whitespace-nowrap px-3 py-2">{money(r.total)}</td>
                            <td className="px-3 py-2">{r.omni_status}</td>
                            <td className="px-3 py-2">{r.batch_status}</td>
                            <td className="whitespace-nowrap px-3 py-2">
                              {r.age_days === null ? '—' : `${r.age_days}d`}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </section>
              )
            })}

            {/* The CFO's read-only reconciliation, 17-Sep-2026. He asked to see
                the list before anything on a live record is changed, so there is
                deliberately no button here. One row per payment request, not per
                bank instruction: a supplier request processed line-by-line
                becomes one instruction per line, and four rejects from one
                supplier are one business problem. */}
            {data.rejected_not_open && data.rejected_not_open.count > 0 && (
              <section>
                <h2 className="text-sm font-semibold" style={{ color: NAVY }}>
                  Rejected or unconfirmed at the bank, but the request is closed —{' '}
                  {data.rejected_not_open.count} payment
                  {data.rejected_not_open.count === 1 ? '' : 's'},{' '}
                  {money(data.rejected_not_open.total)}
                </h2>
                <p className="mb-2 max-w-3xl text-xs" style={{ color: GREY }}>
                  {data.rejected_not_open.note}
                  {data.rejected_not_open.instruction_count !==
                    data.rejected_not_open.count && (
                    <>
                      {' '}These {data.rejected_not_open.count} payments carry{' '}
                      {data.rejected_not_open.instruction_count} separate bank
                      instructions between them.
                    </>
                  )}
                </p>
                <div className="overflow-x-auto rounded-lg border border-[#E5E7EB]">
                  <table className="w-full min-w-[860px] text-sm">
                    <thead className="bg-[#F9FAFB]">
                      <tr>
                        {['Reference', 'Payee', 'Amount', 'Omni says',
                          'Bank instructions', 'Bank said', 'Evidence on file'].map(h => (
                          <th key={h} className="px-3 py-2 text-left text-xs font-semibold"
                              style={{ color: GREY }}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {data.rejected_not_open.rows.map(r => (
                        <tr key={r.ref} className="border-t border-[#F3F4F6] align-top">
                          <td className="px-3 py-2 font-medium" style={{ color: NAVY }}>
                            {r.ref}
                          </td>
                          <td className="px-3 py-2">{r.payee}</td>
                          <td className="whitespace-nowrap px-3 py-2">{money(r.total)}</td>
                          <td className="px-3 py-2">{r.omni_status}</td>
                          <td className="px-3 py-2">
                            {r.instructions.length}
                            {r.processing_method === 'individual' && (
                              <span className="ml-1 text-xs" style={{ color: GREY }}>
                                (one per line)
                              </span>
                            )}
                          </td>
                          <td className="px-3 py-2">
                            {r.bank_reasons.length ? r.bank_reasons.join(', ') : '—'}
                          </td>
                          <td className="px-3 py-2">
                            {r.evidence_recorded ? (
                              <span className="text-[#166534]">Yes</span>
                            ) : (
                              <span className="text-[#991B1B]">None</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            )}

            {/* Bank notifications. The historic pile is shown separately from the
                live alert on purpose: with 863 old ones counted in, the alert
                could never read zero again, and an alarm that is always on is an
                alarm nobody reads. */}
            {data.notifications && (
              <section>
                <h2 className="text-sm font-semibold" style={{ color: NAVY }}>
                  Bank notifications
                </h2>
                <p className="mb-2 max-w-3xl text-xs" style={{ color: GREY }}>
                  {data.notifications.stuck === 0 ? (
                    <>Nothing is stuck. Every notification received since{' '}
                    {data.notifications.backlog_before} and older than{' '}
                    {data.notifications.stuck_hours} hours has been processed
                    {data.notifications.failed > 0
                      ? `, though ${data.notifications.failed} could not be classified at all.`
                      : '.'}</>
                  ) : (
                    <span className="font-semibold text-[#991B1B]">
                      {data.notifications.stuck} notification
                      {data.notifications.stuck === 1 ? '' : 's'} received in the
                      last while have not been processed — the worker has stalled.
                    </span>
                  )}
                  {data.notifications.backlog > 0 && (
                    <> {data.notifications.backlog_note} There are{' '}
                    {data.notifications.backlog} of them.</>
                  )}
                </p>
              </section>
            )}

            <section>
              <h2 className="text-sm font-semibold" style={{ color: NAVY }}>
                Batches the bank has not settled
              </h2>
              <p className="mb-2 text-xs" style={{ color: GREY }}>
                Anything over {data.batches.stale_days} days is called out. A batch
                marked <b>unknown</b> means the instruction left us and no clear
                answer came back — the money may have moved, so it must never be
                resent until the bank confirms.
              </p>
              {Object.entries(data.batches.groups).map(([status, g]) => (
                <div key={status} className="mb-4">
                  <p className="mb-1 text-sm font-medium" style={{ color: NAVY }}>
                    {status} — {g.count} batch{g.count === 1 ? '' : 'es'}, {money(g.total)}
                    {g.over_threshold > 0 && (
                      <span className="ml-2 text-[#991B1B]">
                        {g.over_threshold} over {data.batches.stale_days} days
                      </span>
                    )}
                  </p>
                  <div className="overflow-x-auto rounded-lg border border-[#E5E7EB]">
                    <table className="w-full min-w-[760px] text-sm">
                      <thead className="bg-[#F9FAFB]">
                        <tr>
                          {['Batch', 'Payments', 'Amount', 'Age', 'Sent by',
                            'Last word from FNB'].map(h => (
                            <th key={h} className="px-3 py-2 text-left text-xs font-semibold"
                                style={{ color: GREY }}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {g.rows.map(b => (
                          <tr key={b.key} className="border-t border-[#F3F4F6]">
                            <td className="px-3 py-2 font-medium" style={{ color: NAVY }}>
                              {b.key}
                            </td>
                            <td className="px-3 py-2">{b.payments}</td>
                            <td className="whitespace-nowrap px-3 py-2">{money(b.total)}</td>
                            <td className="whitespace-nowrap px-3 py-2">
                              {b.age_days === null ? '—' : `${b.age_days}d`}
                            </td>
                            <td className="px-3 py-2">{b.owner}</td>
                            <td className="px-3 py-2 text-xs" style={{ color: GREY }}>
                              {b.last_word_from_fnb || '—'}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              ))}
            </section>
          </>
        )}
      </div>
    </div>
  )
}
