'use client'

/**
 * /health/service-providers — ADH service-provider network registry.
 *
 * The operational master of Alpha Direct Health's provider network: who is in
 * the network, their AFA registration / contract status, and whether they are
 * ready to accept ADH clients. Sourced from ADH's own working spreadsheet via a
 * preview-then-commit import; readiness (AFA-registered + QC-confirmed) is
 * derived, never hand-typed. Access is gated on the server (403).
 *
 * This is INTERNAL: it holds provider contact details. It is NOT the public
 * member-facing directory (that lives at /health-care/providers, no emails).
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Loader2, RefreshCw, Upload, Download, Search, CheckCircle2, AlertTriangle,
  Building2, ShieldCheck, ClipboardCheck, X, FileSpreadsheet,
} from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { apiFetch, apiFetchBinary } from '@/lib/api'

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'

interface Provider {
  id: string
  practice_number: string
  name: string
  discipline: string
  town: string
  email: string
  contact_number: string
  location: string
  contract_status: string
  afa_registered: string
  welcome_pack: string
  sticker_displayed: string
  provider_orientation: string
  qc_confirmed: boolean
  qc_date: string
  adh_ready: boolean
  adh_acceptance: string
  ready_mismatch: boolean
  vendor_system: string
  onboarding_link: string
  date_contacted: string
  comment: string
  is_active: boolean
  last_imported_at: string
}
interface Counts {
  total: number; afa_registered: number; afa_pending: number
  adh_ready: number; registered_not_qc: number; mismatches: number
  pending_applications: number
  by_discipline: Record<string, number>
}
interface Application {
  id: string; name: string; discipline: string; town: string
  contact_number: string; email: string; practice_number: string
  note: string; status: string; created_at: string
}
interface ListResp { counts: Counts; results: Provider[] }
interface PreviewResp {
  total_rows: number
  new: { practice_number: string; name: string; values: Record<string, string> }[]
  changed: { practice_number: string; name: string; changes: Record<string, { old: string; new: string }>; values: Record<string, string> }[]
  unchanged: number
  duplicates: string[]
  warnings: string[]
  source_file: string
}

const READINESS_TABS: { key: string; label: string }[] = [
  { key: '', label: 'All' },
  { key: 'ready', label: 'ADH-ready' },
  { key: 'afa_registered', label: 'AFA-registered' },
  { key: 'pending', label: 'AFA pending' },
  { key: 'registered_not_qc', label: 'Registered, no QC' },
  { key: 'mismatch', label: 'Readiness mismatch' },
]

function Tile({ label, value, tone }: { label: string; value: number; tone?: 'ok' | 'warn' }) {
  const color = tone === 'ok' ? '#1B6B36' : tone === 'warn' ? '#A11B1B' : NAVY
  return (
    <div className="rounded-xl border bg-white p-4" style={{ borderColor: '#E6E8EC' }}>
      <div className="text-2xl font-semibold" style={{ color }}>{value}</div>
      <div className="mt-0.5 text-xs text-gray-500">{label}</div>
    </div>
  )
}

function Chip({ text, bg, fg }: { text: string; bg: string; fg: string }) {
  return (
    <span className="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium"
          style={{ background: bg, color: fg }}>{text}</span>
  )
}

function ReadyBadge({ p }: { p: Provider }) {
  if (p.adh_ready) return <Chip text="Ready" bg="#E7F6EC" fg="#1B6B36" />
  if (p.afa_registered === 'Yes') return <Chip text="Reg · no QC" bg="#FEF3E2" fg="#8A5A00" />
  if (p.afa_registered === 'Pending') return <Chip text="AFA signing" bg="#E8F0FE" fg="#1A4B8F" />
  return <Chip text="Not ready" bg="#F3F4F6" fg="#6B7280" />
}

export default function ServiceProvidersPage() {
  const [counts, setCounts] = useState<Counts | null>(null)
  const [rows, setRows] = useState<Provider[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [q, setQ] = useState('')
  const [readiness, setReadiness] = useState('')
  const [savingId, setSavingId] = useState<string | null>(null)
  // 288 providers in one table made a 40,000px page and pushed the QC column
  // off-screen. Page it, and keep the QC action pinned to the right edge.
  const [page, setPage] = useState(1)

  // import flow
  const [preview, setPreview] = useState<PreviewResp | null>(null)
  const [importing, setImporting] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  // applications (from the public apply link)
  const [apps, setApps] = useState<Application[]>([])
  const [showApps, setShowApps] = useState(false)
  const applyUrl = typeof window !== 'undefined' ? `${window.location.origin}/apply` : '/apply'

  // evening exec-dashboard switch
  const [sw, setSw] = useState<{ enabled: boolean; updated_at: string; updated_by: string; last_import_at: string } | null>(null)
  const [swBusy, setSwBusy] = useState(false)
  const loadSwitch = useCallback(async () => {
    try { setSw(await apiFetch('/health/service-providers/dashboard-switch/')) } catch { /* non-fatal */ }
  }, [])
  const toggleSwitch = async () => {
    if (!sw) return
    setSwBusy(true)
    try {
      const r = await apiFetch<{ enabled: boolean; updated_at: string; updated_by: string; last_import_at: string }>(
        '/health/service-providers/dashboard-switch/',
        { method: 'POST', body: JSON.stringify({ enabled: !sw.enabled }) })
      setSw(r)
      setNotice(r.enabled ? 'Evening dashboard is ON — it will send at 6pm.' : 'Evening dashboard is OFF.')
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Could not change the switch.')
    } finally { setSwBusy(false) }
  }
  const fmtDate = (s: string) => s ? new Date(s).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }) : '—'

  const loadApps = useCallback(async () => {
    try {
      const r = await apiFetch<{ results: Application[] }>(
        '/health/service-providers/applications/?status=pending')
      setApps(r.results)
    } catch { /* non-fatal */ }
  }, [])

  const reviewApp = async (id: string, status: 'accepted' | 'declined') => {
    try {
      await apiFetch(`/health/service-providers/applications/${id}/`,
        { method: 'PATCH', body: JSON.stringify({ status }) })
      setApps(a => a.filter(x => x.id !== id))
      const data = await apiFetch<ListResp>('/health/service-providers/')
      setCounts(data.counts)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Could not update the application.')
    }
  }

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const params = new URLSearchParams()
      if (q.trim()) params.set('q', q.trim())
      if (readiness) params.set('readiness', readiness)
      const data = await apiFetch<ListResp>(`/health/service-providers/?${params.toString()}`)
      setCounts(data.counts)
      setRows(data.results)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Could not load providers.')
    } finally {
      setLoading(false)
    }
  }, [q, readiness])

  useEffect(() => { load() }, [load])
  useEffect(() => { setPage(1) }, [q, readiness, rows.length])

  const PAGE_SIZE = 50
  const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE))
  const shown = Math.min(page, pageCount)
  const firstOnPage = (shown - 1) * PAGE_SIZE
  const pageRows = rows.slice(firstOnPage, firstOnPage + PAGE_SIZE)
  useEffect(() => { loadApps() }, [loadApps])
  useEffect(() => { loadSwitch() }, [loadSwitch])

  const patchProvider = async (p: Provider, body: Record<string, unknown>) => {
    setSavingId(p.id)
    try {
      const r = await apiFetch<{ provider: Provider }>(
        `/health/service-providers/${p.id}/`, { method: 'PATCH', body: JSON.stringify(body) })
      setRows(rs => rs.map(x => x.id === p.id ? r.provider : x))
      // readiness may have changed -> refresh tiles
      const data = await apiFetch<ListResp>('/health/service-providers/')
      setCounts(data.counts)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Save failed.')
    } finally {
      setSavingId(null)
    }
  }

  const onPickFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setImporting(true); setError(null); setNotice(null); setPreview(null)
    try {
      const fd = new FormData()
      fd.append('file', file)
      const p = await apiFetch<PreviewResp>('/health/service-providers/import/preview/',
        { method: 'POST', body: fd })
      setPreview(p)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Could not read the file.')
    } finally {
      setImporting(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const commitImport = async () => {
    if (!preview) return
    setImporting(true); setError(null)
    try {
      const rowsToWrite = [
        ...preview.new.map(n => n.values),
        ...preview.changed.map(c => c.values),
      ]
      const r = await apiFetch<{ created: number; updated: number; counts: Counts }>(
        '/health/service-providers/import/commit/',
        { method: 'POST', body: JSON.stringify({ rows: rowsToWrite, source_file: preview.source_file }) })
      setNotice(`Imported: ${r.created} new, ${r.updated} updated.`)
      setPreview(null)
      setCounts(r.counts)
      load()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Import failed.')
    } finally {
      setImporting(false)
    }
  }

  const exportReport = async (filter: string) => {
    try {
      const resp = await apiFetchBinary(`/health/service-providers/export/?filter=${filter}`)
      if (!resp.ok) throw new Error('Export failed.')
      const blob = await resp.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `ADH_service_providers_${filter}.xlsx`
      document.body.appendChild(a); a.click(); a.remove()
      URL.revokeObjectURL(url)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Export failed.')
    }
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <TopBar title="Service Providers" />
      <div className="mx-auto max-w-7xl px-4 pb-24 pt-6">
        <div className="mb-5 flex items-center justify-between gap-3">
          <div className="flex items-center gap-2.5">
            <Building2 size={22} style={{ color: ORANGE }} />
            <div>
              <h1 className="text-xl font-semibold" style={{ color: NAVY }}>Service Providers</h1>
              <p className="text-xs text-gray-500">ADH network registry & readiness</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={() => fileRef.current?.click()}
              className="inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium text-white"
              style={{ background: NAVY }}>
              <Upload size={15} /> Import spreadsheet
            </button>
            <input ref={fileRef} type="file" accept=".xlsx" hidden onChange={onPickFile} />
            <button onClick={load} className="rounded-lg border p-2" style={{ borderColor: '#E6E8EC' }}>
              <RefreshCw size={15} className={loading ? 'animate-spin' : ''} />
            </button>
          </div>
        </div>

        {error && (
          <div className="mb-4 flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">
            <AlertTriangle size={16} className="mt-0.5" /> <span>{error}</span>
          </div>
        )}
        {notice && (
          <div className="mb-4 flex items-center gap-2 rounded-lg border border-green-200 bg-green-50 p-3 text-sm text-green-800">
            <CheckCircle2 size={16} /> <span>{notice}</span>
          </div>
        )}

        {/* evening exec-dashboard switch — big and obvious */}
        {sw && (
          <div className="mb-5 rounded-2xl border-2 p-5"
               style={{ borderColor: sw.enabled ? '#1B8A5A' : '#E6E8EC',
                        background: sw.enabled ? '#F0FBF5' : '#FAFBFC' }}>
            <div className="flex flex-wrap items-center justify-between gap-4">
              <div>
                <div className="flex items-center gap-2">
                  <span className="text-base font-semibold" style={{ color: NAVY }}>
                    Evening dashboard to executives
                  </span>
                  <span className="rounded-full px-2.5 py-0.5 text-xs font-bold"
                        style={sw.enabled
                          ? { background: '#1B8A5A', color: '#fff' }
                          : { background: '#E5E7EB', color: '#6B7280' }}>
                    {sw.enabled ? 'ON' : 'OFF'}
                  </span>
                </div>
                <p className="mt-1 max-w-xl text-sm text-gray-600">
                  When ON, EXCO, the CEO and COO get the 6pm dashboard by email and WhatsApp.
                  Turn it on <b>once you have imported the latest provider file</b>.
                </p>
                <p className="mt-1 text-xs text-gray-500">
                  Latest file imported: <b>{fmtDate(sw.last_import_at)}</b>
                  {sw.updated_by && <> · last changed by {sw.updated_by}</>}
                </p>
              </div>
              <button onClick={toggleSwitch} disabled={swBusy}
                className="inline-flex items-center gap-2 rounded-xl px-5 py-3 text-sm font-bold text-white"
                style={{ background: sw.enabled ? '#B42318' : '#1B8A5A', opacity: swBusy ? 0.6 : 1 }}>
                {swBusy ? <Loader2 size={16} className="animate-spin" /> : null}
                {sw.enabled ? 'Turn OFF' : 'Turn ON'}
              </button>
            </div>
          </div>
        )}

        {/* dashboard tiles */}
        {counts && (
          <div className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            <Tile label="Total providers" value={counts.total} />
            <Tile label="AFA-registered" value={counts.afa_registered} />
            <Tile label="AFA pending" value={counts.afa_pending} />
            <Tile label="ADH-ready" value={counts.adh_ready} tone="ok" />
            <Tile label="Registered, no QC" value={counts.registered_not_qc} tone="warn" />
            <button onClick={() => setShowApps(s => !s)} className="text-left">
              <Tile label="New applications" value={counts.pending_applications}
                    tone={counts.pending_applications ? 'warn' : undefined} />
            </button>
          </div>
        )}

        {/* public apply link + applications review */}
        <div className="mb-5 rounded-xl border bg-white p-4" style={{ borderColor: '#E6E8EC' }}>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="text-sm">
              <span className="font-medium" style={{ color: NAVY }}>Public apply link</span>
              <span className="ml-2 text-gray-500">share with new providers — no login needed</span>
            </div>
            <div className="flex items-center gap-2">
              <code className="rounded bg-gray-50 px-2 py-1 text-xs" style={{ color: NAVY }}>{applyUrl}</code>
              <button onClick={() => navigator.clipboard?.writeText(applyUrl).then(
                        () => setNotice('Apply link copied.'))}
                className="rounded-lg border px-2.5 py-1.5 text-xs font-medium"
                style={{ borderColor: '#E6E8EC', color: NAVY }}>Copy</button>
              <a href={applyUrl} target="_blank" rel="noreferrer"
                className="rounded-lg px-2.5 py-1.5 text-xs font-medium text-white" style={{ background: NAVY }}>
                Open</a>
            </div>
          </div>
          {(showApps || apps.length > 0) && apps.length > 0 && (
            <div className="mt-3 border-t pt-3" style={{ borderColor: '#EEF0F2' }}>
              <div className="mb-2 text-xs font-medium text-gray-500">{apps.length} pending application(s)</div>
              <div className="space-y-2">
                {apps.map(a => (
                  <div key={a.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-gray-50 p-2.5">
                    <div className="text-sm">
                      <span className="font-medium" style={{ color: NAVY }}>{a.name}</span>
                      <span className="text-gray-500"> · {a.discipline || '—'} · {a.town || '—'}</span>
                      <div className="text-xs text-gray-500">{a.email || a.contact_number}{a.practice_number ? ` · #${a.practice_number}` : ''}</div>
                    </div>
                    <div className="flex gap-2">
                      <button onClick={() => reviewApp(a.id, 'accepted')}
                        className="rounded-md px-2.5 py-1 text-xs font-medium" style={{ background: '#E7F6EC', color: '#1B6B36' }}>Accept</button>
                      <button onClick={() => reviewApp(a.id, 'declined')}
                        className="rounded-md px-2.5 py-1 text-xs font-medium" style={{ background: '#FDECEC', color: '#A11B1B' }}>Decline</button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* export bar */}
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <span className="text-xs text-gray-500">Export:</span>
          {[
            { k: 'all', l: 'All' },
            { k: 'afa_registered', l: 'AFA-registered' },
            { k: 'adh_ready', l: 'ADH-ready' },
            { k: 'registered_not_qc', l: "Registered, not QC'd" },
          ].map(x => (
            <button key={x.k} onClick={() => exportReport(x.k)}
              className="inline-flex items-center gap-1 rounded-lg border px-2.5 py-1.5 text-xs font-medium"
              style={{ borderColor: '#E6E8EC', color: NAVY }}>
              <Download size={13} /> {x.l}
            </button>
          ))}
        </div>

        {/* filters */}
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <div className="relative">
            <Search size={15} className="absolute left-2.5 top-2.5 text-gray-400" />
            <input value={q} onChange={e => setQ(e.target.value)}
              placeholder="Search name, practice no., town, email"
              className="w-72 rounded-lg border py-2 pl-8 pr-3 text-sm" style={{ borderColor: '#E6E8EC' }} />
          </div>
          <div className="flex flex-wrap gap-1">
            {READINESS_TABS.map(t => (
              <button key={t.key} onClick={() => setReadiness(t.key)}
                className="rounded-full px-3 py-1.5 text-xs font-medium"
                style={readiness === t.key
                  ? { background: NAVY, color: 'white' }
                  : { background: '#F1F3F5', color: '#374151' }}>
                {t.label}
              </button>
            ))}
          </div>
        </div>

        {/* table */}
        <div className="overflow-x-auto rounded-xl border bg-white" style={{ borderColor: '#E6E8EC' }}>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-xs text-gray-500" style={{ borderColor: '#EEF0F2' }}>
                <th className="px-3 py-2.5">Practice</th>
                <th className="px-3 py-2.5">Provider</th>
                <th className="px-3 py-2.5">Discipline</th>
                <th className="px-3 py-2.5">Town</th>
                <th className="px-3 py-2.5">Contract</th>
                <th className="px-3 py-2.5">Readiness</th>
                <th className="px-3 py-2.5 text-center">Sticker</th>
                <th className="sticky right-0 bg-white px-3 py-2.5 text-center"
                    style={{ boxShadow: '-8px 0 8px -6px rgba(15,23,42,0.18)' }}>QC</th>
              </tr>
            </thead>
            <tbody>
              {loading && (
                <tr><td colSpan={8} className="px-3 py-10 text-center text-gray-400">
                  <Loader2 className="mx-auto animate-spin" /></td></tr>
              )}
              {!loading && rows.length === 0 && (
                <tr><td colSpan={8} className="px-3 py-10 text-center text-gray-400">
                  No providers. Import the spreadsheet to get started.</td></tr>
              )}
              {pageRows.map(p => (
                <tr key={p.id} className="border-b hover:bg-gray-50" style={{ borderColor: '#F3F4F6' }}>
                  <td className="px-3 py-2.5 font-mono text-xs">{p.practice_number}</td>
                  <td className="px-3 py-2.5">
                    <div className="font-medium" style={{ color: NAVY }}>{p.name}</div>
                    {p.ready_mismatch && (
                      <span className="text-[11px] text-amber-700">⚠ manual “{p.adh_acceptance}” ≠ derived</span>
                    )}
                  </td>
                  <td className="px-3 py-2.5 text-gray-600">{p.discipline}</td>
                  <td className="px-3 py-2.5 text-gray-600">{p.town}</td>
                  <td className="px-3 py-2.5 text-gray-600">{p.contract_status || '—'}</td>
                  <td className="px-3 py-2.5"><ReadyBadge p={p} /></td>
                  <td className="px-3 py-2.5 text-center text-xs">{p.sticker_displayed || '—'}</td>
                  <td className="sticky right-0 bg-white px-3 py-2.5 text-center"
                    style={{ boxShadow: '-8px 0 8px -6px rgba(15,23,42,0.18)' }}>
                    <button disabled={savingId === p.id}
                      onClick={() => patchProvider(p, { qc_confirmed: !p.qc_confirmed })}
                      title="Toggle QC confirmed"
                      className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium"
                      style={p.qc_confirmed
                        ? { background: '#E7F6EC', color: '#1B6B36' }
                        : { background: '#F3F4F6', color: '#6B7280' }}>
                      {savingId === p.id ? <Loader2 size={12} className="animate-spin" />
                        : <ClipboardCheck size={12} />}
                      {p.qc_confirmed ? 'Confirmed' : 'Confirm'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!loading && rows.length > 0 && (
            <div className="flex flex-wrap items-center justify-between gap-3 border-t px-3 py-2.5 text-xs text-gray-600"
                 style={{ borderColor: '#EEF0F2' }}>
              <span>
                Showing <b>{firstOnPage + 1}–{firstOnPage + pageRows.length}</b> of <b>{rows.length}</b> providers
              </span>
              {pageCount > 1 && (
                <span className="flex items-center gap-2">
                  <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={shown === 1}
                    className="rounded-md border px-2.5 py-1 font-medium disabled:opacity-40"
                    style={{ borderColor: '#E6E8EC' }}>Previous</button>
                  <span>Page {shown} of {pageCount}</span>
                  <button onClick={() => setPage(p => Math.min(pageCount, p + 1))} disabled={shown === pageCount}
                    className="rounded-md border px-2.5 py-1 font-medium disabled:opacity-40"
                    style={{ borderColor: '#E6E8EC' }}>Next</button>
                </span>
              )}
            </div>
          )}
        </div>
      </div>

      {/* import preview modal */}
      {preview && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="max-h-[85vh] w-full max-w-2xl overflow-y-auto rounded-2xl bg-white p-5">
            <div className="mb-3 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <FileSpreadsheet size={18} style={{ color: ORANGE }} />
                <h2 className="text-base font-semibold" style={{ color: NAVY }}>
                  Review import — {preview.source_file}
                </h2>
              </div>
              <button onClick={() => setPreview(null)}><X size={18} /></button>
            </div>
            <div className="mb-3 grid grid-cols-3 gap-3 text-center">
              <div className="rounded-lg bg-green-50 p-3">
                <div className="text-xl font-semibold text-green-700">{preview.new.length}</div>
                <div className="text-xs text-gray-500">New</div>
              </div>
              <div className="rounded-lg bg-amber-50 p-3">
                <div className="text-xl font-semibold text-amber-700">{preview.changed.length}</div>
                <div className="text-xs text-gray-500">Changed</div>
              </div>
              <div className="rounded-lg bg-gray-50 p-3">
                <div className="text-xl font-semibold text-gray-600">{preview.unchanged}</div>
                <div className="text-xs text-gray-500">Unchanged</div>
              </div>
            </div>
            {preview.warnings.length > 0 && (
              <div className="mb-3 rounded-lg border border-amber-200 bg-amber-50 p-2.5 text-xs text-amber-800">
                {preview.warnings.map((w, i) => <div key={i}>⚠ {w}</div>)}
              </div>
            )}
            {preview.changed.length > 0 && (
              <div className="mb-3 max-h-52 overflow-y-auto rounded-lg border p-2 text-xs" style={{ borderColor: '#EEF0F2' }}>
                {preview.changed.slice(0, 40).map(c => (
                  <div key={c.practice_number} className="border-b py-1.5 last:border-0" style={{ borderColor: '#F3F4F6' }}>
                    <span className="font-medium">{c.name}</span>{' '}
                    <span className="font-mono text-gray-400">{c.practice_number}</span>
                    {Object.entries(c.changes).map(([f, v]) => (
                      <div key={f} className="pl-3 text-gray-500">
                        {f}: <span className="text-gray-400">{v.old || '—'}</span> → <span className="text-gray-700">{v.new || '—'}</span>
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            )}
            <div className="flex justify-end gap-2">
              <button onClick={() => setPreview(null)}
                className="rounded-lg border px-4 py-2 text-sm" style={{ borderColor: '#E6E8EC' }}>Cancel</button>
              <button onClick={commitImport} disabled={importing || (preview.new.length + preview.changed.length === 0)}
                className="inline-flex items-center gap-1.5 rounded-lg px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                style={{ background: ORANGE }}>
                {importing ? <Loader2 size={15} className="animate-spin" /> : <ShieldCheck size={15} />}
                Approve & import
              </button>
            </div>
          </div>
        </div>
      )}
      {importing && !preview && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
          <div className="rounded-xl bg-white px-5 py-4 text-sm"><Loader2 className="mr-2 inline animate-spin" />Reading spreadsheet…</div>
        </div>
      )}
    </div>
  )
}
