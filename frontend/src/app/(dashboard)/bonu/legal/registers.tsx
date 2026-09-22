'use client'

/**
 * The three registers the legal office keeps every day.
 *
 * Each one is the same shape on purpose — a short add form, then the list, then
 * the total — because the officer moves between them all day and a different
 * layout per register would cost a second of re-reading every time.
 *
 * Editing is explicit: press Edit, change, press Save. An always-live grid saves
 * on every keystroke, which on a bonus register means a mistyped digit is
 * recorded before the eye catches it.
 */

import { useState } from 'react'
import { Table, td, tdNum, trBorder, LINE, NAVY, ORANGE, GREEN, RED, money2, Note } from '../_shared'
import {
  Advisory, FeeNote, InvoiceSaving, Overview,
  deleteRow, errText, n, today, writeRow,
} from './shared'

const inputCls =
  'w-full rounded-md border px-2 py-1 text-[13px] outline-none focus:ring-2 focus:ring-offset-0'
const inputStyle = { borderColor: LINE, color: NAVY, ['--tw-ring-color' as string]: '#F4A62355' }

export function Field({
  label, children, hint,
}: { label: string; children: React.ReactNode; hint?: string }) {
  return (
    <label className="block">
      <span className="text-[11px] uppercase tracking-wide" style={{ color: '#6B7280' }}>
        {label}
      </span>
      <div className="mt-1">{children}</div>
      {hint ? <span className="mt-1 block text-[11px]" style={{ color: '#9CA3AF' }}>{hint}</span> : null}
    </label>
  )
}

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={inputCls} style={inputStyle} />
}

export function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={inputCls} style={inputStyle} />
}

export function Btn({
  children, onClick, tone = 'primary', disabled, type = 'button',
}: {
  children: React.ReactNode
  onClick?: () => void
  tone?: 'primary' | 'ghost' | 'danger'
  disabled?: boolean
  type?: 'button' | 'submit'
}) {
  const style =
    tone === 'primary'
      ? { background: NAVY, color: '#fff', borderColor: NAVY }
      : tone === 'danger'
        ? { background: '#fff', color: RED, borderColor: '#F3D6D6' }
        : { background: '#fff', color: NAVY, borderColor: LINE }
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className="rounded-md border px-3 py-1.5 text-[13px] font-medium transition-opacity duration-150 disabled:opacity-40"
      style={style}
    >
      {children}
    </button>
  )
}

/** A short banner that says what went wrong, in the server's own words. */
function Problem({ text }: { text: string | null }) {
  if (!text) return null
  return (
    <div className="mt-3">
      <Note tone="warn">{text}</Note>
    </div>
  )
}

function SectionHead({ title, sub, right }: { title: string; sub?: string; right?: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h2 className="text-[17px] font-bold" style={{ color: NAVY }}>{title}</h2>
        {sub ? <p className="mt-1 max-w-3xl text-[13px]" style={{ color: '#6B7280' }}>{sub}</p> : null}
      </div>
      {right}
    </div>
  )
}

// ---------------------------------------------------------------------------
// 1 — Fee note: the matters billed in-house
// ---------------------------------------------------------------------------

export function FeeNoteTab({ data, reload }: { data: Overview; reload: () => Promise<void> }) {
  const services = Array.from(
    new Set([
      ...data.mappings.map((m) => m.fee_description),
      ...data.tariffs.filter((t) => t.scope === 'internal').map((t) => t.item),
    ]),
  )
  const autofill = (desc: string) => {
    const m = data.mappings.find((x) => x.fee_description === desc)
    if (m) return { unit: m.internal_unit, rate: m.internal_rate }
    const t = data.tariffs.find((x) => x.scope === 'internal' && x.item === desc)
    if (t) return { unit: t.unit === 'Flat rate' ? 'Flat' : t.unit, rate: t.rate }
    return null
  }
  const blank = {
    date: today(), client: '', portfolio: 'BONU',
    description: services[0] || '', unit: 'Flat', rate: '200', qty: '1',
  }
  const [draft, setDraft] = useState<Record<string, string>>(blank)
  const [editing, setEditing] = useState<string | null>(null)
  const [edit, setEdit] = useState<Record<string, string>>({})
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const setDesc = (v: string, into: 'draft' | 'edit') => {
    const a = autofill(v)
    const patch = { description: v, ...(a ? { unit: a.unit, rate: a.rate } : {}) }
    if (into === 'draft') setDraft((d) => ({ ...d, ...patch }))
    else setEdit((d) => ({ ...d, ...patch }))
  }

  const add = async () => {
    setBusy(true); setErr(null)
    try {
      await writeRow('fee-notes', draft)
      setDraft({ ...blank, description: draft.description, unit: draft.unit, rate: draft.rate })
      await reload()
    } catch (e) { setErr(errText(e, 'Could not add that line.')) } finally { setBusy(false) }
  }
  const save = async (id: string) => {
    setBusy(true); setErr(null)
    try { await writeRow('fee-notes', edit, id); setEditing(null); await reload() }
    catch (e) { setErr(errText(e, 'Could not save that line.')) } finally { setBusy(false) }
  }
  const remove = async (id: string) => {
    setBusy(true); setErr(null)
    try { await deleteRow('fee-notes', id); await reload() }
    catch (e) { setErr(errText(e, 'Could not remove that line.')) } finally { setBusy(false) }
  }

  const total = data.fee_notes.reduce((s, f) => s + n(f.amount), 0)

  return (
    <div className="space-y-5">
      <SectionHead
        title="Fee note — matters billed in-house"
        sub="Every matter Alpha Law handled itself. Pick the service and the unit and rate fill themselves in from the internal tariff. This is what the monthly performance bonus is measured on."
      />

      <div className="rounded-xl border p-4" style={{ borderColor: LINE }}>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="Date">
            <Input type="date" value={draft.date} onChange={(e) => setDraft({ ...draft, date: e.target.value })} />
          </Field>
          <Field label="Client / BONU member">
            <Input value={draft.client} placeholder="e.g. Kabo Modise"
              onChange={(e) => setDraft({ ...draft, client: e.target.value })} />
          </Field>
          <Field label="Portfolio">
            <Input value={draft.portfolio} onChange={(e) => setDraft({ ...draft, portfolio: e.target.value })} />
          </Field>
          <Field label="Service">
            <Select value={draft.description} onChange={(e) => setDesc(e.target.value, 'draft')}>
              {services.map((s) => <option key={s}>{s}</option>)}
            </Select>
          </Field>
          <Field label="Unit">
            <Input value={draft.unit} onChange={(e) => setDraft({ ...draft, unit: e.target.value })} />
          </Field>
          <Field label="Rate (BWP)">
            <Input value={draft.rate} inputMode="decimal"
              onChange={(e) => setDraft({ ...draft, rate: e.target.value })} />
          </Field>
          <Field label="Qty / hours">
            <Input value={draft.qty} inputMode="decimal"
              onChange={(e) => setDraft({ ...draft, qty: e.target.value })} />
          </Field>
          <div className="flex items-end">
            <Btn onClick={add} disabled={busy}>Add line</Btn>
          </div>
        </div>
        <Problem text={err} />
      </div>

      <div className="rounded-xl border" style={{ borderColor: LINE }}>
        <Table head={['Date', 'Client', 'Portfolio', 'Service', 'Unit', 'Rate', 'Qty/hrs', 'Amount', '']}>
          {data.fee_notes.length === 0 ? (
            <tr><td className={td} colSpan={9} style={{ color: '#9CA3AF' }}>No matter lines yet — add the first one above.</td></tr>
          ) : data.fee_notes.map((f) => editing === f.id ? (
            <tr key={f.id} style={trBorder}>
              <td className={td}><Input type="date" value={edit.date} onChange={(e) => setEdit({ ...edit, date: e.target.value })} /></td>
              <td className={td}><Input value={edit.client} onChange={(e) => setEdit({ ...edit, client: e.target.value })} /></td>
              <td className={td}><Input value={edit.portfolio} onChange={(e) => setEdit({ ...edit, portfolio: e.target.value })} /></td>
              <td className={td}>
                <Select value={edit.description} onChange={(e) => setDesc(e.target.value, 'edit')}>
                  {Array.from(new Set([edit.description, ...services])).map((s) => <option key={s}>{s}</option>)}
                </Select>
              </td>
              <td className={td}><Input value={edit.unit} onChange={(e) => setEdit({ ...edit, unit: e.target.value })} /></td>
              <td className={tdNum}><Input value={edit.rate} inputMode="decimal" onChange={(e) => setEdit({ ...edit, rate: e.target.value })} /></td>
              <td className={tdNum}><Input value={edit.qty} inputMode="decimal" onChange={(e) => setEdit({ ...edit, qty: e.target.value })} /></td>
              <td className={tdNum} style={{ color: '#9CA3AF' }}>—</td>
              <td className={td}>
                <div className="flex gap-1">
                  <Btn onClick={() => save(f.id)} disabled={busy}>Save</Btn>
                  <Btn tone="ghost" onClick={() => setEditing(null)}>Cancel</Btn>
                </div>
              </td>
            </tr>
          ) : (
            <tr key={f.id} style={trBorder}>
              <td className={td}>{f.date}</td>
              <td className={td} style={{ color: NAVY, fontWeight: 500 }}>{f.client}</td>
              <td className={td}>{f.portfolio}</td>
              <td className={td}>
                {f.description}
                {!f.mapped ? (
                  <span className="ml-2 rounded px-1.5 py-0.5 text-[11px]"
                    style={{ background: '#FEF3C7', color: '#92400E' }}>no external match</span>
                ) : null}
              </td>
              <td className={td}>{f.unit}</td>
              <td className={tdNum}>{money2(n(f.rate))}</td>
              <td className={tdNum}>{f.qty}</td>
              <td className={tdNum} style={{ fontWeight: 600 }}>{money2(n(f.amount))}</td>
              <td className={td}>
                <div className="flex gap-1">
                  <Btn tone="ghost" onClick={() => { setEditing(f.id); setEdit({ ...f } as unknown as Record<string, string>) }}>Edit</Btn>
                  <Btn tone="danger" onClick={() => remove(f.id)} disabled={busy}>Remove</Btn>
                </div>
              </td>
            </tr>
          ))}
        </Table>
        <div className="flex items-center justify-between px-3 py-3" style={{ borderTop: `2px solid ${NAVY}` }}>
          <span className="text-[13px] font-semibold" style={{ color: NAVY }}>Total matter billing — all months</span>
          <span className="text-[15px] font-bold" style={{ color: NAVY }}>{money2(total)}</span>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// 2 — Invoice savings: the external bills argued down
// ---------------------------------------------------------------------------

export function InvoiceLogTab({ data, reload }: { data: Overview; reload: () => Promise<void> }) {
  const blank = {
    date_received: today(), date_reviewed: today(), invoice_ref: '',
    external_attorney: '', original_amount: '0', agreed_amount: '0', note: '',
  }
  const [draft, setDraft] = useState<Record<string, string>>(blank)
  const [editing, setEditing] = useState<string | null>(null)
  const [edit, setEdit] = useState<Record<string, string>>({})
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const sla = data.settings.sla_days

  const add = async () => {
    setBusy(true); setErr(null)
    try { await writeRow('invoice-savings', draft); setDraft(blank); await reload() }
    catch (e) { setErr(errText(e, 'Could not log that invoice.')) } finally { setBusy(false) }
  }
  const save = async (id: string) => {
    setBusy(true); setErr(null)
    try { await writeRow('invoice-savings', edit, id); setEditing(null); await reload() }
    catch (e) { setErr(errText(e, 'Could not save that invoice.')) } finally { setBusy(false) }
  }
  const remove = async (id: string) => {
    setBusy(true); setErr(null)
    try { await deleteRow('invoice-savings', id); await reload() }
    catch (e) { setErr(errText(e, 'Could not remove that invoice.')) } finally { setBusy(false) }
  }

  const total = data.invoice_savings.reduce((s, r) => s + n(r.saving), 0)
  const s = data.summary.sla

  return (
    <div className="space-y-5">
      <SectionHead
        title="Invoice savings — external bills reviewed"
        sub={`Each external attorney's bill as it arrived, and what we agreed to pay. The difference is the saving the quarterly bonus is measured on. Turnaround is counted against the ${sla}-day target.`}
      />

      <div className="grid gap-4 sm:grid-cols-3">
        <div className="rounded-xl border p-4" style={{ borderColor: LINE }}>
          <div className="text-[11px] uppercase tracking-wide" style={{ color: '#6B7280' }}>Total verified saving</div>
          <div className="mt-1 text-[21px] font-bold leading-none" style={{ color: GREEN }}>{money2(total)}</div>
        </div>
        <div className="rounded-xl border p-4" style={{ borderColor: LINE }}>
          <div className="text-[11px] uppercase tracking-wide" style={{ color: '#6B7280' }}>Reviewed inside {sla} days</div>
          <div className="mt-1 text-[21px] font-bold leading-none"
            style={{ color: s.pct === null ? NAVY : Number(s.pct) >= 0.9 ? GREEN : RED }}>
            {s.pct === null ? '—' : `${(Number(s.pct) * 100).toFixed(0)}%`}
          </div>
          <div className="mt-1 text-[12px]" style={{ color: '#6B7280' }}>{s.within} of {s.measured} measured</div>
        </div>
        <div className="rounded-xl border p-4" style={{ borderColor: LINE }}>
          <div className="text-[11px] uppercase tracking-wide" style={{ color: '#6B7280' }}>No arrival date</div>
          <div className="mt-1 text-[21px] font-bold leading-none" style={{ color: s.unknown ? '#B45309' : NAVY }}>{s.unknown}</div>
          <div className="mt-1 text-[12px]" style={{ color: '#6B7280' }}>Turnaround cannot be measured on these</div>
        </div>
      </div>

      <div className="rounded-xl border p-4" style={{ borderColor: LINE }}>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="Date received"><Input type="date" value={draft.date_received}
            onChange={(e) => setDraft({ ...draft, date_received: e.target.value })} /></Field>
          <Field label="Date reviewed"><Input type="date" value={draft.date_reviewed}
            onChange={(e) => setDraft({ ...draft, date_reviewed: e.target.value })} /></Field>
          <Field label="Invoice reference"><Input value={draft.invoice_ref} placeholder="e.g. B&C-0421"
            onChange={(e) => setDraft({ ...draft, invoice_ref: e.target.value })} /></Field>
          <Field label="External attorney"><Input value={draft.external_attorney} placeholder="e.g. Brown & Company"
            onChange={(e) => setDraft({ ...draft, external_attorney: e.target.value })} /></Field>
          <Field label="Original amount (BWP)"><Input value={draft.original_amount} inputMode="decimal"
            onChange={(e) => setDraft({ ...draft, original_amount: e.target.value })} /></Field>
          <Field label="Agreed amount (BWP)"><Input value={draft.agreed_amount} inputMode="decimal"
            onChange={(e) => setDraft({ ...draft, agreed_amount: e.target.value })} /></Field>
          <Field label="Note (optional)"><Input value={draft.note} placeholder="What was taken off, and why"
            onChange={(e) => setDraft({ ...draft, note: e.target.value })} /></Field>
          <div className="flex items-end"><Btn onClick={add} disabled={busy}>Log invoice</Btn></div>
        </div>
        <Problem text={err} />
      </div>

      <div className="rounded-xl border" style={{ borderColor: LINE }}>
        <Table head={['Reviewed', 'Invoice', 'Original', 'Agreed', 'Saving', 'SLA', '']}>
          {data.invoice_savings.length === 0 ? (
            <tr><td className={td} colSpan={7} style={{ color: '#9CA3AF' }}>No invoices logged yet.</td></tr>
          ) : data.invoice_savings.map((r) => editing === r.id ? (
            <tr key={r.id} style={trBorder}>
              <td className={td}>
                <Input type="date" value={edit.date_received ?? ''} onChange={(e) => setEdit({ ...edit, date_received: e.target.value })} />
                <div className="mt-1">
                  <Input type="date" value={edit.date_reviewed ?? ''} onChange={(e) => setEdit({ ...edit, date_reviewed: e.target.value })} />
                </div>
              </td>
              <td className={td}>
                <Input value={edit.invoice_ref} onChange={(e) => setEdit({ ...edit, invoice_ref: e.target.value })} />
                <div className="mt-1">
                  <Input value={edit.external_attorney} onChange={(e) => setEdit({ ...edit, external_attorney: e.target.value })} />
                </div>
              </td>
              <td className={tdNum}><Input value={edit.original_amount} inputMode="decimal" onChange={(e) => setEdit({ ...edit, original_amount: e.target.value })} /></td>
              <td className={tdNum}><Input value={edit.agreed_amount} inputMode="decimal" onChange={(e) => setEdit({ ...edit, agreed_amount: e.target.value })} /></td>
              <td className={td} style={{ color: '#9CA3AF' }}>—</td>
              <td className={td}>
                <div className="flex gap-1">
                  <Btn onClick={() => save(r.id)} disabled={busy}>Save</Btn>
                  <Btn tone="ghost" onClick={() => setEditing(null)}>Cancel</Btn>
                </div>
              </td>
            </tr>
          ) : (
            <tr key={r.id} style={trBorder}>
              <td className={td}>
                {r.date_reviewed}
                <div className="text-[11px]" style={{ color: '#9CA3AF' }}>
                  {r.date_received ? `arrived ${r.date_received}` : 'arrival date not recorded'}
                </div>
              </td>
              <td className={td} style={{ color: NAVY, fontWeight: 500 }}>
                {r.invoice_ref}
                <div className="text-[11px] font-normal" style={{ color: '#9CA3AF' }}>{r.external_attorney || '—'}</div>
              </td>
              <td className={tdNum}>{money2(n(r.original_amount))}</td>
              <td className={tdNum}>{money2(n(r.agreed_amount))}</td>
              <td className={tdNum} style={{ fontWeight: 600, color: n(r.saving) >= 0 ? GREEN : RED }}>{money2(n(r.saving))}</td>
              <td className={td}>
                {r.turnaround_days === null ? (
                  <span style={{ color: '#9CA3AF' }}>not known</span>
                ) : (
                  <span className="rounded px-1.5 py-0.5 text-[11px] font-semibold"
                    style={r.sla_met
                      ? { background: '#ECFDF5', color: GREEN }
                      : { background: '#FEF2F2', color: RED }}>
                    {r.turnaround_days}d {r.sla_met ? 'met' : 'missed'}
                  </span>
                )}
              </td>
              <td className={td}>
                <div className="flex gap-1">
                  <Btn tone="ghost" onClick={() => { setEditing(r.id); setEdit({ ...r } as unknown as Record<string, string>) }}>Edit</Btn>
                  <Btn tone="danger" onClick={() => remove(r.id)} disabled={busy}>Remove</Btn>
                </div>
              </td>
            </tr>
          ))}
        </Table>
        <div className="flex items-center justify-between px-3 py-3" style={{ borderTop: `2px solid ${NAVY}` }}>
          <span className="text-[13px] font-semibold" style={{ color: NAVY }}>Total verified saving — all quarters</span>
          <span className="text-[15px] font-bold" style={{ color: GREEN }}>{money2(total)}</span>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// 4 — Internal advisory to ADI departments
// ---------------------------------------------------------------------------

export function AdvisoryTab({ data, reload }: { data: Overview; reload: () => Promise<void> }) {
  const blank = {
    date: today(), department: '', client: '', matter_ref: '',
    description: '', type_of_work: '', hours: '1',
  }
  const [draft, setDraft] = useState<Record<string, string>>(blank)
  const [editing, setEditing] = useState<string | null>(null)
  const [edit, setEdit] = useState<Record<string, string>>({})
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const add = async () => {
    setBusy(true); setErr(null)
    try { await writeRow('advisory', draft); setDraft(blank); await reload() }
    catch (e) { setErr(errText(e, 'Could not log that entry.')) } finally { setBusy(false) }
  }
  const save = async (id: string) => {
    setBusy(true); setErr(null)
    try { await writeRow('advisory', edit, id); setEditing(null); await reload() }
    catch (e) { setErr(errText(e, 'Could not save that entry.')) } finally { setBusy(false) }
  }
  const remove = async (id: string) => {
    setBusy(true); setErr(null)
    try { await deleteRow('advisory', id); await reload() }
    catch (e) { setErr(errText(e, 'Could not remove that entry.')) } finally { setBusy(false) }
  }

  const hours = data.advisory.reduce((s, a) => s + n(a.hours), 0)
  const value = data.advisory.reduce((s, a) => s + n(a.value), 0)

  return (
    <div className="space-y-5">
      <SectionHead
        title="Internal advisory — work for other departments"
        sub={`Contract reviews, NDAs, disciplinary support, policy vetting. No internal rate applies to this work — it is recorded as scope, and valued at the external hourly rate of ${money2(n(data.settings.external_hourly_rate))} only to show what buying the same advice outside would have cost.`}
      />

      <Note>
        <strong>Illustrative.</strong> This value is a comparison, not income and not a bonus. It is
        shown so the office&rsquo;s work for the rest of the business is visible rather than invisible.
      </Note>

      <div className="rounded-xl border p-4" style={{ borderColor: LINE }}>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="Date"><Input type="date" value={draft.date} onChange={(e) => setDraft({ ...draft, date: e.target.value })} /></Field>
          <Field label="Department">
            <Select value={draft.department} onChange={(e) => setDraft({ ...draft, department: e.target.value })}>
              <option value="">— Select —</option>
              {data.departments.map((d) => <option key={d}>{d}</option>)}
            </Select>
          </Field>
          <Field label="Name of client"><Input value={draft.client} onChange={(e) => setDraft({ ...draft, client: e.target.value })} /></Field>
          <Field label="Matter / reference"><Input value={draft.matter_ref} placeholder="e.g. NDA-0091"
            onChange={(e) => setDraft({ ...draft, matter_ref: e.target.value })} /></Field>
          <Field label="Description"><Input value={draft.description} placeholder="e.g. NDA review"
            onChange={(e) => setDraft({ ...draft, description: e.target.value })} /></Field>
          <Field label="Type of work"><Input value={draft.type_of_work} placeholder="e.g. Contract review"
            onChange={(e) => setDraft({ ...draft, type_of_work: e.target.value })} /></Field>
          <Field label="Hours spent"><Input value={draft.hours} inputMode="decimal"
            onChange={(e) => setDraft({ ...draft, hours: e.target.value })} /></Field>
          <div className="flex items-end"><Btn onClick={add} disabled={busy}>Log entry</Btn></div>
        </div>
        <Problem text={err} />
      </div>

      <div className="rounded-xl border" style={{ borderColor: LINE }}>
        <Table head={['Date', 'Department', 'Client', 'Matter', 'Description', 'Type of work', 'Hours', 'Value', '']}>
          {data.advisory.length === 0 ? (
            <tr><td className={td} colSpan={9} style={{ color: '#9CA3AF' }}>No advisory work logged yet.</td></tr>
          ) : data.advisory.map((a) => editing === a.id ? (
            <tr key={a.id} style={trBorder}>
              <td className={td}><Input type="date" value={edit.date ?? ''} onChange={(e) => setEdit({ ...edit, date: e.target.value })} /></td>
              <td className={td}>
                <Select value={edit.department ?? ''} onChange={(e) => setEdit({ ...edit, department: e.target.value })}>
                  <option value="">— Select —</option>
                  {data.departments.map((d) => <option key={d}>{d}</option>)}
                </Select>
              </td>
              <td className={td}><Input value={edit.client ?? ''} onChange={(e) => setEdit({ ...edit, client: e.target.value })} /></td>
              <td className={td}><Input value={edit.matter_ref ?? ''} onChange={(e) => setEdit({ ...edit, matter_ref: e.target.value })} /></td>
              <td className={td}><Input value={edit.description ?? ''} onChange={(e) => setEdit({ ...edit, description: e.target.value })} /></td>
              <td className={td}><Input value={edit.type_of_work ?? ''} onChange={(e) => setEdit({ ...edit, type_of_work: e.target.value })} /></td>
              <td className={tdNum}><Input value={edit.hours ?? ''} inputMode="decimal" onChange={(e) => setEdit({ ...edit, hours: e.target.value })} /></td>
              <td className={tdNum} style={{ color: '#9CA3AF' }}>—</td>
              <td className={td}>
                <div className="flex gap-1">
                  <Btn onClick={() => save(a.id)} disabled={busy}>Save</Btn>
                  <Btn tone="ghost" onClick={() => setEditing(null)}>Cancel</Btn>
                </div>
              </td>
            </tr>
          ) : (
            <tr key={a.id} style={trBorder}>
              <td className={td}>{a.date}</td>
              <td className={td}>{a.department || '—'}</td>
              <td className={td}>{a.client || '—'}</td>
              <td className={td}>{a.matter_ref || '—'}</td>
              <td className={td} style={{ color: NAVY }}>{a.description}</td>
              <td className={td}>{a.type_of_work || '—'}</td>
              <td className={tdNum}>{a.hours}</td>
              <td className={tdNum} style={{ fontWeight: 600 }}>{money2(n(a.value))}</td>
              <td className={td}>
                <div className="flex gap-1">
                  <Btn tone="ghost" onClick={() => { setEditing(a.id); setEdit({ ...a } as unknown as Record<string, string>) }}>Edit</Btn>
                  <Btn tone="danger" onClick={() => remove(a.id)} disabled={busy}>Remove</Btn>
                </div>
              </td>
            </tr>
          ))}
        </Table>
        <div className="flex items-center justify-between px-3 py-3" style={{ borderTop: `2px solid ${NAVY}` }}>
          <span className="text-[13px] font-semibold" style={{ color: NAVY }}>
            Total — {hours.toFixed(2)} hours
          </span>
          <span className="text-[15px] font-bold" style={{ color: NAVY }}>{money2(value)}</span>
        </div>
      </div>
    </div>
  )
}

export { SectionHead }
export type { Advisory, FeeNote, InvoiceSaving }
