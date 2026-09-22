'use client'

/** /m/staff/money — RETIRED 2026-07-22. Its spend / refunds / PO surfaces were
 * folded into the unified /m/staff/approvals dashboard (one dashboard for
 * approvers, incl. the senior-accountant "refunds to process" list). This stub
 * only redirects so any old bookmark still lands on the right place. */
import { useEffect } from 'react'
import { useRouter } from 'next/navigation'

export default function StaffMoneyRedirect() {
  const router = useRouter()
  useEffect(() => { router.replace('/m/staff/approvals') }, [router])
  return null
}
