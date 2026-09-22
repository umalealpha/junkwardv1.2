'use client'

import { useState } from 'react'
import type { Theme } from '@/lib/themes'
import type { TransformationAssignment, TransformationAssignPayload, TransformationPerson } from '@/lib/api'
import { Crown, AlertTriangle, UserPlus, X, Loader2, Info } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { PersonPicker } from './PersonPicker'
import { fmtDate } from './format'

interface AssignmentPanelProps {
  theme: Theme
  code: string
  targetDate: string | null
  assignments: TransformationAssignment[]
  people: TransformationPerson[]
  peopleLoading: boolean
  onAssign: (code: string, payload: TransformationAssignPayload, person: TransformationPerson) => Promise<{ ok: boolean; error?: string }>
  onUnassign: (code: string, email: string) => Promise<{ ok: boolean; error?: string }>
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean)
  if (parts.length === 0) return '?'
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase()
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
}

const TASK_STATUS_VARIANT: Record<string, 'default' | 'success' | 'warning' | 'danger' | 'info' | 'muted'> = {
  pending: 'default',
  in_progress: 'info',
  done: 'success',
  partial: 'warning',
  blocked: 'danger',
  cancelled: 'muted',
}
const TASK_STATUS_LABEL: Record<string, string> = {
  pending: 'Pending', in_progress: 'In progress', done: 'Done',
  partial: 'Partially complete', blocked: 'Blocked', cancelled: 'Cancelled',
}

export function AssignmentPanel({
  theme, code, targetDate, assignments, people, peopleLoading, onAssign, onUnassign,
}: AssignmentPanelProps) {
  const [formOpen, setFormOpen] = useState(false)
  const [selected, setSelected] = useState<TransformationPerson | null>(null)
  const [dueDate, setDueDate] = useState(targetDate || '')
  const [role, setRole] = useState('')
  const [isOwner, setIsOwner] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [confirmEmail, setConfirmEmail] = useState<string | null>(null)
  const [removing, setRemoving] = useState<string | null>(null)
  const [removeError, setRemoveError] = useState<string | null>(null)

  const currentEmails = assignments.map((a) => a.email)

  const submitAssign = async () => {
    const person = selected
    if (!person) {
      setFormError('Pick a person first.')
      return
    }
    setSubmitting(true)
    setFormError(null)
    const result = await onAssign(code, {
      email: person.email,
      due_date: dueDate || undefined,
      role: role.trim() || undefined,
      is_owner: isOwner,
    }, person)
    setSubmitting(false)
    if (result.ok) {
      setFormOpen(false)
      setSelected(null)
      setRole('')
      setIsOwner(false)
      setDueDate(targetDate || '')
    } else {
      // The 400 "not an active Omni login" (or similar) is the useful message
      // — show it exactly, never a generic "failed".
      setFormError(result.error || 'Could not assign this person.')
    }
  }

  const confirmRemove = async (email: string) => {
    setRemoving(email)
    setRemoveError(null)
    const result = await onUnassign(code, email)
    setRemoving(null)
    setConfirmEmail(null)
    if (!result.ok) setRemoveError(result.error || 'Could not remove this person.')
  }

  return (
    <div className="mt-2 pt-2 border-t" style={{ borderColor: theme.cardBdr }}>
      {assignments.length === 0 ? (
        <p className="text-xs" style={{ color: theme.t3 }}>Nobody assigned yet — no owner.</p>
      ) : (
        <ul className="space-y-1.5">
          {assignments.map((a) => (
            <li key={a.email} className="flex items-center justify-between gap-2 flex-wrap">
              <div className="flex items-center gap-2 min-w-0">
                <span
                  className="w-7 h-7 rounded-full flex items-center justify-center text-[10px] font-bold flex-shrink-0"
                  style={{
                    background: a.is_owner ? theme.orange : theme.g100,
                    color: a.is_owner ? '#FFFFFF' : theme.t2,
                    boxShadow: a.is_owner ? `0 0 0 2px ${theme.oL}` : 'none',
                  }}
                  aria-hidden="true"
                >
                  {initials(a.name)}
                </span>
                <span className="text-xs min-w-0">
                  <span className="font-medium" style={{ color: theme.text }}>{a.name}</span>
                  {a.is_owner && (
                    <span className="inline-flex items-center gap-0.5 ml-1.5" style={{ color: theme.orangeText }}>
                      <Crown className="w-3 h-3" strokeWidth={2} aria-hidden="true" />
                      <span className="font-medium">Owner</span>
                    </span>
                  )}
                  {a.role && <span className="ml-1.5" style={{ color: theme.t3 }}>· {a.role}</span>}
                  <span className="block sm:inline sm:ml-1.5" style={{ color: theme.t3 }}>
                    · Due {fmtDate(a.due_date)}
                  </span>
                </span>
              </div>

              <div className="flex items-center gap-1.5 flex-shrink-0">
                {a.overdue && (
                  <Badge variant="danger" size="sm">
                    <AlertTriangle className="w-3 h-3 mr-1" strokeWidth={1.7} />
                    Overdue
                  </Badge>
                )}
                {a.task_status && (
                  <Badge variant={TASK_STATUS_VARIANT[a.task_status] || 'default'} size="sm">
                    {TASK_STATUS_LABEL[a.task_status] || a.task_status}
                  </Badge>
                )}
                {confirmEmail === a.email ? (
                  <span className="flex items-center gap-1">
                    <span className="text-xs" style={{ color: theme.er }}>Cancel their task?</span>
                    <Button
                      variant="danger" size="sm"
                      onClick={() => confirmRemove(a.email)}
                      disabled={removing === a.email}
                    >
                      {removing === a.email ? <Loader2 className="w-3 h-3 animate-spin" /> : 'Confirm'}
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => setConfirmEmail(null)}>Cancel</Button>
                  </span>
                ) : (
                  <button
                    type="button"
                    onClick={() => setConfirmEmail(a.email)}
                    aria-label={`Remove ${a.name} from this step`}
                    className="w-6 h-6 rounded flex items-center justify-center"
                    style={{ color: theme.t3 }}
                  >
                    <X className="w-3.5 h-3.5" strokeWidth={1.7} />
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}

      {removeError && (
        <p className="text-xs mt-1.5 flex items-center gap-1" style={{ color: theme.er }}>
          <AlertTriangle className="w-3 h-3 flex-shrink-0" strokeWidth={1.7} />{removeError}
        </p>
      )}

      {!formOpen ? (
        <button
          type="button"
          onClick={() => setFormOpen(true)}
          className="mt-2 inline-flex items-center gap-1.5 text-xs font-medium"
          style={{ color: theme.orangeText }}
        >
          <UserPlus className="w-3.5 h-3.5" strokeWidth={1.7} />
          Assign someone
        </button>
      ) : (
        <div className="mt-2 rounded-md p-3 space-y-2" style={{ background: theme.g50, border: `1px solid ${theme.cardBdr}` }}>
          <PersonPicker
            theme={theme}
            people={people}
            loading={peopleLoading}
            value={selected}
            onSelect={setSelected}
            currentEmails={currentEmails}
            label="Search for a person to assign"
          />
          {selected && (
            <p className="text-xs" style={{ color: theme.t2 }}>
              Selected: <span className="font-medium">{selected.name}</span> ({selected.email})
            </p>
          )}
          <div className="flex flex-wrap items-end gap-2">
            <div>
              <label htmlFor={`due-${code}`} className="block text-xs mb-1" style={{ color: theme.t2 }}>Due date</label>
              <input
                id={`due-${code}`}
                type="date"
                value={dueDate}
                onChange={(e) => setDueDate(e.target.value)}
                className="h-9 rounded-md px-2 text-sm"
                style={{ border: `1px solid ${theme.cardBdr}`, background: theme.card, color: theme.text }}
              />
            </div>
            <div className="flex-1 min-w-[160px]">
              <label htmlFor={`role-${code}`} className="block text-xs mb-1" style={{ color: theme.t2 }}>What they are on the hook for</label>
              <input
                id={`role-${code}`}
                type="text"
                value={role}
                onChange={(e) => setRole(e.target.value)}
                placeholder="e.g. build the integration"
                maxLength={120}
                className="h-9 w-full rounded-md px-2 text-sm"
                style={{ border: `1px solid ${theme.cardBdr}`, background: theme.card, color: theme.text }}
              />
            </div>
            <label className="inline-flex items-center gap-1.5 text-xs h-9" style={{ color: theme.text }}>
              <input type="checkbox" checked={isOwner} onChange={(e) => setIsOwner(e.target.checked)} className="rounded" />
              Answerable owner
            </label>
          </div>

          <p className="text-xs flex items-start gap-1.5" style={{ color: theme.t3 }}>
            <Info className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" strokeWidth={1.7} />
            This creates a real Omni task with this deadline — they&apos;ll be chased by Omni&apos;s own reminders, so it can be monitored here.
          </p>

          {formError && (
            <p className="text-xs flex items-center gap-1" style={{ color: theme.er }}>
              <AlertTriangle className="w-3 h-3 flex-shrink-0" strokeWidth={1.7} />{formError}
            </p>
          )}

          <div className="flex items-center gap-2">
            <Button variant="primary" size="sm" onClick={submitAssign} disabled={submitting}>
              {submitting ? <Loader2 className="w-3.5 h-3.5 animate-spin mr-1" /> : null}
              Assign
            </Button>
            <Button
              variant="ghost" size="sm"
              onClick={() => { setFormOpen(false); setFormError(null); setSelected(null) }}
              disabled={submitting}
            >
              Cancel
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
