'use client'

/**
 * /cfo-snapshot — board/EXCO management snapshot (premium, claims, loss ratio).
 *
 * SECURITY FIX (2026-07-14): this used to be a public static file
 * (frontend/public/cfo-snapshot/index.html) served OUTSIDE any login check —
 * an operations-tier employee (or anyone with the URL, logged in or not)
 * could open it directly and see real management financials. It is now a
 * normal dashboard page: the (dashboard) layout already requires sign-in, and
 * this additionally reuses the SAME server-side permission gate as the real
 * CFO Dashboard (CanViewFinancials, via getCFODashboard()) before rendering
 * anything — no separate backend endpoint needed, and the same finance +
 * management allowlist applies everywhere.
 */
import { useEffect, useState } from 'react'
import { ShieldAlert } from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { getCFODashboard } from '@/lib/api'
import { SNAPSHOT_STYLE, SNAPSHOT_BODY } from './snapshot-content'

export default function CfoSnapshotPage() {
  const [state, setState] = useState<'checking' | 'allowed' | 'denied'>('checking')

  useEffect(() => {
    // Reuse the real CFO Dashboard's own gate as a pure permission probe —
    // we don't need its payload, just whether the call succeeds.
    getCFODashboard()
      .then(() => setState('allowed'))
      .catch(() => setState('denied'))
  }, [])

  return (
    <div>
      <TopBar title="CFO Snapshot" breadcrumbs={[{ label: 'Finance' }, { label: 'CFO Snapshot' }]} />
      {state === 'checking' && <div className="p-8 text-sm text-slate-400">Checking access…</div>}
      {state === 'denied' && (
        <div className="p-8 max-w-xl mx-auto">
          <div className="rounded-xl border border-red-200 bg-red-50 p-6 flex gap-3">
            <ShieldAlert className="h-5 w-5 text-red-600 flex-shrink-0 mt-0.5" />
            <div>
              <p className="font-medium text-red-800">Access restricted</p>
              <p className="text-sm text-red-700 mt-1">
                Financial data is restricted to finance and management staff.
              </p>
            </div>
          </div>
        </div>
      )}
      {state === 'allowed' && (
        <>
          <style dangerouslySetInnerHTML={{ __html: SNAPSHOT_STYLE }} />
          {/* Trusted, developer-authored static markup (see snapshot-content.ts) — not user input. */}
          <div dangerouslySetInnerHTML={{ __html: SNAPSHOT_BODY }} />
        </>
      )}
    </div>
  )
}
