'use client'

/**
 * Shared frame for the panels that draw Graphite's nightly analytics onto an
 * Omni working screen. It exists so every one of them says the same thing in
 * the same place: where the number came from and how old it is.
 *
 * That line is not decoration. These figures arrive from another system once a
 * night, so a panel that looks like the rest of Omni but is fourteen hours
 * stale would quietly invite someone to reconcile it against a live Omni
 * figure and find a difference that isn't a difference.
 */
import { ReactNode } from 'react'
import { AlertCircle, Loader2 } from 'lucide-react'

const NAVY = '#0D1B2A'

export function receivedLabel(iso: string | null | undefined): string {
  if (!iso) return 'not received yet'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return 'not received yet'
  return d.toLocaleString('en-GB', {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
  })
}

export function GraphitePanelShell({
  title, subtitle, receivedAt, loading, error, empty, emptyText, children,
  freshness,
}: {
  title: string
  subtitle?: string
  receivedAt?: string | null
  loading?: boolean
  error?: string | null
  empty?: boolean
  emptyText?: string
  // Overrides the "From Graphite · <when>" stamp. A panel Omni computes itself
  // has no push timestamp, and "not received yet" beside real figures reads as
  // if the screen were broken.
  freshness?: string
  children: ReactNode
}) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white overflow-hidden">
      <header className="px-4 py-3 border-b border-slate-100 flex items-start justify-between gap-4">
        <div>
          <h2 className="text-sm font-semibold" style={{ color: NAVY }}>{title}</h2>
          {subtitle && <p className="text-xs text-slate-500 mt-0.5">{subtitle}</p>}
        </div>
        <span className="text-[11px] text-slate-400 whitespace-nowrap pt-0.5">
          {freshness || `From Graphite · ${receivedLabel(receivedAt)}`}
        </span>
      </header>

      {loading ? (
        <div className="p-6 flex items-center gap-2 text-sm text-slate-500">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading…
        </div>
      ) : error ? (
        <div className="p-6 flex items-start gap-2 text-sm text-slate-600">
          <AlertCircle className="h-4 w-4 text-amber-500 mt-0.5" />
          <span>{error}</span>
        </div>
      ) : empty ? (
        <div className="p-6 text-sm text-slate-500">
          {emptyText || 'Graphite has not sent this feed yet.'}
        </div>
      ) : children}
    </section>
  )
}
