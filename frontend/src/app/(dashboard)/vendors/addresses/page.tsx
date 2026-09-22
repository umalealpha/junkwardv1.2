'use client'

/**
 * /vendors/addresses — Supplier Address editor.
 *
 * CFO / Kao (Claims) 2026-07-10: a simple, gated screen for staff to add or
 * fix a supplier's ADDRESS — one at a time, or by Excel/CSV upload. Address
 * only; not the full vendor admin. Backend: billing/vendor_address.py, gated
 * to the `vendor_editor` group (+ superusers).
 */

import { useEffect, useState, useCallback } from 'react'
import { apiFetch } from '@/lib/api'
import { useCompany } from '@/contexts/CompanyContext'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Search, Upload, Download, CheckCircle2, AlertCircle, Building2, Save } from 'lucide-react'

interface Row { id: string; name: string; company: string; address: string; has_address: boolean }
interface UploadResult {
  company: string; commit: boolean; rows_seen: number
  matched: number; updated: number; not_found: number
  not_found_names: string[]; errors: { row: number; error: string }[]
}

export default function SupplierAddressesPage() {
  const { selected } = useCompany()
  const companyCode = selected?.code || 'ADIC'

  const [allowed, setAllowed] = useState<boolean | null>(null)
  const [rows, setRows] = useState<Row[]>([])
  const [count, setCount] = useState(0)
  const [loading, setLoading] = useState(false)
  const [q, setQ] = useState('')
  const [missingOnly, setMissingOnly] = useState(true)
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [savingId, setSavingId] = useState<string | null>(null)
  const [savedId, setSavedId] = useState<string | null>(null)

  // upload
  const [file, setFile] = useState<File | null>(null)
  const [uploadResult, setUploadResult] = useState<UploadResult | null>(null)
  const [uploadBusy, setUploadBusy] = useState(false)
  const [uploadErr, setUploadErr] = useState<string | null>(null)

  useEffect(() => {
    apiFetch<{ allowed: boolean }>('/vendor-address/access/')
      .then((r) => setAllowed(!!r.allowed))
      .catch(() => setAllowed(false))
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams({ company: companyCode })
      if (q.trim()) params.set('q', q.trim())
      if (missingOnly) params.set('missing_only', 'true')
      const r = await apiFetch<{ count: number; results: Row[] }>(`/vendor-address/list/?${params}`)
      setRows(r.results); setCount(r.count)
      setDraft(Object.fromEntries(r.results.map((x) => [x.id, x.address])))
    } finally { setLoading(false) }
  }, [companyCode, q, missingOnly])

  useEffect(() => { if (allowed) load() }, [allowed, load])

  async function saveOne(id: string) {
    setSavingId(id); setSavedId(null)
    try {
      const updated = await apiFetch<Row>(`/vendor-address/${id}/`, {
        method: 'PATCH',
        body: JSON.stringify({ address: draft[id] ?? '' }),
      })
      setRows((prev) => prev.map((r) => (r.id === id ? updated : r)))
      setSavedId(id); setTimeout(() => setSavedId(null), 2000)
    } finally { setSavingId(null) }
  }

  async function runUpload(commit: boolean) {
    if (!file) return
    setUploadBusy(true); setUploadErr(null)
    try {
      const fd = new FormData()
      fd.append('file', file)
      fd.append('company', companyCode)
      fd.append('commit', commit ? 'true' : 'false')
      const r = await apiFetch<UploadResult>('/vendor-address/upload/', { method: 'POST', body: fd })
      setUploadResult(r)
      if (commit) { setFile(null); load() }
    } catch (e) {
      setUploadErr(e instanceof Error ? e.message : 'Upload failed')
    } finally { setUploadBusy(false) }
  }

  function downloadTemplate() {
    const csv = 'name,address\nMANCON PTY LTD,"P O Box 202464, Gaborone"\nExample Supplier (Pty) Ltd,"Plot 1234, Gaborone"\n'
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }))
    const a = document.createElement('a')
    a.href = url; a.download = 'supplier-addresses-template.csv'; a.click()
    URL.revokeObjectURL(url)
  }

  if (allowed === null) {
    return <div className="p-8 text-sm text-muted-foreground">Checking access…</div>
  }
  if (!allowed) {
    return (
      <div className="p-8">
        <Card><CardContent className="p-6 flex items-start gap-3">
          <AlertCircle className="h-5 w-5 text-amber-500 mt-0.5" />
          <div>
            <p className="font-semibold">No access to the supplier-address editor</p>
            <p className="text-sm text-muted-foreground mt-1">
              Ask the CFO / HR to add you to the supplier-address editors, then reload.
            </p>
          </div>
        </CardContent></Card>
      </div>
    )
  }

  return (
    <div>
      <TopBar />
      <div className="p-6 max-w-5xl mx-auto space-y-6">
        <div>
          <h1 className="text-xl font-semibold flex items-center gap-2">
            <Building2 className="h-5 w-5 text-[#F4A623]" /> Supplier Addresses
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            Add or fix a supplier&apos;s address for <strong>{companyCode}</strong>. Change one below, or upload a list.
          </p>
        </div>

        {/* Upload card */}
        <Card><CardContent className="p-5 space-y-3">
          <div className="flex items-center gap-2 font-medium"><Upload className="h-4 w-4" /> Upload a list (Excel or CSV)</div>
          <p className="text-sm text-muted-foreground">
            Two columns: <code>name</code> and <code>address</code>. We match each name to a supplier in {companyCode} and set its address. Preview first, then apply.
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <Button variant="outline" size="sm" onClick={downloadTemplate}>
              <Download className="h-4 w-4 mr-1" /> Template
            </Button>
            <input type="file" accept=".xlsx,.xls,.csv"
                   onChange={(e) => { setFile(e.target.files?.[0] || null); setUploadResult(null) }}
                   className="text-sm" />
            <Button size="sm" variant="outline" disabled={!file || uploadBusy} onClick={() => runUpload(false)}>
              {uploadBusy ? 'Working…' : 'Preview'}
            </Button>
            <Button size="sm" disabled={!file || uploadBusy || !uploadResult} onClick={() => runUpload(true)}>
              Apply
            </Button>
          </div>
          {uploadErr && <p className="text-sm text-red-600">{uploadErr}</p>}
          {uploadResult && (
            <div className="text-sm rounded-md bg-muted/50 p-3">
              <p>{uploadResult.commit ? 'Applied' : 'Preview'} — rows read {uploadResult.rows_seen},
                {' '}matched {uploadResult.matched}, {uploadResult.commit ? 'updated' : 'to update'} {uploadResult.updated},
                {' '}not found {uploadResult.not_found}.</p>
              {uploadResult.not_found_names.length > 0 && (
                <p className="text-muted-foreground mt-1">Not found (check the name matches {companyCode}): {uploadResult.not_found_names.slice(0, 10).join(', ')}{uploadResult.not_found_names.length > 10 ? '…' : ''}</p>
              )}
              {uploadResult.errors.length > 0 && (
                <p className="text-red-600 mt-1">{uploadResult.errors.length} row error(s): {uploadResult.errors.slice(0, 5).map((e) => `row ${e.row}: ${e.error}`).join('; ')}</p>
              )}
            </div>
          )}
        </CardContent></Card>

        {/* Search + edit */}
        <Card><CardContent className="p-5 space-y-4">
          <div className="flex flex-wrap items-center gap-3">
            <div className="relative flex-1 min-w-[220px]">
              <Search className="h-4 w-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
              <input value={q} onChange={(e) => setQ(e.target.value)}
                     onKeyDown={(e) => e.key === 'Enter' && load()}
                     placeholder="Search supplier name…"
                     className="w-full pl-9 pr-3 py-2 text-sm border rounded-md bg-background" />
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={missingOnly} onChange={(e) => setMissingOnly(e.target.checked)} />
              Only missing address
            </label>
            <Button size="sm" onClick={load} disabled={loading}>{loading ? 'Loading…' : 'Search'}</Button>
          </div>

          <p className="text-xs text-muted-foreground">{count} supplier(s){missingOnly ? ' missing an address' : ''} in {companyCode}{rows.length < count ? ` — showing first ${rows.length}` : ''}.</p>

          <div className="divide-y">
            {rows.map((r) => (
              <div key={r.id} className="py-2.5 flex flex-col sm:flex-row sm:items-center gap-2">
                <div className="sm:w-64 shrink-0 text-sm font-medium truncate" title={r.name}>{r.name}</div>
                <input
                  value={draft[r.id] ?? ''}
                  onChange={(e) => setDraft((d) => ({ ...d, [r.id]: e.target.value }))}
                  placeholder="Add address…"
                  className="flex-1 px-3 py-1.5 text-sm border rounded-md bg-background"
                />
                <Button size="sm" variant={savedId === r.id ? 'success' : 'outline'}
                        disabled={savingId === r.id || (draft[r.id] ?? '') === r.address}
                        onClick={() => saveOne(r.id)} className="shrink-0">
                  {savingId === r.id ? 'Saving…' : savedId === r.id
                    ? <><CheckCircle2 className="h-4 w-4 mr-1" /> Saved</>
                    : <><Save className="h-4 w-4 mr-1" /> Save</>}
                </Button>
              </div>
            ))}
            {!loading && rows.length === 0 && (
              <p className="py-6 text-sm text-muted-foreground text-center">No suppliers match. Try clearing the search or the “only missing” filter.</p>
            )}
          </div>
        </CardContent></Card>
      </div>
    </div>
  )
}
