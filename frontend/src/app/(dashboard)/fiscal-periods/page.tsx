'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { closePeriod, getFiscalPeriods, getToken, reopenPeriod } from '@/lib/api'
import type { FiscalPeriod } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { LoadingTable } from '@/components/ui/loading'
import { formatDate, localYmd } from '@/lib/utils'
import { AlertCircle, CheckCircle2, Lock, Clock, ShieldCheck, Unlock, Calendar, X, Loader2 } from 'lucide-react'
import { useTheme } from '@/contexts/ThemeContext'

const STATUS_CONFIG: Record<string, { label: string; icon: typeof CheckCircle2 }> = {
  open:    { label: 'Open',    icon: CheckCircle2 },
  closing: { label: 'Closing', icon: Clock },
  closed:  { label: 'Closed',  icon: Lock },
}

type DialogMode = 'close' | 'reopen'

function getMonthLabel(periodName: string): string {
  const [year, month] = periodName.split('-')
  const date = new Date(Number(year), Number(month) - 1, 1)
  return date.toLocaleDateString('en-GB', { month: 'long', year: 'numeric' })
}

export default function FiscalPeriodsPage() {
  const router = useRouter()
  const { theme } = useTheme()
  const [periods, setPeriods] = useState<FiscalPeriod[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState<string>('')
  const [dialog, setDialog] = useState<{ mode: DialogMode; period: FiscalPeriod } | null>(null)

  useEffect(() => {
    const token = getToken()
    if (!token) { router.replace('/login'); return }
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      // CFO directive 2026-05-18: list only FY25 onwards. The backend
      // applies the same cutoff but we pass it explicitly so older
      // periods can never sneak in via a stale client.
      const res = await getFiscalPeriods({ start_date_gte: '2024-07-01' })
      setPeriods(res.results)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load fiscal periods')
    } finally {
      setLoading(false)
    }
  }

  async function applyDialog(password: string, reason: string): Promise<void> {
    if (!dialog) return
    const { mode, period } = dialog
    if (mode === 'close') {
      await closePeriod(period.id, password, reason)
    } else {
      await reopenPeriod(period.id, password, reason)
    }
    setDialog(null)
    await load()
  }

  const filtered = statusFilter
    ? periods.filter((p) => p.status === statusFilter)
    : periods

  const openCount = periods.filter((p) => p.status === 'open').length
  const closedCount = periods.filter((p) => p.status === 'closed').length

  // Determine current period (today falls within start_date..end_date)
  const todayStr = localYmd(new Date())
  const currentPeriod = periods.find(
    (p) => p.start_date <= todayStr && p.end_date >= todayStr
  )

  // Fiscal year label from the first and last period
  const fyLabel = periods.length > 0
    ? `${getMonthLabel(periods[0].period_name)} \u2013 ${getMonthLabel(periods[periods.length - 1].period_name)}`
    : ''

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Fiscal Periods"
        breadcrumbs={[{ label: 'Accounting' }, { label: 'Fiscal Periods' }]}
      />

      <div className="flex-1 p-6 space-y-6">
        {/* Summary cards */}
        {!loading && periods.length > 0 && (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div
              className="rounded-lg p-5"
              style={{ background: theme.oL, border: `1px solid ${theme.orange}30` }}
            >
              <p className="text-xs font-medium uppercase tracking-wider" style={{ color: theme.orange }}>
                Fiscal Year
              </p>
              <p className="text-lg font-bold mt-2" style={{ color: theme.orange }}>
                {fyLabel}
              </p>
              <p className="text-xs mt-1" style={{ color: theme.t3 }}>
                {periods.length} monthly periods
              </p>
            </div>
            <div
              className="rounded-lg p-5"
              style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}
            >
              <p className="text-xs font-medium uppercase tracking-wider" style={{ color: theme.t2 }}>
                Current Period
              </p>
              <p className="text-lg font-bold mt-2" style={{ color: theme.text }}>
                {currentPeriod ? getMonthLabel(currentPeriod.period_name) : 'N/A'}
              </p>
              <p className="text-xs mt-1" style={{ color: theme.t3 }}>
                {currentPeriod ? `${formatDate(currentPeriod.start_date)} \u2013 ${formatDate(currentPeriod.end_date)}` : 'No open period for today'}
              </p>
            </div>
            <div
              className="rounded-lg p-5"
              style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}
            >
              <p className="text-xs font-medium uppercase tracking-wider" style={{ color: theme.t2 }}>
                Period Status
              </p>
              <div className="flex items-center gap-4 mt-2">
                <div>
                  <p className="text-lg font-bold" style={{ color: theme.ok }}>{openCount}</p>
                  <p className="text-xs" style={{ color: theme.t3 }}>Open</p>
                </div>
                <div>
                  <p className="text-lg font-bold" style={{ color: theme.t3 }}>{closedCount}</p>
                  <p className="text-xs" style={{ color: theme.t3 }}>Closed</p>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Filter tabs */}
        {!loading && periods.length > 0 && (
          <div className="flex items-center gap-1 rounded-lg p-1" style={{ background: theme.g100, width: 'fit-content' }}>
            {[
              { value: '', label: 'All' },
              { value: 'open', label: 'Open' },
              { value: 'closing', label: 'Closing' },
              { value: 'closed', label: 'Closed' },
            ].map((tab) => (
              <button
                key={tab.value}
                onClick={() => setStatusFilter(tab.value)}
                className="px-3 py-1.5 text-xs font-medium rounded-md transition-colors"
                style={{
                  background: statusFilter === tab.value ? theme.card : 'transparent',
                  color: statusFilter === tab.value ? theme.text : theme.t2,
                  boxShadow: statusFilter === tab.value ? '0 1px 2px rgba(0,0,0,0.06)' : 'none',
                }}
              >
                {tab.label}
              </button>
            ))}
          </div>
        )}

        {/* Error */}
        {error && (
          <div
            className="rounded-lg p-4 flex items-center gap-3"
            style={{ background: theme.erB, border: `1px solid ${theme.er}30` }}
          >
            <AlertCircle className="w-5 h-5 flex-shrink-0" style={{ color: theme.er }} />
            <p className="text-sm" style={{ color: theme.er }}>{error}</p>
          </div>
        )}

        {/* Table */}
        <Card>
          <CardContent className="p-0">
            {loading ? (
              <LoadingTable rows={12} cols={5} />
            ) : filtered.length === 0 ? (
              <div className="py-16 text-center">
                <Calendar className="w-8 h-8 mx-auto mb-3" style={{ color: theme.g200 }} />
                <p className="font-medium" style={{ color: theme.t2 }}>
                  {statusFilter ? 'No periods match this filter' : 'No fiscal periods found'}
                </p>
                {!statusFilter && (
                  <p className="text-sm mt-1" style={{ color: theme.t3 }}>
                    Run setup_chart_of_accounts to create fiscal periods.
                  </p>
                )}
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead style={{ background: theme.g100, borderBottom: `2px solid ${theme.cardBdr}` }}>
                    <tr>
                      <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider" style={{ color: theme.g700 }}>
                        Period
                      </th>
                      <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider" style={{ color: theme.g700 }}>
                        Month
                      </th>
                      <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider hidden sm:table-cell" style={{ color: theme.g700 }}>
                        Start Date
                      </th>
                      <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider hidden sm:table-cell" style={{ color: theme.g700 }}>
                        End Date
                      </th>
                      <th className="px-4 py-3 text-center text-xs font-semibold uppercase tracking-wider" style={{ color: theme.g700 }}>
                        Status
                      </th>
                      <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wider" style={{ color: theme.g700 }}>
                        Action
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {filtered.map((period, idx) => {
                      const isCurrent = currentPeriod?.id === period.id
                      const config = STATUS_CONFIG[period.status] || STATUS_CONFIG.open
                      const StatusIcon = config.icon
                      const statusColor =
                        period.status === 'open' ? theme.ok
                          : period.status === 'closing' ? theme.wr
                          : theme.t3
                      const statusBg =
                        period.status === 'open' ? theme.okB
                          : period.status === 'closing' ? theme.wrB
                          : theme.g100

                      return (
                        <tr
                          key={period.id}
                          className="transition-colors"
                          style={{
                            background: isCurrent ? theme.oL : theme.card,
                            borderBottom: idx < filtered.length - 1 ? `1px solid ${theme.cardBdr}` : 'none',
                          }}
                        >
                          <td className="px-4 py-3">
                            <div className="flex items-center gap-2.5">
                              <span className="font-mono text-xs font-medium" style={{ color: theme.t2 }}>
                                {period.period_name}
                              </span>
                              {isCurrent && (
                                <span
                                  className="text-[10px] font-bold uppercase px-1.5 py-0.5 rounded"
                                  style={{ background: theme.orange + '20', color: theme.orange }}
                                >
                                  Current
                                </span>
                              )}
                            </div>
                          </td>
                          <td className="px-4 py-3 font-medium" style={{ color: theme.text }}>
                            {getMonthLabel(period.period_name)}
                          </td>
                          <td className="px-4 py-3 text-xs hidden sm:table-cell" style={{ color: theme.t2 }}>
                            {formatDate(period.start_date)}
                          </td>
                          <td className="px-4 py-3 text-xs hidden sm:table-cell" style={{ color: theme.t2 }}>
                            {formatDate(period.end_date)}
                          </td>
                          <td className="px-4 py-3 text-center">
                            <span
                              className="inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded"
                              style={{ background: statusBg, color: statusColor }}
                            >
                              <StatusIcon className="w-3 h-3" />
                              {config.label}
                            </span>
                          </td>
                          <td className="px-4 py-3 text-right">
                            {period.status === 'open' && (
                              <button
                                onClick={() => setDialog({ mode: 'close', period })}
                                className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded text-xs font-semibold transition-opacity hover:opacity-90"
                                style={{ background: theme.orange, color: '#fff' }}
                              >
                                <Lock className="w-3 h-3" /> Close
                              </button>
                            )}
                            {period.status === 'closed' && (
                              <button
                                onClick={() => setDialog({ mode: 'reopen', period })}
                                className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded text-xs font-semibold transition-opacity hover:opacity-90"
                                style={{ background: theme.g100, color: theme.text, border: `1px solid ${theme.cardBdr}` }}
                              >
                                <Unlock className="w-3 h-3" /> Reopen
                              </button>
                            )}
                            {period.status === 'closing' && (
                              <span className="text-xs italic" style={{ color: theme.t3 }}>
                                In closing
                              </span>
                            )}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {dialog && (
        <PeriodAuthDialog
          mode={dialog.mode}
          period={dialog.period}
          onCancel={() => setDialog(null)}
          onConfirm={applyDialog}
          theme={theme}
        />
      )}
    </div>
  )
}

// ─── CFO authentication dialog ────────────────────────────────────────────────
//
// Open/close fiscal periods are CFO-only AND require the financial-lock
// password (OMNI_FINANCIAL_LOCK_OVERRIDE) so a stale browser tab on a
// shared machine can't trigger them accidentally. The dialog blocks the
// page until the operator either confirms or cancels.

interface PeriodAuthDialogProps {
  mode: DialogMode
  period: FiscalPeriod
  onCancel: () => void
  onConfirm: (password: string, reason: string) => Promise<void>
  theme: any
}

function PeriodAuthDialog({ mode, period, onCancel, onConfirm, theme }: PeriodAuthDialogProps) {
  const [password, setPassword] = useState('')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const isClose = mode === 'close'
  const title   = isClose ? `Close ${period.period_name}` : `Reopen ${period.period_name}`
  const verb    = isClose ? 'Close period' : 'Reopen period'
  const reasonPlaceholder = isClose
    ? 'Optional — reviewer sign-off note'
    : 'Required — why this close is being rolled back'

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setErr(null)
    if (!password) {
      setErr('CFO financial-lock password is required.')
      return
    }
    if (!isClose && !reason.trim()) {
      setErr('Reopen reason is required for the audit log.')
      return
    }
    setBusy(true)
    try {
      await onConfirm(password, reason)
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : 'Failed.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
         style={{ background: 'rgba(13,27,42,0.55)' }}
         onClick={onCancel}>
      <form
        onClick={e => e.stopPropagation()}
        onSubmit={submit}
        className="w-full max-w-md rounded-2xl p-6 space-y-4"
        style={{ background: theme.card, border: `1px solid ${theme.cardBdr}`, boxShadow: '0 20px 60px rgba(13,27,42,0.35)' }}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-2">
            <ShieldCheck className="w-5 h-5" style={{ color: theme.orange }} />
            <h3 className="font-semibold" style={{ color: theme.text }}>{title}</h3>
          </div>
          <button type="button" onClick={onCancel}
                  className="p-1 rounded hover:opacity-70"
                  style={{ color: theme.t2 }}>
            <X className="w-4 h-4" />
          </button>
        </div>

        <p className="text-xs" style={{ color: theme.t2 }}>
          {isClose
            ? `Closing locks ${period.period_name} (${formatDate(period.start_date)} – ${formatDate(period.end_date)}) from new postings. CFO-only.`
            : `Reopening ${period.period_name} unlocks it for new postings. CFO-only. Action is audit-logged.`}
        </p>

        <div>
          <label className="block text-xs font-medium mb-1" style={{ color: theme.t2 }}>
            Financial-lock password
          </label>
          <input
            type="password"
            autoFocus
            value={password}
            onChange={e => setPassword(e.target.value)}
            placeholder="OMNI_FINANCIAL_LOCK_OVERRIDE"
            className="w-full px-3 py-2 rounded-lg text-sm outline-none"
            style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }}
          />
        </div>

        <div>
          <label className="block text-xs font-medium mb-1" style={{ color: theme.t2 }}>
            {isClose ? 'Reviewer sign-off (optional)' : 'Reopen reason'}
          </label>
          <textarea
            rows={2}
            value={reason}
            onChange={e => setReason(e.target.value)}
            placeholder={reasonPlaceholder}
            className="w-full px-3 py-2 rounded-lg text-sm outline-none resize-y"
            style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }}
          />
        </div>

        {err && (
          <div className="rounded-lg px-3 py-2 text-xs"
               style={{ background: theme.erB, color: theme.er, border: `1px solid ${theme.er}40` }}>
            {err}
          </div>
        )}

        <div className="flex items-center justify-end gap-2 pt-1">
          <button type="button" onClick={onCancel}
                  className="px-3 py-1.5 rounded-lg text-sm font-medium"
                  style={{ background: theme.g100, color: theme.text, border: `1px solid ${theme.cardBdr}` }}>
            Cancel
          </button>
          <button type="submit" disabled={busy}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-semibold disabled:opacity-50"
                  style={{ background: theme.orange, color: '#fff' }}>
            {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : (isClose ? <Lock className="w-3.5 h-3.5" /> : <Unlock className="w-3.5 h-3.5" />)}
            {busy ? 'Working…' : verb}
          </button>
        </div>
      </form>
    </div>
  )
}
