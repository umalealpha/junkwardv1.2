'use client'

/**
 * Tax Calendar — statutory compliance board (VAT / PAYE / OWHT / SAT).
 *
 * Rebuilt 2026-09-11 from Oprah Mogomotsi's feature request. What changed:
 *
 * 1. The dates come from the SERVER now. This page used to compute its own
 *    deadlines in `deadlines.ts`, on the pre-2026 rules, and every one of them
 *    was wrong — VAT on the last day of the month instead of the 25th, PAYE on
 *    the 15th instead of the 14th, two company-tax payments instead of four.
 *    All of it now lives in regulatory/tax_calendar.py with tests pinning each
 *    date. There is deliberately NO date arithmetic left on this side.
 *
 * 2. It is a workflow, not a list. Each filing carries an owner, a completion
 *    step and a CFO verification step, and the reminder keeps going until the
 *    CFO closes it — marking your own filing done does not silence anything.
 */

import { useCallback, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  AlertTriangle,
  CalendarDays,
  CheckCircle2,
  Clock,
  Loader2,
  ShieldAlert,
  UserRound,
} from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { Card } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import {
  changeTaxDueDate,
  closeTaxBreach,
  getTaxCalendar,
  markTaxTaskComplete,
  verifyTaxTask,
  type TaxCalendarResponse,
  type TaxComplianceTask,
  type TaxTaskStatus,
} from '@/lib/api'

// ─── Presentation ─────────────────────────────────────────────────────────────

const statusStyle: Record<TaxTaskStatus, { badge: string; icon: React.ReactNode }> = {
  scheduled:         { badge: 'bg-[#F3F4F6] text-[#4B5563] border border-[#E5E7EB]', icon: <CalendarDays className="w-3.5 h-3.5" /> },
  reminding:         { badge: 'bg-[#FFF7ED] text-[#CC6C00] border border-[#FED7AA]', icon: <Clock className="w-3.5 h-3.5" /> },
  preparer_complete: { badge: 'bg-[#EFF6FF] text-[#2563EB] border border-[#DBEAFE]', icon: <UserRound className="w-3.5 h-3.5" /> },
  verified:          { badge: 'bg-[#ECFDF5] text-[#059669] border border-[#D1FAE5]', icon: <CheckCircle2 className="w-3.5 h-3.5" /> },
  late:              { badge: 'bg-[#FFFBEB] text-[#B45309] border border-[#FDE68A]', icon: <AlertTriangle className="w-3.5 h-3.5" /> },
  breach:            { badge: 'bg-[#FEF2F2] text-[#B3261E] border border-[#FEE2E2]', icon: <ShieldAlert className="w-3.5 h-3.5" /> },
}

function fmt(iso: string): string {
  return new Date(iso + 'T00:00:00').toLocaleDateString('en-GB', {
    day: 'numeric', month: 'short', year: 'numeric',
  })
}

/** Plain English for the days column. The number alone reads as noise. */
function whenText(t: TaxComplianceTask): string {
  const d = t.days_to_due
  if (t.status === 'breach') return `${Math.abs(d)} day${Math.abs(d) === 1 ? '' : 's'} overdue`
  if (t.status === 'verified' || t.status === 'late') return 'Closed'
  if (d === 0) return 'Due today'
  if (d < 0) return `${Math.abs(d)} days past`
  return `in ${d} day${d === 1 ? '' : 's'}`
}

type Tab = 'open' | 'breach' | 'awaiting' | 'all'

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function TaxCalendarPage() {
  const router = useRouter()
  const [data, setData] = useState<TaxCalendarResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>('open')
  const [busyId, setBusyId] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setData(await getTaxCalendar(12))
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load the tax calendar.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!localStorage.getItem('alpha_token')) {
      router.push('/login')
      return
    }
    void load()
  }, [router, load])

  const say = (msg: string) => {
    setToast(msg)
    setTimeout(() => setToast(null), 4000)
  }

  const act = async (id: string, fn: () => Promise<unknown>, done: string) => {
    setBusyId(id)
    try {
      await fn()
      await load()
      say(done)
    } catch (e) {
      say(e instanceof Error ? e.message : 'That did not go through.')
    } finally {
      setBusyId(null)
    }
  }

  const onComplete = (t: TaxComplianceTask) => {
    const note = window.prompt(
      `Mark "${t.label}" as filed.\n\nWhat was filed, and the BURS reference if you have one:`, '')
    if (note === null) return
    void act(t.id, () => markTaxTaskComplete(t.id, note),
      'Marked complete. It stays on the board until the CFO verifies it.')
  }

  const onVerify = (t: TaxComplianceTask) => {
    // Past the internal target but before the due date: the CFO is asked why,
    // because that is a service miss worth recording even though nothing was
    // legally breached.
    const lateWindow = t.completed_at
      ? new Date(t.completed_at) > new Date(t.target_date + 'T23:59:59')
      : new Date() > new Date(t.target_date + 'T23:59:59')
    let lateReason = ''
    if (lateWindow) {
      const r = window.prompt(
        `This was finished after the ${fmt(t.target_date)} internal target.\n\nWhy was it late?`, '')
      if (r === null) return
      lateReason = r
    }
    const note = window.prompt('Verification note (optional):', '') ?? ''
    void act(t.id, () => verifyTaxTask(t.id, note, lateReason), 'Verified and closed.')
  }

  const onChangeDate = (t: TaxComplianceTask) => {
    const raw = window.prompt(
      `Change the due date for "${t.label}".

` +
      `Currently ${fmt(t.due_date)}. New date as YYYY-MM-DD:`, t.due_date)
    if (raw === null) return
    const reason = window.prompt(
      'Why is the date changing? The CFO is told about every change.', '')
    if (reason === null) return
    if (!reason.trim()) { say('A date change needs a written reason.'); return }
    void act(t.id, () => changeTaxDueDate(t.id, raw.trim(), reason),
      'Date changed. The CFO has been notified.')
  }

  const onCloseBreach = (t: TaxComplianceTask) => {
    const note = window.prompt(
      `BREACH — the ${fmt(t.due_date)} statutory deadline was missed.\n\n` +
      'This goes in the compliance file. Write what happened and what was done about it:', '')
    if (note === null) return
    if (!note.trim()) { say('A breach cannot be closed without a written reason.'); return }
    void act(t.id, () => closeTaxBreach(t.id, note), 'Breach closed and recorded.')
  }

  const tasks = data?.tasks ?? []
  const shown = tasks.filter((t) => {
    if (tab === 'all') return true
    if (tab === 'breach') return t.status === 'breach'
    if (tab === 'awaiting') return t.status === 'preparer_complete'
    return t.is_open
  })

  const tabs: { key: Tab; label: string; count: number }[] = [
    { key: 'open',     label: 'Open',              count: data?.summary.open ?? 0 },
    { key: 'breach',   label: 'Breach',            count: data?.summary.breach ?? 0 },
    { key: 'awaiting', label: 'Awaiting CFO',      count: data?.summary.awaiting_cfo ?? 0 },
    { key: 'all',      label: 'All',               count: data?.summary.total ?? 0 },
  ]

  return (
    <div className="min-h-screen bg-[#FAFAFA]">
      <TopBar />

      <div className="max-w-6xl mx-auto px-6 py-8">
        <header className="mb-6">
          <h1 className="text-[22px] font-semibold text-[#0D1B2A]">Statutory Tax Compliance</h1>
          <p className="text-[13px] text-[#4B5563] mt-1">
            VAT, PAYE, OWHT and company tax. Every filing is reminded 10 days ahead, and
            stays open until the CFO has verified it.
            {data ? <span className="ml-1">VAT cycle: {data.vat_cycle}.</span> : null}
          </p>
        </header>

        {data && data.summary.breach > 0 && (
          <div className="mb-5 rounded-lg border border-[#FEE2E2] bg-[#FEF2F2] px-4 py-3 flex items-start gap-3">
            <ShieldAlert className="w-5 h-5 text-[#B3261E] mt-0.5 shrink-0" />
            <div className="text-[13px] text-[#7F1D1D]">
              <strong>{data.summary.breach} statutory deadline{data.summary.breach === 1 ? '' : 's'} missed.</strong>{' '}
              This carries BURS penalty and interest exposure. Only the CFO can close these,
              and only with a written reason.
            </div>
          </div>
        )}

        {data && data.summary.unassigned > 0 && (
          <div className="mb-5 rounded-lg border border-[#FED7AA] bg-[#FFF7ED] px-4 py-3 text-[13px] text-[#7C2D12]">
            <strong>{data.summary.unassigned} filing{data.summary.unassigned === 1 ? ' has' : 's have'} no owner.</strong>{' '}
            Nobody is being reminded about {data.summary.unassigned === 1 ? 'it' : 'them'}.
          </div>
        )}

        <div className="flex gap-1 mb-4">
          {tabs.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={cn(
                'px-3.5 py-1.5 rounded-md text-[13px] transition-colors',
                tab === t.key
                  ? 'bg-[#0D1B2A] text-white'
                  : 'text-[#4B5563] hover:bg-[#F3F4F6]',
              )}
            >
              {t.label}
              <span className={cn('ml-1.5 text-[11px]', tab === t.key ? 'text-white/60' : 'text-[#6B7280]')}>
                {t.count}
              </span>
            </button>
          ))}
        </div>

        {loading && (
          <div className="flex items-center gap-2 text-[13px] text-[#4B5563] py-12 justify-center">
            <Loader2 className="w-4 h-4 animate-spin" /> Loading the calendar…
          </div>
        )}

        {error && (
          <Card className="p-5 text-[13px] text-[#B3261E]">{error}</Card>
        )}

        {!loading && !error && shown.length === 0 && (
          <Card className="p-10 text-center text-[13px] text-[#4B5563]">
            Nothing here. {tab === 'open' ? 'No filings are currently open.' : ''}
          </Card>
        )}

        <div className="space-y-2">
          {shown.map((t) => {
            const s = statusStyle[t.status]
            const busy = busyId === t.id
            return (
              <Card key={t.id} className="px-5 py-4">
                <div className="flex items-start justify-between gap-4 flex-wrap">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-[14px] font-medium text-[#0D1B2A]">{t.tax_type_label}</span>
                      <span className="text-[13px] text-[#4B5563]">{t.period_label}</span>
                      <span className={cn('inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px]', s.badge)}>
                        {s.icon}{t.status_display}
                      </span>
                    </div>
                    <div className="text-[12px] text-[#4B5563] mt-1.5">
                      Due <strong className="text-[#374151]">{fmt(t.due_date)}</strong>
                      <span className="mx-1.5">·</span>
                      Prepare by {fmt(t.target_date)}
                      <span className="mx-1.5">·</span>
                      {whenText(t)}
                      {t.owner_detail && (<><span className="mx-1.5">·</span>{t.owner_detail.name}</>)}
                      {!t.owner_detail && (<><span className="mx-1.5">·</span><span className="text-[#B3261E]">no owner</span></>)}
                    </div>
                    {t.breach_note && (
                      <div className="text-[12px] text-[#7F1D1D] mt-2 bg-[#FEF2F2] rounded px-2.5 py-1.5">
                        {t.breach_note}
                      </div>
                    )}
                    {t.original_due_date && (
                      <div className="text-[12px] text-[#7C2D12] mt-2 bg-[#FFF7ED] rounded px-2.5 py-1.5">
                        Date moved from {fmt(t.original_due_date)}.
                        {/* Only the LATEST change is kept on the row, so say so —
                            attributing the original move to whoever moved it last
                            would be a quiet lie. Every move is emailed to the CFO. */}
                        {t.date_changed_by_name ? ` Last changed by ${t.date_changed_by_name}` : ''}
                        {t.date_change_reason ? ` — ${t.date_change_reason}` : ''}
                      </div>
                    )}
                    {t.late_reason && !t.breach_note && (
                      <div className="text-[12px] text-[#7C2D12] mt-2">Late: {t.late_reason}</div>
                    )}
                  </div>

                  <div className="flex items-center gap-2 shrink-0">
                    {busy && <Loader2 className="w-4 h-4 animate-spin text-[#6B7280]" />}
                    {!busy && t.status === 'breach' && data?.can_verify && (
                      <button
                        onClick={() => onCloseBreach(t)}
                        className="px-3 py-1.5 rounded-md text-[12px] bg-[#B3261E] text-white hover:bg-[#8C1D18]"
                      >
                        Close breach
                      </button>
                    )}
                    {!busy && data?.can_edit_dates && t.is_open && t.status !== 'breach' && (
                      <button
                        onClick={() => onChangeDate(t)}
                        className="px-3 py-1.5 rounded-md text-[12px] text-[#4B5563] hover:bg-[#F3F4F6]"
                      >
                        Change date
                      </button>
                    )}
                    {!busy && (t.status === 'reminding' || t.status === 'scheduled') && (
                      <button
                        onClick={() => onComplete(t)}
                        className="px-3 py-1.5 rounded-md text-[12px] border border-[#D1D5DB] text-[#374151] hover:bg-[#F9FAFB]"
                      >
                        Mark filed
                      </button>
                    )}
                    {!busy && t.status === 'preparer_complete' && data?.can_verify && (
                      <button
                        onClick={() => onVerify(t)}
                        className="px-3 py-1.5 rounded-md text-[12px] bg-[#0D1B2A] text-white hover:bg-[#16283d]"
                      >
                        Verify &amp; close
                      </button>
                    )}
                    {!busy && t.status === 'preparer_complete' && !data?.can_verify && (
                      <span className="text-[12px] text-[#4B5563]">waiting on the CFO</span>
                    )}
                  </div>
                </div>
              </Card>
            )
          })}
        </div>
      </div>

      {toast && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 bg-[#0D1B2A] text-white text-[13px] px-4 py-2.5 rounded-lg shadow-lg">
          {toast}
        </div>
      )}
    </div>
  )
}
