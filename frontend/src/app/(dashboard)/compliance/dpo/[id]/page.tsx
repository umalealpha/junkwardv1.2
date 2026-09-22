'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useParams } from 'next/navigation'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { apiFetch } from '@/lib/api'
import { Check, CheckCircle2, Circle, Loader2, Plus, Save } from 'lucide-react'

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
  status: 'draft' | 'in_review' | 'conditions_open' | 'approved' | 'closed'
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
  // Full DPIA form
  dpia_ref: string
  date_opened: string | null
  department: string
  process_owner: string
  system_used: string
  new_change_existing: string
  planned_go_live: string
  vendor_involved: boolean
  cross_border: boolean
  purpose: string
  data_subjects: string
  data_categories: Record<string, boolean>
  data_other: string
  special_category_detail: string
  vulnerable: boolean
  vulnerable_note: string
  volume: string
  triggers: Record<string, boolean>
  lawful_basis: string
  lawful_basis_label: string
  lawful_basis_note: string
  special_category_basis: string
  how_informed: string
  how_rights: string
  internal_sharing: string
  external_vendors: string
  cross_border_detail: string
  retention_period: string
  retention_reason: string
  disposal_method: string
  security_controls: Record<string, boolean>
  security_note: string
  decision: string
  decision_label: string
  dpo_review_note: string
  statement_of_alignment: string
  risks: DPIARisk[]
}

interface DPIARisk {
  id: string
  order: number
  title: string
  what_could_go_wrong: string
  controls: string
  likelihood: number
  impact: number
  score: number
  mitigation: string
  residual_score: number | null
  residual_band: string
}

interface DPIAFormState {
  project: string
  description: string
  data_types: string
  status: DPIA['status']
  deadline: string
  risk_rating: DPIA['risk_rating']
  residual_risk: DPIA['residual_risk']
  dpo_reviewer: string
  compliance_officer: string
}

const STATUS_OPTIONS = [
  { value: 'draft', label: 'Draft' },
  { value: 'in_review', label: 'In review' },
  { value: 'conditions_open', label: 'Conditions open' },
  { value: 'approved', label: 'Approved' },
  { value: 'closed', label: 'Closed' },
] as const

const RISK_OPTIONS = [
  { value: 'P1', label: 'P1 — High risk' },
  { value: 'P2', label: 'P2 — Medium risk' },
  { value: 'P3', label: 'P3 — Low risk' },
] as const

const RESIDUAL_OPTIONS = [
  { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
] as const

const inputClass =
  'w-full rounded-md border border-gray-200 bg-white px-3 py-2 text-sm text-gray-800 focus:border-[#F47C20] focus:outline-none'

function formatDate(value?: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

function riskColor(rating: string): string {
  if (rating === 'P1') return '#dc2626'
  if (rating === 'P2') return '#f59e0b'
  return '#16a34a'
}

function SignOffPill({
  title,
  name,
  signed,
  signedAt,
  signing,
  onNameChange,
  onToggle,
}: {
  title: string
  name: string
  signed: boolean
  signedAt: string | null
  signing?: boolean
  onNameChange: (value: string) => void
  onToggle: () => void
}) {
  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4">
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold uppercase tracking-wide text-[#1D3270]">{title}</span>
        {signed ? (
          <CheckCircle2 className="h-5 w-5 text-green-600" />
        ) : (
          <Circle className="h-5 w-5 text-gray-300" />
        )}
      </div>
      <input
        value={name}
        onChange={(event) => onNameChange(event.target.value)}
        placeholder="Name"
        className="mt-2 w-full border-b border-dashed border-gray-300 bg-transparent px-0 py-1 text-sm font-medium text-gray-800 focus:border-[#F47C20] focus:outline-none"
      />
      <div className="mt-3 flex items-center justify-between gap-2">
        <span className="text-xs text-gray-500">{signed ? `Signed ${formatDate(signedAt)}` : 'Not signed'}</span>
        <button
          type="button"
          onClick={onToggle}
          disabled={signing}
          className="rounded-md bg-[#1D3270] px-3 py-1.5 text-xs font-semibold text-white hover:bg-[#16265a] disabled:opacity-50"
        >
          {signing ? 'Saving…' : signed ? 'Unsign' : 'Sign'}
        </button>
      </div>
    </div>
  )
}

function ConditionRow({ condition, onToggle }: { condition: DPIACondition; onToggle: () => void }) {
  return (
    <div className="flex items-start gap-3 rounded-lg border border-gray-200 bg-white p-3">
      <button
        type="button"
        onClick={onToggle}
        aria-label={condition.done ? 'Mark as not done' : 'Mark as done'}
        className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded border ${
          condition.done ? 'border-green-600 bg-green-600 text-white' : 'border-gray-300 bg-white text-transparent'
        }`}
      >
        {condition.done && <Check className="h-4 w-4" />}
      </button>
      <div className="min-w-0 flex-1">
        <p className={`text-sm ${condition.done ? 'text-gray-400 line-through' : 'text-gray-800'}`}>
          {condition.text}
        </p>
        <p className="mt-1 text-xs text-gray-500">
          <span>Owner: {condition.owner || '—'}</span>
          {condition.due_date ? <span className="ml-2">Due {formatDate(condition.due_date)}</span> : null}
        </p>
      </div>
    </div>
  )
}

export default function DPIADetailPage() {
  const params = useParams()
  const id = typeof params.id === 'string' ? params.id : ''

  const [dpia, setDpia] = useState<DPIA | null>(null)
  const [form, setForm] = useState<DPIAFormState | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [addingCondition, setAddingCondition] = useState(false)
  const [signingField, setSigningField] = useState<'dpo' | 'compliance' | 'cfo' | null>(null)
  const [activeTab, setActiveTab] = useState<'pilot' | 'rollout'>('pilot')
  const [newConditionText, setNewConditionText] = useState('')
  const [error, setError] = useState('')
  const [saveError, setSaveError] = useState('')
  // Full DPIA form (separate state so the existing summary form is untouched)
  const [full, setFull] = useState<Record<string, any>>({})
  const [savingFull, setSavingFull] = useState(false)

  const load = useCallback(async () => {
    if (!id) {
      setError('Invalid DPIA ID')
      setLoading(false)
      return
    }
    setLoading(true)
    setError('')
    try {
      const data = await apiFetch<DPIA>(`/dpo/dpia/${id}/`)
      setDpia(data)
      setFull(data as unknown as Record<string, any>)
      setForm({
        project: data.project,
        description: data.description,
        data_types: data.data_types,
        status: data.status,
        deadline: data.deadline ?? '',
        risk_rating: data.risk_rating,
        residual_risk: data.residual_risk,
        dpo_reviewer: data.dpo_reviewer,
        compliance_officer: data.compliance_officer,
      })
    } catch {
      setError('Failed to load DPIA.')
    } finally {
      setLoading(false)
    }
  }, [id])

  useEffect(() => {
    void load()
  }, [load])

  const pilotConditions = useMemo(
    () => (dpia?.conditions ?? []).filter((condition) => condition.phase === 'pilot'),
    [dpia],
  )
  const rolloutConditions = useMemo(
    () => (dpia?.conditions ?? []).filter((condition) => condition.phase === 'rollout'),
    [dpia],
  )
  const pilotDone = useMemo(() => pilotConditions.filter((condition) => condition.done).length, [pilotConditions])
  const rolloutDone = useMemo(() => rolloutConditions.filter((condition) => condition.done).length, [rolloutConditions])

  const activeConditions = activeTab === 'pilot' ? pilotConditions : rolloutConditions
  const activeDone = activeTab === 'pilot' ? pilotDone : rolloutDone

  const saveForm = async () => {
    if (!dpia || !form) return
    setSaving(true)
    setSaveError('')
    setError('')
    try {
      const updated = await apiFetch<DPIA>(`/dpo/dpia/${dpia.id}/`, {
        method: 'PATCH',
        body: JSON.stringify({
          project: form.project,
          description: form.description,
          data_types: form.data_types,
          status: form.status,
          deadline: form.deadline ? form.deadline : null,
          risk_rating: form.risk_rating,
          residual_risk: form.residual_risk,
          dpo_reviewer: form.dpo_reviewer,
          compliance_officer: form.compliance_officer,
        }),
      })
      setDpia(updated)
      setForm({
        project: updated.project,
        description: updated.description,
        data_types: updated.data_types,
        status: updated.status,
        deadline: updated.deadline ?? '',
        risk_rating: updated.risk_rating,
        residual_risk: updated.residual_risk,
        dpo_reviewer: updated.dpo_reviewer,
        compliance_officer: updated.compliance_officer,
      })
    } catch {
      setSaveError('Failed to save DPIA.')
    } finally {
      setSaving(false)
    }
  }

  const saveFull = async () => {
    if (!dpia) return
    setSavingFull(true)
    setError('')
    try {
      const f = full
      const payload = {
        dpia_ref: f.dpia_ref, department: f.department, process_owner: f.process_owner,
        system_used: f.system_used, new_change_existing: f.new_change_existing,
        planned_go_live: f.planned_go_live, vendor_involved: !!f.vendor_involved, cross_border: !!f.cross_border,
        purpose: f.purpose, data_subjects: f.data_subjects, data_categories: f.data_categories || {},
        data_other: f.data_other, special_category_detail: f.special_category_detail,
        vulnerable: !!f.vulnerable, vulnerable_note: f.vulnerable_note, volume: f.volume,
        triggers: f.triggers || {}, lawful_basis: f.lawful_basis, lawful_basis_note: f.lawful_basis_note,
        special_category_basis: f.special_category_basis, how_informed: f.how_informed, how_rights: f.how_rights,
        internal_sharing: f.internal_sharing, external_vendors: f.external_vendors, cross_border_detail: f.cross_border_detail,
        retention_period: f.retention_period, retention_reason: f.retention_reason, disposal_method: f.disposal_method,
        security_controls: f.security_controls || {}, security_note: f.security_note,
        decision: f.decision, dpo_review_note: f.dpo_review_note, statement_of_alignment: f.statement_of_alignment,
        date_opened: f.date_opened || null,
      }
      const updated = await apiFetch<DPIA>(`/dpo/dpia/${dpia.id}/`, { method: 'PATCH', body: JSON.stringify(payload) })
      setDpia(updated)
      setFull(updated as unknown as Record<string, any>)
    } catch {
      setError('Failed to save the DPIA form.')
    } finally {
      setSavingFull(false)
    }
  }

  const toggleSign = async (person: 'dpo' | 'compliance' | 'cfo') => {
    if (!dpia) return
    const signed =
      person === 'dpo' ? dpia.dpo_signed : person === 'compliance' ? dpia.compliance_signed : dpia.cfo_signed
    // Persist the typed name alongside the flag, so signing never leaves a
    // signed-but-nameless record if the user didn't press "Save changes" first.
    const patch =
      person === 'dpo'
        ? { dpo_signed: !signed, dpo_reviewer: form?.dpo_reviewer ?? dpia.dpo_reviewer }
        : person === 'compliance'
          ? { compliance_signed: !signed, compliance_officer: form?.compliance_officer ?? dpia.compliance_officer }
          : { cfo_signed: !signed }
    setSigningField(person)
    setError('')
    try {
      const updated = await apiFetch<DPIA>(`/dpo/dpia/${dpia.id}/`, {
        method: 'PATCH',
        body: JSON.stringify(patch),
      })
      setDpia(updated)
    } catch {
      setError('Failed to update sign-off.')
    } finally {
      setSigningField(null)
    }
  }

  const updateCondition = async (conditionId: string, patch: Partial<DPIACondition>) => {
    if (!dpia) return
    setError('')
    try {
      const updated = await apiFetch<DPIACondition>(`/dpo/dpia-conditions/${conditionId}/`, {
        method: 'PATCH',
        body: JSON.stringify(patch),
      })
      setDpia((prev) =>
        prev
          ? {
              ...prev,
              conditions: prev.conditions.map((condition) => (condition.id === updated.id ? updated : condition)),
            }
          : prev,
      )
    } catch {
      setError('Failed to update condition.')
    }
  }

  const addCondition = async () => {
    if (!dpia || !newConditionText.trim()) return
    setAddingCondition(true)
    setError('')
    try {
      const created = await apiFetch<DPIACondition>('/dpo/dpia-conditions/', {
        method: 'POST',
        body: JSON.stringify({
          dpia: dpia.id,
          phase: activeTab,
          text: newConditionText.trim(),
          order: activeConditions.length,
        }),
      })
      setDpia((prev) => (prev ? { ...prev, conditions: [...prev.conditions, created] } : prev))
      setNewConditionText('')
    } catch {
      setError('Failed to add condition.')
    } finally {
      setAddingCondition(false)
    }
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-50">
        <TopBar title="Data Protection — DPIA Register" />
        <main className="mx-auto max-w-4xl px-4 py-8">
          <Card>
            <CardContent className="flex items-center justify-center gap-2 py-16 text-gray-500">
              <Loader2 className="h-5 w-5 animate-spin text-[#F47C20]" />
              Loading DPIA…
            </CardContent>
          </Card>
        </main>
      </div>
    )
  }

  if (!dpia || !form) {
    return (
      <div className="min-h-screen bg-gray-50">
        <TopBar title="Data Protection — DPIA Register" />
        <main className="mx-auto max-w-4xl px-4 py-8">
          <Card>
            <CardContent className="py-16 text-center text-red-600">{error || 'DPIA not found.'}</CardContent>
          </Card>
        </main>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <TopBar title="Data Protection — DPIA Register" />
      <main className="mx-auto max-w-4xl space-y-6 px-4 py-8">
        {error ? (
          <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>
        ) : null}

        <Card>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap items-center gap-3">
              <input
                type="text"
                value={form.project}
                onChange={(event) => setForm({ ...form, project: event.target.value })}
                className="w-full max-w-xl rounded-md border border-gray-200 bg-white px-3 py-2 text-xl font-semibold text-[#1D3270] focus:border-[#F47C20] focus:outline-none"
              />
              <span
                className="rounded-full px-4 py-1.5 text-base font-bold text-white"
                style={{ backgroundColor: riskColor(form.risk_rating) }}
              >
                {form.risk_rating}
              </span>
              <span className="rounded-full border border-gray-300 bg-white px-3 py-1 text-sm text-gray-600">
                Residual: {form.residual_risk}
              </span>
            </div>
            {dpia.special_category ? (
              <p className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                Health/special-category data — P1 mandatory
              </p>
            ) : null}
          </CardContent>
        </Card>

        <Card>
          <CardContent>
            <h2 className="mb-4 text-lg font-semibold" style={{ color: '#1D3270' }}>
              Sign-off
            </h2>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              <SignOffPill
                title="DPO"
                name={form.dpo_reviewer}
                signed={dpia.dpo_signed}
                signedAt={dpia.dpo_signed_at}
                signing={signingField === 'dpo'}
                onNameChange={(value) => setForm({ ...form, dpo_reviewer: value })}
                onToggle={() => toggleSign('dpo')}
              />
              <SignOffPill
                title="Compliance Officer"
                name={form.compliance_officer}
                signed={dpia.compliance_signed}
                signedAt={dpia.compliance_signed_at}
                signing={signingField === 'compliance'}
                onNameChange={(value) => setForm({ ...form, compliance_officer: value })}
                onToggle={() => toggleSign('compliance')}
              />
              <SignOffPill
                title="CFO"
                name={dpia.cfo_signed ? 'Prathap Ganesharajah' : ''}
                signed={dpia.cfo_signed}
                signedAt={dpia.cfo_signed_at}
                signing={signingField === 'cfo'}
                onNameChange={() => undefined}
                onToggle={() => toggleSign('cfo')}
              />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardContent>
            <h2 className="mb-4 text-lg font-semibold" style={{ color: '#1D3270' }}>
              Conditions
            </h2>
            <div className="mb-4 flex gap-2 border-b border-gray-200">
              <button
                type="button"
                onClick={() => setActiveTab('pilot')}
                className={`border-b-2 pb-2 text-sm font-medium transition ${
                  activeTab === 'pilot'
                    ? 'border-[#1D3270] text-[#1D3270]'
                    : 'border-transparent text-gray-500 hover:text-gray-700'
                }`}
              >
                Pilot (current scope) · {pilotDone}/{pilotConditions.length}
              </button>
              <button
                type="button"
                onClick={() => setActiveTab('rollout')}
                className={`border-b-2 pb-2 text-sm font-medium transition ${
                  activeTab === 'rollout'
                    ? 'border-[#1D3270] text-[#1D3270]'
                    : 'border-transparent text-gray-500 hover:text-gray-700'
                }`}
              >
                Before customer rollout · {rolloutDone}/{rolloutConditions.length}
              </button>
            </div>

            <p className="mb-3 text-sm text-gray-500">
              {activeDone} of {activeConditions.length} conditions done
            </p>

            <div className="space-y-3">
              {activeConditions.length === 0 ? (
                <p className="rounded-lg border border-dashed border-gray-200 px-4 py-6 text-sm text-gray-500">
                  No conditions in this phase yet.
                </p>
              ) : (
                activeConditions.map((condition) => (
                  <ConditionRow
                    key={condition.id}
                    condition={condition}
                    onToggle={() => updateCondition(condition.id, { done: !condition.done })}
                  />
                ))
              )}

              <div className="flex items-center gap-2 rounded-lg border border-gray-200 bg-white p-3">
                <Plus className="h-4 w-4 shrink-0 text-gray-400" />
                <input
                  type="text"
                  value={newConditionText}
                  onChange={(event) => setNewConditionText(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') {
                      event.preventDefault()
                      void addCondition()
                    }
                  }}
                  placeholder="Add a condition…"
                  className="w-full border-0 bg-transparent px-0 py-1 text-sm text-gray-800 focus:outline-none"
                />
                <button
                  type="button"
                  onClick={() => void addCondition()}
                  disabled={addingCondition || !newConditionText.trim()}
                  className="shrink-0 rounded-md bg-[#F47C20] px-3 py-1.5 text-sm font-semibold text-white hover:bg-[#d96a18] disabled:opacity-50"
                >
                  {addingCondition ? 'Adding…' : 'Add'}
                </button>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="space-y-4">
            <h2 className="text-lg font-semibold" style={{ color: '#1D3270' }}>
              DPIA details
            </h2>

            <div>
              <label className="mb-1 block text-sm font-medium text-gray-700">Description</label>
              <textarea
                rows={4}
                value={form.description}
                onChange={(event) => setForm({ ...form, description: event.target.value })}
                className={inputClass}
              />
            </div>

            <div>
              <label className="mb-1 block text-sm font-medium text-gray-700">Data types</label>
              <textarea
                rows={3}
                value={form.data_types}
                onChange={(event) => setForm({ ...form, data_types: event.target.value })}
                className={inputClass}
              />
            </div>

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div>
                <label className="mb-1 block text-sm font-medium text-gray-700">Status</label>
                <select
                  value={form.status}
                  onChange={(event) => setForm({ ...form, status: event.target.value as DPIA['status'] })}
                  className={inputClass}
                >
                  {STATUS_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label className="mb-1 block text-sm font-medium text-gray-700">Deadline</label>
                <input
                  type="date"
                  value={form.deadline}
                  onChange={(event) => setForm({ ...form, deadline: event.target.value })}
                  className={inputClass}
                />
              </div>

              <div>
                <label className="mb-1 block text-sm font-medium text-gray-700">Risk rating</label>
                <select
                  value={form.risk_rating}
                  onChange={(event) => setForm({ ...form, risk_rating: event.target.value as DPIA['risk_rating'] })}
                  className={inputClass}
                >
                  {RISK_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label className="mb-1 block text-sm font-medium text-gray-700">Residual risk</label>
                <select
                  value={form.residual_risk}
                  onChange={(event) =>
                    setForm({ ...form, residual_risk: event.target.value as DPIA['residual_risk'] })
                  }
                  className={inputClass}
                >
                  {RESIDUAL_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            {saveError ? <p className="text-sm text-red-600">{saveError}</p> : null}

            <div className="flex justify-end">
              <button
                type="button"
                onClick={() => void saveForm()}
                disabled={saving || signingField !== null || addingCondition}
                className="inline-flex items-center rounded-md bg-[#F47C20] px-5 py-2 text-sm font-semibold text-white shadow hover:bg-[#d96a18] disabled:opacity-50"
              >
                <Save className="mr-2 h-4 w-4" />
                {saving ? 'Saving…' : 'Save changes'}
              </button>
            </div>
          </CardContent>
        </Card>

        {/* ── Full DPIA form (DPIA-2026-004 layout) ── */}
        <Card>
          <CardContent className="space-y-6">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold" style={{ color: '#1D3270' }}>
                Full DPIA form{full.dpia_ref ? ` — ${full.dpia_ref}` : ''}
              </h2>
              <button
                type="button"
                onClick={() => void saveFull()}
                disabled={savingFull}
                className="inline-flex items-center rounded-md bg-[#F47C20] px-4 py-2 text-sm font-semibold text-white hover:bg-[#d96a18] disabled:opacity-50"
              >
                <Save className="mr-2 h-4 w-4" />
                {savingFull ? 'Saving…' : 'Save form'}
              </button>
            </div>

            {([
              ['Department', 'department', 'input'],
              ['Process owner', 'process_owner', 'input'],
              ['DPIA reference', 'dpia_ref', 'input'],
              ['Planned go-live', 'planned_go_live', 'input'],
              ['Volume', 'volume', 'input'],
              ['Vulnerable-persons note', 'vulnerable_note', 'area'],
              ['Systems used', 'system_used', 'area'],
              ['1. Purpose of processing', 'purpose', 'area'],
              ['2. Data subjects', 'data_subjects', 'area'],
              ['Other data', 'data_other', 'area'],
              ['Special-category detail', 'special_category_detail', 'area'],
              ['4. Lawful basis — note', 'lawful_basis_note', 'area'],
              ['Special-category basis', 'special_category_basis', 'area'],
              ['How individuals are informed', 'how_informed', 'area'],
              ['How rights are exercised', 'how_rights', 'area'],
              ['5. Internal sharing', 'internal_sharing', 'area'],
              ['External vendors / recipients', 'external_vendors', 'area'],
              ['Cross-border transfers', 'cross_border_detail', 'area'],
              ['6. Retention period', 'retention_period', 'area'],
              ['Reason for retention', 'retention_reason', 'area'],
              ['Disposal method', 'disposal_method', 'area'],
              ['Security note', 'security_note', 'area'],
              ['9. DPO review note', 'dpo_review_note', 'input'],
              ['Statement of alignment', 'statement_of_alignment', 'area'],
            ] as [string, string, string][]).map(([label, key, kind]) => (
              <div key={key}>
                <label className="mb-1 block text-sm font-medium text-gray-700">{label}</label>
                {kind === 'area' ? (
                  <textarea
                    rows={2}
                    value={full[key] ?? ''}
                    onChange={(e) => setFull({ ...full, [key]: e.target.value })}
                    className={inputClass}
                  />
                ) : (
                  <input
                    value={full[key] ?? ''}
                    onChange={(e) => setFull({ ...full, [key]: e.target.value })}
                    className={inputClass}
                  />
                )}
              </div>
            ))}

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              <div>
                <label className="mb-1 block text-sm font-medium text-gray-700">Date opened</label>
                <input
                  type="date"
                  value={(full.date_opened as string) ?? ''}
                  onChange={(e) => setFull({ ...full, date_opened: e.target.value })}
                  className={inputClass}
                />
              </div>
              <div>
                <label className="mb-1 block text-sm font-medium text-gray-700">New / Change / Existing</label>
                <select
                  value={full.new_change_existing ?? ''}
                  onChange={(e) => setFull({ ...full, new_change_existing: e.target.value })}
                  className={inputClass}
                >
                  <option value="">—</option>
                  <option value="new">New</option>
                  <option value="change">Change</option>
                  <option value="existing">Existing</option>
                </select>
              </div>
              <div>
                <label className="mb-1 block text-sm font-medium text-gray-700">Lawful basis</label>
                <select
                  value={full.lawful_basis ?? ''}
                  onChange={(e) => setFull({ ...full, lawful_basis: e.target.value })}
                  className={inputClass}
                >
                  <option value="">—</option>
                  {[
                    ['contract', 'Contract'],
                    ['legal_obligation', 'Legal obligation'],
                    ['legitimate_interests', 'Legitimate interests'],
                    ['consent', 'Consent'],
                    ['vital_interests', 'Vital interests'],
                    ['public_task', 'Public task'],
                  ].map(([v, l]) => (
                    <option key={v} value={v}>{l}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="mb-1 block text-sm font-medium text-gray-700">Decision</label>
                <select
                  value={full.decision ?? ''}
                  onChange={(e) => setFull({ ...full, decision: e.target.value })}
                  className={inputClass}
                >
                  <option value="">—</option>
                  <option value="proceed">Proceed</option>
                  <option value="proceed_conditions">Proceed with conditions</option>
                  <option value="do_not_proceed">Do not proceed</option>
                </select>
              </div>
            </div>

            <div className="flex flex-wrap gap-4 text-sm text-gray-700">
              {([
                ['vendor_involved', 'Vendor involved'],
                ['cross_border', 'Cross-border transfer'],
                ['vulnerable', 'Vulnerable data subjects'],
              ] as [string, string][]).map(([k, l]) => (
                <label key={k} className="inline-flex items-center gap-2">
                  <input type="checkbox" checked={!!full[k]} onChange={(e) => setFull({ ...full, [k]: e.target.checked })} />
                  {l}
                </label>
              ))}
            </div>

            {([
              ['Data categories', 'data_categories', ['identity', 'contact', 'financial', 'policy_claims', 'location_online', 'criminal']],
              ['DPIA triggers', 'triggers', ['special_category', 'large_scale', 'profiling', 'monitoring', 'new_tech', 'new_vendor', 'cross_border', 'vulnerable']],
              ['7. Security controls', 'security_controls', ['access_control', 'mfa', 'encryption_transit', 'encryption_rest', 'logging', 'backups', 'staff_training', 'vendor_assurance', 'data_minimisation', 'pseudonymisation']],
            ] as [string, string, string[]][]).map(([label, groupKey, keys]) => (
              <div key={groupKey}>
                <label className="mb-2 block text-sm font-medium text-gray-700">{label}</label>
                <div className="flex flex-wrap gap-x-4 gap-y-2 text-sm capitalize text-gray-700">
                  {keys.map((k) => (
                    <label key={k} className="inline-flex items-center gap-2">
                      <input
                        type="checkbox"
                        checked={!!(full[groupKey] || {})[k]}
                        onChange={(e) => setFull({ ...full, [groupKey]: { ...(full[groupKey] || {}), [k]: e.target.checked } })}
                      />
                      {k.replace(/_/g, ' ')}
                    </label>
                  ))}
                </div>
              </div>
            ))}

            <div>
              <label className="mb-2 block text-sm font-medium text-gray-700">8. Risks &amp; mitigations</label>
              {(dpia.risks ?? []).length === 0 ? (
                <p className="text-sm text-gray-500">No risks recorded.</p>
              ) : (
                <div className="space-y-3">
                  {(dpia.risks ?? []).map((r) => (
                    <div key={r.id} className="rounded-lg border border-gray-200 p-3 text-sm">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="font-medium text-[#1D3270]">{r.title}</span>
                        <span className="text-xs text-gray-500">
                          L{r.likelihood} × I{r.impact} = {r.score}
                          {r.residual_score != null ? ` → residual ${r.residual_score}` : ''}
                          {r.residual_band ? ` (${r.residual_band})` : ''}
                        </span>
                      </div>
                      <p className="mt-1 text-gray-700">{r.what_could_go_wrong}</p>
                      <p className="mt-1 text-gray-500"><strong>Controls:</strong> {r.controls}</p>
                      <p className="mt-1 text-gray-500"><strong>Mitigation:</strong> {r.mitigation}</p>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </CardContent>
        </Card>
      </main>
    </div>
  )
}
