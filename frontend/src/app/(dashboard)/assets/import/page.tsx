'use client'

import { useEffect, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  previewAssetImport, commitAssetImport, getCompanies, getToken,
} from '@/lib/api'
import type { AssetImportBatch, Company } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { ArrowLeft, AlertCircle, Upload, CheckCircle2, FileText, ChevronRight } from 'lucide-react'
import { localYmd } from '@/lib/utils'

type Step = 'upload' | 'preview' | 'committed'

export default function AssetImportPage() {
  const router = useRouter()
  const fileInputRef = useRef<HTMLInputElement>(null)

  const [step, setStep] = useState<Step>('upload')
  const [companies, setCompanies] = useState<Company[]>([])
  const [companyId, setCompanyId] = useState('')
  const [cutoverDate, setCutoverDate] = useState(() => localYmd(new Date()))
  const [file, setFile] = useState<File | null>(null)

  const [batch, setBatch] = useState<AssetImportBatch | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [committed, setCommitted] = useState<{ created: number; errors: { row_index: number; field: string; message: string }[] } | null>(null)

  useEffect(() => {
    const token = getToken()
    if (!token) { router.replace('/login'); return }
    getCompanies()
      .then(res => {
        setCompanies(res.results)
        const def = res.results.find(c => c.is_default) || res.results[0]
        if (def) setCompanyId(def.id)
      })
      .catch(err => setError(err instanceof Error ? err.message : 'Failed to load companies'))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function handlePreview() {
    if (!file || !companyId) return
    setError(null)
    setBusy(true)
    try {
      const fd = new FormData()
      fd.append('file', file)
      fd.append('cutover_date', cutoverDate)
      fd.append('company', companyId)
      fd.append('source', 'odoo')
      const previewBatch = await previewAssetImport(fd)
      setBatch(previewBatch)
      setStep('preview')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Preview failed')
    } finally {
      setBusy(false)
    }
  }

  async function handleCommit() {
    if (!batch) return
    setError(null)
    setBusy(true)
    try {
      const result = await commitAssetImport(batch.id)
      setCommitted({ created: result.created, errors: result.errors })
      setStep('committed')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Commit failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Import from Odoo"
        breadcrumbs={[
          { label: 'Finance' },
          { label: 'Fixed Assets', href: '/assets' },
          { label: 'Import' },
        ]}
        actions={
          <Button variant="outline" size="sm" leftIcon={<ArrowLeft className="w-3.5 h-3.5" />} onClick={() => router.push('/assets')}>
            Back to register
          </Button>
        }
      />

      <div className="flex-1 p-6 max-w-5xl space-y-4">
        <Stepper step={step} />

        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-[#DC2626]" />
            <p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}

        {step === 'upload' && (
          <Card>
            <CardHeader><CardTitle>1. Upload your Odoo asset register</CardTitle></CardHeader>
            <CardContent className="space-y-4">
              <p className="text-sm text-[#374151]">
                Export your Odoo fixed assets as CSV (Accounting → Assets → Action → Export).
                Cutover date is the last day for which depreciation has already been booked
                in Odoo — forward depreciation in alpha-finance starts after this date.
              </p>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <label className="block">
                  <span className="block text-xs font-medium text-[#374151] mb-1">Company</span>
                  <select value={companyId} onChange={e => setCompanyId(e.target.value)} className="w-full h-10 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm">
                    {companies.map(c => <option key={c.id} value={c.id}>{c.code} — {c.name}</option>)}
                  </select>
                </label>
                <label className="block">
                  <span className="block text-xs font-medium text-[#374151] mb-1">Cutover date</span>
                  <input type="date" value={cutoverDate} onChange={e => setCutoverDate(e.target.value)} className="w-full h-10 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm" />
                </label>
              </div>

              <div
                onClick={() => fileInputRef.current?.click()}
                onDragOver={e => e.preventDefault()}
                onDrop={e => {
                  e.preventDefault()
                  const f = e.dataTransfer.files[0]
                  if (f) setFile(f)
                }}
                className="border-2 border-dashed border-[#D1D5DB] rounded-lg p-8 text-center cursor-pointer hover:border-[#F07F00] hover:bg-[#FFF7ED] transition-colors"
              >
                <Upload className="w-8 h-8 mx-auto mb-2 text-[#9CA3AF]" strokeWidth={1.5} />
                {file ? (
                  <div>
                    <p className="text-sm font-medium text-[#111827]">{file.name}</p>
                    <p className="text-xs text-[#6B7280]">{(file.size / 1024).toFixed(1)} KB — click to change</p>
                  </div>
                ) : (
                  <>
                    <p className="text-sm text-[#374151]">Drop CSV here or click to browse</p>
                    <p className="text-xs text-[#9CA3AF] mt-1">Standard Odoo asset export columns are auto-detected</p>
                  </>
                )}
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".csv,text/csv,application/vnd.ms-excel"
                  className="hidden"
                  onChange={e => setFile(e.target.files?.[0] || null)}
                />
              </div>

              <div className="flex justify-end">
                <Button
                  variant="accent"
                  rightIcon={<ChevronRight className="w-3.5 h-3.5" />}
                  disabled={!file || !companyId || busy}
                  onClick={handlePreview}
                >
                  {busy ? 'Parsing…' : 'Preview rows'}
                </Button>
              </div>
            </CardContent>
          </Card>
        )}

        {step === 'preview' && batch && (
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <FileText className="w-4 h-4" />
                2. Preview — {batch.file_name}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <Stat label="Total rows" value={batch.rows_total} />
                <Stat label="Valid" value={batch.rows_valid} tone="ok" />
                <Stat label="Invalid" value={batch.rows_invalid} tone={batch.rows_invalid > 0 ? 'bad' : 'ok'} />
                <Stat label="Cutover" value={batch.cutover_date} />
              </div>

              {batch.validation_errors.length > 0 && (
                <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 max-h-48 overflow-y-auto">
                  <p className="text-sm font-medium text-[#B91C1C] mb-1">Validation errors</p>
                  <ul className="text-xs text-[#7F1D1D] space-y-1">
                    {batch.validation_errors.slice(0, 50).map((e, i) => (
                      <li key={i}>
                        Row {e.row_index >= 0 ? e.row_index : '—'} · {e.field}: {e.message}
                      </li>
                    ))}
                    {batch.validation_errors.length > 50 && (
                      <li className="italic">…and {batch.validation_errors.length - 50} more</li>
                    )}
                  </ul>
                </div>
              )}

              <div className="border border-[#E5E7EB] rounded-lg overflow-hidden">
                <div className="overflow-x-auto max-h-96">
                  <table className="w-full text-xs border-collapse">
                    <thead className="bg-[#F3F4F6] border-b-2 border-[#E5E7EB] sticky top-0">
                      <tr>
                        <th className="px-3 py-2 text-left font-semibold text-[#374151]">#</th>
                        <th className="px-3 py-2 text-left font-semibold text-[#374151]">Tag</th>
                        <th className="px-3 py-2 text-left font-semibold text-[#374151]">Name</th>
                        <th className="px-3 py-2 text-left font-semibold text-[#374151]">Cat.</th>
                        <th className="px-3 py-2 text-right font-semibold text-[#374151]">Cost</th>
                        <th className="px-3 py-2 text-right font-semibold text-[#374151]">Opening accum.</th>
                        <th className="px-3 py-2 text-left font-semibold text-[#374151]">Method</th>
                        <th className="px-3 py-2 text-left font-semibold text-[#374151]">Months</th>
                        <th className="px-3 py-2 text-left font-semibold text-[#374151]">Purchase</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[#E5E7EB] bg-white">
                      {batch.parsed_rows.slice(0, 200).map((r, i) => {
                        const row = r as Record<string, unknown>
                        return (
                          <tr key={i}>
                            <td className="px-3 py-1.5 text-[#6B7280] font-mono">{String(row.row_index ?? '')}</td>
                            <td className="px-3 py-1.5 font-mono">{String(row.tag_number ?? '')}</td>
                            <td className="px-3 py-1.5">{String(row.name ?? '')}</td>
                            <td className="px-3 py-1.5">{String(row.category_code ?? '—')}</td>
                            <td className="px-3 py-1.5 text-right tabular-nums">{String(row.cost ?? '')}</td>
                            <td className="px-3 py-1.5 text-right tabular-nums">{String(row.opening_accumulated_depreciation ?? '0')}</td>
                            <td className="px-3 py-1.5">{String(row.method ?? '')}</td>
                            <td className="px-3 py-1.5">{String(row.useful_life_months ?? '')}</td>
                            <td className="px-3 py-1.5">{String(row.purchase_date ?? '')}</td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
                {batch.parsed_rows.length > 200 && (
                  <p className="px-3 py-2 text-xs text-[#9CA3AF] bg-[#F9FAFB] border-t border-[#E5E7EB]">
                    Showing first 200 of {batch.parsed_rows.length} rows.
                  </p>
                )}
              </div>

              <div className="flex justify-between">
                <Button variant="outline" onClick={() => { setStep('upload'); setBatch(null) }} disabled={busy}>
                  Re-upload
                </Button>
                <Button
                  variant="accent"
                  onClick={handleCommit}
                  disabled={busy || batch.rows_invalid > 0 || batch.rows_valid === 0}
                  rightIcon={<CheckCircle2 className="w-3.5 h-3.5" />}
                >
                  {busy ? 'Importing…' : `Commit ${batch.rows_valid} asset${batch.rows_valid === 1 ? '' : 's'}`}
                </Button>
              </div>
              {batch.rows_invalid > 0 && (
                <p className="text-xs text-[#B91C1C]">Fix the validation errors in your CSV and re-upload before committing.</p>
              )}
            </CardContent>
          </Card>
        )}

        {step === 'committed' && committed && (
          <Card>
            <CardContent className="p-8 text-center space-y-4">
              <CheckCircle2 className="w-12 h-12 mx-auto text-[#047857]" strokeWidth={1.5} />
              <h2 className="text-lg font-semibold text-[#111827]">
                Imported {committed.created} asset{committed.created === 1 ? '' : 's'}
              </h2>
              <p className="text-sm text-[#6B7280]">
                Opening accumulated depreciation has been carried over from Odoo.
                Forward depreciation will start the month after the cutover date.
              </p>
              <div className="flex justify-center gap-2">
                <Button variant="outline" onClick={() => router.push('/assets')}>
                  Open register
                </Button>
                <Button variant="accent" onClick={() => router.push('/reports/asset-register')}>
                  View Asset Register report
                </Button>
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  )
}

function Stepper({ step }: { step: Step }) {
  const items = [
    { id: 'upload', label: 'Upload' },
    { id: 'preview', label: 'Preview' },
    { id: 'committed', label: 'Done' },
  ]
  const idx = items.findIndex(i => i.id === step)
  return (
    <div className="flex items-center gap-3">
      {items.map((it, i) => (
        <div key={it.id} className="flex items-center gap-2">
          <span
            className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold border ${
              i <= idx
                ? 'bg-[#F07F00] text-white border-[#F07F00]'
                : 'bg-white text-[#9CA3AF] border-[#D1D5DB]'
            }`}
          >
            {i + 1}
          </span>
          <span className={`text-sm ${i === idx ? 'font-semibold text-[#111827]' : 'text-[#6B7280]'}`}>
            {it.label}
          </span>
          {i < items.length - 1 && <span className="text-[#D1D5DB]">→</span>}
        </div>
      ))}
    </div>
  )
}

function Stat({ label, value, tone }: { label: string; value: number | string; tone?: 'ok' | 'bad' }) {
  return (
    <Card>
      <CardContent className="p-3">
        <p className="text-xs font-medium text-[#6B7280] uppercase tracking-wider">{label}</p>
        <p
          className={`text-lg font-semibold ${
            tone === 'ok' ? 'text-[#047857]' : tone === 'bad' ? 'text-[#B91C1C]' : 'text-[#111827]'
          }`}
        >
          {value}
        </p>
      </CardContent>
    </Card>
  )
}
