'use client'

import { useEffect, useState, useCallback, useRef } from 'react'
import { useRouter } from 'next/navigation'
import { getToken, SSO_SENTINEL_TOKEN, apiFetch } from '@/lib/api'
import { acquireApiToken, SSO_API_CALLS_READY } from '@/auth/msal'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Search, AlertCircle, ChevronLeft, ChevronRight, Activity, Download, Sparkles } from 'lucide-react'

interface AuditEntry {
  id: string
  table_name: string
  record_id: string
  action: string
  action_display: string
  old_values: Record<string, unknown> | null
  new_values: Record<string, unknown> | null
  user_username: string | null
  ip_address: string | null
  description: string | null
  created_at: string
}

interface PaginatedAudit {
  count: number
  next: string | null
  previous: string | null
  results: AuditEntry[]
}

interface AuditAskResult {
  answer: string
  advice: string
  sources: string[]
  subject?: string
}

const PAGE_SIZE = 50

const ACTION_STYLES: Record<string, string> = {
  create:  'bg-[#ECFDF5] text-[#047857] border-[#A7F3D0]',
  update:  'bg-[#EFF6FF] text-[#1D4ED8] border-[#BFDBFE]',
  delete:  'bg-[#FEF2F2] text-[#B91C1C] border-[#FECACA]',
  post:    'bg-[#FFFBEB] text-[#92400E] border-[#FDE68A]',
  approve: 'bg-[#ECFDF5] text-[#047857] border-[#A7F3D0]',
  reverse: 'bg-[#F3F4F6] text-[#374151] border-[#D1D5DB]',
}

export default function AuditLogPage() {
  const router = useRouter()
  const [entries, setEntries] = useState<AuditEntry[]>([])
  const [count, setCount] = useState(0)
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [tableFilter, setTableFilter] = useState('')
  const [actionFilter, setActionFilter] = useState('')
  const [userFilter, setUserFilter] = useState('')
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<AuditEntry | null>(null)
  const closeBtnRef = useRef<HTMLButtonElement | null>(null)

  // "Ask about changes" — read-only NL Q&A over the user-admin audit trail.
  const [question, setQuestion] = useState('')
  const [asking, setAsking] = useState(false)
  const [askResult, setAskResult] = useState<AuditAskResult | null>(null)
  const [askError, setAskError] = useState<string | null>(null)

  const ask = useCallback(async () => {
    const q = question.trim()
    if (!q) return
    setAsking(true); setAskError(null); setAskResult(null)
    try {
      // Pre-fill with the current page's filters so the answer is scoped to
      // what the operator is looking at.
      const filters: Record<string, string> = {}
      if (userFilter)  filters.username = userFilter
      if (tableFilter) filters.table_name = tableFilter
      if (fromDate)    filters.from_date = fromDate
      if (toDate)      filters.to_date = toDate
      const data = await apiFetch<AuditAskResult>('/admin/audit-ask/', {
        method: 'POST',
        body: JSON.stringify({ question: q, filters }),
      })
      setAskResult(data)
    } catch (err) {
      setAskError(err instanceof Error ? err.message : 'Could not get an answer')
    } finally {
      setAsking(false)
    }
  }, [question, userFilter, tableFilter, fromDate, toDate])

  // a11y: when the modal opens, move focus to the close button so keyboard
  // users land inside the dialog; pressing Escape closes it.
  useEffect(() => {
    if (!selected) return
    closeBtnRef.current?.focus()
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setSelected(null) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [selected])

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const params = new URLSearchParams()
      params.set('page', String(page))
      params.set('page_size', String(PAGE_SIZE))
      if (search)        params.set('search', search)
      if (tableFilter)   params.set('table_name', tableFilter)
      if (actionFilter)  params.set('action', actionFilter)
      if (userFilter)    params.set('user', userFilter)
      if (fromDate)      params.set('from_date', fromDate)
      if (toDate)        params.set('to_date', toDate)
      // Build auth header: prefer MSAL Bearer (SSO) → fall back to legacy
      // DRF Token; ignore the SSO sentinel marker.
      const headers: Record<string, string> = {}
      let bearer: string | null = null
      if (SSO_API_CALLS_READY) bearer = await acquireApiToken()
      if (bearer) {
        headers['Authorization'] = `Bearer ${bearer}`
      } else {
        const token = getToken()
        if (token && token !== SSO_SENTINEL_TOKEN) {
          headers['Authorization'] = `Token ${token}`
        }
      }
      const res = await fetch(`/api/v1/audit-log/?${params}`, { headers })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data: PaginatedAudit = await res.json()
      setEntries(data.results)
      setCount(data.count)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load audit log')
    } finally {
      setLoading(false)
    }
  }, [page, search, tableFilter, actionFilter, userFilter, fromDate, toDate])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load])

  const totalPages = Math.max(1, Math.ceil(count / PAGE_SIZE))

  function exportCsv() {
    const header = ['When', 'Who', 'Action', 'Table', 'Record ID', 'Description', 'IP']
    const rows = entries.map(e => [
      new Date(e.created_at).toISOString(),
      e.user_username || '',
      e.action,
      e.table_name,
      e.record_id,
      (e.description || '').replace(/\n/g, ' '),
      e.ip_address || '',
    ])
    const csv = [header, ...rows]
      .map(r => r.map(c => `"${String(c).replace(/"/g, '""')}"`).join(','))
      .join('\n')
    const blob = new Blob([csv], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `audit-log-page-${page}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Audit Log"
        breadcrumbs={[{ label: 'Audit Log' }]}
        actions={
          <Button variant="outline" size="sm" leftIcon={<Download className="w-3.5 h-3.5" />} onClick={exportCsv} disabled={!entries.length}>
            Export this page (CSV)
          </Button>
        }
      />

      <div className="flex-1 p-6 space-y-4">
        <Card>
          <CardContent className="p-4 grid grid-cols-1 md:grid-cols-6 gap-3 items-end">
            <div className="md:col-span-2 relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[#9CA3AF] pointer-events-none" strokeWidth={1.5} />
              <input type="text" placeholder="Search description, table, user..."
                value={search} onChange={e => { setSearch(e.target.value); setPage(1) }}
                className="w-full h-10 bg-white border border-[#D1D5DB] rounded-md pl-9 pr-3 text-sm" />
            </div>
            <select value={actionFilter} onChange={e => { setActionFilter(e.target.value); setPage(1) }}
              className="h-10 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm">
              <option value="">All actions</option>
              <option value="create">Create</option>
              <option value="update">Update</option>
              <option value="delete">Delete</option>
              <option value="post">Post</option>
              <option value="approve">Approve</option>
              <option value="reverse">Reverse</option>
            </select>
            <input type="text" placeholder="Table (e.g. JournalEntry)"
              value={tableFilter} onChange={e => { setTableFilter(e.target.value); setPage(1) }}
              className="h-10 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm" />
            <input type="text" placeholder="User"
              value={userFilter} onChange={e => { setUserFilter(e.target.value); setPage(1) }}
              className="h-10 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm" />
            <div className="flex gap-1">
              <input type="date" value={fromDate} onChange={e => { setFromDate(e.target.value); setPage(1) }}
                className="flex-1 h-10 bg-white border border-[#D1D5DB] rounded-md px-2 text-xs" />
              <input type="date" value={toDate} onChange={e => { setToDate(e.target.value); setPage(1) }}
                className="flex-1 h-10 bg-white border border-[#D1D5DB] rounded-md px-2 text-xs" />
            </div>
          </CardContent>
        </Card>

        {/* Ask about changes — read-only NL Q&A over the user-admin audit trail */}
        <Card>
          <CardContent className="p-4 space-y-3">
            <div className="flex items-center gap-2">
              <Sparkles className="w-4 h-4 text-[#F4A623]" strokeWidth={1.5} />
              <h2 className="text-sm font-semibold text-[#0D1B2A]">Ask about changes</h2>
            </div>
            <p className="text-xs text-[#6B7280]">
              Plain-English questions about who changed a user&apos;s title, role, company access
              or admin rights — answered only from this audit trail. Read-only.
            </p>
            <div className="flex flex-col md:flex-row gap-2">
              <textarea
                value={question}
                onChange={e => setQuestion(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); ask() } }}
                rows={2}
                placeholder="e.g. Why did kago's company access change this month?"
                className="flex-1 bg-white border border-[#D1D5DB] rounded-md px-3 py-2 text-sm resize-y" />
              <Button onClick={ask} disabled={asking || !question.trim()} className="md:self-start">
                {asking ? 'Asking…' : 'Ask'}
              </Button>
            </div>

            {askError && (
              <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-md p-2 flex items-center gap-2">
                <AlertCircle className="w-4 h-4 text-[#DC2626]" />
                <p className="text-[#DC2626] text-xs">{askError}</p>
              </div>
            )}

            {askResult && (
              <div className="bg-[#F9FAFB] border border-[#E5E7EB] rounded-md p-3 space-y-2">
                <p className="text-sm text-[#0D1B2A] whitespace-pre-wrap">{askResult.answer}</p>
                {askResult.advice && (
                  <p className="text-xs text-[#92400E] bg-[#FFFBEB] border border-[#FDE68A] rounded px-2 py-1">
                    {askResult.advice}
                  </p>
                )}
                {askResult.sources.length > 0 && (
                  <div className="flex flex-wrap items-center gap-1.5 pt-1">
                    <span className="text-xs text-[#6B7280]">Sources:</span>
                    {askResult.sources.map(id => {
                      const row = entries.find(e => e.id === id)
                      return (
                        <button
                          key={id}
                          type="button"
                          onClick={() => { if (row) setSelected(row) }}
                          disabled={!row}
                          title={row ? 'Open this audit entry' : 'On another page'}
                          className="text-xs font-mono px-1.5 py-0.5 rounded border border-[#BFDBFE] bg-[#EFF6FF] text-[#1D4ED8] disabled:opacity-50 disabled:cursor-default">
                          {id.slice(0, 8)}
                        </button>
                      )
                    })}
                  </div>
                )}
              </div>
            )}
          </CardContent>
        </Card>

        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-[#DC2626]" />
            <p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}

        <Card>
          <CardContent className="p-0">
            {loading ? (
              <p className="px-6 py-8 text-center text-sm text-[#6B7280]">Loading…</p>
            ) : entries.length === 0 ? (
              <div className="px-6 py-12 text-center text-[#6B7280]">
                <Activity className="w-10 h-10 mx-auto mb-3 text-[#D1D5DB]" strokeWidth={1.5} />
                <p className="text-sm">No audit entries match these filters.</p>
              </div>
            ) : (
              <>
                <div className="overflow-x-auto max-h-[70vh]">
                  <table className="w-full text-sm border-collapse">
                    <thead className="bg-[#F3F4F6] border-b-2 border-[#E5E7EB] sticky top-0">
                      <tr>
                        <th className="px-3 py-2 text-left text-xs font-semibold text-[#374151] uppercase">When</th>
                        <th className="px-3 py-2 text-left text-xs font-semibold text-[#374151] uppercase">Who</th>
                        <th className="px-3 py-2 text-left text-xs font-semibold text-[#374151] uppercase">Action</th>
                        <th className="px-3 py-2 text-left text-xs font-semibold text-[#374151] uppercase">Table</th>
                        <th className="px-3 py-2 text-left text-xs font-semibold text-[#374151] uppercase">Description</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[#E5E7EB] bg-white">
                      {entries.map(e => (
                        <tr
                          key={e.id}
                          onClick={() => setSelected(e)}
                          onKeyDown={(ev) => {
                            if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); setSelected(e) }
                          }}
                          role="button"
                          tabIndex={0}
                          aria-label={`Open audit entry: ${e.action_display || e.action} on ${e.table_name}`}
                          className={`hover:bg-[#F9FAFB] cursor-pointer focus:outline-none focus:bg-[#FFF7ED] ${askResult?.sources.includes(e.id) ? 'bg-[#FFFBEB]' : ''}`}>
                          <td className="px-3 py-1.5 text-xs text-[#6B7280] whitespace-nowrap">
                            {new Date(e.created_at).toLocaleString('en-BW')}
                          </td>
                          <td className="px-3 py-1.5">{e.user_username || '—'}</td>
                          <td className="px-3 py-1.5">
                            <span className={`inline-block px-2 py-0.5 rounded-md text-xs font-medium border ${ACTION_STYLES[e.action] || ACTION_STYLES.update}`}>
                              {e.action_display || e.action}
                            </span>
                          </td>
                          <td className="px-3 py-1.5 font-mono text-xs text-[#374151]">{e.table_name}</td>
                          <td className="px-3 py-1.5 text-[#374151] max-w-md truncate">{e.description || '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                {totalPages > 1 && (
                  <div className="flex items-center justify-between px-4 py-3 border-t border-[#E5E7EB]">
                    <span className="text-xs text-[#6B7280]">Page {page} of {totalPages} — {count} total entries</span>
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

      {selected && (
        <div
          className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4"
          onClick={() => setSelected(null)}
          role="dialog"
          aria-modal="true"
          aria-label={`Audit entry detail: ${selected.action_display || selected.action} on ${selected.table_name}`}
        >
          <div className="bg-white rounded-lg shadow-xl w-full max-w-2xl p-6 space-y-3 max-h-[90vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
            <h2 className="text-lg font-semibold">{selected.action_display} — {selected.table_name}</h2>
            <p className="text-xs text-[#6B7280]">
              {new Date(selected.created_at).toLocaleString('en-BW')} · {selected.user_username || '—'}{selected.ip_address ? ` · ${selected.ip_address}` : ''}
            </p>
            {selected.description && <p className="text-sm">{selected.description}</p>}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <p className="text-xs uppercase tracking-wider text-[#6B7280] mb-1">Before</p>
                <pre className="bg-[#F9FAFB] border border-[#E5E7EB] rounded p-2 text-xs overflow-auto max-h-72">{selected.old_values ? JSON.stringify(selected.old_values, null, 2) : '—'}</pre>
              </div>
              <div>
                <p className="text-xs uppercase tracking-wider text-[#6B7280] mb-1">After</p>
                <pre className="bg-[#F9FAFB] border border-[#E5E7EB] rounded p-2 text-xs overflow-auto max-h-72">{selected.new_values ? JSON.stringify(selected.new_values, null, 2) : '—'}</pre>
              </div>
            </div>
            <div className="flex justify-end pt-2">
              <Button ref={closeBtnRef} variant="outline" onClick={() => setSelected(null)}>Close</Button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
