'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { apiFetch } from '@/lib/api'
import { Plus, Loader2, Inbox } from 'lucide-react'

interface DPIACondition {
  id: string
  dpia: string
  phase: 'pilot' | 'rollout'
  phase_label: string
  text: string
  owner: string
  due_date: string | null
  done: boolean
  done_at: string | null
  order: number
}

interface DPIA {
  id: string
  project: string
  description: string
  data_types: string
  special_category: boolean
  risk_rating: 'P1' | 'P2' | 'P3'
  risk_rating_label: string
  residual_risk: 'low' | 'medium' | 'high'
  residual_risk_label: string
  status: string
  status_label: string
  dpo_reviewer: string
  dpo_signed: boolean
  dpo_signed_at: string | null
  compliance_officer: string
  compliance_signed: boolean
  compliance_signed_at: string | null
  cfo_signed: boolean
  cfo_signed_at: string | null
  deadline: string | null
  created_at: string
  updated_at: string
  conditions: DPIACondition[]
  pilot_total: number
  pilot_done: number
  rollout_total: number
  rollout_done: number
}

const ratingBg: Record<DPIA['risk_rating'], string> = {
  P1: '#dc2626',
  P2: '#f59e0b',
  P3: '#16a34a',
}

export default function DpoRegisterPage() {
  const router = useRouter()
  const [items, setItems] = useState<DPIA[]>([])
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setLoading(true)
      // API base + pagination handled per project convention (apiFetch prepends
      // /api/v1; list endpoints are paginated → tolerate {results:[...]}).
      const res = await apiFetch<any>('/dpo/dpia/?page_size=200')
      const data: DPIA[] = Array.isArray(res) ? res : (res?.results ?? [])
      setItems(data)
      setError(null)
    } catch {
      setError('Unable to load DPIA register.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const sortedItems = useMemo(() => {
    return [...items].sort((a, b) => b.created_at.localeCompare(a.created_at))
  }, [items])

  // Manus QC 14-Aug-2026: one click auto-created a persistent blank P3 draft
  // in the register before the user had entered anything. Ask for the project
  // name first via a small modal and only POST on Save — nothing is written to
  // the register until the user commits.
  const [showNewModal, setShowNewModal] = useState(false)
  const [newProject, setNewProject] = useState('')

  const openNewModal = () => {
    setError(null)
    setNewProject('')
    setShowNewModal(true)
  }

  const handleCreate = async () => {
    const name = newProject.trim()
    if (!name) {
      setError('Enter a project name to start the DPIA.')
      return
    }
    if (creating) return
    setCreating(true)
    setError(null)
    try {
      const created = await apiFetch<DPIA>('/dpo/dpia/', {
        method: 'POST',
        body: JSON.stringify({
          project: name,
          description: '',
          data_types: '',
          special_category: false,
          risk_rating: 'P3',
          residual_risk: 'medium',
          status: 'draft',
          dpo_reviewer: '',
          compliance_officer: '',
          deadline: null,
        }),
      })
      setShowNewModal(false)
      router.push(`/compliance/dpo/${created.id}`)
    } catch {
      setError('Unable to create DPIA.')
    } finally {
      setCreating(false)
    }
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <TopBar title="Data Protection — DPIA Register" />

      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
        <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h1 className="text-2xl font-bold" style={{ color: '#1D3270' }}>
              DPIA Register
            </h1>
            <p className="text-sm text-gray-500">
              Data Protection Impact Assessments — Botswana Data Protection Act
            </p>
          </div>

          <button
            onClick={openNewModal}
            disabled={creating}
            className="inline-flex items-center justify-center rounded-md px-4 py-2 text-sm font-semibold text-white shadow-sm transition-opacity hover:opacity-90 disabled:opacity-60"
            style={{ backgroundColor: '#F47C20' }}
          >
            {creating ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Plus className="mr-2 h-4 w-4" />
            )}
            New DPIA
          </button>
        </div>

        {showNewModal ? (
          <div
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
            onClick={() => (creating ? null : setShowNewModal(false))}
          >
            <div
              className="w-full max-w-md rounded-lg bg-white p-6 shadow-xl"
              onClick={(e) => e.stopPropagation()}
            >
              <h2 className="text-lg font-semibold" style={{ color: '#0D1B2A' }}>
                Start a new DPIA
              </h2>
              <p className="mt-1 text-sm text-gray-500">
                Give the assessment a project name. Nothing is saved to the register
                until you press Create.
              </p>
              <label className="mt-4 block text-xs font-medium uppercase tracking-wide text-gray-600">
                Project name
              </label>
              <input
                autoFocus
                value={newProject}
                onChange={(e) => setNewProject(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') handleCreate() }}
                placeholder="e.g. Health portal, Vendor onboarding, Payroll import"
                className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-orange-500 focus:outline-none focus:ring-1 focus:ring-orange-500"
              />
              {error ? (
                <p className="mt-2 text-sm text-red-700">{error}</p>
              ) : null}
              <div className="mt-6 flex justify-end gap-2">
                <button
                  onClick={() => setShowNewModal(false)}
                  disabled={creating}
                  className="rounded-md border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-60"
                >
                  Cancel
                </button>
                <button
                  onClick={handleCreate}
                  disabled={creating || !newProject.trim()}
                  className="inline-flex items-center rounded-md px-3 py-1.5 text-sm font-semibold text-white shadow-sm hover:opacity-90 disabled:opacity-60"
                  style={{ backgroundColor: '#F47C20' }}
                >
                  {creating ? (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  ) : null}
                  Create
                </button>
              </div>
            </div>
          </div>
        ) : null}

        {error ? (
          <div className="mb-4 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {error}
          </div>
        ) : null}

        {loading ? (
          <Card>
            <CardContent className="flex items-center justify-center py-20 text-gray-500">
              <Loader2 className="mr-2 h-5 w-5 animate-spin" />
              Loading DPIA register...
            </CardContent>
          </Card>
        ) : sortedItems.length === 0 ? (
          <Card>
            <CardContent className="flex flex-col items-center justify-center py-20 text-gray-500">
              <Inbox className="mb-3 h-10 w-10" />
              <p className="font-medium">No DPIAs yet</p>
              <p className="text-sm">Create a new DPIA to get started.</p>
            </CardContent>
          </Card>
        ) : (
          <Card>
            <CardContent className="p-0">
              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-gray-200">
                  <thead className="bg-gray-50">
                    <tr>
                      <th className="px-6 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500">
                        Project
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500">
                        Rating
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500">
                        Residual
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500">
                        Status
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500">
                        Deadline
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500">
                        Progress
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200 bg-white">
                    {sortedItems.map((item) => {
                      const total = item.pilot_total + item.rollout_total
                      const done = item.pilot_done + item.rollout_done
                      const pct = total === 0 ? 0 : Math.round((done / total) * 100)

                      return (
                        <tr
                          key={item.id}
                          onClick={() => router.push(`/compliance/dpo/${item.id}`)}
                          className="cursor-pointer hover:bg-gray-50"
                        >
                          <td className="whitespace-nowrap px-6 py-4 text-sm font-medium" style={{ color: '#1D3270' }}>
                            {item.project}
                          </td>
                          <td className="whitespace-nowrap px-6 py-4">
                            <span
                              className="inline-flex items-center rounded-full px-2.5 py-1 text-xs font-bold text-white"
                              style={{ backgroundColor: ratingBg[item.risk_rating] }}
                            >
                              {item.risk_rating}
                            </span>
                          </td>
                          <td className="whitespace-nowrap px-6 py-4 text-sm text-gray-700">
                            {item.residual_risk_label}
                          </td>
                          <td className="whitespace-nowrap px-6 py-4 text-sm text-gray-700">
                            {item.status_label}
                          </td>
                          <td className="whitespace-nowrap px-6 py-4 text-sm text-gray-700">
                            {item.deadline ? new Date(item.deadline + 'T00:00:00').toLocaleDateString() : '—'}
                          </td>
                          <td className="whitespace-nowrap px-6 py-4">
                            <div className="flex items-center gap-3">
                              <div className="h-2 w-24 overflow-hidden rounded-full bg-gray-200">
                                <div
                                  className="h-full rounded-full"
                                  style={{ backgroundColor: '#F47C20', width: `${pct}%` }}
                                />
                              </div>
                              <span className="text-xs text-gray-500">
                                {done}/{total} conditions done
                              </span>
                            </div>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        )}
      </main>
    </div>
  )
}
