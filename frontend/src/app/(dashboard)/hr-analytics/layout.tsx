'use client'

/**
 * /hr-analytics layout — reuses the HRIS second-factor password gate.
 *
 * Bug 2026-06-08: /hr-analytics is a sibling route of /hris, so it did NOT
 * inherit hris/layout.tsx's unlock gate. It called /hris/api/employees/
 * while still locked, the backend correctly returned 401
 * {requires_unlock:true}, and the page rendered that as a dead
 * "Couldn't load HR data — HTTP 401" error (every metric 0, charts empty)
 * instead of prompting for the HRIS password.
 *
 * Fix: wrap this route in the SAME gate the /hris pages use, so a locked
 * user is prompted for the HRIS password (8h unlock on UserProfile) before
 * the analytics page tries to load. No gate duplication — we render the
 * existing HRISLayout component.
 */
import type { ReactNode } from 'react'
import HRISLayout from '../hris/layout'

export default function HrAnalyticsLayout({ children }: { children: ReactNode }) {
  return <HRISLayout>{children}</HRISLayout>
}
