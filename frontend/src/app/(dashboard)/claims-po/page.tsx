'use client'

// Claims PO — list + upload (Phase 4 of the claims-PO engine port).
// Upload an assessor's PDF → backend parses it → review screen at
// /claims-po/{id} → two DRAFT purchase orders (repairer + parts).
// The server owns all parsing and money math.

import { useEffect, useState, useCallback, useRef } from 'react'
import { useRouter } from 'next/navigation'
import { listClaimsAssessments, uploadClaimsAssessment, getClaimsAssessment, getToken } from '@/lib/api'
import type { ClaimsAssessment } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { LoadingTable } from '@/components/ui/loading'
import { AlertCircle, Upload, FileText, Loader2 } from 'lucide-react'

const STATUS_STYLES: Record<string, string> = {
  uploaded:         'bg-gray-100 text-gray-700 border-gray-300',
  parsing:          'bg-amber-50 text-amber-800 border-amber-300',
  ready_for_review: 'bg-blue-50 text-blue-800 border-blue-300',
  parse_failed:     'bg-red-50 text-red-800 border-red-300',
  pos_created:      'bg-emerald-50 text-emerald-800 border-emerald-300',
  failed:           'bg-red-50 text-red-800 border-red-300',
}

export default function ClaimsPoListPage() {
  const router = useRouter()
  const fileRef = useRef<HTMLInputElement>(null)

  const [rows, setRows]           = useState<ClaimsAssessment[]>([])
  const [loading, setLoading]     = useState(true)
  const [uploading, setUploading] = useState(false)
  const [proc, setProc] = useState<null | {
    id: string; pct: number; stage: string; label: string; etaMs: number; startedAt: number
  }>(null)
  const [nowTick, setNowTick] = useState(0)   // 1s ticker so the ETA counts down
  const [error, setError]         = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await listClaimsAssessments()
      setRows(res.results)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load assessments')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load()
  }, [load, router])

  const STAGE_LABEL: Record<string, string> = {
    uploaded: 'Uploaded — starting…',
    parsing: 'Reading the assessment PDF…',
    generating: 'Creating the purchase orders…',
    done: 'Done',
    failed: 'Failed',
  }

  async function onFileChosen(file: File | null) {
    if (!file) return
    setError(null)
    setUploading(true)
    try {
      // Instant return (202) — the parse + PO-generation runs in the
      // background; we show a progress bar and poll until it finishes.
      const res = await uploadClaimsAssessment(file)
      setProc({
        id: res.id, pct: res.progress_pct || 8, stage: res.progress_stage,
        label: STAGE_LABEL[res.progress_stage] || 'Working…',
        etaMs: res.eta_ms || 22000, startedAt: Date.now(),
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed')
    } finally {
      setUploading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  // Poll the in-flight assessment until the background pipeline finishes.
  // Gives up after repeated errors or a hard deadline so it can never spin
  // forever (Fable audit 2026-07-08).
  useEffect(() => {
    if (!proc) return
    let stop = false
    let fails = 0
    const startedAt = proc.startedAt
    const deadlineMs = Math.max(90000, (proc.etaMs || 22000) * 5)
    const poll = async () => {
      if (Date.now() - startedAt > deadlineMs) {
        setError('This is taking longer than expected. It may still finish — check the list below in a moment, and re-upload only if it does not appear.')
        setProc(null); await load(); return
      }
      try {
        const a = await getClaimsAssessment(proc.id)
        if (stop) return
        fails = 0
        if (a.progress_stage === 'done') {
          router.push(`/claims-po/${proc.id}`)
          return
        }
        if (a.progress_stage === 'failed') {
          setError(a.parse_error || 'The PDF could not be read as a vehicle assessment.')
          setProc(null)
          await load()
          return
        }
        setProc((cur) => cur && {
          ...cur, pct: a.progress_pct || cur.pct,
          stage: a.progress_stage,
          label: STAGE_LABEL[a.progress_stage] || cur.label,
        })
      } catch {
        fails += 1
        if (fails >= 6) {   // ~7s of solid failures — stop, don't spin forever
          setError('We lost contact while processing the file. Please check the list below and re-upload if it did not go through.')
          setProc(null); await load()
        }
      }
    }
    const iv = setInterval(poll, 1200)
    poll()
    return () => { stop = true; clearInterval(iv) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [proc?.id])

  // 1-second ticker so the ETA countdown moves while we poll.
  useEffect(() => {
    if (!proc) return
    const t = setInterval(() => setNowTick((n) => n + 1), 1000)
    return () => clearInterval(t)
  }, [proc])

  return (
    <div className="min-h-screen bg-[#F8F9FA]">
      <TopBar title="Claims PO" subtitle="Assessment PDF → draft repairer + parts purchase orders" />

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 space-y-4">
        <div className="flex items-center justify-between">
          <p className="text-sm text-gray-600">
            Upload an assessor&apos;s report (PDF). The engine parses it and opens the review
            screen where you allocate vendors and generate the two draft POs.
          </p>
          <div className="flex items-center gap-2">
            <input
              ref={fileRef}
              type="file"
              accept="application/pdf,.pdf"
              className="hidden"
              aria-label="Assessment PDF"
              onChange={(e) => onFileChosen(e.target.files?.[0] ?? null)}
            />
            <Button
              className="bg-[#0D1B2A] hover:bg-[#1a2940] text-white"
              disabled={uploading || !!proc}
              onClick={() => fileRef.current?.click()}
            >
              <Upload className="w-4 h-4 mr-1" />
              {uploading ? 'Uploading…' : proc ? 'Processing…' : 'Upload assessment PDF'}
            </Button>
          </div>
        </div>

        {proc && (() => {
          void nowTick   // read the 1s ticker so the countdown re-renders
          const elapsed = Date.now() - proc.startedAt
          const remain = Math.max(0, Math.round((proc.etaMs - elapsed) / 1000))
          const shownPct = Math.min(96, Math.max(proc.pct, Math.round((elapsed / proc.etaMs) * 90)))
          return (
            <Card className="border-[#F4A623]/40 bg-[#FFF8EE]">
              <CardContent className="p-4">
                <div className="flex items-center gap-2 mb-2">
                  <Loader2 className="w-4 h-4 text-[#B04E00] animate-spin" />
                  <span className="text-sm font-medium text-[#0D1B2A]">{proc.label}</span>
                  <span className="ml-auto text-xs text-gray-600 tabular-nums">
                    {remain > 0 ? `about ${remain}s left` : 'taking longer than usual…'}
                  </span>
                </div>
                <div className="h-2.5 w-full rounded-full bg-[#F4A623]/20 overflow-hidden">
                  <div
                    className="h-full rounded-full bg-[#F4A623] transition-[width] duration-700 ease-out"
                    style={{ width: `${shownPct}%` }}
                  />
                </div>
                <p className="mt-2 text-xs text-gray-500">
                  You can keep working — we&apos;ll open the review screen as soon as it&apos;s ready.
                </p>
              </CardContent>
            </Card>
          )
        })()}

        {error && (
          <Card className="border-red-200 bg-red-50">
            <CardContent className="p-3 flex items-start gap-2">
              <AlertCircle className="w-4 h-4 text-red-700 mt-0.5" />
              <span className="text-sm text-red-700">{error}</span>
            </CardContent>
          </Card>
        )}

        <Card>
          <CardContent className="p-0">
            {loading ? (
              <div className="p-6"><LoadingTable rows={6} /></div>
            ) : rows.length === 0 ? (
              <div className="p-12 text-center text-gray-500">
                <FileText className="w-10 h-10 mx-auto mb-2 opacity-40" />
                <p className="text-sm">No assessments uploaded yet.</p>
              </div>
            ) : (
              <table className="w-full text-sm">
                <thead className="bg-gray-50 border-b border-gray-200">
                  <tr className="text-left text-xs uppercase text-gray-600">
                    <th className="px-4 py-3">Claim #</th>
                    <th className="px-4 py-3">Client</th>
                    <th className="px-4 py-3">Registration</th>
                    <th className="px-4 py-3">Status</th>
                    <th className="px-4 py-3">Uploaded</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((a) => (
                    <tr key={a.id}
                      className="border-b border-gray-100 hover:bg-gray-50 cursor-pointer"
                      onClick={() => router.push(`/claims-po/${a.id}`)}>
                      <td className="px-4 py-3 font-mono text-xs">{a.claim_number || '—'}</td>
                      <td className="px-4 py-3">{a.client_name || '—'}</td>
                      <td className="px-4 py-3">{a.registration || '—'}</td>
                      <td className="px-4 py-3">
                        <span className={`text-xs px-2 py-0.5 rounded-full border ${STATUS_STYLES[a.status] || 'bg-gray-100'}`}>
                          {a.status_display}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-gray-600">
                        {a.created_at ? new Date(a.created_at).toLocaleString() : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
