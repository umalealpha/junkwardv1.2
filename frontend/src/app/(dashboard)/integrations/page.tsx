'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { API_BASE, SSO_SENTINEL_TOKEN } from '@/lib/api'
import {
  Link2,
  RefreshCw,
  Search,
  ChevronLeft,
  ChevronRight,
  Loader2,
  AlertCircle,
  CheckCircle2,
  X,
  Send,
  Zap,
  Clock,
  Filter,
} from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import axios from 'axios'

// ─── Types ────────────────────────────────────────────────────────────────────

interface IntegrationEvent {
  id: string
  source_system: string
  event_type: string
  status: 'received' | 'processing' | 'processed' | 'failed' | 'skipped'
  received_at: string
  processed_at: string | null
  result_type: string | null
  result_id: string | null
  error_message: string | null
  retry_count: number
  event_data?: Record<string, any>
}

interface PaginatedEvents {
  count: number
  next: string | null
  previous: string | null
  results: IntegrationEvent[]
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function getAxios() {
  // QA-007 fix 2026-06-04: (1) hardcoded http://localhost:8000 (user's own
  // machine in prod) -> same-origin API_BASE. (2) legacy `Token <alpha_token>`
  // is empty under Microsoft SSO -> 401. Async request interceptor attaches the
  // same auth the canonical client uses: MSAL Bearer when SSO on, else DRF token.
  const inst = axios.create({ baseURL: API_BASE })
  inst.interceptors.request.use(async (config) => {
    let auth = ''
    try {
      const { acquireApiToken, SSO_API_CALLS_READY } = await import('@/auth/msal')
      if (SSO_API_CALLS_READY) {
        const b = await acquireApiToken()
        if (b) auth = `Bearer ${b}`
      }
    } catch { /* fall back to legacy token */ }
    if (!auth) {
      // FE-SWEEP swarm 2026-06-08 #17: SSO users have alpha_token ===
      // SSO_SENTINEL_TOKEN. Skip the sentinel so we don't send "Token __sso__".
      const t = localStorage.getItem('alpha_token')
      if (t && t !== SSO_SENTINEL_TOKEN) auth = `Token ${t}`
    }
    if (auth) config.headers.Authorization = auth
    return config
  })
  return inst
}

function fmtDatetime(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toLocaleString('en-GB', {
    day: '2-digit', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

// ─── Status config ────────────────────────────────────────────────────────────

const statusConfig: Record<string, { badge: string; label: string; dot: string }> = {
  received: {
    badge: 'bg-[#EFF6FF] text-[#2563EB] border border-[#BFDBFE]',
    label: 'Received',
    dot: 'bg-[#2563EB]',
  },
  processing: {
    badge: 'bg-[#FFFBEB] text-[#D97706] border border-[#FDE68A]',
    label: 'Processing',
    dot: 'bg-[#D97706]',
  },
  processed: {
    badge: 'bg-[#ECFDF5] text-[#059669] border border-[#A7F3D0]',
    label: 'Processed',
    dot: 'bg-[#059669]',
  },
  failed: {
    badge: 'bg-[#FEF2F2] text-[#DC2626] border border-[#FEE2E2]',
    label: 'Failed',
    dot: 'bg-[#DC2626]',
  },
  skipped: {
    badge: 'bg-[#F3F4F6] text-[#9CA3AF] border border-[#E5E7EB]',
    label: 'Skipped',
    dot: 'bg-[#9CA3AF]',
  },
}

const eventTypeConfig: Record<string, { badge: string; label: string }> = {
  policy_issued:        { badge: 'bg-[#EFF6FF] text-[#2563EB]',  label: 'Policy Issued' },
  claim_approved:       { badge: 'bg-[#FFF7ED] text-[#CC6C00]',  label: 'Claim Approved' },
  commission_calculated:{ badge: 'bg-[#F5F3FF] text-[#7C3AED]',  label: 'Commission Calc.' },
  policy_cancelled:     { badge: 'bg-[#FEF2F2] text-[#DC2626]',  label: 'Policy Cancelled' },
}

function getEventTypeBadge(eventType: string) {
  return eventTypeConfig[eventType] ?? {
    badge: 'bg-[#F3F4F6] text-[#9CA3AF]',
    label: eventType.replace(/_/g, ' '),
  }
}

// ─── Test Event Modal ─────────────────────────────────────────────────────────

interface TestEventModalProps {
  onClose: () => void
  onSuccess: () => void
}

function TestEventModal({ onClose, onSuccess }: TestEventModalProps) {
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)
  const defaultPayload = JSON.stringify(
    {
      source_system: 'graphite',
      event_type: 'policy_issued',
      event_data: {
        policy_number: 'POL-TEST-001',
        policy_type: 'Test Policy',
        insured_name: 'Test Customer',
        insured_graphite_id: 'GFT-TEST-001',
        premium_amount: '1000.00',
        vat_amount: '140.00',
        policy_start: '2026-04-01',
        issue_date: '2026-03-24',
        due_days: 30,
      },
    },
    null, 2
  )
  const [payload, setPayload] = useState(defaultPayload)

  const handleSend = async () => {
    setSubmitting(true)
    setError(null)
    try {
      let data: Record<string, any>
      try {
        data = JSON.parse(payload)
      } catch {
        setError('Invalid JSON payload')
        setSubmitting(false)
        return
      }
      const api = getAxios()
      await api.post('/events/', data)
      setSuccess(true)
      setTimeout(() => { onSuccess(); onClose() }, 1500)
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || 'Failed to send event')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/50 z-[200] flex items-center justify-center p-4">
      <div className="bg-white border border-[#E5E7EB] rounded-xl w-full max-w-2xl shadow-2xl">
        <div className="flex items-center justify-between px-6 py-4 border-b border-[#E5E7EB]">
          <div className="flex items-center gap-2">
            <Send className="w-4 h-4 text-[#F07F00]" />
            <h2 className="text-base font-semibold text-[#0B0B3B]">Send Test Event</h2>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 text-[#9CA3AF] hover:text-[#374151] hover:bg-[#F3F4F6] rounded-lg transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="p-6 space-y-4">
          <div>
            <label className="block text-xs font-medium text-[#374151] mb-1.5">Event Payload (JSON)</label>
            <textarea
              value={payload}
              onChange={(e) => setPayload(e.target.value)}
              rows={18}
              spellCheck={false}
              className="w-full px-3 py-3 bg-[#F9FAFB] border border-[#D1D5DB] rounded-lg text-sm font-mono text-[#111827] focus:outline-none focus:border-[#F07F00] focus:ring-[3px] focus:ring-[rgba(240,127,0,0.1)] resize-none leading-relaxed transition-all"
            />
          </div>

          {error && (
            <div className="flex items-start gap-2 p-3 bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg">
              <AlertCircle className="w-4 h-4 text-[#DC2626] flex-shrink-0 mt-0.5" />
              <p className="text-sm text-[#DC2626]">{error}</p>
            </div>
          )}

          {success && (
            <div className="flex items-center gap-2 p-3 bg-[#ECFDF5] border border-[#A7F3D0] rounded-lg">
              <CheckCircle2 className="w-4 h-4 text-[#059669]" />
              <p className="text-sm text-[#059669]">Test event sent successfully!</p>
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-[#E5E7EB] bg-[#F9FAFB] rounded-b-xl">
          <button
            onClick={onClose}
            className="px-4 py-2 rounded-lg text-sm font-medium text-[#6B7280] hover:text-[#111827] hover:bg-[#F3F4F6] transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSend}
            disabled={submitting || success}
            className={cn(
              'flex items-center gap-2 px-5 py-2 rounded-lg text-sm font-medium transition-colors',
              submitting || success
                ? 'bg-[#E5E7EB] text-[#9CA3AF] cursor-not-allowed'
                : 'bg-[#F07F00] hover:bg-[#CC6C00] text-white'
            )}
          >
            {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
            {submitting ? 'Sending...' : success ? 'Sent!' : 'Send Event'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

const PAGE_SIZE = 20

export default function IntegrationsPage() {
  const router = useRouter()

  const [events, setEvents] = useState<IntegrationEvent[]>([])
  const [count, setCount] = useState(0)
  const [next, setNext] = useState<string | null>(null)
  const [previous, setPrevious] = useState<string | null>(null)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [statusFilter, setStatusFilter] = useState('')
  const [typeFilter, setTypeFilter] = useState('')
  const [search, setSearch] = useState('')
  const [searchInput, setSearchInput] = useState('')

  const [retryingIds, setRetryingIds] = useState<Set<string>>(new Set())
  const [retryResults, setRetryResults] = useState<Record<string, 'ok' | 'err'>>({})
  const [showTestModal, setShowTestModal] = useState(false)

  const loadEvents = useCallback(async (pg: number) => {
    setLoading(true)
    setError(null)
    try {
      const api = getAxios()
      const params: Record<string, string> = {
        ordering: '-received_at',
        page: String(pg),
        page_size: String(PAGE_SIZE),
      }
      if (statusFilter) params.status = statusFilter
      if (typeFilter) params.event_type = typeFilter
      if (search) params.search = search

      const res = await api.get('/events/', { params })
      const data: PaginatedEvents = res.data
      setEvents(data.results)
      setCount(data.count)
      setNext(data.next)
      setPrevious(data.previous)
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || 'Failed to load events')
    } finally {
      setLoading(false)
    }
  }, [statusFilter, typeFilter, search])

  useEffect(() => {
    const token = localStorage.getItem('alpha_token')
    if (!token) { router.push('/login'); return }
    loadEvents(page)
  }, [loadEvents, page, router])

  const handleRetry = async (eventId: string) => {
    setRetryingIds((prev) => new Set(prev).add(eventId))
    try {
      const api = getAxios()
      await api.post(`/events/${eventId}/retry/`)
      setRetryResults((prev) => ({ ...prev, [eventId]: 'ok' }))
      setTimeout(() => loadEvents(page), 1000)
    } catch {
      setRetryResults((prev) => ({ ...prev, [eventId]: 'err' }))
    } finally {
      setRetryingIds((prev) => {
        const next = new Set(prev)
        next.delete(eventId)
        return next
      })
    }
  }

  const applyFilters = () => {
    setSearch(searchInput)
    setPage(1)
  }

  const totalPages = Math.max(1, Math.ceil(count / PAGE_SIZE))

  const processed = events.filter((e) => e.status === 'processed').length
  const failed = events.filter((e) => e.status === 'failed').length
  const pending = events.filter((e) => e.status === 'received' || e.status === 'processing').length

  const STATUS_FILTERS = [
    { value: '', label: 'All' },
    { value: 'received', label: 'Received' },
    { value: 'processed', label: 'Processed' },
    { value: 'failed', label: 'Failed' },
  ]

  const TYPE_FILTERS = [
    { value: '', label: 'All Types' },
    { value: 'policy_issued', label: 'Policy Issued' },
    { value: 'claim_approved', label: 'Claim Approved' },
    { value: 'commission_calculated', label: 'Commission' },
    { value: 'policy_cancelled', label: 'Policy Cancelled' },
  ]

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Integrations"
        breadcrumbs={[{ label: 'Integrations' }]}
        actions={
          <Button
            variant="accent"
            size="sm"
            leftIcon={<Send className="w-3.5 h-3.5" />}
            onClick={() => setShowTestModal(true)}
          >
            Send Test Event
          </Button>
        }
      />

      {showTestModal && (
        <TestEventModal
          onClose={() => setShowTestModal(false)}
          onSuccess={() => { setPage(1); loadEvents(1) }}
        />
      )}

      <div className="flex-1 p-6 space-y-6">
        {/* Stats */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <Card className="p-5">
            <p className="text-xs font-medium text-[#6B7280] uppercase tracking-wider">Total Events</p>
            <p className="text-3xl font-bold text-[#0B0B3B] mt-2">{count}</p>
            <p className="text-xs text-[#9CA3AF] mt-1">in current filter</p>
          </Card>
          <Card className="p-5">
            <p className="text-xs font-medium text-[#6B7280] uppercase tracking-wider">Processed</p>
            <p className="text-3xl font-bold text-[#059669] mt-2">{processed}</p>
            <p className="text-xs text-[#9CA3AF] mt-1">this page</p>
          </Card>
          <Card className="p-5">
            <p className="text-xs font-medium text-[#6B7280] uppercase tracking-wider">Failed</p>
            <p className={cn('text-3xl font-bold mt-2', failed > 0 ? 'text-[#DC2626]' : 'text-[#9CA3AF]')}>{failed}</p>
            <p className="text-xs text-[#9CA3AF] mt-1">this page</p>
          </Card>
          <Card className="p-5">
            <p className="text-xs font-medium text-[#6B7280] uppercase tracking-wider">Pending</p>
            <p className={cn('text-3xl font-bold mt-2', pending > 0 ? 'text-[#D97706]' : 'text-[#9CA3AF]')}>{pending}</p>
            <p className="text-xs text-[#9CA3AF] mt-1">this page</p>
          </Card>
        </div>

        {/* Filters */}
        <Card className="p-4">
          <div className="flex flex-col sm:flex-row gap-3 items-start sm:items-center flex-wrap">
            <div className="flex items-center gap-1.5 flex-shrink-0">
              <Filter className="w-3.5 h-3.5 text-[#9CA3AF]" />
              <span className="text-xs text-[#9CA3AF] font-medium">Filters:</span>
            </div>

            <div className="flex gap-1.5 flex-wrap">
              {STATUS_FILTERS.map((f) => (
                <button
                  key={f.value}
                  onClick={() => { setStatusFilter(f.value); setPage(1) }}
                  className={cn(
                    'px-3 py-1 rounded-lg text-xs font-medium border transition-colors',
                    statusFilter === f.value
                      ? 'bg-[#FFF7ED] text-[#CC6C00] border-[#FED7AA]'
                      : 'bg-white text-[#6B7280] border-[#D1D5DB] hover:bg-[#F9FAFB] hover:text-[#374151]'
                  )}
                >
                  {f.label}
                </button>
              ))}
            </div>

            <div className="h-4 w-px bg-[#E5E7EB] hidden sm:block" />

            <select
              value={typeFilter}
              onChange={(e) => { setTypeFilter(e.target.value); setPage(1) }}
              className="px-3 py-1.5 bg-white border border-[#D1D5DB] rounded-lg text-xs text-[#374151] focus:outline-none focus:border-[#F07F00] focus:ring-[3px] focus:ring-[rgba(240,127,0,0.1)] transition-all"
            >
              {TYPE_FILTERS.map((f) => (
                <option key={f.value} value={f.value}>{f.label}</option>
              ))}
            </select>

            <div className="flex items-center gap-2 flex-1 min-w-0 sm:max-w-xs">
              <div className="relative flex-1">
                <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-[#9CA3AF]" />
                <input
                  type="text"
                  value={searchInput}
                  onChange={(e) => setSearchInput(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && applyFilters()}
                  placeholder="Search events..."
                  className="w-full pl-8 pr-3 py-1.5 bg-white border border-[#D1D5DB] rounded-lg text-xs text-[#111827] placeholder-[#9CA3AF] focus:outline-none focus:border-[#F07F00] focus:ring-[3px] focus:ring-[rgba(240,127,0,0.1)] transition-all"
                />
              </div>
              <button
                onClick={applyFilters}
                className="px-3 py-1.5 bg-white hover:bg-[#F9FAFB] text-[#374151] text-xs rounded-lg border border-[#D1D5DB] transition-colors flex-shrink-0"
              >
                Search
              </button>
            </div>

            <button
              onClick={() => loadEvents(page)}
              disabled={loading}
              className="ml-auto flex items-center gap-1.5 px-3 py-1.5 bg-white hover:bg-[#F9FAFB] text-[#374151] text-xs rounded-lg border border-[#D1D5DB] transition-colors flex-shrink-0"
            >
              <RefreshCw className={cn('w-3.5 h-3.5', loading && 'animate-spin')} />
              Refresh
            </button>
          </div>
        </Card>

        {/* Events table */}
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <CardTitle className="flex items-center gap-2">
                <Link2 className="w-4 h-4 text-[#F07F00]" />
                Event Log
              </CardTitle>
              <span className="text-xs text-[#9CA3AF]">{count} total events</span>
            </div>
          </CardHeader>

          {loading ? (
            <div className="flex items-center justify-center py-16 gap-2 text-[#9CA3AF]">
              <Loader2 className="w-5 h-5 animate-spin" />
              <span className="text-sm">Loading events...</span>
            </div>
          ) : error ? (
            <div className="flex items-center justify-center py-16 gap-2 text-[#DC2626]">
              <AlertCircle className="w-5 h-5" />
              <span className="text-sm">{error}</span>
            </div>
          ) : events.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 gap-3 text-[#9CA3AF]">
              <Zap className="w-8 h-8 text-[#D1D5DB]" />
              <p className="text-sm">No events found</p>
              <button
                onClick={() => setShowTestModal(true)}
                className="text-xs text-[#F07F00] hover:text-[#CC6C00] underline transition-colors"
              >
                Send a test event
              </button>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm border-collapse">
                <thead className="bg-[#F3F4F6] border-b-2 border-[#E5E7EB]">
                  <tr>
                    <th className="text-left px-4 py-3 text-xs font-semibold text-[#374151] uppercase tracking-wider">Received At</th>
                    <th className="text-left px-4 py-3 text-xs font-semibold text-[#374151] uppercase tracking-wider">Source</th>
                    <th className="text-left px-4 py-3 text-xs font-semibold text-[#374151] uppercase tracking-wider">Event Type</th>
                    <th className="text-left px-4 py-3 text-xs font-semibold text-[#374151] uppercase tracking-wider">Status</th>
                    <th className="text-left px-4 py-3 text-xs font-semibold text-[#374151] uppercase tracking-wider">Result</th>
                    <th className="text-left px-4 py-3 text-xs font-semibold text-[#374151] uppercase tracking-wider">Error</th>
                    <th className="text-right px-4 py-3 text-xs font-semibold text-[#374151] uppercase tracking-wider">Retry</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#E5E7EB] bg-white">
                  {events.map((ev) => {
                    const sc = statusConfig[ev.status] ?? statusConfig.received
                    const etc = getEventTypeBadge(ev.event_type)
                    const isRetrying = retryingIds.has(ev.id)
                    const retryResult = retryResults[ev.id]

                    return (
                      <tr
                        key={ev.id}
                        className={cn(
                          'transition-colors hover:bg-[#FFF7ED]',
                          ev.status === 'failed'     && 'border-l-2 border-[#DC2626]',
                          ev.status === 'processing' && 'border-l-2 border-[#D97706]',
                          ev.status === 'processed'  && 'border-l-2 border-[#059669]/20',
                        )}
                      >
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-1.5">
                            <span className={cn('w-1.5 h-1.5 rounded-full flex-shrink-0', sc.dot)} />
                            <span className="text-xs text-[#6B7280] whitespace-nowrap">{fmtDatetime(ev.received_at)}</span>
                          </div>
                        </td>
                        <td className="px-4 py-3">
                          <span className="text-sm text-[#374151] capitalize">{ev.source_system || '—'}</span>
                        </td>
                        <td className="px-4 py-3">
                          <span className={cn('inline-flex px-2 py-0.5 rounded-full text-xs font-medium', etc.badge)}>
                            {etc.label}
                          </span>
                        </td>
                        <td className="px-4 py-3">
                          <span className={cn('inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-medium', sc.badge)}>
                            {ev.status === 'processing' && <Clock className="w-3 h-3" />}
                            {sc.label}
                            {ev.retry_count > 0 && (
                              <span className="text-[#9CA3AF] ml-0.5">({ev.retry_count}x)</span>
                            )}
                          </span>
                        </td>
                        <td className="px-4 py-3">
                          {ev.result_type && ev.result_id ? (
                            <div className="flex flex-col gap-0.5">
                              <span className="text-xs text-[#059669] capitalize">{ev.result_type.replace(/_/g, ' ')}</span>
                              <span className="text-xs text-[#9CA3AF] font-mono">{ev.result_id.slice(0, 8)}…</span>
                            </div>
                          ) : (
                            <span className="text-[#D1D5DB]">—</span>
                          )}
                        </td>
                        <td className="px-4 py-3 max-w-xs">
                          {ev.error_message ? (
                            <span className="text-xs text-[#DC2626] truncate block" title={ev.error_message}>
                              {ev.error_message.slice(0, 60)}{ev.error_message.length > 60 ? '…' : ''}
                            </span>
                          ) : (
                            <span className="text-[#D1D5DB]">—</span>
                          )}
                        </td>
                        <td className="px-4 py-3 text-right">
                          {ev.status === 'failed' ? (
                            retryResult === 'ok' ? (
                              <span className="inline-flex items-center gap-1 text-xs text-[#059669]">
                                <CheckCircle2 className="w-3.5 h-3.5" />
                                Queued
                              </span>
                            ) : retryResult === 'err' ? (
                              <span className="inline-flex items-center gap-1 text-xs text-[#DC2626]">
                                <AlertCircle className="w-3.5 h-3.5" />
                                Failed
                              </span>
                            ) : (
                              <button
                                onClick={() => handleRetry(ev.id)}
                                disabled={isRetrying}
                                className={cn(
                                  'inline-flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium rounded-lg border transition-colors',
                                  isRetrying
                                    ? 'text-[#9CA3AF] border-[#E5E7EB] cursor-not-allowed'
                                    : 'text-[#F07F00] border-[#FED7AA] hover:bg-[#FFF7ED]'
                                )}
                              >
                                {isRetrying ? (
                                  <Loader2 className="w-3 h-3 animate-spin" />
                                ) : (
                                  <RefreshCw className="w-3 h-3" />
                                )}
                                Retry
                              </button>
                            )
                          ) : (
                            <span className="text-[#D1D5DB]">—</span>
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}

          {/* Pagination */}
          {!loading && !error && count > 0 && (
            <div className="flex items-center justify-between px-4 py-3 border-t border-[#E5E7EB] bg-[#F9FAFB]">
              <p className="text-xs text-[#9CA3AF]">
                Page {page} of {totalPages} — {count} total events
              </p>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  disabled={!previous || loading}
                  className={cn(
                    'flex items-center gap-1 px-3 py-1.5 text-xs font-medium rounded-lg border transition-colors',
                    !previous || loading
                      ? 'text-[#D1D5DB] border-[#E5E7EB] cursor-not-allowed'
                      : 'text-[#6B7280] border-[#D1D5DB] hover:bg-[#F9FAFB]'
                  )}
                >
                  <ChevronLeft className="w-3.5 h-3.5" />
                  Prev
                </button>
                <div className="flex items-center gap-1">
                  {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
                    const pageNum = Math.max(1, Math.min(page - 2 + i, totalPages - 4 + i))
                    return (
                      <button
                        key={pageNum}
                        onClick={() => setPage(pageNum)}
                        className={cn(
                          'w-7 h-7 text-xs font-medium rounded-lg border transition-colors',
                          pageNum === page
                            ? 'bg-[#FFF7ED] text-[#CC6C00] border-[#FED7AA]'
                            : 'text-[#6B7280] border-[#D1D5DB] hover:bg-[#F9FAFB]'
                        )}
                      >
                        {pageNum}
                      </button>
                    )
                  })}
                </div>
                <button
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                  disabled={!next || loading}
                  className={cn(
                    'flex items-center gap-1 px-3 py-1.5 text-xs font-medium rounded-lg border transition-colors',
                    !next || loading
                      ? 'text-[#D1D5DB] border-[#E5E7EB] cursor-not-allowed'
                      : 'text-[#6B7280] border-[#D1D5DB] hover:bg-[#F9FAFB]'
                  )}
                >
                  Next
                  <ChevronRight className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          )}
        </Card>
      </div>
    </div>
  )
}
