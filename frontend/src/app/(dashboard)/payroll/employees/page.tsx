'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import {
  getEmployees, getToken, revealEmployeePii,
  archiveEmployee, unarchiveEmployee, getArchiveExpiring, bulkArchiveEmployees,
  setEmployeeKeepAccess,
  terminateEmployee,
} from '@/lib/api'
import type { Employee } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Search, AlertCircle, ChevronLeft, ChevronRight, Users as UsersIcon, Building2, Eye, EyeOff, Archive, ArchiveRestore, Clock, UserMinus } from 'lucide-react'
import { useCompany } from '@/contexts/CompanyContext'
import SmartUpload from '@/components/SmartUpload'
import { localYmd } from '@/lib/utils'

const PAGE_SIZE = 25

export default function EmployeesPage() {
  const router = useRouter()
  // Local company picker (CFO directive 2026-05-18: payroll views must
  // expose an entity filter inline — the global TopBar switcher is too
  // far from the table to be obvious. Writing to the same CompanyContext
  // keeps the rest of the app in sync.).
  const { selectedId, setSelectedId, companies, loaded: companiesLoaded } = useCompany()
  const [items, setItems] = useState<Employee[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [page, setPage] = useState(1)
  const [count, setCount] = useState(0)
  // One-button bank-account reveal (CFO 2026-07-20). Bank numbers are encrypted
  // at rest and shown masked; a click fetches the full number for authorised
  // staff and the reveal is audit-logged server-side. Cleared on every reload
  // so a revealed number never lingers across pages/filters.
  const [revealed, setRevealed] = useState<Record<string, string>>({})
  const [revealing, setRevealing] = useState<string | null>(null)
  // Terminated Employee Archive (Oprah Mogomotsi feature request, 2026-08-13).
  // "Show archived" defaults OFF so archived staff stay out of the everyday
  // list; the backend independently restricts the archived slice + the
  // archive/unarchive actions to HR/Finance Managers (403 for anyone else).
  const [showArchived, setShowArchived] = useState(false)
  const [archiving, setArchiving] = useState<string | null>(null)
  const [expiringCount, setExpiringCount] = useState(0)
  // Bulk archive (Unami Butale feature request, 2026-08-22): tick several
  // terminated leavers and archive them in one action with one shared reason.
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [bulkBusy, setBulkBusy] = useState(false)

  const doReveal = useCallback(async (id: string) => {
    setRevealing(id)
    try {
      const r = await revealEmployeePii(id)
      setRevealed(prev => ({ ...prev, [id]: r.bank_account_no || '—' }))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Reveal failed')
    } finally { setRevealing(null) }
  }, [])

  const load = useCallback(async () => {
    setLoading(true); setError(null); setRevealed({}); setSelected(new Set())
    try {
      const res = await getEmployees({
        page, status: statusFilter || undefined, search: search || undefined,
        archived: showArchived,
      })
      setItems(res.results); setCount(res.count)
    } catch (err) { setError(err instanceof Error ? err.message : 'Failed to load') }
    finally { setLoading(false) }
  }, [page, search, statusFilter, showArchived])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load()
    // Reload when the selected company changes. apiFetch auto-injects
    // company=<id> on every GET so we don't pass it explicitly here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load, selectedId])

  useEffect(() => {
    // Retention-expiry HR review flag. 403s silently for non-HR/Finance —
    // no auto-purge, this is a review nudge only.
    getArchiveExpiring(90).then(r => setExpiringCount(r.count)).catch(() => {})
  }, [])

  const doArchive = useCallback(async (emp: Employee) => {
    const reason = window.prompt(`Archive ${emp.full_name} — reason (required):`) ?? null
    if (reason === null) return
    if (!reason.trim()) { setError('Archive reason is required.'); return }
    setArchiving(emp.id)
    try {
      await archiveEmployee(emp.id, reason.trim())
      await load()
    } catch (err) { setError(err instanceof Error ? err.message : 'Archive failed') }
    finally { setArchiving(null) }
  }, [load])

  // Terminate Employee (D. Ikgopoleng feature request, bug dfc0b768; CFO-authorised
  // 2026-09-10). Records the exit reason + date and closes the profile. The record
  // is RETAINED — archiving (hiding it) stays a separate, deliberate second step.
  const doTerminate = useCallback(async (emp: Employee) => {
    const reason = window.prompt(
      `Terminate ${emp.full_name}\n\nReason — type one of:\n` +
      'resignation, dismissal, retirement, redundancy, end_of_contract, death, other',
    ) ?? null
    if (reason === null) return
    if (!reason.trim()) { setError('Termination reason is required.'); return }
    const today = localYmd()
    const when = window.prompt(`Last working day for ${emp.full_name} (YYYY-MM-DD):`, today) ?? null
    if (when === null) return
    if (!/^\d{4}-\d{2}-\d{2}$/.test(when.trim())) { setError('Date must be YYYY-MM-DD.'); return }
    setArchiving(emp.id); setError(null)
    try {
      await terminateEmployee(emp.id, when.trim(), reason.trim().toLowerCase())
      await load()
    } catch (err) { setError(err instanceof Error ? err.message : 'Terminate failed') }
    finally { setArchiving(null) }
  }, [load])

  // Keep access after exit (CFO 2026-09-18). A leaver's Omni login is closed
  // automatically once their last working day has passed; an external
  // contractor who keeps working with us must be exempt, or the automation
  // cuts them off mid-engagement.
  const doToggleKeepAccess = useCallback(async (emp: Employee) => {
    const next = !emp.keep_access_after_exit
    if (next && !window.confirm(
      `Keep ${emp.full_name} signed into Omni after their exit date?

` +
      'Only tick this for someone who still works with us, such as an external ' +
      'contractor. Everyone else should lose access.')) return
    setArchiving(emp.id)
    try {
      await setEmployeeKeepAccess(emp.id, next)
      await load()
    } catch (err) { setError(err instanceof Error ? err.message : 'Could not change access') }
    finally { setArchiving(null) }
  }, [load])

  const doUnarchive = useCallback(async (emp: Employee) => {
    const reason = window.prompt(`Unarchive ${emp.full_name} — reason (rehire / audit / legal hold):`) ?? null
    if (reason === null) return
    if (!reason.trim()) { setError('Unarchive reason is required.'); return }
    setArchiving(emp.id)
    try {
      await unarchiveEmployee(emp.id, reason.trim())
      await load()
    } catch (err) { setError(err instanceof Error ? err.message : 'Unarchive failed') }
    finally { setArchiving(null) }
  }, [load])

  // A row can be archived only if it is a terminated leaver not already archived
  // (same rule as the per-row Archive button). The backend re-checks and also
  // needs a termination date, reporting any that lack one as skipped.
  const canArchive = useCallback((e: Employee) => e.status === 'terminated' && !e.is_archived, [])
  const eligible = items.filter(canArchive)
  const allEligibleSelected = eligible.length > 0 && eligible.every(e => selected.has(e.id))

  const toggleOne = useCallback((id: string) => {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }, [])

  const toggleAllEligible = useCallback(() => {
    setSelected(prev => {
      const allOn = eligible.length > 0 && eligible.every(e => prev.has(e.id))
      if (allOn) return new Set()
      return new Set(eligible.map(e => e.id))
    })
  }, [eligible])

  const doBulkArchive = useCallback(async () => {
    const ids = Array.from(selected)
    if (ids.length === 0) return
    const reason = window.prompt(`Archive ${ids.length} selected employee${ids.length === 1 ? '' : 's'} — reason (required):`) ?? null
    if (reason === null) return
    if (!reason.trim()) { setError('Archive reason is required.'); return }
    setBulkBusy(true); setError(null)
    try {
      const r = await bulkArchiveEmployees(ids, reason.trim())
      if (r.skipped > 0) {
        const why = r.results.filter(x => !x.ok)
          .map(x => `${x.name || x.id}: ${x.detail || 'skipped'}`).join('; ')
        setError(`${r.archived} archived, ${r.skipped} skipped — ${why}`)
      }
      await load()
    } catch (err) { setError(err instanceof Error ? err.message : 'Bulk archive failed') }
    finally { setBulkBusy(false) }
  }, [selected, load])

  const totalPages = Math.max(1, Math.ceil(count / PAGE_SIZE))

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar title="Employees" breadcrumbs={[{ label: 'Payroll' }, { label: 'Employees' }]} />

      <div className="flex-1 p-6 space-y-4">
        <SmartUpload section="employees" onCommitted={load} />
        <div className="flex gap-3 items-end flex-wrap">
          <div className="relative flex-1 min-w-[200px] max-w-sm">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[#9CA3AF] pointer-events-none" strokeWidth={1.5} />
            <input type="text" placeholder="Search by name, department, email..."
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
          <select value={statusFilter} onChange={e => { setStatusFilter(e.target.value); setPage(1) }}
            className="h-10 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm">
            <option value="">All statuses</option>
            <option value="active">Active</option>
            <option value="on_leave">On leave</option>
            <option value="suspended">Suspended</option>
            <option value="terminated">Terminated</option>
          </select>
          <Button
            variant={showArchived ? 'primary' : 'outline'} size="sm"
            leftIcon={<Archive className="w-3.5 h-3.5" />}
            onClick={() => { setShowArchived(a => !a); setPage(1) }}
          >
            {showArchived ? 'Showing archived' : 'Show archived'}
          </Button>
        </div>

        {expiringCount > 0 && (
          <div className="bg-[#FFFBEB] border border-[#FDE68A] rounded-lg p-3 flex items-center gap-2">
            <Clock className="w-4 h-4 text-[#B45309] shrink-0" strokeWidth={1.5} />
            <p className="text-[#92400E] text-sm">
              {expiringCount} archived record{expiringCount === 1 ? '' : 's'} approach{expiringCount === 1 ? 'es' : ''} the end of its retention window in the next 90 days — HR review needed. Records are never auto-purged.
            </p>
          </div>
        )}

        <p className="text-xs text-[#6B7280] flex items-center gap-1.5">
          <Eye className="w-3.5 h-3.5 text-[#B04E00] shrink-0" strokeWidth={1.5} />
          Bank numbers are encrypted and hidden. Click <span className="font-medium text-[#B04E00]">Reveal</span> to see a full number — every reveal is recorded in the audit trail.
        </p>

        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-[#DC2626]" />
            <p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}

        {selected.size > 0 && (
          <div className="bg-[#FFF7ED] border border-[#FED7AA] rounded-lg p-3 flex items-center justify-between gap-3">
            <p className="text-[#9A3412] text-sm">
              {selected.size} terminated employee{selected.size === 1 ? '' : 's'} selected for archiving.
            </p>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" onClick={() => setSelected(new Set())} disabled={bulkBusy}>
                Clear
              </Button>
              <Button variant="primary" size="sm" onClick={doBulkArchive} disabled={bulkBusy}
                leftIcon={<Archive className="w-3.5 h-3.5" />}>
                {bulkBusy ? 'Archiving…' : `Archive selected (${selected.size})`}
              </Button>
            </div>
          </div>
        )}

        <Card>
          <CardContent className="p-0">
            {loading ? <p className="px-6 py-8 text-center text-sm text-[#6B7280]">Loading…</p>
            : items.length === 0 ? (
              <div className="px-6 py-12 text-center text-[#6B7280]">
                <UsersIcon className="w-10 h-10 mx-auto mb-3 text-[#D1D5DB]" strokeWidth={1.5} />
                <p className="text-sm">No employees yet.</p>
                <p className="text-xs text-[#9CA3AF] mt-1">Run <code>python manage.py setup_employees</code> to seed the staff register.</p>
              </div>
            ) : (
              <>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm border-collapse">
                    <thead className="bg-[#F3F4F6] border-b-2 border-[#E5E7EB]">
                      <tr>
                        {!showArchived && (
                          <th className="px-4 py-3 text-left w-10">
                            {eligible.length > 0 && (
                              <input type="checkbox" aria-label="Select all terminated employees on this page"
                                checked={allEligibleSelected} onChange={toggleAllEligible}
                                className="w-4 h-4 accent-[#B04E00] cursor-pointer" />
                            )}
                          </th>
                        )}
                        <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase">Name</th>
                        <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase">Department</th>
                        <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase hidden md:table-cell">Title</th>
                        <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase hidden lg:table-cell">Email</th>
                        <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase">Bank account</th>
                        <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase">Status</th>
                        <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase">
                          {showArchived ? 'Archived' : 'Archive'}
                        </th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[#E5E7EB] bg-white">
                      {items.map(e => (
                        <tr key={e.id} className={selected.has(e.id) ? 'bg-[#FFF7ED]' : undefined}>
                          {!showArchived && (
                            <td className="px-4 py-3 w-10">
                              {canArchive(e) && (
                                <input type="checkbox" aria-label={`Select ${e.full_name}`}
                                  checked={selected.has(e.id)} onChange={() => toggleOne(e.id)}
                                  className="w-4 h-4 accent-[#B04E00] cursor-pointer" />
                              )}
                            </td>
                          )}
                          <td className="px-4 py-3 font-medium text-[#111827]">{e.full_name}</td>
                          <td className="px-4 py-3 text-[#374151]">{e.department || '—'}</td>
                          <td className="px-4 py-3 text-[#374151] hidden md:table-cell">{e.job_title || '—'}</td>
                          <td className="px-4 py-3 text-[#6B7280] hidden lg:table-cell">{e.email || '—'}</td>
                          <td className="px-4 py-3 whitespace-nowrap">
                            {!e.bank_account_no ? (
                              <span className="text-[#9CA3AF]">—</span>
                            ) : revealed[e.id] !== undefined ? (
                              <span className="inline-flex items-center gap-2">
                                <span className="font-mono text-[#111827]">{revealed[e.id]}</span>
                                <button type="button" title="Hide"
                                  onClick={() => setRevealed(r => { const n = { ...r }; delete n[e.id]; return n })}
                                  className="text-[#6B7280] hover:text-[#111827] inline-flex items-center">
                                  <EyeOff className="w-3.5 h-3.5" strokeWidth={1.5} />
                                </button>
                              </span>
                            ) : (
                              <span className="inline-flex items-center gap-2">
                                <span className="font-mono text-[#6B7280]">{e.bank_account_no}</span>
                                <button type="button" title="Reveal full number (recorded)"
                                  onClick={() => doReveal(e.id)} disabled={revealing === e.id}
                                  className="text-[#B04E00] hover:underline text-xs inline-flex items-center gap-1 disabled:opacity-50">
                                  <Eye className="w-3.5 h-3.5" strokeWidth={1.5} />
                                  {revealing === e.id ? '…' : 'Reveal'}
                                </button>
                              </span>
                            )}
                          </td>
                          <td className="px-4 py-3">
                            <span className="text-xs">{e.status_display}</span>
                            {e.status === 'terminated' && (
                              <button type="button"
                                onClick={() => doToggleKeepAccess(e)} disabled={archiving === e.id}
                                title={e.keep_access_after_exit
                                  ? 'This person keeps their Omni login after leaving. Click to remove that.'
                                  : 'Keep this person signed into Omni after their exit date (external contractor).'}
                                className={`block mt-1 text-[11px] hover:underline disabled:opacity-50 ${
                                  e.keep_access_after_exit ? 'text-[#B04E00] font-medium' : 'text-[#6B7280]'}`}>
                                {e.keep_access_after_exit ? 'Keeps Omni access' : 'Keep Omni access'}
                              </button>
                            )}
                          </td>
                          <td className="px-4 py-3 whitespace-nowrap">
                            {e.is_archived ? (
                              <span className="inline-flex flex-col gap-1">
                                <span className="text-xs text-[#6B7280]" title={e.archive_reason}>
                                  {e.archived_by_name || 'Archived'} · retention to {e.retention_expiry_date || '—'}
                                </span>
                                <button type="button"
                                  onClick={() => doUnarchive(e)} disabled={archiving === e.id}
                                  className="text-[#B04E00] hover:underline text-xs inline-flex items-center gap-1 disabled:opacity-50 self-start">
                                  <ArchiveRestore className="w-3.5 h-3.5" strokeWidth={1.5} />
                                  {archiving === e.id ? '…' : 'Unarchive'}
                                </button>
                              </span>
                            ) : e.status === 'terminated' ? (
                              <button type="button" title="Hide from active lists and all pay runs — retains all data"
                                onClick={() => doArchive(e)} disabled={archiving === e.id}
                                className="text-[#B04E00] hover:underline text-xs inline-flex items-center gap-1 disabled:opacity-50">
                                <Archive className="w-3.5 h-3.5" strokeWidth={1.5} />
                                {archiving === e.id ? '…' : 'Archive'}
                              </button>
                            ) : (
                              <button type="button" title="Record the exit and close the profile — all history is retained"
                                onClick={() => doTerminate(e)} disabled={archiving === e.id}
                                className="text-[#B42318] hover:underline text-xs inline-flex items-center gap-1 disabled:opacity-50">
                                <UserMinus className="w-3.5 h-3.5" strokeWidth={1.5} />
                                {archiving === e.id ? '…' : 'Terminate'}
                              </button>
                            )}
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
