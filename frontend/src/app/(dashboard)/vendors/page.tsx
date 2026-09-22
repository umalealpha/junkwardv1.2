'use client'

/**
 * /vendors — dedicated Vendor list page (split from /contacts).
 *
 * Pre-filters contact_type=vendor so AP staff have a focused workspace
 * separate from customers/brokers/employees. Wraps the existing
 * getContacts API with the filter pinned.
 */

import { useEffect, useState, useCallback, useRef } from 'react'
import { useRouter } from 'next/navigation'
import { getContacts, getToken, apiFetchBinary } from '@/lib/api'
import type { Contact } from '@/lib/api'
import { useCompany } from '@/contexts/CompanyContext'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { LoadingTable } from '@/components/ui/loading'
import { cn } from '@/lib/utils'
import { Plus, Search, Briefcase, AlertCircle, ChevronLeft, ChevronRight, Mail, Phone, Upload, Download, X, CheckCircle2 } from 'lucide-react'
import SmartUpload from '@/components/SmartUpload'

export default function VendorsPage() {
  const router = useRouter()
  const { selected, companies } = useCompany()
  const [vendors, setVendors] = useState<Contact[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [totalCount, setTotalCount] = useState(0)
  const PAGE_SIZE = 25

  // Bulk upload state
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadCompanyCode, setUploadCompanyCode] = useState<string>(selected?.code || 'ADIC')
  const [uploadResult, setUploadResult] = useState<{ created: number; updated: number; errors: { row: number; error: string }[]; commit: boolean; company: string } | null>(null)
  const [showUploadModal, setShowUploadModal] = useState(false)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const params: Record<string, string> = {
        page: String(page),
        page_size: String(PAGE_SIZE),
        contact_type: 'vendor',
      }
      if (search) params.search = search
      const res = await getContacts(params)
      setVendors(res.results); setTotalCount(res.count)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load vendors')
    } finally { setLoading(false) }
  }, [page, search])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load])

  const totalPages = Math.max(1, Math.ceil(totalCount / PAGE_SIZE))
  const rangeStart = totalCount === 0 ? 0 : (page - 1) * PAGE_SIZE + 1
  const rangeEnd = Math.min(page * PAGE_SIZE, totalCount)

  // Sync upload-company dropdown with the topbar switcher
  useEffect(() => {
    if (selected?.code) setUploadCompanyCode(selected.code)
  }, [selected?.code])

  const uploadFile = async (file: File, commit: boolean) => {
    setUploading(true); setUploadResult(null)
    try {
      const form = new FormData()
      form.append('file', file)
      form.append('company', uploadCompanyCode)
      form.append('contact_type', 'vendor')
      if (commit) form.append('commit', 'true')
      // FE-SWEEP swarm 2026-06-08 #16: was sending "Token __sso__" for SSO
      // users → 401. apiFetchBinary uses Bearer/Token correctly + skips sentinel.
      const res = await apiFetchBinary('/api/v1/admin/cfo-upload-vendors/', {
        method: 'POST',
        body: form,
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
      setUploadResult(data)
      if (commit) {
        setShowUploadModal(false)
        load()
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Upload failed')
    } finally { setUploading(false) }
  }

  const inputCls = 'h-10 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm text-[#111827] placeholder-[#9CA3AF] focus:outline-none focus:border-[#F07F00] focus:ring-[3px] focus:ring-[rgba(240,127,0,0.1)] transition-all'

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Vendors"
        breadcrumbs={[{ label: 'Procurement' }, { label: 'Vendors' }]}
        actions={
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" leftIcon={<Upload className="w-3.5 h-3.5" />}
              onClick={() => { setShowUploadModal(true); setUploadResult(null) }}>
              Upload vendors
            </Button>
            <Button variant="accent" size="sm" leftIcon={<Plus className="w-3.5 h-3.5" />} onClick={() => router.push('/contacts/new?type=vendor')}>New Vendor</Button>
          </div>
        }
      />

      <div className="flex-1 p-6 space-y-4">
        <SmartUpload section="vendors" />
        <div className="flex items-center justify-between gap-3">
          <div className="relative flex-1 max-w-md">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[#9CA3AF] pointer-events-none" strokeWidth={1.5} />
            <input
              type="text"
              placeholder="Search vendors by name, tax ID, registration..."
              aria-label="Search vendors"
              value={search}
              onChange={(e) => { setSearch(e.target.value); setPage(1) }}
              className={cn(inputCls, 'pl-9 w-full')}
            />
          </div>
          <div className="flex items-center gap-3 text-sm text-[#6B7280]">
            <span className="font-medium font-mono-nums tabular-nums">{rangeStart}-{rangeEnd} / {totalCount}</span>
            <button aria-label="Previous page" className="p-1.5 rounded hover:bg-[#F3F4F6] disabled:opacity-30 disabled:cursor-not-allowed" disabled={page === 1} onClick={() => setPage(page - 1)}><ChevronLeft className="w-4 h-4" /></button>
            <button aria-label="Next page" className="p-1.5 rounded hover:bg-[#F3F4F6] disabled:opacity-30 disabled:cursor-not-allowed" disabled={page >= totalPages} onClick={() => setPage(page + 1)}><ChevronRight className="w-4 h-4" /></button>
          </div>
        </div>

        {error && <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2"><AlertCircle className="w-4 h-4 text-[#DC2626]" /><p className="text-[#DC2626] text-sm">{error}</p></div>}

        <Card>
          <CardContent className="p-0">
            {loading ? <LoadingTable rows={8} cols={6} /> : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm border-collapse">
                  <thead className="bg-[#F9FAFB] border-b border-[#E5E7EB]">
                    <tr>
                      <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Name</th>
                      <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Reg #</th>
                      <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Tax ID</th>
                      <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Email</th>
                      <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Phone</th>
                      <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Currency</th>
                      <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Status</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#E5E7EB] bg-white">
                    {vendors.length === 0 ? (
                      <tr><td colSpan={7} className="px-4 py-16 text-center"><Briefcase className="w-8 h-8 text-[#D1D5DB] mx-auto mb-2" /><p className="text-[#6B7280] text-sm font-medium">No vendors found</p></td></tr>
                    ) : vendors.map(v => (
                      <tr key={v.id} onClick={() => router.push(`/contacts/${v.id}`)} className="cursor-pointer hover:bg-[#FFF7ED] transition-colors">
                        <td className="px-3 py-3 font-medium text-[#111827]">{v.name}</td>
                        <td className="px-3 py-3 text-[#6B7280] text-xs">{v.registration_number || '—'}</td>
                        <td className="px-3 py-3 text-[#6B7280] text-xs font-mono">{v.tax_id || '—'}</td>
                        <td className="px-3 py-3 text-[#6B7280] text-xs">{v.email ? <span className="inline-flex items-center gap-1"><Mail className="w-3 h-3" />{v.email}</span> : '—'}</td>
                        <td className="px-3 py-3 text-[#6B7280] text-xs">{v.phone ? <span className="inline-flex items-center gap-1"><Phone className="w-3 h-3" />{v.phone}</span> : '—'}</td>
                        <td className="px-3 py-3 text-[#374151] text-xs font-mono">{v.currency_code}</td>
                        <td className="px-3 py-3"><span className={cn('inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium', v.is_active ? 'bg-[#ECFDF5] text-[#059669]' : 'bg-[#F3F4F6] text-[#6B7280]')}>{v.is_active ? 'Active' : 'Inactive'}</span></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {showUploadModal && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4" onClick={() => setShowUploadModal(false)}>
          <div className="bg-white rounded-lg p-6 max-w-lg w-full" onClick={e => e.stopPropagation()}>
            <div className="flex justify-between items-center mb-4">
              <h2 className="text-lg font-semibold text-[#0B0B3B]">Upload vendors</h2>
              <button onClick={() => setShowUploadModal(false)}><X className="w-5 h-5 text-[#6B7280]" /></button>
            </div>
            <p className="text-sm text-[#6B7280] mb-3">
              CSV or XLSX with columns: name (required), registration_number,
              tax_id, email, phone, address, currency_code, payment_terms_days,
              is_resident, notes.
            </p>
            <a href="/api/v1/admin/cfo-upload-vendors/template/" download
               className="inline-flex items-center gap-1 text-xs text-[#F07F00] hover:underline mb-4">
              <Download className="w-3 h-3" /> Download CSV template
            </a>
            <div className="mb-4">
              <label className="block text-xs font-semibold text-[#374151] mb-1">Company</label>
              <select value={uploadCompanyCode} onChange={e => setUploadCompanyCode(e.target.value)}
                className="w-full h-10 border border-[#D1D5DB] rounded px-3 text-sm">
                {companies.map(c => <option key={c.id} value={c.code}>{c.code} — {c.name}</option>)}
              </select>
              <p className="text-xs text-[#9CA3AF] mt-1">
                Every uploaded row will be stamped with this company. Vendors do
                not bleed across subsidiaries.
              </p>
            </div>
            <input ref={fileInputRef} type="file" accept=".xlsx,.csv" hidden
              onChange={e => { const f = e.target.files?.[0]; if (f) uploadFile(f, false); e.currentTarget.value = '' }} />
            <Button variant="outline" onClick={() => fileInputRef.current?.click()}
              disabled={uploading}>
              {uploading ? 'Reading file…' : 'Choose file (dry-run preview)'}
            </Button>
            {uploadResult && (
              <div className="mt-4 p-3 rounded bg-[#F9FAFB] border border-[#E5E7EB] text-xs">
                <p className="font-semibold text-[#0B0B3B]">
                  {uploadResult.commit ? 'Imported' : 'Dry run'} · {uploadResult.company}
                </p>
                <p className="mt-1 text-[#374151]">
                  Created <strong>{uploadResult.created}</strong> · Updated existing <strong>{uploadResult.updated}</strong> · Errors <strong>{uploadResult.errors.length}</strong>
                </p>
                {uploadResult.errors.length > 0 && (
                  <ul className="mt-2 text-[11px] text-[#B91C1C]">
                    {uploadResult.errors.slice(0, 8).map((e, i) => <li key={i}>row {e.row}: {e.error}</li>)}
                  </ul>
                )}
                {!uploadResult.commit && uploadResult.created + uploadResult.updated > 0 && (
                  <Button variant="accent" size="sm" leftIcon={<CheckCircle2 className="w-3.5 h-3.5" />}
                    className="mt-3" disabled={uploading}
                    onClick={() => { const f = fileInputRef.current?.files?.[0]; if (f) uploadFile(f, true) }}>
                    Confirm and import to {uploadResult.company}
                  </Button>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
