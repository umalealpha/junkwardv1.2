'use client'

/**
 * /hris/leave-exceptions — READ-ONLY Time Doctor guard visibility
 * (CFO 2026-09-18, Easy PR E: "Time Doctor visibility").
 *
 * Line managers can now see whether their reports have tripped the productive-
 * hours guard, without being able to change anything. HR / exec see everyone;
 * a line manager sees their direct reports; anyone else sees only their own
 * row. The page is READ-ONLY by design: there is no button that could trigger
 * a deduction, override or write. Threshold values are unchanged from
 * hris.leave_excuse_service; this page only surfaces the state that path
 * already produces.
 */
import { useEffect, useState } from 'react'
import { authedHrisFetch } from '../_shared'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Eye, ShieldCheck } from 'lucide-react'

const NAVY = '#1D3270'
const ORANGE = '#F47C20'
const SERIF = 'Georgia, "Book Antiqua", serif'

interface Row {
  employee: string
  profile_id?: string
  date: string
  productive_hours: number
  band?: string
  status: string
  guard_state: string
  explanation?: string
  reason?: string
  held?: boolean
}

interface Day { date: string; snapshot: boolean; rows: Row[] }
interface Payload {
  me: { name: string; email: string }
  scope: 'hr' | 'manager' | 'self'
  low_hours: number
  read_only: boolean
  thresholds_unchanged: boolean
  days: Day[]
}

export default function LeaveExceptionsPage() {
  const [data, setData] = useState<Payload | null>(null)
  const [err, setErr] = useState<string>('')

  useEffect(() => {
    let alive = true
    authedHrisFetch('/hris/api/leave-exceptions/?days=7')
      .then(async r => {
        const body = await r.json().catch(() => ({}))
        if (!alive) return
        if (r.ok) setData(body as Payload)
        else setErr(body?.detail || `HTTP ${r.status}`)
      })
      .catch(e => alive && setErr(String(e)))
    return () => { alive = false }
  }, [])

  return (
    <div style={{ fontFamily: SERIF }} className="p-6 space-y-4">
      <TopBar />
      <div className="flex items-start gap-3 rounded border p-3"
           style={{ borderColor: ORANGE, background: '#FFF7EE' }}>
        <Eye className="mt-0.5 h-5 w-5" style={{ color: ORANGE }} />
        <div className="text-sm">
          <div className="font-semibold" style={{ color: NAVY }}>
            Read-only preview
          </div>
          <div>
            This page shows the current Time Doctor guard state for people you
            are authorised to see. Nothing on this page can trigger a
            deduction, override a decision, or change a threshold. Thresholds
            are unchanged from the existing policy.
          </div>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle style={{ color: NAVY }} className="flex items-center gap-2">
            <ShieldCheck className="h-5 w-5" /> Time Doctor guard — last 7 days
          </CardTitle>
        </CardHeader>
        <CardContent>
          {err && <div className="text-red-700 text-sm">{err}</div>}
          {!err && !data && <div className="text-sm text-gray-600">Loading…</div>}
          {data && (
            <>
              <div className="text-xs text-gray-600 mb-2">
                Scope: <strong>{data.scope}</strong> ·
                Low-hours line: <strong>{data.low_hours}h</strong> ·
                Read-only view — no deduction is triggered by opening this page.
              </div>
              <table className="w-full text-sm border-collapse">
                <thead>
                  <tr className="text-left border-b">
                    <th className="py-1 pr-3">Employee</th>
                    <th className="py-1 pr-3">Day</th>
                    <th className="py-1 pr-3">Tracked (productive h)</th>
                    <th className="py-1 pr-3">Shortfall vs {data.low_hours}h</th>
                    <th className="py-1 pr-3">Guard state</th>
                    <th className="py-1 pr-3">Reason / note</th>
                  </tr>
                </thead>
                <tbody>
                  {data.days.flatMap(d => d.rows).length === 0 && (
                    <tr><td className="py-2 text-gray-500" colSpan={6}>
                      No rows — nobody in your scope has tripped the guard
                      in the last 7 days.
                    </td></tr>
                  )}
                  {data.days.flatMap(d => d.rows.map(r => (
                    <tr key={`${d.date}-${r.profile_id}-${r.employee}`}
                        className="border-b last:border-b-0">
                      <td className="py-1 pr-3">{r.employee}</td>
                      <td className="py-1 pr-3">{r.date}</td>
                      <td className="py-1 pr-3">{r.productive_hours.toFixed(2)}</td>
                      <td className="py-1 pr-3">
                        {Math.max(0, data.low_hours - r.productive_hours).toFixed(2)}
                      </td>
                      <td className="py-1 pr-3">{r.guard_state || r.status}</td>
                      <td className="py-1 pr-3 text-gray-700">
                        {r.explanation || r.reason || (r.held ? 'hours still arriving' : '')}
                      </td>
                    </tr>
                  )))}
                </tbody>
              </table>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
