'use client'

/**
 * /internal-audit/findings — Findings Register (spec Module 5).
 *
 * Structure is the control. The create form will not submit unless all five
 * elements are present (criteria, condition + evidence, root cause, effect,
 * rating justification); root cause is a forced dropdown with no "human error"
 * / "oversight"; and the rating is shown auto-computed from likelihood x impact
 * — never typed. Creating/editing is gated to the audit function
 * (me.can_edit_internal_audit); everyone else sees a read-only register.
 */

import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { apiFetch, getMe, type UserProfile } from '@/lib/api'
import { Plus, ShieldAlert, X } from 'lucide-react'

const NAVY = '#1D3270'
const ORANGE = '#F47C20'
const RATING_COLOR: Record<string, string> = {
  low: '#0F8B6C', medium: '#F4A623', high: '#E8590C', critical: '#D72638',
}

const ROOT_CAUSES = [
  ['control_design', 'Control design failure'],
  ['missing_control', 'Missing control'],
  ['training_gap', 'Training gap'],
  ['system_limitation', 'System limitation'],
  ['tone_at_the_top', 'Tone-at-the-top'],
  ['resource_constraint', 'Resource constraint'],
  ['other', 'Other (justify)'],
] as const

const EFFECTS = [
  ['financial', 'Financial'], ['regulatory', 'Regulatory'], ['reputational', 'Reputational'],
  ['operational', 'Operational'], ['fraud', 'Fraud exposure'],
] as const

interface Finding {
  id: string
  engagement: string
  engagement_ref: string
  reference: string
  title: string
  root_cause_label: string
  effect_category_label: string
  rating: string
  status_label: string
  fraud_flag: boolean
}
interface Engagement { id: string; reference: string; title: string }

function ratingBand(l: number, i: number): string {
  const s = l * i
  if (s <= 4) return 'low'; if (s <= 9) return 'medium'; if (s <= 15) return 'high'; return 'critical'
}

export default function FindingsRegister() {
  const [me, setMe] = useState<UserProfile | null>(null)
  const [findings, setFindings] = useState<Finding[]>([])
  const [engagements, setEngagements] = useState<Engagement[]>([])
  const [showForm, setShowForm] = useState(false)
  const [showEng, setShowEng] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const canEdit = !!me?.can_edit_internal_audit

  const load = useCallback(() => {
    // List endpoints are paginated ({count,results:[...]}) — normalise to an array.
    apiFetch<any>('/internal-audit/findings/')
      .then((res) => setFindings(Array.isArray(res) ? res : (res?.results ?? [])))
      .catch((e) => setErr(String(e)))
    apiFetch<any>('/internal-audit/engagements/')
      .then((res) => setEngagements(Array.isArray(res) ? res : (res?.results ?? [])))
      .catch(() => {})
  }, [])

  useEffect(() => { getMe().then(setMe).catch(() => {}); load() }, [load])

  return (
    <div>
      <TopBar title="Internal Audit — Findings Register" />
      <div className="p-6 space-y-5">
        <div className="flex items-center justify-between">
          <p className="text-sm text-gray-500">
            Every finding carries all five elements, a forced root cause, and an auto-computed rating.
          </p>
          {canEdit && (
            <div className="flex gap-2">
              <button onClick={() => setShowEng(true)}
                className="inline-flex items-center gap-2 rounded-lg border px-4 py-2 text-sm font-medium"
                style={{ color: NAVY, borderColor: NAVY }}>
                <Plus className="h-4 w-4" /> New engagement
              </button>
              <button onClick={() => setShowForm(true)}
                className="inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium text-white"
                style={{ background: NAVY }}>
                <Plus className="h-4 w-4" /> New finding
              </button>
            </div>
          )}
        </div>

        {err && <Card><CardContent className="p-4 text-sm text-red-600">{err}</CardContent></Card>}

        <Card>
          <CardContent className="p-0">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-xs uppercase text-gray-400">
                  <th className="p-3">Ref</th><th className="p-3">Title</th><th className="p-3">Engagement</th>
                  <th className="p-3">Root cause</th><th className="p-3">Effect</th>
                  <th className="p-3">Rating</th><th className="p-3">Status</th>
                </tr>
              </thead>
              <tbody>
                {findings.length === 0 && (
                  <tr><td colSpan={7} className="p-6 text-center text-gray-400">No findings yet.</td></tr>
                )}
                {findings.map((f) => (
                  <tr key={f.id} className="border-b last:border-0 hover:bg-gray-50">
                    <td className="p-3 text-gray-500">{f.reference || '—'}</td>
                    {/* Plain bold text, NOT a link. The earlier navy-blue styling
                        read as a clickable link that went nowhere; a real
                        detail/drill-down view lands in Phase 2. */}
                    <td className="p-3 font-medium text-gray-900">
                      {f.fraud_flag && (
                        <span title="Fraud-flagged finding" aria-label="Fraud-flagged">
                          <ShieldAlert className="mr-1 inline h-4 w-4 text-red-600" />
                        </span>
                      )}
                      {f.title}
                    </td>
                    <td className="p-3 text-gray-500">{f.engagement_ref}</td>
                    <td className="p-3">{f.root_cause_label}</td>
                    <td className="p-3">{f.effect_category_label}</td>
                    <td className="p-3">
                      <span className="rounded px-2 py-0.5 text-xs font-semibold text-white"
                        style={{ background: RATING_COLOR[f.rating] || '#6B7280' }}>
                        {f.rating || '—'}
                      </span>
                    </td>
                    <td className="p-3 text-gray-500">{f.status_label}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>
      </div>

      {showForm && canEdit && (
        <NewFindingForm engagements={engagements} onClose={() => setShowForm(false)}
          onSaved={() => { setShowForm(false); load() }} />
      )}
      {showEng && canEdit && (
        <NewEngagementForm onClose={() => setShowEng(false)}
          onSaved={() => { setShowEng(false); load() }} />
      )}
    </div>
  )
}

// Overlay portaled to <body> so it escapes <main>'s `relative z-[1]` stacking
// context. Without this the sidebar (z-40 at the document root) paints OVER the
// modal's left edge and clips wide (max-w-2xl) forms' field labels at laptop
// width — the "New finding" cut-off Oprah found (QA 2026-07-24). At the root the
// modal's z-50 sits above the sidebar, so the whole form is visible.
function ModalShell({ children }: { children: ReactNode }) {
  if (typeof document === 'undefined') return null
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-6">
      {children}
    </div>,
    document.body,
  )
}

function NewEngagementForm({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [e, setE] = useState({ reference: '', title: '', engagement_type: 'assurance', lead_auditor: '' })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const set = (k: string, v: string) => setE((p) => ({ ...p, [k]: v }))

  const submit = async () => {
    setSaving(true); setError(null)
    try {
      await apiFetch('/internal-audit/engagements/', { method: 'POST', body: JSON.stringify(e) })
      onSaved()
    } catch (err: any) {
      setError(err?.message ? String(err.message) : 'Could not create engagement (reference must be unique).')
    } finally { setSaving(false) }
  }

  return (
    <ModalShell>
      <div className="w-full max-w-md rounded-xl bg-white p-6 shadow-xl">
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-lg font-semibold" style={{ color: NAVY }}>New engagement</h3>
          <button onClick={onClose}><X className="h-5 w-5 text-gray-400" /></button>
        </div>
        <div className="grid gap-3">
          <label className="block"><span className="mb-1 block text-xs font-medium text-gray-500">Reference * (e.g. IA-2026-001)</span>
            <input className="w-full rounded-lg border px-3 py-2 text-sm" value={e.reference} onChange={(ev) => set('reference', ev.target.value)} /></label>
          <label className="block"><span className="mb-1 block text-xs font-medium text-gray-500">Title *</span>
            <input className="w-full rounded-lg border px-3 py-2 text-sm" value={e.title} onChange={(ev) => set('title', ev.target.value)} /></label>
          <label className="block"><span className="mb-1 block text-xs font-medium text-gray-500">Type</span>
            <select className="w-full rounded-lg border px-3 py-2 text-sm" value={e.engagement_type} onChange={(ev) => set('engagement_type', ev.target.value)}>
              <option value="assurance">Assurance</option><option value="advisory">Advisory</option><option value="investigation">Investigation</option>
            </select></label>
          <label className="block"><span className="mb-1 block text-xs font-medium text-gray-500">Lead auditor</span>
            <input className="w-full rounded-lg border px-3 py-2 text-sm" value={e.lead_auditor} onChange={(ev) => set('lead_auditor', ev.target.value)} /></label>
        </div>
        {error && <p className="mt-3 rounded bg-red-50 p-2 text-sm text-red-600">{error}</p>}
        <div className="mt-5 flex justify-end gap-3">
          <button onClick={onClose} className="rounded-lg border px-4 py-2 text-sm">Cancel</button>
          <button onClick={submit} disabled={saving || !e.reference || !e.title}
            className="rounded-lg px-4 py-2 text-sm font-medium text-white disabled:opacity-50" style={{ background: ORANGE }}>
            {saving ? 'Saving…' : 'Create'}
          </button>
        </div>
      </div>
    </ModalShell>
  )
}

function NewFindingForm({ engagements, onClose, onSaved }: {
  engagements: Engagement[]; onClose: () => void; onSaved: () => void
}) {
  const [f, setF] = useState({
    engagement: engagements[0]?.id || '', reference: '', title: '',
    criteria: '', condition: '', evidence_reference: '',
    root_cause: 'missing_control', root_cause_justification: '',
    effect_category: 'operational', effect_detail: '',
    likelihood: 3, impact: 3, rating_justification: '',
    fraud_flag: false, regulatory_tag: false, nbfira_reference: '',
  })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const set = (k: string, v: unknown) => setF((p) => ({ ...p, [k]: v }))
  const rating = useMemo(() => ratingBand(f.likelihood, f.impact), [f.likelihood, f.impact])

  // A 'Fraud exposure' effect category implies the flag — Finding.save() forces
  // it server-side, so mirror that here instead of letting the box say
  // otherwise. Any other category can still be flagged by hand (e.g. a
  // financial misstatement caused by fraud).
  const fraudImplied = f.effect_category === 'fraud'
  const fraudFlag = fraudImplied || f.fraud_flag

  const submit = async () => {
    setSaving(true); setError(null)
    try {
      await apiFetch('/internal-audit/findings/', {
        method: 'POST',
        body: JSON.stringify({ ...f, fraud_flag: fraudFlag }),
      })
      onSaved()
    } catch (e: any) {
      setError(e?.message ? String(e.message) : 'The finding could not be saved — check every element is filled.')
    } finally { setSaving(false) }
  }

  return (
    <ModalShell>
      <div className="w-full max-w-2xl rounded-xl bg-white p-6 shadow-xl">
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-lg font-semibold" style={{ color: NAVY }}>New finding</h3>
          <button onClick={onClose}><X className="h-5 w-5 text-gray-400" /></button>
        </div>

        {engagements.length === 0 && (
          <p className="mb-3 rounded bg-amber-50 p-2 text-xs text-amber-700">
            No engagement yet — create one first (findings hang off an engagement).
          </p>
        )}

        <div className="grid grid-cols-1 gap-3">
          <Field label="Engagement">
            <select className="input" value={f.engagement} onChange={(e) => set('engagement', e.target.value)}>
              {engagements.map((e) => <option key={e.id} value={e.id}>{e.reference} — {e.title}</option>)}
            </select>
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Reference (optional)"><input className="input" value={f.reference} onChange={(e) => set('reference', e.target.value)} /></Field>
            <Field label="Title *"><input className="input" value={f.title} onChange={(e) => set('title', e.target.value)} /></Field>
          </div>
          <Field label="Criteria * (the standard / policy / expectation)">
            <textarea className="input" rows={2} value={f.criteria} onChange={(e) => set('criteria', e.target.value)} />
          </Field>
          <Field label="Condition * (what was found)">
            <textarea className="input" rows={2} value={f.condition} onChange={(e) => set('condition', e.target.value)} />
          </Field>
          <Field label="Evidence reference * (workpaper backing the condition)">
            <input className="input" value={f.evidence_reference} onChange={(e) => set('evidence_reference', e.target.value)} />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Root cause *">
              <select className="input" value={f.root_cause} onChange={(e) => set('root_cause', e.target.value)}>
                {ROOT_CAUSES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </Field>
            <Field label="Effect category *">
              <select className="input" value={f.effect_category} onChange={(e) => set('effect_category', e.target.value)}>
                {EFFECTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </Field>
          </div>
          {f.root_cause === 'other' && (
            <Field label="Root cause justification * (required for 'Other')">
              <textarea className="input" rows={2} value={f.root_cause_justification} onChange={(e) => set('root_cause_justification', e.target.value)} />
            </Field>
          )}
          <Field label="Effect * (specific — BWP amount/range, regulatory, etc.)">
            <textarea className="input" rows={2} value={f.effect_detail} onChange={(e) => set('effect_detail', e.target.value)} />
          </Field>
          <div className="grid grid-cols-3 items-end gap-3">
            <Field label="Likelihood (1-5)">
              <input type="number" min={1} max={5} className="input" value={f.likelihood}
                onChange={(e) => set('likelihood', Number(e.target.value))} />
            </Field>
            <Field label="Impact (1-5)">
              <input type="number" min={1} max={5} className="input" value={f.impact}
                onChange={(e) => set('impact', Number(e.target.value))} />
            </Field>
            <div className="pb-1">
              <div className="text-xs text-gray-400">Rating (auto)</div>
              <span className="inline-block rounded px-3 py-1 text-sm font-semibold text-white"
                style={{ background: RATING_COLOR[rating] }}>{rating}</span>
            </div>
          </div>
          <Field label="Rating justification *">
            <textarea className="input" rows={2} value={f.rating_justification} onChange={(e) => set('rating_justification', e.target.value)} />
          </Field>
          {/* Feeds the dashboard's "Open fraud-flagged" tile (Module 8). Before
              this the flag could only be set indirectly, by choosing effect
              category 'Fraud exposure' — nothing on the form said so, so the
              tile looked permanently stuck on 0 (Oprah, QA 2026-07-25). */}
          <label className="flex items-center gap-2 text-sm text-gray-600">
            <input type="checkbox" checked={fraudFlag} disabled={fraudImplied}
              onChange={(e) => set('fraud_flag', e.target.checked)} />
            <span className="inline-flex items-center gap-1.5">
              <ShieldAlert className="h-4 w-4 text-red-600" />
              Fraud-flagged finding
              <span className="text-xs text-gray-400">
                {fraudImplied
                  ? '(implied by effect category "Fraud exposure")'
                  : '(suspected or actual fraud — counts on the dashboard fraud tile)'}
              </span>
            </span>
          </label>
          <label className="flex items-center gap-2 text-sm text-gray-600">
            <input type="checkbox" checked={f.regulatory_tag} onChange={(e) => set('regulatory_tag', e.target.checked)} />
            Regulatory (NBFIRA) finding
          </label>
          {f.regulatory_tag && (
            <Field label="NBFIRA return line item">
              <input className="input" value={f.nbfira_reference} onChange={(e) => set('nbfira_reference', e.target.value)} />
            </Field>
          )}
        </div>

        {error && <p className="mt-3 rounded bg-red-50 p-2 text-sm text-red-600">{error}</p>}

        <div className="mt-5 flex justify-end gap-3">
          <button onClick={onClose} className="rounded-lg border px-4 py-2 text-sm">Cancel</button>
          <button onClick={submit} disabled={saving || engagements.length === 0}
            className="rounded-lg px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
            style={{ background: ORANGE }}>
            {saving ? 'Saving…' : 'Save finding'}
          </button>
        </div>
      </div>

      <style jsx>{`
        .input { width: 100%; border: 1px solid #d1d5db; border-radius: 0.5rem; padding: 0.5rem 0.75rem; font-size: 0.875rem; }
        .input:focus { outline: none; border-color: ${NAVY}; box-shadow: 0 0 0 2px rgba(29,50,112,0.15); }
      `}</style>
    </ModalShell>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-gray-500">{label}</span>
      {children}
    </label>
  )
}
