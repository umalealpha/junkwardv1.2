'use client'

/**
 * /cfo — retired 2026-08-22 (dashboard merge).
 *
 * The CFO Dashboard's unique control tiles now live in <CfoControlsSection/> at
 * the bottom of the single executive dashboard (/dashboard), which shows them
 * only to finance/CFO users. This route now redirects there so any old links or
 * bookmarks keep working.
 */

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'

export default function CfoDashboardRedirect() {
  const router = useRouter()
  useEffect(() => { router.replace('/dashboard') }, [router])
  return null
}
