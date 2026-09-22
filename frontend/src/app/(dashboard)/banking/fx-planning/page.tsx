'use client'

/**
 * /banking/fx-planning — FX Payment Planning (CFO 2026-08-24).
 *
 * Alpha Direct makes many USD and ZAR foreign payments each month, captured
 * directly on FNB. Because nobody plans ahead they pile up in one day. This
 * board is the forward view: a driver-based forecast of how much USD and ZAR
 * will be needed and when, so the team schedules and pre-funds forex.
 *
 * Phase 1 driver = "clockwork" (recurring payees learned from the FNB Forex
 * download). Every predicted line traces to a reason, so the CFO can defend it.
 * Numbers come from the server (fx_planning/services.py); nothing is guessed
 * here. Colours come from useTheme() so it follows the ERP themes; Book Antiqua
 * arrives via font-sans.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { useTheme } from '@/contexts/ThemeContext'
import {
  getFxCalendar, getFxPayees, getFxBacktest, importFxFile, rebuildFxForecast,
  raiseFxPlanned, createFxPlanned, updateFxPayee, deleteFxPlanned,
  type FxCalendar, type FxPayee, type FxPlannedLine, type FxBacktest,
} from '@/lib/api'
import {
  Upload, RefreshCw, CalendarClock, Loader2, Plus, ArrowUpRight, Globe,
  AlertTriangle, CheckCircle2, Info, ChevronDown, ChevronRight, Target, Eye,
} from 'lucide-react'

const WEEK_CHOICES = [4, 8, 12, 26]

/** Exact money — a treasury board never abbreviates. */
function money(v: string | number | null | undefined): string {
  const n = Number(v ?? 0)
  return n.toLocaleString('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

const DRIVER_LABEL: Record<string, string> = {
  recurring: 'Recurring', reinsurance: 'Reinsurance', claim: 'Claim', manual: 'Manual',
}

export default function FxPlanningPage() {
  const { theme: t } = useTheme()
  const [weeks, setWeeks] = useState(12)
  const [cal, setCal] = useState<FxCalendar | null>(null)
  const [payees, setPayees] = useState<FxPayee[]>([])
  const [backtest, setBacktest] = useState<FxBacktest | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<string>('')
  const [msg, setMsg] = useState<{ kind: 'ok' | 'err' | 'info'; text: string } | null>(null)
  const [showPayees, setShowPayees] = useState(false)
  const [showAccuracy, setShowAccuracy] = useState(false)
  const [showAdd, setShowAdd] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const [addForm, setAddForm] = useState({
    beneficiary: '', currency: 'USD', expected_amount: '',
    expected_value_date: '', source_account: '', notes: '',
  })

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [c, p, b] = await Promise.all([
        getFxCalendar(weeks), getFxPayees(), getFxBacktest(3).catch(() => null),
      ])
      setCal(c); setPayees(p); setBacktest(b)
    } catch (e: any) {
      setMsg({ kind: 'err', text: e?.message || 'Could not load the FX plan.' })
    } finally {
      setLoading(false)
    }
  }, [weeks])

  useEffect(() => { load() }, [load])

  async function onUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0]
    if (!f) return
    setBusy('import'); setMsg(null)
    try {
      const r = await importFxFile(f)
      setMsg({
        kind: 'ok',
        text: `Loaded ${r.import.imported_count} new payment(s). `
          + `${r.payees_detected} payee(s) analysed, ${r.planned_created} planned line(s) added.`,
      })
      await load()
    } catch (err: any) {
      setMsg({ kind: 'err', text: err?.message || 'Import failed.' })
    } finally {
      setBusy(''); if (fileRef.current) fileRef.current.value = ''
    }
  }

  async function onRebuild() {
    setBusy('rebuild'); setMsg(null)
    try {
      const r = await rebuildFxForecast()
      setMsg({ kind: 'ok', text: `Forecast rebuilt: ${r.planned_created} new planned line(s).` })
      await load()
    } catch (err: any) {
      setMsg({ kind: 'err', text: err?.message || 'Rebuild failed.' })
    } finally { setBusy('') }
  }

  async function onRaise(line: FxPlannedLine) {
    setBusy(line.id); setMsg(null)
    try {
      const r = await raiseFxPlanned(line.id)
      setMsg({ kind: 'ok', text: `Payment request ${r.payment_request_ref} raised — now in the finance queue.` })
      await load()
    } catch (err: any) {
      setMsg({ kind: 'err', text: err?.message || 'Could not raise the payment request.' })
    } finally { setBusy('') }
  }

  // Type a known foreign payment straight in (the save call existed but was never
  // surfaced — CFO 2026-08-26). It lands on the calendar like any other line.
  async function onAddPlanned() {
    if (!addForm.beneficiary.trim() || !addForm.expected_amount.trim() || !addForm.expected_value_date) {
      setMsg({ kind: 'err', text: 'Payee, amount and value date are needed to add a payment.' })
      return
    }
    setBusy('add'); setMsg(null)
    try {
      await createFxPlanned({
        beneficiary: addForm.beneficiary.trim(),
        currency: addForm.currency,
        expected_amount: addForm.expected_amount.trim(),
        expected_value_date: addForm.expected_value_date,
        source_account: addForm.source_account.trim() || undefined,
        notes: addForm.notes.trim() || undefined,
      })
      setMsg({ kind: 'ok', text: 'Payment added to the plan.' })
      setShowAdd(false)
      setAddForm({ beneficiary: '', currency: 'USD', expected_amount: '', expected_value_date: '', source_account: '', notes: '' })
      await load()
    } catch (err: any) {
      setMsg({ kind: 'err', text: err?.message || 'Could not add the payment.' })
    } finally { setBusy('') }
  }

  const totals = cal?.totals || {}
  const currencies = cal?.currencies || []
  // Worst-case = the highest stress percentage on file (rate-risk buffer, FX-002).
  const stressPcts = cal?.stress_pcts || []
  const worstPct = stressPcts.length ? stressPcts[stressPcts.length - 1] : null
  const worstOf = (m?: Record<string, string>) =>
    worstPct && m ? m[worstPct] : undefined
  const watchPayees = payees.filter((p) => p.watch && !p.active)

  const badge = (kind: string) => {
    const bg = kind === 'ok' ? t.okB : kind === 'err' ? t.erB : t.inf
    const fg = kind === 'ok' ? t.ok : kind === 'err' ? t.er : t.navy
    return { background: bg, color: fg }
  }

  return (
    <div className="min-h-screen font-sans" style={{ background: t.bg, color: t.text }}>
      <TopBar title="FX Payment Planning" breadcrumbs={[{ label: 'Banking' }]} />

      <div className="mx-auto max-w-6xl px-4 py-6 space-y-6">
        {/* Intro + controls */}
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="max-w-2xl">
            <div className="flex items-center gap-2 text-lg font-semibold" style={{ color: t.navy }}>
              <Globe size={20} /> Load your forex payments — the AI predicts when to pay
            </div>
            <p className="text-sm mt-1" style={{ color: t.t2 }}>
              Type each USD or ZAR payment you know is coming. The board projects them onto a
              calendar so the team plans and pre-funds forex, instead of doing them all in one day.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <select
              value={weeks}
              onChange={(e) => setWeeks(Number(e.target.value))}
              aria-label="Weeks to project"
              className="rounded-md px-2 py-1.5 text-sm border"
              style={{ background: t.input, borderColor: t.cardBdr, color: t.text }}
            >
              {WEEK_CHOICES.map((w) => <option key={w} value={w}>Next {w} weeks</option>)}
            </select>
            <Button onClick={() => { setShowAdd((v) => !v); setMsg(null) }}>
              <Plus size={16} /><span className="ml-1">Add a payment</span>
            </Button>
            <Button variant="outline" onClick={onRebuild} disabled={busy === 'rebuild'}>
              {busy === 'rebuild' ? <Loader2 size={16} className="animate-spin" /> : <RefreshCw size={16} />}
              <span className="ml-1">Rebuild</span>
            </Button>
          </div>
        </div>

        {/* Optional shortcut — demoted from a top button (CFO 2026-08-26). */}
        <div className="text-xs flex items-center gap-2" style={{ color: t.t3 }}>
          <input ref={fileRef} type="file" accept=".pdf,.csv,.xlsx,.xlsm" className="hidden" onChange={onUpload} />
          <span>Shortcut, optional:</span>
          <button onClick={() => fileRef.current?.click()} disabled={busy === 'import'}
                  className="inline-flex items-center gap-1 underline disabled:opacity-50" style={{ color: t.navy }}>
            {busy === 'import' ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
            Upload the FNB “Forex” download
          </button>
          <span>to auto-learn your recurring payees instead of typing them.</span>
        </div>

        {/* Add-a-payment form */}
        {showAdd && (
          <Card><CardContent className="py-4">
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              <label className="text-sm">Payee
                <input value={addForm.beneficiary} onChange={(e) => setAddForm({ ...addForm, beneficiary: e.target.value })}
                  placeholder="e.g. Munich Re" className="mt-1 w-full rounded-md px-2 py-1.5 text-sm border"
                  style={{ background: t.input, borderColor: t.cardBdr, color: t.text }} />
              </label>
              <label className="text-sm">Currency
                <select value={addForm.currency} onChange={(e) => setAddForm({ ...addForm, currency: e.target.value })}
                  className="mt-1 w-full rounded-md px-2 py-1.5 text-sm border"
                  style={{ background: t.input, borderColor: t.cardBdr, color: t.text }}>
                  <option value="USD">USD</option>
                  <option value="ZAR">ZAR</option>
                  <option value="EUR">EUR</option>
                  <option value="GBP">GBP</option>
                </select>
              </label>
              <label className="text-sm">Amount
                <input value={addForm.expected_amount} onChange={(e) => setAddForm({ ...addForm, expected_amount: e.target.value })}
                  inputMode="decimal" placeholder="0.00" className="mt-1 w-full rounded-md px-2 py-1.5 text-sm border"
                  style={{ background: t.input, borderColor: t.cardBdr, color: t.text }} />
              </label>
              <label className="text-sm">Value date
                <input type="date" value={addForm.expected_value_date} onChange={(e) => setAddForm({ ...addForm, expected_value_date: e.target.value })}
                  className="mt-1 w-full rounded-md px-2 py-1.5 text-sm border"
                  style={{ background: t.input, borderColor: t.cardBdr, color: t.text }} />
              </label>
              <label className="text-sm">From account (optional)
                <input value={addForm.source_account} onChange={(e) => setAddForm({ ...addForm, source_account: e.target.value })}
                  placeholder="e.g. FNB USD" className="mt-1 w-full rounded-md px-2 py-1.5 text-sm border"
                  style={{ background: t.input, borderColor: t.cardBdr, color: t.text }} />
              </label>
              <label className="text-sm">Note (optional)
                <input value={addForm.notes} onChange={(e) => setAddForm({ ...addForm, notes: e.target.value })}
                  placeholder="what it is for" className="mt-1 w-full rounded-md px-2 py-1.5 text-sm border"
                  style={{ background: t.input, borderColor: t.cardBdr, color: t.text }} />
              </label>
            </div>
            <div className="flex gap-2 mt-3">
              <Button onClick={onAddPlanned} disabled={busy === 'add'}>
                {busy === 'add' ? <Loader2 size={16} className="animate-spin" /> : <Plus size={16} />}
                <span className="ml-1">Add to the plan</span>
              </Button>
              <Button variant="outline" onClick={() => setShowAdd(false)} disabled={busy === 'add'}>Cancel</Button>
            </div>
          </CardContent></Card>
        )}

        {msg && (
          <div className="rounded-md px-3 py-2 text-sm flex items-center gap-2" style={badge(msg.kind)}>
            {msg.kind === 'ok' ? <CheckCircle2 size={16} /> : msg.kind === 'err'
              ? <AlertTriangle size={16} /> : <Info size={16} />}
            {msg.text}
          </div>
        )}

        {loading ? (
          <div className="flex items-center gap-2 text-sm py-16 justify-center" style={{ color: t.t2 }}>
            <Loader2 className="animate-spin" size={18} /> Loading the plan…
          </div>
        ) : !cal || cal.weeks.every((w) => w.lines.length === 0) ? (
          <Card><CardContent className="py-12 text-center">
            <CalendarClock size={28} className="mx-auto mb-2" style={{ color: t.t3 }} />
            <div className="font-medium">No planned foreign payments yet</div>
            <p className="text-sm mt-1" style={{ color: t.t2 }}>
              Upload the FNB “Forex” history download (the PDF you get from Online Banking)
              and the recurring payees will appear on the calendar automatically.
            </p>
          </CardContent></Card>
        ) : (
          <>
            {/* Headline totals */}
            <div className="grid gap-3" style={{ gridTemplateColumns: `repeat(${Math.min(currencies.length + 1, 4)}, minmax(0,1fr))` }}>
              {currencies.map((c) => (
                <Card key={c}><CardContent className="py-4">
                  <div className="text-xs uppercase tracking-wide" style={{ color: t.t3 }}>
                    {c} needed · next {weeks}w
                  </div>
                  <div className="text-2xl font-semibold mt-1" style={{ color: t.navy }}>
                    {c} {money(totals[c]?.amount)}
                  </div>
                  <div className="text-xs mt-1" style={{ color: t.t2 }}>
                    ≈ P {money(totals[c]?.bwp)} · {totals[c]?.count} payment(s)
                  </div>
                </CardContent></Card>
              ))}
              <Card><CardContent className="py-4">
                <div className="text-xs uppercase tracking-wide" style={{ color: t.t3 }}>
                  Total pula impact
                </div>
                <div className="text-2xl font-semibold mt-1" style={{ color: t.orangeText }}>
                  P {money(cal.bwp_total)}
                </div>
                {worstPct && worstOf(cal.bwp_total_stressed) ? (
                  <div className="text-xs mt-1 flex items-center gap-1" style={{ color: t.er }}>
                    <AlertTriangle size={12} />
                    worst case P {money(worstOf(cal.bwp_total_stressed))} if pula weakens {worstPct}%
                  </div>
                ) : (
                  <div className="text-xs mt-1" style={{ color: t.t2 }}>estimate at latest rates</div>
                )}
              </CardContent></Card>
            </div>

            {/* Per-account funding */}
            {cal.by_account.length > 0 && (
              <Card><CardContent className="py-4">
                <div className="font-medium mb-2" style={{ color: t.navy }}>Fund these accounts</div>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead><tr style={{ color: t.t3 }} className="text-left">
                      <th className="py-1 pr-4 font-medium">FNB account</th>
                      <th className="py-1 pr-4 font-medium">Currency</th>
                      <th className="py-1 pr-4 font-medium text-right">Needed</th>
                      <th className="py-1 pr-4 font-medium text-right">≈ Pula</th>
                      {worstPct && <th className="py-1 pr-4 font-medium text-right">Worst case (+{worstPct}%)</th>}
                      <th className="py-1 font-medium text-right">Payments</th>
                    </tr></thead>
                    <tbody>
                      {cal.by_account.map((a, i) => (
                        <tr key={i} style={{ borderTop: `1px solid ${t.cardBdr}` }}>
                          <td className="py-1.5 pr-4 font-mono text-xs">{a.account}</td>
                          <td className="py-1.5 pr-4">{a.currency}</td>
                          <td className="py-1.5 pr-4 text-right">{a.currency} {money(a.amount)}</td>
                          <td className="py-1.5 pr-4 text-right" style={{ color: t.t2 }}>P {money(a.bwp)}</td>
                          {worstPct && (
                            <td className="py-1.5 pr-4 text-right" style={{ color: t.er }}>
                              P {money(worstOf(a.bwp_stressed))}
                            </td>
                          )}
                          <td className="py-1.5 text-right">{a.count}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </CardContent></Card>
            )}

            {/* Weekly calendar */}
            <div className="space-y-3">
              {cal.weeks.filter((w) => w.lines.length > 0).map((w) => (
                <Card key={w.week_start}><CardContent className="py-3">
                  <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
                    <div className="font-medium" style={{ color: t.navy }}>
                      Week of {w.label}
                    </div>
                    <div className="flex gap-3 text-xs" style={{ color: t.t2 }}>
                      {Object.entries(w.by_currency).map(([c, v]) => (
                        <span key={c}><b style={{ color: t.text }}>{c} {money(v.amount)}</b> ({v.count})</span>
                      ))}
                    </div>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <tbody>
                        {w.lines.map((line) => (
                          <tr key={line.id} style={{ borderTop: `1px solid ${t.cardBdr}` }}>
                            <td className="py-1.5 pr-3 whitespace-nowrap" style={{ color: t.t2 }}>
                              {line.expected_value_date}
                            </td>
                            <td className="py-1.5 pr-3">{line.beneficiary}</td>
                            <td className="py-1.5 pr-3 text-right whitespace-nowrap font-medium">
                              {line.currency} {money(line.expected_amount)}
                            </td>
                            <td className="py-1.5 pr-3 text-right whitespace-nowrap text-xs" style={{ color: t.t3 }}>
                              ≈ P {money(line.estimated_bwp)}{line.rate_is_estimate ? '*' : ''}
                            </td>
                            <td className="py-1.5 pr-3 text-xs">
                              <span className="rounded px-1.5 py-0.5" style={{ background: t.g50, color: t.t2 }}>
                                {DRIVER_LABEL[line.driver] || line.driver}
                              </span>
                            </td>
                            <td className="py-1.5 text-right whitespace-nowrap">
                              {line.status === 'requested' && line.payment_request_ref ? (
                                <span className="text-xs" style={{ color: t.ok }}>
                                  <CheckCircle2 size={13} className="inline mr-1" />{line.payment_request_ref}
                                </span>
                              ) : line.status === 'planned' ? (
                                <Button size="sm" variant="outline" onClick={() => onRaise(line)}
                                        disabled={busy === line.id}>
                                  {busy === line.id
                                    ? <Loader2 size={13} className="animate-spin" />
                                    : <ArrowUpRight size={13} />}
                                  <span className="ml-1">Raise request</span>
                                </Button>
                              ) : (
                                <span className="text-xs" style={{ color: t.t3 }}>{line.status}</span>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </CardContent></Card>
              ))}
            </div>
            <p className="text-xs" style={{ color: t.t3 }}>
              * Pula figure uses the latest rate on file, not yet a Finance-approved rate — a planning estimate only.
            </p>

            {/* Forecast accuracy scorecard */}
            {backtest && backtest.has_data && (
              <Card><CardContent className="py-3">
                <button className="flex items-center gap-1 font-medium w-full"
                        style={{ color: t.navy }} onClick={() => setShowAccuracy((s) => !s)}>
                  {showAccuracy ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                  <Target size={15} className="ml-0.5" />
                  Forecast accuracy
                  <span className="ml-2 rounded px-1.5 py-0.5 text-xs font-semibold"
                        style={badge(backtest.overall_accuracy_pct >= 80 ? 'ok'
                          : backtest.overall_accuracy_pct >= 60 ? 'info' : 'err')}>
                    {backtest.overall_accuracy_pct}% over last {backtest.periods.length} month(s)
                  </span>
                </button>
                {showAccuracy && (
                  <div className="overflow-x-auto mt-3">
                    <table className="w-full text-sm">
                      <thead><tr style={{ color: t.t3 }} className="text-left">
                        <th className="py-1 pr-4 font-medium">Month</th>
                        <th className="py-1 pr-4 font-medium text-right">Predicted</th>
                        <th className="py-1 pr-4 font-medium text-right">Actual</th>
                        <th className="py-1 pr-4 font-medium text-right">Predicted P</th>
                        <th className="py-1 pr-4 font-medium text-right">Actual P</th>
                        <th className="py-1 font-medium text-right">Accuracy</th>
                      </tr></thead>
                      <tbody>
                        {backtest.periods.map((pd) => (
                          <tr key={`${pd.year}-${pd.month}`} style={{ borderTop: `1px solid ${t.cardBdr}` }}>
                            <td className="py-1.5 pr-4">{pd.label}</td>
                            <td className="py-1.5 pr-4 text-right">{pd.predicted_count}</td>
                            <td className="py-1.5 pr-4 text-right">{pd.actual_count}</td>
                            <td className="py-1.5 pr-4 text-right" style={{ color: t.t2 }}>P {money(pd.predicted_bwp)}</td>
                            <td className="py-1.5 pr-4 text-right" style={{ color: t.t2 }}>P {money(pd.actual_bwp)}</td>
                            <td className="py-1.5 text-right font-medium"
                                style={{ color: pd.accuracy_pct >= 80 ? t.ok : pd.accuracy_pct >= 60 ? t.navy : t.er }}>
                              {pd.accuracy_pct}%
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    <p className="text-xs mt-2" style={{ color: t.t3 }}>
                      How close the plan was to what actually left the bank each month — the higher, the more you can trust the forward numbers.
                    </p>
                  </div>
                )}
              </CardContent></Card>
            )}

            {/* Watch list — material payees that aren't clockwork */}
            {watchPayees.length > 0 && (
              <Card><CardContent className="py-3">
                <div className="flex items-center gap-1.5 font-medium mb-1" style={{ color: t.navy }}>
                  <Eye size={15} /> Watch — plan these by hand ({watchPayees.length})
                </div>
                <p className="text-xs mb-2" style={{ color: t.t2 }}>
                  Seen more than once but not on a steady monthly/quarterly rhythm, so they are not
                  auto-added to the calendar. Don’t forget them — add them manually when due.
                </p>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <tbody>
                      {watchPayees.map((p) => (
                        <tr key={p.id} style={{ borderTop: `1px solid ${t.cardBdr}` }}>
                          <td className="py-1.5 pr-4">{p.display_name}</td>
                          <td className="py-1.5 pr-4">{p.currency}</td>
                          <td className="py-1.5 pr-4 text-right">
                            {p.amount_varies && p.amount_min && p.amount_max
                              ? `${money(p.amount_min)} – ${money(p.amount_max)}`
                              : money(p.typical_amount)}
                          </td>
                          <td className="py-1.5 pr-4" style={{ color: t.t3 }}>{p.cadence}</td>
                          <td className="py-1.5 text-right" style={{ color: t.t3 }}>seen {p.occurrences}×</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </CardContent></Card>
            )}

            {/* Recurring payees */}
            <Card><CardContent className="py-3">
              <button className="flex items-center gap-1 font-medium"
                      style={{ color: t.navy }} onClick={() => setShowPayees((s) => !s)}>
                {showPayees ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                Recurring payees ({payees.filter((p) => p.active).length} active)
              </button>
              {showPayees && (
                <div className="overflow-x-auto mt-3">
                  <table className="w-full text-sm">
                    <thead><tr style={{ color: t.t3 }} className="text-left">
                      <th className="py-1 pr-4 font-medium">Payee</th>
                      <th className="py-1 pr-4 font-medium">Ccy</th>
                      <th className="py-1 pr-4 font-medium text-right">Typical</th>
                      <th className="py-1 pr-4 font-medium">Cadence</th>
                      <th className="py-1 pr-4 font-medium text-right">Seen</th>
                      <th className="py-1 pr-4 font-medium text-right">Confidence</th>
                      <th className="py-1 font-medium text-right">In plan?</th>
                    </tr></thead>
                    <tbody>
                      {payees.map((p) => (
                        <tr key={p.id} style={{ borderTop: `1px solid ${t.cardBdr}` }}>
                          <td className="py-1.5 pr-4">{p.display_name}</td>
                          <td className="py-1.5 pr-4">{p.currency}</td>
                          <td className="py-1.5 pr-4 text-right">
                            {money(p.typical_amount)}
                            {p.amount_varies && p.amount_min && p.amount_max && (
                              <span className="block text-xs" style={{ color: t.er }}>
                                {money(p.amount_min)} – {money(p.amount_max)}
                              </span>
                            )}
                          </td>
                          <td className="py-1.5 pr-4">{p.cadence}</td>
                          <td className="py-1.5 pr-4 text-right">{p.occurrences}× / {p.months_active}m</td>
                          <td className="py-1.5 pr-4 text-right">{p.confidence}%</td>
                          <td className="py-1.5 text-right">
                            <input type="checkbox" checked={p.active}
                              onChange={async (e) => {
                                try {
                                  await updateFxPayee(p.id, { active: e.target.checked })
                                  await load()
                                } catch (err: any) {
                                  setMsg({ kind: 'err', text: err?.message || 'Update failed.' })
                                }
                              }} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </CardContent></Card>
          </>
        )}
      </div>
    </div>
  )
}
