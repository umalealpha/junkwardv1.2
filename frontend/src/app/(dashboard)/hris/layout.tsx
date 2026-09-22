'use client'

/**
 * /hris layout — second-factor password gate.
 *
 * CFO directive 2026-05-18: 'HR is confidential. Whenever someone clicks
 * HRIS, it should prompt for a password.' Even the five whitelisted
 * users (Prathap / Arun / Kago / Pako / Unami + superuser) must enter
 * the shared HRIS password before any HRIS page renders. The unlock
 * sticks for HRIS_UNLOCK_HOURS (default 8) on the server side; after
 * that the backend 401s with `requires_unlock: true` and this gate
 * re-prompts.
 */
import { useEffect, useState, type ReactNode } from 'react'
import { useRouter, usePathname } from 'next/navigation'
import { apiFetch, getToken } from '@/lib/api'
import { Lock, Loader2, AlertTriangle, KeyRound } from 'lucide-react'

interface LockStatus {
  allowed: boolean
  locked: boolean
  unlocked: boolean
  unlocked_until: string | null
  unlock_hours: number
  role: string
  reason: string
  capabilities?: string[]
}

// Employee Self-Service routes — every staff member (interns included) reaches
// their OWN leave / payslips / profile here, with NO whitelist and NO HRIS
// password (CFO 2026-07-09). Exact-match only, so /hris/leave/report and every
// other team/HR surface stays behind the wall below. The page components AND
// the backend _gate still enforce own-data scope, so this only opens the door.
// '/hris/induction' is self-service on purpose: every new joiner must sit the
// induction, and they are exactly the people NOT on the HRIS whitelist. The
// API behind it is IsAuthenticated with no _gate and scopes to the caller.
// '/hris/training' is deliberately NOT here — that one is Human Capital only.
const SELF_SERVICE_ROUTES = ['/hris', '/hris/leave', '/hris/payslips', '/hris/profile', '/hris/letters', '/hris/pip', '/hris/staff-loans', '/hris/leave-encashment', '/hris/disciplinary', '/hris/my-dialogue', '/hris/team-dialogues', '/hris/talent-cockpit', '/hris/pulse', '/hris/my-brief', '/hris/incentives', '/hris/induction']

// Manager tier (CFO 2026-08-07). A line manager who is not on the five-person
// HRIS whitelist hit "HRIS is restricted" here and could only give monthly
// feedback through the one-click email link — Bharath's original complaint.
// Gated on 'assess_team', which only 'mgr' and above hold, NOT on 'view_self'
// which every employee has. The page itself then scopes to the caller's own
// reports and the API 403s anyone else, so this is the outermost of three
// checks, not the only one.
const TEAM_ROUTES = ['/hris/monthly-feedback']

export default function HRISLayout({ children }: { children: ReactNode }) {
  const router = useRouter()
  const pathname = usePathname()
  const [status, setStatus] = useState<LockStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const probe = async () => {
    setLoading(true); setError(null)
    try {
      const data = await apiFetch<LockStatus>('/hris/lock-status/')
      setStatus(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to check HRIS lock status')
    } finally { setLoading(false) }
  }

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    probe()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!password) return
    setSubmitting(true); setError(null)
    try {
      const data = await apiFetch<LockStatus & { unlocked: boolean }>('/hris/unlock/', {
        method: 'POST',
        body: JSON.stringify({ password }),
      })
      if (data.unlocked) {
        setPassword('')
        await probe()
      } else {
        setError('Unlock failed.')
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Unlock failed'
      setError(msg.includes('Incorrect') ? 'Incorrect HRIS password.' : msg)
    } finally { setSubmitting(false) }
  }

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[#F9FAFB]">
        <h1 className="sr-only">HRIS</h1>
        <Loader2 className="w-6 h-6 animate-spin text-[#6B7280]" />
      </div>
    )
  }

  // Employee Self-Service pass-through — anyone reaching their OWN leave /
  // payslips / profile / incentives is let straight in, before BOTH the
  // "restricted" wall and the password prompt. This is the fix for staff +
  // interns being walled out of their own HR (CFO 2026-07-09).
  //
  // NOT conditioned on `!status.allowed` (CFO 2026-08-21). It was, and — exactly
  // as with TEAM_ROUTES below (CFO 2026-08-09) — that made being whitelisted
  // WORSE than not: a whitelisted user (e.g. a manager applying incentives for
  // her own team) fell straight through to the shared-HRIS-password prompt on
  // her own self-service page. 'view_self' is held by every authenticated role,
  // and the backend re-enforces every self-service surface, so this is the
  // outermost of the checks, not the only one.
  if (status
      && SELF_SERVICE_ROUTES.includes(pathname)
      && (status.capabilities || []).includes('view_self')) {
    return <>{children}</>
  }

  // Manager pass-through — their own team's monthly feedback.
  //
  // NOT conditioned on `!status.allowed` (CFO 2026-08-09). It was, and that made
  // it fire only for managers who were OFF the whitelist. A manager who is ON it
  // fell straight through to the password prompt below — so the COO could not
  // coach his own team without being told the shared HRIS password, which is the
  // one thing ten managers must not all be told. Being whitelisted made the
  // experience WORSE than not being whitelisted.
  //
  // 'assess_team' is the real per-manager control and the backend enforces it
  // again (TEAM_CAPS in hris/feature_views.py), with the page scoping to the
  // caller's own reports. This is the outermost of three checks, not the only one.
  if (status
      && TEAM_ROUTES.includes(pathname)
      && (status.capabilities || []).includes('assess_team')) {
    return <>{children}</>
  }

  // Not whitelisted at all
  if (status && !status.allowed) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[#F9FAFB] p-6">
        <div className="bg-white rounded-lg border border-[#E5E7EB] p-8 max-w-md text-center">
          <Lock className="w-12 h-12 mx-auto text-[#DC2626] mb-4" strokeWidth={1.5} />
          <h1 className="text-lg font-semibold text-[#0B0B3B]">HRIS is restricted</h1>
          <p className="text-sm text-[#6B7280] mt-2">
            HR data is confidential and limited to authorised personnel.
            Contact the CFO if you believe you should have access.
          </p>
          <button onClick={() => router.replace('/dashboard')}
            className="mt-6 inline-flex items-center gap-2 px-4 py-2 bg-[#F07F00] text-white rounded text-sm font-semibold">
            Back to dashboard
          </button>
        </div>
      </div>
    )
  }

  // Whitelisted but locked → prompt for password
  if (status && status.locked) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[#F9FAFB] p-6">
        <form onSubmit={submit}
          className="bg-white rounded-lg border border-[#E5E7EB] p-8 max-w-md w-full">
          <div className="flex items-center gap-3 mb-4">
            <KeyRound className="w-7 h-7 text-[#F07F00]" strokeWidth={1.5} />
            <div>
              <h1 className="text-lg font-semibold text-[#0B0B3B]">HRIS password required</h1>
              <p className="text-xs text-[#6B7280]">
                HR data is confidential. Enter the HRIS password to continue.
              </p>
            </div>
          </div>
          <label className="block text-xs font-semibold text-[#374151] uppercase tracking-wider mb-1">
            HRIS password
          </label>
          <input
            type="password" autoFocus
            value={password} onChange={e => setPassword(e.target.value)}
            className="w-full h-10 px-3 border border-[#D1D5DB] rounded text-sm focus:outline-none focus:border-[#F07F00]"
            placeholder="Enter password"
          />
          {error && (
            <div className="mt-3 text-xs text-[#B91C1C] flex items-center gap-1.5">
              <AlertTriangle className="w-3.5 h-3.5" /> {error}
            </div>
          )}
          <button type="submit" disabled={submitting || !password}
            className="mt-5 w-full h-10 bg-[#F07F00] text-white rounded font-semibold disabled:opacity-60">
            {submitting ? 'Unlocking…' : 'Unlock HRIS'}
          </button>
          <p className="mt-4 text-[11px] text-[#9CA3AF] text-center">
            Unlocks HRIS for {status.unlock_hours} hour{status.unlock_hours === 1 ? '' : 's'}.
            Cancel any time by signing out.
          </p>
          <button type="button" onClick={() => router.replace('/dashboard')}
            className="mt-2 w-full h-8 text-xs text-[#6B7280] hover:text-[#0B0B3B]">
            Back to dashboard
          </button>
        </form>
      </div>
    )
  }

  // Unlocked → render the HRIS pages
  return <>{children}</>
}
