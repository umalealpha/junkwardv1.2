'use client'

/**
 * /bonu/capture — the guided BONU ERP capture screen.
 *
 * CFO 2026-08-17: Kutlo could not record revenue, supplier bills, admin fees or
 * other expenses — the only way in was the raw grid on the Schedule tab, where
 * the real columns hide among blank 'Column N' labels. So he passed revenue as a
 * hand-typed journal and coded it wrong.
 *
 * Four plain-English forms, driven entirely by the backend spec at
 * /bonu/capture/ so the field labels and the sheet columns can never drift apart.
 * Each save writes ONE row to the sheet that already backs it, through the same
 * path the grid uses — so totals, P&L and the validator pick it up at once.
 *
 * This screen does NOT post to the ledger. It fills the BONU sub-ledger; the
 * monthly GL journal stays a finance approval with the correct BONU accounts.
 */

import { useEffect, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { apiFetch } from '@/lib/api'
import { Loader2, Check, TrendingUp, FileText, Briefcase, Receipt, ArrowLeft, Upload, Sparkles } from 'lucide-react'
import { BonuTabs, NAVY, ORANGE, GREEN, RED, AMBER, LINE, Note, Stat, money2 } from '../_shared'

interface Field {
  column: string; label: string; required: boolean
  kind: 'text' | 'money' | 'date' | 'month' | 'select'
  help?: string; options?: string[]; default?: string
}
interface Form {
  key: string; title: string; blurb: string; icon: string; sheet: string
  sheet_exists: boolean; sheet_total: string; row_count: number; fields: Field[]
}

const ICONS: Record<string, any> = {
  'trending-up': TrendingUp, 'file-text': FileText, 'briefcase': Briefcase, 'receipt': Receipt,
}
const num = (s: string) => { const n = Number(String(s).replace(/[^0-9.-]/g, '')); return Number.isFinite(n) ? n : null }

export default function BonuCapturePage() {
  const [forms, setForms] = useState<Form[]>([])
  const [loading, setLoading] = useState(true)
  const [active, setActive] = useState<Form | null>(null)
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)
  const [reading, setReading] = useState(false)
  const [readMsg, setReadMsg] = useState<string | null>(null)
  // Supplier cap: when a bill takes a member over P90k the server refuses; the
  // accountant may record anyway with a reason.
  const [capBlock, setCapBlock] = useState<{ detail: string; remaining?: string } | null>(null)
  const [capReason, setCapReason] = useState('')

  const load = async () => {
    const r = await apiFetch<{ forms: Form[] }>('/bonu/capture/').catch(() => null)
    if (!r) { setErr('Could not load the capture forms.'); setLoading(false); return }
    setForms(r.forms); setLoading(false)
  }
  useEffect(() => { load() }, [])

  const open = (f: Form) => {
    const seed: Record<string, string> = {}
    f.fields.forEach((fl) => { seed[fl.column] = fl.default ?? '' })
    setDraft(seed); setActive(f); setErr(null); setDone(null); setReadMsg(null)
    setCapBlock(null); setCapReason('')
  }
  const back = () => {
    setActive(null); setErr(null); setReadMsg(null); setCapBlock(null); setCapReason('')
  }

  // Supplier form only: read an uploaded invoice and pre-fill the figures. The
  // reader never fills the customer — the accountant attaches that by hand.
  const readInvoice = async (file: File | null) => {
    if (!file || !active) return
    setReading(true); setReadMsg(null); setErr(null)
    try {
      const fd = new FormData(); fd.append('file', file)
      const r = await apiFetch<{ ok: boolean; needs_manual?: boolean; message?: string; values?: Record<string, string> }>(
        '/bonu/capture/supplier/read/', { method: 'POST', body: fd },
      )
      if (r.values && Object.keys(r.values).length) {
        setDraft((d) => ({ ...d, ...r.values }))
      }
      setReadMsg(r.message || (r.ok ? 'Read. Check the figures and attach the customer.'
        : 'Could not read that file — please type it in.'))
    } catch {
      setReadMsg('Could not read that file — please type the bill in below.')
    } finally { setReading(false) }
  }

  const save = async (override = false) => {
    if (!active) return
    setBusy(true); setErr(null)
    try {
      const body: Record<string, unknown> = { values: draft }
      if (override) { body.override = true; body.override_reason = capReason }
      const r = await apiFetch<{ sheet_total: string; row_count: number }>(
        `/bonu/capture/${active.key}/`,
        { method: 'POST', body: JSON.stringify(body) },
      )
      setDone(`Recorded. ${active.title.replace('Record ', '').replace(/^a /, '')} saved — `
        + `this schedule now totals ${money2(num(r.sheet_total) ?? 0)} across ${r.row_count} line(s).`)
      setCapBlock(null); setCapReason('')
      // reset the form for the next entry, keeping the date for a quick run of them
      const keepDate = draft['Date'] || draft['Inv Date'] || ''
      const seed: Record<string, string> = {}
      active.fields.forEach((fl) => {
        seed[fl.column] = fl.kind === 'date' && keepDate ? keepDate : (fl.default ?? '')
      })
      setDraft(seed)
      load()
    } catch (e: any) {
      const code = e?.body?.code
      if (code === 'CAP_EXCEEDED') {
        setCapBlock({ detail: e.body.detail, remaining: e.body.remaining }); setErr(null)
      } else if (code === 'OVERRIDE_REASON_REQUIRED') {
        setErr('Type a reason to record a bill over the P90,000 cap.')
      } else {
        setCapBlock(null)
        setErr(e?.body?.detail || e?.message?.replace(/^\d+\s*/, '') || 'Could not save. Check the required fields.')
      }
    } finally { setBusy(false) }
  }

  if (loading) {
    return (
      <>
        <TopBar title="BONU" />
        <div className="p-6"><Loader2 className="animate-spin" style={{ color: ORANGE }} /></div>
      </>
    )
  }

  return (
    <>
      <TopBar title="BONU" />
      <div className="mx-auto max-w-4xl space-y-4 p-4 sm:p-6">
        <BonuTabs active="/bonu/capture" />

        {!active ? (
          <>
            <div>
              <h1 className="text-[22px] font-bold" style={{ color: NAVY }}>Capture</h1>
              <p className="mt-1 text-[13px]" style={{ color: '#6B7280' }}>
                Record what BONU earns and what it spends, one line at a time. Pick what you are
                recording — the form asks only for what that line needs, and files it in the right
                schedule. No spreadsheet.
              </p>
            </div>

            <Note tone="info" title="What each button does">
              Everything you record here lands in the BONU schedule and updates its totals and
              profit-and-loss straight away. It does <b>not</b> post to the general ledger — the
              monthly ledger journal stays a Finance sign-off, with the correct BONU accounts.
            </Note>

            <div className="grid gap-3 sm:grid-cols-2">
              {forms.map((f) => {
                const Icon = ICONS[f.icon] ?? Receipt
                return (
                  <button
                    key={f.key}
                    onClick={() => open(f)}
                    className="group rounded-xl border p-4 text-left transition-shadow duration-150 hover:shadow-md"
                    style={{ borderColor: LINE, background: 'white' }}
                  >
                    <div className="flex items-start gap-3">
                      <div className="rounded-lg p-2" style={{ background: '#FFF7EC' }}>
                        <Icon size={20} style={{ color: ORANGE }} />
                      </div>
                      <div className="min-w-0">
                        <div className="text-[15px] font-bold" style={{ color: NAVY }}>{f.title}</div>
                        <div className="mt-1 text-[12px] leading-relaxed" style={{ color: '#6B7280' }}>
                          {f.blurb}
                        </div>
                        <div className="mt-2 text-[11px]" style={{ color: '#9AA5B1' }}>
                          {f.sheet_exists
                            ? `${f.row_count} line(s) · total ${money2(num(f.sheet_total) ?? 0)}`
                            : 'No lines yet — your first one starts this schedule.'}
                        </div>
                      </div>
                    </div>
                  </button>
                )
              })}
            </div>
          </>
        ) : (
          <>
            <button onClick={back} className="flex items-center gap-1 text-[13px] font-medium"
              style={{ color: '#6B7280' }}>
              <ArrowLeft size={15} /> All capture options
            </button>

            <Card>
              <CardContent className="space-y-4 p-5">
                <div>
                  <h1 className="text-[19px] font-bold" style={{ color: NAVY }}>{active.title}</h1>
                  <p className="mt-1 text-[12px]" style={{ color: '#6B7280' }}>{active.blurb}</p>
                </div>

                {active.key === 'supplier' ? (
                  <div className="rounded-xl p-4" style={{ background: '#FFF7EC', border: `1px solid #F6DFB4` }}>
                    <div className="flex items-center gap-2">
                      <Sparkles size={16} style={{ color: ORANGE }} />
                      <span className="text-[13px] font-bold" style={{ color: NAVY }}>
                        Load the supplier invoice here
                      </span>
                    </div>
                    <p className="mt-1 text-[12px]" style={{ color: '#6B7280' }}>
                      Drop an Excel or PDF invoice and Omni reads the figures for you — invoice
                      number, date and amount. Then choose the firm and <b>attach the customer</b>:
                      every supplier bill must be tied to the member it was incurred for.
                    </p>
                    <label className="mt-3 inline-flex cursor-pointer items-center gap-2 rounded-lg px-4 py-2 text-[13px] font-semibold text-white"
                      style={{ background: ORANGE }}>
                      {reading ? <Loader2 size={15} className="animate-spin" /> : <Upload size={15} />}
                      {reading ? 'Reading…' : 'Choose invoice file'}
                      <input type="file" accept=".xlsx,.xls,.csv,.pdf" className="hidden"
                        disabled={reading}
                        onChange={(e) => readInvoice(e.target.files?.[0] ?? null)} />
                    </label>
                    {readMsg ? (
                      <div className="mt-2 text-[12px]" style={{ color: NAVY }}>{readMsg}</div>
                    ) : null}
                  </div>
                ) : null}

                {done ? (
                  <div className="flex items-start gap-2 rounded-lg px-3 py-2 text-[13px]"
                    style={{ background: '#F0FDF4', border: `1px solid #C7EBD9`, color: '#166534' }}>
                    <Check size={16} className="mt-[2px] shrink-0" style={{ color: GREEN }} />
                    <span>{done}</span>
                  </div>
                ) : null}
                {err ? (
                  <div className="rounded-lg px-3 py-2 text-[13px]"
                    style={{ background: '#FEF7F7', border: `1px solid #F3D6D6`, color: RED }}>
                    {err}
                  </div>
                ) : null}

                <div className="grid gap-3 sm:grid-cols-2">
                  {active.fields.map((fl) => (
                    <label key={fl.column} className="block">
                      <span className="text-[12px] font-medium" style={{ color: NAVY }}>
                        {fl.label}{fl.required ? <span style={{ color: RED }}> *</span> : null}
                      </span>
                      {fl.kind === 'select' ? (
                        <select
                          value={draft[fl.column] ?? ''}
                          onChange={(e) => setDraft({ ...draft, [fl.column]: e.target.value })}
                          className="mt-1 w-full rounded-lg border px-3 py-2 text-[13px]"
                          style={{ borderColor: LINE, color: NAVY }}
                        >
                          <option value="">—</option>
                          {(fl.options ?? []).map((o) => <option key={o} value={o}>{o}</option>)}
                        </select>
                      ) : (
                        <input
                          type={fl.kind === 'date' ? 'date' : 'text'}
                          inputMode={fl.kind === 'money' ? 'decimal' : undefined}
                          value={draft[fl.column] ?? ''}
                          onChange={(e) => setDraft({ ...draft, [fl.column]: e.target.value })}
                          placeholder={fl.kind === 'money' ? '0.00' : ''}
                          className="mt-1 w-full rounded-lg border px-3 py-2 text-[13px]"
                          style={{ borderColor: LINE, color: NAVY }}
                        />
                      )}
                      {fl.help ? (
                        <span className="mt-1 block text-[11px]" style={{ color: '#9AA5B1' }}>{fl.help}</span>
                      ) : null}
                    </label>
                  ))}
                </div>

                {capBlock ? (
                  <div className="rounded-xl p-4" style={{ background: '#FFF7EC', border: `1px solid #F6DFB4` }}>
                    <div className="text-[13px] font-bold" style={{ color: AMBER }}>
                      Over the P90,000 member cap
                    </div>
                    <p className="mt-1 text-[12px]" style={{ color: '#374151' }}>{capBlock.detail}</p>
                    <label className="mt-3 block text-[12px] font-medium" style={{ color: NAVY }}>
                      Reason to record it anyway<span style={{ color: RED }}> *</span>
                      <textarea
                        value={capReason}
                        onChange={(e) => setCapReason(e.target.value)}
                        rows={2}
                        placeholder="e.g. appeal authorised by the union; benefit top-up approved."
                        className="mt-1 w-full rounded-lg border px-3 py-2 text-[13px]"
                        style={{ borderColor: LINE, color: NAVY }}
                      />
                    </label>
                    <button
                      onClick={() => save(true)}
                      disabled={busy || !capReason.trim()}
                      className="mt-2 rounded-lg px-4 py-2 text-[13px] font-semibold text-white disabled:opacity-60"
                      style={{ background: AMBER }}
                    >
                      {busy ? <Loader2 size={15} className="inline animate-spin" /> : 'Record over the cap'}
                    </button>
                  </div>
                ) : null}

                <div className="flex items-center gap-3 pt-1">
                  <button
                    onClick={() => save()}
                    disabled={busy}
                    className="rounded-lg px-5 py-2 text-[13px] font-semibold text-white disabled:opacity-60"
                    style={{ background: NAVY }}
                  >
                    {busy ? <Loader2 size={15} className="inline animate-spin" /> : 'Record this line'}
                  </button>
                  <span className="text-[11px]" style={{ color: '#9AA5B1' }}>
                    <span style={{ color: RED }}>*</span> required. The form clears for the next entry.
                  </span>
                </div>
              </CardContent>
            </Card>
          </>
        )}
      </div>
    </>
  )
}
