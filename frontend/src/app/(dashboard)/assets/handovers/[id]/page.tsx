'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams } from 'next/navigation'
import Link from 'next/link'
import {
  getAssetHandover, handoverItRelease, handoverFinanceRecord, handoverAccept,
  apiFetchBinary,
} from '@/lib/api'
import type { AssetHandover } from '@/lib/api'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { StatusBadge } from '@/components/ui/badge'
import { LoadingCard } from '@/components/ui/loading'
import { pushToast } from '@/components/Toaster'
import { useTheme } from '@/contexts/ThemeContext'

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'

export default function HandoverDetailPage() {
  const { id } = useParams<{ id: string }>()
  const { theme, isFun } = useTheme()

  const [handover, setHandover] = useState<AssetHandover | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      setHandover(await getAssetHandover(id))
    } catch (e) {
      pushToast({ type: 'error', message: (e as Error).message })
    } finally {
      setLoading(false)
    }
  }, [id])

  useEffect(() => { load() }, [load])

  async function runAction(
    fn: (id: string) => Promise<AssetHandover>,
    successMsg: string,
  ) {
    setBusy(true)
    try {
      const updated = await fn(id)
      setHandover(updated)
      pushToast({ type: 'success', message: successMsg })
    } catch (e) {
      pushToast({ type: 'error', message: (e as Error).message })
      // Re-fetch so the UI reflects the true server state after a failure.
      load()
    } finally {
      setBusy(false)
    }
  }

  async function downloadPdf() {
    try {
      const r = await apiFetchBinary(`/asset-handovers/${id}/pdf/`)
      if (!r.ok) throw new Error(`Could not fetch PDF (HTTP ${r.status})`)
      const blob = await r.blob()
      const url = URL.createObjectURL(blob)
      window.open(url, '_blank')
      setTimeout(() => URL.revokeObjectURL(url), 10000)
    } catch (e) {
      pushToast({ type: 'error', message: (e as Error).message })
    }
  }

  if (loading) {
    return <div className="p-6"><LoadingCard message="Loading hand-over…" /></div>
  }

  if (!handover) {
    return (
      <div className="p-6">
        <Card>
          <CardContent className="p-8 text-center text-sm" style={{ color: theme.t3 }}>
            Hand-over not found.
          </CardContent>
        </Card>
      </div>
    )
  }

  const h = handover

  return (
    <div className="p-6 space-y-4 max-w-4xl">
      {/* ── Header card ──────────────────────────────────────────────── */}
      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.09em]" style={{ color: theme.t2 }}>
              Asset hand-over
            </p>
            <CardTitle
              className={`font-serif text-2xl mt-1 ${isFun ? 'tracking-tight' : ''}`}
              style={{ color: NAVY }}
            >
              {h.handover_number}
            </CardTitle>
          </div>
          <div className="flex items-center gap-2">
            <StatusBadge status={h.status_display} size="md" />
            <Button variant="outline" size="sm" onClick={downloadPdf}>Download PDF</Button>
          </div>
        </CardHeader>
        <CardContent className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-3 text-sm">
          <Field label="Asset" value={`${h.asset_tag} — ${h.asset_name}`} theme={theme} />
          <Field
            label="Requisition"
            theme={theme}
            value={
              <Link
                href={`/assets/requisitions/${h.requisition}`}
                className="font-medium underline underline-offset-2"
                style={{ color: ORANGE }}
              >
                {h.requisition_number}
              </Link>
            }
          />
          <Field label="Recipient" value={h.recipient_name} theme={theme} />
          <Field label="Recipient email" value={h.recipient_email || '—'} theme={theme} />
          <Field label="Condition on issue" value={h.condition_on_issue || '—'} theme={theme} />
          <Field label="Accessories" value={h.accessories || '—'} theme={theme} />
        </CardContent>
      </Card>

      {/* ── Complete banner ─────────────────────────────────────────── */}
      {h.is_complete && (
        <div
          className="rounded-lg border px-4 py-3 text-sm font-medium"
          style={{ background: '#ECFDF5', borderColor: '#A7F3D0', color: '#047857' }}
        >
          Complete — the asset is now in use.
        </div>
      )}

      {/* ── Signature stepper ───────────────────────────────────────── */}
      <Card>
        <CardHeader>
          <CardTitle className="font-serif" style={{ color: NAVY }}>Signatures</CardTitle>
        </CardHeader>
        <CardContent className="space-y-0">
          <Step
            index={1}
            title="IT releases"
            theme={theme}
            done={!!h.it_released_at}
            byName={h.it_released_by_name}
            at={h.it_released_at}
            action={
              h.status === 'pending' ? (
                <Button
                  variant="accent" size="sm" disabled={busy}
                  style={{ background: ORANGE, borderColor: ORANGE }}
                  onClick={() => runAction(handoverItRelease, 'IT release signed.')}
                >
                  Sign — IT release
                </Button>
              ) : null
            }
          />
          <Step
            index={2}
            title="Finance records"
            theme={theme}
            done={!!h.finance_recorded_at}
            byName={h.finance_recorded_by_name}
            at={h.finance_recorded_at}
            action={
              !h.finance_recorded_at ? (
                <Button
                  variant="accent" size="sm" disabled={busy || h.status !== 'it_released'}
                  style={{ background: ORANGE, borderColor: ORANGE }}
                  onClick={() => runAction(handoverFinanceRecord, 'Finance record signed.')}
                >
                  Sign — Finance record
                </Button>
              ) : null
            }
          />
          <Step
            index={3}
            title="Employee accepts"
            theme={theme}
            done={!!h.employee_accepted_at}
            byName={h.employee_accepted_by_name}
            at={h.employee_accepted_at}
            isLast
            action={
              !h.employee_accepted_at ? (
                <div className="space-y-1.5">
                  <Button
                    variant="accent" size="sm" disabled={busy || h.status !== 'finance_recorded'}
                    style={{ background: ORANGE, borderColor: ORANGE }}
                    onClick={() => runAction(handoverAccept, 'Hand-over accepted.')}
                  >
                    Sign — Accept
                  </Button>
                  <p className="text-xs" style={{ color: theme.t3 }}>
                    Only the named recipient can accept (signed in as them).
                  </p>
                </div>
              ) : null
            }
          />
        </CardContent>
      </Card>
    </div>
  )
}

function fmtDate(v: string | null): string {
  if (!v) return ''
  const d = new Date(v)
  return Number.isNaN(d.getTime()) ? v : d.toLocaleString('en-BW')
}

function Field({ label, value, theme }: {
  label: string; value: React.ReactNode; theme: { t2: string; text: string }
}) {
  return (
    <div>
      <p className="text-[10.5px] font-semibold uppercase tracking-[0.09em]" style={{ color: theme.t2 }}>
        {label}
      </p>
      <p className="mt-0.5" style={{ color: theme.text }}>{value}</p>
    </div>
  )
}

function Step({ index, title, done, byName, at, action, isLast, theme }: {
  index: number
  title: string
  done: boolean
  byName: string
  at: string | null
  action: React.ReactNode
  isLast?: boolean
  theme: { t2: string; t3: string; text: string; ok: string; cardBdr: string }
}) {
  return (
    <div className="flex gap-3">
      {/* Rail: numbered / ticked node + connector */}
      <div className="flex flex-col items-center">
        <span
          className="flex items-center justify-center w-7 h-7 rounded-full text-xs font-semibold shrink-0"
          style={
            done
              ? { background: '#ECFDF5', color: theme.ok, border: `1px solid #A7F3D0` }
              : { background: '#FFFFFF', color: theme.t3, border: `1px solid ${theme.cardBdr}` }
          }
        >
          {done ? '✓' : index}
        </span>
        {!isLast && <span className="w-px flex-1 my-1" style={{ background: theme.cardBdr }} />}
      </div>
      {/* Body */}
      <div className={`flex-1 flex items-start justify-between gap-4 ${isLast ? '' : 'pb-5'}`}>
        <div>
          <p className="text-sm font-medium" style={{ color: theme.text }}>{title}</p>
          {done ? (
            <p className="text-xs mt-0.5" style={{ color: theme.t2 }}>
              {byName || 'Signed'}{at ? ` · ${fmtDate(at)}` : ''}
            </p>
          ) : (
            <p className="text-xs mt-0.5" style={{ color: theme.t3 }}>Awaiting signature</p>
          )}
        </div>
        {action}
      </div>
    </div>
  )
}
