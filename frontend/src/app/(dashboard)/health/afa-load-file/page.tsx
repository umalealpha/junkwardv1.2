'use client'

/**
 * /health/afa-load-file — the ADH → AFA member load file.
 *
 * What Ritah sees each morning: today's file, anything held back and the plain
 * reason why, a masked preview of what AFA would receive, and the Release
 * button. Sending is deliberately a human act until AFA answer the open
 * questions and a fortnight of files match what was previously submitted by
 * hand.
 *
 * The preview masks ID numbers, passports, bank accounts and birth years.
 * Deciding whether to release a file does not require reading 289 Omang
 * numbers. Access is gated on the server (403), not by hiding this page.
 */
import { useCallback, useEffect, useState } from 'react'
import {
  Loader2, RefreshCw, Send, ShieldAlert, AlertTriangle, CheckCircle2,
  Clock, FileWarning, Building2, Eye, EyeOff,
} from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import {
  getAfaRuns, getAfaRun, buildAfaRun, releaseAfaRun, getAfaGroups,
  type AfaRun, type AfaGroupMap,
} from '@/lib/api'

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'

const STATUS_STYLE: Record<AfaRun['status'], { bg: string; fg: string; icon: typeof Clock }> = {
  built:    { bg: '#FEF3E2', fg: '#8A5A00', icon: Clock },
  released: { bg: '#E8F0FE', fg: '#1A4B8F', icon: Send },
  sent:     { bg: '#E7F6EC', fg: '#1B6B36', icon: CheckCircle2 },
  failed:   { bg: '#FDECEC', fg: '#A11B1B', icon: AlertTriangle },
  aborted:  { bg: '#FDECEC', fg: '#A11B1B', icon: ShieldAlert },
}

function StatusChip({ run }: { run: AfaRun }) {
  const s = STATUS_STYLE[run.status] ?? STATUS_STYLE.built
  const Icon = s.icon
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium"
          style={{ background: s.bg, color: s.fg }}>
      <Icon size={13} /> {run.statusLabel}
    </span>
  )
}

export default function AfaLoadFilePage() {
  const [runs, setRuns] = useState<AfaRun[]>([])
  const [selected, setSelected] = useState<AfaRun | null>(null)
  const [groups, setGroups] = useState<AfaGroupMap[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<'build' | 'release' | null>(null)
  const [unmasked, setUnmasked] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const [r, g] = await Promise.all([getAfaRuns(), getAfaGroups()])
      setRuns(r.results)
      setGroups(g.results)
      if (r.results.length) setSelected(await getAfaRun(r.results[0].id))
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Could not load the load-file history.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const openRun = async (id: string, showAll = false) => {
    setError(null)
    try { setSelected(await getAfaRun(id, showAll)) }
    catch (e: unknown) { setError(e instanceof Error ? e.message : 'Could not open that run.') }
  }

  const onBuild = async () => {
    setBusy('build'); setError(null); setNotice(null)
    try {
      const run = await buildAfaRun()
      setNotice(`Built ${run.rowCount} rows for ${run.runDate}.`)
      await load()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'The build failed.')
    } finally { setBusy(null) }
  }

  const onRelease = async () => {
    if (!selected) return
    setBusy('release'); setError(null); setNotice(null)
    try {
      const run = await releaseAfaRun(selected.id)
      setNotice(run.status === 'sent'
        ? `Delivered ${run.fileName} to AFA.`
        : 'Released. Automatic sending is switched off, so nothing has left yet.')
      await load()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'The release failed.')
    } finally { setBusy(null) }
  }

  const unmappedGroups = groups.filter(g => !g.imedGroupName?.trim()).length

  return (
    <>
      <TopBar />
      <main className="mx-auto w-full max-w-6xl px-5 py-7">

        <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold" style={{ color: NAVY }}>AFA Load File</h1>
            <p className="mt-1 text-sm text-slate-500">
              The daily ADH membership submitted to AFA. Built from Graphite, checked here,
              sent when you release it.
            </p>
          </div>
          <div className="flex gap-2">
            <button onClick={() => void load()} disabled={loading}
                    className="inline-flex items-center gap-2 rounded-lg border border-slate-300 px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50">
              <RefreshCw size={15} className={loading ? 'animate-spin' : ''} /> Refresh
            </button>
            <button onClick={() => void onBuild()} disabled={busy !== null}
                    className="inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
                    style={{ background: NAVY }}>
              {busy === 'build' ? <Loader2 size={15} className="animate-spin" /> : <RefreshCw size={15} />}
              Build today&apos;s file
            </button>
          </div>
        </header>

        {selected && !selected.autosendEnabled && (
          <div className="mb-4 flex items-start gap-3 rounded-lg border p-3 text-sm"
               style={{ borderColor: ORANGE, background: '#FEF9F0' }}>
            <ShieldAlert size={18} style={{ color: ORANGE }} className="mt-0.5 shrink-0" />
            <p className="text-slate-700">
              <strong>Automatic sending is off.</strong> Files are built and checked here, but
              nothing reaches AFA until someone releases it. That stays the case until AFA
              confirm the open questions and a fortnight of files match what was sent by hand.
            </p>
          </div>
        )}

        {unmappedGroups > 0 && (
          <div className="mb-4 flex items-start gap-3 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm">
            <Building2 size={18} className="mt-0.5 shrink-0 text-amber-700" />
            <p className="text-slate-700">
              {unmappedGroups} employer {unmappedGroups === 1 ? 'group has' : 'groups have'} no
              iMed name recorded. Their members are held back rather than sent under a name AFA
              would reject.
            </p>
          </div>
        )}

        {error && (
          <div className="mb-4 rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-800">{error}</div>
        )}
        {notice && (
          <div className="mb-4 rounded-lg border border-emerald-300 bg-emerald-50 p-3 text-sm text-emerald-800">{notice}</div>
        )}

        {loading ? (
          <div className="flex items-center gap-2 py-16 text-slate-500">
            <Loader2 className="animate-spin" size={18} /> Loading…
          </div>
        ) : runs.length === 0 ? (
          <div className="rounded-xl border border-dashed border-slate-300 p-12 text-center text-slate-500">
            No load file has been built yet. Use <strong>Build today&apos;s file</strong> to make the first one.
          </div>
        ) : (
          <div className="grid gap-6 lg:grid-cols-[280px_1fr]">

            <aside className="space-y-1.5">
              {runs.map(r => (
                <button key={r.id} onClick={() => void openRun(r.id)}
                        className={`w-full rounded-lg border px-3 py-2.5 text-left transition ${
                          selected?.id === r.id ? 'border-slate-800 bg-white shadow-sm' : 'border-slate-200 hover:bg-white'}`}>
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm font-medium" style={{ color: NAVY }}>{r.runDate}</span>
                    <StatusChip run={r} />
                  </div>
                  <div className="mt-1 text-xs text-slate-500">
                    {r.rowCount} rows{r.heldCount ? ` · ${r.heldCount} held` : ''}
                  </div>
                </button>
              ))}
            </aside>

            {selected && (
              <section className="space-y-5">

                {selected.status === 'aborted' && (
                  <div className="rounded-lg border border-red-300 bg-red-50 p-4">
                    <div className="flex items-center gap-2 font-semibold text-red-800">
                      <ShieldAlert size={17} /> This run was stopped before a file was made
                    </div>
                    <p className="mt-2 text-sm text-red-900">{selected.abortReason}</p>
                  </div>
                )}

                <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                  {[
                    { label: 'Rows to send', value: selected.rowCount },
                    { label: 'New members', value: selected.newCount },
                    { label: 'Changed', value: selected.changedCount },
                    { label: 'Departures', value: selected.departureCount },
                  ].map(k => (
                    <div key={k.label} className="rounded-lg border border-slate-200 bg-white p-3">
                      <div className="text-xs text-slate-500">{k.label}</div>
                      <div className="mt-0.5 text-xl font-semibold" style={{ color: NAVY }}>{k.value}</div>
                    </div>
                  ))}
                </div>

                {selected.heldCount > 0 && (
                  <div className="rounded-lg border border-amber-300 bg-amber-50 p-4">
                    <div className="flex items-center gap-2 font-semibold text-amber-900">
                      <FileWarning size={17} /> {selected.heldCount} held back
                    </div>
                    <p className="mt-1 text-sm text-amber-900">
                      These are not in the file. Nothing was guessed on their behalf.
                    </p>
                    <ul className="mt-3 space-y-1.5">
                      {Object.entries(selected.heldReasons).map(([reason, n]) => (
                        <li key={reason} className="flex gap-3 text-sm text-slate-700">
                          <span className="w-10 shrink-0 text-right font-semibold tabular-nums">{n}</span>
                          <span>{reason}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                <div className="rounded-lg border border-slate-200 bg-white">
                  <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-4 py-3">
                    <div>
                      <div className="text-sm font-semibold" style={{ color: NAVY }}>
                        What AFA would receive
                      </div>
                      <div className="text-xs text-slate-500">
                        {selected.fileName} · pipe-delimited · 35 columns
                        {selected.masked ? ' · ID and bank numbers hidden' : ' · showing full detail'}
                      </div>
                    </div>
                    <button onClick={() => { setUnmasked(!unmasked); void openRun(selected.id, !unmasked) }}
                            className="inline-flex items-center gap-2 rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50">
                      {selected.masked ? <><Eye size={13} /> Show full detail</> : <><EyeOff size={13} /> Hide detail</>}
                    </button>
                  </div>
                  <div className="max-h-80 overflow-auto">
                    <pre className="p-4 font-mono text-[11px] leading-relaxed text-slate-700">
                      {(selected.preview ?? []).join('\n') || 'Nothing to send.'}
                    </pre>
                  </div>
                  {selected.previewTruncated && (
                    <div className="border-t border-slate-200 px-4 py-2 text-xs text-slate-500">
                      Showing the first 200 rows of {selected.rowCount}.
                    </div>
                  )}
                </div>

                <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-200 bg-white p-4">
                  <div className="text-sm text-slate-600">
                    {selected.status === 'sent' ? (
                      <>Delivered to AFA {selected.sentAt ? `on ${new Date(selected.sentAt).toLocaleString('en-GB')}` : ''}.</>
                    ) : selected.status === 'failed' ? (
                      <span className="text-red-700">{selected.sendError}</span>
                    ) : (
                      <>Releasing sends this file to AFA. Check the held list first.</>
                    )}
                  </div>
                  <button onClick={() => void onRelease()}
                          disabled={busy !== null || selected.status === 'sent'
                                    || selected.status === 'aborted' || selected.rowCount === 0}
                          className="inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40"
                          style={{ background: ORANGE }}>
                    {busy === 'release' ? <Loader2 size={15} className="animate-spin" /> : <Send size={15} />}
                    Release to AFA
                  </button>
                </div>

              </section>
            )}
          </div>
        )}
      </main>
    </>
  )
}
