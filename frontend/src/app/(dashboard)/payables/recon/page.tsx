'use client'

/**
 * /payables/recon — Supplier Payables Reconciliation (CFO ask 2026-07-25).
 *
 * The monthly board: per supplier, what was billed, what was paid, what was
 * not — and WHY. Every unpaid, held or unposted bill must carry a reason code
 * and a written justification before the month can be signed off; overdue and
 * flagged bills raise an escalation. Sign-off is CFO / FC / FM only, and never
 * the person who built the run.
 *
 * Styling: every colour comes from `useTheme()` (lib/themes.ts) so the page
 * follows Light / Fun / Heavenly like the rest of the ERP, and inherits the
 * contrast-tuned tokens (`orangeText`, `t3`) rather than hand-picked hex.
 * Book Antiqua arrives via Tailwind's `font-sans` mapping.
 *
 * Numbers come from the server (supplier_recon/services.py), computed as at the
 * period end and reconciled to billing + payments. Nothing is calculated here.
 */
import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { LoadingTable } from '@/components/ui/loading'
import { useTheme } from '@/contexts/ThemeContext'
import { useCompany } from '@/contexts/CompanyContext'
import { useNumberFormat } from '@/contexts/NumberFormatContext'
import { formatAmount, parseAmount, cn } from '@/lib/utils'
import {
  getSupplierReconRuns, buildSupplierReconRun, getSupplierReconDashboard,
  getSupplierReconLines, getSupplierReconItems, getSupplierReconReasonCodes,
  getSupplierReconBlockers, actionSupplierReconItem, escalateSupplierReconItem,
  finaliseSupplierReconRun, reopenSupplierReconRun, supplierReconExportPath,
  apiFetchRaw,
  uploadSupplierStatement, listSupplierStatements, getSupplierStatement,
  rematchSupplierStatement,
  type SupplierReconRun, type SupReconDashboard, type SupReconLine,
  type SupReconItem, type SupReasonCode, type SupReconBlocker,
  type SupStatementDetail, type SupStatementListItem,
} from '@/lib/api'
import {
  AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, Clock, Download,
  FileWarning, Loader2, Lock, RefreshCw, ShieldAlert, Unlock, X,
} from 'lucide-react'

const MIN_JUSTIFICATION = 20

const AGEING_LABELS: Record<string, string> = {
  current: 'Current', days_31_60: '31–60', days_61_90: '61–90',
  days_91_120: '91–120', over_120: '120+',
}

// Claims-related supplier categories. The live set is read from the dashboard
// payload (`claim_categories`) so the client never drifts from the server's
// CLAIM_BACKED_CATEGORIES; this literal is only the pre-load fallback and MUST
// mirror the server, claims_other included (H96).
const CLAIM_CATEGORIES_FALLBACK = [
  'panel_beater', 'parts', 'towing', 'assessor', 'glass', 'medical', 'claims_other',
]
// The claim types offered in the "Claims-related" dropdown (S3), per spec.
const CLAIM_TYPE_OPTIONS: { value: string; label: string }[] = [
  { value: 'parts', label: 'Parts Supplier' },
  { value: 'glass', label: 'Glass Fitment' },
  { value: 'panel_beater', label: 'Panel Beater' },
  { value: 'towing', label: 'Towing' },
]
const CATEGORY_LABELS: Record<string, string> = {
  panel_beater: 'Panel Beater', parts: 'Parts Supplier', towing: 'Towing',
  assessor: 'Assessor', glass: 'Glass Fitment', medical: 'Medical Provider',
  claims_other: 'Claims Supplier (trade unconfirmed)',
  general: 'General / Operations',
}

function currentMonth() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

/** Last 18 months, newest first — you reconcile the recent past, not 2019. */
function monthOptions() {
  const out: string[] = []
  const d = new Date()
  for (let i = 0; i < 18; i++) {
    out.push(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`)
    d.setMonth(d.getMonth() - 1)
  }
  return out
}

export default function SupplierReconPage() {
  const { theme: t } = useTheme()
  const { selectedId: companyId, selected: company } = useCompany()
  const { mode } = useNumberFormat()
  const currency = company?.base_currency || 'BWP'
  const fmt = useCallback(
    (v: string | number) => formatAmount(v, currency, mode), [currency, mode])

  const [period, setPeriod] = useState(currentMonth())
  const [run, setRun] = useState<SupplierReconRun | null>(null)
  const [dash, setDash] = useState<SupReconDashboard | null>(null)
  const [lines, setLines] = useState<SupReconLine[]>([])
  const [itemsByLine, setItemsByLine] = useState<Record<string, SupReconItem[]>>({})
  const [reasons, setReasons] = useState<SupReasonCode[]>([])
  const [blockers, setBlockers] = useState<SupReconBlocker[] | null>(null)

  const [openLine, setOpenLine] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [denied, setDenied] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [onlyOpen, setOnlyOpen] = useState(false)
  const [sortByOverdue, setSortByOverdue] = useState(true)
  // Redesign S1/S3 view + filter state.
  const [view, setView] = useState<'outstanding' | 'actioned'>('outstanding')
  const [ageGroup, setAgeGroup] = useState<'all' | 'claims' | 'operational'>('all')
  const [claimType, setClaimType] = useState('')          // '' = all claim types
  const [actionedItems, setActionedItems] = useState<SupReconItem[] | null>(null)
  const [actionedLoading, setActionedLoading] = useState(false)
  const [openCat, setOpenCat] = useState<string | null>(null)   // S2 expand

  const [draft, setDraft] = useState<Record<string, { reason: string; text: string; hold: boolean }>>({})

  const months = useMemo(monthOptions, [])

  // Semantic tones, from the theme — never hand-picked hex.
  const TONE = useMemo(() => ({
    good: t.ok, warn: t.wr, bad: t.er, info: t.inf,
    accent: t.orangeText, navy: t.navy, teal: t.teal,
  }), [t])

  const matchTone = useCallback((s: string) => ({
    matched: TONE.good, partial: TONE.warn, no_grn: TONE.warn,
    no_po: TONE.bad, no_claim: TONE.bad, unmatched: TONE.bad,
  }[s] || TONE.info), [TONE])

  const payTone = useCallback((s: string) => ({
    paid: TONE.good, partially_paid: TONE.warn,
    unpaid: TONE.bad, held: TONE.navy,
  }[s] || TONE.info), [TONE])

  const lineTone = useCallback((s: string) => ({
    cleared: TONE.good, exception: TONE.warn, pending: TONE.bad,
  }[s] || TONE.info), [TONE])

  const loadRun = useCallback(async (silent = false) => {
    if (!companyId) { setLoading(false); return }
    if (!silent) setLoading(true)
    setErr(null)
    try {
      const runs = await getSupplierReconRuns({ period })
      // Match the selected entity ONLY. A `|| runs[0]` fallback would show a
      // multi-entity user (the CFO) another company's payables under this
      // company's heading and currency.
      const found = runs.find((r) => r.company === companyId) || null
      setRun(found)
      if (found) {
        const [d, l] = await Promise.all([
          getSupplierReconDashboard(found.id), getSupplierReconLines(found.id),
        ])
        setDash(d); setLines(l)
      } else {
        setDash(null); setLines([]); setItemsByLine({})
      }
      setBlockers(null)
    } catch (e) {
      const status = (e as { status?: number }).status
      if (status === 403) setDenied(true)
      else setErr(describe(e))
    } finally {
      setLoading(false)
    }
  }, [companyId, period])

  useEffect(() => { loadRun() }, [loadRun])

  useEffect(() => {
    getSupplierReconReasonCodes().then(setReasons).catch(() => setReasons([]))
  }, [])

  // S3: re-fetch the dashboard scoped to the selected group so the "Still owed,
  // by age" cards recalculate. Only fires on a real group change (the age bands
  // are per-bill and live server-side, so this cannot be derived client-side).
  useEffect(() => {
    if (!run) return
    if ((dash?.ageing_group ?? 'all') === ageGroup) return
    let cancelled = false
    getSupplierReconDashboard(run.id, ageGroup)
      .then((d) => { if (!cancelled) setDash(d) })
      .catch(() => { /* keep the current cards on a transient error */ })
    return () => { cancelled = true }
  }, [ageGroup, run, dash])

  // S1: load the actioned bills once when the panel is first opened for a run.
  useEffect(() => {
    if (view !== 'actioned' || !run || actionedItems !== null) return
    setActionedLoading(true)
    getSupplierReconItems({ run: run.id, invoiced_actioned: true })
      .then(setActionedItems)
      .catch(() => setActionedItems([]))
      .finally(() => setActionedLoading(false))
  }, [view, run, actionedItems])

  // A new month / entity invalidates the cached actioned list and the group
  // scope, so nothing stale carries across.
  useEffect(() => { setActionedItems(null); setAgeGroup('all'); setClaimType('') },
            [run?.id])

  const build = async () => {
    if (!companyId) return
    setBusy(true); setErr(null); setNotice(null)
    try {
      await buildSupplierReconRun(companyId, period)
      setNotice(`${period} refreshed from the bills and payments ledger.`)
      await loadRun(true)
    } catch (e) { setErr(describe(e)) } finally { setBusy(false) }
  }

  const openLineItems = async (lineId: string) => {
    if (openLine === lineId) { setOpenLine(null); return }
    setOpenLine(lineId)
    if (itemsByLine[lineId]) return
    try {
      const items = await getSupplierReconItems({ line: lineId })
      setItemsByLine((prev) => ({ ...prev, [lineId]: items }))
    } catch (e) { setErr(describe(e)) }
  }

  const refreshLine = async (lineId: string) => {
    const [items, l, d] = await Promise.all([
      getSupplierReconItems({ line: lineId }),
      run ? getSupplierReconLines(run.id) : Promise.resolve([] as SupReconLine[]),
      run ? getSupplierReconDashboard(run.id, ageGroup) : Promise.resolve(null),
    ])
    setItemsByLine((prev) => ({ ...prev, [lineId]: items }))
    if (l.length) setLines(l)
    if (d) setDash(d)
    // A bill actioned here changes what counts as "invoiced, actioned" (S1),
    // so drop the cached panel list — it reloads next time it is opened.
    setActionedItems(null)
  }

  const saveDecision = async (item: SupReconItem, lineId: string) => {
    const d = draft[item.id] || {
      reason: item.reason_code || '', text: item.justification || '',
      hold: item.payment_status === 'held',
    }
    setBusy(true); setErr(null); setNotice(null)
    try {
      await actionSupplierReconItem(item.id, {
        reason_code: d.reason || null, justification: d.text, hold: d.hold,
      })
      setNotice(`Recorded against ${item.invoice_number}.`)
      setDraft((prev) => { const n = { ...prev }; delete n[item.id]; return n })
      await refreshLine(lineId)
    } catch (e) { setErr(describe(e)) } finally { setBusy(false) }
  }

  const escalate = async (item: SupReconItem, lineId: string) => {
    const text = (draft[item.id]?.text || item.justification || '').trim()
    if (text.length < MIN_JUSTIFICATION) {
      setErr('Write at least a sentence explaining what the reviewer must decide.')
      return
    }
    setBusy(true); setErr(null)
    try {
      await escalateSupplierReconItem(item.id, text)
      setNotice(`Escalated ${item.invoice_number}.`)
      await refreshLine(lineId)
    } catch (e) { setErr(describe(e)) } finally { setBusy(false) }
  }

  const checkBlockers = async () => {
    if (!run) return
    setBusy(true); setErr(null)
    try {
      const r = await getSupplierReconBlockers(run.id)
      setBlockers(r.items)
      setNotice(r.count === 0
        ? 'Nothing outstanding — this month is ready to sign off.'
        : `${r.count} bill(s) still need attention.`)
    } catch (e) { setErr(describe(e)) } finally { setBusy(false) }
  }

  const finalise = async () => {
    if (!run) return
    setBusy(true); setErr(null); setNotice(null)
    try {
      const updated = await finaliseSupplierReconRun(run.id)
      setRun(updated)
      setNotice(`${run.period_label} is signed off and locked.`)
      await loadRun(true)
    } catch (e) { setErr(describe(e)) } finally { setBusy(false) }
  }

  const reopen = async () => {
    if (!run) return
    const reason = window.prompt(
      'Reopening a signed-off month is recorded in the audit trail.\n\nWhy is it being reopened?')
    if (reason === null) return
    if (reason.trim().length < MIN_JUSTIFICATION) {
      setErr('Reopening needs a proper reason on record — at least a sentence.')
      return
    }
    setBusy(true); setErr(null)
    try {
      const updated = await reopenSupplierReconRun(run.id, reason.trim())
      setRun(updated)
      setNotice(`${run.period_label} reopened.`)
      await loadRun(true)
    } catch (e) { setErr(describe(e)) } finally { setBusy(false) }
  }

  const exportXlsx = async () => {
    if (!run) return
    setBusy(true); setErr(null)
    try {
      const res = await apiFetchRaw(supplierReconExportPath(run.id))
      if (!res.ok) throw new Error('The export could not be produced.')
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `supplier-recon-${run.company_code}-${run.period_label}.xlsx`
      document.body.appendChild(a); a.click(); a.remove()
      URL.revokeObjectURL(url)
      setNotice('Excel file downloaded.')
    } catch (e) { setErr(describe(e)) } finally { setBusy(false) }
  }

  if (denied) {
    return (
      <div className="flex flex-col min-h-screen" style={{ background: t.bg }}>
        <TopBar title="Supplier Reconciliation" breadcrumbs={[{ label: 'Payables' }]} />
        <div className="p-6">
          <Card><CardContent className="p-6 text-sm" style={{ color: t.text }}>
            Supplier reconciliation is limited to finance and payables staff.
            Speak to the Financial Controller if you need access.
          </CardContent></Card>
        </div>
      </div>
    )
  }

  const k = dash?.kpis
  // The live claims/operational split — from the server, fallback pre-load.
  const claimCats = useMemo(
    () => new Set(dash?.claim_categories ?? CLAIM_CATEGORIES_FALLBACK), [dash])

  const filteredLines = useMemo(() => {
    let out = onlyOpen ? lines.filter((l) => l.status !== 'cleared') : lines
    // S3: the All / Claims-related / Operational toggle is page-wide.
    if (ageGroup === 'claims') {
      out = out.filter((l) => claimCats.has(l.category))
      if (claimType) out = out.filter((l) => l.category === claimType)
    } else if (ageGroup === 'operational') {
      out = out.filter((l) => !claimCats.has(l.category))
    }
    return out
  }, [lines, onlyOpen, ageGroup, claimType, claimCats])
  // Most-overdue-first by default (Bharath): oldest days past due, then the
  // overdue amount, then the server's name order. Toggle back to A–Z any time.
  const visibleLines = useMemo(() => {
    if (!sortByOverdue)
      return [...filteredLines].sort((a, b) =>
        a.supplier_name.localeCompare(b.supplier_name))
    return [...filteredLines].sort((a, b) =>
      (b.max_days_past_due - a.max_days_past_due)
      || (parseAmount(b.overdue) - parseAmount(a.overdue))
      || a.supplier_name.localeCompare(b.supplier_name))
  }, [filteredLines, sortByOverdue])

  // S2: "Still owed, by category" — claims-related grouped by claim type
  // (expandable to its suppliers), operational listed by supplier. Pure
  // client-side roll-up of the already-loaded supplier lines.
  const stillOwed = useMemo(() => {
    const catMap = new Map<string, SupReconLine[]>()
    const operational: SupReconLine[] = []
    for (const l of lines) {
      if (parseAmount(l.unpaid) <= 0) continue
      if (claimCats.has(l.category)) {
        const arr = catMap.get(l.category) ?? []
        arr.push(l); catMap.set(l.category, arr)
      } else {
        operational.push(l)
      }
    }
    const claims = [...catMap.entries()].map(([category, rows]) => ({
      category, rows,
      owed: rows.reduce((s, r) => s + parseAmount(r.unpaid), 0),
    })).sort((a, b) => b.owed - a.owed)
    operational.sort((a, b) => parseAmount(b.unpaid) - parseAmount(a.unpaid))
    const total = claims.reduce((s, c) => s + c.owed, 0)
      + operational.reduce((s, r) => s + parseAmount(r.unpaid), 0)
    return { claims, operational, total }
  }, [lines, claimCats])

  return (
    <div className="flex flex-col min-h-screen" style={{ background: t.bg }}>
      <TopBar
        title="Supplier Reconciliation"
        breadcrumbs={[{ label: 'Payables' }, { label: 'Supplier Reconciliation' }]}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <select
              value={period}
              onChange={(e) => setPeriod(e.target.value)}
              className="h-8 rounded-md border px-2 text-sm"
              style={{ background: t.input, borderColor: t.cardBdr, color: t.text }}
            >
              {months.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
            <Button variant="outline" size="sm" onClick={build} disabled={busy || !companyId}
                    leftIcon={busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                                   : <RefreshCw className="w-3.5 h-3.5" />}>
              {run ? 'Refresh month' : 'Build month'}
            </Button>
            {run && (
              <Button variant="outline" size="sm" onClick={exportXlsx} disabled={busy}
                      leftIcon={<Download className="w-3.5 h-3.5" />}>
                Excel
              </Button>
            )}
            {run && !run.is_locked && (
              <>
                <Button variant="outline" size="sm" onClick={checkBlockers} disabled={busy}
                        leftIcon={<FileWarning className="w-3.5 h-3.5" />}>
                  What is outstanding?
                </Button>
                <Button size="sm" onClick={finalise} disabled={busy}
                        leftIcon={<Lock className="w-3.5 h-3.5" />}>
                  Sign off month
                </Button>
              </>
            )}
            {run?.is_locked && (
              <Button variant="outline" size="sm" onClick={reopen} disabled={busy}
                      leftIcon={<Unlock className="w-3.5 h-3.5" />}>
                Reopen
              </Button>
            )}
          </div>
        }
      />

      <div className="p-4 sm:p-6 space-y-4">
        {err && <Banner tone={t.er} bg={t.erB}>{err}</Banner>}
        {notice && !err && <Banner tone={t.ok} bg={t.okB}>{notice}</Banner>}

        {!companyId && !loading && (
          <Card><CardContent className="p-6 text-sm" style={{ color: t.text }}>
            Pick a company in the bar at the top to see its supplier reconciliation.
          </CardContent></Card>
        )}

        {loading && <LoadingTable />}

        {!loading && companyId && !run && (
          <Card><CardContent className="p-6 text-sm space-y-3" style={{ color: t.text }}>
            <p>No reconciliation exists for <strong>{period}</strong> yet.</p>
            <p style={{ color: t.t2 }}>
              Building it pulls every supplier bill for the month from the ledger,
              works out what was paid as at month end, and lists what still needs
              a reason.
            </p>
            <Button size="sm" onClick={build} disabled={busy}>Build {period}</Button>
          </CardContent></Card>
        )}

        {run && k && dash && (
          <>
            {/* ── The page answers itself before you read a tile ───────── */}
            <Card style={{ borderLeft: `4px solid ${TONE.accent}` }}>
              <CardContent className="p-4">
                {/* Plain red/green headline — the first thing the eye hits. */}
                <div className="flex items-center gap-2 mb-2 text-base font-semibold">
                  <span className="inline-block w-2.5 h-2.5 rounded-full"
                        style={{ background: k.overdue_count > 0 ? TONE.bad : TONE.good }} />
                  {k.overdue_count > 0
                    ? <span style={{ color: TONE.bad }}>
                        {k.overdue_count} payment{k.overdue_count === 1 ? '' : 's'} overdue
                        {k.overdue_30_count > 0 ? ` — ${k.overdue_30_count} over 30 days` : ''}
                      </span>
                    : <span style={{ color: TONE.good }}>
                        All current — nothing overdue
                      </span>}
                </div>
                <p className="text-base" style={{ color: t.text }}>
                  <strong>{fmt(k.total_unpaid)}</strong> still owed to{' '}
                  <strong>{k.supplier_count}</strong> suppliers.{' '}
                  {k.unactioned_count > 0
                    ? <>
                        <strong style={{ color: TONE.bad }}>
                          {k.unactioned_count} bill{k.unactioned_count === 1 ? '' : 's'}
                        </strong>{' '}still need a reason.
                      </>
                    : <span style={{ color: TONE.good }}>Every bill has been explained.</span>}
                  {' '}
                  {k.overdue_count > 0
                    ? <strong style={{ color: TONE.bad }}>
                        {k.overdue_count} overdue{k.overdue_30_count > 0
                          ? `, ${k.overdue_30_count} of them 30+ days` : ''}.
                      </strong>
                    : <span style={{ color: t.t2 }}>Nothing is overdue yet.</span>}
                </p>
                <div className="flex flex-wrap items-center gap-3 text-xs mt-2"
                     style={{ color: t.t2 }}>
                  <Pill label={run.status_display}
                        tone={run.is_locked ? TONE.good
                            : run.status === 'reopened' ? TONE.warn : TONE.navy} />
                  <span>{run.period_start} → {run.period_end} · {run.company_code}</span>
                  {run.owner_name && (
                    <span style={{ color: TONE.accent }}>
                      Owner: {run.owner_name}
                    </span>
                  )}
                  {run.prepared_by_name && <span>Built by {run.prepared_by_name}</span>}
                  {run.is_locked && run.reviewed_by_name &&
                    <span>Signed off by {run.reviewed_by_name}</span>}
                </div>
              </CardContent>
            </Card>

            {/* ── Four tiles. "Still owed" is the hero. ─────────────────── */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
              <Tile t={t} label="Still owed" value={fmt(k.total_unpaid)} hero
                    tone={parseAmount(k.total_unpaid) > 0 ? TONE.bad : TONE.good} />
              <Tile t={t} label="Billed this month" value={fmt(k.total_invoiced)}
                    sub={`${k.invoice_count} bills`} />
              <Tile t={t} label="Paid" value={fmt(k.total_paid)} tone={TONE.good}
                    sub={`${k.pct_paid}% of billed`} />
              <Tile t={t} label="Needs a reason" value={String(k.unactioned_count)}
                    tone={k.unactioned_count ? TONE.bad : TONE.good}
                    sub={`of ${k.invoice_count} bills`} />
            </div>

            {/* ── Exceptions, with the two secondary money figures ─────── */}
            <Card><CardContent className="p-4">
              <div className="text-sm font-medium mb-3" style={{ color: t.text }}>
                Exceptions
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
                <Mini t={t} label="On hold" value={fmt(k.total_held)}
                      tone={parseAmount(k.total_held) > 0 ? TONE.navy : undefined} />
                <Mini t={t} label="Escalated" value={fmt(k.total_escalated)}
                      sub={`${k.open_escalations} open`}
                      tone={k.open_escalations ? TONE.accent : undefined} />
                <Mini t={t} label="Overdue" value={String(k.overdue_count)}
                      icon={<Clock className="w-3 h-3" />}
                      sub={k.overdue_30_count ? `${k.overdue_30_count} over 30 days` : undefined}
                      tone={k.overdue_count ? TONE.bad : undefined} />
                <Mini t={t} label="No PO" value={String(k.no_po_count)}
                      icon={<ShieldAlert className="w-3 h-3" />}
                      tone={k.no_po_count ? TONE.bad : undefined} />
                <Mini t={t} label="No claim authority" value={String(k.no_claim_count)}
                      icon={<AlertTriangle className="w-3 h-3" />}
                      tone={k.no_claim_count ? TONE.bad : undefined} />
                <Mini t={t} label="Not 3-way matched" value={String(k.unmatched_count)}
                      icon={<FileWarning className="w-3 h-3" />}
                      tone={k.unmatched_count ? TONE.warn : undefined} />
              </div>
            </CardContent></Card>

            {/* ── Still owed, by category (S2) ─────────────────────────── */}
            {(stillOwed.claims.length > 0 || stillOwed.operational.length > 0) && (
              <Card><CardContent className="p-4">
                <div className="text-sm font-medium" style={{ color: t.text }}>
                  Still owed, by category
                </div>
                <p className="text-[11px] mb-3" style={{ color: t.t3 }}>
                  Claims-related, by claim type, and operational, by supplier.
                </p>
                <div className="grid gap-4 lg:grid-cols-2">
                  {/* Group 1 — claims-related, by claim type (expandable) */}
                  <div>
                    <div className="text-[11px] uppercase tracking-wide mb-1"
                         style={{ color: t.t3 }}>Claims-related</div>
                    {stillOwed.claims.length === 0 && (
                      <div className="text-xs py-2" style={{ color: t.t3 }}>
                        Nothing outstanding.
                      </div>
                    )}
                    {stillOwed.claims.map((c) => {
                      const isOpen = openCat === c.category
                      return (
                        <Fragment key={c.category}>
                          <button type="button"
                            onClick={() => setOpenCat(isOpen ? null : c.category)}
                            className="w-full flex items-center gap-2 border-b py-2 text-left"
                            style={{ borderColor: t.cardBdr, color: t.text }}>
                            {isOpen ? <ChevronDown className="w-3 h-3" />
                                    : <ChevronRight className="w-3 h-3" />}
                            <span className="flex-1 text-sm">
                              {CATEGORY_LABELS[c.category] ?? c.category}
                            </span>
                            <span className="text-xs" style={{ color: t.t3 }}>
                              {c.rows.length} supplier{c.rows.length === 1 ? '' : 's'}
                            </span>
                            <span className="text-sm font-semibold tabular-nums w-28 text-right">
                              {fmt(String(c.owed))}
                            </span>
                          </button>
                          {isOpen && c.rows.map((r) => (
                            <div key={r.id}
                              className="flex justify-between py-1 pl-7 pr-1 text-xs border-b"
                              style={{ borderColor: t.cardBdr, color: t.t2 }}>
                              <span>{r.supplier_name}</span>
                              <span className="tabular-nums">{fmt(r.unpaid)}</span>
                            </div>
                          ))}
                        </Fragment>
                      )
                    })}
                  </div>
                  {/* Group 2 — operational, by supplier */}
                  <div>
                    <div className="text-[11px] uppercase tracking-wide mb-1"
                         style={{ color: t.t3 }}>Operational</div>
                    {stillOwed.operational.length === 0 && (
                      <div className="text-xs py-2" style={{ color: t.t3 }}>
                        Nothing outstanding.
                      </div>
                    )}
                    {stillOwed.operational.map((r) => (
                      <div key={r.id}
                        className="flex items-center gap-2 border-b py-2"
                        style={{ borderColor: t.cardBdr, color: t.text }}>
                        <span className="flex-1 text-sm">{r.supplier_name}</span>
                        <span className="text-xs" style={{ color: t.t3 }}>
                          {r.invoice_count} bill{r.invoice_count === 1 ? '' : 's'}
                        </span>
                        <span className="text-sm font-semibold tabular-nums w-28 text-right">
                          {fmt(r.unpaid)}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
                <p className="text-[11px] mt-3" style={{ color: t.t3 }}>
                  {fmt(String(stillOwed.total))} of {fmt(k.total_unpaid)} still owed, by category.
                </p>
              </CardContent></Card>
            )}

            {/* ── Draft exposure ───────────────────────────────────────── */}
            {k.not_posted_count > 0 && (
              <Card style={{ borderLeft: `4px solid ${TONE.warn}` }}>
                <CardContent className="p-4">
                  <div className="text-sm font-medium" style={{ color: TONE.warn }}>
                    {fmt(k.total_not_posted)} across {k.not_posted_count} bill(s)
                    has been raised but never posted to the ledger
                  </div>
                  <p className="text-xs mt-1" style={{ color: t.t2 }}>
                    These are real supplier obligations that do not appear in the
                    general ledger, on the balance sheet, or in the AP Aging
                    report. They are included here on purpose — leaving them out
                    would suggest nothing is owed. Each one still needs a reason.
                  </p>
                </CardContent>
              </Card>
            )}

            {/* ── Ageing ───────────────────────────────────────────────── */}
            <Card><CardContent className="p-4">
              <div className="flex items-center gap-2 mb-3">
                <div className="text-sm font-medium" style={{ color: t.text }}>
                  Still owed, by age
                </div>
                {ageGroup !== 'all' && (
                  <span className="text-[11px] px-2 py-0.5 rounded-full"
                        style={{ background: t.navy, color: '#fff' }}>
                    {ageGroup === 'claims' ? 'Claims-related' : 'Operational'}
                  </span>
                )}
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
                {Object.keys(AGEING_LABELS).map((key) => {
                  const v = dash.ageing[key] ?? '0'
                  const hot = (key === 'days_91_120' || key === 'over_120')
                              && parseAmount(v) > 0
                  return (
                    <div key={key} className="rounded-md border p-3"
                         style={{ borderColor: t.cardBdr, background: t.card }}>
                      <div className="text-[11px] uppercase tracking-wide"
                           style={{ color: t.t3 }}>
                        {AGEING_LABELS[key]}
                      </div>
                      <div className="text-sm font-semibold tabular-nums text-right"
                           style={{ color: hot ? TONE.bad : t.text }}>
                        {fmt(v)}
                      </div>
                    </div>
                  )
                })}
              </div>
              <p className="text-[11px] mt-2" style={{ color: t.t3 }}>
                Same age bands as the AP Aging report, so the two agree.
              </p>
            </CardContent></Card>

            {/* ── Unclassified vendors ─────────────────────────────────── */}
            {dash.unclassified_vendors.length > 0 && (
              <Card style={{ borderLeft: `4px solid ${TONE.warn}` }}>
                <CardContent className="p-4">
                  <div className="text-sm font-medium mb-1" style={{ color: TONE.warn }}>
                    {dash.unclassified_vendors.length} supplier(s) had bills this
                    month but are not classified, so they are not on the board
                  </div>
                  <p className="text-xs mb-2" style={{ color: t.t2 }}>
                    Classify them (panel beater, parts, towing, general) so their
                    bills are reconciled too.
                  </p>
                  <div className="text-xs space-y-1">
                    {dash.unclassified_vendors.map((v) => (
                      <div key={v.contact_id}
                           className="flex justify-between border-b py-1"
                           style={{ borderColor: t.cardBdr, color: t.text }}>
                        <span>{v.contact__name}</span>
                        <span className="tabular-nums" style={{ color: t.t2 }}>
                          {fmt(v.invoiced)}
                        </span>
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>
            )}

            {/* ── Blockers ─────────────────────────────────────────────── */}
            {blockers && blockers.length > 0 && (
              <Card style={{ borderLeft: `4px solid ${t.er}` }}>
                <CardContent className="p-4">
                  <div className="text-sm font-medium mb-2" style={{ color: t.er }}>
                    {blockers.length} bill(s) block sign-off
                  </div>
                  <div className="text-xs space-y-1">
                    {blockers.map((b) => (
                      <div key={b.item_id}
                           className="flex flex-wrap gap-2 border-b py-1"
                           style={{ borderColor: t.cardBdr, color: t.text }}>
                        <span className="font-medium">{b.invoice_number}</span>
                        <span style={{ color: t.t2 }}>{b.supplier}</span>
                        <span className="tabular-nums" style={{ color: t.t2 }}>
                          {fmt(b.amount_outstanding)}
                        </span>
                        <span style={{ color: t.er }}>{b.problems.join(', ')}</span>
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>
            )}

            {/* ── Supplier table ───────────────────────────────────────── */}
            <Card><CardContent className="p-0">
              <div className="p-4 pb-2 space-y-3">
                {/* Row 1: Suppliers title + Outstanding / Invoices actioned (S1) */}
                <div className="flex items-center justify-between gap-3 flex-wrap">
                  <div className="text-sm font-medium" style={{ color: t.text }}>
                    Suppliers
                  </div>
                  <div className="flex items-center gap-1">
                    {([['outstanding', 'Outstanding'],
                       ['actioned', 'Invoices actioned']] as const).map(([v, lbl]) => (
                      <button key={v} type="button" onClick={() => setView(v)}
                        className="text-xs px-3 py-1 rounded-md border transition-colors"
                        style={{
                          borderColor: view === v ? t.navy : t.cardBdr,
                          boxShadow: view === v ? `inset 0 0 0 1px ${t.navy}` : undefined,
                          color: view === v ? t.navy : t.t2,
                          fontWeight: view === v ? 600 : 400,
                        }}>
                        {lbl}
                      </button>
                    ))}
                  </div>
                </div>
                {/* Row 2 (Outstanding only): group toggle + claim-type + sorts (S3) */}
                {view === 'outstanding' && (
                  <div className="flex flex-wrap items-center gap-3">
                    <div className="flex items-center gap-1">
                      {([['all', 'All'], ['claims', 'Claims-related'],
                         ['operational', 'Operational']] as const).map(([g, lbl]) => (
                        <button key={g} type="button"
                          onClick={() => { setAgeGroup(g); if (g !== 'claims') setClaimType('') }}
                          className="text-xs px-3 py-1 rounded-md border transition-colors"
                          style={{
                            borderColor: ageGroup === g ? t.navy : t.cardBdr,
                            boxShadow: ageGroup === g ? `inset 0 0 0 1px ${t.navy}` : undefined,
                            color: ageGroup === g ? t.navy : t.t2,
                            fontWeight: ageGroup === g ? 600 : 400,
                          }}>
                          {lbl}
                        </button>
                      ))}
                    </div>
                    {ageGroup === 'claims' && (
                      <select value={claimType}
                        onChange={(e) => setClaimType(e.target.value)}
                        className="text-xs rounded-md border px-2 py-1 bg-transparent"
                        style={{ borderColor: t.cardBdr, color: t.text }}>
                        <option value="">All claim types</option>
                        {CLAIM_TYPE_OPTIONS.map((o) => (
                          <option key={o.value} value={o.value}>{o.label}</option>
                        ))}
                      </select>
                    )}
                    <label className="flex items-center gap-2 text-xs"
                           style={{ color: t.t2 }}>
                      <input type="checkbox" checked={sortByOverdue}
                             onChange={(e) => setSortByOverdue(e.target.checked)} />
                      Most overdue first
                    </label>
                    <label className="flex items-center gap-2 text-xs"
                           style={{ color: t.t2 }}>
                      <input type="checkbox" checked={onlyOpen}
                             onChange={(e) => setOnlyOpen(e.target.checked)} />
                      Only those with something outstanding
                    </label>
                    {ageGroup !== 'all' && (
                      <span className="text-[11px]" style={{ color: t.t3 }}>
                        recalculates the age cards above ↑
                      </span>
                    )}
                  </div>
                )}
              </div>

              {/* S1: the "Invoices actioned" definition panel + table */}
              {view === 'actioned' && (
                <ActionedPanel t={t} fmt={fmt} items={actionedItems}
                               loading={actionedLoading}
                               onClose={() => setView('outstanding')} />
              )}

              {view === 'outstanding' && (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs uppercase tracking-wide border-b"
                        style={{ color: t.t3, borderColor: t.cardBdr }}>
                      <th className="px-4 py-2">Supplier</th>
                      <th className="px-4 py-2">Type</th>
                      <th className="px-4 py-2 text-right">Billed</th>
                      <th className="px-4 py-2 text-right">Paid</th>
                      <th className="px-4 py-2 text-right">Still owed</th>
                      <th className="px-4 py-2 text-right">Overdue by</th>
                      <th className="px-4 py-2 text-right">On hold</th>
                      <th className="px-4 py-2 text-center">Bills</th>
                      <th className="px-4 py-2">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibleLines.length === 0 && (
                      <tr><td colSpan={9} className="px-4 py-6 text-center"
                              style={{ color: t.t3 }}>
                        No suppliers on this board.
                      </td></tr>
                    )}
                    {visibleLines.map((l) => {
                      const isOpen = openLine === l.id
                      const edge = lineTone(l.status)
                      return (
                        <Fragment key={l.id}>
                          <tr className="border-b cursor-pointer"
                              style={{ borderColor: t.cardBdr, color: t.text }}
                              onClick={() => openLineItems(l.id)}>
                            <td className="px-4 py-2 font-medium"
                                style={{ boxShadow: `inset 3px 0 0 0 ${edge}` }}>
                              <span className="inline-flex items-center gap-1">
                                {isOpen ? <ChevronDown className="w-3.5 h-3.5" />
                                        : <ChevronRight className="w-3.5 h-3.5" />}
                                {l.supplier_name}
                              </span>
                            </td>
                            <td className="px-4 py-2 text-xs" style={{ color: t.t2 }}>
                              {l.category_display}
                            </td>
                            <td className="px-4 py-2 text-right tabular-nums">{fmt(l.invoiced)}</td>
                            <td className="px-4 py-2 text-right tabular-nums"
                                style={{ color: parseAmount(l.paid) > 0 ? TONE.good : t.t3 }}>
                              {fmt(l.paid)}
                            </td>
                            <td className="px-4 py-2 text-right tabular-nums font-semibold"
                                style={{ color: parseAmount(l.overdue) > 0 ? TONE.bad
                                       : parseAmount(l.unpaid) > 0 ? TONE.good : t.t3 }}>
                              {fmt(l.unpaid)}
                            </td>
                            <td className="px-4 py-2 text-right tabular-nums">
                              {l.max_days_past_due > 0
                                ? <span style={{ color: TONE.bad,
                                                 fontWeight: l.max_days_past_due >= 30 ? 700 : 400 }}>
                                    {l.max_days_past_due} day{l.max_days_past_due === 1 ? '' : 's'}
                                  </span>
                                : parseAmount(l.unpaid) > 0
                                  ? <span style={{ color: TONE.good }}>Not due</span>
                                  : <span style={{ color: t.t3 }}>—</span>}
                            </td>
                            <td className="px-4 py-2 text-right tabular-nums"
                                style={{ color: parseAmount(l.held) > 0 ? TONE.navy : t.t3 }}>
                              {fmt(l.held)}
                            </td>
                            <td className="px-4 py-2 text-center tabular-nums">
                              {l.invoice_count}
                              {l.unactioned_count > 0 && (
                                <span className="ml-1 text-[11px]" style={{ color: TONE.bad }}>
                                  ({l.unactioned_count} to explain)
                                </span>
                              )}
                            </td>
                            <td className="px-4 py-2">
                              <Pill label={l.status_display} tone={edge} />
                            </td>
                          </tr>
                          {isOpen && (
                            <tr>
                              <td colSpan={9} className="px-4 py-3"
                                  style={{ background: t.g50 }}>
                                <BillList
                                  t={t} TONE={TONE}
                                  matchTone={matchTone} payTone={payTone}
                                  items={itemsByLine[l.id]}
                                  reasons={reasons}
                                  locked={run.is_locked}
                                  busy={busy}
                                  fmt={fmt}
                                  draft={draft}
                                  setDraft={setDraft}
                                  onSave={(it) => saveDecision(it, l.id)}
                                  onEscalate={(it) => escalate(it, l.id)}
                                />
                                <SupplierStatementPanel
                                  t={t} TONE={TONE} fmt={fmt}
                                  lineId={l.id} locked={run.is_locked}
                                />
                              </td>
                            </tr>
                          )}
                        </Fragment>
                      )
                    })}
                  </tbody>
                </table>
              </div>
              )}
            </CardContent></Card>

            <p className="text-[11px]" style={{ color: t.t3 }}>
              This board explains payments — it does not make them. Money still
              moves through Payments and the FNB flow.
            </p>
          </>
        )}
      </div>
    </div>
  )
}

/* ─────────────────────────── small pieces ─────────────────────────── */

function Banner({ tone, bg, children }:
                { tone: string; bg: string; children: React.ReactNode }) {
  return (
    <div className="rounded-md border px-3 py-2 text-sm"
         style={{ borderColor: tone, color: tone, background: bg }}>
      {children}
    </div>
  )
}

function Pill({ label, tone }: { label: string; tone: string }) {
  return (
    <span className="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium"
          style={{ backgroundColor: `${tone}1A`, color: tone }}>
      {label}
    </span>
  )
}

type ThemeLike = ReturnType<typeof useTheme>['theme']

/**
 * S1 — "Invoices actioned" panel. A bill counts as actioned when it has an
 * invoice number captured AND no reason is still outstanding (both, per spec).
 * The definition rides as a subtitle so anyone opening the panel understands
 * the filter without asking; the X collapses back to the Outstanding view.
 */
function ActionedPanel({ t, fmt, items, loading, onClose }: {
  t: ThemeLike
  fmt: (v: string | number) => string
  items: SupReconItem[] | null
  loading: boolean
  onClose: () => void
}) {
  const statusColour = (it: SupReconItem) =>
    it.payment_status === 'paid' ? t.ok
      : it.payment_status === 'held' ? t.navy : t.er
  const statusLabel = (it: SupReconItem) =>
    it.payment_status === 'held' ? 'On hold'
      : it.payment_status === 'paid' ? 'Paid' : 'Unpaid'
  return (
    <div className="border-t" style={{ borderColor: t.cardBdr }}>
      <div className="flex items-start justify-between p-4 pb-2">
        <div>
          <div className="text-sm font-medium" style={{ color: t.text }}>
            What is invoiced, actioned?
          </div>
          <p className="text-[11px]" style={{ color: t.t3 }}>
            Bills with an invoice number captured and no reason still outstanding.
          </p>
        </div>
        <button type="button" onClick={onClose} aria-label="Close"
                className="p-1 rounded-md" style={{ color: t.t2 }}>
          <X className="w-4 h-4" />
        </button>
      </div>
      {loading && (
        <div className="px-4 py-6 text-sm flex items-center gap-2"
             style={{ color: t.t3 }}>
          <Loader2 className="w-4 h-4 animate-spin" /> Loading actioned bills…
        </div>
      )}
      {!loading && items && items.length === 0 && (
        <div className="px-4 py-6 text-sm text-center" style={{ color: t.t3 }}>
          No bills have been actioned on this board yet.
        </div>
      )}
      {!loading && items && items.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide border-b"
                  style={{ color: t.t3, borderColor: t.cardBdr }}>
                <th className="px-4 py-2">Bill</th>
                <th className="px-4 py-2">Supplier</th>
                <th className="px-4 py-2 text-right">Amount</th>
                <th className="px-4 py-2">Invoice No.</th>
                <th className="px-4 py-2">Due</th>
                <th className="px-4 py-2">Status</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it) => (
                <tr key={it.id} className="border-b"
                    style={{ borderColor: t.cardBdr, color: t.text }}>
                  <td className="px-4 py-2 font-medium">{it.invoice_number}</td>
                  <td className="px-4 py-2" style={{ color: t.t2 }}>{it.supplier_name}</td>
                  <td className="px-4 py-2 text-right tabular-nums">{fmt(it.amount)}</td>
                  <td className="px-4 py-2" style={{ color: t.t2 }}>{it.invoice_number || '—'}</td>
                  <td className="px-4 py-2" style={{ color: t.t2 }}>{it.due_date || '—'}</td>
                  <td className="px-4 py-2">
                    <span className="text-xs font-medium" style={{ color: statusColour(it) }}>
                      {statusLabel(it)}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function Tile({ t, label, value, sub, tone, hero }:
              { t: ThemeLike; label: string; value: string; sub?: string
                tone?: string; hero?: boolean }) {
  return (
    <div className="rounded-lg border p-4"
         style={{ background: t.card, borderColor: t.cardBdr, boxShadow: t.cardSh }}>
      <div className="text-[11px] uppercase tracking-wide" style={{ color: t.t3 }}>
        {label}
      </div>
      <div className={cn('font-semibold tabular-nums', hero ? 'text-3xl' : 'text-xl')}
           style={{ color: tone || t.text }}>
        {value}
      </div>
      {sub && <div className="text-[11px]" style={{ color: t.t3 }}>{sub}</div>}
    </div>
  )
}

function Mini({ t, label, value, sub, tone, icon }:
              { t: ThemeLike; label: string; value: string; sub?: string
                tone?: string; icon?: React.ReactNode }) {
  return (
    <div className="rounded-md border p-2.5"
         style={{ background: t.card, borderColor: t.cardBdr }}>
      <div className="text-[10px] uppercase tracking-wide inline-flex items-center gap-1"
           style={{ color: t.t3 }}>
        {icon}{label}
      </div>
      <div className="text-sm font-semibold tabular-nums"
           style={{ color: tone || t.text }}>
        {value}
      </div>
      {sub && <div className="text-[10px]" style={{ color: t.t3 }}>{sub}</div>}
    </div>
  )
}

interface BillListProps {
  t: ThemeLike
  TONE: { good: string; warn: string; bad: string; info: string
          accent: string; navy: string; teal: string }
  matchTone: (s: string) => string
  payTone: (s: string) => string
  items?: SupReconItem[]
  reasons: SupReasonCode[]
  locked: boolean
  busy: boolean
  fmt: (v: string | number) => string
  draft: Record<string, { reason: string; text: string; hold: boolean }>
  setDraft: React.Dispatch<React.SetStateAction<Record<string, { reason: string; text: string; hold: boolean }>>>
  onSave: (item: SupReconItem) => void
  onEscalate: (item: SupReconItem) => void
}

function BillList({ t, TONE, matchTone, payTone, items, reasons, locked, busy,
                    fmt, draft, setDraft, onSave, onEscalate }: BillListProps) {
  if (!items) return <div className="text-xs py-2" style={{ color: t.t3 }}>Loading bills…</div>
  if (items.length === 0) return <div className="text-xs py-2" style={{ color: t.t3 }}>No bills.</div>

  return (
    <div className="space-y-3">
      {items.map((it) => {
        const d = draft[it.id] || {
          reason: it.reason_code || '',
          text: it.justification || '',
          hold: it.payment_status === 'held',
        }
        const set = (patch: Partial<typeof d>) =>
          setDraft((prev) => ({ ...prev, [it.id]: { ...d, ...patch } }))
        const tooShort = d.text.trim().length > 0 && d.text.trim().length < MIN_JUSTIFICATION
        const liveEsc = it.escalations.filter(
          (e) => e.status === 'open' || e.status === 'acknowledged')

        return (
          <div key={it.id} className="rounded-md border p-3 space-y-2"
               style={{ background: t.card, borderColor: t.cardBdr,
                        borderLeft: `3px solid ${payTone(it.payment_status)}` }}>
            <div className="flex flex-wrap items-center gap-2 text-sm"
                 style={{ color: t.text }}>
              <span className="font-medium">{it.invoice_number}</span>
              <Pill label={it.payment_status_display} tone={payTone(it.payment_status)} />
              <Pill label={it.match_status_display} tone={matchTone(it.match_status)} />
              {!it.is_posted && <Pill label="Not in ledger" tone={TONE.warn} />}
              {it.due_state === 'not_due' && it.payment_status !== 'paid' && (
                <Pill label="Not yet due" tone={TONE.good} />
              )}
              {it.is_overdue && (
                <Pill
                  label={it.due_state === 'overdue_30'
                    ? `${it.days_past_due} days overdue — 30+`
                    : `${it.days_past_due} days overdue`}
                  tone={TONE.bad} />
              )}
              {it.is_overdue && !it.actioned && (
                <Pill label="Reason required" tone={TONE.bad} />
              )}
              {it.actioned && (
                <span className="inline-flex items-center gap-1 text-[11px]"
                      style={{ color: TONE.good }}>
                  <CheckCircle2 className="w-3 h-3" />
                  {/* S5: system-stamped from the recorder's OMNI profile. */}
                  Recorded by {it.actioned_by_name || 'staff'}
                  {it.actioned_by_department ? ` · ${it.actioned_by_department}` : ''}
                </span>
              )}
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-5 gap-2 text-xs">
              <Field t={t} label="Billed" value={fmt(it.amount)} num />
              <Field t={t} label="Paid" value={fmt(it.amount_paid)} num />
              <Field t={t} label="Still owed" value={fmt(it.amount_outstanding)} num
                     tone={it.is_overdue ? TONE.bad
                         : it.due_state === 'not_due' ? TONE.good : undefined} />
              <Field t={t} label="Due" value={it.due_date || '—'}
                     tone={it.is_overdue ? TONE.bad
                         : it.due_state === 'not_due' ? TONE.good : undefined} />
              <Field t={t} label="PO / claim"
                     value={[it.po_number, it.claim_reference].filter(Boolean).join(' · ') || '—'} />
            </div>

            {it.currency && it.currency !== 'BWP' && (
              <div className="text-[11px]" style={{ color: TONE.warn }}>
                Billed in {it.currency} {it.face_amount} — shown above converted to
                Pula at the rate locked on the bill.
              </div>
            )}

            {liveEsc.length > 0 && (
              <div className="text-[11px] rounded-md px-2 py-1"
                   style={{ backgroundColor: `${TONE.accent}14`, color: TONE.accent }}>
                Escalated{liveEsc[0].raised_to_name
                  ? ` to ${liveEsc[0].raised_to_name}` : ''}: {liveEsc[0].justification}
              </div>
            )}

            {it.needs_justification && !locked && (
              <div className="space-y-2">
                <div className="flex flex-wrap items-center gap-2">
                  <select
                    value={d.reason}
                    onChange={(e) => set({ reason: e.target.value })}
                    className="h-8 rounded-md border px-2 text-xs min-w-[16rem]"
                    style={{ background: t.input, borderColor: t.cardBdr, color: t.text }}
                  >
                    <option value="">Why was this not paid? —</option>
                    {reasons.map((r) => (
                      <option key={r.id} value={r.id}>
                        {r.label}{r.requires_escalation ? ' (escalates)' : ''}
                      </option>
                    ))}
                  </select>
                  <label className="flex items-center gap-1 text-xs" style={{ color: t.t2 }}>
                    <input type="checkbox" checked={d.hold}
                           onChange={(e) => set({ hold: e.target.checked })} />
                    Put on hold
                  </label>
                </div>
                <textarea
                  value={d.text}
                  onChange={(e) => set({ text: e.target.value })}
                  rows={2}
                  placeholder="Explain the position in your own words — this is what the reviewer and the auditor read."
                  className="w-full rounded-md border px-2 py-1.5 text-xs"
                  style={{ background: t.input, borderColor: t.cardBdr, color: t.text }}
                />
                <div className="flex flex-wrap items-center gap-2">
                  <Button size="sm"
                          disabled={busy || !d.reason || d.text.trim().length < MIN_JUSTIFICATION}
                          onClick={() => onSave(it)}>
                    Record
                  </Button>
                  <Button size="sm" variant="outline" disabled={busy}
                          onClick={() => onEscalate(it)}>
                    Escalate
                  </Button>
                  {tooShort && (
                    <span className="text-[11px]" style={{ color: TONE.bad }}>
                      A bit more detail — at least {MIN_JUSTIFICATION} characters.
                    </span>
                  )}
                  {it.requires_escalation && liveEsc.length === 0 && (
                    <span className="text-[11px]" style={{ color: TONE.accent }}>
                      This one will raise an escalation when recorded.
                    </span>
                  )}
                </div>
              </div>
            )}

            {it.needs_justification && locked && it.justification && (
              <div className="text-xs" style={{ color: t.t2 }}>
                <span className="font-medium">{it.reason_code_label}: </span>
                {it.justification}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

function Field({ t, label, value, num, tone }:
               { t: ThemeLike; label: string; value: string; num?: boolean
                 tone?: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide" style={{ color: t.t3 }}>
        {label}
      </div>
      <div className={cn(num && 'tabular-nums')} style={{ color: tone || t.text }}>
        {value}
      </div>
    </div>
  )
}

/* ───────────────────── supplier statement panel ───────────────────── */
const PROPOSAL_LABEL: Record<string, string> = {
  pay_now: 'Pay now', hold: 'Hold', do_not_pay: 'Do not pay',
  investigate: 'Investigate',
}
const MATCH_LABEL: Record<string, string> = {
  matched: 'Matched', amount_variance: 'Amount variance',
  credit_note: 'Credit note', already_paid: 'Already paid',
  statement_only: 'On statement only', omni_only: 'In Omni only',
  duplicate: 'Duplicate', unmatched_payment: 'Unmatched payment',
}

function SupplierStatementPanel({ t, TONE, fmt, lineId, locked }: {
  t: ThemeLike
  TONE: Record<string, string>
  fmt: (v: string | number) => string
  lineId: string
  locked: boolean
}) {
  const [list, setList] = useState<SupStatementListItem[]>([])
  const [detail, setDetail] = useState<SupStatementDetail | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const proposalTone = (p: string) =>
    p === 'pay_now' ? TONE.good : p === 'do_not_pay' ? TONE.bad
      : p === 'hold' ? TONE.navy : TONE.warn

  const refresh = useCallback(async () => {
    try {
      const r = await listSupplierStatements(lineId)
      setList(r.statements)
      if (r.statements.length && !detail) {
        setDetail(await getSupplierStatement(r.statements[0].id))
      }
    } catch (e) { setErr(describe(e)) }
  }, [lineId, detail])

  useEffect(() => { void refresh() }, [lineId])   // eslint-disable-line react-hooks/exhaustive-deps

  const onFile = async (file: File) => {
    setBusy(true); setErr('')
    try {
      const d = await uploadSupplierStatement(lineId, file)
      setDetail(d)
      await refresh()
    } catch (e) { setErr(describe(e)) } finally { setBusy(false) }
  }

  const rematch = async () => {
    if (!detail) return
    setBusy(true); setErr('')
    try { setDetail(await rematchSupplierStatement(detail.id)) }
    catch (e) { setErr(describe(e)) } finally { setBusy(false) }
  }

  return (
    <div className="mt-4 rounded-lg border p-3"
         style={{ borderColor: t.cardBdr, background: t.card }}>
      <div className="flex items-center justify-between gap-2 mb-2">
        <div className="flex items-center gap-2 text-sm font-medium"
             style={{ color: t.text }}>
          <FileWarning className="w-4 h-4" style={{ color: TONE.accent }} />
          Supplier statement
        </div>
        <div className="flex items-center gap-2">
          {detail && (
            <Button variant="outline" size="sm" onClick={rematch} disabled={busy}>
              <RefreshCw className="w-3.5 h-3.5 mr-1" /> Re-match
            </Button>
          )}
          {!locked && (
            <label className="inline-flex items-center gap-1 text-xs px-3 py-1.5
                              rounded-md cursor-pointer"
                   style={{ background: TONE.accent, color: '#fff',
                            opacity: busy ? 0.6 : 1 }}>
              {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    : <Download className="w-3.5 h-3.5 rotate-180" />}
              Upload CSV / Excel
              <input type="file" accept=".csv,.txt,.xlsx,.xlsm,.xls,.ods"
                     className="hidden" disabled={busy}
                     onChange={(e) => {
                       const f = e.target.files?.[0]
                       if (f) void onFile(f)
                       e.target.value = ''
                     }} />
            </label>
          )}
        </div>
      </div>

      {err && (
        <div className="text-xs mb-2 px-2 py-1 rounded"
             style={{ background: t.g50, color: TONE.bad }}>{err}</div>
      )}

      {!detail && !err && (
        <p className="text-xs" style={{ color: t.t3 }}>
          No statement uploaded for this supplier this month. Upload the
          statement the supplier sent and Omni will reconcile it against the
          bills above and propose what to pay.
        </p>
      )}

      {detail && (
        <>
          <div className="flex flex-wrap gap-4 mb-2 text-xs" style={{ color: t.t2 }}>
            <span>File: <b style={{ color: t.text }}>{detail.file_name}</b></span>
            {detail.closing_balance != null && (
              <span>Statement balance:{' '}
                <b className="tabular-nums" style={{ color: t.text }}>
                  {fmt(detail.closing_balance)}</b></span>
            )}
            <span>Proposed to pay:{' '}
              <b className="tabular-nums" style={{ color: TONE.good }}>
                {fmt(detail.summary.proposed_pay_total)}</b></span>
            <span style={{ color: t.t3 }}>{detail.summary.total_lines} lines</span>
          </div>

          {detail.parse_warnings && detail.parse_warnings.length > 0 && (
            <div className="text-[11px] mb-2" style={{ color: TONE.warn }}>
              {detail.parse_warnings.length} row(s) needed attention:{' '}
              {detail.parse_warnings.slice(0, 3).map((w) => `row ${w.row} — ${w.message}`).join('; ')}
            </div>
          )}

          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr style={{ color: t.t3 }}>
                  <th className="text-left px-2 py-1">Reference</th>
                  <th className="text-left px-2 py-1">Result</th>
                  <th className="text-left px-2 py-1">Omni bill</th>
                  <th className="text-right px-2 py-1">Variance</th>
                  <th className="text-right px-2 py-1">Proposed</th>
                  <th className="text-left px-2 py-1">Recommendation</th>
                  <th className="text-left px-2 py-1">Why</th>
                </tr>
              </thead>
              <tbody>
                {detail.matches.map((m) => (
                  <tr key={m.id} className="border-t"
                      style={{ borderColor: t.cardBdr, color: t.text }}>
                    <td className="px-2 py-1">{m.statement_reference || '—'}</td>
                    <td className="px-2 py-1" style={{ color: t.t2 }}>
                      {MATCH_LABEL[m.match_type] || m.match_type}</td>
                    <td className="px-2 py-1">{m.omni_invoice || '—'}</td>
                    <td className="px-2 py-1 text-right tabular-nums"
                        style={{ color: parseAmount(m.variance) !== 0 ? TONE.warn : t.t3 }}>
                      {parseAmount(m.variance) !== 0 ? fmt(m.variance) : '—'}</td>
                    <td className="px-2 py-1 text-right tabular-nums">
                      {parseAmount(m.proposed_amount) > 0 ? fmt(m.proposed_amount) : '—'}</td>
                    <td className="px-2 py-1">
                      <span className="px-2 py-0.5 rounded-full text-[11px] font-medium"
                            style={{ background: proposalTone(m.proposal) + '22',
                                     color: proposalTone(m.proposal) }}>
                        {PROPOSAL_LABEL[m.proposal] || m.proposal}</span>
                    </td>
                    <td className="px-2 py-1" style={{ color: t.t3 }}>{m.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-[10px] mt-2" style={{ color: t.t3 }}>
            A recommendation only — payment is authorised in FNB. Omni never pays
            an unmatched, duplicate, unapproved or already-paid line automatically.
          </p>
        </>
      )}
    </div>
  )
}

/** Pull the most useful sentence out of a DRF error body. */
function describe(e: unknown): string {
  const anyE = e as { message?: string; body?: unknown }
  const body = anyE?.body
  if (body && typeof body === 'object') {
    const b = body as Record<string, unknown>
    if (typeof b.blocking === 'string') return b.blocking
    for (const key of Object.keys(b)) {
      const v = b[key]
      if (typeof v === 'string') return v
      if (Array.isArray(v) && typeof v[0] === 'string') return v[0] as string
    }
  }
  return anyE?.message || 'Something went wrong.'
}
