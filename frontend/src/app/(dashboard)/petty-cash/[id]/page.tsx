'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams, useRouter } from 'next/navigation'
import Link from 'next/link'
import {
  getToken,
  getPettyCashVoucher,
  submitPettyCashVoucher,
  approvePettyCashVoucher,
  rejectPettyCashVoucher,
  amendPettyCashVoucher,
  returnPettyCashVoucherToDraft,
  getAllAccounts,
  reopenPettyCashVoucher,
  emailPettyCashVoucherLink,
  getPettyCashVoucherReceipts,
  uploadPettyCashVoucherReceipt,
  deletePettyCashVoucherReceipt,
  downloadPettyCashReceipt,
  type PettyCashVoucher,
  type PettyCashReceipt,
} from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { SearchableSelect } from '@/components/ui/SearchableSelect'
import { accountsLoadErrorMessage } from '../accountsLoadError'
import {
  ArrowLeft, Send, CheckCircle2, XCircle, RotateCcw, AlertTriangle,
  Receipt, Clock, Mail, Paperclip, Upload, Trash2, FileText, Pencil,
} from 'lucide-react'

const QUICK_RECIPIENTS = [
  { label: 'Kago Tshutlhedi', email: 'ktshutlhedi@alphadirect.co.bw' },
  { label: 'Pako Kago', email: 'pkago@alphadirect.co.bw' },
  { label: 'CFO', email: 'pganesharajah@alphadirect.co.bw' },
]

function fmtMoney(s: string | null | undefined): string {
  if (!s) return '—'
  const n = Number(s)
  if (!isFinite(n)) return s
  return n.toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
function fmtDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('en-GB', {
    day: '2-digit', month: 'short', year: 'numeric',
  })
}
function fmtDateTime(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('en-GB', {
    day: '2-digit', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

const STATUS_BADGE: Record<string, { bg: string; fg: string }> = {
  draft:            { bg: '#F3F4F6', fg: '#374151' },
  pending_approval: { bg: '#FFFBEB', fg: '#92400E' },
  one_signature:    { bg: '#FEF3C7', fg: '#B45309' },
  posted:           { bg: '#ECFDF5', fg: '#047857' },
  reimbursed:       { bg: '#EFF6FF', fg: '#1D4ED8' },
  rejected:         { bg: '#FEF2F2', fg: '#B91C1C' },
}

export default function PettyCashVoucherDetailPage() {
  const router = useRouter()
  const params = useParams<{ id: string }>()
  const id = params?.id as string

  const [v, setV] = useState<PettyCashVoucher | null>(null)
  const [loading, setLoading] = useState(true)
  const [acting, setActing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showReject, setShowReject] = useState(false)
  const [rejectReason, setRejectReason] = useState('')
  const [showEmail, setShowEmail] = useState(false)
  const [emailTo, setEmailTo] = useState('')
  const [emailNote, setEmailNote] = useState('')
  const [emailBusy, setEmailBusy] = useState(false)
  const [emailMsg, setEmailMsg] = useState<string | null>(null)

  async function sendLink() {
    setEmailBusy(true); setEmailMsg(null)
    try {
      const res = await emailPettyCashVoucherLink(id, emailTo.trim(), emailNote.trim())
      setEmailMsg(`Sent to ${res.recipient}.`)
      setEmailTo(''); setEmailNote('')
      setTimeout(() => { setShowEmail(false); setEmailMsg(null) }, 2000)
    } catch (err) {
      setEmailMsg(err instanceof Error ? err.message : 'Could not send.')
    } finally {
      setEmailBusy(false)
    }
  }

  // Amend-before-signing (Keetile's request, CFO approved 2026-08-05) and, from
  // 2026-08-07, the same panel on a POSTED voucher — the window between the second
  // signature and the replenishment paying it out.
  const [showAmend, setShowAmend] = useState(false)
  const [amendAmount, setAmendAmount] = useState('')
  const [amendAccount, setAmendAccount] = useState('')
  const [amendReason, setAmendReason] = useState('')
  const [showUnpost, setShowUnpost] = useState(false)
  const [unpostReason, setUnpostReason] = useState('')
  // A custodian cannot type a UUID. The GL line is picked from the same expense-account list the
  // new-voucher page uses, loaded only when the panel is opened.
  const [expenseAccounts, setExpenseAccounts] = useState<{ id: string; code: string; name: string }[]>([])
  // An empty dropdown and a dropdown that FAILED to load look identical to the
  // custodian. Reported 2026-08-14: the panel said nothing at all. Which reason
  // to show depends on the error — see accountsLoadError.ts.
  const [accountsError, setAccountsError] = useState<string | null>(null)

  const [receipts, setReceipts] = useState<PettyCashReceipt[]>([])
  const [uploadBusy, setUploadBusy] = useState(false)
  const [uploadErr, setUploadErr] = useState<string | null>(null)

  const loadReceipts = useCallback(async () => {
    try { setReceipts(await getPettyCashVoucherReceipts(id)) } catch { /* leave as-is */ }
  }, [id])

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      setV(await getPettyCashVoucher(id))
      await loadReceipts()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load voucher')
    } finally {
      setLoading(false)
    }
  }, [id, loadReceipts])

  async function onReceiptFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    e.target.value = ''   // allow re-selecting the same file
    if (!file) return
    setUploadBusy(true); setUploadErr(null)
    try {
      await uploadPettyCashVoucherReceipt(id, file)
      await loadReceipts()
      setV((cur) => cur ? { ...cur, receipt_count: (cur.receipt_count || 0) + 1 } : cur)
    } catch (err) {
      setUploadErr(err instanceof Error ? err.message : 'Upload failed')
    } finally {
      setUploadBusy(false)
    }
  }

  async function removeReceipt(receiptId: string) {
    if (!window.confirm('Remove this receipt?')) return
    setUploadErr(null)
    try {
      await deletePettyCashVoucherReceipt(id, receiptId)
      await loadReceipts()
    } catch (err) {
      setUploadErr(err instanceof Error ? err.message : 'Could not remove')
    }
  }

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    void load()
  }, [router, load])

  async function act(fn: () => Promise<PettyCashVoucher>, successLabel: string) {
    setActing(true); setError(null)
    try {
      const updated = await fn()
      setV(updated)
      setShowReject(false)
      setRejectReason('')
    } catch (err) {
      setError(err instanceof Error ? err.message : `Failed to ${successLabel}`)
    } finally {
      setActing(false)
    }
  }

  if (loading || !v) {
    return (
      <div className="flex flex-col min-h-screen">
        <TopBar title="Voucher" breadcrumbs={[{ label: 'Petty Cash', href: '/petty-cash' }, { label: '…' }]} />
        <div className="flex-1 p-6 text-sm text-[#6B7280]">{error || 'Loading…'}</div>
      </div>
    )
  }

  const c = STATUS_BADGE[v.status] || STATUS_BADGE.draft

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title={v.voucher_number}
        breadcrumbs={[
          { label: 'Petty Cash', href: '/petty-cash' },
          { label: v.voucher_number },
        ]}
        actions={
          <div className="flex gap-2">
            <Button variant="outline" size="sm" leftIcon={<Mail className="w-3.5 h-3.5" />}
              onClick={() => { setShowEmail((s) => !s); setEmailMsg(null) }}>
              Email link
            </Button>
            <Link href="/petty-cash">
              <Button variant="outline" size="sm" leftIcon={<ArrowLeft className="w-3.5 h-3.5" />}>
                Back
              </Button>
            </Link>
          </div>
        }
      />

      <div className="flex-1 p-6 max-w-4xl space-y-4">
        {v.location_access_notice && (
          <div className="flex items-start gap-2 p-3 rounded-md bg-[#FEF2F2] border-2 border-[#DC2626]">
            <AlertTriangle className="w-5 h-5 text-[#DC2626] flex-shrink-0 mt-0.5" />
            <p className="text-sm text-[#991B1B] font-medium leading-relaxed">
              {v.location_access_notice.text}
            </p>
          </div>
        )}

        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 text-[#DC2626] flex-shrink-0 mt-0.5" />
            <p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}

        {showEmail && (
          <Card>
            <CardContent className="p-4">
              <p className="text-sm font-medium text-[#374151] mb-1">Email this voucher's link</p>
              <p className="text-xs text-[#6B7280] mb-3">Sends whoever you pick a direct link to open this voucher. Alpha Direct addresses only.</p>
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

        {/* Header card */}
        <Card>
          <CardHeader>
            <div className="flex items-baseline justify-between">
              <CardTitle className="font-mono">{v.voucher_number}</CardTitle>
              <span
                className="inline-flex text-xs font-semibold uppercase tracking-wider px-2.5 py-1 rounded border"
                style={{ background: c.bg, color: c.fg, borderColor: `${c.fg}30` }}
              >
                {v.status_display}
              </span>
            </div>
            {v.je_number && (
              <p className="text-xs text-[#047857] mt-1">
                Posted to journal entry <span className="font-mono">{v.je_number}</span>
              </p>
            )}
            {v.reimbursement_number && (
              <p className="text-xs text-[#1D4ED8] mt-1">
                Swept into reimbursement <span className="font-mono">{v.reimbursement_number}</span>
              </p>
            )}
          </CardHeader>
          <CardContent>
            <dl className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-2 text-sm">
              <div>
                <dt className="text-[#6B7280]">Location</dt>
                <dd className="text-[#111827]">{v.location_name}</dd>
              </div>
              <div>
                <dt className="text-[#6B7280]">Voucher date</dt>
                <dd className="text-[#111827]">{fmtDate(v.voucher_date)}</dd>
              </div>
              <div>
                <dt className="text-[#6B7280]">Payee</dt>
                <dd className="text-[#111827]">{v.payee}</dd>
              </div>
              <div>
                <dt className="text-[#6B7280]">Amount</dt>
                <dd className="text-[#111827] font-mono tabular-nums text-lg">
                  BWP {fmtMoney(v.amount)}
                </dd>
              </div>
              <div>
                <dt className="text-[#6B7280]">Expense account</dt>
                <dd className="text-[#111827]">
                  {v.expense_account_code
                    ? <><span className="font-mono">{v.expense_account_code}</span> · {v.expense_account_name}</>
                    : <span className="text-[#B45309]">Not coded yet — the petty-cash team sets this with &ldquo;Amend amount / GL&rdquo; before signing.</span>}
                </dd>
              </div>
              <div>
                <dt className="text-[#6B7280]">Receipt</dt>
                <dd className="text-[#111827] flex items-center gap-2">
                  {v.receipt_attached
                    ? <><CheckCircle2 className="w-4 h-4 text-[#059669]" /> Filed</>
                    : <><Clock className="w-4 h-4 text-[#9CA3AF]" /> Outstanding</>}
                  {v.receipt_reference && (
                    <span className="text-xs text-[#6B7280]">· {v.receipt_reference}</span>
                  )}
                </dd>
              </div>
              <div className="md:col-span-2">
                <dt className="text-[#6B7280]">Description</dt>
                <dd className="text-[#111827] whitespace-pre-wrap">{v.description}</dd>
              </div>
              {v.rejection_reason && (
                <div className="md:col-span-2">
                  <dt className="text-[#B91C1C]">Rejection reason</dt>
                  <dd className="text-[#B91C1C] whitespace-pre-wrap">{v.rejection_reason}</dd>
                </div>
              )}
            </dl>
          </CardContent>
        </Card>

        {/* Receipts — attach the till slip / invoice here (no more Excel) */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Paperclip className="w-4 h-4 text-[#F07F00]" />
              Receipts
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-xs text-[#6B7280] mb-3">
              Attach the till slip or invoice here — a phone photo (JPG/PNG/HEIC) or a PDF, up to 15 MB.
              This replaces the paper voucher pad and the Excel log.
            </p>

            {receipts.length > 0 ? (
              <div className="space-y-2 mb-3">
                {receipts.map((r) => (
                  <div key={r.id} className="flex items-center justify-between gap-3 p-2.5 rounded border border-[#E5E7EB] bg-[#F9FAFB]">
                    <div className="flex items-center gap-2 min-w-0">
                      <FileText className="w-4 h-4 text-[#6B7280] flex-shrink-0" />
                      {/* A link cannot do this: the token is in localStorage, so a plain
                          navigation carries no Authorization header and the endpoint answers
                          401 (verified live). Fetch it as a blob instead. */}
                      <button type="button"
                        onClick={() => downloadPettyCashReceipt(v.id, r.id, r.filename)
                          .catch(e => setError(e instanceof Error ? e.message
                            : 'Could not download the receipt'))}
                        className="text-sm text-[#0B0B3B] hover:underline truncate text-left">
                        {r.filename}
                      </button>
                      <span className="text-[10px] text-[#9CA3AF] flex-shrink-0">
                        {(r.file_size_bytes / 1024).toFixed(0)} KB · {r.uploaded_by_username || '—'}
                      </span>
                    </div>
                    {v.status !== 'posted' && v.status !== 'reimbursed' && (
                      <button onClick={() => removeReceipt(r.id)}
                        className="text-[#B91C1C] hover:bg-[#FEF2F2] rounded p-1 flex-shrink-0" title="Remove">
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-[#9CA3AF] italic mb-3">No receipt attached yet.</p>
            )}

            {uploadErr && <p className="text-xs text-[#B91C1C] mb-2">{uploadErr}</p>}

            {v.status !== 'reimbursed' ? (
              <label className="inline-flex items-center gap-2 text-sm font-medium text-white bg-[#F07F00] hover:bg-[#c96700] rounded-md px-3 py-2 cursor-pointer">
                <Upload className="w-4 h-4" />
                {uploadBusy ? 'Uploading…' : 'Attach a receipt'}
                <input type="file" accept="image/*,application/pdf" className="hidden"
                  disabled={uploadBusy} onChange={onReceiptFile} />
              </label>
            ) : (
              <p className="text-xs text-[#6B7280]">This voucher is reimbursed — receipts are locked.</p>
            )}
          </CardContent>
        </Card>

        {/* Workflow trail */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Receipt className="w-4 h-4 text-[#0B0B3B]" />
              Workflow trail
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-2 text-sm">
              <li className="flex gap-3">
                <span className="text-[#9CA3AF] w-32 flex-shrink-0">Submitted</span>
                <span className="text-[#374151]">
                  {v.submitted_by_username
                    ? `${v.submitted_by_username} · ${fmtDateTime(v.submitted_at)}`
                    : '—'}
                </span>
              </li>
              <li className="flex gap-3">
                <span className="text-[#9CA3AF] w-32 flex-shrink-0">1st signature</span>
                <span className="text-[#374151]">
                  {v.first_approved_by_username
                    ? `${v.first_approved_by_username} · ${fmtDateTime(v.first_approved_at)}`
                    : '—'}
                </span>
              </li>
              <li className="flex gap-3">
                <span className="text-[#9CA3AF] w-32 flex-shrink-0">2nd signature / posted</span>
                <span className="text-[#374151]">
                  {v.approved_by_username
                    ? `${v.approved_by_username} · ${fmtDateTime(v.approved_at)}`
                    : '—'}
                </span>
              </li>
            </ul>
          </CardContent>
        </Card>

        {/* Action bar */}
        {!showReject && (
          <Card>
            <CardContent className="p-4 flex flex-wrap gap-3">
              {v.status === 'draft' && (
                <Button
                  onClick={() => act(() => submitPettyCashVoucher(v.id), 'submit')}
                  disabled={acting}
                  leftIcon={<Send className="w-4 h-4" />}
                >
                  {acting ? 'Submitting…' : 'Submit for Approval'}
                </Button>
              )}
              {(v.status === 'pending_approval' || v.status === 'one_signature') && (
                v.viewer_can_code ? (
                <>
                  <Button
                    onClick={() => act(() => approvePettyCashVoucher(v.id), 'approve')}
                    disabled={acting}
                    leftIcon={<CheckCircle2 className="w-4 h-4" />}
                  >
                    {acting
                      ? 'Signing…'
                      : v.status === 'pending_approval'
                        ? 'Sign (1 of 2)'
                        : 'Sign & Post JE (2 of 2)'}
                  </Button>
                  <Button
                    variant="outline"
                    onClick={() => {
                      setAmendAmount(String(v.amount))
                      setShowAmend(s => !s)
                      if (expenseAccounts.length === 0) {
                        setAccountsError(null)
                        getAllAccounts({ account_type: 'expense' })
                          .then(rows => setExpenseAccounts(
                            (rows as { id: string; code: string; name: string }[]) || []))
                          .catch(err => setAccountsError(accountsLoadErrorMessage(err)))
                      }
                    }}
                    disabled={acting}
                    leftIcon={<Pencil className="w-4 h-4" />}
                  >
                    {showAmend ? 'Cancel amendment' : 'Amend amount / GL'}
                  </Button>
                  <Button
                    variant="outline"
                    onClick={() => setShowReject(true)}
                    disabled={acting}
                    leftIcon={<XCircle className="w-4 h-4" />}
                  >
                    Reject
                  </Button>
                  <p className="text-xs text-[#6B7280] py-1.5 basis-full">
                    {v.status === 'pending_approval'
                      ? 'Petty cash needs two signatures. You add the first; a different person posts it.'
                      : `First signed by ${v.first_approved_by_username ?? '—'}. A different person must add the second signature to post the JE.`}
                  </p>
                </>
                ) : (
                  // The requester who raised this voucher does NOT code or sign it —
                  // that is the petty-cash team's job (CFO directive 2026-08-14). Show
                  // them a plain status line, not a coding step they cannot complete.
                  <p className="text-sm text-[#374151] py-1.5">
                    Submitted. The petty-cash team (Keetile / Legakwa) will put the
                    expense account on it and approve it — nothing more for you to do.
                  </p>
                )
              )}
              {v.status === 'rejected' && (
                <Button
                  variant="outline"
                  onClick={() => act(() => reopenPettyCashVoucher(v.id), 'reopen')}
                  disabled={acting}
                  leftIcon={<RotateCcw className="w-4 h-4" />}
                >
                  {acting ? 'Reopening…' : 'Reopen as Draft'}
                </Button>
              )}
              {/* Posted but not yet paid — the correction window the CFO opened on
                  2026-08-07. It shuts the moment a replenishment pays the voucher out.
                  Corrections are a petty-cash-team job, so only a coder sees them. */}
              {v.status === 'posted' && v.viewer_can_code && (
                <>
                  <Button
                    variant="outline"
                    onClick={() => {
                      setAmendAmount(String(v.amount))
                      setShowAmend(s => !s)
                      setShowUnpost(false)
                      if (expenseAccounts.length === 0) {
                        setAccountsError(null)
                        getAllAccounts({ account_type: 'expense' })
                          .then(rows => setExpenseAccounts(
                            (rows as { id: string; code: string; name: string }[]) || []))
                          .catch(err => setAccountsError(accountsLoadErrorMessage(err)))
                      }
                    }}
                    disabled={acting}
                    leftIcon={<Pencil className="w-4 h-4" />}
                  >
                    {showAmend ? 'Cancel correction' : 'Correct amount / GL'}
                  </Button>
                  <Button
                    variant="outline"
                    onClick={() => { setShowUnpost(s => !s); setShowAmend(false) }}
                    disabled={acting}
                    leftIcon={<RotateCcw className="w-4 h-4" />}
                  >
                    {showUnpost ? 'Cancel' : 'Return to draft'}
                  </Button>
                  <p className="text-xs text-[#6B7280] py-1.5 basis-full">
                    Posted to the GL, but not yet paid out. Correct the amount or the GL line
                    here, or send it back to draft to change anything else — the journal entry
                    is reversed either way. Once a replenishment pays it, it is locked for good.
                  </p>
                </>
              )}
              {showAmend && (v.status === 'pending_approval' || v.status === 'one_signature'
                             || v.status === 'posted') && (
                <div className="basis-full rounded-lg p-4 mt-1"
                     style={{ background: '#FFF7E8', border: '1px solid #F3E4C4' }}>
                  <p className="text-sm font-semibold mb-1" style={{ color: '#0D1B2A' }}>
                    Correct the amount or the GL line
                  </p>
                  <p className="text-xs mb-3" style={{ color: '#6B7280' }}>
                    {v.status === 'posted'
                      ? 'The original journal entry is reversed and a corrected one posted, so the '
                        + 'accounts follow the fix. What was originally asked for is kept on the '
                        + 'voucher, and everyone who signed it is told what changed.'
                      : 'What was originally asked for is kept on the voucher, and they are told '
                        + 'what changed. Any signature already given is cleared, so it needs '
                        + 'signing again — a signature applies to a figure.'}
                  </p>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <div>
                      <label className="block text-[11px] uppercase tracking-wide mb-1"
                             style={{ color: '#6B7280' }}>Amount (BWP)</label>
                      <input value={amendAmount} onChange={e => setAmendAmount(e.target.value)}
                             className="w-full rounded px-2 py-1.5 text-sm border"
                             style={{ borderColor: '#EAEEF3' }} />
                    </div>
                    <div>
                      <label className="block text-[11px] uppercase tracking-wide mb-1"
                             style={{ color: '#6B7280' }}>GL account</label>
                      {/* Typed search, not a scroll — Keetile's request, CFO approved
                          2026-08-07. Same combobox the journal-entry screens use. */}
                      <SearchableSelect
                        options={[
                          { value: '', label: `Keep ${v.expense_account_code || 'the current account'}` },
                          ...expenseAccounts.map(a => ({
                            value: a.id, label: `${a.code} — ${a.name}`,
                          })),
                        ]}
                        value={amendAccount}
                        onChange={setAmendAccount}
                        placeholder="Type a code or name to search…"
                        dense
                      />
                      {accountsError && (
                        <p className="mt-1 text-xs" style={{ color: '#DC2626' }}>{accountsError}</p>
                      )}
                    </div>
                  </div>
                  {/* A reason is required to CHANGE a figure that was already set
                      (an amount edit, or re-coding an account that already had a
                      GL). Coding a voucher that was raised WITHOUT a GL for the
                      first time needs no reason (CFO directive 2026-08-10). */}
                  {(() => {
                    const amountChanged = amendAmount.trim() !== '' && amendAmount.trim() !== String(v.amount)
                    const reasonRequired = amountChanged || !!v.expense_account_code
                    return (
                      <>
                        <label className="block text-[11px] uppercase tracking-wide mt-3 mb-1"
                               style={{ color: '#6B7280' }}>
                          {reasonRequired ? 'Reason — required' : 'Reason (optional — first coding)'}
                        </label>
                        <input value={amendReason} onChange={e => setAmendReason(e.target.value)}
                               placeholder="e.g. till slip says 30.00, not 25.00"
                               className="w-full rounded px-2 py-1.5 text-sm border"
                               style={{ borderColor: (!reasonRequired || amendReason.trim()) ? '#EAEEF3' : '#DC2626' }} />
                        <div className="mt-3">
                          <Button
                            onClick={() => act(() => amendPettyCashVoucher(v.id, {
                              amount: amendAmount.trim() || undefined,
                              expense_account: amendAccount.trim() || undefined,
                              reason: amendReason.trim(),
                            }), 'amend').then(() => { setShowAmend(false); setAmendReason('') })}
                            disabled={acting || (reasonRequired && !amendReason.trim()) || (!reasonRequired && !amendAccount.trim())}
                            leftIcon={<Pencil className="w-4 h-4" />}
                          >
                            {acting ? 'Saving…' : (v.expense_account_code ? 'Save the correction' : 'Save the GL code')}
                          </Button>
                        </div>
                      </>
                    )
                  })()}
                </div>
              )}
              {showUnpost && v.status === 'posted' && (
                <div className="basis-full rounded-lg p-4 mt-1"
                     style={{ background: '#FFF7E8', border: '1px solid #F3E4C4' }}>
                  <p className="text-sm font-semibold mb-1" style={{ color: '#0D1B2A' }}>
                    Send this voucher back to draft
                  </p>
                  <p className="text-xs mb-3" style={{ color: '#6B7280' }}>
                    The journal entry is reversed, so the expense and the tin go back to where
                    they were. Both signatures are cleared — every field becomes editable again,
                    and it has to be signed twice before it posts.
                  </p>
                  <label className="block text-[11px] uppercase tracking-wide mb-1"
                         style={{ color: '#6B7280' }}>Reason — required</label>
                  <input value={unpostReason} onChange={e => setUnpostReason(e.target.value)}
                         placeholder="e.g. rejected by Mr B, cash was never given"
                         className="w-full rounded px-2 py-1.5 text-sm border"
                         style={{ borderColor: unpostReason.trim() ? '#EAEEF3' : '#DC2626' }} />
                  <div className="mt-3">
                    <Button
                      onClick={() => act(
                        () => returnPettyCashVoucherToDraft(v.id, unpostReason.trim()),
                        'return to draft',
                      ).then(() => { setShowUnpost(false); setUnpostReason('') })}
                      disabled={acting || !unpostReason.trim()}
                      leftIcon={<RotateCcw className="w-4 h-4" />}
                    >
                      {acting ? 'Reversing…' : 'Reverse the JE and reopen'}
                    </Button>
                  </div>
                </div>
              )}
              {v.status === 'reimbursed' && (
                <p className="text-xs text-[#6B7280] py-1.5">
                  This voucher has been paid out in a replenishment. It is locked — reverse the
                  replenishment if it has to change.
                </p>
              )}
            </CardContent>
          </Card>
        )}

        {showReject && (
          <Card>
            <CardHeader>
              <CardTitle>Reject voucher</CardTitle>
            </CardHeader>
            <CardContent>
              <label className="block text-xs font-medium text-[#374151] mb-1.5">
                Reason (required)
              </label>
              <textarea
                value={rejectReason}
                onChange={(e) => setRejectReason(e.target.value)}
                rows={3}
                placeholder="Why is this voucher being rejected?"
                className="w-full bg-white border border-[#D1D5DB] rounded-md px-3 py-2 text-sm"
              />
              <div className="mt-3 flex gap-3 justify-end">
                <Button
                  variant="outline"
                  onClick={() => { setShowReject(false); setRejectReason('') }}
                  disabled={acting}
                >
                  Cancel
                </Button>
                <Button
                  onClick={() => act(
                    () => rejectPettyCashVoucher(v.id, rejectReason),
                    'reject',
                  )}
                  disabled={acting || rejectReason.trim().length < 3}
                  leftIcon={<XCircle className="w-4 h-4" />}
                >
                  {acting ? 'Rejecting…' : 'Confirm Rejection'}
                </Button>
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  )
}
