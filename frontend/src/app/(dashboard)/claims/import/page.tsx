'use client'

import { useEffect, useRef, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { previewRecoveryImport, commitRecoveryImport, getCompanies, getMe, getToken } from '@/lib/api'
import type { RecoveryImportBatch, Company, UserProfile } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { ArrowLeft, AlertCircle, Upload, CheckCircle2, FileText, Lock, ChevronRight } from 'lucide-react'

type Step = 'upload' | 'preview' | 'committed'
type Kind = 'subrogation' | 'salvage'

export default function RecoveryImportWizardPage() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const initialKind = (searchParams?.get('kind') === 'salvage' ? 'salvage' : 'subrogation') as Kind
  const fileRef = useRef<HTMLInputElement>(null)

  const [kind, setKind] = useState<Kind>(initialKind)
  const [step, setStep] = useState<Step>('upload')
  const [me, setMe] = useState<UserProfile | null>(null)
  const [companies, setCompanies] = useState<Company[]>([])
  const [companyId, setCompanyId] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [batch, setBatch] = useState<RecoveryImportBatch | null>(null)
  const [committed, setCommitted] = useState<{ created: number; skipped: number } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    getMe().then(setMe).catch(() => setMe(null))
    getCompanies().then(r => {
      setCompanies(r.results)
      const def = r.results.find(c => c.is_default) || r.results[0]
      if (def) setCompanyId(def.id)
    }).catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const canImport = !!me?.can_approve_journal_entries
  if (me && !canImport) {
    return (
      <div className="flex flex-col min-h-screen">
        <TopBar title="Import" breadcrumbs={[{ label: 'Claims' }, { label: 'Import' }]} />
        <div className="flex-1 p-6"><Card><CardContent className="p-12 text-center">
          <Lock className="w-10 h-10 mx-auto mb-3 text-[#9CA3AF]" strokeWidth={1.5} />
          <h2 className="text-lg font-semibold">Approver title required</h2>
          <p className="mt-2 text-sm text-[#6B7280] max-w-md mx-auto">Bulk imports are restricted to CFO / Finance Manager / Financial Controller. Two distinct approvers must sign off via /approvals before commit succeeds.</p>
        </CardContent></Card></div>
      </div>
    )
  }

  async function handlePreview() {
    if (!file) return
    setBusy(true); setError(null)
    try {
      const b = await previewRecoveryImport(kind, file, companyId || undefined)
      setBatch(b); setStep('preview')
    } catch (e) { setError(e instanceof Error ? e.message : 'Preview failed') }
    finally { setBusy(false) }
  }

  async function handleCommit() {
    if (!batch) return
    setBusy(true); setError(null)
    try {
      const result = await commitRecoveryImport(batch.id)
      setCommitted({ created: result.created, skipped: result.skipped_duplicates })
      setStep('committed')
    } catch (e) { setError(e instanceof Error ? e.message : 'Commit failed — imports require dual approval first.') }
    finally { setBusy(false) }
  }

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar title={kind === 'subrogation' ? 'Import Subrogations' : 'Import Salvages'}
        breadcrumbs={[{ label: 'Claims Recoveries' }, { label: kind === 'subrogation' ? 'Subrogations' : 'Salvages', href: `/claims/${kind}s` }, { label: 'Import' }]}
        actions={<Button variant="ghost" size="sm" leftIcon={<ArrowLeft className="w-3.5 h-3.5" />} onClick={() => router.back()}>Back</Button>} />
      <div className="flex-1 p-6 max-w-5xl space-y-4">
        <div className="flex items-center gap-3">
          {(['upload','preview','committed'] as Step[]).map((s, i) => (
            <div key={s} className="flex items-center gap-2">
              <span className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold border ${
                ['upload','preview','committed'].indexOf(step) >= i ? 'bg-[#F07F00] text-white border-[#F07F00]' : 'bg-white text-[#9CA3AF] border-[#D1D5DB]'
              }`}>{i + 1}</span>
              <span className={`text-sm ${step === s ? 'font-semibold text-[#111827]' : 'text-[#6B7280]'}`}>{s.charAt(0).toUpperCase() + s.slice(1)}</span>
              {i < 2 && <span className="text-[#D1D5DB]">→</span>}
            </div>
          ))}
        </div>
        {error && <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2"><AlertCircle className="w-4 h-4 text-[#DC2626]" /><p className="text-[#DC2626] text-sm">{error}</p></div>}

        {step === 'upload' && (
          <Card><CardHeader><CardTitle>1. Upload {kind} CSV / XLSX</CardTitle></CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <label className="block"><span className="block text-xs font-medium text-[#374151] mb-1">Kind</span>
                  <select value={kind} onChange={e => setKind(e.target.value as Kind)} className="w-full h-10 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm">
                    <option value="subrogation">Subrogations</option><option value="salvage">Salvages</option>
                  </select>
                </label>
                <label className="block"><span className="block text-xs font-medium text-[#374151] mb-1">Company</span>
                  <select value={companyId} onChange={e => setCompanyId(e.target.value)} className="w-full h-10 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm">
                    <option value="">— Default —</option>
                    {companies.map(c => <option key={c.id} value={c.id}>{c.code} — {c.name}</option>)}
                  </select>
                </label>
              </div>
              <div onClick={() => fileRef.current?.click()} onDragOver={e => e.preventDefault()}
                   onDrop={e => { e.preventDefault(); if (e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]) }}
                   className="border-2 border-dashed border-[#D1D5DB] rounded-lg p-8 text-center cursor-pointer hover:border-[#F07F00] hover:bg-[#FFF7ED]">
                <Upload className="w-8 h-8 mx-auto mb-2 text-[#9CA3AF]" strokeWidth={1.5} />
                {file ? <p className="text-sm font-medium">{file.name}</p> : <p className="text-sm text-[#374151]">Drop CSV/XLSX or click to browse</p>}
                <input ref={fileRef} type="file" accept=".csv,.xlsx,.xlsm" className="hidden" onChange={e => setFile(e.target.files?.[0] || null)} />
              </div>
              <div className="flex justify-end">
                <Button variant="accent" rightIcon={<ChevronRight className="w-3.5 h-3.5" />} disabled={!file || busy} onClick={handlePreview}>
                  {busy ? 'Parsing…' : 'Preview'}
                </Button>
              </div>
            </CardContent></Card>
        )}

        {step === 'preview' && batch && (
          <Card><CardHeader><CardTitle className="flex items-center gap-2"><FileText className="w-4 h-4" />2. Preview — {batch.file_name}</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-sm">
                <Stat label="Total" value={batch.rows_total} />
                <Stat label="Valid" value={batch.rows_valid} tone="ok" />
                <Stat label="Invalid" value={batch.rows_invalid} tone={batch.rows_invalid ? 'bad' : 'ok'} />
                <Stat label="Status" value={batch.status_display} />
              </div>
              {batch.validation_errors.length > 0 && (
                <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 max-h-48 overflow-y-auto">
                  <p className="text-sm font-medium text-[#B91C1C] mb-1">Validation errors</p>
                  <ul className="text-xs text-[#7F1D1D] space-y-0.5">
                    {batch.validation_errors.slice(0, 50).map((e, i) => <li key={i}>Row {e.row_index >= 0 ? e.row_index : '—'} · {e.field}: {e.message}</li>)}
                  </ul>
                </div>
              )}
              <p className="text-xs text-[#6B7280] bg-[#F9FAFB] border border-[#E5E7EB] rounded p-2">
                <strong>Append-only:</strong> rows whose claim reference already exists are skipped.
                Two distinct approvers must sign off via <a href="/approvals" className="text-[#F07F00] underline">/approvals</a> before commit.
              </p>
              <div className="flex justify-between">
                <Button variant="outline" onClick={() => { setStep('upload'); setBatch(null); setFile(null) }} disabled={busy}>Re-upload</Button>
                <div className="flex gap-2">
                  <Button variant="outline" onClick={() => router.push('/approvals')} disabled={busy}>Send for approval</Button>
                  <Button variant="accent" onClick={handleCommit} disabled={busy || batch.rows_invalid > 0}
                          rightIcon={<CheckCircle2 className="w-3.5 h-3.5" />}>
                    {busy ? 'Committing…' : `Commit ${batch.rows_valid} row${batch.rows_valid === 1 ? '' : 's'}`}
                  </Button>
                </div>
              </div>
            </CardContent></Card>
        )}

        {step === 'committed' && committed && (
          <Card><CardContent className="p-8 text-center space-y-3">
            <CheckCircle2 className="w-12 h-12 mx-auto text-[#047857]" strokeWidth={1.5} />
            <h2 className="text-lg font-semibold">Imported {committed.created} new {kind}{committed.created === 1 ? '' : 's'}</h2>
            <p className="text-sm text-[#6B7280]">Skipped {committed.skipped} duplicate{committed.skipped === 1 ? '' : 's'}.</p>
            <Button variant="accent" onClick={() => router.push(`/claims/${kind}s`)}>Open list</Button>
          </CardContent></Card>
        )}
      </div>
    </div>
  )
}

function Stat({ label, value, tone }: { label: string; value: number | string; tone?: 'ok' | 'bad' }) {
  return (
    <Card><CardContent className="p-3">
      <p className="text-xs uppercase tracking-wider text-[#6B7280]">{label}</p>
      <p className={`text-lg font-semibold ${tone === 'ok' ? 'text-[#047857]' : tone === 'bad' ? 'text-[#B91C1C]' : 'text-[#111827]'}`}>{value}</p>
    </CardContent></Card>
  )
}
