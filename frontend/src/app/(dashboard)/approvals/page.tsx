'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import {
  getAllImportBatches, approveImportBatch, rejectImportBatch, commitImportBatch,
  getPendingDisposals, approveDisposal, rejectDisposal,
  getMe, getToken,
} from '@/lib/api'
import type { ImportBatchSummary, AssetDisposalForApproval, UserProfile } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  AlertCircle, CheckCircle, ThumbsUp, ThumbsDown, ShieldCheck, Boxes, Lock, Send,
} from 'lucide-react'

const STATUS_STYLES: Record<string, string> = {
  draft:              'bg-[#F3F4F6] text-[#374151] border-[#D1D5DB]',
  partially_approved: 'bg-[#FFFBEB] text-[#92400E] border-[#FDE68A]',
  approved:           'bg-[#ECFDF5] text-[#047857] border-[#A7F3D0]',
  committed:          'bg-[#EFF6FF] text-[#1D4ED8] border-[#BFDBFE]',
  rejected:           'bg-[#FEF2F2] text-[#B91C1C] border-[#FECACA]',
  pending_approval:   'bg-[#FFFBEB] text-[#92400E] border-[#FDE68A]',
}

export default function ApprovalsQueuePage() {
  const router = useRouter()
  const [batches, setBatches] = useState<ImportBatchSummary[]>([])
  const [disposals, setDisposals] = useState<AssetDisposalForApproval[]>([])
  const [me, setMe] = useState<UserProfile | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [rejectFor, setRejectFor] = useState<{ kind: 'import' | 'disposal'; id: string; importKind?: ImportBatchSummary['kind']; label: string } | null>(null)
  const [rejectReason, setRejectReason] = useState('')

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const [b, d, m] = await Promise.all([
        getAllImportBatches().catch(() => []),
        getPendingDisposals().catch(() => []),
        getMe().catch(() => null),
      ])
      setBatches(b)
      setDisposals(d)
      setMe(m)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load])

  function flash(msg: string) { setSuccess(msg); setTimeout(() => setSuccess(null), 4000) }

  async function action<T>(fn: () => Promise<T>, msg: string) {
    setBusy(true); setError(null)
    try { await fn(); flash(msg); load() }
    catch (e) { setError(e instanceof Error ? e.message : 'Action failed') }
    finally { setBusy(false) }
  }

  function disabledApproveReason(b: ImportBatchSummary): string | undefined {
    if (!me?.can_approve_journal_entries) return 'Approver title required (CFO / Finance Manager / Financial Controller)'
    if (me.username === b.created_by_username) return 'You uploaded this — segregation of duties prevents you from approving'
    if (me.username === b.first_approver_username) return 'You provided the first approval — a different approver is needed'
    if (b.status !== 'draft' && b.status !== 'partially_approved') return `Batch is ${b.status}`
    return undefined
  }

  function disabledCommitReason(b: ImportBatchSummary): string | undefined {
    if (!me?.can_approve_journal_entries) return 'Approver title required'
    if (b.status !== 'approved') return 'Both approvals must be on file before commit'
    if (b.rows_invalid > 0) return `${b.rows_invalid} rows have validation errors`
    return undefined
  }

  function disabledDisposalApproveReason(d: AssetDisposalForApproval): string | undefined {
    if (!me?.can_approve_journal_entries) return 'Approver title required'
    if (me.username === (d.requested_by_username || '')) return 'You requested this disposal — segregation of duties prevents self-approval'
    return undefined
  }

  // Show only batches that need attention (not committed, not rejected)
  const pendingBatches = batches.filter(b => b.status === 'draft' || b.status === 'partially_approved' || b.status === 'approved')

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Approvals Queue"
        breadcrumbs={[{ label: 'Approvals' }]}
        actions={
          <Button variant="outline" size="sm" onClick={load} disabled={busy}>
            Refresh
          </Button>
        }
      />

      <div className="flex-1 p-6 space-y-4">
        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-[#DC2626]" />
            <p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}
        {success && (
          <div className="bg-[#ECFDF5] border border-[#A7F3D0] rounded-lg p-3 flex items-center gap-2">
            <CheckCircle className="w-4 h-4 text-[#047857]" />
            <p className="text-[#047857] text-sm">{success}</p>
          </div>
        )}

        {/* ── Imports ───────────────────────────────────────────────── */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <ShieldCheck className="w-4 h-4 text-[#F07F00]" />
              Import batches awaiting approval ({pendingBatches.length})
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            {loading ? <p className="px-6 py-8 text-center text-sm text-[#6B7280]">Loading…</p>
            : pendingBatches.length === 0 ? <p className="px-6 py-8 text-center text-sm text-[#6B7280]">All caught up. ✓</p>
            : (
              <ul className="divide-y divide-[#E5E7EB]">
                {pendingBatches.map(b => {
                  const approveDisabled = disabledApproveReason(b)
                  const commitDisabled  = disabledCommitReason(b)
                  return (
                    <li key={`${b.kind}-${b.id}`} className="p-4">
                      <div className="flex items-center gap-3 flex-wrap">
                        <span className="text-xs px-2 py-0.5 rounded-md bg-[#F3F4F6] text-[#374151] uppercase font-mono">{b.kind_label}</span>
                        <p className="font-medium text-[#111827] flex-1 min-w-0 truncate">
                          {b.file_name || `Batch ${b.id.slice(0, 8)}`}
                        </p>
                        <span className={`inline-block px-2 py-0.5 rounded-md text-xs font-medium border ${STATUS_STYLES[b.status] || STATUS_STYLES.draft}`}>
                          {b.status_display}
                        </span>
                      </div>
                      <p className="text-xs text-[#6B7280] mt-1">
                        {b.rows_total} rows ({b.rows_valid} valid, {b.rows_invalid} invalid) ·
                        Uploaded by {b.created_by_username || '—'}
                        {b.first_approver_username && <> · 1st: {b.first_approver_username}</>}
                        {b.second_approver_username && <> · 2nd: {b.second_approver_username}</>}
                      </p>
                      <div className="flex gap-2 mt-3 flex-wrap">
                        <Button size="sm" variant="accent" leftIcon={<ThumbsUp className="w-3.5 h-3.5" />}
                                onClick={() => action(() => approveImportBatch(b.kind, b.id), `Approval recorded for ${b.kind_label}`)}
                                disabled={busy || !!approveDisabled}
                                title={approveDisabled}>
                          Approve
                        </Button>
                        <Button size="sm" variant="outline" leftIcon={<ThumbsDown className="w-3.5 h-3.5" />}
                                onClick={() => { setRejectReason(''); setRejectFor({ kind: 'import', id: b.id, importKind: b.kind, label: `${b.kind_label} import ${b.file_name || b.id.slice(0,8)}` }) }}
                                disabled={busy || !me?.can_approve_journal_entries}>
                          Reject
                        </Button>
                        <Button size="sm" variant="secondary" leftIcon={<Send className="w-3.5 h-3.5" />}
                                onClick={() => action(() => commitImportBatch(b.kind, b.id), `${b.kind_label} import committed`)}
                                disabled={busy || !!commitDisabled}
                                title={commitDisabled}>
                          Commit
                        </Button>
                      </div>
                    </li>
                  )
                })}
              </ul>
            )}
          </CardContent>
        </Card>

        {/* ── Disposals ─────────────────────────────────────────────── */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Boxes className="w-4 h-4 text-[#F07F00]" />
              Asset disposals awaiting approval ({disposals.length})
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            {loading ? <p className="px-6 py-8 text-center text-sm text-[#6B7280]">Loading…</p>
            : disposals.length === 0 ? <p className="px-6 py-8 text-center text-sm text-[#6B7280]">No disposals pending.</p>
            : (
              <ul className="divide-y divide-[#E5E7EB]">
                {disposals.map(d => {
                  const disabled = disabledDisposalApproveReason(d)
                  const gl = parseFloat(d.gain_loss)
                  return (
                    <li key={d.id} className="p-4">
                      <div className="flex items-center gap-3 flex-wrap">
                        <p className="font-medium text-[#111827]">
                          {d.asset_tag} <span className="text-[#6B7280] font-normal">{d.asset_name}</span>
                        </p>
                        <span className="text-xs px-2 py-0.5 rounded-md bg-[#F3F4F6] text-[#374151]">{d.disposal_type_display}</span>
                        <span className={`inline-block px-2 py-0.5 rounded-md text-xs font-medium border ${STATUS_STYLES[d.status] || STATUS_STYLES.pending_approval}`}>
                          {d.status_display}
                        </span>
                      </div>
                      <p className="text-xs text-[#6B7280] mt-1">
                        Disposal date {d.disposal_date} ·
                        Proceeds BWP {d.proceeds} ·
                        NBV BWP {d.nbv_at_disposal} ·
                        <span className={gl >= 0 ? 'text-[#047857]' : 'text-[#B91C1C]'}> {gl >= 0 ? 'Gain' : 'Loss'} BWP {Math.abs(gl).toFixed(2)}</span>
                        {d.requested_by_username && <> · Requested by {d.requested_by_username}</>}
                      </p>
                      {d.notes && <p className="text-xs text-[#6B7280] mt-1 italic">&ldquo;{d.notes}&rdquo;</p>}
                      <div className="flex gap-2 mt-3 flex-wrap">
                        <Button size="sm" variant="accent" leftIcon={<ThumbsUp className="w-3.5 h-3.5" />}
                                onClick={() => action(() => approveDisposal(d.id), 'Disposal approved & posted')}
                                disabled={busy || !!disabled}
                                title={disabled}>
                          Approve &amp; Post
                        </Button>
                        <Button size="sm" variant="outline" leftIcon={<ThumbsDown className="w-3.5 h-3.5" />}
                                onClick={() => { setRejectReason(''); setRejectFor({ kind: 'disposal', id: d.id, label: `disposal of ${d.asset_tag}` }) }}
                                disabled={busy || !me?.can_approve_journal_entries}>
                          Reject
                        </Button>
                      </div>
                    </li>
                  )
                })}
              </ul>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-3 text-xs text-[#6B7280] flex items-start gap-2">
            <Lock className="w-4 h-4 flex-shrink-0 mt-0.5 text-[#F07F00]" />
            <p>
              <strong>Segregation of duties:</strong> the user who uploaded an import or requested a disposal
              cannot also approve it. Imports require <em>two distinct</em> approvers (first approve unlocks the
              second-approve button for someone else). Asset disposals only post to the GL on approval.
            </p>
          </CardContent>
        </Card>
      </div>

      {rejectFor && (
        <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4" onClick={() => !busy && setRejectFor(null)}>
          <div className="bg-white rounded-lg shadow-xl w-full max-w-md p-6 space-y-4" onClick={e => e.stopPropagation()}>
            <h2 className="text-lg font-semibold">Reject {rejectFor.label}</h2>
            <p className="text-sm text-[#6B7280]">A reason is required. The requester / uploader will see it.</p>
            <textarea value={rejectReason} onChange={e => setRejectReason(e.target.value)}
                      className="w-full min-h-[100px] rounded-md p-3 text-sm border border-[#D1D5DB]"
                      placeholder="e.g. Wrong cutover date — please re-upload with H1 dates" />
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setRejectFor(null)} disabled={busy}>Cancel</Button>
              <Button variant="danger" disabled={busy || !rejectReason.trim()}
                      onClick={async () => {
                        if (!rejectFor) return
                        const reason = rejectReason.trim()
                        try {
                          if (rejectFor.kind === 'import' && rejectFor.importKind) {
                            await rejectImportBatch(rejectFor.importKind, rejectFor.id, reason)
                          } else {
                            await rejectDisposal(rejectFor.id, reason)
                          }
                          setRejectFor(null)
                          flash('Rejection recorded')
                          load()
                        } catch (e) {
                          setError(e instanceof Error ? e.message : 'Rejection failed')
                        }
                      }}>
                Reject
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
