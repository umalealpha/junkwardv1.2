'use client'

import { useEffect, useState, useCallback } from 'react'
import Link from 'next/link'
import {
  getRequisitionQueue,
  fmApproveRequisition,
  cfoApproveRequisition,
  rejectRequisition,
} from '@/lib/api'
import type { AssetRequisition } from '@/lib/api'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { StatTile } from '@/components/ui/StatTile'
import { Button } from '@/components/ui/button'
import { Modal, ModalBody, ModalFooter } from '@/components/ui/modal'
import { StatusBadge } from '@/components/ui/badge'
import { Textarea } from '@/components/ui/input'
import { LoadingCard } from '@/components/ui/loading'
import { pushToast } from '@/components/Toaster'
import { useTheme } from '@/contexts/ThemeContext'
import { CheckCircle2, XCircle, Inbox } from 'lucide-react'

const FM_STATUS = 'pending_fm_approval'
const CFO_STATUS = 'pending_cfo_approval'

function fmtBwp(value: string | number): string {
  const n = typeof value === 'string' ? parseFloat(value) : value
  if (Number.isNaN(n)) return 'P —'
  return `P ${new Intl.NumberFormat('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n)}`
}

export default function AssetApprovalsPage() {
  const { theme } = useTheme()
  const [queue, setQueue] = useState<AssetRequisition[]>([])
  const [loading, setLoading] = useState(true)
  const [actingId, setActingId] = useState<string | null>(null)

  // Reject flow state
  const [rejectTarget, setRejectTarget] = useState<AssetRequisition | null>(null)
  const [rejectReason, setRejectReason] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const rows = await getRequisitionQueue()
      setQueue(rows)
    } catch (e) {
      pushToast({ type: 'error', message: (e as Error).message })
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const approve = async (r: AssetRequisition) => {
    setActingId(r.id)
    try {
      if (r.status === FM_STATUS) await fmApproveRequisition(r.id)
      else if (r.status === CFO_STATUS) await cfoApproveRequisition(r.id)
      pushToast({ type: 'success', message: `Requisition ${r.requisition_number} approved.` })
      await load()
    } catch (e) {
      pushToast({ type: 'error', message: (e as Error).message })
    } finally {
      setActingId(null)
    }
  }

  const confirmReject = async () => {
    if (!rejectTarget) return
    const reason = rejectReason.trim()
    if (!reason) {
      pushToast({ type: 'error', message: 'A reason is required to reject a requisition.' })
      return
    }
    setActingId(rejectTarget.id)
    try {
      await rejectRequisition(rejectTarget.id, reason)
      pushToast({ type: 'success', message: `Requisition ${rejectTarget.requisition_number} rejected.` })
      setRejectTarget(null)
      setRejectReason('')
      await load()
    } catch (e) {
      pushToast({ type: 'error', message: (e as Error).message })
    } finally {
      setActingId(null)
    }
  }

  const awaitingFm = queue.filter((r) => r.status === FM_STATUS).length
  const awaitingCfo = queue.filter((r) => r.status === CFO_STATUS).length

  return (
    <div className="flex-1 p-6 space-y-6">
      {/* Header */}
      <div>
        <h1
          className="text-2xl font-serif font-semibold tracking-tight"
          style={{ color: theme.navy }}
        >
          Approvals Queue
        </h1>
        <p className="mt-1 text-sm" style={{ color: theme.t3 }}>
          Requisitions waiting for your decision. You cannot approve a requisition you raised.
        </p>
      </div>

      {/* Stat tiles */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 max-w-xl">
        <StatTile label="Awaiting Finance Manager" value={awaitingFm} tone="warn" />
        <StatTile label="Awaiting CFO" value={awaitingCfo} tone="accent" />
      </div>

      {/* Queue */}
      {loading ? (
        <LoadingCard message="Loading your approvals…" />
      ) : queue.length === 0 ? (
        <Card>
          <CardContent className="py-16 flex flex-col items-center text-center">
            <Inbox className="w-10 h-10 mb-3" style={{ color: theme.t3 }} strokeWidth={1.5} />
            <p className="text-sm" style={{ color: theme.t2 }}>
              Nothing awaits your approval right now.
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-3">
          {queue.map((r) => {
            const busy = actingId === r.id
            return (
              <Card key={r.id}>
                <CardHeader className="flex flex-row items-start justify-between gap-4">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <Link
                        href={`/assets/requisitions/${r.id}`}
                        className="font-mono text-sm font-medium hover:underline"
                        style={{ color: theme.orange }}
                      >
                        {r.requisition_number}
                      </Link>
                      <StatusBadge status={r.status_display} />
                    </div>
                    <CardTitle className="mt-1 text-base" style={{ color: theme.navy }}>
                      {r.category_name}
                    </CardTitle>
                  </div>
                  <div className="text-right shrink-0">
                    <div className="text-lg font-semibold tabular-nums" style={{ color: theme.text }}>
                      {fmtBwp(r.estimated_value)}
                    </div>
                    <div className="text-[11px] uppercase tracking-wide" style={{ color: theme.t3 }}>
                      Estimated value
                    </div>
                  </div>
                </CardHeader>
                <CardContent className="flex flex-col md:flex-row md:items-end md:justify-between gap-4">
                  <div className="space-y-1.5 text-sm min-w-0">
                    <div>
                      <span style={{ color: theme.text }}>{r.recipient_name}</span>{' '}
                      <span style={{ color: theme.t3 }}>· {r.recipient_email}</span>
                    </div>
                    <p style={{ color: theme.t2 }}>{r.reason}</p>
                  </div>
                  <div className="flex gap-2 shrink-0">
                    <Button
                      variant="success"
                      size="sm"
                      loading={busy}
                      disabled={busy}
                      leftIcon={<CheckCircle2 className="w-3.5 h-3.5" />}
                      onClick={() => approve(r)}
                    >
                      Approve
                    </Button>
                    <Button
                      variant="danger"
                      size="sm"
                      disabled={busy}
                      leftIcon={<XCircle className="w-3.5 h-3.5" />}
                      onClick={() => { setRejectTarget(r); setRejectReason('') }}
                    >
                      Reject
                    </Button>
                  </div>
                </CardContent>
              </Card>
            )
          })}
        </div>
      )}

      {/* Reject dialog */}
      <Modal
        open={rejectTarget !== null}
        onOpenChange={(o) => { if (!o) { setRejectTarget(null); setRejectReason('') } }}
        size="sm"
        title="Reject requisition"
        description={rejectTarget ? `${rejectTarget.requisition_number} — ${rejectTarget.category_name}` : undefined}
      >
        <ModalBody>
          <label className="block text-sm font-medium mb-1.5" style={{ color: theme.text }}>
            Reason <span style={{ color: theme.er }}>*</span>
          </label>
          <Textarea
            value={rejectReason}
            onChange={(e) => setRejectReason(e.target.value)}
            placeholder="Why is this requisition being rejected? The requester will see this."
          />
        </ModalBody>
        <ModalFooter>
          <Button
            variant="secondary"
            size="sm"
            disabled={actingId !== null}
            onClick={() => { setRejectTarget(null); setRejectReason('') }}
          >
            Cancel
          </Button>
          <Button
            variant="danger"
            size="sm"
            loading={rejectTarget !== null && actingId === rejectTarget.id}
            disabled={!rejectReason.trim()}
            onClick={confirmReject}
          >
            Reject requisition
          </Button>
        </ModalFooter>
      </Modal>
    </div>
  )
}
