'use client'

/**
 * Internal Audit — route guard.
 *
 * The module is visible only to the audit function (edit) and the exec/board
 * viewers: CEO, COO, CFO, Audit Committee (view-only). Everyone else gets a
 * plain "restricted" screen — the section does not even appear in their menu
 * (Sidebar gates on me.can_view_internal_audit).
 */

import { useEffect, useState } from 'react'
import { getMe, type UserProfile } from '@/lib/api'
import { Lock } from 'lucide-react'

export default function InternalAuditLayout({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<UserProfile | null>(null)
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    getMe().then(setMe).catch(() => setMe(null)).finally(() => setLoaded(true))
  }, [])

  if (!loaded) {
    return <div className="p-8 text-sm text-gray-500">Loading…</div>
  }

  if (!me?.can_view_internal_audit) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 p-16 text-center">
        <Lock className="h-10 w-10 text-gray-400" />
        <h2 className="text-lg font-semibold" style={{ color: '#1D3270' }}>
          Internal Audit is restricted
        </h2>
        <p className="max-w-md text-sm text-gray-500">
          This area is available to Internal Audit and the executive / board viewers only.
          If you need access, contact the Internal Auditor.
        </p>
      </div>
    )
  }

  return <>{children}</>
}
