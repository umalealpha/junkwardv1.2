'use client'

import { useEffect, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import { getToken, getBugReports, updateBugReport, runBugTriage } from '@/lib/api'
import type { BugReport, BugReportList } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { LoadingCard } from '@/components/ui/loading'
import { cn } from '@/lib/utils'
import { Bug, CheckCircle2, AlertCircle, Save, RefreshCw, Wand2 } from 'lucide-react'

const STATUS_COLOR: Record<string, string> = {
  new:         '#6B7280',
  triaged:     '#1D4ED8',
  in_progress: '#CC6C00',
  resolved:    '#059669',
  wont_fix:    '#DC2626',
}

function Badge({ status, label }: { status: string; label: string }) {
  const c = STATUS_COLOR[status] || '#6B7280'
  return (
    <span className="inline-block rounded-full px-2.5 py-0.5 text-xs font-semibold text-white"
          style={{ background: c }}>{label}</span>
  )
}

function Row({ r, isTriager, statuses, onSaved }: {
  r: BugReport; isTriager: boolean
  statuses: { value: string; label: string }[]
  onSaved: (r: BugReport) => void
}) {
  const [status, setStatus] = useState(r.status)
  const [note, setNote]     = useState(r.resolution_note || '')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved]   = useState<string | null>(null)
  const [err, setErr]       = useState<string | null>(null)
  const [queuing, setQueuing] = useState(false)
  const [qcing, setQcing] = useState(false)
  const dirty = status !== r.status || note !== (r.resolution_note || '')

  // Keep the controls honest with the server after a background refetch — but
  // never clobber an in-progress edit. If the triager has unsaved changes the
  // row is `dirty`, so we leave it alone; otherwise we adopt the latest server
  // status/note so the dropdown matches the badge. (Fixes the stale-board bug:
  // a status changed elsewhere now reflects here without a manual reload.)
  useEffect(() => {
    if (!dirty) { setStatus(r.status); setNote(r.resolution_note || '') }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [r.status, r.resolution_note])

  const save = async () => {
    setSaving(true); setErr(null); setSaved(null)
    try {
      const updated = await updateBugReport(r.id, { status, resolution_note: note })
      onSaved(updated)
      setSaved((updated as any).feedback_emailed ? 'Saved — reporter emailed.' : 'Saved.')
    } catch (e: any) {
      setErr(e?.message || 'Save failed.')
    } finally { setSaving(false) }
  }

  const requestAiFix = async () => {
    setQueuing(true); setErr(null); setSaved(null)
    try {
      const updated = await updateBugReport(r.id, { triage_requested: true })
      onSaved(updated)
      setSaved('Queued for AI fix — a draft PR will be opened, then you review & merge.')
    } catch (e: any) {
      setErr(e?.message || 'Could not queue AI fix.')
    } finally { setQueuing(false) }
  }

  const sendToManusQc = async () => {
    setQcing(true); setErr(null); setSaved(null)
    try {
      const updated = await updateBugReport(r.id, { qc_requested: true })
      onSaved(updated)
      setSaved('Sent to Manus for QC — its findings will appear back here.')
    } catch (e: any) {
      setErr(e?.message || 'Could not send to Manus.')
    } finally { setQcing(false) }
  }

  return (
    <Card>
      <CardContent className="py-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 flex-wrap">
              <Badge status={r.status} label={r.status_label} />
              {r.page_url && <span className="text-xs text-[#6B7280] font-mono">{r.page_url}</span>}
              <span className="text-xs text-[#6B7280]">{new Date(r.created_at).toLocaleString()}</span>
            </div>
            <p className="text-sm text-[#111827] mt-2 whitespace-pre-wrap">{r.description}</p>
            <div className="flex items-center gap-3 mt-2 text-xs text-[#6B7280] flex-wrap">
              <span>{r.word_count} words</span>
              <span>{r.screenshot_count} screenshots</span>
              {isTriager && r.reporter_email && <span>by {r.reporter_email}</span>}
              {r.triaged_by && <span>· triaged by {r.triaged_by}</span>}
              <span className="font-mono">{r.id.slice(0, 8)}</span>
              {r.triage_pr_url && (
                <a href={r.triage_pr_url} target="_blank" rel="noopener noreferrer"
                   className="text-[#1D4ED8] font-semibold underline decoration-dotted">Draft PR ↗</a>
              )}
            </div>
            {!isTriager && r.resolution_note && (
              <div className="mt-3 bg-[#FAF7F2] border border-[#e5e7eb] rounded-lg p-3 text-sm text-[#374151]">
                <span className="text-xs text-[#6B7280] font-semibold block mb-1">Note from the team</span>
                {r.resolution_note}
              </div>
            )}
          </div>
        </div>

        {isTriager && (
          <div className="mt-4 pt-4 border-t border-[#F3F4F6] grid grid-cols-1 sm:grid-cols-[180px_1fr_auto] gap-3 items-end">
            <div>
              <label className="block text-xs text-[#374151] font-medium mb-1">Status</label>
              <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status"
                className="w-full bg-white border border-[#D1D5DB] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[#F07F00] focus:ring-[3px] focus:ring-[rgba(240,127,0,0.1)]">
                {statuses.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs text-[#374151] font-medium mb-1">
                Resolution note <span className="text-[#6B7280]">(emailed to the reporter on status change)</span>
              </label>
              <input value={note} onChange={(e) => setNote(e.target.value)} aria-label="Resolution note"
                placeholder="e.g. Fixed in deploy 8707d92 — please hard-refresh."
                className="w-full bg-white border border-[#D1D5DB] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[#F07F00] focus:ring-[3px] focus:ring-[rgba(240,127,0,0.1)]" />
            </div>
            <Button variant="primary" size="md" leftIcon={<Save className="w-3.5 h-3.5" />}
              disabled={!dirty || saving} loading={saving} onClick={save}>
              {saving ? 'Saving…' : 'Save'}
            </Button>
          </div>
        )}

        {isTriager && (
          <div className="mt-3 flex items-center gap-3 flex-wrap">
            {r.triage_requested ? (
              <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-[#1D4ED8] bg-[#EFF6FF] border border-[#BFDBFE] rounded-full px-3 py-1.5">
                <Wand2 className="w-3.5 h-3.5" /> Queued for AI fix — draft PR incoming
              </span>
            ) : (
              <Button variant="secondary" size="sm" leftIcon={<Wand2 className="w-3.5 h-3.5" />}
                disabled={queuing} loading={queuing} onClick={requestAiFix}>
                {queuing ? 'Queuing…' : 'Request AI fix'}
              </Button>
            )}
            <span className="text-xs text-[#6B7280]">
              AI drafts a fix as a <b>draft PR</b> — you review &amp; merge. It never deploys on its own.
            </span>
          </div>
        )}

        {/* Manus QC pickup (CFO 2026-08-29) — flag it here, Manus polls omni and
            posts its finding back onto this item. No more emailing Manus. */}
        {isTriager && (
          <div className="mt-3 flex items-center gap-3 flex-wrap">
            {r.qc_requested ? (
              <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-[#7C3AED] bg-[#F5F3FF] border border-[#DDD6FE] rounded-full px-3 py-1.5">
                <Wand2 className="w-3.5 h-3.5" /> With Manus for QC
                {r.qc_picked_up_at && <> · picked up {new Date(r.qc_picked_up_at).toLocaleString()}</>}
              </span>
            ) : (
              <Button variant="secondary" size="sm" leftIcon={<Wand2 className="w-3.5 h-3.5" />}
                disabled={qcing} loading={qcing} onClick={sendToManusQc}>
                {qcing ? 'Sending…' : 'Send to Manus for QC'}
              </Button>
            )}
            <span className="text-xs text-[#6B7280]">
              Manus reviews it and posts findings back here — no email needed.
            </span>
          </div>
        )}
        {(r.qc_result_note || r.qc_result_pr_url) && (
          <div className="mt-3 bg-[#F5F3FF] border border-[#DDD6FE] rounded-lg p-3 text-sm text-[#374151]">
            <span className="text-xs text-[#6B7280] font-semibold block mb-1">
              Manus QC finding{r.qc_result_at && <> · {new Date(r.qc_result_at).toLocaleString()}</>}
            </span>
            {r.qc_result_note && <p className="whitespace-pre-wrap">{r.qc_result_note}</p>}
            {r.qc_result_pr_url && (
              <a href={r.qc_result_pr_url} target="_blank" rel="noopener noreferrer"
                 className="text-[#1D4ED8] font-semibold underline decoration-dotted mt-1 inline-block">
                Manus draft PR ↗
              </a>
            )}
          </div>
        )}
        {saved && <p className="text-xs text-[#059669] mt-2 flex items-center gap-1"><CheckCircle2 className="w-3.5 h-3.5" />{saved}</p>}
        {err && <p className="text-xs text-[#DC2626] mt-2 flex items-center gap-1"><AlertCircle className="w-3.5 h-3.5" />{err}</p>}
      </CardContent>
    </Card>
  )
}

export default function BugReportsPage() {
  const router = useRouter()
  const [data, setData]       = useState<BugReportList | null>(null)
  const [loading, setLoading] = useState(true)
  const [filter, setFilter]   = useState('')
  const [lastSync, setLastSync] = useState<Date | null>(null)
  const filterRef = useRef(filter)
  useEffect(() => { filterRef.current = filter }, [filter])

  // background=true → silent refetch (no full-page spinner, no wipe on error),
  // used by the auto-refresh poll + focus listener below.
  const load = async (statusFilter?: string, opts?: { background?: boolean }) => {
    if (!opts?.background) setLoading(true)
    try {
      setData(await getBugReports(statusFilter || undefined))
      setLastSync(new Date())
    } catch {
      if (!opts?.background) setData(null)
    } finally {
      if (!opts?.background) setLoading(false)
    }
  }

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load()
    // Auto-refresh so the board never shows a stale status (CFO 2026-06-11 —
    // "I fixed it but it still says New"). Poll every 30s and refetch whenever
    // the tab regains focus, but only while the tab is visible.
    const tick = () => {
      if (typeof document !== 'undefined' && document.visibilityState === 'visible') {
        load(filterRef.current, { background: true })
      }
    }
    const iv = setInterval(tick, 30000)
    window.addEventListener('focus', tick)
    document.addEventListener('visibilitychange', tick)
    return () => {
      clearInterval(iv)
      window.removeEventListener('focus', tick)
      document.removeEventListener('visibilitychange', tick)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const isTriager = data?.is_triager ?? false
  const isCfo     = data?.is_cfo ?? false
  const statuses  = data?.statuses ?? []

  const [running, setRunning] = useState(false)
  const [runMsg, setRunMsg]   = useState<{ ok: boolean; text: string } | null>(null)

  const runTriage = async () => {
    setRunning(true); setRunMsg(null)
    try {
      const res = await runBugTriage()
      const moved = Object.entries(res.moved || {})
        .map(([k, n]) => `${n > 0 ? '+' : ''}${n} ${k}`).join(', ')
      setRunMsg({
        ok: true,
        text: res.processed === 0
          ? 'No new reports to triage.'
          : `Triaged ${res.processed} new report${res.processed === 1 ? '' : 's'}` +
            (moved ? ` (${moved}). Reporters emailed.` : '. Reporters emailed.') +
            (res.remaining ? ` ${res.remaining} still queued — press again to continue.` : ''),
      })
      await load(filter, { background: true })
    } catch (e: any) {
      setRunMsg({ ok: false, text: e?.message || 'Triage run failed.' })
    } finally { setRunning(false) }
  }

  const patchRow = (u: BugReport) =>
    setData(d => d ? { ...d, results: d.results.map(r => r.id === u.id ? u : r) } : d)

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar title="Bug Reports" breadcrumbs={[{ label: 'Bug Reports' }]}
        actions={
          <div className="flex items-center gap-2">
            {lastSync && (
              <span className="hidden sm:inline text-xs text-[#6B7280]">
                Updated {lastSync.toLocaleTimeString()} · auto
              </span>
            )}
            {isCfo && (
              <Button variant="primary" size="sm" leftIcon={<Wand2 className="w-3.5 h-3.5" />}
                disabled={running} loading={running} onClick={runTriage}
                title="Run the AI triage now instead of waiting for the hourly job">
                {running ? 'Running triage…' : 'Run triage now'}
              </Button>
            )}
            <Button variant="secondary" size="sm" leftIcon={<RefreshCw className="w-3.5 h-3.5" />}
              onClick={() => load(filter)}>Refresh</Button>
          </div>
        } />

      <div className="flex-1 p-6 max-w-4xl w-full mx-auto space-y-4">
        <div className="bg-[#0D1B2A] rounded-xl p-5 text-white flex items-center gap-4">
          <div className="flex-shrink-0 w-10 h-10 rounded-lg bg-[#F4A623] flex items-center justify-center">
            <Bug className="w-5 h-5 text-[#0D1B2A]" />
          </div>
          <div>
            <h2 className="text-lg font-bold">{isTriager ? 'Bug Reports — triage board' : 'Your bug reports'}</h2>
            <p className="text-sm text-white/80 mt-0.5">
              {isTriager
                ? 'Move a report through New → Triaged → In Progress → Resolved. Changing the status emails the reporter.'
                : 'Track the status of bugs you reported. You will get an email whenever the status changes.'}
            </p>
          </div>
        </div>

        {isCfo && (
          <div className="rounded-lg border border-[#F4A623]/40 bg-[#FFF8EC] px-4 py-3 text-sm text-[#7A4A00] flex items-start gap-2">
            <Wand2 className="w-4 h-4 mt-0.5 flex-shrink-0" />
            <span>
              <b>Run triage now</b> reads new reports, classifies them, updates each status and
              emails the reporter — the same job that runs every hour, on demand. It does not
              write or deploy code. To have the AI draft an actual code fix, use
              <b> Request AI fix</b> on a report (it opens a draft PR you review &amp; merge).
            </span>
          </div>
        )}

        {runMsg && (
          <div className={cn(
            'rounded-lg px-4 py-3 text-sm flex items-center gap-2 border',
            runMsg.ok
              ? 'bg-[#ECFDF5] border-[#A7F3D0] text-[#065F46]'
              : 'bg-[#FEF2F2] border-[#FECACA] text-[#991B1B]')}>
            {runMsg.ok ? <CheckCircle2 className="w-4 h-4" /> : <AlertCircle className="w-4 h-4" />}
            {runMsg.text}
          </div>
        )}

        {isTriager && (
          <div className="flex items-center gap-2 flex-wrap">
            {[{ value: '', label: 'All' }, ...statuses].map(s => (
              <button key={s.value || 'all'} onClick={() => { setFilter(s.value); load(s.value) }}
                className={cn('text-xs px-3 py-1.5 rounded-full border transition-colors',
                  filter === s.value
                    ? 'bg-[#0D1B2A] text-white border-[#0D1B2A]'
                    : 'bg-white text-[#374151] border-[#D1D5DB] hover:border-[#0D1B2A]')}>
                {s.label}
              </button>
            ))}
          </div>
        )}

        {loading ? (
          <LoadingCard message="Loading bug reports..." className="h-32" />
        ) : !data || data.results.length === 0 ? (
          <Card><CardContent className="py-12 text-center text-[#6B7280]">
            {isTriager ? 'No bug reports' + (filter ? ' with this status.' : ' yet.') : 'You have not reported any bugs yet.'}
          </CardContent></Card>
        ) : (
          data.results.map(r => (
            <Row key={r.id} r={r} isTriager={isTriager} statuses={statuses} onSaved={patchRow} />
          ))
        )}
      </div>
    </div>
  )
}
