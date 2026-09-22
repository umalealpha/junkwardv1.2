'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { getJournalEntries, getToken } from '@/lib/api'
import type { JournalEntry } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { LoadingTable } from '@/components/ui/loading'
import { cn, formatDate, localYmd } from '@/lib/utils'
import { Search, BookOpen, AlertCircle, ChevronLeft, ChevronRight, Plus } from 'lucide-react'
import { useTheme } from '@/contexts/ThemeContext'
import type { Theme } from '@/lib/themes'

// ─── Journal Entries List Page ───────────────────────────────────────────────

const JOURNAL_TYPE_OPTIONS = [
  { value: '',              label: 'All Types' },
  { value: 'sales',         label: 'Sales' },
  { value: 'purchases',     label: 'Purchases' },
  { value: 'cash_receipts', label: 'Cash Receipts' },
  { value: 'cash_payments', label: 'Cash Payments' },
  { value: 'bank',          label: 'Bank' },
  { value: 'general',       label: 'General' },
]

const STATUS_OPTIONS = [
  { value: '',                 label: 'All Statuses' },
  { value: 'draft',            label: 'Draft' },
  { value: 'pending_approval', label: 'Pending Approval' },
  { value: 'rejected',         label: 'Rejected' },
  { value: 'posted',           label: 'Posted' },
  { value: 'reversed',         label: 'Reversed' },
]

function JournalTypeBadge({ type, theme }: { type: string; theme: Theme }) {
  const labels: Record<string, string> = {
    sales: 'Sales',
    purchases: 'Purchases',
    cash_receipts: 'Cash Receipts',
    cash_payments: 'Cash Payments',
    bank: 'Bank',
    general: 'General',
  }

  let color: string
  let bg: string
  switch (type) {
    case 'sales':
      color = theme.ok; bg = theme.okB; break
    case 'purchases':
      color = theme.inf; bg = theme.inB; break
    case 'cash_receipts':
      color = theme.ok; bg = theme.okB; break
    case 'cash_payments':
      color = theme.er; bg = theme.erB; break
    case 'bank':
      color = theme.inf; bg = theme.inB; break
    default:
      color = theme.t2; bg = theme.g100; break
  }

  return (
    <span
      className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium whitespace-nowrap"
      style={{ color, background: bg }}
    >
      {labels[type] || type}
    </span>
  )
}

function StatusBadgeJE({ status, theme }: { status: string; theme: Theme }) {
  const labels: Record<string, string> = {
    posted: 'Posted',
    draft: 'Draft',
    pending_approval: 'Pending Approval',
    rejected: 'Rejected',
    reversed: 'Reversed',
  }

  let color: string
  let bg: string
  switch (status) {
    case 'posted':
      color = theme.inf; bg = theme.inB; break
    case 'draft':
      color = theme.wr; bg = theme.wrB; break
    case 'pending_approval':
      color = theme.orange; bg = theme.oL; break
    case 'rejected':
      color = theme.er; bg = theme.erB; break
    case 'reversed':
      color = theme.t2; bg = theme.g100; break
    default:
      color = theme.t2; bg = theme.g100; break
  }

  return (
    <span
      className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium whitespace-nowrap"
      style={{ color, background: bg }}
    >
      {labels[status] || status}
    </span>
  )
}

export default function JournalEntriesPage() {
  const router = useRouter()
  const { theme } = useTheme()

  const [entries, setEntries] = useState<JournalEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [journalType, setJournalType] = useState('')
  const [status, setStatus] = useState('')
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')
  const [page, setPage] = useState(1)
  const [totalCount, setTotalCount] = useState(0)
  const PAGE_SIZE = 25

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params: Record<string, string> = {
        page: String(page),
        page_size: String(PAGE_SIZE),
        ordering: '-entry_date',
      }
      if (journalType) params.journal_type = journalType
      if (status) params.status = status
      if (search) params.search = search
      if (fromDate) params.entry_date_after = fromDate
      if (toDate) params.entry_date_before = toDate
      const res = await getJournalEntries(params)
      setEntries(res.results)
      setTotalCount(res.count)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load journal entries')
    } finally {
      setLoading(false)
    }
  }, [page, journalType, status, search, fromDate, toDate])

  useEffect(() => {
    const token = getToken()
    if (!token) { router.replace('/login'); return }
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load])

  const totalPages = Math.ceil(totalCount / PAGE_SIZE)

  async function exportAllToCsv() {
    // Pull every page that matches the current filters, then download.
    setLoading(true)
    try {
      const collected: typeof entries = []
      let p = 1
      while (true) {
        const params: Record<string, string> = {
          page: String(p),
          page_size: String(200),
          ordering: '-entry_date',
        }
        if (journalType) params.journal_type = journalType
        if (status) params.status = status
        if (search) params.search = search
        if (fromDate) params.entry_date_after = fromDate
        if (toDate) params.entry_date_before = toDate
        const res = await getJournalEntries(params)
        collected.push(...res.results)
        if (res.results.length < 200 || collected.length >= res.count) break
        p += 1
        if (p > 50) break // safety cap (10,000 rows)
      }
      const header = ['Entry #', 'Date', 'Type', 'Status', 'RP?', 'Currency',
        'Description', 'Created by', 'Submitted by', 'Submitted at',
        'Approved by', 'Approved at', 'Posted Date', 'Lines']
      const rows = collected.map(e => [
        e.entry_number, e.entry_date, e.journal_type, e.status,
        e.is_related_party === true ? 'Yes' : e.is_related_party === false ? 'No' : '—',
        e.currency, e.description || '',
        e.created_by_username || '', e.submitted_by_username || '', e.submitted_at || '',
        e.approved_by_username || '', e.approved_at || '', e.posted_date || '',
        String(e.line_count ?? ''),
      ])
      const csv = [header, ...rows]
        .map(r => r.map(c => `"${String(c).replace(/"/g, '""')}"`).join(','))
        .join('\n')
      const blob = new Blob([csv], { type: 'text/csv' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `journal-entries-${localYmd(new Date())}.csv`
      a.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'CSV export failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Journal Entries"
        breadcrumbs={[{ label: 'Reporting' }, { label: 'Journal Entries' }]}
        actions={
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={exportAllToCsv} disabled={loading}>
              Export CSV
            </Button>
            {/* CFO 2026-09-01: there was no way to create a JE from this page.
                The create tool is Smart Entry (/quick-entry) — link to it here. */}
            <Button size="sm" onClick={() => router.push('/quick-entry')}>
              <Plus className="w-4 h-4 mr-1" /> New Entry
            </Button>
          </div>
        }
      />

      <div className="flex-1 p-6 space-y-4">
        {/* Page hero — rebuilt Omni screens (2026-09-02). */}
        <div>
          <div className="flex items-center gap-2 text-[10.5px] font-semibold uppercase tracking-[0.11em]" style={{ color: theme.orangeText }}>
            <span className="w-[5px] h-[5px] rounded-full" style={{ background: theme.orange }} />
            Accounting &amp; Control
          </div>
          <h1 className="mt-1.5 text-[25px] leading-tight font-bold tracking-[-0.03em]" style={{ color: theme.text }}>Journal Entries</h1>
          <p className="mt-1.5 text-sm" style={{ color: theme.t2 }}>Every entry in the ledger — search it, filter it, or start a new one.</p>
        </div>
        {/* BUG-018 (Oprah 3a, 2026-06-05): clarify the two JE numbering series.
            Posted numbers are NEVER renumbered — this only labels them. */}
        <div
          className="text-xs rounded-md px-3 py-2"
          style={{ background: theme.g50, border: `1px solid ${theme.g200}`, color: theme.t2 }}
        >
          <b>JE numbering:</b>{' '}
          <code>JE-&lt;YEAR&gt;-#####</code> = group / legacy series (incl. reversals) &nbsp;·&nbsp;{' '}
          <code>JE-&lt;ENTITY&gt;-&lt;YEAR&gt;-#####</code> = per-company series (e.g. JE-ADIC-2026-000123).
          Existing posted numbers are retained as-is.
        </div>
        {/* Filter bar */}
        <div className="flex flex-wrap gap-3">
          <div className="relative flex-1 min-w-[200px] max-w-sm">
            <Search
              className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 pointer-events-none"
              style={{ color: theme.t3 }}
              strokeWidth={1.5}
            />
            <input
              type="text"
              placeholder="Search entry # or description..."
              value={search}
              onChange={(e) => { setSearch(e.target.value); setPage(1) }}
              className="h-10 rounded-md px-3 pl-9 text-sm w-full focus:outline-none transition-all"
              style={{
                background: theme.card,
                border: `1px solid ${theme.g200}`,
                color: theme.text,
              }}
            />
          </div>

          <div className="flex items-center gap-2">
            <label className="text-xs whitespace-nowrap font-medium" style={{ color: theme.t2 }}>From</label>
            <input
              type="date"
              aria-label="From date"
              value={fromDate}
              onChange={(e) => { setFromDate(e.target.value); setPage(1) }}
              className="h-10 rounded-md px-3 text-sm focus:outline-none transition-all"
              style={{
                background: theme.card,
                border: `1px solid ${theme.g200}`,
                color: theme.text,
              }}
            />
          </div>

          <div className="flex items-center gap-2">
            <label className="text-xs whitespace-nowrap font-medium" style={{ color: theme.t2 }}>To</label>
            <input
              type="date"
              aria-label="To date"
              value={toDate}
              onChange={(e) => { setToDate(e.target.value); setPage(1) }}
              className="h-10 rounded-md px-3 text-sm focus:outline-none transition-all"
              style={{
                background: theme.card,
                border: `1px solid ${theme.g200}`,
                color: theme.text,
              }}
            />
          </div>

          <select
            aria-label="Journal type"
            value={journalType}
            onChange={(e) => { setJournalType(e.target.value); setPage(1) }}
            className="h-10 rounded-md px-3 text-sm focus:outline-none transition-all"
            style={{
              background: theme.card,
              border: `1px solid ${theme.g200}`,
              color: theme.text,
            }}
          >
            {JOURNAL_TYPE_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>

          <select
            aria-label="Status"
            value={status}
            onChange={(e) => { setStatus(e.target.value); setPage(1) }}
            className="h-10 rounded-md px-3 text-sm focus:outline-none transition-all"
            style={{
              background: theme.card,
              border: `1px solid ${theme.g200}`,
              color: theme.text,
            }}
          >
            {STATUS_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        </div>

        {error && (
          <div className="rounded-lg p-3 flex items-center gap-2" style={{ background: theme.erB, border: `1px solid ${theme.er}20` }}>
            <AlertCircle className="w-4 h-4 flex-shrink-0" style={{ color: theme.er }} />
            <p className="text-sm" style={{ color: theme.er }}>{error}</p>
          </div>
        )}

        {/* Table */}
        <Card>
          <CardContent className="p-0">
            {loading ? (
              <LoadingTable rows={8} cols={7} />
            ) : (
              <>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm border-collapse">
                    <thead style={{ background: theme.g50, borderBottom: `2px solid ${theme.g200}` }}>
                      <tr>
                        {['Entry #', 'Date', 'Description', 'Type', 'Status', 'Lines', 'Source'].map((h, i) => (
                          <th
                            key={h}
                            className={cn(
                              'px-4 py-3 text-xs font-semibold uppercase tracking-wider whitespace-nowrap',
                              i === 5 ? 'text-center' : 'text-left',
                            )}
                            style={{ color: theme.t2 }}
                          >
                            {h}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody style={{ background: theme.card }}>
                      {entries.length === 0 ? (
                        <tr>
                          <td colSpan={7} className="px-4 py-16 text-center">
                            <BookOpen className="w-8 h-8 mx-auto mb-2" style={{ color: theme.g200 }} />
                            <p className="text-sm font-medium" style={{ color: theme.t2 }}>No journal entries found</p>
                          </td>
                        </tr>
                      ) : (
                        entries.map((entry) => (
                          <tr
                            key={entry.id}
                            onClick={() => router.push(`/journal-entries/${entry.id}`)}
                            className="cursor-pointer transition-colors hover:opacity-90"
                            style={{ borderBottom: `1px solid ${theme.g200}` }}
                            onMouseEnter={(e) => { e.currentTarget.style.background = theme.oL }}
                            onMouseLeave={(e) => { e.currentTarget.style.background = '' }}
                          >
                            <td className="px-4 py-3">
                              <span
                                className="font-mono text-xs font-semibold"
                                style={{ color: theme.orange }}
                              >
                                {entry.entry_number}
                              </span>
                            </td>
                            <td className="px-4 py-3 text-xs" style={{ color: theme.t2 }}>
                              {formatDate(entry.entry_date)}
                            </td>
                            <td className="px-4 py-3 max-w-[300px] truncate" style={{ color: theme.text }}>
                              {entry.description || '—'}
                            </td>
                            <td className="px-4 py-3">
                              <JournalTypeBadge type={entry.journal_type} theme={theme} />
                            </td>
                            <td className="px-4 py-3">
                              <StatusBadgeJE status={entry.status} theme={theme} />
                            </td>
                            <td className="px-4 py-3 text-center font-mono text-xs" style={{ color: theme.t2 }}>
                              {entry.line_count}
                            </td>
                            <td className="px-4 py-3 text-xs" style={{ color: theme.t3 }}>
                              {entry.source_type || '—'}
                            </td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                </div>

                {/* Pagination */}
                {totalCount > PAGE_SIZE && (
                  <div
                    className="flex items-center justify-between px-4 py-3"
                    style={{ borderTop: `1px solid ${theme.g200}` }}
                  >
                    <p className="text-xs" style={{ color: theme.t3 }}>
                      Showing {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, totalCount)} of {totalCount}
                    </p>
                    <div className="flex items-center gap-2">
                      <Button
                        variant="ghost"
                        size="sm"
                        leftIcon={<ChevronLeft className="w-3.5 h-3.5" />}
                        disabled={page === 1}
                        onClick={() => setPage(page - 1)}
                      >
                        Prev
                      </Button>
                      <span className="text-xs" style={{ color: theme.t2 }}>{page} / {totalPages}</span>
                      <Button
                        variant="ghost"
                        size="sm"
                        rightIcon={<ChevronRight className="w-3.5 h-3.5" />}
                        disabled={page === totalPages}
                        onClick={() => setPage(page + 1)}
                      >
                        Next
                      </Button>
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
