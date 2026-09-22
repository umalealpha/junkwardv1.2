'use client'

/**
 * Shown in place of a page that belongs to one entity when a different company
 * is active. Hiding the sidebar link is not enough on its own — the route is
 * still reachable by URL, bookmark or the command palette.
 */
import { Building2 } from 'lucide-react'

export function EntityScopeNotice({
  entityName,
  activeName,
  what,
}: {
  /** The entity this page belongs to, e.g. "Alpha Direct Insurtech". */
  entityName: string
  /** The company currently selected, for the "you are in X" line. */
  activeName?: string | null
  /** What is unavailable, e.g. "The Strategic Plan Library". */
  what: string
}) {
  return (
    <div className="max-w-xl mx-auto mt-16 px-6">
      <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/60 p-8 text-center">
        <div className="mx-auto w-12 h-12 rounded-full bg-amber-50 dark:bg-amber-900/30 flex items-center justify-center mb-5">
          <Building2 className="w-6 h-6 text-amber-600 dark:text-amber-400" aria-hidden="true" />
        </div>
        <h1 className="text-lg font-semibold text-slate-900 dark:text-slate-100">
          Not available for this company
        </h1>
        <p className="mt-3 text-sm leading-relaxed text-slate-600 dark:text-slate-300">
          {what} belongs to <span className="font-semibold">{entityName}</span>.
          {activeName ? (
            <> You are currently viewing <span className="font-semibold">{activeName}</span>.</>
          ) : null}
        </p>
        <p className="mt-4 text-sm text-slate-500 dark:text-slate-400">
          Switch the company at the top of the screen to {entityName} to open it.
        </p>
      </div>
    </div>
  )
}
