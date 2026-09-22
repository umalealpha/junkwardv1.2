'use client'

/**
 * /payroll/backlog — release an earlier month's approved incentives and
 * commissions into this month's payroll, on purpose.
 *
 * CFO, payroll.docx §5 (16-Sep-2026): the automatic feed must never push the
 * backlog. It no longer can — `payroll.period_guard` only lets it write into
 * the current month. This screen is the deliberate way through: pick a month,
 * look at every person, and release the ones that have NOT already been paid.
 *
 * The two things this page exists to make obvious:
 *   1. Money lands in THIS month, not the month it was approved in. June's
 *      payroll is posted and paid; you cannot pay it again.
 *   2. A line that already went through payroll, or that Finance settled by
 *      hand, cannot be ticked at all — and the row says which it is. A warning
 *      you can click past is not a control.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { getPayrollBacklog, releasePayrollBacklog } from '@/lib/api'
import type { BacklogPreview, BacklogRow } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { ArrowLeft, AlertTriangle, Lock, CheckCircle2, Send } from 'lucide-react'

const NAVY = '#0D1B2A'

const pula = (v: string | number | undefined) =>
  'P' + Number(v ?? 0).toLocaleString('en-BW',
    { minimumFractionDigits: 2, maximumFractionDigits: 2 })

/** The twelve months before this one — you can only release the past. */
function earlierMonths(count = 12): string[] {
  const out: string[] = []
  const d = new Date()
  for (let i = 1; i <= count; i++) {
    const m = new Date(d.getFullYear(), d.getMonth() - i, 1)
    out.push(`${m.getFullYear()}-${String(m.getMonth() + 1).padStart(2, '0')}`)
  }
  return out
}

export default function PayrollBacklogPage() {
  const router = useRouter()
  const months = useMemo(() => earlierMonths(), [])
  const [period, setPeriod] = useState(months[0])
  const [kind, setKind] = useState<'incentive' | 'commission'>('incentive')
  const [shot, setShot] = useState<BacklogPreview | null>(null)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setMsg(null)
    setPicked(new Set())
    try {
      setShot(await getPayrollBacklog(period, kind))
    } catch (e) {
      setShot(null)
      setMsg({ kind: 'err', text: (e as Error).message || 'Could not load that month.' })
    } finally {
      setLoading(false)
    }
  }, [period, kind])

  useEffect(() => { load() }, [load])

  const rows: BacklogRow[] = shot?.rows ?? []
  const releasable = rows.filter(r => r.releasable)
  const pickedTotal = rows
    .filter(r => picked.has(r.employee_id))
    .reduce((sum, r) => sum + Number(r.amount), 0)

  const toggle = (id: string) => {
    setPicked(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  const allPicked = releasable.length > 0 && releasable.every(r => picked.has(r.employee_id))
  const toggleAll = () => {
    setPicked(allPicked ? new Set() : new Set(releasable.map(r => r.employee_id)))
  }

  const release = async () => {
    if (!shot || picked.size === 0) return
    setBusy(true)
    setMsg(null)
    try {
      const out = await releasePayrollBacklog(period, kind, [...picked])
      setMsg({
        kind: 'ok',
        text: `${out.count} ${out.count === 1 ? 'person' : 'people'} released — ` +
              `${pula(out.total)} raised on the ${out.target_period} payroll. ` +
              'It sits in a pending batch until Finance applies it at the close.',
      })
      await load()
    } catch (e) {
      setMsg({ kind: 'err', text: (e as Error).message || 'The release was refused.' })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-screen bg-[#F7F8FA]">
      <TopBar />
      <div className="max-w-6xl mx-auto px-6 py-8 space-y-6">
        <button onClick={() => router.push('/payroll')}
                className="inline-flex items-center gap-2 text-sm text-[#6B7280] hover:text-[#0D1B2A]">
          <ArrowLeft className="w-4 h-4" /> Payroll
        </button>

        <div>
          <h1 className="text-2xl font-semibold" style={{ color: NAVY }}>
            Release an earlier month
          </h1>
          <p className="text-sm text-[#6B7280] mt-1 max-w-3xl">
            Incentives and commissions approved for a past month no longer reach payroll
            on their own. Pick the month, check each person against what has already been
            paid, and release the ones that are genuinely still owed. Anything you release
            is raised on{' '}
            <strong style={{ color: NAVY }}>{shot?.target_period ?? 'this month'}</strong>{' '}
            — the earlier month is closed and cannot be paid again.
          </p>
        </div>

        <Card>
          <CardContent className="py-4 flex flex-wrap items-end gap-4">
            <label className="text-sm">
              <span className="block text-xs text-[#6B7280] mb-1">Month approved</span>
              <select value={period} onChange={e => setPeriod(e.target.value)}
                      className="border rounded px-3 py-2 text-sm min-w-[10rem]">
                {months.map(m => <option key={m} value={m}>{m}</option>)}
              </select>
            </label>
            <label className="text-sm">
              <span className="block text-xs text-[#6B7280] mb-1">What</span>
              <select value={kind}
                      onChange={e => setKind(e.target.value as 'incentive' | 'commission')}
                      className="border rounded px-3 py-2 text-sm min-w-[10rem]">
                <option value="incentive">Incentives</option>
                <option value="commission">Commissions</option>
              </select>
            </label>
            <div className="ml-auto text-right">
              <p className="text-xs text-[#6B7280]">Selected</p>
              <p className="text-lg font-semibold" style={{ color: NAVY }}>
                {picked.size} · {pula(pickedTotal)}
              </p>
            </div>
            <Button onClick={release} disabled={busy || picked.size === 0 || !!shot?.blocked}
                    style={{ background: NAVY }}>
              <Send className="w-4 h-4 mr-2" />
              {busy ? 'Releasing…' : `Release into ${shot?.target_period ?? 'this month'}`}
            </Button>
          </CardContent>
        </Card>

        {msg && (
          <div className="rounded border px-4 py-3 text-sm"
               style={msg.kind === 'ok'
                 ? { background: '#D1FAE5', borderColor: '#A7F3D0', color: '#065F46' }
                 : { background: '#FEE2E2', borderColor: '#FECACA', color: '#991B1B' }}>
            {msg.text}
          </div>
        )}

        {shot?.blocked && (
          <div className="rounded border px-4 py-3 text-sm flex items-start gap-2"
               style={{ background: '#FEF3C7', borderColor: '#FDE68A', color: '#92400E' }}>
            <Lock className="w-4 h-4 mt-0.5 shrink-0" />
            <span>{shot.blocked}</span>
          </div>
        )}

        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle style={{ color: NAVY }}>
              Approved in {period}
            </CardTitle>
            {shot && (
              <span className="text-sm text-[#6B7280]">
                {shot.releasable_count} can be released · {pula(shot.releasable_total)}
                {shot.held_count > 0 && ` · ${shot.held_count} held back`}
              </span>
            )}
          </CardHeader>
          <CardContent>
            {loading ? (
              <p className="text-sm text-[#6B7280] py-8 text-center">Loading {period}…</p>
            ) : rows.length === 0 ? (
              // Not the same sentence as "nothing to release" — an empty month
              // and a month whose every line is already paid are different
              // facts, and reading one as the other sends someone hunting.
              <p className="text-sm text-[#6B7280] py-8 text-center">
                Nothing was approved for {kind === 'incentive' ? 'incentives' : 'commissions'}{' '}
                in {period}.
              </p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs uppercase text-[#6B7280] border-b">
                    <th className="py-2 w-10">
                      <input type="checkbox" checked={allPicked} onChange={toggleAll}
                             disabled={releasable.length === 0}
                             aria-label="Select everyone that can be released" />
                    </th>
                    <th className="py-2">Person</th>
                    <th className="py-2">Entity</th>
                    <th className="py-2 text-right">Amount</th>
                    <th className="py-2">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map(r => (
                    <tr key={r.employee_id}
                        className={`border-b last:border-0 ${r.releasable ? '' : 'opacity-60'}`}>
                      <td className="py-2">
                        <input type="checkbox" disabled={!r.releasable}
                               checked={picked.has(r.employee_id)}
                               onChange={() => toggle(r.employee_id)}
                               aria-label={`Release ${r.employee_name}`} />
                      </td>
                      <td className="py-2">
                        <span className="font-medium" style={{ color: NAVY }}>{r.employee_name}</span>
                        {r.employee_number && (
                          <span className="text-xs text-[#9CA3AF] ml-2">{r.employee_number}</span>
                        )}
                        {r.source_count > 1 && (
                          <span className="text-xs text-[#6B7280] ml-2">
                            ({r.source_count} lines)
                          </span>
                        )}
                      </td>
                      <td className="py-2 text-[#6B7280]">{r.company || '—'}</td>
                      <td className="py-2 text-right font-semibold" style={{ color: NAVY }}>
                        {pula(r.amount)}
                      </td>
                      <td className="py-2">
                        {r.releasable ? (
                          <span className="inline-flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded"
                                style={{ background: '#D1FAE5', color: '#065F46' }}>
                            <CheckCircle2 className="w-3 h-3" /> still owed
                          </span>
                        ) : (
                          <span className="inline-flex items-start gap-1 text-xs font-semibold px-2 py-0.5 rounded"
                                style={{ background: '#FEE2E2', color: '#991B1B' }}>
                            <AlertTriangle className="w-3 h-3 mt-0.5 shrink-0" />
                            {r.duplicate_reason}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </CardContent>
        </Card>

        <p className="text-xs text-[#9CA3AF] max-w-3xl">
          Releasing raises a pending payroll amendment. It is not a payment: Finance still
          applies the batch at the monthly close, sign-off still approves it, and the money
          only moves when the CFO authorises it in the bank. Every release is recorded
          against your name with the month it came from.
        </p>
      </div>
    </div>
  )
}
