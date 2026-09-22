'use client'

/**
 * The read-and-report side of the legal office: the external comparison, the
 * quarterly bonus tracker, the rate cards, and the two printable reports.
 *
 * The comparison screens carry an "Illustrative" mark wherever a figure is a
 * what-it-would-have-cost rather than money that changed hands, because the two
 * sit next to each other and a reader must never take one for the other. The
 * quarterly bonus, by contrast, is contractual and is not marked.
 */

import { useState, type ReactNode } from 'react'
import { Table, td, tdNum, trBorder, LINE, NAVY, ORANGE, GREEN, RED, AMBER, money2, Note } from '../_shared'
import { Btn, Field, Input, Select, SectionHead } from './registers'
import { Mapping, Overview, deleteRow, downloadReport, monthLabel, n, pct, quarterLabel, errText, writeRow } from './shared'
import { apiFetch } from '@/lib/api'

function Illustrative() {
  return (
    <span className="ml-2 rounded px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide"
      style={{ background: '#FEF3C7', color: '#92400E' }}>Illustrative</span>
  )
}

// ---------------------------------------------------------------------------
// 3 — Savings vs the external panel
// ---------------------------------------------------------------------------

export function ExternalTab({ data }: { data: Overview }) {
  const s = data.summary
  return (
    <div className="space-y-5">
      <SectionHead
        title="Savings against the external panel"
        sub="Every fee-note line priced twice: what Alpha Law charged, and what the same work would have cost at the external panel's tariff. Built from the rate map on the Rates tab — nothing is typed twice."
      />

      {s.unmapped_lines_total > 0 ? (
        <Note tone="warn" title={`${s.unmapped_lines_total} line${s.unmapped_lines_total === 1 ? '' : 's'} have no external match`}>
          Those lines are counted in the internal billing but left out of the comparison, so the
          saving below is understated rather than guessed at. Add the missing service to the rate
          map on the Rates tab and it will be included straight away.
        </Note>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Tile label="Billed by Alpha Law (mapped lines)" value={money2(n(s.lifetime_mapped_internal))}
          sub={n(s.lifetime_internal) !== n(s.lifetime_mapped_internal)
            ? `${money2(n(s.lifetime_internal))} billed in total, including lines with no panel match`
            : undefined} />
        <Tile label="Same work at panel rates" value={money2(n(s.lifetime_external))} />
        <Tile label="Kept by handling it in-house" value={money2(n(s.lifetime_saving))} tone={GREEN} />
        <Tile label="Share of the panel cost saved" value={pct(s.lifetime_pct_saved)} tone={GREEN} />
      </div>

      <div className="rounded-xl border" style={{ borderColor: LINE }}>
        <div className="px-4 py-3 text-[13px] font-bold" style={{ color: NAVY, borderBottom: `1px solid ${LINE}` }}>
          Per matter line<Illustrative />
        </div>
        <Table head={['Date', 'Client', 'Service', 'Qty/hrs', 'Alpha Law', 'Panel rate', 'Panel equivalent', 'Saving', '% saved']}>
          {data.fee_notes.length === 0 ? (
            <tr><td className={td} colSpan={9} style={{ color: '#9CA3AF' }}>No fee note lines yet.</td></tr>
          ) : data.fee_notes.map((f) => (
            <tr key={f.id} style={trBorder}>
              <td className={td}>{f.date}</td>
              <td className={td}>{f.client}</td>
              <td className={td}>{f.description}</td>
              <td className={tdNum}>{f.qty}</td>
              <td className={tdNum}>{money2(n(f.amount))}</td>
              <td className={tdNum}>
                {f.external_rate === null ? '—' : money2(n(f.external_rate))}
                {f.calc_basis ? (
                  <div className="text-[11px] font-normal" style={{ color: '#9CA3AF' }}>{f.calc_basis}</div>
                ) : null}
              </td>
              <td className={tdNum}>
                {f.external_equivalent === null
                  ? <span style={{ color: '#9CA3AF' }}>no match</span>
                  : money2(n(f.external_equivalent))}
              </td>
              <td className={tdNum} style={{ fontWeight: 600, color: f.saving === null ? '#9CA3AF' : n(f.saving) >= 0 ? GREEN : RED }}>
                {f.saving === null ? '—' : money2(n(f.saving))}
              </td>
              <td className={tdNum}>{pct(f.pct_saved)}</td>
            </tr>
          ))}
        </Table>
      </div>

      <div className="rounded-xl border" style={{ borderColor: LINE }}>
        <div className="px-4 py-3 text-[13px] font-bold" style={{ color: NAVY, borderBottom: `1px solid ${LINE}` }}>
          Per client / BONU member<Illustrative />
        </div>
        <Table head={['Client / BONU member', 'Alpha Law', 'Panel equivalent', 'Saving', '% saved']}>
          {data.client_savings.length === 0 ? (
            <tr><td className={td} colSpan={5} style={{ color: '#9CA3AF' }}>No mapped lines yet.</td></tr>
          ) : data.client_savings.map((c) => (
            <tr key={c.client} style={trBorder}>
              <td className={td} style={{ color: NAVY, fontWeight: 500 }}>{c.client}</td>
              <td className={tdNum}>{money2(n(c.internal))}</td>
              <td className={tdNum}>{money2(n(c.external))}</td>
              <td className={tdNum} style={{ fontWeight: 600, color: GREEN }}>{money2(n(c.saving))}</td>
              <td className={tdNum}>{pct(c.pct_saved)}</td>
            </tr>
          ))}
        </Table>
      </div>
    </div>
  )
}

function Tile({ label, value, tone, sub }: { label: string; value: string; tone?: string; sub?: string }) {
  return (
    <div className="rounded-xl border p-4" style={{ borderColor: LINE }}>
      <div className="text-[11px] uppercase tracking-wide" style={{ color: '#6B7280' }}>{label}</div>
      <div className="mt-1 text-[21px] font-bold leading-none" style={{ color: tone || NAVY }}>{value}</div>
      {sub ? <div className="mt-1 text-[12px]" style={{ color: '#6B7280' }}>{sub}</div> : null}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Quarterly bonus tracker
// ---------------------------------------------------------------------------

export function QuarterlyTab({
  data, onQuarter,
}: { data: Overview; onQuarter: (q: string) => void }) {
  const s = data.summary
  return (
    <div className="space-y-5">
      <SectionHead
        title="Quarterly invoice-reduction bonus"
        sub={`${pct(n(data.settings.quarterly_bonus_pct))} of the whole quarter's saving once the quarter reaches ${money2(n(s.quarter_threshold))} — not a percentage of each invoice on its own. Added up automatically from the invoice register.`}
        right={
          <div className="w-44">
            <Field label="Quarter">
              <Select value={data.quarter} onChange={(e) => onQuarter(e.target.value)}>
                {data.quarters.map((q) => <option key={q} value={q}>{quarterLabel(q)}</option>)}
              </Select>
            </Field>
          </div>
        }
      />

      <div className="rounded-xl border" style={{ borderColor: LINE }}>
        <Table head={['Month', 'Saving verified']}>
          {s.quarter_months.map((m) => (
            <tr key={m.month} style={trBorder}>
              <td className={td}>{monthLabel(m.month)}</td>
              <td className={tdNum} style={{ fontWeight: 500 }}>{money2(n(m.saving))}</td>
            </tr>
          ))}
        </Table>
        <div className="flex items-center justify-between px-3 py-3" style={{ borderTop: `2px solid ${NAVY}` }}>
          <span className="text-[13px] font-semibold" style={{ color: NAVY }}>{quarterLabel(data.quarter)} total saving</span>
          <span className="text-[15px] font-bold" style={{ color: NAVY }}>{money2(n(s.quarter_saving))}</span>
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Tile
          label={`Trigger of ${money2(n(s.quarter_threshold))} reached?`}
          value={s.quarter_met ? 'Yes' : 'Not yet'}
          tone={s.quarter_met ? GREEN : AMBER}
          sub={s.quarter_met ? undefined : `${money2(n(s.quarter_threshold) - n(s.quarter_saving))} still to go`}
        />
        <Tile label="Bonus payable this quarter" value={money2(n(s.quarter_bonus))}
          tone={s.quarter_met ? GREEN : undefined}
          sub={s.quarter_met ? 'Subject to the CFO confirming it' : 'Nothing payable until the trigger is reached'} />
      </div>

      <div className="rounded-xl border p-4" style={{ borderColor: LINE }}>
        <div className="text-[13px] font-bold" style={{ color: NAVY }}>
          For context — in-house handling this quarter<Illustrative />
        </div>
        <div className="mt-2 text-[21px] font-bold" style={{ color: NAVY }}>{money2(n(s.quarter_in_house_saving))}</div>
        <p className="mt-2 text-[13px]" style={{ color: '#6B7280' }}>
          This does <strong>not</strong> count toward the trigger above. It is the separate value of
          keeping BONU matters in-house rather than referring them out.
        </p>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Rate cards, the comparison map and the settings behind every figure
// ---------------------------------------------------------------------------

export function RatesTab({ data, reload }: { data: Overview; reload: () => Promise<void> }) {
  const [form, setForm] = useState({ ...data.settings })
  const [msg, setMsg] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const save = async () => {
    setBusy(true); setErr(null); setMsg(null)
    try {
      const r = await apiFetch<{ mappings_repriced: number }>('/bonu/legal/settings/', {
        method: 'PUT',
        body: JSON.stringify({ ...form, quarterly_bonus_pct: form.quarterly_bonus_pct }),
      })
      setMsg(r.mappings_repriced > 0
        ? `Saved. ${r.mappings_repriced} hourly comparison${r.mappings_repriced === 1 ? '' : 's'} moved to the new standard rate; disbursements were left alone.`
        : 'Saved.')
      await reload()
    } catch (e) { setErr(errText(e, 'Could not save those settings.')) } finally { setBusy(false) }
  }

  const internal = data.tariffs.filter((t) => t.scope === 'internal')
  const external = data.tariffs.filter((t) => t.scope === 'external')

  return (
    <div className="space-y-5">
      <SectionHead
        title="Rates behind every figure"
        sub="The internal tariff, the external panel's tariff, and the map that says which of ours is comparable to which of theirs. Change a rate here and every screen recalculates."
      />

      <div className="rounded-xl border p-4" style={{ borderColor: LINE }}>
        <div className="text-[13px] font-bold" style={{ color: NAVY }}>Thresholds</div>
        <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <Field label="Quarterly minimum trigger (BWP)">
            <Input value={form.quarterly_threshold} inputMode="decimal"
              onChange={(e) => setForm({ ...form, quarterly_threshold: e.target.value })} />
          </Field>
          <Field label="Quarterly bonus rate" hint="0.02 means 2%">
            <Input value={form.quarterly_bonus_pct} inputMode="decimal"
              onChange={(e) => setForm({ ...form, quarterly_bonus_pct: e.target.value })} />
          </Field>
          <Field label="Monthly bonus cap (BWP)">
            <Input value={form.monthly_bonus_cap} inputMode="decimal"
              onChange={(e) => setForm({ ...form, monthly_bonus_cap: e.target.value })} />
          </Field>
          <Field label="External attorney hourly rate (BWP/hr)"
            hint="Drives every hourly comparison. Disbursements keep their own rates.">
            <Input value={form.external_hourly_rate} inputMode="decimal"
              onChange={(e) => setForm({ ...form, external_hourly_rate: e.target.value })} />
          </Field>
          <Field label="Invoice review target (days)">
            <Input value={String(form.sla_days)} inputMode="numeric"
              onChange={(e) => setForm({ ...form, sla_days: Number(e.target.value) || 0 })} />
          </Field>
          <div className="flex items-end"><Btn onClick={save} disabled={busy}>Save thresholds</Btn></div>
        </div>
        {err ? <div className="mt-3"><Note tone="warn">{err}</Note></div> : null}
        {msg ? <div className="mt-3"><Note tone="good">{msg}</Note></div> : null}
      </div>

      <RateCard title="Internal tariff — Alpha Law (ADI-HC-LEGAL-TARIFF-2026-002)" rows={internal} />
      <RateCard title="External panel tariff" rows={external} />

      <MapEditor data={data} reload={reload} />
    </div>
  )
}

/** The comparison map, with the add and edit the rest of the module points at.
 *  A service missing from here is a fee-note line with no panel comparison, and
 *  this is the only place that can be put right. */
function MapEditor({ data, reload }: { data: Overview; reload: () => Promise<void> }) {
  const blank = {
    fee_description: '', internal_unit: 'Flat', internal_rate: '0',
    external_item: '', external_unit: 'Flat rate', external_rate: '0',
    calc_basis: 'flat', is_disbursement: false,
  }
  const [draft, setDraft] = useState<Record<string, string | boolean>>(blank)
  const [editing, setEditing] = useState<string | null>(null)
  const [edit, setEdit] = useState<Record<string, string | boolean>>({})
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const add = async () => {
    setBusy(true); setErr(null)
    try { await writeRow('mappings', draft); setDraft(blank); await reload() }
    catch (e) { setErr(errText(e, 'Could not add that mapping.')) } finally { setBusy(false) }
  }
  const save = async (id: string) => {
    setBusy(true); setErr(null)
    try { await writeRow('mappings', edit, id); setEditing(null); await reload() }
    catch (e) { setErr(errText(e, 'Could not save that mapping.')) } finally { setBusy(false) }
  }
  const remove = async (id: string) => {
    setBusy(true); setErr(null)
    try { await deleteRow('mappings', id); await reload() }
    catch (e) { setErr(errText(e, 'Could not remove that mapping.')) } finally { setBusy(false) }
  }
  const open = (m: Mapping) => {
    setEditing(m.id)
    // is_disbursement stays a real boolean all the way to the server — sent as
    // the string "false" it would arrive truthy and turn a fee into a
    // disbursement, which is then exempt from every rate change.
    setEdit({
      fee_description: m.fee_description, internal_unit: m.internal_unit,
      internal_rate: m.internal_rate, external_item: m.external_item,
      external_unit: m.external_unit, external_rate: m.external_rate,
      calc_basis: m.calc_basis, is_disbursement: m.is_disbursement,
    })
  }

  const form = (v: Record<string, string | boolean>, set: (x: Record<string, string | boolean>) => void) => (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <Field label="Our service" hint="Must match the fee note description exactly">
        <Input value={String(v.fee_description ?? '')}
          onChange={(e) => set({ ...v, fee_description: e.target.value })} />
      </Field>
      <Field label="Our unit">
        <Input value={String(v.internal_unit ?? '')} onChange={(e) => set({ ...v, internal_unit: e.target.value })} />
      </Field>
      <Field label="Our rate (BWP)">
        <Input value={String(v.internal_rate ?? '')} inputMode="decimal"
          onChange={(e) => set({ ...v, internal_rate: e.target.value })} />
      </Field>
      <Field label="Panel item">
        <Input value={String(v.external_item ?? '')} onChange={(e) => set({ ...v, external_item: e.target.value })} />
      </Field>
      <Field label="Panel unit">
        <Input value={String(v.external_unit ?? '')} onChange={(e) => set({ ...v, external_unit: e.target.value })} />
      </Field>
      <Field label="Panel rate (BWP)">
        <Input value={String(v.external_rate ?? '')} inputMode="decimal"
          onChange={(e) => set({ ...v, external_rate: e.target.value })} />
      </Field>
      <Field label="Basis" hint="Flat charges the panel fee once; per hour multiplies it by the hours">
        <Select value={String(v.calc_basis ?? 'flat')} onChange={(e) => set({ ...v, calc_basis: e.target.value })}>
          <option value="flat">Flat</option>
          <option value="per_hour">Per hour</option>
        </Select>
      </Field>
      <Field label="Disbursement?" hint="Kept out of any change to the standard hourly rate">
        <Select value={v.is_disbursement ? 'yes' : 'no'}
          onChange={(e) => set({ ...v, is_disbursement: e.target.value === 'yes' })}>
          <option value="no">No</option>
          <option value="yes">Yes</option>
        </Select>
      </Field>
    </div>
  )

  return (
    <div className="rounded-xl border" style={{ borderColor: LINE }}>
      <div className="px-4 py-3" style={{ borderBottom: `1px solid ${LINE}` }}>
        <div className="text-[13px] font-bold" style={{ color: NAVY }}>Rate comparison map</div>
        <p className="mt-1 text-[12px]" style={{ color: '#6B7280' }}>
          A <strong>flat</strong> basis applies the panel&rsquo;s fee once per line, whatever hours we
          spent. <strong>Per hour</strong> multiplies the panel rate by the hours logged. A fee-note
          service that is not listed here has no comparison &mdash; add it and it counts straight away.
        </p>
      </div>

      <div className="border-b p-4" style={{ borderColor: LINE }}>
        {form(draft, setDraft)}
        <div className="mt-3"><Btn onClick={add} disabled={busy}>Add mapping</Btn></div>
        {err && !editing ? <div className="mt-3"><Note tone="warn">{err}</Note></div> : null}
      </div>

      <Table head={['Our service', 'Our rate', 'Panel item', 'Panel rate', 'Basis', '']}>
        {data.mappings.map((m) => (
          <tr key={m.id} style={trBorder}>
            <td className={td} style={{ color: NAVY, fontWeight: 500 }}>{m.fee_description}</td>
            <td className={tdNum}>
              {money2(n(m.internal_rate))}
              <div className="text-[11px] font-normal" style={{ color: '#9CA3AF' }}>{m.internal_unit}</div>
            </td>
            <td className={td}>
              {m.external_item}
              {m.is_disbursement ? (
                <span className="ml-2 rounded px-1.5 py-0.5 text-[11px]"
                  style={{ background: '#EEF2FF', color: '#3730A3' }}>disbursement</span>
              ) : null}
            </td>
            <td className={tdNum}>
              {money2(n(m.external_rate))}
              <div className="text-[11px] font-normal" style={{ color: '#9CA3AF' }}>{m.external_unit}</div>
            </td>
            <td className={td}>{m.calc_basis === 'per_hour' ? 'Per hour' : 'Flat'}</td>
            <td className={td}>
              <div className="flex gap-1">
                <Btn tone="ghost" onClick={() => open(m)}>Edit</Btn>
                <Btn tone="danger" onClick={() => remove(m.id)} disabled={busy}>Remove</Btn>
              </div>
            </td>
          </tr>
        ))}
      </Table>

      {editing ? (
        <div className="border-t p-4" style={{ borderColor: LINE, background: '#F8FAFC' }}>
          <div className="mb-3 text-[13px] font-bold" style={{ color: NAVY }}>Edit mapping</div>
          {form(edit, setEdit)}
          <div className="mt-3 flex gap-2">
            <Btn onClick={() => save(editing)} disabled={busy}>Save mapping</Btn>
            <Btn tone="ghost" onClick={() => { setEditing(null); setErr(null) }}>Cancel</Btn>
          </div>
          {err ? <div className="mt-3"><Note tone="warn">{err}</Note></div> : null}
        </div>
      ) : null}
    </div>
  )
}

function RateCard({ title, rows }: { title: string; rows: Overview['tariffs'] }) {
  return (
    <div className="rounded-xl border" style={{ borderColor: LINE }}>
      <div className="px-4 py-3 text-[13px] font-bold" style={{ color: NAVY, borderBottom: `1px solid ${LINE}` }}>{title}</div>
      <Table head={['Section', 'Service / item', 'Unit', 'Rate']}>
        {rows.length === 0 ? (
          <tr><td className={td} colSpan={4} style={{ color: '#9CA3AF' }}>No rates on file.</td></tr>
        ) : rows.map((t) => (
          <tr key={t.id} style={trBorder}>
            <td className={td} style={{ color: '#6B7280' }}>{t.section}</td>
            <td className={td} style={{ color: NAVY }}>{t.item}</td>
            <td className={td}>{t.unit}</td>
            <td className={tdNum} style={{ fontWeight: 500 }}>{money2(n(t.rate))}</td>
          </tr>
        ))}
      </Table>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Reports — printed straight from the browser, so there is no second copy of
// the numbers in a generator that can drift from the screen.
// ---------------------------------------------------------------------------

export function ReportTab({ data, onQuarter }: { data: Overview; onQuarter: (q: string) => void }) {
  const s = data.summary
  const monthFees = data.fee_notes.filter((f) => (f.date || '').slice(0, 7) === data.month)
  const qMonths = s.quarter_months.map((m) => m.month)
  const quarterSavings = data.invoice_savings.filter(
    (r) => qMonths.includes((r.date_reviewed || '').slice(0, 7)))

  return (
    <div className="space-y-6">
      <SectionHead
        title="Reports"
        sub="Two reports, generated automatically from the work captured on the other tabs — the Monthly Fee Note and the Quarterly Savings & Bonus Report. Nothing is typed twice. Download each as Word or PDF."
      />

      {/* ── Monthly Fee Note ─────────────────────────────────────────────── */}
      <ReportCard
        title={`Monthly Fee Note — ${monthLabel(data.month)}`}
        subtitle="Bernard Balikani Jr · Claims Legal Officer — Compliance"
        kind="feenote"
        params={{ month: data.month }}
        hint="Covers the month chosen at the top of the page. Change the month there to report another month."
      >
        <ReportHead entity="Alpha Direct Insurance Co. (Pty) Ltd"
          line={`Monthly Fee Note (Internal Legal Services Tariff ADI-HC-LEGAL-TARIFF-2026-002) · ${monthLabel(data.month)}`} />
        <ReportSub>Matter billing</ReportSub>
        <Table head={['Date', 'Client / BONU member', 'Service', 'Unit', 'Rate', 'Qty/Hrs', 'Amount']}>
          {monthFees.length === 0 ? (
            <tr><td className={td} colSpan={7} style={{ color: '#9CA3AF' }}>No matter lines logged this month.</td></tr>
          ) : monthFees.map((f) => (
            <tr key={f.id} style={trBorder}>
              <td className={td}>{f.date}</td><td className={td}>{f.client}</td>
              <td className={td}>{f.description}</td><td className={td}>{f.unit}</td>
              <td className={tdNum}>{money2(n(f.rate))}</td><td className={tdNum}>{f.qty}</td>
              <td className={tdNum} style={{ fontWeight: 600 }}>{money2(n(f.amount))}</td>
            </tr>
          ))}
        </Table>
        <FootRow label="Section 1 total — total matter billing" value={money2(n(s.matter_billing))} />

        <ReportSub>External Finances vs. Internal Costs &amp; Savings</ReportSub>
        <Table head={['Service', 'Basis', 'Internal cost', 'External finance equiv.', 'Saving']}>
          {monthFees.length === 0 ? (
            <tr><td className={td} colSpan={5} style={{ color: '#9CA3AF' }}>No matter lines logged this month.</td></tr>
          ) : monthFees.map((f) => (
            <tr key={f.id} style={trBorder}>
              <td className={td}>{f.description}</td>
              <td className={td} style={{ color: '#6B7280' }}>{f.calc_basis || 'Attorney hourly / flat fee'}</td>
              <td className={tdNum}>{money2(n(f.amount))}</td>
              <td className={tdNum}>{f.external_equivalent === null ? '—' : money2(n(f.external_equivalent))}</td>
              <td className={tdNum} style={{ fontWeight: 600, color: f.saving === null ? '#9CA3AF' : GREEN }}>
                {f.saving === null ? '—' : money2(n(f.saving))}
              </td>
            </tr>
          ))}
        </Table>
        <div className="flex justify-between px-3 py-2 text-[13px] font-semibold"
          style={{ borderTop: `1px solid ${NAVY}`, color: NAVY }}>
          <span>Totals — internal / external / saving</span>
          <span>{money2(n(s.matter_billing))} · {money2(n(s.external_equivalent))} · {money2(n(s.in_house_saving))}</span>
        </div>
        <p className="mt-2 text-[12px]" style={{ color: '#6B7280' }}>
          Percentage saved vs. external finance exposure this month: <strong>{pct(s.pct_saved)}</strong> (illustrative
          — does not affect the contractual bonuses).
        </p>

        <ReportSub>Bonus estimate</ReportSub>
        <div className="text-[13px]" style={{ color: NAVY, fontWeight: 600 }}>
          Estimated Monthly Performance Bonus (capped {money2(n(s.monthly_bonus_cap))}): {money2(n(s.monthly_bonus))}
        </div>
        <p className="mt-1 text-[12px]" style={{ color: '#6B7280' }}>
          Indicative only, per Tariff §7.1 threshold table. Final bonus is confirmed by CFO approval.
        </p>
      </ReportCard>

      {/* ── Quarterly Savings & Bonus Report ─────────────────────────────── */}
      <ReportCard
        title={`Quarterly Savings & Bonus Report — ${quarterLabel(data.quarter)}`}
        subtitle="Alpha Direct Insurance Co. (Pty) Ltd — Claims Legal Office · Bernard Balikani Jr"
        kind="quarterly"
        params={{ quarter: data.quarter }}
        hint="The contractual 2% quarterly bonus, the invoices reduced, and the value delivered."
        right={
          <div className="w-40">
            <Field label="Quarter">
              <Select value={data.quarter} onChange={(e) => onQuarter(e.target.value)}>
                {data.quarters.map((q) => <option key={q} value={q}>{quarterLabel(q)}</option>)}
              </Select>
            </Field>
          </div>
        }
      >
        <ReportSub>Contractual quarterly bonus (Sec. 2 — Invoice Savings Log)</ReportSub>
        <Table head={['Month', 'Saving (BWP)']}>
          {s.quarter_months.map((m) => (
            <tr key={m.month} style={trBorder}>
              <td className={td}>{monthLabel(m.month)}</td>
              <td className={tdNum} style={{ fontWeight: 500 }}>{money2(n(m.saving))}</td>
            </tr>
          ))}
        </Table>
        <FootRow label="Quarterly total saving" value={money2(n(s.quarter_saving))} />
        <div className="mt-2 space-y-1 text-[13px]">
          <Line label={`Minimum trigger (${money2(n(s.quarter_threshold))}) met`} value={s.quarter_met ? 'YES' : 'NO'} />
          <Line label="Quarterly bonus payable (2% of total)" value={money2(n(s.quarter_bonus))} strong />
        </div>

        <ReportSub>Invoices reviewed this quarter</ReportSub>
        <Table head={['Date', 'Ref', 'External attorney', 'Original', 'Agreed', 'Saving']}>
          {quarterSavings.length === 0 ? (
            <tr><td className={td} colSpan={6} style={{ color: '#9CA3AF' }}>No invoices logged this quarter.</td></tr>
          ) : quarterSavings.map((r) => (
            <tr key={r.id} style={trBorder}>
              <td className={td}>{r.date_reviewed}</td><td className={td}>{r.invoice_ref}</td>
              <td className={td}>{r.external_attorney}</td>
              <td className={tdNum}>{money2(n(r.original_amount))}</td>
              <td className={tdNum}>{money2(n(r.agreed_amount))}</td>
              <td className={tdNum} style={{ fontWeight: 600, color: GREEN }}>{money2(n(r.saving))}</td>
            </tr>
          ))}
        </Table>

        <ReportSub>Value delivered this quarter</ReportSub>
        <div className="space-y-1 text-[13px]">
          <Line label="Contractual invoice review saving" value={money2(n(s.quarter_saving))} />
          <Line label="In-house handling saving (illustrative)" value={money2(n(s.quarter_in_house_saving))} />
          <Line label="ADI advisory value (illustrative)" value={money2(n(s.advisory_value))} />
        </div>
        <p className="mt-2 text-[12px]" style={{ color: '#6B7280' }}>
          The full per-client breakdown and quarter summary are in the downloaded report.
        </p>
      </ReportCard>
    </div>
  )
}

/** One report: a header carrying the Word/PDF download buttons, and the preview
 *  below. The download is the authoritative document; the preview shows what it
 *  contains before you download it. */
function ReportCard({
  title, subtitle, kind, params, hint, right, children,
}: {
  title: string; subtitle: string
  kind: 'feenote' | 'quarterly'
  params: { month?: string; quarter?: string }
  hint?: string
  right?: ReactNode
  children: ReactNode
}) {
  const [busy, setBusy] = useState<'docx' | 'pdf' | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const get = async (fmt: 'docx' | 'pdf') => {
    setBusy(fmt); setErr(null)
    try { await downloadReport(kind, fmt, params) }
    catch (e) { setErr(errText(e, 'Could not build that report.')) }
    finally { setBusy(null) }
  }

  return (
    <div className="rounded-xl border" style={{ borderColor: LINE }}>
      <div className="flex flex-wrap items-start justify-between gap-3 border-b p-4" style={{ borderColor: LINE }}>
        <div>
          <h3 className="text-[15px] font-bold" style={{ color: NAVY }}>{title}</h3>
          <p className="mt-0.5 text-[12px]" style={{ color: '#6B7280' }}>{subtitle}</p>
          {hint ? <p className="mt-1 max-w-xl text-[12px]" style={{ color: '#9CA3AF' }}>{hint}</p> : null}
        </div>
        <div className="flex items-end gap-2">
          {right}
          <Btn onClick={() => get('docx')} disabled={busy !== null}>
            {busy === 'docx' ? 'Building…' : 'Download Word'}
          </Btn>
          <Btn tone="ghost" onClick={() => get('pdf')} disabled={busy !== null}>
            {busy === 'pdf' ? 'Building…' : 'Download PDF'}
          </Btn>
        </div>
      </div>
      {err ? <div className="px-4 pt-3"><Note tone="warn">{err}</Note></div> : null}
      <div className="p-4">{children}</div>
    </div>
  )
}

function ReportHead({ entity, line }: { entity: string; line: string }) {
  return (
    <div className="mb-3 border-b pb-3" style={{ borderColor: LINE }}>
      <div className="text-[11px] uppercase tracking-[0.14em]" style={{ color: ORANGE }}>{entity}</div>
      <p className="mt-1 text-[12px]" style={{ color: '#6B7280' }}>{line}</p>
    </div>
  )
}

function ReportSub({ children }: { children: React.ReactNode }) {
  return (
    <h4 className="mt-5 mb-1 text-[13px] font-bold uppercase tracking-wide" style={{ color: NAVY }}>{children}</h4>
  )
}

function FootRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between px-3 py-2 text-[13px] font-semibold"
      style={{ borderTop: `1px solid ${NAVY}`, color: NAVY }}>
      <span>{label}</span><span>{value}</span>
    </div>
  )
}

function Line({ label, value, strong }: { label: string; value: string; strong?: boolean }) {
  return (
    <div className="flex justify-between border-b py-1" style={{ borderColor: LINE }}>
      <span style={{ color: strong ? NAVY : '#374151', fontWeight: strong ? 700 : 400 }}>{label}</span>
      <span style={{ color: NAVY, fontWeight: strong ? 700 : 500 }}>{value}</span>
    </div>
  )
}
