'use client'

/**
 * /hris/bank-upload — bulk bank-details upload (CFO 2026-07-15).
 * Uploader files the salary bank file → bank name derived from the branch code
 * → Dorothy (HR Manager) approves → commits to the employee bank fields.
 * Inherits the HRIS password wall from hris/layout.tsx.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  getToken, uploadBankImport, listBankImports, getBankImport,
  approveBankImport, rejectBankImport,
  type BankImportBatch, type BankImportRow,
} from '@/lib/api'
import { Upload, CheckCircle2, XCircle, AlertTriangle, Landmark, Loader2 } from 'lucide-react'

const STATUS_STYLE: Record<string, { bg: string; text: string; label: string }> = {
  ready:         { bg: '#ECFDF5', text: '#065F46', label: 'Ready' },
  ready_no_bank: { bg: '#EFF6FF', text: '#1E40AF', label: 'Ready (bank blank)' },
  no_match:      { bg: '#FEF2F2', text: '#991B1B', label: 'No employee match' },
  incomplete:    { bg: '#FFFBEB', text: '#92400E', label: 'Missing account/branch' },
  account_check: { bg: '#FFF7ED', text: '#9A3412', label: 'Check account' },
}

export default function BankUploadPage() {
  const router = useRouter()
  const fileRef = useRef<HTMLInputElement>(null)
  const [batches, setBatches] = useState<BankImportBatch[]>([])
  const [canApprove, setCanApprove] = useState(false)
  const [selected, setSelected] = useState<BankImportBatch | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [info, setInfo] = useState<string | null>(null)
  const [rejectReason, setRejectReason] = useState('')

  const reload = useCallback(async () => {
    try {
      const r = await listBankImports()
      setBatches(r.results || [])
      setCanApprove(r.can_approve)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not load bank imports.')
    }
  }, [])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    reload()
  }, [reload, router])

  async function onFile(file: File | null | undefined) {
    if (!file) return
    setBusy(true); setErr(null); setInfo(null)
    try {
      const res = await uploadBankImport(file)
      setInfo(`Uploaded ${res.rows_total} rows — ${res.rows_matched} matched, `
        + `${res.rows_committable} ready. Sent to Dorothy for approval.`)
      await reload()
      const full = await getBankImport(res.id)
      setSelected(full)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Upload failed.')
    } finally {
      setBusy(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  async function openBatch(id: string) {
    setErr(null); setInfo(null)
    try { setSelected(await getBankImport(id)) }
    catch (e) { setErr(e instanceof Error ? e.message : 'Could not open batch.') }
  }

  async function doApprove() {
    if (!selected) return
    if (!confirm(`Approve and commit ${selected.rows_matched} employees' bank details? This writes to payroll.`)) return
    setBusy(true); setErr(null)
    try {
      const b = await approveBankImport(selected.id)
      setSelected(b); setInfo(`Approved — ${b.rows_committed} employees updated.`)
      await reload()
    } catch (e) { setErr(e instanceof Error ? e.message : 'Approve failed.') }
    finally { setBusy(false) }
  }

  async function doReject() {
    if (!selected) return
    if (!rejectReason.trim()) { setErr('Add a short reason for rejecting.'); return }
    setBusy(true); setErr(null)
    try {
      const b = await rejectBankImport(selected.id, rejectReason.trim())
      setSelected(b); setInfo('Batch rejected.'); setRejectReason('')
      await reload()
    } catch (e) { setErr(e instanceof Error ? e.message : 'Reject failed.') }
    finally { setBusy(false) }
  }

  const rows: BankImportRow[] = selected?.rows || []

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar title="Bank details upload"
        subtitle="Load staff account + branch from the salary file — Dorothy approves before it saves"
        breadcrumbs={[{ label: 'HRIS', href: '/hris' }, { label: 'Bank details upload' }]} />
      <main className="flex-1 p-6 space-y-4 max-w-5xl">

        <Card>
          <CardContent className="p-5 space-y-3">
            <div className="flex items-center gap-2 font-semibold text-[#0D1B2A]">
              <Landmark className="w-4 h-4 text-[#1D3270]" /> Upload the salary bank file
            </div>
            <p className="text-sm text-[#6B7280]">
              The file needs a <b>name</b>, <b>account number</b> and <b>branch code</b> column.
              The bank name is filled in automatically from the branch code — you don&rsquo;t type it.
              <b> Tip:</b> save the account-number column as <b>Text</b> before uploading, so long
              Stanbic numbers aren&rsquo;t cut to &ldquo;9.06E+12&rdquo;.
            </p>
            <div
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => { e.preventDefault(); onFile(e.dataTransfer.files?.[0]) }}
              onClick={() => fileRef.current?.click()}
              className="border-2 border-dashed rounded-lg p-8 text-center cursor-pointer hover:bg-muted/20">
              {busy ? <Loader2 className="w-6 h-6 mx-auto animate-spin text-[#1D3270]" />
                    : <Upload className="w-6 h-6 mx-auto text-[#9CA3AF]" />}
              <p className="text-sm mt-2">{busy ? 'Working…' : 'Drop the file here, or click to choose (.xlsx / .csv)'}</p>
              <input ref={fileRef} type="file" accept=".xlsx,.xlsm,.csv" className="hidden"
                onChange={(e) => onFile(e.target.files?.[0])} disabled={busy} />
            </div>
            {err && <div className="text-sm text-red-700 flex items-start gap-1"><AlertTriangle className="w-4 h-4 mt-0.5" />{err}</div>}
            {info && <div className="text-sm text-emerald-700 flex items-start gap-1"><CheckCircle2 className="w-4 h-4 mt-0.5" />{info}</div>}
          </CardContent>
        </Card>

        {/* Recent batches */}
        <Card>
          <CardContent className="p-5">
            <div className="font-semibold text-sm mb-3">Recent uploads</div>
            {batches.length === 0 ? (
              <p className="text-sm text-[#9CA3AF]">Nothing uploaded yet.</p>
            ) : (
              <div className="space-y-1.5">
                {batches.map((b) => (
                  <button key={b.id} onClick={() => openBatch(b.id)}
                    className={`w-full text-left flex flex-wrap items-center gap-2 px-3 py-2 rounded-lg border text-sm hover:bg-muted/20 ${selected?.id === b.id ? 'border-[#1D3270]' : 'border-[#E5E7EB]'}`}>
                    <span className="font-medium text-[#0D1B2A] truncate max-w-[220px]">{b.file_name || 'file'}</span>
                    <span className="text-xs px-2 py-0.5 rounded-full border"
                      style={{ background: b.status === 'approved' ? '#ECFDF5' : b.status === 'rejected' ? '#FEF2F2' : '#FFFBEB' }}>
                      {b.status_display}
                    </span>
                    <span className="text-xs text-[#6B7280]">{b.rows_matched}/{b.rows_total} matched
                      {b.status === 'approved' ? ` · ${b.rows_committed} committed` : ''}</span>
                    <span className="ml-auto text-xs text-[#9CA3AF]">by {b.created_by}</span>
                  </button>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Selected batch preview + approve/reject */}
        {selected && (
          <Card>
            <CardContent className="p-5">
              <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
                <div className="font-semibold text-sm">{selected.file_name || 'Batch'} — preview</div>
                <div className="text-xs text-[#6B7280]">{selected.status_display}
                  {selected.approved_by ? ` by ${selected.approved_by}` : ''}
                  {selected.rejection_reason ? ` — ${selected.rejection_reason}` : ''}</div>
              </div>

              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead className="text-left text-[#6B7280] border-b">
                    <tr>
                      <th className="py-1.5 pr-2">#</th>
                      <th className="py-1.5 pr-2">Name in file</th>
                      <th className="py-1.5 pr-2">Matched employee</th>
                      <th className="py-1.5 pr-2">Account</th>
                      <th className="py-1.5 pr-2">Branch</th>
                      <th className="py-1.5 pr-2">Bank (derived)</th>
                      <th className="py-1.5 pr-2">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => {
                      const s = STATUS_STYLE[r.status] || STATUS_STYLE.incomplete
                      return (
                        <tr key={r.row} className="border-b border-gray-50 align-top">
                          <td className="py-1.5 pr-2 text-[#9CA3AF]">{r.row}</td>
                          <td className="py-1.5 pr-2">{r.name}</td>
                          <td className="py-1.5 pr-2">{r.employee || <span className="text-red-600">—</span>}</td>
                          <td className="py-1.5 pr-2 font-mono">{r.account || '—'}</td>
                          <td className="py-1.5 pr-2 font-mono">{r.branch || '—'}</td>
                          <td className="py-1.5 pr-2">{r.bank_name || <span className="text-[#9CA3AF]">—</span>}</td>
                          <td className="py-1.5 pr-2">
                            <span className="px-2 py-0.5 rounded-full" style={{ background: s.bg, color: s.text }}>{s.label}</span>
                            {r.warning && <div className="text-[10px] text-[#B45309] mt-0.5 max-w-[240px]">{r.warning}</div>}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>

              {selected.status === 'pending' && selected.can_approve && (
                <div className="mt-4 border-t pt-3 space-y-2">
                  <div className="text-xs text-[#6B7280]">You are approving as HR Manager — this commits the ready rows to payroll.</div>
                  <div className="flex flex-wrap items-center gap-2">
                    <Button onClick={doApprove} disabled={busy} className="bg-[#0D1B2A] text-white">
                      <CheckCircle2 className="w-4 h-4 mr-1" /> Approve &amp; commit
                    </Button>
                    <input value={rejectReason} onChange={(e) => setRejectReason(e.target.value)}
                      placeholder="Reason to reject…" className="border rounded-lg px-3 py-2 text-sm flex-1 min-w-[200px]" />
                    <Button variant="outline" onClick={doReject} disabled={busy} className="text-red-700 border-red-300">
                      <XCircle className="w-4 h-4 mr-1" /> Reject
                    </Button>
                  </div>
                </div>
              )}
              {selected.status === 'pending' && !selected.can_approve && (
                <p className="mt-4 text-xs text-[#6B7280] border-t pt-3">
                  Waiting for Dorothy (HR Manager) to approve. The person who uploaded a batch can&rsquo;t approve their own.
                </p>
              )}
            </CardContent>
          </Card>
        )}
      </main>
    </div>
  )
}
