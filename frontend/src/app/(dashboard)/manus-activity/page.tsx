'use client'

/**
 * Manus Activity — everything the automated reviewer account has changed.
 *
 * WHY THIS PAGE EXISTS (CFO 2026-08-09): Manus was given manager-level access so
 * it can click through Omni and find what is broken. An automated account that
 * can change real records must be watchable at a glance, by name, without anyone
 * remembering to type a filter into the general audit screen.
 *
 * It reads the SAME audit trail as /audit-log — deliberately. A second audit
 * system is a second thing that can silently stop recording, and then two
 * sources disagree about what happened. This is a lens, not a new log.
 *
 * Changes only. The audit trail records edits, not page views (CFO's call: a log
 * of every screen an AI opened would be noise, and would slow every request).
 */

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { getToken, SSO_SENTINEL_TOKEN } from '@/lib/api'
import { acquireApiToken, SSO_API_CALLS_READY } from '@/auth/msal'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { AlertCircle, ChevronLeft, ChevronRight, Bot, ShieldCheck } from 'lucide-react'

/** The reviewer account this page watches. */
const MANUS_USERNAME = 'manus'
const PAGE_SIZE = 50

interface AuditEntry {
  id: string
  table_name: string
  record_id: string
  action: string
  action_display: string
  old_values: Record<string, unknown> | null
  new_values: Record<string, unknown> | null
  user_username: string | null
  ip_address: string | null
  description: string | null
  created_at: string
}

interface PaginatedAudit {
  count: number
  next: string | null
  previous: string | null
  results: AuditEntry[]
}

/** Which fields actually changed, so the reader sees the edit, not the record. */
function changedFields(e: AuditEntry): string[] {
  const before = e.old_values || {}
  const after = e.new_values || {}
  const keys = new Set([...Object.keys(before), ...Object.keys(after)])
  return [...keys].filter(k => JSON.stringify(before[k]) !== JSON.stringify(after[k]))
}

export default function ManusActivityPage() {
  const router = useRouter()
  const [entries, setEntries] = useState<AuditEntry[]>([])
  const [count, setCount] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [open, setOpen] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const params = new URLSearchParams()
      params.set('page', String(page))
      params.set('page_size', String(PAGE_SIZE))
      params.set('user', MANUS_USERNAME)   // pinned — this page is only ever Manus
      // Same auth as /audit-log: MSAL bearer when SSO is live, else the DRF token.
      const headers: Record<string, string> = {}
      let bearer: string | null = null
      if (SSO_API_CALLS_READY) bearer = await acquireApiToken()
      if (bearer) {
        headers['Authorization'] = `Bearer ${bearer}`
      } else {
        const token = getToken()
        if (token && token !== SSO_SENTINEL_TOKEN) headers['Authorization'] = `Token ${token}`
      }
      const res = await fetch(`/api/v1/audit-log/?${params}`, { headers })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data: PaginatedAudit = await res.json()
      setEntries(data.results)
      setCount(data.count)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load the activity log')
    } finally {
      setLoading(false)
    }
  }, [page])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load()
  }, [load, router])

  const pages = Math.max(1, Math.ceil(count / PAGE_SIZE))

  return (
    <>
      <TopBar />
      <main className="mx-auto max-w-6xl px-4 py-6">
        <div className="mb-5 flex items-start gap-3">
          <Bot className="mt-0.5 h-7 w-7 text-[#F07F00]" strokeWidth={1.5} />
          <div>
            <h1 className="text-xl font-semibold text-[#0B0B3B]">Manus activity</h1>
            <p className="mt-1 text-sm text-[#6B7280]">
              Every record the automated reviewer has changed, newest first. It reads the
              same audit trail as the main log — nothing separate that could stop recording
              without anyone noticing.
            </p>
          </div>
        </div>

        {!loading && !error && count === 0 && (
          <Card>
            <CardContent className="flex items-center gap-3 py-8 text-sm text-[#6B7280]">
              <ShieldCheck className="h-5 w-5 text-[#059669]" strokeWidth={1.5} />
              Manus has not changed anything. Looking at screens is not recorded — only edits are.
            </CardContent>
          </Card>
        )}

        {error && (
          <div className="mb-4 flex items-center gap-2 rounded-lg bg-red-50 p-4 text-sm text-red-700">
            <AlertCircle className="h-4 w-4" /> {error}
          </div>
        )}

        {loading && <div className="py-8 text-sm text-[#6B7280]">Loading…</div>}

        {!loading && count > 0 && (
          <>
            <p className="mb-3 text-sm text-[#6B7280]">
              <b className="text-[#0B0B3B]">{count}</b> change{count === 1 ? '' : 's'} recorded
            </p>
            <div className="space-y-2">
              {entries.map(e => {
                const fields = changedFields(e)
                const isOpen = open === e.id
                return (
                  <Card key={e.id}>
                    <CardContent className="py-3">
                      <button
                        onClick={() => setOpen(isOpen ? null : e.id)}
                        className="flex w-full items-start justify-between gap-3 text-left"
                      >
                        <span className="min-w-0">
                          <span className="block text-sm font-semibold text-[#0B0B3B]">
                            {e.action_display || e.action} · {e.table_name}
                          </span>
                          <span className="mt-0.5 block truncate text-xs text-[#6B7280]">
                            {e.description || e.record_id}
                            {fields.length > 0 && ` · changed: ${fields.slice(0, 4).join(', ')}`}
                            {fields.length > 4 && ` +${fields.length - 4} more`}
                          </span>
                        </span>
                        <span className="shrink-0 text-xs text-[#6B7280]">
                          {new Date(e.created_at).toLocaleString('en-GB')}
                        </span>
                      </button>

                      {isOpen && (
                        <div className="mt-3 overflow-x-auto rounded-lg border border-[#E5E7EB] bg-[#F9FAFB] p-3">
                          <table className="w-full text-xs">
                            <tbody>
                              <tr className="text-[#6B7280]">
                                <td className="py-1 pr-3 font-semibold">Field</td>
                                <td className="py-1 pr-3 font-semibold">Before</td>
                                <td className="py-1 font-semibold">After</td>
                              </tr>
                              {fields.length === 0 && (
                                <tr><td colSpan={3} className="py-1 text-[#6B7280]">No field-level detail recorded.</td></tr>
                              )}
                              {fields.map(f => (
                                <tr key={f} className="align-top">
                                  <td className="py-1 pr-3 font-medium text-[#0B0B3B]">{f}</td>
                                  <td className="py-1 pr-3 text-[#B91C1C]">{JSON.stringify((e.old_values || {})[f]) ?? '—'}</td>
                                  <td className="py-1 text-[#059669]">{JSON.stringify((e.new_values || {})[f]) ?? '—'}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                          {e.ip_address && (
                            <p className="mt-2 text-xs text-[#6B7280]">From {e.ip_address}</p>
                          )}
                        </div>
                      )}
                    </CardContent>
                  </Card>
                )
              })}
            </div>

            {pages > 1 && (
              <div className="mt-4 flex items-center justify-between">
                <Button variant="outline" size="sm" disabled={page <= 1}
                        onClick={() => setPage(p => Math.max(1, p - 1))}>
                  <ChevronLeft className="mr-1 h-4 w-4" /> Previous
                </Button>
                <span className="text-sm text-[#6B7280]">Page {page} of {pages}</span>
                <Button variant="outline" size="sm" disabled={page >= pages}
                        onClick={() => setPage(p => p + 1)}>
                  Next <ChevronRight className="ml-1 h-4 w-4" />
                </Button>
              </div>
            )}
          </>
        )}
      </main>
    </>
  )
}
