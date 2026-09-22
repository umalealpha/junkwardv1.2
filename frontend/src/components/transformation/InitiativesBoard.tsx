'use client'

import { useEffect, useMemo, useState } from 'react'
import type { Theme } from '@/lib/themes'
import type {
  TransformationAssignPayload,
  TransformationInitiative,
  TransformationPerson,
  TransformationProgressUpdate,
} from '@/lib/api'
import { getTransformationPeople } from '@/lib/api'
import { HeartHandshake, Building2, Loader2 } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { fmtDate, fmtMoney } from './format'
import { AssignmentPanel } from './AssignmentPanel'
import { AssignmentsSummary } from './AssignmentsSummary'

interface Props {
  theme: Theme
  initiatives: TransformationInitiative[]
  onProgressChange: (code: string, payload: TransformationProgressUpdate) => Promise<boolean>
  onAssign: (code: string, payload: TransformationAssignPayload, person: TransformationPerson) => Promise<{ ok: boolean; error?: string }>
  onUnassign: (code: string, email: string) => Promise<{ ok: boolean; error?: string }>
}

const STATUS_OPTIONS: { value: TransformationInitiative['status']; label: string }[] = [
  { value: 'not_started', label: 'Not started' },
  { value: 'in_progress', label: 'In progress' },
  { value: 'blocked', label: 'Blocked' },
  { value: 'done', label: 'Done' },
]

// Derived from item.status rather than trusted from item.status_label — the
// progress POST response doesn't carry a label, so an optimistic status
// change would otherwise show a badge that disagrees with the select below it
// until the next full reload.
function statusLabel(status: string): string {
  return STATUS_OPTIONS.find((o) => o.value === status)?.label || status
}

function statusVariant(status: string): 'default' | 'success' | 'warning' | 'danger' | 'info' {
  switch (status) {
    case 'done': return 'success'
    case 'in_progress': return 'info'
    case 'blocked': return 'danger'
    default: return 'default'
  }
}

export function InitiativesBoard({ theme, initiatives, onProgressChange, onAssign, onUnassign }: Props) {
  const [csOnly, setCsOnly] = useState(false)
  const [people, setPeople] = useState<TransformationPerson[]>([])
  const [peopleLoading, setPeopleLoading] = useState(true)

  // Fetched once for the whole section (a couple hundred rows) rather than
  // per-row, so opening the picker on any step is instant.
  useEffect(() => {
    let cancelled = false
    getTransformationPeople()
      .then((res) => { if (!cancelled) setPeople(res.people) })
      .catch(() => { /* the picker just shows "no match" — the section itself still works */ })
      .finally(() => { if (!cancelled) setPeopleLoading(false) })
    return () => { cancelled = true }
  }, [])

  const visible = csOnly ? initiatives.filter((i) => i.improves_customer_service) : initiatives

  const byMonth = useMemo(() => {
    const groups = new Map<number, TransformationInitiative[]>()
    for (const item of visible) {
      const list = groups.get(item.month) || []
      list.push(item)
      groups.set(item.month, list)
    }
    return [...groups.entries()].sort((a, b) => a[0] - b[0])
  }, [visible])

  return (
    <div className="space-y-6">
      <AssignmentsSummary theme={theme} initiatives={initiatives} />

      <label className="inline-flex items-center gap-2 text-sm" style={{ color: theme.text }}>
        <input
          type="checkbox"
          checked={csOnly}
          onChange={(e) => setCsOnly(e.target.checked)}
          className="rounded"
        />
        Show only steps that improve customer service
      </label>

      {byMonth.length === 0 && (
        <p className="text-sm" style={{ color: theme.t3 }}>No steps match this filter.</p>
      )}

      {byMonth.map(([month, items]) => (
        <div key={month}>
          <h3 className="text-sm font-semibold mb-2" style={{ color: theme.navy }}>Month {month}</h3>
          <ul className="space-y-2">
            {items.map((item) => (
              <InitiativeRow
                key={item.code}
                theme={theme}
                item={item}
                onProgressChange={onProgressChange}
                people={people}
                peopleLoading={peopleLoading}
                onAssign={onAssign}
                onUnassign={onUnassign}
              />
            ))}
          </ul>
        </div>
      ))}
    </div>
  )
}

function InitiativeRow({
  theme, item, onProgressChange, people, peopleLoading, onAssign, onUnassign,
}: {
  theme: Theme
  item: TransformationInitiative
  onProgressChange: Props['onProgressChange']
  people: TransformationPerson[]
  peopleLoading: boolean
  onAssign: Props['onAssign']
  onUnassign: Props['onUnassign']
}) {
  const [saving, setSaving] = useState(false)
  const [percentDraft, setPercentDraft] = useState(item.percent)

  // Keep the draft in step with the server-confirmed value (a successful
  // save, or a fresh board load) without clobbering what the user is mid-typing.
  useEffect(() => { setPercentDraft(item.percent) }, [item.percent])

  const commitPercent = async () => {
    if (percentDraft === item.percent) return
    setSaving(true)
    const ok = await onProgressChange(item.code, { percent: percentDraft })
    if (!ok) setPercentDraft(item.percent) // rollback the draft to the last known-good value
    setSaving(false)
  }

  const changeStatus = async (status: TransformationInitiative['status']) => {
    setSaving(true)
    await onProgressChange(item.code, { status })
    setSaving(false)
  }

  return (
    <li
      className="rounded-lg p-3"
      style={{ background: theme.g50, border: `1px solid ${theme.cardBdr}` }}
    >
      <div className="flex flex-col sm:flex-row sm:items-center gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-medium" style={{ color: theme.text }}>{item.title}</span>
            {item.improves_customer_service && (
              <Badge variant="orange" size="sm">
                <HeartHandshake className="w-3 h-3 mr-1" strokeWidth={1.7} />
                Customer service
              </Badge>
            )}
            {item.is_vendor && (
              <Badge variant="info" size="sm">
                <Building2 className="w-3 h-3 mr-1" strokeWidth={1.7} />
                Vendor
              </Badge>
            )}
          </div>
          {item.plain_summary && (
            <p className="text-xs mt-1" style={{ color: theme.t2 }}>{item.plain_summary}</p>
          )}
          <div className="text-xs mt-1 flex items-center gap-3 flex-wrap" style={{ color: theme.t3 }}>
            <span>Owner: {item.manager_name || '—'}</span>
            <span>Target: {fmtDate(item.target_date)}</span>
            {item.annual_saving_bwp > 0 && <span>{fmtMoney(item.annual_saving_bwp)}/yr</span>}
          </div>
        </div>

        <div className="flex items-center gap-2 flex-shrink-0">
          <Badge variant={statusVariant(item.status)} size="sm">{statusLabel(item.status)}</Badge>

          <label className="sr-only" htmlFor={`pct-${item.code}`}>Progress percent for {item.title}</label>
          <input
            id={`pct-${item.code}`}
            type="number"
            min={0}
            max={100}
            value={percentDraft}
            onChange={(e) => setPercentDraft(Math.max(0, Math.min(100, Number(e.target.value) || 0)))}
            onBlur={commitPercent}
            className="w-16 h-8 rounded-md px-2 text-sm text-right"
            style={{ border: `1px solid ${theme.cardBdr}`, background: theme.card, color: theme.text }}
            disabled={saving}
          />
          <span className="text-xs" style={{ color: theme.t3 }}>%</span>

          <label className="sr-only" htmlFor={`status-${item.code}`}>Status for {item.title}</label>
          <select
            id={`status-${item.code}`}
            value={item.status}
            onChange={(e) => changeStatus(e.target.value as TransformationInitiative['status'])}
            className="h-8 rounded-md px-2 text-sm"
            style={{ border: `1px solid ${theme.cardBdr}`, background: theme.card, color: theme.text }}
            disabled={saving}
          >
            {STATUS_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>

          {saving && <Loader2 className="w-4 h-4 animate-spin" style={{ color: theme.t3 }} />}
        </div>
      </div>

      <AssignmentPanel
        theme={theme}
        code={item.code}
        targetDate={item.target_date}
        assignments={item.assignments || []}
        people={people}
        peopleLoading={peopleLoading}
        onAssign={onAssign}
        onUnassign={onUnassign}
      />
    </li>
  )
}
