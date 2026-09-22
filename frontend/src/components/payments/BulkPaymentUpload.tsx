'use client'

/**
 * BulkPaymentUpload — load a list of payments from a file instead of typing them.
 *
 * Legakwa Ntabeni, 2026-09-11: "on payment requests you can only load payments
 * individually, there should be a feature that allows for batch payments such as
 * commission and salary payments."
 *
 * CFO decision the same day, in his words: "if he loads 10 supplier payments via
 * omni csv I want it to display separately in Omni and FNB - so if I want to
 * reject one supplier I don't reject everyone."
 *
 * WHY THE ROWS ARE CREATED ONE CALL AT A TIME, AND NOT BY A BULK ENDPOINT.
 * Omni's payment create path carries roughly six hundred lines of money controls
 * — the duplicate gate, the bank-change gate, first-payment-to-a-new-payee, the
 * hard bank cross-check, supplier terms. A bulk importer that wrote rows into the
 * database would walk past every one of them, and past every control added after
 * it. So the server only READS the file; each row is then created through the
 * ordinary gated endpoint. A row a control refuses is shown with the control's
 * own reason, and the rest still go through. A bulk upload is a faster way of
 * typing, never a second way in.
 */

import { useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2, Loader2, Upload, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  bulkParsePaymentFile, createPaymentRequest, getMyCompanies,
  type BulkParsedRow, type BulkParseResult, type MeCompany, type PaymentCategory,
} from '@/lib/api'

type Outcome = { line: number; name: string; ok: boolean; detail: string }

export default function BulkPaymentUpload(
  { category, entity, onClose, onCreated }: {
    category: PaymentCategory
    /** Only a starting suggestion — the person uploading chooses the company
     *  below. It used to be the decision: the page passed entityOptions[0],
     *  built from whichever companies happened to appear in the rows already on
     *  screen, so a Unicoin file could be filed against Alpha Direct with
     *  nothing on screen naming a company at all
     *  (Manus QC-UNICOIN-BULK-2026-09-12, CRITICAL). */
    entity: string
    onClose: () => void
    onCreated: () => void
  },
) {
  const [file, setFile] = useState<File | null>(null)
  // The company these payments belong to. Deliberately starts EMPTY: an
  // unchosen company must look unchosen, not silently become the first one in
  // a list. Nothing can be created until it is picked.
  const [company, setCompany] = useState('')
  const [companies, setCompanies] = useState<MeCompany[]>([])
  const [companyErr, setCompanyErr] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    getMyCompanies().then(res => {
      if (!alive) return
      // Only companies this person may write to. Offering one they cannot use
      // just moves the refusal to after they have uploaded the file.
      const usable = (res.companies || []).filter(c => c.can_write && c.is_active)
      setCompanies(usable)
      // Pre-select ONLY when the suggestion genuinely matches one of them, or
      // when there is exactly one it could be.
      const match = usable.find(c => c.name === entity || c.code === entity)
      if (match) setCompany(match.id)
      else if (usable.length === 1) setCompany(usable[0].id)
    }).catch(e => {
      if (alive) setCompanyErr(e instanceof Error ? e.message
                                                  : 'Could not load your companies.')
    })
    return () => { alive = false }
  }, [entity])

  const chosen = companies.find(c => c.id === company)
  // SEPARATE is the default and stays the default. Bundling has to be chosen.
  const [asRun, setAsRun] = useState(false)
  const [subject, setSubject] = useState('')
  const [read, setRead] = useState<BulkParseResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null)
  const [outcomes, setOutcomes] = useState<Outcome[] | null>(null)

  const good: BulkParsedRow[] = (read?.rows || []).filter(r => r.ok)

  // Bug lntabeni 2026-09-11: "the Check the file button is not working … clicking
  // it does nothing — no response, no error message". It was doing exactly what it
  // was told: the button was disabled={!file}, so with no file chosen the click
  // never landed and NOTHING said why. Both screenshots show "No file chosen".
  // A disabled control that gives no reason is indistinguishable from a broken
  // one. The button now stays live and SAYS what is missing.
  // ONE canonical template. Before this the only template lived on the retired
  // /payments/upload page and documented a DIFFERENT set of columns, so the two
  // screens disagreed about what a payment file looks like.
  const downloadTemplate = () => {
    const csv = [
      // These header names and these account-type codes are what the reader
      // actually accepts. "Payee name" and "cheque" LOOK right and are both
      // refused - the template must round-trip through the same parser that
      // reads a real upload, or we hand people a file we then reject (/fabe
      // 2026-09-13, proven by running _match_columns on the old header).
      'Recipient name,Account number,Branch code,Account type,Amount,Their reference,Email',
      'Example Supplier (Pty) Ltd,62812345678,282267,1,1500.00,INV-1042,accounts@example.co.bw',
      'Second Payee,1234567890,462005,1,275.50,INV-1043,',
    ].join('\r\n')
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }))
    const a = document.createElement('a')
    a.href = url
    a.download = 'omni-payment-upload-template.csv'
    a.click()
    URL.revokeObjectURL(url)
  }

  const check = async (runChoice = asRun) => {
    if (busy) return
    if (!file) {
      setErr('No file is selected. Click "Choose file" above and pick your payment '
             + 'list (.csv, .xlsx or .xls), then press Check the file again.')
      return
    }
    setBusy(true); setErr(null); setOutcomes(null)
    try {
      setRead(await bulkParsePaymentFile(file, category, runChoice ? 'run' : 'separate'))
    } catch (e) {
      setRead(null)
      setErr(e instanceof Error ? e.message : 'Could not read that file.')
    } finally {
      setBusy(false)
    }
  }

  const create = async () => {
    if (!read || !good.length || busy) return
    if (!chosen) {
      setErr('Choose the company these payments belong to before creating them.')
      return
    }
    const title = subject.trim() || good[0].own_reference || 'Uploaded payment list'
    setBusy(true); setErr(null); setOutcomes([])
    const done: Outcome[] = []

    const record = (line: number, name: string, ok: boolean, detail: string) => {
      done.push({ line, name, ok, detail })
      setOutcomes([...done])
    }

    try {
      if (read.bundled) {
        // ONE request for the whole run. Each payee's own account rides on its
        // own line, so the FNB file Omni writes pays each person their own money.
        setProgress({ done: 0, total: 1 })
        try {
          await createPaymentRequest({
            entity: chosen.name, category, currency: 'BWP', subject: title,
            payee: `${good.length} payees (${title})`,
            account_name: good[0].name,
            account_number: good[0].account_number,
            branch_code: good[0].branch_code,
            account_type: good[0].account_type,
            line_items: good.map(r => ({
              description: r.name, amount: Number(r.amount), gl_code: '',
              ref: r.own_reference,
              payee: r.name,
              account_number: r.account_number,
              account_type: r.account_type,
              branch_code: r.branch_code,
              recipient_reference: r.recipient_reference,
              email: r.email,
            })),
          })
          record(0, title, true, `One request for the whole run — ${good.length} payees.`)
        } catch (e) {
          record(0, title, false, e instanceof Error ? e.message : 'Refused.')
        }
        setProgress({ done: 1, total: 1 })
      } else {
        // One request per payee. Each stands alone all the way to the bank, so
        // refusing one refuses one.
        setProgress({ done: 0, total: good.length })
        for (let i = 0; i < good.length; i++) {
          const r = good[i]
          try {
            await createPaymentRequest({
              entity: chosen.name, category, currency: 'BWP',
              subject: r.own_reference || title,
              payee: r.name,
              account_name: r.name,
              account_number: r.account_number,
              branch_code: r.branch_code,
              account_type: r.account_type,
              line_items: [{
                description: r.own_reference || title,
                amount: Number(r.amount), gl_code: '', ref: r.own_reference,
              }],
            })
            record(r.line, r.name, true, `Created — BWP ${r.amount}.`)
          } catch (e) {
            record(r.line, r.name, false, e instanceof Error ? e.message : 'Refused.')
          }
          setProgress({ done: i + 1, total: good.length })
        }
      }
      onCreated()
    } finally {
      setBusy(false)
    }
  }

  const created = (outcomes || []).filter(o => o.ok).length
  const refused = (outcomes || []).filter(o => !o.ok).length

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4">
      <div className="w-full max-w-4xl rounded-xl bg-white shadow-xl">
        <div className="flex items-center justify-between border-b border-[#E5E7EB] px-5 py-3">
          <h2 className="text-base font-semibold text-[#0D1B2A]">
            Upload a list of payments
          </h2>
          <button onClick={onClose} aria-label="Close"
                  className="text-[#6B7280] hover:text-[#0D1B2A]">
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="space-y-4 px-5 py-4">
          {/* ── 0. WHICH COMPANY. Nothing may be created without it. ── */}
          <div>
            <label htmlFor="bulk-company"
                   className="mb-1 block text-xs font-semibold uppercase tracking-wider text-[#6B7280]">
              Which company are these payments for?
            </label>
            <select id="bulk-company" value={company}
                    onChange={e => { setCompany(e.target.value); setErr(null) }}
                    className="w-full rounded-lg border border-[#D1D5DB] bg-white px-2 py-1.5 text-sm text-[#374151] focus:border-[#F47C20] focus:outline-none">
              <option value="">Choose the company…</option>
              {companies.map(c => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </select>
            {companyErr && (
              <p className="mt-1 text-[12px] font-medium text-[#B45309]">{companyErr}</p>
            )}
            {!companyErr && !company && (
              <p className="mt-1 text-[12px] font-medium text-[#B45309]">
                Pick the company first — these payments are filed against it and paid
                from its bank account.
              </p>
            )}
            {chosen && (
              <p className="mt-1 text-[12px] font-medium text-[#166534]">
                These will be raised for {chosen.name}.
              </p>
            )}
          </div>

          {/* ── 1. the file ────────────────────────────────────────────── */}
          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wider text-[#6B7280]">
              The list
            </label>
            <input type="file" accept=".csv,.xlsx,.xls"
                   onChange={e => { setFile(e.target.files?.[0] || null); setRead(null)
                                    setOutcomes(null); setErr(null) }}
                   className="block w-full text-sm" />
            {/* Say out loud that the file registered. Without this the only proof
                is the browser's own "No file chosen", which reads as decoration. */}
            {file ? (
              <p className="mt-1 text-[12px] font-medium text-[#166534]">
                Selected: {file.name} ({Math.max(1, Math.round(file.size / 1024))} KB)
              </p>
            ) : (
              <p className="mt-1 text-[12px] font-medium text-[#B45309]">
                No file selected yet.
              </p>
            )}
            <p className="mt-1 text-[11px] text-[#6B7280]">
              The FNB template you already make works as it is — Omni finds the headings
              under the version, date and account lines. A plain spreadsheet with a payee,
              an account number and an amount works too.{' '}
              <button type="button" onClick={downloadTemplate}
                      className="font-semibold text-[#F47C20] underline underline-offset-2">
                Download a blank template
              </button>{' '}
              if you would rather start from one.
            </p>
          </div>

          {/* ── 2. how they arrive. Separate is the default. ───────────── */}
          <div className="rounded-lg border border-[#E5E7EB] bg-[#F9FAFB] p-3">
            <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-[#6B7280]">
              How should these arrive?
            </p>
            <label className="flex cursor-pointer items-start gap-2 text-sm">
              <input type="radio" checked={!asRun} className="mt-1"
                     onChange={() => { setAsRun(false); setRead(null) }} />
              <span>
                <b>Separately — one payment each.</b>
                <span className="block text-[12px] text-[#6B7280]">
                  Each payee is its own request in Omni and its own entry in FNB. You can
                  refuse one without touching the others. Use this for suppliers, claims
                  and anything else.
                </span>
              </span>
            </label>
            <label className="mt-2 flex cursor-pointer items-start gap-2 text-sm">
              <input type="radio" checked={asRun} className="mt-1"
                     onChange={() => { setAsRun(true); setRead(null) }} />
              <span>
                <b>As one run — approved in one go.</b>
                <span className="block text-[12px] text-[#6B7280]">
                  Only for a commission run or a salary run. The whole run is approved or
                  refused together, so do not use it where you might need to refuse one
                  person.
                </span>
              </span>
            </label>
          </div>

          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wider text-[#6B7280]">
              What to call it
            </label>
            <input value={subject} onChange={e => setSubject(e.target.value)}
                   placeholder="e.g. Unicoin Commission. July. 2026"
                   className="w-full rounded border border-[#D1D5DB] px-3 py-2 text-sm" />
          </div>

          <Button onClick={() => check()} disabled={busy}>
            {busy && !progress ? <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                               : <Upload className="mr-1 h-4 w-4" />}
            Check the file
          </Button>

          {err && (
            <div className="rounded-md border-2 border-[#DC2626] bg-[#FEE2E2] p-3 text-sm text-[#991B1B]">
              {err}
            </div>
          )}

          {/* ── 3. what Omni read. Nothing created yet. ─────────────────── */}
          {read && !outcomes && (
            <>
              <div className="rounded-md border border-[#BFDBFE] bg-[#EFF6FF] p-3 text-sm text-[#1E3A8A]">
                <b>{read.message}</b>
                <p className="mt-1">{read.how_they_will_arrive}</p>
                <p className="mt-1">
                  Total to pay: <b>BWP {Number(read.total).toLocaleString('en-US',
                    { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</b>
                  {read.source_account && <> · out of account {read.source_account}</>}
                </p>
              </div>

              <div className="max-h-72 overflow-y-auto rounded border border-[#E5E7EB]">
                <table className="w-full text-sm">
                  <thead className="sticky top-0 bg-[#F9FAFB] text-left text-[11px] uppercase tracking-wider text-[#6B7280]">
                    <tr>
                      <th className="px-3 py-2">Line</th>
                      <th className="px-3 py-2">Payee</th>
                      <th className="px-3 py-2">Account</th>
                      <th className="px-3 py-2">Branch</th>
                      <th className="px-3 py-2 text-right">Amount</th>
                      <th className="px-3 py-2">Problem</th>
                    </tr>
                  </thead>
                  <tbody>
                    {read.rows.map(r => (
                      <tr key={r.line}
                          className={r.ok ? 'border-t border-[#E5E7EB]'
                                          : 'border-t border-[#FCA5A5] bg-[#FEF2F2]'}>
                        <td className="px-3 py-2 text-[#6B7280]">{r.line}</td>
                        <td className="px-3 py-2">{r.name || '—'}</td>
                        <td className="px-3 py-2 tabular-nums">{r.account_number || '—'}</td>
                        <td className="px-3 py-2 tabular-nums">{r.branch_code || '—'}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{r.amount || '—'}</td>
                        <td className="px-3 py-2 text-[12px] text-[#991B1B]">
                          {r.problems.join(' ')}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {read.bad > 0 && (
                <div className="rounded-md border-2 border-[#D97706] bg-[#FEF3C7] p-3 text-sm text-[#92400E]">
                  <p className="flex items-center gap-2 font-bold">
                    <AlertTriangle className="h-4 w-4 shrink-0" />
                    {read.bad} line(s) will NOT be created
                  </p>
                  <p className="mt-1">
                    Fix them in the spreadsheet and upload again. The {read.ok} good
                    line(s) can go through now — nothing is lost either way.
                  </p>
                </div>
              )}

              <Button onClick={create} disabled={!read.ok || busy}>
                {busy ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : null}
                {read.bundled
                  ? `Create ONE request for ${read.ok} payees`
                  : `Create ${read.ok} separate payment requests`}
              </Button>
            </>
          )}

          {/* ── 4. what actually happened, row by row ───────────────────── */}
          {outcomes && (
            <>
              {progress && busy && (
                <p className="text-sm text-[#6B7280]">
                  Creating {progress.done} of {progress.total}…
                </p>
              )}
              <div className="rounded-md border border-[#E5E7EB] p-3 text-sm">
                <p className="font-semibold text-[#0D1B2A]">
                  <CheckCircle2 className="mr-1 inline h-4 w-4 text-[#047857]" />
                  {created} created{refused > 0 && `, ${refused} refused`}
                </p>
                {refused > 0 && (
                  <p className="mt-1 text-[12px] text-[#6B7280]">
                    A refused row was stopped by one of Omni&rsquo;s payment checks, with the
                    reason below. Fix it and upload just that one again — the created ones
                    are unaffected.
                  </p>
                )}
                <ul className="mt-2 space-y-1">
                  {outcomes.map((o, i) => (
                    <li key={i} className={o.ok ? 'text-[#065F46]' : 'text-[#991B1B]'}>
                      {o.name}: {o.detail}
                    </li>
                  ))}
                </ul>
              </div>
              {!busy && <Button onClick={onClose}>Done</Button>}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
