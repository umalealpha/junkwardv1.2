'use client'

/**
 * Graphite Feeds — a read-only window onto the analytics Graphite's Alpha Brain
 * pushes into Omni each night. Lists every feed; click one to see its rows.
 * Reads only (GET /graphite-feeds/); changes nothing.
 */

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import {
  Database, RefreshCw, Loader2, AlertCircle, X,
  ChevronLeft, ChevronRight, Clock, ArrowRight,
} from 'lucide-react'
import { apiFetch } from '@/lib/api'
import ClaimsBridge, { type ClaimsBridgeData } from '@/components/graphite/ClaimsBridge'
import { TopBar } from '@/components/layout/TopBar'
import { Card } from '@/components/ui/card'
import { ModalPortal } from '@/components/ui/ModalPortal'
import { cn } from '@/lib/utils'

interface Feed {
  dataset: string
  label: string
  row_count: number
  columns: string[]
  received_at: string | null
}
interface FeedsResponse {
  feeds: Feed[]
  count: number
  source: string
}
interface FeedDetail {
  dataset: string
  label: string
  columns: string[]
  total: number
  page: number
  per_page: number
  results: Record<string, unknown>[]
  totals: Record<string, number>
  received_at: string | null
}

function fmt(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('en-GB', {
    day: '2-digit', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

function cell(v: unknown): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'number') return v.toLocaleString('en-GB')
  return String(v)
}

const PER = 50

// ── Claim drill-down (client name + subject, finance/exec only) ────────────────
interface ClaimDrillRow {
  claim_number: string
  status: string
  claim_type: string
  product_name: string
  policy_number: string
  customer_name: string
  damage_cause: string
  total_payment: string
  total_reserve: string
}
interface ClaimDrillResp {
  total: number
  shown: number
  results: ClaimDrillRow[]
  total_payment: string
  total_reserve: string
  last_synced: string | null
}

// Which feeds can drill to the real claims, and how each row maps onto the
// claims-register filters. Returns null for feeds/rows that don't drill.
function drillFor(dataset: string, row: Record<string, unknown>):
  { query: string; title: string; feedCount: number | null } | null {
  const s = (v: unknown) => String(v ?? '').trim()
  if (dataset === 'claims_by_type' && s(row.claim_type)) {
    return {
      query: `claim_type=${encodeURIComponent(s(row.claim_type))}`,
      title: s(row.claim_type),
      feedCount: typeof row.claim_count === 'number' ? row.claim_count : null,
    }
  }
  if (dataset === 'claims_by_group' && s(row.group) && s(row.group) !== 'OTHER') {
    // 'OTHER' is the catch-all bucket (policies not COMG/DOMG/COMD/MIS); a
    // search on "OTHER" matches no policy, so that row simply doesn't drill.
    const cnt = row.claim_count
    return { query: `search=${encodeURIComponent(s(row.group))}`, title: s(row.group),
             feedCount: typeof cnt === 'number' ? cnt : null }
  }
  if (dataset === 'major_claims' && s(row.claim_no)) {
    return { query: `search=${encodeURIComponent(s(row.claim_no))}`, title: s(row.claim_no), feedCount: 1 }
  }
  return null
}

function money(v: string | number): string {
  const n = typeof v === 'number' ? v : Number(String(v).replace(/,/g, ''))
  return Number.isNaN(n) ? String(v) : n.toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

// The claims behind a feed row: client name + subject, from the claims register.
function DrillBody({ drill, data, loading, error }: {
  drill: { title: string; feedCount: number | null }
  data: ClaimDrillResp | null
  loading: boolean
  error: string | null
}) {
  if (loading) {
    return (
      <div className="flex items-center justify-center py-16 gap-2 text-[#9CA3AF]">
        <Loader2 className="w-5 h-5 animate-spin" /><span className="text-sm">Loading claims…</span>
      </div>
    )
  }
  if (error) {
    return (
      <div className="flex items-center justify-center py-16 gap-2 text-[#DC2626]">
        <AlertCircle className="w-5 h-5" /><span className="text-sm">{error}</span>
      </div>
    )
  }
  if (!data) return null
  return (
    <>
      {drill.feedCount !== null && drill.feedCount !== data.total && (
        <div className="mx-4 mt-3 mb-1 rounded-md bg-[#FFF7ED] border border-[#FED7AA] px-3 py-2 text-xs text-[#9A3412]">
          The nightly summary counted <b>{drill.feedCount.toLocaleString('en-GB')}</b> here; the claims
          register holds <b>{data.total.toLocaleString('en-GB')}</b> of this type. This gap is a known
          feed issue under review.
        </div>
      )}
      {data.results.length > 0 ? (
        <table className="w-full text-sm border-collapse">
          <thead className="bg-[#F3F4F6] border-b-2 border-[#E5E7EB] sticky top-0">
            <tr>
              {['Claim #', 'Client', 'Subject', 'Product', 'Status', 'Payment', 'Reserve'].map((h) => (
                <th key={h} className={cn('px-4 py-3 text-xs font-semibold text-[#374151] uppercase tracking-wider whitespace-nowrap',
                  h === 'Payment' || h === 'Reserve' ? 'text-right' : 'text-left')}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-[#E5E7EB] bg-white">
            {data.results.map((c, i) => (
              <tr key={i} className="hover:bg-[#FFF7ED]">
                <td className="px-4 py-2.5 text-[#374151] whitespace-nowrap">{c.claim_number || '—'}</td>
                <td className="px-4 py-2.5 text-[#0B0B3B] font-medium whitespace-nowrap">{c.customer_name || '—'}</td>
                <td className="px-4 py-2.5 text-[#374151] max-w-xs truncate" title={c.damage_cause}>{c.damage_cause || '—'}</td>
                <td className="px-4 py-2.5 text-[#6B7280] whitespace-nowrap">{c.product_name || '—'}</td>
                <td className="px-4 py-2.5 text-[#6B7280] whitespace-nowrap">{c.status || '—'}</td>
                <td className="px-4 py-2.5 text-[#374151] text-right whitespace-nowrap">{money(c.total_payment)}</td>
                <td className="px-4 py-2.5 text-[#374151] text-right whitespace-nowrap">{money(c.total_reserve)}</td>
              </tr>
            ))}
          </tbody>
          <tfoot className="sticky bottom-0 bg-[#F3F4F6] border-t-2 border-[#D1D5DB]">
            <tr>
              <td className="px-4 py-3 font-semibold text-[#6B7280] whitespace-nowrap" colSpan={5}>
                Total · {data.total.toLocaleString('en-GB')} claims{data.shown < data.total ? ` (showing ${data.shown})` : ''}
              </td>
              <td className="px-4 py-3 font-bold text-[#0B0B3B] text-right whitespace-nowrap">{money(data.total_payment)}</td>
              <td className="px-4 py-3 font-bold text-[#0B0B3B] text-right whitespace-nowrap">{money(data.total_reserve)}</td>
            </tr>
          </tfoot>
        </table>
      ) : (
        <div className="flex items-center justify-center py-16 text-sm text-[#9CA3AF]">
          No claims found for this row in the register.
        </div>
      )}
    </>
  )
}

// ── Detail modal ──────────────────────────────────────────────────────────────

function FeedModal({ dataset, onClose }: { dataset: string; onClose: () => void }) {
  const [data, setData] = useState<FeedDetail | null>(null)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Claim drill-down state (client name + subject behind a claims row).
  const [drill, setDrill] = useState<{ query: string; title: string; feedCount: number | null } | null>(null)
  const [drillData, setDrillData] = useState<ClaimDrillResp | null>(null)
  const [drillLoading, setDrillLoading] = useState(false)
  const [drillError, setDrillError] = useState<string | null>(null)

  const openDrill = useCallback(async (d: { query: string; title: string; feedCount: number | null }) => {
    setDrill(d); setDrillData(null); setDrillLoading(true); setDrillError(null)
    try {
      const r = await apiFetch<ClaimDrillResp>(`/graphite-feeds/claims/?${d.query}&per_page=200`)
      setDrillData(r)
    } catch (e) {
      const body = (e as { body?: { detail?: string } })?.body
      setDrillError(body?.detail || (e instanceof Error ? e.message : 'Could not load the claims'))
    } finally {
      setDrillLoading(false)
    }
  }, [])

  const load = useCallback(async (pg: number) => {
    setLoading(true); setError(null)
    try {
      const d = await apiFetch<FeedDetail>(`/graphite-feeds/${dataset}/?page=${pg}&per_page=${PER}`)
      setData(d)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load this feed')
    } finally {
      setLoading(false)
    }
  }, [dataset])

  useEffect(() => { load(page) }, [load, page])

  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.per_page)) : 1

  // Bug 2787b4b6 (CFO 2026-09-01): render at <body> level via ModalPortal so the
  // panel escapes main's z-[1] stacking context — otherwise the body-level toast
  // stack (Toaster, zIndex 9999) and the page behind paint over it. z-[10000]
  // sits above that toast layer. min-w-0 on the scroll body (below) lets a wide
  // table scroll horizontally instead of spilling past the panel's right edge.
  return (
    <ModalPortal>
    <div className="fixed inset-0 bg-black/50 z-[10000] flex items-center justify-center p-4">
      <div className="bg-white border border-[#E5E7EB] rounded-xl w-full max-w-5xl max-h-[88vh] flex flex-col shadow-2xl">
        <div className="flex items-center justify-between px-6 py-4 border-b border-[#E5E7EB]">
          <div className="flex items-center gap-2 min-w-0">
            {drill && (
              <button
                onClick={() => { setDrill(null); setDrillData(null); setDrillError(null) }}
                className="flex items-center gap-1 text-xs font-medium text-[#6B7280] hover:text-[#0B0B3B] mr-1"
              >
                <ChevronLeft className="w-4 h-4" /> Back
              </button>
            )}
            <Database className="w-4 h-4 text-[#F07F00] flex-shrink-0" />
            <h2 className="text-base font-semibold text-[#0B0B3B] truncate">
              {drill ? `${data?.label ?? dataset} · ${drill.title}` : (data?.label ?? dataset)}
            </h2>
            {!drill && data && (
              <span className="text-xs text-[#9CA3AF]">
                {data.total.toLocaleString('en-GB')} rows
              </span>
            )}
            {drill && drillData && (
              <span className="text-xs text-[#9CA3AF] whitespace-nowrap">
                {drillData.total.toLocaleString('en-GB')} claims
              </span>
            )}
          </div>
          <button
            onClick={onClose}
            className="p-1.5 text-[#9CA3AF] hover:text-[#374151] hover:bg-[#F3F4F6] rounded-lg transition-colors flex-shrink-0"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="flex-1 overflow-auto min-w-0">
          {drill ? (
            <DrillBody drill={drill} data={drillData} loading={drillLoading} error={drillError} />
          ) : loading ? (
            <div className="flex items-center justify-center py-16 gap-2 text-[#9CA3AF]">
              <Loader2 className="w-5 h-5 animate-spin" />
              <span className="text-sm">Loading…</span>
            </div>
          ) : error ? (
            <div className="flex items-center justify-center py-16 gap-2 text-[#DC2626]">
              <AlertCircle className="w-5 h-5" />
              <span className="text-sm">{error}</span>
            </div>
          ) : data && data.results.length > 0 ? (
            <>
            {['claims_by_type', 'claims_by_group', 'major_claims'].includes(data.dataset) && (
              <div className="mx-4 mt-3 -mb-1 text-xs text-[#9CA3AF]">
                Click a row to see the claims behind it — with client name and subject (Finance / Exec only).
              </div>
            )}
            <table className="w-full text-sm border-collapse">
              <thead className="bg-[#F3F4F6] border-b-2 border-[#E5E7EB] sticky top-0">
                <tr>
                  {data.columns.map((c) => (
                    <th key={c} className="text-left px-4 py-3 text-xs font-semibold text-[#374151] uppercase tracking-wider whitespace-nowrap">
                      {c.replace(/_/g, ' ')}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-[#E5E7EB] bg-white">
                {data.results.map((row, i) => {
                  const d = drillFor(data.dataset, row)
                  return (
                    <tr
                      key={i}
                      onClick={d ? () => openDrill(d) : undefined}
                      className={cn('transition-colors hover:bg-[#FFF7ED]', d && 'cursor-pointer')}
                    >
                      {data.columns.map((c) => (
                        <td key={c} className="px-4 py-2.5 text-[#374151] whitespace-nowrap">
                          {cell(row[c])}
                        </td>
                      ))}
                    </tr>
                  )
                })}
              </tbody>
              {data.totals && Object.keys(data.totals).length > 0 && (
                <tfoot className="sticky bottom-0 bg-[#F3F4F6] border-t-2 border-[#D1D5DB]">
                  <tr>
                    {(() => {
                      let labelled = false
                      return data.columns.map((c) => {
                        if (c in data.totals) {
                          return (
                            <td key={c} className="px-4 py-3 font-bold text-[#0B0B3B] whitespace-nowrap">
                              {cell(data.totals[c])}
                            </td>
                          )
                        }
                        if (!labelled) {
                          labelled = true
                          return (
                            <td key={c} className="px-4 py-3 font-semibold text-[#6B7280] whitespace-nowrap">
                              Total · {data.total.toLocaleString('en-GB')} rows
                            </td>
                          )
                        }
                        return <td key={c} className="px-4 py-3" />
                      })
                    })()}
                  </tr>
                </tfoot>
              )}
            </table>
            </>
          ) : (
            <div className="flex items-center justify-center py-16 text-sm text-[#9CA3AF]">
              This feed is empty.
            </div>
          )}
        </div>

        {!drill && data && data.total > data.per_page && (
          <div className="flex items-center justify-between px-4 py-3 border-t border-[#E5E7EB] bg-[#F9FAFB] rounded-b-xl">
            <p className="text-xs text-[#9CA3AF]">Page {page} of {totalPages}</p>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page <= 1 || loading}
                className={cn('flex items-center gap-1 px-3 py-1.5 text-xs font-medium rounded-lg border transition-colors',
                  page <= 1 || loading ? 'text-[#D1D5DB] border-[#E5E7EB] cursor-not-allowed' : 'text-[#6B7280] border-[#D1D5DB] hover:bg-[#F9FAFB]')}
              >
                <ChevronLeft className="w-3.5 h-3.5" /> Prev
              </button>
              <button
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={page >= totalPages || loading}
                className={cn('flex items-center gap-1 px-3 py-1.5 text-xs font-medium rounded-lg border transition-colors',
                  page >= totalPages || loading ? 'text-[#D1D5DB] border-[#E5E7EB] cursor-not-allowed' : 'text-[#6B7280] border-[#D1D5DB] hover:bg-[#F9FAFB]')}
              >
                Next <ChevronRight className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
    </ModalPortal>
  )
}

// ── Page ────────────────────────────────────────────────────────────────────

export default function GraphiteFeedsPage() {
  const router = useRouter()
  const [feeds, setFeeds] = useState<Feed[]>([])
  const [source, setSource] = useState('')
  const [bridge, setBridge] = useState<ClaimsBridgeData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [open, setOpen] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const d = await apiFetch<FeedsResponse>('/graphite-feeds/')
      setFeeds(d.feeds)
      setSource(d.source)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load the feeds')
    } finally {
      setLoading(false)
    }
    // The bridge reads live Graphite, so it loads after the cards and can
    // never hold the page blank. A failure just leaves the panel off.
    apiFetch<{ claims_bridge: ClaimsBridgeData | null }>('/graphite-feeds/claims-bridge/')
      .then(b => setBridge(b.claims_bridge ?? null))
      .catch(() => setBridge(null))
  }, [])

  useEffect(() => {
    const token = localStorage.getItem('alpha_token')
    if (!token) { router.push('/login'); return }
    load()
  }, [load, router])

  const lastReceived = feeds.reduce<string | null>((acc, f) => {
    if (!f.received_at) return acc
    if (!acc || f.received_at > acc) return f.received_at
    return acc
  }, null)

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar title="Graphite Feeds" breadcrumbs={[{ label: 'Graphite Feeds' }]} />

      <div className="flex-1 p-6 space-y-6">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <p className="text-sm text-[#6B7280] max-w-2xl">
              Analytics that Graphite&rsquo;s Alpha Brain pushes into Omni each night.
              Read-only, no personal data. Click a feed to see its figures.
            </p>
            {lastReceived && (
              <p className="text-xs text-[#9CA3AF] mt-1 flex items-center gap-1.5">
                <Clock className="w-3 h-3" /> Last received {fmt(lastReceived)}
              </p>
            )}
          </div>
          <button
            onClick={load}
            disabled={loading}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-white hover:bg-[#F9FAFB] text-[#374151] text-xs rounded-lg border border-[#D1D5DB] transition-colors flex-shrink-0"
          >
            <RefreshCw className={cn('w-3.5 h-3.5', loading && 'animate-spin')} /> Refresh
          </button>
        </div>

        {!loading && !error && bridge && <ClaimsBridge bridge={bridge} />}

        {loading ? (
          <div className="flex items-center justify-center py-20 gap-2 text-[#9CA3AF]">
            <Loader2 className="w-5 h-5 animate-spin" />
            <span className="text-sm">Loading feeds…</span>
          </div>
        ) : error ? (
          <div className="flex items-center justify-center py-20 gap-2 text-[#DC2626]">
            <AlertCircle className="w-5 h-5" />
            <span className="text-sm">{error}</span>
          </div>
        ) : feeds.length === 0 ? (
          <Card className="p-10">
            <div className="flex flex-col items-center gap-3 text-[#9CA3AF]">
              <Database className="w-8 h-8 text-[#D1D5DB]" />
              <p className="text-sm">No feeds have arrived yet.</p>
              <p className="text-xs">They appear here once Graphite sends its nightly push.</p>
            </div>
          </Card>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {feeds.map((f) => (
              <button key={f.dataset} onClick={() => setOpen(f.dataset)} className="text-left">
                <Card className="p-5 h-full hover:border-[#F07F00] hover:shadow-md transition-all cursor-pointer group">
                  <div className="flex items-start justify-between">
                    <div className="flex items-center gap-2">
                      <Database className="w-4 h-4 text-[#F07F00]" />
                      <span className="text-sm font-semibold text-[#0B0B3B]">{f.label}</span>
                    </div>
                    <ArrowRight className="w-4 h-4 text-[#D1D5DB] group-hover:text-[#F07F00] transition-colors" />
                  </div>
                  <p className="text-3xl font-bold text-[#0B0B3B] mt-3">
                    {f.row_count.toLocaleString('en-GB')}
                  </p>
                  <p className="text-xs text-[#9CA3AF] mt-1">rows</p>
                  <p className="text-[11px] text-[#9CA3AF] mt-3 truncate" title={f.columns.join(', ')}>
                    {f.columns.length > 0 ? f.columns.join(' · ') : 'no columns'}
                  </p>
                </Card>
              </button>
            ))}
          </div>
        )}

        {source && !loading && !error && (
          <p className="text-[11px] text-[#9CA3AF]">{source}</p>
        )}
      </div>

      {open && <FeedModal dataset={open} onClose={() => setOpen(null)} />}
    </div>
  )
}
