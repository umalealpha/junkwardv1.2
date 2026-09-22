'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { getPayslips, getPayrollPeriods, getToken, apiFetchBinary, sendPayslipEmail, sendPayslipsBatch } from '@/lib/api'
import type { Payslip, PayrollPeriod } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Search, AlertCircle, ChevronLeft, ChevronRight, FileText, Building2, Download, Send, CheckCircle2 } from 'lucide-react'
import { useCompany } from '@/contexts/CompanyContext'
import SmartUpload from '@/components/SmartUpload'

const PAGE_SIZE = 25

function fmt(v: string | number): string {
  const n = typeof v === 'string' ? parseFloat(v) : v
  if (Number.isNaN(n)) return '—'
  return new Intl.NumberFormat('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n)
}

/** Blank rather than 0.00 for an earning the employee didn't get this month —
 *  a column of zeros reads as "broken" to Finance. */
function fmtOrDash(v: number): string {
  return v === 0 ? '—' : fmt(v)
}

/** Amount of one payslip component, from the lines the API already returns. */
function lineAmount(p: Payslip, code: string): number {
  const line = p.lines?.find(l => l.component_code === code)
  return line ? parseFloat(line.amount) || 0 : 0
}

export default function PayslipsPage() {
  const router = useRouter()
  // CFO directive 2026-05-18: payroll views must expose an inline entity
  // filter so the operator can hop between ADIC / RSA / etc. without
  // leaving the page. Writing to the same CompanyContext keeps the rest
  // of Omni in sync.
  const { selectedId, setSelectedId, companies, loaded: companiesLoaded } = useCompany()
  const [items, setItems] = useState<Payslip[]>([])
  const [periods, setPeriods] = useState<PayrollPeriod[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [periodId, setPeriodId] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [page, setPage] = useState(1)
  const [count, setCount] = useState(0)
  const [downloading, setDownloading] = useState(false)
  // Send-payslip selection (HR: Unami, Dorothy; Finance). Keyed by payslip id.
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [sending, setSending] = useState(false)
  const [rowSending, setRowSending] = useState<string | null>(null)
  const [sendMsg, setSendMsg] = useState<{ kind: 'ok' | 'warn' | 'err'; text: string } | null>(null)

  const toggleOne = (id: string) => {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }
  const allOnPageSelected = items.length > 0 && items.every(p => selected.has(p.id))
  const toggleAllOnPage = () => {
    setSelected(prev => {
      const next = new Set(prev)
      if (allOnPageSelected) items.forEach(p => next.delete(p.id))
      else items.forEach(p => next.add(p.id))
      return next
    })
  }

  async function onSendOne(id: string) {
    setRowSending(id); setSendMsg(null); setError(null)
    try {
      const r = await sendPayslipEmail(id)
      if (r.sent) setSendMsg({ kind: 'ok', text: `Payslip sent to ${r.email}.` })
      else setSendMsg({ kind: 'warn', text: r.detail || 'Could not send this payslip.' })
    } catch (e) {
      setSendMsg({ kind: 'err', text: e instanceof Error ? e.message : 'Send failed.' })
    } finally { setRowSending(null) }
  }

  async function onSendSelected() {
    const ids = Array.from(selected)
    if (ids.length === 0) { setSendMsg({ kind: 'warn', text: 'Tick at least one employee first.' }); return }
    setSending(true); setSendMsg(null); setError(null)
    try {
      const r = await sendPayslipsBatch(ids)
      const parts = [`${r.sent_count} sent`]
      if (r.failed_count) parts.push(`${r.failed_count} skipped`)
      const firstReason = r.failed[0]?.detail
      setSendMsg({
        kind: r.failed_count ? 'warn' : 'ok',
        text: `${parts.join(', ')}.` + (firstReason ? ` First skip: ${firstReason}` : ''),
      })
      setSelected(new Set())
    } catch (e) {
      setSendMsg({ kind: 'err', text: e instanceof Error ? e.message : 'Send failed.' })
    } finally { setSending(false) }
  }

  // Download the selected period's payroll (per-employee + totals) to Excel so
  // the team can reconcile Omni against their own records. Requires a period.
  async function onDownload() {
    if (!periodId) { setError('Pick a period first, then download.'); return }
    setDownloading(true); setError(null)
    try {
      const qs = new URLSearchParams({ period: periodId })
      if (selectedId) qs.set('company', selectedId)
      const r = await apiFetchBinary(`/payroll/export/?${qs.toString()}`)
      if (!r.ok) {
        const j = await r.json().catch(() => ({}))
        setError(j.detail || `Download failed (HTTP ${r.status})`); return
      }
      const blob = await r.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      const pname = periods.find(p => p.id === periodId)?.period_name || 'period'
      a.href = url; a.download = `payroll_${pname}.xlsx`
      document.body.appendChild(a); a.click(); a.remove()
      URL.revokeObjectURL(url)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Download failed')
    } finally { setDownloading(false) }
  }

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const res = await getPayslips({
        page, period: periodId || undefined,
        status: statusFilter || undefined, search: search || undefined,
      })
      setItems(res.results); setCount(res.count)
    } catch (err) { setError(err instanceof Error ? err.message : 'Failed to load') }
    finally { setLoading(false) }
  }, [page, periodId, search, statusFilter])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    getPayrollPeriods().then(r => setPeriods(r.results)).catch(() => {})
    load()
    // Reload when the selected company changes. apiFetch auto-injects
    // company=<id> on every GET so we don't pass it explicitly here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load, selectedId])

  const totalPages = Math.max(1, Math.ceil(count / PAGE_SIZE))

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar title="Payslips" breadcrumbs={[{ label: 'Payroll' }, { label: 'Payslips' }]} />

      <div className="flex-1 p-6 space-y-4">
        <SmartUpload section="payroll" onCommitted={load} />
        <div className="flex gap-3 items-end flex-wrap">
          <div className="relative flex-1 max-w-sm min-w-[200px]">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[#9CA3AF] pointer-events-none" strokeWidth={1.5} />
            <input type="text" placeholder="Search by employee or department..."
              value={search} onChange={e => { setSearch(e.target.value); setPage(1) }}
              className="w-full h-10 bg-white border border-[#D1D5DB] rounded-md pl-9 pr-3 text-sm" />
          </div>
          <div className="relative">
            <Building2 className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[#9CA3AF] pointer-events-none" strokeWidth={1.5} />
            <select
              value={selectedId || ''}
              onChange={e => { setSelectedId(e.target.value || null); setPage(1) }}
              className="h-10 bg-white border border-[#D1D5DB] rounded-md pl-9 pr-3 text-sm min-w-[180px]"
              disabled={!companiesLoaded}
            >
              <option value="">All companies</option>
              {companies.map(c => (
                <option key={c.id} value={c.id}>{c.code || c.name}</option>
              ))}
            </select>
          </div>
          <select value={periodId} onChange={e => { setPeriodId(e.target.value); setPage(1) }}
            className="h-10 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm">
            <option value="">All periods</option>
            {periods.map(p => <option key={p.id} value={p.id}>{p.period_name}</option>)}
          </select>
          <select value={statusFilter} onChange={e => { setStatusFilter(e.target.value); setPage(1) }}
            className="h-10 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm">
            <option value="">All statuses</option>
            <option value="draft">Draft</option>
            <option value="approved">Approved</option>
            <option value="paid">Paid</option>
            <option value="cancelled">Cancelled</option>
          </select>
          <Button variant="outline" size="sm" onClick={onDownload}
                  disabled={downloading || !periodId}
                  leftIcon={<Download className="w-3.5 h-3.5" />}
                  title={!periodId ? 'Pick a period first' : 'Download this period to Excel'}>
            {downloading ? 'Preparing…' : 'Download Excel'}
          </Button>
          <Button size="sm" onClick={onSendSelected}
                  disabled={sending || selected.size === 0}
                  leftIcon={<Send className="w-3.5 h-3.5" />}
                  title={selected.size === 0 ? 'Tick employees to send their payslip' : `Email ${selected.size} payslip(s)`}>
            {sending ? 'Sending…' : `Send payslip${selected.size > 1 ? 's' : ''}${selected.size ? ` (${selected.size})` : ''}`}
          </Button>
        </div>

        {sendMsg && (
          <div className={`rounded-lg p-3 flex items-center gap-2 border ${
            sendMsg.kind === 'ok' ? 'bg-[#F0FDF4] border-[#DCFCE7]'
            : sendMsg.kind === 'warn' ? 'bg-[#FFFBEB] border-[#FEF3C7]'
            : 'bg-[#FEF2F2] border-[#FEE2E2]'}`}>
            {sendMsg.kind === 'ok'
              ? <CheckCircle2 className="w-4 h-4 text-[#16A34A] shrink-0" />
              : <AlertCircle className={`w-4 h-4 shrink-0 ${sendMsg.kind === 'warn' ? 'text-[#B45309]' : 'text-[#DC2626]'}`} />}
            <p className={`text-sm ${sendMsg.kind === 'ok' ? 'text-[#166534]' : sendMsg.kind === 'warn' ? 'text-[#92400E]' : 'text-[#DC2626]'}`}>{sendMsg.text}</p>
          </div>
        )}

        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-[#DC2626]" />
            <p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}

        <Card>
          <CardContent className="p-0">
            {loading ? <p className="px-6 py-8 text-center text-sm text-[#6B7280]">Loading…</p>
            : items.length === 0 ? (
              <div className="px-6 py-12 text-center text-[#6B7280]">
                <FileText className="w-10 h-10 mx-auto mb-3 text-[#D1D5DB]" strokeWidth={1.5} />
                <p className="text-sm">No payslips yet.</p>
                <p className="text-xs text-[#9CA3AF] mt-1">Import a previous-period payroll via the API or run a fresh period.</p>
              </div>
            ) : (
              <>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm border-collapse">
                    <thead className="bg-[#F3F4F6] border-b-2 border-[#E5E7EB]">
                      <tr>
                        <th className="px-4 py-3 text-left w-10">
                          <input type="checkbox" aria-label="Select all on this page"
                            checked={allOnPageSelected} onChange={toggleAllOnPage}
                            className="w-4 h-4 align-middle cursor-pointer" />
                        </th>
                        <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase">Employee</th>
                        <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase hidden md:table-cell">Department</th>
                        <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase">Period</th>
                        {/* Commission + Incentive are material earnings and the CFO's
                            register carries them in every run, but this table only
                            showed the four totals — so they were invisible even once
                            committed (bug report Pako Kago 2026-07-29, problem 2). */}
                        <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase hidden lg:table-cell">Commission</th>
                        <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase hidden lg:table-cell">Incentive</th>
                        <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase">Gross</th>
                        <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase hidden md:table-cell">PAYE</th>
                        <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase">Net</th>
                        {/* CTC column removed (CFO / Pako Kago directive 2026-07-29):
                            cost-to-company is not shown on the payslip register. */}
                        <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase">Status</th>
                        <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase">Send</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[#E5E7EB] bg-white">
                      {items.map(p => (
                        <tr key={p.id} className={selected.has(p.id) ? 'bg-[#F5F8FF]' : undefined}>
                          <td className="px-4 py-3">
                            <input type="checkbox" aria-label={`Select ${p.employee_name}`}
                              checked={selected.has(p.id)} onChange={() => toggleOne(p.id)}
                              className="w-4 h-4 align-middle cursor-pointer" />
                          </td>
                          <td className="px-4 py-3 font-medium text-[#111827]">{p.employee_name}</td>
                          <td className="px-4 py-3 text-[#374151] hidden md:table-cell">{p.department || '—'}</td>
                          <td className="px-4 py-3 text-[#374151] font-mono">{p.period_name}</td>
                          <td className="px-4 py-3 text-right tabular-nums hidden lg:table-cell">{fmtOrDash(lineAmount(p, 'COMMISSION'))}</td>
                          <td className="px-4 py-3 text-right tabular-nums hidden lg:table-cell">{fmtOrDash(lineAmount(p, 'INCENTIVE'))}</td>
                          <td className="px-4 py-3 text-right tabular-nums">{fmt(p.gross_amount)}</td>
                          <td className="px-4 py-3 text-right tabular-nums hidden md:table-cell text-[#B91C1C]">{fmt(p.paye_amount)}</td>
                          <td className="px-4 py-3 text-right tabular-nums font-medium">{fmt(p.net_amount)}</td>
                          <td className="px-4 py-3"><span className="text-xs">{p.status_display}</span></td>
                          <td className="px-4 py-3 text-right">
                            <Button variant="outline" size="sm" onClick={() => onSendOne(p.id)}
                              disabled={rowSending === p.id || sending}
                              leftIcon={<Send className="w-3.5 h-3.5" />}
                              title={`Email this payslip to ${p.employee_name}`}>
                              {rowSending === p.id ? 'Sending…' : 'Send'}
                            </Button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {totalPages > 1 && (
                  <div className="flex items-center justify-between px-4 py-3 border-t border-[#E5E7EB]">
                    <span className="text-xs text-[#6B7280]">Page {page} of {totalPages} — {count} total</span>
                    <div className="flex gap-2">
                      <Button variant="outline" size="sm" onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1} leftIcon={<ChevronLeft className="w-3.5 h-3.5" />}>Prev</Button>
                      <Button variant="outline" size="sm" onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page >= totalPages} rightIcon={<ChevronRight className="w-3.5 h-3.5" />}>Next</Button>
                    </div>
                  </div>
                )}
              </>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
