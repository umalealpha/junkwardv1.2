'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { importSubrogationRealPay, getToken } from '@/lib/api'
import type { RealPayImportResult } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { ArrowLeft, AlertCircle, Upload, CheckCircle2, Banknote } from 'lucide-react'

function fmt(v: string | number): string {
  const n = typeof v === 'string' ? parseFloat(v) : v
  if (Number.isNaN(n)) return '—'
  return new Intl.NumberFormat('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n)
}

export default function RealPayImportPage() {
  const router = useRouter()
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<RealPayImportResult | null>(null)
  const [committed, setCommitted] = useState<RealPayImportResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!getToken()) { router.replace('/login'); return null }

  async function run(commit: boolean) {
    if (!file) return
    setBusy(true); setError(null)
    try {
      const res = await importSubrogationRealPay(file, commit)
      if (commit) { setCommitted(res); setPreview(null) } else { setPreview(res); setCommitted(null) }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Import failed')
    } finally {
      setBusy(false)
    }
  }

  const s = (committed || preview)?.stats
  const shown = committed || preview

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar title="RealPay import"
        breadcrumbs={[{ label: 'Claims Recoveries' }, { label: 'Subrogations', href: '/claims/subrogations' }, { label: 'RealPay import' }]}
        actions={<Button variant="ghost" size="sm" leftIcon={<ArrowLeft className="w-3.5 h-3.5" />} onClick={() => router.push('/claims/subrogations')}>Back</Button>} />

      <div className="flex-1 p-6 max-w-3xl space-y-4">
        <p className="text-sm text-[#6B7280]">
          Upload the RealPay collections report (the "Alpha Direct Third Parties" merchant).
          Successful collections are matched to a subrogation by the RealPay client number
          (which is the claim reference) and recorded as payments. Other merchants, failed
          lines and already-imported lines are ignored. Preview first, then post.
        </p>

        {error && <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2"><AlertCircle className="w-4 h-4 text-[#DC2626]" /><p className="text-[#DC2626] text-sm">{error}</p></div>}

        <Card>
          <CardHeader><CardTitle>1. Choose the RealPay CSV</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            <input type="file" accept=".csv,text/csv"
              onChange={e => { setFile(e.target.files?.[0] || null); setPreview(null); setCommitted(null) }}
              className="block w-full text-sm text-[#374151] file:mr-3 file:py-2 file:px-3 file:rounded-md file:border-0 file:bg-[#0B0B3B] file:text-white file:text-sm hover:file:bg-[#1a1a5c]" />
            <div className="flex gap-2">
              <Button variant="outline" size="sm" leftIcon={<Upload className="w-3.5 h-3.5" />}
                onClick={() => run(false)} disabled={!file || busy}>
                {busy ? 'Reading…' : 'Preview'}
              </Button>
              {preview && preview.stats.posted > 0 && (
                <Button variant="accent" size="sm" leftIcon={<Banknote className="w-3.5 h-3.5" />}
                  onClick={() => run(true)} disabled={busy}>
                  {busy ? 'Posting…' : `Post ${preview.stats.posted} payments`}
                </Button>
              )}
            </div>
          </CardContent>
        </Card>

        {shown && s && (
          <Card>
            <CardHeader className="flex-row items-center gap-2">
              {committed
                ? <><CheckCircle2 className="w-4 h-4 text-[#047857]" /><CardTitle>Posted</CardTitle></>
                : <CardTitle>Preview (nothing saved yet)</CardTitle>}
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
                <Stat label={committed ? 'Payments posted' : 'Will post'} value={String(s.posted)} strong />
                <Stat label="Amount" value={`P ${fmt(shown.posted_amount)}`} strong />
                <Stat label="Matched cases" value={String(s.matched)} />
                <Stat label="Already imported" value={String(s.duplicate)} />
                <Stat label="Unmatched" value={String(s.unmatched)} />
                <Stat label="Other merchants" value={String(s.other_merchant)} />
                <Stat label="Not successful" value={String(s.not_successful)} />
                <Stat label="Rows in file" value={String(s.total)} />
              </div>
              {shown.unmatched_total > 0 && (
                <div className="mt-4 bg-[#FFFBEB] border border-[#FDE68A] rounded-md p-3">
                  <p className="text-sm text-[#92400E] font-medium">{shown.unmatched_total} client number(s) had no matching subrogation case:</p>
                  <p className="text-xs text-[#92400E] mt-1 font-mono break-all">{shown.unmatched_client_numbers.join(', ')}{shown.unmatched_total > shown.unmatched_client_numbers.length ? ' …' : ''}</p>
                </div>
              )}
              {committed && (
                <p className="text-sm text-[#047857] mt-4">Done. The recovered figures on those cases now include these payments.</p>
              )}
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  )
}

function Stat({ label, value, strong }: { label: string; value: string; strong?: boolean }) {
  return (
    <div className="bg-[#F9FAFB] border border-[#E5E7EB] rounded-md px-3 py-2">
      <p className="text-xs text-[#6B7280]">{label}</p>
      <p className={`tabular-nums mt-0.5 ${strong ? 'text-base font-semibold text-[#0B0B3B]' : 'text-sm text-[#374151]'}`}>{value}</p>
    </div>
  )
}
