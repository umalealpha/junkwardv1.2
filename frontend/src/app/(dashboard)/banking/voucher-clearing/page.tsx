'use client'

import { useEffect, useState, useCallback, useMemo } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import {
  getJEClearings, submitJEClearing, approveJEClearing, rejectJEClearing,
  getJEClearingRoles, getJournalEntries, getCompanies, getToken,
} from '@/lib/api'
import type {
  JEClearingRequest, JEClearingRoles, JournalEntry, Company,
} from '@/lib/api'
import { getFyStart, today } from '@/lib/utils'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  AlertCircle, CheckCircle2, RefreshCw, Send, ThumbsUp, ThumbsDown,
  Eraser, Search, ShieldCheck,
} from 'lucide-react'

const STATUS_COLOR: Record<string, string> = {
  pending:  'bg-amber-50 text-amber-800 border-amber-300',
  approved: 'bg-emerald-50 text-emerald-800 border-emerald-300',
  rejected: 'bg-red-50 text-red-800 border-red-300',
}

export default function VoucherClearingPage() {
  const router = useRouter()
  const searchParams = useSearchParams()

  const [roles, setRoles]         = useState<JEClearingRoles | null>(null)
  const [rows, setRows]           = useState<JEClearingRequest[]>([])
  const [statusFilter, setStatusFilter] = useState<'pending' | 'approved' | 'rejected' | ''>('pending')
  const [loading, setLoading]     = useState(true)
  const [error, setError]         = useState<string | null>(null)
  const [info, setInfo]           = useState<string | null>(null)

  // submit form (maker)
  const [mode, setMode]           = useState<'single' | 'bulk'>('single')
  const [jeSearch, setJeSearch]   = useState('')
  const [jeOptions, setJeOptions] = useState<JournalEntry[]>([])
  const [pickedJE, setPickedJE]   = useState<string>('')
  const [reason, setReason]       = useState('')
  const [submitting, setSubmitting] = useState(false)

  // bulk-wipe form (CFO directive 2026-05-26)
  const [companies, setCompanies] = useState<Company[]>([])
  const [bulkCompany, setBulkCompany] = useState<string>('')
  const [bulkFrom,    setBulkFrom]    = useState<string>(getFyStart())
  const [bulkTo,      setBulkTo]      = useState<string>(today())

  // approve/reject (checker)
  const [actingId, setActingId]   = useState<string | null>(null)
  const [decisionNote, setDecisionNote] = useState('')

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const [r, list] = await Promise.all([
        getJEClearingRoles().catch(() => null),
        getJEClearings(
          statusFilter
            ? { status: statusFilter as 'pending' | 'approved' | 'rejected' }
            : {},
        ).catch(() => null),
      ])
      if (r) setRoles(r)
      const results = list && Array.isArray(list.results) ? list.results : []
      setRows(results)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load')
    } finally {
      setLoading(false)
    }
  }, [statusFilter])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load()
    getCompanies()
      .then(r => setCompanies(r.results))
      .catch(() => { /* not blocking — bulk form just shows empty picker */ })
  }, [load, router])

  // Deep-link from JE detail / GL / TB pages:
  //   /banking/voucher-clearing?je=<id>&number=<entry_number>   ← preferred (JE detail)
  //   /banking/voucher-clearing?number=<entry_number>           ← GL / TB drill (no UUID on hand)
  // Pre-fills the search box + pre-picks the JE so the maker only writes the
  // reason. CFO directive 2026-05-26: makers should be able to clear GL/TB
  // entries from the same workflow without first re-typing the JE number.
  useEffect(() => {
    if (!searchParams) return
    const id  = searchParams.get('je') || ''
    const num = searchParams.get('number') || ''
    if (id) {
      setPickedJE(id)
      if (num) setJeSearch(num)
      return
    }
    if (num) {
      // GL / TB landed us here with only the entry number — pull the JE in
      // and auto-pick if exactly one match.
      setJeSearch(num)
      getJournalEntries({ search: num, status: 'posted', page: '1' })
        .then(r => {
          const hits = (r.results || []).slice(0, 20)
          setJeOptions(hits)
          const exact = hits.find(j => j.entry_number === num)
          if (exact) setPickedJE(exact.id)
        })
        .catch(() => { /* swallow — user can retry manually */ })
    }
  }, [searchParams])

  const searchJEs = useCallback(async () => {
    if (!jeSearch.trim()) { setJeOptions([]); return }
    try {
      const r = await getJournalEntries({
        search: jeSearch.trim(),
        status: 'posted',
        page: '1',
      })
      setJeOptions(r.results.slice(0, 20))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'JE search failed')
    }
  }, [jeSearch])

  const onSubmit = async () => {
    if (reason.trim().length < 5) {
      setError('Reason must be at least 5 characters.'); return
    }
    if (mode === 'single' && !pickedJE) {
      setError('Pick a posted JE first.'); return
    }
    if (mode === 'bulk' && !bulkCompany) {
      setError('Pick a company for the bulk clearing.'); return
    }
    if (mode === 'bulk' && (!bulkFrom || !bulkTo)) {
      setError('From + To dates are both required for a bulk clearing.'); return
    }
    setSubmitting(true); setError(null); setInfo(null)
    try {
      let r: JEClearingRequest
      if (mode === 'single') {
        r = await submitJEClearing({
          journal_entry: pickedJE, reason: reason.trim(),
        })
        setInfo(`Clearing request submitted for ${r.je_number}. Awaiting Kago's approval.`)
      } else {
        r = await submitJEClearing({
          bulk_scope: {
            company:   bulkCompany,
            from_date: bulkFrom,
            to_date:   bulkTo,
          },
          reason: reason.trim(),
        })
        const co = companies.find(c => c.id === bulkCompany)
        setInfo(
          `Bulk clearing request submitted for ${co?.name || 'selected company'} `
          + `(${bulkFrom} → ${bulkTo}). Awaiting Kago's approval.`,
        )
      }
      setPickedJE(''); setReason(''); setJeSearch(''); setJeOptions([])
      setStatusFilter('pending')
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Submit failed')
    } finally {
      setSubmitting(false)
    }
  }

  const onApprove = async (id: string) => {
    const row = rows.find(r => r.id === id)
    const isBulk = row?.is_bulk
    const confirmMsg = isBulk
      ? 'Approve this bulk clearing? omni will post a REVERSING journal '
        + 'entry for every posted JE in the chosen company + date range. '
        + 'The originals stay in the GL marked "reversed" and linked to '
        + 'their reversals — a full audit trail. Nothing is deleted.'
      : 'Approve this clearing? omni will post a REVERSING journal entry '
        + 'for the JE. The original stays in the GL marked "reversed" and '
        + 'linked to its reversal — a full audit trail. Nothing is deleted.'
    if (!window.confirm(confirmMsg)) return
    setActingId(id); setError(null); setInfo(null)
    try {
      const r = await approveJEClearing(id, decisionNote)
      setInfo(
        r.deleted_count
          ? `Reversed ${r.deleted_count} JE${r.deleted_count === 1 ? '' : 's'}. `
            + `Original(s) kept + linked to the reversal on clearing #${r.id}.`
          : `Reversed.`,
      )
      setDecisionNote('')
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Approval failed')
    } finally {
      setActingId(null)
    }
  }

  const onReject = async (id: string) => {
    setActingId(id); setError(null); setInfo(null)
    try {
      await rejectJEClearing(id, decisionNote)
      setInfo('Clearing rejected. Original JE untouched.')
      setDecisionNote('')
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Reject failed')
    } finally {
      setActingId(null)
    }
  }

  const pendingCount = useMemo(
    () => rows.filter((r) => r.status === 'pending').length,
    [rows],
  )

  return (
    <div className="min-h-screen bg-[#F8F9FA]">
      <TopBar title="Voucher Clearing (TVs)"
              subtitle="Maker-checker reversal of duplicate or wrong journal entries" />

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 space-y-4">
        {error && (
          <Card className="border-red-200 bg-red-50">
            <CardContent className="p-3 flex items-start gap-2">
              <AlertCircle className="w-4 h-4 text-red-700 mt-0.5" />
              <span className="text-sm text-red-700">{error}</span>
            </CardContent>
          </Card>
        )}
        {info && (
          <Card className="border-emerald-200 bg-emerald-50">
            <CardContent className="p-3 flex items-start gap-2">
              <CheckCircle2 className="w-4 h-4 text-emerald-700 mt-0.5" />
              <span className="text-sm text-emerald-700">{info}</span>
            </CardContent>
          </Card>
        )}

        {/* Role banner */}
        <Card>
          <CardContent className="p-4 flex flex-wrap items-center gap-4">
            <ShieldCheck className="w-5 h-5 text-[#F4A623]" />
            <div className="text-sm">
              Your role:{' '}
              {roles?.is_maker && roles?.is_approver
                ? <strong>Maker + Approver</strong>
                : roles?.is_maker
                  ? <strong>Maker</strong>
                  : roles?.is_approver
                    ? <strong>Approver</strong>
                    : <strong className="text-amber-700">No role</strong>}
              {' — '}
              {roles?.is_maker && 'Submit clearing requests below.'}
              {roles?.is_approver && ' Approve/reject pending requests in the queue.'}
              {!roles?.is_maker && !roles?.is_approver
                && 'Ask the CFO to add you to voucher_clearing_maker or '
                   + 'voucher_clearing_approver group.'}
            </div>
            <div className="ml-auto flex items-center gap-2">
              <span className="text-xs text-gray-600">
                {pendingCount} pending
              </span>
              <Button variant="outline" onClick={load} disabled={loading}>
                <RefreshCw className={`w-4 h-4 mr-1 ${loading ? 'animate-spin' : ''}`} /> Refresh
              </Button>
            </div>
          </CardContent>
        </Card>

        {/* Maker form */}
        {roles?.is_maker && (
          <Card>
            <CardContent className="p-6">
              <div className="flex items-center gap-2 mb-3">
                <Eraser className="w-5 h-5 text-[#F4A623]" />
                <h3 className="text-lg font-medium">Submit clearing request</h3>
              </div>

              {/* Mode picker — CFO directive 2026-05-26 */}
              <div className="flex gap-2 mb-3" role="tablist">
                <button
                  type="button"
                  onClick={() => setMode('single')}
                  className={`px-3 py-1.5 rounded text-sm border ${
                    mode === 'single'
                      ? 'bg-[#0D1B2A] text-white border-[#0D1B2A]'
                      : 'bg-white text-[#374151] border-[#D1D5DB] hover:bg-[#F9FAFB]'
                  }`}
                >
                  Single JE
                </button>
                <button
                  type="button"
                  onClick={() => setMode('bulk')}
                  className={`px-3 py-1.5 rounded text-sm border ${
                    mode === 'bulk'
                      ? 'bg-[#B45309] text-white border-[#B45309]'
                      : 'bg-white text-[#B45309] border-[#FDE08A] hover:bg-[#FFFBEB]'
                  }`}
                >
                  Bulk clearing (period)
                </button>
              </div>

              {mode === 'single' && (
                <>
                  <p className="text-xs text-gray-500 mb-3">
                    Pick a POSTED journal entry that is a duplicate or otherwise
                    wrong. On approval omni posts a REVERSING entry — the
                    original stays in the GL marked &ldquo;reversed&rdquo; and
                    linked to its reversal. Nothing is deleted.
                  </p>

                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    <div>
                      <label className="text-xs font-medium text-gray-600">Find JE</label>
                      <div className="flex gap-2">
                        <input
                          value={jeSearch}
                          onChange={(e) => setJeSearch(e.target.value)}
                          onKeyDown={(e) => e.key === 'Enter' && searchJEs()}
                          placeholder="Entry number / description"
                          className="flex-1 border rounded px-3 py-2 text-sm"
                        />
                        <Button variant="outline" onClick={searchJEs}>
                          <Search className="w-4 h-4 mr-1" /> Search
                        </Button>
                      </div>
                    </div>
                    <div>
                      <label className="text-xs font-medium text-gray-600">Picked JE</label>
                      <select value={pickedJE} onChange={(e) => setPickedJE(e.target.value)}
                        className="w-full border rounded px-3 py-2 text-sm">
                        <option value="">— search above —</option>
                        {jeOptions.map((j) => (
                          <option key={j.id} value={j.id}>
                            {j.entry_number} · {j.entry_date} · {j.description?.slice(0, 60)}
                          </option>
                        ))}
                      </select>
                    </div>
                  </div>
                </>
              )}

              {mode === 'bulk' && (
                <>
                  <div className="mb-3 rounded-md border border-[#FDE08A] bg-[#FFFBEB] px-3 py-2 text-xs text-[#7A5B00]">
                    <strong>Bulk clearing.</strong> On approval omni posts a
                    REVERSING entry for every POSTED journal entry in the chosen
                    company between the two dates (inclusive). Use this to redo a
                    duplicated TB. Originals stay in the GL marked
                    &ldquo;reversed&rdquo; and linked to their reversals —
                    nothing is deleted.
                  </div>
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                    <div>
                      <label className="text-xs font-medium text-gray-600">Company</label>
                      <select
                        value={bulkCompany}
                        onChange={(e) => setBulkCompany(e.target.value)}
                        className="w-full border rounded px-3 py-2 text-sm"
                      >
                        <option value="">— pick company —</option>
                        {companies.map(c => (
                          <option key={c.id} value={c.id}>
                            {c.code} · {c.name}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div>
                      <label className="text-xs font-medium text-gray-600">From date</label>
                      <input
                        type="date"
                        value={bulkFrom}
                        onChange={(e) => setBulkFrom(e.target.value)}
                        className="w-full border rounded px-3 py-2 text-sm"
                      />
                    </div>
                    <div>
                      <label className="text-xs font-medium text-gray-600">To date</label>
                      <input
                        type="date"
                        value={bulkTo}
                        onChange={(e) => setBulkTo(e.target.value)}
                        className="w-full border rounded px-3 py-2 text-sm"
                      />
                    </div>
                  </div>
                </>
              )}

              <div className="grid grid-cols-1 gap-3 mt-3">
                <div>
                  <label className="text-xs font-medium text-gray-600">Reason</label>
                  <textarea value={reason} onChange={(e) => setReason(e.target.value)}
                    placeholder={
                      mode === 'single'
                        ? 'e.g. Duplicate of JE-2026-001234; original posted on 2026-05-22 by Pako; cash overstated by P 10M.'
                        : 'e.g. ADIC FY25 TB was duplicated by mistake — reverse Jul-2024 through Jun-2025 so we can re-upload the correct trial balance.'
                    }
                    rows={3}
                    className="w-full border rounded px-3 py-2 text-sm" />
                </div>
              </div>

              <div className="mt-3 flex justify-end">
                <Button onClick={onSubmit}
                  disabled={
                    submitting
                    || reason.trim().length < 5
                    || (mode === 'single' && !pickedJE)
                    || (mode === 'bulk' && (!bulkCompany || !bulkFrom || !bulkTo))
                  }
                  className={
                    mode === 'bulk'
                      ? 'bg-[#B45309] hover:bg-[#92400E] text-white'
                      : 'bg-[#0D1B2A] hover:bg-[#1a2940] text-white'
                  }>
                  <Send className="w-4 h-4 mr-1" />
                  {submitting
                    ? 'Submitting…'
                    : (mode === 'bulk' ? 'Submit bulk clearing for approval' : 'Submit for approval')}
                </Button>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Queue */}
        <Card>
          <CardContent className="p-0">
            <div className="px-4 py-3 border-b flex items-center gap-2">
              <h3 className="font-medium">Clearing queue</h3>
              <select value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value as
                  'pending' | 'approved' | 'rejected' | '')}
                className="ml-3 text-xs border rounded px-2 py-1">
                <option value="">All</option>
                <option value="pending">Pending</option>
                <option value="approved">Approved</option>
                <option value="rejected">Rejected</option>
              </select>
              <span className="text-xs text-gray-500 ml-auto">Most recent first</span>
            </div>

            {roles?.is_approver && (
              <div className="px-4 py-3 border-b bg-amber-50">
                <label className="text-xs font-medium text-amber-900">
                  Decision note (optional — applied to next Approve / Reject):
                </label>
                <input value={decisionNote} onChange={(e) => setDecisionNote(e.target.value)}
                  placeholder="Why are you approving / rejecting?"
                  className="w-full border rounded px-3 py-2 text-sm mt-1" />
              </div>
            )}

            {loading ? (
              <div className="p-6 text-gray-500 text-sm">Loading…</div>
            ) : rows.length === 0 ? (
              <div className="p-12 text-center text-gray-500 text-sm">
                Nothing to show under this filter.
              </div>
            ) : (
              <table className="w-full text-sm">
                <thead className="bg-gray-50 border-b text-xs uppercase text-gray-600">
                  <tr className="text-left">
                    <th className="px-4 py-3">When</th>
                    <th className="px-4 py-3">JE</th>
                    <th className="px-4 py-3">Description</th>
                    <th className="px-4 py-3 text-right">JE size</th>
                    <th className="px-4 py-3">Reason</th>
                    <th className="px-4 py-3">Submitted by</th>
                    <th className="px-4 py-3">Status</th>
                    <th className="px-4 py-3 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.id} className="border-b border-gray-100">
                      <td className="px-4 py-2 text-xs text-gray-600">
                        {new Date(r.submitted_at).toLocaleString('en-BW', { dateStyle: 'short', timeStyle: 'short' })}
                      </td>
                      <td className="px-4 py-2 font-mono text-xs">
                        {r.is_bulk ? (
                          <span className="text-[#B91C1C]">BULK</span>
                        ) : (r.je_number || '—')}
                      </td>
                      <td className="px-4 py-2 text-xs">
                        {r.is_bulk && r.bulk_scope ? (
                          <span>
                            {companies.find(c => c.id === r.bulk_scope?.company)?.code || '—'}
                            {' · '}
                            {r.bulk_scope.from_date} → {r.bulk_scope.to_date}
                          </span>
                        ) : (r.je_description || '—')}
                      </td>
                      <td className="px-4 py-2 text-right font-mono text-xs">
                        {r.is_bulk ? `${r.deleted_count || 0} JEs` : (r.je_amount || '—')}
                      </td>
                      <td className="px-4 py-2 text-xs">{r.reason}</td>
                      <td className="px-4 py-2 text-xs">{r.submitted_by_name || '—'}</td>
                      <td className="px-4 py-2">
                        <span className={`text-xs px-2 py-0.5 rounded-full border ${STATUS_COLOR[r.status] || ''}`}>
                          {r.status}
                        </span>
                      </td>
                      <td className="px-4 py-2 text-right whitespace-nowrap">
                        {r.status === 'pending' && roles?.is_approver ? (
                          <>
                            <Button size="sm" className="mr-1 bg-emerald-700 hover:bg-emerald-800 text-white"
                              disabled={actingId === r.id}
                              onClick={() => onApprove(r.id)}>
                              <ThumbsUp className="w-3 h-3 mr-1" /> Approve
                            </Button>
                            <Button size="sm" variant="outline"
                              disabled={actingId === r.id}
                              onClick={() => onReject(r.id)}>
                              <ThumbsDown className="w-3 h-3 mr-1" /> Reject
                            </Button>
                          </>
                        ) : r.status === 'approved' ? (
                          <span className="text-xs text-emerald-700">
                            {r.reversal_entry_number
                              ? `Reversal ${r.reversal_entry_number}`
                              : (r.deleted_count
                                  ? `Reversed ${r.deleted_count} JE${r.deleted_count === 1 ? '' : 's'}`
                                  : 'Approved')}
                          </span>
                        ) : r.status === 'rejected' ? (
                          <span className="text-xs text-red-700">
                            By {r.decided_by_name || '—'}
                          </span>
                        ) : (
                          <span className="text-xs text-gray-500">Awaiting approver</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
