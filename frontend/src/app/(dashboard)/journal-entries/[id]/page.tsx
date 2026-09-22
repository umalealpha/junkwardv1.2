'use client'

import { useEffect, useRef, useState } from 'react'
import { useRouter, useParams } from 'next/navigation'
import {
  getJournalEntry, postJournalEntry, getToken,
  submitJournalEntry, approveJournalEntry, rejectJournalEntry, reopenJournalEntry,
  classifyRelatedParty,
  getMe,
  getJournalEntryAttachments, uploadJournalEntryAttachment, deleteJournalEntryAttachment,
} from '@/lib/api'
import type { JournalEntryDetail, UserProfile, JournalEntryAttachment } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/modal'
import { LoadingCard } from '@/components/ui/loading'
import { formatAmount, formatDate, parseAmount, cn } from '@/lib/utils'
import { useNumberFormat } from '@/contexts/NumberFormatContext'
import {
  Send, ArrowLeft, AlertCircle, CheckCircle,
  Clock, ThumbsUp, ThumbsDown, RotateCcw, Lock,
  Paperclip, Upload, Download, Trash2, FileText,
  Eraser,
} from 'lucide-react'
import { useTheme } from '@/contexts/ThemeContext'
import type { Theme } from '@/lib/themes'

// ─── Journal Entry Detail Page ───────────────────────────────────────────────

function JournalTypeBadge({ type, theme }: { type: string; theme: Theme }) {
  const labels: Record<string, string> = {
    sales: 'Sales',
    purchases: 'Purchases',
    cash_receipts: 'Cash Receipts',
    cash_payments: 'Cash Payments',
    bank: 'Bank',
    general: 'General',
  }

  let color: string
  let bg: string
  switch (type) {
    case 'sales':         color = theme.ok;  bg = theme.okB;  break
    case 'purchases':     color = theme.inf; bg = theme.inB;  break
    case 'cash_receipts': color = theme.ok;  bg = theme.okB;  break
    case 'cash_payments': color = theme.er;  bg = theme.erB;  break
    case 'bank':          color = theme.inf; bg = theme.inB;  break
    default:              color = theme.t2;  bg = theme.g100; break
  }

  return (
    <span
      className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium whitespace-nowrap"
      style={{ color, background: bg }}
    >
      {labels[type] || type}
    </span>
  )
}

function StatusBadgeJE({ status, theme, size = 'sm' }: { status: string; theme: Theme; size?: 'sm' | 'md' }) {
  const labels: Record<string, string> = {
    posted: 'Posted',
    draft: 'Draft',
    pending_approval: 'Pending Approval',
    rejected: 'Rejected',
    reversed: 'Reversed',
  }

  let color: string
  let bg: string
  switch (status) {
    case 'posted':           color = theme.inf; bg = theme.inB;  break
    case 'draft':            color = theme.wr;  bg = theme.wrB;  break
    case 'pending_approval': color = theme.orange; bg = theme.oL; break
    case 'rejected':         color = theme.er;  bg = theme.erB;  break
    case 'reversed':         color = theme.t2;  bg = theme.g100; break
    default:                 color = theme.t2;  bg = theme.g100; break
  }

  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full font-medium whitespace-nowrap',
        size === 'md' ? 'px-2.5 py-1 text-xs' : 'px-2 py-0.5 text-xs',
      )}
      style={{ color, background: bg }}
    >
      {labels[status] || status}
    </span>
  )
}

export default function JournalEntryDetailPage() {
  const router = useRouter()
  const params = useParams()
  const id = params.id as string
  const { theme } = useTheme()
  const { mode } = useNumberFormat()
  const fmt = (amount: string | number, currency = 'BWP') => formatAmount(amount, currency, mode)

  const [entry, setEntry] = useState<JournalEntryDetail | null>(null)
  const [me, setMe] = useState<UserProfile | null>(null)
  const [attachments, setAttachments] = useState<JournalEntryAttachment[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [showSubmitConfirm, setShowSubmitConfirm] = useState(false)
  const [showApproveConfirm, setShowApproveConfirm] = useState(false)
  const [showRejectModal, setShowRejectModal] = useState(false)
  const [showReopenConfirm, setShowReopenConfirm] = useState(false)
  const [showPostConfirm, setShowPostConfirm] = useState(false)
  const [rejectReason, setRejectReason] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [successMsg, setSuccessMsg] = useState<string | null>(null)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const [data, profile, atts] = await Promise.all([
        getJournalEntry(id),
        getMe().catch(() => null),
        getJournalEntryAttachments(id).catch(() => [] as JournalEntryAttachment[]),
      ])
      setEntry(data)
      setMe(profile)
      setAttachments(atts)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load journal entry')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    const token = getToken()
    if (!token) { router.replace('/login'); return }
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  async function run<T>(fn: () => Promise<T>, successMessage: string) {
    setBusy(true)
    setError(null)
    try {
      const updated = (await fn()) as unknown as JournalEntryDetail
      setEntry(updated)
      setSuccessMsg(successMessage)
      setTimeout(() => setSuccessMsg(null), 4000)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Action failed')
    } finally {
      setBusy(false)
      setShowSubmitConfirm(false)
      setShowApproveConfirm(false)
      setShowRejectModal(false)
      setShowReopenConfirm(false)
      setShowPostConfirm(false)
    }
  }

  // BUG-001: approve, but if the backend flags this as a likely test/QA entry
  // (409 requires_confirmation), warn the approver and only post on confirm.
  async function handleApprove() {
    setBusy(true); setError(null)
    try {
      let res = await approveJournalEntry(id)
      if ((res as any)?.requires_confirmation) {
        setShowApproveConfirm(false)
        if (!window.confirm((res as any).warning)) { setBusy(false); return }
        res = await approveJournalEntry(id, true)
      }
      setEntry(res as JournalEntryDetail)
      setSuccessMsg('Entry approved and posted')
      setTimeout(() => setSuccessMsg(null), 4000)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Action failed')
    } finally {
      setBusy(false); setShowApproveConfirm(false)
    }
  }

  if (loading) {
    return (
      <div className="flex flex-col min-h-screen">
        <TopBar title="Journal Entry" breadcrumbs={[{ label: 'Journal Entries', href: '/journal-entries' }]} />
        <div className="p-6"><LoadingCard message="Loading journal entry..." className="h-48" /></div>
      </div>
    )
  }

  if (error && !entry) {
    return (
      <div className="flex flex-col min-h-screen">
        <TopBar title="Journal Entry" breadcrumbs={[{ label: 'Journal Entries', href: '/journal-entries' }]} />
        <div className="p-6">
          <div className="rounded-xl p-4 flex items-center gap-3" style={{ background: theme.erB, border: `1px solid ${theme.er}20` }}>
            <AlertCircle className="w-5 h-5" style={{ color: theme.er }} />
            <p style={{ color: theme.er }}>{error}</p>
          </div>
        </div>
      </div>
    )
  }

  if (!entry) return null

  const totalDebits = entry.lines.reduce((sum, line) => sum + parseAmount(line.debit_amount), 0)
  const totalCredits = entry.lines.reduce((sum, line) => sum + parseAmount(line.credit_amount), 0)

  // Workflow flags
  const isCreator = !!(me && entry.created_by_username === me.username)
  const canApprove = !!me?.can_approve_journal_entries
  const canPostDirectly = !!me?.can_post_directly
  const status = entry.status

  const showSubmit = status === 'draft'
  const showApproveReject = status === 'pending_approval' && canApprove && !isCreator
  const showWaitingMsg = status === 'pending_approval' && (isCreator || !canApprove)
  const showReopen = status === 'rejected' && isCreator
  const showDirectPost = status === 'draft' && canPostDirectly

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title={entry.entry_number}
        breadcrumbs={[
          { label: 'Journal Entries', href: '/journal-entries' },
          { label: entry.entry_number },
        ]}
        actions={
          <div className="flex items-center gap-2 flex-wrap">
            <Button
              variant="ghost"
              size="sm"
              leftIcon={<ArrowLeft className="w-3.5 h-3.5" />}
              onClick={() => router.push('/journal-entries')}
            >
              Back
            </Button>

            {showSubmit && (
              <Button
                variant="accent"
                size="sm"
                leftIcon={<Send className="w-3.5 h-3.5" />}
                onClick={() => setShowSubmitConfirm(true)}
                disabled={busy || entry.is_related_party === null || entry.is_related_party === undefined}
                title={
                  entry.is_related_party === null || entry.is_related_party === undefined
                    ? 'Classify the related-party status first (IAS 24)'
                    : undefined
                }
              >
                Submit for Approval
              </Button>
            )}

            {showDirectPost && (
              <Button
                variant="outline"
                size="sm"
                leftIcon={<Lock className="w-3.5 h-3.5" />}
                onClick={() => setShowPostConfirm(true)}
                disabled={busy}
                title="Bypass approval — system / API users only"
              >
                Direct Post (System)
              </Button>
            )}

            {showApproveReject && (
              <>
                <Button
                  variant="outline"
                  size="sm"
                  leftIcon={<ThumbsDown className="w-3.5 h-3.5" />}
                  onClick={() => { setRejectReason(''); setShowRejectModal(true) }}
                  disabled={busy}
                >
                  Reject
                </Button>
                <Button
                  variant="accent"
                  size="sm"
                  leftIcon={<ThumbsUp className="w-3.5 h-3.5" />}
                  onClick={() => setShowApproveConfirm(true)}
                  disabled={busy}
                >
                  Approve &amp; Post
                </Button>
              </>
            )}

            {showReopen && (
              <Button
                variant="accent"
                size="sm"
                leftIcon={<RotateCcw className="w-3.5 h-3.5" />}
                onClick={() => setShowReopenConfirm(true)}
                disabled={busy}
              >
                Reopen &amp; Edit
              </Button>
            )}

            {/* CFO directive 2026-05-26: makers can submit a POSTED JE
                for maker-checker reversal from anywhere it shows up —
                JE list, GL drill-down, TB drill-down — they all land
                here on the JE detail. Deep-link pre-fills the
                voucher-clearing form. Kago approves. Not shown once the
                entry is already reversed or has a pending request. */}
            {entry.status === 'posted' && !entry.reversed_by_number && (
              <Button
                variant="outline"
                size="sm"
                leftIcon={<Eraser className="w-3.5 h-3.5" />}
                onClick={() =>
                  router.push(
                    `/banking/voucher-clearing?je=${entry.id}` +
                    `&number=${encodeURIComponent(entry.entry_number)}`,
                  )
                }
                disabled={busy}
                title="Submit this JE to the maker-checker clearing queue"
              >
                Submit for clearing
              </Button>
            )}
          </div>
        }
      />

      <div className="flex-1 p-6 space-y-6">
        {successMsg && (
          <div className="rounded-xl p-3 flex items-center gap-2" style={{ background: theme.okB, border: `1px solid ${theme.ok}30` }}>
            <CheckCircle className="w-4 h-4" style={{ color: theme.ok }} />
            <p className="text-sm" style={{ color: theme.ok }}>{successMsg}</p>
          </div>
        )}
        {error && (
          <div className="rounded-xl p-3 flex items-center gap-2" style={{ background: theme.erB, border: `1px solid ${theme.er}20` }}>
            <AlertCircle className="w-4 h-4" style={{ color: theme.er }} />
            <p className="text-sm" style={{ color: theme.er }}>{error}</p>
          </div>
        )}

        {/* Related-party classification — required before submit */}
        {(entry.status === 'draft' || entry.status === 'rejected') && (
          <RelatedPartyClassifier
            entry={entry}
            onClassify={async (val) => {
              try {
                const updated = await classifyRelatedParty(id, val)
                setEntry(updated)
                setSuccessMsg(`Classified as ${val ? 'related-party' : 'arm’s-length'} transaction`)
                setTimeout(() => setSuccessMsg(null), 4000)
              } catch (e) {
                setError(e instanceof Error ? e.message : 'Classification failed')
              }
            }}
            theme={theme}
          />
        )}
        {(entry.status !== 'draft' && entry.status !== 'rejected') && entry.is_related_party !== null && entry.is_related_party !== undefined && (
          <div className="rounded-xl p-3 flex items-center gap-2 text-sm"
               style={{ background: entry.is_related_party ? theme.wrB : theme.okB,
                        border: `1px solid ${entry.is_related_party ? theme.wr : theme.ok}30` }}>
            <span style={{ color: entry.is_related_party ? theme.wr : theme.ok }}>
              {entry.is_related_party
                ? '⚠ This is a RELATED PARTY transaction (IAS 24).'
                : '✓ This is an arm’s-length transaction (not related party).'}
            </span>
          </div>
        )}

        {/* Workflow status banner */}
        {showWaitingMsg && (
          <div className="rounded-xl p-3 flex items-start gap-2" style={{ background: theme.wrB, border: `1px solid ${theme.wr}30` }}>
            <Clock className="w-4 h-4 flex-shrink-0 mt-0.5" style={{ color: theme.wr }} />
            <div className="text-sm" style={{ color: theme.wr }}>
              {isCreator
                ? <>This entry is awaiting approval. As the creator, you cannot approve your own entry — segregation of duties. Another approver (CFO, Finance Manager, or Financial Controller) must approve it.</>
                : <>This entry is awaiting approval. Your title does not have approval authority.</>
              }
              {entry.submitted_by_username && (
                <span className="block text-xs mt-1 opacity-75">
                  Submitted by {entry.submitted_by_username} on {entry.submitted_at ? formatDate(entry.submitted_at) : '—'}
                </span>
              )}
            </div>
          </div>
        )}

        {status === 'rejected' && entry.rejection_reason && (
          <div className="rounded-xl p-3 flex items-start gap-2" style={{ background: theme.erB, border: `1px solid ${theme.er}30` }}>
            <ThumbsDown className="w-4 h-4 flex-shrink-0 mt-0.5" style={{ color: theme.er }} />
            <div className="text-sm" style={{ color: theme.er }}>
              <span className="font-medium">Rejected</span> by {entry.approved_by_username || '—'}: {entry.rejection_reason}
            </div>
          </div>
        )}

        {/* Header Card */}
        <Card>
          <CardContent className="py-5">
            <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-6">
              <div>
                <p className="text-xs uppercase tracking-wider" style={{ color: theme.t2 }}>Entry #</p>
                <p className="text-base font-bold mt-1" style={{ color: theme.orange }}>{entry.entry_number}</p>
              </div>
              <div>
                <p className="text-xs uppercase tracking-wider" style={{ color: theme.t2 }}>Type</p>
                <div className="mt-1.5"><JournalTypeBadge type={entry.journal_type} theme={theme} /></div>
              </div>
              <div>
                <p className="text-xs uppercase tracking-wider" style={{ color: theme.t2 }}>Date</p>
                <p className="text-sm mt-1" style={{ color: theme.text }}>{formatDate(entry.entry_date)}</p>
              </div>
              <div>
                <p className="text-xs uppercase tracking-wider" style={{ color: theme.t2 }}>Status</p>
                <div className="mt-1.5"><StatusBadgeJE status={entry.status} theme={theme} size="md" /></div>
              </div>
              <div>
                <p className="text-xs uppercase tracking-wider" style={{ color: theme.t2 }}>Currency</p>
                <p className="text-sm font-medium mt-1" style={{ color: theme.text }}>{entry.currency}</p>
              </div>
              <div>
                <p className="text-xs uppercase tracking-wider" style={{ color: theme.t2 }}>Posted Date</p>
                <p className="text-sm mt-1" style={{ color: theme.t2 }}>
                  {entry.posted_date ? formatDate(entry.posted_date) : '—'}
                </p>
              </div>
            </div>

            {entry.description && (
              <div className="mt-4 pt-4" style={{ borderTop: `1px solid ${theme.g200}` }}>
                <p className="text-xs uppercase tracking-wider mb-1" style={{ color: theme.t2 }}>Description</p>
                <p className="text-sm" style={{ color: theme.t2 }}>{entry.description}</p>
              </div>
            )}

            {/* Audit trail */}
            <div className="mt-3 pt-3 grid grid-cols-1 md:grid-cols-3 gap-x-6 gap-y-1 text-xs" style={{ borderTop: `1px solid ${theme.g200}` }}>
              <div style={{ color: theme.t3 }}>
                Created by: <span style={{ color: theme.text }}>{entry.created_by_username || '—'}</span>
              </div>
              {entry.submitted_by_username && (
                <div style={{ color: theme.t3 }}>
                  Submitted by: <span style={{ color: theme.text }}>{entry.submitted_by_username}</span>
                  {entry.submitted_at && <span> on {formatDate(entry.submitted_at)}</span>}
                </div>
              )}
              {entry.approved_by_username && entry.status !== 'rejected' && (
                <div style={{ color: theme.t3 }}>
                  Approved by: <span style={{ color: theme.text }}>{entry.approved_by_username}</span>
                  {entry.approved_at && <span> on {formatDate(entry.approved_at)}</span>}
                </div>
              )}
            </div>

            {/* Reversal info */}
            {(entry.reversal_of_number || entry.reversed_by_number) && (
              <div className="mt-3 pt-3" style={{ borderTop: `1px solid ${theme.g200}` }}>
                {entry.reversal_of_number && (
                  <p className="text-xs" style={{ color: theme.t3 }}>
                    Reversal of: <span style={{ color: theme.orange }}>{entry.reversal_of_number}</span>
                  </p>
                )}
                {entry.reversed_by_number && (
                  <p className="text-xs" style={{ color: theme.t3 }}>
                    Reversed by: <span style={{ color: theme.orange }}>{entry.reversed_by_number}</span>
                  </p>
                )}
              </div>
            )}

            {entry.source_type && (
              <div className="mt-3">
                <p className="text-xs" style={{ color: theme.t3 }}>
                  Source: <span style={{ color: theme.text }}>{entry.source_type}</span>
                </p>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Supporting documents — attachments */}
        <AttachmentsSection
          jeId={id}
          attachments={attachments}
          onChange={setAttachments}
          theme={theme}
        />

        {/* Journal Lines */}
        <Card>
          <CardHeader><CardTitle>Journal Lines</CardTitle></CardHeader>
          <CardContent className="p-0">
            <div className="overflow-x-auto">
              <table className="w-full text-sm border-collapse">
                <thead style={{ background: theme.g50, borderBottom: `2px solid ${theme.g200}` }}>
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider" style={{ color: theme.t2 }}>Account Code</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider" style={{ color: theme.t2 }}>Account Name</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider" style={{ color: theme.t2 }}>Description</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wider w-32" style={{ color: theme.t2 }}>Debit</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wider w-32" style={{ color: theme.t2 }}>Credit</th>
                  </tr>
                </thead>
                <tbody style={{ background: theme.card }}>
                  {entry.lines.map((line) => (
                    <tr
                      key={line.id}
                      className="table-row-alt"
                      style={{ borderBottom: `1px solid ${theme.g200}` }}
                    >
                      <td className="px-4 py-3">
                        <span className="font-mono text-xs" style={{ color: theme.t2 }}>{line.account}</span>
                      </td>
                      <td className="px-4 py-3 font-medium" style={{ color: theme.text }}>
                        {line.account_name}
                      </td>
                      <td className="px-4 py-3" style={{ color: theme.t2 }}>
                        {line.description || '—'}
                      </td>
                      <td className="px-4 py-3 text-right font-mono-nums" style={{ color: parseAmount(line.debit_amount) > 0 ? theme.text : theme.t3 }}>
                        {parseAmount(line.debit_amount) > 0 ? fmt(line.debit_amount, entry.currency) : '—'}
                      </td>
                      <td className="px-4 py-3 text-right font-mono-nums" style={{ color: parseAmount(line.credit_amount) > 0 ? theme.text : theme.t3 }}>
                        {parseAmount(line.credit_amount) > 0 ? fmt(line.credit_amount, entry.currency) : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
                <tfoot style={{ background: theme.g50, borderTop: `2px solid ${theme.g200}` }}>
                  <tr>
                    <td colSpan={3} className="px-4 py-3 text-right text-sm font-semibold" style={{ color: theme.navy }}>
                      Totals
                    </td>
                    <td className="px-4 py-3 text-right font-mono-nums font-semibold" style={{ color: theme.navy }}>
                      {fmt(totalDebits, entry.currency)}
                    </td>
                    <td className="px-4 py-3 text-right font-mono-nums font-semibold" style={{ color: theme.navy }}>
                      {fmt(totalCredits, entry.currency)}
                    </td>
                  </tr>
                  {Math.abs(totalDebits - totalCredits) > 0.005 && (
                    <tr>
                      <td colSpan={3} className="px-4 py-2 text-right text-xs font-medium" style={{ color: theme.er }}>
                        Difference (out of balance)
                      </td>
                      <td colSpan={2} className="px-4 py-2 text-right font-mono-nums text-xs font-medium" style={{ color: theme.er }}>
                        {fmt(Math.abs(totalDebits - totalCredits), entry.currency)}
                      </td>
                    </tr>
                  )}
                </tfoot>
              </table>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Confirmation modals */}
      <ConfirmDialog
        open={showSubmitConfirm}
        onOpenChange={setShowSubmitConfirm}
        title="Submit for Approval"
        description={`Submit ${entry.entry_number} for approval. Once submitted you cannot edit it until an approver reviews.`}
        confirmLabel="Submit"
        variant="warning"
        loading={busy}
        onConfirm={() => run(() => submitJournalEntry(id), 'Entry submitted for approval')}
      />
      <ConfirmDialog
        open={showApproveConfirm}
        onOpenChange={setShowApproveConfirm}
        title="Approve and Post Journal Entry"
        description={`Approve ${entry.entry_number} and post it to the GL. This action cannot be undone — corrections require a reversal entry.`}
        confirmLabel="Approve & Post"
        variant="warning"
        loading={busy}
        onConfirm={handleApprove}
      />
      <ConfirmDialog
        open={showReopenConfirm}
        onOpenChange={setShowReopenConfirm}
        title="Reopen Rejected Entry"
        description={`Reopen ${entry.entry_number} so you can edit and resubmit it.`}
        confirmLabel="Reopen"
        variant="primary"
        loading={busy}
        onConfirm={() => run(() => reopenJournalEntry(id), 'Entry reopened to draft')}
      />
      <ConfirmDialog
        open={showPostConfirm}
        onOpenChange={setShowPostConfirm}
        title="Post Journal Entry (System Bypass)"
        description={`Direct post bypasses approval. This is allowed only for system / API users. Continue?`}
        confirmLabel="Post Entry"
        variant="warning"
        loading={busy}
        onConfirm={() => run(() => postJournalEntry(id), 'Entry posted')}
      />

      {/* Reject modal — needs reason input */}
{showRejectModal && (
        <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4" onClick={() => !busy && setShowRejectModal(false)}>
          <div className="bg-white rounded-lg shadow-xl w-full max-w-md p-6 space-y-4" onClick={e => e.stopPropagation()} style={{ background: theme.card }}>
            <h2 className="text-lg font-semibold" style={{ color: theme.text }}>Reject Journal Entry</h2>
            <p className="text-sm" style={{ color: theme.t2 }}>
              Reason (required) — the creator will see this when they look at {entry.entry_number}.
            </p>
            <textarea
              value={rejectReason}
              onChange={e => setRejectReason(e.target.value)}
              className="w-full min-h-[100px] rounded-md p-3 text-sm border focus:outline-none focus:ring-2"
              style={{ background: theme.input, color: theme.text, borderColor: theme.g200 }}
              placeholder="e.g. Wrong account on line 2 — should be 6100, not 6101"
            />
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setShowRejectModal(false)} disabled={busy}>Cancel</Button>
              <Button
                variant="danger"
                disabled={busy || !rejectReason.trim()}
                onClick={() => run(() => rejectJournalEntry(id, rejectReason.trim()), 'Entry rejected')}
              >
                {busy ? 'Rejecting…' : 'Reject Entry'}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ─── Related-party classifier ────────────────────────────────────────────────

function RelatedPartyClassifier({
  entry, onClassify, theme,
}: {
  entry: JournalEntryDetail
  onClassify: (value: boolean) => Promise<void>
  theme: Theme
}) {
  const current = entry.is_related_party
  const isSet = current === true || current === false
  return (
    <div className="rounded-xl p-4"
         style={{
           background: isSet ? (current ? theme.wrB : theme.okB) : theme.erB,
           border: `1px solid ${isSet ? (current ? theme.wr : theme.ok) : theme.er}30`,
         }}>
      <p className="text-sm font-semibold mb-2" style={{ color: theme.text }}>
        IAS 24 Related-Party Classification {isSet ? '' : '— required before you can submit this entry'}
      </p>
      <p className="text-xs mb-3" style={{ color: theme.t2 }}>
        Does any line on this entry transact with a related party (director, KMP, KMP family,
        subsidiary, or entity under common control)? You must answer before submitting.
      </p>
      <div className="flex gap-2">
        <button
          onClick={() => onClassify(false)}
          className="flex-1 px-3 py-2 rounded-md text-sm font-medium border transition-colors"
          style={{
            background: current === false ? theme.ok : 'transparent',
            color: current === false ? '#fff' : theme.text,
            borderColor: current === false ? theme.ok : theme.g200,
          }}
        >
          ✓ No — arm&apos;s-length
        </button>
        <button
          onClick={() => onClassify(true)}
          className="flex-1 px-3 py-2 rounded-md text-sm font-medium border transition-colors"
          style={{
            background: current === true ? theme.wr : 'transparent',
            color: current === true ? '#fff' : theme.text,
            borderColor: current === true ? theme.wr : theme.g200,
          }}
        >
          ⚠ Yes — related party
        </button>
      </div>
    </div>
  )
}


// ─── Attachments section ──────────────────────────────────────────────────────

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(2)} MB`
}

function AttachmentsSection({
  jeId, attachments, onChange, theme,
}: {
  jeId: string
  attachments: JournalEntryAttachment[]
  onChange: (atts: JournalEntryAttachment[]) => void
  theme: Theme
}) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [description, setDescription] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)

  async function uploadFiles(files: FileList | File[]) {
    setBusy(true)
    setErr(null)
    try {
      const next: JournalEntryAttachment[] = [...attachments]
      for (const f of Array.from(files)) {
        const att = await uploadJournalEntryAttachment(jeId, f, description)
        next.unshift(att)
      }
      onChange(next)
      setDescription('')
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Upload failed')
    } finally {
      setBusy(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  async function removeOne(att: JournalEntryAttachment) {
    if (!confirm(`Delete "${att.filename}"? This is reversible only by re-uploading.`)) return
    setBusy(true)
    setErr(null)
    try {
      await deleteJournalEntryAttachment(jeId, att.id)
      onChange(attachments.filter(a => a.id !== att.id))
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Delete failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Paperclip className="w-4 h-4" />
          Supporting Documents ({attachments.length})
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div
          onClick={() => fileRef.current?.click()}
          onDragOver={e => e.preventDefault()}
          onDrop={e => {
            e.preventDefault()
            if (e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files)
          }}
          className="border-2 border-dashed rounded-lg p-5 text-center cursor-pointer transition-colors"
          style={{ borderColor: theme.g200 }}
        >
          <Upload className="w-7 h-7 mx-auto mb-1.5" style={{ color: theme.t3 }} strokeWidth={1.5} />
          <p className="text-sm" style={{ color: theme.t2 }}>
            {busy ? 'Uploading…' : 'Drop files here or click to upload'}
          </p>
          <p className="text-xs mt-1" style={{ color: theme.t3 }}>
            Invoice PDFs, contracts, board resolutions — anything that backs up this entry.
          </p>
          <input
            ref={fileRef}
            type="file"
            multiple
            className="hidden"
            onChange={e => e.target.files && uploadFiles(e.target.files)}
            disabled={busy}
          />
        </div>

        <div className="flex gap-2">
          <input
            type="text"
            value={description}
            onChange={e => setDescription(e.target.value)}
            placeholder="Optional description for the next upload (e.g. 'FNB statement May 2026')"
            className="flex-1 h-9 rounded-md px-3 text-sm border focus:outline-none"
            style={{ background: theme.input, color: theme.text, borderColor: theme.g200 }}
            disabled={busy}
          />
        </div>

        {err && (
          <div className="rounded-md p-2 text-sm flex items-center gap-2" style={{ background: theme.erB, color: theme.er }}>
            <AlertCircle className="w-4 h-4" /> {err}
          </div>
        )}

        {attachments.length > 0 && (
          <ul className="divide-y" style={{ borderColor: theme.g200 }}>
            {attachments.map(att => (
              <li key={att.id} className="py-2 flex items-center gap-3">
                <FileText className="w-4 h-4 flex-shrink-0" style={{ color: theme.t2 }} />
                <div className="flex-1 min-w-0">
                  <a
                    href={att.download_url || '#'}
                    target="_blank"
                    rel="noreferrer"
                    className="text-sm font-medium hover:underline truncate block"
                    style={{ color: theme.text }}
                  >
                    {att.filename}
                  </a>
                  <p className="text-xs" style={{ color: theme.t3 }}>
                    {fmtBytes(att.file_size_bytes)} · uploaded by {att.uploaded_by_username} · {formatDate(att.created_at)}
                    {att.description && <> · {att.description}</>}
                  </p>
                </div>
                {att.download_url && (
                  <a
                    href={att.download_url}
                    target="_blank"
                    rel="noreferrer"
                    className="p-1.5 rounded hover:bg-black/5"
                    title="Download"
                  >
                    <Download className="w-4 h-4" style={{ color: theme.t2 }} />
                  </a>
                )}
                <button
                  onClick={() => removeOne(att)}
                  className="p-1.5 rounded hover:bg-black/5"
                  title="Delete"
                  disabled={busy}
                >
                  <Trash2 className="w-4 h-4" style={{ color: theme.er }} />
                </button>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}
