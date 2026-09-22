'use client'

/**
 * /bonu/legal — the in-house legal office's own screen.
 *
 * The Claims Legal Office told the CFO on 18 Aug 2026 that the BONU Legal
 * screens did not carry its daily work. It was right: Omni held the panel
 * firms' bills and nothing about the matters Alpha Law handles itself, nor
 * about the external bills the office argues down before they are paid — the
 * two things the officer is measured on.
 *
 * Five sections, in the order the officer works through them, all off ONE read
 * of /bonu/legal/ so no two panels can be looking at different data. Nothing on
 * this screen posts to the ledger or pays anybody: the bonus figures are
 * calculations, and what is paid stays the CFO's decision.
 */

import { useCallback, useEffect, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { apiFetch } from '@/lib/api'
import { Loader2, Scale } from 'lucide-react'
import { BonuTabs, LINE, NAVY, ORANGE, GREEN, AMBER, money2, Note } from '../_shared'
import { AdvisoryTab, Btn, Field, FeeNoteTab, Input, InvoiceLogTab, Select } from './registers'
import { ExternalTab, QuarterlyTab, RatesTab, ReportTab } from './views'
import { Overview, errText, monthLabel, n, pct, quarterLabel } from './shared'

const SECTIONS = [
  { key: 'dashboard', label: 'Summary' },
  { key: 'feenote', label: 'Fee note' },
  { key: 'invoices', label: 'Invoice savings' },
  { key: 'external', label: 'vs external panel' },
  { key: 'advisory', label: 'Internal advisory' },
  { key: 'quarterly', label: 'Quarterly bonus' },
  { key: 'rates', label: 'Rates' },
  { key: 'reports', label: 'Reports' },
]

export default function BonuLegalPage() {
  const [data, setData] = useState<Overview | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [section, setSection] = useState('dashboard')
  const [month, setMonth] = useState<string | null>(null)
  const [quarter, setQuarter] = useState<string | null>(null)

  const load = useCallback(async () => {
    const qs = new URLSearchParams()
    if (month) qs.set('month', month)
    if (quarter) qs.set('quarter', quarter)
    try {
      const r = await apiFetch<Overview>(`/bonu/legal/${qs.toString() ? `?${qs}` : ''}`)
      setData(r)
      // Clear the error on a success — a stale "could not load" sitting above a
      // full screen is its own bug (Fable, 18 Aug 2026).
      setLoadError(null)
    } catch (e) {
      setLoadError(errText(e, 'Could not load the legal office screen.'))
    }
  }, [month, quarter])

  useEffect(() => { load() }, [load])

  if (loadError && !data) {
    return (
      <>
        <TopBar title="BONU" />
        <div className="p-6"><Note tone="warn" title="Could not open this screen">{loadError}</Note></div>
      </>
    )
  }
  if (!data) {
    return (
      <>
        <TopBar title="BONU" />
        <div className="flex items-center gap-2 p-6 text-[13px]" style={{ color: '#6B7280' }}>
          <Loader2 className="h-4 w-4 animate-spin" /> Loading the legal office…
        </div>
      </>
    )
  }

  return (
    <>
      <TopBar title="BONU" />
      <div className="space-y-5 p-6">
        <div className="print:hidden"><BonuTabs active="/bonu/legal" /></div>

        <div className="flex flex-wrap items-start justify-between gap-4 print:hidden">
          <div className="flex items-start gap-3">
            <div className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg"
              style={{ background: NAVY }}>
              <Scale className="h-5 w-5" style={{ color: ORANGE }} />
            </div>
            <div>
              <h1 className="text-[21px] font-bold leading-tight" style={{ color: NAVY }}>Legal office</h1>
              <p className="mt-1 max-w-2xl text-[13px]" style={{ color: '#6B7280' }}>
                Matters Alpha Law handled itself, and external bills reviewed and reduced before
                they were paid.
              </p>
            </div>
          </div>
          <div className="w-52">
            <Field label="Reporting month">
              <Select value={data.month} onChange={(e) => { setMonth(e.target.value); setQuarter(null) }}>
                {data.months.map((m) => <option key={m} value={m}>{monthLabel(m)}</option>)}
              </Select>
            </Field>
          </div>
        </div>

        {loadError ? <div className="print:hidden"><Note tone="warn">{loadError}</Note></div> : null}

        <div className="flex flex-wrap gap-1 border-b print:hidden" style={{ borderColor: LINE }}>
          {SECTIONS.map((s) => (
            <button key={s.key} onClick={() => setSection(s.key)}
              className="px-3 py-2 text-[13px] font-medium transition-colors duration-150"
              style={{
                color: section === s.key ? NAVY : '#6B7280',
                borderBottom: `2px solid ${section === s.key ? ORANGE : 'transparent'}`,
              }}>
              {s.label}
            </button>
          ))}
        </div>

        <Card>
          <CardContent className="p-5">
            {section === 'dashboard' ? <Summary data={data} reload={load} /> : null}
            {section === 'feenote' ? <FeeNoteTab data={data} reload={load} /> : null}
            {section === 'invoices' ? <InvoiceLogTab data={data} reload={load} /> : null}
            {section === 'external' ? <ExternalTab data={data} /> : null}
            {section === 'advisory' ? <AdvisoryTab data={data} reload={load} /> : null}
            {section === 'quarterly' ? <QuarterlyTab data={data} onQuarter={setQuarter} /> : null}
            {section === 'rates' ? <RatesTab data={data} reload={load} /> : null}
            {section === 'reports' ? <ReportTab data={data} onQuarter={setQuarter} /> : null}
          </CardContent>
        </Card>
      </div>
    </>
  )
}

// ---------------------------------------------------------------------------
// The summary — the five sections of the officer's month, in one read
// ---------------------------------------------------------------------------

function Band({ n: num, title, basis, tag }: { n: string; title: string; basis?: string; tag?: string }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="flex h-5 w-5 items-center justify-center rounded text-[11px] font-bold"
        style={{ background: NAVY, color: '#fff' }}>{num}</span>
      <h3 className="text-[13px] font-bold uppercase tracking-wide" style={{ color: NAVY }}>{title}</h3>
      {basis ? <span className="text-[12px]" style={{ color: '#6B7280' }}>({basis})</span> : null}
      {tag ? (
        <span className="rounded px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide"
          style={{ background: '#FEF3C7', color: '#92400E' }}>{tag}</span>
      ) : null}
    </div>
  )
}

function Tile({ label, value, tone, sub }: { label: string; value: string; tone?: string; sub?: string }) {
  return (
    <div className="rounded-xl border p-4" style={{ borderColor: LINE }}>
      <div className="text-[11px] uppercase tracking-wide" style={{ color: '#6B7280' }}>{label}</div>
      <div className="mt-1 text-[21px] font-bold leading-none" style={{ color: tone || NAVY }}>{value}</div>
      {sub ? <div className="mt-1 text-[12px] leading-snug" style={{ color: '#6B7280' }}>{sub}</div> : null}
    </div>
  )
}

function Summary({ data, reload }: { data: Overview; reload: () => Promise<void> }) {
  const s = data.summary
  const [bonus, setBonus] = useState(s.monthly_bonus_entered ?? '')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)

  useEffect(() => { setBonus(s.monthly_bonus_entered ?? ''); setMsg(null); setErr(null) },
    [s.monthly_bonus_entered, data.month])

  const saveBonus = async () => {
    setBusy(true); setErr(null); setMsg(null)
    try {
      const r = await apiFetch<{ applied: string; capped: boolean }>('/bonu/legal/monthly-bonus/', {
        method: 'PUT',
        body: JSON.stringify({ month: data.month, amount: bonus }),
      })
      setMsg(r.capped
        ? `Recorded. Held to the cap, so ${money2(n(r.applied))} is what counts.`
        : 'Recorded.')
      await reload()
    } catch (e) { setErr(errText(e, 'Could not record that bonus figure.')) } finally { setBusy(false) }
  }

  return (
    <div className="space-y-7">
      <div className="space-y-3">
        <Band n="1" title="BONU Portfolio — Matter Billing" basis="Monthly Performance Bonus basis" />
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <Tile label="Total matter billing this month" value={money2(n(s.matter_billing))} />
          <div className="rounded-xl border p-4" style={{ borderColor: LINE }}>
            <div className="text-[11px] uppercase tracking-wide" style={{ color: '#6B7280' }}>Estimated monthly performance bonus</div>
            <div className="mt-2 flex gap-2">
              <Input value={bonus} inputMode="decimal" placeholder="0.00"
                onChange={(e) => setBonus(e.target.value)} />
              <Btn onClick={saveBonus} disabled={busy}>Save</Btn>
            </div>
            <div className="mt-2 text-[12px] leading-snug" style={{ color: '#6B7280' }}>
              Capped at {money2(n(s.monthly_bonus_cap))} · per Tariff §7.1 threshold table (enter manually
              — final bonus confirmed by CFO).
            </div>
            {err ? <div className="mt-2 text-[12px]" style={{ color: '#DC2626' }}>{err}</div> : null}
            {msg ? <div className="mt-2 text-[12px]" style={{ color: GREEN }}>{msg}</div> : null}
          </div>
          <Tile label="Counting toward the bonus (after cap)" value={money2(n(s.monthly_bonus))} tone={GREEN} />
        </div>
      </div>

      <div className="space-y-3">
        <Band n="2" title="Invoice Review &amp; Reduction" basis="Quarterly 2% Bonus basis" />
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Tile label="Verified invoice saving this month" value={money2(n(s.invoice_saving_month))} tone={GREEN} />
          <Tile label={`Quarterly total saving to date (${quarterLabel(data.quarter)})`} value={money2(n(s.quarter_saving))} tone={GREEN} />
          <Tile label="Quarterly minimum met?" value={s.quarter_met ? 'YES' : 'NO'}
            tone={s.quarter_met ? GREEN : AMBER}
            sub={`Trigger: ${money2(n(s.quarter_threshold))}`} />
          <Tile label="Quarterly bonus payable" value={money2(n(s.quarter_bonus))}
            tone={s.quarter_met ? GREEN : undefined}
            sub={`${pct(n(data.settings.quarterly_bonus_pct))} of quarterly saving, once trigger met`} />
        </div>
      </div>

      <div className="space-y-3">
        <Band n="3" title="Savings vs External Attorney" tag="Illustrative" />
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Tile label="Internal amount billed" value={money2(n(s.matter_billing))} />
          <Tile label="External equivalent cost" value={money2(n(s.external_equivalent))} />
          <Tile label="Saving from handling in-house" value={money2(n(s.in_house_saving))} tone={GREEN} />
          <Tile label="% saved vs external" value={pct(s.pct_saved)} tone={GREEN}
            sub={s.unmapped_lines_month > 0
              ? `${s.unmapped_lines_month} line(s) this month have no panel match and are left out`
              : undefined} />
        </div>
      </div>

      <div className="space-y-3">
        <Band n="4" title="Internal ADI Advisory" tag="Illustrative" />
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <Tile label="Hours logged this month" value={Number(s.advisory_hours).toFixed(2)} />
          <Tile label="Estimated value delivered" value={money2(n(s.advisory_value))} tone={GREEN}
            sub={`At external rate ${money2(n(data.settings.external_hourly_rate))}/hr`} />
        </div>
      </div>

      <div className="rounded-xl border p-5" style={{ borderColor: LINE, background: '#F8FAFC' }}>
        <Band n="5" title={`Total value delivered to the business — ${monthLabel(data.month)}`} />
        <div className="mt-3 space-y-1 text-[13px]">
          <Row label="Invoice review savings" value={money2(n(s.invoice_saving_month))} note="contractual" />
          <Row label="In-house handling saving" value={money2(n(s.in_house_saving))} note="illustrative" />
          <Row label="ADI advisory value" value={money2(n(s.advisory_value))} note="illustrative" />
          <div className="flex items-center justify-between pt-3" style={{ borderTop: `2px solid ${NAVY}` }}>
            <span className="text-[14px] font-bold" style={{ color: NAVY }}>Total value delivered</span>
            <span className="text-[19px] font-bold" style={{ color: NAVY }}>{money2(n(s.total_value))}</span>
          </div>
        </div>
      </div>
    </div>
  )
}

function Row({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div className="flex items-center justify-between border-b py-1.5" style={{ borderColor: LINE }}>
      <span style={{ color: '#374151' }}>
        {label} <span className="text-[11px]" style={{ color: '#9CA3AF' }}>({note})</span>
      </span>
      <span style={{ color: NAVY, fontWeight: 500 }}>{value}</span>
    </div>
  )
}
