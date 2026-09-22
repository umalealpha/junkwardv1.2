'use client'

/**
 * /settings/m365-active-users — list of M365 licensed humans whose last
 * INTERACTIVE sign-in is within the last 6 months. Refreshed by the
 * weekly cron (Friday 06:00 SAST) via `python manage.py sync_m365_active_users`,
 * which also emails the list to HR + Unami.
 *
 * Read-only view: data lives in `licensing.M365ActiveUser`.
 */

import { useEffect, useMemo, useState } from 'react'
import { apiFetch } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { RefreshCw, Mail, Users, CheckCircle2, AlertCircle } from 'lucide-react'

interface ActiveUser {
  id: number
  object_id: string
  display_name: string
  email: string
  user_principal_name: string
  job_title: string
  department: string
  license_count: number
  last_interactive_signin_at: string | null
  refreshed_at: string
}

interface LastRun {
  started_at?: string
  finished_at?: string | null
  success?: boolean
  total_seen?: number
  active_count?: number
  inserted?: number
  updated?: number
  removed?: number
  cutoff_at?: string | null
  triggered_by?: string
  error?: string
  detail?: string
}

export default function M365ActiveUsersPage() {
  const [rows, setRows] = useState<ActiveUser[]>([])
  const [run, setRun] = useState<LastRun | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState('')

  useEffect(() => {
    let alive = true
    ;(async () => {
      try {
        setLoading(true)
        const [list, last] = await Promise.all([
          apiFetch<ActiveUser[]>('/m365-active-users/'),
          apiFetch<LastRun>('/m365-active-users/last-run/'),
        ])
        if (!alive) return
        setRows(Array.isArray(list) ? list : [])
        setRun(last || null)
      } catch (e) {
        if (!alive) return
        setError(String((e as Error).message || e))
      } finally {
        if (alive) setLoading(false)
      }
    })()
    return () => { alive = false }
  }, [])

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase()
    if (!q) return rows
    return rows.filter(r =>
      r.display_name.toLowerCase().includes(q) ||
      r.email.toLowerCase().includes(q) ||
      (r.department || '').toLowerCase().includes(q) ||
      (r.job_title || '').toLowerCase().includes(q)
    )
  }, [rows, filter])

  const csvHref = useMemo(() => {
    const header = ['DisplayName', 'Email', 'JobTitle', 'Department', 'LicenseCount', 'LastInteractiveSignIn']
    const lines = [header.join(',')]
    for (const r of filtered) {
      const cells = [
        r.display_name, r.email, r.job_title || '', r.department || '',
        String(r.license_count),
        r.last_interactive_signin_at ? r.last_interactive_signin_at.slice(0, 10) : '',
      ].map(c => `"${(c || '').replace(/"/g, '""')}"`)
      lines.push(cells.join(','))
    }
    return 'data:text/csv;charset=utf-8,' + encodeURIComponent(lines.join('\n'))
  }, [filtered])

  return (
    <div className="p-6 space-y-4">
      <TopBar title="M365 Active Users" subtitle="Real human users who signed in within the last 6 months. Refreshed every Friday morning." />

      <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-sm">Active</CardTitle></CardHeader>
          <CardContent className="text-2xl font-semibold flex items-center gap-2">
            <Users className="w-5 h-5 text-gray-400" /> {rows.length}
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-sm">Last sync</CardTitle></CardHeader>
          <CardContent className="text-sm">
            {run?.finished_at ? new Date(run.finished_at).toLocaleString() : (run?.detail || '—')}
            {' '}
            {run?.success === false && <span className="text-red-600 inline-flex items-center"><AlertCircle className="w-4 h-4 mr-1" />failed</span>}
            {run?.success && <span className="text-emerald-600 inline-flex items-center"><CheckCircle2 className="w-4 h-4 mr-1" />ok</span>}
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-sm">Cutoff</CardTitle></CardHeader>
          <CardContent className="text-sm">{run?.cutoff_at ? new Date(run.cutoff_at).toLocaleDateString() : '—'}</CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-sm">Last run delta</CardTitle></CardHeader>
          <CardContent className="text-sm">
            +{run?.inserted ?? 0} new · ~{run?.updated ?? 0} upd · −{run?.removed ?? 0} gone
          </CardContent>
        </Card>
      </div>

      <div className="bg-white border rounded shadow-sm">
        <div className="flex items-center gap-3 px-4 py-3 border-b">
          <input
            value={filter}
            onChange={e => setFilter(e.target.value)}
            placeholder="Filter by name / email / department…"
            className="flex-1 border rounded px-3 py-1.5 text-sm"
          />
          <a
            href={csvHref}
            download="m365-active-users.csv"
            className="text-sm text-blue-600 hover:underline inline-flex items-center gap-1"
          >
            <Mail className="w-4 h-4" /> Export CSV
          </a>
          <button
            type="button"
            onClick={() => location.reload()}
            className="text-sm text-gray-600 hover:text-gray-900 inline-flex items-center gap-1"
            title="The schedule refreshes weekly; this just reloads the page."
          >
            <RefreshCw className="w-4 h-4" /> Reload
          </button>
        </div>

        {loading && <div className="p-6 text-sm text-gray-500">Loading…</div>}
        {error && <div className="p-6 text-sm text-red-600">Error: {error}</div>}

        {!loading && !error && (
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-gray-600">
              <tr>
                <th className="px-3 py-2 text-left">#</th>
                <th className="px-3 py-2 text-left">Name</th>
                <th className="px-3 py-2 text-left">Email</th>
                <th className="px-3 py-2 text-left">Job title</th>
                <th className="px-3 py-2 text-left">Department</th>
                <th className="px-3 py-2 text-right">Lic</th>
                <th className="px-3 py-2 text-left">Last sign-in</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((r, i) => (
                <tr key={r.id} className="border-t hover:bg-gray-50">
                  <td className="px-3 py-1.5 text-gray-400">{i + 1}</td>
                  <td className="px-3 py-1.5">{r.display_name}</td>
                  <td className="px-3 py-1.5 text-gray-600">{r.email}</td>
                  <td className="px-3 py-1.5 text-gray-600">{r.job_title || '—'}</td>
                  <td className="px-3 py-1.5 text-gray-600">{r.department || '—'}</td>
                  <td className="px-3 py-1.5 text-right">{r.license_count}</td>
                  <td className="px-3 py-1.5 text-gray-600">
                    {r.last_interactive_signin_at ? r.last_interactive_signin_at.slice(0, 10) : '—'}
                  </td>
                </tr>
              ))}
              {filtered.length === 0 && (
                <tr><td colSpan={7} className="px-3 py-6 text-center text-gray-500">No matches.</td></tr>
              )}
            </tbody>
          </table>
        )}
      </div>

      <p className="text-xs text-gray-500">
        Source: Microsoft Entra ID (Graph signInActivity), service-principal pull.
        Definition: licensed humans whose last <em>interactive</em> sign-in is within the last 6 months.
        Refreshed by the cron entry under <code>infra/cron/m365-license-sync.cron</code> (Friday 06:00 SAST).
        HR and Unami receive a CSV by email each Friday.
      </p>
    </div>
  )
}
