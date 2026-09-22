'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import {
  getRecurringJEs, deleteRecurringJE, runRecurringJEs, getToken,
} from '@/lib/api'
import type { RecurringJEListItem } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  AlertCircle, CheckCircle, Plus, Edit2, Trash2,
  PlayCircle, RotateCw, Calendar,
} from 'lucide-react'

export default function RecurringJEsListPage() {
  const router = useRouter()
  const [items, setItems] = useState<RecurringJEListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [running, setRunning] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await getRecurringJEs()
      setItems(res.results)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load templates')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    const token = getToken()
    if (!token) { router.replace('/login'); return }
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load])

  function flash(msg: string) {
    setSuccess(msg)
    setTimeout(() => setSuccess(null), 4000)
  }

  async function runNow() {
    setRunning(true)
    setError(null)
    try {
      const res = await runRecurringJEs()
      flash(`Generated ${res.entries_generated} draft entries (considered ${res.templates_considered} templates).`)
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Run failed')
    } finally {
      setRunning(false)
    }
  }

  async function remove(item: RecurringJEListItem) {
    if (!confirm(`Delete recurring template "${item.name}"? Existing generated entries are unaffected.`)) return
    try {
      await deleteRecurringJE(item.id)
      flash(`Deleted ${item.name}`)
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Delete failed')
    }
  }

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Recurring Journal Entries"
        breadcrumbs={[{ label: 'Settings' }, { label: 'Recurring JEs' }]}
        actions={
          <div className="flex gap-2">
            <Button variant="outline" size="sm" leftIcon={<RotateCw className="w-3.5 h-3.5" />} onClick={runNow} disabled={running}>
              {running ? 'Running…' : 'Run due now'}
            </Button>
            <Button variant="accent" size="sm" leftIcon={<Plus className="w-3.5 h-3.5" />} onClick={() => router.push('/settings/recurring-journal-entries/new')}>
              New template
            </Button>
          </div>
        }
      />

      <div className="flex-1 p-6 space-y-4">
        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-[#DC2626]" />
            <p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}
        {success && (
          <div className="bg-[#ECFDF5] border border-[#A7F3D0] rounded-lg p-3 flex items-center gap-2">
            <CheckCircle className="w-4 h-4 text-[#047857]" />
            <p className="text-[#047857] text-sm">{success}</p>
          </div>
        )}

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Calendar className="w-4 h-4" />
              {items.length} template{items.length === 1 ? '' : 's'}
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            {loading ? (
              <p className="px-6 py-8 text-center text-sm text-[#6B7280]">Loading…</p>
            ) : items.length === 0 ? (
              <div className="px-6 py-12 text-center text-[#6B7280]">
                <Calendar className="w-10 h-10 mx-auto mb-3 text-[#D1D5DB]" strokeWidth={1.5} />
                <p className="text-sm">No recurring templates yet.</p>
                <p className="text-xs text-[#9CA3AF] mt-1">
                  Create one for monthly accruals, depreciation, prepaid amortisation, etc.
                </p>
              </div>
            ) : (
              <table className="w-full text-sm border-collapse">
                <thead className="bg-[#F3F4F6] border-b-2 border-[#E5E7EB]">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase">Name</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase">Frequency</th>
                    <th className="px-4 py-3 text-center text-xs font-semibold text-[#374151] uppercase">Day</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase hidden md:table-cell">Start</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase hidden md:table-cell">Last generated</th>
                    <th className="px-4 py-3 text-center text-xs font-semibold text-[#374151] uppercase">Lines</th>
                    <th className="px-4 py-3 text-center text-xs font-semibold text-[#374151] uppercase">Active</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#E5E7EB] bg-white">
                  {items.map(t => (
                    <tr key={t.id} className={!t.is_active ? 'opacity-50' : ''}>
                      <td className="px-4 py-3">
                        <div className="font-medium text-[#111827]">{t.name}</div>
                        <div className="text-xs text-[#6B7280] truncate">{t.description}</div>
                      </td>
                      <td className="px-4 py-3 text-[#374151]">{t.frequency_display}</td>
                      <td className="px-4 py-3 text-center text-[#374151]">{t.day_of_period}</td>
                      <td className="px-4 py-3 text-[#374151] hidden md:table-cell">{t.start_date}</td>
                      <td className="px-4 py-3 text-[#374151] hidden md:table-cell">{t.last_generated_for || '—'}</td>
                      <td className="px-4 py-3 text-center">{t.line_count}</td>
                      <td className="px-4 py-3 text-center">
                        {t.is_active
                          ? <span className="inline-block px-2 py-0.5 rounded-md text-xs bg-[#ECFDF5] text-[#047857] border border-[#A7F3D0]">Active</span>
                          : <span className="inline-block px-2 py-0.5 rounded-md text-xs bg-[#F3F4F6] text-[#6B7280] border border-[#D1D5DB]">Paused</span>}
                      </td>
                      <td className="px-4 py-3 text-right whitespace-nowrap">
                        <Button variant="ghost" size="sm" leftIcon={<Edit2 className="w-3.5 h-3.5" />} onClick={() => router.push(`/settings/recurring-journal-entries/${t.id}`)}>Edit</Button>
                        <Button variant="ghost" size="sm" leftIcon={<Trash2 className="w-3.5 h-3.5" />} onClick={() => remove(t)}>Delete</Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-4 text-xs text-[#6B7280]">
            <p className="flex items-start gap-2">
              <PlayCircle className="w-4 h-4 flex-shrink-0 mt-0.5 text-[#F07F00]" />
              <span>
                <strong>How it works:</strong> the daily scheduler creates DRAFT journal entries from each
                template when its next scheduled date arrives. Drafts still go through the
                Submit → Approve workflow — recurrence does <em>not</em> bypass approval.
                You can also click <strong>Run due now</strong> to backfill anything that has been missed.
              </span>
            </p>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
