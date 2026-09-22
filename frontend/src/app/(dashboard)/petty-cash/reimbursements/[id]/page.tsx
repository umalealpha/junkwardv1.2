'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams, useRouter } from 'next/navigation'
import Link from 'next/link'
import {
  getToken, getMe,
  getPettyCashReimbursement,
  submitPettyCashReimbursement,
  fmReviewPettyCashReimbursement,
  rejectPettyCashReimbursement,
  reopenPettyCashReimbursement,
  postPettyCashReimbursement,
  deletePettyCashReimbursement,
  emailPettyCashReimbursementLink,
  type PettyCashReimbursement,
  type UserProfile,
} from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  ArrowLeft, AlertTriangle, CheckCircle2, Send, X, Landmark, ScrollText, Coins, Mail,
} from 'lucide-react'

// People the link is most often sent to (sender can still type any group email).
const QUICK_RECIPIENTS = [
  { label: 'Kago Tshutlhedi', email: 'ktshutlhedi@alphadirect.co.bw' },
  { label: 'Pako Kago', email: 'pkago@alphadirect.co.bw' },
  { label: 'CFO', email: 'pganesharajah@alphadirect.co.bw' },
]

function fmtMoney(s: string | number | null | undefined): string {
  if (s === null || s === undefined || s === '') return '—'
  const n = typeof s === 'number' ? s : Number(s)
  if (!isFinite(n)) return '—'
  return n.toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
function fmtDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })
}

const STATUS_BADGE: Record<string, { bg: string; fg: string }> = {
  draft:       { bg: '#F3F4F6', fg: '#374151' },
  pending_fm:  { bg: '#FFFBEB', fg: '#92400E' },
  pending_cfo: { bg: '#EFF6FF', fg: '#1D4ED8' },
  posted:      { bg: '#ECFDF5', fg: '#047857' },
  rejected:    { bg: '#FEF2F2', fg: '#B91C1C' },
}

export default function PettyCashReimbursementReviewPage() {
  const router = useRouter()
  const params = useParams()
  const id = String(params?.id || '')

  const [r, setR] = useState<PettyCashReimbursement | null>(null)
  const [me, setMe] = useState<UserProfile | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [committed, setCommitted] = useState(false)   // CFO clicked "Commit" -> reveal GL
  const [showReject, setShowReject] = useState(false)
  const [rejectReason, setRejectReason] = useState('')
  // "Email link" panel
  const [showEmail, setShowEmail] = useState(false)
  const [emailTo, setEmailTo] = useState('')
  const [emailNote, setEmailNote] = useState('')
  const [emailBusy, setEmailBusy] = useState(false)
  const [emailMsg, setEmailMsg] = useState<string | null>(null)

  async function sendLink() {
    setEmailBusy(true); setEmailMsg(null)
    try {
      const res = await emailPettyCashReimbursementLink(id, emailTo.trim(), emailNote.trim())
      setEmailMsg(`Sent to ${res.recipient}.`)
      setEmailTo(''); setEmailNote('')
      setTimeout(() => { setShowEmail(false); setEmailMsg(null) }, 2000)
    } catch (err) {
      setEmailMsg(err instanceof Error ? err.message : 'Could not send.')
    } finally {
      setEmailBusy(false)
    }
  }

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const [rr, mm] = await Promise.all([getPettyCashReimbursement(id), getMe()])
      setR(rr); setMe(mm)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load reimbursement')
    } finally {
      setLoading(false)
    }
  }, [id])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    if (id) void load()
  }, [router, id, load])

  async function act(fn: () => Promise<PettyCashReimbursement>, redirectList = false) {
    setBusy(true); setError(null)
    try {
      const updated = await fn()
      setR(updated)
      setCommitted(false); setShowReject(false); setRejectReason('')
      if (redirectList) router.replace('/petty-cash/reimbursements')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Action failed')
    } finally {
      setBusy(false)
    }
  }

  async function discard() {
    if (!window.confirm('Discard this draft reimbursement? The vouchers are released back to the next top-up.')) return
    setBusy(true); setError(null)
    try {
      await deletePettyCashReimbursement(id)
      router.replace('/petty-cash/reimbursements')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to discard')
      setBusy(false)
    }
  }

  const title = me?.title
  const isFM = title === 'finance_manager' || title === 'financial_controller'
  const isCFO = title === 'cfo' || me?.is_administrator === true
  const isCreator = !!me?.username && me.username === r?.created_by_username

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Reimbursement Review"
        breadcrumbs={[
          { label: 'Petty Cash', href: '/petty-cash' },
          { label: 'Reimbursements', href: '/petty-cash/reimbursements' },
          { label: r?.reimbursement_number || '…' },
        ]}
        actions={
          <div className="flex gap-2">
            <Button variant="outline" size="sm" leftIcon={<Mail className="w-3.5 h-3.5" />}
              onClick={() => { setShowEmail((v) => !v); setEmailMsg(null) }}>
              Email link
            </Button>
            <Link href="/petty-cash/reimbursements">
              <Button variant="outline" size="sm" leftIcon={<ArrowLeft className="w-3.5 h-3.5" />}>Back</Button>
            </Link>
          </div>
        }
      />

      <div className="flex-1 p-6 max-w-4xl space-y-4">
        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 text-[#DC2626] flex-shrink-0 mt-0.5" />
            <p className="text-[#DC2626] text-sm whitespace-pre-wrap">{error}</p>
          </div>
        )}

        {/* Email-link panel — "I can't find it / send it to the right person" */}
        {showEmail && (
          <Card>
            <CardContent className="p-4">
              <p className="text-sm font-medium text-[#374151] mb-1">Email this reimbursement's link</p>
              <p className="text-xs text-[#6B7280] mb-3">
                Sends whoever you pick a direct link to open this reimbursement. Alpha Direct addresses only.
              </p>
              <div className="flex flex-wrap gap-1.5 mb-2">
                {QUICK_RECIPIENTS.map((q) => (
                  <button key={q.email} type="button" onClick={() => setEmailTo(q.email)}
                    className={`text-xs px-2.5 py-1 rounded border ${emailTo === q.email ? 'bg-[#0B0B3B] text-white border-[#0B0B3B]' : 'bg-white text-[#374151] border-[#D1D5DB] hover:bg-[#F9FAFB]'}`}>
                    {q.label}
                  </button>
                ))}
              </div>
              <input type="email" value={emailTo} onChange={(e) => setEmailTo(e.target.value)}
                placeholder="name@alphadirect.co.bw"
                className="w-full h-10 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm mb-2" />
              <input type="text" value={emailNote} onChange={(e) => setEmailNote(e.target.value)}
                placeholder="Optional note" maxLength={500}
                className="w-full h-10 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm mb-2" />
              {emailMsg && <p className="text-xs text-[#047857] mb-2">{emailMsg}</p>}
              <div className="flex justify-end gap-2">
                <Button variant="outline" size="sm" onClick={() => setShowEmail(false)}>Close</Button>
                <Button size="sm" disabled={emailBusy || !emailTo.trim()} leftIcon={<Send className="w-3.5 h-3.5" />} onClick={sendLink}>
                  {emailBusy ? 'Sending…' : 'Send link'}
                </Button>
              </div>
            </CardContent>
          </Card>
        )}

        {loading && <p className="text-sm text-[#6B7280]">Loading…</p>}

        {r && (
          <>
            {/* Header / status */}
            <Card>
              <CardHeader>
                <div className="flex items-center justify-between">
                  <CardTitle className="flex items-center gap-2">
                    <Coins className="w-4 h-4 text-[#F07F00]" />
                    {r.reimbursement_number} — {r.location_name}
                  </CardTitle>
                  <span
                    className="inline-flex text-[11px] font-semibold uppercase tracking-wider px-2.5 py-1 rounded border"
                    style={{
                      background: (STATUS_BADGE[r.status] || STATUS_BADGE.draft).bg,
                      color: (STATUS_BADGE[r.status] || STATUS_BADGE.draft).fg,
                    }}
                  >
                    {r.status_display}
                  </span>
                </div>
              </CardHeader>
              <CardContent>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                  <div>
                    <p className="text-[10px] uppercase tracking-wider text-[#6B7280]">Period</p>
                    <p className="text-sm text-[#111827]">{fmtDate(r.period_start)} → {fmtDate(r.period_end)}</p>
                  </div>
                  <div>
                    <p className="text-[10px] uppercase tracking-wider text-[#6B7280]">Vouchers</p>
                    <p className="text-sm text-[#111827]">{r.vouchers?.length ?? r.voucher_count}</p>
                  </div>
                  <div>
                    <p className="text-[10px] uppercase tracking-wider text-[#6B7280]">Total</p>
                    <p className="text-lg font-bold font-mono tabular-nums text-[#0B0B3B]">
                      BWP {fmtMoney(r.gl_preview?.total ?? r.total_amount)}
                    </p>
                  </div>
                  <div>
                    <p className="text-[10px] uppercase tracking-wider text-[#6B7280]">Bank JE</p>
                    <p className="text-sm font-mono text-[#047857]">{r.je_number || '—'}</p>
                  </div>
                </div>

                {/* Who did what */}
                <div className="mt-4 flex flex-wrap gap-x-6 gap-y-1 text-xs text-[#6B7280]">
                  {r.submitted_by_username && <span>Submitted by <b className="text-[#374151]">{r.submitted_by_username}</b></span>}
                  {r.fm_reviewed_by_username && <span>FM-reviewed by <b className="text-[#374151]">{r.fm_reviewed_by_username}</b></span>}
                  {r.posted_by_username && <span>Bank-approved by <b className="text-[#374151]">{r.posted_by_username}</b></span>}
                </div>

                {r.status === 'rejected' && r.rejection_reason && (
                  <div className="mt-3 p-2.5 rounded bg-[#FEF2F2] border border-[#FEE2E2] text-sm text-[#B91C1C]">
                    Rejected: {r.rejection_reason}
                  </div>
                )}
              </CardContent>
            </Card>

            {/* Vouchers being reimbursed */}
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-sm">
                  <ScrollText className="w-4 h-4 text-[#6B7280]" />
                  Vouchers in this reimbursement
                </CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                <div className="overflow-x-auto">
                  <table className="min-w-full text-sm">
                    <thead className="bg-[#F9FAFB] border-y border-[#E5E7EB]">
                      <tr>
                        <th className="text-left px-4 py-2 font-semibold text-[#374151]">Voucher</th>
                        <th className="text-left px-4 py-2 font-semibold text-[#374151]">Date</th>
                        <th className="text-left px-4 py-2 font-semibold text-[#374151]">Payee</th>
                        <th className="text-left px-4 py-2 font-semibold text-[#374151]">Expense account</th>
                        <th className="text-right px-4 py-2 font-semibold text-[#374151]">Amount</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(r.vouchers || []).map((v) => (
                        <tr key={v.id} className="border-b border-[#F3F4F6]">
                          <td className="px-4 py-2 font-mono text-xs text-[#0B0B3B]">{v.voucher_number}</td>
                          <td className="px-4 py-2 text-[#374151]">{fmtDate(v.voucher_date)}</td>
                          <td className="px-4 py-2 text-[#374151]">{v.payee}</td>
                          <td className="px-4 py-2 text-xs text-[#6B7280]">{v.expense_account_code} · {v.expense_account_name}</td>
                          <td className="px-4 py-2 text-right font-mono tabular-nums text-[#111827]">{fmtMoney(v.amount)}</td>
                        </tr>
                      ))}
                      {(!r.vouchers || r.vouchers.length === 0) && (
                        <tr><td colSpan={5} className="px-4 py-6 text-center text-sm text-[#9CA3AF]">No vouchers in scope.</td></tr>
                      )}
                      {r.vouchers && r.vouchers.length > 0 && (
                        <tr className="bg-[#F9FAFB]">
                          <td colSpan={4} className="px-4 py-2 font-semibold text-[#374151]">Total</td>
                          <td className="px-4 py-2 text-right font-mono tabular-nums font-bold text-[#0B0B3B]">
                            {fmtMoney(r.gl_preview?.total ?? r.total_amount)}
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </CardContent>
            </Card>

            {/* GL preview — shown after CFO commits, or once posted */}
            {r.gl_preview && (committed || r.status === 'posted') && (
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-sm">
                    <Landmark className="w-4 h-4 text-[#0B0B3B]" />
                    {r.status === 'posted' ? 'Journal entry posted' : 'This is what will post to the bank'}
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="overflow-x-auto border border-[#E5E7EB] rounded">
                    <table className="min-w-full text-sm">
                      <thead className="bg-[#F9FAFB]">
                        <tr>
                          <th className="text-left px-4 py-2 font-semibold text-[#374151]">GL account</th>
                          <th className="text-right px-4 py-2 font-semibold text-[#374151]">Debit</th>
                          <th className="text-right px-4 py-2 font-semibold text-[#374151]">Credit</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr className="border-t border-[#F3F4F6]">
                          <td className="px-4 py-2"><span className="font-mono text-xs">{r.gl_preview.debit.code}</span> · {r.gl_preview.debit.name}</td>
                          <td className="px-4 py-2 text-right font-mono tabular-nums text-[#111827]">{fmtMoney(r.gl_preview.debit.amount)}</td>
                          <td className="px-4 py-2 text-right text-[#9CA3AF]">—</td>
                        </tr>
                        <tr className="border-t border-[#F3F4F6]">
                          <td className="px-4 py-2"><span className="font-mono text-xs">{r.gl_preview.credit.code}</span> · {r.gl_preview.credit.name}</td>
                          <td className="px-4 py-2 text-right text-[#9CA3AF]">—</td>
                          <td className="px-4 py-2 text-right font-mono tabular-nums text-[#111827]">{fmtMoney(r.gl_preview.credit.amount)}</td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                  {r.status === 'pending_cfo' && committed && (
                    <p className="mt-3 text-xs text-[#6B7280]">
                      Money leaves the bank when you approve. Confirm the accounts above are correct.
                    </p>
                  )}
                </CardContent>
              </Card>
            )}

            {/* Reject reason box */}
            {showReject && (
              <Card>
                <CardContent className="p-4">
                  <label className="block text-xs font-medium text-[#374151] mb-1.5">Reason for rejecting</label>
                  <textarea
                    value={rejectReason} onChange={(e) => setRejectReason(e.target.value)}
                    rows={2} placeholder="e.g. two vouchers have no receipt"
                    className="w-full bg-white border border-[#D1D5DB] rounded-md px-3 py-2 text-sm"
                  />
                  <div className="mt-2 flex justify-end gap-2">
                    <Button variant="outline" size="sm" onClick={() => { setShowReject(false); setRejectReason('') }}>Cancel</Button>
                    <Button size="sm" disabled={busy || rejectReason.trim().length < 3}
                      onClick={() => act(() => rejectPettyCashReimbursement(id, rejectReason.trim()))}>
                      Confirm reject
                    </Button>
                  </div>
                </CardContent>
              </Card>
            )}

            {/* Action bar — depends on status + role */}
            <div className="flex flex-wrap justify-end gap-3">
              {/* DRAFT — maker resubmits or discards */}
              {r.status === 'draft' && (
                <>
                  <Button variant="outline" onClick={discard} disabled={busy} leftIcon={<X className="w-4 h-4" />}>Discard</Button>
                  <Button onClick={() => act(() => submitPettyCashReimbursement(id))} disabled={busy} leftIcon={<Send className="w-4 h-4" />}>
                    Send to FM review
                  </Button>
                </>
              )}

              {/* PENDING_FM — FM approves to CFO, or rejects */}
              {r.status === 'pending_fm' && isFM && !showReject && (
                <>
                  <Button variant="outline" onClick={() => setShowReject(true)} disabled={busy}>Reject</Button>
                  <Button onClick={() => act(() => fmReviewPettyCashReimbursement(id))} disabled={busy} leftIcon={<CheckCircle2 className="w-4 h-4" />}>
                    Approve — send to CFO
                  </Button>
                </>
              )}
              {r.status === 'pending_fm' && !isFM && (
                <p className="text-sm text-[#6B7280] self-center">Waiting for Finance Manager review.</p>
              )}

              {/* PENDING_CFO — CFO commits (reveal GL), then approves the bank payment */}
              {r.status === 'pending_cfo' && isCFO && !showReject && (
                <>
                  <Button variant="outline" onClick={() => setShowReject(true)} disabled={busy}>Reject</Button>
                  {!committed ? (
                    <Button onClick={() => setCommitted(true)} disabled={busy} leftIcon={<Landmark className="w-4 h-4" />}>
                      Commit — show GL accounts
                    </Button>
                  ) : (
                    <Button onClick={() => act(() => postPettyCashReimbursement(id))} disabled={busy} leftIcon={<CheckCircle2 className="w-4 h-4" />}>
                      {busy ? 'Posting…' : 'Approve in bank'}
                    </Button>
                  )}
                </>
              )}
              {r.status === 'pending_cfo' && !isCFO && (
                <p className="text-sm text-[#6B7280] self-center">Reviewed by FM — waiting for CFO approval.</p>
              )}

              {/* REJECTED — creator reopens */}
              {r.status === 'rejected' && isCreator && (
                <Button onClick={() => act(() => reopenPettyCashReimbursement(id))} disabled={busy}>
                  Reopen &amp; rework
                </Button>
              )}

              {/* POSTED — done */}
              {r.status === 'posted' && (
                <div className="w-full bg-[#ECFDF5] border border-[#A7F3D0] rounded-lg p-3 flex items-center gap-2">
                  <CheckCircle2 className="w-5 h-5 text-[#059669]" />
                  <p className="text-[#047857] text-sm">
                    Approved and posted to the bank. Journal entry <span className="font-mono">{r.je_number}</span>. Float restored.
                  </p>
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
