'use client'

/**
 * /bonu/inbox — the bill on the left, the figures on the right.
 *
 * CFO 2026-08-03, recommendations 1–3: firms email the bill in, the accountant CHECKS
 * instead of captures, and a duplicate is caught while the document is still open rather
 * than after we have paid.
 *
 * The whole design intent of this screen is that her eyes move once: warnings at the top
 * because they change what she does, then what was read from the document, then the lines.
 * Nothing on this screen saves until she presses Confirm — and Confirm is the only thing
 * in the module that creates a payable.
 *
 * Backend: /bonu/documents/ · /bonu/documents/<id>/ · …/confirm/ · …/discard/ ·
 *          /bonu/upload-invoice/
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { apiFetch } from '@/lib/api'
import {
  AlertTriangle, CheckCircle2, FileSpreadsheet, Inbox, Loader2, ScanLine, Trash2, Upload,
} from 'lucide-react'
import {
  AMBER, BonuTabs, Field, GREEN, LINE, NAVY, Note, ORANGE, RED, Stat, Table, money2, td,
  trBorder,
} from '../_shared'

interface Warning { code: string; severity: string; message: string }
interface DocRow {
  id: string; filename: string; arrived_by: string; from_address: string
  firm: string; firm_id: string | null; status: string; status_label: string
  extraction_method: string; extraction_error: string
  invoice_number: string; invoice_date: string; header_total: number | null
  lines: number; lines_total: number | null; warnings: Warning[]; received: string
}
interface DocList {
  documents: DocRow[]; waiting: number; needs_ocr: number; failed: number
  firms: { id: string; name: string }[]; no_data_note: string
}
interface DraftLine {
  service_date?: string; matter_ref?: string; member_ref?: string; service_code?: string
  matter_type?: string; matter_description?: string; basis?: string; fee_earner?: string
  units?: number | null; rate?: number | null; amount?: number | string
  _edited?: boolean
}
interface DocDetail {
  id: string; filename: string; status: string; arrived_by: string; from_address: string
  firm_id: string | null; firm: string
  extraction_method: string; extraction_error: string; text_preview: string
  warnings: Warning[]
  draft: {
    header?: { invoice_number?: string; invoice_date?: string; total?: number | null }
    lines?: DraftLine[]; lines_total?: number; notes?: string; ai_error?: string | null
    needs_vision_model?: boolean
  }
  matter_types: { value: string; label: string }[]
  bases: { value: string; label: string }[]
  firms: { id: string; name: string }[]
}

const SEV_ICON_COLOR: Record<string, string> = { high: RED, medium: AMBER, low: '#6B7280' }

export default function BonuInboxPage() {
  const [list, setList] = useState<DocList | null>(null)
  const [doc, setDoc] = useState<DocDetail | null>(null)
  const [header, setHeader] = useState({ invoice_number: '', invoice_date: '', total: '' })
  const [lines, setLines] = useState<DraftLine[]>([])
  const [firmId, setFirmId] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [errTitle, setErrTitle] = useState('Not saved')
  const fileRef = useRef<HTMLInputElement>(null)

  const loadList = useCallback(() => {
    apiFetch<DocList>('/bonu/documents/')
      .then(setList)
      // A failure to LOAD is not a failure to save — they read as the same red box
      // otherwise, which is how a person ends up thinking their work was lost.
      .catch(() => { setErrTitle('Not loaded'); setErr('Could not load the list of bills.') })
  }, [])

  useEffect(loadList, [loadList])

  const open = (id: string) => {
    setMsg(''); setErr('')
    apiFetch<DocDetail>(`/bonu/documents/${id}/`)
      .then((d) => {
        setDoc(d)
        setFirmId(d.firm_id || '')
        setHeader({
          invoice_number: d.draft?.header?.invoice_number || '',
          invoice_date: d.draft?.header?.invoice_date || '',
          total: d.draft?.header?.total != null ? String(d.draft.header.total) : '',
        })
        setLines((d.draft?.lines || []).map((l) => ({ ...l })))
      })
      .catch(() => { setErrTitle('Not opened'); setErr('Could not open that bill.') })
  }

  const upload = async (f: File) => {
    setBusy(true); setMsg(''); setErr('')
    try {
      const body = new FormData()
      body.append('file', f)
      const out = await apiFetch<{ document_id: string }>('/bonu/upload-invoice/', {
        method: 'POST', body,
      })
      loadList()
      if (out?.document_id) open(out.document_id)
      setMsg(`${f.name} read. Check the figures on the right, then confirm.`)
    } catch {
      setErrTitle('Not read'); setErr('That file could not be read.')
    } finally {
      setBusy(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const editLine = (i: number, field: keyof DraftLine, value: string) => {
    setLines((prev) =>
      prev.map((l, idx) =>
        idx === i ? { ...l, [field]: value, _edited: true } : l))
  }

  const linesTotal = lines.reduce((s, l) => s + (Number(l.amount) || 0), 0)
  const headerTotal = Number(header.total) || 0
  const foots = !headerTotal || Math.abs(linesTotal - headerTotal) <= 0.05

  const confirm = async () => {
    if (!doc) return
    setBusy(true); setMsg(''); setErr('')
    try {
      const out = await apiFetch<{ ok: boolean; message: string }>(
        `/bonu/documents/${doc.id}/confirm/`,
        { method: 'POST', body: JSON.stringify({ firm_id: firmId, header, lines }) },
      )
      setMsg(out.message || 'Saved.')
      setDoc(null)
      loadList()
    } catch (e) {
      setErrTitle('Not saved')
      setErr(e instanceof Error ? e.message : 'It was not saved. Check the invoice number and date.')
    } finally {
      setBusy(false)
    }
  }

  const discard = async () => {
    if (!doc) return
    setBusy(true)
    try {
      await apiFetch(`/bonu/documents/${doc.id}/discard/`, { method: 'POST' })
      setMsg('Set aside. Nothing was deleted.')
      setDoc(null)
      loadList()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-screen" style={{ background: '#F6F8FB' }}>
      <TopBar title="BONU — bills to check" />
      <div className="mx-auto max-w-[1400px] px-6 py-5">
        <BonuTabs active="/bonu/inbox" />

        <div className="mt-5 grid grid-cols-1 gap-5 lg:grid-cols-[380px_minmax(0,1fr)]">
          {/* LEFT — what has arrived */}
          <div className="space-y-4">
            <Card>
              <CardContent className="p-4">
                <div className="grid grid-cols-3 gap-3">
                  <Stat label="Waiting" value={String(list?.waiting ?? 0)} tone={ORANGE} />
                  <Stat label="Scans" value={String(list?.needs_ocr ?? 0)} tone={AMBER} />
                  <Stat label="Unreadable" value={String(list?.failed ?? 0)} tone={RED} />
                </div>

                <label
                  className="mt-4 flex cursor-pointer items-center justify-center gap-2 rounded-xl border-2 border-dashed px-4 py-6 text-[13px] transition-colors duration-150 hover:bg-white"
                  style={{ borderColor: LINE, color: NAVY }}
                >
                  {busy ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Upload className="h-4 w-4" style={{ color: ORANGE }} />
                  )}
                  <span>Drop a bill here, or click to choose</span>
                  <input
                    ref={fileRef}
                    type="file"
                    className="hidden"
                    accept=".xlsx,.xlsm,.csv,.pdf,.png,.jpg,.jpeg"
                    onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f) }}
                  />
                </label>
                <div className="mt-2 text-[12px]" style={{ color: '#6B7280' }}>
                  A spreadsheet or a normal PDF is read exactly, with no AI. A photo or a scan
                  needs the vision reader.
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardContent className="p-0">
                {!list?.documents?.length ? (
                  <div className="p-4">
                    <Note>{list?.no_data_note || 'Nothing waiting.'}</Note>
                  </div>
                ) : (
                  <div>
                    {list.documents.map((d) => {
                      const on = doc?.id === d.id
                      const worst = d.warnings?.some((w) => w.severity === 'high')
                      return (
                        <button
                          key={d.id}
                          onClick={() => open(d.id)}
                          className="flex w-full items-start gap-3 px-4 py-3 text-left transition-colors duration-150 hover:bg-slate-50"
                          style={{
                            ...trBorder,
                            background: on ? '#FFF7E8' : undefined,
                            borderLeft: `3px solid ${on ? ORANGE : 'transparent'}`,
                          }}
                        >
                          {d.status === 'needs_ocr' ? (
                            <ScanLine className="mt-0.5 h-4 w-4 shrink-0" style={{ color: AMBER }} />
                          ) : (
                            <FileSpreadsheet
                              className="mt-0.5 h-4 w-4 shrink-0"
                              style={{ color: worst ? RED : '#6B7280' }}
                            />
                          )}
                          <div className="min-w-0">
                            <div className="truncate text-[13px] font-semibold" style={{ color: NAVY }}>
                              {d.filename}
                            </div>
                            <div className="text-[12px]" style={{ color: '#6B7280' }}>
                              {d.firm || 'firm not identified'} · {d.lines} line(s) ·{' '}
                              {d.header_total != null ? money2(d.header_total) : 'no total read'}
                            </div>
                            {d.warnings?.length ? (
                              <div
                                className="mt-1 text-[12px] font-medium"
                                style={{ color: worst ? RED : AMBER }}
                              >
                                {d.warnings.length} thing(s) to look at
                              </div>
                            ) : null}
                          </div>
                        </button>
                      )
                    })}
                  </div>
                )}
              </CardContent>
            </Card>
          </div>

          {/* RIGHT — check and confirm */}
          <div className="space-y-4">
            {msg ? (
              <Note tone="good" title="Done">{msg}</Note>
            ) : null}
            {err ? <Note tone="warn" title={errTitle}>{err}</Note> : null}

            {!doc ? (
              <Card>
                <CardContent className="flex flex-col items-center justify-center gap-3 p-12 text-center">
                  <Inbox className="h-8 w-8" style={{ color: LINE }} />
                  <div className="text-[14px] font-semibold" style={{ color: NAVY }}>
                    Pick a bill on the left
                  </div>
                  <div className="max-w-md text-[13px]" style={{ color: '#6B7280' }}>
                    The figures are filled in from the document. You check them, fix anything that
                    is wrong, and press Confirm. Nothing is saved until you do.
                  </div>
                </CardContent>
              </Card>
            ) : (
              <>
                {doc.warnings?.length ? (
                  <Card>
                    <CardContent className="p-4">
                      <div className="mb-2 flex items-center gap-2 text-[13px] font-bold" style={{ color: NAVY }}>
                        <AlertTriangle className="h-4 w-4" style={{ color: RED }} />
                        Look at these first
                      </div>
                      <ul className="space-y-1.5">
                        {doc.warnings.map((w, i) => (
                          <li key={w.code + i} className="flex gap-2 text-[13px]">
                            <span
                              className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full"
                              style={{ background: SEV_ICON_COLOR[w.severity] || '#6B7280' }}
                            />
                            <span style={{ color: '#374151' }}>{w.message}</span>
                          </li>
                        ))}
                      </ul>
                    </CardContent>
                  </Card>
                ) : null}

                {doc.draft?.needs_vision_model ? (
                  <Note tone="warn" title="This one is a scan">
                    There is no text in this document to read, so the figures cannot be filled in
                    from it. Ask the firm for a spreadsheet or a proper PDF — that is free and
                    exact — or capture it by hand.
                  </Note>
                ) : null}
                {doc.draft?.ai_error ? (
                  <Note tone="warn" title="The line reader was not available">
                    The document was read, but the part that arranges it into lines could not run.
                    The figures below may be empty — capture them by hand this once.
                  </Note>
                ) : null}

                <Card>
                  <CardContent className="p-4">
                    <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
                      <div>
                        <div className="text-[11px] uppercase tracking-wide" style={{ color: '#6B7280' }}>
                          {doc.arrived_by === 'email'
                            ? `Emailed in by ${doc.from_address || 'a firm'}`
                            : 'Uploaded'}
                        </div>
                        <div className="text-[15px] font-bold" style={{ color: NAVY }}>
                          {doc.filename}
                        </div>
                        <div className="text-[12px]" style={{ color: '#6B7280' }}>
                          Read using: {doc.extraction_method || 'unknown'}
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        <button
                          onClick={discard}
                          disabled={busy}
                          className="flex items-center gap-1.5 rounded-lg px-3 py-2 text-[13px] transition-colors duration-150 hover:bg-slate-100"
                          style={{ color: '#6B7280', border: `1px solid ${LINE}` }}
                        >
                          <Trash2 className="h-3.5 w-3.5" /> Set aside
                        </button>
                        <button
                          onClick={confirm}
                          disabled={busy || !lines.length}
                          className="flex items-center gap-1.5 rounded-lg px-4 py-2 text-[13px] font-semibold text-white transition-transform duration-150 hover:-translate-y-px disabled:opacity-50"
                          style={{ background: NAVY }}
                        >
                          {busy ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          ) : (
                            <CheckCircle2 className="h-3.5 w-3.5" style={{ color: ORANGE }} />
                          )}
                          Confirm this bill
                        </button>
                      </div>
                    </div>

                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-4">
                      <Field label="Firm">
                        {(id) => (
                        <select
                          id={id}
                          value={firmId}
                          onChange={(e) => setFirmId(e.target.value)}
                          className="w-full rounded-lg px-2 py-1.5 text-[13px]"
                          style={{ border: `1px solid ${firmId ? LINE : RED}`, color: NAVY }}
                        >
                          <option value="">— choose the firm —</option>
                          {doc.firms.map((f) => (
                            <option key={f.id} value={f.id}>{f.name}</option>
                          ))}
                        </select>
                        )}
                      </Field>
                      <Field label="Invoice number">
                        {(id) => (
                        <input
                          id={id}
                          value={header.invoice_number}
                          onChange={(e) => setHeader({ ...header, invoice_number: e.target.value })}
                          className="w-full rounded-lg px-2 py-1.5 text-[13px]"
                          style={{
                            border: `1px solid ${header.invoice_number ? LINE : RED}`,
                            color: NAVY,
                          }}
                        />
                        )}
                      </Field>
                      <Field label="Invoice date">
                        {(id) => (
                        <input
                          id={id}
                          type="date"
                          value={header.invoice_date}
                          onChange={(e) => setHeader({ ...header, invoice_date: e.target.value })}
                          className="w-full rounded-lg px-2 py-1.5 text-[13px]"
                          style={{
                            border: `1px solid ${header.invoice_date ? LINE : RED}`,
                            color: NAVY,
                          }}
                        />
                        )}
                      </Field>
                      <Field label="Total on the document">
                        {(id) => (
                        <input
                          id={id}
                          value={header.total}
                          onChange={(e) => setHeader({ ...header, total: e.target.value })}
                          className="w-full rounded-lg px-2 py-1.5 text-right text-[13px]"
                          style={{ border: `1px solid ${LINE}`, color: NAVY }}
                        />
                        )}
                      </Field>
                    </div>

                    <div
                      className="mt-3 flex flex-wrap items-center gap-4 rounded-lg px-3 py-2 text-[13px]"
                      style={{
                        background: foots ? '#F0FDF4' : '#FEF7F7',
                        border: `1px solid ${foots ? '#C7EBD9' : '#F3D6D6'}`,
                      }}
                    >
                      <span style={{ color: '#374151' }}>
                        Lines add up to <b>{money2(linesTotal)}</b>
                      </span>
                      {headerTotal ? (
                        <span style={{ color: foots ? GREEN : RED }}>
                          {foots
                            ? 'and that matches the document.'
                            : `but the document says ${money2(headerTotal)} — difference ${money2(Math.abs(linesTotal - headerTotal))}.`}
                        </span>
                      ) : (
                        <span style={{ color: '#6B7280' }}>no total was read off the document.</span>
                      )}
                    </div>
                  </CardContent>
                </Card>

                <Card>
                  <CardContent className="p-0">
                    <Table head={['Date', 'Matter', 'Member', 'Case type', 'Who', 'Basis', 'Hrs', 'Rate', 'Amount']}>
                      {lines.map((l, i) => (
                        <tr key={i} style={trBorder}>
                          <td className={td}>
                            <input
                              type="date"
                              aria-label={`Service date, line ${i + 1}`}
                              value={l.service_date || ''}
                              onChange={(e) => editLine(i, 'service_date', e.target.value)}
                              className="w-[120px] rounded px-1 py-1 text-[12px]"
                              style={{ border: `1px solid ${LINE}` }}
                            />
                          </td>
                          <td className={td}>
                            <input
                              aria-label={`Matter reference, line ${i + 1}`}
                              value={l.matter_ref || ''}
                              onChange={(e) => editLine(i, 'matter_ref', e.target.value)}
                              className="w-[96px] rounded px-1 py-1 text-[12px]"
                              style={{ border: `1px solid ${LINE}` }}
                            />
                          </td>
                          <td className={td}>
                            <input
                              aria-label={`Member reference, line ${i + 1}`}
                              value={l.member_ref || ''}
                              onChange={(e) => editLine(i, 'member_ref', e.target.value)}
                              className="w-[86px] rounded px-1 py-1 text-[12px]"
                              style={{ border: `1px solid ${LINE}` }}
                            />
                          </td>
                          <td className={td}>
                            <select
                              aria-label={`Matter type, line ${i + 1}`}
                              value={l.matter_type || 'other'}
                              onChange={(e) => editLine(i, 'matter_type', e.target.value)}
                              className="w-[150px] rounded px-1 py-1 text-[12px]"
                              style={{
                                border: `1px solid ${(l.matter_type || 'other') === 'other' ? AMBER : LINE}`,
                              }}
                            >
                              {doc.matter_types.map((m) => (
                                <option key={m.value} value={m.value}>{m.label}</option>
                              ))}
                            </select>
                          </td>
                          <td className={td}>
                            <input
                              aria-label={`Fee earner, line ${i + 1}`}
                              value={l.fee_earner || ''}
                              onChange={(e) => editLine(i, 'fee_earner', e.target.value)}
                              className="w-[110px] rounded px-1 py-1 text-[12px]"
                              style={{ border: `1px solid ${LINE}` }}
                            />
                          </td>
                          <td className={td}>
                            <select
                              aria-label={`Charging basis, line ${i + 1}`}
                              value={l.basis || 'hourly'}
                              onChange={(e) => editLine(i, 'basis', e.target.value)}
                              className="w-[104px] rounded px-1 py-1 text-[12px]"
                              style={{ border: `1px solid ${LINE}` }}
                            >
                              {doc.bases.map((b) => (
                                <option key={b.value} value={b.value}>{b.label}</option>
                              ))}
                            </select>
                          </td>
                          <td className={td}>
                            <input
                              aria-label={`Units or hours, line ${i + 1}`}
                              value={l.units ?? ''}
                              onChange={(e) => editLine(i, 'units', e.target.value)}
                              className="w-[54px] rounded px-1 py-1 text-right text-[12px]"
                              style={{ border: `1px solid ${LINE}` }}
                            />
                          </td>
                          <td className={td}>
                            <input
                              aria-label={`Rate, line ${i + 1}`}
                              value={l.rate ?? ''}
                              onChange={(e) => editLine(i, 'rate', e.target.value)}
                              className="w-[74px] rounded px-1 py-1 text-right text-[12px]"
                              style={{ border: `1px solid ${LINE}` }}
                            />
                          </td>
                          <td className={td}>
                            <input
                              aria-label={`Amount, line ${i + 1}`}
                              value={l.amount ?? ''}
                              onChange={(e) => editLine(i, 'amount', e.target.value)}
                              className="w-[92px] rounded px-1 py-1 text-right text-[12px] font-semibold"
                              style={{ border: `1px solid ${LINE}`, color: NAVY }}
                            />
                          </td>
                        </tr>
                      ))}
                      {!lines.length ? (
                        <tr>
                          <td className="px-3 py-6 text-center text-[13px]" colSpan={9} style={{ color: '#6B7280' }}>
                            No lines were read off this document. Ask the firm for it as a
                            spreadsheet, or capture the lines by hand.
                          </td>
                        </tr>
                      ) : null}
                    </Table>
                  </CardContent>
                </Card>

                {doc.text_preview ? (
                  <Card>
                    <CardContent className="p-4">
                      <div className="mb-2 text-[11px] uppercase tracking-wide" style={{ color: '#6B7280' }}>
                        What we read off the document
                      </div>
                      <pre
                        className="max-h-56 overflow-auto whitespace-pre-wrap rounded-lg p-3 text-[12px] leading-relaxed"
                        style={{ background: '#F8FAFC', border: `1px solid ${LINE}`, color: '#374151' }}
                      >
                        {doc.text_preview}
                      </pre>
                    </CardContent>
                  </Card>
                ) : null}
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
