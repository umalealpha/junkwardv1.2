'use client'

/**
 * /bonu/intake — the call-centre claim intake + the case register (Phase 1).
 *
 * A member phones in with a legal matter. The agent opens the case here and
 * allocates it to a panel firm, in one short form. Every later movement is a
 * logged event, which is what the Panel league and Retainer scorecard measure —
 * until now a matter only appeared once its first bill arrived.
 *
 * The member is a SCHEME reference, never a name.
 *
 * TWO AUDIENCES, ONE SCREEN. A call-centre agent can open and track matters
 * but is served NO money: the backend omits the bills and the spend-cap flag
 * unless the viewer can see the BONU financials. So every money field here is
 * optional in the shape and simply absent for an agent - the screen must never
 * assume it is there. The client's NAME is shown to both (CFO, 9 Sep 2026).
 *
 * Backend: /api/v1/bonu/cases/ · /bonu/cases/<id>/ · /bonu/cases/<id>/events/
 */

import { Fragment, useCallback, useEffect, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { apiFetch } from '@/lib/api'
import { localYmd } from '@/lib/utils'
import { CalendarClock, FilePlus2, Loader2, Scale } from 'lucide-react'
import {
  AMBER, BonuTabs, Field, GREEN, LINE, NAVY, Note, ORANGE, RED, Stat, Table, td,
  trBorder,
} from '../_shared'

interface EventRow {
  id: string; happened_on: string; kind: string; kind_label: string
  detail: string; reported_by: string
}
interface BillRow {
  id: string; reference: string; biller: string; bill_date: string
  stage: string; stage_label: string; amount: string
}
interface CapFlag {
  total: string; tier: 'clear' | 'amber' | 'red'; label: string
  amber_at: string; cap: string; headroom: string
}
interface CaseRow {
  id: string; case_ref: string; firm: string; firm_id: string; member_ref: string
  firm_type: string; firm_type_label: string; internal_officer: string; is_in_house: boolean
  region: string; client_id: string; client: string; client_linked: boolean
  matter_type: string; matter_label: string; status: string; status_label: string
  is_open: boolean; instructed_on: string; first_action_on: string
  received_on: string; date_of_loss: string; matter_arose_on: string; firm_contact_on: string
  closed_on: string; days_to_process: number | null
  last_activity_on: string; next_action_due: string; court_date: string
  days_quiet: number | null; outcome_note: string; events?: EventRow[]
  bills?: BillRow[]; billed_total?: string; cap?: CapFlag
}
interface Opt { value: string; label: string }
interface Meta {
  firms: { id: string; name: string }[]
  matter_types: Opt[]; statuses: Opt[]; event_kinds: Opt[]
  firm_types: Opt[]; regions: string[]
}
interface ListResp {
  cases: CaseRow[]; shown: number; open_count: number; total: number
  unlinked_clients: number; meta: Meta
}

const STATUS_TONE: Record<string, string> = {
  instructed: AMBER, active: NAVY, court: NAVY, member: AMBER,
  settled: GREEN, won: GREEN, lost: RED, withdrawn: '#6B7280', abandoned: RED,
}
// Local calendar date, NOT toISOString(): between midnight and 02:00
// Botswana time the UTC form returns yesterday, so a claim or a bill
// opened at 00:30 would be dated the day before it happened.
const today = () => localYmd()

export default function BonuIntakePage() {
  const [d, setD] = useState<ListResp | null>(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')
  const [openId, setOpenId] = useState('')

  // New-claim form
  const [firmType, setFirmType] = useState('external')
  const [firmId, setFirmId] = useState('')
  const [internalOfficer, setInternalOfficer] = useState('')
  const [region, setRegion] = useState('')
  const [memberRef, setMemberRef] = useState('')
  const [matterType, setMatterType] = useState('other')
  const [receivedOn, setReceivedOn] = useState(today())
  const [dateOfLoss, setDateOfLoss] = useState('')
  const [matterAroseOn, setMatterAroseOn] = useState('')
  const [firmContactOn, setFirmContactOn] = useState('')
  const [instructedOn, setInstructedOn] = useState(today())
  const [caseRef, setCaseRef] = useState('')
  const [description, setDescription] = useState('')
  const [warn, setWarn] = useState('')

  // Per-open-case "log a movement" form
  const [evKind, setEvKind] = useState('update')
  const [evDate, setEvDate] = useState(today())
  const [evDetail, setEvDetail] = useState('')

  const load = useCallback(() => {
    apiFetch<ListResp>('/bonu/cases/')
      .then((x) => { setD(x); setErr('') })
      .catch(() => setErr('Could not load the case register.'))
  }, [])
  useEffect(load, [load])
  // Reset the "log a movement" form when a different case row is opened, so a note
  // typed for one case can never be submitted onto another.
  useEffect(() => { setEvKind('update'); setEvDate(today()); setEvDetail('') }, [openId])

  const openClaim = async () => {
    if (firmType === 'external' && !firmId) {
      setMsg(''); setErr('Choose the law firm this matter goes to.'); return
    }
    if (!memberRef.trim()) {
      setMsg(''); setErr('Enter the member’s scheme reference.'); return
    }
    setBusy('new'); setMsg(''); setErr(''); setWarn('')
    try {
      const out = await apiFetch<{ ok: boolean; case: CaseRow; warning?: string
                                   warning_client?: string }>('/bonu/cases/', {
        method: 'POST',
        body: JSON.stringify({
          firm_type: firmType,
          firm_id: firmType === 'external' ? firmId : '',
          internal_officer: firmType === 'in_house' ? internalOfficer.trim() : '',
          region, member_ref: memberRef.trim(), matter_type: matterType,
          received_on: receivedOn, date_of_loss: dateOfLoss,
          matter_arose_on: matterAroseOn, firm_contact_on: firmContactOn,
          instructed_on: instructedOn, case_ref: caseRef.trim(),
          description: description.trim(),
        }),
      })
      setMsg(`Claim opened — ${out.case.case_ref}, with ${out.case.firm}.`)
      // A date typed backwards, or a member who is not on the roll, is worth
      // saying out loud — but neither stopped the claim being opened.
      setWarn([out.warning, out.warning_client].filter(Boolean).join(' '))
      setMemberRef(''); setCaseRef(''); setDescription('')
      setDateOfLoss(''); setMatterAroseOn(''); setFirmContactOn('')
      load()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not open the claim.')
    } finally { setBusy('') }
  }

  const addEvent = async (id: string) => {
    setBusy(id + 'ev'); setErr('')
    try {
      await apiFetch(`/bonu/cases/${id}/events/`, {
        method: 'POST',
        body: JSON.stringify({ kind: evKind, happened_on: evDate, detail: evDetail.trim() }),
      })
      setEvDetail('')
      load()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not log the movement.')
    } finally { setBusy('') }
  }

  const setStatus = async (id: string, status: string) => {
    setBusy(id + 'st'); setErr('')
    try {
      await apiFetch(`/bonu/cases/${id}/`, { method: 'POST', body: JSON.stringify({ status }) })
      load()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not update the case.')
    } finally { setBusy('') }
  }

  if (!d) {
    return (
      <div className="min-h-screen" style={{ background: '#F6F8FB' }}>
        <TopBar title="BONU — claim intake" />
        <div className="mx-auto max-w-[1400px] px-6 py-5">
          <BonuTabs active="/bonu/intake" />
          <div className="mt-5">
            {err ? <Note tone="warn" title="Not loaded">{err}</Note> : (
              <div className="flex items-center gap-2 text-[13px]" style={{ color: '#6B7280' }}>
                <Loader2 className="h-4 w-4 animate-spin" style={{ color: ORANGE }} /> Loading the register…
              </div>
            )}
          </div>
        </div>
      </div>
    )
  }

  const m = d.meta
  const inputCls = 'w-full rounded-lg px-2.5 py-2 text-[13px]'
  const inputStyle = { border: `1px solid ${LINE}`, background: '#fff' }

  return (
    <div className="min-h-screen" style={{ background: '#F6F8FB' }}>
      <TopBar title="BONU — claim intake" />
      <div className="mx-auto max-w-[1400px] px-6 py-5">
        <BonuTabs active="/bonu/intake" />

        {msg ? <div className="mt-4"><Note tone="good">{msg}</Note></div> : null}
        {warn ? <div className="mt-4"><Note tone="warn" title="Opened — but read this">{warn}</Note></div> : null}
        {err ? <div className="mt-4"><Note tone="warn" title="Check the form">{err}</Note></div> : null}

        {/* Open a claim */}
        <Card className="mt-5">
          <CardContent className="p-4">
            <div className="mb-3 flex items-center gap-2 text-[14px] font-bold" style={{ color: NAVY }}>
              <Scale className="h-4 w-4" style={{ color: ORANGE }} /> Open a legal claim
            </div>
            <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
              <Field label="Member scheme reference *">
                {(id) => (
                  <input id={id} value={memberRef} onChange={(e) => setMemberRef(e.target.value)}
                    placeholder="e.g. BONU-01234 (never a name)" className={inputCls} style={inputStyle} />
                )}
              </Field>
              <Field label="Who is handling it *">
                {(id) => (
                  <select id={id} value={firmType} onChange={(e) => setFirmType(e.target.value)}
                    className={inputCls} style={inputStyle}>
                    {m.firm_types.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                  </select>
                )}
              </Field>
              {/* One or the other, never both — so only the relevant field is
                  ever on screen and there is nothing to fill in by mistake. */}
              {firmType === 'external' ? (
                <Field label="Allocate to law firm *">
                  {(id) => (
                    <select id={id} value={firmId} onChange={(e) => setFirmId(e.target.value)}
                      className={inputCls} style={inputStyle}>
                      <option value="">Choose a panel firm…</option>
                      {m.firms.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
                    </select>
                  )}
                </Field>
              ) : (
                <Field label="In-house officer (optional)">
                  {(id) => (
                    <input id={id} value={internalOfficer} onChange={(e) => setInternalOfficer(e.target.value)}
                      placeholder="who in Alpha Law has it" className={inputCls} style={inputStyle} />
                  )}
                </Field>
              )}
              <Field label="Region">
                {(id) => (
                  <select id={id} value={region} onChange={(e) => setRegion(e.target.value)}
                    className={inputCls} style={inputStyle}>
                    <option value="">Not stated</option>
                    {m.regions.map((r) => <option key={r} value={r}>{r}</option>)}
                  </select>
                )}
              </Field>
              <Field label="Matter type">
                {(id) => (
                  <select id={id} value={matterType} onChange={(e) => setMatterType(e.target.value)}
                    className={inputCls} style={inputStyle}>
                    {m.matter_types.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                  </select>
                )}
              </Field>
              <Field label="Claim received" hint="days-to-process counts from here">
                {(id) => (
                  <input id={id} type="date" value={receivedOn}
                    onChange={(e) => setReceivedOn(e.target.value)}
                    className={inputCls} style={inputStyle} />
                )}
              </Field>
              <Field label="Date of loss">
                {(id) => (
                  <input id={id} type="date" value={dateOfLoss}
                    onChange={(e) => setDateOfLoss(e.target.value)}
                    className={inputCls} style={inputStyle} />
                )}
              </Field>
              <Field label="Date matter arose">
                {(id) => (
                  <input id={id} type="date" value={matterAroseOn}
                    onChange={(e) => setMatterAroseOn(e.target.value)}
                    className={inputCls} style={inputStyle} />
                )}
              </Field>
              <Field label={firmType === 'in_house' ? 'Team contacted' : 'Law firm contacted'}>
                {(id) => (
                  <input id={id} type="date" value={firmContactOn}
                    onChange={(e) => setFirmContactOn(e.target.value)}
                    className={inputCls} style={inputStyle} />
                )}
              </Field>
              <Field label="Date instructed">
                {(id) => (
                  <input id={id} type="date" value={instructedOn}
                    onChange={(e) => setInstructedOn(e.target.value)}
                    className={inputCls} style={inputStyle} />
                )}
              </Field>
              <Field label={firmType === 'in_house' ? 'File ref (optional)' : 'Firm’s file ref (optional)'}>
                {(id) => (
                  <input id={id} value={caseRef} onChange={(e) => setCaseRef(e.target.value)}
                    placeholder="auto if blank" className={inputCls} style={inputStyle} />
                )}
              </Field>
              <Field label="What the member reported">
                {(id) => (
                  <input id={id} value={description} onChange={(e) => setDescription(e.target.value)}
                    placeholder="one line" className={inputCls} style={inputStyle} />
                )}
              </Field>
            </div>
            <div className="mt-3 flex items-center justify-between gap-3">
              <div className="text-[12px]" style={{ color: '#6B7280' }}>
                No fees here. This opens the matter and starts the clock; billing is separate.
              </div>
              <button onClick={openClaim} disabled={busy === 'new'}
                className="flex items-center gap-2 rounded-lg px-4 py-2 text-[13px] font-semibold text-white transition-transform duration-150 hover:-translate-y-px disabled:opacity-50"
                style={{ background: NAVY }}>
                {busy === 'new' ? <Loader2 className="h-4 w-4 animate-spin" /> : <FilePlus2 className="h-4 w-4" style={{ color: ORANGE }} />}
                Open claim
              </button>
            </div>
          </CardContent>
        </Card>

        {/* Register summary */}
        <div className="mt-4 grid grid-cols-2 gap-4 lg:grid-cols-4">
          <Card><CardContent className="p-4"><Stat label="Open cases" value={String(d.open_count)} tone={NAVY} /></CardContent></Card>
          <Card><CardContent className="p-4"><Stat label="Cases on file" value={String(d.total)} /></CardContent></Card>
          <Card><CardContent className="p-4"><Stat label="Panel firms" value={String(m.firms.length)} sub="to allocate to" /></CardContent></Card>
          {/* A matter with no client is in NO legal-spend total, so it is
              counted here rather than left to be noticed later. */}
          <Card><CardContent className="p-4">
            <Stat label="No client linked" value={String(d.unlinked_clients)}
              tone={d.unlinked_clients ? AMBER : undefined} sub="not in any cap total" />
          </CardContent></Card>
        </div>

        {/* The register */}
        <Card className="mt-4">
          <CardContent className="p-0">
            <Table head={['Case', 'Member', 'Handled by', 'Region', 'Matter', 'Status',
                          'Days to process', 'Client legal spend', 'Last movement']}>
              {d.cases.map((c) => (
                <Fragment key={c.id}>
                  <tr style={trBorder}>
                    <td className={td}>
                      <button aria-label={`Open the detail for case ${c.case_ref}`}
                        aria-expanded={openId === c.id}
                        onClick={() => setOpenId(openId === c.id ? '' : c.id)}
                        className="font-semibold underline-offset-2 hover:underline" style={{ color: NAVY }}>
                        {c.case_ref}
                      </button>
                    </td>
                    <td className={td}>
                      {c.member_ref}
                      {/* `client_linked` and not `client`: an unlinked matter
                          is in no cap total, and that must show even when the
                          name happens to be blank. */}
                      {c.client_linked
                        ? <div className="text-[11px]" style={{ color: '#9CA3AF' }}>{c.client}</div>
                        : <div className="text-[11px]" style={{ color: AMBER }}>no client linked</div>}
                    </td>
                    <td className={td}>{c.firm}</td>
                    <td className={td}>{c.region || '—'}</td>
                    <td className={td}>{c.matter_label}</td>
                    <td className={td}>
                      <span className="rounded px-1.5 py-0.5 text-[11px] font-semibold"
                        style={{ background: '#F3F4F6', color: STATUS_TONE[c.status] || '#374151' }}>
                        {c.status_label}
                      </span>
                    </td>
                    {/* "not known" when we were never told when the claim
                        arrived — never a nought, which reads as same-day. */}
                    <td className={td}>
                      {c.days_to_process == null
                        ? <span style={{ color: '#9CA3AF' }}>not known</span>
                        : `${c.days_to_process}d${c.is_open ? '' : ' (closed)'}`}
                    </td>
                    {/* The client's WHOLE spend, not this matter's — the cap is
                        per client, and a ceiling must not be crossed on one
                        matter while another quietly pushed them over. */}
                    <td className={td}>
                      {c.cap ? (
                        <span className="rounded px-1.5 py-0.5 text-[11px] font-semibold"
                          style={{
                            background: c.cap.tier === 'red' ? '#FEF2F2'
                              : c.cap.tier === 'amber' ? '#FFFBEB' : 'transparent',
                            color: c.cap.tier === 'red' ? RED
                              : c.cap.tier === 'amber' ? AMBER : '#6B7280',
                          }}>
                          P{Number(c.cap.total).toLocaleString()}
                          {c.cap.label ? ` · ${c.cap.label}` : ''}
                        </span>
                      ) : <span style={{ color: '#9CA3AF' }}>—</span>}
                    </td>
                    <td className={td} style={{ color: (c.days_quiet ?? 0) > 30 && c.is_open ? RED : '#374151' }}>
                      {c.last_activity_on || '—'}{c.days_quiet != null ? ` · ${c.days_quiet}d ago` : ''}
                    </td>
                  </tr>
                  {openId === c.id ? (
                    <tr>
                      <td colSpan={9} className="px-3 py-3" style={{ background: '#F8FAFC', ...trBorder }}>
                        <div className="mb-3 grid gap-3 md:grid-cols-2">
                          <div className="text-[12px]" style={{ color: '#374151' }}>
                            <div className="mb-1 font-bold" style={{ color: NAVY }}>Key dates</div>
                            <div>Claim received: <b>{c.received_on || '—'}</b></div>
                            <div>Date of loss: <b>{c.date_of_loss || '—'}</b></div>
                            <div>Matter arose: <b>{c.matter_arose_on || '—'}</b></div>
                            <div>{c.is_in_house ? 'Team' : 'Law firm'} contacted:{' '}
                              <b>{c.firm_contact_on || '—'}</b></div>
                            <div>Instructed: <b>{c.instructed_on || '—'}</b></div>
                            {c.closed_on ? <div>Closed: <b>{c.closed_on}</b></div> : null}
                            <div>Next / court: <b>{c.next_action_due || c.court_date || '—'}</b></div>
                          </div>
                          <div className="text-[12px]" style={{ color: '#374151' }}>
                            <div className="mb-1 font-bold" style={{ color: NAVY }}>
                              Bills on this matter
                            </div>
                            {(c.bills || []).length ? (
                              <>
                                {(c.bills || []).map((b) => (
                                  <div key={b.id}>{b.bill_date} · {b.reference} · {b.biller} ·{' '}
                                    <b>P{Number(b.amount).toLocaleString()}</b> · {b.stage_label}</div>
                                ))}
                                <div className="mt-1">This matter:{' '}
                                  <b>P{Number(c.billed_total || '0').toLocaleString()}</b></div>
                              </>
                            ) : <div style={{ color: '#9CA3AF' }}>None recorded yet.</div>}
                            {c.cap ? (
                              <div className="mt-1.5" style={{
                                color: c.cap.tier === 'red' ? RED
                                  : c.cap.tier === 'amber' ? AMBER : '#6B7280' }}>
                                This client across ALL their matters:{' '}
                                <b>P{Number(c.cap.total).toLocaleString()}</b> of{' '}
                                P{Number(c.cap.cap).toLocaleString()}
                                {c.cap.label ? ` — ${c.cap.label}` : ''}
                              </div>
                            ) : null}
                          </div>
                        </div>
                        <div className="grid gap-4 lg:grid-cols-2">
                          {/* Timeline */}
                          <div>
                            <div className="mb-2 text-[12px] font-bold" style={{ color: NAVY }}>Movements</div>
                            <div className="space-y-1.5">
                              {(c.events || []).map((e) => (
                                <div key={e.id} className="flex items-start gap-2 text-[12px]" style={{ color: '#374151' }}>
                                  <CalendarClock className="mt-0.5 h-3.5 w-3.5 shrink-0" style={{ color: ORANGE }} />
                                  <div><b>{e.happened_on}</b> · {e.kind_label}{e.detail ? ` — ${e.detail}` : ''}
                                    {e.reported_by ? <span style={{ color: '#9CA3AF' }}> ({e.reported_by})</span> : null}</div>
                                </div>
                              ))}
                              {!(c.events || []).length ? <div className="text-[12px]" style={{ color: '#9CA3AF' }}>No movements yet.</div> : null}
                            </div>
                          </div>
                          {/* Actions */}
                          <div>
                            <div className="mb-2 text-[12px] font-bold" style={{ color: NAVY }}>Log a movement</div>
                            <div className="flex flex-wrap items-end gap-2">
                              <select aria-label="Kind of movement"
                                value={evKind} onChange={(e) => setEvKind(e.target.value)}
                                className="rounded px-2 py-1.5 text-[12px]" style={inputStyle}>
                                {m.event_kinds.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                              </select>
                              <input aria-label="Date the movement happened"
                                type="date" value={evDate} onChange={(e) => setEvDate(e.target.value)}
                                className="rounded px-2 py-1.5 text-[12px]" style={inputStyle} />
                              <input aria-label="What happened"
                                value={evDetail} onChange={(e) => setEvDetail(e.target.value)}
                                placeholder="detail (optional)"
                                className="min-w-[140px] flex-1 rounded px-2 py-1.5 text-[12px]" style={inputStyle} />
                              <button aria-label={`Add a movement to ${c.case_ref}`}
                                onClick={() => addEvent(c.id)} disabled={busy === c.id + 'ev'}
                                className="rounded-lg px-3 py-1.5 text-[12px] font-semibold text-white disabled:opacity-50" style={{ background: NAVY }}>
                                {busy === c.id + 'ev' ? '…' : 'Add'}
                              </button>
                            </div>
                            <div className="mt-3 text-[12px] font-bold" style={{ color: NAVY }}>Set status</div>
                            <select aria-label={`Status of ${c.case_ref}`}
                              value={c.status} onChange={(e) => setStatus(c.id, e.target.value)}
                              disabled={busy === c.id + 'st'}
                              className="mt-1 rounded px-2 py-1.5 text-[12px]" style={inputStyle}>
                              {m.statuses.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                            </select>
                          </div>
                        </div>
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              ))}
              {!d.cases.length ? (
                <tr><td colSpan={9} className="px-3 py-6 text-center text-[13px]" style={{ color: '#6B7280' }}>
                  No cases yet. Open the first claim above — it will show here and feed the Panel league.
                </td></tr>
              ) : null}
            </Table>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
