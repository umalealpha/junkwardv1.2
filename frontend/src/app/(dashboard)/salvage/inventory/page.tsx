'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { Search, AlertCircle, ChevronRight, Filter, Download, Loader2, Plus, Upload, Camera } from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { apiFetch } from '@/lib/api'
import { localYmd } from '@/lib/utils'

interface SalvageItem {
  id: string
  item_code: string
  part_name: string
  status: string
  condition: string
  asking_price: string
  reserve_price: string
  quantity: number
  location: string
  vehicle_brand_name?: string | null
  vehicle_model_name?: string | null
  vehicle_year?: number | null
  company_code?: string | null
  // The item's main photo (its ordering-0 SalvageImage), already served by
  // SalvageItemListSerializer.primary_image — the list just never showed it.
  primary_image?: string | null
}

interface ListResponse { count: number; results: SalvageItem[] }

const STATUSES = ['', 'available', 'reserved', 'sold', 'on_hold', 'scrapped']
const CONDITIONS = ['', 'excellent', 'good', 'fair', 'poor', 'scrap']

export default function SalvageInventoryPage() {
  const { theme } = useTheme()
  const router = useRouter()
  const [items, setItems] = useState<SalvageItem[]>([])
  const [count, setCount] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('')
  const [condition, setCondition] = useState('')
  const [exporting, setExporting] = useState(false)
  const [exportFormat, setExportFormat] = useState<'csv' | 'xlsx'>('csv')

  const url = useMemo(() => {
    const params = new URLSearchParams()
    if (search)    params.set('search', search)
    if (status)    params.set('status', status)
    if (condition) params.set('condition', condition)
    params.set('page_size', '50')
    return `/salvage-items/?${params.toString()}`
  }, [search, status, condition])

  useEffect(() => {
    setLoading(true)
    setError(null)
    const t = setTimeout(() => {
      apiFetch<ListResponse>(url)
        .then(r => { setItems(r.results || []); setCount(r.count || 0) })
        .catch(e => setError(e instanceof Error ? e.message : 'Failed to load'))
        .finally(() => setLoading(false))
    }, 200)
    return () => clearTimeout(t)
  }, [url])

  // ── Export ─────────────────────────────────────────────────────────────────
  // CFO directive 2026-05-18: the salvage inventory needs a download button.
  // We refetch the FULL filtered list (paging cap raised to 10k) so the
  // export reflects every row the user could see, not just the page on
  // screen, then serialise it as CSV or XLSX in the browser.

  async function handleExport() {
    setExporting(true)
    setError(null)
    try {
      const params = new URLSearchParams()
      if (search)    params.set('search', search)
      if (status)    params.set('status', status)
      if (condition) params.set('condition', condition)
      params.set('page_size', '10000')
      const r = await apiFetch<ListResponse>(`/salvage-items/?${params.toString()}`)
      const rows = r.results || []
      const header = [
        'Item Code', 'Part Name', 'Status', 'Condition',
        'Vehicle Brand', 'Vehicle Model', 'Vehicle Year',
        'Quantity', 'Location', 'Asking Price (BWP)', 'Reserve Price (BWP)',
        'Company',
      ]
      const lines = rows.map(it => [
        it.item_code, it.part_name, it.status, it.condition,
        it.vehicle_brand_name || '', it.vehicle_model_name || '',
        it.vehicle_year ?? '', it.quantity,
        it.location || '', it.asking_price, it.reserve_price,
        it.company_code || '',
      ])
      const stamp = localYmd(new Date())
      const filenameBase = `salvage-inventory-${stamp}`
      if (exportFormat === 'xlsx') {
        const xlsx = await import('xlsx')
        const wb = xlsx.utils.book_new()
        const ws = xlsx.utils.aoa_to_sheet([header, ...lines])
        xlsx.utils.book_append_sheet(wb, ws, 'Inventory')
        xlsx.writeFile(wb, `${filenameBase}.xlsx`)
      } else {
        const csvEscape = (v: any) => {
          const s = String(v ?? '')
          return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
        }
        const csv = [header, ...lines].map(r => r.map(csvEscape).join(',')).join('\n') + '\n'
        const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
        const url2 = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url2
        a.download = `${filenameBase}.csv`
        a.click()
        URL.revokeObjectURL(url2)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Export failed')
    } finally {
      setExporting(false)
    }
  }

  return (
    <div className="min-h-screen" style={{ background: theme.bg }}>
      <TopBar title="Salvage · Inventory" />

      <div className="p-4 lg:p-6 max-w-[1400px] mx-auto space-y-5">
        <header className="flex items-center justify-between flex-wrap gap-3">
          <div>
            <h1 className="font-display text-2xl font-bold" style={{ color: theme.navy }}>
              Inventory
            </h1>
            <p className="text-xs mt-0.5" style={{ color: theme.t2 }}>
              {count} item{count === 1 ? '' : 's'} on hand
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Link
              href="/salvage/inventory/new"
              className="inline-flex items-center gap-1.5 h-9 px-3 rounded-md text-sm font-semibold transition-opacity"
              style={{ background: theme.orange, color: '#fff' }}
            >
              <Plus className="w-3.5 h-3.5" /> New item
            </Link>
            <Link
              href="/salvage/inventory/import"
              className="inline-flex items-center gap-1.5 h-9 px-3 rounded-md text-sm font-semibold transition-opacity"
              style={{ background: theme.card, color: theme.text, border: `1px solid ${theme.cardBdr}` }}
            >
              <Upload className="w-3.5 h-3.5" /> Import
            </Link>
            <select
              value={exportFormat}
              onChange={e => setExportFormat(e.target.value as 'csv' | 'xlsx')}
              disabled={exporting}
              className="h-9 px-2 rounded-md text-sm"
              style={{ background: theme.card, border: `1px solid ${theme.cardBdr}`, color: theme.text }}
              aria-label="Export format"
            >
              <option value="csv">CSV</option>
              <option value="xlsx">XLSX</option>
            </select>
            <button
              type="button"
              onClick={handleExport}
              disabled={exporting || count === 0}
              className="inline-flex items-center gap-1.5 h-9 px-3 rounded-md text-sm font-semibold disabled:opacity-50 transition-opacity"
              style={{ background: theme.card, color: theme.text, border: `1px solid ${theme.cardBdr}` }}
            >
              {exporting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Download className="w-3.5 h-3.5" />}
              {exporting ? 'Exporting…' : `Export ${count}`}
            </button>
          </div>
        </header>

        {/* Filters */}
        <div className="rounded-xl p-4 flex flex-wrap items-center gap-3"
             style={{ background: theme.card, border: `1px solid ${theme.cardBdr}`, boxShadow: theme.cardSh }}>
          <div className="relative flex-1 min-w-[220px]">
            <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2"
                    style={{ color: theme.t3 }} />
            <input
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search code, part, VIN, claim number…"
              className="w-full h-9 pl-9 pr-3 rounded-md text-sm"
              style={{ background: theme.g50, border: `1px solid ${theme.cardBdr}`, color: theme.text }}
            />
          </div>
          <Filter className="w-4 h-4" style={{ color: theme.t3 }} />
          <select value={status} onChange={e => setStatus(e.target.value)}
                  className="h-9 px-2 rounded-md text-sm"
                  style={{ background: theme.g50, border: `1px solid ${theme.cardBdr}`, color: theme.text }}>
            {STATUSES.map(s => (
              <option key={s} value={s}>{s ? s.replaceAll('_', ' ') : 'all statuses'}</option>
            ))}
          </select>
          <select value={condition} onChange={e => setCondition(e.target.value)}
                  className="h-9 px-2 rounded-md text-sm"
                  style={{ background: theme.g50, border: `1px solid ${theme.cardBdr}`, color: theme.text }}>
            {CONDITIONS.map(c => (
              <option key={c} value={c}>{c || 'all conditions'}</option>
            ))}
          </select>
        </div>

        {/* Error */}
        {error && (
          <div className="rounded-md p-3 flex items-start gap-2"
               style={{ background: theme.erB, border: `1px solid ${theme.er}30` }}>
            <AlertCircle className="w-4 h-4 mt-0.5" style={{ color: theme.er }} />
            <div>
              <p className="text-sm font-semibold" style={{ color: theme.er }}>
                Couldn&apos;t load inventory
              </p>
              <p className="text-xs mt-0.5" style={{ color: theme.er, opacity: 0.7 }}>{error}</p>
            </div>
          </div>
        )}

        {/* Table */}
        <div className="rounded-xl overflow-hidden"
             style={{ background: theme.card, border: `1px solid ${theme.cardBdr}`, boxShadow: theme.cardSh }}>
          <table className="w-full text-sm">
            <thead>
              <tr style={{ background: theme.g50, borderBottom: `1px solid ${theme.cardBdr}` }}>
                <th className="px-4 py-3 text-left text-[10px] uppercase tracking-wider font-semibold"
                    style={{ color: theme.t2 }}>Code</th>
                <th className="px-4 py-3 text-left text-[10px] uppercase tracking-wider font-semibold"
                    style={{ color: theme.t2 }}>Part</th>
                <th className="px-4 py-3 text-left text-[10px] uppercase tracking-wider font-semibold"
                    style={{ color: theme.t2 }}>Vehicle</th>
                <th className="px-4 py-3 text-left text-[10px] uppercase tracking-wider font-semibold"
                    style={{ color: theme.t2 }}>Cond.</th>
                <th className="px-4 py-3 text-left text-[10px] uppercase tracking-wider font-semibold"
                    style={{ color: theme.t2 }}>Status</th>
                <th className="px-4 py-3 text-right text-[10px] uppercase tracking-wider font-semibold"
                    style={{ color: theme.t2 }}>Asking</th>
                {/* Headerless until 17-Sep-2026. Bharath could not find the photo
                    upload because the ONLY route to the item page — where the
                    Photos panel lives — was an unlabelled 16px chevron in a
                    nameless column. Name the column and the button. */}
                <th className="px-4 py-3 text-right text-[10px] uppercase tracking-wider font-semibold"
                    style={{ color: theme.t2 }}>Open</th>
              </tr>
            </thead>
            <tbody>
              {loading && (
                <tr><td colSpan={7} className="px-4 py-6 text-center text-sm"
                                  style={{ color: theme.t3 }}>Loading…</td></tr>
              )}
              {!loading && items.length === 0 && (
                <tr><td colSpan={7} className="px-4 py-6 text-center text-sm"
                                  style={{ color: theme.t3 }}>
                  No items match. Try clearing the filters.
                </td></tr>
              )}
              {/* Clicking a row is a mouse convenience only. It carries no
                  role and no tabIndex: the labelled Open link is the real
                  control, and making the row focusable as well would be a
                  nested interactive element that announces every item twice
                  to a screen reader. */}
              {items.map(it => (
                <tr key={it.id}
                    onClick={() => router.push(`/salvage/inventory/${it.id}`)}
                    className="cursor-pointer hover:bg-black/[0.04]"
                    style={{ borderBottom: `1px solid ${theme.cardBdr}` }}>
                  <td className="px-4 py-3 font-mono text-xs" style={{ color: theme.text }}>
                    {it.item_code}
                  </td>
                  {/* The main photo rides in the PART cell rather than a column
                      of its own, so the header count and every colSpan above
                      stay exactly as they were. */}
                  <td className="px-4 py-3" style={{ color: theme.text }}>
                    <div className="flex items-center gap-2.5">
                      {it.primary_image ? (
                        /* eslint-disable-next-line @next/next/no-img-element */
                        <img src={it.primary_image} alt=""
                             className="w-9 h-9 rounded object-cover shrink-0"
                             style={{ border: `1px solid ${theme.cardBdr}` }} />
                      ) : (
                        <span className="w-9 h-9 rounded shrink-0 inline-flex items-center justify-center"
                              style={{ background: theme.g100 }}
                              title="No photo yet — press Open to add photos">
                          <Camera className="w-3.5 h-3.5" style={{ color: theme.t3 }} strokeWidth={1.5} />
                        </span>
                      )}
                      <span className="truncate">{it.part_name}</span>
                    </div>
                  </td>
                  <td className="px-4 py-3 text-xs" style={{ color: theme.t2 }}>
                    {[it.vehicle_brand_name, it.vehicle_model_name, it.vehicle_year]
                      .filter(Boolean).join(' ') || '—'}
                  </td>
                  <td className="px-4 py-3 text-xs capitalize" style={{ color: theme.t2 }}>
                    {it.condition}
                  </td>
                  <td className="px-4 py-3">
                    <StatusPill theme={theme} status={it.status} />
                  </td>
                  <td className="px-4 py-3 text-right font-mono-nums" style={{ color: theme.navy }}>
                    BWP {Number(it.asking_price).toLocaleString('en-BW')}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <Link href={`/salvage/inventory/${it.id}`}
                          onClick={e => e.stopPropagation()}
                          className="inline-flex items-center gap-1 pl-2.5 pr-1.5 py-1 rounded-md text-xs font-medium hover:bg-black/5"
                          style={{ color: theme.navy, border: `1px solid ${theme.cardBdr}` }}>
                      Open
                      <ChevronRight className="w-3.5 h-3.5" />
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

function StatusPill({ theme, status }: { theme: any; status: string }) {
  const map: Record<string, { bg: string; fg: string }> = {
    available: { bg: '#ECFDF5', fg: '#059669' },
    reserved:  { bg: '#FFFBEB', fg: '#D97706' },
    sold:      { bg: '#F5F3FF', fg: '#7C3AED' },
    on_hold:   { bg: theme.g100, fg: theme.t2 },
    scrapped:  { bg: '#FEF2F2', fg: '#DC2626' },
  }
  const c = map[status] || { bg: theme.g100, fg: theme.t2 }
  return (
    <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase tracking-wider"
          style={{ background: c.bg, color: c.fg }}>
      {status.replaceAll('_', ' ')}
    </span>
  )
}
