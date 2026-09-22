'use client'

/**
 * /internal-audit/follow-up — Follow-up & Remediation Tracking (spec Module 7).
 *
 * Overdue is DERIVED (target date passed, not yet implemented) — never a stale
 * manual flag. Re-test date defaults from the finding rating (High quarterly,
 * Medium semi-annual, Low annual). Closing an action as Implemented requires
 * evidence. Editing gated to the audit function.
 *
 * Backend: /api/v1/internal-audit/followups/
 */

import { useCallback, useEffect, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { apiFetch, getMe, type UserProfile } from '@/lib/api'
import { AlertCircle } from 'lucide-react'

const NAVY = '#1D3270'

interface FollowUp {
  id: string
  finding: string
  status: string
  status_label: string
  evidence_reference: string
  next_retest_date: string | null
  notes: string
  is_overdue: boolean
}

const STATUS_TONE: Record<string, string> = {
  not_started: '#6B7280', in_progress: '#1E88E5',
  implemented: '#0F8B6C', risk_accepted: '#E8590C',
}

export default function FollowUpTracking() {
  const [me, setMe] = useState<UserProfile | null>(null)
  const [rows, setRows] = useState<FollowUp[]>([])
  const [err, setErr] = useState<string | null>(null)

  const load = useCallback(() => {
    // Paginated ({count,results:[...]}) — normalise to an array before filtering.
    apiFetch<any>('/internal-audit/followups/')
      .then((res) => setRows(Array.isArray(res) ? res : (res?.results ?? [])))
      .catch((e) => setErr(String(e)))
  }, [])
  useEffect(() => { getMe().then(setMe).catch(() => {}); load() }, [load])

  const overdue = rows.filter((r) => r.is_overdue).length

  return (
    <div>
      <TopBar title="Internal Audit — Follow-up Tracking" />
      <div className="p-6 space-y-5">
        <div className="flex items-center gap-3 text-sm text-gray-500">
          <span>{rows.length} tracked actions.</span>
          {overdue > 0 && (
            <span className="inline-flex items-center gap-1 font-medium text-red-600">
              <AlertCircle className="h-4 w-4" /> {overdue} overdue
            </span>
          )}
        </div>

        {err && <Card><CardContent className="p-4 text-sm text-red-600">{err}</CardContent></Card>}

        <Card>
          <CardContent className="p-0">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-xs uppercase text-gray-400">
                  <th className="p-3">Finding</th><th className="p-3">Status</th>
                  <th className="p-3">Re-test due</th><th className="p-3">Evidence</th><th className="p-3">Overdue</th>
                </tr>
              </thead>
              <tbody>
                {rows.length === 0 && (
                  <tr><td colSpan={5} className="p-6 text-center text-gray-400">No follow-up actions yet.</td></tr>
                )}
                {rows.map((r) => (
                  <tr key={r.id} className="border-b last:border-0 hover:bg-gray-50">
                    <td className="p-3 text-gray-500">{r.finding.slice(0, 8)}…</td>
                    <td className="p-3">
                      <span className="rounded px-2 py-0.5 text-xs font-semibold text-white"
                        style={{ background: STATUS_TONE[r.status] || '#6B7280' }}>
                        {r.status_label}
                      </span>
                    </td>
                    <td className="p-3 text-gray-500">{r.next_retest_date || '—'}</td>
                    <td className="p-3 text-gray-500">{r.evidence_reference || '—'}</td>
                    <td className="p-3">
                      {r.is_overdue
                        ? <span className="font-semibold text-red-600">Overdue</span>
                        : <span className="text-gray-400">—</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>

        <p className="text-xs text-gray-400" style={{ color: NAVY }}>
          Re-test cadence follows the finding rating: High → quarterly, Medium → semi-annual, Low → annual.
        </p>
      </div>
    </div>
  )
}
