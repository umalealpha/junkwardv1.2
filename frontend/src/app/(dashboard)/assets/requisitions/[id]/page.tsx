'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams, useRouter } from 'next/navigation'
import Link from 'next/link'
import {
  getAssetRequisition, fmApproveRequisition, cfoApproveRequisition,
  rejectRequisition, cancelRequisition, createHandover, getAssets, getToken,
} from '@/lib/api'
import type { AssetRequisition, AssetListItem } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Modal, ModalBody, ModalFooter } from '@/components/ui/modal'
import { StatusBadge, Badge } from '@/components/ui/badge'
import { Label, Input, Textarea } from '@/components/ui/input'
import { SearchableSelect } from '@/components/ui/SearchableSelect'
import { LoadingCard } from '@/components/ui/loading'
import { pushToast } from '@/components/Toaster'
import { useTheme } from '@/contexts/ThemeContext'
import {
  ArrowLeft, AlertCircle, CheckCircle2, Clock, XCircle,
  User, Boxes, Tag, FileText, Coins, PenLine, ArrowRight,
} from 'lucide-react'

// Money — always "P x,xxx.00".
function fmtP(v: string | number): string {
  const n = typeof v === 'string' ? parseFloat(v) : v
  if (Number.isNaN(n)) return 'P —'
  return `P ${new Intl.NumberFormat('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n)}`
}

function fmtDate(v: string | null): string {
  if (!v) return '—'
  const d = new Date(v)
  return Number.isNaN(d.getTime()) ? v : d.toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' })
}

// AssetListItem does not carry custody_status in every deployment — read it defensively.
type StockAsset = AssetListItem & { custody_status?: string }

export default function RequisitionDetailPage() {
  const router = useRouter()
  const { id } = useParams<{ id: string }>()
  const { theme } = useTheme()

  const [req, setReq] = useState<AssetRequisition | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Which action modal is open.
  const [modal, setModal] = useState<null | 'fm' | 'cfo' | 'reject' | 'cancel'>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setReq(await getAssetRequisition(id))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [id])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load])

  const afterAction = useCallback((message: string) => {
    setModal(null)
    pushToast({ type: 'success', message })
    load()
  }, [load])

  if (loading) {
    return (
      <div className="flex flex-col min-h-screen">
        <TopBar title="Loading…" breadcrumbs={[{ label: 'Fixed Assets', href: '/assets' }, { label: 'Requisitions' }]} />
        <div className="flex-1 p-6"><LoadingCard message="Loading requisition…" /></div>
      </div>
    )
  }

  if (error || !req) {
    return (
      <div className="flex flex-col min-h-screen">
        <TopBar title="Error" breadcrumbs={[{ label: 'Fixed Assets', href: '/assets' }, { label: 'Requisitions' }]} />
        <div className="flex-1 p-6">
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-[#DC2626]" />
            <p className="text-[#DC2626] text-sm">{error || 'Requisition not found'}</p>
          </div>
        </div>
      </div>
    )
  }

  const canApproveFm = req.status === 'pending_fm_approval'
  const canApproveCfo = req.status === 'pending_cfo_approval'
  const canCancel = req.status === 'draft' || req.status.startsWith('pending_')
  const canReject = canApproveFm || canApproveCfo
  const showHandover = req.status === 'approved' && !req.handover_id

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title={`Requisition ${req.requisition_number}`}
        breadcrumbs={[
          { label: 'Fixed Assets', href: '/assets' },
          { label: 'Requisitions' },
          { label: req.requisition_number },
        ]}
        actions={
          <Button variant="outline" size="sm" leftIcon={<ArrowLeft className="w-3.5 h-3.5" />} onClick={() => router.push('/assets')}>
            Back
          </Button>
        }
      />

      <div className="flex-1 p-6 space-y-4">
        {/* ── 1. Header / summary ─────────────────────────────────────────── */}
        <Card>
          <CardContent className="p-6 space-y-5">
            <div className="flex items-start justify-between gap-4 flex-wrap">
              <div>
                <h1 className="text-2xl font-semibold tracking-tight" style={{ fontFamily: 'var(--font-serif, Georgia), serif', color: theme.text }}>
                  {req.requisition_number}
                </h1>
                <div className="mt-2 flex items-center gap-2 flex-wrap">
                  <StatusBadge status={req.status_display} />
                  <Badge variant={req.requires_full_gate ? 'orange' : 'navy'}>
                    {req.requires_full_gate ? 'Full CFO + Finance Manager gate' : 'Single sign-off'}
                  </Badge>
                </div>
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-x-8 gap-y-4 text-sm">
              <Field icon={<Tag className="w-4 h-4" />} label="Type" value={req.req_type_display} />
              <Field icon={<User className="w-4 h-4" />} label="Recipient" value={
                <span>
                  {req.recipient_name || '—'}
                  {req.recipient_email && <span className="block text-xs" style={{ color: theme.t3 }}>{req.recipient_email}</span>}
                </span>
              } />
              <Field icon={<Boxes className="w-4 h-4" />} label="Category" value={req.category_name || '—'} />
              <Field icon={<FileText className="w-4 h-4" />} label="Description" value={req.description || '—'} className="md:col-span-2" />
              <Field icon={<Coins className="w-4 h-4" />} label="Estimated value" value={fmtP(req.estimated_value)} />
              <Field icon={<FileText className="w-4 h-4" />} label="Reason" value={req.reason || '—'} className="md:col-span-2" />
              <Field icon={<User className="w-4 h-4" />} label="Raised by" value={req.requested_by_name || '—'} />
            </div>
          </CardContent>
        </Card>

        {/* ── 2. Approval timeline ────────────────────────────────────────── */}
        <Card>
          <CardHeader><CardTitle>Approval timeline</CardTitle></CardHeader>
          <CardContent className="p-6 pt-2 space-y-3">
            <TimelineStep
              theme={theme}
              title="Finance Manager"
              done={!!req.fm_approved_at}
              doneText={req.fm_approved_at ? `${req.fm_approved_by_name || 'Approved'} · ${fmtDate(req.fm_approved_at)}` : undefined}
              pendingText="Pending"
            />
            <TimelineStep
              theme={theme}
              title="CFO"
              done={!!req.cfo_approved_at}
              notRequired={!req.requires_full_gate}
              doneText={req.cfo_approved_at ? `${req.cfo_approved_by_name || 'Approved'} · ${fmtDate(req.cfo_approved_at)}` : undefined}
              pendingText="Pending"
              notRequiredText="Not required (single sign-off)"
            />

            {req.status === 'rejected' && (
              <div className="mt-2 rounded-lg border border-[#FECACA] bg-[#FEF2F2] p-4 flex items-start gap-2">
                <XCircle className="w-4 h-4 text-[#B91C1C] mt-0.5 flex-shrink-0" />
                <div>
                  <p className="text-sm font-semibold text-[#B91C1C]">Rejected</p>
                  <p className="text-sm text-[#991B1B] mt-0.5">{req.rejection_reason || 'No reason recorded.'}</p>
                </div>
              </div>
            )}
          </CardContent>
        </Card>

        {/* ── 3. Actions ──────────────────────────────────────────────────── */}
        {(canApproveFm || canApproveCfo || canCancel) && (
          <Card>
            <CardHeader><CardTitle>Actions</CardTitle></CardHeader>
            <CardContent className="p-6 pt-2 flex items-center gap-3 flex-wrap">
              {canApproveFm && (
                <Button variant="accent" leftIcon={<CheckCircle2 className="w-4 h-4" />} onClick={() => setModal('fm')}>
                  Finance Manager: Approve
                </Button>
              )}
              {canApproveCfo && (
                <Button variant="accent" leftIcon={<CheckCircle2 className="w-4 h-4" />} onClick={() => setModal('cfo')}>
                  CFO: Approve
                </Button>
              )}
              {canReject && (
                <Button variant="danger" leftIcon={<XCircle className="w-4 h-4" />} onClick={() => setModal('reject')}>
                  Reject
                </Button>
              )}
              {canCancel && (
                <Button variant="ghost" size="sm" onClick={() => setModal('cancel')} style={{ color: theme.t3 }}>
                  Cancel requisition
                </Button>
              )}
            </CardContent>
          </Card>
        )}

        {/* ── 4. Handover creation ────────────────────────────────────────── */}
        {showHandover && (
          <HandoverPanel req={req} onCreated={(hoId) => router.push(`/assets/handovers/${hoId}`)} />
        )}

        {/* ── 5. Existing handover link ───────────────────────────────────── */}
        {req.handover_id && (
          <Card>
            <CardContent className="p-6 flex items-center justify-between gap-4 flex-wrap">
              <div className="flex items-center gap-2">
                <CheckCircle2 className="w-5 h-5 text-[#059669]" />
                <p className="text-sm font-medium" style={{ color: theme.text }}>A handover note has been created for this requisition.</p>
              </div>
              <Link href={`/assets/handovers/${req.handover_id}`}>
                <Button variant="primary" rightIcon={<ArrowRight className="w-4 h-4" />}>Open handover note</Button>
              </Link>
            </CardContent>
          </Card>
        )}
      </div>

      {/* ── Approve modals (optional comment) ─────────────────────────────── */}
      {modal === 'fm' && (
        <CommentModal
          title="Finance Manager approval"
          confirmLabel="Approve"
          onClose={() => setModal(null)}
          onConfirm={async (comment) => {
            await fmApproveRequisition(id, comment)
            afterAction('Finance Manager approval recorded.')
          }}
        />
      )}
      {modal === 'cfo' && (
        <CommentModal
          title="CFO approval"
          confirmLabel="Approve"
          onClose={() => setModal(null)}
          onConfirm={async (comment) => {
            await cfoApproveRequisition(id, comment)
            afterAction('CFO approval recorded.')
          }}
        />
      )}

      {/* ── Reject / cancel (reason) ──────────────────────────────────────── */}
      {modal === 'reject' && (
        <ReasonModal
          title="Reject requisition"
          description="This closes the requisition. The recipient and raiser are notified. A reason is required."
          confirmLabel="Reject requisition"
          reasonRequired
          danger
          onClose={() => setModal(null)}
          onConfirm={async (reason) => {
            await rejectRequisition(id, reason)
            afterAction('Requisition rejected.')
          }}
        />
      )}
      {modal === 'cancel' && (
        <ReasonModal
          title="Cancel requisition"
          description="Withdraw this requisition. You can record an optional note for the audit trail."
          confirmLabel="Cancel requisition"
          danger
          onClose={() => setModal(null)}
          onConfirm={async (reason) => {
            await cancelRequisition(id, reason)
            afterAction('Requisition cancelled.')
          }}
        />
      )}
    </div>
  )
}

// ── Field row ─────────────────────────────────────────────────────────────────
function Field({ icon, label, value, className }: {
  icon?: React.ReactNode; label: string; value: React.ReactNode; className?: string
}) {
  const { theme } = useTheme()
  return (
    <div className={className}>
      <p className="text-xs font-medium uppercase tracking-wider flex items-center gap-1.5" style={{ color: theme.t3 }}>
        {icon}{label}
      </p>
      <p className="mt-1" style={{ color: theme.text }}>{value}</p>
    </div>
  )
}

// ── Timeline step ───────────────────────────────────────────────────────────
function TimelineStep({ theme, title, done, notRequired, doneText, pendingText, notRequiredText }: {
  theme: ReturnType<typeof useTheme>['theme']
  title: string
  done: boolean
  notRequired?: boolean
  doneText?: string
  pendingText: string
  notRequiredText?: string
}) {
  const state: 'done' | 'skip' | 'pending' = done ? 'done' : notRequired ? 'skip' : 'pending'
  const dot = state === 'done'
    ? <CheckCircle2 className="w-5 h-5 text-[#059669]" />
    : state === 'skip'
      ? <Clock className="w-5 h-5" style={{ color: theme.t3 }} />
      : <Clock className="w-5 h-5 text-[#D97706]" />
  const detail = state === 'done' ? doneText : state === 'skip' ? notRequiredText : pendingText
  return (
    <div className="flex items-start gap-3">
      <div className="mt-0.5 flex-shrink-0">{dot}</div>
      <div>
        <p className="text-sm font-medium" style={{ color: theme.text }}>{title}</p>
        <p className="text-xs mt-0.5" style={{ color: state === 'pending' ? '#D97706' : theme.t3 }}>{detail}</p>
      </div>
    </div>
  )
}

// ── Comment modal (optional) ──────────────────────────────────────────────────
function CommentModal({ title, confirmLabel, onClose, onConfirm }: {
  title: string; confirmLabel: string; onClose: () => void; onConfirm: (comment: string) => Promise<void>
}) {
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(false)

  async function go() {
    setBusy(true)
    try {
      await onConfirm(comment.trim())
    } catch (e) {
      pushToast({ type: 'error', message: (e as Error).message })
      setBusy(false)
    }
  }

  return (
    <Modal open onOpenChange={(o) => !o && onClose()} title={title} size="md">
      <ModalBody className="space-y-3">
        <div>
          <Label htmlFor="req-comment">Comment (optional)</Label>
          <Textarea id="req-comment" value={comment} onChange={(e) => setComment(e.target.value)}
                    placeholder="Add an approval note for the record…" />
        </div>
      </ModalBody>
      <ModalFooter>
        <Button variant="secondary" onClick={onClose} disabled={busy}>Close</Button>
        <Button variant="accent" onClick={go} loading={busy}>{confirmLabel}</Button>
      </ModalFooter>
    </Modal>
  )
}

// ── Reason modal ──────────────────────────────────────────────────────────────
function ReasonModal({ title, description, confirmLabel, reasonRequired, danger, onClose, onConfirm }: {
  title: string; description: string; confirmLabel: string
  reasonRequired?: boolean; danger?: boolean
  onClose: () => void; onConfirm: (reason: string) => Promise<void>
}) {
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const disabled = busy || (reasonRequired && !reason.trim())

  async function go() {
    setBusy(true)
    try {
      await onConfirm(reason.trim())
    } catch (e) {
      pushToast({ type: 'error', message: (e as Error).message })
      setBusy(false)
    }
  }

  return (
    <Modal open onOpenChange={(o) => !o && onClose()} title={title} description={description} size="md">
      <ModalBody className="space-y-3">
        <div>
          <Label htmlFor="req-reason" required={reasonRequired}>Reason</Label>
          <Textarea id="req-reason" value={reason} onChange={(e) => setReason(e.target.value)}
                    placeholder={reasonRequired ? 'Required — explain why…' : 'Optional note…'} />
        </div>
      </ModalBody>
      <ModalFooter>
        <Button variant="secondary" onClick={onClose} disabled={busy}>Close</Button>
        <Button variant={danger ? 'danger' : 'accent'} onClick={go} loading={busy} disabled={disabled}>{confirmLabel}</Button>
      </ModalFooter>
    </Modal>
  )
}

// ── Handover creation panel ───────────────────────────────────────────────────
function HandoverPanel({ req, onCreated }: { req: AssetRequisition; onCreated: (hoId: string) => void }) {
  const { theme } = useTheme()
  const isReissue = req.req_type === 'reissue'

  const [assets, setAssets] = useState<StockAsset[]>([])
  const [selectedAsset, setSelectedAsset] = useState('')
  const [condition, setCondition] = useState('')
  const [accessories, setAccessories] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (isReissue) return
    getAssets({ page_size: 200 })
      .then((res) => {
        // Only in-stock assets can start a new-purchase handover; the backend
        // enforces this too, but filtering keeps the picker honest.
        setAssets((res.results as StockAsset[]).filter((a) => a.custody_status === 'in_stock'))
      })
      .catch((e) => pushToast({ type: 'error', message: (e as Error).message }))
  }, [isReissue])

  async function create() {
    const asset = isReissue ? (req.spare_asset || '') : selectedAsset
    if (!asset) {
      pushToast({ type: 'warning', message: 'Choose an asset to hand over.' })
      return
    }
    setBusy(true)
    try {
      const ho = await createHandover(
        isReissue
          ? { requisition: req.id, asset }
          : { requisition: req.id, asset, condition_on_issue: condition.trim(), accessories: accessories.trim() },
      )
      pushToast({ type: 'success', message: 'Handover note created.' })
      onCreated(ho.id)
    } catch (e) {
      pushToast({ type: 'error', message: (e as Error).message })
      setBusy(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <PenLine className="w-4 h-4 text-[#F4A623]" />
          Create handover note
        </CardTitle>
      </CardHeader>
      <CardContent className="p-6 pt-2 space-y-4">
        {isReissue ? (
          <div>
            <Label>Spare asset (fixed for reissue)</Label>
            <div className="h-10 flex items-center rounded-md px-3 text-sm"
                 style={{ background: theme.g100, border: `1px solid ${theme.g200}`, color: theme.text }}>
              {req.spare_asset_tag || '— no spare linked —'}
            </div>
          </div>
        ) : (
          <>
            <div>
              <Label>Asset to hand over</Label>
              <SearchableSelect
                options={assets.map((a) => ({ value: a.id, label: `${a.tag_number} — ${a.name}` }))}
                value={selectedAsset}
                onChange={setSelectedAsset}
                placeholder="Select an asset from stock…"
              />
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <Label htmlFor="ho-condition">Condition on issue</Label>
                <Input id="ho-condition" value={condition} onChange={(e) => setCondition(e.target.value)}
                       placeholder="e.g. New, sealed" />
              </div>
              <div>
                <Label htmlFor="ho-accessories">Accessories</Label>
                <Input id="ho-accessories" value={accessories} onChange={(e) => setAccessories(e.target.value)}
                       placeholder="e.g. Charger, sleeve, dongle" />
              </div>
            </div>
          </>
        )}

        <div className="flex justify-end">
          <Button variant="accent" onClick={create} loading={busy}
                  disabled={busy || (isReissue ? !req.spare_asset : !selectedAsset)}>
            Create handover
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
