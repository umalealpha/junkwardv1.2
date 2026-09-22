'use client'

/**
 * Raise a new reinsurance counterparty.
 *
 * The whole onboarding chain existed with no way to start it — Underwriting
 * could not create a counterparty without a developer. CFO, 17-Sep-2026.
 *
 * Two deliberate choices:
 *  - Only name and short code are required. Everything else can arrive with
 *    the KYC file later, and a long mandatory form is how a register ends up
 *    with a row called "Swiss Re TBC".
 *  - The screen says plainly that the new record is NOT usable yet. "Created"
 *    reading as "approved" is the exact failure the approval chain exists to
 *    prevent.
 */

import { useState } from 'react'
import { createReinsurer, type NewReinsurer } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { AlertTriangle, X } from 'lucide-react'

const NAVY = '#0B0B3B'

const PURPOSES = [
  { value: 'facultative',  label: 'Facultative' },
  { value: 'treaty',       label: 'Treaty' },
  { value: 'retrocession', label: 'Retrocession' },
  { value: 'other',        label: 'Other' },
]

function Field({ label, hint, children }: {
  label: string; hint?: string; children: React.ReactNode
}) {
  return (
    <label className="text-sm block">
      <span className="block text-xs text-[#6B7280] mb-1">{label}</span>
      {children}
      {hint && <span className="block text-[11px] text-[#9CA3AF] mt-1">{hint}</span>}
    </label>
  )
}

const inputCls = 'border rounded px-2 py-1.5 text-sm w-full'

export function NewReinsurerDialog({ onClose, onCreated }: {
  onClose: () => void
  onCreated: (id: string, name: string) => void
}) {
  const [form, setForm] = useState<NewReinsurer>({ name: '', short_code: '' })
  const [purposes, setPurposes] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const set = (k: keyof NewReinsurer) => (
    e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>,
  ) => setForm((f) => ({ ...f, [k]: e.target.value }))

  const togglePurpose = (v: string) =>
    setPurposes((p) => (p.includes(v) ? p.filter((x) => x !== v) : [...p, v]))

  async function submit() {
    setBusy(true); setError(null)
    try {
      const created = await createReinsurer({
        ...form,
        name: form.name.trim(),
        short_code: form.short_code.trim().toUpperCase(),
        onboarding_purposes: purposes,
      })
      onCreated(created.id, created.name)
    } catch (err) {
      // The server's sentence, not a generic one — it names the clashing
      // record or the bad date, which is the whole point of showing it.
      setError(err instanceof Error ? err.message : 'Could not create the reinsurer.')
    } finally {
      setBusy(false)
    }
  }

  const ready = form.name.trim().length > 0 && form.short_code.trim().length > 0

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto
                    bg-black/40 p-4 sm:p-8">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-2xl my-4">
        <div className="flex items-center justify-between border-b px-5 py-3">
          <h2 className="text-base font-semibold" style={{ color: NAVY }}>
            New reinsurer
          </h2>
          <button type="button" onClick={onClose} aria-label="Close"
                  className="text-[#6B7280] hover:text-[#111827]">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="px-5 py-4 space-y-4">
          <div className="rounded border px-3 py-2 text-sm"
               style={{ background: '#FFF7ED', borderColor: '#FED7AA', color: '#9A3412' }}>
            This creates a <strong>draft</strong>. It cannot be used on a placement
            until its KYC documents are on file and it has been approved.
          </div>

          {error && (
            <div className="rounded border px-3 py-2 text-sm flex items-start gap-2"
                 style={{ background: '#FEF2F2', borderColor: '#FEE2E2', color: '#DC2626' }}>
              <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Field label="Name *" hint="What everyone calls them, e.g. Swiss Re">
              <input value={form.name} onChange={set('name')} className={inputCls}
                     autoFocus />
            </Field>
            <Field label="Short code *" hint="Appears on journals and reports">
              <input value={form.short_code} onChange={set('short_code')}
                     className={inputCls} placeholder="SWISSRE" />
            </Field>
            <Field label="Registered legal name">
              <input value={form.legal_name ?? ''} onChange={set('legal_name')}
                     className={inputCls} />
            </Field>
            <Field label="Group" hint="Two lines on one group are one exposure">
              <input value={form.carrier_group ?? ''} onChange={set('carrier_group')}
                     className={inputCls} />
            </Field>
            <Field label="Country of domicile" hint="Two letters, e.g. CH">
              <input value={form.domicile ?? ''} onChange={set('domicile')}
                     className={inputCls} maxLength={2} />
            </Field>
            <Field label="Regulator">
              <input value={form.regulator ?? ''} onChange={set('regulator')}
                     className={inputCls} />
            </Field>
            <Field label="Registration number">
              <input value={form.registration_number ?? ''}
                     onChange={set('registration_number')} className={inputCls} />
            </Field>
            <Field label="Licence number">
              <input value={form.licence_number ?? ''} onChange={set('licence_number')}
                     className={inputCls} />
            </Field>
            <Field label="Placing broker" hint="If the line comes through one">
              <input value={form.broker ?? ''} onChange={set('broker')}
                     className={inputCls} />
            </Field>
            <Field label="Credit rating">
              <input value={form.credit_rating ?? ''} onChange={set('credit_rating')}
                     className={inputCls} placeholder="AA-" />
            </Field>
          </div>

          <div>
            <span className="block text-xs text-[#6B7280] mb-1.5">
              What are we onboarding them for?
            </span>
            <div className="flex flex-wrap gap-2">
              {PURPOSES.map((p) => {
                const on = purposes.includes(p.value)
                return (
                  <button key={p.value} type="button"
                          onClick={() => togglePurpose(p.value)}
                          className="text-xs font-semibold px-3 py-1.5 rounded-full border
                                     transition-colors"
                          style={on
                            ? { background: NAVY, borderColor: NAVY, color: '#FFFFFF' }
                            : { background: '#FFFFFF', borderColor: '#E5E7EB', color: '#374151' }}>
                    {p.label}
                  </button>
                )
              })}
            </div>
            <span className="block text-[11px] text-[#9CA3AF] mt-1.5">
              Approved for facultative is not approved for treaty — each is asked for
              separately.
            </span>
          </div>

          <Field label="Notes">
            <textarea value={form.notes ?? ''} onChange={set('notes')}
                      rows={2} className={inputCls} />
          </Field>
        </div>

        <div className="flex items-center justify-end gap-2 border-t px-5 py-3">
          <Button variant="outline" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button onClick={submit} disabled={busy || !ready} style={{ background: NAVY }}>
            {busy ? 'Creating…' : 'Create draft'}
          </Button>
        </div>
      </div>
    </div>
  )
}
