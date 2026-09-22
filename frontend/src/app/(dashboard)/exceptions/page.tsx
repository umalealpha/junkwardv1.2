'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import {
  getExceptions, getException, acknowledgeException, resolveException,
  dismissException, retryLinker, getExceptionCounts, getToken,
} from '@/lib/api'
import type {
  ExceptionListItem, ExceptionDetail, ExceptionCounts,
} from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  AlertTriangle, AlertCircle, CheckCircle2, X, Search, Eye, Send,
  ShieldAlert, Bell,
} from 'lucide-react'

const PAGE_SIZE = 25

const SEVERITY_STYLES: Record<string, string> = {
  critical: 'bg-red-50 text-red-800 border-red-300',
  high:     'bg-orange-50 text-orange-800 border-orange-300',
  medium:   'bg-amber-50 text-amber-800 border-amber-300',
  low:      'bg-blue-50 text-blue-800 border-blue-300',
}

const STATUS_STYLES: Record<string, string> = {
  open:         'bg-red-50 text-red-800 border-red-300',
  acknowledged: 'bg-amber-50 text-amber-800 border-amber-300',
  in_progress:  'bg-blue-50 text-blue-800 border-blue-300',
  resolved:     'bg-emerald-50 text-emerald-800 border-emerald-300',
  dismissed:    'bg-zinc-100 text-zinc-700 border-zinc-300',
}

const TYPE_LABELS: Record<string, string> = {
  po_bill_mismatch:  'PO ↔ Bill mismatch',
  banking_change:    'Banking detail change',
  ai_fraud_cue:      'AI fraud cue',
  ai_anomaly:        'AI anomaly',
  unmatched_payment: 'Payment vs unmatched bill',
  back_dated_bill:   'Back-dated bill',
  vendor_mismatch:   'Vendor mismatch',
  bill_no_po:        'Vendor bill without PO',
  open_po_at_close:  'Open PO at period close',
  fx_rate_stale:     'Stale FX rate',
  manual:            'Manual',
  other:             'Other',
}

export default function ExceptionsPage() {
  const router = useRouter()
  const [items, setItems]     = useState<ExceptionListItem[]>([])
  const [counts, setCounts]   = useState<ExceptionCounts | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState<string | null>(null)
  const [info, setInfo]       = useState<string | null>(null)
  const [search, setSearch]   = useState('')
  const [statusFilter, setStatusFilter] = useState('open')
  const [severityFilter, setSeverityFilter] = useState('')
  const [typeFilter, setTypeFilter] = useState('')
  const [page, setPage]       = useState(1)
  const [totalCount, setTotalCount] = useState(0)
  const [showDetail, setShowDetail] = useState<ExceptionDetail | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const params: Record<string, string | boolean | number | undefined> = { page }
      if (statusFilter === 'open_only') params.open_only = true
      else if (statusFilter)            params.status    = statusFilter
      if (severityFilter)               params.severity  = severityFilter
      if (typeFilter)                   params.exception_type = typeFilter
      if (search)                       params.search    = search
      const [list, cts] = await Promise.all([
        getExceptions(params as Parameters<typeof getExceptions>[0]),
        getExceptionCounts().catch(() => null),
      ])
      setItems(list.results)
      setTotalCount(list.count)
      if (cts) setCounts(cts)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load exceptions')
    } finally {
      setLoading(false)
    }
  }, [statusFilter, severityFilter, typeFilter, search, page])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load()
  }, [load, router])

  const action = async (fn: () => Promise<unknown>, msg: string) => {
    setError(null); setInfo(null)
    try { await fn(); setInfo(msg); setShowDetail(null); await load() }
    catch (err) { setError(err instanceof Error ? err.message : 'Action failed') }
  }

  const totalPages = Math.max(1, Math.ceil(totalCount / PAGE_SIZE))

  return (
    <div className="min-h-screen bg-[#F8F9FA]">
      <TopBar title="Anomalies" subtitle="Anomalies needing a human eye. Payment exceptions for the committee are on Payment Requests → Exceptions." />

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 space-y-4">
        {/* KPI strip */}
        {counts && (
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            <KpiCard label="Open total"   value={counts.total}    color="navy" icon={<Bell className="w-4 h-4" />} />
            <KpiCard label="Critical"     value={counts.critical} color="red"  icon={<ShieldAlert className="w-4 h-4" />} />
            <KpiCard label="High"         value={counts.high}     color="orange" />
            <KpiCard label="Medium"       value={counts.medium}   color="amber" />
            <KpiCard label="Low"          value={counts.low}      color="blue" />
          </div>
        )}

        <div className="flex items-center justify-between flex-wrap gap-3">
          <div className="flex items-center gap-3 flex-wrap">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
              <input
                type="text" value={search}
                onChange={(e) => { setSearch(e.target.value); setPage(1) }}
                placeholder="Search title or source"
                className="pl-9 pr-3 py-2 border border-gray-300 rounded-md text-sm w-72"
              />
            </div>
            <select value={statusFilter}
              onChange={(e) => { setStatusFilter(e.target.value); setPage(1) }}
              className="px-3 py-2 border border-gray-300 rounded-md text-sm">
              <option value="open_only">Open / Acknowledged / In progress</option>
              <option value="">All statuses</option>
              <option value="open">Open</option>
              <option value="acknowledged">Acknowledged</option>
              <option value="in_progress">In progress</option>
              <option value="resolved">Resolved</option>
              <option value="dismissed">Dismissed</option>
            </select>
            <select value={severityFilter}
              onChange={(e) => { setSeverityFilter(e.target.value); setPage(1) }}
              className="px-3 py-2 border border-gray-300 rounded-md text-sm">
              <option value="">All severities</option>
              <option value="critical">Critical</option>
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
            </select>
            <select value={typeFilter}
              onChange={(e) => { setTypeFilter(e.target.value); setPage(1) }}
              className="px-3 py-2 border border-gray-300 rounded-md text-sm">
              <option value="">All types</option>
              {Object.entries(TYPE_LABELS).map(([k, v]) => (
                <option key={k} value={k}>{v}</option>
              ))}
            </select>
          </div>
        </div>

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

        <Card>
          <CardContent className="p-0">
            {loading ? (
              <div className="p-6 text-gray-500 text-sm">Loading…</div>
            ) : items.length === 0 ? (
              <div className="p-12 text-center text-gray-500">
                <CheckCircle2 className="w-10 h-10 mx-auto mb-2 opacity-40 text-emerald-500" />
                <p className="text-sm">No exceptions matching this filter. ✨</p>
              </div>
            ) : (
              <table className="w-full text-sm">
                <thead className="bg-gray-50 border-b text-xs uppercase text-gray-600">
                  <tr className="text-left">
                    <th className="px-4 py-3">Severity</th>
                    <th className="px-4 py-3">Type</th>
                    <th className="px-4 py-3">Title</th>
                    <th className="px-4 py-3">Source</th>
                    <th className="px-4 py-3">Status</th>
                    <th className="px-4 py-3">Cleared by</th>
                    <th className="px-4 py-3">Raised</th>
                    <th className="px-4 py-3"></th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((x) => (
                    <tr key={x.id} className="border-b border-gray-100 hover:bg-gray-50">
                      <td className="px-4 py-2.5">
                        <span className={`text-xs px-2 py-0.5 rounded-full border ${SEVERITY_STYLES[x.severity] || ''}`}>
                          {x.severity_display}
                        </span>
                      </td>
                      <td className="px-4 py-2.5 text-xs text-gray-600">{x.exception_type_display}</td>
                      <td className="px-4 py-2.5 font-medium">{x.title}</td>
                      <td className="px-4 py-2.5 text-xs text-gray-600">{x.source_label || '—'}</td>
                      <td className="px-4 py-2.5">
                        <span className={`text-xs px-2 py-0.5 rounded-full border ${STATUS_STYLES[x.status] || ''}`}>
                          {x.status_display}
                        </span>
                      </td>
                      <td className="px-4 py-2.5 text-xs text-gray-600">{x.requires_role_display}</td>
                      <td className="px-4 py-2.5 text-xs text-gray-600">
                        {new Date(x.created_at).toLocaleString('en-BW', { dateStyle: 'short', timeStyle: 'short' })}
                      </td>
                      <td className="px-4 py-2.5 text-right">
                        <Button size="sm" variant="outline" onClick={async () => {
                          setShowDetail(await getException(x.id))
                        }}>
                          <Eye className="w-3.5 h-3.5 mr-1" /> View
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </CardContent>
        </Card>

        {totalCount > PAGE_SIZE && (
          <div className="flex justify-between text-sm text-gray-600">
            <span>Page {page} of {totalPages} — {totalCount} total</span>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>Prev</Button>
              <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => setPage(page + 1)}>Next</Button>
            </div>
          </div>
        )}
      </div>

      {showDetail && (
        <DetailModal
          exc={showDetail}
          onClose={() => setShowDetail(null)}
          onAcknowledge={() => action(() => acknowledgeException(showDetail.id), 'Acknowledged')}
          onResolve={(notes) => action(() => resolveException(showDetail.id, notes), 'Resolved')}
          onDismiss={(reason) => action(() => dismissException(showDetail.id, reason), 'Dismissed')}
          onRetryLinker={async () => {
            const r = await retryLinker(showDetail.id)
            setInfo(r.success ? `Linker pinged: ${r.response}` : `Linker dispatch failed: ${r.response}`)
            setShowDetail(await getException(showDetail.id))
          }}
        />
      )}
    </div>
  )
}

function KpiCard({ label, value, color, icon }: {
  label: string; value: number;
  color: 'navy' | 'red' | 'orange' | 'amber' | 'blue';
  icon?: React.ReactNode;
}) {
  const palette: Record<string, string> = {
    navy:   'bg-[#0D1B2A] text-white',
    red:    'bg-red-100 text-red-800',
    orange: 'bg-orange-100 text-orange-800',
    amber:  'bg-amber-100 text-amber-800',
    blue:   'bg-blue-100 text-blue-800',
  }
  return (
    <Card>
      <CardContent className="p-4 flex items-center justify-between">
        <div>
          <div className="text-xs uppercase text-gray-500">{label}</div>
          <div className="text-2xl font-bold mt-1">{value}</div>
        </div>
        <div className={`w-9 h-9 rounded-lg flex items-center justify-center ${palette[color]}`}>
          {icon || <AlertTriangle className="w-4 h-4" />}
        </div>
      </CardContent>
    </Card>
  )
}

function DetailModal({ exc, onClose, onAcknowledge, onResolve, onDismiss, onRetryLinker }: {
  exc: ExceptionDetail;
  onClose: () => void;
  onAcknowledge: () => Promise<void>;
  onResolve: (notes: string) => Promise<void>;
  onDismiss: (reason: string) => Promise<void>;
  onRetryLinker: () => Promise<void>;
}) {
  const [notes, setNotes] = useState('')
  const [reason, setReason] = useState('')
  const [showResolve, setShowResolve] = useState(false)
  const [showDismiss, setShowDismiss] = useState(false)
  const meta = (exc.metadata && typeof exc.metadata === 'object') ? exc.metadata : {}

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-lg max-w-3xl w-full p-6 space-y-3 max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-lg font-medium">{exc.title}</h3>
            <p className="text-xs text-gray-500 mt-0.5">{exc.exception_type_display}</p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="flex flex-wrap gap-2 text-xs">
          <span className={`px-2 py-0.5 rounded-full border ${SEVERITY_STYLES[exc.severity]}`}>
            {exc.severity_display}
          </span>
          <span className={`px-2 py-0.5 rounded-full border ${STATUS_STYLES[exc.status]}`}>
            {exc.status_display}
          </span>
          <span className="px-2 py-0.5 rounded-full bg-gray-100 text-gray-700 border border-gray-200">
            Cleared by: {exc.requires_role_display}
          </span>
          {exc.source_label && (
            <span className="px-2 py-0.5 rounded-full bg-blue-50 text-blue-700 border border-blue-200">
              {exc.source_label}
            </span>
          )}
        </div>

        {exc.description && (
          <div className="text-sm text-gray-800 border-t pt-3 whitespace-pre-wrap">{exc.description}</div>
        )}

        {Object.keys(meta).length > 0 && (
          <div className="border-t pt-3">
            <div className="text-xs uppercase text-gray-500 mb-2">Details</div>
            <div className="grid grid-cols-2 gap-2 text-xs">
              {Object.entries(meta).map(([k, v]) => (
                <div key={k} className="flex">
                  <span className="text-gray-500 mr-2">{k}:</span>
                  <span className="font-mono text-gray-800">{String(v ?? '—')}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {exc.resolution_notes && (
          <div className="text-sm text-emerald-800 bg-emerald-50 border border-emerald-200 rounded p-2">
            <strong>Resolution:</strong> {exc.resolution_notes}
            {exc.resolved_by_username && (
              <span className="text-xs text-emerald-700 ml-2">— {exc.resolved_by_username}</span>
            )}
          </div>
        )}
        {exc.dismissal_reason && (
          <div className="text-sm text-zinc-700 bg-zinc-50 border border-zinc-200 rounded p-2">
            <strong>Dismissed:</strong> {exc.dismissal_reason}
          </div>
        )}

        <div className="border-t pt-3 text-xs text-gray-600">
          {exc.linker_notified_at ? (
            <span>Linker notified at {new Date(exc.linker_notified_at).toLocaleString()} — <span className="font-mono">{exc.linker_response}</span></span>
          ) : (
            <span>Linker not pinged{' '}
              <Button variant="outline" size="sm" className="ml-2" onClick={onRetryLinker}>
                <Send className="w-3.5 h-3.5 mr-1" /> Send to Linker
              </Button>
            </span>
          )}
        </div>

        {exc.is_open && (
          <div className="flex flex-wrap gap-2 pt-3 border-t">
            {exc.status === 'open' && (
              <Button onClick={onAcknowledge} className="bg-amber-700 hover:bg-amber-800 text-white">
                <Bell className="w-4 h-4 mr-1" /> Acknowledge
              </Button>
            )}
            <Button onClick={() => setShowResolve(true)} className="bg-emerald-700 hover:bg-emerald-800 text-white">
              <CheckCircle2 className="w-4 h-4 mr-1" /> Resolve
            </Button>
            <Button variant="outline" onClick={() => setShowDismiss(true)}>
              <X className="w-4 h-4 mr-1" /> Dismiss
            </Button>
          </div>
        )}

        {showResolve && (
          <div className="border border-emerald-200 bg-emerald-50 rounded p-3 space-y-2">
            <div className="text-sm font-medium text-emerald-800">Resolution notes</div>
            <textarea value={notes} onChange={e => setNotes(e.target.value)} rows={3}
              className="w-full p-2 border border-emerald-300 rounded text-sm"
              placeholder="What did you do? Reference the bill/PO/bank account fixed…" />
            <div className="flex justify-end gap-2">
              <Button variant="outline" size="sm" onClick={() => { setShowResolve(false); setNotes('') }}>Cancel</Button>
              <Button size="sm" disabled={!notes.trim()}
                onClick={() => onResolve(notes)}
                className="bg-emerald-700 hover:bg-emerald-800 text-white">
                Confirm resolve
              </Button>
            </div>
          </div>
        )}
        {showDismiss && (
          <div className="border border-zinc-200 bg-zinc-50 rounded p-3 space-y-2">
            <div className="text-sm font-medium text-zinc-800">Dismissal reason</div>
            <p className="text-xs text-zinc-600">
              Use when this is a false positive or no longer relevant.
            </p>
            <textarea value={reason} onChange={e => setReason(e.target.value)} rows={2}
              className="w-full p-2 border border-zinc-300 rounded text-sm" />
            <div className="flex justify-end gap-2">
              <Button variant="outline" size="sm" onClick={() => { setShowDismiss(false); setReason('') }}>Cancel</Button>
              <Button size="sm" disabled={!reason.trim()}
                onClick={() => onDismiss(reason)}
                className="bg-zinc-700 hover:bg-zinc-800 text-white">
                Confirm dismiss
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
