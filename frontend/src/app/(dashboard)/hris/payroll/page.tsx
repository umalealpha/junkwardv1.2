'use client'

/**
 * /hris/payroll — Monthly payroll prep surface.
 *
 * CFO directive 2026-05-21: monthly workflow = upload last month's
 * amendments xlsx → system applies them to the baseline period → new
 * period's payslips appear with AI review on top.
 *
 * Three actions on this page:
 *   1. Pick target period (= the month being CREATED) + baseline period.
 *   2. Upload amendments xlsx — returns parse preview + per-row errors.
 *   3. Apply batch — copies baseline payslips + applies amendments.
 *   4. AI Review — Aria narrative + anomaly flags.
 *
 * The four operations are auth-gated server-side to CFO / Finance Manager
 * / Financial Controller.
 */
import { useEffect, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import {
  ChevronLeft, Upload, FileSpreadsheet, Sparkles, CheckCircle2,
  AlertCircle, Loader2, ArrowRight, Wallet,
} from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { useHrisAccess } from '@/hooks/useHrisAccess'
import { authedHrisFetch, fmtPula } from '../_shared'

interface PayrollPeriod {
  id: string
  period_name: string
  start_date: string
  end_date: string
  status?: string
}

interface AmendmentRow {
  id: string
  employee?: string | null
  employee_ref?: string
  kind: string
  component?: string | null
  amount: string
  reason?: string
  approver?: string
  applied?: boolean
  resolution_error?: string
}

interface AiRow { employee: string; kind: string; amount: string; component: string; reason: string }
interface BatchResp {
  batch_id: string
  rows_created: number
  parse_errors: string[]
  status: string
  ai_rows?: AiRow[]
  redactions_made?: number
}

interface PendingBatch {
  id: string
  target_period: string
  baseline_period: string
  row_count: number
  file_name: string
  uploaded_by: string
  uploaded_by_me: boolean
  created_at: string
  can_apply: boolean
}

interface Preflight {
  rows: number
  headcount: { baseline: number; joiners: number; leavers: number; projected: number }
  gross: { baseline: string; estimated_change: string; estimated_after: string; note: string }
  exceptions: {
    unmatched_rows: { ref: string; reason: string }[]
    unmatched_count: number
    no_baseline_payslip: string[]
    duplicate_component_rows: number
    negative_earning_rows: string[]
  }
  ready: boolean
  period_open: boolean
}

interface ApplyResp {
  batch_id: string
  baseline_period: string
  target_period: string
  payslips_created: number
  payslips_updated: number
  amendments_applied: number
  amendments_failed: number
  errors: string[]
}

interface AIResp {
  summary: Record<string, string>
  prior: Record<string, string> | null
  anomalies: { employee: string; flag: string; amount?: string }[]
  ai_narrative: string
  ai_unavailable_reason: string
}

/**
 * Prior → current only. Bug fix (Oprah, in-app report 2026-06-23): the picker
 * silently accepted baseline == target (overwrites a period with a copy of
 * itself) and baseline newer than target (rolls future payroll onto a closed
 * past month). Returns a human message, or '' when the pair is valid.
 */
function periodValidationError(
  periods: PayrollPeriod[], baselineId: string, targetId: string,
): string {
  if (!baselineId || !targetId) return ''
  if (baselineId === targetId) return 'Baseline and target must be different months.'
  const b = periods.find(p => p.id === baselineId)
  const t = periods.find(p => p.id === targetId)
  if (b && t && b.start_date >= t.start_date) {
    return `Baseline (${b.period_name}) must be an earlier month than the target (${t.period_name}). Payroll rolls prior → current, not backwards.`
  }
  return ''
}

export default function HrisPayrollPage() {
  const router = useRouter()
  const { theme } = useTheme()
  const hrisAllowed = useHrisAccess()

  // CFO directive 2026-05-21: Payroll is HR-team / HR-Manager / CFO /
  // Finance Manager / Financial Controller only. Probe the server so the
  // UI matches the backend gate exactly.
  const [payrollAllowed, setPayrollAllowed] = useState<boolean | undefined>(undefined)
  const [canApply, setCanApply] = useState(false)
  const [accessReason, setAccessReason] = useState('')

  const [periods, setPeriods] = useState<PayrollPeriod[]>([])
  const [targetId,   setTargetId]   = useState('')
  const [baselineId, setBaselineId] = useState('')
  const [busy, setBusy] = useState<null | 'upload' | 'apply' | 'ai' | 'aitext' | 'validate'>(null)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [aiText, setAiText] = useState('')

  const [batch, setBatch] = useState<BatchResp | null>(null)
  const [apply, setApply] = useState<ApplyResp | null>(null)
  const [pf, setPf] = useState<Preflight | null>(null)   // pre-flight: see-before-you-commit
  const [ai, setAi]       = useState<AIResp | null>(null)

  // Parsed batches awaiting apply (CFO 2026-07-23) — lets a DIFFERENT finance
  // approver pick up a batch the uploader is SoD-blocked from applying.
  const [pending, setPending] = useState<PendingBatch[]>([])
  const [applyingId, setApplyingId] = useState<string | null>(null)

  const fileRef = useRef<HTMLInputElement>(null)

  async function loadPending() {
    try {
      const r = await authedHrisFetch('/api/v1/payroll/amendment-batches/')
      if (r.ok) {
        const d = await r.json()
        setPending(Array.isArray(d.batches) ? d.batches : [])
      }
    } catch { /* non-critical — the wizard still works */ }
  }

  async function applyExisting(id: string) {
    setMsg(null); setApplyingId(id)
    try {
      const r = await authedHrisFetch(
        `/api/v1/payroll/amendment-batches/${id}/apply/`, { method: 'POST' })
      const data = await r.json().catch(() => ({}))
      if (!r.ok) { setMsg({ ok: false, text: data.detail || `HTTP ${r.status}` }); return }
      setMsg({
        ok: true,
        text: `Applied ${data.amendments_applied}/${data.amendments_applied + data.amendments_failed} amendments. ` +
              `Payslips: +${data.payslips_created} created, ${data.payslips_updated} updated.`,
      })
      await loadPending()
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : 'Apply failed' })
    } finally { setApplyingId(null) }
  }

  useEffect(() => {
    if (hrisAllowed === false) router.replace('/dashboard')
  }, [hrisAllowed, router])

  useEffect(() => {
    if (hrisAllowed !== true) return
    authedHrisFetch('/api/v1/payroll/access/')
      .then(async r => {
        if (!r.ok) { setPayrollAllowed(false); return }
        const data = await r.json()
        setPayrollAllowed(!!data.allowed)
        setCanApply(!!data.can_apply)
        setAccessReason(data.reason || '')
      })
      .catch(() => setPayrollAllowed(false))
  }, [hrisAllowed])

  useEffect(() => {
    if (payrollAllowed === false) router.replace('/dashboard')
  }, [payrollAllowed, router])

  useEffect(() => {
    if (payrollAllowed !== true) return
    authedHrisFetch('/api/v1/payroll-periods/?page_size=200')
      .then(async r => {
        if (r.ok) {
          const data = await r.json()
          const rows: PayrollPeriod[] = data?.results || data || []
          // Sort by start_date desc so the most recent month appears first
          rows.sort((a, b) => b.start_date.localeCompare(a.start_date))
          setPeriods(rows)
          if (rows[0] && !targetId)   setTargetId(rows[0].id)
          if (rows[1] && !baselineId) setBaselineId(rows[1].id)
        }
      })
      .catch(() => { /* unauth — page will redirect */ })
    loadPending()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [payrollAllowed])

  async function onUpload() {
    setMsg(null); setBatch(null); setApply(null); setAi(null)
    const f = fileRef.current?.files?.[0]
    if (!f) { setMsg({ ok: false, text: 'Pick an Excel file first (.xlsx or .xls).' }); return }
    if (!targetId || !baselineId) {
      setMsg({ ok: false, text: 'Choose target + baseline periods.' }); return
    }
    const pErr = periodValidationError(periods, baselineId, targetId)
    if (pErr) { setMsg({ ok: false, text: pErr }); return }
    setBusy('upload')
    try {
      const fd = new FormData()
      fd.append('file', f)
      fd.append('target_period_id', targetId)
      fd.append('baseline_period_id', baselineId)
      const r = await authedHrisFetch('/api/v1/payroll/amendments/upload/', {
        method: 'POST',
        body: fd,
      })
      const data = await r.json().catch(() => ({}))
      if (!r.ok) { setMsg({ ok: false, text: data.detail || `HTTP ${r.status}` }); return }
      setBatch(data)
      loadPending()
      setMsg({ ok: true, text: `Parsed ${data.rows_created} rows. Review before applying.` })
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : 'Upload failed' })
    } finally {
      setBusy(null)
    }
  }

  async function onAIConvert() {
    setMsg(null); setBatch(null); setApply(null); setAi(null)
    if (!aiText.trim()) { setMsg({ ok: false, text: 'Type the changes in plain English first.' }); return }
    if (!targetId || !baselineId) { setMsg({ ok: false, text: 'Choose target + baseline periods.' }); return }
    const pErr = periodValidationError(periods, baselineId, targetId)
    if (pErr) { setMsg({ ok: false, text: pErr }); return }
    setBusy('aitext')
    try {
      const r = await authedHrisFetch('/api/v1/payroll/amendments/from-text/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_period_id: targetId, baseline_period_id: baselineId, text: aiText }),
      })
      const data = await r.json().catch(() => ({}))
      if (!r.ok) { setMsg({ ok: false, text: data.detail || `HTTP ${r.status}` }); return }
      setBatch(data)
      loadPending()
      setMsg({ ok: true, text: `AI parsed ${data.rows_created} change(s). Review below, then Apply in Step 3.` })
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : 'AI convert failed' })
    } finally {
      setBusy(null)
    }
  }

  async function validate(id: string) {
    setBusy('validate'); setPf(null)
    try {
      const r = await authedHrisFetch(`/api/v1/payroll/amendment-batches/${id}/preflight/`)
      const data = await r.json().catch(() => ({}))
      if (r.ok) setPf(data as Preflight)
      else setMsg({ ok: false, text: data.detail || `HTTP ${r.status}` })
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : 'Validate failed' })
    } finally { setBusy(null) }
  }

  useEffect(() => {   // auto pre-flight whenever a fresh batch is parsed
    if (batch?.batch_id) validate(batch.batch_id)
    else setPf(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [batch?.batch_id])

  async function onApply() {
    if (!batch?.batch_id) return
    setMsg(null); setApply(null)
    setBusy('apply')
    try {
      const r = await authedHrisFetch(
        `/api/v1/payroll/amendment-batches/${batch.batch_id}/apply/`,
        { method: 'POST' },
      )
      const data = await r.json().catch(() => ({}))
      if (!r.ok) { setMsg({ ok: false, text: data.detail || `HTTP ${r.status}` }); return }
      setApply(data)
      setMsg({
        ok: true,
        text: `Applied ${data.amendments_applied}/${data.amendments_applied + data.amendments_failed} amendments. ` +
              `Payslips: +${data.payslips_created} created, ${data.payslips_updated} updated.`,
      })
      loadPending()
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : 'Apply failed' })
    } finally {
      setBusy(null)
    }
  }

  async function onAI() {
    if (!targetId) return
    setMsg(null); setAi(null)
    setBusy('ai')
    try {
      const r = await authedHrisFetch(
        `/api/v1/payroll/periods/${targetId}/ai-review/`,
        { method: 'POST' },
      )
      const data = await r.json().catch(() => ({}))
      if (!r.ok) { setMsg({ ok: false, text: data.detail || `HTTP ${r.status}` }); return }
      setAi(data)
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : 'AI review failed' })
    } finally {
      setBusy(null)
    }
  }

  const periodError = periodValidationError(periods, baselineId, targetId)

  if (hrisAllowed !== true || payrollAllowed === undefined) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ backgroundColor: theme.bg }}>
        <div className="w-8 h-8 border-2 border-[#F07F00] border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }
  if (payrollAllowed === false) {
    return (
      <div className="min-h-screen flex items-center justify-center p-6" style={{ backgroundColor: theme.bg }}>
        <div className="max-w-md rounded-xl p-6"
             style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
          <h2 className="text-lg font-bold mb-1" style={{ color: theme.text }}>Payroll restricted</h2>
          <p className="text-sm" style={{ color: theme.t2 }}>
            Payroll is restricted to HR team, HR Manager, CFO, and Finance Manager / Financial Controller.
            Returning to dashboard…
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar title="Payroll" breadcrumbs={[{ label: 'HRIS', href: '/hris' }, { label: 'Payroll' }]} />
      <main className="flex-1 overflow-y-auto p-6 space-y-5">
        <Link href="/hris" className="inline-flex items-center gap-1.5 text-sm font-medium" style={{ color: theme.t2 }}>
          <ChevronLeft className="w-4 h-4" /> Back to HRIS
        </Link>

        {/* Hero */}
        <div className="rounded-2xl p-6"
             style={{ background: `linear-gradient(135deg, ${theme.navy}, ${theme.navy}e6)`, color: '#fff' }}>
          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="text-[11px] uppercase tracking-widest opacity-60 font-semibold">Monthly payroll</div>
              <h2 className="font-display text-2xl font-bold mt-1 italic">
                Baseline + amendments → next month
              </h2>
              <p className="text-xs opacity-70 mt-1">
                Upload an Excel file (.xlsx or .xls) of changes since last period. System applies + flags anomalies.
              </p>
            </div>
            <Wallet className="w-9 h-9 opacity-30" />
          </div>
        </div>

        {/* Period picker */}
        <div className="rounded-2xl p-5"
             style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
          <h3 className="font-semibold mb-3" style={{ color: theme.text }}>1 — Pick periods</h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium mb-1" style={{ color: theme.t2 }}>
                Baseline (prior month — copy from)
              </label>
              <select value={baselineId} onChange={e => setBaselineId(e.target.value)}
                      className="w-full px-3 py-2 rounded-lg text-sm outline-none"
                      style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }}>
                <option value="">…</option>
                {periods.map(p => (
                  <option key={p.id} value={p.id}>{p.period_name}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium mb-1" style={{ color: theme.t2 }}>
                Target (this month — create)
              </label>
              <select value={targetId} onChange={e => setTargetId(e.target.value)}
                      className="w-full px-3 py-2 rounded-lg text-sm outline-none"
                      style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }}>
                <option value="">…</option>
                {periods.map(p => (
                  <option key={p.id} value={p.id}>{p.period_name}</option>
                ))}
              </select>
            </div>
          </div>
          {periodError && (
            <p className="mt-3 text-xs font-medium inline-flex items-center gap-1.5" style={{ color: theme.er }}>
              <AlertCircle className="w-3.5 h-3.5 flex-shrink-0" /> {periodError}
            </p>
          )}
        </div>

        {/* Upload amendments */}
        <div className="rounded-2xl p-5 space-y-3"
             style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
          <h3 className="font-semibold inline-flex items-center gap-1.5" style={{ color: theme.text }}>
            <Upload className="w-4 h-4" style={{ color: theme.orange }} />
            2 — Upload amendments
          </h3>
          <p className="text-xs" style={{ color: theme.t2 }}>
            Required columns (case-insensitive): <code>employee</code>, <code>kind</code>,
            <code> amount</code>. Optional: <code>component</code>, <code>reason</code>,
            <code> approver</code>. Kinds: hire, terminate, salary_change, allowance_add,
            allowance_remove, deduction_add, deduction_remove, bonus, overtime, arrears,
            tax_override, other.
          </p>
          <div className="flex items-center gap-3 flex-wrap">
            <input ref={fileRef} type="file" accept=".xlsx,.xls,.xlsm,.xlsb,.ods"
                   className="text-sm file:mr-3 file:px-3 file:py-1.5 file:rounded-md file:border-0 file:text-xs file:font-semibold file:cursor-pointer"
                   style={{ color: theme.t2 }} />
            <button type="button" onClick={onUpload} disabled={busy !== null || !!periodError}
                    className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold transition-opacity disabled:opacity-50"
                    style={{ background: theme.orange, color: '#fff' }}
                    title={periodError || ''}>
              {busy === 'upload' ? <Loader2 className="w-4 h-4 animate-spin" /> : <FileSpreadsheet className="w-4 h-4" />}
              {busy === 'upload' ? 'Parsing…' : 'Upload + parse'}
            </button>
          </div>

          {/* AI: plain-English → amendments (Gemini). No file needed. */}
          <div className="rounded-lg p-3 mt-1" style={{ background: theme.oL, border: `1px solid ${theme.orange}33` }}>
            <p className="text-xs font-semibold inline-flex items-center gap-1.5" style={{ color: theme.orange }}>
              <Sparkles className="w-3.5 h-3.5" /> No spreadsheet? Just describe the changes
            </p>
            <p className="text-[11px] mt-1" style={{ color: theme.t2 }}>
              e.g. “Thato salary to 8500. Hire Kabo Moeng as driver at 6000. Mary Bogale resigned.
              Everyone gets a 500 fuel allowance.” Type plain amounts (8500, not 8,500.00). The AI turns it
              into rows you review before applying — nothing is applied automatically.
            </p>
            <textarea value={aiText} onChange={e => setAiText(e.target.value)} rows={3}
                      placeholder="Describe this month's changes in plain English…"
                      className="w-full mt-2 px-3 py-2 rounded-lg text-sm outline-none"
                      style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }} />
            <button type="button" onClick={onAIConvert} disabled={busy !== null || !!periodError}
                    className="mt-2 inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold transition-opacity disabled:opacity-50"
                    style={{ background: theme.navy, color: '#fff' }} title={periodError || ''}>
              {busy === 'aitext' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}
              {busy === 'aitext' ? 'Reading…' : 'Convert with AI'}
            </button>
          </div>

          {batch && (
            <div className="mt-3 rounded-lg p-3 text-xs"
                 style={{ background: theme.g100, color: theme.text }}>
              <p><strong>Batch:</strong> <code>{batch.batch_id}</code> · {batch.rows_created} rows · status: {batch.status}</p>
              {batch.ai_rows && batch.ai_rows.length > 0 && (
                <div className="mt-2">
                  <p className="font-semibold" style={{ color: theme.orange }}>AI understood {batch.ai_rows.length} change(s){batch.redactions_made ? ` · ${batch.redactions_made} sensitive value(s) redacted` : ''}:</p>
                  <table className="w-full mt-1 text-[11px]">
                    <thead><tr style={{ color: theme.t2 }}>
                      <th className="text-left pr-2">Employee</th><th className="text-left pr-2">Kind</th>
                      <th className="text-right pr-2">Amount</th><th className="text-left pr-2">Component</th><th className="text-left">Reason</th>
                    </tr></thead>
                    <tbody>
                      {batch.ai_rows.map((r, i) => (
                        <tr key={i}>
                          <td className="pr-2">{r.employee || '—'}</td><td className="pr-2">{r.kind}</td>
                          <td className="text-right pr-2 tabular-nums">{r.amount}</td>
                          <td className="pr-2">{r.component || '—'}</td><td>{r.reason || '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <p className="mt-1" style={{ color: theme.t2 }}>Check these, then Apply in Step 3. Edit your text and re-run if anything is wrong.</p>
                </div>
              )}
              {batch.parse_errors.length > 0 && (
                <div className="mt-1">
                  <p className="font-semibold" style={{ color: theme.er }}>Parse errors ({batch.parse_errors.length}):</p>
                  <ul className="list-disc ml-5">
                    {batch.parse_errors.slice(0, 10).map((e, i) => <li key={i}>{e}</li>)}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Batches awaiting apply — a DIFFERENT finance approver picks up a
            batch the uploader is blocked from applying (CFO 2026-07-23). */}
        {pending.length > 0 && (
          <div className="rounded-2xl p-5 space-y-3"
               style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
            <h3 className="font-semibold inline-flex items-center gap-1.5" style={{ color: theme.text }}>
              <CheckCircle2 className="w-4 h-4" style={{ color: theme.orange }} />
              Batches awaiting apply
            </h3>
            <p className="text-xs" style={{ color: theme.t2 }}>
              A parsed batch is applied by a Finance approver (Financial Controller or Finance
              Manager). This only stages the payslips — approval and payment happen downstream.
            </p>
            <div className="space-y-2">
              {pending.map(b => (
                <div key={b.id} className="flex flex-wrap items-center gap-3 rounded-lg p-3"
                     style={{ background: theme.g100 }}>
                  <div className="flex-1 min-w-[220px] text-xs" style={{ color: theme.text }}>
                    <div><strong>{b.target_period || '—'}</strong> from {b.baseline_period || '—'} · {b.row_count} change(s)</div>
                    <div style={{ color: theme.t2 }}>
                      Uploaded by {b.uploaded_by || '—'}{b.file_name ? ` · ${b.file_name}` : ''}
                    </div>
                  </div>
                  <button type="button" onClick={() => validate(b.id)}
                          disabled={busy === 'validate'}
                          className="inline-flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-semibold transition-opacity disabled:opacity-50"
                          style={{ background: theme.g100, color: theme.text }}>
                    Check
                  </button>
                  {b.can_apply ? (
                    <button type="button" onClick={() => applyExisting(b.id)}
                            disabled={applyingId === b.id}
                            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold transition-opacity disabled:opacity-50"
                            style={{ background: theme.navy, color: '#fff' }}>
                      {applyingId === b.id ? <Loader2 className="w-4 h-4 animate-spin" /> : <CheckCircle2 className="w-4 h-4" />}
                      {applyingId === b.id ? 'Applying…' : 'Apply'}
                    </button>
                  ) : (
                    <span className="text-xs px-3 py-2 rounded-lg" style={{ background: theme.card, color: theme.t2 }}>
                      Apply requires a Finance approver (Financial Controller / Finance Manager).
                    </span>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* 2b — Pre-flight: what will change + what looks wrong, before Apply */}
        {(pf || busy === 'validate') && (
          <div className="rounded-2xl p-5 space-y-3"
               style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
            <h3 className="font-semibold inline-flex items-center gap-1.5" style={{ color: theme.text }}>
              <CheckCircle2 className="w-4 h-4" style={{ color: theme.orange }} />
              2b — Pre-flight (what will change)
            </h3>
            {busy === 'validate' && <p className="text-xs" style={{ color: theme.t2 }}>Checking…</p>}
            {pf && (
              <>
                <div className="grid grid-cols-2 gap-3 text-xs">
                  <div className="rounded-lg p-3" style={{ background: theme.g100, color: theme.text }}>
                    <div style={{ color: theme.t2 }}>Headcount</div>
                    <div className="text-base font-semibold tabular-nums">{pf.headcount.baseline} → {pf.headcount.projected}</div>
                    <div style={{ color: theme.t2 }}>+{pf.headcount.joiners} joiner(s) · −{pf.headcount.leavers} leaver(s)</div>
                  </div>
                  <div className="rounded-lg p-3" style={{ background: theme.g100, color: theme.text }}>
                    <div style={{ color: theme.t2 }}>Earnings movement (estimate)</div>
                    <div className="text-base font-semibold tabular-nums">{fmtPula(parseFloat(pf.gross.estimated_change))}</div>
                    <div style={{ color: theme.t2 }}>{pf.rows} change(s) · exact figures at Apply</div>
                  </div>
                </div>
                {(pf.exceptions.unmatched_count > 0 || pf.exceptions.no_baseline_payslip.length > 0 || pf.exceptions.duplicate_component_rows > 0 || pf.exceptions.negative_earning_rows.length > 0) ? (
                  <div className="rounded-lg p-3 text-xs" style={{ background: 'rgba(220,38,38,0.06)', color: '#b91c1c' }}>
                    <p className="font-semibold">Fix before you apply:</p>
                    <ul className="list-disc ml-5 mt-1 space-y-0.5">
                      {pf.exceptions.unmatched_count > 0 && <li>{pf.exceptions.unmatched_count} row(s) not matched to a person: {pf.exceptions.unmatched_rows.slice(0,5).map(u => u.ref).join(', ')}</li>}
                      {pf.exceptions.no_baseline_payslip.length > 0 && <li>No last-month payslip (nothing to amend): {pf.exceptions.no_baseline_payslip.slice(0,5).join(', ')}</li>}
                      {pf.exceptions.duplicate_component_rows > 0 && <li>{pf.exceptions.duplicate_component_rows} duplicate line(s) for the same person + component (one would overwrite the other)</li>}
                      {pf.exceptions.negative_earning_rows.length > 0 && <li>{pf.exceptions.negative_earning_rows.length} negative earning row(s): {pf.exceptions.negative_earning_rows.slice(0,5).join(', ')}</li>}
                    </ul>
                  </div>
                ) : (
                  <div className="rounded-lg p-3 text-xs inline-flex items-center gap-1.5" style={{ background: 'rgba(16,185,129,0.08)', color: '#047857' }}>
                    <CheckCircle2 className="w-4 h-4" /> Clean — ready to apply{pf.period_open ? '' : ' (period is locked — re-open it first)'}.
                  </div>
                )}
              </>
            )}
          </div>
        )}

        {/* Apply batch */}
        <div className="rounded-2xl p-5 space-y-3"
             style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
          <h3 className="font-semibold inline-flex items-center gap-1.5" style={{ color: theme.text }}>
            <ArrowRight className="w-4 h-4" style={{ color: theme.orange }} />
            3 — Apply batch to target period
          </h3>
          <p className="text-xs" style={{ color: theme.t2 }}>
            Copies every baseline payslip into the target period, then applies the
            amendments. Existing target payslips are overwritten (atomic).
          </p>
          <button type="button" onClick={onApply} disabled={!batch || busy !== null || !canApply || !!periodError}
                  className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold transition-opacity disabled:opacity-50"
                  style={{ background: theme.navy, color: '#fff' }}
                  title={periodError || (!canApply ? 'Apply requires CFO / Finance Manager / Financial Controller' : '')}>
            {busy === 'apply' ? <Loader2 className="w-4 h-4 animate-spin" /> : <CheckCircle2 className="w-4 h-4" />}
            {busy === 'apply' ? 'Applying…' : (canApply ? 'Apply' : 'Apply (approver only)')}
          </button>

          {apply && (
            <div className="mt-3 rounded-lg p-3 text-xs" style={{ background: theme.g100, color: theme.text }}>
              <p><strong>Result:</strong> {apply.payslips_created} created, {apply.payslips_updated} updated.
                 Amendments: {apply.amendments_applied} applied / {apply.amendments_failed} failed.</p>
              {apply.errors.length > 0 && (
                <div className="mt-1">
                  <p className="font-semibold" style={{ color: theme.er }}>Errors ({apply.errors.length}):</p>
                  <ul className="list-disc ml-5">
                    {apply.errors.slice(0, 10).map((e, i) => <li key={i}>{e}</li>)}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>

        {/* AI review */}
        <div className="rounded-2xl p-5 space-y-3"
             style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
          <h3 className="font-semibold inline-flex items-center gap-1.5" style={{ color: theme.text }}>
            <Sparkles className="w-4 h-4" style={{ color: theme.orange }} />
            4 — AI Review (Aria)
          </h3>
          <button type="button" onClick={onAI} disabled={!targetId || busy !== null}
                  className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold transition-opacity disabled:opacity-50"
                  style={{ background: theme.orange, color: '#fff' }}>
            {busy === 'ai' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}
            {busy === 'ai' ? 'Reviewing…' : 'Run AI review'}
          </button>

          {ai && (
            <div className="mt-3 space-y-3">
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
                <KV label="Employees" value={ai.summary.count} theme={theme} />
                <KV label="Gross"     value={fmtPula(parseFloat(ai.summary.gross_total))} theme={theme} />
                <KV label="PAYE"      value={fmtPula(parseFloat(ai.summary.paye_total))} theme={theme} />
                <KV label="Net"       value={fmtPula(parseFloat(ai.summary.net_total))} theme={theme} />
              </div>

              {ai.anomalies.length > 0 && (
                <div className="rounded-lg p-3" style={{ background: '#fef2f2', border: '1px solid #fecaca' }}>
                  <p className="text-xs font-semibold text-red-800 mb-1">Anomalies ({ai.anomalies.length}):</p>
                  <ul className="list-disc ml-5 text-xs text-red-800">
                    {ai.anomalies.slice(0, 15).map((a, i) => (
                      <li key={i}>{a.employee}: {a.flag}{a.amount && ` (P ${a.amount})`}</li>
                    ))}
                  </ul>
                </div>
              )}

              {ai.ai_narrative && (
                <div className="rounded-lg p-3" style={{ background: theme.oL, border: `1px solid ${theme.orange}33` }}>
                  <p className="text-xs font-semibold mb-1" style={{ color: theme.orange }}>Aria narrative:</p>
                  <p className="text-sm whitespace-pre-wrap" style={{ color: theme.text }}>{ai.ai_narrative}</p>
                </div>
              )}

              {ai.ai_unavailable_reason && (
                <div className="rounded-lg p-3" style={{ background: '#fef3c7', border: '1px solid #fde68a' }}>
                  <p className="text-xs text-amber-800">
                    AI narrative unavailable: <code>{ai.ai_unavailable_reason}</code>.
                    Deterministic anomaly probes still ran.
                  </p>
                </div>
              )}
            </div>
          )}
        </div>

        {msg && (
          <div className="rounded-lg px-3 py-2 text-sm flex items-start gap-2"
               style={{
                 background: msg.ok ? theme.okB : theme.erB,
                 color:      msg.ok ? theme.ok  : theme.er,
                 border:     `1px solid ${msg.ok ? theme.ok : theme.er}40`,
               }}>
            {msg.ok ? <CheckCircle2 className="w-4 h-4 flex-shrink-0 mt-0.5" /> :
                      <AlertCircle  className="w-4 h-4 flex-shrink-0 mt-0.5" />}
            <div>{msg.text}</div>
          </div>
        )}
      </main>
    </div>
  )
}

function KV({ label, value, theme }: { label: string; value: string; theme: any }) {
  return (
    <div className="rounded-md px-3 py-2" style={{ background: theme.g100 }}>
      <div className="text-[10px] uppercase tracking-wider font-semibold" style={{ color: theme.t2 }}>{label}</div>
      <div className="text-sm font-bold tabular-nums mt-0.5" style={{ color: theme.text }}>{value}</div>
    </div>
  )
}
