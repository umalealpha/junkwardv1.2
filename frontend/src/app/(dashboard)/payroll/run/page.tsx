'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import {
  getPayrollPeriods, getMe, getToken, getCompanies,
  previewPayrollImport, commitPayrollImport, createPayrollPeriod,
  listPayrollImportBatches, approvePayrollImport, discardPayrollImport,
} from '@/lib/api'
import type { PayrollPeriod, UserProfile, Company, PayrollImportBatch } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { ArrowLeft, Calendar, Lock, AlertTriangle, Upload, Globe, CheckCircle2, Plus } from 'lucide-react'

// Suggest the month AFTER the latest existing period (or the current month if
// there are none) so "New period" pre-fills the obvious next window.
function suggestNextPeriod(periods: PayrollPeriod[]): { period_name: string; start_date: string; end_date: string; pay_date: string } {
  const pad = (n: number) => String(n).padStart(2, '0')
  let year: number, month: number // month is 1-12
  const latest = periods[0]?.period_name // periods come newest-first ("2026-06")
  if (latest && /^\d{4}-\d{2}$/.test(latest)) {
    const [y, m] = latest.split('-').map(Number)
    year = m === 12 ? y + 1 : y
    month = m === 12 ? 1 : m + 1
  } else {
    const now = new Date()
    year = now.getFullYear(); month = now.getMonth() + 1
  }
  const lastDay = new Date(year, month, 0).getDate() // day 0 of next month = last day of this one
  return {
    period_name: `${year}-${pad(month)}`,
    start_date:  `${year}-${pad(month)}-01`,
    end_date:    `${year}-${pad(month)}-${pad(lastDay)}`,
    pay_date:    `${year}-${pad(month)}-25`,
  }
}

export default function RunPayrollPage() {
  const router = useRouter()
  const [me, setMe] = useState<UserProfile | null>(null)
  const [periods, setPeriods] = useState<PayrollPeriod[]>([])
  const [companies, setCompanies] = useState<Company[]>([])
  const [pendingBatches, setPendingBatches] = useState<PayrollImportBatch[]>([])
  const [batchBusy, setBatchBusy] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  // uploader state
  const [companyId, setCompanyId] = useState('')
  const [periodId, setPeriodId] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [batch, setBatch] = useState<PayrollImportBatch | null>(null)
  const [msg, setMsg] = useState<{ kind: 'ok' | 'err' | 'info'; text: string } | null>(null)

  // new-period form
  const [showNewPeriod, setShowNewPeriod] = useState(false)
  const [np, setNp] = useState({ period_name: '', start_date: '', end_date: '', pay_date: '' })
  const [npBusy, setNpBusy] = useState(false)
  const [npErr, setNpErr] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [m, p, c, b] = await Promise.all([
        getMe().catch(() => null),
        getPayrollPeriods(),
        getCompanies({ is_active: 'true' }).catch(() => ({ results: [] as Company[] })),
        listPayrollImportBatches().catch(() => ({ results: [] as PayrollImportBatch[] })),
      ])
      setMe(m); setPeriods(p.results); setCompanies(c.results)
      // Only batches still needing action (draft / partially approved / approved-not-yet-committed).
      const ACTIVE = ['draft', 'partially_approved', 'approved']
      setPendingBatches((b.results || []).filter(x => ACTIVE.includes(x.status)))
    } finally { setLoading(false) }
  }, [])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load])

  const canRun = !!me?.can_approve_journal_entries        // create periods, upload (Finance)
  const isProcessor = !!me?.is_payroll_processor          // entity payroll processor (e.g. Veritas)
  const canUpload = canRun || isProcessor                 // who may upload/preview a file
  const canApprovePayroll = !!me?.can_approve_payroll      // sign-off (HR Mgr / Finance Mgr / CFO)
  const canCommit = canApprovePayroll || isProcessor       // finalise into payslips
  const selectedCompany = companies.find(c => c.id === companyId)
  const foreignCcy = selectedCompany && selectedCompany.base_currency && selectedCompany.base_currency !== 'BWP'
    ? selectedCompany.base_currency : null

  const onPreview = async () => {
    if (!file || !periodId) { setMsg({ kind: 'err', text: 'Pick a period and a file first.' }); return }
    setBusy(true); setMsg(null); setBatch(null)
    try {
      const b = await previewPayrollImport(file, periodId, companyId || undefined)
      setBatch(b)
      if (b.rows_invalid > 0) {
        setMsg({ kind: 'err', text: `${b.rows_invalid} of ${b.rows_total} rows have errors — fix the file and re-upload.` })
      } else {
        setMsg({ kind: 'ok', text: `Read ${b.rows_valid} valid rows. It's listed below under "Payroll imports awaiting action" — a different manager approves it there, then commit.` })
      }
    } catch (e) {
      setMsg({ kind: 'err', text: e instanceof Error ? e.message : 'Upload failed.' })
    } finally { setBusy(false) }
  }

  const openNewPeriod = () => {
    setNp(suggestNextPeriod(periods))
    setNpErr(null)
    setShowNewPeriod(true)
  }

  const onCreatePeriod = async () => {
    if (!np.period_name || !np.start_date || !np.end_date) {
      setNpErr('Period name, start date and end date are required.'); return
    }
    setNpBusy(true); setNpErr(null)
    try {
      const created = await createPayrollPeriod({
        period_name: np.period_name.trim(),
        start_date:  np.start_date,
        end_date:    np.end_date,
        pay_date:    np.pay_date || undefined,
        status:      'open',
      })
      await load()
      setPeriodId(created.id)          // auto-select the new period for the upload
      setShowNewPeriod(false)
      setMsg({ kind: 'ok', text: `Payroll period ${created.period_name} created and opened.` })
    } catch (e) {
      // Most common: duplicate period_name, or not an approver title.
      setNpErr(e instanceof Error ? e.message : 'Could not create the period.')
    } finally { setNpBusy(false) }
  }

  // Approve / commit a pending batch straight from the list (a second manager
  // finds the uploaded batch here and signs it off — the person who uploaded
  // it cannot approve their own).
  const onApproveBatch = async (id: string) => {
    setBatchBusy(id); setMsg(null)
    try {
      await approvePayrollImport(id)
      setMsg({ kind: 'ok', text: 'Payroll batch approved. It can now be committed.' })
      await load()
    } catch (e) {
      setMsg({ kind: 'err', text: e instanceof Error ? e.message : 'Approval failed.' })
    } finally { setBatchBusy(null) }
  }

  // Discard a wrong draft upload so it can be replaced. Allowed for the person
  // who uploaded it (they can't approve their own) or any approver.
  const onDiscardBatch = async (id: string, fileName?: string) => {
    if (!window.confirm(`Remove the draft upload "${fileName || 'this file'}"? This deletes the pending import so you can upload the corrected file. It does not affect any committed payroll.`)) return
    setBatchBusy(id); setMsg(null)
    try {
      await discardPayrollImport(id)
      setMsg({ kind: 'ok', text: 'Draft upload removed. You can now upload the corrected file above.' })
      await load()
    } catch (e) {
      setMsg({ kind: 'err', text: e instanceof Error ? e.message : 'Could not remove the draft.' })
    } finally { setBatchBusy(null) }
  }

  const onCommitBatch = async (id: string) => {
    setBatchBusy(id); setMsg(null)
    try {
      const res = await commitPayrollImport(id)
      setMsg({ kind: 'ok', text: `Done — ${res.created} payslips created, ${res.skipped_duplicates} skipped (already there).` })
      await load()
    } catch (e) {
      setMsg({ kind: 'info', text: e instanceof Error ? e.message : 'Commit failed.' })
    } finally { setBatchBusy(null) }
  }

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Run Payroll"
        breadcrumbs={[{ label: 'Payroll' }, { label: 'Run' }]}
        actions={
          <Button variant="ghost" size="sm" leftIcon={<ArrowLeft className="w-3.5 h-3.5" />} onClick={() => router.push('/payroll/payslips')}>Back</Button>
        }
      />
      <div className="flex-1 p-6 max-w-3xl space-y-4">
        {!canUpload && me && (
          <Card><CardContent className="p-4 flex items-start gap-2">
            <Lock className="w-4 h-4 text-[#92400E] flex-shrink-0 mt-0.5" />
            <p className="text-sm text-[#92400E]">Uploading payroll is restricted to CFO / Finance Manager / Financial Controller, or a designated entity payroll processor.</p>
          </CardContent></Card>
        )}

        {/* ── Upload payslips from file ─────────────────────────────── */}
        <Card>
          <CardHeader><CardTitle className="flex items-center gap-2"><Upload className="w-4 h-4 text-[#F07F00]" /> Upload payroll file</CardTitle></CardHeader>
          <CardContent className="p-4 space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <label className="space-y-1">
                <span className="text-xs font-semibold uppercase text-[#6B7280]">Company</span>
                <select
                  className="w-full border border-[#E5E7EB] rounded px-2 py-1.5 text-sm bg-white"
                  value={companyId} onChange={e => setCompanyId(e.target.value)} disabled={busy}
                >
                  <option value="">— select company —</option>
                  {companies.map(c => (
                    <option key={c.id} value={c.id}>{c.code} — {c.name}{c.base_currency && c.base_currency !== 'BWP' ? ` (${c.base_currency})` : ''}</option>
                  ))}
                </select>
              </label>
              <label className="space-y-1">
                <span className="text-xs font-semibold uppercase text-[#6B7280]">Pay period</span>
                <select
                  className="w-full border border-[#E5E7EB] rounded px-2 py-1.5 text-sm bg-white"
                  value={periodId} onChange={e => setPeriodId(e.target.value)} disabled={busy}
                >
                  <option value="">— select period —</option>
                  {periods.map(p => (
                    <option key={p.id} value={p.id}>{p.period_name}</option>
                  ))}
                </select>
              </label>
            </div>

            {foreignCcy && (
              <div className="bg-[#EFF6FF] border border-[#BFDBFE] rounded p-3 flex items-start gap-2 text-xs text-[#1E40AF]">
                <Globe className="w-4 h-4 flex-shrink-0 mt-0.5" />
                <p>
                  <strong>{selectedCompany?.code} is paid in {foreignCcy}.</strong> Upload the amounts in {foreignCcy}
                  {' '}(columns: Gross, PAYE, Net Salary). Payslips will print in {foreignCcy}; omni converts to BWP
                  at the approved {foreignCcy}→BWP rate for group reporting. No Botswana PAYE is applied.
                </p>
              </div>
            )}

            <label className="space-y-1 block">
              <span className="text-xs font-semibold uppercase text-[#6B7280]">File (CSV / XLSX)</span>
              <input
                type="file" accept=".csv,.xlsx,.xlsm"
                onChange={e => setFile(e.target.files?.[0] ?? null)} disabled={busy}
                className="block w-full text-sm file:mr-3 file:py-1.5 file:px-3 file:rounded file:border-0 file:bg-[#F07F00] file:text-white file:text-sm hover:file:bg-[#D96E00]"
              />
            </label>

            <div className="flex items-center gap-2">
              <Button size="sm" onClick={onPreview} disabled={busy || !canUpload || !file || !periodId}>
                {busy ? 'Working…' : 'Upload & preview'}
              </Button>
              {batch && batch.rows_invalid === 0 && (
                <span className="text-xs text-[#6B7280]">Uploaded — see “Payroll imports awaiting action” below to approve &amp; commit.</span>
              )}
            </div>

            {msg && (
              <div className={`rounded p-3 text-sm flex items-start gap-2 ${
                msg.kind === 'ok' ? 'bg-[#ECFDF5] border border-[#A7F3D0] text-[#065F46]'
                : msg.kind === 'err' ? 'bg-[#FEF2F2] border border-[#FECACA] text-[#991B1B]'
                : 'bg-[#FFFBEB] border border-[#FDE68A] text-[#92400E]'}`}>
                {msg.kind === 'ok' ? <CheckCircle2 className="w-4 h-4 flex-shrink-0 mt-0.5" /> : <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />}
                <p>{msg.text}</p>
              </div>
            )}

            {batch && (
              <div className="text-xs text-[#6B7280] border-t border-[#E5E7EB] pt-2">
                Batch <code className="text-[#374151]">{batch.id.slice(0, 8)}</code> · {batch.rows_total} rows
                {' '}({batch.rows_valid} valid, {batch.rows_invalid} invalid) · status <strong>{batch.status_display || batch.status}</strong>.
                {' '}One HR/Finance Manager or the CFO (not the uploader) must approve it below before Commit works.
              </div>
            )}
          </CardContent>
        </Card>

        {/* ── Payroll imports awaiting action ──────────────────────── */}
        {pendingBatches.length > 0 && (
          <Card>
            <CardHeader><CardTitle className="flex items-center gap-2"><CheckCircle2 className="w-4 h-4 text-[#F07F00]" /> Payroll imports awaiting action</CardTitle></CardHeader>
            <CardContent className="p-0">
              <table className="w-full text-sm border-collapse">
                <thead className="bg-[#F3F4F6] border-b-2 border-[#E5E7EB]">
                  <tr>
                    <th className="px-4 py-2 text-left text-xs font-semibold uppercase">Period</th>
                    <th className="px-4 py-2 text-left text-xs font-semibold uppercase">File</th>
                    <th className="px-4 py-2 text-center text-xs font-semibold uppercase">Rows</th>
                    <th className="px-4 py-2 text-left text-xs font-semibold uppercase">Uploaded by</th>
                    <th className="px-4 py-2 text-left text-xs font-semibold uppercase">Status</th>
                    <th className="px-4 py-2 text-right text-xs font-semibold uppercase">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#E5E7EB] bg-white">
                  {pendingBatches.map(b => (
                    <tr key={b.id}>
                      <td className="px-4 py-2 font-mono">{b.period_name || '—'}</td>
                      <td className="px-4 py-2 text-[#374151] max-w-[12rem] truncate" title={b.file_name}>{b.file_name || '—'}</td>
                      <td className="px-4 py-2 text-center">{b.rows_valid}/{b.rows_total}{b.rows_invalid > 0 && <span className="text-[#B91C1C]"> ({b.rows_invalid} bad)</span>}</td>
                      <td className="px-4 py-2 text-[#374151]">{b.created_by_username || '—'}</td>
                      <td className="px-4 py-2"><span className="text-xs px-2 py-0.5 rounded bg-[#F3F4F6] text-[#374151]">{b.status_display || b.status}</span></td>
                      <td className="px-4 py-2 text-right">
                        <div className="flex items-center justify-end gap-2">
                          {b.status === 'approved' ? (
                            <Button size="sm" onClick={() => onCommitBatch(b.id)} disabled={!canCommit || batchBusy === b.id || b.rows_invalid > 0}>
                              {batchBusy === b.id ? 'Working…' : 'Commit'}
                            </Button>
                          ) : (
                            <Button size="sm" variant="outline" onClick={() => onApproveBatch(b.id)} disabled={!canApprovePayroll || batchBusy === b.id || b.rows_invalid > 0}>
                              {batchBusy === b.id ? 'Working…' : 'Approve'}
                            </Button>
                          )}
                          {b.status === 'draft' && (canApprovePayroll || b.created_by_username === me?.username) && (
                            <Button size="sm" variant="ghost" onClick={() => onDiscardBatch(b.id, b.file_name)} disabled={batchBusy === b.id} title="Remove this draft so you can upload a corrected file">
                              {batchBusy === b.id ? '…' : 'Discard'}
                            </Button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="px-4 py-2 text-xs text-[#6B7280] border-t border-[#E5E7EB]">
                One approval is needed (HR/Finance Manager or CFO). The manager who uploaded a batch cannot approve it — a different manager must. Uploaded the wrong file? Use <strong>Discard</strong> to remove your draft, then upload the corrected one — nothing is paid until a batch is approved and committed.
              </p>
            </CardContent>
          </Card>
        )}

        {/* ── Periods ──────────────────────────────────────────────── */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between gap-2">
            <CardTitle className="flex items-center gap-2"><Calendar className="w-4 h-4 text-[#F07F00]" /> Payroll periods</CardTitle>
            {canRun && !showNewPeriod && (
              <Button size="sm" leftIcon={<Plus className="w-3.5 h-3.5" />} onClick={openNewPeriod} disabled={busy}>New period</Button>
            )}
          </CardHeader>

          {showNewPeriod && (
            <div className="mx-6 mb-3 border border-[#E5E7EB] rounded-lg p-4 bg-[#FAFAFA] space-y-3">
              <p className="text-xs font-semibold uppercase text-[#6B7280]">Create a payroll period</p>
              <div className="grid grid-cols-2 gap-3">
                <label className="space-y-1">
                  <span className="text-xs text-[#6B7280]">Period name</span>
                  <input value={np.period_name} onChange={e => setNp({ ...np, period_name: e.target.value })}
                    placeholder="2026-07"
                    className="w-full border border-[#E5E7EB] rounded px-2 py-1.5 text-sm bg-white font-mono" />
                </label>
                <label className="space-y-1">
                  <span className="text-xs text-[#6B7280]">Pay date</span>
                  <input type="date" value={np.pay_date} onChange={e => setNp({ ...np, pay_date: e.target.value })}
                    className="w-full border border-[#E5E7EB] rounded px-2 py-1.5 text-sm bg-white" />
                </label>
                <label className="space-y-1">
                  <span className="text-xs text-[#6B7280]">Start date</span>
                  <input type="date" value={np.start_date} onChange={e => setNp({ ...np, start_date: e.target.value })}
                    className="w-full border border-[#E5E7EB] rounded px-2 py-1.5 text-sm bg-white" />
                </label>
                <label className="space-y-1">
                  <span className="text-xs text-[#6B7280]">End date</span>
                  <input type="date" value={np.end_date} onChange={e => setNp({ ...np, end_date: e.target.value })}
                    className="w-full border border-[#E5E7EB] rounded px-2 py-1.5 text-sm bg-white" />
                </label>
              </div>
              {npErr && (
                <div className="rounded p-2 text-xs bg-[#FEF2F2] border border-[#FECACA] text-[#991B1B] flex items-start gap-1.5">
                  <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" /> <span>{npErr}</span>
                </div>
              )}
              <div className="flex items-center gap-2">
                <Button size="sm" onClick={onCreatePeriod} disabled={npBusy}>{npBusy ? 'Creating…' : 'Create period'}</Button>
                <Button size="sm" variant="ghost" onClick={() => setShowNewPeriod(false)} disabled={npBusy}>Cancel</Button>
              </div>
            </div>
          )}

          <CardContent className="p-0">
            {loading ? <p className="px-6 py-8 text-center text-sm text-[#6B7280]">Loading…</p>
            : periods.length === 0 ? (
              <div className="px-6 py-12 text-center text-[#6B7280]">
                <p className="text-sm">No payroll periods yet.</p>
                <p className="text-xs mt-1 text-[#9CA3AF]">
                  {canRun ? 'Use “New period” above to open one (e.g. July 2026).'
                          : 'Ask the CFO / Finance Manager to open one.'}
                </p>
              </div>
            ) : (
              <table className="w-full text-sm border-collapse">
                <thead className="bg-[#F3F4F6] border-b-2 border-[#E5E7EB]">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-semibold uppercase">Period</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold uppercase">Window</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold uppercase">Pay date</th>
                    <th className="px-4 py-3 text-center text-xs font-semibold uppercase">Payslips</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold uppercase">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#E5E7EB] bg-white">
                  {periods.map(p => (
                    <tr key={p.id}>
                      <td className="px-4 py-2 font-mono">{p.period_name}</td>
                      <td className="px-4 py-2 text-[#374151]">{p.start_date} → {p.end_date}</td>
                      <td className="px-4 py-2 text-[#374151]">{p.pay_date || '—'}</td>
                      <td className="px-4 py-2 text-center">{p.payslip_count}</td>
                      <td className="px-4 py-2"><span className="text-xs px-2 py-0.5 rounded bg-[#F3F4F6] text-[#374151]">{p.status_display}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </CardContent>
        </Card>

        <div className="bg-[#FFFBEB] border border-[#FDE68A] rounded p-3 flex items-start gap-2 text-xs text-[#92400E]">
          <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
          <p><strong>BURS warning:</strong> for BWP companies the seeded tax bands are placeholders — verify against the latest Income Tax Act schedule before any real run. Foreign-currency companies (e.g. ADRisk INR) use the tax already withheld in the upload, not BURS.</p>
        </div>
      </div>
    </div>
  )
}
