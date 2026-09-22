'use client'

/**
 * /bonu/legal-bills — where legal bills are entered, and the client spend cap.
 *
 * Part 2 of Kelvin Kimani's spec (9 Sep 2026). Every client carries an 80,000
 * legal-spend ceiling ACROSS ALL their matters, and that ceiling is only as
 * good as the bills feeding it — so this is the one screen where a legal bill
 * is captured, tied to the matter (and through it the client) it belongs to,
 * and counted.
 *
 * Three sections, in the order the work actually happens:
 *   Capture      enter the bill; if it arrived with a name and no number, the
 *                screen finds the client or asks which one it is
 *   Bills        the register, with the unallocated exceptions FIRST
 *   Client caps  who is approaching 80,000, before a new bill tips them over
 *
 * NOTHING HERE MOVES MONEY. Capturing a bill, allocating it and marking it
 * paid are records. Every real payment is released by the CFO in the FNB app
 * with two-factor, and nothing on this screen posts to the ledger.
 */

import { Fragment, useCallback, useEffect, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { apiFetch } from '@/lib/api'
import { localYmd } from '@/lib/utils'
import { AlertTriangle, Loader2, ReceiptText, Scale, Search, Upload } from 'lucide-react'
import {
  AMBER, BonuTabs, Field, GREEN, LINE, NAVY, Note, ORANGE, RED, Stat, Table, money2, td,
  tdNum, trBorder,
} from '../_shared'

// ---------------------------------------------------------------------------
// Shapes. Every money value crosses the wire as a STRING and is only turned
// into a number to DRAW it — a Decimal round-tripped through a JavaScript
// number is how a figure comes to disagree with the one beside it.
// ---------------------------------------------------------------------------

interface Alloc {
  id: string; case_id: string; case_ref: string
  client_id: string; client: string; amount: string
}
interface Bill {
  id: string; source: string; source_label: string; firm: string; firm_id: string
  reference: string; bill_date: string; received_on: string; amount: string
  discount: string; amount_paid: string; paid_on: string; outstanding: string
  source_row: number | null
  stage: string; stage_label: string
  allocation_state: string; allocation_label: string
  billed_client_name: string; note: string; captured_by: string
  allocations: Alloc[]; allocated_total: string; why_unallocated?: string
}
interface Opt { value: string; label: string }
interface BillsResp {
  bills: Bill[]; shown: number; total: number; unallocated_count: number
  money: { billed: string; paid: string; outstanding: string; discount: string
           paid_without_date: number; bills: number }
  meta: {
    firms: { id: string; name: string }[]
    sources: Opt[]; stages: Opt[]
    cap: string; amber_at: string; cap_blocks_capture: boolean
  }
}
interface Cap {
  total: string; tier: 'clear' | 'amber' | 'red'; label: string
  amber_at: string; cap: string; headroom: string
}
interface ClientRow {
  id: string; membership_no: string; name: string; district: string
  status: string; status_label: string; on_current_list: boolean
  cap: Cap; matters?: number
}
interface CapResp {
  clients: ClientRow[]; cap: string; amber_at: string; cap_blocks_capture: boolean
  counts: { red: number; amber: number; clear: number }
  unallocated: { bills: number; amount: string; note: string }
}
interface MatchOut {
  outcome: 'exact' | 'ambiguous' | 'none'; via: string; note?: string
  candidates: { id: string; membership_no: string; name: string; district: string }[]
}

const n = (s: string | null | undefined) => (s == null || s === '' ? null : Number(s))
// Local calendar date, NOT toISOString(): between midnight and 02:00
// Botswana time the UTC form returns yesterday, so a claim or a bill
// opened at 00:30 would be dated the day before it happened.
const today = () => localYmd()

/** The one place a cap tier becomes a colour, so no two panels disagree. */
const TIER_TONE: Record<string, string> = { clear: GREEN, amber: AMBER, red: RED }

const SECTIONS = [
  { key: 'capture', label: 'Capture a bill' },
  { key: 'bills', label: 'Bills' },
  { key: 'caps', label: 'Client caps' },
]

const inputCls = 'w-full rounded-lg px-2.5 py-2 text-[13px]'
const inputStyle = { border: `1px solid ${LINE}`, background: '#fff' }

function CapPill({ cap }: { cap: Cap }) {
  if (cap.tier === 'clear') {
    return (
      <span className="text-[12px]" style={{ color: '#6B7280' }}>
        {money2(n(cap.total))} of {money2(n(cap.cap))}
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-semibold"
      style={{ background: cap.tier === 'red' ? '#FEF2F2' : '#FFFBEB',
               color: TIER_TONE[cap.tier] }}>
      <AlertTriangle className="h-3 w-3" />
      {cap.label} · {money2(n(cap.total))}
    </span>
  )
}

export default function BonuLegalBillsPage() {
  const [section, setSection] = useState('capture')
  const [d, setD] = useState<BillsResp | null>(null)
  const [caps, setCaps] = useState<CapResp | null>(null)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState('')
  const [openId, setOpenId] = useState('')

  // Capture form
  const [source, setSource] = useState('external')
  const [firmId, setFirmId] = useState('')
  const [reference, setReference] = useState('')
  const [billDate, setBillDate] = useState(today())
  const [receivedOn, setReceivedOn] = useState(today())
  const [amount, setAmount] = useState('')
  const [clientName, setClientName] = useState('')
  const [note, setNote] = useState('')
  const [match, setMatch] = useState<MatchOut | null>(null)
  const [dupWarning, setDupWarning] = useState('')
  const [reading, setReading] = useState(false)
  const [readMsg, setReadMsg] = useState('')

  // Allocation editor, for the bill currently opened in the register
  const [allocRows, setAllocRows] = useState<{ case_ref: string; case_id: string; amount: string }[]>([])
  const [caseQuery, setCaseQuery] = useState('')
  const [caseHits, setCaseHits] = useState<{ id: string; case_ref: string; client: string }[]>([])
  const [learnAlias, setLearnAlias] = useState(true)

  const load = useCallback(() => {
    apiFetch<BillsResp>('/bonu/legal/bills/')
      .then((x) => { setD(x); setErr('') })
      .catch(() => setErr('Could not load the bill register.'))
    apiFetch<CapResp>('/bonu/legal/cap/')
      .then(setCaps)
      .catch(() => { /* the board is a read; the register above is the primary */ })
  }, [])
  useEffect(load, [load])

  // Reset the allocation editor when a different bill is opened, so lines
  // typed against one bill can never be submitted onto another.
  useEffect(() => {
    const b = d?.bills.find((x) => x.id === openId)
    setAllocRows(b ? b.allocations.map((a) => ({ case_ref: a.case_ref, case_id: a.case_id,
                                                 amount: a.amount })) : [])
    setCaseQuery(''); setCaseHits([])
  }, [openId, d])

  const findCases = async (q: string) => {
    setCaseQuery(q)
    if (q.trim().length < 2) { setCaseHits([]); return }
    try {
      const r = await apiFetch<{ cases: { id: string; case_ref: string; client: string }[] }>(
        `/bonu/cases/?member_ref=${encodeURIComponent(q.trim())}`)
      setCaseHits(r.cases.slice(0, 8))
    } catch { setCaseHits([]) }
  }

  // Read the figures off the document instead of typing them. This SAVES
  // NOTHING: it fills the form, and recording the bill is still the deliberate
  // step below, so the duplicate guard and the 80,000 ceiling both still run.
  const readBill = async (file: File | null) => {
    if (!file) return
    setReading(true); setReadMsg(''); setErr(''); setMsg('')
    try {
      const fd = new FormData()
      fd.append('file', file)
      const r = await apiFetch<{ ok: boolean; needs_manual?: boolean; message?: string
                                 values?: { reference?: string; bill_date?: string
                                            amount?: string } }>(
        '/bonu/legal/bills/read/', { method: 'POST', body: fd })
      const v = r.values || {}
      if (v.reference) setReference(v.reference)
      if (v.bill_date) setBillDate(v.bill_date)
      if (v.amount) setAmount(v.amount)
      setReadMsg(r.message || 'Read the document. Check the figures below.')
    } catch (e) {
      setReadMsg(e instanceof Error ? e.message
        : 'Could not read that file. Type the bill in below.')
    } finally { setReading(false) }
  }

  const capture = async (confirmDuplicate = false) => {
    setBusy('new'); setErr(''); setMsg(''); if (!confirmDuplicate) setDupWarning('')
    try {
      const r = await apiFetch<{ bill: Bill; match?: MatchOut; cap_breaches?: unknown[] }>(
        '/bonu/legal/bills/', {
          method: 'POST',
          body: JSON.stringify({
            source, firm_id: source === 'external' ? firmId : '', reference: reference.trim(),
            bill_date: billDate, received_on: receivedOn, amount: amount.trim(),
            billed_client_name: clientName.trim(), note: note.trim(),
            confirm_duplicate: confirmDuplicate,
          }),
        })
      setMatch(r.match || null)
      setDupWarning('')
      setMsg(r.bill.allocation_state === 'allocated'
        ? `Bill ${r.bill.reference} recorded and allocated to ${r.bill.allocations[0]?.client}.`
        : `Bill ${r.bill.reference} recorded, but not yet tied to a client — it is in the `
          + `exceptions on the Bills tab until somebody assigns it.`)
      setReference(''); setAmount(''); setClientName(''); setNote('')
      load()
      if (r.bill.allocation_state !== 'allocated') setSection('bills')
    } catch (e) {
      // A suspected duplicate comes back as a refusal carrying the bill we
      // already hold, so the person can look at it and then decide.
      const m = e instanceof Error ? e.message : 'Could not record the bill.'
      if (/already has a bill/i.test(m)) setDupWarning(m); else setErr(m)
    } finally { setBusy('') }
  }

  const saveAllocation = async (bill: Bill) => {
    setBusy(bill.id); setErr(''); setMsg('')
    try {
      const r = await apiFetch<{ bill: Bill; alias?: { ok: boolean; reason?: string } }>(
        `/bonu/legal/bills/${bill.id}/allocate/`, {
          method: 'POST',
          body: JSON.stringify({
            allocations: allocRows.map((x) => ({ case_id: x.case_id, amount: x.amount })),
            ...(learnAlias && bill.billed_client_name
              ? { learn_alias: bill.billed_client_name } : {}),
          }),
        })
      setMsg(r.alias && !r.alias.ok
        ? `Bill allocated. The name was not learned: ${r.alias.reason}`
        : 'Bill allocated.'
          + (r.alias?.ok ? ' The spelling is remembered, so the next one matches itself.' : ''))
      setOpenId('')
      load()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not allocate the bill.')
    } finally { setBusy('') }
  }

  const setStage = async (bill: Bill, stage: string) => {
    setBusy(bill.id + 'st')
    try {
      await apiFetch(`/bonu/legal/bills/${bill.id}/stage/`, {
        method: 'POST', body: JSON.stringify({ stage }),
      })
      load()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not update the bill.')
    } finally { setBusy('') }
  }

  if (!d) {
    return (
      <>
        <TopBar title="BONU — legal bills" />
        <div className="p-6">
          <BonuTabs active="/bonu/legal-bills" />
          <div className="mt-5">
            {err ? <Note tone="warn" title="Not loaded">{err}</Note> : (
              <div className="flex items-center gap-2 text-[13px]" style={{ color: '#6B7280' }}>
                <Loader2 className="h-4 w-4 animate-spin" style={{ color: ORANGE }} />
                Loading the bill register…
              </div>
            )}
          </div>
        </div>
      </>
    )
  }

  const m = d.meta
  // Exceptions first. A register sorted by date buries the only rows that
  // need a person, and an unallocated bill is money in no client total.
  const ordered = [...d.bills].sort((a, b) =>
    (a.allocation_state === 'unallocated' ? 0 : 1) - (b.allocation_state === 'unallocated' ? 0 : 1))
  const allocSum = allocRows.reduce((t, r) => t + (Number(r.amount) || 0), 0)

  return (
    <>
      <TopBar title="BONU — legal bills" />
      <div className="space-y-5 p-6">
        <BonuTabs active="/bonu/legal-bills" />

        <div className="flex items-start gap-3">
          <div className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg"
            style={{ background: NAVY }}>
            <ReceiptText className="h-5 w-5" style={{ color: ORANGE }} />
          </div>
          <div>
            <h1 className="text-[21px] font-bold leading-tight" style={{ color: NAVY }}>
              Legal bills &amp; client caps
            </h1>
            <p className="mt-1 max-w-3xl text-[13px]" style={{ color: '#6B7280' }}>
              Every bill is tied to a matter, and a matter belongs to a client — so a client
              running total builds itself against the {money2(n(m.cap))} ceiling. Amber from{' '}
              {money2(n(m.amber_at))}. Recording a bill here pays nobody; the CFO releases every
              payment in the bank.
            </p>
          </div>
        </div>

        {msg ? <Note tone="good">{msg}</Note> : null}
        {err ? <Note tone="warn" title="Check the form">{err}</Note> : null}

        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <Card><CardContent className="p-4">
            <Stat label="Bills on file" value={String(d.total)} />
          </CardContent></Card>
          <Card><CardContent className="p-4">
            <Stat label="Unallocated" value={String(d.unallocated_count)}
              tone={d.unallocated_count ? RED : undefined} sub="in no client total" />
          </CardContent></Card>
          <Card><CardContent className="p-4">
            <Stat label="At the cap" value={String(caps?.counts.red ?? 0)}
              tone={caps?.counts.red ? RED : undefined} sub={`${money2(n(m.cap))} reached`} />
          </CardContent></Card>
          <Card><CardContent className="p-4">
            <Stat label="Approaching" value={String(caps?.counts.amber ?? 0)}
              tone={caps?.counts.amber ? AMBER : undefined} sub={`from ${money2(n(m.amber_at))}`} />
</CardContent></Card>
        </div>

        {/* What the lawyers billed and what has actually left the bank. Every
            figure here is added over every bill the filters match, never over
            the rows on screen — a total that changes as you page is how a
            reconciliation stops meaning anything. The count below each figure
            comes from the same set as the figure, so the strip always adds up
            the list underneath it. */}
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <Card><CardContent className="p-4">
            <Stat label="Billed by the firms" value={money2(n(d.money.billed))}
              sub={`${d.money.bills} fee notes`} />
          </CardContent></Card>
          <Card><CardContent className="p-4">
            <Stat label="Paid to the firms" value={money2(n(d.money.paid))}
              sub={(n(d.money.discount) ?? 0) > 0
                ? `after ${money2(n(d.money.discount))} of agreed discounts` : 'cash out'} />
          </CardContent></Card>
          <Card><CardContent className="p-4">
            <Stat label="Still owed" value={money2(n(d.money.outstanding))}
              tone={(n(d.money.outstanding) ?? 0) > 0 ? AMBER : undefined}
              sub="billed less discounts and payments" />
          </CardContent></Card>
          <Card><CardContent className="p-4">
            <Stat label="Payments with no date"
              value={String(d.money.paid_without_date)}
              tone={d.money.paid_without_date ? AMBER : undefined}
              sub="cannot be placed in a month" />
          </CardContent></Card>
        </div>

        <div className="flex flex-wrap gap-1 border-b" style={{ borderColor: LINE }}>
          {SECTIONS.map((s) => (
            <button key={s.key} onClick={() => setSection(s.key)}
              className="px-3 py-2 text-[13px] font-medium transition-colors duration-150"
              style={{
                color: section === s.key ? NAVY : '#6B7280',
                borderBottom: `2px solid ${section === s.key ? ORANGE : 'transparent'}`,
              }}>
              {s.label}
              {s.key === 'bills' && d.unallocated_count ? (
                <span className="ml-1.5 rounded px-1 text-[10px] font-bold"
                  style={{ background: '#FEF2F2', color: RED }}>{d.unallocated_count}</span>
              ) : null}
            </button>
          ))}
        </div>

        {/* ---------------- Capture ---------------- */}
        {section === 'capture' ? (
          <Card><CardContent className="p-4">
            <div className="mb-3 flex items-center gap-2 text-[14px] font-bold" style={{ color: NAVY }}>
              <Scale className="h-4 w-4" style={{ color: ORANGE }} /> Record a legal bill
            </div>

            {/* Reading the bill is optional and fills the form only. The firm
                and the client are deliberately never read off the document:
                the firm comes from the panel, and the cap is totalled on a
                real client link, so a guessed name is worse than a blank. */}
            <div className="mb-4 rounded-lg p-3" style={{ background: '#F8FAFC',
              border: `1px solid ${LINE}` }}>
              <div className="text-[12px]" style={{ color: '#6B7280' }}>
                Have the bill as a file? Omni can read the <b>bill number, date and
                amount</b> off it so you do not have to type them. You still choose the
                firm and say who the client is. Nothing is saved until you press
                <b> Record bill</b>.
              </div>
              <label className="mt-2 inline-flex cursor-pointer items-center gap-2 rounded-lg px-4 py-2 text-[13px] font-semibold text-white"
                style={{ background: ORANGE }}>
                {reading ? <Loader2 className="h-4 w-4 animate-spin" />
                  : <Upload className="h-4 w-4" />}
                {reading ? 'Reading…' : 'Read it off the bill'}
                <input aria-label="Bill document to read"
                  type="file" accept=".xlsx,.xlsm,.xls,.csv,.pdf" className="hidden"
                  disabled={reading}
                  onChange={(e) => readBill(e.target.files?.[0] ?? null)} />
              </label>
              {readMsg ? (
                <div className="mt-2 text-[12px]" style={{ color: NAVY }}>{readMsg}</div>
              ) : null}
            </div>

            <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
              <Field label="Who billed us *">
                {(id) => (
                <select id={id} value={source} onChange={(e) => setSource(e.target.value)}
                  className={inputCls} style={inputStyle}>
                  {m.sources.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
                )}
              </Field>
              {source === 'external' ? (
                <Field label="Law firm *">
                  {(id) => (
                  <select id={id} value={firmId} onChange={(e) => setFirmId(e.target.value)}
                    className={inputCls} style={inputStyle}>
                    <option value="">Choose the firm…</option>
                    {m.firms.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
                  </select>
                  )}
                </Field>
              ) : (
                <div className="flex items-end pb-2 text-[12px]" style={{ color: '#6B7280' }}>
                  In-house work — no external firm on this bill.
                </div>
              )}
              <Field label="Bill / invoice number *">
                {(id) => (
                <input id={id} value={reference} onChange={(e) => setReference(e.target.value)}
                  placeholder="e.g. INV-1042" className={inputCls} style={inputStyle} />
                )}
              </Field>
              <Field label="Bill date *">
                {(id) => (
                <input id={id} type="date" value={billDate} onChange={(e) => setBillDate(e.target.value)}
                  className={inputCls} style={inputStyle} />
                )}
              </Field>
              <Field label="Date we received it">
                {(id) => (
                <input id={id} type="date" value={receivedOn} onChange={(e) => setReceivedOn(e.target.value)}
                  className={inputCls} style={inputStyle} />
                )}
              </Field>
              <Field label="Amount (BWP) *">
                {(id) => (
                <input id={id} value={amount} onChange={(e) => setAmount(e.target.value)}
                  placeholder="0.00" className={inputCls} style={inputStyle} />
                )}
              </Field>
              <div className="md:col-span-2">
                <Field label="Client name as the firm wrote it">
                  {(id) => (
                  <input id={id} value={clientName} onChange={(e) => setClientName(e.target.value)}
                    placeholder="if the bill shows a name and not our number"
                    className={inputCls} style={inputStyle} />
                  )}
                </Field>
              </div>
              <Field label="Note">
                {(id) => (
                <input id={id} value={note} onChange={(e) => setNote(e.target.value)}
                  placeholder="optional" className={inputCls} style={inputStyle} />
                )}
              </Field>
            </div>

            {dupWarning ? (
              <div className="mt-3">
                <Note tone="warn" title="This looks like a bill we already have">
                  {dupWarning}
                  <div className="mt-2">
                    <button onClick={() => capture(true)} disabled={busy === 'new'}
                      className="rounded-lg px-3 py-1.5 text-[12px] font-semibold text-white disabled:opacity-50"
                      style={{ background: RED }}>
                      Record it anyway — it is a different bill
                    </button>
                  </div>
                </Note>
              </div>
            ) : null}

            {match && match.outcome !== 'exact' ? (
              <div className="mt-3">
                <Note tone="warn" title={match.outcome === 'ambiguous'
                  ? 'More than one client could be meant' : 'No client matched that name'}>
                  {match.note || (match.outcome === 'ambiguous'
                    ? 'A name on its own is not proof of who it is, so the bill was left for '
                      + 'you to assign on the Bills tab. Candidates:'
                    : 'The bill is in the exceptions on the Bills tab. Assign it there, and '
                      + 'the spelling will be remembered for next time.')}
                  {match.candidates.length ? (
                    <ul className="mt-1.5 space-y-0.5">
                      {match.candidates.map((c) => (
                        <li key={c.id}>· {c.name} — {c.membership_no}
                          {c.district ? ` (${c.district})` : ''}</li>
                      ))}
                    </ul>
                  ) : null}
                </Note>
              </div>
            ) : null}

            <div className="mt-3 flex items-center justify-between gap-3">
              <div className="text-[12px]" style={{ color: '#6B7280' }}>
                No money moves. {m.cap_blocks_capture
                  ? `The hard ceiling is ON: a bill taking a client past ${money2(n(m.cap))} is refused.`
                  : `Passing ${money2(n(m.cap))} turns the client red here; it does not block the bill.`}
              </div>
              <button onClick={() => capture(false)} disabled={busy === 'new'}
                className="flex items-center gap-2 rounded-lg px-4 py-2 text-[13px] font-semibold text-white transition-transform duration-150 hover:-translate-y-px disabled:opacity-50"
                style={{ background: NAVY }}>
                {busy === 'new' ? <Loader2 className="h-4 w-4 animate-spin" />
                  : <ReceiptText className="h-4 w-4" style={{ color: ORANGE }} />}
                Record bill
              </button>
            </div>
          </CardContent></Card>
        ) : null}

        {/* ---------------- Bills ---------------- */}
        {section === 'bills' ? (
          <Card><CardContent className="p-0">
            <Table head={['Bill', 'Billed by', 'Date', 'Billed', 'Paid', 'Outstanding',
                          'Allocated to', 'State', 'Stage']}>
              {ordered.map((b) => (
                <Fragment key={b.id}>
                  <tr style={trBorder}>
                    <td className={td}>
                      <button aria-label={`Open the detail for bill ${b.reference}`}
                        aria-expanded={openId === b.id}
                        onClick={() => setOpenId(openId === b.id ? '' : b.id)}
                        className="font-semibold underline-offset-2 hover:underline"
                        style={{ color: NAVY }}>{b.reference}</button>
                    </td>
                    <td className={td}>{b.firm}</td>
                    <td className={td}>{b.bill_date}</td>
                    <td className={tdNum}>{money2(n(b.amount))}</td>
                    <td className={tdNum}>
                      {money2(n(b.amount_paid))}
                      {(n(b.amount_paid) ?? 0) > 0 && !b.paid_on ? (
                        <div className="text-[10px] font-semibold" style={{ color: AMBER }}
                          title="The register recorded this payment with no readable date, so it cannot be placed in a month.">
                          no date
                        </div>
                      ) : null}
                    </td>
                    <td className={tdNum}
                      style={{ color: (n(b.outstanding) ?? 0) > 0 ? RED : '#6B7280' }}>
                      {money2(n(b.outstanding))}
                    </td>
                    <td className={td}>
                      {b.allocations.length
                        ? b.allocations.map((a) => (
                          <div key={a.id}>{a.case_ref}
                            {a.client ? ` · ${a.client}` : ' · no client'}
                            {b.allocations.length > 1 ? ` — ${money2(n(a.amount))}` : ''}</div>
                        ))
                        : <span style={{ color: '#9CA3AF' }}>
                          {b.billed_client_name || 'nobody yet'}</span>}
                    </td>
                    <td className={td}>
                      <span className="rounded px-1.5 py-0.5 text-[11px] font-semibold"
                        style={{ background: b.allocation_state === 'unallocated' ? '#FEF2F2' : '#F3F4F6',
                                 color: b.allocation_state === 'unallocated' ? RED : GREEN }}>
                        {b.allocation_label}
                      </span>
                    </td>
                    <td className={td}>
                      <select aria-label={`Stage of bill ${b.reference}`}
                        value={b.stage} onChange={(e) => setStage(b, e.target.value)}
                        disabled={busy === b.id + 'st'}
                        className="rounded px-2 py-1 text-[12px]" style={inputStyle}>
                        {m.stages.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                      </select>
                    </td>
                  </tr>
                  {openId === b.id ? (
                    <tr>
                      <td colSpan={9} className="px-3 py-3" style={{ background: '#F8FAFC', ...trBorder }}>
                        {b.why_unallocated ? (
                          <div className="mb-3"><Note tone="warn" title="Why this is an exception">
                            {b.why_unallocated}</Note></div>
                        ) : null}
                        <div className="mb-2 text-[12px] font-bold" style={{ color: NAVY }}>
                          Which matters is {money2(n(b.amount))} for?
                        </div>
                        <div className="space-y-1.5">
                          {allocRows.map((row, i) => (
                            <div key={row.case_id} className="flex items-center gap-2">
                              <div className="min-w-[160px] text-[12px]" style={{ color: '#374151' }}>
                                {row.case_ref}
                              </div>
                              <input aria-label={`Amount for matter ${row.case_ref}`}
                                value={row.amount}
                                onChange={(e) => setAllocRows(allocRows.map((r, j) =>
                                  j === i ? { ...r, amount: e.target.value } : r))}
                                className="w-32 rounded px-2 py-1 text-[12px] text-right"
                                style={inputStyle} />
                              <button aria-label={`Remove matter ${row.case_ref} from this bill`}
                                onClick={() => setAllocRows(allocRows.filter((_, j) => j !== i))}
                                className="text-[12px]" style={{ color: RED }}>remove</button>
                            </div>
                          ))}
                        </div>

                        <div className="mt-2 flex flex-wrap items-end gap-2">
                          <div className="relative">
                            <div className="flex items-center gap-1.5">
                              <Search className="h-3.5 w-3.5" style={{ color: ORANGE }} />
                              <input aria-label="Find a matter by member reference"
                                value={caseQuery} onChange={(e) => findCases(e.target.value)}
                                placeholder="add a matter by member reference"
                                className="w-64 rounded px-2 py-1 text-[12px]" style={inputStyle} />
                            </div>
                            {caseHits.length ? (
                              <div className="absolute z-10 mt-1 w-64 rounded-lg bg-white shadow-lg"
                                style={{ border: `1px solid ${LINE}` }}>
                                {caseHits.map((h) => (
                                  <button key={h.id}
                                    aria-label={`Add matter ${h.case_ref} to this bill`}
                                    onClick={() => {
                                      if (!allocRows.some((r) => r.case_id === h.id)) {
                                        setAllocRows([...allocRows, { case_id: h.id,
                                          case_ref: h.case_ref, amount: '' }])
                                      }
                                      setCaseQuery(''); setCaseHits([])
                                    }}
                                    className="block w-full px-2 py-1.5 text-left text-[12px] hover:bg-gray-50">
                                    {h.case_ref}{h.client ? ` · ${h.client}` : ''}
                                  </button>
                                ))}
                              </div>
                            ) : null}
                          </div>

                          <div className="text-[12px]"
                            style={{ color: Math.abs(allocSum - Number(b.amount)) < 0.005
                              ? GREEN : RED }}>
                            lines add to {money2(allocSum)} of {money2(n(b.amount))}
                          </div>

                          {/* The tickbox sits INSIDE its label, so the words
                              name it and clicking them toggles it. */}
                          {b.billed_client_name ? (
                            <label className="flex items-center gap-1.5 text-[12px]"
                              style={{ color: '#374151' }}>
                              <input type="checkbox" checked={learnAlias}
                                onChange={(e) => setLearnAlias(e.target.checked)} />
                              <span>remember “{b.billed_client_name}” for this client</span>
                            </label>
                          ) : null}

                          <button aria-label={`Save the allocation for bill ${b.reference}`}
                            onClick={() => saveAllocation(b)} disabled={busy === b.id}
                            className="rounded-lg px-3 py-1.5 text-[12px] font-semibold text-white disabled:opacity-50"
                            style={{ background: NAVY }}>
                            {busy === b.id ? '…' : 'Save allocation'}
                          </button>
                        </div>
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              ))}
              {!ordered.length ? (
                <tr><td colSpan={9} className="px-3 py-6 text-center text-[13px]"
                  style={{ color: '#6B7280' }}>
                  No legal bills recorded yet. Enter the first one on the Capture tab.
                </td></tr>
              ) : null}
            </Table>
            {/* One page of the register is capped. Say so on the screen rather
                than letting the oldest fee notes drop off the end in silence. */}
            {d.shown < d.money.bills ? (
              <p className="px-3 py-2 text-[12px]" style={{ color: '#6B7280' }}>
                Showing the {d.shown} most recent of {d.money.bills} fee notes.
                Narrow the filters above to see the rest.
              </p>
            ) : null}
          </CardContent></Card>
        ) : null}

        {/* ---------------- Client caps ---------------- */}
        {section === 'caps' ? (
          <>
            {caps && caps.unallocated.bills ? (
              <Note tone="warn" title="Some legal spend is in no total below">
                {caps.unallocated.bills} bill(s) worth {money2(n(caps.unallocated.amount))} are not
                yet tied to a client, so every figure on this board understates by up to that
                much. Assign them on the Bills tab.
              </Note>
            ) : null}
            <Card><CardContent className="p-0">
              <Table head={['Client', 'Number', 'District', 'Matters', 'Legal spend',
                            'Headroom', 'Flag']}>
                {(caps?.clients || []).map((c) => (
                  <tr key={c.id} style={trBorder}>
                    <td className={td}>
                      <span style={{ color: NAVY, fontWeight: 600 }}>{c.name || '(no name)'}</span>
                      {!c.on_current_list ? (
                        <div className="text-[11px]" style={{ color: AMBER }}>
                          not on the current membership list
                        </div>
                      ) : null}
                    </td>
                    <td className={td}>{c.membership_no}</td>
                    <td className={td}>{c.district || '—'}</td>
                    <td className={tdNum}>{c.matters ?? '—'}</td>
                    <td className={tdNum} style={{ color: TIER_TONE[c.cap.tier], fontWeight: 600 }}>
                      {money2(n(c.cap.total))}
                    </td>
                    <td className={tdNum}>{money2(n(c.cap.headroom))}</td>
                    <td className={td}><CapPill cap={c.cap} /></td>
                  </tr>
                ))}
                {!(caps?.clients || []).length ? (
                  <tr><td colSpan={7} className="px-3 py-6 text-center text-[13px]"
                    style={{ color: '#6B7280' }}>
                    No client has any allocated legal spend yet.
                  </td></tr>
                ) : null}
              </Table>
            </CardContent></Card>
          </>
        ) : null}
      </div>
    </>
  )
}
